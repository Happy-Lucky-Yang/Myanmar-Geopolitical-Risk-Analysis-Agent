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
  GET  /api/map        - 返回省级风险分级填色地图 HTML
  GET  /api/trend      - 返回趋势数据 JSON
  GET  /health         - 健康检查
"""
import sys
import os
import logging
import threading

# 确保项目根目录在 sys.path 中，以便各模块相互导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, Response, render_template, g
from flask.json.provider import DefaultJSONProvider
from utils.data_contract import RISK_VERSION, business_date, calendar_series, coverage_metadata, json_safe, time_window, utc_now
try:
    from flask_cors import CORS
except ImportError:
    def CORS(flask_app, *args, **kwargs):
        logging.getLogger(__name__).warning("flask-cors 未安装，跨域支持已禁用")
        return flask_app
from datetime import datetime

# 导入各模块
from utils.config import load_config, get_flask_config
from analyzer.ner import get_ner_extractor
from analyzer.sentiment import get_sentiment_analyzer
from analyzer.llm_client import build_degraded_result, get_llm_client
from analyzer.risk_scorer import get_risk_scorer
from analyzer.trend import get_trend_analyzer
from analyzer.prompts import NEWS_ANALYSIS_PROMPT, build_analysis_prompt
from analyzer.data_loader import get_data_loader
from analyzer.knowledge_graph import get_knowledge_graph
from analyzer.report_generator import get_report_generator, SnapshotChanged
from visualization.chart_gen import get_chart_generator
from data.scheduler import get_scheduler

# ============================================================
# Flask 应用初始化
# ============================================================
app = Flask(__name__)
class FiniteJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        kwargs['allow_nan'] = False
        return super().dumps(json_safe(obj), **kwargs)
app.json = FiniteJSONProvider(app)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
_flask_runtime_cfg = get_flask_config()
CORS(
    app,
    resources={r"/api/*": {"origins": _flask_runtime_cfg.get(
        "cors_origins", ["http://127.0.0.1:5000", "http://localhost:5000"]
    )}},
)
logger = logging.getLogger(__name__)
from utils.security import init_security
init_security(app)


def enqueue_request(kind, payload):
    from utils.security import repository
    from storage.jobs import JobQueue
    job_id = JobQueue(repository()).enqueue(kind, payload, owner_id=g.user['id'],
                                            idempotency_key=request.headers.get('Idempotency-Key'))
    return jsonify(success=True, data={"job_id": job_id, "status": "queued"}), 202


@app.before_request
def validate_query_window():
    if "days" in request.args or "end_date" in request.args:
        try:
            days = int(request.args.get("days", "30"))
            time_window(days, request.args.get("end_date"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "days 须为1～3660整数，end_date 须为有效日期"}), 400
    if request.method == 'GET' and request.path.startswith('/api/') and request.args.get('revision'):
        try:
            if get_data_loader().revision() != request.args['revision']:
                return jsonify(success=False, error='数据版本已更新，请刷新页面'), 409
            g.requested_revision = request.args['revision']
        except Exception:
            return jsonify(success=False, error='数据版本不可读取，未使用旧快照回填'), 503


@app.after_request
def verify_requested_revision(response):
    expected = getattr(g, 'requested_revision', None)
    if expected and response.status_code < 400:
        try:
            if get_data_loader().revision() != expected:
                return app.make_response((jsonify(success=False, error='读取期间数据已更新，请刷新页面'), 409))
        except Exception:
            return app.make_response((jsonify(success=False, error='无法校验数据版本，请重试'), 503))
        response.headers['X-Data-Revision'] = expected
    return response


def get_map_generator():
    """延迟加载 folium，使地图依赖缺失时其他 API 仍可启动。"""
    from visualization.map_gen import get_map_generator as factory
    return factory()

def _start_scheduler():
    """仅在实际启动服务时启用调度器，导入 Flask app 不产生网络副作用。"""
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


@app.route("/dashboard")
def page_dashboard():
    """综合态势仪表盘页面"""
    return render_template("dashboard.html")


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


@app.route("/api/models", methods=["GET"])
def list_models():
    """
    可用模型列表接口

    返回 config.yaml 中 llm.available_models 配置，
    前端据此渲染模型选择下拉框。
    """
    try:
        cfg = load_config()
        llm_cfg = cfg.get("llm", {})
        models = llm_cfg.get("available_models", [])
        default_model = llm_cfg.get("model_name", "glm-4-flash")
        return jsonify({
            "success": True,
            "data": {
                "models": models,
                "default": default_model
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    文本分析接口

    请求体 (JSON):
    {
        "text": "缅甸军方与克钦独立军在掸邦北部发生武装冲突...",
        "instruction": "请分析该事件对中缅关系的影响",  // 可选
        "model": "glm-4-flash"  // 可选，指定分析模型
    }

    也支持 multipart/form-data 文件上传（.txt / .json / .csv）：
        - text_file: 文本文件（自动读取内容拼入 text）
        - model: 可选模型 ID

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
        warnings = []

        # ------ 支持 JSON 和 multipart/form-data 两种请求 ------
        model_id = None
        if request.content_type and "multipart/form-data" in request.content_type:
            req_data = {}
            # 处理文件上传
            uploaded = request.files.get("text_file")
            if uploaded and uploaded.filename:
                content = uploaded.read().decode("utf-8", errors="replace")
                req_data["text"] = content
            else:
                req_data["text"] = request.form.get("text", "")
            model_id = request.form.get("model")
            req_data["instruction"] = request.form.get("instruction")
        else:
            req_data = request.get_json()
            if not req_data or not isinstance(req_data, dict):
                return jsonify({"success": False, "error": "请求体必须为 JSON 对象"}), 400
            model_id = req_data.get("model")

        if "text" not in req_data or not req_data.get("text"):
            return jsonify({"success": False, "error": "缺少 'text' 字段"}), 400

        text = req_data["text"]
        if not isinstance(text, str) or not text.strip():
            return jsonify({"success": False, "error": "'text' 必须为非空字符串"}), 400

        # 输入长度限制（防止 DoS）
        MAX_TEXT_LENGTH = 20000
        if len(text) > MAX_TEXT_LENGTH:
            return jsonify({
                "success": False,
                "error": f"文本过长 ({len(text)} 字符)，最大支持 {MAX_TEXT_LENGTH} 字符"
            }), 400

        instruction = req_data.get("instruction", None)
        if instruction is not None and not isinstance(instruction, str):
            return jsonify({"success": False, "error": "'instruction' 必须为字符串"}), 400
        MAX_INSTRUCTION_LENGTH = 2000
        if instruction and len(instruction) > MAX_INSTRUCTION_LENGTH:
            return jsonify({
                "success": False,
                "error": f"instruction 过长，最大支持 {MAX_INSTRUCTION_LENGTH} 字符",
            }), 400

        if app.config['SHARED_MODE'] and not getattr(g, 'task_execution', False):
            return enqueue_request('manual', {'text': text, 'instruction': instruction, 'model': model_id})

        # 1. 文本预处理
        loader = get_data_loader()
        cleaned_text = loader.clean_text(text)

        # 2. 命名实体识别
        try:
            ner = get_ner_extractor()
            entities = ner.extract_entities(cleaned_text)
        except Exception as e:
            entities = {"locations": [], "organizations": [], "persons": [], "events": []}
            warnings.append(f"NER 降级: {e}")
            logger.warning("NER 降级: %s", type(e).__name__)

        # 3. 情感分析（双语感知：中文 SnowNLP / 英文 VADER）
        try:
            sentiment_analyzer = get_sentiment_analyzer()
            sentiment_result = sentiment_analyzer.get_risk_sentiment(cleaned_text)
        except Exception as e:
            sentiment_result = {
                "sentiment_score": 0.5,
                "risk_score": 0.5,
                "risk_level": "medium",
                "source": "fallback",
            }
            warnings.append(f"情感分析降级: {e}")
            logger.warning("情感分析降级: %s", type(e).__name__)

        # 4. 大模型分析（使用 prompts.py 模板）
        llm_result = None
        try:
            llm = get_llm_client()
            llm_result = llm.analyze_news(cleaned_text, instruction, model=model_id)
            if llm_result.get("analysis_status") == "degraded":
                warnings.append(f"LLM 降级: {llm_result.get('error', '未知原因')}")
        except Exception as e:
            llm_result = build_degraded_result(f"LLM 分析失败: {e}")
            warnings.append(f"LLM 分析失败: {e}")

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

        gdelt_metrics = None
        indicators = {"conflict_frequency": 1.0 if has_conflict else 0.0,
                      "sentiment_avg": sentiment_result.get("risk_score"),
                      "nightlight_change": None, "refugee_change": None, "event_severity": None}
        risk_result = scorer.calculate_risk_score(indicators)
        risk_result.update(run_kind="manual", gdelt_used=False,
                           scope="仅用户输入的文本，不代表全国当日风险")

        # 7. 保存分析结果
        analysis_record = {
            "text": cleaned_text[:200] + "...",
            "entities": entities,
            "sentiment": sentiment_result,
            "llm_analysis": llm_result,
            "risk_score": risk_result,
            "analyzed_at": utc_now().isoformat(),
            "run_kind": "manual", "algorithm_version": RISK_VERSION,
            "owner_id": getattr(g, "user", {}).get("id"), "shared": False,
            "run_id": getattr(g, "job_id", None)
        }
        try:
            analysis_id = loader.save_analysis_result(analysis_record)
            # 手工文本只保存独立分析快照，不写正式日风险。
        except Exception as e:
            warnings.append(f"分析结果持久化失败: {e}")
            logger.warning("分析结果持久化失败: %s", type(e).__name__)
            if app.config['SHARED_MODE']:
                return jsonify(success=False, error="分析结果保存失败，可重试任务"), 503
            analysis_id = None

        alert = None

        # 9. 诊断性归因分析
        diagnostic = None
        try:
            from analyzer.diagnostic import get_diagnostic_analyzer
            diag = get_diagnostic_analyzer()
            diagnostic = diag.diagnose(risk_result)
        except Exception as e:
            warnings.append(f"诊断分析不可用: {e}")
            logger.warning("诊断分析不可用: %s", type(e).__name__)

        return jsonify({
            "success": True,
            "data": {
                "analysis_id": analysis_id,
                "entities": entities,
                "sentiment": sentiment_result,
                "llm_analysis": llm_result,
                "risk_score": risk_result,
                "gdelt_metrics": gdelt_metrics if gdelt_metrics and gdelt_metrics.get("article_count", 0) > 0 else None,
                "alert": alert,
                "diagnostic": diagnostic,
                "warnings": warnings,
            }
        })

    except Exception as e:
        logger.error('/api/analyze 处理失败: %s', type(e).__name__)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/chain", methods=["POST"])
def chain_analysis():
    """
    链式推理分析接口

    请求体: {"text": "...", "chain_depth": 2}  # depth: 1-4
    """
    try:
        req_data = request.get_json(silent=True)
        if not isinstance(req_data, dict):
            return jsonify({"success": False, "error": "请求体必须为对象"}), 400
        text = req_data.get("text", "")
        depth = req_data.get("chain_depth", 2)
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 4:
            return jsonify({"success": False, "error": "chain_depth 必须为1～4整数"}), 400
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            return jsonify({"success": False, "error": "缺少 text 字段"}), 400

        if app.config['SHARED_MODE'] and not getattr(g, 'task_execution', False):
            return enqueue_request('chain', req_data)
        from analyzer.chain_reasoner import get_chain_reasoner
        reasoner = get_chain_reasoner()
        model_id = req_data.get("model")
        result = reasoner.run_chain(text, depth=int(depth), model=model_id)
        if app.config['SHARED_MODE']:
            get_data_loader().save_analysis_result({**result, 'run_kind': 'manual',
                'owner_id': g.user['id'], 'run_id': getattr(g, 'job_id', None), 'shared': False})

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/history", methods=["GET"])
def history_events():
    """
    历史事件查询接口

    参数: event_type, severity_min, year
    """
    try:
        from data.historical_events import get_historical_events
        he = get_historical_events()

        event_type = request.args.get("event_type", None)
        severity_min = request.args.get("severity_min", 0, type=int)
        year = request.args.get("year", None, type=int)

        events = he.get_events(event_type=event_type, severity_min=severity_min, year=year)
        stats = he.get_event_stats()
        markers = he.get_markers_for_chart()

        return jsonify({
            "success": True,
            "data": {
                "events": events,
                "stats": stats,
                "chart_markers": markers
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/multimodal", methods=["GET"])
def multimodal_alignment():
    """
    多模态时空对齐接口

    参数: months (默认12)
    返回: 对齐矩阵 + 相关性分析
    """
    try:
        from analyzer.multimodal_aligner import get_multimodal_aligner
        aligner = get_multimodal_aligner()
        try:
            if 'days' in request.args and 'months' in request.args:
                raise ValueError('days 与 months 只能选择一种窗口')
            end = business_date(request.args['end_date']) if 'end_date' in request.args else business_date()
            if 'days' in request.args:
                days = int(request.args['days'])
                start, _ = time_window(days, end)
                months = 12
            else:
                months = int(request.args.get('months', '12'))
                start = business_date(aligner.month_keys(months, end)[0] + '-01')
                days = (end - start).days + 1
        except (TypeError, ValueError) as exc:
            return jsonify(success=False, error=str(exc)), 400
        filters = {'days': days, 'end_date': end.isoformat(),
                   'region': request.args.get('region', 'MMR'), 'source': request.args.get('source') or None}
        observations = aligner.observations()
        aligned = aligner.align_monthly(months=months, end_date=end, start_date=start,
                                        region=filters['region'], source=filters['source'], observations=observations)
        annual = aligner.align_annual(**filters, observations=observations)
        correlations = aligner.compute_correlations(aligned)
        province_data = aligner.get_province_alignment(**filters)

        return jsonify({
            "success": True,
            "data": {
                "aligned": aligned,
                "annual": annual,
                "correlations": correlations,
                "province_alignment": province_data,
                "filters": filters,
                "metadata": {"requested_start": start.isoformat(), "requested_end": end.isoformat(),
                             "complete_months": len(aligned), "timezone": "Asia/Yangon",
                             "period_end_exclusive": True, "algorithm_version": "multimodal-v3",
                             "note": "只比较窗口内完整月；边缘残缺月及未结束月份不参与，年度观测不展开"}
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/geo_potential", methods=["GET"])
def geo_potential():
    """
    地缘位势评估接口 (距离加权模型 + 空间自相关)

    返回: 各省位势评分、Moran's I、风险热点
    """
    try:
        from analyzer.geo_potential import get_geo_potential_analyzer
        analyzer = get_geo_potential_analyzer()
        provinces = _build_province_risk_data()
        result = analyzer.full_analysis({r['province']: r['risk_score'] for r in provinces})
        result['observation_metadata'] = [{k: v for k, v in r.items() if k != 'events'} for r in provinces]
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sources/health", methods=["GET"])
def sources_health():
    """
    数据源健康状态接口

    返回: 各数据源最近爬取的成功率/状态(healthy/degraded/dead)/最近明细
    """
    try:
        from data.source_health import get_source_health_tracker
        return jsonify({
            "success": True,
            "data": get_source_health_tracker().get_health()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/diagnostic", methods=["GET"])
def diagnostic_analysis():
    """
    诊断性分析接口 (驱动机制归因 + 上升原因详解 + 未来风险预警)

    参数: days (变化归因窗口, 默认14), ahead (未来预测天数, 默认7)
    返回: 变化归因 + rise_explanation(上升原因详解) + future_outlook(未来预警)
    """
    try:
        from analyzer.diagnostic import get_diagnostic_analyzer
        diag = get_diagnostic_analyzer()
        days = int(request.args.get('days', '14'))
        ahead = int(request.args.get('ahead', '7'))
        if request.args.get('source'):
            return jsonify(success=False, error='贡献比较使用已保存的综合日指标，不支持将单来源子集当综合评分'), 400
        result = diag.explain_and_forecast(days=days, days_ahead=ahead,
                                          end_date=request.args.get('end_date'), region=request.args.get('region', 'MMR'))
        result['revision'] = get_data_loader().revision()
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/alert", methods=["GET"])
def alert_status():
    """
    预警状态查询接口

    返回: 当前预警等级、活跃预警数、历史记录
    """
    try:
        from analyzer.alert_monitor import get_alert_monitor
        monitor = get_alert_monitor()

        end_date = request.args.get('end_date')
        region = request.args.get('region', 'MMR')
        if request.args.get('source'):
            return jsonify(success=False, error='正式预警按区域综合指标计算，不支持单来源预警'), 400
        status = monitor.get_current_status(end_date=end_date, region=region)
        history = monitor.get_alert_history(limit=20, end_date=end_date, region=region)
        thresholds = monitor.get_threshold_lines()

        return jsonify({
            "success": True,
            "data": {
                "status": status,
                "history": history,
                "thresholds": thresholds
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/alert/acknowledge", methods=["POST"])
def acknowledge_alert():
    """确认预警"""
    try:
        from analyzer.alert_monitor import get_alert_monitor
        monitor = get_alert_monitor()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('alert_id'), str) or not body['alert_id']:
            return jsonify(success=False, error='alert_id 必须是非空字符串'), 400
        success = monitor.acknowledge_alert(body['alert_id'], user_id=getattr(g, 'user', {}).get('id'))
        return jsonify({"success": success}), 200 if success else 404
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

        from data.gdelt_files import compute_metrics_from_events
        loader = get_data_loader()
        revision = loader.revision()
        events = [e for e in _query_events(days) if e.get('source', 'gdelt') == 'gdelt']
        metrics = compute_metrics_from_events(events)
        start, end = time_window(days, request.args.get('end_date'))
        dates = sorted({e['date'] for e in events})
        metrics['metadata'] = {
            'requested_start': start.isoformat(), 'requested_end': end.isoformat(),
            'end_exclusive': True, 'actual_start': dates[0] if dates else None,
            'actual_end': dates[-1] if dates else None, 'valid_days': len(dates),
            'coverage': len(dates) / days, 'coverage_note': '有事件记录日占比，不是采集完整率',
            'sources': ['gdelt'] if events else [], 'timezone': 'Asia/Yangon',
            'latest_observation': dates[-1] if dates else None, 'revision': revision,
            'quality': '事件规则派生指标，未做独立事件核验', 'unit': '比例/规则烈度（0～1）'}
        if loader.revision() != revision:
            return jsonify(success=False, error='读取期间数据已更新，请重试'), 409
        return jsonify(success=True, data=metrics)
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
        if app.config['SHARED_MODE']:
            if request.method == "GET":
                return jsonify(success=True, data={"mode": "independent_worker", "embedded": False})
            body = request.get_json(silent=True) or {}
            action = body.get("action") if isinstance(body, dict) else None
            if action in {"crawl", "analysis"}:
                return enqueue_request('pipeline', {'source_mode': 'live' if action == 'crawl' else 'existing', 'include_llm': False})
            if action in {"gdelt", "nightlight", "economic"}:
                return enqueue_request(action, {})
            return jsonify(success=False, error="未知任务"), 400
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
        elif action == "nightlight":
            scheduler.trigger_nightlight()
            return jsonify({"success": True, "message": "夜光数据刷新已触发"})
        elif action == "economic":
            scheduler.trigger_economic()
            return jsonify({"success": True, "message": "经济数据刷新已触发"})
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
    省级风险地图接口

    查询参数:
        - days: 查询最近多少天的数据（默认 7）
        - mode: choropleth=分级填色（默认） / hybrid=风险圆点+省界描边高亮

    响应: HTML 字符串（带 5 分钟 HTML 缓存，模式切换秒开）
    """
    try:
        days = request.args.get("days", 7, type=int)
        mode = request.args.get("mode", "choropleth")

        def _build():
            risk_data = _build_province_risk_data()
            map_gen = get_map_generator()
            if mode == "hybrid":
                return map_gen.generate_risk_hybrid_map(risk_data)
            return map_gen.generate_heatmap(risk_data)

        html = _cached_map_html(f"risk:{mode}:{days}", 300, _build)
        return Response(html, mimetype="text/html", headers={'X-Data-Revision': g.map_revision})

    except SnapshotChanged as e:
        return jsonify(success=False, error=str(e)), 409
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/map/unified", methods=["GET"])
def unified_map():
    """
    统一地图接口（自定义图层面板版）

    单张地图内含全部可叠加图层（分级填色/圆点光晕/事件密度 KDE/
    省界/国界），由前端图层面板控制开关、互斥与透明度，
    支持风险与事件密度的叠加对比分析。

    查询参数:
        - days: 统计窗口（默认 7）

    响应: HTML 字符串（带 5 分钟 HTML 缓存）
    """
    try:
        days = request.args.get("days", 7, type=int)

        def _build():
            map_gen = get_map_generator()

            # 省级风险数据
            risk_data = _build_province_risk_data()

            # KDE 密度（来自事件累积库，库空时尝试首次填充）
            density = None
            try:
                events = _query_events(days)
                if events:
                    from analyzer.event_density import (
                        get_event_density_analyzer)
                    cand = get_event_density_analyzer().compute(
                        events, days=days, end_date=request.args.get('end_date'))
                    if not cand.get("degraded"):
                        density = cand
            except Exception as e:
                logger.warning(f"[Map] KDE 密度准备失败: {e}")

            if not risk_data and density is None:
                return map_gen.generate_default_map()
            return map_gen.generate_unified_map(
                risk_data, density, days=days)

        html = _cached_map_html(f"unified:{days}", 300, _build)
        return Response(html, mimetype="text/html", headers={'X-Data-Revision': g.map_revision})

    except SnapshotChanged as e:
        return jsonify(success=False, error=str(e)), 409
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/map/events", methods=["GET"])
def event_density_map():
    """
    事件密度（KDE）地图接口

    基于 GDELT 事件经纬度的加权核密度估计，叠加 GADM 真实边界。
    数据来自事件累积库（逐轮增量积累，支持 7/14/30/90 天窗口）；
    库为空时自动触发一次 CSV 全量拉取填充。

    查询参数:
        - days: 统计窗口（默认 7，按事件日期过滤）

    响应: HTML 字符串（带 10 分钟 HTML 缓存，模式切换秒开）
    """
    try:
        days = request.args.get("days", 7, type=int)

        def _build():
            events = _query_events(days)
            map_gen = get_map_generator()
            if not events:
                return map_gen.generate_notice_map("所选窗口暂无事件记录，未回填旧数据")
            from analyzer.event_density import get_event_density_analyzer
            density = get_event_density_analyzer().compute(events, days=days, end_date=request.args.get('end_date'))
            if density.get("degraded"):
                return map_gen.generate_notice_map(density["degraded"])
            return map_gen.generate_event_density_map(density, days=days)

        html = _cached_map_html(f"events:{days}", 600, _build)
        return Response(html, mimetype="text/html", headers={'X-Data-Revision': g.map_revision})

    except SnapshotChanged as e:
        return jsonify(success=False, error=str(e)), 409
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
        warnings = []

        loader = get_data_loader()
        revision = loader.revision()
        end_date = (business_date(request.args['end_date']) if 'end_date' in request.args else business_date()).isoformat()
        region = request.args.get("region", "MMR")
        source = request.args.get('source') or None
        domain = request.args.get('domain', 'observed')
        if domain not in {'observed', 'legacy'}:
            return jsonify(success=False, error='domain 须为 observed 或 legacy'), 400
        legacy = domain == 'legacy'
        history = loader.load_risk_history(days=days, end_date=end_date, region=region, include_legacy=legacy) if not source else []
        if source:
            warnings.append('来源子集尚未重算日风险，综合风险留空；报告仅列出该来源的事件。')
        dates, scores = calendar_series(history, days, end_date)
        metadata = coverage_metadata(history, days, end_date, region=region,
                                     data_domain="legacy" if legacy else "observed",
                                     algorithm_version="legacy" if legacy else RISK_VERSION,
                                     read_errors=getattr(loader, "last_read_errors", []))

        # 趋势分析
        trend_analyzer = get_trend_analyzer()
        trend_result = trend_analyzer.full_analysis(scores, dates=dates)

        # 预测（使用 trend.py 的完整线性回归外推，取代 _simple_forecast）
        forecast_result = trend_analyzer.forecast(scores, days_ahead=7, dates=dates)
        if legacy:
            forecast_result.update(forecast=[], interval=None, backtest=None, status="legacy", reason="旧版快照不作正式预测")

        # 异常检测
        anomalies = trend_analyzer.detect_anomalies(scores) if not legacy else []
        for anomaly in anomalies:
            anomaly["date"] = dates[anomaly["index"]]

        result = {
            "dates": dates,
            "history": scores,
            "forecast": forecast_result["forecast"],
            "forecast_meta": {k: v for k, v in forecast_result.items() if k != "forecast"},
            "metadata": metadata,
            "revision": revision,
            "filters": {"days": days, "end_date": end_date, "region": region, "source": source, "domain": domain},
            "report_available": not legacy,
            "trend_analysis": trend_result,
            "anomalies": anomalies
        }

        # 添加预警阈值参考线
        try:
            from analyzer.alert_monitor import get_alert_monitor
            monitor = get_alert_monitor()
            result["threshold_lines"] = monitor.get_threshold_lines()
        except Exception as e:
            warnings.append(f"预警阈值不可用: {e}")
            logger.warning("预警阈值不可用: %s", e)

        # 添加历史事件标注
        try:
            from data.historical_events import get_historical_events
            he = get_historical_events()
            result["event_markers"] = [marker for marker in he.get_markers_for_chart()
                                       if marker.get('coord', [None])[0] in dates] if region == 'MMR' and not source else []
        except Exception as e:
            warnings.append(f"历史事件标注不可用: {e}")
            logger.warning("历史事件标注不可用: %s", e)

        # 生成图表数据
        if include_chart:
            chart_gen = get_chart_generator()
            chart_data = chart_gen.generate_trend_data(
                dates=dates,
                scores=scores,
                moving_avg=trend_result.get("moving_average", []),
                forecast=forecast_result.get("forecast", []),
                forecast_meta=result.get("forecast_meta"),
                threshold_lines=result.get("threshold_lines"),
                event_markers=result.get("event_markers")
            )
            result["chart_data"] = chart_data

        result["warnings"] = warnings
        if loader.revision() != revision:
            raise SnapshotChanged('读取趋势期间数据已更新，请刷新后重试')
        return jsonify({"success": True, "data": result})

    except SnapshotChanged as e:
        return jsonify(success=False, error=str(e)), 409
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# 知识图谱接口
# ============================================================

