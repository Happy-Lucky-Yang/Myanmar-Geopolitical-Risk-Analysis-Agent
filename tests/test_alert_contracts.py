"""正式预警的持续性、缺测、幂等、审计与隔离回归。"""
from datetime import timedelta
import json

import pytest

from analyzer.alert_monitor import AlertMonitor
from utils.data_contract import business_date


def records(scores, end=None):
    end = end or business_date()
    return [{'date': (end - timedelta(days=len(scores) - i - 1)).isoformat(),
             'risk_score': value, 'run_kind': 'existing', 'algorithm_version': 'risk-v2',
             'sources': ['fixture'], 'sample_count': 3, 'region': 'MMR',
             'details': {'indicator_coverage': 0.55, 'data_status': 'partial'}}
            for i, value in enumerate(scores)]


def test_persistence_hysteresis_and_real_zero():
    monitor = AlertMonitor()
    assert monitor.evaluate([])['risk_score'] is None
    assert monitor.evaluate(records([85]))['level'] == 'unknown'
    assert monitor.evaluate(records([85, 86]))['level'] == 'red'
    assert monitor.evaluate(records([85, 86, 77, 77]))['level'] == 'red'
    assert monitor.evaluate(records([85, 86, 74]))['level'] == 'red'
    result = monitor.evaluate(records([85, 86, 74, 74]))
    assert result['level'] == 'orange'
    assert len(result['transitions']) == 2
    result = monitor.evaluate(records([0, 0]))
    assert result['level'] == 'green' and result['risk_score'] == 0


@pytest.mark.parametrize('change', [
    {'run_kind': 'manual'}, {'run_kind': 'demo'}, {'run_kind': 'legacy'},
    {'algorithm_version': 'risk-v1'}, {'risk_score': None}, {'risk_score': float('nan')},
    {'synthetic': True}, {'stale': True}, {'quality': 'estimated'}, {'sample_count': 0},
    {'details': {'indicator_coverage': 0.3}},
])
def test_invalid_day_cannot_bridge_continuity(change):
    monitor = AlertMonitor()
    rows = records([85, 85, 85])
    rows[1].update(change)
    assert monitor.evaluate(rows)['level'] == 'unknown'
    assert monitor.check_risk_score(99) is None


def test_stale_history_never_fires_or_claims_normal():
    monitor = AlertMonitor()
    old = business_date() - timedelta(days=10)
    rows = records([85, 85], old)
    result = monitor.evaluate(rows)
    assert result['level'] == 'unknown' and result['risk_score'] is None
    assert monitor.check_history(rows, end_date=old) == []
    assert not monitor.path.exists()


def test_idempotent_persistence_and_acknowledgement(tmp_path):
    monitor = AlertMonitor()
    rows = records([85, 85])
    alerts = monitor.check_history(rows)
    assert len(alerts) == 1
    assert monitor.path.is_relative_to(tmp_path)
    assert monitor.check_history(rows) == []
    aid = alerts[0]['id']
    assert monitor.acknowledge_alert(aid, 'analyst-fixture')
    assert monitor.acknowledge_alert(aid, 'other')
    saved = AlertMonitor().get_alert_history()[0]
    assert saved['acknowledged_by'] == 'analyst-fixture'
    assert len(saved['audit']) == 1
    assert saved['acknowledged_at'].endswith('+00:00')
    monitor._history = lambda *args: rows
    assert monitor.get_current_status()['active_alerts'] == 0
    assert monitor.get_current_status()['level'] == 'red'
    monitor.path.write_text('{broken', encoding='utf-8')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        monitor.check_history(rows)
    assert monitor.path.read_text(encoding='utf-8') == '{broken'


def test_read_does_not_persist_and_history_is_not_truncated():
    monitor = AlertMonitor()
    monitor._history = lambda *args: records([85, 85])
    assert monitor.get_current_status()['level'] == 'red'
    assert not monitor.path.exists()
    rows = [{'id': 'legacy-' + str(i), 'date': business_date().isoformat()} for i in range(105)]
    monitor._write_file(rows)
    monitor.check_history(records([85, 85]))
    assert len(monitor.get_alert_history(limit=None)) == 106
