"""
visualization.viirs_renderer - VIIRS 夜光遥感图像渲染

功能:
  - 读取 EOG 月度 VIIRS 夜光 GeoTIFF（波段1 avg_rad 平均辐亮度，波段2 cf_cvg 无云观测次数）
  - 优先使用矢量裁剪版（国界外为 nodata）；缺失时退回规则裁剪版并用 GADM L0 国界掩膜
  - cf_cvg=0 的像元是"本月没有有效观测"，不是"没有灯光"，单独用灰色标出
  - 固定对数色标（0.5–60 nW/cm²/sr），不同月份颜色可直接比较
  - 叠加缅甸国界与省级行政边界（GADM 4.1）
  - 支持 Web 内联（base64）、缩略图和高清导出（300 DPI PNG）

数据源（可用环境变量 VIIRS_ROOT 覆盖根目录）:
  C:\\Users\\10748\\Desktop\\缅甸地缘智能分析\\私密文件\\夜间灯光
  ├── MMR_VNL_矢量裁剪版\\MMR_VNL_202001-202608\\data_mmr\\MMR_VNL_YYYYMM.tif
  ├── MMR_VNL_规则裁剪版_2020-2023\\MMR_VNL_202001-202608\\data\\MMR_VNL_YYYYMM.tif
  └── MMR_VNL_规则裁剪版_2024-2026\\MMR_VNL_202001-202608\\data\\MMR_VNL_YYYYMM.tif
"""
import os
import glob
import logging
import base64
import io
import re
import threading
import warnings
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# VIIRS 数据根目录（私密文件夹，不在仓库内）
_DEFAULT_ROOT = r"C:\Users\10748\Desktop\缅甸地缘智能分析\私密文件\夜间灯光"

# 固定色标范围：低于 0.5 视为背景噪声（气辉/杂散光），60 以上为仰光等城市核心
RAD_MIN = 0.5
RAD_MAX = 60.0
_CBAR_TICKS = [0.5, 1, 2, 5, 10, 20, 50]

_BG = '#1a1a2e'
_NO_OBS = '#59606e'
_BOUNDARY = '#4fd1c5'

_SOURCE_LABELS = {
    'vector': '矢量裁剪版（国界外为 nodata）',
    'rule': '规则裁剪版 + GADM 国界掩膜',
}

# 渲染锁：matplotlib 不是线程安全的，Flask 多线程并发请求必须串行出图
_render_lock = threading.Lock()
_cache_lock = threading.Lock()
_png_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_grid_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_stats_cache: Dict[tuple, dict] = {}
_PNG_CACHE_SIZE = 64
_GRID_CACHE_SIZE = 12

# 缓存已扫描的文件列表：{root: {YYYYMM: (path, kind)}}
_file_cache: Dict[str, Dict[str, Tuple[str, str]]] = {}
_ring_cache: Dict[Tuple[int, float, float], list] = {}


def viirs_root() -> str:
    return os.environ.get('VIIRS_ROOT', '').strip() or _DEFAULT_ROOT


def reset_cache() -> None:
    """清空文件扫描与渲染缓存（数据目录变化或测试时使用）。"""
    with _cache_lock:
        _file_cache.clear()
        _png_cache.clear()
        _grid_cache.clear()
        _stats_cache.clear()


def _scan_files() -> Dict[str, Tuple[str, str]]:
    """扫描所有可用的 VIIRS 月度文件，返回 {YYYYMM: (filepath, kind)}，矢量裁剪版优先。"""
    root = viirs_root()
    if root in _file_cache:
        return _file_cache[root]

    patterns = [
        ('rule', os.path.join(root, 'MMR_VNL_规则裁剪版_*', '*', 'data', 'MMR_VNL_*.tif')),
        ('vector', os.path.join(root, 'MMR_VNL_矢量裁剪版', '*', 'data_mmr', 'MMR_VNL_*.tif')),
    ]
    files: Dict[str, Tuple[str, str]] = {}
    for kind, pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            m = re.search(r'MMR_VNL_(\d{6})\.tif$', path)
            if not m:
                continue
            ym = m.group(1)
            # 后扫描的矢量版覆盖规则版；规则版两个分包重复时取 2024-2026（更新版）
            if kind == 'vector' or ym not in files or '2024-2026' in path:
                files[ym] = (path, kind)
    _file_cache[root] = files
    logger.info(f"[VIIRS] 扫描到 {len(files)} 期月度夜光数据（{root}）")
    return files


