"""仅使用显式确认的*_test数据库；每次独立schema保留以便失败诊断。"""
import os
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

pytestmark = pytest.mark.postgis


@pytest.fixture
def repo(monkeypatch):
    url = os.environ.get('POSTGIS_TEST_URL')
    if not url:
        pytest.skip('未提供PostGIS测试实例；跳过不是集成通过')
    from sqlalchemy.engine import make_url
    from sqlalchemy import text
    from storage.repository import PostgresRepository
    parsed = make_url(url)
    if not parsed.database or not parsed.database.endswith('_test') or os.environ.get('POSTGIS_TEST_CONFIRM') != parsed.database:
        pytest.fail('必须确认独立的*_test数据库名称')
    admin = PostgresRepository(url)
    schema = 'test_' + uuid4().hex
    try:
        with admin.engine.begin() as conn:
            assert conn.execute(text('SELECT postgis_version()')).scalar_one()
            conn.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    finally:
        admin.engine.dispose()
    scoped_url = parsed.update_query_dict({'options': '-csearch_path=' + schema + ',public'}).render_as_string(hide_password=False)
    monkeypatch.setenv('MIGRATION_DATABASE_URL', scoped_url)
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini')), 'head')
    instance = PostgresRepository(scoped_url)
    instance.test_schema = schema
    try:
        yield instance
    finally:
        instance.engine.dispose()


def test_postgis_geometry_transactions_and_article_provenance(repo):
    from sqlalchemy import text, select, func
    from analyzer.data_loader import DataLoader
    rows = DataLoader().preprocess([
        {'content': 'fixture', 'source': 'A', 'url': 'https://a.test/report', 'pub_time': '2026-01-02'},
        {'content': 'fixture', 'source': 'B', 'url': 'https://b.test/report', 'pub_time': '2026-01-01'}])
    repo.save_articles(rows)
    repo.save_articles(rows)
    assert len(repo.load_articles()) == 1
    assert repo.load_articles()[0]['pub_time'] == '2026-01-01'
    with repo.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(repo.schema.article_sources)).scalar_one() == 2
        indexes = conn.execute(text('SELECT indexdef FROM pg_indexes WHERE schemaname=:schema'), {'schema': repo.test_schema}).scalars().all()
        assert sum('USING gist' in index for index in indexes) == 2
    event = {'event_id': '1', 'date': '20260101', 'lat': 16.85, 'lon': 96.2}
    repo.save_events([event], watermark='20260101000000')
    with pytest.raises(ValueError):
        repo.save_events([{**event, 'event_id': '2'}, {'date': 'invalid'}], watermark='20260101001500')
    assert repo.checkpoint() == '20260101000000'
    assert len(repo.load_events(days=None)) == 1
    with repo.engine.connect() as conn:
        assert conn.execute(text('SELECT ST_SRID(geom) FROM events')).scalar_one() == 4326
        assert conn.execute(text('SELECT ST_IsValid(geom) FROM events')).scalar_one()


def test_postgis_daily_risk_retry_and_import_resume(repo, tmp_path):
    from sqlalchemy import select, func
    from scripts.import_data import import_directory
    import json
    row = {'date': '2026-01-01', 'risk_score': 0, 'details': {}, 'run_kind': 'existing',
           'algorithm_version': 'risk-v2', 'input_hash': 'same-fixture', 'sources': ['test'], 'sample_count': 1}
    repo.save_risk(row)
    repo.save_risk(row)
    with repo.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(repo.schema.daily_risk)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(repo.schema.analysis_runs)).scalar_one() == 1
    source = tmp_path / 'risk_scores.jsonl'
    source.write_text(json.dumps({'date': '2025-01-01', 'risk_score': 40}) + '\n{bad', encoding='utf-8')
    first = import_directory(tmp_path, repo)
    again = import_directory(tmp_path, repo)
    assert first['written'] == 1 and again['written'] == 0 and again['invalid'] == 1
    with repo.engine.connect() as conn:
        assert set(conn.execute(select(repo.schema.import_items.c.status)).scalars()) == {'ok', 'invalid'}


