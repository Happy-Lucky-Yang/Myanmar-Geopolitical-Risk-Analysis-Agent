"""
analyzer.data_loader - 数据读取与清洗模块
负责从 data/ 目录加载原始数据，执行预处理，输出结构化数据
"""
import os
import json
import hashlib
import re
import logging
import threading
import pandas as pd
from datetime import datetime
from typing import List, Dict, Set, Optional


logger = logging.getLogger(__name__)


class DataLoader:
    """数据加载与清洗器"""

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

    def __init__(self, raw_dir: str = "./data/raw",
                 processed_dir: str = "./data/processed",
                 external_dir: str = "./data/external"):
        self._raw_dir = raw_dir
        self._processed_dir = processed_dir
        self._external_dir = external_dir
        self._write_lock = threading.Lock()  # JSONL 并发写入锁

        # 确保目录存在
        for d in [raw_dir, processed_dir, external_dir]:
            os.makedirs(d, exist_ok=True)

    # ============================================================
    # 数据加载
    # ============================================================

    def load_csv(self, filepath: str) -> List[Dict]:
        """加载 CSV 文件"""
        try:
            df = pd.read_csv(filepath, encoding="utf-8-sig")
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"[DataLoader] CSV 加载失败 {filepath}: {e}")
            return []

    def load_json(self, filepath: str) -> List[Dict]:
        """加载 JSON 文件"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else [data]
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[DataLoader] JSON 加载失败 {filepath}: {e}")
            return []

    def load_raw_news(self, date: str = None) -> List[Dict]:
        """
        加载原始新闻数据（合并所有数据源：缅华网 + GDELT + Myanmar Now）

        :param date: 日期（YYYYMMDD），默认加载最新文件
        :return: 新闻条目列表
        """
        # 支持的文件前缀
        prefixes = ["myanmar_news_", "gdelt_news_", "myanmar_now_", "rss_news_"]

        if date:
            all_news = []
            for prefix in prefixes:
                for ext in ["json", "csv"]:
                    path = os.path.join(self._raw_dir, f"{prefix}{date}.{ext}")
                    if os.path.exists(path):
                        data = self.load_json(path) if ext == "json" else self.load_csv(path)
                        all_news.extend(data)
            return all_news

        # 加载所有来源的最新文件
        all_news = []
        for prefix in prefixes:
            files = sorted(
                [f for f in os.listdir(self._raw_dir)
                 if f.startswith(prefix) and (f.endswith(".json") or f.endswith(".csv"))],
                reverse=True
            )
            if files:
                filepath = os.path.join(self._raw_dir, files[0])
                data = self.load_json(filepath) if filepath.endswith(".json") else self.load_csv(filepath)
                all_news.extend(data)

        return all_news

    def load_external_data(self, filename: str) -> List[Dict]:
        """加载外部数据（遥感指数、统计公报等）"""
        filepath = os.path.join(self._external_dir, filename)
        if not os.path.exists(filepath):
            return []
        if filepath.endswith(".json"):
            return self.load_json(filepath)
        else:
            return self.load_csv(filepath)

    # ============================================================
    # 文本清洗
    # ============================================================

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

    def normalize_date(self, date_str: str) -> str:
        """将各种日期格式统一为 YYYY-MM-DD"""
        if not date_str:
            return datetime.now().strftime("%Y-%m-%d")

        date_str = date_str.strip()
        for fmt in self._DATE_FORMATS:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        return datetime.now().strftime("%Y-%m-%d")

    def deduplicate(self, news_list: List[Dict], key_field: str = "content") -> List[Dict]:
        """基于文本内容哈希去重"""
        seen_hashes: Set[str] = set()
        deduped = []

        for item in news_list:
            text = item.get(key_field, "")
            text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
            if text_hash not in seen_hashes:
                seen_hashes.add(text_hash)
                deduped.append(item)

        removed = len(news_list) - len(deduped)
        if removed > 0:
            logger.info(f"[DataLoader] 去重: 移除 {removed} 条重复新闻")

        return deduped

    # ============================================================
    # 预处理流水线
    # ============================================================

    def preprocess(self, news_list: List[Dict]) -> List[Dict]:
        """
        批量预处理新闻：清洗 + 去重 + 日期格式化 + 过滤

        :param news_list: 原始新闻列表
        :return: 预处理后的新闻列表
        """
        # 1. 清洗文本
        for item in news_list:
            item["title"] = self.clean_text(item.get("title", ""))
            item["content"] = self.clean_text(item.get("content", ""))
            # 统一日期字段名
            date_val = item.get("pub_time") or item.get("date", "")
            item["pub_time"] = self.normalize_date(date_val)

        # 2. 去重
        news_list = self.deduplicate(news_list)

        # 3. 过滤空内容
        news_list = [
            item for item in news_list
            if item.get("content") or item.get("title")
        ]

        logger.info(f"[DataLoader] 预处理完成，剩余 {len(news_list)} 条新闻")
        return news_list

    # ============================================================
    # 保存处理结果
    # ============================================================

    def save_processed(self, news_list: List[Dict], filename: str = None) -> str:
        """保存预处理后的数据到 processed 目录"""
        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"processed_{date_str}.csv"

        filepath = os.path.join(self._processed_dir, filename)
        df = pd.DataFrame(news_list)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")

        logger.info(f"[DataLoader] 已保存至 {filepath}")
        return filepath

    def save_analysis_result(self, result: Dict, filename: str = None) -> str:
        """保存单条分析结果"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"analysis_{timestamp}.json"

        filepath = os.path.join(self._processed_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return filepath

    # ============================================================
    # 风险历史数据
    # ============================================================

    def load_risk_history(self, days: int = 30) -> List[Dict]:
        """
        加载历史风险评分数据
        容错处理：跳过损坏行并记录 warning，不会因单行损坏而崩溃
        """
        history_file = os.path.join(self._processed_dir, "risk_scores.jsonl")
        if not os.path.exists(history_file):
            return []

        records = []
        bad_lines = 0
        with open(history_file, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    bad_lines += 1
                    logger.warning(
                        f"[DataLoader] risk_scores.jsonl 第 {line_no} 行损坏，已跳过"
                    )

        if bad_lines > 0:
            logger.warning(
                f"[DataLoader] risk_scores.jsonl 共 {bad_lines} 行损坏"
            )

        return records[-days:] if records else []

    def append_risk_score(self, date: str, risk_score: float, risk_level: str,
                          details: Dict = None):
        """追加一条风险评分记录（线程安全，使用写入锁）"""
        history_file = os.path.join(self._processed_dir, "risk_scores.jsonl")

        record = {
            "date": date,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "details": details or {},
            "recorded_at": datetime.now().isoformat()
        }

        with self._write_lock:
            with open(history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


# 模块级单例
_loader_instance = None
_loader_lock = threading.Lock()


def get_data_loader() -> DataLoader:
    """获取全局数据加载器单例（线程安全）"""
    global _loader_instance
    if _loader_instance is None:
        with _loader_lock:
            if _loader_instance is None:
                _loader_instance = DataLoader()
    return _loader_instance
