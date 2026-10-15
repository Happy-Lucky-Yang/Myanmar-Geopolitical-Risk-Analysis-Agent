"""独立产品 Blueprint；默认关闭新增能力，旧业务 API 不受影响。"""
from flask import Blueprint, jsonify, render_template, request
from utils.product_features import enabled, flags

bp = Blueprint('product', __name__)


@bp.get('/literature')
def literature_page():
    return render_template('literature.html')


@bp.get('/guide')
def guide_page():
    return render_template('guide.html')


@bp.get('/api/product/status')
def product_status():
    return jsonify(success=True, data=flags())


@bp.get('/api/view-options')
def view_options():
    from data.admin_boundaries import get_province_features, PROVINCE_EN2CN
    from analyzer.data_loader import get_data_loader
    from utils.data_contract import source_evidence
    regions = [{'code': 'MMR', 'name': '缅甸全国'}]
    warnings = []
    try:
        for feature in get_province_features():
            props = feature['properties']
            regions.append({'code': props['GID_1'], 'name': PROVINCE_EN2CN.get(props['NAME_1'], props['NAME_1'])})
    except (KeyError, OSError):
        warnings.append('行政区列表暂不可用')
    sources = set()
    try:
        for article in get_data_loader().load_raw_news():
            if isinstance(article, dict):
                sources.update(e['source'] for e in source_evidence(article))
    except Exception:
        warnings.append('来源列表暂不可用')
    return jsonify(success=True, data={'regions': regions, 'sources': sorted(sources), 'warnings': warnings})


@bp.get('/api/literature')
def literature():
    if not enabled('literature'):
        return jsonify(success=False, error='文献中心尚未启用：ENABLE_LITERATURE'), 503
    try:
        from analyzer.knowledge_base import get_knowledge_base
        from analyzer.literature_catalog import browse, overview
        kb = get_knowledge_base()
        filters = {key: request.args.get(key) or None for key in ('category', 'module', 'indicator')}
        filters.update(topics=request.args.getlist('topic') or None,
                       core_only=request.args.get('core_only') == 'true')
        for key in ('year_from', 'year_to'):
            if request.args.get(key):
                filters[key] = int(request.args[key])
        result = browse(kb, request.args.get('q', ''), int(request.args.get('offset', 0)),
                        int(request.args.get('limit', 20)), **filters)
        result['overview'] = overview(kb)
        return jsonify(success=True, data=result)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception:
        return jsonify(success=False, error='文献索引读取失败，请检查配置和索引'), 503


@bp.get('/api/literature/<record_id>')
def literature_record(record_id):
    if not enabled('literature'):
        return jsonify(success=False, error='文献中心尚未启用'), 503
    from analyzer.knowledge_base import get_knowledge_base
    from analyzer.literature_catalog import public_record
    record = get_knowledge_base().get_record(record_id)
    if record is None:
        return jsonify(success=False, error='文献不存在'), 404
    return jsonify(success=True, data=public_record(record))
