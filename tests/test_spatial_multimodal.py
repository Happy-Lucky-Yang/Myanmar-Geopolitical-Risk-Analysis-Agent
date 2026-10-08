"""空间和多模态算法的固定夹具；只验数学与数据边界，不宣称真实准确率。"""
import numpy as np
import pytest
from shapely.geometry import box
from analyzer.spatial_analysis import aggregate_provinces, locate_event, morans_i
from analyzer.multimodal_aligner import MultimodalAligner


def regions():
    return [{'code': str(i), 'province': f'区域{i}', 'english': f'Region {i}',
             'geometry': box(i, 0, i + 1, 1)} for i in range(9)]


def test_boundary_location_requires_event_evidence():
    geo = regions()
    assert locate_event({'lat': .5, 'lon': .5}, geo) == ('0', 'point_in_boundary')
    assert locate_event({'lat': .5, 'lon': 1}, geo)[0] is None
    assert locate_event({'location': 'Region 0'}, geo)[0] is None
    assert locate_event({'event_location': 'Region 0', 'location_method': 'manual_verified'}, geo)[0] == '0'


def test_province_aggregation_keeps_missing_and_deduplicates():
    event = {'event_id': '1', 'lat': .5, 'lon': .5, 'date': '20260101', 'root_code': '19', 'quad_class': '4'}
    data = aggregate_provinces([event, dict(event), {**event, 'event_id': '2', 'lon': 20}],
                               7, '2026-01-07', regions=regions())
    assert data[0]['sample_count'] == 1 and data[0]['risk_score'] == 88
    assert data[1]['risk_score'] is None and data[1]['event_count'] is None
    assert data[0]['unlocated_total'] == 1


def test_moran_permutation_reproducible_and_zero_variance_guarded():
    values = [{'region': str(i), 'risk_score': i * 10} for i in range(9)]
    first = morans_i(values, regions())
    assert first == morans_i(values, regions())
    assert first['permutations'] == 999 and 0 < first['p_value'] <= 1
    zero = morans_i([{'region': str(i), 'risk_score': 0} for i in range(9)], regions())
    assert zero['moran_i'] is None and zero['p_value'] is None


def test_multimodal_month_calendar_missing_and_annual_exclusion():
    aligner = MultimodalAligner()
    rows = aligner.align_monthly(3, '2026-03-31', observations=[
        {'id': 'annual', 'indicator': 'nightlight', 'frequency': 'annual', 'quality': 'observed',
         'region': 'MMR', 'period_start': '2026-01-01', 'value': .7, 'source': 'official'}], news=[], events=[])
    assert [r['month'] for r in rows] == ['2026-01', '2026-02', '2026-03']
    assert all(r['nightlight'] is None and r['conflict_count'] is None for r in rows)
    assert aligner.compute_correlations(rows)['nightlight_vs_conflict'] is None


def test_correlations_require_independent_observations():
    aligner = MultimodalAligner()
    rows = [{'month': f'2025-{i + 1:02}', 'frequency': 'monthly', 'nightlight_delta': i,
             'conflict_count': i * 2, 'quality': {'nightlight_delta': 'derived', 'conflict_count': 'derived'},
             'observation_ids': {'nightlight_delta': str(i), 'conflict_count': str(i)}} for i in range(12)]
    assert aligner.compute_correlations(rows)['nightlight_vs_conflict'] == 1
    assert aligner.compute_correlations(rows[:11])['nightlight_vs_conflict'] is None
    for row in rows:
        row['observation_ids']['nightlight_delta'] = 'same-annual-observation'
    assert aligner.compute_correlations(rows)['sample_counts']['nightlight_vs_conflict'] == 1


