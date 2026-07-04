"""
data.rss_crawler - RSS/Atom 新闻源爬虫模块
支持多个国际缅甸新闻源的 RSS/Atom 订阅：
  - Frontier Myanmar (frontiermyanmar.net) — 深度分析、政治报道
  - Democratic Voice of Burma / DVB (dvb.no.org) — 突发新闻、社会报道
  - The Diplomat - Myanmar (thediplomat.com) — 地缘政治分析

输出字段：title, pub_time, content, url, source, language, crawled_at
"""
import os
import re
import json
import time
import logging
import threading
import requests
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ============================================================
# RSS 源配置（内置默认值，可通过 config.yaml 覆盖）
# ============================================================
DEFAULT_RSS_FEEDS = {
    "frontier_myanmar": {
        "name": "Frontier Myanmar",
        "rss_url": "https://www.frontiermyanmar.net/en/feed",
        "website": "https://www.frontiermyanmar.net",
        "language": "en",
        "fallback_url": "https://www.frontiermyanmar.net/en/news",
    },
    "dvb": {
        "name": "Democratic Voice of Burma",
        "rss_url": "https://www.dvb.no.org/feed",
        "website": "https://www.dvb.no.org",
        "language": "en",
        "fallback_url": "https://www.dvb.no.org/news",
    },
    "diplomat_myanmar": {
        "name": "The Diplomat (Myanmar)",
        "rss_url": "https://thediplomat.com/feed/",
        "website": "https://thediplomat.com",
        "language": "en",
        "fallback_url": None,  # 无备用（付费墙限制）
    },
}


