"""共享模式身份、CSRF、角色和私密记录访问边界。"""
import hmac
import os
import secrets
from datetime import timedelta
from uuid import uuid4

from flask import g, request, session, jsonify, redirect
from werkzeug.security import check_password_hash, generate_password_hash
from utils.data_contract import fingerprint, utc_now

_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


def repository():
    from storage.repository import get_repository
    repo = get_repository()
    if repo is None:
        raise RuntimeError('共享模式要求 PostgreSQL，不能使用文件后端')
    return repo


def user_by_id(uid):
    from sqlalchemy import select
    repo = repository()
    with repo.engine.connect() as conn:
        row = conn.execute(select(repo.schema.users).where(repo.schema.users.c.id == uid,
                           repo.schema.users.c.active.is_(True))).mappings().first()
    return dict(row) if row else None


def audit(action, target=None, user_id=None):
    repo = repository()
    with repo.engine.begin() as conn:
        repo._insert(conn, repo.schema.audit_logs, {'id': uuid4().hex, 'action': action,
                     'target': target, 'user_id': user_id, 'payload': {}})


def rate_allowed(action, target, limit, seconds=60):
    """数据库锁串行统计，多 Web worker 共用额度；不存文本、密码和原始IP。"""
    from sqlalchemy import select, func, text
    repo = repository()
    t = repo.schema.audit_logs
    with repo.engine.begin() as conn:
        lock = int(fingerprint([action, target])[:15], 16)
        conn.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
        count = conn.execute(select(func.count()).select_from(t).where(t.c.action == action,
                    t.c.target == target, t.c.created_at > utc_now() - timedelta(seconds=seconds))).scalar_one()
        if count >= limit:
            return False
        repo._insert(conn, t, {'id': uuid4().hex, 'action': action, 'target': target, 'payload': {}})
    return True


def init_security(app):
    shared = os.environ.get('APP_SHARED_MODE', 'false').lower() == 'true'
    app.config['SHARED_MODE'] = shared
    secret = os.environ.get('APP_SECRET_KEY', '')
    if shared:
        if len(secret) < 32 or 'your-' in secret.lower():
            raise RuntimeError('共享模式 APP_SECRET_KEY 必须设置独立的至少32字符随机值')
        repository()
    app.secret_key = secret or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', 'true' if shared else 'false').lower() == 'true',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))

    @app.before_request
    def guard():
        g.user = {}
        if not app.config['SHARED_MODE']:
            return None
        public = request.endpoint in {'health_check', 'static', 'login_page', 'session_info', 'login'}
        if session.get('uid'):
            g.user = user_by_id(session['uid']) or {}
        if not public and not g.user:
            if not request.path.startswith('/api/'):
                return redirect('/login')
            return jsonify(success=False, error='请先登录'), 401
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            expected = session.get('csrf', '')
            supplied = request.headers.get('X-CSRF-Token', '')
            if not expected or not hmac.compare_digest(expected.encode('utf-8'), supplied.encode('utf-8')):
                return jsonify(success=False, error='CSRF 校验失败'), 403
            if request.endpoint != 'login':
                if not g.user:
                    return jsonify(success=False, error='请先登录'), 401
                analyst = {'analyze', 'chain_analysis', 'acknowledge_alert', 'share_analysis'}
                required = 'analyst' if request.endpoint in analyst else 'reader' if request.endpoint == 'logout' else 'admin'
                levels = {'reader': 0, 'analyst': 1, 'admin': 2}
                if levels.get(g.user['role'], -1) < levels[required]:
                    return jsonify(success=False, error='权限不足'), 403
                if not rate_allowed('write_attempt', fingerprint(g.user['id']), 30):
                    return jsonify(success=False, error='请求过于频繁'), 429

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        if app.config['SHARED_MODE'] and (request.path.startswith('/api/') or request.path == '/login'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/login')
    def login_page():
        from flask import render_template
        return render_template('login.html')

    @app.get('/api/session')
    def session_info():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        user = getattr(g, 'user', {})
        return jsonify(success=True, data={'shared_mode': app.config['SHARED_MODE'], 'csrf_token': session['csrf'],
                       'user': {k: user[k] for k in ('id', 'username', 'role')} if user else None})

    @app.post('/api/login')
    def login():
        if not app.config['SHARED_MODE']:
            return jsonify(success=False, error='本地模式未启用登录'), 409
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('username'), str) or not isinstance(body.get('password'), str):
            return jsonify(success=False, error='用户名或密码格式错误'), 400
        if len(body['username']) > 128 or len(body['password']) > 1024:
            return jsonify(success=False, error='输入过长'), 400
        target = fingerprint(request.remote_addr or 'unknown')
        if not rate_allowed('login_attempt', target, 10, 300):
            return jsonify(success=False, error='登录尝试过多，请稍后重试'), 429
        from sqlalchemy import select
        repo = repository()
        with repo.engine.connect() as conn:
            row = conn.execute(select(repo.schema.users).where(repo.schema.users.c.username == body['username'],
                               repo.schema.users.c.active.is_(True))).mappings().first()
        password_valid = check_password_hash(row['password_hash'] if row else _DUMMY_PASSWORD_HASH, body['password'])
        if row is None or not password_valid:
            return jsonify(success=False, error='用户名或密码错误'), 401
        session.clear()
        session.update(uid=row['id'], csrf=secrets.token_urlsafe(32))
        session.permanent = True
        audit('login', user_id=row['id'])
        return jsonify(success=True, data={'csrf_token': session['csrf']})

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(success=True)

    @app.get('/api/jobs/<job_id>')
    def job_status(job_id):
        if not app.config['SHARED_MODE']:
            return jsonify(success=False, error='任务队列未启用'), 409
        from storage.jobs import JobQueue
        result = JobQueue(repository()).get(job_id, g.user['id'], g.user['role'] == 'admin')
        return (jsonify(success=True, data=result), 200) if result else (jsonify(success=False, error='任务不存在'), 404)

    @app.get('/api/analyses/<run_id>')
    def analysis_detail(run_id):
        if not app.config['SHARED_MODE']:
            return jsonify(success=False, error='共享记录接口未启用'), 409
        from sqlalchemy import select, or_
        repo = repository()
        t = repo.schema.analysis_runs
        with repo.engine.connect() as conn:
            row = conn.execute(select(t.c.payload, t.c.shared).where(t.c.id == run_id,
                or_(t.c.owner_id == g.user['id'], t.c.shared.is_(True)))).first()
        return (jsonify(success=True, data={**row.payload, 'shared': row.shared}), 200) if row else (jsonify(success=False, error='记录不存在'), 404)

    @app.post('/api/analyses/<run_id>/share')
    def share_analysis(run_id):
        if not app.config['SHARED_MODE']:
            return jsonify(success=False, error='共享记录接口未启用'), 409
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or type(body.get('shared')) is not bool:
            return jsonify(success=False, error='shared 必须为布尔值'), 400
        from sqlalchemy import update
        repo = repository()
        with repo.engine.begin() as conn:
            changed = conn.execute(update(repo.schema.analysis_runs).where(repo.schema.analysis_runs.c.id == run_id,
                repo.schema.analysis_runs.c.owner_id == g.user['id']).values(shared=body['shared'])).rowcount
            if changed:
                repo._insert(conn, repo.schema.audit_logs, {'id': uuid4().hex, 'action': 'share' if body['shared'] else 'unshare',
                             'user_id': g.user['id'], 'target': run_id, 'payload': {}})
        return jsonify(success=bool(changed)), 200 if changed else 404