def test_kde_absolute_scaling_and_duplicate_coordinates():
    from analyzer.event_density import EventDensityAnalyzer
    analyzer = EventDensityAnalyzer({'grid_rows': 30, 'grid_cols': 30, 'min_events': 2, 'bandwidth_km': 50})
    events = [{'event_id': str(i), 'date': '2026-01-01', 'lat': 16.85, 'lon': 96.2,
               'root_code': '19', 'quad_class': '4'} for i in range(4)]
    base = analyzer.compute(events[:2], days=7, end_date='2026-01-07')
    doubled = analyzer.compute(events, days=7, end_date='2026-01-07')
    assert base['degraded'] is None
    np.testing.assert_allclose(doubled['absolute_density'], base['absolute_density'] * 2)
    replayed = analyzer.compute(events + events, days=7, end_date='2026-01-07')
    np.testing.assert_allclose(replayed['absolute_density'], doubled['absolute_density'])
    assert base['bandwidth_km'] == doubled['bandwidth_km'] == 50


def monthly_observation(month=1, **changes):
    return {'id': f'obs-{month}', 'indicator': 'nightlight', 'frequency': 'monthly',
            'quality': 'observed', 'region': 'MMR', 'source': 'fixture',
            'period_start': f'2026-{month:02}-01', 'period_end': f'2026-{month + 1:02}-01',
            'value': month - 1, 'unit': 'nW/cm²/sr', 'dataset_version': 'v1', **changes}


@pytest.mark.parametrize('end,start,expected', [
    ('2026-03-31', None, ['2026-01', '2026-02', '2026-03']),
    ('2026-03-15', None, ['2026-01', '2026-02']),
    ('2026-03-31', '2026-02-15', ['2026-03']),
    ('2026-03-15', '2026-02-15', []),
])
def test_multimodal_requires_complete_months(end, start, expected):
    rows = MultimodalAligner().align_monthly(3, end, start_date=start, observations=[], news=[], events=[])
    assert [r['month'] for r in rows] == expected
    assert all(r['period_end_exclusive'] is True for r in rows)


@pytest.mark.parametrize('changes', [{'unit': None}, {'period_end': None}, {'period_end': '2026-01-31'},
                                     {'quality': 'estimated'}, {'run_kind': 'demo'}, {'run_kind': 'manual'}])
def test_multimodal_rejects_incomplete_or_non_observed_nightlight(changes):
    row = MultimodalAligner().align_monthly(1, '2026-01-31', news=[], events=[],
                                           observations=[monthly_observation(**changes)])[0]
    assert row['nightlight'] is None


@pytest.mark.parametrize('changes', [{}, {'source': 'other'}, {'unit': 'index'}, {'dataset_version': 'v2'}])
def test_multimodal_zero_and_sequence_identity(changes):
    observations = [monthly_observation(), monthly_observation(2, **changes)]
    rows = MultimodalAligner().align_monthly(2, '2026-02-28', news=[], events=[], observations=observations)
    assert rows[0]['nightlight'] == 0 and rows[0]['quality']['nightlight'] == 'observed'
    assert rows[1]['nightlight_delta'] == (None if changes else 1)


def test_multimodal_duplicate_observations_conflict_is_order_independent():
    aligner = MultimodalAligner()
    one = monthly_observation()
    def align(obs):
        return aligner.align_monthly(2, '2026-02-28', observations=obs, news=[], events=[])
    assert align([one, dict(one)])[0]['nightlight'] == 0
    for conflict in ({**one, 'value': 9}, monthly_observation(2, id=one['id'])):
        rows = align([one, conflict])
        assert rows == align([conflict, one])
        assert rows[0]['nightlight'] is None and rows[0]['quality']['nightlight'] == 'ambiguous'
        assert rows[0]['warnings']


