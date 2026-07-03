"""
visualization.chart_gen - 图表生成模块
使用 pyecharts 生成趋势折线图数据（返回 JSON 供前端渲染）
"""
from typing import List, Dict, Optional


class TrendChartGenerator:
    """趋势图表数据生成器"""

    def generate_trend_data(self, dates: List[str], scores: List[float],
                            moving_avg: List[float] = None,
                            title: str = "缅甸地缘风险趋势") -> Dict:
        """
        生成趋势折线图数据（JSON 格式，供前端 ECharts 渲染）

        :param dates: 日期列表 ["2026-01-01", "2026-01-02", ...]
        :param scores: 风险分列表 [0.5, 0.6, ...]
        :param moving_avg: 移动平均序列（可选）
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
                "name": "风险分",
                "min": 0,
                "max": 1
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
                "name": "风险分",
                "min": 0,
                "max": 1
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


def get_chart_generator() -> TrendChartGenerator:
    """获取全局图表生成器单例"""
    global _chart_instance
    if _chart_instance is None:
        _chart_instance = TrendChartGenerator()
    return _chart_instance
