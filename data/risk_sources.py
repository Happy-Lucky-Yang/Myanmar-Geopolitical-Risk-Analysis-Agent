"""权威来源适配与只读同步；所有状态和覆盖均显式，原始输入不公开。"""
import os
import time
import re
from datetime import timedelta
from xml.etree import ElementTree as ET
from urllib.parse import urljoin
from utils.risk_contract import batch, pure_day, number, official_url
from utils.data_contract import fingerprint, normalize_observation, business_date

ACLED_TYPES = {'Battles', 'Explosions/Remote violence', 'Violence against civilians'}
OFAC_TAGS = {'BURMA-EO14014'}
TOKEN_URL = 'https://acleddata.com/oauth/token'
ACLED_URL = 'https://acleddata.com/api/acled/read'


def parse_acled(rows, manifest):
    result = []
    seen = {}
    for raw in rows:
        required = {'event_id_cnty', 'event_date', 'event_type', 'country', 'fatalities'}
        if not isinstance(raw, dict) or not required <= raw.keys():
            raise ValueError('ACLED表头不支持')
        if raw['country'] not in {'Myanmar', 'Burma'}:
            continue
        day = business_date(raw['event_date'])
        if day is None:
            raise ValueError('ACLED日期无效')
        row = {'event_id': str(raw['event_id_cnty'] or ''), 'date': day.isoformat(), 'source': 'acled',
               'event_type': raw['event_type'], 'sub_event_type': raw.get('sub_event_type', ''),
               'fatalities': number(raw['fatalities'], 0, optional=True),
               'lat': number(raw.get('latitude'), -90, 90, optional=True),
               'lon': number(raw.get('longitude'), -180, 180, optional=True),
               'time_precision': number(raw.get('time_precision'), 1, 3, optional=True),
               'geo_precision': number(raw.get('geo_precision'), 1, 3, optional=True),
               'admin1': raw.get('admin1'), 'actor1': str(raw.get('actor1') or ''),
               'actor2': str(raw.get('actor2') or ''), 'original_sources': str(raw.get('source') or ''),
               'dataset_version': manifest['dataset_version'], 'run_kind': 'existing'}
        if not row['event_id'] or not pure_day(manifest['start']) <= day < pure_day(manifest['end']):
            raise ValueError('ACLED事件ID缺失或超出覆盖')
        # 原始行政区名称不自动当成GADM代码。
        mapping = manifest.get('admin1_mapping', {})
        row['region'] = mapping.get(row['admin1'])
        digest = fingerprint(row)
        if row['event_id'] in seen and seen[row['event_id']] != digest:
            raise ValueError('同ID出现不一致记录，须选择明确修订版本')
        if row['event_id'] not in seen:
            actor_map = manifest.get('actor_mapping', {})
            mapped = [actor_map.get(row[k]) for k in ('actor1','actor2')]
            if all(isinstance(v,str) and v for v in mapped):
                row['canonical_actors'] = mapped
            result.append(row)
        seen[row['event_id']] = digest
    return batch('acled', result, manifest)


def parse_idmc(rows, manifest):
    """支持官方GIDD标准列，特殊/分区文件须先显式转换，拒绝猜列。"""
    result = []
    for raw in rows:
        required = {'ISO3', 'Year', 'Conflict Stock Displacement', 'Conflict Internal Displacements'}
        if not isinstance(raw, dict) or not required <= raw.keys():
            raise ValueError('IDMC文件结构未验证，需ISO3/Year/Conflict Stock Displacement/Conflict Internal Displacements')
        if raw['ISO3'] != 'MMR':
            continue
        year_value = number(raw['Year'], 1900, 9997)
        if year_value != int(year_value):
            raise ValueError('IDMC年份必须为整数')
        year = int(year_value)
        for field, indicator, unit in (
            ('Conflict Stock Displacement', 'idp_stock', 'persons'),
            ('Conflict Internal Displacements', 'idp_flows', 'displacements')):
            value = number(raw[field], 0, optional=True)
            if value is None:
                continue
            result.append(normalize_observation({'source': 'idmc', 'indicator': indicator,
                'region': 'MMR', 'value': value, 'unit': unit, 'quality': 'observed',
                'period_start': f'{year}-01-01', 'period_end': f'{year+1}-01-01', 'frequency': 'annual',
                'dataset_version': manifest['dataset_version'], 'product': 'GIDD conflict', 'run_kind': 'existing'}))
    return batch('idmc', result, manifest)


