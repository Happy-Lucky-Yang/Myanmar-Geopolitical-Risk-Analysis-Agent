"""
data.crawler - 新闻爬虫模块
负责从缅甸相关新闻源定时抓取新闻，保存为结构化数据

支持的新闻源：
  - 缅甸缅华网 (mhwmm.com)  —— 中文，静态HTML
  - 路透社缅甸 (reuters.com) —— 英文
  - 伊洛瓦底报 (irrawaddy.com) —— 英文
  - Myanmar Now (myanmar-now.org) —— 英文/缅甸文

输出字段：title, pub_time, content, url, source, crawled_at
"""
import os
import re
import json
import csv
import time
import random
import logging
import hashlib
import requests
from datetime import datetime
from typing import List, Dict, Set, Optional
from bs4 import BeautifulSoup
from utils.config import get_crawler_config, get_storage_config

# ============================================================
# 日志配置
# ============================================================
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "crawler.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================
# User-Agent 池（避免单 UA 被封）
# ============================================================
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


class NewsCrawler:
    """新闻爬虫：从多个来源抓取缅甸相关新闻"""

    def __init__(self):
        cfg = get_crawler_config()
        self._sources = cfg.get("sources", [])
        self._timeout = cfg.get("timeout", 15)
        self._user_agent = cfg.get("user_agent", UA_POOL[0])
        self._max_retries = cfg.get("max_retries", 3)
        self._backoff = cfg.get("backoff", 2)
        self._max_pages = cfg.get("max_pages", 3)
        self._storage_cfg = get_storage_config()

        # 数据目录
        self._raw_dir = os.path.join("data", "raw")
        os.makedirs(self._raw_dir, exist_ok=True)

        # 已抓取 URL 集合（增量去重）
        self._urls_seen_file = os.path.join(self._raw_dir, "urls_seen.txt")
        self._urls_seen = self._load_urls_seen()

    # ============================================================
    # HTTP 请求（带重试与退避）
    # ============================================================

    def _get_headers(self) -> dict:
        """生成随机请求头"""
        return {
            "User-Agent": random.choice(UA_POOL),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    def retry_request(self, url: str, max_retries: int = None,
                      backoff: int = None) -> Optional[requests.Response]:
        """
        带重试机制的 HTTP GET 请求

        :param url: 目标 URL
        :param max_retries: 最大重试次数
        :param backoff: 退避基数（秒），实际延迟 = backoff * 2^attempt
        :return: Response 对象或 None
        """
        if max_retries is None:
            max_retries = self._max_retries
        if backoff is None:
            backoff = self._backoff

        for attempt in range(max_retries):
            try:
                resp = requests.get(
                    url,
                    headers=self._get_headers(),
                    timeout=self._timeout,
                    allow_redirects=True
                )

                if resp.status_code == 200:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    return resp

                if resp.status_code in (403, 429):
                    wait = backoff * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"HTTP {resp.status_code} - {url}, 等待 {wait:.1f}s 后重试")
                    time.sleep(wait)
                    continue

                logger.error(f"HTTP {resp.status_code} - {url}")
                return None

            except requests.RequestException as e:
                wait = backoff * (2 ** attempt)
                logger.warning(f"请求异常: {e}, 等待 {wait:.1f}s 后重试 ({attempt+1}/{max_retries})")
                time.sleep(wait)

        logger.error(f"请求最终失败: {url}（已重试 {max_retries} 次）")
        return None

    # ============================================================
    # URL 去重
    # ============================================================

    def _load_urls_seen(self) -> Set[str]:
        """加载已抓取 URL 集合"""
        if not os.path.exists(self._urls_seen_file):
            return set()
        with open(self._urls_seen_file, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())

    def _save_urls_seen(self):
        """持久化已抓取 URL 集合"""
        with open(self._urls_seen_file, "w", encoding="utf-8") as f:
            for url in sorted(self._urls_seen):
                f.write(url + "\n")

    def _is_new_url(self, url: str) -> bool:
        """检查 URL 是否为新链接"""
        normalized = url.rstrip("/")
        if normalized in self._urls_seen:
            return False
        self._urls_seen.add(normalized)
        return True

    # ============================================================
    # 缅华网 (mhwmm.com) 专用解析
    # ============================================================

    def _get_mhwmm_article_urls(self, base_url: str, max_pages: int = None) -> List[str]:
        """
        从缅华网列表页提取文章详情页 URL

        :param base_url: 列表页 URL，如 https://www.mhwmm.com/miandianxinwen/
        :param max_pages: 最大翻页数
        :return: 文章 URL 列表
        """
        if max_pages is None:
            max_pages = self._max_pages

        urls = []
        base_url_clean = base_url.rstrip("/")
        for page in range(1, max_pages + 1):
            if page == 1:
                page_url = base_url
            else:
                # 分页格式：https://www.mhwmm.com/miandianxinwen/index_{page}.html
                page_url = f"{base_url_clean}/index_{page}.html"

            logger.info(f"[缅华网] 解析列表页第 {page} 页: {page_url}")
            resp = self.retry_request(page_url)
            if resp is None:
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # 查找文章链接：通常在 .article-list h2 a 或 .news-list a
            # 尝试多种选择器以适配页面变化
            links = []
            for selector in [".article-list h2 a", ".news-list a", "h2 a", ".list-item a"]:
                links = soup.select(selector)
                if links:
                    break

            # 兜底：查找所有包含 /miandianxinwen/ 的 <a> 标签
            if not links:
                links = [
                    a for a in soup.find_all("a", href=True)
                    if "/miandianxinwen/" in a.get("href", "")
                    and a.get("href", "").endswith(".html")
                    and len(a.get_text(strip=True)) > 5
                ]

            for a in links:
                href = a.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.mhwmm.com" + href
                if href and self._is_new_url(href):
                    urls.append(href)

            # 翻页间隔
            time.sleep(random.uniform(1.0, 2.0))

        logger.info(f"[缅华网] 共发现 {len(urls)} 篇新文章")
        return urls

    def _parse_mhwmm_article(self, url: str, html: str) -> Dict:
        """
        解析缅华网单篇文章详情页

        页面结构特征：
        - 标题：<h1> 标签
        - 发布时间：.topcont 内 <span> 包含 "发布于" 字样
        - 正文容器：<div class="article_cont">
        - 正文段落：<p style="text-indent:2em;"> 内的 <span>
        - 作者/来源：正文第一个 <p> 内 "缅华网  伊江树报道"

        :param url: 文章 URL
        :param html: 页面 HTML 文本
        :return: 结构化新闻字典
        """
        soup = BeautifulSoup(html, "lxml")

        # ---- 1. 标题 ----
        # 页面可能有多个 h1（logo 区域也有 h1），需要找到有文字的那个
        title = ""
        for h1_tag in soup.find_all("h1"):
            text = h1_tag.get_text(strip=True)
            if text and len(text) > 3:
                title = text
                break

        # ---- 2. 发布时间 ----
        pub_time = ""
        # 方式一：查找包含"发布于"的 <span>
        time_span = soup.find("span", string=re.compile(r"发布于"))
        if time_span:
            raw = time_span.get_text(strip=True)
            match = re.search(r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})", raw)
            if match:
                pub_time = match.group(1)
            else:
                # 兼容只有日期的情况
                match2 = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
                if match2:
                    pub_time = match2.group(1)

        # 方式二：兜底 - 在 .topcont 内搜索日期
        if not pub_time:
            topcont = soup.find(class_="topcont")
            if topcont:
                match = re.search(r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}(?::\d{2})?)", topcont.get_text())
                if match:
                    pub_time = match.group(1)

        # ---- 3. 正文 ----
        article_div = soup.find("div", class_="article_cont")
        content_paragraphs = []
        source_name = ""
        author = ""

        if article_div:
            # 提取所有正文段落
            for p in article_div.find_all("p"):
                text = p.get_text(" ", strip=True)
                # 跳过空行或极短文本（图片说明等）
                if not text or len(text) < 5:
                    continue
                content_paragraphs.append(text)

            # ---- 4. 从第一个段落提取来源/作者 ----
            if content_paragraphs:
                first_para = content_paragraphs[0]
                # 匹配 "缅华网    伊江树报道" 或类似格式
                m = re.search(r"(缅华网)\s+(.*?报道)", first_para)
                if m:
                    source_name = m.group(1)
                    author = m.group(2)
                    # 从正文中移除来源信息
                    content_paragraphs[0] = re.sub(
                        r"缅华网\s+.*?报道", "", first_para
                    ).strip()
                    if not content_paragraphs[0]:
                        content_paragraphs.pop(0)
        else:
            # 兜底：抓取所有 text-indent:2em 的段落
            for p in soup.select('p[style*="text-indent:2em"]'):
                text = p.get_text(" ", strip=True)
                if text and len(text) >= 10:
                    content_paragraphs.append(text)

        content = "\n".join(content_paragraphs)

        # ---- 5. 文章导读（可选） ----
        summary = ""
        daodu_div = soup.find(class_="wzdaodu")
        if daodu_div:
            ddd = daodu_div.find_next(class_="ddd")
            if ddd:
                summary = ddd.get_text(strip=True)[:200]

        return {
            "title": title,
            "pub_time": pub_time,
            "content": content,
            "url": url,
            "source": "缅甸缅华网",
            "author": author,
            "source_website": source_name,
            "summary": summary,
            "language": "zh",
            "crawled_at": datetime.now().isoformat()
        }

    def _crawl_mhwmm(self, source_url: str) -> List[Dict]:
        """
        完整爬取缅华网流程：列表页 → 文章列表 → 逐篇解析

        :param source_url: 列表页 URL
        :return: 新闻条目列表
        """
        article_urls = self._get_mhwmm_article_urls(source_url)
        news_list = []

        for i, url in enumerate(article_urls):
            logger.info(f"[缅华网] 解析文章 ({i+1}/{len(article_urls)}): {url}")

            resp = self.retry_request(url)
            if resp is None:
                continue

            try:
                article = self._parse_mhwmm_article(url, resp.text)
                if article["title"] and article["content"]:
                    news_list.append(article)
                else:
                    logger.warning(f"[缅华网] 文章内容为空: {url}")
            except Exception as e:
                logger.error(f"[缅华网] 解析失败 {url}: {e}")

            # 请求间隔，避免被封
            time.sleep(random.uniform(1.0, 2.5))

        return news_list

    # ============================================================
    # 路透社 (reuters.com) 解析骨架
    # ============================================================

    def _crawl_reuters(self, source_url: str) -> List[Dict]:
        """
        爬取路透社缅甸版块

        注意：路透社可能有较强的反爬措施，需关注 403/CAPTCHA
        """
        news_list = []
        resp = self.retry_request(source_url)
        if resp is None:
            return news_list

        soup = BeautifulSoup(resp.text, "lxml")

        # 路透社文章列表通常在 [data-testid="MediaStoryCard"] 或 article 标签
        articles = soup.find_all("article") or soup.select("[data-testid*='MediaStoryCard']")

        for article in articles:
            try:
                # 标题
                title_tag = article.find("h3") or article.find("h2") or article.find("a")
                title = title_tag.get_text(strip=True) if title_tag else ""

                # 链接
                link_tag = article.find("a", href=True)
                href = link_tag["href"] if link_tag else ""
                if href and not href.startswith("http"):
                    href = "https://www.reuters.com" + href

                if not href or not self._is_new_url(href):
                    continue

                # 尝试获取文章详情
                time.sleep(random.uniform(1.5, 3.0))
                detail_resp = self.retry_request(href)
                content = ""
                pub_time = ""

                if detail_resp:
                    detail_soup = BeautifulSoup(detail_resp.text, "lxml")

                    # 正文
                    content_div = detail_soup.find("div", class_=re.compile(r"article-body"))
                    if content_div:
                        paragraphs = content_div.find_all("p")
                        content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

                    # 时间
                    time_tag = detail_soup.find("time")
                    if time_tag:
                        pub_time = time_tag.get("datetime", "") or time_tag.get_text(strip=True)

                if title and content:
                    news_list.append({
                        "title": title,
                        "pub_time": pub_time,
                        "content": content,
                        "url": href,
                        "source": "路透社缅甸",
                        "language": "en",
                        "crawled_at": datetime.now().isoformat()
                    })

            except Exception as e:
                logger.error(f"[路透社] 解析条目失败: {e}")
                continue

        return news_list

    # ============================================================
    # 伊洛瓦底报 (irrawaddy.com) 解析骨架
    # ============================================================

    def _crawl_irrawaddy(self, source_url: str) -> List[Dict]:
        """
        爬取伊洛瓦底报

        列表页选择器: h3.entry-title a
        详情页选择器: div.entry-content
        """
        news_list = []
        resp = self.retry_request(source_url)
        if resp is None:
            return news_list

        soup = BeautifulSoup(resp.text, "lxml")

        # 文章列表
        links = soup.select("h3.entry-title a") or soup.select("h2.entry-title a")

        for link in links:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if not href or not self._is_new_url(href):
                continue

            time.sleep(random.uniform(1.0, 2.0))
            detail_resp = self.retry_request(href)
            if detail_resp is None:
                continue

            detail_soup = BeautifulSoup(detail_resp.text, "lxml")

            # 正文
            content_div = detail_soup.find("div", class_="entry-content")
            content = ""
            if content_div:
                paragraphs = content_div.find_all("p")
                content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

            # 发布时间
            pub_time = ""
            time_tag = detail_soup.find("time") or detail_soup.find(class_="entry-date")
            if time_tag:
                pub_time = time_tag.get("datetime", "") or time_tag.get_text(strip=True)

            if title and content:
                news_list.append({
                    "title": title,
                    "pub_time": pub_time,
                    "content": content,
                    "url": href,
                    "source": "伊洛瓦底报",
                    "language": "en",
                    "crawled_at": datetime.now().isoformat()
                })

        return news_list

    # ============================================================
    # 通用调度：根据来源名称路由到对应解析器
    # ============================================================

    def _crawl_source(self, source_name: str, url: str) -> List[Dict]:
        """
        根据来源名称路由到对应的爬虫解析器

        :param source_name: 来源名称（对应 config.yaml 中的 name）
        :param url: 来源 URL
        :return: 新闻条目列表
        """
        # 路由表：来源名称 → 解析函数
        source_handlers = {
            "缅甸缅华网": self._crawl_mhwmm,
            "缅甸中文网": self._crawl_mhwmm,  # 复用同一解析逻辑
            "路透社缅甸": self._crawl_reuters,
            "伊洛瓦底报": self._crawl_irrawaddy,
        }

        handler = source_handlers.get(source_name)
        if handler:
            return handler(url)

        # 未知来源：使用通用解析（降级处理）
        logger.warning(f"[Crawler] 未知来源 '{source_name}'，使用通用解析")
        return self._crawl_generic(url, source_name)

    def _crawl_generic(self, url: str, source_name: str) -> List[Dict]:
        """通用爬虫兜底（适用于未适配的来源）"""
        news_list = []
        resp = self.retry_request(url)
        if resp is None:
            return news_list

        soup = BeautifulSoup(resp.text, "lxml")

        # 尝试常见的文章选择器
        articles = (
            soup.find_all("article")
            or soup.find_all("div", class_="news-item")
            or soup.find_all("div", class_="post")
        )

        for article in articles:
            try:
                title_tag = article.find("h2") or article.find("h3") or article.find("a")
                title = title_tag.get_text(strip=True) if title_tag else ""

                content_tag = article.find("p") or article.find("div", class_="content")
                content = content_tag.get_text(strip=True) if content_tag else ""

                date_tag = article.find("time") or article.find("span", class_="date")
                pub_time = date_tag.get_text(strip=True) if date_tag else ""

                if title and content:
                    news_list.append({
                        "title": title,
                        "pub_time": pub_time,
                        "content": content,
                        "url": url,
                        "source": source_name,
                        "language": "unknown",
                        "crawled_at": datetime.now().isoformat()
                    })
            except Exception as e:
                logger.error(f"[通用] 解析条目失败: {e}")

        return news_list

    # ============================================================
    # 主流程
    # ============================================================

    def crawl_all_sources(self) -> List[Dict]:
        """
        爬取所有启用的新闻源

        :return: 新闻条目列表
        """
        all_news = []

        for source in self._sources:
            if not source.get("enabled", True):
                continue

            name = source["name"]
            url = source["url"]
            logger.info(f"[Crawler] ====== 开始爬取: {name} ({url}) ======")

            try:
                news_list = self._crawl_source(name, url)
                all_news.extend(news_list)
                logger.info(f"[Crawler] {name}: 获取 {len(news_list)} 条新文章")
            except Exception as e:
                logger.error(f"[Crawler] {name} 爬取异常: {e}", exc_info=True)

            # 来源间间隔
            time.sleep(random.uniform(2.0, 4.0))

        # 保存已见 URL
        self._save_urls_seen()

        logger.info(f"[Crawler] ====== 本轮总计获取 {len(all_news)} 条新闻 ======")
        return all_news

    # ============================================================
    # 数据保存
    # ============================================================

    def save_news(self, news_list: List[Dict], filename: str = None) -> Optional[str]:
        """
        将新闻数据保存到 data/raw/ 目录

        :param news_list: 新闻条目列表
        :param filename: 文件名，默认按日期生成
        :return: 保存的文件路径
        """
        if not news_list:
            logger.info("[Crawler] 无数据可保存")
            return None

        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            fmt = self._storage_cfg.get("format", "json")
            filename = f"myanmar_news_{date_str}.{fmt}"

        filepath = os.path.join(self._raw_dir, filename)

        if filepath.endswith(".json"):
            # JSON 格式（追加模式：如当天已有文件则合并）
            existing = []
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)

            # 合并并去重（基于 URL）
            seen_urls = {item["url"] for item in existing}
            for item in news_list:
                if item["url"] not in seen_urls:
                    existing.append(item)
                    seen_urls.add(item["url"])

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

            logger.info(f"[Crawler] 已保存 {len(existing)} 条新闻至 {filepath}")
        else:
            # CSV 格式
            keys = ["title", "pub_time", "content", "url", "source", "crawled_at"]
            file_exists = os.path.exists(filepath)

            with open(filepath, "a" if file_exists else "w",
                      encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                if not file_exists:
                    writer.writeheader()
                writer.writerows(news_list)

            logger.info(f"[Crawler] 已追加 {len(news_list)} 条新闻至 {filepath}")

        return filepath

    # ============================================================
    # 便捷方法：单独测试某篇文章
    # ============================================================

    def test_single_article(self, url: str) -> Dict:
        """
        测试单篇文章的解析结果（调试用）

        :param url: 文章 URL
        :return: 解析结果字典
        """
        resp = self.retry_request(url)
        if resp is None:
            return {"error": f"请求失败: {url}"}

        if "mhwmm" in url:
            return self._parse_mhwmm_article(url, resp.text)

        return {"error": f"未适配的来源: {url}"}


# ============================================================
# 独立运行入口
# ============================================================

if __name__ == "__main__":
    crawler = NewsCrawler()

    # 可通过命令行参数指定模式
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # 测试单篇文章
        test_url = sys.argv[2] if len(sys.argv) > 2 else "https://www.mhwmm.com/miandianxinwen/"
        result = crawler.test_single_article(test_url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 单次爬取
        news = crawler.crawl_all_sources()
        crawler.save_news(news)
        print(f"爬取完成，共 {len(news)} 条新闻")
