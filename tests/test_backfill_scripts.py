"""回补脚本契约：时间算子正确、区间校验严格、失败保持缺测不伪造数据。"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.backfill_rss_by_date import dated_url, requested_days  # noqa: E402


class _Args:
    def __init__(self, dates=(), start=None, end=None):
        self.dates, self.start, self.end = list(dates), start, end


def test_dated_url_scopes_query_and_keeps_locale():
    url = ('https://news.google.com/rss/search?q=Myanmar+conflict'
           '&hl=en-US&gl=US&ceid=US:en')
    built = dated_url(url, date(2026, 10, 25))
    assert 'after%3A2026-10-25' in built and 'before%3A2026-10-26' in built
    # before 为排他日：区间只覆盖 10-25 当天，不会重复取回次日数据。
    assert 'hl=en-US' in built and 'ceid=US%3Aen' in built
    assert 'conflict' in built  # 原关键词不被替换掉


def test_dated_url_handles_percent_encoded_chinese_query():
    url = ('https://news.google.com/rss/search?q=%E7%BC%85%E7%94%B8'
           '&hl=zh-CN&gl=CN&ceid=CN:zh-Hans')
    built = dated_url(url, date(2026, 11, 1))
    assert 'after%3A2026-11-01' in built and 'before%3A2026-11-02' in built
    assert '%E7%BC%85%E7%94%B8' in built  # “缅甸”原词保留


def test_requested_days_validates_range():
    assert requested_days(_Args(dates=['2026-10-23', '2026-10-25'])) == [date(2026, 10, 23), date(2026, 10, 25)]
    span = requested_days(_Args(start='2026-10-23', end='2026-10-26'))
    assert span == [date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26)]
    for bad in (_Args(start='2026-10-23'), _Args(end='2026-10-26'),
               _Args(start='2026-10-26', end='2026-10-23'),
               _Args(start='2026-01-01', end='2026-12-31'), _Args()):
        with pytest.raises(SystemExit):
            requested_days(bad)
