"""以事件发生地证据匹配行政边界；没有样本的省份保持缺测。"""
import math
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


WEIGHT_KINDS = ('queen', 'distance_threshold', 'distance_decay')


def _region_centroids(selected):
    """取各单元代表点作为质心，返回 [(lat, lon), ...]。"""
    pts = []
    for r in selected:
        p = r['geometry'].representative_point()
        pts.append((p.y, p.x))
    return pts


def _haversine_km(a, b):
    """两点球面大圆距离（km），R=6371，与地缘位势模块口径一致。"""
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def spatial_weights(selected, kind='queen'):
    """构建行标准化空间权重矩阵（组长模块二(三)的三种定义）。

    kind:
      queen            行政边界相接（含仅共点）取 1
      distance_threshold 质心距离 ≤ 阈值取 1，阈值=全部最近邻距离的中位数
      distance_decay   w(i,j)=1/d(i,j)²，与地缘位势衰减设定一致
    返回 (W_row_standardized, raw, row_sum_raw)。验收断言(1)：非孤立行标准化后和为 1（容差 1e-6）。
    """
    n = len(selected)
    raw = np.zeros((n, n))
    if kind == 'queen':
        for i in range(n):
            for j in range(i + 1, n):
                if selected[i]['geometry'].touches(selected[j]['geometry']):
                    raw[i, j] = raw[j, i] = 1.0
    elif kind in ('distance_threshold', 'distance_decay'):
        cents = _region_centroids(selected)
        dist = np.zeros((n, n))
        for i in range(n):
            for j in range(i + 1, n):
                dist[i, j] = dist[j, i] = _haversine_km(cents[i], cents[j])
        if kind == 'distance_threshold':
            nn = [dist[i][dist[i] > 0].min() for i in range(n) if (dist[i] > 0).any()]
            threshold = float(np.median(nn)) if nn else 0.0
            for i in range(n):
                for j in range(n):
                    if i != j and dist[i, j] <= threshold:
                        raw[i, j] = 1.0
        else:
            for i in range(n):
                for j in range(n):
                    if i != j and dist[i, j] > 0:
                        raw[i, j] = 1.0 / (dist[i, j] ** 2)
    else:
        raise ValueError(f"未知空间权重矩阵类型: {kind}")
    row_sum = raw.sum(axis=1)
    W = np.divide(raw, row_sum[:, None], out=np.zeros_like(raw), where=row_sum[:, None] > 0)
    # 验收断言(1)：行标准化后非孤立行之和为 1
    for i in range(n):
        if row_sum[i] > 0:
            assert abs(W[i].sum() - 1.0) <= 1e-6, f"第{i}行标准化后和须为1"
    return W, raw, row_sum


def _prepare_spatial(province_data, regions, kind='queen'):
    """按 province_data 出现顺序选取有效单元（去重），返回 (codes, values, W, row_sum, selected)。"""
    regions = province_shapes() if regions is None else regions
    region_by_code = {r['code']: r for r in regions}
    value_by_code, order = {}, []
    for row in province_data:
        if not isinstance(row, dict):
            continue
        code = row.get('region')
        if code in region_by_code and finite_number(row.get('risk_score')):
            if code not in value_by_code:
                order.append(code)
            value_by_code[code] = row['risk_score']
    selected = [region_by_code[c] for c in order]
    values = np.array([value_by_code[c] for c in order], dtype=float)
    W, _raw, row_sum = spatial_weights(selected, kind) if selected else (np.zeros((0, 0)), None, np.zeros(0))
    return order, values, W, row_sum, selected


def _moran_statistic(z, W):
    """全局 Moran's I = (n/ΣW)·(z'Wz)/(z'z)；零方差或无邻接返回 None。"""
    s = float(W.sum())
    zz = float(z @ z)
    if s <= 1e-12 or zz <= 1e-12:
        return None
    return float(len(z) / s * (z @ W @ z) / zz)


