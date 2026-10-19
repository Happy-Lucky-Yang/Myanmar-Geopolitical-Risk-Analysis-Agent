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
    monkeypatch.setattr('storage.workspace.WorkspaceStore',lambda repo:store)
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


def test_startup_without_database_dependencies():
    """独立进程阻断数据库包，防止已导入模块掩盖启动回归。"""
    import subprocess
    import sys
    code = '''
import builtins
import os
import socket
import sys
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] in {'sqlalchemy', 'psycopg', 'geoalchemy2'}:
        raise ModuleNotFoundError(name, name=name)
    return original(name, *args, **kwargs)
def offline(*args, **kwargs):
    raise OSError('offline test')
builtins.__import__ = blocked
socket.socket.connect = offline
from app import app
client = app.test_client()
for path in ('/', '/map', '/trend', '/dashboard', '/graph', '/workspace'):
    assert client.get(path).status_code == 200, path
assert client.get('/api/workspace/conversations').status_code == 503
assert 'storage.workspace' not in sys.modules
os.environ['ENABLE_WORKSPACE'] = 'true'
assert client.get('/api/workspace/conversations').status_code == 503
from flask import Flask, g
from workspace_api import bp
shared = Flask('missing-dependency-test')
shared.config['SHARED_MODE'] = True
shared.register_blueprint(bp)
@shared.before_request
def identity():
    g.user = {'id': 'fixture'}
response = shared.test_client().get('/api/workspace/conversations')
assert response.status_code == 503
assert '数据库依赖' in response.get_json()['error']
print('DATABASE_OPTIONAL_STARTUP_OK')
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                            encoding='utf-8', errors='replace', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'DATABASE_OPTIONAL_STARTUP_OK' in result.stdout
