"""
run_full_pipeline.py - 全流程集成测试脚本
模拟完整一天的数据流：爬取 → 预处理 → NER/情感 → LLM → 评分 → 入库

用法：
  python run_full_pipeline.py           # 完整流程
  python run_full_pipeline.py --skip-crawl  # 跳过爬取，使用已有数据
  python run_full_pipeline.py --demo         # 使用模拟数据（不依赖网络）
"""
import sys
import os
import json
import time
from datetime import datetime

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.config import load_config
from analyzer.data_loader import get_data_loader
from analyzer.ner import get_ner_extractor
from analyzer.sentiment import get_sentiment_analyzer
from analyzer.risk_scorer import get_risk_scorer
from analyzer.trend import get_trend_analyzer

# ============================================================
# 模拟数据（用于 --demo 模式）
# ============================================================

DEMO_NEWS = [
    {
        "title": "缅甸军方与克钦独立军在掸邦北部发生武装冲突",
        "pub_time": "2026-06-14",
        "content": "缅甸军方与克钦独立军在掸邦北部抹谷镇附近发生激烈交火。冲突持续约4小时，军方出动空中力量进行轰炸，导致多个村庄平民被迫转移。联合国难民署表示已有约2000名平民逃离冲突区域。",
        "url": "https://www.mhwmm.com/demo/1.html",
        "source": "缅甸缅华网"
    },
    {
        "title": "中缅经济走廊新项目启动仪式在内比都举行",
        "pub_time": "2026-06-13",
        "content": "中缅经济走廊新项目启动仪式在内比都举行，两国领导人共同出席。项目涵盖基础设施建设、能源合作和农业发展等领域，总投资额约5亿美元。",
        "url": "https://www.mhwmm.com/demo/2.html",
        "source": "缅甸缅华网"
    },
    {
        "title": "缅甸央行宣布新一轮汇率调控措施",
        "pub_time": "2026-06-12",
        "content": "缅甸中央银行宣布实施新一轮汇率调控措施，旨在稳定缅元汇率。经济学家分析认为，此举对抑制通胀有一定作用，但短期内难以扭转经济下行趋势。",
        "url": "https://www.mhwmm.com/demo/3.html",
        "source": "缅甸缅华网"
    },
    {
        "title": "若开邦难民危机加剧，联合国呼吁国际援助",
        "pub_time": "2026-06-11",
        "content": "若开邦地区的难民危机持续加剧，联合国难民署呼吁国际社会提供更多援助。目前已有超过10万人流离失所，食品和医疗物资严重短缺。",
        "url": "https://www.mhwmm.com/demo/4.html",
        "source": "缅甸缅华网"
    },
    {
        "title": "缅甸与泰国就边境贸易通道问题举行会谈",
        "pub_time": "2026-06-10",
        "content": "缅甸与泰国就边境贸易通道问题举行新一轮会谈。双方同意逐步恢复因冲突关闭的贸易口岸，但具体时间表尚未确定。",
        "url": "https://www.mhwmm.com/demo/5.html",
        "source": "缅甸缅华网"
    }
]


# ============================================================
# 流水线步骤
# ============================================================

def step_crawl(skip: bool = False) -> list:
    """步骤1：爬取新闻数据（缅华网 + GDELT）"""
    print("\n" + "=" * 60)
    print("  步骤 1/6: 爬取新闻数据")
    print("=" * 60)

    if skip:
        print("[跳过] 使用已有数据")
        loader = get_data_loader()
        return loader.load_raw_news()

    all_news = []

    # --- 1a: 缅华网爬虫 ---
    try:
        from data.crawler import NewsCrawler
        crawler = NewsCrawler()
        news = crawler.crawl_all_sources()
        crawler.save_news(news)
        all_news.extend(news)
        print(f"[缅华网] 爬取 {len(news)} 条新闻")
    except Exception as e:
        print(f"[警告] 缅华网爬取失败: {e}")

    # --- 1b: GDELT 全球事件数据库 ---
    try:
        from data.gdelt_crawler import get_gdelt_crawler
        gdelt = get_gdelt_crawler()
        gdelt_news = gdelt.crawl()
        gdelt.save_news(gdelt_news)
        all_news.extend(gdelt_news)
        print(f"[GDELT] 获取 {len(gdelt_news)} 条事件新闻")
    except Exception as e:
        print(f"[警告] GDELT 查询失败: {e}")

    # 如果两个源都失败，使用模拟数据
    if not all_news:
        print("[警告] 所有数据源均失败，使用模拟数据")
        all_news = DEMO_NEWS

    return all_news


