"""
data.source_health - 数据源健康追踪器

记录每个数据源每轮爬取的成功/失败与产出条数，滚动保留最近 N 次，
持久化到 DATA_ROOT/processed/source_health.json（重启不丢失），
供 /api/sources/health 与前端"数据源健康"卡片展示。

状态判定规则：
- healthy  : 成功率 >= 70% 且最近一次成功
- degraded : 其余情况（时好时坏）
- dead     : 最近 3 次全部失败（站点停运/被封，应关注替代源）
"""
import os
import json
import time
import logging
import threading
from collections import deque
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 每个源最多保留的历史记录条数
MAX_RECORDS = 20


class SourceHealthTracker:
    """线程安全的数据源健康追踪器（进程内单例 + JSON 持久化）"""

    def __init__(self, persist_path: str = None):
        self._lock = threading.Lock()
        self._records: Dict[str, deque] = {}
        if persist_path is None:
            from utils.config import get_data_paths
            persist_path = os.path.join(
                get_data_paths()["processed"], "source_health.json"
            )
        self._file = persist_path
        self._load()

    # ============================================================
    # 记录与查询
    # ============================================================

    def record(self, source: str, ok: bool, count: int = 0,
               error: Optional[str] = None):
        """
        上报一次爬取结果

        :param source: 数据源名称（如 "缅甸缅华网"、"GDELT"）
        :param ok: 本轮是否成功（成功 = 请求正常完成，允许 0 条产出）
        :param count: 本轮新增条数
        :param error: 失败原因（截断保存，避免文件膨胀）
        """
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "ok": bool(ok),
            "count": int(count),
            "error": (str(error)[:150] if error else None),
        }
        with self._lock:
            self._records.setdefault(source, deque(maxlen=MAX_RECORDS)).append(entry)
            self._save()

    def get_health(self) -> Dict:
        """汇总所有数据源的健康状态"""
        with self._lock:
            result = {}
            for source, dq in self._records.items():
                records = list(dq)
                total = len(records)
                success = sum(1 for r in records if r["ok"])
                rate = round(success / total, 3) if total else 0.0

                last3 = records[-3:]
                if total >= 3 and all(not r["ok"] for r in last3):
                    status = "dead"
                elif total > 0 and records[-1]["ok"] and rate >= 0.7:
                    status = "healthy"
                else:
                    status = "degraded"

                # 最近一次成功时间
                last_success = None
                for r in reversed(records):
                    if r["ok"]:
                        last_success = r["time"]
                        break

                result[source] = {
                    "status": status,
                    "success_rate": rate,
                    "recent_attempts": total,
                    "recent_success": success,
                    "last_count": records[-1]["count"] if records else 0,
                    "last_success": last_success,
                    "last_error": records[-1].get("error") if records else None,
                    "records": records[-10:],  # 前端展示最近 10 条明细
                }
            return result

    # ============================================================
    # 持久化
    # ============================================================

    def _load(self):
        try:
            if os.path.exists(self._file):
                with open(self._file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for source, records in data.items():
                    self._records[source] = deque(records, maxlen=MAX_RECORDS)
        except Exception as e:
            logger.warning(f"[SourceHealth] 加载历史记录失败: {e}")

    def _save(self):
        """（调用方需已持有锁）写入 JSON，失败仅告警不抛出"""
        try:
            data = {s: list(dq) for s, dq in self._records.items()}
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._file)
        except Exception as e:
            logger.warning(f"[SourceHealth] 持久化失败: {e}")


# ============================================================
# 进程级单例
# ============================================================
_instance = None
_instance_lock = threading.Lock()


def get_source_health_tracker() -> SourceHealthTracker:
    """获取全局健康追踪器单例（线程安全）"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = SourceHealthTracker()
    return _instance
