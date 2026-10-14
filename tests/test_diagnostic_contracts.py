"""贡献分析只使用实际权重和同一日期快照，不构造因果或缺测预测。"""
from datetime import date, timedelta

import pytest

from analyzer.diagnostic import (DiagnosticAnalyzer, assert_no_causal_verbs, change_identity,
                                 shapley_attribution, shapley_values)


def record(day, score, contribution):
    return {'date': day.isoformat(), 'risk_score': score, 'run_kind': 'existing',
            'region': 'MMR', 'algorithm_version': 'risk-v2', 'sources': ['fixture'],
            'details': {'indicator_scores': {
                'conflict_frequency': {'contribution': contribution, 'quality': 'derived'},
                'nightlight_change': {'contribution': None, 'quality': 'missing'}}}}


def test_missing_contribution_and_zero_do_not_crash():
    analyzer = DiagnosticAnalyzer()
    row = record(date(2026, 1, 1), 0, 0)
    result = analyzer.diagnose(row)
    assert result['total_score'] == 0
    assert result['primary_driver'] is None
    assert len(result['drivers']) == 1
    assert analyzer.diagnose({'risk_score': None, 'details': None})['total_score'] is None


def test_equal_calendar_periods_and_stored_contributions():
    analyzer = DiagnosticAnalyzer()
    start = date(2026, 1, 1)
    rows = [record(start + timedelta(days=i), 20 if i < 7 else 80, .2 if i < 7 else .8) for i in range(14)]
    result = analyzer.diagnose_from_history(14, '2026-01-14', records=rows)
    assert result['delta'] == 60
    assert result['changes'][0]['change'] == 60
    assert result['unexplained_delta'] == 0
    assert result['older_period'] == '2026-01-01 ~ 2026-01-07'
    assert result['recent_period'] == '2026-01-08 ~ 2026-01-14'
    assert result['older_coverage'] == result['recent_coverage'] == 1
    partial = rows[2:]
    result = analyzer.diagnose_from_history(14, '2026-01-14', records=partial)
    assert result['delta'] is None and 'error' in result
    odd = analyzer.diagnose_from_history(15, '2026-01-14', records=rows)
    assert odd['period_days'] == 7 and odd['window_days'] == 14


def test_forecast_does_not_fill_missing_or_mix_versions():
    analyzer = DiagnosticAnalyzer()
    start = date(2026, 1, 1)
    rows = [record(start + timedelta(days=i), 70, .7) for i in range(14)]
    rows[-1]['run_kind'] = 'demo'
    result = analyzer.explain_and_forecast(14, end_date='2026-01-14', records=rows)
    assert result['future_outlook']['predicted_score'] is None
    assert result['future_outlook']['status'] == 'insufficient'
    assert result['future_outlook']['projected_level'] == 'unknown'
    with pytest.raises(ValueError):
        analyzer.explain_and_forecast(3, records=[])


def test_missing_indicator_is_not_invented_as_zero():
    analyzer = DiagnosticAnalyzer()
    result = analyzer.diagnose_change(
        {'risk_score': 20, 'indicator_scores': {'a': {'contribution': .2}}},
        {'risk_score': 60, 'indicator_scores': {'b': {'contribution': .6}}})
    assert result['changes'] == []
    assert result['uncomparable_indicators'] == ['a', 'b']
    assert result['unexplained_delta'] == -40


def test_shapley_values_two_indicator_dilution():
    """手算例：w={a:.5,b:.5}, Z={a:1,b:0}。v(N)=.5；φ(a)=.75, φ(b)=-.25（加入 b 稀释归一均值）。"""
    phi, v_full = shapley_values({'a': 0.5, 'b': 0.5}, {'a': 1.0, 'b': 0.0})
    assert round(v_full, 6) == 0.5
    assert round(phi['a'], 6) == 0.75 and round(phi['b'], 6) == -0.25
    assert round(sum(phi.values()), 6) == round(v_full, 6)        # 效率公理 Σφ=v(N)


