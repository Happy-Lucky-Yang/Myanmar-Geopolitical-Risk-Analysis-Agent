"""
data.gdelt_crawler - GDELT 数据适配器
将 GDELT DOC 2.0 API 返回的原始数据转换为与现有 NewsCrawler 一致的格式，
并支持增量去重和统一保存。

输出字段（与 data/crawler.py 一致）：
  title, pub_time, content, url, source, crawled_at,
  source_website, language, gdelt_tone, gdelt_themes, gdelt_locations
"""
import os
import json
import logging
import threading
import time
from datetime import datetime
from typing import List, Dict, Optional

from data.gdelt_client import (
    GDELTClient,
    extract_event_severity,
    extract_tone_sentiment,
    extract_locations,
    compute_gdelt_risk_metrics,
)
from utils.config import get_gdelt_config

logger = logging.getLogger(__name__)


class GDELTCrawler:
    """GDELT 新闻适配器：查询 GDELT API 并输出兼容格式"""

    def __init__(self, config: Dict = None):
        self._cfg = config or get_gdelt_config()
        self._enabled = self._cfg.get("enabled", True)
        self._client = GDELTClient(self._cfg)

        # 数据目录
        self._raw_dir = os.path.join("data", "raw")
        os.makedirs(self._raw_dir, exist_ok=True)

        # 增量去重
        self._urls_seen_file = os.path.join(self._raw_dir, "gdelt_urls_seen.txt")
        self._urls_seen = self._load_urls_seen()
        self._urls_lock = threading.Lock()  # URL 集合并发保护

        # GDELT 指标缓存（TTL = 5分钟，避免频繁调用 API）
        self._metrics_cache = None
        self._metrics_cache_time = 0
        self._metrics_ttl = self._cfg.get("metrics_cache_seconds", 300)

    # ============================================================
    # URL 去重
    # ============================================================

    def _load_urls_seen(self) -> set:
        """加载已处理 URL 集合"""
        if not os.path.exists(self._urls_seen_file):
            return set()
        with open(self._urls_seen_file, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())

    def _save_urls_seen(self):
        """持久化已处理 URL 集合（上限 10000）"""
        urls = sorted(self._urls_seen)
        if len(urls) > 10000:
            urls = urls[-10000:]
            self._urls_seen = set(urls)
        with open(self._urls_seen_file, "w", encoding="utf-8") as f:
            for url in urls:
                f.write(url + "\n")

    def _is_new_url(self, url: str) -> bool:
        """检查 URL 是否为新链接（线程安全）"""
        normalized = url.rstrip("/")
        with self._urls_lock:
            if normalized in self._urls_seen:
                return False
            self._urls_seen.add(normalized)
        return True

    # ============================================================
    # 数据转换
    # ============================================================

    def _normalize_article(self, raw_article: Dict) -> Dict:
        """
        将 GDELT 原始文章转换为与 NewsCrawler 兼容的格式

        :param raw_article: GDELT API 返回的原始文章字典
        :return: 标准化新闻字典
        """
        url = raw_article.get("url", "")

        # 解析发布时间
        pub_time = ""
        seendate = raw_article.get("seendate", "")
        if seendate:
            # GDELT seendate 格式: "20260615120000" (YYYYMMDDHHmmss)
            try:
                dt = datetime.strptime(seendate[:8], "%Y%m%d")
                pub_time = dt.strftime("%Y-%m-%d")
            except ValueError:
                pub_time = datetime.now().strftime("%Y-%m-%d")
        else:
            pub_time = datetime.now().strftime("%Y-%m-%d")

        # 提取情感
        tone_info = extract_tone_sentiment(raw_article)

        # 提取事件严重程度
        severity_info = extract_event_severity(raw_article)

        # 提取地理位置
        locations = extract_locations(raw_article)

        # 提取来源域名
        source_domain = raw_article.get("domain", "")

        # GDELT themes 列表
        themes = raw_article.get("themes", []) or []

        # 构建内容摘要（GDELT DOC API 不返回全文，只有 URL 和元数据）
        social_embed = raw_article.get("social_embeddata", "")
        content = ""
        if social_embed:
            # social_embeddata 包含文章的社交媒体嵌入信息
            content = str(social_embed)[:500]

        # 自动检测语言：根据 title 内容判断（而非硬编码 "en"）
        title = raw_article.get("title", "")
        lang = "zh" if any('\u4e00' <= c <= '\u9fff' for c in title) else "en"

        return {
            # === 与 NewsCrawler 兼容的标准字段 ===
            "title": title,
            "pub_time": pub_time,
            "content": content,
            "url": url,
            "source": "GDELT",
            "source_website": source_domain,
            "language": lang,
            "crawled_at": datetime.now().isoformat(),

            # === GDELT 专有扩展字段 ===
            "gdelt_tone": tone_info,
            "gdelt_themes": themes,
            "gdelt_locations": locations,
            "gdelt_severity": severity_info,
        }

    # ============================================================
    # 核心方法：爬取 & 保存
    # ============================================================

    def crawl(self, timespan_days: int = None) -> List[Dict]:
        """
        从 GDELT 查询缅甸相关新闻，并转换为兼容格式

        :param timespan_days: 查询最近多少天（默认从 config 读取）
        :return: 标准化新闻字典列表（仅包含新文章）
        """
        if not self._enabled:
            logger.info("[GDELT Crawler] 未启用，跳过")
            return []

        days = timespan_days or self._cfg.get("timespan_days", 7)

        # 使用多关键词查询
        use_multi = self._cfg.get("use_multi_query", True)

        if use_multi:
            # 从 config 读取关键词列表（支持中英文双语）
            queries = self._cfg.get("query_keywords", None)
            raw_articles = self._client.search_articles_multi(
                queries=queries,  # None 时使用 gdelt_client 内置默认值
                timespan_days=days,
                source_country=self._cfg.get("source_country", "BM"),
                max_results_per_query=self._cfg.get("max_results", 100),
            )
        else:
            raw_articles = self._client.search_articles(
                timespan_days=days,
                source_country=self._cfg.get("source_country", "BM"),
                max_results=self._cfg.get("max_results", 100),
            )

        # 转换为兼容格式 & 去重
        news_list = []
        for raw in raw_articles:
            url = raw.get("url", "")
            if url and self._is_new_url(url):
                article = self._normalize_article(raw)
                if article["title"]:
                    news_list.append(article)

        logger.info(
            f"[GDELT Crawler] 查询 {days} 天数据: "
            f"原始 {len(raw_articles)} 条, 新增 {len(news_list)} 条（去重后）"
        )

        # 保存去重 URL
        self._save_urls_seen()

        return news_list

    def save_news(self, news_list: List[Dict], filename: str = None) -> Optional[str]:
        """
        将 GDELT 新闻数据保存到 data/raw/ 目录

        :param news_list: 新闻条目列表
        :param filename: 文件名（默认按日期生成）
        :return: 保存的文件路径
        """
        if not news_list:
            logger.info("[GDELT Crawler] 无数据可保存")
            return None

        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"gdelt_news_{date_str}.json"

        filepath = os.path.join(self._raw_dir, filename)

        # 追加模式：如当天已有文件则合并
        existing = []
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)

        # 合并并去重
        seen_urls = {item.get("url", "") for item in existing}
        for item in news_list:
            if item.get("url", "") not in seen_urls:
                existing.append(item)
                seen_urls.add(item.get("url", ""))

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(f"[GDELT Crawler] 已保存 {len(existing)} 条新闻至 {filepath}")
        return filepath

    # ============================================================
    # 风险指标计算（供 app.py 调用）
    # ============================================================

    def get_risk_metrics(self, timespan_days: int = 7) -> Dict:
        """
        查询 GDELT 并计算聚合风险指标

        TTL 缓存机制：同一 timespan_days 在缓存有效期内直接返回缓存结果，
        避免 /api/analyze 每次请求都调用 GDELT API。

        :param timespan_days: 查询最近多少天
        :return: compute_gdelt_risk_metrics() 的返回字典
        """
        # 检查缓存是否有效
        now = time.time()
        if (self._metrics_cache is not None
                and self._metrics_cache.get("_timespan") == timespan_days
                and (now - self._metrics_cache_time) < self._metrics_ttl):
            logger.debug("[GDELT Crawler] 使用缓存指标")
            return self._metrics_cache

        if not self._enabled:
            logger.info("[GDELT Crawler] 未启用，返回默认指标")
            return compute_gdelt_risk_metrics([])

        # 使用多关键词查询（与 crawl() 一致）
        use_multi = self._cfg.get("use_multi_query", True)
        if use_multi:
            queries = self._cfg.get("query_keywords", None)
            raw_articles = self._client.search_articles_multi(
                queries=queries,
                timespan_days=timespan_days,
                source_country=self._cfg.get("source_country", "BM"),
                max_results_per_query=self._cfg.get("max_results", 100),
            )
        else:
            raw_articles = self._client.search_articles(
                timespan_days=timespan_days,
                source_country=self._cfg.get("source_country", "BM"),
                max_results=self._cfg.get("max_results", 100),
            )

        result = compute_gdelt_risk_metrics(raw_articles)
        result["_timespan"] = timespan_days

        # 更新缓存
        self._metrics_cache = result
        self._metrics_cache_time = now

        return result


# ============================================================
# 模块级单例
# ============================================================

_gdelt_crawler_instance = None
_gdelt_crawler_lock = threading.Lock()


def get_gdelt_crawler() -> GDELTCrawler:
    """获取全局 GDELT 爬虫单例（线程安全）"""
    global _gdelt_crawler_instance
    if _gdelt_crawler_instance is None:
        with _gdelt_crawler_lock:
            if _gdelt_crawler_instance is None:
                _gdelt_crawler_instance = GDELTCrawler()
    return _gdelt_crawler_instance