def test_postgis_concurrent_claim_lease_recovery_and_private_jobs(repo):
    from sqlalchemy import update
    from storage.jobs import JobQueue
    from utils.data_contract import utc_now
    with repo.engine.begin() as conn:
        for uid in ('owner', 'other'):
            repo._insert(conn, repo.schema.users, {'id': uid, 'username': uid, 'password_hash': 'disabled-fixture', 'role': 'analyst'})
    queue = JobQueue(repo)
    ids = [queue.enqueue('manual', {'text': 'desensitized-' + str(i)}, owner_id='owner', idempotency_key=str(i)) for i in range(8)]
    assert queue.enqueue('manual', {'text': 'desensitized-0'}, owner_id='owner', idempotency_key='0') == ids[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: JobQueue(repo).claim(), range(8)))
    assert len({job['id'] for job in claims}) == 8
    assert queue.get(ids[0], 'other', True) is None
    current = claims[0]
    with repo.engine.begin() as conn:
        conn.execute(update(repo.schema.jobs).where(repo.schema.jobs.c.id == current['id']).values(lease_until=utc_now() - timedelta(seconds=1)))
    recovered = queue.claim()
    assert recovered['id'] == current['id'] and recovered['lease_token'] != current['lease_token']
    assert not queue.heartbeat(current)
    assert not queue.finish(current, result={'stale': True})
    from storage.repository import task_scope
    from storage.jobs import LeaseLost
    from threading import Event
    with task_scope(queue, current, Event()):
        with pytest.raises(LeaseLost):
            repo.save_articles([{'content': 'stale must not persist'}])
        with pytest.raises(LeaseLost):
            repo.save_events([{'event_id': 'stale', 'date': '20260101'}], watermark='20260101000000')
    assert not repo.load_articles() and repo.checkpoint() is None
    with task_scope(queue, recovered, Event()):
        repo.save_articles([{'content': 'recovered persisted'}])
    assert len(repo.load_articles()) == 1
    assert queue.finish(recovered, result={'success': True})


def test_postgis_lease_expiring_inside_write_rolls_back(repo):
    from storage.jobs import JobQueue, LeaseLost
    from storage.repository import task_scope
    from threading import Event
    from sqlalchemy import update, func
    queue = JobQueue(repo)
    queue.enqueue('pipeline', {})
    job = queue.claim()
    revision = repo.revision()
    with pytest.raises(LeaseLost):
        with task_scope(queue, job, Event()):
            with repo.write_transaction() as conn:
                repo.save_articles([{'content': 'must roll back on expiry'}], conn)
                conn.execute(update(repo.schema.jobs).where(repo.schema.jobs.c.id == job['id']).values(
                    lease_until=func.clock_timestamp() - timedelta(seconds=1)))
    assert not repo.load_articles() and repo.revision() == revision
    assert queue.finish(job, result={'success': True})


def test_postgis_source_health_retains_history_and_shares_latest(repo, monkeypatch):
    from data.source_health import SourceHealthTracker
    import storage.repository as storage
    from sqlalchemy import select, func
    monkeypatch.setattr(storage, 'get_repository', lambda: repo)
    first, second = SourceHealthTracker(), SourceHealthTracker()
    for _ in range(25):
        first.record('fixture', True, 0)
    health = second.get_health()['fixture']
    assert health['status'] == 'healthy' and health['recent_attempts'] == 20
    with repo.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(repo.schema.source_runs)).scalar_one() == 25


def test_postgis_observation_import_versions_are_immutable(repo, tmp_path, monkeypatch):
    from scripts.import_data import import_directory
    from analyzer.multimodal_aligner import MultimodalAligner
    import storage.repository as storage
    import json
    from sqlalchemy import select, func
    row = {'source': 'fixture', 'indicator': 'nightlight', 'region': 'MMR', 'frequency': 'monthly',
           'period_start': '2026-01-01', 'period_end': '2026-02-01', 'value': 0, 'unit': 'nW/cm²/sr',
           'quality': 'observed', 'run_kind': 'existing', 'dataset_version': 'v1'}
    path = tmp_path / 'indicator_observations.jsonl'
    path.write_text(json.dumps(row) + '\n' + json.dumps({**row, 'dataset_version': 'v2', 'value': 2}), encoding='utf-8')
    assert import_directory(tmp_path, repo)['written'] == 2
    assert import_directory(tmp_path, repo)['written'] == 0
    with pytest.raises(ValueError):
        repo.save_observations([{**row, 'value': 99}])
    with repo.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(repo.schema.indicator_observations)).scalar_one() == 2
    monkeypatch.setattr(storage, 'get_repository', lambda: repo)
    rows = MultimodalAligner().align_monthly(1, '2026-01-31', news=[], events=[])
    assert rows[0]['nightlight'] is None and rows[0]['quality']['nightlight'] == 'ambiguous'


