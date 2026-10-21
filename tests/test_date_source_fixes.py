"""日期解析/修复、缅华网分页、GDELT 永久缺失批次、本机代理降级的回归测试。"""
import io
import zipfile
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from utils.data_contract import business_date
from utils.date_repair import fix_missing_dates


def test_business_date_parses_rss_and_english_dates():
    # RFC-822 按自带时区换算到仰光业务日（UTC 19:21 = 仰光次日 01:51）
    assert business_date('Mon, 19 Oct 2026 19:21:59 GMT') == date(2026, 10, 20)
    assert business_date('Tue, 04 Aug 2026 18:06:00 +0900') == date(2026, 8, 4)
    assert business_date('October 20, 2026') == date(2026, 10, 20)
    assert business_date('Oct 20, 2026') == date(2026, 10, 20)
    assert business_date('Mon, 99 Foo 2026') is None


def test_fix_missing_dates_rejects_historical_and_impossible_dates():
    crawled = '2026-10-20T09:00:00'
    rows = [
        {'pub_time': 'Mon, 19 Oct 2026 19:21:59 GMT', 'crawled_at': crawled},              # 可解析，不改动
        {'pub_time': '', 'content': '自2021年2月1日政变以来局势持续动荡', 'crawled_at': crawled},
        {'pub_time': '', 'content': '据2026年10月18日消息，若开邦交火', 'crawled_at': crawled},
        {'pub_time': '', 'url': 'https://news.un.org/zh/story/2018/04/1006692', 'crawled_at': crawled},
        {'pub_time': '', 'url': 'https://example.test/2026/02/31/a', 'crawled_at': crawled},
        {'pub_time': 'garbage', 'url': 'https://example.test/2026/10/15/b.html', 'crawled_at': crawled},
        {'pub_time': ''},
    ]
    stats = fix_missing_dates(rows)
    assert rows[0]['pub_time'].startswith('Mon') and 'date_quality' not in rows[0]
    # 正文中的历史日期不能把新报道回溯到 2021 年
    assert rows[1]['pub_time'] == '2026-10-20' and rows[1]['date_quality'] == 'fallback_crawled'
    assert rows[2]['pub_time'] == '2026-10-18' and rows[2]['date_quality'] == 'inferred_content'
    # "年/月/文章ID" 不是日期；2 月 31 日不是合法日期
    assert rows[3]['date_quality'] == 'fallback_crawled'
    assert rows[4]['date_quality'] == 'fallback_crawled'
    assert rows[5]['pub_time'] == '2026-10-15' and rows[5]['pub_time_raw'] == 'garbage'
    assert stats == {'fixed': 5, 'from_url': 1, 'from_content': 1, 'from_crawled': 3, 'still_missing': 1}


def test_mhwmm_pagination_uses_query_page(monkeypatch):
    with patch('data.crawler.get_crawler_config', return_value={'sources': [], 'max_pages': 3}), \
         patch('data.crawler.get_storage_config', return_value={'format': 'json'}):
        from data.crawler import NewsCrawler
        crawler = NewsCrawler()
    called = []
    monkeypatch.setattr(crawler, 'retry_request', lambda url, **kw: called.append(url) or SimpleNamespace(text=''))
    monkeypatch.setattr('data.crawler.time.sleep', lambda *_: None)
    crawler._get_mhwmm_article_urls('https://www.mhwmm.com/miandianxinwen/', max_pages=3)
    assert called == ['https://www.mhwmm.com/miandianxinwen/',
                      'https://www.mhwmm.com/miandianxinwen?page=2',
                      'https://www.mhwmm.com/miandianxinwen?page=3']


def test_gdelt_old_permanent_gap_is_skipped(monkeypatch):
    import data.gdelt_files as files
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.writestr('events.csv', '')
    monkeypatch.setattr(files, '_get_latest_batch_ts', lambda *a: datetime(2026, 1, 1, 3, 0))
    called = []

    def get(url, **kwargs):
        called.append(url)
        return SimpleNamespace(status_code=404 if '003000' in url else 200,
                               content=archive.getvalue(), raise_for_status=lambda: None)
    monkeypatch.setattr(files.requests, 'get', get)
    rows, watermark = files.fetch_myanmar_events(max_files=12, since_ts=datetime(2026, 1, 1))
    # 00:30 早于最新批次 2.5 小时仍缺失 → 视为永久空洞，水位越过它推进到最新
    assert rows == [] and watermark == datetime(2026, 1, 1, 3, 0) and len(called) == 12


def test_local_proxy_down_falls_back_to_direct(monkeypatch):
    import utils.config as config
    monkeypatch.setattr(config, '_proxy_probe', {})
    monkeypatch.setenv('PROXY', 'http://127.0.0.1:1')
    assert config.get_proxy() == {}
    monkeypatch.setenv('PROXY', 'http://proxy.example.test:8080')  # 非本机代理不探测
    assert config.get_proxy() == {'http': 'http://proxy.example.test:8080', 'https': 'http://proxy.example.test:8080'}
