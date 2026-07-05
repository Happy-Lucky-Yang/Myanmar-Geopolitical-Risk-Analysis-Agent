"""
缅甸地缘风险智能分析原型系统 - Flask 主入口

页面路由：
  GET  /         - 对话分析页面
  GET  /map      - 风险地图页面
  GET  /trend    - 趋势预测页面

API 接口：
  POST /api/analyze    - 接收文本，返回完整分析结果
  GET  /api/gdelt      - 查询 GDELT 事件数据
  GET  /api/scheduler  - 查看调度器状态
  POST /api/scheduler  - 手动触发爬取/分析任务
  GET  /api/map        - 返回风险热力地图 HTML
  GET  /api/trend      - 返回趋势数据 JSON
  GET  /health         - 健康检查
"""
import sys
import os

# 确保项目根目录在 sys.path 中，以便各模块相互导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, Response, render_template
from flask_cors import CORS
from datetime import datetime

# 导入各模块
from utils.config import load_config, get_flask_config
from analyzer.ner import get_ner_extractor
from analyzer.sentiment import get_sentiment_analyzer
from analyzer.llm_client import get_llm_client
from analyzer.risk_scorer import get_risk_scorer
from analyzer.trend import get_trend_analyzer
from analyzer.prompts import NEWS_ANALYSIS_PROMPT, build_analysis_prompt
from analyzer.data_loader import get_data_loader
from analyzer.knowledge_graph import get_knowledge_graph
from visualization.map_gen import get_map_generator
from visualization.chart_gen import get_chart_generator
from data.scheduler import get_scheduler

# ============================================================
# Flask 应用初始化
# ============================================================
app = Flask(__name__)
CORS(app)  # 允许前端跨域请求

# 启动后台调度器（仅在 Flask 实际服务进程中启动）
# Flask debug 模式下：父进程 (reloader) WERKZEUG_RUN_MAIN 未设置 → 不启动
#                      子进程 (实际服务) WERKZEUG_RUN_MAIN="true" → 启动
# 非 debug 模式：直接启动
import os as _os
_werkzeug_child = _os.environ.get("WERKZEUG_RUN_MAIN") == "true"
_not_debug = not load_config().get("flask", {}).get("debug", True)
if _werkzeug_child or _not_debug:
    _scheduler = get_scheduler()
    _scheduler.start()


# ============================================================
# 页面路由（渲染 HTML 模板）
# ============================================================

@app.route("/")
def page_chat():
    """对话分析页面"""
    return render_template("chat.html")


@app.route("/map")
def page_map():
    """风险地图页面"""
    return render_template("map.html")


@app.route("/trend")
def page_trend():
    """趋势预测页面"""
    return render_template("trend.html")


# ============================================================
# API 接口
# ============================================================

