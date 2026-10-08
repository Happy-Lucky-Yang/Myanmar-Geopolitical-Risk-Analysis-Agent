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
from copy import deepcopy
from utils.data_contract import business_date, time_window, fingerprint, json_safe, utc_now

logger = logging.getLogger(__name__)

# 默认保留天数（缅甸事件日均约 30 条，180 天约 5~6 千条，JSON 约 1MB）
DEFAULT_KEEP_DAYS = 180


class EventStore:
    """GDELT 事件累积库（线程安全，原子写入）"""

    def __init__(self, persist_path: str = None,
                 keep_days: int = DEFAULT_KEEP_DAYS):
        from storage.repository import get_repository
        self._repository = get_repository()
        self._watermark = None
        self._load_error = False
        self._lock = threading.Lock()
        self._keep_days = keep_days
        if persist_path is None:
            from utils.config import get_data_paths
            persist_path = os.path.join(
                get_data_paths()["processed"], "gdelt_event_store.json")
        self._file = persist_path
        self._events: List[Dict] = []
        self._ids = set()
        if self._repository is None:
            self._load()

    # ============================================================
    # 追加与查询
    # ============================================================

    def append(self, events: List[Dict], watermark=None) -> int:
        """
        追加事件（按 event_id 去重；无 ID 的旧格式事件按全字段指纹去重）

        :return: 实际新增条数
        """
        if self._load_error:
            raise RuntimeError("事件文件损坏，禁止覆盖；请先备份并人工核验")
        if watermark is not None:
            if len(str(watermark)) != 14:
                raise ValueError("水位必须为14位UTC批次时间")
            datetime.strptime(str(watermark), "%Y%m%d%H%M%S")
        events = list(events or [])
        if any(not isinstance(ev, dict) or business_date(ev.get("date")) is None for ev in events):
            raise ValueError("事件批次存在无效记录，未提交或推进水位")
        if self._repository is not None:
            return len(self._repository.save_events(events, watermark=watermark))
        added = 0
        with self._lock:
            before = (list(self._events), set(self._ids), self._watermark)
            for ev in events:
                eid = str(ev.get("event_id") or self._fingerprint(ev))
                if eid in self._ids:
                    continue
                self._ids.add(eid)
                self._events.append(deepcopy(ev))
                added += 1
            if watermark and (not self._watermark or str(watermark) > self._watermark):
                self._watermark = str(watermark)
            if added or watermark:
                try:
                    self._save_locked()
                except Exception:
                    self._events, self._ids, self._watermark = before
                    raise
        if added:
            logger.info(
                f"[EventStore] 新增 {added} 条事件"
                f"（库内累计 {len(self._events)} 条）")
        return added

    def load(self, days: int = None, end_date=None) -> List[Dict]:
        """
        读取事件（可选时间窗口过滤）

        :param days: 仅返回最近 N 天（按事件日期），None=全部
        """
        if self._repository is not None:
            return self._repository.load_events(days, end_date)
        with self._lock:
            records = [ev for ev in self._events if ev.get("run_kind", "existing") in {"live", "existing"}]
            if days is None:
                return deepcopy(records)
            start, end = time_window(days, end_date)
            return deepcopy([ev for ev in records if business_date(ev.get("date")) is not None
                             and start <= business_date(ev["date"]) < end])

    def checkpoint(self):
        return self._repository.checkpoint() if self._repository is not None else self._watermark

    def stats(self) -> Dict:
        """库概况（供健康检查/日志）"""
        if self._repository is not None:
            records = self.load()
            dates = sorted(ev.get("date", "") for ev in records)
            return {"total": len(records), "earliest": dates[0] if dates else None,
                    "latest": dates[-1] if dates else None,
                    "located": sum(ev.get("lat") is not None for ev in records)}
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
        return fingerprint(ev)

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
                "updated_at": utc_now().isoformat(),
                "watermark": self._watermark,
                "event_count": len(self._events),
                "events": self._events,
            }
            os.makedirs(os.path.dirname(os.path.abspath(self._file)), exist_ok=True)
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(json_safe(payload), f, ensure_ascii=False, allow_nan=False)
            os.replace(tmp, self._file)
        except Exception as e:
            logger.warning("[EventStore] 持久化失败，批次已回滚")
            raise

    def _load(self):
        try:
            if not os.path.exists(self._file):
                return
            with open(self._file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            with self._lock:
                candidates = payload.get("events", [])
                self._events = [ev for ev in candidates if isinstance(ev, dict) and business_date(ev.get("date")) is not None]
                if len(candidates) != len(self._events):
                    self._load_error = True
                    logger.warning("[EventStore] 跳过 %s 条无效事件，保留原文件", len(candidates) - len(self._events))
                self._watermark = payload.get("watermark")
                self._ids = {str(ev.get("event_id") or self._fingerprint(ev)) for ev in self._events}
            logger.info(
                f"[EventStore] 载入 {len(self._events)} 条历史事件")
        except Exception as e:
            self._load_error = True
            logger.warning("[EventStore] 加载失败（%s），已阻止覆盖损坏文件", type(e).__name__)


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
