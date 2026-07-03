"""
analyzer.llm_client - 大模型 API 调用模块
对接实验室部署的地缘环境大模型（兼容 OpenAI Chat Completions 格式）
内置重试机制、JSON 解析、降级处理
"""
import json
import time
import logging
from typing import Dict, Optional, List
from openai import OpenAI
from utils.config import get_llm_config
from analyzer.prompts import NEWS_ANALYSIS_PROMPT, build_analysis_prompt

logger = logging.getLogger(__name__)


class LLMClient:
    """大模型客户端，封装 HTTP POST 调用逻辑"""

    def __init__(self):
        """初始化 OpenAI 兼容客户端"""
        cfg = get_llm_config()
        self._client = OpenAI(
            base_url=cfg.get("base_url", "http://localhost:8000/v1"),
            api_key=cfg.get("api_key", "dummy-key"),
        )
        self._model = cfg.get("model_name", "geopolitical-gpt")
        self._temperature = cfg.get("temperature", 0.3)
        self._max_tokens = cfg.get("max_tokens", 2048)
        self._max_retries = cfg.get("max_retries", 3)
        self._timeout_seconds = cfg.get("timeout_seconds", 30)

    def analyze_news(self, text: str, instruction: str = None) -> Dict:
        """
        将新闻文本发送给大模型，获取结构化分析结果

        :param text: 新闻文本
        :param instruction: 分析指令（可选），默认使用 prompts.py 中的模板
        :return: 结构化字典
        """
        # 使用 prompts.py 中的提示词模板
        if instruction is None:
            instruction = NEWS_ANALYSIS_PROMPT.format(text="")

        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text}
        ]

        return self._call_with_retry(messages)

    def call_with_prompt(self, prompt_name: str, text: str) -> Dict:
        """
        使用指定名称的提示词模板调用大模型

        :param prompt_name: 提示词名称（如 "risk_assessment", "event_classification"）
        :param text: 新闻文本
        :return: 分析结果字典
        """
        from analyzer.prompts import get_prompt_by_name
        prompt_template = get_prompt_by_name(prompt_name)
        instruction = prompt_template.format(text="")

        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": text}
        ]

        return self._call_with_retry(messages)

    def _call_with_retry(self, messages: List[Dict]) -> Dict:
        """
        带重试机制的 LLM 调用

        :param messages: OpenAI Chat 格式消息列表
        :return: 解析后的字典
        """
        last_error = None

        for attempt in range(self._max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    timeout=self._timeout_seconds,
                )
                content = response.choices[0].message.content.strip()

                # 尝试解析 JSON
                result = self._parse_json_response(content)
                return result

            except Exception as e:
                last_error = e
                wait = 2 * (2 ** attempt)
                logger.warning(
                    f"[LLM] 调用失败 (尝试 {attempt+1}/{self._max_retries}): {e}"
                )
                if attempt < self._max_retries - 1:
                    logger.info(f"[LLM] 等待 {wait}s 后重试...")
                    time.sleep(wait)

        # 所有重试均失败，返回降级结果
        logger.error(f"[LLM] 调用最终失败，返回降级结果")
        return {
            "event_type": "未知",
            "china_myanmar_impact": "分析失败",
            "risk_warning": "大模型调用异常",
            "key_entities": [],
            "summary": f"LLM Error: {str(last_error)}"
        }

    def _parse_json_response(self, content: str) -> Dict:
        """
        解析大模型返回的 JSON 内容
        处理可能的 markdown 代码块包裹情况

        :param content: 原始返回文本
        :return: 解析后的字典
        """
        content = content.strip()

        # 去除 ```json ... ``` 包裹
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines).strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试用正则提取 JSON 块
        json_match = None
        import re
        # 查找最外层的 { ... }
        pattern = re.compile(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', re.DOTALL)
        matches = pattern.findall(content)
        for match in matches:
            try:
                result = json.loads(match)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue

        logger.warning(f"[LLM] JSON 解析失败，返回原始文本")
        return {
            "event_type": "解析失败",
            "raw_response": content[:500]
        }

    def batch_analyze(self, texts: list, delay: float = 1.0) -> list:
        """
        批量分析（带延迟避免过载）

        :param texts: 文本列表
        :param delay: 每次调用间隔（秒）
        :return: 分析结果列表
        """
        results = []
        for i, text in enumerate(texts):
            logger.info(f"[LLM] 分析第 {i+1}/{len(texts)} 条...")
            result = self.analyze_news(text)
            results.append(result)
            if i < len(texts) - 1:
                time.sleep(delay)
        return results


# 模块级单例
_llm_instance = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端单例"""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMClient()
    return _llm_instance
