"""
大模型 API 客户端。

对外始终返回稳定的地缘分析字段，并通过 analysis_status/cached/error
显式标记正常、缓存或降级状态。OpenAI SDK、API Key 或远端服务不可用时，
模块本身仍可导入，调用方也不会因可选能力缺失而崩溃。
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:  # LLM 是可选能力
    OpenAI = None

from analyzer.prompts import NEWS_ANALYSIS_PROMPT, get_prompt_by_name
from utils.config import get_data_paths, get_llm_config

logger = logging.getLogger(__name__)

_DEFAULT_ANALYSIS = {
    "event_type": "未知",
    "severity": 1,
    "china_myanmar_impact": "暂无可靠分析",
    "risk_warning": "大模型分析不可用，请结合规则结果复核",
    "key_entities": [],
    "key_locations": [],
    "summary": "暂无可靠的大模型分析结果",
    "sentiment": "neutral",
}
_REQUIRED_FIELDS = tuple(_DEFAULT_ANALYSIS.keys())
_TEXT_FIELDS = ("event_type", "china_myanmar_impact", "risk_warning", "summary")


def build_degraded_result(error: str) -> Dict:
    """Build the public stable response used when LLM setup/calls fail."""
    result = dict(_DEFAULT_ANALYSIS)
    result["key_entities"] = []
    result["key_locations"] = []
    result.update({
        "analysis_status": "degraded",
        "cached": False,
        "error": str(error),
    })
    return result


class LLMClient:
    """OpenAI Chat Completions 兼容客户端，含校验、重试和本地缓存。

    支持多提供商模型：每个模型可配置独立的 base_url 和 api_key_env，
    运行时按需创建对应的 OpenAI 客户端并缓存。
    """

    def __init__(self, config: Dict = None, client=None, cache_path=None,
                 sleep_fn=None):
        cfg = dict(config or get_llm_config())
        self._model = cfg.get("model_name", "geopolitical-gpt")
        self._temperature = cfg.get("temperature", 0.3)
        self._max_tokens = cfg.get("max_tokens", 2048)
        self._max_retries = max(1, int(cfg.get("max_retries", 3)))
        self._timeout_seconds = max(1, int(cfg.get("timeout_seconds", 30)))
        self._cache_enabled = bool(cfg.get("cache_enabled", True))
        self._sleep = sleep_fn or time.sleep
        self._api_key = str(cfg.get("api_key", "")).strip()
        self._base_url = cfg.get("base_url", "http://localhost:8000/v1")
        self._availability_error = ""

        # 多提供商支持：构建模型 → (base_url, api_key) 注册表
        self._model_registry: Dict[str, Dict] = {}
        self._client_cache: Dict[str, object] = {}
        for m in cfg.get("available_models", []):
            mid = m.get("id", "")
            if not mid:
                continue
            # 解析 base_url：模型专属 > 全局默认
            m_base = m.get("base_url", self._base_url)
            # 解析 api_key：从指定环境变量 > 全局 api_key
            key_env = m.get("api_key_env", "")
            if key_env:
                m_key = os.environ.get(key_env, "").strip()
            else:
                m_key = self._api_key
            self._model_registry[mid] = {
                "base_url": m_base,
                "api_key": m_key,
            }

        if client is not None:
            self._client = client
        elif OpenAI is None:
            self._client = None
            self._availability_error = "openai SDK 未安装"
        elif self._is_placeholder_key(self._api_key):
            self._client = None
            self._availability_error = "LLM API Key 未配置"
        else:
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
            )

        default_cache = Path(get_data_paths()["raw"]) / "llm_cache.jsonl"
        self._cache_path = Path(cache_path) if cache_path else default_cache
        self._cache_lock = threading.Lock()
        self._cache = self._load_cache() if self._cache_enabled else {}

    @staticmethod
    def _is_placeholder_key(api_key: str) -> bool:
        return not api_key or "your-" in api_key.lower() or "粘贴" in api_key

    def _get_client_for_model(self, model: str = None):
        """根据模型 ID 获取对应的 OpenAI 客户端（按需创建并缓存）。

        :param model: 模型 ID，None 表示使用默认客户端
        :return: (client, actual_model, error)
        """
        if not model or model not in self._model_registry:
            return self._client, model or self._model, self._availability_error

        reg = self._model_registry[model]
        if model in self._client_cache:
            return self._client_cache[model], model, ""

        m_key = reg["api_key"]
        if self._is_placeholder_key(m_key) or not m_key:
            return None, model, f"模型 {model} 的 API Key 未配置"

        if OpenAI is None:
            return None, model, "openai SDK 未安装"

        try:
            client = OpenAI(base_url=reg["base_url"], api_key=m_key)
            self._client_cache[model] = client
            return client, model, ""
        except Exception as e:
            return None, model, str(e)

    def analyze_news(self, text: str, instruction: str = None,
                     model: str = None) -> Dict:
        """分析单条新闻；自定义指令只作为附加关注点，不覆盖 JSON 契约。

        :param model: 可选模型ID，覆盖默认模型（用于前端模型切换）
        """
        system_prompt = NEWS_ANALYSIS_PROMPT.format(text="")
        if instruction and instruction.strip():
            system_prompt += f"\n\n用户额外关注：{instruction.strip()}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        return self._analyze_messages(messages, text, model=model)

    def call_with_prompt(self, prompt_name: str, text: str) -> Dict:
        """使用命名模板调用，仍应用统一返回契约和缓存。"""
        instruction = get_prompt_by_name(prompt_name).format(text="")
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text},
        ]
        return self._analyze_messages(messages, text)

    def _analyze_messages(self, messages: List[Dict], text: str,
                          model: str = None) -> Dict:
        cache_key = self._build_cache_key(messages, text, model)
        cached = self._cache.get(cache_key)
        if cached is not None:
            result = self._normalize_result(cached)
            if result["analysis_status"] == "ok":
                result["analysis_status"] = "cached"
                result["cached"] = True
                return result
            logger.warning("[LLM] 缓存条目校验失败，重新请求: %s", result["error"])
            self._cache.pop(cache_key, None)

        # 多提供商路由：根据 model 选择对应的客户端
        client, actual_model, err = self._get_client_for_model(model)
        if client is None:
            return self._degraded_result(err or "LLM 客户端不可用")

        result = self._call_with_retry(messages, normalize=True,
                                        model=actual_model, client=client)
        if self._cache_enabled and result["analysis_status"] == "ok":
            self._store_cache(cache_key, result)
        return result

    def _call_with_retry(self, messages: List[Dict], normalize: bool = False,
                          model: str = None, client=None) -> Dict:
        """Call the model; normalize only the main news-analysis contract.

        Chain reasoning uses this compatibility method with its own JSON schema,
        so the default preserves arbitrary JSON dictionaries.
        """
        _client = client or self._client
        _model = model or self._model
        last_error: Optional[Exception] = None
        last_degraded = None
        for attempt in range(self._max_retries):
            normalized_failure = None
            try:
                response = _client.chat.completions.create(
                    model=_model,
                    messages=messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    timeout=self._timeout_seconds,
                )
                content = (response.choices[0].message.content or "").strip()
                parsed = self._parse_json_response(content)
                if normalize:
                    normalized = self._normalize_result(parsed)
                    if normalized["analysis_status"] != "ok":
                        normalized_failure = normalized
                        raise ValueError(normalized["error"])
                    return normalized
                if parsed.get("_parse_error"):
                    raise ValueError(parsed["_parse_error"])
                return parsed
            except Exception as exc:
                last_error = exc
                last_degraded = normalized_failure
                logger.warning(
                    "[LLM] 调用失败 (尝试 %s/%s): %s",
                    attempt + 1,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries - 1:
                    self._sleep(2 * (2 ** attempt))

        error = str(last_error or "未知错误")
        if normalize:
            return last_degraded or self._degraded_result(error)
        return {"error": error}

    def _parse_json_response(self, content: str) -> Dict:
        content = content.strip()
        if content.startswith("```"):
            lines = [line for line in content.splitlines()
                     if not line.strip().startswith("```")]
            content = "\n".join(lines).strip()

        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        pattern = re.compile(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', re.DOTALL)
        for match in pattern.findall(content):
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
        return {"_parse_error": "LLM 响应不是有效 JSON", "raw_response": content[:500]}

    def _normalize_result(self, payload: Dict) -> Dict:
        parse_error = payload.get("_parse_error")
        invalid = [field for field in _REQUIRED_FIELDS if field not in payload]
        result = dict(payload)
        result.pop("_parse_error", None)
        for field, default in _DEFAULT_ANALYSIS.items():
            result.setdefault(field, list(default) if isinstance(default, list) else default)

        for field in _TEXT_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                result[field] = _DEFAULT_ANALYSIS[field]
                invalid.append(field)

        severity = payload.get("severity")
        if (
            isinstance(severity, bool)
            or not isinstance(severity, (int, float))
            or not float(severity).is_integer()
            or not 1 <= int(severity) <= 5
        ):
            result["severity"] = 1
            invalid.append("severity")
        else:
            result["severity"] = int(severity)

        for field in ("key_entities", "key_locations"):
            value = payload.get(field)
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                result[field] = []
                invalid.append(field)
            else:
                result[field] = value

        if payload.get("sentiment") not in ("positive", "negative", "neutral"):
            result["sentiment"] = "neutral"
            invalid.append("sentiment")

        if parse_error:
            result["event_type"] = "解析失败"
            result["analysis_status"] = "degraded"
            result["error"] = parse_error
        elif invalid:
            result["analysis_status"] = "degraded"
            result["error"] = "LLM 响应缺少或包含无效字段: " + ", ".join(sorted(set(invalid)))
        else:
            result["analysis_status"] = "ok"
            result["error"] = ""
        result["cached"] = False
        return result

    def _degraded_result(self, error: str) -> Dict:
        return build_degraded_result(error)

    def _build_cache_key(self, messages: List[Dict], text: str,
                         model: str = None) -> str:
        material = json.dumps(
            {"model": model or self._model, "messages": messages, "text": text},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _load_cache(self) -> Dict[str, Dict]:
        if not self._cache_path.exists():
            return {}
        cache = {}
        try:
            with self._cache_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                        if record.get("key") and isinstance(record.get("result"), dict):
                            cache[record["key"]] = record["result"]
                    except json.JSONDecodeError:
                        logger.warning("[LLM] 忽略损坏的缓存行")
        except OSError as exc:
            logger.warning("[LLM] 缓存读取失败: %s", exc)
        return cache

    def _store_cache(self, key: str, result: Dict):
        record_result = dict(result)
        record_result["cached"] = False
        with self._cache_lock:
            try:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                with self._cache_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(
                        {"key": key, "result": record_result},
                        ensure_ascii=False,
                    ) + os.linesep)
                self._cache[key] = record_result
            except OSError as exc:
                logger.warning("[LLM] 缓存写入失败: %s", exc)

    def batch_analyze(self, texts: list, delay: float = 1.0) -> list:
        results = []
        for index, text in enumerate(texts):
            result = self.analyze_news(text)
            results.append(result)
            if index < len(texts) - 1 and result["analysis_status"] != "cached":
                self._sleep(delay)
        return results


_llm_instance = None
_llm_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """获取全局 LLM 单例。"""
    global _llm_instance
    if _llm_instance is None:
        with _llm_lock:
            if _llm_instance is None:
                _llm_instance = LLMClient()
    return _llm_instance
