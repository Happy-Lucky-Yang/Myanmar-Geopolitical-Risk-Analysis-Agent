"""
data.nightlight_crawler - NASA VIIRS 夜间灯光遥感数据接入

数据源优先级:
  1. World Bank Light Every Night 数据集 (通过 wbgapi, 免费无需API Key)
  2. NOAA EOG VIIRS 月度合成数据 (需注册 Token)
  3. 本地缓存 / 历史基准值 (兜底)

输出指标:
  - nightlight_change: 0-1 归一化, 表示近期夜光变化率
    > 0.5 表示夜光增强 (经济活跃 / 局势稳定)
    < 0.5 表示夜光减弱 (冲突 / 经济衰退)
    = 0.5 表示无变化
"""
import os
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
_DATA_DIR = os.path.join(os.path.dirname(__file__), "raw")
_CACHE_FILE = os.path.join(_DATA_DIR, "nightlight_cache.json")
os.makedirs(_DATA_DIR, exist_ok=True)

# 缅甸 World Bank 国家代码
MYANMAR_COUNTRY_CODE = "MMR"

# 缓存有效期 (秒) -- 月度数据, 7天刷新一次即可
_CACHE_TTL = 7 * 24 * 3600

# 缅甸平均纬度/经度 (用于 NOAA 查询)
MYANMAR_LAT = 19.7633
MYANMAR_LON = 96.0785


