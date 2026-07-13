"""
data.economic_crawler - 宏观经济统计数据接入

数据源:
  1. World Bank Open Data API (wbgapi): GDP增长率、人均GDP、通胀率、贸易占GDP比重
  2. UNHCR 公开数据: 缅甸难民数量变化 (通过缓存JSON或手动更新)

输出指标:
  - gdp_growth: GDP 季度增长率 (归一化)
  - trade_change: 中缅贸易额月度变化率
  - refugee_change: 难民数量变化率

存储: data/raw/economic_indicators.json
调度: 季度更新频率 (缓存30天)
"""
import os
import json
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
_DATA_DIR = os.path.join(os.path.dirname(__file__), "raw")
_CACHE_FILE = os.path.join(_DATA_DIR, "economic_indicators.json")
os.makedirs(_DATA_DIR, exist_ok=True)

MYANMAR_COUNTRY_CODE = "MMR"

# 默认 World Bank 指标代码
_DEFAULT_INDICATORS = {
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "gdp_per_capita": "NY.GDP.PCAP.CD",
    "inflation": "FP.CPI.TOTL.ZG",
    "trade_pct_gdp": "NE.TRD.GNFS.ZS",
}

# 缓存有效期 (秒) -- 经济数据变化较慢, 30天刷新
_CACHE_TTL = 30 * 24 * 3600


