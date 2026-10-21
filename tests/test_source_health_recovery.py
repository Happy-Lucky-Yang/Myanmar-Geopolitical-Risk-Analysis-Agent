"""数据源健康恢复：双通道回退、状态判定、停用源隐藏、GDELT 上报合并、UN News 频率。"""
from types import SimpleNamespace

PROXY = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}


def test_rss_request_falls_back_to_direct_when_proxy_fails(monkeypatch):
    import data.rss_crawler as rss
    monkeypatch.setattr('utils.config.get_proxy', lambda: dict(PROXY))
    crawler = rss.RSSNewsCrawler(config={'max_retries': 2})
    seen = []

    def fake_get(url, **kwargs):
        seen.append(kwargs.get('proxies'))
        if kwargs.get('proxies'):
            raise rss.requests.exceptions.ProxyError('proxy down')
        return SimpleNamespace(status_code=200, text='ok')
    monkeypatch.setattr(rss.requests, 'get', fake_get)
    monkeypatch.setattr(rss.time, 'sleep', lambda *_: None)
    resp = crawler._request('https://example.test/feed')
    assert resp.status_code == 200
    assert seen[0] and seen[1] is None  # 首选代理，失败后直连


def test_direct_request_falls_back_to_proxy_when_needed(monkeypatch):
    import data.myanmar_now_crawler as en
    monkeypatch.setattr('utils.config.get_proxy', lambda: dict(PROXY))
    crawler = en.EnglishNewsCrawler(config={'max_retries': 2})
    seen = []

    def fake_get(url, **kwargs):
        seen.append(kwargs.get('proxies'))
        return SimpleNamespace(status_code=200, text='ok')
    monkeypatch.setattr(en.requests, 'get', fake_get)
    resp = crawler._request('https://www.irrawaddy.com/news/burma')
    assert resp.status_code == 200 and seen[0] == PROXY


def test_health_status_recovers_after_recent_success(tmp_path):
    from data.source_health import SourceHealthTracker
    recovered = SourceHealthTracker(str(tmp_path / 'h.json'))
    recovered.record('s', False, error='x')
    recovered.record('s', False, error='x')
    recovered.record('s', True, count=3)
    assert recovered.get_health()['s']['status'] == 'healthy'  # 最近一次成功即恢复健康

    flaky = SourceHealthTracker(str(tmp_path / 'h2.json'))
    flaky.record('x', True)
    flaky.record('x', False, error='network')
    assert flaky.get_health()['x']['status'] == 'degraded'  # 24h 内有过成功

    down = SourceHealthTracker(str(tmp_path / 'h3.json'))
    for _ in range(3):
        down.record('y', False, error='403')
    assert down.get_health()['y']['status'] == 'dead'


def test_health_endpoint_hides_disabled_sources(monkeypatch):
    import app as app_module
    fake = {'Frontier Myanmar': {'status': 'dead'},
            'Popular Myanmar': {'status': 'dead'},
            '缅甸缅华网': {'status': 'healthy'},
            'Google News 聚合(中文)': {'status': 'healthy'}}
    monkeypatch.setattr('data.source_health.get_source_health_tracker',
                        lambda: SimpleNamespace(get_health=lambda: fake))
    data = app_module.app.test_client().get('/api/sources/health').get_json()['data']
    assert '缅甸缅华网' in data and 'Google News 聚合(中文)' in data
    assert 'Frontier Myanmar' not in data and 'Popular Myanmar' not in data


def test_gdelt_doc_failure_is_not_recorded_as_dead(monkeypatch):
    import data.gdelt_crawler as mod
    recorded = []

    class Tracker:
        def record(self, *args, **kwargs):
            recorded.append((args, kwargs))
    monkeypatch.setattr('data.source_health.get_source_health_tracker', lambda: Tracker())
    crawler = mod.GDELTCrawler(config={'enabled': True, 'use_multi_query': True,
                                       'query_keywords': ['Myanmar'], 'max_results': 5})
    monkeypatch.setattr(crawler._client, 'search_articles_multi', lambda **kwargs: [])
    assert crawler.crawl(timespan_days=7) == []
    assert all(args[1] is not False for args, _ in recorded)


def test_un_news_follows_crawl_cadence():
    from data.scheduler import CrawlerScheduler
    scheduler = CrawlerScheduler(config={'scheduler': {'enabled': False}})
    assert scheduler._un_news_interval == 4  # 跟随新闻爬取每 4 小时一次
