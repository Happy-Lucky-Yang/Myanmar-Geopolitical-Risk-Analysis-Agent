"""多源数据/基准/面板/事件审核管理，默认预演。不得在业务库擅自执行apply。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(args):
    from utils.risk_contract import read_json, table_records
    from storage.risk_store import RiskStore
    from utils.config import get_data_paths
    from utils.product_features import enabled
    store = RiskStore()
    if not store.ready():
        raise ValueError('多源数据库迁移尚未完成')
    if args.apply:
        expected = store.repo.engine.url.database if store.repo else str(Path(get_data_paths()['root']).resolve())
        if args.confirm != expected:
            raise ValueError('apply须用--confirm精确确认数据库名或DATA_ROOT绝对路径')
        if not enabled('risk_panels'):
            raise ValueError('写入须显式启用ENABLE_RISK_PANELS')
    payload = None
    if args.action == 'import':
        from data.risk_sources import parse_acled, parse_idmc, parse_viirs, parse_ofac_actions
        document = read_json(args.input) if args.input.lower().endswith('.json') else None
        manifest = read_json(args.manifest) if args.manifest else (document or {}).get('manifest')
        source = args.source or (document or {}).get('source')
        rows = (document or {}).get('records') if document else table_records(args.input,args.sheet)
        parsers = {'acled':parse_acled,'ofac':parse_ofac_actions,'idmc':parse_idmc,'viirs':parse_viirs}
        if source == 'worldbank':
            from utils.data_contract import normalize_observation
            from utils.risk_contract import batch
            obs = [normalize_observation(r) for r in rows]
            if any(r['source']!='worldbank' or r['frequency']!='annual' or r['quality']!='proxy'
                   or r['indicator']!='electricity_access' or r['unit']!='%' for r in obs):
                raise ValueError('WB只接受年度电力可及性代理原值')
            payload=batch(source,obs,manifest)
        elif source in parsers:
            payload=parsers[source](rows,manifest)
        else:
            raise ValueError('未知数据源')
        if args.apply:
            store.save_batch(payload)
            from data.source_health import get_source_health_tracker
            get_source_health_tracker().record(source, True, len(payload['records']))
        # 不在控制台披露受许可约束的事件正文。
        return {'status':'saved' if args.apply else 'dry_run','source':source,'batch_id':payload['id'],
                'count':len(payload['records']),'complete':payload['manifest']['complete']}
    if args.action == 'sync-acled':
        from data.risk_sources import ACLEDAdapter
        if not args.apply:
            return {'status':'dry_run','reason':'未发网络请求；确认授权和目标后才调用ACLED'}
        payload=ACLEDAdapter().fetch(read_json(args.manifest))
        if 'manifest' in payload:
            store.save_batch(payload)
            return {'status':'saved','count':len(payload['records']),'batch_id':payload['id']}
        return payload
    if args.action == 'inspect-ofac':
        from data.risk_sources import OFACAdapter
        return OFACAdapter().inspect()
    if args.action == 'calibrate':
        from analyzer.risk_panels import fit_calibration
        payload=fit_calibration(read_json(args.input),args.description)
        name='risk_calibrations'
    elif args.action == 'build':
        from analyzer.risk_panels import build_panels
        from data.event_store import get_event_store
        calibration=next((c for c in store.load('risk_calibrations') if c['id']==args.calibration_id),None)
        if args.calibration_id and calibration is None:
            raise ValueError('基准不存在')
        payload=build_panels(store.load('risk_source_batches'),get_event_store().load(days=None),args.frequency,
            args.count if args.count is not None else (5 if args.frequency=='annual' else 12),args.end_date,args.region,args.mode,
            read_json(args.parameters) if args.parameters else {},calibration)
        name='risk_panels'
    elif args.action == 'match':
        from analyzer.event_links import candidates
        inputs=read_json(args.input)
        payload=candidates(inputs['acled'],inputs['gdelt'])
        name='event_links'
    elif args.action == 'review':
        from analyzer.event_links import review
        inputs=read_json(args.input)
        candidate=next((r for r in store.load('event_links') if r['id']==inputs['candidate_id']),None)
        if candidate is None:
            raise ValueError('候选不存在')
        payload=review(candidate,inputs['decision'],inputs['reviewer_id'],inputs['evidence'])
        if args.apply and store.repo:
            from utils.security import user_by_id
            user=user_by_id(inputs['reviewer_id'])
            if not user or user['role']!='admin':
                raise PermissionError('审核人必须为当前有效管理员')
        name='event_links'
    else:
        raise ValueError('未知动作')
    if args.apply:
        store.save(name,payload if isinstance(payload,list) else [payload])
    return {'status':'saved' if args.apply else 'dry_run','data':payload}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['import','sync-acled','inspect-ofac','calibrate','build','match','review'])
    parser.add_argument('--input',default='')
    parser.add_argument('--manifest')
    parser.add_argument('--source')
    parser.add_argument('--sheet')
    parser.add_argument('--parameters')
    parser.add_argument('--description',default='')
    parser.add_argument('--calibration-id')
    parser.add_argument('--frequency',choices=['monthly','annual'],default='monthly')
    parser.add_argument('--count',type=int)
    parser.add_argument('--end-date')
    parser.add_argument('--region',default='MMR')
    parser.add_argument('--mode',choices=['formal','proxy'],default='formal')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--confirm')
    args=parser.parse_args()
    try:
        print(json.dumps(run(args),ensure_ascii=True,allow_nan=False))
    except Exception as exc:
        # 不把供应商异常、连接串或原始正文回显到日志。
        print(json.dumps({'status':'failed','error_type':type(exc).__name__}))
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
