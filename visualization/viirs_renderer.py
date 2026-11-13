"""
visualization.viirs_renderer - VIIRS 夜光遥感图像渲染

功能:
  - 读取 EOG 月度 VIIRS 夜光 GeoTIFF（规则裁剪版）
  - 渲染为伪彩色图（深色背景 + 暖色灯光，适合学术答辩展示）
  - 叠加缅甸省级行政边界（GADM 4.1）
  - 支持 Web 内联（base64）和高清导出（300 DPI PNG）

数据源:
  C:\\Users\\10748\\Desktop\\缅甸地缘智能分析\\私密文件\\夜间灯光
  ├── MMR_VNL_规则裁剪版_2020-2023\\MMR_VNL_202001-202608\\data\\MMR_VNL_YYYYMM.tif
  └── MMR_VNL_规则裁剪版_2024-2026\\MMR_VNL_202001-202608\\data\\MMR_VNL_YYYYMM.tif
"""
import os
import glob
import logging
import base64
import io
import re
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# VIIRS 数据根目录（私密文件夹）
_VIIRS_ROOT = r"C:\Users\10748\Desktop\缅甸地缘智能分析\私密文件\夜间灯光"

# 搜索路径模式
_SEARCH_PATTERNS = [
    os.path.join(_VIIRS_ROOT, "MMR_VNL_规则裁剪版_2020-2023", "MMR_VNL_202001-202608", "data", "MMR_VNL_*.tif"),
    os.path.join(_VIIRS_ROOT, "MMR_VNL_规则裁剪版_2024-2026", "MMR_VNL_202001-202608", "data", "MMR_VNL_*.tif"),
]

# 缓存已扫描的文件列表
_file_cache: Optional[Dict[str, str]] = None


def _scan_files() -> Dict[str, str]:
    """扫描所有可用的 VIIRS 月度文件，返回 {YYYYMM: filepath} 字典。"""
    global _file_cache
    if _file_cache is not None:
        return _file_cache

    files = {}
    for pattern in _SEARCH_PATTERNS:
        for path in glob.glob(pattern):
            # 从文件名提取年月：MMR_VNL_202401.tif → 202401
            m = re.search(r'MMR_VNL_(\d{6})\.tif$', path)
            if m:
                ym = m.group(1)
                # 如果两个目录都有同一月份，优先取 2024-2026 包（更新版）
                if ym not in files or '2024-2026' in path:
                    files[ym] = path
    _file_cache = files
    logger.info(f"[VIIRS] 扫描到 {len(files)} 期月度夜光数据")
    return files


def list_available_months() -> List[str]:
    """返回所有可用月份列表（YYYY-MM 格式），按时间正序。"""
    files = _scan_files()
    months = []
    for ym in sorted(files.keys()):
        # 202401 → 2024-01
        months.append(f"{ym[:4]}-{ym[4:]}")
    return months


def get_file_path(month: str) -> Optional[str]:
    """获取指定月份的 GeoTIFF 文件路径。month 格式：YYYY-MM 或 YYYYMM。"""
    ym = month.replace('-', '')
    files = _scan_files()
    return files.get(ym)


