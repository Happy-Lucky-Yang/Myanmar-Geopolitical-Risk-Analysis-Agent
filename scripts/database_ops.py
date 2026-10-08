"""数据库核验、受保护导出及备份恢复；默认只预演，不安装软件、不切库、不删除。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.data_contract import json_safe, utc_now
from scripts.import_data import digest
from sqlalchemy.exc import SQLAlchemyError


@contextmanager
def read_snapshot(repo):
    from sqlalchemy import text
    with repo.engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
        with conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            conn.execute(text('SET LOCAL search_path = public, pg_catalog'))
            yield conn


def database_summary(repo, conn):
    from sqlalchemy import select, func, text
    counts = {name: conn.execute(select(func.count()).select_from(table)).scalar_one()
              for name, table in repo.schema.metadata.tables.items()}
    dates = {}
    for name, column in (('articles', 'business_date'), ('events', 'event_date'), ('daily_risk', 'date')):
        table = getattr(repo.schema, name)
        dates[name] = list(conn.execute(select(func.min(table.c[column]), func.max(table.c[column]))).one())
    invalid_geometry = {}
    for name in ('regions', 'events'):
        table = getattr(repo.schema, name)
        invalid_geometry[name] = conn.execute(select(func.count()).select_from(table).where(
            table.c.geom.is_not(None), (func.ST_SRID(table.c.geom) != 4326) | ~func.ST_IsValid(table.c.geom))).scalar_one()
    return json_safe({'counts': counts, 'date_ranges': dates, 'invalid_geometry': invalid_geometry,
                     'alembic_revision': conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),
                     'postgis_version': conn.execute(text('SELECT postgis_version()')).scalar_one()})


def target_directory(root, name, exists=False):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not name or Path(name).name != name or name in {'.', '..'} or ':' in name or '\\' in name:
        raise ValueError('产物名称必须是授权根目录内的单层目录名')
    target = root / name
    if target.is_symlink() or os.path.isjunction(target) or not target.resolve().is_relative_to(root):
        raise ValueError('不允许链接目录或越界路径')
    if exists:
        if not target.is_dir():
            raise ValueError('备份目录不存在')
    elif target.exists():
        raise ValueError('禁止覆盖已有产物目录')
    return target


def write_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(json_safe(value), handle, ensure_ascii=False, indent=2, allow_nan=False)


def export_business(repo, target):
    """仅导出正式/legacy业务数据；账号、私密输入和任务必须通过完整备份保全。"""
    from sqlalchemy import select
    target.mkdir(mode=0o700)
    files = []
    def save(relative, rows, jsonl=False, record_count=None):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if jsonl:
            with path.open('x', encoding='utf-8') as handle:
                for row in rows:
                    handle.write(json.dumps(json_safe(row), ensure_ascii=False, allow_nan=False) + '\n')
        else:
            write_json(path, rows)
        files.append({'file': relative, 'records': len(rows) if record_count is None else record_count,
                      'sha256': digest(path)})
    with read_snapshot(repo) as conn:
        summary = database_summary(repo, conn)
        s = repo.schema
        articles = list(conn.execute(select(s.articles.c.payload)).scalars())
        save('raw/myanmar_news_export.json', [r for r in articles if r.get('run_kind', 'legacy') not in {'manual', 'demo'}])
        risks = list(conn.execute(select(s.daily_risk.c.payload).where(s.daily_risk.c.data_domain == 'observed')).scalars())
        save('processed/daily_risk.jsonl', risks, True)
        legacy = list(conn.execute(select(s.analysis_runs.c.payload).where(
            s.analysis_runs.c.run_kind == 'legacy', s.analysis_runs.c.owner_id.is_(None))).scalars())
        save('processed/risk_scores.jsonl', [r for r in legacy if 'risk_score' in r and 'date' in r], True)
        runs = list(conn.execute(select(s.analysis_runs.c.payload).where(
            s.analysis_runs.c.run_kind.in_(['live', 'existing']), s.analysis_runs.c.owner_id.is_(None))).scalars())
        save('archive/formal_analysis_runs.jsonl', runs, True)
        events = list(conn.execute(select(s.events.c.payload).where(s.events.c.run_kind.in_(['live', 'existing']))).scalars())
        checkpoints = dict(conn.execute(select(s.ingestion_checkpoints.c.source, s.ingestion_checkpoints.c.watermark)).all())
        save('processed/gdelt_event_store.json', {'events': events, 'watermark': checkpoints.get('gdelt'),
             'event_count': len(events), 'updated_at': utc_now().isoformat()}, record_count=len(events))
        obs = s.indicator_observations
        query = select(obs, s.sources.c.name.label('source')).join(s.sources, obs.c.source_id == s.sources.c.id).where(
            obs.c.run_kind.in_(['live', 'existing']))
        fields = ('id', 'source', 'indicator', 'region', 'period_start', 'period_end', 'frequency',
                  'value', 'unit', 'quality', 'dataset_version', 'product', 'run_kind')
        from utils.data_contract import normalize_observation
        observations = []
        for row in conn.execute(query).mappings():
            original = normalize_observation(row['payload'])
            if any(json_safe(row[key]) != original[key] for key in fields):
                raise ValueError('观测类型化字段与原值不一致；导出未完成，请复核原始证据')
            observations.append(original)
        save('external/indicator_observations.jsonl', observations, True)
    manifest = {'status': 'complete', 'kind': 'business_export', 'created_at': utc_now().isoformat(),
                'database': summary, 'files': files,
                'omitted': ['用户及口令哈希', '私密分析', '任务和审计', 'legacy事件及观测', '空间边界和部分关系表'],
                'warning': '这是用于并列核验的业务导出，不是完整备份；不能据此声称无损切回文件后端。'}
    write_json(target / 'manifest.json', manifest)
    return manifest


def pg_environment(url):
    """凭据仅进入子进程环境，不出现在命令行、返回值或日志文本中。"""
    if (url.get_backend_name() != 'postgresql' or not url.host or not url.username
            or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}', url.database or '')
            or any(c.isspace() or c in ',/\\' for c in url.host)):
        raise ValueError('运维URL需要显式PostgreSQL主机、用户和简单库名，不接受多主机或连接串库名')
    env = os.environ.copy()
    for key in ('PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGUSER', 'PGDATABASE', 'PGPASSWORD', 'PGSERVICE', 'PGOPTIONS'):
        env.pop(key, None)
    if set(url.query) - {'sslmode'}:
        raise ValueError('数据库运维URL仅支持sslmode查询参数；不允许隐藏search_path或其他目标覆盖')
    env.update(PGHOST=url.host or 'localhost', PGPORT=str(url.port or 5432),
               PGUSER=url.username or '', PGDATABASE=url.database or '', PGCONNECT_TIMEOUT='5')
    if url.password is not None:
        env['PGPASSWORD'] = url.password
    if url.query.get('sslmode'):
        if url.query['sslmode'] not in {'disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'}:
            raise ValueError('sslmode必须为单个有效模式')
        env['PGSSLMODE'] = url.query['sslmode']
    return env


def pg_binary(name, pg_bin=None):
    candidate = Path(pg_bin) / (name + ('.exe' if os.name == 'nt' else '')) if pg_bin else None
    binary = str(candidate) if candidate and candidate.is_file() else shutil.which(name) if not pg_bin else None
    if not binary:
        raise RuntimeError('未找到PostgreSQL客户端；请指定已批准安装的 --pg-bin 目录')
    return binary


def run_pg(binary, arguments, env, stdout=subprocess.PIPE):
    try:
        subprocess.run([binary, *arguments], env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                       stderr=subprocess.PIPE, check=True, timeout=3600)
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError('PostgreSQL客户端操作失败；未输出可能含敏感信息的原始错误，保留失败产物供本机复核') from None


def backup_database(repo, target, url, pg_bin=None):
    from sqlalchemy import text
    binary, env = pg_binary('pg_dump', pg_bin), pg_environment(url)
    target.mkdir(mode=0o700)
    archive = target / 'database.dump'
    with read_snapshot(repo) as conn:
        summary = database_summary(repo, conn)
        snapshot = conn.execute(text('SELECT pg_export_snapshot()')).scalar_one()
        with archive.open('xb') as handle:
            run_pg(binary, ['--format=custom', '--no-owner', '--no-privileges', '--schema=public',
                            '--strict-names', '--snapshot=' + snapshot], env, stdout=handle)
    manifest = {'status': 'complete', 'kind': 'postgres_backup', 'schema': 'public',
                'created_at': utc_now().isoformat(), 'sha256': digest(archive), 'database': summary}
    write_json(target / 'manifest.json', manifest)
    return manifest


def backup_manifest(target):
    archive, manifest_path = target / 'database.dump', target / 'manifest.json'
    if (archive.is_symlink() or manifest_path.is_symlink()
            or not archive.is_file() or not manifest_path.is_file()):
        raise ValueError('备份文件缺失或为链接')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('status') != 'complete'
            or manifest.get('kind') != 'postgres_backup' or manifest.get('schema') != 'public'
            or not isinstance(manifest.get('database'), dict)
            or not all(key in manifest['database'] for key in ('counts', 'date_ranges', 'invalid_geometry', 'alembic_revision'))
            or manifest.get('sha256') != digest(archive)):
        raise ValueError('备份未完成、类型错误或校验失败，禁止恢复')
    return manifest


def restore_database(repo, target, url, pg_bin=None):
    from sqlalchemy import text
    if not (url.database or '').endswith('_test'):
        raise ValueError('恢复只能针对名称以_test结尾的新建隔离库')
    manifest = backup_manifest(target)
    binary, env = pg_binary('pg_restore', pg_bin), pg_environment(url)
    with read_snapshot(repo) as conn:
        conn.execute(text('SELECT postgis_version()')).scalar_one()
        existing = conn.execute(text("""SELECT count(*) FROM (
            SELECT c.oid, 'pg_class'::regclass AS catalog FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f','c')
            UNION ALL
            SELECT p.oid, 'pg_proc'::regclass FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
            UNION ALL
            SELECT t.oid, 'pg_type'::regclass FROM pg_type t
            JOIN pg_namespace n ON n.oid=t.typnamespace
            WHERE n.nspname='public' AND t.typtype IN ('d','e','r')
            ) objects WHERE NOT EXISTS (SELECT 1 FROM pg_depend d
                WHERE d.objid=objects.oid AND d.classid=objects.catalog AND d.deptype='e')""")).scalar_one()
        if existing:
            raise ValueError('隔离库public已有非扩展对象，禁止覆盖恢复')
    run_pg(binary, ['--dbname=' + url.database, '--no-owner', '--no-privileges', '--exit-on-error',
                    '--single-transaction', str(target / 'database.dump')], env)
    with read_snapshot(repo) as conn:
        actual = database_summary(repo, conn)
    expected = manifest['database']
    matched = all(actual[key] == expected[key] for key in ('counts', 'date_ranges', 'invalid_geometry', 'alembic_revision'))
    report = {'status': 'verified' if matched else 'mismatch', 'expected': expected, 'actual': actual,
              'warning': '隔离恢复库不得启动worker或共享上线；仍需角色授权、空间查询和私密权限验收。'}
    write_json(target / ('restore-check-' + uuid4().hex + '.json'), report)
    if not matched:
        raise RuntimeError('恢复后核验不一致；隔离库保留，不切换主库')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='只预演，显式apply和精确库名确认后执行；不删除、不切主库')
    parser.add_argument('action', choices=['verify', 'export', 'backup', 'restore'])
    parser.add_argument('--root', type=Path, required=True, help='已授权且受保护的产物根目录')
    parser.add_argument('--name', help='全新产物目录名；restore时指定已有备份目录名')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-database')
    parser.add_argument('--pg-bin', type=Path)
    args = parser.parse_args(argv)
    name = args.name or (args.action + '-' + utc_now().strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    if args.action == 'restore' and not args.name:
        parser.error('restore必须指定备份目录名')
    try:
        target = target_directory(args.root, name, exists=args.action == 'restore')
        if args.action == 'restore':
            backup_manifest(target)
        if not args.apply:
            print(json.dumps({'mode': 'dry-run', 'action': args.action, 'writes': 0,
                              'requires': 'DATABASE_TOOL_URL、精确库名确认及受保护目录；restore仅限空_test库'}, ensure_ascii=False))
            return 0
        from sqlalchemy.engine import make_url
        from storage.repository import PostgresRepository
        raw_url = os.environ.get('DATABASE_TOOL_URL', '')
        if not raw_url:
            raise ValueError('必须设置DATABASE_TOOL_URL')
        url = make_url(raw_url)
        if not url.database or url.database != args.confirm_database:
            raise ValueError('必须精确确认目标库名称')
        pg_environment(url)
        if any(os.environ.get(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS')):
            raise ValueError('请在独立进程中清除PGHOSTADDR/PGSERVICE/PGOPTIONS，保证摘要与客户端连接目标一致')
        url = url.set(port=url.port or 5432)
        repo = PostgresRepository(url.render_as_string(hide_password=False))
        try:
            if args.action == 'verify':
                with read_snapshot(repo) as conn:
                    report = database_summary(repo, conn)
                target.mkdir(mode=0o700)
                write_json(target / 'verification.json', report)
            elif args.action == 'export':
                export_business(repo, target)
            elif args.action == 'backup':
                backup_database(repo, target, url, args.pg_bin)
            else:
                restore_database(repo, target, url, args.pg_bin)
        finally:
            repo.engine.dispose()
        print(json.dumps({'mode': 'apply', 'action': args.action, 'status': 'complete'}, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, SQLAlchemyError) as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__,
                          'note': '未切库或删除；检查参数、客户端、目录和目标库权限。'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
