"""
analyzer.event_density - 事件核密度估计（KDE）分析

基于 GDELT 事件经纬度生成"地缘紧张热点"连续密度面：
- 权重：事件严重度（data.gdelt_files.event_severity_weight，
  冲突事件对密度的贡献高于外交事件，与风险指标口径一致）
- 带宽：scipy Scott 法则（随样本量自适应，加权版本）
- 掩膜：GADM 4.1 国界（L0），国境之外的密度置零
  （用 matplotlib.path 做点在多边形内判定，避免引入 shapely 依赖）

输出供 visualization.map_gen 渲染事件密度地图。
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# 缅甸网格范围（依 GADM L0 包围盒略收紧）
LAT_RANGE = (9.4, 28.6)
LON_RANGE = (91.8, 101.3)


class EventDensityAnalyzer:
    """事件密度分析器（KDE）"""

    def __init__(self, config: Dict = None):
        if config is None:
            try:
                from utils.config import load_config
                config = load_config().get("kde", {})
            except Exception:
                config = {}
        self._grid_rows = int(config.get("grid_rows", 120))
        self._grid_cols = int(config.get("grid_cols", 95))
        self._min_events = int(config.get("min_events", 5))
        # 多信源互证：只统计 ≥min_sources 家信源报道的事件；
        # 互证事件（≥2 家信源 或 ≥2 篇报道）权重乘以 verified_boost。
        # 缅甸事件实测多为单信源但多篇报道，故互证信号以报道数补充。
        self._min_sources = int(config.get("min_sources", 1))
        self._verified_boost = float(config.get("verified_boost", 1.25))
        self._mask_paths = None  # 惰性加载国界掩膜
        self._bandwidth_km = float(config.get("bandwidth_km", 50))
        self._color_max = float(config.get("color_max_per_km2_day", 0.001))
        if not np.isfinite(self._bandwidth_km) or self._bandwidth_km <= 0 or not np.isfinite(self._color_max) or self._color_max <= 0:
            raise ValueError("KDE带宽和公共色标上限必须为正有限数")
        self._country_geometry = None

    # ============================================================
    # 国界掩膜（GADM L0）
    # ============================================================

    def _get_mask_paths(self):
        """构建国界多边形路径（忽略内环孔洞，国界场景影响可忽略）"""
        if self._mask_paths is None:
            from matplotlib.path import Path
            from data.admin_boundaries import load_boundaries

            feat = load_boundaries(0)["features"][0]
            geom = feat["geometry"]
            polys = (geom["coordinates"]
                     if geom["type"] == "MultiPolygon"
                     else [geom["coordinates"]])
            paths = []
            for poly in polys:
                ring = np.asarray(poly[0])[:, :2]  # 外环 (lon, lat)
                if len(ring) >= 3:
                    paths.append(Path(ring))
            self._mask_paths = paths
        return self._mask_paths

    def _inside_country(self, lonlat: np.ndarray) -> np.ndarray:
        """判定 (lon, lat) 点集是否位于缅甸国境（任一边界多边形内）"""
        from shapely import contains_xy
        from shapely.geometry import shape
        from shapely.ops import unary_union
        from data.admin_boundaries import load_boundaries
        if self._country_geometry is None:
            self._country_geometry = unary_union([shape(f['geometry']) for f in load_boundaries(0)['features']])
        return contains_xy(self._country_geometry, lonlat[:, 0], lonlat[:, 1])

    # ============================================================
    # 核心计算
    # ============================================================

    def compute(self, events: List[Dict], days: int = None, end_date=None) -> Dict:
        """
        计算事件密度面

        :param events: GDELT 事件列表（需含 lat/lon，None 坐标自动剔除）
        :param days: 仅统计最近 N 天事件（按 SQLDATE 过滤），None=全部
        :return: {
            "degraded": 降级原因或 None,
            "grid": [[lat, lon, weight(0~1)], ...] 兼容输出,
            "z_matrix": 归一化密度矩阵(rows×cols，境外为 0，供栅格渲染),
            "bbox": [[lat_min, lon_min], [lat_max, lon_max]],
            "event_count": 统计窗口内事件总数,
            "located_count": 其中参与密度计算数（有效坐标且达信源门槛）,
            "verified_count": 互证事件数（≥2 信源或 ≥2 篇报道）,
            "peak_lat"/"peak_lon": 密度峰值位置,
            "window_days": 过滤窗口
        }
        """
        from data.gdelt_files import event_severity_weight

        # 时间窗口过滤
        from analyzer.spatial_analysis import unique_events
        from utils.data_contract import finite_number, business_date
        window_events = unique_events(events or [], days, end_date)

        lats, lons, weights = [], [], []
        verified = 0
        for ev in window_events:
            if not finite_number(ev.get("lat")) or not finite_number(ev.get("lon")):
                continue
            if not -90 <= ev['lat'] <= 90 or not -180 <= ev['lon'] <= 180:
                continue
            if not self._inside_country(np.array([[ev['lon'], ev['lat']]]))[0]:
                continue
            # 信源门槛过滤（默认 1 即不过滤）
            sources = ev.get("num_sources", 1)
            sources = sources if finite_number(sources) else 1
            if sources < self._min_sources:
                continue
            severity = event_severity_weight(ev)
            if not finite_number(severity):
                continue
            lats.append(float(ev["lat"]))
            lons.append(float(ev["lon"]))
            w = severity
            # 互证判定：≥2 家信源 或 ≥2 篇报道（缅甸事件多为单信源多篇）
            if sources >= 2:
                verified += 1
            weights.append(w)

        total = len(window_events)
        located = len(lats)

        if located < self._min_events:
            return {
                "degraded": (
                    f"窗口内有效定位事件仅 {located} 条"
                    f"（低于 KDE 最小样本 {self._min_events} 条），"
                    f"暂无法生成可靠的密度面，请等待数据积累"
                ),
                "event_count": total,
                "located_count": located,
                "window_days": days,
            }

        # 加权核密度估计（Scott 带宽，随样本量自适应）
        from pyproj import Transformer
        projection = '+proj=laea +lat_0=19 +lon_0=96 +datum=WGS84 +units=m +no_defs'
        transform = Transformer.from_crs('EPSG:4326', projection, always_xy=True)
        event_x, event_y = transform.transform(lons, lats)
        positions_km = np.column_stack([event_x, event_y]) / 1000.0

        # 规则网格求值
        grid_lon = np.linspace(LON_RANGE[0], LON_RANGE[1], self._grid_cols)
        grid_lat = np.linspace(LAT_RANGE[0], LAT_RANGE[1], self._grid_rows)
        glon, glat = np.meshgrid(grid_lon, grid_lat)
        pts = np.vstack([glon.ravel(), glat.ravel()])

        grid_x, grid_y = transform.transform(pts[0], pts[1])
        grid_km = np.column_stack([grid_x, grid_y]) / 1000.0
        z = np.zeros(len(grid_km), dtype=float)
        # 固定各向同性公里带宽，分块求和；重复坐标和共线数据无需协方差求逆。
        for offset in range(0, len(positions_km), 128):
            distance2 = ((grid_km[:, None, :] - positions_km[None, offset:offset + 128, :]) ** 2).sum(axis=2)
            kernels = np.exp(-distance2 / (2 * self._bandwidth_km ** 2)) / (2 * np.pi * self._bandwidth_km ** 2)
            z += kernels @ np.asarray(weights[offset:offset + 128])
        observed_dates = [business_date(e['date']) for e in window_events]
        normalization_days = days or max(1, (max(observed_dates) - min(observed_dates)).days + 1)
        z /= normalization_days

        # 国界掩膜：境外的密度置零（事件点本身在境内，但核函数会向外扩散）
        z[~self._inside_country(pts.T)] = 0.0

        zmax = z.max()
        if zmax <= 0:
            return {
                "degraded": "密度面计算异常（掩膜后无有效密度）",
                "event_count": total,
                "located_count": located,
                "window_days": days,
            }
        absolute_z = z.copy().reshape(self._grid_rows, self._grid_cols)
        comparable_z = np.clip(absolute_z / self._color_max, 0, 1)
        z = z / zmax  # 归一化 0~1

        # 峰值位置
        peak_idx = int(z.argmax())
        peak_lat = float(glat.ravel()[peak_idx])
        peak_lon = float(glon.ravel()[peak_idx])

        # 输出非零网格点（阈值去噪，控制前端体积）
        zz = z.reshape(self._grid_rows, self._grid_cols)
        grid = []
        for i in range(self._grid_rows):
            for j in range(self._grid_cols):
                w = zz[i, j]
                if w > 0.02:
                    grid.append([round(float(glat[i, j]), 4),
                                 round(float(glon[i, j]), 4),
                                 round(float(w), 3)])

        return {
            "degraded": None,
            "grid": grid,
            "z_matrix": zz,
            "absolute_density": absolute_z,
            "comparable_z_matrix": comparable_z,
            "relative_density_note": "z_matrix仅供窗口内相对分布，不可跨窗口比较强弱",
            "unit": "加权事件/(平方公里·天)",
            "bandwidth_km": self._bandwidth_km,
            "color_max_per_km2_day": self._color_max,
            "algorithm_version": "kde-laea-v2",
            "projection": projection,
            "normalization_days": normalization_days,
            "reported_multi_source_count": verified,
            "quality_note": "多来源报道数不证明来源独立；不据此额外放大权重",
            "bbox": [[LAT_RANGE[0], LON_RANGE[0]],
                     [LAT_RANGE[1], LON_RANGE[1]]],
            "event_count": total,
            "located_count": located,
            "verified_count": verified,
            "peak_lat": peak_lat,
            "peak_lon": peak_lon,
            "window_days": days,
        }


# ============================================================
# 进程级单例
# ============================================================
_instance = None
_instance_lock = threading.Lock()


def get_event_density_analyzer() -> EventDensityAnalyzer:
    """获取全局事件密度分析器单例（线程安全）"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EventDensityAnalyzer()
    return _instance