@app.route("/api/kg/query", methods=["GET"])
def kg_query():
    """
    知识图谱查询接口

    参数: entity (实体名), max_nodes (最大节点数, 默认30)
    """
    try:
        entity = request.args.get("entity", None)
        max_nodes = request.args.get("max_nodes", 30, type=int)

        if request.args.get('region', 'MMR') != 'MMR':
            return jsonify(success=False, error='图谱提及关系尚未关联明确事件区域，不能按任意提及地名筛选'), 400
        filters = {'max_nodes': max_nodes, 'days': request.args.get('days', 30, type=int),
                   'end_date': request.args.get('end_date'), 'source': request.args.get('source') or None,
                   'domain': request.args.get('domain', 'observed')}
        if not 1 <= max_nodes <= 500 or filters['domain'] not in {'observed', 'demo', 'legacy'}:
            return jsonify(success=False, error='无效图谱节点上限或数据域'), 400
        kg = get_knowledge_graph()
        if entity:
            data = kg.query_entities(entity, **filters)
        else:
            data = kg.get_graph_data_for_vis(**filters)

        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/kg/seed", methods=["POST"])
def kg_seed():
    """知识图谱种子数据填充 (仅当 Neo4j 启用时有效)"""
    try:
        from data.kg_seeder import KGSeeder
        seeder = KGSeeder()
        result = seeder.seed_all()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/network", methods=["GET"])
