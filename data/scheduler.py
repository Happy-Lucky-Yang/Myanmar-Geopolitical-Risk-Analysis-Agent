"""
data.scheduler - 统一定时调度器
负责在后台定时执行以下任务：
  1. 缅华网新闻爬取
  2. GDELT 全球事件数据库查询
  3. 新闻分析流水线（NER + 情感 + 风险评分）
  4. 夜间灯光遥感数据刷新（月度）
  5. 宏观经济统计数据刷新（季度）

使用方式：
  - 在 app.py 启动时自动启动后台线程
  - 独立运行：python -m data.scheduler
"""
import os
import sys
import time
import logging
import threading
from datetime import datetime
from typing import Dict, Optional

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import load_config, get_crawler_config

# ============================================================
# 日志配置
# ============================================================
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("scheduler")
logger.setLevel(logging.INFO)

# 文件处理器
fh = logging.FileHandler(os.path.join(LOG_DIR, "scheduler.log"), encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)

# 控制台处理器
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(ch)


class CrawlerScheduler:
    """
    统一爬虫调度器
    
    功能：
      - 定时爬取缅华网新闻
      - 定时查询 GDELT 事件数据
      - 定时运行分析流水线
      - 状态监控（最后执行时间、执行结果）
    """

    def __init__(self, config: Dict = None):
        self._cfg = config or load_config()
        self._crawler_cfg = get_crawler_config()
        
        # 调度配置
        scheduler_cfg = self._cfg.get("scheduler", {})
        self._enabled = scheduler_cfg.get("enabled", True)
        self._crawl_interval = scheduler_cfg.get("crawl_interval_hours", 4)
        self._gdelt_interval = scheduler_cfg.get("gdelt_interval_hours", 6)
        self._analysis_interval = scheduler_cfg.get("analysis_interval_hours", 12)
        
        # 状态跟踪
        self._last_crawl_time = None
        self._last_gdelt_time = None
        self._last_analysis_time = None
        self._last_crawl_result = None
        self._last_gdelt_result = None
        self._last_analysis_result = None
        
        # 线程控制
        self._running = False
        self._thread = None

        # 任务互斥锁（防止手动触发与定时触发同时执行同一任务）
        self._crawl_lock = threading.Lock()
        self._gdelt_lock = threading.Lock()
        self._analysis_lock = threading.Lock()

        # 遥感/经济数据刷新间隔（小时）
        self._nightlight_interval = scheduler_cfg.get("nightlight_interval_hours", 168)  # 7天
        self._economic_interval = scheduler_cfg.get("economic_interval_hours", 720)     # 30天
        self._last_nightlight_time = None
        self._last_economic_time = None
        self._last_nightlight_result = None
        self._last_economic_result = None

    # ============================================================
    # 任务执行
    # ============================================================

    def _run_crawl_job(self):
        """执行新闻爬取任务（缅华网 + 英文媒体 + RSS 源）"""
        if not self._crawl_lock.acquire(blocking=False):
            logger.warning("[Scheduler] 爬取任务已在执行中，跳过本次")
            return
        try:
            logger.info(f"[Scheduler] ====== 新闻爬取开始: {datetime.now()} ======")
            self._last_crawl_time = datetime.now()

            all_news = []

            # 1a: 缅华网爬虫
            try:
                from data.crawler import NewsCrawler
                crawler = NewsCrawler()
                news = crawler.crawl_all_sources()
                crawler.save_news(news)
                all_news.extend(news)
                logger.info(f"[Scheduler] 缅华网: {len(news)} 条")
            except Exception as e:
                logger.error(f"[Scheduler] 缅华网爬取失败: {e}", exc_info=True)

            # 1b: 英文媒体（Myanmar Now + Irrawaddy）
            try:
                from data.myanmar_now_crawler import get_english_crawler
                en_crawler = get_english_crawler()
                en_news = en_crawler.crawl_all()
                en_crawler.save_news(en_news)
                all_news.extend(en_news)
                logger.info(f"[Scheduler] 英文媒体: {len(en_news)} 条")
            except Exception as e:
                logger.error(f"[Scheduler] 英文媒体爬取失败: {e}", exc_info=True)

            # 1c: RSS 新闻源（Frontier Myanmar + DVB + The Diplomat）
            try:
                from data.rss_crawler import get_rss_crawler
                rss_crawler = get_rss_crawler()
                rss_news = rss_crawler.crawl_all()
                rss_crawler.save_news(rss_news)
                all_news.extend(rss_news)
                logger.info(f"[Scheduler] RSS 源: {len(rss_news)} 条")
            except Exception as e:
                logger.error(f"[Scheduler] RSS 爬取失败: {e}", exc_info=True)

            self._last_crawl_result = {
                "success": True,
                "count": len(all_news),
                "time": self._last_crawl_time.isoformat()
            }
            logger.info(f"[Scheduler] 爬取完成: 共 {len(all_news)} 条新闻")
        finally:
            self._crawl_lock.release()

    def _run_gdelt_job(self):
        """执行 GDELT 查询任务"""
        if not self._gdelt_lock.acquire(blocking=False):
            logger.warning("[Scheduler] GDELT 任务已在执行中，跳过本次")
            return
        try:
            logger.info(f"[Scheduler] ====== GDELT 查询开始: {datetime.now()} ======")
            self._last_gdelt_time = datetime.now()

            try:
                from data.gdelt_crawler import get_gdelt_crawler
                gdelt = get_gdelt_crawler()
                news = gdelt.crawl()
                filepath = gdelt.save_news(news)

                # 计算风险指标
                metrics = gdelt.get_risk_metrics(timespan_days=7)

                self._last_gdelt_result = {
                    "success": True,
                    "count": len(news),
                    "filepath": filepath,
                    "metrics": metrics,
                    "time": self._last_gdelt_time.isoformat()
                }
                logger.info(
                    f"[Scheduler] GDELT 完成: {len(news)} 条新闻, "
                    f"冲突 {metrics['conflict_count']} 条"
                )

            except Exception as e:
                self._last_gdelt_result = {
                    "success": False,
                    "error": str(e),
                    "time": self._last_gdelt_time.isoformat()
                }
                logger.error(f"[Scheduler] GDELT 失败: {e}", exc_info=True)
        finally:
            self._gdelt_lock.release()

    def _run_nightlight_job(self):
        """刷新夜间灯光遥感数据（月度/周度）"""
        try:
            logger.info(f"[Scheduler] ====== 夜光数据刷新: {datetime.now()} ======")
            self._last_nightlight_time = datetime.now()

            from data.nightlight_crawler import get_nightlight_crawler
            nl = get_nightlight_crawler()
            result = nl.force_refresh()

            self._last_nightlight_result = {
                "success": True,
                "nightlight_change": result.get("nightlight_change"),
                "time": self._last_nightlight_time.isoformat()
            }
            logger.info(f"[Scheduler] 夜光数据: nl_change={result.get('nightlight_change')}")
        except Exception as e:
            self._last_nightlight_result = {
                "success": False, "error": str(e),
                "time": datetime.now().isoformat()
            }
            logger.error(f"[Scheduler] 夜光刷新失败: {e}", exc_info=True)

    def _run_economic_job(self):
        """刷新宏观经济统计数据（季度/月度）"""
        try:
            logger.info(f"[Scheduler] ====== 经济数据刷新: {datetime.now()} ======")
            self._last_economic_time = datetime.now()

            from data.economic_crawler import get_economic_crawler
            econ = get_economic_crawler()
            result = econ.force_refresh()

            self._last_economic_result = {
                "success": True,
                "gdp_growth": result.get("gdp_growth"),
                "refugee_change": result.get("refugee_change"),
                "time": self._last_economic_time.isoformat()
            }
            logger.info(f"[Scheduler] 经济数据: GDP={result.get('gdp_growth')}, "
                        f"难民变化={result.get('refugee_change')}")
        except Exception as e:
            self._last_economic_result = {
                "success": False, "error": str(e),
                "time": datetime.now().isoformat()
            }
            logger.error(f"[Scheduler] 经济数据刷新失败: {e}", exc_info=True)

    def _run_analysis_job(self):
        """执行分析流水线任务"""
        if not self._analysis_lock.acquire(blocking=False):
            logger.warning("[Scheduler] 分析任务已在执行中，跳过本次")
            return
        try:
            logger.info(f"[Scheduler] ====== 分析流水线开始: {datetime.now()} ======")
            self._last_analysis_time = datetime.now()

            try:
                from analyzer.data_loader import get_data_loader
                from analyzer.ner import get_ner_extractor
                from analyzer.sentiment import get_sentiment_analyzer
                from analyzer.risk_scorer import get_risk_scorer

                loader = get_data_loader()
                news_list = loader.load_raw_news()

                if not news_list:
                    self._last_analysis_result = {
                        "success": False,
                        "error": "无数据可分析",
                        "time": self._last_analysis_time.isoformat()
                    }
                    logger.warning("[Scheduler] 无数据可分析")
                    return

                # NER + 情感分析（双语感知）
                ner = get_ner_extractor()
                sentiment = get_sentiment_analyzer()

                for item in news_list:
                    text = item.get("content", "") or item.get("title", "")
                    lang = item.get("language", None)  # "zh" / "en" / None

                    if text:
                        # 情感分析：GDELT 文章优先使用预计算的 tone
                        gdelt_tone = item.get("gdelt_tone", None)
                        sent_result = sentiment.get_risk_sentiment(
                            text, lang=lang, gdelt_tone=gdelt_tone
                        )
                        item["sentiment_score"] = sent_result.get("risk_score", 0.5)
                        item["sentiment_source"] = sent_result.get("source", "unknown")

                        # NER（自动语言检测）
                        try:
                            entities = ner.extract_entities(text)
                            item["entities"] = entities
                        except ImportError:
                            item["entities"] = {"locations": [], "organizations": [], "persons": [], "events": []}

                # 风险评分
                scorer = get_risk_scorer()
                risk_result = scorer.compute_daily_risk(news_list)

                # 保存到历史
                today = datetime.now().strftime("%Y-%m-%d")
                loader.append_risk_score(
                    date=today,
                    risk_score=risk_result["risk_score"],
                    risk_level=risk_result["risk_level"],
                    details=risk_result.get("raw_indicators", {})
                )

                self._last_analysis_result = {
                    "success": True,
                    "news_count": len(news_list),
                    "risk_score": risk_result["risk_score"],
                    "risk_level": risk_result["risk_level"],
                    "time": self._last_analysis_time.isoformat()
                }
                logger.info(
                    f"[Scheduler] 分析完成: {len(news_list)} 条新闻, "
                    f"风险分 {risk_result['risk_score']}"
                )

            except Exception as e:
                self._last_analysis_result = {
                    "success": False,
                    "error": str(e),
                    "time": self._last_analysis_time.isoformat()
                }
                logger.error(f"[Scheduler] 分析失败: {e}", exc_info=True)
        finally:
            self._analysis_lock.release()

    # ============================================================
    # 调度循环
    # ============================================================

    def _scheduler_loop(self):
        """后台调度循环"""
        logger.info(
            f"[Scheduler] 启动成功 | 爬取间隔={self._crawl_interval}h, "
            f"GDELT间隔={self._gdelt_interval}h, 分析间隔={self._analysis_interval}h"
        )
        
        # 首次立即执行
        self._run_crawl_job()
        self._run_gdelt_job()
        self._run_analysis_job()
        self._run_nightlight_job()
        self._run_economic_job()
        
        # 计算下次执行时间
        next_crawl = time.time() + self._crawl_interval * 3600
        next_gdelt = time.time() + self._gdelt_interval * 3600
        next_analysis = time.time() + self._analysis_interval * 3600
        next_nightlight = time.time() + self._nightlight_interval * 3600
        next_economic = time.time() + self._economic_interval * 3600
        
        while self._running:
            now = time.time()
            
            # 检查是否需要执行爬取
            if now >= next_crawl:
                self._run_crawl_job()
                next_crawl = now + self._crawl_interval * 3600
            
            # 检查是否需要执行 GDELT
            if now >= next_gdelt:
                self._run_gdelt_job()
                next_gdelt = now + self._gdelt_interval * 3600
            
            # 检查是否需要执行分析
            if now >= next_analysis:
                self._run_analysis_job()
                next_analysis = now + self._analysis_interval * 3600

            # 检查是否需要刷新夜光数据
            if now >= next_nightlight:
                self._run_nightlight_job()
                next_nightlight = now + self._nightlight_interval * 3600

            # 检查是否需要刷新经济数据
            if now >= next_economic:
                self._run_economic_job()
                next_economic = now + self._economic_interval * 3600
            
            # 每 60 秒检查一次
            time.sleep(60)
        
        logger.info("[Scheduler] 已停止")

    # ============================================================
    # 公开接口
    # ============================================================

    def start(self):
        """启动后台调度线程"""
        if not self._enabled:
            logger.info("[Scheduler] 调度器已禁用")
            return
        
        if self._running:
            logger.warning("[Scheduler] 已在运行中")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()
        logger.info("[Scheduler] 后台调度线程已启动")

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[Scheduler] 已停止")

    def get_status(self) -> Dict:
        """获取调度器状态"""
        return {
            "enabled": self._enabled,
            "running": self._running,
            "config": {
                "crawl_interval_hours": self._crawl_interval,
                "gdelt_interval_hours": self._gdelt_interval,
                "analysis_interval_hours": self._analysis_interval,
            },
            "last_crawl": self._last_crawl_result,
            "last_gdelt": self._last_gdelt_result,
            "last_analysis": self._last_analysis_result,
            "last_nightlight": self._last_nightlight_result,
            "last_economic": self._last_economic_result,
        }

    def trigger_crawl(self):
        """手动触发一次爬取（异步执行，立即返回）"""
        logger.info("[Scheduler] 手动触发: 新闻爬取")
        threading.Thread(target=self._run_crawl_job, daemon=True).start()

    def trigger_gdelt(self):
        """手动触发一次 GDELT 查询（异步执行，立即返回）"""
        logger.info("[Scheduler] 手动触发: GDELT 查询")
        threading.Thread(target=self._run_gdelt_job, daemon=True).start()

    def trigger_analysis(self):
        """手动触发一次分析（异步执行，立即返回）"""
        logger.info("[Scheduler] 手动触发: 分析流水线")
        threading.Thread(target=self._run_analysis_job, daemon=True).start()

    def trigger_nightlight(self):
        """手动触发夜光数据刷新"""
        logger.info("[Scheduler] 手动触发: 夜光数据刷新")
        threading.Thread(target=self._run_nightlight_job, daemon=True).start()

    def trigger_economic(self):
        """手动触发经济数据刷新"""
        logger.info("[Scheduler] 手动触发: 经济数据刷新")
        threading.Thread(target=self._run_economic_job, daemon=True).start()


# ============================================================
# 全局单例
# ============================================================

_scheduler_instance = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> CrawlerScheduler:
    """获取全局调度器单例（线程安全）"""
    global _scheduler_instance
    if _scheduler_instance is None:
        with _scheduler_lock:
            if _scheduler_instance is None:
                _scheduler_instance = CrawlerScheduler()
    return _scheduler_instance


# ============================================================
# 独立运行入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  缅甸地缘风险系统 - 定时调度器")
    print("  按 Ctrl+C 停止")
    print("=" * 60)
    
    scheduler = get_scheduler()
    scheduler.start()
    
    try:
        # 保持主线程运行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止...")
        scheduler.stop()
        print("已停止")
