"""以事件发生地证据匹配行政边界；没有样本的省份保持缺测。"""
from functools import lru_cache
from collections import defaultdict

import numpy as np
from shapely.geometry import shape, Point
from utils.data_contract import business_date, finite_number, fingerprint, time_window


@lru_cache(maxsize=1)
def province_shapes():
    from data.admin_boundaries import get_province_features, PROVINCE_EN2CN
    result = []
    for feature in get_province_features():
        props = feature['properties']
        english = props.get('NAME_1', '')
        geometry = shape(feature['geometry'])
        if not geometry.is_valid:
            from shapely import make_valid
            geometry = make_valid(geometry)
        result.append({'code': props['GID_1'], 'province': PROVINCE_EN2CN.get(english, english),
                       'english': english, 'geometry': geometry})
    return result


def unique_events(events, days=None, end_date=None, source=None):
    window = time_window(days, end_date) if days is not None else None
    seen, result = set(), []
    for event in events:
        if not isinstance(event, dict) or event.get('run_kind', 'existing') not in {'live', 'existing'}:
            continue
        day = business_date(event.get('date'))
        if day is None or (window and not window[0] <= day < window[1]):
            continue
        if source and event.get('source', 'gdelt') != source:
            continue
        external = event.get('event_id') or event.get('global_event_id')
        key = fingerprint([event.get('source', 'gdelt'), str(external)]) if external else fingerprint(event)
        if key not in seen:
            seen.add(key)
            result.append({**event, 'date': day.isoformat()})
    return result


def locate_event(event, regions=None):
    regions = province_shapes() if regions is None else regions
    lat, lon = event.get('lat'), event.get('lon')
    if finite_number(lat) and finite_number(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
        matches = [r for r in regions if r['geometry'].covers(Point(lon, lat))]
        return (matches[0]['code'], 'point_in_boundary') if len(matches) == 1 else (None, 'ambiguous_or_outside')
    # 只接受明确的事件发生地字段，不扫描文章中的任意地名。
    place = event.get('event_location')
    if isinstance(place, str) and event.get('location_method') in {'manual_verified', 'explicit_event_location'}:
        matches = [r for r in regions if place.strip().casefold() in
                   {r['code'].casefold(), r['province'].casefold(), r['english'].casefold()}]
        if len(matches) == 1:
            return matches[0]['code'], event['location_method']
    return None, 'unlocated'


def aggregate_provinces(events, days=30, end_date=None, source=None, regions=None):
    from data.gdelt_files import event_severity_weight
    regions = province_shapes() if regions is None else regions
    selected = unique_events(events, days, end_date, source)
    grouped, unlocated = defaultdict(list), 0
    for event in selected:
        code, method = locate_event(event, regions)
        if code is None:
            unlocated += 1
        else:
            grouped[code].append({**event, 'location_method': method})
    result = []
    for region in regions:
        samples = grouped[region['code']]
        # CAMEO规则烈度均值是描述性指标，不是经过校准的地区发生概率。
        scores = [event_severity_weight(e) * 100 for e in samples if str(e.get('root_code', '')) in
                  {f'{i:02}' for i in range(1, 21)}]
        score = round(float(np.mean(scores)), 2) if scores else None
        representative = region['geometry'].representative_point()
        result.append({'province': region['province'], 'region': region['code'],
                       'lat': representative.y, 'lon': representative.x, 'risk_score': score,
                       'risk_level': '无数据' if score is None else '高' if score >= 70 else '中' if score >= 40 else '低',
                       'metric_label': '已定位事件规则烈度均值', 'sample_count': len(samples),
                       'scored_count': len(scores), 'event_count': len(samples) if samples else None,
                       'sources': sorted({e.get('source', 'gdelt') for e in samples}),
                       'location_methods': sorted({e['location_method'] for e in samples}),
                       'valid_days': len({e['date'] for e in samples}), 'requested_days': days,
                       'data_status': 'observed_events' if samples else 'missing',
                       'algorithm_version': 'event-severity-v2', 'boundary_version': 'GADM-4.1',
                       'events': samples, 'unlocated_total': unlocated})
    return result


def morans_i(province_data, regions=None, permutations=999, seed=42):
    regions = province_shapes() if regions is None else regions
    by_code = {r['region']: r for r in province_data if finite_number(r.get('risk_score'))}
    selected = [r for r in regions if r['code'] in by_code]
    n = len(selected)
    meta = {'sample_count': n, 'permutations': permutations, 'seed': seed, 'weights': 'queen_row_standardized',
            'algorithm_version': 'moran-v2', 'interpretation': '描述性空间相关，不代表因果关系'}
    if n < 8:
        return {**meta, 'status': 'insufficient', 'moran_i': None, 'p_value': None, 'reason': '至少需要8个有效区域'}
    values = np.array([by_code[r['code']]['risk_score'] for r in selected], dtype=float)
    centered = values - values.mean()
    weights = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            if selected[i]['geometry'].touches(selected[j]['geometry']):
                weights[i, j] = weights[j, i] = 1
    row_sum = weights.sum(axis=1)
    weights = np.divide(weights, row_sum[:, None], out=np.zeros_like(weights), where=row_sum[:, None] > 0)
    if centered @ centered <= 1e-12 or weights.sum() == 0:
        return {**meta, 'status': 'insufficient', 'moran_i': None, 'p_value': None, 'reason': '零方差或无邻接区域'}
    def statistic(z):
        return float(n / weights.sum() * (z @ weights @ z) / (z @ z))
    observed = statistic(centered)
    expected = -1 / (n - 1)
    rng = np.random.default_rng(seed)
    samples = [statistic(rng.permutation(centered)) for _ in range(permutations)]
    p = (1 + sum(abs(s - expected) >= abs(observed - expected) for s in samples)) / (permutations + 1)
    return {**meta, 'status': 'ok', 'moran_i': observed, 'p_value': p, 'expected_i': expected,
            'isolated_regions': int((row_sum == 0).sum())}
