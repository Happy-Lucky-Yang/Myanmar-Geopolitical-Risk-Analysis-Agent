"""贡献分析只使用实际权重和同一日期快照，不构造因果或缺测预测。"""
from datetime import date, timedelta

import pytest

from analyzer.diagnostic import DiagnosticAnalyzer


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