def morans_i(province_data, regions=None, permutations=999, seed=42, weight_kind='queen'):
    """全局 Moran's I（行标准化权重 + 固定种子置换检验）。

    单侧检验（研究假设为存在正空间自相关）；同时报告观测值 I、期望值 −1/(n−1)、置换 p 值。
    验收断言(3)：输入整体逆序时 I 不变，作为实现正确性自检。
    """
    codes, values, weights, row_sum, selected = _prepare_spatial(province_data, regions, weight_kind)
    n = len(selected)
    meta = {'sample_count': n, 'permutations': permutations, 'seed': seed,
            'weights': f'{weight_kind}_row_standardized', 'weight_kind': weight_kind,
            'algorithm_version': 'moran-v3', 'interpretation': '描述性空间相关，不代表因果关系'}
    if n < 8:
        return {**meta, 'status': 'insufficient', 'moran_i': None, 'p_value': None, 'reason': '至少需要8个有效区域'}
    centered = values - values.mean()
    if centered @ centered <= 1e-12 or weights.sum() == 0:
        return {**meta, 'status': 'insufficient', 'moran_i': None, 'p_value': None, 'reason': '零方差或无邻接区域'}
    observed = _moran_statistic(centered, weights)
    # 验收断言(3)：整体逆序自检（同时逆序 z 与 W，二次型不变）
    reversed_i = _moran_statistic(centered[::-1], weights[::-1, ::-1])
    assert reversed_i is not None and abs(reversed_i - observed) <= 1e-9, "Moran's I 逆序不变性自检失败"
    expected = -1 / (n - 1)
    rng = np.random.default_rng(seed)
    samples = [_moran_statistic(rng.permutation(centered), weights) for _ in range(permutations)]
    p = (1 + sum(abs(s - expected) >= abs(observed - expected) for s in samples if s is not None)) / (permutations + 1)
    return {**meta, 'status': 'ok', 'moran_i': observed, 'p_value': p, 'expected_i': expected,
            'isolated_regions': int((row_sum == 0).sum())}


def lisa(province_data, regions=None, permutations=999, seed=42, weight_kind='queen', alpha=0.05):
    """局部 Moran's I（LISA），按 z(i) 与邻域加权值正负分四类（组长模块二(一)）。

    I(i)=z(i)·Σ_j w(i,j)z(j)，z(i)=(x(i)−x̄)/σ。HH=热点、LL=冷点、HL=孤立高值、LH=低值包围中的高值洼地。
    逐单元置换伪 p 值，p≥alpha 者 significant=False（可视化置灰）。
    """
    codes, values, W, row_sum, selected = _prepare_spatial(province_data, regions, weight_kind)
    n = len(selected)
    meta = {'sample_count': n, 'permutations': permutations, 'seed': seed, 'alpha': alpha,
            'weights': f'{weight_kind}_row_standardized', 'weight_kind': weight_kind,
            'algorithm_version': 'lisa-v1', 'interpretation': '局部空间关联，描述性，不代表因果'}
    if n < 8:
        return {**meta, 'status': 'insufficient', 'units': [], 'reason': '至少需要8个有效区域'}
    sigma = float(values.std())
    if sigma <= 1e-12 or W.sum() == 0:
        return {**meta, 'status': 'insufficient', 'units': [], 'reason': '零方差或无邻接区域'}
    z = (values - values.mean()) / sigma
    lag = W @ z
    local_i = z * lag
    rng = np.random.default_rng(seed)
    counts = np.zeros(n)
    for _ in range(permutations):
        zp = rng.permutation(z)
        ip = zp * (W @ zp)
        counts += (np.abs(ip) >= np.abs(local_i))
    p = (1 + counts) / (permutations + 1)
    units = []
    for i in range(n):
        if z[i] > 0 and lag[i] > 0:
            cls = 'HH'
        elif z[i] < 0 and lag[i] < 0:
            cls = 'LL'
        elif z[i] > 0:
            cls = 'HL'
        else:
            cls = 'LH'
        sig = bool(p[i] < alpha)
        units.append({'region': codes[i], 'local_moran_i': float(local_i[i]), 'z': float(z[i]),
                      'spatial_lag': float(lag[i]), 'cluster': cls, 'p_value': float(p[i]),
                      'significant': sig, 'color': cls if sig else 'gray',
                      'isolated': bool(row_sum[i] == 0)})
    return {**meta, 'status': 'ok', 'units': units,
            'significant_count': int(sum(u['significant'] for u in units))}


