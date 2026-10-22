"""GDELT 事件库往前补拉约 30 天（后台运行；事件库按 event_id 去重，中断后重跑安全）。

用法：
    python scripts/backfill_gdelt_events.py            # 默认 2880 个 15 分钟文件（约 30 天）
    python scripts/backfill_gdelt_events.py 1344       # 约 14 天
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logging.getLogger('urllib3').setLevel(logging.WARNING)

from data.gdelt_files import fetch_myanmar_events
from data.event_store import get_event_store

max_files = int(sys.argv[1]) if len(sys.argv) > 1 else 2880
store = get_event_store()
print('补拉前:', store.stats(), '| watermark:', store.checkpoint(), flush=True)

start = time.time()
# target_events 设极大值：全量模式下不因事件数达标而提前停止
events, newest = fetch_myanmar_events(max_files=max_files, target_events=10 ** 9)
added = store.append(events, watermark=newest.strftime('%Y%m%d%H%M%S') if newest else None)
stats = store.stats()
print(f'\n补拉完成：下载 {max_files} 个批次文件，解析缅甸事件 {len(events)} 条，新入库 {added} 条，'
      f'耗时 {(time.time() - start) / 60:.1f} 分钟', flush=True)
print('补拉后:', stats, '| watermark:', store.checkpoint(), flush=True)
