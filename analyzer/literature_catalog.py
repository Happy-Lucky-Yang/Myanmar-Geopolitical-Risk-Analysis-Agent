"""文献公开展示模型；不暴露私密索引中的文件名、备注或内部判断。"""
import re
from urllib.parse import urlsplit, quote

PUBLIC_FIELDS = ('id', 'title', 'title_zh', 'authors', 'year', 'journal', 'abstract',
                 'keywords', 'topics', 'modules', 'indicators', 'is_core', 'category')


def safe_link(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password:
            return None
        if '\\' in value:
            return None
        return value
    except ValueError:
        return None


def public_record(record):
    result = {k: record.get(k) for k in PUBLIC_FIELDS}
    doi = str(record.get('doi') or '').strip()
    doi = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', doi, flags=re.I)
    result['links'] = []
    if re.fullmatch(r'10\.\d{4,9}/\S+', doi, flags=re.I):
        result['doi'] = doi
        result['links'].append({'label': 'DOI／出版社页面（不一定开放全文）',
                                'url': 'https://doi.org/' + quote(doi, safe='/')})
    # 开放全文必须由管理员在索引源中审核，不能仅凭存在 URL 就承诺授权。
    oa = record.get('open_access')
    if isinstance(oa, dict) and oa.get('reviewed') is True and oa.get('license'):
        url = safe_link(oa.get('url'))
        if url:
            result['links'].append({'label': '已审核开放全文', 'url': url, 'license': str(oa['license'])})
    result['fulltext_available'] = len(result['links']) > (1 if result.get('doi') else 0)
    return result


def browse(kb, query='', offset=0, limit=20, **filters):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('offset 须为非负整数，limit 须为1～100')
    if len(query) > 300:
        raise ValueError('检索词最长300字符')
    # 复用稳定排序；此处仅处理数百篇题录，未引入外部搜索服务。
    matches = kb.search(query=query, limit=0, **filters)
    records = {r['id']: r for r in kb.index.get('records', [])}
    rows = matches['results'][offset:offset + limit]
    return {'results': [public_record(records[r['id']]) for r in rows],
            'total': matches['total_matched'], 'offset': offset, 'limit': limit,
            'status': kb.index.get('status', 'ok'), 'layer': 'metadata'}


def overview(kb):
    records = kb.index.get('records', [])
    return {'record_count': len(records), 'core_count': sum(bool(r.get('is_core')) for r in records),
            'fulltext_count': sum(public_record(r)['fulltext_available'] for r in records),
            'facets': kb.index.get('facets', {}), 'status': kb.index.get('status', 'unavailable'),
            'layer': '题录与摘要，不是全文库'}
