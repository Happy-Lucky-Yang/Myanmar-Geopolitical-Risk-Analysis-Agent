"""九指标月/年面板纯计算：正式与代理分轨，标准化基准显式冻结。"""
import math
from datetime import date, timedelta
from utils.data_contract import finite_number, fingerprint
from utils.risk_contract import pure_day, periods, VERSIONS
from analyzer.geo_indicators import (DEFAULT_WEIGHTS_V3, INDICATOR_NAMES, NEIGHBORS,
    WESTERN, canon_country, CONFLICT_ROOTS, event_severity_weight)

NEGATIVE = {'china_myanmar_relation', 'neighbor_relation', 'western_relation', 'nightlight_change'}


def metric(raw=None, source='', method='', unit='', quality='missing', reason='', version=''):
    if raw is not None and not finite_number(raw):
        raw, quality, reason = None, 'missing', '指标非有限，禁止合成'
    return {'raw': raw, 'source': source, 'method': method, 'unit': unit,
            'quality': quality, 'reason': reason, 'dataset_version': version}


def select_batch(batches, source, start, end, region='MMR', ids=None):
    options = [b for b in batches if b['source'] == source and b['manifest']['start'] <= start
        and b['manifest']['end'] >= end and b['manifest']['region'] in {'MMR', region}
        and (not ids or b['id'] in ids)]
    if len(options) != 1:
        return None, '来源未覆盖当前周期' if not options else '多个来源版本/批次重叠，需显式选择batch_ids'
    return options[0], ''


