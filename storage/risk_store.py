"""多源输入/冻结基准/面板单机与PostgreSQL存储，显式后端、不可变版本。"""
import json
import os
import tempfile
import threading
from pathlib import Path
from utils.data_contract import fingerprint, json_safe

_LOCK = threading.RLock()
NAMES = ('risk_source_batches', 'risk_calibrations', 'risk_panels', 'event_links')


class RiskStore:
    def __init__(self, repo=None):
        from storage.repository import get_repository
        from utils.config import get_data_paths
        self.repo = repo or get_repository()
        self.path = Path(get_data_paths()['external']) / 'risk_state.json'

    def ready(self):
        if self.repo is None:
            return True
        from sqlalchemy import inspect
        return set(NAMES) <= set(inspect(self.repo.engine).get_table_names())

    def _read(self):
        if not self.path.exists():
            return {name:{} for name in NAMES}
        if self.path.is_symlink() or self.path.stat().st_size > 128*1024*1024:
            raise ValueError('风险存储越界或过大')
        result = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(result, dict) or set(result) != set(NAMES) or any(not isinstance(v,dict) for v in result.values()):
            raise ValueError('风险存储损坏，禁止覆盖')
        return result

    def load(self, name):
        if name not in NAMES:
            raise ValueError('未知风险集合')
        if self.repo:
            from sqlalchemy import select
            with self.repo.engine.connect() as conn:
                rows = list(conn.execute(select(self.repo.schema.metadata.tables[name].c.payload)).scalars())
        else:
            with _LOCK:
                rows = list(self._read()[name].values())
        for row in rows:
            if not isinstance(row,dict) or not row.get('id'):
                raise ValueError('风险记录损坏')
            content = {k:v for k,v in row.items() if k!='id' and not (name=='risk_source_batches' and k=='acquired_at')}
            if fingerprint(content)!=row['id']:
                raise ValueError('风险记录摘要不匹配，不读取损坏版本')
        return sorted(rows, key=lambda r:r['id'])

    def revision(self):
        # 独立修订，不冒充旧日风险或全局原始数据的版本。
        return fingerprint({name:self.load(name) for name in NAMES})

    def save(self, name, rows):
        if name not in NAMES or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) or len(r['id'])!=64 for r in rows):
            raise ValueError('风险记录集合或ID无效')
        rows = json_safe(rows)
        for row in rows:
            content = {k:v for k,v in row.items() if k!='id' and not (name=='risk_source_batches' and k=='acquired_at')}
            if fingerprint(content) != row['id']:
                raise ValueError('风险输入摘要不匹配，拒绝损坏或篡改记录')
        if self.repo:
            from sqlalchemy import select
            table = self.repo.schema.metadata.tables[name]
            with self.repo.write_transaction() as conn:
                for row in rows:
                    values = {'id':row['id'], 'payload':row}
                    if name == 'risk_source_batches':
                        m = row['manifest']
                        values['source_version_key'] = fingerprint([row['source'], m['dataset_version'], m['region'], m['start'], m['end']])
                    if name == 'risk_panels':
                        values.update({k:row[k] for k in ('frequency','region','algorithm_version')})
                    if name == 'event_links':
                        values['reviewer_id'] = row.get('reviewer_id')
                    self.repo._insert(conn, table, values)
                    current = conn.execute(select(table.c.payload).where(table.c.id==row['id']).with_for_update()).scalar_one_or_none()
                    ignored = {'acquired_at'} if name == 'risk_source_batches' else set()
                    if current is None or fingerprint({k:v for k,v in current.items() if k not in ignored}) != fingerprint({k:v for k,v in row.items() if k not in ignored}):
                        raise ValueError('不可变风险记录冲突，未覆盖')
        else:
            with _LOCK:
                data = self._read()
                for row in rows:
                    previous = data[name].get(row['id'])
                    if previous and fingerprint(previous) != fingerprint(row):
                        raise ValueError('不可变风险记录冲突，未覆盖')
                    data[name][row['id']] = row
                self.path.parent.mkdir(parents=True,exist_ok=True)
                if self.path.parent.is_symlink():
                    raise ValueError('风险存储目录不能是链接')
                fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='risk-', suffix='.tmp')
                # 临时文件仅用于原子提交；失败留存供人工核查。
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump(data,handle,ensure_ascii=False,allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
        return len(rows)

    def save_batch(self, value):
        with _LOCK:
            m = value['manifest']
            same = [b for b in self.load('risk_source_batches') if b['source']==value['source'] and
                    b['manifest']['dataset_version']==m['dataset_version'] and
                    all(b['manifest'][k]==m[k] for k in ('region','start','end'))]
            if any(b['id'] != value['id'] for b in same):
                raise ValueError('同源同周期同版本发生变化，请显式使用新修订版本')
            existing = next((b for b in same if b['id']==value['id']), None)
            # acquired_at属于首次取得记录，不因重试改变。
            return self.save('risk_source_batches', [existing or value])


def execute_risk_job(job):
    from utils.product_features import enabled
    if not enabled('risk_panels'):
        raise RuntimeError('风险面板未启用')
    from utils.security import user_by_id
    if not job.get('owner_id'):
        raise PermissionError('风险面板任务必须有管理员发起人')
    user = user_by_id(job['owner_id'])
    if not user or user['role'] != 'admin':
        raise PermissionError('任务发起人已无管理权限')
    from analyzer.risk_panels import build_panels
    from data.event_store import get_event_store
    store = RiskStore()
    if not store.ready():
        raise RuntimeError('风险面板迁移未完成')
    args = job['payload']
    from risk_api import options
    options(args)
    calibration = next((r for r in store.load('risk_calibrations') if r['id']==args.get('calibration_id')),None)
    if args.get('calibration_id') and calibration is None:
        raise ValueError('基准不存在')
    revision = store.revision()
    panels = build_panels(store.load('risk_source_batches'),get_event_store().load(days=None),
        frequency=args.get('frequency','monthly'), count=args.get('count',12), end_date=args.get('end_date'),
        region=args.get('region','MMR'),mode=args.get('mode','formal'),parameters=args.get('parameters'),
        calibration=calibration,batch_ids=args.get('batch_ids'))
    if store.revision()!=revision:
        raise ValueError('输入读取期间变化，请重新提交')
    store.save('risk_panels', panels)
    return {'success':True,'data':{'panel_ids':[p['id'] for p in panels], 'count':len(panels)}}