def normalize_month(month: str) -> Optional[str]:
    """YYYY-MM / YYYYMM → YYYYMM；格式非法返回 None。"""
    m = re.fullmatch(r'(\d{4})-?(\d{2})', (month or '').strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    return m.group(1) + m.group(2)


def list_available_months() -> List[str]:
    """返回所有可用月份列表（YYYY-MM 格式），按时间正序。"""
    return [f"{ym[:4]}-{ym[4:]}" for ym in sorted(_scan_files())]


def missing_months() -> List[str]:
    """首末月份之间缺失的月份（YYYY-MM）。"""
    yms = sorted(_scan_files())
    if not yms:
        return []
    have, out = set(yms), []
    y, mo = int(yms[0][:4]), int(yms[0][4:])
    while f"{y}{mo:02d}" <= yms[-1]:
        if f"{y}{mo:02d}" not in have:
            out.append(f"{y}-{mo:02d}")
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return out


def get_file_path(month: str) -> Optional[str]:
    """获取指定月份的 GeoTIFF 文件路径。month 格式：YYYY-MM 或 YYYYMM。"""
    ym = normalize_month(month)
    entry = _scan_files().get(ym) if ym else None
    return entry[0] if entry else None


def status() -> dict:
    """遥感模块健康状态：数据目录、期数、缺月、依赖。"""
    root = viirs_root()
    files = _scan_files()
    kinds = [kind for _, kind in files.values()]
    try:
        import rasterio  # noqa: F401
        renderer_ok, renderer_error = True, None
    except Exception as exc:
        renderer_ok, renderer_error = False, f"缺少 rasterio：{exc}"
    months = list_available_months()
    return {
        'root_exists': os.path.isdir(root),
        'months_count': len(months),
        'first': months[0] if months else None,
        'last': months[-1] if months else None,
        'missing': missing_months(),
        'sources': {'vector': kinds.count('vector'), 'rule': kinds.count('rule')},
        'renderer_ok': renderer_ok,
        'renderer_error': renderer_error,
        'scale': {'min': RAD_MIN, 'max': RAD_MAX, 'unit': 'nW/cm²/sr', 'type': 'log'},
        'healthy': bool(months) and renderer_ok,
    }


def _boundary_rings(level: int, min_extent: float = 0.0, tolerance: float = 0.0) -> list:
    """GADM 边界环（外环 + 内环）的坐标数组，进程内缓存。

    丹老群岛、伊洛瓦底三角洲海岸线极度曲折，原样描边会糊成一片色块：
    tolerance（度）做 Douglas-Peucker 简化，min_extent（度）丢弃小岛环。
    """
    key = (level, min_extent, tolerance)
    if key in _ring_cache:
        return _ring_cache[key]
    from data.admin_boundaries import load_boundaries
    rings = []
    for feature in load_boundaries(level=level).get('features', []):
        geom = feature.get('geometry') or {}
        if tolerance > 0:
            try:
                from shapely.geometry import mapping, shape
                geom = mapping(shape(geom).simplify(tolerance, preserve_topology=True))
            except Exception as exc:
                logger.debug(f"[VIIRS] 边界简化不可用，使用原始坐标: {exc}")
        polys = [geom.get('coordinates', [])] if geom.get('type') == 'Polygon' else geom.get('coordinates', [])
        for poly in polys:
            for ring in poly:
                arr = np.asarray(ring, dtype=float)
                if len(arr) > 2 and np.ptp(arr[:, :2], axis=0).max() >= min_extent:
                    rings.append(arr[:, :2])
    _ring_cache[key] = rings
    return rings


def _block_reduce(arr: np.ndarray, factor: int, fn) -> np.ndarray:
    """按 factor×factor 块聚合（不足整块处用 NaN 补齐）。"""
    if factor <= 1:
        return arr
    h, w = arr.shape
    ph, pw = -h % factor, -w % factor
    if ph or pw:
        arr = np.pad(arr, ((0, ph), (0, pw)), constant_values=np.nan)
    blocks = arr.reshape(arr.shape[0] // factor, factor, arr.shape[1] // factor, factor)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return fn(blocks, axis=(1, 3))


def _load_grid(ym: str, max_rows: int) -> dict:
    """读取并降采样到不超过 max_rows 行，返回绘图所需数组与全分辨率统计。"""
    path, kind = _scan_files()[ym]
    key = (path, os.path.getmtime(path), max_rows)
    with _cache_lock:
        if key in _grid_cache:
            _grid_cache.move_to_end(key)
            return _grid_cache[key]

    import rasterio
    with rasterio.open(path) as src:
        rad = src.read(1).astype(np.float32)
        cvg = src.read(2).astype(np.float32) if src.count >= 2 else None
        transform, nodata, bounds = src.transform, src.nodata, src.bounds

    inside = np.isfinite(rad)
    if nodata is not None:
        inside &= rad != nodata
    if kind == 'rule':
        # 规则版是矩形外包框，含泰国/孟加拉/印度/中国灯光，必须按国界掩膜
        try:
            from rasterio.features import geometry_mask
            from data.admin_boundaries import load_boundaries
            geoms = [f['geometry'] for f in load_boundaries(level=0).get('features', [])]
            inside &= geometry_mask(geoms, out_shape=rad.shape, transform=transform, invert=True)
        except Exception as exc:
            logger.warning(f"[VIIRS] 规则版国界掩膜失败，将显示外包框全部像元: {exc}")
    observed = inside & (cvg > 0) if cvg is not None else inside.copy()
    no_obs = inside & ~observed

    land = int(inside.sum())
    obs_rad = rad[observed]
    stats = {
        'land_pixels': land,
        'observed_pixels': int(observed.sum()),
        'coverage_pct': round(100.0 * observed.sum() / land, 1) if land else 0.0,
        'mean_rad': round(float(np.mean(np.clip(obs_rad, 0, None))), 3) if obs_rad.size else None,
        'lit_pct': round(100.0 * float((obs_rad >= RAD_MIN).sum()) / obs_rad.size, 2) if obs_rad.size else None,
        'max_rad': round(float(obs_rad.max()), 2) if obs_rad.size else None,
    }

    factor = max(1, int(np.ceil(rad.shape[0] / max_rows)))
    rad_obs = np.where(observed, rad, np.nan)
    no_obs_f = np.where(inside, no_obs.astype(np.float32), np.nan)
    grid = {
        'rad': _block_reduce(rad_obs, factor, np.nanmean),
        'no_obs': _block_reduce(no_obs_f, factor, np.nanmean),
        'extent': [bounds.left, bounds.right, bounds.bottom, bounds.top],
        'kind': kind,
        'stats': stats,
    }
    with _cache_lock:
        _grid_cache[key] = grid
        _stats_cache[key[:2]] = stats
        while len(_grid_cache) > _GRID_CACHE_SIZE:
            _grid_cache.popitem(last=False)
    return grid


def render_meta(month: str) -> dict:
    """返回指定月份的数据来源与像元统计（按像元计数，非等积面积）。"""
    ym = normalize_month(month)
    if not ym or ym not in _scan_files():
        raise ValueError(f"VIIRS 数据不存在: {month}")
    path, kind = _scan_files()[ym]
    # 统计是全分辨率的，与降采样尺度无关：已渲染过就直接复用，避免再读一遍 GeoTIFF
    stats = _stats_cache.get((path, os.path.getmtime(path)))
    if stats is None:
        stats = _load_grid(ym, 1320)['stats']
    return {
        'month': f"{ym[:4]}-{ym[4:]}",
        'source': kind,
        'source_label': _SOURCE_LABELS[kind],
        **stats,
    }


def render_viirs(
    month: str,
    dpi: int = 150,
    show_boundaries: bool = True,
    figsize: Tuple[float, float] = (10, 8),
    colormap: str = 'inferno',
    title: Optional[str] = None,
    compact: bool = False,
) -> bytes:
    """
    渲染指定月份的 VIIRS 夜光图像为 PNG 字节流。

    参数:
        month: 月份，YYYY-MM 或 YYYYMM
        dpi: 输出分辨率（默认150，高清可设300）
        show_boundaries: 是否叠加国界与省级行政边界
        figsize: 图像尺寸（英寸）
        colormap: matplotlib 色带名称
        title: 自定义标题，默认自动生成
        compact: 缩略图模式（无标题、色标与图例）

    返回:
        PNG 图像的 bytes
    """
    ym = normalize_month(month)
    if not ym or ym not in _scan_files():
        raise ValueError(f"VIIRS 数据不存在: {month}")

    cache_key = (ym, dpi, tuple(figsize), show_boundaries, colormap, title, compact, viirs_root())
    with _cache_lock:
        if cache_key in _png_cache:
            _png_cache.move_to_end(cache_key)
            return _png_cache[cache_key]

    import matplotlib
    import matplotlib.colors as mcolors
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import LineCollection
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch
    from matplotlib.ticker import FixedFormatter, FixedLocator

    # 降采样到略高于输出像素，避免对 1000 万像元做插值（原实现单张 15–20 秒）
    max_rows = max(200, int(figsize[1] * dpi * 1.1))
    grid = _load_grid(ym, max_rows)
    left, right, bottom, top = grid['extent']

    with _render_lock:
        fig = Figure(figsize=figsize, facecolor=_BG)
        FigureCanvasAgg(fig)
        if compact:
            ax = fig.add_axes([0, 0, 1, 1])
        else:
            ax = fig.add_subplot(1, 1, 1)
        ax.set_facecolor(_BG)

        # 无有效观测（cf_cvg=0）的国内像元：灰色，避免被误读为"断电/无灯光"
        no_obs = np.ma.masked_less(np.nan_to_num(grid['no_obs'], nan=0.0), 0.5)
        ax.imshow(no_obs, extent=grid['extent'], cmap=mcolors.ListedColormap([_NO_OBS]),
                  vmin=0, vmax=1, interpolation='nearest', zorder=1)

        # 国内有观测像元：固定对数色标
        rad = np.ma.masked_invalid(np.clip(grid['rad'], RAD_MIN, None))
        cmap = matplotlib.colormaps[colormap].with_extremes(bad=(0, 0, 0, 0))
        norm = mcolors.LogNorm(vmin=RAD_MIN, vmax=RAD_MAX, clip=True)
        im = ax.imshow(rad, extent=grid['extent'], cmap=cmap, norm=norm,
                       interpolation='nearest', zorder=2)

        if show_boundaries:
            try:
                if not compact:
                    ax.add_collection(LineCollection(_boundary_rings(1, 0.15, 0.01), colors=_BOUNDARY,
                                                     linewidths=0.4, alpha=0.5, zorder=3))
                ax.add_collection(LineCollection(_boundary_rings(0, 0.3 if compact else 0.1,
                                                                 0.04 if compact else 0.015),
                                                 colors=_BOUNDARY, linewidths=0.5 if compact else 0.7,
                                                 alpha=0.8, zorder=4))
            except Exception as e:
                logger.warning(f"[VIIRS] 边界叠加失败: {e}")

        ax.set_xlim(left, right)
        ax.set_ylim(bottom, top)
        # 经纬度网格按中纬度余弦校正纵横比，避免缅甸被横向拉宽
        ax.set_aspect(1.0 / np.cos(np.deg2rad((top + bottom) / 2)))
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

        if not compact:
            stats = grid['stats']
            ax.set_title(title or f"Myanmar VIIRS Nightlight — {ym[:4]}/{ym[4:]}",
                         color='white', fontsize=14, fontweight='bold', pad=22)
            ax.text(0.5, 1.012, f"EOG VIIRS DNB monthly · avg_rad · valid observations "
                    f"{stats['coverage_pct']:.0f}% of land pixels",
                    transform=ax.transAxes, ha='center', va='bottom', color='#cfdee5', fontsize=9)
            cbar = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.02, extend='max')
            cbar.ax.yaxis.set_major_locator(FixedLocator(_CBAR_TICKS))
            cbar.ax.yaxis.set_major_formatter(FixedFormatter([f"{t:g}" for t in _CBAR_TICKS]))
            cbar.ax.yaxis.set_minor_locator(FixedLocator([]))
            cbar.ax.tick_params(colors='white', labelsize=9)
            cbar.outline.set_edgecolor('#59606e')
            cbar.set_label('avg_rad (nW/cm²/sr), fixed log scale', color='white', fontsize=10)
            legend = ax.legend(handles=[Patch(facecolor=_NO_OBS, label='No valid observation (cf_cvg = 0)')],
                               loc='lower left', fontsize=8, frameon=True, facecolor=_BG,
                               edgecolor='#59606e', labelcolor='white')
            legend.set_zorder(5)

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, bbox_inches=None if compact else 'tight',
                    facecolor=_BG, edgecolor='none')
    png = buf.getvalue()

    with _cache_lock:
        _png_cache[cache_key] = png
        while len(_png_cache) > _PNG_CACHE_SIZE:
            _png_cache.popitem(last=False)
    return png


