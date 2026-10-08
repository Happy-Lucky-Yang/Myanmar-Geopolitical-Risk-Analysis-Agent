"""离线验证迁移SQL、导入白名单和存储失败边界，不代替PostGIS集成。"""
import io
import json
from pathlib import Path
from datetime import datetime

import pytest


def test_alembic_offline_postgis_sql():
    from alembic.config import Config
    from alembic import command
    output = io.StringIO()
    cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'), output_buffer=output)
    command.upgrade(cfg, 'head', sql=True)
    sql = output.getvalue()
    assert 'CREATE EXTENSION IF NOT EXISTS postgis' in sql
    assert 'geometry(MULTIPOLYGON,4326)' in sql
    assert 'USING gist' in sql
    assert 'CREATE TABLE jobs' in sql
    assert 'daily_risk_version' in sql
    assert 'CREATE TABLE data_revision' in sql
    assert 'CREATE FUNCTION public.bump_data_revision()' in sql
    assert 'ON public.articles' in sql and 'ON public.indicator_observations' in sql
    assert 'FOR EACH STATEMENT EXECUTE FUNCTION' in sql
    assert 'indicator_series_period' in sql and 'ADD COLUMN period_end DATE' in sql
    from storage import schema, schema_v1
    assert 'period_end' not in schema_v1.indicator_observations.c
    assert 'period_end' in schema.indicator_observations.c
    assert 'data_revision' in schema.metadata.tables
    assert 'data_revision' not in schema_v1.metadata.tables


def test_postgres_never_falls_back(monkeypatch):
    from storage.repository import get_repository, PostgresRepository
    monkeypatch.setenv('STORAGE_BACKEND', 'postgres')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(RuntimeError):
        get_repository()
    with pytest.raises(ValueError):
        PostgresRepository('sqlite://')


def test_import_preview_whitelist_and_bad_rows(tmp_path):
    from scripts.import_data import import_directory
    (tmp_path / '.env').write_text('ignored', encoding='utf-8')
    (tmp_path / 'notes.json').write_text('ignored', encoding='utf-8')
    (tmp_path / 'manual').mkdir()
    (tmp_path / 'manual' / 'daily_risk.jsonl').write_text('{}', encoding='utf-8')
    (tmp_path / 'myanmar_news_test.json').write_text(json.dumps([
        {'title': 'same', 'pub_time': '2026-01-01'}, {'title': 'same'}, {'content': 'missing date'}, 3]), encoding='utf-8')
    (tmp_path / 'risk_scores.jsonl').write_text('{broken\n' + json.dumps({'date': '2026-01-01', 'risk_score': 0}) + '\n', encoding='utf-8')
    report = import_directory(tmp_path)
    assert report['mode'] == 'dry-run' and report['written'] == 0
    assert len(report['files']) == 2
    assert report['duplicate'] == 1 and report['invalid'] == 2
    assert report['missing_date'] == 2
    assert report['files'][1]['errors'][0]['position'] == 1


def observation(**changes):
    return {'source': 'fixture', 'indicator': 'nightlight', 'frequency': 'monthly',
            'region': 'MMR', 'period_start': '2026-01-01', 'period_end': '2026-02-01',
            'unit': 'nW/cm²/sr', 'value': 0, 'quality': 'observed',
            'run_kind': 'existing', 'dataset_version': 'fixture-v1', **changes}


@pytest.mark.parametrize('changes', [{'quality': None}, {'unit': ''}, {'value': float('nan')},
    {'value': True}, {'value': None}, {'run_kind': None}, {'dataset_version': None},
    {'period_end': '2026-01-31'}, {'period_end_exclusive': False}, {'period_start': '2026-01-01T00:00:00Z'}])
def test_observation_contract_rejects_ambiguous_input(changes):
    from utils.data_contract import normalize_observation
    with pytest.raises(ValueError):
        normalize_observation(observation(**changes))


