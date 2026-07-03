"""
data.preprocessor - 文本预处理模块
负责新闻文本清洗、去重、日期格式化
"""
import re
import hashlib
from datetime import datetime
from typing import List, Dict, Set


class TextPreprocessor:
    """文本预处理器"""

    # 常见噪声模式
    _AD_PATTERNS = [
        r"点击关注.*",
        r"扫描二维码.*",
        r"版权声明.*",
        r"转载须.*",
        r"责任编辑.*",
    ]

    # 日期格式映射
    _DATE_FORMATS = [
        "%Y-%m-%d",
        "%Y年%m月%d日",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]

    def clean_text(self, text: str) -> str:
        """
        清洗单条文本

        :param text: 原始文本
        :return: 清洗后的文本
        """
        if not text:
            return ""

        # 1. 去除 HTML 残留标签
        text = re.sub(r"<[^>]+>", "", text)

        # 2. 去除特殊空白字符
        text = re.sub(r"[\r\t\xa0]+", " ", text)

        # 3. 合并多余空格
        text = re.sub(r"\s+", " ", text).strip()

        # 4. 去除广告/版权声明等噪声
        for pattern in self._AD_PATTERNS:
            text = re.sub(pattern, "", text)

        # 5. 去除 URL
        text = re.sub(r"https?://\S+", "", text)

        return text.strip()

    def deduplicate(self, news_list: List[Dict], key_field: str = "content") -> List[Dict]:
        """
        基于文本内容哈希去重

        :param news_list: 新闻条目列表
        :param key_field: 用于去重的字段名
        :return: 去重后的列表
        """
        seen_hashes: Set[str] = set()
        deduped = []

        for item in news_list:
            text = item.get(key_field, "")
            # 使用 MD5 哈希作为指纹
            text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()

            if text_hash not in seen_hashes:
                seen_hashes.add(text_hash)
                deduped.append(item)

        removed = len(news_list) - len(deduped)
        if removed > 0:
            print(f"[Preprocessor] 去重: 移除 {removed} 条重复新闻")

        return deduped

    def normalize_date(self, date_str: str) -> str:
        """
        将各种日期格式统一为 YYYY-MM-DD

        :param date_str: 原始日期字符串
        :return: 格式化后的日期字符串，解析失败返回原字符串
        """
        if not date_str:
            return datetime.now().strftime("%Y-%m-%d")

        date_str = date_str.strip()

        for fmt in self._DATE_FORMATS:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # TODO: 如果常见格式都不匹配，可以尝试 dateutil.parser
        print(f"[Preprocessor] 日期解析失败: '{date_str}'，使用当前日期")
        return datetime.now().strftime("%Y-%m-%d")

    def preprocess_batch(self, news_list: List[Dict]) -> List[Dict]:
        """
        批量预处理新闻

        :param news_list: 原始新闻列表
        :return: 预处理后的新闻列表
        """
        # 1. 清洗文本
        for item in news_list:
            item["title"] = self.clean_text(item.get("title", ""))
            item["content"] = self.clean_text(item.get("content", ""))
            item["date"] = self.normalize_date(item.get("date", ""))

        # 2. 去重
        news_list = self.deduplicate(news_list)

        # 3. 过滤空内容
        news_list = [
            item for item in news_list
            if item.get("content") or item.get("title")
        ]

        print(f"[Preprocessor] 预处理完成，剩余 {len(news_list)} 条新闻")
        return news_list


# 模块级单例
_preprocessor_instance = None


def get_preprocessor() -> TextPreprocessor:
    """获取全局预处理器单例"""
    global _preprocessor_instance
    if _preprocessor_instance is None:
        _preprocessor_instance = TextPreprocessor()
    return _preprocessor_instance
