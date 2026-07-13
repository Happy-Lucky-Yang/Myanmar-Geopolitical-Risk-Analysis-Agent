"""
analyzer.alert_monitor - 动态预警面板

process.html 要求: "关键阈值监测、实时风险提示、辅助决策"

功能:
  - 定义预警阈值: 风险分>=80(红色)、>=60(橙色)、>=40(黄色)
  - 实时监测: 每次分析后检查是否触发预警
  - 预警历史: 存入 data/raw/alerts.json
  - 当前预警状态查询接口

前端:
  - 导航栏预警指示灯 (红/橙/绿)
  - 预警弹窗/横幅通知
  - 趋势页面阈值参考线
"""
import os
import json
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 预警等级定义
# ============================================================
ALERT_LEVELS = {
    "red": {"threshold": 80, "label": "红色预警", "color": "#f85149",
            "description": "风险极高，需立即关注"},
    "orange": {"threshold": 60, "label": "橙色预警", "color": "#d29922",
               "description": "风险较高，需密切关注"},
    "yellow": {"threshold": 40, "label": "黄色预警", "color": "#e3b341",
               "description": "风险中等，建议关注"},
    "green": {"threshold": 0, "label": "正常", "color": "#3fb950",
              "description": "风险较低"},
}

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw")
_ALERTS_FILE = os.path.join(_DATA_DIR, "alerts.json")
os.makedirs(_DATA_DIR, exist_ok=True)


class AlertMonitor:
    """动态预警监测器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._alerts = self._load_alerts()

    def check_risk_score(self, risk_score: float, details: Dict = None) -> Optional[Dict]:
        """
        检查风险分是否触发预警

        :param risk_score: 0-100 风险分
        :param details: 指标详情 (可选)
        :return: 预警字典 (若触发) 或 None
        """
        level = self._score_to_level(risk_score)
        level_info = ALERT_LEVELS[level]

        if level == "green":
            return None  # 无预警

        alert = {
            "id": f"alert_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "level": level,
            "label": level_info["label"],
            "color": level_info["color"],
            "risk_score": round(risk_score, 2),
            "description": level_info["description"],
            "details": details or {},
            "triggered_at": datetime.now().isoformat(),
            "acknowledged": False,
        }

        # 存储预警记录
        self._save_alert(alert)

        logger.info(f"[Alert] 触发 {level_info['label']}: 风险分={risk_score:.1f}")
        return alert

    def get_current_status(self) -> Dict:
        """
        获取当前预警状态

        :return: {
            "level": "red"|"orange"|"yellow"|"green",
            "label": str,
            "color": str,
            "risk_score": float,
            "active_alerts": int,
            "latest_alert": Dict|None
        }
        """
        recent_alerts = self._get_recent_alerts(hours=24)

        if recent_alerts:
            latest = recent_alerts[0]
            level = latest["level"]
        else:
            level = "green"
            latest = None

        level_info = ALERT_LEVELS[level]

        return {
            "level": level,
            "label": level_info["label"],
            "color": level_info["color"],
            "description": level_info["description"],
            "risk_score": latest["risk_score"] if latest else 0,
            "active_alerts": len(recent_alerts),
            "latest_alert": latest,
        }

    def get_alert_history(self, limit: int = 20) -> List[Dict]:
        """获取预警历史记录"""
        with self._lock:
            return list(reversed(self._alerts[-limit:]))

    def get_threshold_lines(self) -> List[Dict]:
        """
        获取阈值参考线数据 (用于 ECharts markLine)

        :return: [{"yAxis": 80, "name": "红色预警", "color": "#f85149"}, ...]
        """
        return [
            {"yAxis": info["threshold"], "name": info["label"], "color": info["color"]}
            for name, info in ALERT_LEVELS.items()
            if name != "green"
        ]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """确认预警"""
        with self._lock:
            for alert in self._alerts:
                if alert.get("id") == alert_id:
                    alert["acknowledged"] = True
                    alert["acknowledged_at"] = datetime.now().isoformat()
                    self._save_all_alerts()
                    return True
        return False

    # ============================================================
    # 内部方法
    # ============================================================

    @staticmethod
    def _score_to_level(score: float) -> str:
        if score >= 80:
            return "red"
        elif score >= 60:
            return "orange"
        elif score >= 40:
            return "yellow"
        return "green"

    def _get_recent_alerts(self, hours: int = 24) -> List[Dict]:
        """获取最近N小时的预警"""
        cutoff = datetime.now().timestamp() - hours * 3600
        recent = []
        with self._lock:
            for alert in reversed(self._alerts):
                try:
                    ts = datetime.fromisoformat(alert["triggered_at"]).timestamp()
                    if ts >= cutoff:
                        recent.append(alert)
                except Exception:
                    continue
        return recent

    def _save_alert(self, alert: Dict):
        """保存单条预警"""
        with self._lock:
            self._alerts.append(alert)
            # 只保留最近100条
            if len(self._alerts) > 100:
                self._alerts = self._alerts[-100:]
            self._save_all_alerts()

    def _save_all_alerts(self):
        """保存所有预警到文件"""
        try:
            with open(_ALERTS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._alerts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Alert] 保存预警历史失败: {e}")

    def _load_alerts(self) -> List[Dict]:
        """加载预警历史"""
        try:
            if os.path.exists(_ALERTS_FILE):
                with open(_ALERTS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[Alert] 加载预警历史失败: {e}")
        return []


# ============================================================
# 单例
# ============================================================
_instance = None
_lock = threading.Lock()


def get_alert_monitor() -> AlertMonitor:
    """获取全局预警监测器单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = AlertMonitor()
    return _instance
