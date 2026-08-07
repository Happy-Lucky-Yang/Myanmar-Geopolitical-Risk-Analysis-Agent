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
        self._mask_paths = None  # 惰性加载国界掩膜

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
        inside = np.zeros(len(lonlat), dtype=bool)
        for path in self._get_mask_paths():
            inside |= path.contains_points(lonlat)
        return inside

    # ============================================================
    # 核心计算
    # ============================================================

    def compute(self, events: List[Dict], days: int = None) -> Dict:
        """
        计算事件密度面

        :param events: GDELT 事件列表（需含 lat/lon，None 坐标自动剔除）
        :param days: 仅统计最近 N 天事件（按 SQLDATE 过滤），None=全部
        :return: {
            "degraded": 降级原因或 None,
            "grid": [[lat, lon, weight(0~1)], ...] 供热力图渲染,
            "event_count": 统计窗口内事件总数,
            "located_count": 其中含有效坐标数,
            "peak_lat"/"peak_lon": 密度峰值位置,
            "window_days": 过滤窗口
        }
        """
        from data.gdelt_files import event_severity_weight

        # 时间窗口过滤
        cutoff = None
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        window_events = [
            ev for ev in (events or [])
            if cutoff is None or ev.get("date", "") >= cutoff
        ]

        lats, lons, weights = [], [], []
        for ev in window_events:
            if ev.get("lat") is None or ev.get("lon") is None:
                continue
            lats.append(float(ev["lat"]))
            lons.append(float(ev["lon"]))
            weights.append(max(0.05, event_severity_weight(ev)))

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
        from scipy.stats import gaussian_kde
        positions = np.vstack([lons, lats])
        kde = gaussian_kde(positions, weights=np.asarray(weights))

        # 规则网格求值
        grid_lon = np.linspace(LON_RANGE[0], LON_RANGE[1], self._grid_cols)
        grid_lat = np.linspace(LAT_RANGE[0], LAT_RANGE[1], self._grid_rows)
        glon, glat = np.meshgrid(grid_lon, grid_lat)
        pts = np.vstack([glon.ravel(), glat.ravel()])

        z = kde(pts)

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
            "event_count": total,
            "located_count": located,
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
