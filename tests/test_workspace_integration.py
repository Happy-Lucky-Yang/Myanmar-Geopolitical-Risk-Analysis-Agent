"""必须在确认的独立PostGIS库验收，不使用SQLite冒充多用户事务测试。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update, func
from tests.test_postgis_integration import repo
from storage.workspace import WorkspaceStore, MissingRecord, LimitReached

pytestmark=pytest.mark.postgis


@pytest.fixture
def store(repo):
    with repo.engine.begin() as conn:
        for uid in ('alice','bob','admin'):
            conn.execute(insert(repo.schema.users).values(id=uid,username=uid,password_hash='disabled-fixture',
                         role='admin' if uid=='admin' else 'analyst'))
    return WorkspaceStore(repo)


def test_workspace_owner_feedback_delete_and_private_jobs(store):
    assert store.ready()
    cid=store.create('alice','私密测试会话')
    for uid in ('bob','admin'):
        assert not store.conversations(uid)
        with pytest.raises(MissingRecord):store.conversation(uid,cid)
        with pytest.raises(MissingRecord):store.rename(uid,cid,'越权')
        with pytest.raises(MissingRecord):store.remove(uid,cid)
    mid=uuid4().hex
    with store.repo.engine.begin() as conn:
        conn.execute(insert(store.t['messages']).values(id=mid,owner_id='alice',conversation_id=cid,
                     role='assistant',content='合成回答',payload={'status':'ok','evidence':{'revision':'fixture'}}))
    with pytest.raises(MissingRecord):store.feedback('bob',mid,{'rating':5})
    store.feedback('alice',mid,{'rating':4})
    store.feedback('alice',mid,{'rating':3,'comment':'补充来源'})
    assert store.conversation('alice',cid)['feedback'][0]['rating']==3
    rid=store.reserve('alice',cid,'测试问题',{},'same')
    with pytest.raises(MissingRecord):store.result('bob',rid)
    store.remove('alice',cid)
    with store.repo.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(store.t['message_feedback'])).scalar_one()==0
        job=conn.execute(select(store.t['jobs']).where(store.t['jobs'].c.id==rid)).mappings().one()
        assert job['status']=='failed' and job['result']=={} and job['payload']=={}
        assert conn.execute(select(func.count()).select_from(store.t['agent_requests'])).scalar_one()==0


def test_workspace_concurrent_idempotency_quota_and_failure_recovery(store):
    cid=store.create('alice','并发测试')
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda _:store.reserve('alice',cid,'问题',{},'same'),range(4)))
    assert len(set(ids))==1
    with store.repo.engine.begin() as conn:
        conn.execute(update(store.t['jobs']).where(store.t['jobs'].c.id==ids[0]).values(status='failed'))
    assert store.conversation('alice',cid)['requests'][0]['status']=='failed'
    store.reserve('alice',cid,'新问题',{},'new')
    for i in range(18):store.reserve('alice',None,'摘要',{},str(i),kind='briefing')
    with pytest.raises(LimitReached):store.reserve('alice',None,'超额',{},'over',kind='briefing')
    store.remove('alice',cid)
    with pytest.raises(LimitReached):store.reserve('alice',None,'删除也不重置额度',{},'over2',kind='briefing')


def test_workspace_auto_cooldown_preferences_and_suggestions(store):
    rid=store.reserve(None,None,'自动研判',{},'same',kind='auto')
    assert rid==store.reserve(None,None,'自动研判',{},'same',kind='auto')
    with pytest.raises(LimitReached):store.reserve(None,None,'自动研判',{},'another',kind='auto')
    store.preferences('alice',{'suggestions':True,'scenario':'trade'})
    for _ in range(5):store.record_usage('alice','map')
    assert store.suggestion('alice')['feature']=='graph'
    assert store.suggestion('alice') is None
    assert store.preferences('bob')['scenario']=='research'
    store.preferences('alice',{})
    assert store.suggestion('alice') is None


def test_workspace_worker_rechecks_disabled_owner(store,monkeypatch):
    from analyzer.assistant_tasks import execute_assistant
    for flag in ('WORKSPACE','RISK_BRIEFING'):monkeypatch.setenv('ENABLE_'+flag,'true')
    monkeypatch.setattr('analyzer.assistant_tasks.get_repository',lambda:store.repo)
    monkeypatch.setattr('analyzer.assistant_tasks.generate_answer',lambda *a:pytest.fail('停用账号不能调用模型'))
    cid=store.create('alice','停用测试')
    rid=store.reserve('alice',cid,'问题',{},'disabled')
    with store.repo.engine.begin() as conn:
        conn.execute(update(store.repo.schema.users).where(store.repo.schema.users.c.id=='alice').values(active=False))
    with pytest.raises(PermissionError):execute_assistant({'id':rid,'owner_id':'alice'})