@app.route("/health", methods=["GET"])
def health_check():
    """健康检查接口"""
    return jsonify({
        "status": "ok",
        "service": "缅甸地缘风险分析系统",
        "timestamp": datetime.now().isoformat()
    })


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    文本分析接口

    请求体 (JSON):
    {
        "text": "缅甸军方与克钦独立军在掸邦北部发生武装冲突...",
        "instruction": "请分析该事件对中缅关系的影响"  // 可选
    }

    响应 (JSON):
    {
        "success": true,
        "data": {
            "entities": {...},
            "sentiment": {...},
            "llm_analysis": {...},
            "risk_score": {...}
        }
    }
    """
    try:
        req_data = request.get_json()
        if not req_data or not isinstance(req_data, dict):
            return jsonify({"success": False, "error": "请求体必须为 JSON 对象"}), 400

        if "text" not in req_data:
            return jsonify({"success": False, "error": "缺少 'text' 字段"}), 400

        text = req_data["text"]
        if not isinstance(text, str) or not text.strip():
            return jsonify({"success": False, "error": "'text' 必须为非空字符串"}), 400

        # 输入长度限制（防止 DoS）
        MAX_TEXT_LENGTH = 10000
        if len(text) > MAX_TEXT_LENGTH:
            return jsonify({
                "success": False,
                "error": f"文本过长 ({len(text)} 字符)，最大支持 {MAX_TEXT_LENGTH} 字符"
            }), 400

        instruction = req_data.get("instruction", None)

        # 1. 文本预处理
        loader = get_data_loader()
        cleaned_text = loader.clean_text(text)

        # 2. 命名实体识别
        ner = get_ner_extractor()
        entities = ner.extract_entities(cleaned_text)

        # 3. 情感分析（双语感知：中文 SnowNLP / 英文 VADER）
        sentiment_analyzer = get_sentiment_analyzer()
        sentiment_result = sentiment_analyzer.get_risk_sentiment(cleaned_text)

        # 4. 大模型分析（使用 prompts.py 模板）
        llm_result = None
        try:
            llm = get_llm_client()
            llm_result = llm.analyze_news(cleaned_text, instruction)
        except Exception as e:
            llm_result = {"error": f"LLM 分析失败: {str(e)}"}

        # 5. 风险评分（0-100 分制）
        scorer = get_risk_scorer()
        # 从 config 读取冲突关键词（带兜底默认值）
        _kw_cfg = load_config().get("conflict_keywords", {})
        conflict_keywords_zh = _kw_cfg.get("zh", ["冲突", "战斗", "空袭", "武装", "交火", "爆炸", "袭击", "制裁"])
        conflict_keywords_en = _kw_cfg.get("en", ["conflict", "attack", "airstrike", "armed", "ceasefire",
                                "sanction", "coup", "refugee", "protest", "military"])
        text_lower = cleaned_text.lower()
        has_conflict = (
            any(kw in cleaned_text for kw in conflict_keywords_zh)
            or any(kw in text_lower for kw in conflict_keywords_en)
        )

        # 尝试从 GDELT 获取事件指标（增强冲突频次和严重程度）
        gdelt_metrics = None
        try:
            from data.gdelt_crawler import get_gdelt_crawler
            gdelt = get_gdelt_crawler()
            gdelt_metrics = gdelt.get_risk_metrics(timespan_days=7)
        except Exception:
            pass  # GDELT 不可用时静默跳过

        # 构建外部数据
        external_data = {}
        if gdelt_metrics and gdelt_metrics.get("article_count", 0) > 0:
            external_data = {
                "gdelt_conflict_frequency": gdelt_metrics["conflict_frequency"],
                "gdelt_avg_tone_risk": gdelt_metrics["avg_tone_risk"],
                "gdelt_avg_severity": gdelt_metrics["avg_severity"],
                "gdelt_max_severity": gdelt_metrics["max_severity"],
            }

        # 使用 compute_daily_risk 进行融合评分
        indicators = {
            "conflict_frequency": 1.0 if has_conflict else 0.2,
            "sentiment_avg": sentiment_result["risk_score"],
            "nightlight_change": 0.0,   # TODO: 接入遥感数据
            "refugee_change": 0.0,      # TODO: 接入难民统计
            "event_severity": 0.8 if has_conflict else 0.3
        }
        # 如果有 GDELT 数据，融合到指标中
        if gdelt_metrics and gdelt_metrics.get("article_count", 0) > 0:
            text_freq = indicators["conflict_frequency"]
            gdelt_freq = gdelt_metrics["conflict_frequency"]
            indicators["conflict_frequency"] = 0.6 * text_freq + 0.4 * gdelt_freq
            # GDELT 事件严重程度替代关键词估计
            indicators["event_severity"] = gdelt_metrics["avg_severity"]

        risk_result = scorer.calculate_risk_score(indicators)
        risk_result["gdelt_used"] = gdelt_metrics is not None and gdelt_metrics.get("article_count", 0) > 0

        # 6. 写入知识图谱（如果 Neo4j 启用）
        try:
            kg = get_knowledge_graph()
            kg.add_news_analysis(
                news_item={"title": cleaned_text[:100], "date": datetime.now().strftime("%Y-%m-%d"), "source": "user_input"},
                entities=entities,
                llm_result=llm_result
            )
        except Exception:
            pass  # Neo4j 不可用时静默跳过

        # 7. 保存分析结果
        analysis_record = {
            "text": cleaned_text[:200] + "...",
            "entities": entities,
            "sentiment": sentiment_result,
            "llm_analysis": llm_result,
            "risk_score": risk_result,
            "analyzed_at": datetime.now().isoformat()
        }
        loader.save_analysis_result(analysis_record)

        # 追加风险分记录
        today = datetime.now().strftime("%Y-%m-%d")
        loader.append_risk_score(
            date=today,
            risk_score=risk_result["risk_score"],
            risk_level=risk_result["risk_level"],
            details=indicators
        )

        return jsonify({
            "success": True,
            "data": {
                "entities": entities,
                "sentiment": sentiment_result,
                "llm_analysis": llm_result,
                "risk_score": risk_result,
                "gdelt_metrics": gdelt_metrics if gdelt_metrics and gdelt_metrics.get("article_count", 0) > 0 else None
            }
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/gdelt", methods=["GET"])
def gdelt_events():
    """
    GDELT 事件数据查询接口

    查询参数:
        - days: 查询最近多少天（默认 7）

    响应: JSON 格式
    {
        "success": true,
        "data": {
            "article_count": int,
            "conflict_count": int,
            "conflict_frequency": float,
            "avg_tone_risk": float,
            "avg_severity": float,
            "max_severity": float,
            "event_summary": {...},
            "top_locations": [...]
        }
    }
    """
    try:
        days = request.args.get("days", 7, type=int)

        from data.gdelt_crawler import get_gdelt_crawler
        gdelt = get_gdelt_crawler()
        metrics = gdelt.get_risk_metrics(timespan_days=days)

        return jsonify({
            "success": True,
            "data": metrics
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scheduler", methods=["GET", "POST"])
def scheduler_control():
    """
    调度器控制接口

    GET  - 查看调度器状态
    POST - 手动触发任务
        Body: {"action": "crawl" | "gdelt" | "analysis" | "status"}
    """
    try:
        scheduler = get_scheduler()

        if request.method == "GET":
            return jsonify({
                "success": True,
                "data": scheduler.get_status()
            })

        # POST: 手动触发
        body = request.get_json() or {}
        action = body.get("action", "status")

        if action == "crawl":
            scheduler.trigger_crawl()
            return jsonify({"success": True, "message": "爬取任务已触发"})
        elif action == "gdelt":
            scheduler.trigger_gdelt()
            return jsonify({"success": True, "message": "GDELT 查询已触发"})
        elif action == "analysis":
            scheduler.trigger_analysis()
            return jsonify({"success": True, "message": "分析流水线已触发"})
        else:
            return jsonify({
                "success": True,
                "data": scheduler.get_status()
            })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/map", methods=["GET"])
def risk_map():
    """
    风险热力地图接口

    查询参数:
        - days: 查询最近多少天的数据（默认 7）

    响应: HTML 字符串（可直接在浏览器中渲染）
    """
    try:
        days = request.args.get("days", 7, type=int)

        loader = get_data_loader()
        history = loader.load_risk_history(days=days)

        map_gen = get_map_generator()

        if history:
            risk_data = _build_province_risk_data(history)
            html = map_gen.generate_heatmap(risk_data)
        else:
            html = map_gen.generate_default_map()

        return Response(html, mimetype="text/html")

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/trend", methods=["GET"])
def trend():
    """
    趋势分析接口

    查询参数:
        - days: 查询最近多少天的数据（默认 30）
        - chart: 是否返回图表数据（默认 true）

    响应格式（README 规范）:
    {
        "dates": ["2026-01-01", ...],
        "history": [45.2, 48.1, ...],
        "forecast": [50.3, 51.0, ...]
    }
    """
    try:
        days = request.args.get("days", 30, type=int)
        include_chart = request.args.get("chart", "true").lower() == "true"

        loader = get_data_loader()
        history = loader.load_risk_history(days=days)

        if not history:
            return jsonify({
                "success": True,
                "data": {
                    "dates": [],
                    "history": [],
                    "forecast": [],
                    "trend_analysis": {"trend": "无数据", "data_points": 0},
                    "chart_data": None
                }
            })

        # 提取日期和分数序列
        dates = [record["date"] for record in history]
        scores = [record["risk_score"] for record in history]

        # 趋势分析
        trend_analyzer = get_trend_analyzer()
        trend_result = trend_analyzer.full_analysis(scores)

        # 预测（使用 trend.py 的完整线性回归外推，取代 _simple_forecast）
        forecast_result = trend_analyzer.forecast(scores, days_ahead=7)

        # 异常检测
        anomalies = trend_analyzer.detect_anomalies(scores)

        result = {
            "dates": dates,
            "history": scores,
            "forecast": forecast_result["forecast"],
            "forecast_meta": {
                "slope": forecast_result["slope"],
                "confidence": forecast_result["confidence"],
                "r_squared": forecast_result.get("r_squared", 0.0)
            },
            "trend_analysis": trend_result,
            "anomalies": anomalies
        }

        # 生成图表数据
        if include_chart:
            chart_gen = get_chart_generator()
            chart_data = chart_gen.generate_trend_data(
                dates=dates,
                scores=scores,
                moving_avg=trend_result.get("moving_average", [])
            )
            result["chart_data"] = chart_data

        return jsonify({"success": True, "data": result})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# 辅助函数
# ============================================================

def _build_province_risk_data(history: list) -> list:
    """
    从历史数据构建省级风险数据（用于地图渲染）
    TODO: 实际应根据 NER 提取的地名关联到省份
    """
    from visualization.map_gen import MYANMAR_PROVINCES

    avg_score = sum(r["risk_score"] for r in history) / len(history) if history else 50

    risk_data = []
    for province, (lat, lon) in MYANMAR_PROVINCES.items():
        # 边境省份风险更高（简化逻辑）
        border_provinces = ["掸邦", "克钦邦", "克伦邦", "若开邦", "钦邦"]
        if province in border_provinces:
            score = min(avg_score * 1.3, 100)
        else:
            score = avg_score * 0.8

        risk_data.append({
            "province": province,
            "risk_score": round(score, 2),
            "risk_level": "高风险" if score >= 70 else "中风险" if score >= 40 else "低风险",
            "lat": lat,
            "lon": lon
        })

    return risk_data


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    cfg = load_config()
    flask_cfg = get_flask_config()

    print("=" * 60)
    print("  缅甸地缘风险智能分析原型系统")
    print(f"  启动地址: http://{flask_cfg.get('host', '0.0.0.0')}:{flask_cfg.get('port', 5000)}")
    print("=" * 60)

    app.run(
        host=flask_cfg.get("host", "0.0.0.0"),
        port=flask_cfg.get("port", 5000),
        debug=flask_cfg.get("debug", True)
    )
