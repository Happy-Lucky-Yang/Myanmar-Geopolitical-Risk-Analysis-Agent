"""独立 worker；只在显式运行时领取任务或启用调度，不在 Web 进程启动。"""
import argparse
import logging
import threading
import time
from datetime import datetime, timezone

from storage.repository import get_repository, task_scope
from storage.jobs import JobQueue

logger = logging.getLogger(__name__)


def _reuse_committed_snapshot(job):
    """手工与链式任务重试命中同一 job_id 时，返回首次已提交的 analysis_runs 快照。

    LLM 或下游服务在重试中可能给出不同输出；_insert 已保证 analysis_runs 幂等保留首份，
    但 jobs.result 会因重新执行而漂移。提前返回快照以保持两边一致，
    不重新调用外部服务，不写第二次。"""
    if job['kind'] not in {'manual', 'chain'}:
        return None
    repo = get_repository()
    if repo is None:
        return None
    from sqlalchemy import select
    t = repo.schema.analysis_runs
    with repo.engine.connect() as conn:
        row = conn.execute(select(t.c.payload).where(
            t.c.id == job['id'], t.c.owner_id == job['owner_id'])).scalar_one_or_none()
    if row is None:
        return None
    return {'success': True, 'data': {
        'analysis_id': job['id'], 'reused_snapshot': True,
        'entities': row.get('entities'), 'sentiment': row.get('sentiment'),
        'llm_analysis': row.get('llm_analysis'), 'risk_score': row.get('risk_score'),
        'chain_steps': row.get('chain_steps'), 'final_answer': row.get('final_answer'),
        'warnings': ['重试命中首次分析快照，未重新调用外部服务']}}


def execute_job(job):
    kind, payload = job['kind'], job['payload']
    reused = _reuse_committed_snapshot(job)
    if reused is not None:
        return reused
    if kind in {'manual', 'chain'}:
        from app import app, analyze, chain_analysis
        from flask import g
        from utils.security import user_by_id
        user = user_by_id(job['owner_id'])
        if not user or user['role'] not in {'analyst', 'admin'}:
            raise PermissionError('任务发起人已无分析权限')
        endpoint = '/api/analyze' if kind == 'manual' else '/api/chain'
        with app.test_request_context(endpoint, method='POST', json=payload):
            g.user = user
            g.task_execution = True
            g.job_id = job['id']
            response = app.make_response(analyze() if kind == 'manual' else chain_analysis())
            data = response.get_json()
            if response.status_code >= 400 or not data.get('success'):
                raise RuntimeError('分析任务失败，未确认完成')
            return data
    if kind == 'pipeline':
        from pipeline import run_pipeline
        mode = payload.get('source_mode', 'existing')
        result = run_pipeline(source_mode=mode, persist=mode != 'demo',
                              include_llm=bool(payload.get('include_llm', False)))
        if not result['success']:
            raise RuntimeError('流水线阶段失败')
        return result
    if kind == 'gdelt':
        from data.gdelt_crawler import get_gdelt_crawler
        return get_gdelt_crawler().get_risk_metrics(timespan_days=int(payload.get('days', 7)))
    if kind == 'nightlight':
        from data.nightlight_crawler import get_nightlight_crawler
        return {'value': get_nightlight_crawler().get_nightlight_change(), 'quality': 'proxy'}
    if kind == 'economic':
        from data.economic_crawler import get_economic_crawler
        return {'value': get_economic_crawler().get_refugee_change(), 'quality': 'proxy'}
    raise ValueError('未知任务')


def run_one(queue):
    job = queue.claim()
    if not job:
        return False
    stopped = threading.Event()
    lease_lost = threading.Event()
    def heartbeat():
        while not stopped.wait(max(1, queue.lease_seconds / 3)):
            try:
                if not queue.heartbeat(job):
                    lease_lost.set()
                    return
            except Exception:
                lease_lost.set()
                return
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with task_scope(queue, job, lease_lost):
            result = execute_job(job)
        if not lease_lost.is_set():
            queue.finish(job, result=result)
    except Exception as exc:
        # 异常可能包含私密输入或供应商回显，只记录类型和任务ID。
        logger.warning('任务 %s 失败: %s', job['id'], type(exc).__name__)
        if not lease_lost.is_set():
            queue.finish(job, error=type(exc).__name__)
    finally:
        stopped.set()
        thread.join(timeout=5)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='独立 PostgreSQL 任务进程')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--schedule', action='store_true', help='显式启用每小时采集调度')
    args = parser.parse_args(argv)
    repo = get_repository()
    queue = JobQueue(repo)
    while True:
        if args.schedule:
            hour = datetime.now(timezone.utc).strftime('%Y%m%d%H')
            queue.enqueue('pipeline', {'source_mode': 'live', 'include_llm': False},
                          idempotency_key='hourly:' + hour)
        worked = run_one(queue)
        if args.once:
            return 0
        if not worked:
            time.sleep(2)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
