"""
tests.test_crawler - 爬虫模块单元测试
用于验证各新闻源的解析函数是否正确提取字段

运行方式：
  python -m pytest tests/test_crawler.py -v
  或直接运行：
  python tests/test_crawler.py
"""
import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 模拟 HTML 数据（基于缅华网实际页面结构）
# ============================================================

MHWMM_LIST_PAGE_HTML = """
<html>
<body>
<div class="article-list">
    <div class="list-item">
        <h2><a href="https://www.mhwmm.com/miandianxinwen/2026/06/15/article1.html">
            在现2026—27财年内缅甸规划出口玉米130多万吨
        </a></h2>
        <span>2026-06-15</span>
    </div>
    <div class="list-item">
        <h2><a href="https://www.mhwmm.com/miandianxinwen/2026/06/14/article2.html">
            缅甸军方与克钦独立军在掸邦北部发生武装冲突
        </a></h2>
        <span>2026-06-14</span>
    </div>
    <div class="list-item">
        <h2><a href="/miandianxinwen/2026/06/13/article3.html">
            中缅经济走廊新项目启动仪式在内比都举行
        </a></h2>
        <span>2026-06-13</span>
    </div>
</div>
</body>
</html>
"""

MHWMM_ARTICLE_HTML = """
<html>
<body>
<h1>在现2026—27财年内缅甸规划出口玉米130多万吨</h1>
<div class="topcont clearfix">
    <span>发布于2026-06-15 20:01:13</span>
    <span>来源：缅华网</span>
</div>
<div class="beforeart">
    <div class="wzdaodu">
        <div class="ddd">在现在的2026-27财年度12个月内，缅甸规划出口玉米130多万吨...</div>
    </div>
</div>
<div class="article_cont">
    <p style="text-indent:2em;"><span>缅华网  伊江树报道</span></p>
    <p style="text-indent:2em;"><span>据缅甸农业、畜牧与灌溉部农业局消息，在现在的2026-27财年度12个月内，
    缅甸规划出口玉米130多万吨，预计将获得3.9亿美元外汇收入。</span></p>
    <p style="text-indent:2em;"><span>缅甸玉米主要出口到泰国、菲律宾、越南、印尼等东盟国家以及印度。
    2025-26财年，缅甸已出口玉米约110万吨，获得约3.2亿美元。</span></p>
    <p style="text-indent:2em;"><span>缅甸玉米主要产区包括掸邦南部、勃固省、马圭省、实皆省等地。
    近年来，由于边境贸易通道不稳定，部分出口计划受到影响。</span></p>
    <p style="text-indent:2em;"><span>农业局官员表示，政府将继续支持玉米种植户，
    提供优良品种和技术指导，以实现出口目标。</span></p>
</div>
</body>
</html>
"""


# ============================================================
# 测试类
# ============================================================

class TestMhwmmParser(unittest.TestCase):
    """缅华网解析器测试"""

    def setUp(self):
        """初始化爬虫实例（mock 配置避免读取真实文件）"""
        # Mock 配置加载
        with patch('data.crawler.get_crawler_config') as mock_crawler_cfg, \
             patch('data.crawler.get_storage_config') as mock_storage_cfg:

            mock_crawler_cfg.return_value = {
                "sources": [],
                "timeout": 15,
                "max_retries": 1,
                "backoff": 1,
                "max_pages": 1,
                "user_agent": "test-agent"
            }
            mock_storage_cfg.return_value = {
                "news_dir": "./test_data",
                "format": "json"
            }

            from data.crawler import NewsCrawler
            self.crawler = NewsCrawler()

    def test_parse_mhwmm_article_title(self):
        """测试：标题提取"""
        result = self.crawler._parse_mhwmm_article(
            "https://www.mhwmm.com/test.html",
            MHWMM_ARTICLE_HTML
        )
        self.assertEqual(result["title"], "在现2026—27财年内缅甸规划出口玉米130多万吨")

    def test_parse_mhwmm_article_pub_time(self):
        """测试：发布时间提取"""
        result = self.crawler._parse_mhwmm_article(
            "https://www.mhwmm.com/test.html",
            MHWMM_ARTICLE_HTML
        )
        self.assertEqual(result["pub_time"], "2026-06-15 20:01:13")

    def test_parse_mhwmm_article_content(self):
        """测试：正文提取"""
        result = self.crawler._parse_mhwmm_article(
            "https://www.mhwmm.com/test.html",
            MHWMM_ARTICLE_HTML
        )
        content = result["content"]
        self.assertIn("玉米", content)
        self.assertIn("130多万吨", content)
        self.assertIn("掸邦南部", content)
        # 确认来源信息已从正文中移除
        self.assertNotIn("缅华网", content.split("\n")[0] if content else "")

    def test_parse_mhwmm_article_source_author(self):
        """测试：来源和作者提取"""
        result = self.crawler._parse_mhwmm_article(
            "https://www.mhwmm.com/test.html",
            MHWMM_ARTICLE_HTML
        )
        self.assertEqual(result["source_website"], "缅华网")
        self.assertIn("伊江树", result["author"])

    def test_parse_mhwmm_article_summary(self):
        """测试：导读提取"""
        result = self.crawler._parse_mhwmm_article(
            "https://www.mhwmm.com/test.html",
            MHWMM_ARTICLE_HTML
        )
        self.assertIn("2026-27财年", result["summary"])

    def test_parse_mhwmm_article_fields_complete(self):
        """测试：所有必需字段都存在"""
        result = self.crawler._parse_mhwmm_article(
            "https://www.mhwmm.com/test.html",
            MHWMM_ARTICLE_HTML
        )
        required_fields = ["title", "pub_time", "content", "url", "source", "crawled_at"]
        for field in required_fields:
            self.assertIn(field, result, f"缺少字段: {field}")
            self.assertTrue(result[field], f"字段为空: {field}")

    def test_parse_mhwmm_article_language(self):
        """测试：语言标记"""
        result = self.crawler._parse_mhwmm_article(
            "https://www.mhwmm.com/test.html",
            MHWMM_ARTICLE_HTML
        )
        self.assertEqual(result["language"], "zh")


