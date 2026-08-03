"""Offline tests for the stable LLM client contract."""
import json
from types import SimpleNamespace

from analyzer.llm_client import LLMClient


VALID_RESULT = {
    "event_type": "军事冲突",
    "severity": 4,
    "china_myanmar_impact": "边境安全承压",
    "risk_warning": "关注冲突升级",
    "key_entities": ["缅甸军方"],
    "key_locations": ["掸邦"],
    "summary": "掸邦发生冲突",
    "sentiment": "negative",
}


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        message = SimpleNamespace(content=outcome)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, outcomes):
        self.chat = SimpleNamespace(completions=FakeCompletions(outcomes))


def make_config(**overrides):
    cfg = {
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "model_name": "test-model",
        "temperature": 0.0,
        "max_tokens": 256,
        "max_retries": 2,
        "timeout_seconds": 1,
        "cache_enabled": True,
    }
    cfg.update(overrides)
    return cfg


def test_custom_instruction_keeps_structured_output_contract(tmp_path):
    fake = FakeClient([json.dumps(VALID_RESULT, ensure_ascii=False)])
    client = LLMClient(
        config=make_config(cache_enabled=False),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试正文", instruction="重点分析管道安全")

    system_prompt = fake.chat.completions.calls[0]["messages"][0]["content"]
    assert "请严格返回JSON格式" in system_prompt
    assert "重点分析管道安全" in system_prompt
    assert result["analysis_status"] == "ok"
    assert result["cached"] is False


def test_successful_result_is_cached_by_content(tmp_path):
    fake = FakeClient([json.dumps(VALID_RESULT, ensure_ascii=False)])
    client = LLMClient(
        config=make_config(),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    first = client.analyze_news("同一条新闻")
    second = client.analyze_news("同一条新闻")

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["analysis_status"] == "cached"
    assert len(fake.chat.completions.calls) == 1


def test_malformed_response_returns_complete_degraded_result(tmp_path):
    fake = FakeClient(["not-json"])
    client = LLMClient(
        config=make_config(cache_enabled=False, max_retries=1),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "degraded"
    assert result["event_type"] == "解析失败"
    assert result["severity"] == 1
    assert result["key_entities"] == []
    assert result["key_locations"] == []
    assert result["error"]


def test_malformed_response_is_retried_and_can_recover(tmp_path):
    fake = FakeClient(["not-json", json.dumps(VALID_RESULT, ensure_ascii=False)])
    client = LLMClient(
        config=make_config(cache_enabled=False),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "ok"
    assert len(fake.chat.completions.calls) == 2


def test_fenced_json_response_is_accepted(tmp_path):
    fenced = "```json\n" + json.dumps(VALID_RESULT, ensure_ascii=False) + "\n```"
    fake = FakeClient([fenced])
    client = LLMClient(
        config=make_config(cache_enabled=False),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "ok"
    assert result["event_type"] == "军事冲突"


def test_missing_fields_return_complete_degraded_contract(tmp_path):
    fake = FakeClient([json.dumps({"event_type": "冲突"}, ensure_ascii=False)])
    client = LLMClient(
        config=make_config(cache_enabled=False, max_retries=1),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "degraded"
    assert set(VALID_RESULT).issubset(result)
    assert "severity" in result["error"]


def test_rate_limit_is_retried(tmp_path):
    fake = FakeClient([
        RuntimeError("429 rate limit"),
        json.dumps(VALID_RESULT, ensure_ascii=False),
    ])
    client = LLMClient(
        config=make_config(cache_enabled=False),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "ok"
    assert len(fake.chat.completions.calls) == 2


def test_schema_validation_rejects_wrong_types_and_list_items(tmp_path):
    invalid = dict(VALID_RESULT)
    invalid.update({
        "event_type": {},
        "china_myanmar_impact": [],
        "risk_warning": None,
        "summary": 123,
        "key_entities": ["缅甸军方", 7],
    })
    encoded = json.dumps(invalid, ensure_ascii=False)
    fake = FakeClient([encoded, encoded])
    client = LLMClient(
        config=make_config(cache_enabled=False),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "degraded"
    assert result["event_type"] == "未知"
    assert result["key_entities"] == []
    assert "event_type" in result["error"]
    assert len(fake.chat.completions.calls) == 2


def test_invalid_cache_entry_is_revalidated_and_refreshed(tmp_path):
    from analyzer.prompts import NEWS_ANALYSIS_PROMPT

    fake = FakeClient([json.dumps(VALID_RESULT, ensure_ascii=False)])
    client = LLMClient(
        config=make_config(),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )
    text = "同一条新闻"
    messages = [
        {"role": "system", "content": NEWS_ANALYSIS_PROMPT.format(text="")},
        {"role": "user", "content": text},
    ]
    key = client._build_cache_key(messages, text)
    client._cache[key] = {"event_type": {"corrupted": True}}

    result = client.analyze_news(text)

    assert result["analysis_status"] == "ok"
    assert result["cached"] is False
    assert len(fake.chat.completions.calls) == 1


def test_retry_exhaustion_returns_complete_degraded_result(tmp_path):
    fake = FakeClient([TimeoutError("timeout"), TimeoutError("timeout")])
    client = LLMClient(
        config=make_config(),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "degraded"
    assert result["event_type"] == "未知"
    assert "timeout" in result["error"]
    assert len(fake.chat.completions.calls) == 2


def test_placeholder_key_skips_network_immediately(tmp_path):
    fake = FakeClient([AssertionError("network must not be called")])
    client = LLMClient(
        config=make_config(api_key="your-api-key-here"),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client.analyze_news("测试")

    assert result["analysis_status"] == "degraded"
    assert "API Key" in result["error"]
    assert fake.chat.completions.calls == []


def test_internal_retry_keeps_chain_specific_json_schema(tmp_path):
    chain_result = {"actors": ["缅甸军方"], "location": "掸邦", "event": "冲突"}
    fake = FakeClient([json.dumps(chain_result, ensure_ascii=False)])
    client = LLMClient(
        config=make_config(cache_enabled=False),
        client=fake,
        cache_path=tmp_path / "cache.jsonl",
        sleep_fn=lambda _: None,
    )

    result = client._call_with_retry([
        {"role": "system", "content": "返回链式步骤 JSON"},
        {"role": "user", "content": "测试"},
    ])

    assert result == chain_result
