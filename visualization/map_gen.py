"""
visualization.map_gen - Folium 地图生成（暗色主题）

两类地图：
1. 省级风险分级填色图（choropleth）：GADM L1 真实省界按风险分染色，
   连续色阶 6 档，悬停/点击查看详情
2. 事件密度（KDE）地图：真实国界/省界 + 加权核密度热力面

性能设计：GADM 边界经 Douglas-Peucker 简化并缓存，
渲染用 HTML 体积约为原始 GeoJSON 的 1/5，显著加快前端加载。
"""
import copy
import math
import base64
import threading
import logging
import folium
import numpy as np
from folium.plugins import HeatMap
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 缅甸主要省份及其大致经纬度（历史兼容，app.py 引用）
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

# ============================================================
# 风险色阶：连续渐变（6 档语义分级，颜色本身连续插值）
# ============================================================
_RISK_STOPS = [
    (0.00, (46, 213, 115)),    # 低风险 绿
    (0.20, (163, 222, 63)),    # 中低 黄绿
    (0.40, (255, 211, 42)),    # 中等 黄
    (0.55, (255, 165, 2)),     # 中高 橙
    (0.70, (255, 99, 72)),     # 高 橙红
    (0.85, (255, 71, 87)),     # 高风险 红
    (1.00, (164, 18, 60)),     # 极高 暗红
]

# 图例分级（展示用）
_LEGEND_LEVELS = [
    ("低风险", 0.0), ("中低", 0.25), ("中等", 0.45),
    ("中高", 0.6), ("高", 0.75), ("极高", 0.95),
]

# 简化后边界缓存 {level: geojson_dict}
_SIMPLIFIED_CACHE: Dict[int, dict] = {}
_SIMPLIFY_EPSILON = 0.008  # 约 0.9km，国界渲染足够


def risk_color_continuous(score_norm: float) -> str:
    """归一化风险分(0~1) → 连续插值色（hex）"""
    s = max(0.0, min(1.0, float(score_norm)))
    for i in range(len(_RISK_STOPS) - 1):
        p0, c0 = _RISK_STOPS[i]
        p1, c1 = _RISK_STOPS[i + 1]
        if s <= p1:
            t = (s - p0) / (p1 - p0) if p1 > p0 else 0.0
            rgb = [int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3)]
            return "#{:02x}{:02x}{:02x}".format(*rgb)
    return "#{:02x}{:02x}{:02x}".format(*_RISK_STOPS[-1][1])


def risk_level_name(score_norm: float) -> str:
    """归一化风险分 → 6 档文字等级"""
    if score_norm >= 0.85:
        return "极高风险"
    if score_norm >= 0.70:
        return "高风险"
    if score_norm >= 0.55:
        return "中高风险"
    if score_norm >= 0.40:
        return "中等风险"
    if score_norm >= 0.20:
        return "中低风险"
    return "低风险"


# ============================================================
# 边界简化（Douglas-Peucker，迭代实现避免深递归）
# ============================================================