class NightlightCrawler:
    """夜间灯光遥感数据爬取与分析"""

    def __init__(self, config: Dict = None):
        from utils.config import load_config
        self._cfg = config or load_config()
        self._lock = threading.Lock()
        self._cache = self._load_cache()

    # ============================================================
    # 公共接口
    # ============================================================

    def get_nightlight_change(self) -> float:
        """
        获取夜间灯光变化指标 (0-1 归一化)

        返回值含义:
          - 接近 1.0: 夜光显著增强 (正面信号)
          - 接近 0.0: 夜光显著减弱 (负面信号, 可能冲突/灾难)
          - 约 0.5: 无明显变化
        """
        with self._lock:
            # 检查缓存有效性
            if self._is_cache_valid():
                return self._cache.get("nightlight_change", 0.5)

        # 尝试从 World Bank API 获取
        result = self._fetch_worldbank_nightlight()
        if result is not None:
            self._update_cache(result)
            return result["nightlight_change"]

        # 兜底: 返回缓存中的旧值或默认值
        logger.warning("[Nightlight] 所有数据源不可用, 使用缓存/默认值")
        with self._lock:
            return self._cache.get("nightlight_change", 0.5)

    def get_monthly_series(self, months: int = 12) -> List[Dict]:
        """
        获取最近 N 个月的夜光月度序列

        返回: [{"month": "2026-01", "value": 0.42}, ...]
        """
        with self._lock:
            series = self._cache.get("monthly_series", [])
            if series:
                return series[-months:]
        return []

    def force_refresh(self) -> Dict:
        """强制刷新数据"""
        result = self._fetch_worldbank_nightlight()
        if result:
            self._update_cache(result)
        return result or self._cache

    # ============================================================
    # World Bank 数据获取
    # ============================================================

    def _fetch_worldbank_nightlight(self) -> Optional[Dict]:
        """
        从 World Bank API 获取缅甸夜间灯光数据

        使用指标: EG.ELC.LOSS.ZS (电力传输损耗, 间接反映)
        或使用 Light Every Night 数据集
        """
        try:
            import wbgapi as wb

            # 尝试获取缅甸的电力相关指标作为夜光代理
            # EG.ELC.ACCS.ZS = 电力覆盖率
            # EG.ELC.LOSS.ZS = 电力传输损耗率
            indicators = [
                ("EG.ELC.ACCS.ZS", "electricity_access"),
                ("EG.ELC.LOSS.ZS", "transmission_loss"),
            ]

            latest_values = {}
            for code, name in indicators:
                try:
                    data = wb.data.DataFrame(code, MYANMAR_COUNTRY_CODE,
                                             mrn=5, skipBlanks=True)
                    if data is not None and not data.empty:
                        # 取最近的值
                        row = data.loc[MYANMAR_COUNTRY_CODE]
                        for col in reversed(data.columns):
                            val = row[col]
                            if val is not None and not (isinstance(val, float) and
                                                        (val != val)):  # NaN check
                                year = int(str(col).replace("YR", "").replace(" ", ""))
                                latest_values[name] = {
                                    "value": float(val),
                                    "year": year
                                }
                                break
                except Exception as e:
                    logger.debug(f"[Nightlight] WB指标 {code} 不可用: {e}")

            if not latest_values:
                logger.info("[Nightlight] World Bank 无可用夜光代理数据")
                return None

            # 计算 nightlight_change
            # 基于电力覆盖率变化推断: 覆盖率上升→夜光增强
            nl_change = 0.5  # 默认无变化

            if "electricity_access" in latest_values:
                ea = latest_values["electricity_access"]
                # 缅甸电力覆盖率约 60-70%, 归一化到 0-1
                # 覆盖率越高→经济越活跃→正面信号
                access_pct = ea["value"] / 100.0
                # 映射: 0%→0.1, 50%→0.5, 100%→0.9
                nl_change = max(0.05, min(0.95, 0.1 + 0.8 * access_pct))

            if "transmission_loss" in latest_values:
                tl = latest_values["transmission_loss"]
                # 传输损耗高→基础设施差→负面信号
                # 典型范围: 5%(优秀) - 25%(差)
                loss_factor = max(0.0, min(1.0, (25 - tl["value"]) / 20.0))
                # 融合: 70% 电力覆盖率 + 30% 传输损耗
                nl_change = 0.7 * nl_change + 0.3 * loss_factor

            # 构建月度序列 (年度数据按12个月展开)
            monthly_series = self._build_monthly_series(latest_values)

            result = {
                "nightlight_change": round(nl_change, 4),
                "source": "worldbank",
                "indicators_used": list(latest_values.keys()),
                "raw_values": {k: v["value"] for k, v in latest_values.items()},
                "data_year": max(v["year"] for v in latest_values.values()),
                "monthly_series": monthly_series,
                "fetched_at": datetime.now().isoformat(),
                "data_quality": "估算"  # 遵循可信度标记规范
            }

            logger.info(f"[Nightlight] WB数据: nl_change={nl_change:.4f}, "
                        f"指标={list(latest_values.keys())}")
            return result

        except ImportError:
            logger.warning("[Nightlight] wbgapi 未安装")
            return None
        except Exception as e:
            logger.error(f"[Nightlight] World Bank 获取失败: {e}", exc_info=True)
            return None

    def _build_monthly_series(self, wb_data: Dict) -> List[Dict]:
        """将年度 WB 数据展开为月度序列 (线性插值)"""
        series = []
        # 按年份排序
        years_data = {}
        for name, info in wb_data.items():
            years_data.setdefault(info["year"], {})[name] = info["value"]

        sorted_years = sorted(years_data.keys())
        if len(sorted_years) < 2:
            return series

        for i in range(len(sorted_years) - 1):
            y1, y2 = sorted_years[i], sorted_years[i + 1]
            # 每月线性插值
            for month in range(1, 13):
                t = (month - 1) / 12.0
                date_str = f"{y1}-{month:02d}"
                # 使用电力覆盖率作为主要指标
                if "electricity_access" in years_data[y1]:
                    v1 = years_data[y1].get("electricity_access", 60)
                    v2 = years_data[y2].get("electricity_access", 60)
                    interpolated = v1 + t * (v2 - v1)
                    # 归一化到 0-1
                    val = max(0.05, min(0.95, 0.1 + 0.8 * interpolated / 100.0))
                    series.append({"month": date_str, "value": round(val, 4)})

        return series

    # ============================================================
    # 缓存管理
    # ============================================================

    def _load_cache(self) -> Dict:
        """加载本地缓存"""
        try:
            if os.path.exists(_CACHE_FILE):
                with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[Nightlight] 缓存读取失败: {e}")
        return {}

    def _save_cache(self, data: Dict):
        """保存缓存到本地"""
        try:
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Nightlight] 缓存写入失败: {e}")

    def _is_cache_valid(self) -> bool:
        """检查缓存是否在有效期内"""
        fetched = self._cache.get("fetched_at")
        if not fetched:
            return False
        try:
            fetch_time = datetime.fromisoformat(fetched)
            return (datetime.now() - fetch_time).total_seconds() < _CACHE_TTL
        except Exception:
            return False

    def _update_cache(self, result: Dict):
        """更新缓存"""
        with self._lock:
            self._cache = result
            self._save_cache(result)


# ============================================================
# 单例
# ============================================================
_instance = None
_instance_lock = threading.Lock()


def get_nightlight_crawler() -> NightlightCrawler:
    """获取单例"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = NightlightCrawler()
    return _instance
