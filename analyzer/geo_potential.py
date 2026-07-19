"""
analyzer.geo_potential - 地缘位势评估模块

process.html 第三层核心要求:
  "地缘位势评估: 距离加权打分模型（影响力 ∝ 1/距离²），多指标综合评分"
  "轻量算法库: ...空间自相关"

功能:
  1. 距离加权位势模型: 计算某地缘中心点对各省份的辐射影响力 (∝ 1/距离²)
  2. 多指标综合位势评分: 融合风险分 + 距离衰减 + 战略权重
  3. 空间自相关 (Moran's I): 衡量风险的空间聚集程度
  4. 热点识别: 识别高-高聚集区 (Getis-Ord 简化版)

集成: /api/geo_potential 接口, 综合态势仪表盘
"""
import logging
import math
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# 地缘战略中心点 (影响力辐射源)
# ============================================================
# 每个中心点代表一个地缘影响力来源, weight 表示战略重要性
STRATEGIC_CENTERS = {
    "中缅边境(瑞丽)": {"lat": 24.01, "lon": 97.85, "weight": 1.0,
                    "desc": "中缅陆路贸易与油气管道入口"},
    "皎漂港": {"lat": 19.43, "lon": 93.55, "weight": 0.9,
              "desc": "中缅经济走廊出海口/深水港"},
    "内比都": {"lat": 19.76, "lon": 96.07, "weight": 0.8,
              "desc": "缅甸首都/军政府中心"},
    "泰缅边境(妙瓦底)": {"lat": 16.69, "lon": 98.50, "weight": 0.7,
                     "desc": "缅泰贸易口岸/难民通道"},
    "仰光": {"lat": 16.87, "lon": 96.20, "weight": 0.75,
            "desc": "最大城市/经济中心"},
}


