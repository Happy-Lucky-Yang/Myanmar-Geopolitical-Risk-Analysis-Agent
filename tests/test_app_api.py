"""Flask API smoke and degradation visibility tests."""
from unittest.mock import patch

import pytest


@pytest.mark.parametrize('query,expected', [
    ('days=31&end_date=2026-03-31', ['2026-03']),
    ('days=30&end_date=2026-03-31', []),
    ('days=90&end_date=2026-03-31', ['2026-01', '2026-02', '2026-03']),
    ('days=60&end_date=2026-03-15', ['2026-02']),
    ('months=2&end_date=2026-03-15', ['2026-02']),
    ('days=3660&end_date=2026-03-15', None),
])
def test_multimodal_api_calendar_window(query, expected):
    from app import app
    result = app.test_client().get('/api/multimodal?' + query)
    assert result.status_code == 200
    data = result.json['data']
    if expected is not None:
        assert [r['month'] for r in data['aligned']] == expected
    else:
        assert len(data['aligned']) >= 119
    assert data['metadata']['period_end_exclusive'] is True


@pytest.mark.parametrize('query', ['months=no', 'months=0', 'months=121', 'months=1.5',
                                    'months=2&days=30', 'days=bad', 'end_date=invalid'])
def test_multimodal_api_rejects_invalid_window(query):
    from app import app
    assert app.test_client().get('/api/multimodal?' + query).status_code == 400


def test_multimodal_province_filter(monkeypatch):
    import analyzer.spatial_analysis as spatial
    from app import app
    monkeypatch.setattr(spatial, 'aggregate_provinces', lambda *args: [
        {'region': 'r1'}, {'region': 'r2'}])
    data = app.test_client().get('/api/multimodal?days=31&end_date=2026-01-31&region=r2').json['data']
    assert [r['region'] for r in data['province_alignment']] == ['r2']


@pytest.mark.parametrize('endpoint', ['/api/map', '/api/map/events', '/api/map/unified'])
def test_map_snapshot_changes_are_conflicts(endpoint, monkeypatch):
    import app as module
    from analyzer.report_generator import SnapshotChanged
    def changed(*args, **kwargs):
        raise SnapshotChanged('fixture revision changed')
    monkeypatch.setattr(module, '_cached_map_html', changed)
    assert module.app.test_client().get(endpoint).status_code == 409


def test_revision_checked_before_and_after_research_request(monkeypatch):
    import app as module
    from unittest.mock import MagicMock
    loader = MagicMock()
    loader.revision.side_effect = ['first', 'second']
    monkeypatch.setattr(module, 'get_data_loader', lambda: loader)
    result = module.app.test_client().get('/api/multimodal?days=1&end_date=2026-01-01&revision=first')
    assert result.status_code == 409
    loader.revision.side_effect = ['second']
    assert module.app.test_client().get('/api/multimodal?revision=first').status_code == 409


