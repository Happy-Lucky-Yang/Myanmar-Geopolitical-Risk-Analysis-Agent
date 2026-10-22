"""缅华网历史新闻回补：按 ?page=N 翻页采集，提高日指标覆盖率（趋势/预警依赖）。

用法：
    python scripts/backfill_mhwmm_history.py 12     # 爬前 12 页（约 12×29 篇，去重后入库）
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from data.crawler import NewsCrawler

pages = int(sys.argv[1]) if len(sys.argv) > 1 else 12
crawler = NewsCrawler()
crawler._max_pages = pages          # 覆盖 config 的 max_pages（默认 3）
base = 'https://www.mhwmm.com/miandianxinwen/'

news = crawler._crawl_mhwmm(base)
print(f'\n采集 {len(news)} 条（去重后新文章）', flush=True)
# 文件名必须带 myanmar_news_ 前缀，否则 DataLoader.load_raw_news 不会读取
from datetime import datetime
path = crawler.save_news(news, filename=f"myanmar_news_backfill_{datetime.now():%Y%m%d}.json")
print('已保存:', path, flush=True)

from collections import Counter
days = Counter((n.get('pub_time') or '')[:10] for n in news)
print('覆盖日期数:', len([d for d in days if d]), flush=True)
print('最近 15 天分布:', sorted(days.items())[-15:], flush=True)
