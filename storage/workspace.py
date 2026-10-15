"""按服务端身份隔离个人数据；使用现有 PostgreSQL，不提供匿名文件回退。"""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, insert, update, delete, func, text, inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from utils.data_contract import business_date, fingerprint, utc_now, json_safe
from storage.workspace_schema import TABLES


class MissingRecord(LookupError):
    pass


class LimitReached(ValueError):
    pass


def validate_preferences(body):
    if not isinstance(body, dict) or set(body) - {'region', 'scenario', 'verbosity', 'suggestions'}:
        raise ValueError('偏好字段无效')
    result = {'region': body.get('region', 'MMR'), 'scenario': body.get('scenario', 'research'),
              'verbosity': body.get('verbosity', 'brief'), 'suggestions': body.get('suggestions', False)}
    if result['scenario'] not in {'research','travel','trade'} or result['verbosity'] not in {'brief','detailed'}:
        raise ValueError('偏好选项无效')
    if type(result['suggestions']) is not bool:
        raise ValueError('suggestions 必须为布尔值')
    if not isinstance(result['region'], str) or not 1 <= len(result['region']) <= 64:
        raise ValueError('区域无效')
    return result


class WorkspaceStore:
    def __init__(self, repo):
        if repo is None:
            raise RuntimeError('个人工作区要求 PostgreSQL 共享模式')
        self.repo = repo
        self.t = repo.schema.metadata.tables

    def ready(self):
        return set(TABLES).issubset(inspect(self.repo.engine).get_table_names())

    def _conversation(self, conn, uid, cid, lock=False):
        t = self.t['conversations']
        q = select(t).where(t.c.id == cid, t.c.owner_id == uid)
        row = conn.execute(q.with_for_update() if lock else q).mappings().first()
        if row is None:
            raise MissingRecord('会话不存在')
        return dict(row)

    def conversations(self, uid, offset=0):
        t = self.t['conversations']
        with self.repo.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(t).where(t.c.owner_id == uid)
                    .order_by(t.c.created_at.desc(), t.c.id).offset(offset).limit(20)).mappings()]

    def create(self, uid, title):
        cid = uuid4().hex
        with self.repo.engine.begin() as conn:
            conn.execute(insert(self.t['conversations']).values(id=cid, owner_id=uid, title=title))
        return cid

    def conversation(self, uid, cid):
        with self.repo.engine.connect() as conn:
            result = self._conversation(conn, uid, cid)
            m, f, r = self.t['messages'], self.t['message_feedback'], self.t['agent_requests']
            # 首版会话限200条消息；客户端创建新会话继续。
            result['messages'] = [dict(row) for row in conn.execute(select(m).where(m.c.owner_id == uid,
                m.c.conversation_id == cid).order_by(m.c.created_at, m.c.id)).mappings()]
            result['feedback'] = [dict(row) for row in conn.execute(select(f).join(m, f.c.message_id == m.c.id)
                .where(m.c.conversation_id == cid, f.c.owner_id == uid)).mappings()]
            j = self.t['jobs']
            result['requests'] = []
            for row in conn.execute(select(r.c.id, r.c.status, r.c.created_at, j.c.status.label('job_status'))
                    .outerjoin(j, j.c.id == r.c.id).where(r.c.owner_id == uid,
                    r.c.conversation_id == cid).order_by(r.c.created_at)).mappings():
                value = dict(row)
                if value.pop('job_status') == 'failed' and value['status'] != 'done':
                    value['status'] = 'failed'
                result['requests'].append(value)
            return result

    def rename(self, uid, cid, title):
        with self.repo.engine.begin() as conn:
            self._conversation(conn, uid, cid, True)
            conn.execute(update(self.t['conversations']).where(self.t['conversations'].c.id == cid).values(title=title))

    def remove(self, uid, cid):
        r, j = self.t['agent_requests'], self.t['jobs']
        with self.repo.engine.begin() as conn:
            # 与worker一致：先锁任务，再锁会话。任务已无正文，只清空状态与结果。
            ids = select(r.c.id).where(r.c.owner_id == uid, r.c.conversation_id == cid)
            conn.execute(update(j).where(j.c.id.in_(ids), j.c.owner_id == uid).values(
                status='failed', error='会话已删除', payload={}, result={}, lease_token=None, lease_until=None,
                max_attempts=1, attempts=1))
            self._conversation(conn, uid, cid, True)
            conn.execute(delete(self.t['conversations']).where(self.t['conversations'].c.id == cid,
                                                              self.t['conversations'].c.owner_id == uid))
            conn.execute(insert(self.t['audit_logs']).values(id=uuid4().hex, user_id=uid,
                         action='conversation_removed', target=cid, payload={}))

    def preferences(self, uid, body=None):
        t = self.t['user_preferences']
        with self.repo.engine.begin() as conn:
            if body is not None:
                data = validate_preferences(body)
                q = pg_insert(t).values(owner_id=uid, payload=data)
                conn.execute(q.on_conflict_do_update(index_elements=['owner_id'], set_={'payload':data}))
            value = conn.execute(select(t.c.payload).where(t.c.owner_id == uid)).scalar_one_or_none()
            return value or validate_preferences({})

    def feedback(self, uid, mid, body=None):
        m, f = self.t['messages'], self.t['message_feedback']
        with self.repo.engine.begin() as conn:
            row = conn.execute(select(m).where(m.c.id == mid, m.c.owner_id == uid,
                m.c.role == 'assistant').with_for_update()).mappings().first()
            if row is None or row['payload'].get('status') != 'ok':
                raise MissingRecord('可评价的回答不存在')
            if body is None:
                conn.execute(delete(f).where(f.c.message_id == mid, f.c.owner_id == uid))
                return
            rating, tag, comment = body.get('rating'), body.get('tag', ''), body.get('comment', '')
            if type(rating) is not int or not 1 <= rating <= 5:
                raise ValueError('评分须为1～5整数')
            if tag not in {'','helpful','unclear','wrong','missing_evidence'} or not isinstance(comment,str) or len(comment)>1000:
                raise ValueError('反馈标签或意见无效')
            q = pg_insert(f).values(message_id=mid,owner_id=uid,rating=rating,tag=tag,comment=comment)
            conn.execute(q.on_conflict_do_update(index_elements=['message_id'],set_={'rating':rating,'tag':tag,'comment':comment}))

    def reserve(self, uid, cid, question, evidence, key, kind='chat', region='MMR'):
        r, a, j = self.t['agent_requests'], self.t['audit_logs'], self.t['jobs']
        bucket = fingerprint(['agent',uid or 'automatic'])
        request_key = fingerprint([uid,kind,key])
        with self.repo.engine.begin() as conn:
            conn.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k':int(bucket[:15],16)})
            old = conn.execute(select(r).where(r.c.request_key == request_key)).mappings().first()
            if old:
                old_evidence = old['payload'].get('evidence', {})
                if (old['payload'].get('question') != question or old['conversation_id'] != cid
                        or any(old_evidence.get(k) != evidence.get(k) for k in ('days', 'end_date', 'region'))):
                    raise ValueError('同一请求键不能用于不同问题或会话')
                return old['id']
            if cid:
                self._conversation(conn,uid,cid,True)
                m = self.t['messages']
                if conn.execute(select(func.count()).select_from(m).where(m.c.conversation_id==cid)).scalar_one() >= 198:
                    raise LimitReached('会话过长，请新建会话')
                conn.execute(update(r).where(r.c.owner_id == uid, r.c.conversation_id == cid,
                    r.c.status.in_(['queued', 'running']), r.c.id.in_(select(j.c.id).where(
                    j.c.status == 'failed'))).values(status='failed'))
                if conn.execute(select(func.count()).select_from(r).where(r.c.conversation_id==cid,
                    r.c.status.in_(['queued','running']))).scalar_one():
                    raise LimitReached('当前会话仍有任务未完成')
            day = business_date().isoformat()
            # 额度审计不随会话删除，避免删除后绕过预算。仅存不可逆桶标识。
            used = conn.execute(select(func.count()).select_from(a).where(a.c.action=='agent_reserve',
                a.c.target==bucket, a.c.payload['day'].astext==day)).scalar_one()
            if used >= (5 if uid is None else 20):
                raise LimitReached('今日调用额度已用完')
            if kind == 'auto' and conn.execute(select(func.count()).select_from(r).where(r.c.kind=='auto',
                    r.c.region==region,r.c.created_at > utc_now()-timedelta(hours=6))).scalar_one():
                raise LimitReached('自动研判处于6小时冷却期')
            rid = uuid4().hex
            body = json_safe({'question':question, 'evidence':evidence})
            conn.execute(insert(r).values(id=rid,owner_id=uid,conversation_id=cid,request_key=request_key,
                         kind=kind,status='queued',region=region,payload=body,result={}))
            conn.execute(insert(j).values(id=rid,kind='assistant',owner_id=uid,idempotency_key=request_key,
                status='queued',payload={'request_id':rid},max_attempts=1))
            conn.execute(insert(a).values(id=uuid4().hex, action='agent_reserve',target=bucket,payload={'day':day}))
            if cid:
                conn.execute(insert(self.t['messages']).values(id=uuid4().hex,owner_id=uid,
                    conversation_id=cid,role='user',content=question,payload={'request_id':rid}))
            return rid

    def result(self, uid, rid):
        r,j = self.t['agent_requests'],self.t['jobs']
        with self.repo.engine.connect() as conn:
            row = conn.execute(select(r).where(r.c.id==rid,r.c.owner_id==uid)).mappings().first()
            if row is None:
                raise MissingRecord('任务不存在')
            result = dict(row)
            job = conn.execute(select(j.c.status,j.c.error).where(j.c.id==rid)).first()
            if job and job.status=='failed' and result['status']!='done':
                result['status']='failed'
                result['result']={'status':'failed','reason':'任务中断或未完成；不会自动重复调用，请重新提问'}
            return {k:result[k] for k in ('id','status','result','created_at','kind')}

    def record_usage(self, uid, feature):
        if feature not in {'map','trend','dashboard','graph','literature','workspace'}:
            raise ValueError('未知功能')
        t = self.t['feature_usage_daily']
        with self.repo.engine.begin() as conn:
            q = pg_insert(t).values(owner_id=uid,date=business_date(),feature=feature,count=1)
            conn.execute(q.on_conflict_do_update(index_elements=['owner_id','date','feature'],set_={'count':t.c.count+1}))

    def suggestion(self, uid):
        if not self.preferences(uid).get('suggestions'):
            return None
        t,a = self.t['feature_usage_daily'],self.t['audit_logs']
        with self.repo.engine.begin() as conn:
            lock = int(fingerprint(['suggestion',uid])[:15],16)
            conn.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k':lock})
            if conn.execute(select(func.count()).select_from(a).where(a.c.user_id==uid,
                    a.c.action=='feature_suggestion',a.c.created_at > utc_now()-timedelta(days=7))).scalar_one():
                return None
            rows = conn.execute(select(t.c.feature,func.sum(t.c.count).label('n')).where(t.c.owner_id==uid,
                t.c.date>=business_date()-timedelta(days=6)).group_by(t.c.feature)).all()
            total = sum(row.n for row in rows)
            top = max(rows,key=lambda row:row.n) if rows else None
            if not top or top.n < 5 or top.n / total < .8:
                return None
            target = 'graph' if top.feature=='map' else 'literature' if top.feature!='literature' else 'dashboard'
            conn.execute(insert(a).values(id=uuid4().hex,user_id=uid,action='feature_suggestion',payload={}))
            return {'feature':target,'message':'也可以试试相关证据与研究资料；此提示可在偏好中关闭。'}
