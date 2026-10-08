"""PostgreSQL 持久任务队列：短事务领取、租约心跳、令牌隔离和幂等提交。"""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update, and_, or_, func
from sqlalchemy.dialects.postgresql import insert

from utils.data_contract import fingerprint, json_safe

KINDS = {'pipeline', 'manual', 'chain', 'gdelt', 'nightlight', 'economic'}


class LeaseLost(RuntimeError):
    """失效 worker 不得提交业务写入。"""


class JobQueue:
    def __init__(self, repository, lease_seconds=120):
        if repository is None:
            raise RuntimeError('多人任务队列需要 postgres 后端')
        self.repo = repository
        self.table = repository.schema.jobs
        self.lease_seconds = lease_seconds

    def enqueue(self, kind, payload, owner_id=None, idempotency_key=None):
        if kind not in KINDS or not isinstance(payload, dict):
            raise ValueError('不支持的任务类型或输入')
        token = idempotency_key or uuid4().hex
        if not isinstance(token, str) or len(token) > 128:
            raise ValueError('幂等键过长或类型不正确')
        key = fingerprint({'owner': owner_id, 'kind': kind, 'token': token})
        with self.repo.engine.begin() as conn:
            if owner_id:
                users = self.repo.schema.users
                conn.execute(select(users.c.id).where(users.c.id == owner_id).with_for_update())
            existing = conn.execute(select(self.table).where(self.table.c.idempotency_key == key)).mappings().first()
            if existing:
                if existing['payload'] != json_safe(payload):
                    raise ValueError('同一幂等键不能对应不同输入')
                return existing['id']
            pending = conn.execute(select(func.count()).select_from(self.table).where(
                self.table.c.owner_id == owner_id, self.table.c.status.in_(['queued', 'running']))).scalar_one()
            if pending >= 20:
                raise ValueError('未完成任务过多，请等待后重试')
            jid = uuid4().hex
            conn.execute(insert(self.table).values(id=jid, kind=kind, owner_id=owner_id,
                         idempotency_key=key, status='queued', payload=json_safe(payload))
                         .on_conflict_do_nothing(index_elements=['idempotency_key']))
            return conn.execute(select(self.table.c.id).where(self.table.c.idempotency_key == key)).scalar_one()

    def claim(self):
        t = self.table
        now = func.clock_timestamp()
        with self.repo.engine.begin() as conn:
            conn.execute(update(t).where(t.c.status == 'running', t.c.lease_until < now,
                         t.c.attempts >= t.c.max_attempts).values(status='failed', error='任务重试耗尽', updated_at=now))
            row = conn.execute(select(t).where(t.c.attempts < t.c.max_attempts,
                or_(and_(t.c.status == 'queued', t.c.available_at <= now),
                    and_(t.c.status == 'running', t.c.lease_until < now)))
                .order_by(t.c.available_at, t.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if row is None:
                return None
            token = uuid4().hex
            conn.execute(update(t).where(t.c.id == row['id']).values(status='running',
                         attempts=t.c.attempts + 1, lease_token=token,
                         lease_until=now + timedelta(seconds=self.lease_seconds), updated_at=now))
            return {**row, 'lease_token': token, 'attempts': row['attempts'] + 1}

    def heartbeat(self, job):
        now = func.clock_timestamp()
        with self.repo.engine.begin() as conn:
            result = conn.execute(update(self.table).where(self._owned(job, now)).values(
                lease_until=now + timedelta(seconds=self.lease_seconds), updated_at=now))
            return result.rowcount == 1

    def assert_owned(self, conn, job, lease_lost=None):
        if lease_lost is not None and lease_lost.is_set():
            raise LeaseLost('任务已失去租约')
        t = self.table
        row = conn.execute(select(t.c.status, t.c.lease_token, t.c.lease_until).where(
            t.c.id == job['id']).with_for_update()).mappings().first()
        now = conn.execute(select(func.clock_timestamp())).scalar_one()
        if (row is None or row['status'] != 'running' or row['lease_token'] != job['lease_token']
                or row['lease_until'] is None or row['lease_until'] <= now):
            raise LeaseLost('任务租约已过期或被接管')

    def _owned(self, job, now):
        t = self.table
        return and_(t.c.id == job['id'], t.c.status == 'running',
                    t.c.lease_token == job['lease_token'], t.c.lease_until > now)

    def finish(self, job, result=None, error=None):
        now = func.clock_timestamp()
        values = {'updated_at': now, 'lease_token': None, 'lease_until': None}
        if error:
            values.update(status='queued' if job['attempts'] < job['max_attempts'] else 'failed',
                          available_at=now + timedelta(seconds=min(300, 2 ** job['attempts'])),
                          error=str(error)[:120])
        else:
            values.update(status='done', result=json_safe(result or {}), error=None)
        with self.repo.engine.begin() as conn:
            return conn.execute(update(self.table).where(self._owned(job, now)).values(**values)).rowcount == 1

    def get(self, job_id, owner_id, admin=False):
        query = select(self.table).where(self.table.c.id == job_id)
        # 即使管理员也不能读取其他人的私密手工任务。
        query = query.where(or_(self.table.c.owner_id == owner_id,
                               and_(self.table.c.owner_id.is_(None), admin)))
        with self.repo.engine.connect() as conn:
            row = conn.execute(query).mappings().first()
        if row is None:
            return None
        return {k: row[k] for k in ('id', 'kind', 'status', 'attempts', 'created_at', 'updated_at', 'result', 'error')}