def test_health_endpoint_imports_and_responds_without_optional_services():
    from app import app

    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_analyze_rejects_non_object_json():
    from app import app

    response = app.test_client().post("/api/analyze", json=["not", "an", "object"])

    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_analyze_exposes_optional_stage_warnings(caplog):
    import app as app_module

    class Loader:
        def clean_text(self, text):
            return text.strip()

        def save_analysis_result(self, record):
            return "analysis.json"

        def append_risk_score(self, **kwargs):
            raise AssertionError("手工分析不能写日指标")

    class NER:
        def extract_entities(self, text):
            raise RuntimeError("LAC model missing")

    class Sentiment:
        def get_risk_sentiment(self, text):
            return {"sentiment_score": 0.2, "risk_score": 0.8, "risk_level": "high", "source": "test"}

    class LLM:
        def analyze_news(self, text, instruction=None):
            return {
                "event_type": "未知", "severity": 1,
                "china_myanmar_impact": "暂无可靠分析",
                "risk_warning": "请人工复核", "key_entities": [],
                "key_locations": [], "summary": "不可用", "sentiment": "neutral",
                "analysis_status": "degraded", "cached": False,
                "error": "LLM API Key 未配置",
            }

    class Scorer:
        def calculate_risk_score(self, indicators):
            return {"risk_score": 60.0, "risk_level": "中风险", "indicator_scores": {}}

    with patch.object(app_module, "get_data_loader", return_value=Loader()), \
         patch.object(app_module, "get_ner_extractor", return_value=NER()), \
         patch.object(app_module, "get_sentiment_analyzer", return_value=Sentiment()), \
         patch.object(app_module, "get_llm_client", side_effect=RuntimeError("LLM API Key 未配置")), \
         patch.object(app_module, "get_risk_scorer", return_value=Scorer()), \
         patch.object(app_module, "get_knowledge_graph", side_effect=RuntimeError("Neo4j disabled")), \
         patch("data.gdelt_crawler.get_gdelt_crawler", side_effect=RuntimeError("offline")), \
         patch("data.nightlight_crawler.get_nightlight_crawler", side_effect=RuntimeError("offline")), \
         patch("data.economic_crawler.get_economic_crawler", side_effect=RuntimeError("offline")), \
         patch("analyzer.alert_monitor.get_alert_monitor", side_effect=RuntimeError("offline")), \
         patch("analyzer.diagnostic.get_diagnostic_analyzer", side_effect=RuntimeError("offline")):
        response = app_module.app.test_client().post("/api/analyze", json={"text": "测试新闻"})

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["entities"] == {
        "locations": [], "organizations": [], "persons": [], "events": [],
    }
    assert payload["data"]["llm_analysis"]["analysis_status"] == "degraded"
    assert payload["data"]["llm_analysis"]["event_type"] == "未知"
    assert payload["data"]["llm_analysis"]["cached"] is False
    assert any("LAC model missing" in warning for warning in payload["data"]["warnings"])
    assert any("LLM API Key 未配置" in warning for warning in payload["data"]["warnings"])
    assert payload["data"]["risk_score"]["run_kind"] == "manual"
    assert payload["data"]["alert"] is None
    assert not any("Neo4j" in warning for warning in payload["data"]["warnings"])
    assert 'LAC model missing' not in caplog.text


def test_manual_uncaught_failure_does_not_log_private_echo(monkeypatch, caplog):
    from types import SimpleNamespace
    import app as module
    marker = '仅测试私密正文回显'
    def fail(text):
        raise RuntimeError(marker)
    monkeypatch.setattr(module, 'get_data_loader', lambda: SimpleNamespace(clean_text=fail))
    response = module.app.test_client().post('/api/analyze', json={'text': marker})
    assert response.status_code == 500
    assert marker not in caplog.text and 'RuntimeError' in caplog.text


def test_analyze_rejects_invalid_or_oversized_custom_instruction():
    from app import app

    invalid = app.test_client().post(
        "/api/analyze", json={"text": "测试", "instruction": ["not", "text"]}
    )
    oversized = app.test_client().post(
        "/api/analyze", json={"text": "测试", "instruction": "x" * 2001}
    )

    assert invalid.status_code == 400
    assert oversized.status_code == 400


def test_trend_exposes_optional_annotation_warnings():
    import app as app_module

    class Loader:
        def revision(self):
            return 'fixture-revision'

        def load_risk_history(self, days, **filters):
            return [{"date": "2026-06-15", "risk_score": 50.0}]

    class Trend:
        def full_analysis(self, scores, dates=None):
            return {"moving_average": [], "trend": "数据不足"}

        def forecast(self, scores, days_ahead, dates=None):
            return {"forecast": [], "slope": 0.0, "confidence": 0.0, "r_squared": 0.0}

        def detect_anomalies(self, scores):
            return []

    with patch.object(app_module, "get_data_loader", return_value=Loader()), \
         patch.object(app_module, "get_trend_analyzer", return_value=Trend()), \
         patch("analyzer.alert_monitor.get_alert_monitor", side_effect=RuntimeError("alert offline")), \
         patch("data.historical_events.get_historical_events", side_effect=RuntimeError("history offline")):
        response = app_module.app.test_client().get("/api/trend?chart=false")

    payload = response.get_json()
    assert response.status_code == 200
    assert any("alert offline" in warning for warning in payload["data"]["warnings"])
    assert any("history offline" in warning for warning in payload["data"]["warnings"])
