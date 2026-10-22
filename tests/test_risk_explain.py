"""信源立场标注与 AI 风险解读的回归测试（离线，不调用真实模型）。"""
import pytest
from types import SimpleNamespace


def test_source_stance_annotates_and_counts():
    from utils import source_meta
    news = [{'source': 'The Irrawaddy'}, {'source': '缅甸缅华网'},
            {'source': 'The Irrawaddy'}, {'source': 'unknown-x'}]
    source_meta.annotate_stance(news)
    assert news[0]['source_stance'] == 'independent' and news[0]['source_stance_label']
    assert news[1]['source_stance'] == 'domestic'
    assert news[3]['source_stance'] == 'unlabeled'
    counts = source_meta.stance_counts(news)
    assert counts == {'independent': 2, 'domestic': 1, 'unlabeled': 1}
    assert '独立' in source_meta.explain(counts)


def test_health_endpoint_includes_stance(monkeypatch):
    import app as app_module
    fake = {'缅甸缅华网': {'status': 'healthy', 'success_rate': 1.0, 'recent_attempts': 1,
                        'recent_success': 1, 'last_count': 1, 'last_success': None,
                        'last_error': None, 'records': []}}
    monkeypatch.setattr('data.source_health.get_source_health_tracker',
                        lambda: SimpleNamespace(get_health=lambda: fake))
    data = app_module.app.test_client().get('/api/sources/health').get_json()['data']
    assert data['缅甸缅华网']['stance']['stance'] == 'domestic'


def test_risk_explain_validate_rejects_foreign_numbers_and_causality():
    from analyzer.risk_explainer import validate
    evidence = {'date': '2026-10-20', 'risk_score': 55.06,
                'indicators': [{'indicator': '冲突频次', 'points': 12.5}]}
    good = {'summary': '风险分 55.06', 'top_drivers': [{'indicator': '冲突频次', 'points': 12.5}],
            'explanations': ['冲突频次贡献 12.5 分'], 'suggestions': [], 'data_gaps': [],
            'confidence': '中'}
    assert validate(good, evidence)['summary'] == '风险分 55.06'
    with pytest.raises(ValueError):
        validate({**good, 'summary': '风险高达 88.8'}, evidence)          # 证据外数值
    with pytest.raises(ValueError):
        validate({**good, 'summary': '冲突导致风险上升'}, evidence)        # 因果断言
    with pytest.raises(ValueError):
        validate({**good, 'summary': '详见 https://evil.test'}, evidence)  # 链接


def test_risk_explain_rule_fallback(monkeypatch):
    from analyzer import risk_explainer as explainer
    explainer._cache.clear()
    explainer._calls.clear()
    record = {'date': '2026-10-20', 'risk_score': 55.06, 'risk_level': '中风险', 'news_count': 48,
              'details': {'indicator_coverage': 0.4, 'indicator_scores': {
                  'conflict_frequency': {'value': 0.8, 'weight': 0.3,
                                         'contribution_points': 24.0, 'quality': 'derived'},
                  'nightlight_change': {'value': None, 'weight': 0.2,
                                        'contribution_points': None, 'quality': 'missing'}}},
              'source_stances': {'domestic': 20, 'independent': 8}}
    monkeypatch.setattr(explainer, 'latest_record', lambda date=None: record)
    monkeypatch.setattr(explainer, '_call_model', lambda evidence, model: None)
    result = explainer.explain_risk()
    assert result['status'] == 'degraded' and result['model_version'] == 'rule-based'
    assert result['answer']['top_drivers'][0]['indicator'] == '冲突频次'
    assert result['answer']['confidence'] == '低'
    assert result['evidence']['source_stances']['domestic'] == 20


def test_risk_explain_endpoint_no_data_and_ok(monkeypatch):
    import app as app_module
    client = app_module.app.test_client()
    monkeypatch.setattr('analyzer.risk_explainer.explain_risk',
                        lambda date=None, model=None: {'status': 'no_data', 'reason': '无记录'})
    assert client.post('/api/risk/explain', json={}).status_code == 404
    monkeypatch.setattr('analyzer.risk_explainer.explain_risk',
                        lambda date=None, model=None: {'status': 'ok', 'answer': {'summary': 'x'},
                                                       'model_version': 'glm-4-flash', 'date': '2026-10-20'})
    response = client.post('/api/risk/explain', json={'model': 'glm-4-flash'})
    assert response.status_code == 200 and response.get_json()['data']['status'] == 'ok'
    assert client.post('/api/risk/explain', json={'date': 'bad'}).status_code == 400
