"""信源立场标注：只服务于展示与解释，不参与评分权重。

立场表由 config.yaml 的 source_stance 段维护；未知来源标为 unlabeled，
以便前端如实显示"未标注"而不是默认成中立。
"""
from typing import Dict, List, Optional

UNLABELED = {'stance': 'unlabeled', 'label': '未标注', 'note': ''}


def _table() -> Dict:
    try:
        from utils.config import load_config
        table = load_config().get('source_stance') or {}
        return table if isinstance(table, dict) else {}
    except Exception:
        return {}


def stance_of(source: str) -> Dict:
    """返回来源的立场信息：{stance, label, note}。"""
    entry = _table().get(str(source or '').strip())
    if not isinstance(entry, dict) or not entry.get('stance'):
        return dict(UNLABELED)
    return {'stance': entry['stance'], 'label': entry.get('label', entry['stance']),
            'note': entry.get('note', '')}


def annotate_stance(news_list: List[Dict]) -> List[Dict]:
    """原地给新闻条目补 source_stance / source_stance_label 字段。"""
    for item in news_list or []:
        if not isinstance(item, dict):
            continue
        info = stance_of(item.get('source'))
        item.setdefault('source_stance', info['stance'])
        item.setdefault('source_stance_label', info['label'])
    return news_list


def stance_counts(news_list: List[Dict]) -> Dict[str, int]:
    """按立场统计条数，用于日指标来源构成展示。"""
    counts: Dict[str, int] = {}
    for item in news_list or []:
        if not isinstance(item, dict):
            continue
        stance = item.get('source_stance') or stance_of(item.get('source'))['stance']
        counts[stance] = counts.get(stance, 0) + 1
    return counts


def stance_label(stance: str) -> str:
    for fallback in (_table().values(),):
        for entry in fallback:
            if isinstance(entry, dict) and entry.get('stance') == stance:
                return entry.get('label', stance)
    return {'unlabeled': '未标注', 'domestic': '境内中文', 'independent': '独立',
            'ethnic': '民族', 'official': '官方', 'military': '军方',
            'international': '国际组织', 'aggregator': '聚合/事件库'}.get(stance, stance)


def explain(summary: Optional[Dict]) -> str:
    """把 stance_counts 渲染成一句人话，例如“境内中文 20 · 独立 8 · 聚合 15”。"""
    if not summary:
        return ''
    parts = []
    for stance, count in sorted(summary.items(), key=lambda pair: -pair[1]):
        parts.append(f"{stance_label(stance)} {count}")
    return ' · '.join(parts)