def network_analysis():
    """
    关系网络分析接口 (NetworkX)

    返回: 中心性指标、关键行为体、社区结构
    """
    try:
        from analyzer.network_analyzer import get_network_analyzer
        analyzer = get_network_analyzer()
        loader = get_data_loader()
        revision = loader.revision()
        domain = request.args.get('domain', 'observed')
        if domain not in {'observed', 'demo'}:
            return jsonify(success=False, error='网络数据域须为 observed 或 demo'), 400
        result = analyzer.analyze(days=request.args.get('days', 30, type=int),
            end_date=request.args.get('end_date'), region=request.args.get('region', 'MMR'),
            source=request.args.get('source') or None, domain=domain)
        if loader.revision() != revision:
            return jsonify(success=False, error='读取网络期间数据已更新，请重试'), 409
        result['revision'] = revision
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# 报告生成接口
# ============================================================

@app.route("/api/report", methods=["GET"])
def generate_report():
    """
    自动化结构化报告生成接口

    查询参数:
        - format: html | docx (默认 html)
        - days: 分析最近多少天 (默认 30)

    响应:
        - html: 返回 HTML 页面
        - docx: 返回 docx 文件下载
    """
    try:
        fmt = request.args.get("format", "html").lower()
        days = request.args.get("days", 30, type=int)

        if request.args.get('domain', 'observed') != 'observed':
            return jsonify(success=False, error='正式报告不支持旧版或演示数据域'), 400
        if fmt not in {'html', 'docx', 'json'}:
            return jsonify(success=False, error='format 须为 html、docx 或 json'), 400
        generator = get_report_generator()
        snapshot = generator.build_snapshot(days=days, end_date=request.args.get('end_date'),
            region=request.args.get('region', 'MMR'), source=request.args.get('source') or None,
            expected_revision=request.args.get('revision'))
        if fmt == 'json':
            return jsonify(success=True, data=snapshot)
        if fmt == "docx":
            docx_bytes = generator.generate_docx_report(snapshot=snapshot)
            return Response(
                docx_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f"attachment; filename=myanmar_risk_report_{days}d.docx",
                         "X-Snapshot-ID": snapshot['snapshot_id']}
            )
        else:
            html = generator.generate_html_report(snapshot=snapshot)
            return Response(html, mimetype="text/html", headers={'X-Snapshot-ID': snapshot['snapshot_id']})

    except SnapshotChanged as e:
        return jsonify(success=False, error=str(e)), 409
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# 辅助函数
# ============================================================

