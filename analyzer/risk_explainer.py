"""风险成因解读：只读正式日指标，调用大模型解释"分数为什么这么高"并给条件化建议。

与工作区 AI 研判（异步队列、需 PostgreSQL + worker）不同，这里是同步轻量解读：
- 证据只来自当天正式风险记录：分数、指标贡献、覆盖率、样本数、来源立场构成
- 模型默认 GLM-4-Flash（免费），可切换 DeepSeek；不可用时按规则给出解读，不阻塞页面
- 程序校验：只允许引用证据中的数值，禁止因果断言与链接；校验不过重试一次，再不过降级
"""
import json
import logging
import re
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'glm-4-flash'
ALLOWED_MODELS = {'glm-4-flash', 'glm-4-plus', 'glm-4-long', 'deepseek-chat', 'deepseek-reasoner'}
PROMPT_VERSION = 'risk-explain-v1'
CACHE_TTL = 600
_RATE_LIMIT = 6          # 每窗口最多调用次数
_RATE_WINDOW = 60.0

INDICATOR_LABELS = {
    'conflict_frequency': '冲突频次',
    'sentiment_avg': '情感负面度',
    'nightlight_change': '夜光变化',
    'refugee_change': '难民变化',
    'event_severity': '事件严重度',
    'china_myanmar_relation': '中缅关系',
    'neighbor_relation': '周边邻国关系',
    'western_relation': '西方关系',
    'china_us_rivalry': '中美博弈',
    'displacement': '流离失所',
    'intl_sanction': '国际制裁',
}

SYSTEM_PROMPT = '''你是缅甸地缘风险研究助手。用户会给你一天的正式风险记录，包含总分、各指标贡献、样本数与来源构成。
你的任务是解释"风险分主要由什么构成"，并给出条件化建议。

必须遵守：
1. 只能引用输入中出现的数值，不得创造新数字、百分比或日期。
2. 指标贡献是加权分解，不是因果证明；禁止使用"导致/造成/引起"等因果断言。
3. 建议必须条件化，区分出行、贸易与研究场景；禁止军事部署类指令。
4. 覆盖率低或样本少时必须明确指出数据不足，不得假装确定。
5. 严格区分两类情况：quality=missing 才是“数据缺失”；value=0 且 quality 为 derived/observed
   是“观测到零”（例如本期报道未命中冲突关键词），**绝不得把 value=0 说成缺失**，
   也不得把它列入 data_gaps。可参照输入中的 missing_indicators 与 zero_observed_indicators 两个列表。
6. 不得输出URL、文件路径或系统配置。
7. source_stances 是来源立场构成（domestic=境内中文, independent=独立, ethnic=民族,
   aggregator=聚合/事件库, international=国际组织, unlabeled=未标注）；若来源单一
   （只有一个立场类别占绝大多数），在 data_gaps 里提示“来源单一，未多源互证”。
8. 输出JSON，键为 summary, top_drivers, explanations, suggestions, data_gaps, confidence。
   - summary: 不超过150字
   - top_drivers: 最多3个对象，每个仅含 indicator（指标中文名）与 points（贡献分，必须来自输入）
   - explanations: 最多3条，每条不超过100字
   - suggestions: 最多3条，每条不超过100字
   - data_gaps: 最多3条
   - confidence: "高"/"中"/"低"之一'''

_lock = threading.Lock()
_cache: Dict[tuple, tuple] = {}
_calls = []


def _check_rate() -> bool:
    now = time.time()
    with _lock:
        while _calls and now - _calls[0] > _RATE_WINDOW:
            _calls.pop(0)
        if len(_calls) >= _RATE_LIMIT:
            return False
        _calls.append(now)
        return True


def latest_record(date: Optional[str] = None) -> Optional[Dict]:
    """取指定日或最新一天的正式风险记录。"""
    from analyzer.data_loader import get_data_loader
    records = get_data_loader().load_risk_history(days=400)
    rows = [r for r in records if isinstance(r, dict) and r.get('risk_score') is not None]
    if date:
        rows = [r for r in rows if str(r.get('date')) == str(date)]
    if not rows:
        return None
    return sorted(rows, key=lambda r: str(r.get('date')))[-1]