def test_postgis_import_verify_original_runs_mtime_and_tampering(repo, tmp_path):
    import json
    from sqlalchemy import select, update, func
    from scripts.import_data import import_directory, verify_directory
    rows = [{'run_id': 'original-' + str(i), 'date': '2026-01-01', 'risk_score': 0,
             'run_kind': 'existing', 'input_hash': 'same', 'algorithm_version': 'risk-v2'} for i in range(2)]
    path = tmp_path / 'risk_scores.jsonl'
    path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    assert import_directory(tmp_path, repo)['written'] == 2
    os.utime(path, (10, 10))
    assert import_directory(tmp_path, repo)['written'] == 0
    assert verify_directory(tmp_path, repo)['verified'] == 2
    with repo.engine.begin() as conn:
        table = repo.schema.analysis_runs
        saved = list(conn.execute(select(table.c.payload)).scalars())
        assert {r['original_run_id'] for r in saved} == {'original-0', 'original-1'}
        assert conn.execute(select(func.count()).select_from(repo.schema.daily_risk)).scalar_one() == 1
        row = saved[0]
        conn.execute(update(table).where(table.c.id == row['run_id']).values(payload={**row, 'risk_score': 99}))
    check = verify_directory(tmp_path, repo)
    assert check['mismatched'] == 1 and check['verified'] == 1 and check['written'] == 0
    path.write_text(json.dumps({**rows[0], 'risk_score': 2}), encoding='utf-8')
    assert verify_directory(tmp_path, repo)['missing'] == 1


def test_postgis_legacy_observation_reimport_cannot_claim_typed_upgrade(repo, tmp_path):
    from utils.data_contract import normalize_observation
    from scripts.import_data import import_directory, verify_directory
    from sqlalchemy import update
    import json
    row = normalize_observation({'source': 'fixture', 'indicator': 'gdp_growth', 'region': 'MMR',
        'frequency': 'annual', 'period_start': '2025-01-01', 'period_end': '2026-01-01',
        'value': 0, 'unit': '%', 'quality': 'observed', 'run_kind': 'existing', 'dataset_version': 'v1'})
    path = tmp_path / 'indicator_observations.jsonl'
    path.write_text(json.dumps(row), encoding='utf-8')
    assert import_directory(tmp_path, repo)['written'] == 1
    with repo.engine.begin() as conn:
        table = repo.schema.indicator_observations
        conn.execute(update(table).where(table.c.id == row['id']).values(
            run_kind='legacy', dataset_version='legacy', product='', period_end=None))
    with pytest.raises(ValueError):
        repo.save_observations([row])
    assert verify_directory(tmp_path, repo)['mismatched'] == 1
    reviewed = {**row, 'id': 'reviewed-fixture', 'dataset_version': 'v1-reviewed'}
    assert repo.save_observations([reviewed]) == ['reviewed-fixture']


def test_postgis_evidence_revision_commit_and_rollback(repo):
    from sqlalchemy import select, func
    row = {'content': 'fixture evidence', 'pub_time': '2026-01-01', 'source': 'fixture',
           'run_kind': 'existing', 'analyzed_at': '2026-01-02T00:00:00Z', 'ner_version': 'fixture-v1',
           'entities': {'organizations': ['A', 'B']}}
    before = repo.revision()
    repo.save_articles([row])
    assert repo.revision() != before
    updated = repo.revision()
    with pytest.raises(RuntimeError):
        with repo.engine.begin() as conn:
            repo.save_articles([{**row, 'content': 'rolled back'}], conn)
            raise RuntimeError('模拟回滚')
    assert repo.revision() == updated
    with repo.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(repo.schema.entity_mentions)).scalar_one() == 2
        assert conn.execute(select(func.count()).select_from(repo.schema.relation_evidence)).scalar_one() == 1
    repo.save_articles([{**row, 'analyzed_at': '2026-01-03T00:00:00Z', 'entities': {'organizations': ['A', 'C']}}])
    assert repo.revision() != updated
    assert repo.load_articles()[0]['entities']['organizations'] == ['A', 'C']