def _dp_simplify(points: List, epsilon: float) -> List:
    """对单个环做 Douglas-Peucker 简化（保留首尾点）"""
    n = len(points)
    if n < 5:
        return points
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    eps2 = epsilon * epsilon
    while stack:
        s, e = stack.pop()
        if e <= s + 1:
            continue
        ax, ay = points[s]
        bx, by = points[e]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        max_d, idx = -1.0, -1
        for i in range(s + 1, e):
            px, py = points[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = max(0.0, min(1.0, t))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > max_d:
                max_d, idx = d2, i
        if max_d > eps2:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return [p for p, k in zip(points, keep) if k]


def _simplify_geojson(gj: dict, epsilon: float) -> dict:
    """简化 FeatureCollection 内所有多边形环，坐标保留 4 位小数"""
    out_features = []
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        new_coords = None
        if gtype == "Polygon" and coords:
            rings = [_dp_simplify(r, epsilon) for r in coords]
            rings = [r for r in rings if len(r) >= 4]
            if rings:
                new_coords = [[[round(x, 4) for x in pt] for pt in r]
                              for r in rings]
        elif gtype == "MultiPolygon" and coords:
            polys = []
            for poly in coords:
                rings = [_dp_simplify(r, epsilon) for r in poly]
                rings = [r for r in rings if len(r) >= 4]
                if rings:
                    polys.append([[[round(x, 4) for x in pt] for pt in r]
                                  for r in rings])
            if polys:
                new_coords = polys
        if new_coords is None:
            continue
        out_features.append({
            "type": "Feature",
            "properties": feat.get("properties", {}),
            "geometry": {"type": gtype, "coordinates": new_coords},
        })
    return {"type": "FeatureCollection", "features": out_features}


def _simplified_boundaries(level: int) -> dict:
    """加载并缓存简化后的 GADM 边界（渲染专用，勿用于空间统计）"""
    if level not in _SIMPLIFIED_CACHE:
        from data.admin_boundaries import load_boundaries
        raw = load_boundaries(level)
        _SIMPLIFIED_CACHE[level] = _simplify_geojson(raw, _SIMPLIFY_EPSILON)
        logger.info(
            f"[MapGen] 边界简化 L{level}: "
            f"{len(_SIMPLIFIED_CACHE[level]['features'])} features"
        )
    return _SIMPLIFIED_CACHE[level]


def _legend_html() -> str:
    """风险色阶图例（固定渐变条 + 6 档标签）"""
    stops = ", ".join(
        f"rgb({c[0]},{c[1]},{c[2]}) {int(p * 100)}%"
        for p, c in _RISK_STOPS
    )
    labels = "".join(
        f'<span style="flex:1;text-align:center">{name}</span>'
        for name, _ in _LEGEND_LEVELS
    )
    return (
        '<div style="position:fixed;bottom:10px;right:10px;z-index:999;'
        'background:rgba(0,0,0,0.75);padding:8px 12px;border-radius:6px;'
        'color:#ccc;font-size:11px;width:240px;">'
        '<div style="margin-bottom:4px;font-weight:600">风险等级色阶 (0-100)</div>'
        f'<div style="height:10px;border-radius:3px;'
        f'background:linear-gradient(to right,{stops})"></div>'
        f'<div style="display:flex;margin-top:3px">{labels}</div>'
        '</div>'
    )


def _source_note_html(text: str) -> str:
    """左下角数据来源说明栏"""
    return (
        '<div style="position:fixed;bottom:10px;left:60px;z-index:999;'
        'background:rgba(0,0,0,0.7);padding:6px 12px;border-radius:4px;'
        'color:#ccc;font-size:11px;">' + text + '</div>'
    )


def _kde_legend_html() -> str:
    """事件密度色标图例（左下角，避免与风险色阶重叠）"""
    return (
        '<div style="position:fixed;bottom:10px;left:10px;z-index:999;'
        'background:rgba(0,0,0,0.75);padding:8px 12px;border-radius:6px;'
        'color:#ccc;font-size:11px;width:200px;">'
        '<div style="margin-bottom:4px;font-weight:600">事件密度 (KDE)</div>'
        '<div style="height:10px;border-radius:3px;background:'
        'linear-gradient(to right,#1e90ff,#ffa502,#ff6348,#ff4757)"></div>'
        '<div style="display:flex;margin-top:3px">'
        '<span style="flex:1">低</span>'
        '<span style="flex:1;text-align:center">中</span>'
        '<span style="flex:1;text-align:right">高</span>'
        '</div></div>'
    )


def render_density_png(z, bbox) -> str:
    """
    将密度矩阵渲染为 PNG（base64 data URI），供 folium ImageOverlay 叠加

    栅格渲染替代点式热力：任意缩放均平滑连续，无圆点伪影。
    低密度区（≤0.02）设为全透明，不遮盖底图。

    :param z: 归一化密度矩阵 (rows×cols，境外为 0)
    :param bbox: [[lat_min, lon_min], [lat_max, lon_max]]
    :return: data:image/png;base64,... 字符串
    """
    import io as _io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    rows, cols = z.shape
    cmap = LinearSegmentedColormap.from_list(
        "kde", ["#1e90ff", "#ffa502", "#ff6348", "#ff4757"])
    cmap.set_bad(alpha=0)  # 掩膜区域透明
    zm = np.ma.masked_where(z <= 0.02, z)

    fig = plt.figure(figsize=(cols / 25.0, rows / 25.0), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(
        zm, origin="lower", cmap=cmap,
        extent=[bbox[0][1], bbox[1][1], bbox[0][0], bbox[1][0]],
        interpolation="bilinear",
    )
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(
        buf.getvalue()).decode("ascii")


class RiskMapGenerator:
    """风险地图生成器（分级填色 + 事件密度）"""

    # ============================================================
    # 1. 省级风险分级填色图（choropleth）
    # ============================================================

    def generate_heatmap(self, risk_data: List[Dict]) -> str:
        """
        生成省级风险分级填色地图：GADM L1 省界按风险分连续染色

        :param risk_data: [{province, risk_score, risk_level}, ...]
        :return: HTML 字符串
        """
        from data.admin_boundaries import PROVINCE_EN2CN
        from folium.features import GeoJsonPopup, GeoJsonTooltip

        m = folium.Map(
            location=MYANMAR_CENTER, zoom_start=6,
            tiles="CartoDB dark_matter"
        )

        by_prov = {item.get("province", ""): item for item in risk_data}
        gj = copy.deepcopy(_simplified_boundaries(1))

        # 注入展示属性（中文名/分值文本），供 tooltip 与 popup 使用
        for feat in gj["features"]:
            name_en = feat["properties"].get("NAME_1", "")
            name_cn = PROVINCE_EN2CN.get(name_en, name_en)
            item = by_prov.get(name_cn)
            feat["properties"]["name_cn"] = name_cn
            if item:
                norm = self._normalize_score(item.get("risk_score", 50))
                feat["properties"]["score_text"] = (
                    f"{risk_level_name(norm)} · {norm * 100:.1f} 分"
                )
            else:
                feat["properties"]["score_text"] = "暂无数据"

        def style_fn(f):
            name_cn = f["properties"].get("name_cn", "")
            item = by_prov.get(name_cn)
            if item:
                norm = self._normalize_score(item.get("risk_score", 50))
                return {
                    "color": "#2a2d35",
                    "weight": 0.8,
                    "fillColor": risk_color_continuous(norm),
                    "fillOpacity": 0.62,
                }
            return {
                "color": "#2a2d35", "weight": 0.8,
                "fillColor": "#3a3f47", "fillOpacity": 0.25,
            }

        def highlight_fn(f):
            return {"weight": 2, "color": "#ffffff", "fillOpacity": 0.75}

        layer = folium.GeoJson(
            gj,
            name="省级风险",
            style_function=style_fn,
            highlight_function=highlight_fn,
            tooltip=GeoJsonTooltip(
                fields=["name_cn", "score_text"], labels=False,
                style="background:#1a1d23;color:#e0e0e0;border-radius:4px;"
                      "pointer-events:none;"
            ),
        )
        GeoJsonPopup(
            fields=["name_cn", "score_text"],
            labels=True,
            style="background:#1a1d23;color:#e0e0e0;border-radius:6px;"
                  "font-size:13px;",
        ).add_to(layer)
        layer.add_to(m)

        # 国界描边（L0，覆盖在省界之上更清晰）
        folium.GeoJson(
            _simplified_boundaries(0),
            name="国界",
            style_function=lambda f: {
                "color": "#9aa0a8", "weight": 1.4, "fill": False,
            },
        ).add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)
        m.get_root().html.add_child(folium.Element(_legend_html()))
        m.get_root().html.add_child(folium.Element(_source_note_html(
            "数据源: 新闻文本 + GDELT + 夜光/经济(WB) · 边界: GADM 4.1 · "
            "点击省份查看详情"
        )))
        return m._repr_html_()

    # ============================================================
    # 1b. 省级风险混合图（风险圆点 + 省界描边，悬停/点击高亮）
    # ============================================================

    def generate_risk_hybrid_map(self, risk_data: List[Dict]) -> str:
        """
        生成混合风格风险地图：经典风险圆点 + GADM 真实省界描边

        兼顾小组讨论中两种偏好：圆点直观展示风险强度，
        省界提供行政区划认知；悬停/点击省份时边框高亮。

        :param risk_data: [{province, risk_score, risk_level}, ...]
        :return: HTML 字符串
        """
        from data.admin_boundaries import PROVINCE_EN2CN, province_centroids
        from folium.features import GeoJsonPopup, GeoJsonTooltip

        m = folium.Map(
            location=MYANMAR_CENTER, zoom_start=6,
            tiles="CartoDB dark_matter"
        )

        by_prov = {item.get("province", ""): item for item in risk_data}
        centroids = province_centroids()  # 真实几何质心，替代手写坐标
        # 预计算各省质心坐标（光晕/圆点共用）
        prov_points = {
            item.get("province", ""): (
                centroids.get(item.get("province", ""))
                or MYANMAR_PROVINCES.get(item.get("province", "")))
            for item in risk_data
        }

        # 荧光光晕层（热力图风格渐变荧光，垫于边界与圆点之下）
        heat_data = []
        for item in risk_data:
            pt = prov_points.get(item.get("province", ""))
            if pt:
                heat_data.append([
                    pt[0], pt[1],
                    self._normalize_score(item.get("risk_score", 50))])
        if heat_data:
            HeatMap(
                heat_data,
                name="风险光晕",
                radius=25,
                blur=22,
                max_zoom=10,
                gradient={0.2: "#2ed573", 0.5: "#ffd32a",
                          0.8: "#ff6348", 1.0: "#ff4757"},
            ).add_to(m)
            # 光晕 canvas 默认拦截指针事件，会遮蔽省界悬停高亮，禁用之
            m.get_root().header.add_child(folium.Element(
                "<style>.leaflet-overlay-pane canvas"
                "{pointer-events:none;}</style>"))

        gj = copy.deepcopy(_simplified_boundaries(1))

        # 注入展示属性（供 tooltip/popup/高亮取色）
        for feat in gj["features"]:
            name_en = feat["properties"].get("NAME_1", "")
            name_cn = PROVINCE_EN2CN.get(name_en, name_en)
            item = by_prov.get(name_cn)
            feat["properties"]["name_cn"] = name_cn
            if item:
                norm = self._normalize_score(item.get("risk_score", 50))
                feat["properties"]["score_text"] = (
                    f"{risk_level_name(norm)} · {norm * 100:.1f} 分"
                )
                feat["properties"]["_risk_color"] = risk_color_continuous(norm)
            else:
                feat["properties"]["score_text"] = "暂无数据"
                feat["properties"]["_risk_color"] = "#3a3f47"

        def style_fn(f):
            # 近透明填充：视觉上不可见，但让多边形内部参与鼠标命中，
            # 否则 fill:false 时仅 1px 边线可触发悬停高亮（闪烁根因）
            return {
                "color": "#7a828c", "weight": 1.1,
                "fill": True, "fillColor": "#000000", "fillOpacity": 0.01,
            }

        def highlight_fn(f):
            # 悬停/点击高亮：白色粗边框 + 本省风险色淡填色
            return {
                "weight": 2.6, "color": "#ffffff",
                "fill": True,
                "fillColor": f["properties"].get("_risk_color", "#3a3f47"),
                "fillOpacity": 0.30,
            }

        layer = folium.GeoJson(
            gj,
            name="省界（悬停高亮）",
            style_function=style_fn,
            highlight_function=highlight_fn,
            tooltip=GeoJsonTooltip(
                fields=["name_cn", "score_text"], labels=False,
                # pointer-events:none 防止 tooltip 截获鼠标导致高亮闪烁
                style="background:#1a1d23;color:#e0e0e0;border-radius:4px;"
                      "pointer-events:none;"
            ),
        )
        GeoJsonPopup(
            fields=["name_cn", "score_text"],
            labels=True,
            style="background:#1a1d23;color:#e0e0e0;border-radius:6px;"
                  "font-size:13px;",
        ).add_to(layer)
        layer.add_to(m)

        # 风险圆点（位于真实几何质心，半径/颜色随风险分）
        for item in risk_data:
            province = item.get("province", "")
            risk_level = item.get("risk_level", "未知")
            score_norm = self._normalize_score(item.get("risk_score", 50))

            latlon = prov_points.get(province)
            if not latlon:
                continue
            color = risk_color_continuous(score_norm)
            trend_dir = "↑" if score_norm > 0.6 else "↓" if score_norm < 0.4 else "→"
            popup_html = (
                f"<div style='min-width:150px'>"
                f"<b style='font-size:14px'>{province}</b><br>"
                f"<hr style='border:1px solid #ddd;margin:4px 0'>"
                f"风险分: <b>{score_norm * 100:.1f}</b><br>"
                f"风险等级: <b>{risk_level}</b><br>"
                f"趋势: {trend_dir}<br>"
                f"<span style='font-size:11px;color:#666'>"
                f"经纬度: ({latlon[0]:.2f}, {latlon[1]:.2f})"
                f"</span></div>"
            )
            folium.CircleMarker(
                location=list(latlon),
                radius=6 + score_norm * 10,
                color=color,
                fill=True,
                fill_opacity=0.75,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"{province}: {score_norm * 100:.1f}",
                # 禁止事件冒泡，避免干扰省界悬停高亮的稳定性
                bubbling_mouse_events=False,
            ).add_to(m)

        # 国界描边
        folium.GeoJson(
            _simplified_boundaries(0),
            name="国界",
            style_function=lambda f: {
                "color": "#9aa0a8", "weight": 1.4, "fill": False,
            },
        ).add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)
        m.get_root().html.add_child(folium.Element(_legend_html()))
        m.get_root().html.add_child(folium.Element(_source_note_html(
            "数据源: 新闻文本 + GDELT + 夜光/经济(WB) · 边界: GADM 4.1 · "
            "悬停省份高亮，点击查看详情"
        )))
        return m._repr_html_()

    # ============================================================
    # 1c. 统一地图（自定义图层面板版：五图层可开关叠加）
    # ============================================================

    def generate_unified_map(self, risk_data: List[Dict],
                             density: Dict = None, days: int = 7) -> str:
        """
        生成统一地图：单张地图内含全部可叠加图层，由前端自定义
        图层面板控制开关/互斥/透明度（替代多地图切换）。

        图层（自下而上）：
            choropleth 区域填色（与散点互斥，默认关）
            kde        事件密度栅格（可叠加，默认开）
            borders    省界交互层（悬停高亮/tooltip/弹窗）
            hybrid     风险散点+荧光光晕（与填色互斥，默认开）
            country    国界描边

        通过注入脚本将图层对象注册到 window._mmLayers，
        供前端面板控制；不使用 folium 原生 LayerControl。

        :param risk_data: 省级风险数据（可为空）
        :param density: KDE compute() 结果（可为 None 或降级态）
        :param days: 窗口天数（展示用）
        :return: HTML 字符串
        """
        from data.admin_boundaries import PROVINCE_EN2CN, province_centroids
        from folium.features import GeoJsonPopup, GeoJsonTooltip

        m = folium.Map(
            location=MYANMAR_CENTER, zoom_start=6,
            tiles="CartoDB dark_matter"
        )

        by_prov = {item.get("province", ""): item for item in risk_data}
        centroids = province_centroids()
        prov_points = {
            item.get("province", ""): (
                centroids.get(item.get("province", ""))
                or MYANMAR_PROVINCES.get(item.get("province", "")))
            for item in risk_data
        }

        def _inject_props(gj):
            for feat in gj["features"]:
                name_en = feat["properties"].get("NAME_1", "")
                name_cn = PROVINCE_EN2CN.get(name_en, name_en)
                item = by_prov.get(name_cn)
                feat["properties"]["name_cn"] = name_cn
                if item:
                    norm = self._normalize_score(item.get("risk_score", 50))
                    feat["properties"]["score_text"] = (
                        f"{risk_level_name(norm)} · {norm * 100:.1f} 分")
                    feat["properties"]["_risk_color"] = (
                        risk_color_continuous(norm))
                    feat["properties"]["_risk_level"] = risk_level_name(norm)
                    feat["properties"]["_trend"] = (
                        "↑" if norm > 0.6
                        else "↓" if norm < 0.4 else "→")
                else:
                    feat["properties"]["score_text"] = "暂无数据"
                    feat["properties"]["_risk_color"] = "#3a3f47"
                    feat["properties"]["_risk_level"] = "--"
                    feat["properties"]["_trend"] = "--"

        # ---- 1) 区域填色层（默认关，与散点互斥）----
        gj_fill = copy.deepcopy(_simplified_boundaries(1))
        _inject_props(gj_fill)

        def fill_fn(f):
            name_cn = f["properties"].get("name_cn", "")
            item = by_prov.get(name_cn)
            if item:
                norm = self._normalize_score(item.get("risk_score", 50))
                return {
                    "color": "#2a2d35", "weight": 0.8,
                    "fillColor": risk_color_continuous(norm),
                    "fillOpacity": 0.62,
                }
            return {
                "color": "#2a2d35", "weight": 0.8,
                "fillColor": "#3a3f47", "fillOpacity": 0.25,
            }

        def fill_highlight_fn(f):
            """悬停/点击高亮：白色粗边框 + 填充透明度提升"""
            return {
                "weight": 2.6, "color": "#ffffff",
                "fillOpacity": 0.82,
            }

        # show=False：默认不上图（与散点互斥，由面板切换时才加载，
        # 避免面板绑定前短暂出现填色+散点同屏）
        choro_layer = folium.GeoJson(
            gj_fill, name="区域填色", style_function=fill_fn,
            highlight_function=fill_highlight_fn,
            tooltip=GeoJsonTooltip(
                fields=["name_cn", "score_text"], labels=False,
                style="background:#1a1d23;color:#e0e0e0;border-radius:4px;"
                      "pointer-events:none;"
            ),
            show=False)
        GeoJsonPopup(
            fields=["name_cn", "score_text", "_risk_level", "_trend"],
            labels=True,
            style="background:#1a1d23;color:#e0e0e0;border-radius:6px;"
                  "font-size:13px;",
        ).add_to(choro_layer)

        # ---- 2) 事件密度栅格层（可叠加；默认不上图，由面板开启）----
        kde_layer = None
        if (density and not density.get("degraded")
                and density.get("z_matrix") is not None):
            data_uri = render_density_png(
                density["z_matrix"], density["bbox"])
            kde_layer = folium.raster_layers.ImageOverlay(
                image=data_uri,
                bounds=density["bbox"],
                name="事件密度 (KDE)",
                opacity=0.60,
                interactive=False,
            )

        # ---- 3) 混合层：荧光光晕 + 风险散点（默认开，与填色互斥）----
        hybrid_group = folium.FeatureGroup(name="风险散点")
        heat_data = []
        for item in risk_data:
            pt = prov_points.get(item.get("province", ""))
            if pt:
                heat_data.append([
                    pt[0], pt[1],
                    self._normalize_score(item.get("risk_score", 50))])
        if heat_data:
            HeatMap(
                heat_data,
                radius=25, blur=22, max_zoom=10,
                gradient={0.2: "#2ed573", 0.5: "#ffd32a",
                          0.8: "#ff6348", 1.0: "#ff4757"},
            ).add_to(hybrid_group)

        for item in risk_data:
            province = item.get("province", "")
            risk_level = item.get("risk_level", "未知")
            score_norm = self._normalize_score(item.get("risk_score", 50))
            latlon = prov_points.get(province)
            if not latlon:
                continue
            color = risk_color_continuous(score_norm)
            trend_dir = ("↑" if score_norm > 0.6
                         else "↓" if score_norm < 0.4 else "→")
            popup_html = (
                f"<div style='min-width:150px'>"
                f"<b style='font-size:14px'>{province}</b><br>"
                f"<hr style='border:1px solid #ddd;margin:4px 0'>"
                f"风险分: <b>{score_norm * 100:.1f}</b><br>"
                f"风险等级: <b>{risk_level}</b><br>"
                f"趋势: {trend_dir}<br>"
                f"<span style='font-size:11px;color:#666'>"
                f"经纬度: ({latlon[0]:.2f}, {latlon[1]:.2f})"
                f"</span></div>"
            )
            folium.CircleMarker(
                location=list(latlon),
                radius=6 + score_norm * 10,
                color=color,
                fill=True,
                fill_opacity=0.75,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"{province}: {score_norm * 100:.1f}",
                bubbling_mouse_events=False,
            ).add_to(hybrid_group)

        # ---- 4) 省界交互层（悬停高亮 + tooltip + 弹窗）----
        gj_line = copy.deepcopy(_simplified_boundaries(1))
        _inject_props(gj_line)

        def line_fn(f):
            return {
                "color": "#7a828c", "weight": 1.1,
                "fill": True, "fillColor": "#000000",
                "fillOpacity": 0.01,
            }

        def line_highlight_fn(f):
            return {
                "weight": 2.6, "color": "#ffffff",
                "fill": True,
                "fillColor": f["properties"].get("_risk_color", "#3a3f47"),
                "fillOpacity": 0.30,
            }

        borders_layer = folium.GeoJson(
            gj_line,
            name="省界",
            style_function=line_fn,
            highlight_function=line_highlight_fn,
            tooltip=GeoJsonTooltip(
                fields=["name_cn", "score_text"], labels=False,
                style="background:#1a1d23;color:#e0e0e0;border-radius:4px;"
                      "pointer-events:none;"
            ),
        )
        GeoJsonPopup(
            fields=["name_cn", "score_text"],
            labels=True,
            style="background:#1a1d23;color:#e0e0e0;border-radius:6px;"
                  "font-size:13px;",
        ).add_to(borders_layer)

        # ---- 5) 国界描边 ----
        country_layer = folium.GeoJson(
            _simplified_boundaries(0),
            name="国界",
            style_function=lambda f: {
                "color": "#9aa0a8", "weight": 1.4, "fill": False,
            },
        )

        # 叠加顺序（自下而上）：填色 → KDE → 省界 → 散点光晕 → 国界
        choro_layer.add_to(m)
        if kde_layer is not None:
            kde_layer.add_to(m)
        borders_layer.add_to(m)
        hybrid_group.add_to(m)
        country_layer.add_to(m)

        # 光晕 canvas 不拦截指针事件（保护省界悬停高亮）
        m.get_root().header.add_child(folium.Element(
            "<style>.leaflet-overlay-pane canvas"
            "{pointer-events:none;}</style>"))

        # 省界层 pointer-events:none：纯视觉，使鼠标事件穿透至下层填色区域
        # （区域填色模式下，填充区域需要直接响应悬停/点击）
        m.get_root().header.add_child(folium.Element(
            "<style>"
            f"#{borders_layer.get_name()} {{pointer-events:none;}}"
            f"#{borders_layer.get_name()} path {{pointer-events:none;}}"
            "</style>"))

        # 图例（保留可视化辅助；底部文字说明已移除，保持页面干净）
        m.get_root().html.add_child(folium.Element(_legend_html()))
        if kde_layer is not None:
            m.get_root().html.add_child(folium.Element(_kde_legend_html()))

        # 图层注册表：供前端自定义面板控制。
        # 注意：① script 段的子元素已处在外层 <script> 块内，
        # 绝不能再包 <script> 标签（嵌套会提前闭合外层块）；
        # ② setTimeout(0) 延迟到当前脚本块执行完毕后再注册，
        # 确保地图与图层变量均已定义；③ _repr_html_ 包进 iframe(srcdoc)，
        # 需同时注册到父窗口供外层面板读取（同源可访问）。
        kde_js = kde_layer.get_name() if kde_layer is not None else "null"
        # KDE 默认不上图（避免初始重叠遮挡），由面板勾选开启
        kde_default_off = (
            f"try{{{m.get_name()}.removeLayer({kde_js});}}catch(e){{}}"
            if kde_layer is not None else "")
        reg_script = (
            "setTimeout(function(){"
            + kde_default_off +
            "function _reg(t){"
            f"t._mmMap = {m.get_name()};"
            "t._mmLayers = {"
            f"choropleth: {choro_layer.get_name()},"
            f"hybrid: {hybrid_group.get_name()},"
            f"kde: {kde_js},"
            f"borders: {borders_layer.get_name()},"
            f"country: {country_layer.get_name()}"
            "};}"
            "_reg(window);"
            "if(window.parent && window.parent !== window){"
            "try{_reg(window.parent);}catch(e){}"
            "}"
            "}, 0);"
        )
        m.get_root().script.add_child(folium.Element(reg_script))

        return m._repr_html_()

    # ============================================================
    # 2. 默认地图（无数据时：灰色省界 + 提示）
    # ============================================================

    def generate_default_map(self) -> str:
        """生成默认地图（无历史数据时展示灰色行政区划）"""
        m = folium.Map(
            location=MYANMAR_CENTER, zoom_start=6,
            tiles="CartoDB dark_matter"
        )
        try:
            folium.GeoJson(
                _simplified_boundaries(1),
                name="邦/省界",
                style_function=lambda f: {
                    "color": "#4a4f58", "weight": 0.8,
                    "fillColor": "#3a3f47", "fillOpacity": 0.25,
                },
                tooltip=folium.GeoJsonTooltip(
                    fields=["NAME_1"], labels=False,
                    style="background:#1a1d23;color:#e0e0e0;border-radius:4px;"
                          "pointer-events:none;"
                ),
            ).add_to(m)
        except Exception as e:
            logger.warning(f"[MapGen] 边界加载失败，使用无边界默认地图: {e}")

        m.get_root().html.add_child(folium.Element(_source_note_html(
            "暂无历史风险数据，展示行政区划底图（GADM 4.1）"
        )))
        return m._repr_html_()

    # ============================================================
    # 3. 事件密度（KDE）地图
    # ============================================================

    def generate_event_density_map(self, density: Dict, days: int = 7) -> str:
        """
        生成事件密度（KDE）地图：真实国界/省界 + 事件密度热力面

        :param density: analyzer.event_density 的 compute() 返回值
        :param days: 统计窗口（展示用）
        :return: HTML 字符串
        """
        m = folium.Map(
            location=MYANMAR_CENTER, zoom_start=6,
            tiles="CartoDB dark_matter"
        )

        # 国界（简化版，加速渲染）
        folium.GeoJson(
            _simplified_boundaries(0),
            name="国界",
            style_function=lambda f: {
                "color": "#9aa0a8", "weight": 1.4, "fill": False,
            },
        ).add_to(m)

        # 省界（简化版，悬停显示名称）
        folium.GeoJson(
            _simplified_boundaries(1),
            name="邦/省界",
            style_function=lambda f: {
                "color": "#4a4f58", "weight": 0.8,
                "dashArray": "4", "fill": False,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["NAME_1"], labels=False,
                style="background:#1a1d23;color:#e0e0e0;border-radius:4px;"
                      "pointer-events:none;"
            ),
        ).add_to(m)

        # KDE 密度栅格层（matplotlib PNG 叠加，缩放无圆点伪影）
        z = density.get("z_matrix")
        if z is not None and density.get("bbox"):
            data_uri = render_density_png(z, density["bbox"])
            folium.raster_layers.ImageOverlay(
                image=data_uri,
                bounds=density["bbox"],
                name="事件密度 (KDE)",
                opacity=0.85,
                interactive=False,
            ).add_to(m)
        elif density.get("grid"):
            # 兜底：旧点式热力（无 z_matrix 时）
            HeatMap(
                density["grid"],
                name="事件密度 (KDE)",
                radius=22, blur=18, max_zoom=11,
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
        m.get_root().html.add_child(folium.Element(_kde_legend_html()))
        m.get_root().html.add_child(folium.Element(_source_note_html(
            f"事件密度(KDE): GDELT 近{days}天 "
            f"{density.get('event_count', 0)} 条事件 / "
            f"参与计算 {density.get('located_count', 0)} 条 / "
            f"多信源互证 {density.get('verified_count', 0)} 条 · "
            "边界: GADM 4.1"
        )))
        return m._repr_html_()

    # ============================================================
    # 4. 提示地图（降级场景）
    # ============================================================

    def generate_notice_map(self, message: str) -> str:
        """生成带提示信息的默认地图（数据不足等降级场景）"""
        m = folium.Map(
            location=MYANMAR_CENTER, zoom_start=6,
            tiles="CartoDB dark_matter"
        )
        try:
            folium.GeoJson(
                _simplified_boundaries(0),
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

    # ============================================================
    # 工具方法
    # ============================================================

    def _normalize_score(self, score: float) -> float:
        """
        风险分归一化到 0~1（兼容 0~100 分制与 0~1 比例两种输入）

        app.py 传入的是 0~100 分制；若上游改为比例值也能正确渲染，
        避免颜色/半径计算因量纲错误失真。
        """
        try:
            score = float(score)
        except (TypeError, ValueError):
            return 0.5
        norm = score / 100.0 if score > 1.0 else score
        return max(0.0, min(1.0, norm))

    def _risk_color(self, score: float) -> str:
        """3 档离散色（兼容保留；新渲染请使用 risk_color_continuous）"""
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
