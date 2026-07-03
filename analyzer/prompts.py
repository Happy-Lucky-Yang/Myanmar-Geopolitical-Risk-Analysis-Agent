"""
analyzer.prompts - 大模型提示词模板库
用于引导大语言模型输出结构化的地缘分析结果
"""

# ============================================================
# 通用分析提示词
# ============================================================

NEWS_ANALYSIS_PROMPT = """你是一位地缘政治分析专家，专注于东南亚及缅甸地区的地缘环境研究。
请对以下缅甸相关新闻进行结构化分析，以JSON格式返回结果。

要求返回的JSON字段：
1. event_type: 事件类型（军事冲突/政治变动/经济制裁/人道危机/外交事件/自然灾害/民族矛盾/基础设施建设/其他）
2. severity: 严重程度（1-5，1=轻微，5=极严重）
3. china_myanmar_impact: 对中缅关系的影响分析（50字以内）
4. risk_warning: 风险提示（50字以内）
5. key_entities: 关键实体列表（国家、组织、人名）
6. key_locations: 关键地名列表
7. summary: 50字以内的中文摘要
8. sentiment: 情感倾向（positive/negative/neutral）

请严格返回JSON格式，不要包含多余文字。

新闻内容：
{text}"""

# ============================================================
# 事件分类提示词
# ============================================================

EVENT_CLASSIFICATION_PROMPT = """请将以下缅甸相关新闻归类到以下事件类型之一：
- 军事冲突：武装交火、空袭、军事行动
- 政治变动：政权更迭、选举、政策变化
- 经济制裁：国际制裁、贸易限制
- 人道危机：难民、饥荒、疫情
- 外交事件：高层互访、合作协议、外交声明
- 自然灾害：地震、洪水、台风
- 民族矛盾：民族武装冲突、种族歧视
- 基础设施建设：中缅经济走廊、管道、港口
- 其他

仅返回事件类型名称，不要多余文字。

新闻内容：
{text}"""

# ============================================================
# 中缅关系影响评估
# ============================================================

CHINA_MYANMAR_IMPACT_PROMPT = """分析以下事件对中缅关系的影响，从以下维度评估：
1. 对中缅经济走廊的影响
2. 对边境安全的影响
3. 对双边外交关系的影响
4. 对中国在缅投资的影响

以JSON格式返回，每个维度给出：
- impact_direction: "正面"/"负面"/"中性"
- impact_score: 1-5（1=影响极小，5=影响极大）
- brief: 20字以内简述

新闻内容：
{text}"""

# ============================================================
# 风险评分辅助提示词
# ============================================================

RISK_ASSESSMENT_PROMPT = """请评估以下缅甸相关新闻的地缘风险程度。

以JSON格式返回：
1. risk_score: 0-100的整数（0=无风险，100=极高风险）
2. risk_factors: 主要风险因素列表
3. time_horizon: 风险时间维度（"即时"/"短期"/"中期"/"长期"）
4. confidence: 评估置信度（"高"/"中"/"低"）

新闻内容：
{text}"""

# ============================================================
# 可解释性链式推理提示词（分步）
# ============================================================

CHAIN_OF_THOUGHT_PROMPTS = [
    "近期缅甸发生了哪些重大事件？请按时间顺序列出。",
    "这些事件对中缅油气管道安全有什么影响？",
    "当前缅甸的地缘风险等级如何？请综合评估。",
    "未来3个月内，缅甸局势可能如何演变？"
]

# ============================================================
# 辅助函数
# ============================================================

def build_analysis_prompt(text: str, custom_instruction: str = None) -> list:
    """
    构建分析提示词消息列表（OpenAI Chat 格式）

    :param text: 新闻文本
    :param custom_instruction: 自定义指令（可选）
    :return: messages 列表
    """
    if custom_instruction:
        system_content = custom_instruction
    else:
        system_content = NEWS_ANALYSIS_PROMPT.format(text="")

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": text}
    ]


def get_prompt_by_name(name: str) -> str:
    """
    通过名称获取提示词模板

    :param name: 提示词名称
    :return: 提示词字符串
    """
    prompts = {
        "news_analysis": NEWS_ANALYSIS_PROMPT,
        "event_classification": EVENT_CLASSIFICATION_PROMPT,
        "china_myanmar_impact": CHINA_MYANMAR_IMPACT_PROMPT,
        "risk_assessment": RISK_ASSESSMENT_PROMPT,
    }
    return prompts.get(name, NEWS_ANALYSIS_PROMPT)