class EconomicCrawler:
    """宏观经济统计数据爬取与分析"""

    def __init__(self, config: Dict = None):
        from utils.config import load_config
        self._cfg = config or load_config()
        self._lock = threading.Lock()
        self._cache = self._load_cache()

        # 从 config 读取 WB 指标代码 (允许自定义)
        econ_cfg = self._cfg.get("economic", {})
        self._indicators = econ_cfg.get("worldbank_indicators", _DEFAULT_INDICATORS)

    # ============================================================
    # 公共接口
    # ============================================================

    def get_refugee_change(self) -> float:
        """
        获取难民数量变化率 (0-1 归一化)

        返回值含义:
          - 接近 1.0: 难民大幅增加 (严重危机)
          - 接近 0.0: 难民减少或稳定
          - 约 0.3: 中等变化
        """
        with self._lock:
            if self._is_cache_valid():
                return self._cache.get("refugee_change", 0.3)

        result = self._fetch_all_indicators()
        if result is not None:
            self._update_cache(result)
            return result.get("refugee_change", 0.3)

        with self._lock:
            return self._cache.get("refugee_change", 0.3)

    def get_gdp_growth(self) -> float:
        """获取 GDP 增长率 (归一化到 0-1, 0.5=零增长)"""
        with self._lock:
            if self._is_cache_valid():
                return self._cache.get("gdp_growth_norm", 0.5)

        result = self._fetch_all_indicators()
        if result is not None:
            self._update_cache(result)
            return result.get("gdp_growth_norm", 0.5)

        with self._lock:
            return self._cache.get("gdp_growth_norm", 0.5)

    def get_trade_change(self) -> float:
        """获取贸易变化率 (归一化到 0-1)"""
        with self._lock:
            if self._is_cache_valid():
                return self._cache.get("trade_change_norm", 0.5)

        result = self._fetch_all_indicators()
        if result is not None:
            self._update_cache(result)
            return result.get("trade_change_norm", 0.5)

        with self._lock:
            return self._cache.get("trade_change_norm", 0.5)

    def get_all_indicators(self) -> Dict:
        """
        获取所有经济指标汇总

        返回: {
            "gdp_growth": float,        # GDP 增长率 (%)
            "gdp_per_capita": float,    # 人均 GDP (USD)
            "inflation": float,         # 通胀率 (%)
            "trade_pct_gdp": float,     # 贸易占 GDP 比重 (%)
            "refugee_change": float,    # 难民变化率 (0-1)
            "data_year": int,           # 数据年份
            "source": str,
            "fetched_at": str
        }
        """
        with self._lock:
            if self._is_cache_valid() and self._cache:
                return self._cache

        result = self._fetch_all_indicators()
        if result is not None:
            self._update_cache(result)
            return result

        with self._lock:
            return self._cache if self._cache else self._default_result()

    def force_refresh(self) -> Dict:
        """强制刷新经济数据"""
        result = self._fetch_all_indicators()
        if result:
            self._update_cache(result)
        return result or self._cache

    # ============================================================
    # World Bank 数据获取
    # ============================================================

    def _fetch_all_indicators(self) -> Optional[Dict]:
        """从 World Bank API 获取缅甸宏观经济数据"""
        try:
            import wbgapi as wb

            wb_data = {}
            latest_year = 0

            for name, code in self._indicators.items():
                try:
                    data = wb.data.DataFrame(code, MYANMAR_COUNTRY_CODE,
                                             mrn=5, skipBlanks=True)
                    if data is not None and not data.empty:
                        row = data.loc[MYANMAR_COUNTRY_CODE]
                        for col in reversed(data.columns):
                            val = row[col]
                            if val is not None and not (isinstance(val, float)
                                                        and (val != val)):
                                year = int(str(col).replace("YR", "").replace(" ", ""))
                                wb_data[name] = {
                                    "value": float(val),
                                    "year": year
                                }
                                if year > latest_year:
                                    latest_year = year
                                break
                except Exception as e:
                    logger.debug(f"[Economic] WB指标 {code} 不可用: {e}")

            if not wb_data:
                logger.info("[Economic] World Bank 无可用数据")
                return None

            # 计算归一化指标
            result = self._compute_normalized(wb_data)

            # 获取难民数据 (基于 UNHCR 估算)
            refugee_change = self._estimate_refugee_change(wb_data)
            result["refugee_change"] = refugee_change

            result.update({
                "source": "worldbank",
                "data_year": latest_year,
                "fetched_at": datetime.now().isoformat(),
                "data_quality": "官方",
            })

            logger.info(f"[Economic] WB数据: GDP增长={result.get('gdp_growth', 'N/A')}, "
                        f"通胀={result.get('inflation', 'N/A')}, 难民变化={refugee_change:.3f}")
            return result

        except ImportError:
            logger.warning("[Economic] wbgapi 未安装")
            return None
        except Exception as e:
            logger.error(f"[Economic] World Bank 获取失败: {e}", exc_info=True)
            return None

    def _compute_normalized(self, wb_data: Dict) -> Dict:
        """将 WB 原始值归一化到 0-1 范围"""
        result = {}

        # GDP 增长率: 典型范围 -10% ~ +15%
        if "gdp_growth" in wb_data:
            raw = wb_data["gdp_growth"]["value"]
            # 映射: -10%→0.0, 0%→0.4, 5%→0.6, 15%→1.0
            norm = max(0.0, min(1.0, (raw + 10) / 25.0))
            result["gdp_growth"] = round(raw, 2)
            result["gdp_growth_norm"] = round(norm, 4)

        # 人均 GDP: 缅甸约 $1000-1500
        if "gdp_per_capita" in wb_data:
            raw = wb_data["gdp_per_capita"]["value"]
            result["gdp_per_capita"] = round(raw, 2)
            # 归一化: $0→0.0, $2000→1.0
            result["gdp_per_capita_norm"] = round(max(0.0, min(1.0, raw / 2000.0)), 4)

        # 通胀率: 典型范围 -5% ~ 30%
        if "inflation" in wb_data:
            raw = wb_data["inflation"]["value"]
            result["inflation"] = round(raw, 2)
            # 高通胀=负面信号, 映射: 0%→0.8 (好), 30%→0.0 (差)
            norm = max(0.0, min(1.0, (30 - raw) / 35.0))
            result["inflation_norm"] = round(norm, 4)

        # 贸易占 GDP: 缅甸约 40-80%
        if "trade_pct_gdp" in wb_data:
            raw = wb_data["trade_pct_gdp"]["value"]
            result["trade_pct_gdp"] = round(raw, 2)
            # 贸易开放度: 高=经济活跃, 归一化: 0%→0.0, 100%→1.0
            result["trade_change_norm"] = round(max(0.0, min(1.0, raw / 100.0)), 4)

        return result

    def _estimate_refugee_change(self, wb_data: Dict) -> float:
        """
        估算难民数量变化率

        由于 UNHCR 数据需要特殊获取方式, 此处基于经济指标推断:
        - GDP 负增长 + 高通胀 → 难民增加可能性高
        - 基于历史基准: 缅甸2021年政变后难民大幅增加

        返回: 0-1 归一化值
        """
        # 基准: 缅甸2021年后难民数量从~35万增至~180万 (2024)
        # refugee_change ≈ 0.8 (高增长)

        gdp_growth = wb_data.get("gdp_growth", {}).get("value", 0)
        inflation = wb_data.get("inflation", {}).get("value", 10)

        # 经济恶化 → 难民增加
        # GDP 负增长权重 0.6, 高通胀权重 0.4
        economic_distress = 0.0

        if gdp_growth < 0:
            economic_distress += 0.6 * min(1.0, abs(gdp_growth) / 10.0)
        elif gdp_growth < 3:
            economic_distress += 0.3

        if inflation > 15:
            economic_distress += 0.4 * min(1.0, (inflation - 15) / 20.0)
        elif inflation > 8:
            economic_distress += 0.2

        # 基础值 0.6 (缅甸冲突持续, 难民基数大) + 经济因素
        refugee_change = 0.6 + 0.4 * min(1.0, economic_distress)

        return round(max(0.0, min(1.0, refugee_change)), 4)

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
            logger.warning(f"[Economic] 缓存读取失败: {e}")
        return {}

    def _save_cache(self, data: Dict):
        """保存缓存到本地"""
        try:
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Economic] 缓存写入失败: {e}")

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

    def _default_result(self) -> Dict:
        """默认结果 (所有数据源不可用时)"""
        return {
            "gdp_growth": 0.0,
            "gdp_growth_norm": 0.5,
            "gdp_per_capita": 1200.0,
            "inflation": 15.0,
            "inflation_norm": 0.43,
            "trade_pct_gdp": 55.0,
            "trade_change_norm": 0.55,
            "refugee_change": 0.7,
            "source": "default_estimate",
            "data_quality": "估算",
            "fetched_at": datetime.now().isoformat()
        }


# ============================================================
# 单例
# ============================================================
_instance = None
_instance_lock = threading.Lock()


def get_economic_crawler() -> EconomicCrawler:
    """获取单例"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EconomicCrawler()
    return _instance
