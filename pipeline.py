"""Unified, observable end-to-end analysis pipeline."""
from __future__ import annotations

import copy
import html
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, TypedDict

from utils.config import get_data_paths, load_config

logger = logging.getLogger(__name__)

SourceMode = Literal["live", "existing", "demo"]


class StageResult(TypedDict):
    status: Literal["ok", "skipped", "degraded", "failed"]
    message: str


class PipelineResult(TypedDict):
    success: bool
    source_mode: SourceMode
    processed_count: int
    stages: Dict[str, StageResult]
    warnings: List[str]
    risk: Dict
    trend: Dict
    artifacts: Dict[str, str]


DEMO_NEWS = [
    {
        "title": "缅甸军方与克钦独立军在掸邦北部发生武装冲突",
        "pub_time": "2026-06-14",
        "content": "缅甸军方与克钦独立军在掸邦北部发生激烈交火和空袭，平民被迫转移。",
        "url": "https://example.invalid/demo/1",
        "source": "离线演示",
        "language": "zh",
    },
    {
        "title": "中缅经济走廊新项目启动",
        "pub_time": "2026-06-13",
        "content": "两国启动基础设施合作项目，协议推动地区发展和稳定。",
        "url": "https://example.invalid/demo/2",
        "source": "离线演示",
        "language": "zh",
    },
    {
        "title": "缅甸央行公布汇率调控措施",
        "pub_time": "2026-06-12",
        "content": "央行公布本月汇率调控安排，有关部门将继续观察市场情况。",
        "url": "https://example.invalid/demo/3",
        "source": "离线演示",
        "language": "zh",
    },
    {
        "title": "若开邦难民危机加剧",
        "pub_time": "2026-06-11",
        "content": "若开邦难民危机加剧，食品和医疗物资短缺。",
        "url": "https://example.invalid/demo/4",
        "source": "离线演示",
        "language": "zh",
    },
    {
        "title": "缅甸与泰国举行边境贸易会谈",
        "pub_time": "2026-06-10",
        "content": "双方举行例行会议并讨论边境贸易通道。",
        "url": "https://example.invalid/demo/5",
        "source": "离线演示",
        "language": "zh",
    },
]


def _stage(status: str, message: str) -> StageResult:
    return {"status": status, "message": message}


def _base_result(source_mode: SourceMode) -> PipelineResult:
    return {
        "success": False,
        "source_mode": source_mode,
        "processed_count": 0,
        "stages": {},
        "warnings": [],
        "risk": {},
        "trend": {},
        "artifacts": {},
    }


def _add_warning(result: PipelineResult, stage: str, message: str):
    warning = f"[{stage}] {message}"
    result["warnings"].append(warning)
    logger.warning(warning)


def _load_source(source_mode: SourceMode, result: PipelineResult) -> List[Dict]:
    if source_mode == "demo":
        result["stages"]["source"] = _stage("ok", "已加载 5 条内置离线数据")
        return copy.deepcopy(DEMO_NEWS)

    from analyzer.data_loader import get_data_loader
    if source_mode == "existing":
        news = get_data_loader().load_raw_news()
        status = "ok" if news else "failed"
        result["stages"]["source"] = _stage(status, f"已加载 {len(news)} 条本地新闻")
        return news

    news: List[Dict] = []
    collectors = []
    try:
        from data.crawler import NewsCrawler
        crawler = NewsCrawler()
        collected = crawler.crawl_all_sources()
        crawler.save_news(collected)
        news.extend(collected)
        collectors.append(f"网页源 {len(collected)} 条")
    except Exception as exc:
        _add_warning(result, "source", f"网页爬虫失败: {exc}")
    try:
        from data.myanmar_now_crawler import get_english_crawler
        crawler = get_english_crawler()
        collected = crawler.crawl_all()
        crawler.save_news(collected)
        news.extend(collected)
        collectors.append(f"英文媒体 {len(collected)} 条")
    except Exception as exc:
        _add_warning(result, "source", f"英文媒体失败: {exc}")
    try:
        from data.rss_crawler import get_rss_crawler
        crawler = get_rss_crawler()
        collected = crawler.crawl_all()
        crawler.save_news(collected)
        news.extend(collected)
        collectors.append(f"RSS {len(collected)} 条")
    except Exception as exc:
        _add_warning(result, "source", f"RSS 失败: {exc}")
    try:
        from data.gdelt_crawler import get_gdelt_crawler
        crawler = get_gdelt_crawler()
        collected = crawler.crawl()
        crawler.save_news(collected)
        news.extend(collected)
        collectors.append(f"GDELT {len(collected)} 条")
    except Exception as exc:
        _add_warning(result, "source", f"GDELT 失败: {exc}")

    result["stages"]["source"] = _stage(
        "ok" if news else "failed",
        "；".join(collectors) if collectors else "所有实时数据源均不可用",
    )
    return news