def raw_panel(start, end, frequency, region, batches, gdelt, parameters=None, mode='formal', batch_ids=None):
    parameters = parameters or {}
    if not isinstance(parameters,dict) or set(parameters)-{'lambda','mu','approved_version'}:
        raise ValueError('指标参数字段无效')
    for key in ('lambda','mu'):
        if key in parameters and (not isinstance(parameters[key],dict) or any(not finite_number(v) or v<0 for v in parameters[key].values())):
            raise ValueError('烈度和制裁系数必须为非负有限数')
    approved = isinstance(parameters.get('approved_version'),str) and bool(parameters['approved_version'].strip())
    if mode not in VERSIONS or frequency not in {'monthly', 'annual'}:
        raise ValueError('无效面板模式或周期')
    if (start, end) not in periods(frequency, 1, (pure_day(end)-timedelta(days=1)).isoformat()):
        raise ValueError('面板须为完整自然月/年')
    from analyzer.spatial_analysis import unique_events, locate_event
    events = unique_events(gdelt, (pure_day(end)-pure_day(start)).days,
                           (pure_day(end)-timedelta(days=1)).isoformat(), source='gdelt')
    evs = events if region == 'MMR' else [e for e in events if locate_event(e)[0] == region]
    values = {key: metric(reason='尚无可用数据') for key in DEFAULT_WEIGHTS_V3}
    def relation(targets):
        nets = []
        for target in sorted(targets):
            rows = [e for e in evs if {canon_country(e.get('actor1_country')), canon_country(e.get('actor2_country'))} == {'MMR', target}]
            valid = [(e['goldstein'], e['num_mentions']) for e in rows if finite_number(e.get('goldstein'))
                and -10 <= e['goldstein'] <= 10 and finite_number(e.get('num_mentions')) and e['num_mentions'] > 0]
            if valid:
                scale = max(m for _,m in valid)
                weighted = [(g,m/scale) for g,m in valid]
                nets.append((target,sum(g*m for g,m in weighted)/sum(m for _,m in weighted)))
        return (sum(v for _,v in nets)/len(nets) if nets else None), [k for k,_ in nets]
    for key, targets in [('china_myanmar_relation', {'CHN'}), ('neighbor_relation', NEIGHBORS),
                         ('western_relation', WESTERN-{'GBR','EUU'})]:
        net, observed_targets = relation(targets)
        n = len(observed_targets)
        values[key] = metric(net, 'gdelt', 'country_pair_net_then_equal_mean:'+','.join(observed_targets), 'Goldstein',
            'derived' if n==len(targets) else 'partial' if n else 'missing', f'有效国家对{n}/{len(targets)}，缺少对象不假定友好', 'GDELT')
    rivalry = [e for e in events if {canon_country(e.get('actor1_country')), canon_country(e.get('actor2_country'))} == {'CHN', 'USA'}
        and finite_number(e.get('goldstein')) and -10 <= e['goldstein'] < 0
        and finite_number(e.get('num_mentions')) and e['num_mentions'] > 0
        and (e.get('action_country') in {'BM', 'MMR'} or e.get('country_code') in {'BM', 'MMR'}
             or ('Myanmar' in str(e.get('location', ''))) or ('缅甸' in str(e.get('location', '')))
             or locate_event(e)[0] is not None)]
    bg = sum(abs(e['goldstein'])*(1+math.log(e['num_mentions'])) for e in rivalry) if rivalry else None
    values['china_us_rivalry'] = metric(bg, 'gdelt', 'negative_goldstein_log_mentions', 'intensity',
                                       'derived' if bg is not None else 'missing', '全国博弈背景，不当作省级事件计数', 'GDELT')
    used = []
    acled, reason = select_batch(batches, 'acled', start, end, region, batch_ids)
    if acled and acled['manifest']['complete']:
        used.append(acled['id'])
        from data.risk_sources import ACLED_TYPES
        rows = [r for r in acled['records'] if start <= r['date'] < end and r['event_type'] in ACLED_TYPES]
        if region != 'MMR':
            rows = [r for r in rows if r.get('region') == region]
            region_covered = region in acled['manifest'].get('admin1_mapping', {}).values()
        else:
            region_covered = True
        version = acled['manifest']['dataset_version']
        if region_covered:
            values['conflict_frequency'] = metric(len(rows), 'acled', 'armed_event_count', 'events', 'observed', version=version)
            coeff = parameters.get('lambda', {})
            if not approved or not all(finite_number(coeff.get(t)) and coeff[t] >= 0 for t in ACLED_TYPES):
                values['event_severity'] = metric(source='acled', reason='blocked_parameters：烈度系数λ未审定', version=version)
            elif not rows or any(r.get('fatalities') is None for r in rows):
                values['event_severity'] = metric(source='acled', reason='无冲突或有未报告死亡人数，严重度不能补0', version=version)
            else:
                se = sum(coeff[r['event_type']]*math.log1p(r['fatalities']) for r in rows)/len(rows)
                values['event_severity'] = metric(se, 'acled', 'mean_lambda_log_fatalities', 'intensity', 'derived', version=version)
    else:
        values['conflict_frequency']['reason'] = reason or 'ACLED覆盖不完整'
        values['event_severity']['reason'] = reason or 'ACLED覆盖不完整'
    if mode == 'proxy' and values['conflict_frequency']['raw'] is None:
        conflict = [e for e in evs if str(e.get('root_code')) in CONFLICT_ROOTS]
        # 无记录不推断零冲突；固定CAMEO计数和规则烈度仅为代理。
        if evs:
            values['conflict_frequency'] = metric(len(conflict), 'gdelt', 'cameo_conflict_record_count', 'records', 'proxy', version='GDELT')
            if conflict:
                values['event_severity'] = metric(sum(event_severity_weight(e) for e in conflict)/len(conflict),
                    'gdelt', 'cameo_rule_severity_mean', 'rule_index', 'proxy', '不是死亡人数测度', 'GDELT')
    ofac, reason = select_batch(batches, 'ofac', start, end, 'MMR', batch_ids)
    if ofac:
        used.append(ofac['id'])
        rows = [r for r in ofac['records'] if r['change'] in {'addition','redesignation'} and
                r.get('date') and start <= r['date'] < end]
        mu = parameters.get('mu', {})
        if not ofac['manifest']['complete'] or any(not r['reviewed'] for r in ofac['records']):
            reason = 'OFAC覆盖不完整或存在待核验措施'
        elif not approved or not mu or any(not finite_number(mu.get(r['measure_type'])) or mu[r['measure_type']] < 0 for r in rows):
            reason = 'blocked_parameters：制裁系数μ未审定'
        else:
            values['intl_sanction'] = metric(sum(mu[r['measure_type']] for r in rows), 'ofac',
                'new_legal_measures_weighted', 'intensity', 'derived', '仅OFAC，欧盟尚未接入', ofac['manifest']['dataset_version'])
    values['intl_sanction']['reason'] = values['intl_sanction']['reason'] or reason
    if mode == 'proxy' and values['intl_sanction']['raw'] is None:
        from analyzer.geo_indicators import GREAT_POWERS
        sanction = [e for e in evs if str(e.get('root_code')) == '17' and
            'MMR' in {canon_country(e.get('actor1_country')), canon_country(e.get('actor2_country'))}
            and {canon_country(e.get('actor1_country')), canon_country(e.get('actor2_country'))} & GREAT_POWERS]
        if sanction:
            values['intl_sanction'] = metric(len(sanction), 'gdelt', 'cameo_coercion_proxy', 'records', 'proxy', '胁迫报道并非法定制裁措施', 'GDELT')
    def observation(source, indicator, a, b):
        src, why = select_batch(batches, source, a, b, region, batch_ids)
        if not src:
            return None, why, ''
        used.append(src['id'])
        found = [r for r in src['records'] if r.get('indicator') == indicator and r.get('region') == region
                 and r.get('period_start') == a and r.get('period_end') == b]
        if len({fingerprint(r) for r in found}) != 1:
            return None, '缺少同地区完整周期原值或存在冲突版本', src['manifest']['dataset_version']
        return found[0], '', src['manifest']['dataset_version']
    def brightness(a, b):
        if frequency == 'monthly':
            return observation('viirs', 'nightlight', a, b)
        months = periods('monthly', 12, (pure_day(b)-timedelta(days=1)).isoformat())
        rows = [observation('viirs', 'nightlight', x, y)[0] for x,y in months]
        if any(r is None for r in rows) or len({(r['dataset_version'], r['product'], r['unit']) for r in rows}) != 1:
            return None, '全年需12个同产品可比月亮度', ''
        return {**rows[0], 'value':sum(r['value'] for r in rows)/12}, '', rows[0]['dataset_version']
    prev_start = pure_day(start).replace(year=pure_day(start).year-1).isoformat()
    prev_end = pure_day(end).replace(year=pure_day(end).year-1).isoformat()
    nl, why, version = brightness(start, end)
    old, _, _ = brightness(prev_start, prev_end)
    if nl and old and old['value'] > 0 and (nl['product'], nl['dataset_version']) == (old['product'], old['dataset_version']):
        values['nightlight_change'] = metric((nl['value']-old['value'])/old['value'], 'viirs', 'brightness_yoy', 'ratio', 'derived', version=version)
    else:
        values['nightlight_change'] = metric(source='viirs', reason=why or '同比基期缺失/为0/产品不可比')
    stock, why, version = observation('idmc', 'idp_stock', start, end)
    previous = periods(frequency, 1, (pure_day(start)-timedelta(days=1)).isoformat())[0]
    old_stock, _, _ = observation('idmc', 'idp_stock', *previous)
    if stock and old_stock and stock['dataset_version'] == old_stock['dataset_version']:
        values['displacement'] = metric(stock['value']-old_stock['value'], 'idmc', 'stock_change', 'persons', 'derived',
            '存量变化，不等于新增唯一人数；未按人口归一', version)
    else:
        values['displacement'] = metric(source='idmc', reason=why or '缺少连续同版IDP存量')
    # WB独立年度代理，只读明确已导入观测，不请求网络。
    if mode == 'proxy' and frequency == 'annual' and values['nightlight_change']['raw'] is None:
        wb, _, version = observation('worldbank', 'electricity_access', start, end)
        if wb:
            values['nightlight_change'] = metric(wb['value'], 'worldbank', 'annual_electricity_access_proxy', '%', 'proxy',
                '年度电力背景水平，不是夜光变化', version)
    return {'frequency': frequency, 'period_start': start, 'period_end': end, 'period_end_exclusive': True,
            'region': region, 'mode': mode, 'algorithm_version': VERSIONS[mode], 'indicators': values,
            'parameters': parameters, 'parameters_id': fingerprint(parameters), 'batch_ids': sorted(set(used)),
            'gdelt_input_hash': fingerprint(evs), 'score': None}