# 地图 HTML 缓存（生成一张 folium 地图需数秒，缓存后模式切换秒开）
_MAP_HTML_CACHE = {}
_map_cache_lock = threading.Lock()


def _cached_map_html(key: str, ttl: int, builder) -> str:
    """
    带 TTL 的地图 HTML 缓存

    :param key: 缓存键（如 "risk:7" / "events:7"）
    :param ttl: 有效期（秒）
    :param builder: 未命中时调用的生成函数
    :return: HTML 字符串
    """
    import time as _time
    loader = get_data_loader()
    revision = loader.revision()
    if request.args.get('revision') and request.args['revision'] != revision:
        raise SnapshotChanged('地图数据已更新，请刷新后重试')
    g.map_revision = revision
    key = (key, revision, request.args.get('end_date') or business_date().isoformat(), tuple(sorted(request.args.items())))
    now = _time.time()
    with _map_cache_lock:
        hit = _MAP_HTML_CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    html = builder()  # 生成在锁外，避免阻塞其他请求
    if loader.revision() != revision:
        raise SnapshotChanged('地图生成期间数据已更新，请重试')
    with _map_cache_lock:
        _MAP_HTML_CACHE[key] = (now, html)
        # 限制缓存条目数（不同 days 参数组合有限，简单清理即可）
        if len(_MAP_HTML_CACHE) > 20:
            oldest = min(_MAP_HTML_CACHE, key=lambda k: _MAP_HTML_CACHE[k][0])
            _MAP_HTML_CACHE.pop(oldest, None)
    return html


