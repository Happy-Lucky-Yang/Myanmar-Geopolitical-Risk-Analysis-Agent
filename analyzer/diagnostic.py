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


class LegacyDiagnosticAnalyzer:
    """保留旧版用于历史对照；正式入口使用下方贡献分析器。"""

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

    def explain_and_forecast(self, days: int = 14, days_ahead: int = 7) -> Dict:
        """
        风险上升原因详解 + 未来风险预警（诊断增强）

        在 diagnose_from_history 基础上，补充：
          - rise_explanation: 逐指标带具体数值的上升原因详解
          - future_outlook: 基于线性外推的未来风险预警（预测分/预警级别/先行信号）

        :param days: 归因窗口天数
        :param days_ahead: 未来预测天数
        :return: 增强诊断结果
        """
        try:
            from analyzer.data_loader import get_data_loader
            loader = get_data_loader()
            history = loader.load_risk_history(days=days)
        except Exception as e:
            return {"error": f"无法加载历史数据: {e}"}

        if len(history) < 4:
            return {"error": "历史数据不足 (需至少4天)", "data_points": len(history)}

        base = self.diagnose_from_history(days=days)

        mid = len(history) // 2
        older, recent = history[:mid], history[mid:]

        def _agg_raw(records):
            agg, cnt = {}, max(len(records), 1)
            for rec in records:
                for k, v in rec.get("details", {}).items():
                    if isinstance(v, (int, float)):
                        agg[k] = agg.get(k, 0.0) + v
            return {k: v / cnt for k, v in agg.items()}

        older_raw, recent_raw = _agg_raw(older), _agg_raw(recent)

        # ---- 上升原因详解：逐指标带数值 ----
        detail_list = []
        for key in recent_raw:
            meta = INDICATOR_META.get(key, {"name": key, "desc": ""})
            v_old, v_new = older_raw.get(key, 0.0), recent_raw.get(key, 0.0)
            d = v_new - v_old
            if abs(d) < 1e-6:
                continue
            detail_list.append({
                "indicator": key, "name": meta["name"], "desc": meta["desc"],
                "value_older": round(v_old, 3), "value_recent": round(v_new, 3),
                "change": round(d, 3),
                "direction": "上升" if d > 0 else "下降",
            })
        detail_list.sort(key=lambda x: abs(x["change"]), reverse=True)

        rising = [x for x in detail_list if x["change"] > 0]
        is_rising = base.get("delta", 0) > 0

        if is_rising and rising:
            lead = rising[0]
            explain_text = (
                f"风险分上升 {base.get('delta', 0):.1f} 分。"
                f"主因是「{lead['name']}」由 {lead['value_older']} 升至 {lead['value_recent']}"
                f"（{lead['desc']}）。"
            )
            if len(rising) >= 2:
                explain_text += f"同时「{rising[1]['name']}」亦上升，形成叠加推动。"
        elif is_rising:
            explain_text = f"风险分上升 {base.get('delta', 0):.1f} 分，但各指标变化均不显著，属综合波动。"
        else:
            explain_text = f"风险分未上升（变化 {base.get('delta', 0):+.1f} 分），无需上升归因。"

        # ---- 未来风险预警：线性外推 ----
        try:
            from analyzer.trend import get_trend_analyzer
            scores = [r["risk_score"] for r in history]
            fc = get_trend_analyzer().forecast(scores, days_ahead=days_ahead)
            predicted = fc["forecast"][-1] if fc.get("forecast") else scores[-1]
        except Exception:
            predicted = history[-1]["risk_score"]
            fc = {"slope": 0.0, "confidence": "低"}

        level = self._score_to_level(predicted)
        leading = [f"{x['name']}持续{x['direction']}" for x in detail_list[:3]]

        outlook_text = (
            f"按当前趋势外推，{days_ahead} 天后风险分约 {predicted:.1f} 分，"
            f"对应「{level['label']}」。"
        )
        if level["threshold"] >= 60:
            outlook_text += f"{level['description']}，建议提前部署监测与预案。"
        elif fc.get("slope", 0) > 0:
            outlook_text += "风险呈上行趋势，建议关注先行指标变化。"
        else:
            outlook_text += "风险总体可控，维持常规监测。"

        base["rise_explanation"] = {
            "is_rising": is_rising,
            "detail": detail_list,
            "text": explain_text,
        }
        base["future_outlook"] = {
            "predicted_score": round(predicted, 2),
            "days_ahead": days_ahead,
            "projected_level": level["level"],
            "projected_label": level["label"],
            "slope": fc.get("slope", 0.0),
            "confidence": fc.get("confidence", "低"),
            "leading_signals": leading,
            "text": outlook_text,
        }
        return base

    @staticmethod
    def _score_to_level(score: float) -> Dict:
        """风险分 → 预警级别（与 alert_monitor 阈值一致）"""
        if score >= 80:
            return {"level": "red", "label": "红色预警", "threshold": 80,
                    "description": "风险极高，需立即关注"}
        if score >= 60:
            return {"level": "orange", "label": "橙色预警", "threshold": 60,
                    "description": "风险较高，需密切关注"}
        if score >= 40:
            return {"level": "yellow", "label": "黄色预警", "threshold": 40,
                    "description": "风险中等，建议关注"}
        return {"level": "green", "label": "正常", "threshold": 0,
                "description": "风险较低"}

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