def build_evidence(record: Dict) -> Dict:
    """把日风险记录压缩成可供模型引用、且便于程序校验的证据。"""
    details = record.get('details') or {}
    scores = details.get('indicator_scores') or {}
    indicators = []
    for key, item in scores.items():
        if not isinstance(item, dict):
            continue
        indicators.append({
            'key': key,
            'indicator': INDICATOR_LABELS.get(key, key),
            'value': item.get('value'),
            'weight': item.get('weight'),
            'points': item.get('contribution_points'),
            'quality': item.get('quality'),
        })
    indicators.sort(key=lambda x: -(x['points'] or 0))
    # 显式区分“观测到零”与“缺测”：实测发现模型会把 value=0 误说成“数据缺失”
    for item in indicators:
        if item['quality'] == 'missing' or item['value'] is None:
            item['status'] = 'missing'
        elif item['value'] == 0:
            item['status'] = 'zero_observed'
        else:
            item['status'] = 'valued'
    missing = [i['indicator'] for i in indicators if i['status'] == 'missing']
    zeros = [i['indicator'] for i in indicators if i['status'] == 'zero_observed']
    # pipeline 以整体 risk 作 details 保存，立场/来源/异常标记可能只在 details 里
    return {
        'date': record.get('date'),
        'risk_score': record.get('risk_score'),
        'risk_level': record.get('risk_level') or details.get('risk_level'),
        'news_count': record.get('news_count') or record.get('sample_count') or details.get('news_count'),
        'indicator_coverage': details.get('indicator_coverage'),
        'algorithm_version': record.get('algorithm_version') or details.get('algorithm_version'),
        'anomaly_flag': record.get('anomaly_flag') or details.get('anomaly_flag'),
        'anomaly_reason': record.get('anomaly_reason') or details.get('anomaly_reason'),
        'indicators': indicators,
        'missing_indicators': missing,
        'zero_observed_indicators': zeros,
        'status_note': 'missing=缺测（无观测）；zero_observed=观测到零（有数据且值为0）；valued=有非零观测。两者不得混用。',
        'source_stances': record.get('source_stances') or details.get('source_stances') or {},
        'sources': record.get('sources') or details.get('sources') or [],
        'note': 'points 为加权贡献分；贡献不等于因果',
    }


def _stance_from_news(date: Optional[str]) -> Dict[str, int]:
    """旧日指标未存立场构成时，按当天正式且去重的新闻实时统计（只服务于展示）。"""
    try:
        from analyzer.data_loader import get_data_loader
        from utils.data_contract import FORMAL_MODES, article_identity, business_date
        from utils.source_meta import annotate_stance, stance_counts
        day = business_date(date)
        seen, unique = set(), []
        for n in get_data_loader().load_raw_news():
            if not isinstance(n, dict) or n.get('run_kind') not in FORMAL_MODES:
                continue
            if business_date(n.get('pub_time')) != day:
                continue
            key = article_identity(n)
            if key in seen:
                continue
            seen.add(key)
            unique.append(n)
        return stance_counts(annotate_stance(unique))
    except Exception:
        return {}


def rule_based(evidence: Dict) -> Dict:
    """大模型不可用时的规则解读：解释贡献最高的指标，不编造。"""
    drivers = [i for i in evidence['indicators'] if i.get('points')][:3]
    missing = [i['indicator'] for i in evidence['indicators'] if i.get('quality') == 'missing']
    coverage = evidence.get('indicator_coverage')
    parts = [f"{evidence['date']} 风险分 {evidence['risk_score']}（{evidence.get('risk_level')}），样本 {evidence.get('news_count')} 条"]
    if drivers:
        top = drivers[0]
        parts.append(f"贡献最高的是{top['indicator']}（{top['points']} 分，权重 {top.get('weight')}）")
    if coverage is not None:
        parts.append(f"指标覆盖率 {coverage}")
    return {
        'summary': '；'.join(parts) + '。',
        'top_drivers': [{'indicator': d['indicator'], 'points': d['points']} for d in drivers],
        'explanations': ['规则解读：按加权贡献排序，仅描述构成，不代表因果。'],
        'suggestions': ['建议人工复核贡献最高的指标及其原始新闻证据。'],
        'data_gaps': [f"{name} 缺测" for name in missing[:3]] or ([] if coverage else ['覆盖率未知']),
        'confidence': '低',
    }


def _numbers(text: str):
    return set(re.findall(r'\d+(?:\.\d+)?', text or ''))


