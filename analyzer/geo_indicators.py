"""
analyzer.geo_indicators - 九指标地缘环境风险派生层

落实组长《缅甸地缘环境风险评价·九项指标的测度与计算方法》与《地缘新闻事件
编码规则说明 v1.0》：把 GDELT 事件行汇总为九项方向统一（值越大风险越高）、
量纲统一到 [0,1] 的指标，供 analyzer.risk_scorer 的九指标体系加权合成。

三层权重（要素层 地缘关系0.6 / 地理环境0.2 / 地缘结构0.2，比 3:1:1）由
胡志丁等(2013)三要素框架 + 胡志丁等(2014)权力等级论导出，全局权重见
config.yaml 的 risk_weights_v3；本模块只负责“指标原始值→方向统一→[0,1]”。

数据诚实性（与项目一贯口径一致）：
  - 双边关系类指标依赖 GDELT 国家对事件，样本天然稀疏；无样本时置 missing，
    由 risk_scorer 动态归一化剔除并重分配权重，绝不以 0 冒充“关系良好”。
  - 事件严重度：GDELT 无伤亡人数字段，故以 CAMEO 根码/QuadClass 烈度权重
    （event_severity_weight，0~1）作代理，非组长原式的 λ_k·ln(1+死亡数)。
  - 夜光变化 / 流离失所：非文本指标，来自 external_data（当前为 World Bank
    电力可及性代理与占位），未声明真实观测质量时按 missing 处理。
  - 中美博弈 / 国际制裁为无界计数强度，用可配置饱和上限映射到 [0,1]；该上限
    为方案层临时值，待积累面板历史后改用经验 Min-Max（见 SATURATION_* 注释）。
"""
import logging
import math
from typing import Dict, Iterable, List, Optional, Set, Tuple

from data.gdelt_files import ROOT_INFO, event_severity_weight
from utils.data_contract import finite_number

logger = logging.getLogger(__name__)

# ============================================================
# 国家码归一：GDELT 官方用 FIPS 10-4（BM/CH/US），历史设计文档曾观测到
# ISO3（MMR/CHN/USA）。两种写法都归一到同一 canonical 键，避免因编码体系
# 差异漏配双边事件。
# ============================================================
_COUNTRY_VARIANTS: Dict[str, Set[str]] = {
    "MMR": {"MMR", "BM", "BURMA", "MYANMAR"},
    "CHN": {"CHN", "CH", "CHINA", "PRC"},
    "USA": {"USA", "US", "UNITED STATES", "UST"},
    "THA": {"THA", "TH", "THAILAND"},
    "IND": {"IND", "IN", "INDIA"},
    "BGD": {"BGD", "BG", "BANGLADESH"},
    "LAO": {"LAO", "LA", "LAOS"},
    "GBR": {"GBR", "UK", "UNITED KINGDOM"},
    "FRA": {"FRA", "FR", "FRANCE"},
    "DEU": {"DEU", "GM", "GERMANY"},
    "ITA": {"ITA", "IT", "ITALY"},
    "ESP": {"ESP", "SP", "SPAIN"},
    "NLD": {"NLD", "NL", "NETHERLANDS"},
    "RUS": {"RUS", "RS", "RUSSIA"},
    "JPN": {"JPN", "JA", "JAPAN"},
    "EUU": {"EU", "EEC", "EUR", "EUROPEAN UNION", "ECC"},
}
_VARIANT_LOOKUP: Dict[str, str] = {
    variant: canon for canon, variants in _COUNTRY_VARIANTS.items() for variant in variants
}

# 周边邻国（组长：泰/印/孟/老）
NEIGHBORS: Set[str] = {"THA", "IND", "BGD", "LAO"}
# 西方（美缅）关系：美国 + 欧盟主要成员国 + 欧盟集体行为体
WESTERN: Set[str] = {"USA", "GBR", "FRA", "DEU", "ITA", "ESP", "NLD", "EUU"}
# 国际制裁判定中的“大国/国际组织”行为体
GREAT_POWERS: Set[str] = {"USA", "CHN", "RUS", "GBR", "FRA", "DEU", "JPN", "EUU"}

