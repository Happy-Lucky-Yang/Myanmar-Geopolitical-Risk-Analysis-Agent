"""
data.source_health - 数据源健康追踪器

记录每个数据源每轮爬取的成功/失败与产出条数，滚动保留最近 N 次，
持久化到 DATA_ROOT/processed/source_health.json（重启不丢失），
供 /api/sources/health 与前端"数据源健康"卡片展示。

状态判定规则（以“当前是否可用”为主，历史成功率仅作稳定性参考）：
- healthy  : 最近一次运行成功（含降级通道/备用入口成功）
- degraded : 最近一次失败，但 24 小时内有过成功（时好时坏/网络抖动）
- dead     : 最近一次失败且 24 小时内无成功，或最近 3 次全部失败

说明：采集器的“成功”已包含降级路径（代理/直连双通道兜底、RSS fallback 页面、
GDELT CSV 备用通道），不会再因单一通道不可用而误判失联；早期失败保留在成功率里
反映稳定性，不再长期拖住状态标签。
"""
import os
import json
import time
import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 每个源最多保留的历史记录条数
MAX_RECORDS = 20

# 状态判定使用的近期窗口大小（避免旧失败长期拖累状态）
RECENT_WINDOW = 6


class SourceHealthTracker:
    """线程安全的数据源健康追踪器（进程内单例 + JSON 持久化）"""

    def __init__(self, persist_path: str = None):
        self._lock = threading.Lock()
        self._records: Dict[str, deque] = {}
        from storage.repository import get_repository
        self._repo = get_repository()
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
        from utils.data_contract import utc_now
        entry = {
            "time": utc_now().isoformat(timespec="seconds"),
            "ok": bool(ok),
            "count": int(count),
            "error": (str(error)[:150] if error else None),
        }
        with self._lock:
            if self._repo is not None:
                self._repo.save_source_run(source, entry)
                return
            self._records.setdefault(source, deque(maxlen=MAX_RECORDS)).append(entry)
            self._save()

    def get_health(self) -> Dict:
        """汇总所有数据源的健康状态"""
        with self._lock:
            if self._repo is not None:
                self._records = self._repo.load_source_runs(MAX_RECORDS)
            result = {}
            for source, dq in self._records.items():
                records = list(dq)
                total = len(records)
                success = sum(1 for r in records if r["ok"])
                rate = round(success / total, 3) if total else 0.0

                last3 = records[-3:]
                recent = records[-RECENT_WINDOW:]
                recent_rate = (sum(1 for r in recent if r["ok"]) / len(recent)
                               if recent else 0.0)

                # 最近一次成功时间
                last_success = None
                for r in reversed(records):
                    if r["ok"]:
                        last_success = r["time"]
                        break

                if total >= 3 and all(not r["ok"] for r in last3):
                    status = "dead"
                elif records and records[-1]["ok"]:
                    status = "healthy"
                elif _within_hours(last_success, 24):
                    status = "degraded"
                else:
                    status = "degraded" if total < 3 else "dead"

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
        if self._repo is not None:
            self._records = self._repo.load_source_runs(MAX_RECORDS)
            return
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
# 内部工具
# ============================================================

def _within_hours(timestamp: Optional[str], hours: int) -> bool:
    """时间戳是否落在最近 hours 小时内（解析失败视为不在窗口内）"""
    if not timestamp:
        return False
    try:
        moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - moment <= timedelta(hours=hours)
    except ValueError:
        return False


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
