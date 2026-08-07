"""
data.admin_boundaries - 缅甸行政边界数据加载（GADM 4.1）

数据来源：GADM 4.1（https://gadm.org），仅限学术/非商业用途，
论文与开源分发时须注明来源。文件位于 data/static/gadm/：
    gadm41_MMR_0.json  国界（1 个多边形）
    gadm41_MMR_1.json  邦/省一级（15 个：7 邦 + 7 省 + 内比都联邦区）
    gadm41_MMR_2.json  县（63 个）
    gadm41_MMR_3.json  镇区（286 个）

用途：风险地图真实省界渲染、VIIRS 夜光分区统计、KDE 密度面裁剪。
"""
import os
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_GADM_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "static", "gadm"
)

# GADM 英文行政区划名（NAME_1） ↔ 系统中文名（与 map_gen.MYANMAR_PROVINCES 一致）
PROVINCE_EN2CN = {
    "Yangon": "仰光省",
    "Mandalay": "曼德勒省",
    "Naypyitaw": "内比都",
    "Shan": "掸邦",
    "Kachin": "克钦邦",
    "Kayin": "克伦邦",
    "Chin": "钦邦",
    "Kayah": "克耶邦",
    "Mon": "孟邦",
    "Rakhine": "若开邦",
    "Bago": "勃固省",
    "Magway": "马圭省",
    "Sagaing": "实皆省",
    "Tanintharyi": "德林达依省",
    "Ayeyarwady": "伊洛瓦底省",
}
PROVINCE_CN2EN = {v: k for k, v in PROVINCE_EN2CN.items()}

_cache: Dict[int, dict] = {}


def load_boundaries(level: int = 1) -> dict:
    """
    加载指定层级的 GeoJSON FeatureCollection（进程内缓存）

    :param level: 0=国界 1=邦/省 2=县 3=镇区
    :return: GeoJSON 字典
    """
    if level in _cache:
        return _cache[level]
    path = os.path.join(_GADM_DIR, f"gadm41_MMR_{level}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"GADM 边界文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _cache[level] = data
    return data


def get_province_features() -> List[dict]:
    """返回邦/省一级（Level 1）的 feature 列表"""
    return load_boundaries(1)["features"]


def province_centroids() -> Dict[str, tuple]:
    """
    计算各邦/省几何质心（经纬度），中文名索引

    用多边形外环顶点均值近似，可替代 map_gen 中手写的固定坐标，
    使地图标注落在真实边界内部。

    :return: {中文名: (lat, lon)}
    """
    centroids = {}
    for feat in get_province_features():
        name_en = feat["properties"].get("NAME_1", "")
        name_cn = PROVINCE_EN2CN.get(name_en, name_en)

        # 取最大多边形的第一个外环（Polygon 或 MultiPolygon 均兼容）
        geom = feat["geometry"]
        if geom["type"] == "Polygon":
            ring = geom["coordinates"][0]
        else:  # MultiPolygon
            ring = max(geom["coordinates"], key=lambda p: len(p[0]))[0]

        lons = [pt[0] for pt in ring]
        lats = [pt[1] for pt in ring]
        centroids[name_cn] = (sum(lats) / len(lats), sum(lons) / len(lons))
    return centroids


def get_province_polygon(name: str) -> Optional[dict]:
    """
    按中文名或英文名取单个邦/省的多边形 feature

    :param name: 中文名（掸邦）或 GADM 英文名（Shan）
    :return: feature 字典，未找到返回 None
    """
    name_en = name if name in PROVINCE_EN2CN else PROVINCE_CN2EN.get(name)
    for feat in get_province_features():
        if feat["properties"].get("NAME_1") == name_en:
            return feat
    return None