def signature(item):
    return [item['source'], item['method'], item['unit'], item['dataset_version'], item['quality']]


def fit_calibration(panels, description):
    if len(panels) < 2 or not isinstance(description, str) or not description.strip():
        raise ValueError('冻结基准至少两条参考面板并需说明')
    if len({(p['frequency'], p['mode'], p['parameters_id']) for p in panels}) != 1:
        raise ValueError('参考面板频率、模式、系数不能混合')
    specs = {}
    for key in DEFAULT_WEIGHTS_V3:
        items = [p['indicators'][key] for p in panels if finite_number(p['indicators'][key]['raw'])]
        if not items:
            continue
        if len({fingerprint(signature(i)) for i in items}) != 1:
            raise ValueError('参考面板来源、产品或方法不可比')
        specs[key] = {'min':min(i['raw'] for i in items), 'max':max(i['raw'] for i in items),
                      'signature': signature(items[0])}
    result = {'frequency': panels[0]['frequency'], 'mode': panels[0]['mode'],
        'parameters_id': panels[0]['parameters_id'], 'specs':specs, 'description':description,
        'reference_periods': sorted({(p['period_start'],p['period_end']) for p in panels}),
        'regions':sorted({p['region'] for p in panels}), 'input_hash':fingerprint(panels)}
    result['id'] = fingerprint(result)
    return result