class RSSNewsCrawler:
    """RSS/Atom 新闻源爬虫"""

    def __init__(self, config: Dict = None):
        self._cfg = config or {}
        self._timeout = self._cfg.get("timeout", 15)
        self._max_retries = self._cfg.get("max_retries", 2)
        self._backoff = self._cfg.get("backoff", 2)
        self._max_per_source = self._cfg.get("max_per_source", 30)

        # 数据目录
        self._raw_dir = os.path.join("data", "raw")
        os.makedirs(self._raw_dir, exist_ok=True)

        # URL 去重
        self._urls_seen_file = os.path.join(self._raw_dir, "rss_urls_seen.txt")
        self._urls_seen = self._load_urls_seen()

        # 合并配置中的 RSS feeds
        self._feeds = dict(DEFAULT_RSS_FEEDS)
        if "rss_feeds" in self._cfg:
            for key, val in self._cfg["rss_feeds"].items():
                self._feeds[key] = val

    # ============================================================
    # HTTP 请求
    # ============================================================

    def _request(self, url: str) -> Optional[requests.Response]:
        """带重试的 HTTP GET"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 MyanmarRiskSystem/1.0",
            "Accept": "application/rss+xml, application/xml, text/xml, text/html, */*",
        }
        for attempt in range(self._max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=self._timeout)
                if resp.status_code == 200:
                    return resp
                elif resp.status_code in (429, 503):
                    time.sleep(self._backoff * (2 ** attempt))
                else:
                    logger.warning(f"[RSS] HTTP {resp.status_code}: {url}")
                    return None
            except requests.RequestException as e:
                logger.warning(f"[RSS] 请求异常: {e}")
                time.sleep(self._backoff * (2 ** attempt))
        return None

    # ============================================================
    # URL 去重
    # ============================================================

    def _load_urls_seen(self) -> set:
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
        normalized = url.rstrip("/")
        if normalized in self._urls_seen:
            return False
        self._urls_seen.add(normalized)
        return True

    # ============================================================
    # RSS/Atom 解析
    # ============================================================

    def _parse_feed(self, xml_text: str, source_key: str) -> List[Dict]:
        """
        解析 RSS/Atom XML 内容（不依赖 feedparser，使用 BeautifulSoup）

        :param xml_text: RSS/Atom XML 字符串
        :param source_key: 来源标识
        :return: 新闻条目列表
        """
        soup = BeautifulSoup(xml_text, "lxml-xml")  # XML 解析器
        feed_config = self._feeds.get(source_key, {})
        articles = []

        # 尝试 RSS 2.0 格式
        items = soup.find_all("item")
        if not items:
            # 尝试 Atom 格式
            items = soup.find_all("entry")

        for item in items[:self._max_per_source]:
            # 提取标题
            title_tag = item.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            if not title:
                continue

            # 提取链接
            link = ""
            link_tag = item.find("link")
            if link_tag:
                link = link_tag.get("href", "") or link_tag.get_text(strip=True)
            guid_tag = item.find("guid")
            if not link and guid_tag:
                link = guid_tag.get_text(strip=True)

            if not link or not self._is_new_url(link):
                continue

            # 提取发布时间
            pub_time = ""
            for date_tag_name in ["pubDate", "published", "updated", "dc:date"]:
                date_tag = item.find(date_tag_name)
                if date_tag:
                    pub_time = date_tag.get_text(strip=True)
                    break

            # 提取描述/摘要
            desc_tag = item.find("description") or item.find("summary") or item.find("content")
            description = desc_tag.get_text(strip=True) if desc_tag else ""
            # 清理 HTML 标签
            description = re.sub(r"<[^>]+>", "", description)
            description = re.sub(r"\s+", " ", description).strip()

            articles.append({
                "title": title,
                "pub_time": pub_time,
                "content": description[:2000],
                "url": link,
                "source": feed_config.get("name", source_key),
                "source_website": feed_config.get("website", ""),
                "language": feed_config.get("language", "en"),
                "crawled_at": datetime.now().isoformat(),
            })

        return articles

    # ============================================================
    # 单源爬取
    # ============================================================

    def _crawl_single_feed(self, source_key: str) -> List[Dict]:
        """爬取单个 RSS 源"""
        feed_config = self._feeds.get(source_key)
        if not feed_config:
            logger.warning(f"[RSS] 未知源: {source_key}")
            return []

        rss_url = feed_config.get("rss_url", "")
        logger.info(f"[RSS] 爬取 {feed_config['name']}: {rss_url}")

        resp = self._request(rss_url)
        if resp is None:
            # 尝试 fallback URL（直接爬取网页）
            fallback = feed_config.get("fallback_url")
            if fallback:
                logger.info(f"[RSS] RSS 失败，尝试 fallback: {fallback}")
                return self._crawl_fallback_page(fallback, source_key)
            return []

        articles = self._parse_feed(resp.text, source_key)
        logger.info(f"[RSS] {feed_config['name']}: 获取 {len(articles)} 条")
        return articles

    def _crawl_fallback_page(self, url: str, source_key: str) -> List[Dict]:
        """RSS 失败时直接爬取网页列表页"""
        resp = self._request(url)
        if resp is None:
            return []

        feed_config = self._feeds.get(source_key, {})
        soup = BeautifulSoup(resp.text, "lxml")
        articles = []

        for item in soup.select("article, .post-item, .node-teaser, .views-row"):
            link_tag = item.select_one("a[href]")
            if not link_tag:
                continue

            href = link_tag.get("href", "")
            if not href.startswith("http"):
                href = feed_config.get("website", "") + href

            if not self._is_new_url(href):
                continue

            title = link_tag.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            time_tag = item.select_one("time, .date, .submitted")
            pub_time = ""
            if time_tag:
                pub_time = time_tag.get("datetime", "") or time_tag.get_text(strip=True)

            articles.append({
                "title": title,
                "pub_time": pub_time,
                "content": "",
                "url": href,
                "source": feed_config.get("name", source_key),
                "source_website": feed_config.get("website", ""),
                "language": feed_config.get("language", "en"),
                "crawled_at": datetime.now().isoformat(),
            })

            if len(articles) >= self._max_per_source:
                break

        logger.info(f"[RSS] Fallback {feed_config.get('name', source_key)}: 获取 {len(articles)} 条")
        return articles

    # ============================================================
    # 统一入口
    # ============================================================

    def crawl_all(self, sources: List[str] = None) -> List[Dict]:
        """
        爬取所有启用的 RSS 新闻源

        :param sources: 要爬取的源标识列表，None=全部
        :return: 新闻条目列表
        """
        if sources is None:
            sources = list(self._feeds.keys())

        all_news = []
        for key in sources:
            if key not in self._feeds:
                continue
            # 检查是否启用
            feed_cfg = self._feeds[key]
            if not feed_cfg.get("enabled", True):
                logger.info(f"[RSS] 跳过已禁用源: {key}")
                continue

            try:
                news = self._crawl_single_feed(key)
                all_news.extend(news)
            except Exception as e:
                logger.error(f"[RSS] {key} 爬取失败: {e}")

            time.sleep(1)  # 源间延迟

        self._save_urls_seen()
        logger.info(f"[RSS] 总计获取 {len(all_news)} 条新闻")
        return all_news

    def save_news(self, news_list: List[Dict], filename: str = None) -> Optional[str]:
        """保存 RSS 新闻数据"""
        if not news_list:
            return None

        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"rss_news_{date_str}.json"

        filepath = os.path.join(self._raw_dir, filename)

        # 追加模式
        existing = []
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)

        seen_urls = {item.get("url", "") for item in existing}
        for item in news_list:
            if item.get("url", "") not in seen_urls:
                existing.append(item)
                seen_urls.add(item.get("url", ""))

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(f"[RSS] 已保存 {len(existing)} 条至 {filepath}")
        return filepath


# ============================================================
# 模块级单例
# ============================================================

_rss_crawler_instance = None
_rss_crawler_lock = threading.Lock()


def get_rss_crawler(config: Dict = None) -> RSSNewsCrawler:
    """获取全局 RSS 新闻爬虫单例（线程安全）"""
    global _rss_crawler_instance
    if _rss_crawler_instance is None:
        with _rss_crawler_lock:
            if _rss_crawler_instance is None:
                _rss_crawler_instance = RSSNewsCrawler(config)
    return _rss_crawler_instance
