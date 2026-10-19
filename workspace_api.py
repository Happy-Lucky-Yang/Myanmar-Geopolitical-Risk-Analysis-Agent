"""个人工作区 API：所有者由登录会话确定，客户端不得指定。"""
from functools import wraps
from flask import Blueprint, current_app, g, jsonify, request, render_template, Response
import json
from utils.product_features import enabled
from utils.data_contract import json_safe, time_window

bp = Blueprint('workspace', __name__)
WRITE_ROLES = {
    'workspace.create_conversation':'analyst', 'workspace.rename_conversation':'analyst',
    'workspace.delete_conversation':'analyst', 'workspace.send_message':'analyst',
    'workspace.set_preferences':'reader', 'workspace.set_feedback':'analyst',
    'workspace.remove_feedback':'analyst', 'workspace.usage':'reader',
    'workspace.suggestion':'reader', 'workspace.request_briefing':'analyst',
}


def secured(function):
    @wraps(function)
    def call(*args,**kwargs):
        if not enabled('workspace') or not current_app.config.get('SHARED_MODE'):
            return jsonify(success=False,error='个人工作区未启用；需要共享账户、PostgreSQL及ENABLE_WORKSPACE'),503
        if not getattr(g,'user',{}).get('id'):
            return jsonify(success=False,error='请先登录'),401
        expected = request.headers.get('X-Workspace-User')
        if expected and expected != g.user['id']:
            return jsonify(success=False,error='账户已切换，请刷新页面'),409
        try:
            from storage.workspace import WorkspaceStore, MissingRecord, LimitReached
            from storage.repository import get_repository
        except ImportError:
            return jsonify(success=False,error='工作区数据库依赖不可用，请在当前解释器中安装项目依赖'),503
        try:
            store=WorkspaceStore(get_repository())
            if not store.ready():
                return jsonify(success=False,error='工作区数据库迁移尚未完成，请联系管理员'),503
            return function(store,g.user['id'],*args,**kwargs)
        except MissingRecord:
            return jsonify(success=False,error='记录不存在'),404
        except LimitReached as exc:
            return jsonify(success=False,error=str(exc)),429
        except ValueError as exc:
            return jsonify(success=False,error=str(exc)),400
        except Exception:
            current_app.logger.warning('工作区请求失败；未记录用户正文')
            return jsonify(success=False,error='工作区暂不可用，请检查数据库及任务服务'),503
    return call


def body():
    value=request.get_json(silent=True)
    if not isinstance(value,dict):
        raise ValueError('请求必须为JSON对象')
    if 'owner_id' in value or 'user_id' in value:
        raise ValueError('不得由客户端指定用户身份')
    return value


def title(value):
    if not isinstance(value,str) or not value.strip() or len(value)>160:
        raise ValueError('标题须为1～160字符')
    return value.strip()


@bp.get('/workspace')
def page():
    return render_template('workspace.html')


@bp.get('/api/workspace/conversations')
@secured
def list_conversations(store,uid):
    offset=int(request.args.get('offset',0))
    if offset<0: raise ValueError('分页偏移无效')
    return jsonify(success=True,data=store.conversations(uid,offset))


@bp.post('/api/workspace/conversations')
@secured
def create_conversation(store,uid):
    return jsonify(success=True,data={'id':store.create(uid,title(body().get('title','新对话')))}),201


@bp.get('/api/workspace/conversations/<cid>')
@secured
def get_conversation(store,uid,cid):
    return jsonify(success=True,data=store.conversation(uid,cid))


@bp.patch('/api/workspace/conversations/<cid>')
@secured
def rename_conversation(store,uid,cid):
    store.rename(uid,cid,title(body().get('title')))
    return jsonify(success=True)


@bp.delete('/api/workspace/conversations/<cid>')
@secured
def delete_conversation(store,uid,cid):
    store.remove(uid,cid)
    return jsonify(success=True)


