"""
analyzer.diagnostic - 诊断性分析模块

process.html 第四层要求:
  "诊断性分析: 驱动机制解析、关键影响因素归因"

功能:
  1. 指标贡献度归因: 分解综合风险分, 量化各指标对风险的贡献占比
  2. 驱动因素识别: 识别主导风险的关键因素 (Top drivers)
  3. 变化归因: 对比时间窗口, 解析风险变化的驱动来源
  4. 归因文本生成: 自然语言描述驱动机制

集成: /api/diagnostic 接口, 融入 /api/analyze 响应
"""
import logging
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 指标中文名与影响方向说明
INDICATOR_META = {
    "conflict_frequency": {"name": "冲突频次", "desc": "武装冲突与暴力事件的发生密度"},
    "sentiment_avg": {"name": "舆情负面度", "desc": "媒体报道的负面情感强度"},
    "nightlight_change": {"name": "夜光变化", "desc": "夜间灯光衰减反映的经济活动萎缩"},
    "refugee_change": {"name": "难民变化", "desc": "人口流离失所的规模变化"},
    "event_severity": {"name": "事件严重度", "desc": "地缘事件的烈度与影响范围"},
}


class DiagnosticAnalyzer:
    """诊断性归因分析器"""

    def diagnose(self, risk_result: Dict) -> Dict:
        """
        对单次风险评分结果进行贡献度归因

        :param risk_result: risk_scorer.calculate_risk_score 的输出
            (含 indicator_scores: {key: {value, weight, contribution}})
        :return: {
            "total_score": float,
            "drivers": [{indicator, name, contribution, contribution_pct, value, weight}],
            "primary_driver": str,
            "attribution_text": str,
            "concentration": float  # 驱动集中度 (HHI)
        }
        """
        indicator_scores = risk_result.get("indicator_scores", {})
        total_score = risk_result.get("risk_score", 0.0)

        if not indicator_scores:
            return {
                "total_score": total_score,
                "drivers": [],
                "primary_driver": None,
                "attribution_text": "缺少指标明细，无法归因。",
                "concentration": 0.0,
            }

        # 提取各指标贡献 (contribution 为 0-1 尺度, 转百分比)
        total_contribution = sum(
            d.get("contribution", 0.0) for d in indicator_scores.values()
        )

        drivers = []
        for key, detail in indicator_scores.items():
            contribution = detail.get("contribution", 0.0)
            if detail.get("note"):  # 跳过占位指标
                continue
            pct = (contribution / total_contribution * 100) if total_contribution > 0 else 0
            meta = INDICATOR_META.get(key, {"name": key, "desc": ""})
            drivers.append({
                "indicator": key,
                "name": meta["name"],
                "desc": meta["desc"],
                "value": detail.get("value", 0.0),
                "weight": detail.get("weight", 0.0),
                "contribution": round(contribution, 4),
                "contribution_pct": round(pct, 2),
            })

        # 按贡献降序
        drivers.sort(key=lambda x: x["contribution"], reverse=True)

        primary_driver = drivers[0]["name"] if drivers else None

        # 驱动集中度 (Herfindahl-Hirschman Index, 0-1, 越高越集中)
        concentration = sum((d["contribution_pct"] / 100) ** 2 for d in drivers)

        # 生成归因文本
        attribution_text = self._build_attribution_text(
            total_score, drivers, concentration
        )

        return {
            "total_score": total_score,
            "drivers": drivers,
            "primary_driver": primary_driver,
            "attribution_text": attribution_text,
            "concentration": round(concentration, 4),
        }

    def diagnose_change(self, current: Dict, previous: Dict) -> Dict:
        """
        对比两次评分, 归因风险变化的驱动来源

        :param current: 当前 indicator_scores 或原始指标
        :param previous: 上一期 indicator_scores 或原始指标
        :return: 变化归因结果
        """
        cur_ind = current.get("indicator_scores", current)
        prev_ind = previous.get("indicator_scores", previous)

        cur_score = current.get("risk_score", 0.0)
        prev_score = previous.get("risk_score", 0.0)
        delta = cur_score - prev_score

        # 逐指标变化
        changes = []
        all_keys = set(cur_ind.keys()) | set(prev_ind.keys())
        for key in all_keys:
            cur_c = self._extract_contribution(cur_ind.get(key))
            prev_c = self._extract_contribution(prev_ind.get(key))
            change = cur_c - prev_c
            if abs(change) < 1e-6:
                continue
            meta = INDICATOR_META.get(key, {"name": key})
            changes.append({
                "indicator": key,
                "name": meta["name"],
                "change": round(change * 100, 2),  # 转为分数变化
                "direction": "上升" if change > 0 else "下降",
            })

        changes.sort(key=lambda x: abs(x["change"]), reverse=True)

        # 主导变化因素
        main_change = changes[0] if changes else None
        trend_word = "上升" if delta > 0 else "下降" if delta < 0 else "持平"

        if main_change:
            change_text = (
                f"风险分较上期{trend_word} {abs(delta):.1f} 分，"
                f"主要由「{main_change['name']}」{main_change['direction']}驱动"
                f"（贡献变化 {main_change['change']:+.1f} 分）。"
            )
        else:
            change_text = f"风险分较上期基本{trend_word}，无显著驱动因素变化。"

        return {
            "current_score": cur_score,
            "previous_score": prev_score,
            "delta": round(delta, 2),
            "trend": trend_word,
            "changes": changes,
            "main_change": main_change,
            "change_text": change_text,
        }

    def diagnose_from_history(self, days: int = 14) -> Dict:
        """
        从历史数据自动进行变化归因 (对比最近一半 vs 前一半)

        :param days: 分析窗口天数
        :return: 变化归因结果
        """
        try:
            from analyzer.data_loader import get_data_loader
            loader = get_data_loader()
            history = loader.load_risk_history(days=days)
        except Exception as e:
            return {"error": f"无法加载历史数据: {e}"}

        if len(history) < 4:
            return {"error": "历史数据不足 (需至少4天)", "data_points": len(history)}

        mid = len(history) // 2
        older = history[:mid]
        recent = history[mid:]

        # 聚合各期指标 (使用 details 中的原始指标)
        def _aggregate(records):
            agg = {}
            count = len(records)
            for rec in records:
                details = rec.get("details", {})
                for k, v in details.items():
                    if isinstance(v, (int, float)):
                        agg[k] = agg.get(k, 0.0) + v
            return {k: v / max(count, 1) for k, v in agg.items()}

        older_ind = _aggregate(older)
        recent_ind = _aggregate(recent)

        older_score = sum(r["risk_score"] for r in older) / max(len(older), 1)
        recent_score = sum(r["risk_score"] for r in recent) / max(len(recent), 1)

        # 构建对比 (以原始指标 * 权重估算贡献)
        from analyzer.risk_scorer import get_risk_scorer
        scorer = get_risk_scorer()
        weights = scorer._weights

        def _to_contrib(ind):
            return {k: {"contribution": ind.get(k, 0) * w}
                    for k, w in weights.items()}

        result = self.diagnose_change(
            {"risk_score": recent_score, "indicator_scores": _to_contrib(recent_ind)},
            {"risk_score": older_score, "indicator_scores": _to_contrib(older_ind)},
        )
        result["window_days"] = days
        result["older_period"] = f"{older[0]['date']} ~ {older[-1]['date']}"
        result["recent_period"] = f"{recent[0]['date']} ~ {recent[-1]['date']}"
        return result

    # ============================================================
    # 内部工具
    # ============================================================

    @staticmethod
    def _extract_contribution(detail) -> float:
        """从指标明细中提取贡献值"""
        if isinstance(detail, dict):
            return detail.get("contribution", 0.0)
        if isinstance(detail, (int, float)):
            return float(detail)
        return 0.0

    def _build_attribution_text(self, total_score: float,
                                drivers: List[Dict], concentration: float) -> str:
        """生成自然语言归因描述"""
        if not drivers:
            return "无有效驱动指标。"

        level = "高" if total_score >= 70 else "中" if total_score >= 40 else "低"
        parts = [f"当前综合风险为{level}风险（{total_score:.1f}分）。"]

        # 主导因素
        top = drivers[0]
        parts.append(
            f"最主要驱动因素是「{top['name']}」，贡献了 {top['contribution_pct']:.0f}% 的风险，"
            f"{top['desc']}。"
        )

        # 次要因素
        if len(drivers) >= 2:
            second = drivers[1]
            parts.append(
                f"其次为「{second['name']}」（{second['contribution_pct']:.0f}%）。"
            )

        # 集中度解读
        if concentration > 0.5:
            parts.append("风险来源高度集中于单一因素，建议重点监测。")
        elif concentration > 0.3:
            parts.append("风险由少数几个因素主导。")
        else:
            parts.append("风险来源较为分散，属多因素综合作用。")

        return "".join(parts)


# ============================================================
# 单例
# ============================================================
_instance = None
_lock = threading.Lock()


def get_diagnostic_analyzer() -> DiagnosticAnalyzer:
    """获取全局诊断分析器单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = DiagnosticAnalyzer()
    return _instance