def _collect_external_metrics(result: PipelineResult) -> Dict:
    external = {}
    try:
        from data.gdelt_crawler import get_gdelt_crawler
        metrics = get_gdelt_crawler().get_risk_metrics(timespan_days=7)
        if metrics.get("article_count", 0) > 0:
            external.update({
                "gdelt_conflict_frequency": metrics.get("conflict_frequency"),
                "gdelt_avg_tone_risk": metrics.get("avg_tone_risk"),
                "gdelt_avg_severity": metrics.get("avg_severity"),
            })
    except Exception as exc:
        _add_warning(result, "risk", f"GDELT 指标不可用: {exc}")
    try:
        from data.nightlight_crawler import get_nightlight_crawler
        external["nightlight_change"] = get_nightlight_crawler().get_nightlight_change()
    except Exception as exc:
        _add_warning(result, "risk", f"夜光指标不可用: {exc}")
    try:
        from data.economic_crawler import get_economic_crawler
        external["refugee_change"] = get_economic_crawler().get_refugee_change()
    except Exception as exc:
        _add_warning(result, "risk", f"经济指标不可用: {exc}")
    return external


def _write_artifacts(result: PipelineResult, run_dir: Path):
    run_dir.mkdir(parents=True, exist_ok=True)

    trend_path = run_dir / "trend.json"
    trend_path.write_text(json.dumps(result["trend"], ensure_ascii=False, indent=2), encoding="utf-8")
    result["artifacts"]["trend"] = str(trend_path)

    map_path = run_dir / "map.html"
    try:
        from visualization.map_gen import get_map_generator
        score = float(result["risk"].get("risk_score", 0))
        map_html = get_map_generator().generate_heatmap([{
            "province": "缅甸全国",
            "lat": 19.76,
            "lon": 96.07,
            "risk_score": score,
            "risk_level": result["risk"].get("risk_level", "未知"),
        }])
        result["stages"]["visualization"] = _stage("ok", "已生成风险地图与趋势数据")
    except Exception as exc:
        map_html = (
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<title>缅甸风险地图（降级）</title><body>"
            f"<h1>缅甸全国风险：{html.escape(str(result['risk'].get('risk_score', 0)))}</h1>"
            f"<p>交互地图组件不可用：{html.escape(str(exc))}</p></body></html>"
        )
        result["stages"]["visualization"] = _stage("degraded", f"地图降级为静态 HTML: {exc}")
        _add_warning(result, "visualization", str(exc))
    map_path.write_text(map_html, encoding="utf-8")
    result["artifacts"]["map"] = str(map_path)

    report_path = run_dir / "report.html"
    basic_report = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<title>缅甸地缘风险分析报告</title><body>"
        "<h1>缅甸地缘风险分析报告</h1>"
        f"<p>风险分：{html.escape(str(result['risk'].get('risk_score', 0)))}</p>"
        f"<p>风险等级：{html.escape(str(result['risk'].get('risk_level', '未知')))}</p>"
        f"<p>趋势：{html.escape(str(result['trend'].get('trend', '数据不足')))}</p>"
    )
    if result["source_mode"] == "demo":
        # 完整报告器会读取 World Bank 数据；demo 必须保持严格离线。
        report_html = basic_report + "<p>离线演示报告，不含外部经济指标。</p></body></html>"
        result["stages"]["report"] = _stage("ok", "已生成严格离线 HTML 研判报告")
    else:
        try:
            from analyzer.report_generator import get_report_generator
            report_html = get_report_generator().generate_html_report(days=30)
            result["stages"]["report"] = _stage("ok", "已生成 HTML 研判报告")
        except Exception as exc:
            report_html = basic_report + (
                f"<p>完整报告组件不可用：{html.escape(str(exc))}</p></body></html>"
            )
            result["stages"]["report"] = _stage("degraded", f"报告降级为基础 HTML: {exc}")
            _add_warning(result, "report", str(exc))
    report_path.write_text(report_html, encoding="utf-8")
    result["artifacts"]["report"] = str(report_path)

    summary_path = run_dir / "summary.json"
    result["artifacts"]["summary"] = str(summary_path)
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(source_mode: SourceMode = "live", include_llm: bool = True,
                 persist: bool = True) -> PipelineResult:
    """Run the complete workflow and return a JSON-serializable stage report.

    ``demo`` mode always disables every network-backed stage, even when
    ``include_llm`` is true.
    """
    if source_mode not in ("live", "existing", "demo"):
        raise ValueError("source_mode 必须是 live、existing 或 demo")

    result = _base_result(source_mode)
    try:
        from analyzer.data_loader import get_data_loader
        loader = get_data_loader()
    except Exception as exc:
        result["stages"]["storage"] = _stage("failed", str(exc))
        _add_warning(result, "storage", str(exc))
        return result
    news = _load_source(source_mode, result)
    if not news:
        result["stages"].setdefault("source", _stage("failed", "无数据可处理"))
        return result

    try:
        news = loader.preprocess(news)
        if not news:
            result["stages"]["preprocess"] = _stage("failed", "清洗后无有效新闻")
            return result
        result["stages"]["preprocess"] = _stage("ok", f"清洗后 {len(news)} 条新闻")
    except Exception as exc:
        result["stages"]["preprocess"] = _stage("failed", str(exc))
        return result

    from analyzer.ner import get_ner_extractor
    from analyzer.sentiment import get_sentiment_analyzer
    ner = get_ner_extractor()
    sentiment = get_sentiment_analyzer()
    nlp_errors = 0
    for item in news:
        text = item.get("content", "") or item.get("title", "")
        try:
            item["entities"] = ner.extract_entities(text)
        except Exception as exc:
            nlp_errors += 1
            item["entities"] = {"locations": [], "organizations": [], "persons": [], "events": []}
            _add_warning(result, "nlp", f"NER 降级: {exc}")
        try:
            sent = sentiment.get_risk_sentiment(
                text,
                lang=item.get("language"),
                gdelt_tone=item.get("gdelt_tone"),
            )
            item["sentiment_score"] = sent["sentiment_score"]
            item["risk_sentiment"] = sent["risk_level"]
            item["sentiment_source"] = sent["source"]
        except Exception as exc:
            nlp_errors += 1
            item["sentiment_score"] = 0.5
            item["risk_sentiment"] = "unknown"
            _add_warning(result, "nlp", f"情感分析降级: {exc}")
    result["stages"]["nlp"] = _stage(
        "degraded" if nlp_errors else "ok",
        f"已处理 {len(news)} 条，降级 {nlp_errors} 次",
    )

    if source_mode == "demo":
        result["stages"]["llm"] = _stage("skipped", "demo 模式强制禁用网络 LLM")
    elif not include_llm:
        result["stages"]["llm"] = _stage("skipped", "调用方已禁用 LLM")
    else:
        from analyzer.llm_client import build_degraded_result, get_llm_client
        degraded = 0
        try:
            client = get_llm_client()
        except Exception as exc:
            client = None
            _add_warning(result, "llm", f"LLM 客户端初始化失败: {exc}")
            for item in news:
                item["llm_analysis"] = build_degraded_result(str(exc))
            degraded = len(news)
        if client is not None:
            for item in news:
                try:
                    llm_result = client.analyze_news(
                        item.get("content", "") or item.get("title", "")
                    )
                except Exception as exc:
                    llm_result = build_degraded_result(str(exc))
                    _add_warning(result, "llm", f"单条新闻分析失败: {exc}")
                item["llm_analysis"] = llm_result
                degraded += llm_result.get("analysis_status") == "degraded"
        result["stages"]["llm"] = _stage(
            "degraded" if degraded else "ok",
            f"已分析 {len(news)} 条，降级 {degraded} 条",
        )

    try:
        from analyzer.risk_scorer import get_risk_scorer
        external = _collect_external_metrics(result) if source_mode == "live" else {}
        result["risk"] = get_risk_scorer().compute_daily_risk(news, external_data=external)
        result["stages"]["risk"] = _stage("ok", "综合风险评分完成")
    except Exception as exc:
        result["stages"]["risk"] = _stage("failed", str(exc))
        return result

    result["processed_count"] = len(news)
    if persist:
        try:
            loader.append_risk_score(
                date=datetime.now().strftime("%Y-%m-%d"),
                risk_score=result["risk"]["risk_score"],
                risk_level=result["risk"]["risk_level"],
                details=result["risk"].get("raw_indicators", {}),
            )
            result["stages"]["persist"] = _stage("ok", "风险记录已持久化")
        except Exception as exc:
            result["stages"]["persist"] = _stage("failed", str(exc))
            _add_warning(result, "persist", str(exc))
            return result
    else:
        result["stages"]["persist"] = _stage("skipped", "persist=False")

    try:
        history = loader.load_risk_history(days=90) if persist else []
        scores = [row["risk_score"] for row in history] or [result["risk"]["risk_score"]]
        from analyzer.trend import get_trend_analyzer
        result["trend"] = get_trend_analyzer().full_analysis(scores)
        result["stages"]["trend"] = _stage("ok", f"使用 {len(scores)} 个风险点完成趋势分析")
    except Exception as exc:
        result["trend"] = {"trend": "数据不足", "error": str(exc)}
        result["stages"]["trend"] = _stage("degraded", str(exc))
        _add_warning(result, "trend", str(exc))

    neo4j_cfg = load_config().get("neo4j", {})
    if source_mode == "demo" or not neo4j_cfg.get("enabled", False):
        result["stages"]["knowledge_graph"] = _stage("skipped", "Neo4j 未启用或处于 demo 模式")
    else:
        try:
            from analyzer.knowledge_graph import get_knowledge_graph
            graph = get_knowledge_graph()
            for item in news:
                graph.add_news_analysis(item, item.get("entities", {}), item.get("llm_analysis", {}))
            result["stages"]["knowledge_graph"] = _stage("ok", f"写入 {len(news)} 条新闻分析")
        except Exception as exc:
            result["stages"]["knowledge_graph"] = _stage("degraded", str(exc))
            _add_warning(result, "knowledge_graph", str(exc))

    if persist:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir = Path(get_data_paths()["processed"]) / "runs" / timestamp
        # summary.json 由 _write_artifacts 最后写入，因此先放入最终成功状态，
        # 确保磁盘摘要与返回给 CLI/调度器的 PipelineResult 一致。
        result["stages"]["artifacts"] = _stage("ok", "运行产物写入完成")
        result["success"] = True
        try:
            _write_artifacts(result, run_dir)
        except Exception as exc:
            result["success"] = False
            result["stages"]["artifacts"] = _stage("failed", str(exc))
            _add_warning(result, "artifacts", str(exc))
            return result
    else:
        result["stages"]["visualization"] = _stage("skipped", "persist=False")
        result["stages"]["report"] = _stage("skipped", "persist=False")
        result["stages"]["artifacts"] = _stage("skipped", "persist=False")
        result["success"] = True
    return result
