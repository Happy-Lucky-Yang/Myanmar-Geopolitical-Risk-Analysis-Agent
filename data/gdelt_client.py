"""
data.gdelt_client - GDELT DOC 2.0 API 客户端
对接 GDELT (Global Database of Events, Language, and Tone) 数据库，
查询缅甸相关地缘政治事件新闻。

GDELT DOC 2.0 API:
  - 按关键词 + 国家过滤查询新闻文章
  - 返回主题标签（themes）、情感分数（tone）、地理位置（locations）
  - 免费、无需 API Key

参考文档：https://blog.gdeltproject.org/gdelt-doc-2-0-api-released/
"""
import time
import random
import logging
import threading
import requests
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ============================================================
# GDELT CAMEO 事件编码分类（用于地缘风险分级）
# ============================================================
# 参考：CAMEO Codebook - https://parusanalytics.com/eventdata/cameo.dir/CAMEO.Manual.1.1b3.pdf

# 冲突/暴力事件（高严重程度）
CONFLICT_CAMEO_CODES = [
    "18",  # Use unconventional mass violence (武力)
    "19",  # Assassinate
    "20",  # Fight with small arms and light weapons
    "21",  # Fight with military forces
    "22",  # Use unconventional mass violence
    "17",  # Coerce (胁迫)
]

# 抗议/动荡事件（中高严重程度）
UNREST_CAMEO_CODES = [
    "14",  # Protest
    "15",  # Exhibit force posture
    "16",  # Reduce relations
    "13",  # Make pessimistic comment
]

# 外交/合作事件（低严重程度 / 正面信号）
DIPLOMACY_CAMEO_CODES = [
    "01",  # Make public statement
    "02",  # Appeal
    "03",  # Express intent to cooperate
    "04",  # Consult
    "05",  # Engage in diplomatic cooperation
    "06",  # Engage in material cooperation
    "07",  # Provide aid
    "08",  # Yield
    "09",  # Investigate
]

# 地缘风险相关主题关键词（GDELT themes 体系）
GEOPOLITICAL_THEMES = [
    "CONFLICT_WAR",
    "CRISIS_DISASTER",
    "TERROR",
    "POLITICAL",
    "UN_ACC",          # UN activity
    "MILITARY",        # Military related
    "HUMAN_RIGHTS",    # Human rights
    "REFUGEES",        # Refugee crisis
    "ECONOMIC",        # Economic
    "DIPLOMACY",
    "TAX_TAXES",
    "ENVIRONMENT",     # Environmental issues
    "SANCTIONS",
]

# GDELT DOC 2.0 API 基础 URL
GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# ============================================================
# 全局请求限速器（模块级，跨实例/跨线程生效）
# ============================================================
# GDELT 官方限流按 IP 计：每 5 秒最多 1 个请求。
# 同一进程内可能有多个调用方（定时任务 + /api/analyze + 页面刷新），
# 因此在模块级维护"上次请求时间戳"，确保任意两次请求间隔达标。
_pace_lock = threading.Lock()
_last_request_ts = 0.0  # 使用 time.monotonic() 时钟


