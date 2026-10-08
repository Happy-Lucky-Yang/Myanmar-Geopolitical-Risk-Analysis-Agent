"""共享权限与CSRF的离线路由测试；数据库行级权限另作实库验收。"""
import pytest
from flask import Flask, jsonify
from utils import security


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setenv('APP_SHARED_MODE', 'true')
    monkeypatch.setenv('APP_SECRET_KEY', 'offline-test-session-key-32-characters-only')
    monkeypatch.setenv('COOKIE_SECURE', 'false')
    monkeypatch.setattr(security, 'repository', lambda: object())
    monkeypatch.setattr(security, 'rate_allowed', lambda *args: True)
    users = {role: {'id': role, 'username': role, 'role': role} for role in ('reader', 'analyst', 'admin')}
    monkeypatch.setattr(security, 'user_by_id', users.get)
    app = Flask(__name__)
    security.init_security(app)
    app.add_url_rule('/api/analyze', 'analyze', lambda: jsonify(success=True), methods=['POST'])
    app.add_url_rule('/api/scheduler', 'scheduler_control', lambda: jsonify(success=True), methods=['POST'])
    app.add_url_rule('/api/read', 'read', lambda: jsonify(success=True))
    return app.test_client()


def authenticate(client, role):
    with client.session_transaction() as session:
        session['uid'] = role
    return client.get('/api/session').get_json()['data']['csrf_token']


def test_anonymous_and_csrf_guards(secured):
    assert secured.get('/api/read').status_code == 401
    assert secured.post('/api/login', json={'username': 'test', 'password': 'test'}).status_code == 403
    authenticate(secured, 'analyst')
    assert secured.post('/api/analyze', json={}).status_code == 403
    response = secured.get('/api/read')
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('role,analysis_status,scheduler_status', [('reader', 403, 403), ('analyst', 200, 403), ('admin', 200, 200)])
def test_roles(secured, role, analysis_status, scheduler_status):
    token = authenticate(secured, role)
    headers = {'X-CSRF-Token': token}
    assert secured.post('/api/analyze', json={}, headers=headers).status_code == analysis_status
    assert secured.post('/api/scheduler', json={}, headers=headers).status_code == scheduler_status


def test_inactive_user_session_loses_access(secured):
    authenticate(secured, 'missing-user')
    assert secured.get('/api/read').status_code == 401


def test_write_rate_limit(secured, monkeypatch):
    token = authenticate(secured, 'analyst')
    monkeypatch.setattr(security, 'rate_allowed', lambda *args: False)
    assert secured.post('/api/analyze', json={}, headers={'X-CSRF-Token': token}).status_code == 429


def test_shared_mode_rejects_file_backend(monkeypatch):
    monkeypatch.setenv('APP_SHARED_MODE', 'true')
    monkeypatch.setenv('APP_SECRET_KEY', 'offline-test-session-key-32-characters-only')
    with pytest.raises(RuntimeError, match='PostgreSQL'):
        security.init_security(Flask(__name__))