def parse_viirs(rows, manifest):
    result = []
    for raw in rows:
        row = normalize_observation(raw)
        if (row['source'] != 'viirs' or row['frequency'] != 'monthly' or row['indicator'] != 'nightlight'
                or row['quality'] != 'observed' or row['unit'] != 'nW/cm²/sr' or not row['product']
                or row['dataset_version'] != manifest['dataset_version'] or row['value'] < 0):
            raise ValueError('VIIRS须为真实月度分区平均辐射亮度，单位nW/cm²/sr且产品版本明确')
        result.append(row)
    return batch('viirs', result, manifest)


def parse_ofac_actions(rows, manifest):
    """已核验法律措施表：对象列表不作计数单位；未审核项保留待核验。"""
    result, seen = [], {}
    for raw in rows:
        if not isinstance(raw, dict) or not {'action_id', 'effective_date', 'program', 'change', 'measure_type', 'url', 'reviewed'} <= raw.keys():
            raise ValueError('OFAC措施表结构未验证')
        if raw['program'] not in OFAC_TAGS:
            continue
        day = pure_day(raw['effective_date']) if raw['effective_date'] else None
        reviewed = raw['reviewed'] is True or raw['reviewed'] == 'true'
        row = {'action_id': str(raw['action_id']), 'date': day.isoformat() if day else None,
            'program': raw['program'], 'change': raw['change'], 'measure_type': raw['measure_type'],
            'url': official_url(raw['url'], 'ofac'), 'reviewed': reviewed and day is not None,
            'dataset_version': manifest['dataset_version']}
        if row['change'] not in {'addition', 'amendment', 'removal', 'redesignation'} or not row['action_id']:
            raise ValueError('措施类型/行动ID无效')
        key = (row['action_id'], row['measure_type'], row['change'])
        if key in seen and seen[key] != row:
            raise ValueError('同一法律措施存在矛盾记录')
        if key not in seen:
            seen[key] = row
            result.append(row)
    return batch('ofac', result, manifest)


def parse_sdn(xml):
    """官方SDN XML仅核对当前对象状态，不推导历史新措施。"""
    if len(xml) > 32*1024*1024 or b'\x00' in xml or re.search(br'<!DOCTYPE|<!ENTITY', xml, re.I):
        raise ValueError('XML含实体定义或过大')
    root = ET.fromstring(xml)
    output = []
    for e in root.iter():
        if e.tag.split('}')[-1] != 'sdnEntry':
            continue
        tags = {n.text for n in e.iter() if n.tag.split('}')[-1] == 'program'}
        if tags & OFAC_TAGS:
            uid = e.get('uid') or next((n.text for n in e if n.tag.split('}')[-1]=='uid'), None)
            if not uid:
                raise ValueError('SDN条目缺少稳定UID')
            output.append({'uid': uid, 'programs': sorted(tags), 'kind': 'current_list_only'})
    return output


class SourceHTTP:
    """有界同站请求，不回显认证和供应商正文；不自动持久化下载内容。"""
    def __init__(self, source, session=None, sleep=time.sleep):
        import requests
        self.session = session or requests.Session()
        self.source, self.sleep = source, sleep

    def request(self, method, url, **kwargs):
        import requests
        from utils.config import get_proxy
        kwargs.setdefault('proxies', get_proxy() or {})
        for redirect in range(3):
            official_url(url, self.source)
            for attempt in range(3):
                try:
                    self.sleep(2)
                    response = self.session.request(method, url, timeout=30, allow_redirects=False,
                                                    stream=True, **kwargs)
                except requests.RequestException:
                    raise RuntimeError('来源网络请求失败') from None
                try:
                    status = response.status_code
                    if status in {429, 502, 503, 504}:
                        if attempt == 2:
                            raise RuntimeError('来源限流或暂不可用')
                        self.sleep(min(8, 2**attempt))
                        continue
                    if status in {301, 302, 303, 307, 308}:
                        # 认证请求不跟随跳转，防止请求体或Bearer外泄。
                        if method != 'GET':
                            raise RuntimeError('认证请求不允许重定向')
                        url = urljoin(url, response.headers.get('Location', ''))
                        break
                    if status in {401, 403}:
                        raise PermissionError(str(status))
                    if status >= 400:
                        raise RuntimeError('来源HTTP错误')
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 32*1024*1024:
                            raise ValueError('来源响应过大')
                        chunks.append(chunk)
                    return b''.join(chunks)
                finally:
                    response.close()
            else:
                raise RuntimeError('来源重试耗尽')
        raise ValueError('重定向过多')


