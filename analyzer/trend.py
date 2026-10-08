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


class LegacyTrendAnalyzer:
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


class TrendAnalyzer(LegacyTrendAnalyzer):
    """trend-v2：按业务日计算，缺测不补值，时间回测先于模型复杂化。"""

    @staticmethod
    def _series(scores, dates=None):
        from utils.data_contract import business_date, finite_number
        if dates is not None:
            if len(scores) != len(dates):
                raise ValueError("日期与评分长度不一致")
            parsed = [business_date(d) for d in dates]
            if any(d is None for d in parsed) or any(a >= b for a, b in zip(parsed, parsed[1:])):
                raise ValueError("日期必须有效、唯一、递增")
            x = np.array([(d - parsed[0]).days for d in parsed], dtype=float)
        else:
            x = np.arange(len(scores), dtype=float)
        valid = np.array([finite_number(v) for v in scores], dtype=bool)
        y = np.array([float(v) if finite_number(v) else np.nan for v in scores])
        span = int(x[-1] - x[0] + 1) if len(x) else 0
        return x, y, valid, span

    def moving_average(self, scores, window=None):
        from utils.data_contract import finite_number
        window = window or self._ma_window
        if window < 1:
            raise ValueError("窗口必须为正整数")
        result = []
        for i in range(window - 1, len(scores)):
            values = [v for v in scores[i - window + 1:i + 1] if finite_number(v)]
            result.append(round(float(np.mean(values)), 4) if len(values) >= np.ceil(window * .8) else None)
        return result

    def linear_regression(self, scores, dates=None):
        x, y, valid, span = self._series(scores, dates)
        count = int(valid.sum())
        if count < 14 or count / max(span, 1) < .8:
            return {"slope": None, "intercept": None, "r_squared": None,
                    "trend": "数据不足", "data_points": count}
        slope, intercept = np.polyfit(x[valid], y[valid], 1)
        residual = y[valid] - (slope * x[valid] + intercept)
        total = np.sum((y[valid] - np.mean(y[valid])) ** 2)
        return {"slope": round(float(slope), 6), "intercept": round(float(intercept), 4),
                "r_squared": round(float(1 - np.sum(residual ** 2) / total), 4) if total > 0 else None,
                "trend": "上升" if slope > .005 else "下降" if slope < -.005 else "平稳",
                "data_points": count, "slope_unit": "风险分/日"}

    def full_analysis(self, scores, dates=None):
        x, y, valid, span = self._series(scores, dates)
        reg = self.linear_regression(scores, dates)
        return {"moving_average": self.moving_average(scores), "regression": reg, "trend": reg["trend"],
                "latest_score": float(y[valid][-1]) if valid.any() else None,
                "avg_score": round(float(np.mean(y[valid])), 4) if valid.any() else None,
                "data_points": int(valid.sum()), "calendar_days": span,
                "coverage": round(int(valid.sum()) / max(span, 1), 4), "algorithm_version": "trend-v2"}

    @staticmethod
    def _predict(model, x, y, target):
        if model == "linear":
            a, b = np.polyfit(x, y, 1)
            values = a * target + b
        elif model == "ewma":
            level = y[0]
            for i in range(1, len(y)):
                alpha = 1 - .7 ** max(1, x[i] - x[i - 1])
                level = alpha * y[i] + (1 - alpha) * level
            values = np.full(len(target), level)
        else:
            values = np.full(len(target), y[-1])
        return np.clip(values, 0, 100)

    def forecast(self, scores, days_ahead=7, dates=None):
        if not isinstance(days_ahead, int) or not 1 <= days_ahead <= 30:
            raise ValueError("预测期须为 1～30 天")
        x, y, valid, span = self._series(scores, dates)
        count = int(valid.sum())
        result = {"forecast": [], "slope": None, "r_squared": None, "backtest": None,
                  "interval": None, "confidence": "未验收", "algorithm_version": "trend-v2"}
        if count < 14 or count / max(span, 1) < .8 or not len(valid) or not valid[-1]:
            return {**result, "status": "insufficient", "reason": "需至少14个有效日、覆盖率≥80%，且截止日有观测"}
        models = ("last", "ewma", "linear")
        errors = {m: [] for m in models}
        folds = 0
        if count >= 56 and span >= 56:
            for cut in range(14, span - 6, 7):
                train = valid & (x < cut)
                test = valid & (x >= cut) & (x < cut + 7)
                if train.sum() < 14 or train.sum() / cut < .8 or test.sum() < 6:
                    continue
                folds += 1
                for model in models:
                    errors[model].extend((y[test] - self._predict(model, x[train], y[train], x[test])).tolist())
        model = "last"
        if folds >= 4:
            metrics = {m: {"mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(np.square(e)))),
                           "samples": len(e)} for m, e in errors.items()}
            model = min(models, key=lambda m: (metrics[m]["mae"], metrics[m]["rmse"]))
            result["backtest"] = {"windows": folds, "horizon_days": 7, "models": metrics,
                                  "selected_model": model, "status": "探索性滚动回测，非独立留出验收"}
        target = x[-1] + np.arange(1, days_ahead + 1)
        prediction = self._predict(model, x[valid], y[valid], target)
        if folds >= 4 and days_ahead <= 7:
            lo, hi = np.quantile(errors[model], [.1, .9])
            result["interval"] = {"lower": np.clip(prediction + lo, 0, 100).round(2).tolist(),
                                  "upper": np.clip(prediction + hi, 0, 100).round(2).tolist(),
                                  "label": "滚动回测残差10%～90%经验区间，非概率保证"}
        reg = self.linear_regression(scores, dates)
        result.update(forecast=prediction.round(2).tolist(), slope=reg["slope"], r_squared=reg["r_squared"],
                      status="exploratory", model=model, confidence="探索性；不以R²表示可靠性",
                      reason="回测不足，保留末值基线" if folds < 4 else "以滚动MAE/RMSE选择模型")
        return result

    def detect_anomalies(self, scores, threshold=3.5):
        from utils.data_contract import finite_number
        anomalies = []
        for i, value in enumerate(scores):
            history = [v for v in scores[max(0, i - 28):i] if finite_number(v)]
            if not finite_number(value) or len(history) < 14:
                continue
            median = float(np.median(history))
            mad = float(np.median(np.abs(np.array(history) - median)))
            scale = max(1.0, 1.4826 * mad)
            deviation = abs(value - median) / scale
            if deviation > threshold:
                anomalies.append({"index": i, "value": value, "deviation": round(deviation, 4),
                                  "type": "peak" if value > median else "trough", "method": "历史窗口中位数/MAD"})
        return anomalies


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
