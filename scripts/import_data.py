"""授权目录导入：默认预演，显式 apply 和目标库名称确认后才写库。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.data_contract import (article_identity, business_date, fingerprint, json_safe,
                                 valid_risk_record, timestamp_utc)

SKIP = {'.git', '.venv', 'venv', 'venv312', 'py312', 'node_modules', '__pycache__', 'manual', 'demo'}
NEWS = ('myanmar_news_', 'gdelt_news_', 'myanmar_now_', 'rss_news_')


def file_kind(path):
    name = path.name
    if name.startswith(NEWS) and path.suffix.lower() in {'.json', '.csv'}:
        return 'articles'
    if name in {'risk_scores.jsonl', 'daily_risk.jsonl'}:
        return 'risk'
    if name in {'gdelt_event_store.json', 'historical_events.json'}:
        return 'events'
    if name == 'source_health.json':
        return 'health'
    if name == 'indicator_observations.jsonl':
        return 'observations'
    return None


def candidates(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('root 必须是授权数据目录')
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP and not d.startswith('.')
                   and not Path(current, d).is_symlink() and not os.path.isjunction(Path(current, d))]
        for name in sorted(files):
            path = Path(current, name)
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            kind = file_kind(path)
            if kind:
                yield path, kind


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def rows(path, kind, content=None):
    """JSONL 用物理行号；JSON 数组/CSV 用从1开始的记录号。"""
    try:
        decoded = (path.read_bytes() if content is None else content).decode('utf-8-sig')
    except UnicodeError:
        yield 1, None, '文件不是有效UTF-8文本'
        return
    if path.suffix == '.jsonl':
        with io.StringIO(decoded) as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    yield number, json.loads(line), None
                except ValueError:
                    yield number, None, 'JSON语法错误'
        return
    if path.suffix == '.csv':
        with io.StringIO(decoded, newline='') as handle:
            for number, row in enumerate(csv.DictReader(handle), 1):
                yield number, row, None
        return
    try:
        data = json.loads(decoded)
        if kind == 'events' and isinstance(data, dict):
            data = data.get('events', [])
        if kind == 'health' and isinstance(data, dict):
            data = [{'source': k, 'health': v} for k, v in data.items()]
        if not isinstance(data, list):
            raise ValueError('需要记录数组')
        for number, row in enumerate(data, 1):
            yield number, row, None
    except (ValueError, UnicodeError):
        yield 1, None, 'JSON文件格式错误'


def read_frozen(path):
    before = path.stat()
    snapshot = path.read_bytes()
    after = path.stat()
    if any(getattr(before, key) != getattr(after, key) for key in ('st_mtime_ns', 'st_ctime_ns', 'st_size', 'st_ino')):
        raise RuntimeError('文件读取期间发生变化；请冻结输入后重试')
    return snapshot, before.st_mtime


def normalize(row, kind, path, file_mtime=None):
    if not isinstance(row, dict):
        raise ValueError('记录不是对象')
    row = dict(row)
    if kind == 'articles':
        if not any(isinstance(row.get(k), str) and row[k].strip() for k in ('content', 'text', 'title')):
            raise ValueError('文章缺少标题和正文')
        row.setdefault('content', row.get('text', ''))
        value = row.get('published_at') or row.get('pub_time') or row.get('date')
        day = business_date(value)
        row['pub_time'] = day.isoformat() if day else ''
        row['date_precision'] = 'timestamp' if timestamp_utc(value) else 'day' if day else 'missing'
        return row, article_identity(row), day is None
    if kind == 'risk':
        if not valid_risk_record(row):
            raise ValueError('风险记录日期、评分或详情无效')
        row.setdefault('run_kind', 'legacy')
        row.setdefault('algorithm_version', 'legacy')
        if row.get('run_id'):
            row.setdefault('original_run_id', row.pop('run_id'))
        if row['run_kind'] not in {'live', 'existing', 'legacy'}:
            raise ValueError('手工或演示记录不导入正式业务域')
        stamp = timestamp_utc(row.get('recorded_at')) or timestamp_utc(row.get('analyzed_at'))
        row['timestamp_quality'] = 'original' if stamp else 'legacy_file_time'
        row['recorded_at'] = (stamp or datetime.fromtimestamp(
            path.stat().st_mtime if file_mtime is None else file_mtime, timezone.utc)).isoformat()
        return row, fingerprint(row), False
    if kind == 'events':
        day = business_date(row.get('date') or row.get('sqldate'))
        if day is None:
            raise ValueError('事件缺少有效日期')
        row['date'] = day.isoformat()
        row.setdefault('run_kind', 'legacy' if path.name == 'historical_events.json' else 'existing')
        row.setdefault('source', 'historical' if path.name == 'historical_events.json' else 'gdelt')
        return row, fingerprint({'source': row['source'], 'id': row.get('event_id') or row.get('id') or fingerprint(row)}), False
    if kind == 'observations':
        from utils.data_contract import normalize_observation
        row = normalize_observation(row)
        if row['run_kind'] in {'manual', 'demo'}:
            raise ValueError('手工或演示观测不导入正式业务目录')
        return row, row['id'], False
    if kind == 'health':
        if not row.get('source'):
            raise ValueError('来源健康记录缺少来源名')
        return row, fingerprint(row), False
    raise ValueError('不支持的格式')


def apply_row(repo, conn, kind, row, identity):
    if kind == 'articles':
        return repo.save_articles([row], conn)[0]
    if kind == 'risk':
        record = {**row, 'run_id': identity}
        repo.save_analysis(record, conn)
        return repo.save_risk(record, conn)
    if kind == 'events':
        return repo.save_events([row], source=row['source'], conn=conn)[0]
    if kind == 'observations':
        return repo.save_observations([row], conn)[0]
    sid = repo.source(conn, row['source'])
    repo._insert(conn, repo.schema.source_runs, {'id': identity, 'source_id': sid,
                 'status': 'legacy', 'payload': json_safe(row)})
    return identity


def import_directory(root, repo=None):
    root = Path(root).resolve(strict=True)
    report = {'mode': 'apply' if repo else 'dry-run', 'files': [], 'valid': 0,
              'duplicate': 0, 'missing_date': 0, 'invalid': 0, 'written': 0}
    seen = set()
    observation_versions = {}
    for path, kind in candidates(root):
        snapshot, file_mtime = read_frozen(path)
        checksum = hashlib.sha256(snapshot).hexdigest()
        name = path.relative_to(root).as_posix()
        batch_id = fingerprint({'name': name, 'sha256': checksum})
        detail = {'file': name, 'kind': kind, 'sha256': checksum, 'bytes': len(snapshot),
                  'batch_id': batch_id, 'file_mtime': file_mtime, 'errors': [], 'records': 0,
                  'position_type': 'line' if path.suffix == '.jsonl' else 'record'}
        if repo:
            from sqlalchemy import select
            with repo.engine.begin() as conn:
                batches = repo.schema.import_batches
                repo._insert(conn, batches, {'id': batch_id, 'file_hash': checksum,
                             'file_name': name, 'status': 'running', 'payload': {'kind': kind, 'file_mtime': file_mtime}})
                previous = conn.execute(select(batches.c.payload).where(batches.c.id == batch_id).with_for_update()).scalar_one()
                detail['file_mtime'] = previous.get('file_mtime', file_mtime)
        for number, raw, error in rows(path, kind, content=snapshot):
            detail['records'] += 1
            try:
                if error:
                    raise ValueError(error)
                row, identity, missing = normalize(raw, kind, path, detail['file_mtime'])
                report['missing_date'] += int(missing)
                key = (kind, identity)
                if kind == 'observations':
                    series = tuple(row[k] for k in ('source', 'indicator', 'region', 'period_start',
                                    'frequency', 'product', 'dataset_version'))
                    signature = fingerprint(row)
                    if any(k in observation_versions and observation_versions[k] != signature for k in (identity, series)):
                        raise ValueError('同ID或同源周期版本观测冲突；需要人工复核')
                    observation_versions[identity] = observation_versions[series] = signature
                if key in seen:
                    report['duplicate'] += 1
                else:
                    report['valid'] += 1
                    seen.add(key)
                if repo:
                    from sqlalchemy import select
                    item_id = fingerprint({'batch': batch_id, 'row': number})
                    with repo.engine.begin() as conn:
                        batch = repo.schema.import_batches
                        conn.execute(select(batch.c.id).where(batch.c.id == batch_id).with_for_update())
                        table = repo.schema.import_items
                        status = conn.execute(select(table.c.status).where(table.c.id == item_id)).scalar_one_or_none()
                        if status == 'ok':
                            continue
                        try:
                            with conn.begin_nested():
                                record_id = apply_row(repo, conn, kind, row, identity)
                            status, error = 'ok', None
                            report['written'] += 1
                        except Exception as exc:
                            status, error = 'failed', type(exc).__name__
                        repo._upsert(conn, table, {'id': item_id, 'batch_id': batch_id, 'row_number': number,
                                     'status': status, 'record_id': record_id if status == 'ok' else None,
                                     'error': error}, ['batch_id', 'row_number'])
                        if error:
                            report['invalid'] += 1
                            detail['errors'].append({'position': number, 'error': error})
            except (ValueError, TypeError, UnicodeError) as exc:
                report['invalid'] += 1
                detail['errors'].append({'position': number, 'error': str(exc)})
                if repo:
                    with repo.engine.begin() as conn:
                        repo._upsert(conn, repo.schema.import_items, {
                            'id': fingerprint({'batch': batch_id, 'row': number}),
                            'batch_id': batch_id, 'row_number': number, 'status': 'invalid',
                            'record_id': None, 'error': type(exc).__name__}, ['batch_id', 'row_number'])
        if repo:
            from sqlalchemy import update
            with repo.engine.begin() as conn:
                conn.execute(update(repo.schema.import_batches).where(repo.schema.import_batches.c.id == batch_id)
                             .values(status='partial' if detail['errors'] else 'done', payload=json_safe(detail)))
        report['files'].append(detail)
    return report


def verify_directory(root, repo):
    """只读核验原文件、批次行号与入库原值；不会把当前日快照代替历史原分析。"""
    from sqlalchemy import select, text
    from utils.data_contract import source_evidence
    root = Path(root).resolve(strict=True)
    result = {'mode': 'verify', 'written': 0, 'verified': 0, 'invalid': 0,
              'missing': 0, 'mismatched': 0, 'files': []}
    targets = {'articles': repo.schema.articles, 'events': repo.schema.events,
               'health': repo.schema.source_runs, 'observations': repo.schema.indicator_observations}
    with repo.engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
        with conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            for path, kind in candidates(root):
                snapshot, file_mtime = read_frozen(path)
                name = path.relative_to(root).as_posix()
                batch_id = fingerprint({'name': name, 'sha256': hashlib.sha256(snapshot).hexdigest()})
                batches = repo.schema.import_batches
                batch = conn.execute(select(batches.c.payload).where(batches.c.id == batch_id)).scalar_one_or_none()
                file_mtime = (batch or {}).get('file_mtime', file_mtime)
                detail = {'file': name, 'batch_id': batch_id, 'errors': []}
                for number, raw, error in rows(path, kind, snapshot):
                    try:
                        if error:
                            raise ValueError(error)
                        row, identity, _ = normalize(raw, kind, path, file_mtime)
                    except (ValueError, TypeError, UnicodeError):
                        result['invalid'] += 1
                        detail['errors'].append({'position': number, 'status': 'invalid_input'})
                        continue
                    items = repo.schema.import_items
                    item = conn.execute(select(items.c.status, items.c.record_id).where(
                        items.c.batch_id == batch_id, items.c.row_number == number)).first()
                    if item is None or item.status != 'ok':
                        result['missing'] += 1
                        detail['errors'].append({'position': number, 'status': 'not_imported'})
                        continue
                    table = repo.schema.analysis_runs if kind == 'risk' else targets[kind]
                    target_id = identity if kind == 'risk' else item.record_id
                    stored = conn.execute(select(table.c.payload).where(table.c.id == target_id)).scalar_one_or_none()
                    same = stored is not None
                    if same and kind == 'articles':
                        expected_evidence = {(e['source'], e['url']) for e in source_evidence(row)}
                        actual_evidence = {(e['source'], e['url']) for e in source_evidence(stored)}
                        same = article_identity(stored) == identity and expected_evidence <= actual_evidence
                        day = business_date(row.get('published_at') or row.get('pub_time'))
                        actual_day = business_date(stored.get('published_at') or stored.get('pub_time'))
                        same = same and (day is None or (actual_day is not None and actual_day <= day))
                    elif same:
                        same = all(key in stored and fingerprint(stored[key]) == fingerprint(value) for key, value in row.items())
                        if kind == 'observations':
                            typed = conn.execute(select(table).where(table.c.id == target_id)).mappings().first()
                            same = same and all(json_safe(typed[key]) == row[key] for key in (
                                'indicator', 'region', 'period_start', 'period_end', 'frequency', 'value',
                                'unit', 'quality', 'dataset_version', 'product', 'run_kind'))
                    if same:
                        result['verified'] += 1
                    else:
                        result['mismatched'] += 1
                        detail['errors'].append({'position': number, 'status': 'stored_value_changed_or_missing'})
                result['files'].append(detail)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='数据导入预演；不扫描密钥、笔记和虚拟环境')
    parser.add_argument('--root', type=Path, required=True)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true')
    actions.add_argument('--verify', action='store_true', help='只读核验文件与已有导入记录，不写业务库')
    parser.add_argument('--confirm-database')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    args.root = args.root.resolve(strict=True)
    if args.output:
        args.output = args.output.resolve()
        if not args.output.is_relative_to(args.root) or args.output.exists() or file_kind(args.output):
            parser.error('导入报告必须为授权目录内的全新非业务文件，不能覆盖已有资料')
        if not args.output.parent.is_dir():
            parser.error('报告父目录不存在')
    repo = None
    if args.apply or args.verify:
        from sqlalchemy.engine import make_url
        url = os.environ.get('IMPORT_DATABASE_URL', '')
        if not url or not args.confirm_database or make_url(url).database != args.confirm_database:
            parser.error('apply/verify 需要 IMPORT_DATABASE_URL 且 --confirm-database 与库名完全一致')
        from storage.repository import PostgresRepository
        repo = PostgresRepository(url)
    try:
        report = verify_directory(args.root, repo) if args.verify else import_directory(args.root, repo)
    finally:
        if repo:
            repo.engine.dispose()
    text = json.dumps(json_safe(report), ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        # 报告可能含私密文件名，只允许输出到授权数据目录内。
        if not args.output.resolve().is_relative_to(args.root.resolve()):
            parser.error('导入报告必须留在授权数据目录内')
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(text)
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}, ensure_ascii=False))
    return 2 if any(report.get(key, 0) for key in ('invalid', 'missing', 'mismatched')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
