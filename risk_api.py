"""多源面板独立接口；GET只读取已保存面板，不采集、不拟合基准。"""
from flask import Blueprint, jsonify, request, current_app
from utils.product_features import enabled
from utils.risk_contract import periods, VERSIONS
from utils.data_contract import fingerprint

bp = Blueprint('risk_panels', __name__)


def options(body):
    if not isinstance(body,dict) or set(body)-{'frequency','count','end_date','region','mode','parameters','calibration_id','batch_ids'}:
        raise ValueError('面板请求字段无效')
    frequency = body.get('frequency','monthly')
    count = body.get('count',5 if frequency=='annual' else 12)
    windows = periods(frequency,count,body.get('end_date'))
    mode = body.get('mode','formal')
    region = body.get('region','MMR')
    if mode not in VERSIONS or not isinstance(region,str) or not 1 <= len(region) <= 64:
        raise ValueError('模式或地区无效')
    params = body.get('parameters',{})
    if not isinstance(params,dict) or set(params)-{'lambda','mu','approved_version'}:
        raise ValueError('只接受审定λ/μ及参数版本，不接受身份或认证信息')
    from utils.data_contract import finite_number
    for k in ('lambda','mu'):
        if k in params and (not isinstance(params[k],dict) or len(params[k])>20 or any(
                not isinstance(t,str) or len(t)>64 or not finite_number(v) or v<0 for t,v in params[k].items())):
            raise ValueError('系数必须为非负有限数且类型名称有限长')
    if 'approved_version' in params and (not isinstance(params['approved_version'],str) or not 1<=len(params['approved_version'])<=128):
        raise ValueError('审定参数版本无效')
    if body.get('batch_ids') is not None and (not isinstance(body['batch_ids'],list) or len(body['batch_ids'])>100
            or any(not isinstance(i,str) or len(i)!=64 for i in body['batch_ids'])):
        raise ValueError('batch_ids须为最多100项列表')
    return windows


@bp.get('/api/risk_panels')
def panels():
    if not enabled('risk_panels'):
        return jsonify(success=False,error='多源风险面板尚未启用，旧日风险仍可使用'),503
    try:
        from storage.risk_store import RiskStore
        store = RiskStore()
        if not store.ready():
            return jsonify(success=False,error='多源面板数据库迁移尚未完成'),503
        frequency = request.args.get('frequency','monthly')
        query = {'frequency':frequency,'count':int(request.args.get('count',5 if frequency=='annual' else 12)),
                 'mode':request.args.get('mode','formal'),'region':request.args.get('region','MMR'),
                 'end_date':request.args.get('end_date')}
        windows = options(query)
        revision = store.revision()
        if request.args.get('panel_revision') and request.args['panel_revision'] != revision:
            return jsonify(success=False,error='面板版本已更新，请刷新'),409
        rows = [p for p in store.load('risk_panels') if p['frequency']==frequency and p['mode']==query['mode']
                and p['region']==query['region'] and (p['period_start'],p['period_end']) in windows
                and (not request.args.get('calibration_id') or p['calibration_id']==request.args['calibration_id'])]
        by_period = {}
        for p in rows:
            by_period.setdefault(p['period_start'],[]).append(p)
        result = []
        for a,b in windows:
            found = by_period.get(a,[])
            if len(found)==1:
                result.append(found[0])
            else:
                result.append({'period_start':a,'period_end':b,'score':None,'status':'ambiguous' if found else 'not_computed',
                    'reason':'同周期存在多个快照，请选择基准/版本' if found else '尚未计算；需先导入授权资料、确认参数和基准'})
        if store.revision()!=revision:
            return jsonify(success=False,error='面板读取期间数据已变化'),409
        return jsonify(success=True,data={'panels':result,'revision':revision,'filters':query,
            'algorithm_version':VERSIONS[query['mode']], 'unit':'风险分（非概率）',
            'calibrations':[{'id':c['id'],'frequency':c['frequency'],'mode':c['mode'],'description':c['description']}
                            for c in store.load('risk_calibrations')],
            'note':'新月/年面板独立于旧日风险；正式缺项不合成，代理不作正式预警。'})
    except (ValueError,TypeError) as exc:
        return jsonify(success=False,error=str(exc)),400
    except Exception:
        return jsonify(success=False,error='多源面板存储不可用，未退回旧分伪装新版'),503


@bp.get('/api/risk_sources/status')
def source_status():
    status = {s:{'enabled':enabled(s),'status':'not_loaded','reason':reason} for s,reason in {
        'acled':'需要myACLED授权；未获批时仅可选GDELT代理',
        'ofac':'官方法律措施须有可靠生效日/类型；不以SDN对象数代替',
        'idmc':'导入官方GIDD文件；年度全国统计不复制到邦省/月度',
        'viirs':'本轮不联网下载；需真实月分区亮度，WB另作年度代理'}.items()}
    if enabled('risk_panels'):
        try:
            from storage.risk_store import RiskStore
            store = RiskStore()
            if store.ready():
                for b in store.load('risk_source_batches'):
                    if b['source'] not in status:
                        continue
                    item = status[b['source']]
                    item.setdefault('batches',[]).append({'id':b['id'],'count':len(b['records']),
                        'manifest':{k:b['manifest'][k] for k in ('start','end','region','complete','dataset_version','url')}})
                    item['status']='loaded'
        except Exception:
            return jsonify(success=False,error='来源状态读取失败'),503
    return jsonify(success=True,data=status)


@bp.post('/api/risk_panels/build')
def request_build():
    if not enabled('risk_panels') or not current_app.config.get('SHARED_MODE'):
        return jsonify(success=False,error='面板写任务需要启用共享模式；本机使用预演CLI'),503
    try:
        body = request.get_json(silent=True)
        options(body)
        from flask import g
        from storage.jobs import JobQueue
        from storage.repository import get_repository
        if getattr(g,'user',{}).get('role')!='admin':
            return jsonify(success=False,error='仅管理员可发起面板任务'),403
        repo=get_repository()
        from storage.risk_store import RiskStore
        if not RiskStore(repo).ready():
            return jsonify(success=False,error='多源面板迁移尚未完成，未创建任务'),503
        jid=JobQueue(repo).enqueue('risk_panels',body,owner_id=g.user['id'],
            idempotency_key=request.headers.get('Idempotency-Key') or fingerprint(body))
        return jsonify(success=True,data={'job_id':jid}),202
    except (ValueError,TypeError) as exc:
        return jsonify(success=False,error=str(exc)),400
    except Exception:
        return jsonify(success=False,error='风险面板任务服务不可用，未确认提交'),503