class GDELTClient:
    """GDELT DOC 2.0 API 客户端"""

    def __init__(self, config: Dict = None):
        cfg = config or {}
        self._base_url = cfg.get("api_url", GDELT_DOC_API_URL)
        self._default_lang = cfg.get("language", "english")
        self._max_results = cfg.get("max_results", 100)
        self._timespan_days = cfg.get("timespan_days", 7)
        self._timeout = cfg.get("timeout", 30)
        self._max_retries = cfg.get("max_retries", 3)
        self._backoff = cfg.get("backoff", 5)  # GDELT 要求每5秒一个请求
        # 全局限速参数：最小请求间隔（留 0.5s 余量）+ 随机抖动上限
        self._min_interval = cfg.get("min_interval", 5.5)
        self._jitter = cfg.get("jitter", 2.0)
        # 代理（可换出口 IP，缓解共享配额限流；未配置时直连）
        from utils.config import get_proxy
        self._proxies = get_proxy()
        # 默认关键词（缅甸地缘政治）
        self._default_query = cfg.get(
            "query",
            '"Myanmar" OR "Burma" OR conflict OR military OR ceasefire OR sanction OR refugee'
        )
        # 默认国家过滤：Myanmar (BM)
        self._default_country = cfg.get("source_country", "BM")

    def _pace_request(self):
        """
        全局限速：确保任意两次 GDELT 请求间隔 >= min_interval + 随机抖动。

        在锁内预定本次请求的时间槽再睡眠，多线程并发时自动排队，
        避免多个调用方同时发出请求触发 429。
        """
        global _last_request_ts
        with _pace_lock:
            now = time.monotonic()
            target = max(now, _last_request_ts + self._min_interval
                         + random.uniform(0.0, self._jitter))
            _last_request_ts = target
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _make_request(self, params: Dict) -> Optional[Dict]:
        """
        带重试机制的 HTTP GET 请求

        :param params: 查询参数字典
        :return: JSON 响应字典，或 None（请求失败）
        """
        for attempt in range(self._max_retries):
            # 全局限速排队（含重试请求）
            self._pace_request()
            try:
                resp = requests.get(
                    self._base_url,
                    params=params,
                    timeout=self._timeout,
                    headers={
                        "User-Agent": "MyanmarRiskSystem/1.0 (Academic Research)",
                        "Accept": "application/json",
                    },
                    proxies=self._proxies or None,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    return data
                elif resp.status_code == 429:
                    # 限流重试：优先遵循服务器返回的 Retry-After 头，
                    # 否则回退到指数退避；均叠加随机抖动避免贴边再被拒
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else 0.0
                    except ValueError:
                        wait = 0.0
                    if wait > 0:
                        wait += random.uniform(0.0, self._jitter)
                    else:
                        wait = max(5, self._backoff * (2 ** attempt)) \
                            + random.uniform(0.0, self._jitter)
                    logger.warning(
                        f"[GDELT] API 限流 (429), 等待 {wait:.0f}s 后重试"
                    )
                    time.sleep(wait)
                elif resp.status_code == 503:
                    wait = self._backoff * (2 ** attempt)
                    logger.warning(
                        f"[GDELT] 服务不可用 (503), 等待 {wait}s 后重试"
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"[GDELT] HTTP {resp.status_code}: {resp.text[:200]}")
                    return None

            except requests.Timeout:
                logger.warning(f"[GDELT] 请求超时 ({self._timeout}s), 重试 {attempt+1}/{self._max_retries}")
                time.sleep(self._backoff * (2 ** attempt))
            except requests.RequestException as e:
                logger.warning(f"[GDELT] 请求异常: {e}, 重试 {attempt+1}/{self._max_retries}")
                time.sleep(self._backoff * (2 ** attempt))
            except ValueError as e:
                logger.error(f"[GDELT] JSON 解析失败: {e}")
                return None

        logger.error("[GDELT] 所有重试均失败")
        return None

    def search_articles(
        self,
        query: str = None,
        timespan_days: int = None,
        source_country: str = None,
        max_results: int = None,
        language: str = None,
    ) -> List[Dict]:
        """
        查询 GDELT 新闻文章

        :param query: 搜索关键词（默认：缅甸地缘政治相关）
        :param timespan_days: 查询最近多少天的数据
        :param source_country: 来源国家代码（默认 BM = 缅甸）
        :param max_results: 最大返回条数
        :param language: 语言过滤
        :return: 原始 GDELT 文章列表
        """
        params = {
            "query": query or self._default_query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": max_results or self._max_results,
            "timespan": f"{timespan_days or self._timespan_days}days",
            "sort": "datedesc",
            "sourcecountry": source_country or self._default_country,
        }

        lang = language or self._default_lang
        if lang:
            params["sourcelang"] = lang

        logger.info(
            f"[GDELT] 查询: query='{params['query'][:60]}...', "
            f"timespan={params['timespan']}, country={params['sourcecountry']}"
        )

        data = self._make_request(params)
        if data and "articles" in data:
            articles = data["articles"]
            logger.info(f"[GDELT] 获取 {len(articles)} 条文章")
            return articles

        if data and "status" in data:
            logger.warning(f"[GDELT] 返回状态: {data.get('status')}")

        return []

    def search_articles_multi(
        self,
        queries: List[str] = None,
        timespan_days: int = None,
        source_country: str = None,
        max_results_per_query: int = None,
    ) -> List[Dict]:
        """
        多关键词查询（按语言合并为 OR 组合查询 + 去重）

        优化策略：GDELT 限流每 IP 每 5 秒 1 个请求，因此将关键词按
        语言分组，每组用 OR 合并为一次请求（N 个关键词 -> 最多 2 次请求），
        大幅减少请求次数；请求间隔由全局限速器 _pace_request() 保障。

        :param queries: 关键词列表
        :param timespan_days: 查询最近多少天
        :param source_country: 来源国家代码
        :param max_results_per_query: 每组查询最大返回数
        :return: 合并去重后的文章列表
        """
        if queries is None:
            queries = [
                'Myanmar conflict',
                'Myanmar military ceasefire',
                'Myanmar refugee crisis',
                'Myanmar sanctions',
                'Myanmar China economic',
            ]

        # 按语言分组（中文/英文），每组合并为一条 OR 查询
        groups = {"english": [], "chinese": []}
        for q in queries:
            if any('\u4e00' <= c <= '\u9fff' for c in q):
                groups["chinese"].append(q)
            else:
                groups["english"].append(q)
        group_items = [(lang, qs) for lang, qs in groups.items() if qs]

        all_articles = []
        seen_urls = set()

        for i, (lang, qs) in enumerate(group_items):
            combined_query = " OR ".join(f'("{q}")' for q in qs)
            logger.info(
                f"[GDELT] 合并查询 ({i+1}/{len(group_items)}): "
                f"{len(qs)} 个关键词 [lang={lang}]"
            )
            articles = self.search_articles(
                query=combined_query,
                timespan_days=timespan_days,
                source_country=source_country,
                max_results=max_results_per_query or self._max_results,
                language=lang,
            )

            for article in articles:
                url = article.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_articles.append(article)

            # 请求间隔由全局限速器保障，无需额外 sleep

        logger.info(f"[GDELT] 多关键词查询完成，共 {len(all_articles)} 条去重文章")
        return all_articles


# ============================================================
# GDELT 事件分析辅助函数
# ============================================================

def extract_event_severity(article: Dict) -> Dict:
    """
    从 GDELT 文章的主题/事件编码中提取事件严重程度

    :param article: GDELT 原始文章字典
    :return: {
        "has_conflict": bool,     # 是否包含冲突事件
        "has_unrest": bool,       # 是否包含动荡事件
        "has_diplomacy": bool,    # 是否包含外交事件
        "max_severity": float,    # 最大严重程度 (0.0 ~ 1.0)
        "event_categories": list  # 事件分类列表
    }
    """
    themes = article.get("themes", []) or []
    has_conflict = False
    has_unrest = False
    has_diplomacy = False
    max_severity = 0.0
    event_categories = []

    for theme in themes:
        theme_str = str(theme)

        # 提取 CAMEO 事件码（GDELT themes 格式可能为 "CAMEO_CODE" 或 "THEME_NAME"）
        # 冲突事件
        for code in CONFLICT_CAMEO_CODES:
            if theme_str.startswith(code) and len(theme_str) <= len(code) + 2:
                has_conflict = True
                max_severity = max(max_severity, 0.9)
                if "conflict" not in event_categories:
                    event_categories.append("conflict")

        # 动荡事件
        for code in UNREST_CAMEO_CODES:
            if theme_str.startswith(code) and len(theme_str) <= len(code) + 2:
                has_unrest = True
                max_severity = max(max_severity, 0.6)
                if "unrest" not in event_categories:
                    event_categories.append("unrest")

        # 外交事件
        for code in DIPLOMACY_CAMEO_CODES:
            if theme_str.startswith(code) and len(theme_str) <= len(code) + 2:
                has_diplomacy = True
                if "diplomacy" not in event_categories:
                    event_categories.append("diplomacy")

        # 主题关键词匹配（补充）
        theme_upper = theme_str.upper()
        for kw in GEOPOLITICAL_THEMES:
            if kw in theme_upper:
                if kw in ("CONFLICT_WAR", "TERROR", "CRISIS_DISASTER"):
                    has_conflict = True
                    max_severity = max(max_severity, 0.8)
                    if "conflict" not in event_categories:
                        event_categories.append("conflict")
                elif kw in ("POLITICAL", "MILITARY"):
                    has_unrest = True
                    max_severity = max(max_severity, 0.5)
                    if "unrest" not in event_categories:
                        event_categories.append("unrest")
                elif kw in ("REFUGEES", "HUMAN_RIGHTS", "SANCTIONS"):
                    max_severity = max(max_severity, 0.6)
                    if "humanitarian" not in event_categories:
                        event_categories.append("humanitarian")
                elif kw in ("DIPLOMACY", "ECONOMIC"):
                    has_diplomacy = True
                    if "diplomacy" not in event_categories:
                        event_categories.append("diplomacy")

    # 如果没有任何事件匹配，给一个基础分
    if max_severity == 0.0:
        max_severity = 0.2  # 基础分（文章存在但无显著事件编码）

    return {
        "has_conflict": has_conflict,
        "has_unrest": has_unrest,
        "has_diplomacy": has_diplomacy,
        "max_severity": round(max_severity, 2),
        "event_categories": event_categories,
    }


def extract_tone_sentiment(article: Dict) -> Dict:
    """
    从 GDELT 文章的情感字段提取情感分数

    GDELT tone 字段格式: "tone,positive,negative,polarity,positive_count,negative_count"
    tone 值范围: -100 ~ +100（负 = 负面，正 = 正面）

    :param article: GDELT 原始文章字典
    :return: {
        "tone_score": float,       # 原始 GDELT tone (-100 ~ +100)
        "normalized_score": float, # 归一化到 [0, 1]
        "risk_score": float,       # 风险分 = 1 - normalized
    }
    """
    tone_field = article.get("tone", "")
    tone_score = 0.0

    if tone_field:
        try:
            # tone 字段可能是逗号分隔的字符串
            parts = str(tone_field).split(",")
            tone_score = float(parts[0])
        except (ValueError, IndexError):
            tone_score = 0.0

    # 归一化: [-100, +100] → [0, 1]
    # tone = -100 → normalized = 0（极度负面）
    # tone = +100 → normalized = 1（极度正面）
    normalized = (tone_score + 100) / 200
    normalized = max(0.0, min(1.0, normalized))

    return {
        "tone_score": round(tone_score, 2),
        "normalized_score": round(normalized, 4),
        "risk_score": round(1.0 - normalized, 4),
    }


def extract_locations(article: Dict) -> List[Dict]:
    """
    从 GDELT 文章的 locations 字段提取地理位置

    :param article: GDELT 原始文章字典
    :return: 位置信息列表 [{"name": "...", "lat": ..., "lon": ..., "type": "..."}, ...]
    """
    locations_raw = article.get("locations", "")
    if not locations_raw:
        return []

    locations = []
    # locations 字段可能是逗号分隔的字符串列表
    parts = str(locations_raw).split(";")

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # 格式: "type#countrycode#full_name#adm1code#lat#lon#featureid"
        fields = part.split("#")
        if len(fields) >= 6:
            try:
                loc = {
                    "name": fields[2],
                    "lat": float(fields[4]) if fields[4] else 0.0,
                    "lon": float(fields[5]) if fields[5] else 0.0,
                    "type": fields[0] if fields[0] else "unknown",
                    "country_code": fields[1] if len(fields) > 1 else "",
                }
                locations.append(loc)
            except (ValueError, IndexError):
                continue

    return locations


def compute_gdelt_risk_metrics(articles: List[Dict]) -> Dict:
    """
    从 GDELT 文章集合计算聚合风险指标

    :param articles: GDELT 原始文章列表
    :return: {
        "article_count": int,
        "conflict_count": int,
        "conflict_frequency": float,   # 冲突文章占比 (0~1)
        "avg_tone_risk": float,        # 平均情感风险分 (0~1)
        "avg_severity": float,         # 平均事件严重程度 (0~1)
        "max_severity": float,         # 最大事件严重程度 (0~1)
        "event_summary": {...},        # 事件分类统计
        "top_locations": [...],        # 出现最多的地点
    }
    """
    if not articles:
        return {
            "article_count": 0,
            "conflict_count": 0,
            "conflict_frequency": 0.0,
            "avg_tone_risk": 0.5,
            "avg_severity": 0.0,
            "max_severity": 0.0,
            "event_summary": {},
            "top_locations": [],
        }

    n = len(articles)
    conflict_count = 0
    tone_risks = []
    severities = []
    event_counts = {}
    location_counts = {}

    for article in articles:
        # 事件严重程度
        severity_info = extract_event_severity(article)
        severities.append(severity_info["max_severity"])
        if severity_info["has_conflict"]:
            conflict_count += 1

        # 统计事件分类
        for cat in severity_info["event_categories"]:
            event_counts[cat] = event_counts.get(cat, 0) + 1

        # 情感分数
        tone_info = extract_tone_sentiment(article)
        tone_risks.append(tone_info["risk_score"])

        # 位置统计
        for loc in extract_locations(article):
            loc_name = loc["name"]
            location_counts[loc_name] = location_counts.get(loc_name, 0) + 1

    # 聚合指标
    conflict_frequency = conflict_count / max(n, 1)
    avg_tone_risk = sum(tone_risks) / len(tone_risks) if tone_risks else 0.5
    avg_severity = sum(severities) / len(severities) if severities else 0.0
    max_severity_val = max(severities) if severities else 0.0

    # 排序地点（取 top 10）
    top_locations = sorted(
        location_counts.items(), key=lambda x: x[1], reverse=True
    )[:10]

    return {
        "article_count": n,
        "conflict_count": conflict_count,
        "conflict_frequency": round(conflict_frequency, 4),
        "avg_tone_risk": round(avg_tone_risk, 4),
        "avg_severity": round(avg_severity, 4),
        "max_severity": round(max_severity_val, 4),
        "event_summary": event_counts,
        "top_locations": [{"name": k, "count": v} for k, v in top_locations],
    }