class ACLEDAdapter:
    def __init__(self, http=None):
        self.http = http or SourceHTTP('acled')
        self.token = self.refresh = None
        self.expires = 0

    def authenticate(self):
        import json
        user, password = os.getenv('ACLED_USERNAME'), os.getenv('ACLED_PASSWORD')
        if not user or not password:
            raise PermissionError('ACLED尚未配置授权账户')
        data = {'client_id': 'acled', 'scope': 'authenticated', 'grant_type': 'password',
                'username': user, 'password': password}
        if self.refresh:
            data = {'client_id': 'acled', 'grant_type': 'refresh_token', 'refresh_token': self.refresh}
        value = json.loads(self.http.request('POST', TOKEN_URL, data=data))
        if not value.get('access_token'):
            raise PermissionError('ACLED认证返回无效')
        self.token, self.refresh = value['access_token'], value.get('refresh_token')
        self.expires = time.monotonic() + number(value.get('expires_in'), 1)

    def fetch(self, manifest, max_pages=20):
        import json
        from utils.product_features import enabled
        if not enabled('acled'):
            return {'status': 'disabled', 'reason': 'ACLED尚未启用'}
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ValueError('分页上限1—100')
        if time.monotonic() >= self.expires:
            self.authenticate()
        rows = []
        refreshed = False
        for page in range(1, max_pages+1):
            while True:
                try:
                    params = {'country': 'Myanmar', 'event_date': manifest['start']+'|'+
                              (pure_day(manifest['end'])-timedelta(days=1)).isoformat(),
                              'event_date_where': 'BETWEEN', 'limit': 5000, 'page': page}
                    payload = json.loads(self.http.request('GET', ACLED_URL, params=params,
                        headers={'Authorization': 'Bearer '+self.token}))
                    break
                except PermissionError as exc:
                    if str(exc) != '401' or refreshed:
                        raise PermissionError('ACLED授权不足或已过期') from None
                    self.authenticate()
                    refreshed = True
            if not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(payload.get('data'), list):
                raise ValueError('ACLED响应结构变化')
            records = payload['data']
            rows.extend(records)
            if len(rows) > 100000:
                raise ValueError('ACLED批次过大')
            if len(records) < 5000:
                return parse_acled(rows, {**manifest, 'complete': True, 'pages': page})
        # 达到上限不声明完整，不推进任何水位。
        return parse_acled(rows, {**manifest, 'complete': False, 'pages': max_pages})


class OFACAdapter:
    def __init__(self, http=None):
        self.http = http or SourceHTTP('ofac')

    def inspect(self):
        from utils.product_features import enabled
        if not enabled('ofac'):
            return {'status': 'disabled', 'reason': 'OFAC尚未启用'}
        from bs4 import BeautifulSoup
        entry = 'https://ofac.treasury.gov/sanctions-list-service'
        soup = BeautifulSoup(self.http.request('GET', entry), 'html.parser')
        links = []
        for a in soup.select('a[href]'):
            url = urljoin(entry, a['href'])
            try:
                official_url(url, 'ofac')
            except ValueError:
                continue
            links.append(url)
        return {'status': 'review_required', 'links': sorted(set(links)),
                'reason': '发现官方入口，须核验文件及措施生效日；当前名单不能直接转换为新增强度'}

    def inspect_list(self, url):
        from utils.product_features import enabled
        if not enabled('ofac'):
            return {'status':'disabled'}
        return {'status':'current_list_only', 'url':official_url(url,'ofac'),
                'entries':parse_sdn(self.http.request('GET',url)),
                'reason':'只核对当前名单对象，不生成新增制裁强度'}

    def inspect_action(self, url):
        from bs4 import BeautifulSoup
        from utils.product_features import enabled
        if not enabled('ofac'):
            return {'status': 'disabled'}
        soup = BeautifulSoup(self.http.request('GET', official_url(url, 'ofac')), 'html.parser')
        title = soup.select_one('h1')
        date = soup.select_one('time[datetime]')
        return {'url': url, 'title': title.get_text(' ', strip=True) if title else '',
                'publication_date': date.get('datetime') if date else None, 'effective_date': None,
                'status': 'review_required', 'reason': '发布日期不自动作为生效日；措施类型须人工核验'}
