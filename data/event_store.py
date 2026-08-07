"""
data.event_store - GDELT 缅甸事件累积库（追加持久化）

解决"单一窗口样本稀疏"问题：每轮调度只增量拉取新批次，
按 GlobalEventID 去重后追加入库，长期积累形成 30/90 天乃至
更长的历史事件库，供 KDE 密度分析与风险指标复用。

跨源可信度：GDELT 本身聚合全球新闻媒体，同一事件被多家
信源报道时 NumSources>1（已保存为 num_sources 字段），
KDE 侧可对多信源互证事件加权，实现"相互印证真实性"。

存储：DATA_ROOT/processed/gdelt_event_store.json
保留策略：超过 keep_days（默认 180 天）的事件自动修剪。
"""
import os
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认保留天数（缅甸事件日均约 30 条，180 天约 5~6 千条，JSON 约 1MB）
DEFAULT_KEEP_DAYS = 180


class EventStore:
    """GDELT 事件累积库（线程安全，原子写入）"""

    def __init__(self, persist_path: str = None,
                 keep_days: int = DEFAULT_KEEP_DAYS):
        self._lock = threading.Lock()
        self._keep_days = keep_days
        if persist_path is None:
            from utils.config import get_data_paths
            persist_path = os.path.join(
                get_data_paths()["processed"], "gdelt_event_store.json")
        self._file = persist_path
        self._events: List[Dict] = []
        self._ids = set()
        self._load()

    # ============================================================
    # 追加与查询
    # ============================================================

    def append(self, events: List[Dict]) -> int:
        """
        追加事件（按 event_id 去重；无 ID 的旧格式事件按全字段指纹去重）

        :return: 实际新增条数
        """
        added = 0
        with self._lock:
            for ev in events or []:
                eid = ev.get("event_id") or self._fingerprint(ev)
                if eid in self._ids:
                    continue
                self._ids.add(eid)
                self._events.append(ev)
                added += 1
            if added:
                self._prune_locked()
                self._save_locked()
        if added:
            logger.info(
                f"[EventStore] 新增 {added} 条事件"
                f"（库内累计 {len(self._events)} 条）")
        return added

    def load(self, days: int = None) -> List[Dict]:
        """
        读取事件（可选时间窗口过滤）

        :param days: 仅返回最近 N 天（按事件日期），None=全部
        """
        with self._lock:
            if not days:
                return list(self._events)
            cutoff = (datetime.now() - timedelta(days=days)
                      ).strftime("%Y%m%d")
            return [ev for ev in self._events
                    if ev.get("date", "") >= cutoff]

    def stats(self) -> Dict:
        """库概况（供健康检查/日志）"""
        with self._lock:
            dates = sorted(ev.get("date", "") for ev in self._events)
            return {
                "total": len(self._events),
                "earliest": dates[0] if dates else None,
                "latest": dates[-1] if dates else None,
                "located": sum(1 for ev in self._events
                               if ev.get("lat") is not None),
            }

    # ============================================================
    # 内部方法（调用方需持锁）
    # ============================================================

    @staticmethod
    def _fingerprint(ev: Dict) -> str:
        """无 event_id 时的去重指纹：日期+位置+事件码"""
        return f"{ev.get('date')}|{ev.get('event_code')}|{ev.get('location')}"

    def _prune_locked(self):
        """修剪超过保留期的事件"""
        cutoff = (datetime.now() - timedelta(days=self._keep_days)
                  ).strftime("%Y%m%d")
        before = len(self._events)
        kept = []
        kept_ids = set()
        for ev in self._events:
            if ev.get("date", "") >= cutoff:
                kept.append(ev)
                kept_ids.add(ev.get("event_id")
                             or self._fingerprint(ev))
        self._events = kept
        self._ids = kept_ids
        if len(kept) < before:
            logger.info(f"[EventStore] 修剪 {before - len(kept)} 条过期事件")

    def _save_locked(self):
        try:
            payload = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "event_count": len(self._events),
                "events": self._events,
            }
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self._file)
        except Exception as e:
            logger.warning(f"[EventStore] 持久化失败: {e}")

    def _load(self):
        try:
            if not os.path.exists(self._file):
                return
            with open(self._file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            with self._lock:
                self._events = payload.get("events", [])
                self._ids = {
                    ev.get("event_id") or self._fingerprint(ev)
                    for ev in self._events
                }
                self._prune_locked()
            logger.info(
                f"[EventStore] 载入 {len(self._events)} 条历史事件")
        except Exception as e:
            logger.warning(f"[EventStore] 加载失败: {e}")


# ============================================================
# 进程级单例
# ============================================================
_instance = None
_instance_lock = threading.Lock()


def get_event_store() -> EventStore:
    """获取全局事件累积库单例（线程安全）"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EventStore()
    return _instance