# CAMEO 根码：冲突频次口径（攻击/战斗/非常规大规模暴力），不含 17 胁迫（归国际制裁）
CONFLICT_ROOTS: Set[str] = {"18", "19", "20"}
SANCTION_ROOT: str = "17"

# 无界计数强度 → [0,1] 的临时饱和上限（方案层建议值，待面板历史改经验 Min-Max）
RIVALRY_SATURATION: float = 50.0    # 中美博弈 Σ|g|·(1+ln m) 饱和点
SANCTION_SATURATION: float = 10.0   # 国际制裁 CAMEO-17 大国事件计数饱和点

ALGORITHM_VERSION = "geo-indicators-v1"


def canon_country(code: Optional[str]) -> Optional[str]:
    """把 FIPS 或 ISO3 国家码归一到 canonical 键；未知/空返回 None。"""
    if not isinstance(code, str) or not code.strip():
        return None
    return _VARIANT_LOOKUP.get(code.strip().upper())


def _actor_pair(event: Dict) -> Tuple[Optional[str], Optional[str]]:
    return canon_country(event.get("actor1_country")), canon_country(event.get("actor2_country"))


def _mention_weight(event: Dict) -> float:
    """报道量权重 m_j = NumMentions，缺失/非正回退 1（等权）。"""
    m = event.get("num_mentions")
    return float(m) if finite_number(m) and m > 0 else 1.0


def _root(event: Dict) -> str:
    return str(event.get("root_code", "")).strip().zfill(2)


def _is_classified(event: Dict) -> bool:
    return _root(event) in ROOT_INFO


def weighted_goldstein_net(events: Iterable[Dict], targets: Set[str]) -> Tuple[Optional[float], int]:
    """
    缅甸与目标行为体集合的双边关系净值（组长的 R = Σ(g_j·m_j)/Σm_j）。

    仅统计“一方为缅甸、另一方落在 targets”的事件；goldstein 缺失的事件跳过。
    :return: (净值 R ∈ [-10,10] 或 None, 参与加权的事件数)
    """
    num = 0.0
    den = 0.0
    count = 0
    for event in events:
        a1, a2 = _actor_pair(event)
        pair = {a1, a2}
        if "MMR" not in pair or not (pair & targets):
            continue
        g = event.get("goldstein")
        if not finite_number(g):
            continue
        m = _mention_weight(event)
        num += float(g) * m
        den += m
        count += 1
    if count == 0 or den <= 0:
        return None, 0
    return num / den, count


def _relation_to_risk(net: Optional[float]) -> Optional[float]:
    """负向关系净值 R∈[-10,10] → 方向统一的风险值 [0,1]：Z=(10-R)/20。

    R=+10（最友好）→ 0；R=-10（最敌对）→ 1；R=None（无样本）→ None。
    """
    if not finite_number(net):
        return None
    return max(0.0, min(1.0, (10.0 - float(net)) / 20.0))


def china_us_rivalry(events: Iterable[Dict]) -> Tuple[Optional[float], float, int]:
    """中美博弈（正向）：涉中美同现且定位缅甸的冲突型事件强度累加。

    s_j = |g_j|·(1+ln m_j)，仅计 g_j<0；BG=Σs_j；Z=min(1, BG/饱和上限)。
    :return: (风险值[0,1] 或 None, 原始 BG, 样本数)
    """
    bg = 0.0
    count = 0
    for event in events:
        a1, a2 = _actor_pair(event)
        if not {"CHN", "USA"} <= {a1, a2}:
            continue
        g = event.get("goldstein")
        if not finite_number(g) or g >= 0:
            continue
        m = _mention_weight(event)
        bg += abs(float(g)) * (1.0 + math.log(m))
        count += 1
    if count == 0:
        return None, 0.0, 0
    return min(1.0, bg / RIVALRY_SATURATION), bg, count