def getis_ord_gi_star(province_data, regions=None, weight_kind='queen', z_threshold=1.96):
    """Getis-Ord Gi*（组长模块二(一)公式），按 z 阈值判读显著热点/冷点。

    G(i)*=[Σ_j w(i,j)x(j) − X̄·Σ_j w(i,j)] / [S·((n·Σ_j w(i,j)² − (Σ_j w(i,j))²)/(n−1))^(1/2)]，
    G>1.96 为 0.05 水平显著热点，<−1.96 为显著冷点。
    """
    codes, values, W, row_sum, selected = _prepare_spatial(province_data, regions, weight_kind)
    n = len(selected)
    meta = {'sample_count': n, 'weights': f'{weight_kind}_row_standardized', 'weight_kind': weight_kind,
            'z_threshold': z_threshold, 'algorithm_version': 'gistar-v1',
            'interpretation': 'Gi* 近似正态 z 分数，描述性热点/冷点，不代表因果'}
    if n < 8:
        return {**meta, 'status': 'insufficient', 'units': [], 'reason': '至少需要8个有效区域'}
    x_bar = float(values.mean())
    S = float(values.std())
    if S <= 1e-12:
        return {**meta, 'status': 'insufficient', 'units': [], 'reason': '零方差'}
    units = []
    for i in range(n):
        w_i = W[i]
        sum_w = float(w_i.sum())
        sum_wx = float(w_i @ values)
        sum_w2 = float((w_i ** 2).sum())
        inner = (n * sum_w2 - sum_w ** 2) / (n - 1)
        gi = (sum_wx - x_bar * sum_w) / (S * math.sqrt(inner)) if inner > 1e-12 else None
        units.append({'region': codes[i], 'gi_star': gi,
                      'hotspot': bool(gi is not None and gi > z_threshold),
                      'coldspot': bool(gi is not None and gi < -z_threshold),
                      'isolated': bool(row_sum[i] == 0)})
    return {**meta, 'status': 'ok', 'units': units,
            'hotspots': [u['region'] for u in units if u['hotspot']],
            'coldspots': [u['region'] for u in units if u['coldspot']]}


def spatial_autocorrelation_report(province_data, regions=None, permutations=999, seed=42, alpha=0.05):
    """三种权重矩阵下的全局 Moran's I 敏感性 + 主口径（queen）LISA/Gi*（组长模块二(三)(四)）。

    若三种口径结论方向一致（同显著/同不显著），以 queen 为主口径；否则并列呈报并标注需说明差异。
    """
    per_matrix = {}
    for kind in WEIGHT_KINDS:
        per_matrix[kind] = morans_i(province_data, regions, permutations, seed, weight_kind=kind)
    oks = [per_matrix[k] for k in WEIGHT_KINDS if per_matrix[k]['status'] == 'ok']
    if not oks:
        reason = next((per_matrix[k].get('reason') for k in WEIGHT_KINDS
                       if per_matrix[k].get('reason')), '有效区域不足或零方差')
        return {'status': 'insufficient', 'algorithm_version': 'spatial-report-v1',
                'by_matrix': per_matrix, 'reason': reason}
    sig_flags = [bool(r['p_value'] < alpha) for r in oks]
    consistent = all(sig_flags) or not any(sig_flags)
    primary = per_matrix['queen']
    result = {'status': 'ok', 'algorithm_version': 'spatial-report-v1', 'alpha': alpha,
              'seed': seed, 'permutations': permutations, 'by_matrix': per_matrix,
              'direction_consistent': consistent,
              'primary_weight': 'queen',
              'note': ('三口径结论方向一致，以 queen 邻接为主口径' if consistent
                       else '三口径结论方向不一致，须并列呈报并说明差异来源，不得只择其一')}
    if primary['status'] == 'ok':
        result['lisa'] = lisa(province_data, regions, permutations, seed, 'queen', alpha)
        result['gi_star'] = getis_ord_gi_star(province_data, regions, 'queen')
    return result
