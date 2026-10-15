"""个人研判与公共自动摘要任务；复用既有租约，不自动重试外部生成。"""
import json
from uuid import uuid4
from sqlalchemy import select, update, insert
from storage.workspace import WorkspaceStore, MissingRecord
from storage.repository import get_repository
from utils.product_features import enabled
from utils.data_contract import fingerprint, json_safe
from analyzer.risk_assistant import build_evidence, auto_eligibility, generate_answer, PROMPT_VERSION


def execute_assistant(job):
    if not enabled('workspace') or not enabled('risk_briefing'):
        raise RuntimeError('研判功能未启用')
    repo = get_repository()
    store = WorkspaceStore(repo)
    req = store.t['agent_requests']
    with repo.write_transaction() as conn:
        row = conn.execute(select(req).where(req.c.id==job['id'],req.c.owner_id==job['owner_id'])
                           .with_for_update()).mappings().first()
        if row is None:
            raise MissingRecord('任务对应会话已删除')
        row = dict(row)
        if row['status']=='done':
            return {'request_id':row['id'],'reused':True}
        if row['status']!='queued':
            raise RuntimeError('任务已执行或中断，不自动重试外部调用')
        if row['owner_id']:
            users=repo.schema.users
            user=conn.execute(select(users).where(users.c.id==row['owner_id'],users.c.active.is_(True),
                users.c.role.in_(['analyst','admin']))).first()
            if user is None:
                raise PermissionError('任务发起人已无分析权限')
        elif row['kind']!='auto' or not enabled('auto_briefing'):
            raise PermissionError('自动研判未启用')
        conn.execute(update(req).where(req.c.id==row['id']).values(status='running'))
    context=[]
    prefs={}
    if row['conversation_id']:
        conversation=store.conversation(row['owner_id'],row['conversation_id'])
        context=[m for m in conversation['messages'] if m['payload'].get('request_id')!=row['id']]
        prefs=store.preferences(row['owner_id'])
    result=generate_answer(row['payload']['question'],row['payload']['evidence'],context,prefs)
    with repo.write_transaction() as conn:
        # 查询所有权并锁会话，避免删除期间任务重新写回个人记录。
        if row['conversation_id']:
            store._conversation(conn,row['owner_id'],row['conversation_id'],True)
        if row['owner_id']:
            users=repo.schema.users
            if conn.execute(select(users.c.id).where(users.c.id==row['owner_id'],users.c.active.is_(True),
                     users.c.role.in_(['analyst','admin']))).first() is None:
                raise PermissionError('账号已停用或降权，未保存结果')
        status='done' if result['status'] in {'ok','insufficient'} else 'failed'
        conn.execute(update(req).where(req.c.id==row['id'],req.c.owner_id==row['owner_id']).values(
            status=status,result=json_safe(result)))
        if row['conversation_id']:
            answer=result.get('answer',{})
            content='\n'.join(item['text'] for values in answer.values() for item in values) or result.get('reason','')
            conn.execute(insert(store.t['messages']).values(id=uuid4().hex,owner_id=row['owner_id'],
                conversation_id=row['conversation_id'],role='assistant',content=content,
                payload=json_safe({**result,'request_id':row['id']})))
    return {'request_id':row['id'],'status':status}


def maybe_auto_briefing():
    if not all(enabled(name) for name in ('workspace','risk_briefing','auto_briefing')):
        return {'status':'disabled','reason':'自动研判未启用'}
    repo=get_repository()
    if repo is None:
        return {'status':'unavailable','reason':'自动研判要求共享PostgreSQL队列'}
    store=WorkspaceStore(repo)
    if not store.ready():
        return {'status':'unavailable','reason':'工作区迁移尚未执行'}
    from analyzer.data_loader import get_data_loader
    loader=get_data_loader()
    revision=loader.revision()
    history=loader.load_risk_history(days=16)
    eligible,reason=auto_eligibility(history)
    if not eligible:
        return {'status':'insufficient','reason':reason}
    question='请解释正式风险的近期变化，并给出有证据、附条件的风险应对建议。'
    evidence=build_evidence(question,days=16)
    if evidence['revision'] != revision:
        return {'status':'insufficient','reason':'证据读取期间修订变化，下次正式分析后重新检查'}
    from utils.config import get_llm_config
    cfg=get_llm_config()
    # 幂等键不含读取时刻，不含密钥；相同证据不因刷新重新调用。
    content={k:v for k,v in evidence.items() if k!='revision'}
    key=fingerprint([content, PROMPT_VERSION, cfg.get('model_name'),cfg.get('base_url')])
    rid=store.reserve(None,None,question,evidence,key,kind='auto')
    return {'status':'queued','request_id':rid}
