"""
analyzer.chain_reasoner - 链式推理模拟（多轮 LLM 问答串联）

申报书原文: "多轮API问答串联分析步骤，模拟分步逻辑推理"

实现:
  - 预定义推理链模板 (事件识别 → 影响分析 → 趋势研判 → 建议生成)
  - 每步调用 LLM，将前序结果注入下一步 prompt
  - 输出: reasoning_chain 数组

集成:
  /api/analyze 新增可选参数 chain_depth (默认1=单步，2-4=链式)
"""
import logging
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 推理链步骤模板
# ============================================================
CHAIN_TEMPLATES = {
    "event_identification": {
        "step": 1,
        "name": "事件识别与分类",
        "system_prompt": (
            "你是一名地缘政治分析专家。请对以下新闻文本进行事件识别与分类。\n"
            "输出 JSON 格式:\n"
            '{"event_type": "事件类型(军事冲突/政治变动/经济制裁/外交事件/人道主义危机/自然灾害)",\n'
            ' "key_actors": ["关键行为体列表"],\n'
            ' "location": "事件地点",\n'
            ' "timeframe": "时间范围",\n'
            ' "severity": "严重程度(1-5)",\n'
            ' "summary": "一句话摘要"}\n'
            "只输出 JSON，不要附加说明。"
        )
    },
    "impact_analysis": {
        "step": 2,
        "name": "影响分析",
        "system_prompt": (
            "你是一名地缘政治分析专家。基于以下事件信息，分析其对中缅关系和区域稳定的影响。\n\n"
            "## 事件信息:\n{previous_result}\n\n"
            "输出 JSON 格式:\n"
            '{"china_myanmar_impact": "对中缅关系的影响",\n'
            ' "regional_stability": "对区域稳定的影响",\n'
            ' "economic_impact": "经济影响",\n'
            ' "affected_infrastructure": ["受影响的基础设施/项目"],\n'
            ' "risk_factors": ["主要风险因素列表"]}\n'
            "只输出 JSON，不要附加说明。"
        )
    },
    "trend_assessment": {
        "step": 3,
        "name": "趋势研判",
        "system_prompt": (
            "你是一名地缘政治趋势分析专家。基于以下事件分析和影响评估，研判未来发展趋势。\n\n"
            "## 事件: {event_info}\n"
            "## 影响分析: {impact_info}\n\n"
            "输出 JSON 格式:\n"
            '{"short_term_trend": "短期趋势(1-3个月)",\n'
            ' "medium_term_trend": "中期趋势(3-12个月)",\n'
            ' "escalation_probability": "升级概率(低/中/高)",\n'
            ' "key_indicators_to_watch": ["需关注的先行指标"],\n'
            ' "scenario_best": "最佳情景",\n'
            ' "scenario_worst": "最坏情景"}\n'
            "只输出 JSON，不要附加说明。"
        )
    },
    "recommendation": {
        "step": 4,
        "name": "建议生成",
        "system_prompt": (
            "你是一名地缘政策顾问。基于以下完整分析链，为中方相关决策提供建议。\n\n"
            "## 事件: {event_info}\n"
            "## 影响: {impact_info}\n"
            "## 趋势: {trend_info}\n\n"
            "输出 JSON 格式:\n"
            '{"policy_recommendations": ["政策建议列表(3-5条)"],\n'
            ' "risk_mitigation": ["风险缓解措施"],\n'
            ' "early_warning_signals": ["早期预警信号"],\n'
            ' "confidence_level": "分析置信度(高/中/低)",\n'
            ' "data_gaps": ["数据缺口/需要进一步调查的领域"]}\n'
            "只输出 JSON，不要附加说明。"
        )
    }
}

# 推理链顺序
CHAIN_ORDER = ["event_identification", "impact_analysis", "trend_assessment", "recommendation"]