def test_shapley_attribution_a1_a3_and_insufficient():
    """A1（ΣC=1）、A3（Σφ=R）；缺权重/值时退化为 insufficient 不报错。"""
    row = {'risk_score': 68, 'indicator_scores': {
        'a': {'weight': 0.6, 'value': 0.8, 'contribution': 0.48, 'quality': 'derived'},
        'b': {'weight': 0.4, 'value': 0.5, 'contribution': 0.20, 'quality': 'derived'}}}
    res = shapley_attribution(row)
    assert res['status'] == 'ok'
    assert abs(sum(res['shares'].values()) - 1.0) < 1e-6                       # A1
    assert abs(sum(res['shapley'].values()) - res['risk_index']) < 1e-6        # A3
    assert abs(res['risk_index'] * 100 - 68) < 0.01
    poor = shapley_attribution({'risk_score': 50, 'indicator_scores': {'a': {'contribution': .5}}})
    assert poor['status'] == 'insufficient' and poor['shapley'] == {}


def test_change_identity_a2_full_coverage_fixed_weights():
    """A2：固定权重、同一全覆盖指标集下 Σw·ΔZ=ΔR，残差为零。"""
    cur = {'risk_score': 68, 'indicator_scores': {
        'a': {'weight': 0.6, 'value': 0.8}, 'b': {'weight': 0.4, 'value': 0.5}}}
    prev = {'risk_score': 16, 'indicator_scores': {
        'a': {'weight': 0.6, 'value': 0.2}, 'b': {'weight': 0.4, 'value': 0.1}}}
    ci = change_identity(cur, prev)
    assert ci['status'] == 'ok' and ci['A2_holds'] is True
    assert abs(ci['residual']) <= 0.01 and ci['delta_r'] == 52


def test_causal_verb_filter_blocks_and_passes():
    assert assert_no_causal_verbs('冲突频次对当期风险的贡献份额为 40%') is not None
    for bad in ('冲突导致风险上升', '制裁引起紧张', '事件造成风险'):
        with pytest.raises(AssertionError):
            assert_no_causal_verbs(bad)


def test_diagnose_includes_shapley_and_keeps_legacy_keys():
    analyzer = DiagnosticAnalyzer()
    row = {'risk_score': 68, 'indicator_scores': {
        'a': {'weight': 0.6, 'value': 0.8, 'contribution': 0.48, 'quality': 'derived'},
        'b': {'weight': 0.4, 'value': 0.5, 'contribution': 0.20, 'quality': 'derived'}}}
    result = analyzer.diagnose(row)
    assert result['total_score'] == 68 and 'drivers' in result and 'attribution_text' in result
    assert result['shapley']['status'] == 'ok'
    assert abs(sum(result['shapley']['shares'].values()) - 1.0) < 1e-6


def test_geo_risk_v3_diagnostic_shapley_end_to_end():
    """九指标 risk-v3 → 诊断 Shapley 全链：稀疏缺测下 A1/A3 仍成立。"""
    from analyzer.risk_scorer import get_geo_risk_scorer
    events = [
        {'source': 'gdelt', 'event_id': '1', 'date': '20260101', 'root_code': '19',
         'quad_class': '4', 'goldstein': -9.5, 'num_mentions': 12, 'actor1_country': 'BM', 'actor2_country': 'BM'},
        {'source': 'gdelt', 'event_id': '2', 'date': '20260101', 'root_code': '17',
         'quad_class': '3', 'goldstein': -7.0, 'num_mentions': 5, 'actor1_country': 'US', 'actor2_country': 'BM'},
        {'source': 'gdelt', 'event_id': '3', 'date': '20260101', 'root_code': '06',
         'quad_class': '1', 'goldstein': 6.0, 'num_mentions': 3, 'actor1_country': 'CH', 'actor2_country': 'BM'},
    ]
    risk = get_geo_risk_scorer().compute_event_risk(
        events, {'nightlight_change': {'value': -0.2, 'quality': 'observed'}})
    sh = DiagnosticAnalyzer().diagnose(risk)['shapley']
    assert sh['status'] == 'ok'
    assert abs(sum(sh['shares'].values()) - 1.0) < 1e-6                    # A1
    assert abs(sum(sh['shapley'].values()) - sh['risk_index']) < 1e-6      # A3
    assert abs(sh['risk_index'] * 100 - risk['risk_score']) < 0.01         # Σφ×100 = 报告风险分