def step_preprocess(news_list: list) -> list:
    """步骤2：文本预处理"""
    print("\n" + "=" * 60)
    print("  步骤 2/6: 文本预处理")
    print("=" * 60)

    loader = get_data_loader()
    processed = loader.preprocess(news_list)
    print(f"[完成] 预处理后剩余 {len(processed)} 条新闻")
    return processed


def step_ner_sentiment(news_list: list) -> list:
    """步骤3：NER + 情感分析"""
    print("\n" + "=" * 60)
    print("  步骤 3/6: NER + 情感分析")
    print("=" * 60)

    ner = get_ner_extractor()
    sentiment = get_sentiment_analyzer()

    for i, item in enumerate(news_list):
        text = item.get("content", "") or item.get("title", "")

        # NER
        try:
            entities = ner.extract_entities(text)
            item["entities"] = entities
        except Exception as e:
            print(f"  [警告] NER 失败 (#{i}): {e}")
            item["entities"] = {}

        # 情感分析（双语感知：传递 language 和 gdelt_tone）
        try:
            lang = item.get("language", None)
            gdelt_tone = item.get("gdelt_tone", None)
            sent_result = sentiment.get_risk_sentiment(
                text, lang=lang, gdelt_tone=gdelt_tone
            )
            item["sentiment_score"] = sent_result["sentiment_score"]
            item["risk_sentiment"] = sent_result["risk_level"]
            item["sentiment_source"] = sent_result.get("source", "unknown")
        except Exception as e:
            print(f"  [警告] 情感分析失败 (#{i}): {e}")
            item["sentiment_score"] = 0.5
            item["risk_sentiment"] = "unknown"

        print(f"  [{i+1}/{len(news_list)}] {item.get('title', '')[:30]}... "
              f"情感={item.get('sentiment_score', 'N/A'):.2f} "
              f"实体={len(item.get('entities', {}).get('locations', []))}地")

    print(f"[完成] {len(news_list)} 条新闻已分析")
    return news_list


def step_llm_analysis(news_list: list) -> list:
    """步骤4：大模型分析（可选，失败不中断）"""
    print("\n" + "=" * 60)
    print("  步骤 4/6: 大模型分析（可选）")
    print("=" * 60)

    try:
        from analyzer.llm_client import get_llm_client
        llm = get_llm_client()

        # 只对前 3 条做 LLM 分析（避免耗时过长）
        for i, item in enumerate(news_list[:3]):
            text = item.get("content", "")[:1000]  # 截断避免超限
            try:
                result = llm.analyze_news(text)
                item["llm_analysis"] = result
                print(f"  [{i+1}] 事件类型: {result.get('event_type', 'N/A')}")
            except Exception as e:
                print(f"  [{i+1}] LLM 失败: {e}")
                item["llm_analysis"] = None

        print(f"[完成] {min(3, len(news_list))} 条已进行 LLM 分析")
    except ImportError:
        print("[跳过] openai 未安装")
    except Exception as e:
        print(f"[跳过] LLM 不可用: {e}")

    return news_list