def validate(answer: Dict, evidence: Dict) -> Dict:
    """结构 + 引用值校验：数字必须来自证据，禁止因果词与链接。"""
    if not isinstance(answer, dict):
        raise ValueError('回答结构无效')
    required = {'summary', 'top_drivers', 'explanations', 'suggestions', 'data_gaps', 'confidence'}
    if not required.issubset(answer):
        raise ValueError('缺少必要字段')
    allowed_numbers = _numbers(json.dumps(evidence, ensure_ascii=False))
    texts = [answer['summary']] + list(answer['explanations']) + list(answer['suggestions']) + list(answer['data_gaps'])
    texts += [str(d.get('indicator', '')) + ' ' + str(d.get('points', '')) for d in answer['top_drivers']]
    for text in texts:
        text = str(text or '')
        if re.search(r'https?://|file:|[A-Za-z]:\\', text):
            raise ValueError('回答包含链接或路径')
        if re.search(r'导致|造成|引起', text):
            raise ValueError('不得宣称因果')
        if _numbers(text) - allowed_numbers:
            raise ValueError('回答含证据外的数值')
    if not isinstance(answer['top_drivers'], list) or len(answer['top_drivers']) > 3:
        raise ValueError('top_drivers 无效')
    for driver in answer['top_drivers']:
        if not isinstance(driver, dict) or 'indicator' not in driver or 'points' not in driver:
            raise ValueError('top_drivers 条目无效')
    if answer['confidence'] not in {'高', '中', '低'}:
        raise ValueError('confidence 无效')
    # 置信度硬性约束：覆盖不足或样本太少时不得自称“高”
    confidence = answer['confidence']
    coverage = evidence.get('indicator_coverage')
    if confidence == '高' and (coverage is None or coverage < 0.5):
        confidence = '中'
    try:
        if confidence == '高' and int(evidence.get('news_count') or 0) < 5:
            confidence = '中'
    except (TypeError, ValueError):
        pass
    return {
        'summary': str(answer['summary'])[:400],
        'top_drivers': [{'indicator': str(d['indicator'])[:30], 'points': d['points']}
                        for d in answer['top_drivers']],
        'explanations': [str(x)[:200] for x in answer['explanations']][:3],
        'suggestions': [str(x)[:200] for x in answer['suggestions']][:3],
        'data_gaps': [str(x)[:200] for x in answer['data_gaps']][:3],
        'confidence': confidence,
    }


def explain_risk(date: Optional[str] = None, model: Optional[str] = None) -> Dict:
    """同步解读指定日/最新日风险；模型不可用时自动降级为规则解读。"""
    model = model if model in ALLOWED_MODELS else DEFAULT_MODEL
    record = latest_record(date)
    if record is None:
        return {'status': 'no_data', 'reason': '没有可用的正式风险记录', 'date': date, 'model_version': None}
    evidence = build_evidence(record)
    if not evidence['source_stances']:
        # 早期日指标没有该字段（input_hash 去重不会回写），按当天新闻现场补齐
        evidence['source_stances'] = _stance_from_news(evidence['date'])
    cache_key = (str(evidence['date']), record.get('input_hash'), model)
    with _lock:
        cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return {**cached[1], 'cached': True}

    result = None
    if _check_rate():
        try:
            result = _call_model(evidence, model)
        except Exception as exc:
            logger.warning('[RiskExplain] 模型调用失败: %s', type(exc).__name__)
    payload = result or {
        'status': 'degraded',
        'reason': '大模型不可用或调用受限，已按规则解读',
        'answer': rule_based(evidence),
        'model_version': 'rule-based',
    }
    payload.update(date=evidence['date'], algorithm_version=evidence.get('algorithm_version'),
                   prompt_version=PROMPT_VERSION, evidence=evidence,
                   disclaimer='辅助研判，需人工核查；指标贡献不是因果证明。')
    with _lock:
        _cache[cache_key] = (time.time(), payload)
    return payload


def _call_model(evidence: Dict, model: str) -> Optional[Dict]:
    from analyzer.llm_client import get_llm_client
    llm = get_llm_client()
    client, actual, error = llm._get_client_for_model(model)
    if client is None or error:
        return None
    content = json.dumps(evidence, ensure_ascii=False)
    for attempt in range(2):
        body = content
        if attempt:
            body += '\n注意：仅输出规定JSON；所有数值必须来自上述记录；不得写因果或链接。'
        raw = llm._call_with_retry([
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': body},
        ], model=actual, client=client)
        if not isinstance(raw, dict) or raw.get('error'):
            continue
        try:
            answer = validate(raw, evidence)
        except ValueError:
            continue
        return {'status': 'ok', 'answer': answer, 'model_version': actual}
    return None