class GeoPotentialAnalyzer:
    """地缘位势评估器"""

    def __init__(self):
        self._provinces = None

    def _get_provinces(self) -> Dict[str, Tuple[float, float]]:
        """获取缅甸省份坐标 (复用 map_gen 定义)"""
        if self._provinces is None:
            from visualization.map_gen import MYANMAR_PROVINCES
            self._provinces = MYANMAR_PROVINCES
        return self._provinces

    # ============================================================
    # 距离加权位势模型
    # ============================================================

    def compute_potential(self, province_risk: Dict[str, float] = None) -> Dict:
        """
        计算各省份的地缘位势评分

        位势模型: 某省份的地缘位势 = Σ (中心点战略权重 / 距离²) × 该省风险分

        :param province_risk: {省份: 风险分(0-100)}, 若为None则从历史数据构建
        :return: {
            "provinces": [{province, lat, lon, risk_score, potential, dominant_center}],
            "centers": [...],
            "max_potential": float
        }
        """
        provinces = self._get_provinces()

        if province_risk is None:
            province_risk = self._build_province_risk()

        results = []
        for province, (lat, lon) in provinces.items():
            risk = province_risk.get(province, 50.0)

            # 计算来自各战略中心的影响力叠加
            total_influence = 0.0
            center_influences = {}
            for cname, cinfo in STRATEGIC_CENTERS.items():
                dist = self._haversine(lat, lon, cinfo["lat"], cinfo["lon"])
                # 影响力 ∝ weight / 距离² (距离下限 10km 避免除零/奇点)
                dist_km = max(dist, 10.0)
                influence = cinfo["weight"] / ((dist_km / 100.0) ** 2)
                center_influences[cname] = influence
                total_influence += influence

            # 地缘位势 = 影响力叠加 × 风险归一化
            potential = total_influence * (risk / 100.0)

            # 主导影响中心
            dominant = max(center_influences.items(), key=lambda x: x[1])[0] \
                if center_influences else None

            results.append({
                "province": province,
                "lat": lat,
                "lon": lon,
                "risk_score": round(risk, 2),
                "total_influence": round(total_influence, 4),
                "potential": round(potential, 4),
                "dominant_center": dominant,
            })

        # 按位势降序
        results.sort(key=lambda x: x["potential"], reverse=True)
        max_potential = max((r["potential"] for r in results), default=1.0)

        # 归一化位势到 0-100 便于展示
        for r in results:
            r["potential_normalized"] = round(
                (r["potential"] / max_potential) * 100 if max_potential > 0 else 0, 2
            )

        return {
            "provinces": results,
            "centers": [
                {"name": k, **v} for k, v in STRATEGIC_CENTERS.items()
            ],
            "max_potential": round(max_potential, 4),
            "model": "距离加权 (影响力 ∝ 战略权重/距离²)",
        }

    # ============================================================
    # 空间自相关 (Moran's I)
    # ============================================================

    def compute_spatial_autocorrelation(self, province_risk: Dict[str, float] = None) -> Dict:
        """
        计算全局 Moran's I 空间自相关指数

        Moran's I ∈ [-1, 1]:
          > 0: 正空间自相关 (相似值聚集, 高风险相邻)
          ≈ 0: 随机分布
          < 0: 负空间自相关 (高低相间)

        公式: I = (n/W) × (ΣΣ w_ij(x_i-x̄)(x_j-x̄)) / (Σ(x_i-x̄)²)
        权重 w_ij 使用距离倒数 (1/距离)
        """
        provinces = self._get_provinces()
        if province_risk is None:
            province_risk = self._build_province_risk()

        names = list(provinces.keys())
        n = len(names)
        if n < 3:
            return {"morans_i": 0.0, "interpretation": "数据不足", "n": n}

        values = [province_risk.get(name, 50.0) for name in names]
        mean_x = sum(values) / n

        # 构建空间权重矩阵 (距离倒数, 行标准化)
        W = 0.0
        numerator = 0.0
        for i in range(n):
            lat_i, lon_i = provinces[names[i]]
            for j in range(n):
                if i == j:
                    continue
                lat_j, lon_j = provinces[names[j]]
                dist = self._haversine(lat_i, lon_i, lat_j, lon_j)
                w_ij = 1.0 / max(dist, 1.0)  # 距离倒数权重
                W += w_ij
                numerator += w_ij * (values[i] - mean_x) * (values[j] - mean_x)

        denominator = sum((v - mean_x) ** 2 for v in values)

        if denominator < 1e-9 or W < 1e-9:
            morans_i = 0.0
        else:
            morans_i = (n / W) * (numerator / denominator)

        # 解读
        if morans_i > 0.3:
            interpretation = "显著正空间自相关：风险呈现明显地理聚集"
        elif morans_i > 0.1:
            interpretation = "弱正空间自相关：风险有一定聚集趋势"
        elif morans_i < -0.1:
            interpretation = "负空间自相关：高低风险区交错分布"
        else:
            interpretation = "空间随机分布：无明显聚集模式"

        return {
            "morans_i": round(morans_i, 4),
            "interpretation": interpretation,
            "mean_risk": round(mean_x, 2),
            "n": n,
        }

    def identify_hotspots(self, province_risk: Dict[str, float] = None,
                          threshold: float = 60.0) -> List[Dict]:
        """
        识别风险热点区 (高-高聚集: 自身高风险且邻近也高风险)

        :param threshold: 高风险阈值
        :return: 热点省份列表
        """
        provinces = self._get_provinces()
        if province_risk is None:
            province_risk = self._build_province_risk()

        names = list(provinces.keys())
        hotspots = []

        for name in names:
            risk = province_risk.get(name, 50.0)
            if risk < threshold:
                continue

            # 计算邻近省份 (最近3个) 的平均风险
            lat, lon = provinces[name]
            dists = []
            for other in names:
                if other == name:
                    continue
                olat, olon = provinces[other]
                d = self._haversine(lat, lon, olat, olon)
                dists.append((other, d))
            dists.sort(key=lambda x: x[1])
            neighbors = dists[:3]
            neighbor_risk = sum(province_risk.get(nb, 50.0) for nb, _ in neighbors) / max(len(neighbors), 1)

            # 高-高聚集判定
            if neighbor_risk >= threshold * 0.8:
                hotspots.append({
                    "province": name,
                    "risk_score": round(risk, 2),
                    "neighbor_avg_risk": round(neighbor_risk, 2),
                    "cluster_type": "高-高聚集" if neighbor_risk >= threshold else "高风险孤岛",
                    "neighbors": [nb for nb, _ in neighbors],
                })

        hotspots.sort(key=lambda x: x["risk_score"], reverse=True)
        return hotspots

    def full_analysis(self, province_risk: Dict[str, float] = None) -> Dict:
        """完整地缘位势分析 (位势 + 空间自相关 + 热点)"""
        if province_risk is None:
            province_risk = self._build_province_risk()

        potential = self.compute_potential(province_risk)
        autocorr = self.compute_spatial_autocorrelation(province_risk)
        hotspots = self.identify_hotspots(province_risk)

        return {
            "potential": potential,
            "spatial_autocorrelation": autocorr,
            "hotspots": hotspots,
            "top_potential_provinces": potential["provinces"][:5],
        }

    # ============================================================
    # 内部工具
    # ============================================================

    def _build_province_risk(self) -> Dict[str, float]:
        """从历史数据构建省级风险分 (复用 app.py 的边境逻辑作为降级)"""
        provinces = self._get_provinces()
        try:
            from analyzer.data_loader import get_data_loader
            loader = get_data_loader()
            history = loader.load_risk_history(days=7)
            avg_score = (sum(r["risk_score"] for r in history) / len(history)) \
                if history else 50.0
        except Exception:
            avg_score = 50.0

        border_provinces = ["掸邦", "克钦邦", "克伦邦", "若开邦", "钦邦"]
        result = {}
        for province in provinces:
            if province in border_provinces:
                result[province] = min(avg_score * 1.3, 100.0)
            else:
                result[province] = avg_score * 0.8
        return result

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """计算两点间球面距离 (km)"""
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (math.sin(dphi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
        return 2 * R * math.asin(math.sqrt(a))


# ============================================================
# 单例
# ============================================================
_instance = None
_lock = threading.Lock()


def get_geo_potential_analyzer() -> GeoPotentialAnalyzer:
    """获取全局地缘位势评估器单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = GeoPotentialAnalyzer()
    return _instance
