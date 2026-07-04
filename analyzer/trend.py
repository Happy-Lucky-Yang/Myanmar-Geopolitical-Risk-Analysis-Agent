"""
analyzer.trend - 趋势分析模块
提供移动平均、简单线性回归等统计方法，判断风险趋势（上升/下降/平稳）
"""
from typing import List, Dict, Tuple
import logging
import threading
import numpy as np
from utils.config import get_trend_config

logger = logging.getLogger(__name__)


class TrendAnalyzer:
    """风险趋势分析器"""

    def __init__(self):
        cfg = get_trend_config()
        self._ma_window = cfg.get("moving_avg_window", 7)
        self._min_regression_points = cfg.get("min_regression_points", 14)

    def moving_average(self, scores: List[float], window: int = None) -> List[float]:
        """
        计算移动平均

        :param scores: 时间序列风险分列表
        :param window: 窗口大小（天），默认使用配置文件中的值
        :return: 移动平均序列（长度 = len(scores) - window + 1）
        """
        if window is None:
            window = self._ma_window

        if len(scores) < window:
            logger.info(f"[Trend] 数据点({len(scores)})不足窗口({window})，返回空序列")
            return []

        arr = np.array(scores, dtype=float)
        # 使用 cumsum 技巧计算移动平均
        cumsum = np.cumsum(arr)
        cumsum = np.insert(cumsum, 0, 0)
        ma = (cumsum[window:] - cumsum[:-window]) / window

        return [round(float(v), 4) for v in ma]

    def linear_regression(self, scores: List[float]) -> Dict:
        """
        简单线性回归，拟合 y = a*x + b

        :param scores: 时间序列数据
        :return: {
            "slope": 0.02,        # 斜率（每日变化量）
            "intercept": 0.45,    # 截距
            "r_squared": 0.85,    # 拟合优度
            "trend": "上升"       # 上升/下降/平稳
        }
        """
        if len(scores) < self._min_regression_points:
            return {
                "slope": 0.0,
                "intercept": 0.0,
                "r_squared": 0.0,
                "trend": "数据不足",
                "data_points": len(scores)
            }

        x = np.arange(len(scores), dtype=float)
        y = np.array(scores, dtype=float)

        # 最小二乘法：y = a*x + b
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_x2 = np.sum(x ** 2)

        denom = n * sum_x2 - sum_x ** 2
        if denom == 0:
            return {"slope": 0.0, "intercept": float(np.mean(y)),
                    "r_squared": 0.0, "trend": "平稳"}

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # R² 计算
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        # 趋势判断
        # TODO: 阈值可根据实际数据调整
        if slope > 0.005:
            trend = "上升"
        elif slope < -0.005:
            trend = "下降"
        else:
            trend = "平稳"

        return {
            "slope": round(float(slope), 6),
            "intercept": round(float(intercept), 4),
            "r_squared": round(float(r_squared), 4),
            "trend": trend
        }

    def full_analysis(self, scores: List[float]) -> Dict:
        """
        完整趋势分析：移动平均 + 线性回归 + 趋势判断

        :param scores: 时间序列风险分
        :return: {
            "moving_average": [...],
            "regression": {...},
            "trend": "上升",
            "latest_score": 0.65,
            "avg_score": 0.52
        }
        """
        ma = self.moving_average(scores)
        reg = self.linear_regression(scores)

        return {
            "moving_average": ma,
            "regression": reg,
            "trend": reg["trend"],
            "latest_score": round(scores[-1], 4) if scores else 0.0,
            "avg_score": round(float(np.mean(scores)), 4) if scores else 0.0,
            "data_points": len(scores)
        }

    def detect_anomalies(self, scores: List[float], threshold: float = 2.0) -> List[Dict]:
        """
        异常检测：标记偏离均值超过 threshold 个标准差的数据点

        :param scores: 时间序列数据
        :param threshold: 标准差倍数阈值
        :return: 异常点列表 [{"index": 5, "value": 0.9, "deviation": 2.5}, ...]
        """
        if len(scores) < 3:
            return []

        arr = np.array(scores, dtype=float)
        mean = np.mean(arr)
        std = np.std(arr)

        if std == 0:
            return []

        anomalies = []
        for i, val in enumerate(scores):
            z_score = abs(val - mean) / std
            if z_score > threshold:
                anomalies.append({
                    "index": i,
                    "value": round(val, 4),
                    "z_score": round(float(z_score), 4),
                    "type": "peak" if val > mean else "trough"
                })

        return anomalies

    # ============================================================
    # 加权移动平均
    # ============================================================

    def weighted_moving_average(self, scores: List[float], window: int = None) -> List[float]:
        """
        加权移动平均（近期权重更高）

        :param scores: 时间序列
        :param window: 窗口大小
        :return: 加权移动平均序列
        """
        if window is None:
            window = self._ma_window

        if len(scores) < window:
            return []

        arr = np.array(scores, dtype=float)
        # 线性权重：[1, 2, 3, ..., window]
        weights = np.arange(1, window + 1, dtype=float)
        weights /= weights.sum()

        result = []
        for i in range(len(arr) - window + 1):
            segment = arr[i:i + window]
            wma = np.sum(segment * weights)
            result.append(round(float(wma), 4))

        return result

    # ============================================================
    # STL 分解（简化版）
    # ============================================================

    def stl_decompose(self, scores: List[float], period: int = 7) -> Dict:
        """
        简化版 STL 分解：将时间序列分解为趋势、季节性、残差

        注意：这是简化实现，不依赖 statsmodels。
        如需要完整 STL，可使用：pip install statsmodels

        :param scores: 时间序列
        :param period: 周期长度（默认7天）
        :return: {
            "trend": [...],       # 趋势分量
            "seasonal": [...],    # 季节性分量
            "residual": [...],    # 残差
        }
        """
        if len(scores) < period * 2:
            return {
                "trend": scores,
                "seasonal": [0.0] * len(scores),
                "residual": [0.0] * len(scores),
                "note": "数据不足以分解"
            }

        arr = np.array(scores, dtype=float)
        n = len(arr)

        # 1. 趋势：使用中心移动平均
        ma = self.moving_average(scores, window=period)
        # 对齐长度（移动平均会缩短序列）
        offset = (n - len(ma)) // 2
        trend = [None] * n
        for i, v in enumerate(ma):
            trend[offset + i] = v

        # 前向/后向填充 None
        for i in range(n):
            if trend[i] is None:
                trend[i] = trend[i+1] if i+1 < n and trend[i+1] is not None else (trend[i-1] if i > 0 else arr[i])

        trend = np.array(trend, dtype=float)

        # 2. 去趋势
        detrended = arr - trend

        # 3. 季节性：按周期位置取均值
        seasonal = np.zeros(n)
        for pos in range(period):
            indices = list(range(pos, n, period))
            if indices:
                mean_val = np.mean(detrended[indices])
                for idx in indices:
                    seasonal[idx] = mean_val

        # 去均值
        seasonal -= np.mean(seasonal)

        # 4. 残差
        residual = arr - trend - seasonal

        return {
            "trend": [round(float(v), 4) for v in trend],
            "seasonal": [round(float(v), 4) for v in seasonal],
            "residual": [round(float(v), 4) for v in residual],
        }

    # ============================================================
    # 预测外推
    # ============================================================

    def forecast(self, scores: List[float], days_ahead: int = 7) -> Dict:
        """
        基于线性回归的短期预测

        :param scores: 历史风险分序列
        :param days_ahead: 预测天数
        :return: {
            "forecast": [...],    # 预测值
            "slope": 0.05,        # 斜率
            "confidence": "中"    # 置信度
        }
        """
        if len(scores) < 3:
            last_val = scores[-1] if scores else 50.0
            return {
                "forecast": [round(last_val, 2)] * days_ahead,
                "slope": 0.0,
                "confidence": "低"
            }

        # 用最近 N 个点做线性拟合
        window = min(14, len(scores))
        recent = np.array(scores[-window:], dtype=float)
        x = np.arange(window, dtype=float)

        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(recent)
        sum_xy = np.sum(x * recent)
        sum_x2 = np.sum(x ** 2)

        denom = n * sum_x2 - sum_x ** 2
        if denom == 0:
            slope = 0.0
            intercept = float(np.mean(recent))
        else:
            slope = (n * sum_xy - sum_x * sum_y) / denom
            intercept = (sum_y - slope * sum_x) / n

        # R² 计算
        y_pred = slope * x + intercept
        ss_res = np.sum((recent - y_pred) ** 2)
        ss_tot = np.sum((recent - np.mean(recent)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        # 外推
        last_x = window - 1
        forecast_vals = []
        for i in range(1, days_ahead + 1):
            pred = slope * (last_x + i) + intercept
            pred = max(0, min(100, round(float(pred), 2)))
            forecast_vals.append(pred)

        # 置信度
        if r_squared > 0.7:
            confidence = "高"
        elif r_squared > 0.4:
            confidence = "中"
        else:
            confidence = "低"

        return {
            "forecast": forecast_vals,
            "slope": round(float(slope), 6),
            "intercept": round(float(intercept), 4),
            "r_squared": round(float(r_squared), 4),
            "confidence": confidence
        }


# 模块级单例
_trend_instance = None
_trend_lock = threading.Lock()


def get_trend_analyzer() -> TrendAnalyzer:
    """获取全局趋势分析器单例（线程安全）"""
    global _trend_instance
    if _trend_instance is None:
        with _trend_lock:
            if _trend_instance is None:
                _trend_instance = TrendAnalyzer()
    return _trend_instance
