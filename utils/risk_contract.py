"""多源输入契约：不依赖数据库、Flask或网络；周期/覆盖/版本不可猜测。"""
import csv
import io
import json
import re
from pathlib import Path
from datetime import date, timedelta
from zipfile import ZipFile
from urllib.parse import urlsplit, parse_qsl
from utils.data_contract import business_date, finite_number, fingerprint, json_safe, utc_now

HOSTS = {
    'acled': {'acleddata.com'},
    'ofac': {'ofac.treasury.gov', 'sanctionslist.ofac.treas.gov'},
    'idmc': {'www.internal-displacement.org', 'internal-displacement.org', 'data.humdata.org'},
    'viirs': {'www.earthdata.nasa.gov', 'ladsweb.modaps.eosdis.nasa.gov', 'eogdata.mines.edu'},
    'worldbank': {'api.worldbank.org', 'data.worldbank.org'},
}
VERSIONS = {'formal': 'risk-v4-multisource', 'proxy': 'risk-v4-proxy'}


def official_url(value, source):
    try:
        p = urlsplit(value)
        if (p.scheme != 'https' or p.hostname not in HOSTS[source] or p.port not in (None, 443)
                or p.username or p.password or '\\' in value or any(ord(c) < 33 for c in value)
                or any(k.lower() in {'client_id','key','api_key','token','access_token','password','username','email'} for k,_ in parse_qsl(p.query))):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ValueError('来源URL必须为批准的官方HTTPS地址') from None
    return value


def pure_day(value):
    day = business_date(value)
    if day is None or value not in (day, day.isoformat()) or not 1900 <= day.year <= 9998:
        raise ValueError('日期必须为1900—9998范围内的纯日期')
    return day


def number(value, minimum=None, maximum=None, optional=False):
    if optional and value in (None, ''):
        return None
    try:
        result = float(value) if isinstance(value, str) else value
    except ValueError:
        raise ValueError('无效数值') from None
    if (not finite_number(result) or (minimum is not None and result < minimum)
            or (maximum is not None and result > maximum)):
        raise ValueError('数值缺失、非有限或超出范围')
    return result


def periods(frequency='monthly', count=12, end_date=None):
    end = business_date() if end_date is None else pure_day(end_date)
    if type(count) is not int or frequency not in {'monthly', 'annual'} or not 1 <= count <= (120 if frequency == 'monthly' else 30):
        raise ValueError('月窗口1—120，年窗口1—30，count须为整数')
    # 只包含截止日已完成的自然月/年。
    exclusive = end + timedelta(days=1)
    boundary = exclusive.replace(day=1) if frequency == 'monthly' else exclusive.replace(month=1, day=1)
    index = boundary.year * 12 + boundary.month - 1 if frequency == 'monthly' else boundary.year
    output = []
    if index-count < (1900*12 if frequency=='monthly' else 1900):
        raise ValueError('周期超出支持范围')
    for i in range(index - count, index):
        a = date(i // 12, i % 12 + 1, 1) if frequency == 'monthly' else date(i, 1, 1)
        b = date((i + 1) // 12, (i + 1) % 12 + 1, 1) if frequency == 'monthly' else date(i + 1, 1, 1)
        output.append((a.isoformat(), b.isoformat()))
    return output


def batch(source, records, manifest):
    if source not in HOSTS or not isinstance(records, list) or len(records) > 100000:
        raise ValueError('未知来源或批次过大')
    if not isinstance(manifest,dict) or set(manifest)-{'start','end','complete','region','dataset_version','license','url',
            'admin1_mapping','actor_mapping','pages','published_at','file_hash'}:
        raise ValueError('覆盖清单字段无效，不能夹带认证或私密配置')
    m = dict(manifest)
    start, end = pure_day(m.get('start')), pure_day(m.get('end'))
    if end <= start or type(m.get('complete')) is not bool:
        raise ValueError('覆盖需要有效起止日与显式complete布尔标记')
    for key in ('dataset_version', 'license'):
        if not isinstance(m.get(key), str) or not m[key].strip() or len(m[key]) > 256:
            raise ValueError('必须声明数据版本与使用许可')
    m['url'] = official_url(m.get('url'), source)
    m['region'] = m.get('region', 'MMR')
    if not isinstance(m['region'], str) or not 1 <= len(m['region']) <= 64:
        raise ValueError('无效覆盖区域')
    m['start'], m['end'] = start.isoformat(), end.isoformat()
    m['end_exclusive'] = True
    for r in records:
        if not isinstance(r,dict):
            raise ValueError('批次记录必须为对象')
        if r.get('period_start') and not (start <= pure_day(r['period_start']) < pure_day(r['period_end']) <= end):
            raise ValueError('观测周期超出覆盖清单')
    result = {'source': source, 'manifest': m, 'records': json_safe(records)}
    result['id'] = fingerprint(result)
    result['acquired_at'] = utc_now().isoformat()
    return result


def table_records(path, sheet=None):
    """严格有界CSV/XLSX入口；未知表头由来源解析器拒绝，不自动猜文件格式。"""
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('输入文件过大，须拆批')
    if path.suffix.lower() == '.csv':
        return list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
    if path.suffix.lower() != '.xlsx':
        raise ValueError('仅支持UTF-8 CSV或XLSX')
    with ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 64 * 1024 * 1024:
            raise ValueError('压缩文件展开过大')
        for info in z.infolist():
            if info.filename.endswith('.xml'):
                xml = z.read(info)
                if b'\x00' in xml or re.search(br'<!DOCTYPE|<!ENTITY',xml,re.I):
                    raise ValueError('XLSX包含非UTF-8 XML或实体定义')
                for col in re.findall(br'\br=["\x27]([A-Z]+)[0-9]+',xml):
                    if len(col)>3:
                        raise ValueError('XLSX列索引过大')
    from analyzer.knowledge_base import read_xlsx
    book = read_xlsx(path)
    if sheet is None and len(book) != 1:
        raise ValueError('多工作表文件须指定sheet')
    rows = book.get(sheet) if sheet else next(iter(book.values()), [])
    if not rows:
        raise ValueError('表为空或工作表不存在')
    header = [str(c).strip() for c in rows[0]]
    if len(set(header)) != len(header):
        raise ValueError('表头重复')
    return [dict(zip(header, row)) for row in rows[1:] if any(str(c).strip() for c in row)]


def read_json(path):
    p = Path(path)
    if p.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('JSON文件过大')
    return json.loads(p.read_text(encoding='utf-8-sig'))