class ChainReasoner:
    """链式推理引擎"""

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from analyzer.llm_client import get_llm_client
            self._llm = get_llm_client()
        return self._llm

    def run_chain(self, text: str, depth: int = 2) -> Dict:
        """
        执行链式推理

        :param text: 原始新闻文本
        :param depth: 推理深度 (1=仅事件识别, 2=+影响分析, 3=+趋势研判, 4=+建议)
        :return: {"chain": [...], "final_summary": {...}}
        """
        depth = max(1, min(4, depth))
        chain = []
        previous_results = {}

        steps_to_run = CHAIN_ORDER[:depth]

        for step_key in steps_to_run:
            template = CHAIN_TEMPLATES[step_key]

            # 构建 prompt (注入前序结果)
            prompt = self._build_step_prompt(step_key, text, previous_results)

            # 调用 LLM
            try:
                llm = self._get_llm()
                # 使用自定义 prompt
                messages = [
                    {"role": "system", "content": template["system_prompt"]},
                    {"role": "user", "content": prompt}
                ]
                result = llm._call_with_retry(messages)

                chain.append({
                    "step": template["step"],
                    "name": template["name"],
                    "question": self._get_step_question(step_key),
                    "answer": result,
                    "confidence": self._estimate_confidence(result),
                })

                previous_results[step_key] = result

            except Exception as e:
                logger.error(f"[Chain] 步骤 {step_key} 失败: {e}")
                chain.append({
                    "step": template["step"],
                    "name": template["name"],
                    "question": self._get_step_question(step_key),
                    "answer": {"error": str(e)},
                    "confidence": 0,
                })
                break  # 链式推理中断后停止

        # 构建最终摘要
        final_summary = self._build_final_summary(chain, previous_results)

        return {
            "chain_depth": depth,
            "chain": chain,
            "steps_completed": len(chain),
            "final_summary": final_summary,
        }

    def _build_step_prompt(self, step_key: str, text: str, previous: Dict) -> str:
        """构建当前步骤的 prompt"""
        if step_key == "event_identification":
            return text

        if step_key == "impact_analysis":
            event_info = self._format_result(previous.get("event_identification", {}))
            return CHAIN_TEMPLATES[step_key]["system_prompt"].format(
                previous_result=event_info
            ).replace(CHAIN_TEMPLATES[step_key]["system_prompt"].split("\n")[0] + "\n", "") + \
                f"\n\n原始新闻:\n{text[:2000]}"

        if step_key == "trend_assessment":
            event_info = self._format_result(previous.get("event_identification", {}))
            impact_info = self._format_result(previous.get("impact_analysis", {}))
            return f"事件信息:\n{event_info}\n\n影响分析:\n{impact_info}"

        if step_key == "recommendation":
            event_info = self._format_result(previous.get("event_identification", {}))
            impact_info = self._format_result(previous.get("impact_analysis", {}))
            trend_info = self._format_result(previous.get("trend_assessment", {}))
            return (f"事件:\n{event_info}\n\n影响:\n{impact_info}\n\n"
                    f"趋势:\n{trend_info}")

        return text

    def _format_result(self, result: Dict) -> str:
        """格式化结果为可读文本"""
        import json
        try:
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception:
            return str(result)

    def _get_step_question(self, step_key: str) -> str:
        """获取步骤对应的分析目标描述"""
        questions = {
            "event_identification": "该事件属于什么类型？关键行为体有哪些？",
            "impact_analysis": "该事件对中缅关系和区域稳定有何影响？",
            "trend_assessment": "未来局势可能如何发展？",
            "recommendation": "基于分析，应采取哪些应对措施？",
        }
        return questions.get(step_key, "")

    def _estimate_confidence(self, result: Dict) -> float:
        """基于结果完整度估计置信度 (0-1)"""
        if not result or "error" in result:
            return 0.0

        # 基于返回字段数量估算
        expected_fields = 4
        actual = len([k for k in result.keys() if k not in ("error", "raw_response")])
        return min(1.0, actual / expected_fields * 0.8 + 0.2)

    def _build_final_summary(self, chain: List, previous: Dict) -> Dict:
        """构建最终摘要"""
        summary = {
            "event_type": "未知",
            "key_actors": [],
            "risk_level": "未知",
            "recommendations": [],
        }

        # 从各步骤提取关键信息
        event = previous.get("event_identification", {})
        if event:
            summary["event_type"] = event.get("event_type", "未知")
            summary["key_actors"] = event.get("key_actors", [])

        impact = previous.get("impact_analysis", {})
        if impact:
            severity = event.get("severity", 3)
            summary["risk_level"] = "高" if severity >= 4 else "中" if severity >= 2 else "低"

        rec = previous.get("recommendation", {})
        if rec:
            summary["recommendations"] = rec.get("policy_recommendations", [])

        return summary


# ============================================================
# 单例
# ============================================================
_instance = None
_lock = threading.Lock()


def get_chain_reasoner() -> ChainReasoner:
    """获取全局链式推理引擎单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ChainReasoner()
    return _instance
