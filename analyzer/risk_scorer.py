"""
analyzer.risk_scorer - 多指标加权风险评分模块
基于多个维度（冲突频次、情感均值、灯光变化、难民变化等）计算综合风险分

支持功能：
  - Min-Max 归一化（历史95分位数作为max）
  - 加权求和 → 0-100 分制
  - 日/周风险分计算
  - 线性插值填充缺失值
"""
import logging
import threading
from typing import Dict, List
import numpy as np
from utils.config import get_risk_weights, load_config

logger = logging.getLogger(__name__)

# 默认权重（当 config.yaml 未配置时使用）
DEFAULT_WEIGHTS = {
    "conflict_frequency": 0.30,
    "sentiment_avg": 0.25,
    "nightlight_change": 0.20,
    "refugee_change": 0.15,
    "event_severity": 0.10,
}

# 占位指标：尚未接入真实数据源的指标键名
# 当这些指标值为 0 时，其权重将被重新分配给有效指标
PLACEHOLDER_INDICATORS = {"nightlight_change", "refugee_change"}


class RiskScorer:
    """多指标加权风险评分器"""

    def __init__(self, weights: Dict[str, float] = None):
        raw_weights = weights or get_risk_weights()
        self._weights = raw_weights if raw_weights else DEFAULT_WEIGHTS
        self._validate_weights()

    def _validate_weights(self):
        """校验权重之和是否为 1.0（允许浮点误差）"""
        if not self._weights:
            logger.warning("[RiskScorer] 权重为空，使用默认权重")
            self._weights = DEFAULT_WEIGHTS
            return
        total = sum(self._weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(f"[RiskScorer] 权重之和为 {total:.4f}，不等于 1.0")

    # ============================================================
    # 归一化
    # ============================================================

    def normalize_indicators(self, raw_values: Dict[str, float],
                              historical_data: Dict[str, List[float]] = None) -> Dict[str, float]:
        """
        Min-Max 归一化指标值到 [0, 1]
        使用历史数据的 95 分位数作为 max，避免极端值影响

        :param raw_values: 原始指标值
        :param historical_data: 各指标的历史值序列（用于计算95分位数）
        :return: 归一化后的指标字典
        """
        normalized = {}

        for key, value in raw_values.items():
            if historical_data and key in historical_data and len(historical_data[key]) >= 5:
                # 使用历史95分位数作为max，5分位数作为min
                hist = np.array(historical_data[key], dtype=float)
                p95 = float(np.percentile(hist, 95))
                p5 = float(np.percentile(hist, 5))

                if p95 - p5 < 1e-6:  # 避免除零
                    normalized[key] = 0.5
                else:
                    norm_val = (value - p5) / (p95 - p5)
                    normalized[key] = max(0.0, min(1.0, norm_val))
            else:
                # 无历史数据时，假设值已在 [0, 1] 范围内
                normalized[key] = max(0.0, min(1.0, value))

        return {k: round(v, 4) for k, v in normalized.items()}

    # ============================================================
    # 核心评分
    # ============================================================

    def calculate_risk_score(self, indicators: Dict[str, float]) -> Dict:
        """
        计算加权综合风险分（0-100 分制）

        动态权重归一化：
          - 占位指标（nightlight_change / refugee_change）若值为 0，
            其权重按比例重新分配给有效指标，避免评分被"僵尸权重"稀释

        :param indicators: 各指标值（已归一化到 0~1）
        :return: {
            "risk_score": 62.5,      # 0-100
            "risk_level": "中风险",
            "indicator_scores": {...},
            "weights_used": {...},
            "weight_rebalanced": bool  # 是否发生了动态归一化
        }
        """
        # ------ 动态权重归一化 ------
        effective_weights = {}
        skipped_weights = {}
        for key, weight in self._weights.items():
            value = indicators.get(key, 0.0)
            # 占位指标且值为 0 → 跳过（视为无数据源）
            if key in PLACEHOLDER_INDICATORS and value <= 0.0:
                skipped_weights[key] = weight
                continue
            effective_weights[key] = weight

        # 归一化有效权重到 1.0
        rebalanced = False
        total = sum(effective_weights.values())
        if total > 0 and abs(total - 1.0) > 0.001:
            effective_weights = {k: v / total for k, v in effective_weights.items()}
            rebalanced = True
        elif total <= 0:
            # 全部指标都为 0 的极端情况 → 使用原始权重
            effective_weights = dict(self._weights)

        # ------ 加权评分 ------
        score = 0.0
        weighted_details = {}
        for key, weight in effective_weights.items():
            value = indicators.get(key, 0.0)
            value = max(0.0, min(1.0, value))
            contribution = value * weight
            score += contribution
            weighted_details[key] = {
                "value": round(value, 4),
                "weight": round(weight, 4),
                "contribution": round(contribution, 4)
            }

        # 记录被跳过的占位指标
        for key, weight in skipped_weights.items():
            weighted_details[key] = {
                "value": 0.0,
                "weight": 0.0,
                "contribution": 0.0,
                "note": "占位指标（数据源未接入）"
            }

        # 转换为 0-100 分制
        score_100 = round(score * 100, 2)

        # 风险等级
        if score_100 >= 70:
            level = "高风险"
        elif score_100 >= 40:
            level = "中风险"
        else:
            level = "低风险"

        if rebalanced:
            logger.debug(
                f"[RiskScorer] 动态归一化: 跳过 {list(skipped_weights.keys())}, "
                f"有效权重 {list(effective_weights.keys())}"
            )

        return {
            "risk_score": score_100,
            "risk_level": level,
            "indicator_scores": weighted_details,
            "weights_used": {k: round(v, 4) for k, v in effective_weights.items()},
            "weight_rebalanced": rebalanced
        }

    # ============================================================
    # 从原始新闻数据计算日风险分（完整流水线）
    # ============================================================

    def compute_daily_risk(self, daily_news: List[Dict],
                            external_data: Dict = None,
                            historical_data: Dict[str, List[float]] = None) -> Dict:
        """
        从原始新闻数据计算当日综合风险分

        :param daily_news: 当日新闻列表，每条包含 text, sentiment_score 等
        :param external_data: 外部数据（灯光指数、难民数、GDELT 事件指标等）
        :param historical_data: 各指标历史序列（用于归一化）
        :return: 完整的风险评分结果
        """
        if not daily_news:
            return {"risk_score": 0.0, "risk_level": "无数据", "news_count": 0}

        n = len(daily_news)

        # 1. 冲突频次（基于关键词匹配，从 config 读取）
        _kw_cfg = load_config().get("conflict_keywords", {})
        conflict_keywords = _kw_cfg.get("zh", []) + _kw_cfg.get("en", [])
        if not conflict_keywords:
            # 兜底默认值
            conflict_keywords = [
                "冲突", "战斗", "空袭", "武装", "交火", "爆炸", "袭击",
                "制裁", "政变", "难民", "抗议", "暴动",
                "conflict", "attack", "airstrike", "armed", "ceasefire",
                "sanction", "coup", "refugee", "protest", "military",
            ]
        conflict_count = sum(
            1 for item in daily_news
            if any(kw.lower() in (item.get("text", "") or item.get("title", "")).lower()
                   for kw in conflict_keywords)
        )
        text_conflict_freq = conflict_count / max(n, 1)

        # 2. 情感均值（负面程度）
        sentiments = [item.get("sentiment_score", 0.5) for item in daily_news]
        avg_sentiment = sum(sentiments) / len(sentiments)
        sentiment_neg = 1.0 - avg_sentiment

        # 3. 外部数据
        ext = external_data or {}
        nightlight_change = ext.get("nightlight_change", 0.0)
        refugee_change = ext.get("refugee_change", 0.0)

        # 4. GDELT 事件数据融合
        gdelt_available = False
        gdelt_conflict_freq = None
        gdelt_severity = None
        gdelt_tone_risk = None

        if ext.get("gdelt_conflict_frequency") is not None:
            gdelt_available = True
            gdelt_conflict_freq = ext["gdelt_conflict_frequency"]
        if ext.get("gdelt_avg_severity") is not None:
            gdelt_severity = ext["gdelt_avg_severity"]
        if ext.get("gdelt_avg_tone_risk") is not None:
            gdelt_tone_risk = ext["gdelt_avg_tone_risk"]

        # 融合策略：
        # - conflict_frequency: 文本关键词 60% + GDELT 事件码 40%（如有 GDELT）
        # - event_severity: GDELT 事件严重程度（如有），否则用文本关键词估计
        # - sentiment_avg: SnowNLP 情感 + GDELT tone 融合（如有）
        if gdelt_available and gdelt_conflict_freq is not None:
            conflict_freq = 0.6 * text_conflict_freq + 0.4 * gdelt_conflict_freq
        else:
            conflict_freq = text_conflict_freq

        if gdelt_available and gdelt_tone_risk is not None:
            # 融合 SnowNLP 和 GDELT tone
            sentiment_neg = 0.6 * sentiment_neg + 0.4 * gdelt_tone_risk

        if gdelt_available and gdelt_severity is not None:
            event_severity = gdelt_severity
        else:
            event_severity = conflict_freq * 0.8 + sentiment_neg * 0.2

        # 构建原始指标
        raw_indicators = {
            "conflict_frequency": conflict_freq,
            "sentiment_avg": sentiment_neg,
            "nightlight_change": max(0.0, -nightlight_change),  # 负增长→高风险
            "refugee_change": refugee_change,
            "event_severity": event_severity
        }

        # 5. 归一化
        normalized = self.normalize_indicators(raw_indicators, historical_data)

        # 6. 加权计算
        result = self.calculate_risk_score(normalized)
        result["news_count"] = n
        result["raw_indicators"] = {k: round(v, 4) for k, v in raw_indicators.items()}
        result["conflict_count"] = conflict_count
        result["gdelt_used"] = gdelt_available

        return result

    # ============================================================
    # 周/月聚合
    # ============================================================

    def calculate_weekly_score(self, daily_scores: List[float]) -> float:
        """计算周平均风险分"""
        if not daily_scores:
            return 0.0
        return round(sum(daily_scores) / len(daily_scores), 2)

    def fill_missing(self, scores: List[float]) -> List[float]:
        """
        线性插值填充缺失值（前向填充 + 线性插值）

        :param scores: 含缺失值的风险分序列（None 或 0 表示缺失）
        :return: 填充后的序列
        """
        result = list(scores)

        # 前向填充
        for i in range(1, len(result)):
            if result[i] is None or result[i] == 0:
                result[i] = result[i-1] if result[i-1] is not None else 0

        # 线性插值
        arr = np.array(result, dtype=float)
        nans = np.isnan(arr)
        if nans.any() and not nans.all():
            x = np.arange(len(arr))
            arr[nans] = np.interp(x[nans], x[~nans], arr[~nans])

        return [round(float(v), 2) for v in arr]

    def update_weights(self, new_weights: Dict[str, float]):
        """动态更新权重"""
        self._weights = new_weights
        self._validate_weights()


# 模块级单例
_scorer_instance = None
_scorer_lock = threading.Lock()


def get_risk_scorer() -> RiskScorer:
    """获取全局风险评分器单例（线程安全）"""
    global _scorer_instance
    if _scorer_instance is None:
        with _scorer_lock:
            if _scorer_instance is None:
                _scorer_instance = RiskScorer()
    return _scorer_instance