def test_observation_preview_preserves_versions_and_reports_conflicts(tmp_path):
    from scripts.import_data import import_directory
    rows = [observation(), observation(), observation(value=3), observation(dataset_version='v2', value=4),
            observation(run_kind='demo'), observation(unit=None)]
    (tmp_path / 'indicator_observations.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    result = import_directory(tmp_path)
    assert result['valid'] == 2 and result['duplicate'] == 1 and result['invalid'] == 3
    assert result['written'] == 0
    from utils.data_contract import normalize_observation
    assert normalize_observation(observation())['value'] == 0
    assert normalize_observation(observation(value=None, quality='missing'))['value'] is None


def test_import_output_rejected_before_apply(tmp_path, monkeypatch):
    import scripts.import_data as importer
    called = []
    monkeypatch.setattr(importer, 'import_directory', lambda *args: called.append(True))
    source = tmp_path / 'notes.json'
    source.write_text('preserve', encoding='utf-8')
    with pytest.raises(SystemExit):
        importer.main(['--root', str(tmp_path), '--apply', '--output', str(source)])
    assert not called and source.read_text(encoding='utf-8') == 'preserve'


def test_source_evidence_and_earliest_date_survive_reprocessing():
    from analyzer.data_loader import DataLoader
    loader = DataLoader()
    rows = [
        {'content': 'same report', 'source': 'A', 'url': 'https://a.test/1', 'published_at': '2026-01-03T01:00:00Z'},
        {'content': 'same report', 'source': 'B', 'url': 'https://b.test/2', 'pub_time': '2026-01-01'}]
    merged = loader.preprocess(rows)
    again = loader.preprocess(merged)[0]
    assert again['pub_time'] == '2026-01-01' and again['published_at'] is None
    assert again['source_evidence'] == [{'source': 'A', 'url': 'https://a.test/1'}, {'source': 'B', 'url': 'https://b.test/2'}]
    assert rows[0]['published_at'] == '2026-01-03T01:00:00Z'


def test_event_watermark_rolls_back_and_damage_is_preserved(tmp_path, monkeypatch):
    from data.event_store import EventStore
    path = tmp_path / 'events.json'
    store = EventStore(str(path))
    store.append([], watermark='20260101000000')
    def fail():
        raise OSError('simulated')
    monkeypatch.setattr(store, '_save_locked', fail)
    with pytest.raises(OSError):
        store.append([{'event_id': '1', 'date': '20260101'}], watermark='20260101001500')
    assert store.checkpoint() == '20260101000000' and store.stats()['total'] == 0
    path.write_text('{broken', encoding='utf-8')
    damaged = EventStore(str(path))
    with pytest.raises(RuntimeError):
        damaged.append([], watermark='20260101003000')
    assert path.read_text(encoding='utf-8') == '{broken'


@pytest.mark.parametrize('expired_at', ['before', 'commit', None])
def test_task_write_fence_checks_before_write_and_before_commit(expired_at):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from threading import Event
    from storage.repository import PostgresRepository, task_scope, current_task, get_repository
    from storage.jobs import LeaseLost
    repo = PostgresRepository.__new__(PostgresRepository)
    state = {'committed': False, 'rolled_back': False, 'written': False, 'checks': 0}
    @contextmanager
    def begin():
        try:
            yield SimpleNamespace(engine=repo.engine)
            state['committed'] = True
        except Exception:
            state['rolled_back'] = True
            raise
    repo.engine = SimpleNamespace(begin=begin)
    def guard(conn, job, lost):
        state['checks'] += 1
        if expired_at == 'before' or (expired_at == 'commit' and state['checks'] == 2):
            raise LeaseLost('模拟失租约')
    queue = SimpleNamespace(repo=repo, assert_owned=guard)
    def write():
        with task_scope(queue, {'id': 'fixture'}, Event()):
            assert get_repository() is repo
            with repo.write_transaction():
                state['written'] = True
    if expired_at:
        with pytest.raises(LeaseLost):
            write()
        assert state['rolled_back'] and not state['committed']
    else:
        write()
        assert state['committed'] and state['checks'] == 2
    assert state['written'] == (expired_at != 'before')
    assert current_task() is None


@pytest.mark.parametrize('method,args', [
    ('save_articles', ([{'content': 'fixture'}],)),
    ('save_analysis', ({'run_kind': 'manual'},)),
    ('save_risk', ({'date': '2026-01-01', 'risk_score': 0, 'details': {}},)),
    ('save_events', ([{'date': '2026-01-01'}],)),
    ('save_source_run', ('fixture', {'ok': True, 'count': 0})),
    ('save_observations', ([],)),
])
def test_all_task_business_writes_enter_fence(method, args):
    from storage.repository import PostgresRepository
    from storage.jobs import LeaseLost
    repo = PostgresRepository.__new__(PostgresRepository)
    def refused():
        raise LeaseLost('模拟拒绝')
    repo.write_transaction = refused
    with pytest.raises(LeaseLost):
        getattr(repo, method)(*args)


def test_worker_lost_lease_never_acknowledges_and_restores_context(monkeypatch):
    import worker
    from types import SimpleNamespace
    from storage.repository import current_task
    calls = []
    queue = SimpleNamespace(repo=object(), lease_seconds=120,
        claim=lambda: {'id': 'fixture'}, finish=lambda *a, **kw: calls.append(kw))
    def execute(job):
        assert current_task()[0] is queue
        current_task()[2].set()
        return {'success': True}
    monkeypatch.setattr(worker, 'execute_job', execute)
    assert worker.run_one(queue)
    assert not calls and current_task() is None


@pytest.mark.parametrize('module,name', [('data.crawler', 'NewsCrawler'),
    ('data.myanmar_now_crawler', 'EnglishNewsCrawler'), ('data.rss_crawler', 'RSSNewsCrawler'),
    ('data.gdelt_crawler', 'GDELTCrawler')])
def test_postgres_collectors_do_not_advance_file_dedup_or_fallback(module, name, monkeypatch, tmp_path):
    import importlib
    from types import SimpleNamespace
    import storage.repository as storage
    calls = []
    def save(rows):
        calls.append(rows)
        raise RuntimeError('模拟数据库失败')
    monkeypatch.setattr(storage, 'get_repository', lambda: SimpleNamespace(save_collected_articles=save))
    cls = getattr(importlib.import_module(module), name)
    crawler = cls.__new__(cls)
    crawler._urls_seen_file = str(tmp_path / 'urls.txt')
    crawler._urls_seen = {'https://example.invalid/report'}
    assert crawler._load_urls_seen() == set()
    assert crawler._is_new_url('https://example.invalid/report')
    crawler._save_urls_seen()
    with pytest.raises(RuntimeError):
        crawler.save_news([{'content': 'fixture'}])
    assert calls and not list(tmp_path.iterdir())


def test_postgres_source_health_is_shared_and_database_failure_is_visible(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import storage.repository as storage
    from data.source_health import SourceHealthTracker
    records = {}
    def save(source, entry):
        records.setdefault(source, []).append(entry)
    repo = SimpleNamespace(save_source_run=save, load_source_runs=lambda limit: records.copy())
    monkeypatch.setattr(storage, 'get_repository', lambda: repo)
    first = SourceHealthTracker(str(tmp_path / 'unused.json'))
    second = SourceHealthTracker(str(tmp_path / 'unused.json'))
    first.record('fixture', True, 0)
    assert second.get_health()['fixture']['status'] == 'healthy'
    def fail(*args):
        raise RuntimeError('数据库离线')
    repo.save_source_run = fail
    with pytest.raises(RuntimeError):
        first.record('fixture', False)
    assert not (tmp_path / 'unused.json').exists()


def test_task_proxy_cache_never_overwrites_shared_files(tmp_path):
    from storage.repository import task_scope
    from data.economic_crawler import EconomicCrawler
    from data.nightlight_crawler import NightlightCrawler
    from types import SimpleNamespace
    from threading import Event
    for cls in (EconomicCrawler, NightlightCrawler):
        crawler = cls.__new__(cls)
        path = tmp_path / cls.__name__
        path.write_text('preserve', encoding='utf-8')
        crawler._cache_file = str(path)
        with task_scope(SimpleNamespace(repo=object()), {'id': 'fixture'}, Event()):
            crawler._save_cache({'value': 99})
        assert path.read_text(encoding='utf-8') == 'preserve'


@pytest.fixture
def ops_fixture(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import scripts.database_ops as ops
    from sqlalchemy.engine import make_url
    summary = {'counts': {'articles': 0}, 'date_ranges': {}, 'invalid_geometry': {}, 'alembic_revision': 'fixture'}
    conn = SimpleNamespace(execute=lambda query: SimpleNamespace(scalar_one=lambda: 'snapshot-fixture'))
    monkeypatch.setattr(ops, 'read_snapshot', lambda repo: nullcontext(conn))
    monkeypatch.setattr(ops, 'database_summary', lambda repo, connection: dict(summary))
    monkeypatch.setattr(ops, 'pg_binary', lambda *a: 'mock-pg')
    return ops, conn, summary, make_url('postgresql+psycopg://tester:fixture-secret@localhost/ops_test')


@pytest.mark.parametrize('action', ['verify', 'export', 'backup', 'restore'])
def test_database_tools_default_dry_run_never_connects_or_creates(action, tmp_path, monkeypatch, capsys):
    import scripts.database_ops as ops
    import storage.repository as storage
    if action == 'restore':
        (tmp_path / 'target').mkdir()
        monkeypatch.setattr(ops, 'backup_manifest', lambda path: {})
    def forbidden(*args):
        pytest.fail('预演不能创建数据库连接')
    monkeypatch.setattr(storage, 'PostgresRepository', forbidden)
    before = set(tmp_path.iterdir())
    assert ops.main([action, '--root', str(tmp_path), '--name', 'target']) == 0
    assert set(tmp_path.iterdir()) == before
    assert json.loads(capsys.readouterr().out)['writes'] == 0


@pytest.mark.parametrize('name', ['../escape', 'a/b', 'a\\b', 'C:other', '.', '..', ''])
def test_database_tool_paths_reject_escape(tmp_path, name):
    from scripts.database_ops import target_directory
    with pytest.raises(ValueError):
        target_directory(tmp_path, name)


def test_database_tool_refuses_existing_or_link_target(tmp_path, monkeypatch):
    import scripts.database_ops as ops
    existing = tmp_path / 'existing'
    existing.mkdir()
    with pytest.raises(ValueError):
        ops.target_directory(tmp_path, 'existing')
    monkeypatch.setattr(ops.os.path, 'isjunction', lambda path: True)
    with pytest.raises(ValueError):
        ops.target_directory(tmp_path, 'existing', exists=True)


@pytest.mark.parametrize('url', ['sqlite:///ops_test', 'postgresql://user@/ops_test',
    'postgresql://localhost/ops_test', 'postgresql://user@localhost/host=other',
    'postgresql://user@host1,host2/ops_test', 'postgresql://user@localhost/ops_test?options=-csearch_path=other',
    'postgresql://user@localhost/ops_test?sslmode=invalid',
    'postgresql://user@localhost/ops_test?sslmode=require&sslmode=disable'])
def test_database_tool_url_cannot_override_confirmed_target(url):
    from sqlalchemy.engine import make_url
    from scripts.database_ops import pg_environment
    with pytest.raises(ValueError):
        pg_environment(make_url(url))


def test_database_tool_environment_and_errors_do_not_expose_credentials(ops_fixture, monkeypatch):
    ops, _, _, url = ops_fixture
    monkeypatch.setenv('PGHOSTADDR', '192.0.2.1')
    monkeypatch.setenv('PGSERVICE', 'wrong-service')
    monkeypatch.setenv('PGOPTIONS', '-csearch_path=wrong')
    env = ops.pg_environment(url)
    assert env['PGDATABASE'] == 'ops_test' and env['PGPASSWORD'] == url.password
    assert not {'PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS'} & env.keys()
    def fail(argv, **kwargs):
        assert url.password not in ' '.join(argv)
        assert kwargs['stdin'] == ops.subprocess.DEVNULL and kwargs['check']
        raise ops.subprocess.CalledProcessError(1, argv, stderr=url.password)
    monkeypatch.setattr(ops.subprocess, 'run', fail)
    with pytest.raises(RuntimeError) as error:
        ops.run_pg('mock', ['--dbname=ops_test'], env)
    assert url.password not in str(error.value)


def test_database_tool_sql_error_is_sanitized_and_engine_disposed(tmp_path, monkeypatch, capsys):
    import scripts.database_ops as ops
    import storage.repository as storage
    from types import SimpleNamespace
    from sqlalchemy.exc import OperationalError
    disposed = []
    monkeypatch.setenv('DATABASE_TOOL_URL', 'postgresql://tester:fixture-secret@localhost/ops_test')
    monkeypatch.setattr(storage, 'PostgresRepository', lambda url: SimpleNamespace(
        engine=SimpleNamespace(dispose=lambda: disposed.append(True))))
    def fail(*args):
        raise OperationalError('SELECT private', {}, Exception('fixture-secret'))
    monkeypatch.setattr(ops, 'read_snapshot', fail)
    assert ops.main(['verify', '--root', str(tmp_path), '--apply', '--confirm-database', 'ops_test']) == 2
    assert disposed and not list(tmp_path.iterdir())
    assert 'fixture-secret' not in capsys.readouterr().out


@pytest.mark.parametrize('dangerous', ['PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS'])
def test_database_tool_main_rejects_inherited_pg_env_before_connecting(tmp_path, monkeypatch, capsys, dangerous):
    import scripts.database_ops as ops
    import storage.repository as storage
    monkeypatch.setenv('DATABASE_TOOL_URL', 'postgresql://tester:secret@localhost:6000/ops_test')
    monkeypatch.setenv(dangerous, 'would-divert-target')
    monkeypatch.delenv('PGPORT', raising=False)
    def forbidden(*args):
        pytest.fail('主连接环境不安全时不得创建仓储')
    monkeypatch.setattr(storage, 'PostgresRepository', forbidden)
    assert ops.main(['verify', '--root', str(tmp_path), '--apply', '--confirm-database', 'ops_test']) == 2
    output = capsys.readouterr().out
    assert 'fixture' not in output and 'secret' not in output and dangerous not in output


def test_database_tool_main_pins_default_port_for_repository(tmp_path, monkeypatch):
    import scripts.database_ops as ops
    import storage.repository as storage
    seen = []
    from types import SimpleNamespace
    monkeypatch.setenv('DATABASE_TOOL_URL', 'postgresql://tester:secret@localhost/ops_test')
    for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS', 'PGPORT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ops, 'read_snapshot', lambda repo: __import__('contextlib').nullcontext(
        SimpleNamespace(execute=lambda q: SimpleNamespace(scalar_one=lambda: 'fixture'))))
    monkeypatch.setattr(ops, 'database_summary', lambda *a: {'counts': {}, 'date_ranges': {},
        'invalid_geometry': {}, 'alembic_revision': 'fixture', 'postgis_version': 'fixture'})
    def capture(url):
        seen.append(url)
        return SimpleNamespace(engine=SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(storage, 'PostgresRepository', capture)
    assert ops.main(['verify', '--root', str(tmp_path), '--apply', '--confirm-database', 'ops_test']) == 0
    from sqlalchemy.engine import make_url
    assert make_url(seen[0]).port == 5432


def test_verify_directory_flags_missing_batch_and_stale_stored_value(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from contextlib import contextmanager
    from scripts.import_data import verify_directory
    from storage import schema
    source = tmp_path / 'risk_scores.jsonl'
    source.write_text(json.dumps({'date': '2026-01-01', 'risk_score': 0, 'run_kind': 'legacy',
                                  'algorithm_version': 'legacy', 'run_id': 'original'}) + '\n', encoding='utf-8')
    normalized = {'date': '2026-01-01', 'risk_score': 0, 'run_kind': 'legacy',
                  'algorithm_version': 'legacy', 'original_run_id': 'original',
                  'recorded_at': '1970-01-01T00:00:00+00:00', 'timestamp_quality': 'legacy_file_time'}
    cases = {
        'batch_missing': (None, None, None, 'missing'),
        'item_missing': ({'file_mtime': 0}, None, None, 'missing'),
        'stored_missing': ({'file_mtime': 0}, 'ok', None, 'mismatched'),
        'stored_mismatch': ({'file_mtime': 0}, 'ok', {**normalized, 'risk_score': 50}, 'mismatched'),
        'verified': ({'file_mtime': 0}, 'ok', normalized, 'verified'),
    }
    for case, (batch, item_status, stored, expected) in cases.items():
        item_row = SimpleNamespace(status=item_status, record_id='x') if item_status else None
        calls = []
        def execute(query, *a, **kw):
            text = str(query)
            calls.append(text)
            if 'SET TRANSACTION' in text:
                return None
            if 'import_batches' in text:
                return SimpleNamespace(scalar_one_or_none=lambda: batch)
            if 'import_items' in text:
                return SimpleNamespace(first=lambda: item_row)
            return SimpleNamespace(scalar_one_or_none=lambda: stored)
        conn = SimpleNamespace(execute=execute,
            begin=lambda: __import__('contextlib').nullcontext(None))
        engine = SimpleNamespace(connect=lambda: SimpleNamespace(
            execution_options=lambda **k: _Ctx(conn)))

        repo = SimpleNamespace(schema=schema, engine=engine)
        result = verify_directory(tmp_path, repo)
        assert result[expected] == 1, (case, result)
        assert result['missing'] + result['mismatched'] + result['verified'] == 1


class _Ctx:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self.value
    def __exit__(self, *exc):
        return False


def test_worker_reuses_committed_snapshot_for_retry(monkeypatch):
    from types import SimpleNamespace
    import worker
    from storage import schema
    conn = SimpleNamespace(execute=lambda q: SimpleNamespace(
        scalar_one_or_none=lambda: stored))
    repo = SimpleNamespace(engine=SimpleNamespace(connect=lambda: _Ctx(conn)), schema=schema)
    monkeypatch.setattr(worker, 'get_repository', lambda: repo)
    stored = {'entities': {'locations': ['Myanmar']}, 'risk_score': {'score': 1},
              'sentiment': {'risk_level': 'high'}, 'llm_analysis': {'summary': 'first'}}
    job = {'id': 'J1', 'owner_id': 'u1', 'kind': 'manual', 'payload': {'text': 'x'},
           'lease_token': 'T', 'attempts': 2, 'max_attempts': 3}
    reused = worker._reuse_committed_snapshot(job)
    assert reused['success'] and reused['data']['analysis_id'] == 'J1'
    assert reused['data']['reused_snapshot'] and reused['data']['entities'] == stored['entities']
    assert reused['data']['risk_score'] == stored['risk_score']


def test_worker_first_attempt_falls_through_when_no_snapshot(monkeypatch):
    from types import SimpleNamespace
    import worker
    from storage import schema
    conn = SimpleNamespace(execute=lambda q: SimpleNamespace(scalar_one_or_none=lambda: None))
    repo = SimpleNamespace(engine=SimpleNamespace(connect=lambda: _Ctx(conn)), schema=schema)
    monkeypatch.setattr(worker, 'get_repository', lambda: repo)
    assert worker._reuse_committed_snapshot({'id': 'J1', 'owner_id': 'u1', 'kind': 'manual'}) is None
    assert worker._reuse_committed_snapshot({'id': 'J1', 'owner_id': 'u1', 'kind': 'pipeline'}) is None



@pytest.mark.parametrize('manifest', [[], None, {}, {'status': 'complete', 'kind': 'business_export'}])
def test_database_backup_invalid_manifest_is_rejected(tmp_path, manifest):
    from scripts.database_ops import backup_manifest
    (tmp_path / 'database.dump').write_bytes(b'fixture')
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        backup_manifest(tmp_path)


def test_database_backup_snapshot_hash_and_partial_failure(ops_fixture, tmp_path, monkeypatch):
    ops, _, summary, url = ops_fixture
    commands = []
    def run(binary, args, env, stdout):
        commands.append(args)
        stdout.write(b'fixture-dump')
    monkeypatch.setattr(ops, 'run_pg', run)
    target = tmp_path / 'backup'
    manifest = ops.backup_database(None, target, url)
    assert manifest['database'] == summary
    assert ops.backup_manifest(target) == manifest
    assert '--snapshot=snapshot-fixture' in commands[0] and '--schema=public' in commands[0]
    (target / 'database.dump').write_bytes(b'tampered')
    with pytest.raises(ValueError):
        ops.backup_manifest(target)
    def fail(binary, args, env, stdout):
        stdout.write(b'partial')
        raise RuntimeError('模拟失败')
    monkeypatch.setattr(ops, 'run_pg', fail)
    with pytest.raises(RuntimeError):
        ops.backup_database(None, tmp_path / 'partial', url)
    assert (tmp_path / 'partial' / 'database.dump').read_bytes() == b'partial'
    assert not (tmp_path / 'partial' / 'manifest.json').exists()


@pytest.mark.parametrize('case', ['non_test', 'nonempty', 'mismatch', 'match'])
def test_database_restore_boundary_and_verification(ops_fixture, tmp_path, monkeypatch, case):
    from types import SimpleNamespace
    ops, conn, summary, url = ops_fixture
    monkeypatch.setattr(ops, 'backup_manifest', lambda target: {'database': summary})
    conn.execute = lambda query: SimpleNamespace(scalar_one=lambda: 1 if case == 'nonempty' else 0)
    calls = []
    monkeypatch.setattr(ops, 'run_pg', lambda binary, args, env: calls.append(args))
    if case == 'non_test':
        url = url.set(database='production')
    if case == 'mismatch':
        monkeypatch.setattr(ops, 'database_summary', lambda *a: {**summary, 'counts': {'articles': 99}})
    if case in {'non_test', 'nonempty'}:
        with pytest.raises(ValueError):
            ops.restore_database(None, tmp_path, url)
        assert not calls and not list(tmp_path.iterdir())
    elif case == 'mismatch':
        with pytest.raises(RuntimeError):
            ops.restore_database(None, tmp_path, url)
        assert calls and list(tmp_path.glob('restore-check-*.json'))
    else:
        assert ops.restore_database(None, tmp_path, url)['status'] == 'verified'
        assert '--single-transaction' in calls[0] and '--exit-on-error' in calls[0]


def test_database_export_matches_event_file_and_observation_contract(ops_fixture, tmp_path):
    from types import SimpleNamespace
    from storage import schema
    from data.event_store import EventStore
    from utils.data_contract import normalize_observation
    from scripts.import_data import import_directory
    ops, conn, _, _ = ops_fixture
    event = {'event_id': 'fixture', 'date': '2026-01-01', 'run_kind': 'existing'}
    obs = normalize_observation(observation())
    results = iter([[{'content': 'visible', 'pub_time': '2026-01-01'}, {'content': 'private', 'run_kind': 'manual'}],
                    [], [], [], [event], [('gdelt', '20260101000000')],
                    [{**obs, 'source_id': 'internal-id', 'payload': obs}]])
    queries = []
    def execute(query):
        queries.append(str(query))
        result = next(results)
        return SimpleNamespace(scalars=lambda: result, all=lambda: result, mappings=lambda: result)
    conn.execute = execute
    target = tmp_path / 'export'
    manifest = ops.export_business(SimpleNamespace(schema=schema), target)
    store = EventStore(str(target / 'processed' / 'gdelt_event_store.json'))
    assert store.load() == [event] and store.checkpoint() == '20260101000000'
    assert next(f for f in manifest['files'] if 'event_store' in f['file'])['records'] == 1
    exported = json.loads((target / 'external' / 'indicator_observations.jsonl').read_text(encoding='utf-8'))
    assert normalize_observation(exported) == obs and 'source_id' not in exported
    articles = json.loads((target / 'raw' / 'myanmar_news_export.json').read_text(encoding='utf-8'))
    assert len(articles) == 1 and articles[0]['content'] == 'visible'
    assert 'owner_id IS NULL' in queries[2] and 'owner_id IS NULL' in queries[3]
    assert import_directory(target)['invalid'] == 0


def test_import_freezes_file_mtime_and_detects_changes(tmp_path, monkeypatch):
    from scripts.import_data import normalize, read_frozen
    from types import SimpleNamespace
    path = tmp_path / 'risk_scores.jsonl'
    path.write_text('{}', encoding='utf-8')
    raw = {'date': '2026-01-01', 'risk_score': 0, 'run_id': 'original'}
    row, identity, _ = normalize(raw, 'risk', path, file_mtime=0)
    assert row['original_run_id'] == 'original' and 'run_id' not in row
    assert row['recorded_at'] == '1970-01-01T00:00:00+00:00'
    assert normalize(raw, 'risk', path, file_mtime=0)[1] == identity
    stats = iter([SimpleNamespace(st_mtime_ns=1, st_ctime_ns=1, st_size=2, st_ino=1),
                  SimpleNamespace(st_mtime_ns=2, st_ctime_ns=1, st_size=2, st_ino=1)])
    monkeypatch.setattr(Path, 'stat', lambda self, **kw: next(stats))
    with pytest.raises(RuntimeError):
        read_frozen(path)


def test_gdelt_checkpoint_stops_at_first_missing_batch(monkeypatch):
    import data.gdelt_files as files
    import zipfile
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.writestr('events.csv', '')
    monkeypatch.setattr(files, '_get_latest_batch_ts', lambda *a: datetime(2026, 1, 1, 1, 0))
    called = []
    def get(url, **kwargs):
        called.append(url)
        return type('Response', (), {'status_code': 404 if '003000' in url else 200,
                     'content': archive.getvalue(), 'raise_for_status': lambda self: None})()
    monkeypatch.setattr(files.requests, 'get', get)
    rows, watermark = files.fetch_myanmar_events(max_files=4, since_ts=datetime(2026, 1, 1))
    assert rows == [] and watermark == datetime(2026, 1, 1, 0, 15)
    assert len(called) == 2 and '001500' in called[0]
