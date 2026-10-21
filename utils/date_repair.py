"""发布日期修复：URL/正文推断，失败用爬取时间兜底，并以 date_quality 标明来历。

只修复 pub_time 无法解析为业务日的条目；原值保留在 pub_time_raw，便于审计。
正文日期只在爬取日前 CONTENT_LOOKBACK_DAYS 天内才采信，避免把正文里引用的
历史日期（如"2021年2月1日政变"）误当作新报道的发布日。
"""
import re
from datetime import date, timedelta
from typing import Dict, List, Optional

from utils.data_contract import business_date

CONTENT_LOOKBACK_DAYS = 30

# 路径段日期要求以 / 或结尾收尾，避免 /2018/04/1006692 这类"年/月/文章ID"被读成日期
_URL_PATTERNS = (
    r'/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)',
    r'(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)',
    r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)',
)
_CONTENT_PATTERNS = (
    r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
    r'(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)',
)


def _calendar_date(match) -> Optional[date]:
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _date_from_url(url: str, reference: date) -> Optional[date]:
    for pattern in _URL_PATTERNS:
        for match in re.finditer(pattern, url or ''):
            day = _calendar_date(match)
            if day is not None and day <= reference:
                return day
    return None


def _date_from_content(content: str, reference: date) -> Optional[date]:
    earliest = reference - timedelta(days=CONTENT_LOOKBACK_DAYS)
    for pattern in _CONTENT_PATTERNS:
        for match in re.finditer(pattern, content or ''):
            day = _calendar_date(match)
            if day is not None and earliest <= day <= reference:
                return day
    return None


def fix_missing_dates(news_list: List[Dict]) -> Dict[str, int]:
    """原地修复 pub_time 缺失或不可解析的新闻，返回各策略命中计数。"""
    stats = {'fixed': 0, 'from_url': 0, 'from_content': 0, 'from_crawled': 0, 'still_missing': 0}
    for item in news_list:
        if not isinstance(item, dict) or business_date(item.get('pub_time')) is not None:
            continue
        crawled = business_date(item.get('crawled_at'))
        reference = crawled or business_date()
        for key, quality, day in (
            ('from_url', 'inferred_url', lambda: _date_from_url(item.get('url', ''), reference)),
            ('from_content', 'inferred_content', lambda: _date_from_content(item.get('content', ''), reference)),
            ('from_crawled', 'fallback_crawled', lambda: crawled),
        ):
            found = day()
            if found is not None:
                if item.get('pub_time'):
                    item.setdefault('pub_time_raw', item['pub_time'])
                item.update(pub_time=found.isoformat(), date_precision='day', date_quality=quality)
                stats[key] += 1
                stats['fixed'] += 1
                break
        else:
            stats['still_missing'] += 1
    return stats