def step_risk_scoring(news_list: list) -> dict:
    """步骤5：风险评分（融合 GDELT 事件数据）"""
    print("\n" + "=" * 60)
    print("  步骤 5/6: 多指标加权风险评分")
    print("=" * 60)

    scorer = get_risk_scorer()

    # 尝试从 GDELT 获取事件指标（增强 conflict_frequency 和 event_severity）
    gdelt_metrics = None
    try:
        from data.gdelt_crawler import get_gdelt_crawler
        gdelt = get_gdelt_crawler()
        gdelt_metrics = gdelt.get_risk_metrics(timespan_days=7)
        print(f"  GDELT 事件: {gdelt_metrics['article_count']} 条文章, "
              f"冲突 {gdelt_metrics['conflict_count']} 条, "
              f"冲突频率 {gdelt_metrics['conflict_frequency']:.2%}")
    except Exception as e:
        print(f"  [警告] GDELT 指标获取失败: {e}，仅使用文本指标")

    # 构建外部数据（融合 GDELT 指标）
    external_data = {}
    if gdelt_metrics and gdelt_metrics['article_count'] > 0:
        external_data = {
            "gdelt_conflict_frequency": gdelt_metrics['conflict_frequency'],
            "gdelt_avg_tone_risk": gdelt_metrics['avg_tone_risk'],
            "gdelt_avg_severity": gdelt_metrics['avg_severity'],
            "gdelt_max_severity": gdelt_metrics['max_severity'],
            "gdelt_event_summary": gdelt_metrics['event_summary'],
            "gdelt_top_locations": gdelt_metrics['top_locations'],
        }

    # 使用新的 compute_daily_risk 方法
    result = scorer.compute_daily_risk(news_list, external_data=external_data)

    print(f"  新闻数量: {result.get('news_count', 0)}")
    print(f"  冲突新闻: {result.get('conflict_count', 0)}")
    print(f"  风险分数: {result.get('risk_score', 0)}")
    print(f"  风险等级: {result.get('risk_level', 'N/A')}")

    # 保存到历史
    loader = get_data_loader()
    today = datetime.now().strftime("%Y-%m-%d")
    details = result.get("raw_indicators", {})
    if external_data:
        details["gdelt"] = external_data
    loader.append_risk_score(
        date=today,
        risk_score=result["risk_score"],
        risk_level=result["risk_level"],
        details=details
    )
    print(f"[完成] 风险分已追加到历史记录")

    return result


def step_trend_analysis() -> dict:
    """步骤6：趋势分析"""
    print("\n" + "=" * 60)
    print("  步骤 6/6: 趋势分析")
    print("=" * 60)

    loader = get_data_loader()
    history = loader.load_risk_history(days=90)

    if not history:
        print("[跳过] 无历史数据")
        return {"trend": "无数据"}

    scores = [record["risk_score"] for record in history]
    dates = [record["date"] for record in history]

    trend_analyzer = get_trend_analyzer()

    # 完整趋势分析
    trend_result = trend_analyzer.full_analysis(scores)
    print(f"  趋势方向: {trend_result['trend']}")
    print(f"  最新分数: {trend_result['latest_score']}")
    print(f"  平均分数: {trend_result['avg_score']}")

    # 预测
    forecast_result = trend_analyzer.forecast(scores, days_ahead=7)
    print(f"  7日预测: {forecast_result['forecast']}")
    print(f"  预测置信度: {forecast_result['confidence']}")

    # STL 分解
    if len(scores) >= 14:
        stl = trend_analyzer.stl_decompose(scores)
        print(f"  STL 趋势分量: {stl['trend'][:3]}...")

    # 异常检测
    anomalies = trend_analyzer.detect_anomalies(scores)
    if anomalies:
        print(f"  异常点: {len(anomalies)} 个")

    return {
        "trend": trend_result,
        "forecast": forecast_result,
        "history_length": len(scores)
    }


# ============================================================
# 主入口
# ============================================================

def main():
    """运行完整流水线"""
    print("=" * 60)
    print("  缅甸地缘风险智能分析系统 - 全流程集成测试")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 加载配置
    try:
        cfg = load_config()
        print("[配置] 加载成功")
    except Exception as e:
        print(f"[配置] 加载失败: {e}，使用默认配置")

    # 解析命令行参数
    args = sys.argv[1:]
    skip_crawl = "--skip-crawl" in args
    demo_mode = "--demo" in args

    start_time = time.time()

    # 执行流水线
    if demo_mode:
        news_list = DEMO_NEWS
        print("\n[模式] 使用模拟数据")
    else:
        news_list = step_crawl(skip=skip_crawl)

    if not news_list:
        print("\n[错误] 无数据可处理，流水线终止")
        return

    news_list = step_preprocess(news_list)
    news_list = step_ner_sentiment(news_list)
    news_list = step_llm_analysis(news_list)
    risk_result = step_risk_scoring(news_list)
    trend_result = step_trend_analysis()

    # 总结
    elapsed = round(time.time() - start_time, 2)
    print("\n" + "=" * 60)
    print(f"  流水线完成！耗时: {elapsed}s")
    print(f"  处理新闻: {len(news_list)} 条")
    print(f"  当日风险: {risk_result.get('risk_score', 'N/A')} ({risk_result.get('risk_level', 'N/A')})")
    print(f"  趋势判断: {trend_result.get('trend', {}).get('trend', 'N/A')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
