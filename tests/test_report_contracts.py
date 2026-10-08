"""报告读取已存数据并复用快照；不触网，不把缺测当当前评分。"""
import io

import pytest

from analyzer.report_generator import ReportGenerator, SnapshotChanged
from analyzer.data_loader import DataLoader
from data.event_store import get_event_store


def seed():
    loader = DataLoader()
    loader.append_risk_score('2026-01-01', 0, '低风险', run_kind='existing', sample_count=2,
                             sources=['fixture'], details={'indicator_scores': {
                                 'conflict_frequency': {'contribution': 0, 'value': 0, 'weight': 1}}})
    get_event_store().append([{'event_id': 'fixture', 'date': '20260101', 'run_kind': 'existing',
        'source': 'fixture', 'lat': 16.9, 'lon': 96.2, 'root_code': '19',
        'location': '<script>alert(1)</script>', 'source_url': 'https://example.test/evidence'}])
    return loader


def test_report_same_snapshot_formats_and_escaping():
    seed()
    generator = ReportGenerator()
    snapshot = generator.build_snapshot(days=2, end_date='2026-01-02')
    assert snapshot['metadata']['valid_days'] == 1
    assert snapshot['metadata']['coverage'] == .5
    assert snapshot['metadata']['stale']
    context = generator._build_report_context(snapshot)
    assert context['risk_summary']['score'] == '无当前观测'
    assert context['forecast']['forecast'] == []
    assert context['daily_values'] == [('2026-01-01', 0), ('2026-01-02', None)]
    html = generator.generate_html_report(snapshot=snapshot)
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>alert(1)</script>' not in html
    assert 'World Bank' not in html and 'VIIRS' not in html
    assert snapshot['snapshot_id'] in html and 'fixture' in html
    from docx import Document
    document = Document(io.BytesIO(generator.generate_docx_report(snapshot=snapshot)))
    paragraphs = '\n'.join(p.text for p in document.paragraphs)
    assert snapshot['snapshot_id'] in paragraphs and 'fixture' in paragraphs
    assert '2026-01-02: 缺测' in paragraphs


def test_source_filter_does_not_relabel_national_score():
    seed()
    snapshot = ReportGenerator().build_snapshot(days=2, end_date='2026-01-02', source='other')
    assert snapshot['history'] == snapshot['events'] == []
    assert snapshot['warnings']


def test_revision_changes_on_event_and_observation_updates(tmp_path):
    loader = seed()
    revision = loader.revision()
    get_event_store().append([{'event_id': 'second', 'date': '20260101'}])
    assert loader.revision() != revision
    with pytest.raises(SnapshotChanged):
        ReportGenerator().build_snapshot(expected_revision=revision)
    revision = loader.revision()
    from pathlib import Path
    (Path(loader._external_dir) / 'indicator_observations.jsonl').write_text('{}\n', encoding='utf-8')
    assert loader.revision() != revision


def test_api_report_bad_format_and_stale_revision():
    import app
    client = app.app.test_client()
    assert client.get('/api/report?format=unknown').status_code == 400
    assert client.get('/api/report?revision=outdated').status_code == 409
    response = client.get('/api/report?format=json&days=2&end_date=2026-01-02')
    assert response.status_code == 200
    assert response.json['data']['metadata']['valid_days'] == 0


@pytest.mark.parametrize('days', [30, 60, 90, 30])
def test_trend_chart_null_days_and_report_revision(days):
    seed()
    from app import app
    client = app.test_client()
    trend = client.get(f'/api/trend?days={days}&end_date=2026-01-02')
    assert trend.status_code == 200
    data = trend.json['data']
    assert data['history'][-2:] == [0, None]
    assert data['chart_data']['series'][0]['data'][-2:] == [0, None]
    assert len(data['dates']) == days
    assert data['report_available']
    report = client.get('/api/report', query_string={**data['filters'],
                         'format': 'json', 'revision': data['revision']})
    assert report.status_code == 200
    assert report.json['data']['history'][0]['risk_score'] == 0
    get_event_store().append([{'event_id': 'new', 'date': '20260102'}])
    assert client.get('/api/report', query_string={'revision': data['revision']}).status_code == 409


def test_trend_revision_race_and_source_isolation(monkeypatch):
    seed()
    import app
    from analyzer.data_loader import get_data_loader
    client = app.app.test_client()
    data = client.get('/api/trend?source=fixture&end_date=2026-01-01').json['data']
    assert all(v is None for v in data['history']) and data['warnings']
    assert client.get('/api/report?domain=legacy').status_code == 400
    assert not client.get('/api/trend?domain=legacy').json['data']['report_available']
    revisions = iter(['before', 'after'])
    monkeypatch.setattr(get_data_loader(), 'revision', lambda: next(revisions))
    assert client.get('/api/trend').status_code == 409


def test_chart_empirical_bounds_and_zero_values():
    from visualization.chart_gen import TrendChartGenerator
    chart = TrendChartGenerator().generate_trend_data(
        ['2026-01-01', '2026-01-02'], [None, 0], moving_avg=[None], forecast=[1, 2],
        forecast_meta={'interval': {'lower': [0, 0], 'upper': [3, 4], 'label': '残差经验区间'}})
    assert len(chart['series']) == 5
    assert chart['series'][0]['data'] == [None, 0, None, None]
    assert chart['series'][2]['data'] == [None, 0, 1, 2]
    assert chart['series'][3]['data'] == [None, None, 0, 0]
    assert all(len(s['data']) == len(chart['xAxis']) for s in chart['series'])