def _query_events(days=30):
    from data.event_store import get_event_store
    from analyzer.spatial_analysis import unique_events, locate_event
    end_date = request.args.get('end_date')
    events = unique_events(get_event_store().load(days=days, end_date=end_date), days, end_date,
                           source=request.args.get('source') or None)
    region = request.args.get('region', 'MMR')
    return events if region == 'MMR' else [e for e in events if locate_event(e)[0] == region]


def _build_province_risk_data(history=None) -> list:
    """保留兼容入口；全国历史分不得投射成省级观测。"""
    from analyzer.spatial_analysis import aggregate_provinces
    days = request.args.get('days', 7, type=int)
    return aggregate_provinces(_query_events(days), days, request.args.get('end_date'))


@app.get('/api/regions')
def region_observations():
    from analyzer.spatial_analysis import morans_i
    data = _build_province_risk_data()
    return jsonify(success=True, data={'regions': data, 'spatial_statistics': morans_i(data),
                   'metric': '已定位事件规则烈度均值；非全国日风险、非发生概率',
                   'filters': dict(request.args), 'revision': get_data_loader().revision()})


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

    # debug 模式仅在 reloader 子进程启动；非 debug 模式直接启动。
    if os.environ.get("ENABLE_EMBEDDED_SCHEDULER", "false").lower() == "true":
        if os.environ.get("APP_SHARED_MODE", "false").lower() == "true":
            raise RuntimeError("共享部署仅允许独立 worker 调度")
        if not flask_cfg.get("debug", False) or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            _start_scheduler()

    app.run(
        host=flask_cfg.get("host", "127.0.0.1"),
        port=flask_cfg.get("port", 5000),
        debug=flask_cfg.get("debug", False)
    )
