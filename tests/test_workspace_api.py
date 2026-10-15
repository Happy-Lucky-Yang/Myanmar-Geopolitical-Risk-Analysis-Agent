"""路由权限测试使用桩，只证明HTTP权限边界；事务隔离另由PostGIS测试验证。"""
from types import SimpleNamespace
import pytest
from flask import Flask
from storage.workspace import MissingRecord


@pytest.fixture
def client(monkeypatch):
    import utils.security as security
    import workspace_api
    app=Flask(__name__)
    monkeypatch.setenv('APP_SHARED_MODE','true')
    monkeypatch.setenv('APP_SECRET_KEY','isolated-test-only-'*3)
    monkeypatch.setenv('COOKIE_SECURE','false')
    monkeypatch.setenv('ENABLE_WORKSPACE','true')
    monkeypatch.setattr(security,'repository',lambda:object())
    monkeypatch.setattr(security,'rate_allowed',lambda *a:True)
    users={u:{'id':u,'username':u,'role':role} for u,role in [('alice','analyst'),('bob','analyst'),('admin','admin'),('reader','reader')]}
    monkeypatch.setattr(security,'user_by_id',lambda uid:users.get(uid))
    def conversation(uid,cid):
        if uid!='alice' or cid!='a':raise MissingRecord()
        return {'id':cid,'messages':[]}
    store=SimpleNamespace(ready=lambda:True,conversation=conversation,preferences=lambda uid,body=None:{},create=lambda *a:'new')
    monkeypatch.setattr(workspace_api,'WorkspaceStore',lambda repo:store)
    monkeypatch.setattr('storage.repository.get_repository',lambda:object())
    security.init_security(app)
    app.register_blueprint(workspace_api.bp)
    return app.test_client()


def login(client,uid):
    with client.session_transaction() as session:
        session['uid']=uid;session['csrf']='fixture'


def test_private_http_reads_exports_and_admin_boundary(client):
    assert client.get('/api/workspace/conversations/a').status_code==401
    for uid in ('bob','admin'):
        login(client,uid)
        for path in ('/api/workspace/conversations/a','/api/workspace/conversations/a/export'):
            assert client.get(path).status_code==404
    login(client,'alice')
    response=client.get('/api/workspace/conversations/a/export')
    assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
    assert client.get('/api/workspace/conversations/a',headers={'X-Workspace-User':'bob'}).status_code==409


def test_workspace_csrf_role_and_identity_validation(client):
    login(client,'alice')
    assert client.post('/api/workspace/conversations',json={}).status_code==403
    headers={'X-CSRF-Token':'fixture'}
    assert client.post('/api/workspace/conversations',json={'owner_id':'bob'},headers=headers).status_code==400
    assert client.post('/api/workspace/conversations',json={},headers=headers).status_code==201
    login(client,'reader')
    assert client.post('/api/workspace/conversations',json={},headers=headers).status_code==403
    assert client.put('/api/workspace/preferences',json={},headers=headers).status_code==200
    login(client,'disabled')
    assert client.get('/api/workspace/conversations/a').status_code==401
