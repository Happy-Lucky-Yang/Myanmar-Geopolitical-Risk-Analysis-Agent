"""
data.myanmar_now_crawler - 英文新闻爬虫模块
支持多个英文缅甸新闻源：
  - Myanmar Now (myanmar-now.org) — 独立英文媒体
  - The Irrawaddy (irrawaddy.com) — 老牌缅甸英文新闻

输出字段：title, pub_time, content, url, source, language, crawled_at
"""
import os
import re
import json
import time
import random
import logging
import threading
import requests
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


class EnglishNewsCrawler:
    """英文缅甸新闻爬虫"""

    def __init__(self, config: Dict = None):
        self._cfg = config or {}
        self._timeout = self._cfg.get("timeout", 15)
        self._max_retries = self._cfg.get("max_retries", 3)
        self._backoff = self._cfg.get("backoff", 2)
        self._max_pages = self._cfg.get("max_pages", 2)

        # 数据目录
        self._raw_dir = os.path.join("data", "raw")
        os.makedirs(self._raw_dir, exist_ok=True)

        # URL 去重
        self._urls_seen_file = os.path.join(self._raw_dir, "english_urls_seen.txt")
        self._urls_seen = self._load_urls_seen()

    # ============================================================
    # HTTP 请求
    # ============================================================

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random.choice(UA_POOL),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _request(self, url: str) -> Optional[requests.Response]:
        """带重试的 HTTP GET"""
        for attempt in range(self._max_retries):
            try:
                resp = requests.get(
                    url, headers=self._get_headers(),
                    timeout=self._timeout, allow_redirects=True
                )
                if resp.status_code == 200:
                    return resp
                elif resp.status_code in (429, 503):
                    wait = self._backoff * (2 ** attempt)
                    logger.warning(f"[EN-Crawler] HTTP {resp.status_code}, 等待 {wait}s")
                    time.sleep(wait)
                else:
                    logger.warning(f"[EN-Crawler] HTTP {resp.status_code}: {url}")
                    return None
            except requests.RequestException as e:
                logger.warning(f"[EN-Crawler] 请求异常: {e}")
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
    # Myanmar Now
    # ============================================================

    def _crawl_myanmar_now(self) -> List[Dict]:
        """
        爬取 Myanmar Now 最新文章
        站点: https://www.myanmar-now.org/en/news
        """
        articles = []
        base_url = "https://www.myanmar-now.org/en/news"

        for page in range(1, self._max_pages + 1):
            url = base_url if page == 1 else f"{base_url}?page={page}"
            logger.info(f"[MyanmarNow] 爬取列表页: {url}")

            resp = self._request(url)
            if resp is None:
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # 解析文章列表
            for item in soup.select("article, .view-content .views-row, .node-teaser"):
                link_tag = item.select_one("a[href]")
                if not link_tag:
                    continue

                href = link_tag.get("href", "")
                if not href.startswith("http"):
                    href = "https://www.myanmar-now.org" + href

                if not self._is_new_url(href):
                    continue

                title = link_tag.get_text(strip=True)
                if not title:
                    title_tag = item.select_one("h1, h2, h3, .title")
                    title = title_tag.get_text(strip=True) if title_tag else ""

                if not title:
                    continue

                # 尝试获取正文
                content = self._crawl_article_content(href)

                # 发布时间
                time_tag = item.select_one("time, .date, .submitted")
                pub_time = ""
                if time_tag:
                    pub_time = time_tag.get("datetime", "") or time_tag.get_text(strip=True)

                articles.append({
                    "title": title,
                    "pub_time": pub_time,
                    "content": content,
                    "url": href,
                    "source": "Myanmar Now",
                    "source_website": "myanmar-now.org",
                    "language": "en",
                    "crawled_at": datetime.now().isoformat(),
                })

            time.sleep(random.uniform(1, 2))

        logger.info(f"[MyanmarNow] 获取 {len(articles)} 条文章")
        return articles

    def _crawl_article_content(self, url: str) -> str:
        """获取单篇文章正文"""
        resp = self._request(url)
        if resp is None:
            return ""

        soup = BeautifulSoup(resp.text, "lxml")

        # 尝试多种正文选择器
        for selector in [
            "article .field--name-body",
            ".node__content",
            "article .content",
            ".article-body",
            "main article",
            ".field--type-text-long",
        ]:
            body = soup.select_one(selector)
            if body:
                paragraphs = body.find_all("p")
                text = " ".join(p.get_text(strip=True) for p in paragraphs)
                if len(text) > 50:
                    return text[:2000]

        # 降级：获取所有 <p> 标签
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(strip=True) for p in paragraphs)
        return text[:2000]

    # ============================================================
    # The Irrawaddy
    # ============================================================

    def _crawl_irrawaddy(self) -> List[Dict]:
        """
        爬取 The Irrawaddy 最新文章
        站点: https://www.irrawaddy.com/news/burma
        """
        articles = []
        base_url = "https://www.irrawaddy.com/news/burma"

        for page in range(1, self._max_pages + 1):
            url = base_url if page == 1 else f"{base_url}/page/{page}"
            logger.info(f"[Irrawaddy] 爬取列表页: {url}")

            resp = self._request(url)
            if resp is None:
                break

            soup = BeautifulSoup(resp.text, "lxml")

            for item in soup.select("article, .post-item, .td-block-span12"):
                link_tag = item.select_one("a[href]")
                if not link_tag:
                    continue

                href = link_tag.get("href", "")
                if not href.startswith("http"):
                    href = "https://www.irrawaddy.com" + href

                if "/news/burma" not in href and "/news/" not in href:
                    continue

                if not self._is_new_url(href):
                    continue

                title = link_tag.get_text(strip=True)
                if not title:
                    title_tag = item.select_one("h1, h2, h3, .entry-title, .td-module-title a")
                    title = title_tag.get_text(strip=True) if title_tag else ""

                if not title or len(title) < 10:
                    continue

                content = self._crawl_article_content(href)

                time_tag = item.select_one("time, .td-post-date, .date")
                pub_time = ""
                if time_tag:
                    pub_time = time_tag.get("datetime", "") or time_tag.get_text(strip=True)

                articles.append({
                    "title": title,
                    "pub_time": pub_time,
                    "content": content,
                    "url": href,
                    "source": "The Irrawaddy",
                    "source_website": "irrawaddy.com",
                    "language": "en",
                    "crawled_at": datetime.now().isoformat(),
                })

            time.sleep(random.uniform(1, 2))

        logger.info(f"[Irrawaddy] 获取 {len(articles)} 条文章")
        return articles

    # ============================================================
    # 统一入口
    # ============================================================

    def crawl_all(self, sources: List[str] = None) -> List[Dict]:
        """
        爬取所有启用的英文新闻源

        :param sources: 要爬取的源列表，None=全部
        :return: 新闻条目列表
        """
        if sources is None:
            sources = ["myanmar_now", "irrawaddy"]

        all_news = []

        if "myanmar_now" in sources:
            try:
                news = self._crawl_myanmar_now()
                all_news.extend(news)
            except Exception as e:
                logger.error(f"[EN-Crawler] Myanmar Now 爬取失败: {e}")

        if "irrawaddy" in sources:
            try:
                news = self._crawl_irrawaddy()
                all_news.extend(news)
            except Exception as e:
                logger.error(f"[EN-Crawler] Irrawaddy 爬取失败: {e}")

        self._save_urls_seen()
        logger.info(f"[EN-Crawler] 总计获取 {len(all_news)} 条英文新闻")
        return all_news

    def save_news(self, news_list: List[Dict], filename: str = None) -> Optional[str]:
        """保存英文新闻数据"""
        if not news_list:
            return None

        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"myanmar_now_{date_str}.json"

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

        logger.info(f"[EN-Crawler] 已保存 {len(existing)} 条至 {filepath}")
        return filepath


# 模块级单例
_english_crawler_instance = None
_english_crawler_lock = threading.Lock()


def get_english_crawler() -> EnglishNewsCrawler:
    """获取全局英文新闻爬虫单例（线程安全）"""
    global _english_crawler_instance
    if _english_crawler_instance is None:
        with _english_crawler_lock:
            if _english_crawler_instance is None:
                _english_crawler_instance = EnglishNewsCrawler()
    return _english_crawler_instance
