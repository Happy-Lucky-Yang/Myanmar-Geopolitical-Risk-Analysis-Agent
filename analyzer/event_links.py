"""跨源候选匹配不自动认定同一事件；审定链接保留两端版本与出处。"""
import math
from utils.data_contract import business_date, finite_number, fingerprint, canonical_url


def event_hash(event):
    value = {k:v for k,v in event.items() if k not in {'evidence_verified','independent_source_count','evidence_links'}}
    day = business_date(value.get('date'))
    value['date'] = day.isoformat() if day else value.get('date')
    return fingerprint(value)


def event_key(event):
    return fingerprint([event.get('source','gdelt'), event.get('event_id'), event.get('dataset_version','GDELT')])


def compatible(a, b):
    acled = a if a.get('source') == 'acled' else b
    gdelt = b if acled is a else a
    types = {'Battles': {'19'}, 'Explosions/Remote violence': {'18','20'}, 'Violence against civilians': {'18','20'}}
    return str(gdelt.get('root_code')) in types.get(acled.get('event_type'), set())


def candidates(acled, gdelt):
    matches = []
    for a in acled:
        if a.get('time_precision') != 1 or a.get('geo_precision') != 1 or not a.get('region'):
            continue
        for b in gdelt:
            if b.get('geo_precision') != 1 or not b.get('region') or a['region'] != b['region'] or not compatible(a,b):
                continue
            days = (business_date(a.get('date'))-business_date(b.get('date'))).days if business_date(a.get('date')) and business_date(b.get('date')) else 99
            if abs(days) > 1 or any(not finite_number(e.get(k)) for e in (a,b) for k in ('lat','lon')):
                continue
            # 同事件要求行为体字符串有明确映射，不采用模糊名称猜测。
            actors = a.get('canonical_actors')
            if not actors or set(actors) != set(b.get('canonical_actors') or []):
                continue
            p1,p2 = math.radians(a['lat']),math.radians(b['lat'])
            dlat,dlon = p2-p1,math.radians(b['lon']-a['lon'])
            h = math.sin(dlat/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
            distance = 6371*2*math.asin(math.sqrt(min(1,max(0,h))))
            if distance > 10:
                continue
            row = {'left':event_key(a), 'right':event_key(b), 'left_hash':event_hash(a),'right_hash':event_hash(b),
                   'distance_km':round(distance,3), 'day_difference':days, 'status':'candidate',
                   'method':'region_date_precision_type_actor_distance_v1', 'reviewer_id':None,
                   'independent_sources':[], 'note':'候选不是已确认现实事件'}
            row['id'] = fingerprint(row)
            matches.append(row)
    counts = {}
    for r in matches:
        for key in ('left','right'):
            counts[r[key]] = counts.get(r[key],0)+1
    for r in matches:
        r['ambiguous'] = counts[r['left']] > 1 or counts[r['right']] > 1
        # id覆盖完整候选内容。
        r['id'] = fingerprint({k:v for k,v in r.items() if k!='id'})
    return matches


def review(candidate, decision, reviewer_id, evidence):
    if decision not in {'confirmed','rejected'} or not isinstance(reviewer_id,str) or not reviewer_id:
        raise ValueError('链接审核需明确决定与审核人')
    if not isinstance(evidence,dict) or not evidence.get('rationale') or not evidence.get('origins'):
        raise ValueError('审核需原始出处和同事件判断依据')
    origins = evidence['origins']
    if not isinstance(origins,list) or any(not isinstance(o,dict) or not o.get('source') or not canonical_url(o.get('url')) for o in origins):
        raise ValueError('出处须为明确来源URL配对')
    # 独立性只能显式核验；URL不同、数据库不同都不自动证明独立。
    groups = {o['independence_group'] for o in origins if o.get('independence_verified') is True and o.get('independence_group')}
    r = {**candidate, 'candidate_id':candidate['id'], 'status':decision, 'reviewer_id':reviewer_id,
         'evidence':evidence, 'independent_sources':sorted(groups)}
    r['id'] = fingerprint({k:v for k,v in r.items() if k!='id'})
    return r


def annotate(events, links):
    output = []
    for event in events:
        accepted = [r for r in links if r['status']=='confirmed' and
            ((r['left']==event_key(event) and r['left_hash']==event_hash(event)) or
             (r['right']==event_key(event) and r['right_hash']==event_hash(event)))]
        disputed = {r.get('candidate_id') for r in links if r['status']=='rejected'}
        accepted = [r for r in accepted if r.get('candidate_id') not in disputed]
        groups = set(g for r in accepted for g in r.get('independent_sources',[]))
        output.append({**event, 'independent_source_count':len(groups),
            'evidence_verified':len(groups)>=2,'evidence_links':[r['id'] for r in accepted]})
    return output
