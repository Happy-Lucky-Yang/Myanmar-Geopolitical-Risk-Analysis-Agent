"""
visualization.map_gen - 地图生成模块
使用 folium 生成缅甸省级风险热力地图，返回 HTML 字符串
"""
from typing import List, Dict, Optional
import folium
from folium.plugins import HeatMap

# 缅甸主要省份及其大致经纬度
MYANMAR_PROVINCES = {
    "仰光省": (16.87, 96.20),
    "曼德勒省": (21.97, 96.08),
    "内比都": (19.76, 96.07),
    "掸邦": (21.50, 98.00),
    "克钦邦": (25.00, 97.50),
    "克伦邦": (17.00, 97.75),
    "钦邦": (21.50, 93.50),
    "克耶邦": (19.50, 97.50),
    "孟邦": (16.30, 97.70),
    "若开邦": (20.50, 93.20),
    "勃固省": (18.00, 96.50),
    "马圭省": (20.00, 95.00),
    "实皆省": (23.50, 95.00),
    "德林达依省": (12.25, 99.00),
    "伊洛瓦底省": (15.50, 95.50),
}

# 缅甸中心点（用于初始化地图）
MYANMAR_CENTER = (19.76, 96.07)


class RiskMapGenerator:
    """风险热力地图生成器"""

    def generate_heatmap(self, risk_data: List[Dict]) -> str:
        """
        生成缅甸风险热力地图

        :param risk_data: 风险数据列表，每条包含省份和风险分
            [
                {"province": "仰光省", "risk_score": 0.8, "lat": 16.87, "lon": 96.20},
                ...
            ]
        :return: HTML 字符串（可直接嵌入网页或返回给前端）
        """
        # 创建基础地图
        m = folium.Map(
            location=MYANMAR_CENTER,
            zoom_start=6,
            tiles="CartoDB positron"
        )

        # 准备热力数据
        heat_data = []
        for item in risk_data:
            province = item.get("province", "")
            risk_score = item.get("risk_score", 0.5)

            # 查找坐标
            if "lat" in item and "lon" in item:
                lat, lon = item["lat"], item["lon"]
            elif province in MYANMAR_PROVINCES:
                lat, lon = MYANMAR_PROVINCES[province]
            else:
                continue  # 跳过未知省份

            # 热力权重 = 风险分 * 10（folium HeatMap 需要正数值）
            heat_data.append([lat, lon, risk_score * 10])

        # 添加热力图层
        if heat_data:
            HeatMap(
                heat_data,
                radius=30,
                blur=20,
                max_zoom=10,
                gradient={0.2: "green", 0.5: "yellow", 0.8: "orange", 1.0: "red"}
            ).add_to(m)

        # 为每个省份添加标记
        for item in risk_data:
            province = item.get("province", "")
            risk_score = item.get("risk_score", 0.5)
            risk_level = item.get("risk_level", "未知")

            if province in MYANMAR_PROVINCES:
                lat, lon = MYANMAR_PROVINCES[province]

                # 根据风险等级选择颜色
                color = self._risk_color(risk_score)

                popup_text = (
                    f"<b>{province}</b><br>"
                    f"风险分: {risk_score:.2f}<br>"
                    f"风险等级: {risk_level}"
                )

                folium.CircleMarker(
                    location=[lat, lon],
                    radius=8 + risk_score * 12,
                    color=color,
                    fill=True,
                    fill_opacity=0.7,
                    popup=folium.Popup(popup_text, max_width=200)
                ).add_to(m)

        # 渲染为 HTML 字符串
        html_str = m._repr_html_()
        return html_str

    def generate_default_map(self) -> str:
        """
        生成默认地图（无数据时展示缅甸行政区划）

        :return: HTML 字符串
        """
        m = folium.Map(
            location=MYANMAR_CENTER,
            zoom_start=6,
            tiles="CartoDB positron"
        )

        # 标注所有省份
        for province, (lat, lon) in MYANMAR_PROVINCES.items():
            folium.Marker(
                location=[lat, lon],
                popup=province,
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(m)

        return m._repr_html_()

    def _risk_color(self, score: float) -> str:
        """根据风险分返回颜色"""
        if score >= 0.7:
            return "red"
        elif score >= 0.4:
            return "orange"
        else:
            return "green"


# 模块级单例
_map_instance = None


def get_map_generator() -> RiskMapGenerator:
    """获取全局地图生成器单例"""
    global _map_instance
    if _map_instance is None:
        _map_instance = RiskMapGenerator()
    return _map_instance
