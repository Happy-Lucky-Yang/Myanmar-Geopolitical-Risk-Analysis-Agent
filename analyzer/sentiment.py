"""
analyzer.sentiment - 双语情感分析模块
支持中文（SnowNLP）和英文（VADER）文本的情感打分
输出 0~1 分数：0 = 极度负面，1 = 极度正面

数据来源可信度：
  - 中文文本：SnowNLP 朴素贝叶斯模型（标注为 "snownlp"）
  - 英文文本：VADER 词典方法（标注为 "vader"）
  - GDELT 文章：GDELT tone 字段（标注为 "gdelt"，来自 GDELT 官方计算）
"""
import re
import threading
from typing import Dict, List

try:
    from snownlp import SnowNLP
except ImportError:
    SnowNLP = None

# 英文情感：NLTK VADER（词典资源需在环境配置阶段显式安装）
try:
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    _VADER_AVAILABLE = True
except ImportError:
    _VADER_AVAILABLE = False


class SentimentAnalyzer:
    """双语情感分析器，自动检测语言"""

    def __init__(self):
        self._vader = None

    def _ensure_vader_loaded(self):
        """懒加载 VADER"""
        if self._vader is None:
            if not _VADER_AVAILABLE:
                return
            try:
                self._vader = SentimentIntensityAnalyzer()
            except LookupError:
                # 资源下载必须由环境配置显式完成，运行时保持离线可控。
                self._vader = None

    def _detect_language(self, text: str) -> str:
        """简单语言检测：基于中文字符比例"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        total_chars = len(text.strip())
        if total_chars == 0:
            return "zh"
        return "zh" if chinese_chars / total_chars > 0.3 else "en"

    def analyze(self, text: str, lang: str = None) -> float:
        """
        对单条文本进行情感分析（自动语言检测）

        :param text: 输入文本
        :param lang: 强制指定语言（"zh" / "en"），None=自动检测
        :return: 情感分数 [0, 1]，越高越正面
        """
        if not text or not text.strip():
            return 0.5

        if lang is None:
            lang = self._detect_language(text)

        if lang == "en":
            return self._analyze_en(text)
        else:
            return self._analyze_zh(text)

    def _analyze_zh(self, text: str) -> float:
        """中文情感分析：SnowNLP"""
        score, _ = self._analyze_zh_with_source(text)
        return score

    def _analyze_zh_with_source(self, text: str):
        """返回中文得分及真实使用的数据来源。"""
        if SnowNLP is None:
            return self._analyze_zh_fallback(text), "fallback"
        try:
            s = SnowNLP(text)
            return round(s.sentiments, 4), "snownlp"
        except Exception:
            return self._analyze_zh_fallback(text), "fallback"

    def _analyze_zh_fallback(self, text: str) -> float:
        """SnowNLP 不可用时使用透明、可复核的关键词基线。"""
        positive_words = [
            "和平", "合作", "协议", "进展", "增长", "稳定", "改善",
            "援助", "恢复", "成功", "发展", "对话",
        ]
        negative_words = [
            "冲突", "空袭", "爆炸", "难民", "政变", "暴力", "危机",
            "制裁", "动荡", "袭击", "伤亡", "死亡", "破坏", "武装",
        ]
        pos = sum(1 for word in positive_words if word in text)
        neg = sum(1 for word in negative_words if word in text)
        total = pos + neg
        if total == 0:
            return 0.5
        return round(pos / total, 4)

    def _analyze_en(self, text: str) -> float:
        """
        英文情感分析：VADER (Valence Aware Dictionary for sEntiment Reasoning)
        VADER compound 范围 [-1, +1]，归一化到 [0, 1]

        长文档处理：VADER 面向短句设计，整篇长文直接打分 compound 会
        饱和到 ±1.0（表现为情感分恒为 0/1）。按句拆分后取均值，
        得到更细粒度的文档级情感分。
        """
        self._ensure_vader_loaded()

        if self._vader is None:
            # VADER 不可用，降级为关键词匹配
            return self._analyze_en_fallback(text)

        sentences = self._split_sentences(text)
        if not sentences:
            return 0.5
        compounds = [
            self._vader.polarity_scores(s)["compound"]
            for s in sentences[:300]
        ]
        compound = sum(compounds) / len(compounds)
        return round((compound + 1) / 2, 4)

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """按句拆分（英文句号/问号/叹号或换行），过滤过短片段。"""
        parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text.strip())
        return [p.strip() for p in parts if len(p.strip()) >= 3]

    def _analyze_en_fallback(self, text: str) -> float:
        """英文情感降级方案：逐句关键词判断后取均值。

        逐句打分避免长文档中负面词累积导致得分饱和到 0；
        未命中关键词的句子记中性 0.5，使结果更接近真实分布。
        """
        positive_words = [
            "peace", "ceasefire", "cooperation", "agreement", "progress",
            "development", "growth", "stability", "reform", "dialogue",
            "support", "improve", "recovery", "success"
        ]
        negative_words = [
            "conflict", "attack", "airstrike", "killed", "dead", "casualties",
            "sanction", "coup", "protest", "crisis", "refugee", "violence",
            "bombing", "armed", "military", "war", "destruction", "flee",
            "hunger", "famine", "insecurity", "emergency"
        ]
        sentences = self._split_sentences(text) or [text]
        scores = []
        for sent in sentences[:300]:
            sent_lower = sent.lower()
            pos = sum(1 for w in positive_words if w in sent_lower)
            neg = sum(1 for w in negative_words if w in sent_lower)
            if pos + neg == 0:
                scores.append(0.5)
            else:
                scores.append(pos / (pos + neg))
        return round(sum(scores) / len(scores), 4)

    def analyze_batch(self, texts: List[str]) -> List[float]:
        """批量情感分析"""
        return [self.analyze(text) for text in texts]

    def get_risk_sentiment(self, text: str, lang: str = None,
                           gdelt_tone: Dict = None) -> Dict:
        """
        获取风险情感评估（将情感分转为风险视角）

        :param text: 输入文本
        :param lang: 语言（"zh" / "en"），None=自动检测
        :param gdelt_tone: GDELT 预计算的 tone 字典（如有则优先使用）
        :return: {
            "sentiment_score": 0.3,
            "risk_score": 0.7,
            "risk_level": "high",
            "source": "vader" | "snownlp" | "gdelt" | "fallback"
        }
        """
        # 优先使用 GDELT tone（数据来源：GDELT 官方计算）
        if gdelt_tone and gdelt_tone.get("normalized_score") is not None:
            score = gdelt_tone["normalized_score"]
            source = "gdelt"
        else:
            if lang is None:
                lang = self._detect_language(text)
            if lang == "en":
                score = self._analyze_en(text)
                source = "vader" if self._vader else "fallback"
            else:
                score, source = self._analyze_zh_with_source(text)

        risk_score = round(1.0 - score, 4)

        if risk_score >= 0.7:
            level = "high"
        elif risk_score >= 0.4:
            level = "medium"
        else:
            level = "low"

        return {
            "sentiment_score": score,
            "risk_score": risk_score,
            "risk_level": level,
            "source": source
        }

    def aggregate_sentiment(self, scores: List[float]) -> Dict:
        """聚合多条文本的情感分数"""
        import numpy as np

        if not scores:
            return {"mean": 0.5, "min": 0.0, "max": 1.0, "std": 0.0, "count": 0}

        arr = np.array(scores)
        return {
            "mean": round(float(arr.mean()), 4),
            "min": round(float(arr.min()), 4),
            "max": round(float(arr.max()), 4),
            "std": round(float(arr.std()), 4),
            "count": len(scores)
        }


# 模块级单例
_sentiment_instance = None
_sentiment_lock = threading.Lock()


def get_sentiment_analyzer() -> SentimentAnalyzer:
    """获取全局情感分析器单例（线程安全）"""
    global _sentiment_instance
    if _sentiment_instance is None:
        with _sentiment_lock:
            if _sentiment_instance is None:
                _sentiment_instance = SentimentAnalyzer()
    return _sentiment_instance