def intl_sanction(events: Iterable[Dict]) -> Tuple[Optional[float], int, int]:
    """国际制裁（正向）：CAMEO 17（胁迫/含制裁）且对方为大国/国际组织的事件计数。

    Z = min(1, S/饱和上限)。
    :return: (风险值[0,1] 或 None, 计数 S, 样本数)
    """
    count = 0
    for event in events:
        if _root(event) != SANCTION_ROOT:
            continue
        a1, a2 = _actor_pair(event)
        if "MMR" in {a1, a2} and ({a1, a2} & GREAT_POWERS):
            count += 1
    if count == 0:
        return None, 0, 0
    return min(1.0, count / SANCTION_SATURATION), count, count


def conflict_and_severity(events: List[Dict]) -> Tuple[Optional[float], Optional[float], int, int]:
    """冲突频次（正向，CAMEO 18/19/20 占已分类事件比例）与事件严重度（正向，代理）。

    :return: (conflict_frequency[0,1], event_severity[0,1], 冲突数, 已分类数)
    """
    classified = [e for e in events if _is_classified(e)]
    n_classified = len(classified)
    conflict = [e for e in classified if _root(e) in CONFLICT_ROOTS]
    cf = (len(conflict) / n_classified) if n_classified else None
    severities = [event_severity_weight(e) for e in conflict]
    severities = [s for s in severities if finite_number(s)]
    sev = (sum(severities) / len(severities)) if severities else None
    return cf, sev, len(conflict), n_classified


def _external_indicator(external: Dict, key: str, negative: bool) -> Tuple[Optional[float], str]:
    """从 external_data 取非文本指标；仅显式声明 observed 且为有限数才纳入。

    :param negative: True 表示原始值越大风险越低（如夜光增亮），需方向翻转。
    :return: (方向统一到 [0,1] 的值 或 None, 质量标记)
    """
    obs = external.get(key)
    if not isinstance(obs, dict) or obs.get("quality") != "observed":
        return None, "missing"
    value = obs.get("value")
    if not finite_number(value):
        return None, "missing"
    value = float(value)
    unified = -value if negative else value
    return max(0.0, min(1.0, unified)), "observed"


def compute_geo_indicators(events: Optional[List[Dict]], external_data: Optional[Dict] = None) -> Dict:
    """
    由 GDELT 事件行 + 外部数据计算九项指标（方向统一、量纲 [0,1]、带质量标记）。

    :param events: 已去重的 GDELT 事件列表（含 actor1/2_country、goldstein、num_mentions）
    :param external_data: 非文本指标（nightlight_change / displacement），需声明 quality
    :return: {"indicators": {key: {"value","quality","sample_count","raw","direction","method"}},
              "algorithm_version", "source", "event_count"}
    """
    events = [e for e in (events or []) if isinstance(e, dict)]
    external = external_data or {}

    cf, sev, n_conflict, n_classified = conflict_and_severity(events)
    r_cn, cn_n = weighted_goldstein_net(events, {"CHN"})
    r_nb, nb_n = weighted_goldstein_net(events, NEIGHBORS)
    r_west, west_n = weighted_goldstein_net(events, WESTERN)
    rivalry_z, rivalry_bg, rivalry_n = china_us_rivalry(events)
    sanction_z, sanction_s, sanction_n = intl_sanction(events)
    nightlight_z, nightlight_q = _external_indicator(external, "nightlight_change", negative=True)
    displacement_z, displacement_q = _external_indicator(external, "displacement", negative=False)

    def _mk(value, quality, sample, raw, direction, method):
        return {"value": round(value, 4) if finite_number(value) else None,
                "quality": quality, "sample_count": sample,
                "raw": round(raw, 4) if finite_number(raw) else raw,
                "direction": direction, "method": method}

    def _q(value, sample):
        return "missing" if value is None else ("observed" if sample else "derived")

    indicators = {
        "conflict_frequency": _mk(cf, _q(cf, n_classified), n_conflict, n_conflict, "positive",
                                  "CAMEO 18/19/20 占已分类事件比例"),
        "event_severity": _mk(sev, _q(sev, n_conflict), n_conflict, sev, "positive",
                              "冲突事件 CAMEO 烈度权重均值（代理，非死亡人数口径）"),
        "china_myanmar_relation": _mk(_relation_to_risk(r_cn), _q(_relation_to_risk(r_cn), cn_n), cn_n, r_cn,
                                      "negative", "中缅 Goldstein 按 NumMentions 加权净值 R=(10-R)/20"),
        "neighbor_relation": _mk(_relation_to_risk(r_nb), _q(_relation_to_risk(r_nb), nb_n), nb_n, r_nb,
                                 "negative", "缅-泰印孟老 Goldstein 加权净值（等权 β_k）"),
        "western_relation": _mk(_relation_to_risk(r_west), _q(_relation_to_risk(r_west), west_n), west_n, r_west,
                                "negative", "缅-美欧 Goldstein 加权净值（等权 β_k）"),
        "china_us_rivalry": _mk(rivalry_z, _q(rivalry_z, rivalry_n), rivalry_n, rivalry_bg, "positive",
                                "中美同现冲突型 Σ|g|·(1+ln m)，饱和上限 %.0f" % RIVALRY_SATURATION),
        "intl_sanction": _mk(sanction_z, _q(sanction_z, sanction_n), sanction_n, sanction_s, "positive",
                             "CAMEO 17 且对方为大国/国际组织计数，饱和上限 %.0f" % SANCTION_SATURATION),
        "nightlight_change": _mk(nightlight_z, nightlight_q, 0, nightlight_z, "negative",
                                 "非文本指标（当前 World Bank 电力可及性代理，非 VIIRS）"),
        "displacement": _mk(displacement_z, displacement_q, 0, displacement_z, "positive",
                            "非文本指标（IDMC/IDP，当前为占位）"),
    }
    return {"indicators": indicators, "algorithm_version": ALGORITHM_VERSION,
            "source": "gdelt_csv", "event_count": len(events),
            "classified_count": n_classified,
            "note": "双边关系类样本稀疏时置 missing 由评分层动态归一剔除；严重度为 CAMEO 烈度代理。"}