def score_panel(panel, calibration=None):
    import copy
    p = copy.deepcopy(panel)
    p.pop('id', None)
    if calibration and p['region'] not in calibration['regions']:
        raise ValueError('目标区域不在冻结参考面板内')
    if calibration and (calibration['frequency'],calibration['mode'],calibration['parameters_id']) != (p['frequency'],p['mode'],p['parameters_id']):
        raise ValueError('基准频率、模式或参数版本不匹配')
    weights = DEFAULT_WEIGHTS_V3
    available = {}
    for key, item in p['indicators'].items():
        item['value'] = None
        spec = (calibration or {}).get('specs', {}).get(key)
        if not finite_number(item['raw']):
            continue
        if not spec or spec['signature'] != signature(item) or not finite_number(spec['min']) or not finite_number(spec['max']) or spec['max'] <= spec['min'] or not finite_number(spec['max']-spec['min']):
            item['reason'] = '缺少同源冻结基准或常量列不可标准化'
            continue
        value = (item['raw']-spec['min'])/(spec['max']-spec['min'])
        item['outside_reference'] = not 0 <= value <= 1
        item['value'] = max(0, min(1, 1-value if key in NEGATIVE else value))
        if p['mode'] == 'proxy' or item['quality'] in {'observed','derived'}:
            available[key] = weights[key]
    coverage = sum(available.values())
    complete = len(available) == 9
    p['score'] = round(sum(p['indicators'][k]['value']*w for k,w in available.items())/coverage*100,2) if coverage and (complete or p['mode']=='proxy') else None
    p['weights_used'] = {k:w/coverage for k,w in available.items()} if p['score'] is not None else {}
    p['coverage'] = round(coverage,4)
    for k,item in p['indicators'].items():
        item['contribution_points'] = 100*item['value']*p['weights_used'][k] if k in p['weights_used'] else None
    p['status'] = 'ok' if complete else 'partial_proxy' if p['score'] is not None else 'blocked'
    p['calibration_id'] = calibration['id'] if calibration else None
    p['comparable_id'] = fingerprint([p['parameters_id'], p['calibration_id'], p['weights_used'],
                                      {k:signature(v) for k,v in p['indicators'].items()}])
    p['label'] = '正式九指标（仅OFAC制裁覆盖）' if p['mode']=='formal' else '部分指标代理分（不作正式预警）'
    p['id'] = fingerprint(p)
    return p


def build_panels(batches, gdelt, frequency='monthly', count=12, end_date=None, region='MMR', mode='formal', parameters=None, calibration=None, batch_ids=None):
    return [score_panel(raw_panel(a,b,frequency,region,batches,gdelt,parameters,mode,batch_ids),calibration)
            for a,b in periods(frequency,count,end_date)]