class TestMhwmmListParser(unittest.TestCase):
    """缅华网列表页解析测试"""

    def setUp(self):
        with patch('data.crawler.get_crawler_config') as mock_crawler_cfg, \
             patch('data.crawler.get_storage_config') as mock_storage_cfg:

            mock_crawler_cfg.return_value = {
                "sources": [],
                "timeout": 15,
                "max_retries": 1,
                "backoff": 1,
                "max_pages": 1,
                "user_agent": "test-agent"
            }
            mock_storage_cfg.return_value = {
                "news_dir": "./test_data",
                "format": "json"
            }

            from data.crawler import NewsCrawler
            self.crawler = NewsCrawler()

    @patch('data.crawler.NewsCrawler.retry_request')
    def test_get_article_urls(self, mock_request):
        """测试：从列表页提取文章链接"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = MHWMM_LIST_PAGE_HTML
        mock_resp.apparent_encoding = "utf-8"
        mock_request.return_value = mock_resp

        # 清空已见 URL 集合
        self.crawler._urls_seen = set()

        urls = self.crawler._get_mhwmm_article_urls(
            "https://www.mhwmm.com/miandianxinwen/",
            max_pages=1
        )

        # 应提取到至少 2 个链接
        self.assertGreaterEqual(len(urls), 2)
        # 应包含完整 URL
        for url in urls:
            self.assertTrue(url.startswith("http"), f"URL 不是完整路径: {url}")


class TestRetryRequest(unittest.TestCase):
    """重试机制测试"""

    def setUp(self):
        with patch('data.crawler.get_crawler_config') as mock_crawler_cfg, \
             patch('data.crawler.get_storage_config') as mock_storage_cfg:

            mock_crawler_cfg.return_value = {
                "sources": [],
                "timeout": 5,
                "max_retries": 2,
                "backoff": 0.1,
                "max_pages": 1,
                "user_agent": "test-agent"
            }
            mock_storage_cfg.return_value = {
                "news_dir": "./test_data",
                "format": "json"
            }

            from data.crawler import NewsCrawler
            self.crawler = NewsCrawler()

    @patch('data.crawler.requests.get')
    def test_retry_on_403(self, mock_get):
        """测试：403 时触发重试"""
        # 第一次返回 403，第二次成功
        resp_fail = MagicMock()
        resp_fail.status_code = 403

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.text = "<html>OK</html>"
        resp_ok.apparent_encoding = "utf-8"

        mock_get.side_effect = [resp_fail, resp_ok]

        result = self.crawler.retry_request("http://example.com")
        self.assertIsNotNone(result)
        self.assertEqual(mock_get.call_count, 2)

    @patch('data.crawler.requests.get')
    def test_retry_exhausted(self, mock_get):
        """测试：重试耗尽返回 None"""
        import requests as req
        mock_get.side_effect = req.RequestException("Connection refused")

        result = self.crawler.retry_request("http://example.com")
        self.assertIsNone(result)
        self.assertEqual(mock_get.call_count, 2)  # max_retries=2


# ============================================================
# 手动运行入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  缅甸地缘风险系统 - 爬虫模块测试")
    print("=" * 60)
    unittest.main(verbosity=2)
