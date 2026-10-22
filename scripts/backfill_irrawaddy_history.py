"""Irrawaddy 历史翻页回补：提高最近 30 天的日指标覆盖率。

用法：python scripts/backfill_irrawaddy_history.py 6
"""
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from data.myanmar_now_crawler import get_english_crawler

pages = int(sys.argv[1]) if len(sys.argv) > 1 else 6
crawler = get_english_crawler()
crawler._max_pages = pages

news = crawler._crawl_irrawaddy()
print(f'\n采集 {len(news)} 条', flush=True)
# 文件名必须带 myanmar_now_ 前缀，否则 DataLoader.load_raw_news 不会读取
from datetime import datetime
print('已保存:', crawler.save_news(news, filename=f"myanmar_now_backfill_{datetime.now():%Y%m%d}.json"), flush=True)

days = Counter((n.get('pub_time') or '')[:16] for n in news)
print('日期样本:', sorted(days.items())[:6], '...', sorted(days.items())[-6:], flush=True)
