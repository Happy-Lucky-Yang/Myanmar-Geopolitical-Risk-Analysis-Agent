"""UN News 中文适配：仅采集有真实发布日期的单篇新闻，不把专题背景当今日新闻。"""
import re
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from utils.data_contract import business_date, canonical_url, utc_now

ENTRY = 'https://news.un.org/zh/focus/miandian'
SOURCE = 'UN News 中文'
AGENT = 'MyanmarResearchBot/1.0'
ARTICLE = re.compile(r'^/zh/story/\d{4}/\d{2}/\d+/?$')


def allowed_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.hostname == 'news.un.org' and p.port in (None, 443)
                and not p.username and not p.password and '\\' not in url)
    except ValueError:
        return False


def article_links(html, base=ENTRY):
    soup = BeautifulSoup(html, 'html.parser')
    return sorted({canonical_url(urljoin(base, a['href'])) for a in soup.select('a[href]')
                   if allowed_url(urljoin(base, a['href'])) and ARTICLE.fullmatch(urlsplit(urljoin(base, a['href'])).path)})


def parse_article(html, url):
    if not allowed_url(url) or not ARTICLE.fullmatch(urlsplit(url).path):
        raise ValueError('仅支持 UN News 中文单篇新闻')
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script, style, nav, footer'):
        node.decompose()
    title = soup.select_one('h1')
    body = (soup.select_one('#block-views-block-content-fields-block-body .field--name-field-text-column')
            or soup.select_one('.field--name-body') or soup.select_one('[itemprop="articleBody"]'))
    displayed_stamp = soup.select_one('.views-field-field-news-date time[datetime]')
    metadata_stamp = soup.select_one('meta[property="article:published_time"]')
    stamp = displayed_stamp or metadata_stamp or soup.select_one('time[datetime]')
    raw_date = (stamp.get('content') or stamp.get('datetime')) if stamp else None
    metadata_date = metadata_stamp.get('content') if metadata_stamp else None
    day = business_date(raw_date)
    if day is None or day > business_date():
        raise ValueError('发布日期缺失、无效或在未来：待人工核验')
    if not title or not body:
        raise ValueError('正文结构变化：未找到题名或正文')
    title_text = title.get_text(' ', strip=True)
    text = body.get_text(' ', strip=True)
    if not any(word in title_text + text for word in ('缅甸', '罗兴亚')):
        return None
    if len(text) < 40:
        raise ValueError('正文过短：不把摘要冒充全文')
    url = canonical_url(url)
    return {'title': title_text, 'content': text, 'url': url, 'source': SOURCE,
            'language': 'zh', 'pub_time': day.isoformat(), 'published_at': raw_date,
            'crawled_at': utc_now().isoformat(), 'run_kind': 'live', 'content_quality': 'fulltext',
            'publication_metadata': metadata_date,
            'publication_date_conflict': bool(metadata_date and business_date(metadata_date) != day),
            'date_quality': 'observed', 'source_evidence': [{'source': SOURCE, 'url': url}]}


class UNNewsCrawler:
    def __init__(self, session=None, sleep=time.sleep, interval=2):
        self.session = session or requests.Session()
        self.sleep = sleep
        self.interval = max(2, interval)
        self.robots = None
        self.review_required = []
        self.failures = []

    def _get(self, url, robots=False):
        from utils.config import get_proxy
        for redirect in range(4):
            if not allowed_url(url):
                raise ValueError('不允许跨站或非 HTTPS 请求')
            if not robots and (self.robots is None or not self.robots.can_fetch(AGENT, url)):
                raise ValueError('站点访问规则未允许抓取')
            for attempt in range(3):
                self.sleep(self.interval if attempt == 0 else min(30, 2 ** (attempt + 1)))
                with self.session.get(url, timeout=20, allow_redirects=False, stream=True,
                                      proxies=get_proxy() or {}, headers={'User-Agent': AGENT}) as response:
                    if response.status_code in (429, 503):
                        if attempt == 2:
                            raise RuntimeError('UN News 限流或暂不可用')
                        continue
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get('Location', ''))
                        break
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 4 * 1024 * 1024:
                            raise ValueError('响应过大')
                        chunks.append(chunk)
                    return b''.join(chunks).decode('utf-8')
            else:
                raise RuntimeError('重试次数耗尽')
        raise ValueError('重定向过多')

    def crawl(self, known_urls=(), limit=20):
        self.review_required, self.failures = [], []
        rules = self._get('https://news.un.org/robots.txt', robots=True)
        self.robots = RobotFileParser()
        self.robots.parse(rules.splitlines())
        links = article_links(self._get(ENTRY))
        if not links:
            raise ValueError('专题页没有可验证的单篇新闻入口，需核验页面结构')
        known = {canonical_url(u) for u in known_urls}
        selected = [u for u in links if u not in known][:min(20, max(0, limit))]
        rows = []
        for url in selected:
            try:
                row = parse_article(self._get(url), url)
                if row:
                    rows.append(row)
            except ValueError:
                self.review_required.append(url)
            except (requests.RequestException, RuntimeError):
                self.failures.append(url)
        return rows


def collect_un_news(loader):
    """仅由显式 live 流水线调用；先保存后记录成功，不维护提前推进的去重水位。"""
    from data.source_health import get_source_health_tracker
    tracker = get_source_health_tracker()
    crawler = UNNewsCrawler()
    try:
        known = [row.get('url', '') for row in loader.load_raw_news() if isinstance(row, dict)]
        rows = crawler.crawl(known)
        if rows:
            loader.save_articles(rows)
        issue = f'待核验{len(crawler.review_required)}；失败{len(crawler.failures)}'
        tracker.record(SOURCE, not (crawler.review_required or crawler.failures), len(rows),
                       issue if crawler.review_required or crawler.failures else None)
        return rows
    except Exception as exc:
        tracker.record(SOURCE, False, 0, type(exc).__name__)
        raise
