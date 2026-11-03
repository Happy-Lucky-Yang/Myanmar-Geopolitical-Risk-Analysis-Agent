"""按业务日定向回补 Google News 聚合源，填补日指标缺失的日期。

用途：调度器停摆若干天后，普通爬取只取当前列表页，无法回填历史窗口。
Google News RSS 支持在查询里加 after:/before: 时间算子，可逐日定向取回。
产物文件名必须带 rss_news_ 前缀，否则 DataLoader.load_raw_news 不会读取。

用法：
    python scripts/backfill_rss_by_date.py 2026-10-23 2026-10-24
    python scripts/backfill_rss_by_date.py --from 2026-10-23 --to 2026-11-03
"""
import argparse
import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from data.rss_crawler import get_rss_crawler

# 仅这两个源是可按时间算子检索的聚合入口；其余源走各自的翻页回补脚本。
FEEDS = ('google_news_en', 'google_news_zh')
FILENAME = f'rss_news_backfill_{datetime.now():%Y%m%d}.json'


def dated_url(rss_url: str, day: date) -> str:
    """把 after:/before: 追加到原查询；保留 hl/gl/ceid 等区域参数不变。"""
    parts = urlsplit(rss_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    base_q = (query.get('q') or ['Myanmar'])[0]
    # Google News 的 before: 为排他日，因此区间是 [day, day+1)。
    scoped = f'{base_q} after:{day:%Y-%m-%d} before:{day + timedelta(days=1):%Y-%m-%d}'
    query['q'] = [scoped]
    rebuilt = urlencode({k: v[0] if len(v) == 1 else v for k, v in query.items()}, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, rebuilt, ''))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dates', nargs='*', help='要回补的业务日 YYYY-MM-DD')
    parser.add_argument('--from', dest='start', help='回补区间起始日（含）')
    parser.add_argument('--to', dest='end', help='回补区间结束日（含）')
    parser.add_argument('--per-source', type=int, default=100, help='单源单日最多取回条数')
    return parser.parse_args()


def requested_days(args) -> list:
    if args.start or args.end:
        if not (args.start and args.end):
            sys.exit('--from 与 --to 必须同时给出')
        start = datetime.strptime(args.start, '%Y-%m-%d').date()
        end = datetime.strptime(args.end, '%Y-%m-%d').date()
        if end < start:
            sys.exit('--to 不能早于 --from')
        if (end - start).days > 90:
            sys.exit('单次回补区间不得超过 90 天，请分段执行以免长时间占用代理通道')
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if args.dates:
        return [datetime.strptime(value, '%Y-%m-%d').date() for value in args.dates]
    sys.exit('请给出日期参数，或使用 --from/--to 指定区间')


def main():
    args = parse_args()
    days = requested_days(args)
    crawler = get_rss_crawler()
    crawler._max_per_source = args.per_source
    today = date.today()
    feeds = {key: dict(crawler._feeds[key]) for key in FEEDS if key in crawler._feeds}
    if not feeds:
        sys.exit('配置中找不到 Google News 聚合源，无法按日期回补')

    total, per_day = 0, Counter()
    for day in days:
        if day > today:
            print(f'{day}: 晚于本机日期，跳过（不伪造未来观测）', flush=True)
            continue
        collected = []
        for key, config in feeds.items():
            url = dated_url(config['rss_url'], day)
            try:
                response = crawler._request(url)
            except Exception as error:
                print(f'{day} [{key}] 请求异常: {type(error).__name__}', flush=True)
                continue
            if response is None:
                # 不抛错也不落盘：该日仍缺测，由覆盖率与告警如实反映
                print(f'{day} [{key}] 未取回内容（限流或无结果），保持缺测', flush=True)
                continue
            articles = crawler._parse_feed(response.text, key)
            collected.extend(articles)
            print(f'{day} [{key}] 取回 {len(articles)} 条', flush=True)
        if collected:
            crawler.save_news(collected, filename=FILENAME)
            crawler._save_urls_seen()   # 与落盘同步，避免标记已见却没存下数据
            per_day[day.isoformat()] = len(collected)
            total += len(collected)
        else:
            per_day[day.isoformat()] = 0

    print('\n===== 回补汇总 =====', flush=True)
    for day in days:
        print(f'  {day}: {per_day.get(day.isoformat(), 0)} 条', flush=True)
    print(f'合计新增 {total} 条，写入 {FILENAME}', flush=True)
    if total == 0:
        print('未取回任何条目：日指标将保持缺测，不会用旧值或零值填充。', flush=True)
    else:
        print('下一步：python -c "from pipeline import run_pipeline; run_pipeline(source_mode=\'existing\', include_llm=False)"', flush=True)


if __name__ == '__main__':
    main()
