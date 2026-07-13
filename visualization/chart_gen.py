"""
visualization.chart_gen - 图表生成模块
使用 pyecharts 生成趋势折线图数据（返回 JSON 供前端渲染）
"""
import threading
from typing import List, Dict, Optional


class TrendChartGenerator:
    """趋势图表数据生成器"""

    def generate_trend_data(self, dates: List[str], scores: List[float],
                            moving_avg: List[float] = None,
                            forecast: List[float] = None,
                            forecast_meta: Dict = None,
                            threshold_lines: List[Dict] = None,
                            event_markers: List[Dict] = None,
                            title: str = "缅甸地缘风险趋势") -> Dict:
        """
        生成趋势折线图数据（JSON 格式，供前端 ECharts 渲染）

        :param dates: 日期列表 ["2026-01-01", "2026-01-02", ...]
        :param scores: 风险分列表 [0.5, 0.6, ...]
        :param moving_avg: 移动平均序列（可选）
        :param forecast: 预测序列（可选）
        :param forecast_meta: 预测元数据（可选）
        :param threshold_lines: 预警阈值线（可选）
        :param event_markers: 历史事件标注（可选）
        :param title: 图表标题
        :return: 图表数据字典
        """
        chart_data = {
            "title": title,
            "xAxis": dates,
            "series": [
                {
                    "name": "风险分",
                    "type": "line",
                    "data": [round(s, 4) for s in scores],
                    "smooth": True,
                    "lineStyle": {"width": 2},
                    "areaStyle": {"opacity": 0.1}
                }
            ],
            "yAxis": {
                "name": "风险分（0-100）",
                "min": 0,
                "max": 100
            }
        }

        # 添加移动平均线
        if moving_avg and len(moving_avg) > 0:
            # 移动平均序列比原始数据短，需要对齐
            offset = len(scores) - len(moving_avg)
            ma_padded = [None] * offset + [round(v, 4) for v in moving_avg]

            chart_data["series"].append({
                "name": f"移动平均",
                "type": "line",
                "data": ma_padded,
                "smooth": True,
                "lineStyle": {"width": 2, "type": "dashed"},
                "symbol": "none"
            })

        # 添加预测序列 (虚线 + 置信区间阴影)
        if forecast and len(forecast) > 0:
            # 预测日期扩展
            from datetime import datetime, timedelta
            if dates:
                try:
                    last_date = datetime.strptime(dates[-1], "%Y-%m-%d")
                    forecast_dates = [(last_date + timedelta(days=i+1)).strftime("%Y-%m-%d")
                                     for i in range(len(forecast))]
                    chart_data["xAxis"] = dates + forecast_dates
                except Exception:
                    chart_data["xAxis"] = dates + [f"D+{i+1}" for i in range(len(forecast))]

            # 实际数据填充 None 占位
            actual_padded = [round(s, 4) for s in scores] + [None] * len(forecast)
            chart_data["series"][0]["data"] = actual_padded

            # 预测序列
            forecast_padded = [None] * len(scores) + [round(f, 4) for f in forecast]
            # 连接点: 预测序列的第一个点 = 实际数据的最后一个点
            if scores:
                forecast_padded[len(scores) - 1] = round(scores[-1], 4)

            chart_data["series"].append({
                "name": "预测",
                "type": "line",
                "data": forecast_padded,
                "smooth": True,
                "lineStyle": {"width": 2, "type": "dashed"},
                "itemStyle": {"color": "#d29922"},
                "symbol": "none"
            })

            chart_data["forecast_meta"] = forecast_meta or {}

        # 添加预警阈值参考线 (markLine)
        if threshold_lines:
            mark_lines = [
                {"yAxis": line["yAxis"], "name": line["name"],
                 "lineStyle": {"color": line["color"], "type": "dotted", "width": 1}}
                for line in threshold_lines
            ]
            chart_data["series"][0].setdefault("markLine", {})
            chart_data["series"][0]["markLine"]["data"] = mark_lines
            chart_data["series"][0]["markLine"]["silent"] = True

        # 添加历史事件标注 (markPoint)
        if event_markers:
            chart_data["series"][0].setdefault("markPoint", {})
            chart_data["series"][0]["markPoint"]["data"] = event_markers[:10]
            chart_data["series"][0]["markPoint"]["symbol"] = "triangle"
            chart_data["series"][0]["markPoint"]["symbolSize"] = 12

        return chart_data

    def generate_risk_comparison(self, provinces: List[str],
                                  scores: List[float],
                                  title: str = "各省风险对比") -> Dict:
        """
        生成各省风险对比柱状图数据

        :param provinces: 省份名称列表
        :param scores: 对应的风险分
        :param title: 图表标题
        :return: 图表数据字典
        """
        # 按风险分降序排列
        paired = sorted(zip(provinces, scores), key=lambda x: x[1], reverse=True)
        sorted_provinces = [p[0] for p in paired]
        sorted_scores = [round(p[1], 4) for p in paired]

        # 根据分数设置颜色
        colors = []
        for s in sorted_scores:
            if s >= 0.7:
                colors.append("#e74c3c")  # 红
            elif s >= 0.4:
                colors.append("#f39c12")  # 橙
            else:
                colors.append("#27ae60")  # 绿

        return {
            "title": title,
            "xAxis": sorted_provinces,
            "series": [
                {
                    "name": "风险分",
                    "type": "bar",
                    "data": sorted_scores,
                    "itemStyle": {
                        "color": colors
                    }
                }
            ],
            "yAxis": {
                "name": "风险分（0-100）",
                "min": 0,
                "max": 100
            }
        }

    def generate_event_distribution(self, event_types: Dict[str, int],
                                     title: str = "事件类型分布") -> Dict:
        """
        生成事件类型饼图数据

        :param event_types: 事件类型统计 {"军事冲突": 15, "政治变动": 8, ...}
        :param title: 图表标题
        :return: 图表数据字典
        """
        pie_data = [
            {"name": name, "value": count}
            for name, count in sorted(event_types.items(), key=lambda x: x[1], reverse=True)
        ]

        return {
            "title": title,
            "series": [
                {
                    "name": "事件类型",
                    "type": "pie",
                    "radius": ["30%", "70%"],
                    "data": pie_data,
                    "label": {"show": True, "formatter": "{b}: {c} ({d}%)"},
                }
            ]
        }


# 模块级单例
_chart_instance = None
_chart_lock = threading.Lock()


def get_chart_generator() -> TrendChartGenerator:
    """获取全局图表生成器单例（线程安全）"""
    global _chart_instance
    if _chart_instance is None:
        with _chart_lock:
            if _chart_instance is None:
                _chart_instance = TrendChartGenerator()
    return _chart_instance
