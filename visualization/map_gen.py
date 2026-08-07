"""
visualization.map_gen - 地图生成模块
使用 folium 生成缅甸省级风险热力地图，返回 HTML 字符串
"""
import threading
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
        :return: HTML 字符串
        """
        # 创建基础地图
        m = folium.Map(
            location=MYANMAR_CENTER,
            zoom_start=6,
            tiles="CartoDB dark_matter"
        )

        # 准备热力数据
        heat_data = []
        for item in risk_data:
            province = item.get("province", "")
            risk_score = item.get("risk_score", 0.5)

            if "lat" in item and "lon" in item:
                lat, lon = item["lat"], item["lon"]
            elif province in MYANMAR_PROVINCES:
                lat, lon = MYANMAR_PROVINCES[province]
            else:
                continue

            # 强度统一归一化到 0~1（兼容 0~100 分制输入）
            heat_data.append([lat, lon, self._normalize_score(risk_score)])

        # 添加热力图层
        if heat_data:
            HeatMap(
                heat_data,
                radius=30,
                blur=20,
                max_zoom=10,
                gradient={0.2: "green", 0.5: "yellow", 0.8: "orange", 1.0: "red"}
            ).add_to(m)

        # 为每个省份添加详细标记
        for item in risk_data:
            province = item.get("province", "")
            risk_score = item.get("risk_score", 0.5)
            risk_level = item.get("risk_level", "未知")
            score_norm = self._normalize_score(risk_score)

            if province in MYANMAR_PROVINCES:
                lat, lon = MYANMAR_PROVINCES[province]
                color = self._risk_color(score_norm)

                # 详细弹窗内容（半径 6~16 像素，避免巨圆遮盖全图）
                trend_dir = "↑" if score_norm > 0.6 else "↓" if score_norm < 0.4 else "→"
                popup_html = (
                    f"<div style='min-width:150px'>"
                    f"<b style='font-size:14px'>{province}</b><br>"
                    f"<hr style='border:1px solid #ddd;margin:4px 0'>"
                    f"风险分: <b>{score_norm * 100:.1f}</b><br>"
                    f"风险等级: <b>{risk_level}</b><br>"
                    f"趋势: {trend_dir}<br>"
                    f"<span style='font-size:11px;color:#666'>"
                    f"经纬度: ({lat:.2f}, {lon:.2f})"
                    f"</span></div>"
                )

                folium.CircleMarker(
                    location=[lat, lon],
                    radius=6 + score_norm * 10,
                    color=color,
                    fill=True,
                    fill_opacity=0.7,
                    popup=folium.Popup(popup_html, max_width=250),
                    tooltip=f"{province}: {score_norm * 100:.1f}"
                ).add_to(m)

        # 添加数据来源说明
        title_html = (
            '<div style="position:fixed;bottom:10px;left:60px;z-index:999;'
            'background:rgba(0,0,0,0.7);padding:6px 12px;border-radius:4px;'
            'color:#ccc;font-size:11px;">'
            '数据源: 新闻文本 + GDELT + VIIRS遥感 + World Bank'
            '</div>'
        )
        m.get_root().html.add_child(folium.Element(title_html))

        return m._repr_html_()

    def generate_default_map(self) -> str:
        """
        生成默认地图（无数据时展示缅甸行政区划）
        """
        m = folium.Map(
            location=MYANMAR_CENTER,
            zoom_start=6,
            tiles="CartoDB dark_matter"
        )

        # 标注所有省份
        for province, (lat, lon) in MYANMAR_PROVINCES.items():
            folium.Marker(
                location=[lat, lon],
                popup=province,
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(m)

        return m._repr_html_()

    def generate_event_density_map(self, density: Dict, days: int = 7) -> str:
        """
        生成事件密度（KDE）地图：真实国界/省界 + 事件密度热力面

        :param density: analyzer.event_density 的 compute() 返回值
        :param days: 统计窗口（展示用）
        :return: HTML 字符串
        """
        from data.admin_boundaries import load_boundaries

        m = folium.Map(
            location=MYANMAR_CENTER,
            zoom_start=6,
            tiles="CartoDB dark_matter"
        )

        # 国界（GADM L0）
        folium.GeoJson(
            load_boundaries(0),
            name="国界",
            style_function=lambda f: {
                "color": "#9aa0a8", "weight": 1.4,
                "fill": False,
            },
        ).add_to(m)

        # 省界（GADM L1，虚线细描）
        folium.GeoJson(
            load_boundaries(1),
            name="邦/省界",
            style_function=lambda f: {
                "color": "#4a4f58", "weight": 0.8,
                "dashArray": "4", "fill": False,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["NAME_1"], labels=False,
                style="background:#1a1d23;color:#e0e0e0;border-radius:4px;"
            ),
        ).add_to(m)

        # KDE 密度热力层
        grid = density.get("grid", [])
        if grid:
            HeatMap(
                grid,
                name="事件密度 (KDE)",
                radius=22,
                blur=18,
                max_zoom=11,
                gradient={0.2: "#1e90ff", 0.5: "#ffa502",
                          0.8: "#ff6348", 1.0: "#ff4757"}
            ).add_to(m)

        # 密度峰值标注
        if density.get("peak_lat") is not None:
            folium.CircleMarker(
                location=[density["peak_lat"], density["peak_lon"]],
                radius=6,
                color="#ff4757",
                fill=True,
                fill_opacity=0.9,
                tooltip=(
                    f"密度峰值: ({density['peak_lat']:.2f}, "
                    f"{density['peak_lon']:.2f})"
                )
            ).add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)

        # 说明栏
        info_html = (
            '<div style="position:fixed;bottom:10px;left:60px;z-index:999;'
            'background:rgba(0,0,0,0.7);padding:6px 12px;border-radius:4px;'
            'color:#ccc;font-size:11px;">'
            f'事件密度(KDE): GDELT 近{days}天 '
            f'{density.get("event_count", 0)} 条事件 / '
            f'有效定位 {density.get("located_count", 0)} 条 · '
            '权重=事件严重度 · 边界: GADM 4.1'
            '</div>'
        )
        m.get_root().html.add_child(folium.Element(info_html))

        return m._repr_html_()

    def generate_notice_map(self, message: str) -> str:
        """生成带提示信息的默认地图（数据不足等降级场景）"""
        m = folium.Map(
            location=MYANMAR_CENTER,
            zoom_start=6,
            tiles="CartoDB dark_matter"
        )
        try:
            from data.admin_boundaries import load_boundaries
            folium.GeoJson(
                load_boundaries(0),
                style_function=lambda f: {
                    "color": "#9aa0a8", "weight": 1.4, "fill": False,
                },
            ).add_to(m)
        except Exception:
            pass
        notice_html = (
            '<div style="position:fixed;top:60px;left:50%;transform:'
            'translateX(-50%);z-index:999;background:rgba(30,33,40,0.95);'
            'border:1px solid #ffa502;padding:10px 18px;border-radius:6px;'
            'color:#e0e0e0;font-size:13px;max-width:80%;">'
            f'⚠️ {message}</div>'
        )
        m.get_root().html.add_child(folium.Element(notice_html))
        return m._repr_html_()

    def _normalize_score(self, score: float) -> float:
        """
        风险分归一化到 0~1（兼容 0~100 分制与 0~1 比例两种输入）

        app.py 传入的是 0~100 分制；若上游改为比例值也能正确渲染，
        避免半径/颜色计算因量纲错误生成遮盖全图的巨圆。
        """
        try:
            score = float(score)
        except (TypeError, ValueError):
            return 0.5
        norm = score / 100.0 if score > 1.0 else score
        return max(0.0, min(1.0, norm))

    def _risk_color(self, score: float) -> str:
        """根据归一化风险分(0~1)返回颜色"""
        if score >= 0.7:
            return "red"
        elif score >= 0.4:
            return "orange"
        else:
            return "green"


# 模块级单例
_map_instance = None
_map_lock = threading.Lock()


def get_map_generator() -> RiskMapGenerator:
    """获取全局地图生成器单例（线程安全）"""
    global _map_instance
    if _map_instance is None:
        with _map_lock:
            if _map_instance is None:
                _map_instance = RiskMapGenerator()
    return _map_instance