def to_scorer_input(result: Dict) -> Tuple[Dict[str, float], Dict[str, str]]:
    """把 compute_geo_indicators 结果拆成 risk_scorer 需要的 (values, quality)。

    missing 指标不放入 values（由 RiskScorer 动态归一化处理权重重分配）。
    """
    values: Dict[str, float] = {}
    quality: Dict[str, str] = {}
    for key, item in (result.get("indicators") or {}).items():
        quality[key] = item.get("quality", "missing")
        if item.get("value") is not None and quality[key] in {"observed", "derived"}:
            values[key] = item["value"]
    return values, quality


# 九项指标全局权重（组长权重分配表：要素层 0.6/0.2/0.2 × 层内权重，逐级归一）。
# config.yaml 的 risk_weights_v3 为权威来源；此处为缺省回退，二者须一致。
DEFAULT_WEIGHTS_V3: Dict[str, float] = {
    "conflict_frequency": 0.162,
    "china_myanmar_relation": 0.138,
    "china_us_rivalry": 0.133,
    "event_severity": 0.120,
    "nightlight_change": 0.110,
    "neighbor_relation": 0.102,
    "displacement": 0.090,
    "western_relation": 0.078,
    "intl_sanction": 0.067,
}

# 指标中文名（供诊断归因、前端展示复用）
INDICATOR_NAMES: Dict[str, str] = {
    "conflict_frequency": "冲突频次",
    "event_severity": "事件严重度",
    "china_myanmar_relation": "中缅关系",
    "neighbor_relation": "周边邻国关系",
    "western_relation": "西方（美缅）关系",
    "china_us_rivalry": "中美博弈",
    "intl_sanction": "国际制裁",
    "nightlight_change": "夜光变化",
    "displacement": "流离失所",
}

# 要素层归属（用于按地缘关系/地理环境/地缘结构聚合展示）
INDICATOR_ELEMENT: Dict[str, str] = {
    "conflict_frequency": "地缘关系", "china_myanmar_relation": "地缘关系",
    "event_severity": "地缘关系", "neighbor_relation": "地缘关系",
    "western_relation": "地缘关系",
    "nightlight_change": "地理环境", "displacement": "地理环境",
    "china_us_rivalry": "地缘结构", "intl_sanction": "地缘结构",
}
