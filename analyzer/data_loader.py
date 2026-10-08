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
from pathlib import Path
from uuid import uuid4
from utils.data_contract import (RISK_VERSION, RUN_MODES, article_identity, business_date,
                                 canonical_url, fingerprint, json_safe, select_history,
                                 time_window, timestamp_utc, utc_now, valid_risk_record, source_evidence)
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

    def __init__(self, raw_dir: str = None,
                 processed_dir: str = None,
                 external_dir: str = None):
        # 从统一路径中枢获取（支持通过 DATA_ROOT 外置到项目外私密目录）
        from utils.config import get_data_paths
        _paths = get_data_paths()
        self._raw_dir = raw_dir or _paths["raw"]
        self._processed_dir = processed_dir or _paths["processed"]
        self._external_dir = external_dir or _paths["external"]
        self._write_lock = threading.Lock()  # JSONL 并发写入锁
        from storage.repository import get_repository
        self._repository = get_repository()
        self.last_read_errors = []

        # 确保目录存在
        for d in [self._raw_dir, self._processed_dir, self._external_dir]:
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

        :param date: 发布业务日（YYYY-MM-DD 或 YYYYMMDD），默认加载所有已保存新闻
        :return: 新闻条目列表
        """
        if self._repository is not None:
            return self._repository.load_articles(date=date)
        # 支持的文件前缀
        prefixes = ["myanmar_news_", "gdelt_news_", "myanmar_now_", "rss_news_"]

        if date:
            day = business_date(date)
            if day is None:
                raise ValueError("无效发布日期")
            return [row for row in self.load_raw_news() if isinstance(row, dict) and
                    business_date(row.get("published_at") or row.get("pub_time") or row.get("date")) == day]

        # 加载所有来源的最新文件
        all_news = []
        for prefix in prefixes:
            files = sorted(
                [f for f in os.listdir(self._raw_dir)
                 if f.startswith(prefix) and (f.endswith(".json") or f.endswith(".csv"))],
                reverse=True
            )
            for filename in files:
                filepath = os.path.join(self._raw_dir, filename)
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
        if not isinstance(text, str) or not text:
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
        parsed = business_date(date_str) if date_str else None
        return parsed.isoformat() if parsed else ""

    def deduplicate(self, news_list: List[Dict], key_field: str = "content") -> List[Dict]:
        """基于文本内容哈希去重"""
        seen_hashes: Set[str] = set()
        deduped = []

        by_hash = {}
        for item in news_list:
            if not isinstance(item, dict):
                continue
            text_hash = article_identity(item)
            incoming_urls = item.get("source_urls", [])
            if not isinstance(incoming_urls, list):
                incoming_urls = []
            incoming_urls = sorted(set(filter(None, map(canonical_url, incoming_urls + [item.get("url")]))))
            incoming_sources = item.get("sources", [])
            if not isinstance(incoming_sources, list):
                incoming_sources = []
            incoming_sources = sorted({str(s) for s in incoming_sources + [item.get("source")] if s})
            if text_hash not in seen_hashes:
                seen_hashes.add(text_hash)
                copied = dict(item)
                copied["content_hash"] = text_hash
                copied["source_urls"] = incoming_urls
                copied["sources"] = incoming_sources
                copied["source_evidence"] = source_evidence(item)
                by_hash[text_hash] = copied
                deduped.append(copied)
            else:
                existing = by_hash[text_hash]
                existing["source_urls"] = sorted(set(existing["source_urls"] + incoming_urls))
                existing["sources"] = sorted(set(existing["sources"] + incoming_sources))
                existing["source_evidence"] = source_evidence({
                    "source_evidence": existing["source_evidence"] + source_evidence(item)})
                old_time = existing.get("published_at") or existing.get("pub_time") or existing.get("date")
                new_time = item.get("published_at") or item.get("pub_time") or item.get("date")
                old_day, new_day = business_date(old_time), business_date(new_time)
                old_stamp, new_stamp = timestamp_utc(old_time), timestamp_utc(new_time)
                earlier = (new_day is not None and (old_day is None or new_day < old_day
                           or (new_day == old_day and new_stamp is not None and old_stamp is not None and new_stamp < old_stamp)))
                if earlier:
                    existing["pub_time"] = new_day.isoformat()
                    existing["published_at"] = new_stamp.isoformat() if new_stamp else None
                    existing["date_precision"] = "timestamp" if new_stamp else "day"
                    existing["date_quality"] = "observed"

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
        news_list = [dict(item) for item in news_list if isinstance(item, dict)]
        # 1. 清洗文本
        for item in news_list:
            item["title"] = self.clean_text(item.get("title", ""))
            item["content"] = self.clean_text(item.get("content", ""))
            # 统一日期字段名
            date_val = item.get("published_at") or item.get("pub_time") or item.get("date", "")
            item["pub_time"] = self.normalize_date(date_val)
            stamp = timestamp_utc(date_val)
            item["published_at"] = stamp.isoformat() if stamp else None
            item["date_precision"] = "timestamp" if stamp else "day" if item["pub_time"] else "missing"
            item["date_quality"] = "observed" if item["pub_time"] else "missing"
            item["url"] = canonical_url(item.get("url"))

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

    def save_articles(self, items):
        """流水线统一存储入口；文件模式使用已清洗新闻快照。"""
        if self._repository is not None:
            return self._repository.save_articles(items)
        path = Path(self._raw_dir) / ("myanmar_news_" + fingerprint(items) + ".json")
        with self._write_lock:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(json_safe(items), handle, ensure_ascii=False, allow_nan=False)
        return str(path)

    def save_analysis_result(self, result: Dict, filename: str = None) -> str:
        """保存单条分析结果"""
        if self._repository is not None:
            return self._repository.save_analysis(result)
        if filename is None:
            filename = f"analysis_{uuid4().hex}.json"
        filepath = os.path.join(self._processed_dir, "manual", os.path.basename(filename))
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(json_safe(result), f, ensure_ascii=False, indent=2, allow_nan=False)

        return filepath

    # ============================================================
    # 风险历史数据
    # ============================================================

    def load_risk_history(self, days=30, end_date=None, include_legacy=False,
                          algorithm_version=RISK_VERSION, region="MMR") -> List[Dict]:
        """查询真实日历窗口；默认只读正式日指标，旧快照必须显式选择。"""
        time_window(days, end_date)
        self.last_read_errors = []
        if self._repository is not None:
            return self._repository.load_risk_history(days, end_date, include_legacy, algorithm_version, region)
        filename = "risk_scores.jsonl" if include_legacy else "daily_risk.jsonl"
        records = self._read_risk_file(Path(self._processed_dir) / filename)
        return select_history(records, days, end_date, include_legacy, algorithm_version, region)

    def _read_risk_file(self, path):
        records = []
        if not path.exists():
            return records
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not valid_risk_record(record):
                        raise ValueError("日期/评分/对象格式不合法")
                    records.append(record)
                except (ValueError, TypeError):
                    self.last_read_errors.append({"line": line_no, "file": path.name})
                    logger.warning("[DataLoader] %s 第 %s 行无效，已隔离", path.name, line_no)
        return records

    def revision(self):
        if self._repository is not None:
            return self._repository.revision()
        candidates = set(Path(self._processed_dir).glob('*.json*'))
        for prefix in ('myanmar_news_', 'gdelt_news_', 'myanmar_now_', 'rss_news_'):
            candidates.update(p for p in Path(self._raw_dir).glob(prefix + '*') if p.suffix in {'.json', '.csv'})
        candidates.add(Path(self._external_dir) / 'indicator_observations.jsonl')
        stamps = []
        for path in sorted(candidates):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if path.is_file():
                stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
        return fingerprint(stamps)

    def append_risk_score(self, date, risk_score, risk_level, details=None,
                          run_kind="legacy", algorithm_version=None,
                          region="MMR", sample_count=0, sources=None, input_hash=None):
        """保存分析快照，正式、手工、演示数据物理隔离；保留旧记录不覆盖。"""
        if run_kind not in RUN_MODES:
            raise ValueError("未知运行类型")
        record = {
            "date": date, "risk_score": risk_score, "risk_level": risk_level,
            "details": details or {}, "recorded_at": utc_now().isoformat(),
            "run_kind": run_kind, "algorithm_version": algorithm_version or ("legacy" if run_kind == "legacy" else RISK_VERSION),
            "region": region, "sample_count": sample_count, "sources": sources or [],
            "input_hash": input_hash,
        }
        if not valid_risk_record(record):
            raise ValueError("风险记录必须有有效日期和 0～100 的有限评分")
        if self._repository is not None and run_kind != "demo":
            return self._repository.save_risk(record)
        if run_kind in {"demo", "manual"}:
            path = Path(self._processed_dir) / run_kind / "risk_scores.jsonl"
        else:
            path = Path(self._processed_dir) / ("risk_scores.jsonl" if run_kind == "legacy" else "daily_risk.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            if input_hash and run_kind in {"live", "existing"}:
                previous = select_history(self._read_risk_file(path), 1, date,
                                          algorithm_version=record["algorithm_version"], region=region)
                if previous and previous[-1].get("input_hash") == input_hash:
                    return previous[-1]
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(json_safe(record), ensure_ascii=False, allow_nan=False) + "\n")
        return record


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