class DiagnosticAnalyzer:
    """只分解已保存的实际贡献，不用当前权重反推历史或作因果解释。"""

    @staticmethod
    def _contributions(record):
        from utils.data_contract import finite_number
        nested = record.get('details')
        nested = nested if isinstance(nested, dict) else {}
        details = record.get('indicator_scores', nested.get('indicator_scores', {}))
        details = details if isinstance(details, dict) else {}
        return {k: v for k, v in details.items() if isinstance(v, dict)
                and finite_number(v.get('contribution')) and v['contribution'] >= 0
                and v.get('quality', 'derived') in {'observed', 'derived'}}

    def diagnose(self, risk_result):
        from utils.data_contract import finite_number
        details = self._contributions(risk_result)
        score = risk_result.get('risk_score')
        total = sum(v['contribution'] for v in details.values())
        drivers = []
        for key, value in details.items():
            drivers.append({'indicator': key, 'name': INDICATOR_META.get(key, {}).get('name', key),
                'value': value.get('value'), 'weight': value.get('weight'),
                'contribution': value['contribution'], 'contribution_points': round(value['contribution'] * 100, 4),
                'contribution_pct': round(value['contribution'] / total * 100, 2) if total else 0})
        drivers.sort(key=lambda v: (-v['contribution'], v['indicator']))
        text = ('按实际有效权重分解风险分，贡献不代表因果作用。' if drivers
                else '缺少有效贡献明细，不能用当前权重反推历史。')
        if drivers and total == 0:
            text += '有效指标贡献均为零，不指定主导因素。'
        return {'total_score': score if finite_number(score) else None, 'drivers': drivers,
                'primary_driver': drivers[0]['name'] if drivers and total else None,
                'attribution_text': text, 'contribution_text': text,
                'concentration': sum((r['contribution_pct'] / 100) ** 2 for r in drivers) if total else None,
                'algorithm_version': 'contribution-v2'}

    def diagnose_change(self, current, previous):
        from utils.data_contract import finite_number
        cur, prev = self._contributions(current), self._contributions(previous)
        current_score, previous_score = current.get('risk_score'), previous.get('risk_score')
        delta = current_score - previous_score if all(map(finite_number, [current_score, previous_score])) else None
        changes = []
        for key in sorted(cur.keys() & prev.keys()):
            change = 100 * (cur[key]['contribution'] - prev[key]['contribution'])
            changes.append({'indicator': key, 'name': INDICATOR_META.get(key, {}).get('name', key),
                            'change': round(change, 4), 'direction': '上升' if change > 0 else '下降' if change < 0 else '持平'})
        changes.sort(key=lambda v: (-abs(v['change']), v['indicator']))
        unavailable = sorted(cur.keys() ^ prev.keys())
        text = '比较等长业务日窗口内的实际贡献均值；贡献变化包含指标值和有效权重变化，不作因果归因。'
        if unavailable:
            text += '部分指标覆盖不同，贡献差不可完整解释总分变化。'
        residual = delta - sum(v['change'] for v in changes) if delta is not None else None
        return {'current_score': current_score, 'previous_score': previous_score,
                'delta': round(delta, 4) if delta is not None else None,
                'trend': '数据不足' if delta is None else '上升' if delta > 0 else '下降' if delta < 0 else '持平',
                'changes': changes, 'main_change': changes[0] if changes and changes[0]['change'] else None,
                'uncomparable_indicators': unavailable, 'unexplained_delta': round(residual, 4) if residual is not None else None,
                'change_text': text, 'algorithm_version': 'contribution-v2'}

    def diagnose_from_history(self, days=14, end_date=None, region='MMR', algorithm_version='risk-v2', records=None):
        from datetime import timedelta
        from utils.data_contract import business_date, time_window, select_history, coverage_metadata
        if type(days) is not int or not 4 <= days <= 3660:
            raise ValueError('贡献比较窗口须为4～3660天')
        period = days // 2
        effective_days = period * 2
        start, end = time_window(effective_days, end_date)
        split = start + timedelta(days=period)
        if records is None:
            from analyzer.data_loader import get_data_loader
            records = get_data_loader().load_risk_history(days=effective_days, end_date=end_date,
                                                          region=region, algorithm_version=algorithm_version)
        history = select_history(records, effective_days, end_date, algorithm_version=algorithm_version, region=region)
        older = [r for r in history if business_date(r['date']) < split]
        recent = [r for r in history if business_date(r['date']) >= split]
        metadata = {'window_days': effective_days, 'requested_days': days, 'period_days': period,
                    'older_period': f'{start} ~ {split - timedelta(days=1)}',
                    'recent_period': f'{split} ~ {end - timedelta(days=1)}',
                    'older_coverage': len(older) / period, 'recent_coverage': len(recent) / period,
                    'metadata': coverage_metadata(history, effective_days, end_date, region=region, algorithm_version=algorithm_version)}
        if len(older) < 2 or len(recent) < 2 or min(len(older), len(recent)) / period < .8:
            return {**metadata, 'error': '两期各需至少2个有效日且覆盖率≥80%，不补零比较', 'delta': None, 'changes': []}

        def aggregate(rows):
            contributions = [self._contributions(row) for row in rows]
            keys = set.intersection(*(set(c) for c in contributions))
            return {'risk_score': sum(r['risk_score'] for r in rows) / len(rows),
                    'indicator_scores': {k: {'contribution': sum(c[k]['contribution'] for c in contributions) / len(rows)} for k in keys}}

        return {**self.diagnose_change(aggregate(recent), aggregate(older)), **metadata}

    def explain_and_forecast(self, days=14, days_ahead=7, end_date=None, region='MMR', algorithm_version='risk-v2', records=None):
        from utils.data_contract import calendar_series, select_history
        from analyzer.trend import get_trend_analyzer
        if records is None:
            from analyzer.data_loader import get_data_loader
            records = get_data_loader().load_risk_history(days=days, end_date=end_date,
                                                          region=region, algorithm_version=algorithm_version)
        base = self.diagnose_from_history(days, end_date, region, algorithm_version, records)
        selected = select_history(records, days, end_date, algorithm_version=algorithm_version, region=region)
        dates, scores = calendar_series(selected, days, end_date)
        fc = get_trend_analyzer().forecast(scores, days_ahead=days_ahead, dates=dates)
        predicted = fc['forecast'][-1] if fc.get('forecast') else None
        base['future_outlook'] = {'predicted_score': predicted, 'days_ahead': days_ahead,
            'projected_level': 'unknown', 'projected_label': '探索性外推，非正式预警' if predicted is not None else '样本不足',
            'status': fc['status'], 'slope': fc.get('slope'), 'model': fc.get('model'),
            'backtest': fc.get('backtest'), 'interval': fc.get('interval'),
            'reliability': fc['confidence'], 'text': fc.get('reason', '')}
        base['rise_explanation'] = {'is_rising': base.get('delta') is not None and base['delta'] > 0,
                                   'detail': [], 'text': base.get('change_text', base.get('error'))}
        return base


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