@bp.get('/api/workspace/conversations/<cid>/export')
@secured
def export_conversation(store,uid,cid):
    data=store.conversation(uid,cid)
    return Response(json.dumps(json_safe(data),ensure_ascii=False),mimetype='application/json',
                    headers={'Content-Disposition':'attachment; filename="conversation.json"','Cache-Control':'no-store'})


def submit(store,uid,cid=None,kind='chat'):
    if not enabled('risk_briefing'):
        return jsonify(success=False,error='AI研判尚未启用：ENABLE_RISK_BRIEFING'),503
    data=body()
    if data.get('source') or data.get('domain', 'observed') != 'observed':
        raise ValueError('AI研判只支持正式观测且不支持单来源筛选')
    question=data.get('question')
    if not isinstance(question,str) or not question.strip() or len(question)>2000:
        raise ValueError('问题须为1～2000字符')
    key=request.headers.get('Idempotency-Key','')
    if not key or len(key)>128:
        raise ValueError('需要1～128字符的Idempotency-Key')
    if cid:
        store.conversation(uid,cid)
    days=data.get('days',30)
    if type(days) is not int:
        raise ValueError('days 必须为整数')
    time_window(days,data.get('end_date'))
    region=data.get('region') or store.preferences(uid)['region']
    from data.admin_boundaries import get_province_features
    codes={'MMR'} | {f['properties']['GID_1'] for f in get_province_features()}
    if region not in codes:
        raise ValueError('请选择有效行政区域代码')
    from analyzer.risk_assistant import build_evidence
    evidence=build_evidence(question,days,data.get('end_date'),region)
    rid=store.reserve(uid,cid,question,evidence,key,kind,region)
    return jsonify(success=True,data={'request_id':rid,'status':'queued',
                   'message':'已入队；需独立worker运行，未启动时不会生成回答'}),202


@bp.post('/api/workspace/conversations/<cid>/messages')
@secured
def send_message(store,uid,cid):
    return submit(store,uid,cid)


@bp.post('/api/workspace/briefings')
@secured
def request_briefing(store,uid):
    return submit(store,uid,kind='briefing')


@bp.get('/api/workspace/requests/<rid>')
@secured
def request_result(store,uid,rid):
    return jsonify(success=True,data=store.result(uid,rid))


@bp.get('/api/workspace/preferences')
@secured
def get_preferences(store,uid):
    return jsonify(success=True,data=store.preferences(uid))


@bp.put('/api/workspace/preferences')
@secured
def set_preferences(store,uid):
    return jsonify(success=True,data=store.preferences(uid,body()))


@bp.put('/api/workspace/messages/<mid>/feedback')
@secured
def set_feedback(store,uid,mid):
    store.feedback(uid,mid,body()); return jsonify(success=True)


@bp.delete('/api/workspace/messages/<mid>/feedback')
@secured
def remove_feedback(store,uid,mid):
    store.feedback(uid,mid); return jsonify(success=True)


@bp.post('/api/workspace/usage')
@secured
def usage(store,uid):
    store.record_usage(uid,body().get('feature')); return jsonify(success=True)


@bp.post('/api/workspace/suggestion')
@secured
def suggestion(store,uid):
    return jsonify(success=True,data=store.suggestion(uid))


@bp.get('/api/workspace/briefings/latest')
@secured
def latest_briefing(store,uid):
    if not enabled('risk_briefing'):
        return jsonify(success=False,error='AI研判尚未启用'),503
    from sqlalchemy import select
    r=store.t['agent_requests']
    with store.repo.engine.connect() as conn:
        result=conn.execute(select(r.c.id,r.c.result,r.c.created_at).where(r.c.kind=='auto',
            r.c.owner_id.is_(None),r.c.status=='done').order_by(r.c.created_at.desc()).limit(1)).mappings().first()
    return jsonify(success=True,data=dict(result) if result else None)
