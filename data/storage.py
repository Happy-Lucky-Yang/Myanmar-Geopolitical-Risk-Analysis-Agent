"""
data.storage - 数据存储模块
负责新闻数据和分析结果的持久化（CSV/JSON）
"""
import os
import json
import csv
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional
from utils.config import get_storage_config


class DataStorage:
    """数据存储器，管理新闻数据和分析结果的读写"""

    def __init__(self):
        cfg = get_storage_config()
        self._news_dir = cfg.get("news_dir", "./news_data")
        self._format = cfg.get("format", "csv")
        os.makedirs(self._news_dir, exist_ok=True)

    def save_news(self, news_list: List[Dict], filename: str = None) -> str:
        """
        保存新闻数据

        :param news_list: 新闻条目列表
        :param filename: 文件名（可选）
        :return: 保存的文件路径
        """
        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"myanmar_news_{date_str}.{self._format}"

        filepath = os.path.join(self._news_dir, filename)

        if self._format == "json" or filename.endswith(".json"):
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(news_list, f, ensure_ascii=False, indent=2)
        else:
            df = pd.DataFrame(news_list)
            df.to_csv(filepath, index=False, encoding="utf-8-sig")

        print(f"[Storage] 已保存 {len(news_list)} 条数据至 {filepath}")
        return filepath

    def load_news(self, filename: str = None, date: str = None) -> List[Dict]:
        """
        加载新闻数据

        :param filename: 指定文件名
        :param date: 指定日期（YYYY-MM-DD 或 YYYYMMDD）
        :return: 新闻条目列表
        """
        if filename is None and date is not None:
            date_clean = date.replace("-", "")
            # 尝试 CSV 和 JSON
            for ext in ["csv", "json"]:
                candidate = f"myanmar_news_{date_clean}.{ext}"
                candidate_path = os.path.join(self._news_dir, candidate)
                if os.path.exists(candidate_path):
                    filename = candidate
                    break

        if filename is None:
            # 加载最新的文件
            files = sorted(
                [f for f in os.listdir(self._news_dir)
                 if f.startswith("myanmar_news_") and (f.endswith(".csv") or f.endswith(".json"))],
                reverse=True
            )
            if not files:
                return []
            filename = files[0]

        filepath = os.path.join(self._news_dir, filename)

        if not os.path.exists(filepath):
            print(f"[Storage] 文件不存在: {filepath}")
            return []

        if filepath.endswith(".json"):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            df = pd.read_csv(filepath, encoding="utf-8-sig")
            return df.to_dict("records")

    def save_analysis_result(self, result: Dict, filename: str = None) -> str:
        """
        保存单条分析结果

        :param result: 分析结果字典
        :param filename: 文件名
        :return: 文件路径
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"analysis_{timestamp}.json"

        filepath = os.path.join(self._news_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return filepath

    def load_risk_history(self, days: int = 30) -> List[Dict]:
        """
        加载历史风险评分数据

        :param days: 加载最近多少天的数据
        :return: 风险评分历史列表
        """
        # TODO: 实现从文件中加载历史风险评分
        # 可以考虑使用一个专门的 risk_scores.jsonl 文件
        history_file = os.path.join(self._news_dir, "risk_scores.jsonl")

        if not os.path.exists(history_file):
            return []

        records = []
        with open(history_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        # 只返回最近 days 天的数据
        return records[-days:] if records else []

    def append_risk_score(self, date: str, risk_score: float, risk_level: str,
                          details: Dict = None):
        """
        追加一条风险评分记录到历史文件

        :param date: 日期
        :param risk_score: 风险分
        :param risk_level: 风险等级
        :param details: 详细信息
        """
        history_file = os.path.join(self._news_dir, "risk_scores.jsonl")

        record = {
            "date": date,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "details": details or {},
            "recorded_at": datetime.now().isoformat()
        }

        with open(history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# 模块级单例
_storage_instance = None


def get_storage() -> DataStorage:
    """获取全局数据存储器单例"""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = DataStorage()
    return _storage_instance