def test_multimodal_reads_old_events_and_excludes_private_news(monkeypatch):
    from data.event_store import get_event_store
    from analyzer.data_loader import get_data_loader
    event = {'event_id': 'old', 'date': '20260110', 'root_code': '01', 'source': 'fixture'}
    get_event_store().append([event])
    news = [{'content': mode, 'pub_time': '2026-01-01', 'sentiment_score': .8,
             'run_kind': mode, 'source': 'fixture'} for mode in ['manual', 'demo', 'legacy']]
    news.append({'content': '正式', 'published_at': '2025-12-31T20:00:00Z',
                 'sentiment_score': 0, 'run_kind': 'existing', 'source': 'fixture'})
    monkeypatch.setattr(get_data_loader(), 'load_raw_news', lambda: news)
    rows = MultimodalAligner().align_monthly(3, '2026-03-31', observations=[])
    assert rows[0]['conflict_count'] == 0 and rows[0]['gdelt_events'] == 1
    assert rows[0]['sentiment_avg'] == 1 and rows[0]['sample_count']['sentiment_articles'] == 1
    unknown = MultimodalAligner().align_monthly(1, '2026-01-31', observations=[], news=[],
                                               events=[{**event, 'root_code': None}])[0]
    assert unknown['conflict_count'] is None and unknown['gdelt_events'] == 1


@pytest.mark.parametrize('positive,risk', [(0, 1), (1, 0), (.25, .75), (None, None),
                                          (float('nan'), None), (-.1, None), (1.1, None)])
def test_multimodal_sentiment_uses_risk_direction(positive, risk):
    news = [{'content': '正式情感夹具', 'pub_time': '2026-01-10', 'source': 'fixture',
             'run_kind': 'existing', 'sentiment_score': positive}]
    row = MultimodalAligner().align_monthly(1, '2026-01-31', observations=[], events=[], news=news)[0]
    assert row['sentiment_avg'] == risk
    assert row['quality']['sentiment_avg'] == ('missing' if risk is None else 'derived')
    assert row['units']['sentiment_avg'] == '情感风险指数0～1'


def test_multimodal_correlation_does_not_mix_series():
    rows = [{'month': f'2025-{i + 1:02}', 'frequency': 'monthly', 'nightlight_delta': i,
             'conflict_count': i * 2, 'nightlight_series_key': 'a' if i < 6 else 'b',
             'quality': {'nightlight_delta': 'derived', 'conflict_count': 'derived'},
             'observation_ids': {'nightlight_delta': str(i), 'conflict_count': str(i)}} for i in range(12)]
    result = MultimodalAligner().compute_correlations(rows)
    assert result['nightlight_vs_conflict'] is None
    assert result['sample_counts']['nightlight_vs_conflict'] == 12
    assert '不一致' in result['reasons']['nightlight_vs_conflict']


def test_annual_observations_keep_granularity_versions_and_filters():
    aligner = MultimodalAligner()
    row = {'indicator': 'gdp_growth', 'frequency': 'annual', 'source': 'fixture', 'run_kind': 'existing',
           'region': 'MMR', 'value': 0, 'quality': 'observed', 'unit': '%', 'dataset_version': 'v1',
           'period_start': '2025-01-01', 'period_end': '2026-01-01'}
    observations = [row, dict(row), {**row, 'dataset_version': 'v2', 'value': 1},
                    {**row, 'unit': None}, {**row, 'run_kind': 'demo'}]
    result = aligner.align_annual(365, '2025-12-31', observations=observations)
    assert len(result['observations']) == 2 and result['invalid_count'] == 1
    assert result['observations'][0]['value'] == 0
    assert all(r['frequency'] == 'annual' for r in result['observations'])
    assert not aligner.align_annual(364, '2025-12-31', observations=observations)['observations']
    assert not aligner.align_annual(365, '2025-12-31', source='other', observations=observations)['observations']
    assert not aligner.align_annual(365, '2025-12-31', region='r1', observations=observations)['observations']
    months = aligner.align_monthly(12, '2025-12-31', observations=observations, news=[], events=[])
    assert all(r['nightlight'] is None for r in months)


def test_low_risk_value_is_not_interpreted_as_fraction():
    from visualization.map_gen import RiskMapGenerator
    generator = RiskMapGenerator()
    assert generator._normalize_score(1) == .01
    assert generator._normalize_score(0) == 0
    with pytest.raises(ValueError):
        generator._normalize_score(None)