def render_viirs(
    month: str,
    dpi: int = 150,
    show_boundaries: bool = True,
    figsize: Tuple[float, float] = (10, 8),
    colormap: str = 'inferno',
    title: Optional[str] = None,
) -> bytes:
    """
    渲染指定月份的 VIIRS 夜光图像为 PNG 字节流。

    参数:
        month: 月份，YYYY-MM 或 YYYYMM
        dpi: 输出分辨率（默认150，高清可设300）
        show_boundaries: 是否叠加省级行政边界
        figsize: 图像尺寸（英寸）
        colormap: matplotlib 色带名称
        title: 自定义标题，默认自动生成

    返回:
        PNG 图像的 bytes
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    import rasterio

    filepath = get_file_path(month)
    if filepath is None:
        raise ValueError(f"VIIRS 数据不存在: {month}")

    # 读取 GeoTIFF
    with rasterio.open(filepath) as src:
        data = src.read(1)  # 第一波段：cf_cvg 或 avg_rad
        transform = src.transform
        crs = src.crs
        nodata = src.nodata

    # 处理 nodata 和负值
    mask = (data == nodata) if nodata is not None else np.zeros_like(data, dtype=bool)
    data_float = data.astype(np.float32).copy()
    data_float[mask] = np.nan

    # 创建图形（深色背景风格）
    fig, ax = plt.subplots(1, 1, figsize=figsize, facecolor='#1a1a2e')
    ax.set_facecolor('#0d0d1a')

    # 计算图像范围（用于 extent）
    height, width = data.shape
    left = transform.c
    top = transform.f
    right = left + width * transform.a
    bottom = top + height * transform.e  # e 为负值

    # 使用对数色标增强夜光视觉效果
    vmin = np.nanpercentile(data_float[data_float > 0], 2) if np.any(data_float > 0) else 0.1
    vmax = np.nanpercentile(data_float, 98) if not np.all(np.isnan(data_float)) else 1.0

    norm = mcolors.LogNorm(vmin=max(vmin, 0.01), vmax=max(vmax, 1.0), clip=False)
    cmap = plt.colormaps.get_cmap(colormap)
    cmap.set_bad(color='#0d0d1a')  # nodata 区域用深色填充

    im = ax.imshow(
        data_float,
        extent=[left, right, bottom, top],
        cmap=cmap,
        norm=norm,
        interpolation='bilinear',
        alpha=0.95,
    )

    # 叠加省级行政边界
    if show_boundaries:
        try:
            from data.admin_boundaries import load_boundaries
            import json

            boundaries = load_boundaries(level=1)
            for feature in boundaries.get('features', []):
                geom = feature.get('geometry', {})
                if geom.get('type') == 'Polygon':
                    coords = geom['coordinates'][0]
                    path = Path(coords)
                    patch = PathPatch(path, facecolor='none', edgecolor='#00ffcc',
                                     linewidth=0.8, alpha=0.7, transform=ax.transData)
                    ax.add_patch(patch)
                elif geom.get('type') == 'MultiPolygon':
                    for polygon in geom['coordinates']:
                        coords = polygon[0]
                        path = Path(coords)
                        patch = PathPatch(path, facecolor='none', edgecolor='#00ffcc',
                                         linewidth=0.8, alpha=0.7, transform=ax.transData)
                        ax.add_patch(patch)
        except Exception as e:
            logger.warning(f"[VIIRS] 边界叠加失败: {e}")

    # 设置标题
    if title is None:
        ym = month.replace('-', '')
        title = f"Myanmar VIIRS Nightlight — {ym[:4]}/{ym[4:]}"
    ax.set_title(title, color='white', fontsize=14, fontweight='bold', pad=12)

    # 添加色标
    cbar = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.02)
    cbar.ax.tick_params(colors='white', labelsize=9)
    cbar.set_label('Radiance (nW/cm²/sr)', color='white', fontsize=10)

    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_xticks([])
    ax.set_yticks([])

    # 导出为 PNG
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_to_base64(month: str, dpi: int = 100, **kwargs) -> str:
    """渲染并返回 base64 编码的 data URI（用于网页内联显示）。"""
    png_bytes = render_viirs(month, dpi=dpi, **kwargs)
    b64 = base64.b64encode(png_bytes).decode('ascii')
    return f"data:image/png;base64,{b64}"


def save_hd(month: str, output_dir: str, dpi: int = 300, **kwargs) -> str:
    """渲染高清图像并保存到文件，返回文件路径。"""
    png_bytes = render_viirs(month, dpi=dpi, figsize=(14, 11), **kwargs)
    ym = month.replace('-', '')
    filename = f"VIIRS_Myanmar_{ym}_HD.png"
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'wb') as f:
        f.write(png_bytes)
    logger.info(f"[VIIRS] 高清图像已保存: {filepath}")
    return filepath


# 简单测试
if __name__ == '__main__':
    months = list_available_months()
    print(f"可用月份: {len(months)} 期")
    if months:
        print(f"最新: {months[-1]}")
        latest = months[-1].replace('-', '')
        path = get_file_path(latest)
        print(f"文件: {path}")
