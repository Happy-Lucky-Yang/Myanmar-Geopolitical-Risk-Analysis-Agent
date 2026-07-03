"""
analyzer.sentiment - 情感分析模块
使用 SnowNLP 对新闻文本进行情感打分，输出 0~1 分数
0 = 极度负面，1 = 极度正面
"""
from typing import List, Dict
from snownlp import SnowNLP


class SentimentAnalyzer:
    """基于 SnowNLP 的情感分析器"""

    def analyze(self, text: str) -> float:
        """
        对单条文本进行情感分析

        :param text: 输入文本
        :return: 情感分数 [0, 1]，越高越正面
        """
        if not text or not text.strip():
            return 0.5  # 空文本返回中性分数

        s = SnowNLP(text)
        return round(s.sentiments, 4)

    def analyze_batch(self, texts: List[str]) -> List[float]:
        """
        批量情感分析

        :param texts: 文本列表
        :return: 情感分数列表
        """
        return [self.analyze(text) for text in texts]

    def get_risk_sentiment(self, text: str) -> Dict:
        """
        获取风险情感评估（将情感分转为风险视角）

        :param text: 输入文本
        :return: {
            "sentiment_score": 0.3,
            "risk_level": "high",   # high / medium / low
            "risk_score": 0.7       # 风险分 = 1 - 情感分
        }
        """
        score = self.analyze(text)
        risk_score = round(1.0 - score, 4)

        # TODO: 风险等级阈值可根据实际数据调整
        if risk_score >= 0.7:
            level = "high"
        elif risk_score >= 0.4:
            level = "medium"
        else:
            level = "low"

        return {
            "sentiment_score": score,
            "risk_score": risk_score,
            "risk_level": level
        }

    def aggregate_sentiment(self, scores: List[float]) -> Dict:
        """
        聚合多条文本的情感分数

        :param scores: 情感分数列表
        :return: {
            "mean": 0.45,
            "min": 0.1,
            "max": 0.9,
            "std": 0.2,
            "count": 10
        }
        """
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


def get_sentiment_analyzer() -> SentimentAnalyzer:
    """获取全局情感分析器单例"""
    global _sentiment_instance
    if _sentiment_instance is None:
        _sentiment_instance = SentimentAnalyzer()
    return _sentiment_instance
