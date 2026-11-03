"""一键运行爬虫 - 面向非工程师成员的简化入口。

用法：
  python run_crawler_only.py --help          查看全部模式（不会开始爬取）
  python run_crawler_only.py                 单次爬取全部启用来源
  python run_crawler_only.py --rss           仅 RSS 聚合与独立媒体
  python run_crawler_only.py --gdelt         仅 GDELT 事件与指标
  python run_crawler_only.py --source 缅华网 仅指定来源
  python run_crawler_only.py --schedule      前台常驻定时调度（Ctrl+C 停止）
  python run_crawler_only.py --dry-run       只报告将做什么与数据落点，不联网

设计约束：
- 非交互环境（CI、计划任务、管道）不得因等待回车而挂起，故不使用 input()。
- 数据实际落点由 utils.config.get_data_paths() 决定（可能是仓库外的 DATA_ROOT），
  打印路径必须来自该函数，不能写死 data/raw，否则成员会误以为数据丢失。
- 来源名称取自 config，不在提示文案里硬编码已停用的站点。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from utils.config import get_data_paths, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='缅甸新闻爬虫一键入口', formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='默认单次爬取全部启用来源；任何模式都不会触发分析流水线或大模型调用。')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--schedule', action='store_true', help='前台常驻定时调度，Ctrl+C 停止')
    mode.add_argument('--gdelt', action='store_true', help='仅查询 GDELT 事件并计算指标')
    mode.add_argument('--rss', action='store_true', help='仅爬取 RSS 聚合与独立媒体')
    mode.add_argument('--source', help='仅爬取指定来源名称（见 --list-sources）')
    parser.add_argument('--list-sources', action='store_true', help='列出当前启用来源后退出')
    parser.add_argument('--dry-run', action='store_true', help='只打印计划与数据落点，不联网')
    parser.add_argument('--days', type=int, default=7, help='GDELT 查询天数（默认 7）')
    parser.add_argument('--json', action='store_true', help='以 JSON 输出结果，便于脚本消费')
    return parser


def active_sources() -> dict:
    """来源清单来自配置，避免把已停用站点写死在文案里。"""
    config = load_config()
    feeds = {key: feed for key, feed in (config.get('rss_feeds') or {}).items()
             if isinstance(feed, dict) and feed.get('enabled', True)}
    return {'缅华网': 'data.crawler.NewsCrawler.crawl_all_sources',
            '英文媒体（Irrawaddy 等）': 'data.myanmar_now_crawler.EnglishNewsCrawler.crawl_all',
            **{feed.get('name', key): f'data.rss_crawler.{key}' for key, feed in feeds.items()},
            'GDELT': 'data.gdelt_crawler.GDELTNewsCrawler.crawl'}


def report(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print('=' * 50)
    for key, value in result.items():
        print(f'  {key}: {value}')
    print('=' * 50)


def crawl_once(only: str | None, days: int) -> dict:
    """逐源独立容错：单源失败不阻断其余来源，但失败必须显式出现在结果里。"""
    collected, failures = {}, {}
    targets = {'mhwmm': only in (None, '缅华网'),
               'english': only in (None, '英文媒体（Irrawaddy 等）'),
               'rss': only in (None, 'RSS', 'rss') or (only or '').startswith('Google') or (only or '').startswith('Kachin') or (only or '').startswith('UN'),
               'gdelt': only in (None, 'GDELT')}
    if only and only not in ('RSS',) and not any(targets.values()):
        targets['rss'] = True   # 未识别的名称先交给按名称过滤的 RSS 通道处理

    if targets['mhwmm']:
        try:
            from data.crawler import NewsCrawler
            crawler = NewsCrawler()
            news = crawler.crawl_all_sources()
            crawler.save_news(news)
            collected['缅华网'] = len(news)
        except Exception as error:
            failures['缅华网'] = f'{type(error).__name__}: {error}'

    if targets['english']:
        try:
            from data.myanmar_now_crawler import get_english_crawler
            crawler = get_english_crawler()
            news = crawler.crawl_all()
            crawler.save_news(news)
            collected['英文媒体'] = len(news)
        except Exception as error:
            failures['英文媒体'] = f'{type(error).__name__}: {error}'

    if targets['rss']:
        try:
            from data.rss_crawler import get_rss_crawler
            crawler = get_rss_crawler()
            news = crawler.crawl_all()
            crawler.save_news(news)
            collected['RSS 源'] = len(news)
        except Exception as error:
            failures['RSS 源'] = f'{type(error).__name__}: {error}'

    if targets['gdelt']:
        try:
            from data.gdelt_crawler import get_gdelt_crawler
            crawler = get_gdelt_crawler()
            news = crawler.crawl(timespan_days=days)
            crawler.save_news(news)
            collected['GDELT'] = len(news)
            if only in (None, 'GDELT'):
                collected['GDELT 指标'] = json.dumps(
                    crawler.get_risk_metrics(timespan_days=days), ensure_ascii=False)[:200]
        except Exception as error:
            failures['GDELT'] = f'{type(error).__name__}: {error}'

    paths = get_data_paths()
    return {'采集条数': collected, '失败来源': failures or '无',
            '数据落点': paths['raw'], '指标落点': paths['processed'],
            '下一步': '运行 python run_full_pipeline.py --skip-crawl --skip-llm 重算日指标'}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    if args.list_sources:
        print(json.dumps(list(active_sources().keys()), ensure_ascii=False, indent=2))
        return 0

    paths = get_data_paths()
    print(f'数据落点（由 DATA_ROOT/config 决定，非仓库内 data/）：{paths["root"]}')

    if args.dry_run:
        report({'模式': 'dry-run（未联网）', '启用来源': list(active_sources()),
                '将写入': paths['raw']}, args.json)
        return 0

    if args.schedule:
        from data.scheduler import get_scheduler
        scheduler = get_scheduler()
        scheduler.start()
        print('定时调度已启动，按 Ctrl+C 停止。')
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            scheduler.stop()
            print('已停止调度。')
        return 0

    only = 'GDELT' if args.gdelt else ('RSS' if args.rss else args.source)
    result = crawl_once(only, args.days)
    report(result, args.json)
    # 有来源失败时返回非零，便于计划任务与脚本发现，而不是静默“跑完了”
    return 1 if result['失败来源'] != '无' else 0


if __name__ == '__main__':
    raise SystemExit(main())
