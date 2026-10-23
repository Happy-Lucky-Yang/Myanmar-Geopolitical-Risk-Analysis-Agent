"""计算方法页的只读公共投影；不抓取、拟合、导入或暴露完整配置。"""
from utils.data_contract import finite_number, fingerprint
from functools import lru_cache

DAILY = {'conflict_frequency','sentiment_avg','nightlight_change','refugee_change','event_severity'}
GEO = {'conflict_frequency','china_myanmar_relation','china_us_rivalry','event_severity',
       'nightlight_change','neighbor_relation','displacement','western_relation','intl_sanction'}


@lru_cache(maxsize=1)
def boundary_texture():
    """页眉装饰来自本地GADM行政界；只作纹理，不表达风险或统计。"""
    try:
        from data.admin_boundaries import load_boundaries
        from shapely.geometry import shape
        paths = []
        for feature in load_boundaries(1)['features']:
            geometry = shape(feature['geometry']).simplify(.15, preserve_topology=True)
            polygons = [geometry] if geometry.geom_type == 'Polygon' else geometry.geoms
            for polygon in polygons:
                # 统一经纬度映射；仅做装饰，保持省界形状与相对位置。
                points = [(round((x-92)*9,2),round((29-y)*4,2)) for x,y in polygon.exterior.coords]
                paths.append('M' + ' L'.join(f'{x},{y}' for x,y in points) + ' Z')
        return tuple(paths)
    except Exception:
        return ()  # 可选装饰缺失不阻塞全国观测


def method_context():
    from utils.config import load_config
    from utils.product_features import enabled
    config = load_config()
    result = {
        'weights':{k:v for k,v in config.get('risk_weights',{}).items() if k in DAILY and finite_number(v)},
        'weights_v3':{k:v for k,v in config.get('risk_weights_v3',{}).items() if k in GEO and finite_number(v)},
        'parameters':[], 'calibrations':[],
        'parameters_note':'多源面板未启用，未读取已保存审定参数和冻结基准；不使用默认系数补位。',
    }
    if not enabled('risk_panels'):
        return result
    try:
        from storage.risk_store import RiskStore
        store = RiskStore()
        if not store.ready():
            result['parameters_note'] = '多源存储尚未就绪，暂不可读取审定参数与冻结基准。'
            return result
        seen = set()
        for panel in store.load('risk_panels'):
            params = panel.get('parameters') or {}
            version = params.get('approved_version')
            key = (version, fingerprint({k:params.get(k) for k in ('lambda','mu')}))
            if not version or key in seen:
                continue
            seen.add(key)
            result['parameters'].append({
                'version':version,
                **{key:{k:v for k,v in params.get(key,{}).items() if finite_number(v)}
                   for key in ('lambda','mu')},
            })
        result['calibrations'] = [{k:row.get(k) for k in ('id','frequency','mode','description')}
                                  for row in store.load('risk_calibrations')]
        result['parameters_note'] = '以下为已保存版本，不表示已获得全行业专家共识；权重、系数和基准变更不得拼接比较。'
        if not result['parameters']:
            result['parameters_note'] += ' 当前没有可展示的已保存审定系数，不合成缺条件的正式分。'
    except Exception:
        result['parameters_note'] = '参数或基准存储暂不可读取，未回填默认版本；旧日指标仍可查看。'
    return result
