"""启动即采集（boot crawl）的回归测试：后台线程、失败不抛出、返回结果。"""
import time


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_boot_crawl_runs_once_in_background(monkeypatch):
    import app as app_module
    calls = []

    class FakeScheduler:
        def run_crawl_once(self):
            calls.append(1)
            return {'success': True, 'count': 3, 'errors': []}

    monkeypatch.setattr(app_module, 'get_scheduler', lambda: FakeScheduler())
    app_module._start_boot_crawl()
    assert _wait_until(lambda: calls == [1])


def test_boot_crawl_failure_does_not_raise(monkeypatch):
    import app as app_module
    touched = []

    class BrokenScheduler:
        def run_crawl_once(self):
            touched.append(1)
            raise RuntimeError('网络不可用')

    monkeypatch.setattr(app_module, 'get_scheduler', lambda: BrokenScheduler())
    app_module._start_boot_crawl()   # 不得抛出
    assert _wait_until(lambda: touched == [1])


def test_scheduler_run_crawl_once_returns_result(monkeypatch):
    from data.scheduler import CrawlerScheduler
    scheduler = CrawlerScheduler(config={'scheduler': {'enabled': False}})
    monkeypatch.setattr(scheduler, '_run_crawl_job',
                        lambda: setattr(scheduler, '_last_crawl_result', {'success': True, 'count': 7}))
    assert scheduler.run_crawl_once() == {'success': True, 'count': 7}