def render_thumbnail(month: str, dpi: int = 80) -> bytes:
    """画廊缩略图：只画国界，无标题/色标。"""
    return render_viirs(month, dpi=dpi, figsize=(3.2, 4.0), compact=True)


def render_to_base64(month: str, dpi: int = 100, **kwargs) -> str:
    """渲染并返回 base64 编码的 data URI（用于网页内联显示）。"""
    png_bytes = render_viirs(month, dpi=dpi, **kwargs)
    b64 = base64.b64encode(png_bytes).decode('ascii')
    return f"data:image/png;base64,{b64}"


def warm_up(thumbs: int = 6) -> None:
    """后台预热：渲染最新月主图与画廊缩略图，避免首个访客等冷启动（导入 + 边界简化 + 读图）。"""
    def _run():
        try:
            months = list_available_months()
            if not months or not status()['renderer_ok']:
                return
            render_viirs(months[-1], dpi=150)
            render_meta(months[-1])
            for m in reversed(months[-thumbs:]):
                render_thumbnail(m)
            logger.info(f"[VIIRS] 预热完成：{months[-1]} 主图 + {min(thumbs, len(months))} 张缩略图")
        except Exception as exc:
            logger.warning(f"[VIIRS] 预热失败（不影响按需渲染）: {exc}")
    threading.Thread(target=_run, name='viirs-warmup', daemon=True).start()


def save_hd(month: str, output_dir: str, dpi: int = 300, **kwargs) -> str:
    """渲染高清图像并保存到文件，返回文件路径。"""
    png_bytes = render_viirs(month, dpi=dpi, figsize=(14, 11), **kwargs)
    ym = normalize_month(month)
    filename = f"VIIRS_Myanmar_{ym}_HD.png"
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'wb') as f:
        f.write(png_bytes)
    logger.info(f"[VIIRS] 高清图像已保存: {filepath}")
    return filepath


# 简单测试
if __name__ == '__main__':
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    months = list_available_months()
    if months:
        print(f"最新: {months[-1]} → {get_file_path(months[-1])}")
        print(json.dumps(render_meta(months[-1]), ensure_ascii=False, indent=2))
