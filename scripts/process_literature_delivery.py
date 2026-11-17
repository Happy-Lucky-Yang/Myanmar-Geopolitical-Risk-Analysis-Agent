"""组长交付文献包处理流水线（2026-11-16）。

phase:
  extract    把仓库根四个 zip 移入私密目录并按正确文件名解包（GBK/UTF-8 标志自适应）
  inventory  逐文件 SHA256/分类/去重；PDF 抽前3页文本、EPUB 抽 OPF 元数据；落 inventory.jsonl
  metadata   由清单生成题录记录（待人工核查标记），与文献总表按题名/DOI 合并匹配
  refugees   难民数据包 CSV/XLSX 核验摘要 → 待审隔离层候选（eligible_for_scoring=0）
  rebuild    基础总表 + 交付题录合并重建知识库索引缓存

约定：PDF/EPUB 全文不公开；索引只暴露题录层；私密路径不进 public_record。
用法: venv312\\Scripts\\python.exe scripts/process_literature_delivery.py <phase> [--apply]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')

PRIV = Path(os.environ.get('LIT_DELIVERY_DIR', ''))
DEST = PRIV
ZIPS = ['01_02_03文献数据库等864个文件.zip',
        '待整理缅甸区域国别地缘政治_全部PDF_part480等1248个文件.zip',
        '相关文献等60个文件.zip',
        '难民数据等96个文件.zip']
DATA_EXT = {'.csv', '.xlsx', '.xls', '.json', '.sqlite3', '.db', '.parquet', '.dta', '.sav'}
TEXT_EXT = {'.txt', '.md', '.html', '.htm'}
OFFICE_EXT = {'.docx', '.doc', '.pptx', '.ppt'}


def sha256(path):
    h = hashlib.sha256()
    with open(lp(path), 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def lp(path):
    r"""Windows 长路径支持：超 MAX_PATH 时加 \\?\ 前缀（交付包内有 220+ 字符的文件名）。"""
    s = os.path.abspath(str(path))
    if os.name == 'nt' and len(s) > 250 and not s.startswith('\\\\?\\'):
        return '\\\\?\\' + s
    return s


def safe_component(name):
    """清洗 Windows 非法字符与首尾空白/点，避免解包落盘失败。"""
    name = re.sub(r'[<>:"|?*\x00-\x1f]', '_', name)
    return name.strip(' .') or '_'


def fixed_name(info):
    """zip 条目名：UTF-8 标志位优先；否则按中文 Windows 惯例 cp437→gbk 还原。"""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename
    try:
        decoded = raw.encode('cp437').decode('gbk')
        if '' not in decoded:
            return decoded
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return raw


def phase_extract():
    if not str(DEST):
        sys.exit('请先设置环境变量 LIT_DELIVERY_DIR 指向私密交付目录')
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name in ZIPS:
        src = ROOT / name
        moved = DEST / name
        if src.exists():
            shutil.move(str(src), str(moved))
            print('moved', name, flush=True)
        elif not moved.exists():
            print('MISSING', name, flush=True)
            continue
        outdir = DEST / Path(name).stem
        count = 0
        with zipfile.ZipFile(moved) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                rel = fixed_name(info).replace('\\', '/')
                rel = '/'.join(safe_component(p) for p in rel.split('/') if p not in ('', '.', '..'))
                target = outdir / rel
                os.makedirs(lp(target.parent), exist_ok=True)
                with z.open(info) as fin, open(lp(target), 'wb') as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
                manifest.append({'zip': name, 'entry': rel, 'path': str(target.relative_to(DEST)),
                                 'bytes': info.file_size})
                count += 1
        print('extracted', name, count, flush=True)
    (DEST / 'extract_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    print('extract manifest files:', len(manifest), flush=True)


def pdf_probe(path):
    out = {'pages': None, 'encrypted': False, 'text': '', 'error': None}
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        out['encrypted'] = bool(reader.is_encrypted)
        if out['encrypted']:
            try:
                reader.decrypt('')
            except Exception:
                out['error'] = 'encrypted'
                return out
        out['pages'] = len(reader.pages)
        chunks = []
        for page in reader.pages[:3]:
            try:
                chunks.append(page.extract_text() or '')
            except Exception:
                chunks.append('')
        out['text'] = '\n'.join(chunks)[:6000]
        meta = reader.metadata or {}
        out['pdf_title'] = str(meta.get('/Title') or '')[:300]
        out['pdf_author'] = str(meta.get('/Author') or '')[:300]
    except Exception as exc:
        out['error'] = f'{type(exc).__name__}: {exc}'[:200]
    return out


def epub_probe(path):
    out = {'title': '', 'creator': '', 'date': '', 'publisher': '', 'error': None}
    try:
        with zipfile.ZipFile(path) as z:
            opf = [n for n in z.namelist() if n.endswith('.opf')]
            if not opf:
                out['error'] = 'no opf'
                return out
            import xml.etree.ElementTree as ET
            raw = z.read(opf[0])
            # 不可信 XML：与 knowledge_base.read_xlsx 同口径，拒绝 DTD/实体声明防 XXE/实体膨胀
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                out['error'] = 'opf contains DTD/ENTITY, refused'
                return out
            root = ET.fromstring(raw)
            ns = {'dc': 'http://purl.org/dc/elements/1.1/'}
            for key, tag in (('title', 'title'), ('creator', 'creator'), ('date', 'date'), ('publisher', 'publisher')):
                node = root.find(f'.//dc:{tag}', ns)
                if node is not None and node.text:
                    out[key] = node.text.strip()[:300]
    except Exception as exc:
        out['error'] = f'{type(exc).__name__}: {exc}'[:200]
    return out


def classify(ext):
    if ext == '.pdf':
        return 'pdf'
    if ext == '.epub':
        return 'epub'
    if ext in DATA_EXT:
        return 'data'
    if ext in OFFICE_EXT:
        return 'office'
    if ext in TEXT_EXT:
        return 'text'
    if ext == '.py':
        return 'script'
    return 'other'


def phase_inventory():
    rows, seen = [], {}
    files = []
    for root, _dirs, names in os.walk(lp(DEST)):
        for name in names:
            if name in ('extract_manifest.json',) or name.startswith(('inventory', 'summary', 'delivery_', 'match_', 'refugee_')):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, lp(DEST))
            files.append((full, rel))
    files.sort(key=lambda fr: fr[1])
    for i, (full, rel) in enumerate(files):
        path = Path(rel)
        ext = path.suffix.lower()
        digest = sha256(full)
        zip_of = rel.replace('\\', '/').split('/')[0]
        row = {'path': rel.replace('\\', '/'), 'zip': zip_of, 'ext': ext, 'kind': classify(ext),
               'bytes': os.stat(lp(full)).st_size, 'sha256': digest, 'dup_of': None}
        if digest in seen:
            row['dup_of'] = seen[digest]
        else:
            seen[digest] = row['path']
            if row['kind'] == 'pdf':
                row.update(pdf_probe(lp(full)))
            elif row['kind'] == 'epub':
                row.update(epub_probe(lp(full)))
        rows.append(row)
        if (i + 1) % 200 == 0:
            print('inventory', i + 1, '/', len(files), flush=True)
    with open(DEST / 'inventory.jsonl', 'w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    uniq = [r for r in rows if not r['dup_of']]
    summary = {'files': len(rows), 'unique': len(uniq), 'duplicates': len(rows) - len(uniq),
               'by_kind': dict(Counter(r['kind'] for r in rows)),
               'unique_by_kind': dict(Counter(r['kind'] for r in uniq)),
               'pdf_errors': dict(Counter((r.get('error') or 'ok').split(':')[0] for r in uniq if r['kind'] == 'pdf')),
               'pdf_no_text': sum(1 for r in uniq if r['kind'] == 'pdf' and not (r.get('text') or '').strip()),
               'by_zip': dict(Counter(r['zip'] for r in rows))}
    (DEST / 'inventory_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


# ------------------------------------------------------------------
# metadata：题录抽取 + 与文献总表匹配
# ------------------------------------------------------------------
TOPIC_RULES = [
    ('缅甸·军方与安全事务', ['军方', '缅军', '政变', '国防军', '敏昂莱', '军事', '停火', '武装冲突', '军政府']),
    ('缅甸·民族政治与族群冲突', ['克钦', '若开', '掸邦', '民族地方武装', '民地武', '族群', '罗兴亚', '克伦', '德昂', '果敢', '佤邦']),
    ('缅甸·对外关系与中缅双边', ['中缅', '中国—缅甸', '中缅经济走廊', '一带一路', '皎漂', '瑞丽', '边境贸易', '胞波']),
    ('缅甸·经济走廊与互联互通', ['经济走廊', '互联互通', '基础设施', '港口', '铁路', '公路', '油气管道', '投资']),
    ('大国博弈与全球地缘', ['中美', '美国', '西方', '制裁', '印太', 'QUAD', '大国', '博弈', '稀土', '供应链']),
    ('缅甸·社会人道与治理', ['难民', '流离失所', 'IDP', '人道', '治理', '选举', '民主', '人权', '教育', '卫生']),
    ('缅甸·资源能源与农业', ['稀土', '能源', '电力', '夜光', '农业', '矿产', '资源', '玉石', '天然气']),
    ('缅甸·历史与地理环境', ['历史', '地理', '殖民', '独立', '王朝', '地形', '气候']),
    ('地缘环境理论与总论', ['地缘环境', '地缘政治', '地缘经济', '地缘战略', '理论', '分析框架', '国别']),
    ('空间统计与空间自相关', ['空间自相关', 'Moran', '空间统计', '地理加权', '空间计量', '核密度']),
    ('地缘关系网络分析', ['社会网络', '关系网络', '网络分析', '共现', '中心性']),
    ('地缘位势与影响力测算', ['位势', '引力模型', '影响力', '距离衰减', '辐射']),
    ('地缘风险·脆弱性与归因', ['风险', '脆弱性', '归因', '预警', '韧性']),
    ('数据·事件数据集与智能方法', ['GDELT', 'ACLED', '数据集', '机器学习', '深度学习', '大数据', '事件数据']),
]
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_DOI_RE = re.compile(r'10\.\d{4,9}/[^\s,;）)"<>]+')
_YEAR_RE = re.compile(r'(1[89]\d{2}|20\d{2})')
_ZH_SKIP = ('摘要', '关键词', 'Abstract', '中图', '分类号', 'doi', 'DOI', 'http', '学报', '杂志',
            '年第', '卷第', '收稿', '基金', '作者单位', '期刊', '编辑部', '出版社')


def norm_title(t):
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', (t or '').lower())


def assign_topics(text):
    low = (text or '').lower()
    hits = []
    for topic, kws in TOPIC_RULES:
        n = sum(1 for k in kws if k.lower() in low)
        if n:
            hits.append((n, topic))
    hits.sort(key=lambda x: -x[0])
    return [t for _, t in hits[:3]] or ['其他']


def _zh_meta(lines):
    out = {}
    title = None
    for i, ln in enumerate(lines[:12]):
        if 8 <= len(ln) <= 60 and not any(k in ln for k in _ZH_SKIP) and not ln.isdigit():
            title = ln
            out['title_at'] = i
            break
    if title:
        out['title'] = title
        for ln in lines[out['title_at'] + 1:out['title_at'] + 4]:
            if re.fullmatch(r'[\u4e00-\u9fff·]{2,4}([ ,，、\d]{1,3}[\u4e00-\u9fff·]{2,4}){0,5}', ln):
                out['authors'] = [a for a in re.split(r'[ ,，、]+', re.sub(r'\d', '', ln)) if a][:6]
                break
    abs_lines, kw_lines, mode = [], [], None
    for ln in lines:
        if ln.startswith('摘要') or ln.startswith('提 要'):
            mode = 'abs'
            ln = re.sub(r'^(摘要|提 要)[:：\s]*', '', ln)
        elif ln.startswith('关键词') or ln.startswith('关 键 词'):
            mode = 'kw'
            ln = re.sub(r'^(关键词|关 键 词)[:：\s]*', '', ln)
        elif ln.startswith(('Abstract', 'Key words', 'Keywords', '中图分类号', '一、', '1 ', '引言')):
            mode = None
        if mode == 'abs' and ln:
            abs_lines.append(ln)
        elif mode == 'kw' and ln:
            kw_lines.append(ln)
    if abs_lines:
        out['abstract'] = ' '.join(abs_lines)[:1500]
    if kw_lines:
        out['keywords'] = [k for k in re.split(r'[;；,，\s]+', ' '.join(kw_lines)) if k and len(k) <= 20][:10]
    for ln in lines[:6]:
        if any(k in ln for k in ('学报', '杂志', '期刊', '论坛', '评论', '研究')) and _YEAR_RE.search(ln):
            out['journal'] = re.sub(r'[\d年第卷期总第\s]+$', '', ln)[:60]
            break
    return out


def _en_meta(lines, pdf_title):
    out = {}
    title = (pdf_title or '').strip()
    if not (10 <= len(title) <= 300):
        title = ''
        for ln in lines[:8]:
            if len(ln) > 25 and not any(k in ln for k in ('Abstract', 'http', 'doi', 'Vol.', 'No.', 'pp.', 'Copyright', 'Available', 'journal')):
                title = ln
                break
    if title:
        out['title'] = title[:300]
    for ln in lines[:10]:
        if (' and ' in ln or ',' in ln) and any(k in ln for k in ('University', 'Department', 'Institute', 'College')) or re.match(r'^[A-Z][a-z]+ [A-Z][a-z]+(,| and )', ln):
            authors = re.split(r',| and ', ln)
            out['authors'] = [re.sub(r'\(.*?\)', '', a).strip() for a in authors if 3 < len(a.strip()) < 40][:6]
            break
    abs_lines, kw_lines, mode = [], [], None
    for ln in lines:
        low = ln.lower()
        if low.startswith('abstract'):
            mode = 'abs'
            ln = re.sub(r'^abstract[:.\s]*', '', ln, flags=re.I)
        elif low.startswith(('keywords', 'key words')):
            mode = 'kw'
            ln = re.sub(r'^(keywords|key words)[:.\s]*', '', ln, flags=re.I)
        elif low.startswith(('introduction', '1.', 'jel', 'copyright')):
            mode = None
        if mode == 'abs' and ln:
            abs_lines.append(ln)
        elif mode == 'kw' and ln:
            kw_lines.append(ln)
    if abs_lines:
        out['abstract'] = ' '.join(abs_lines)[:1500]
    if kw_lines:
        out['keywords'] = [k for k in re.split(r'[;,.]\s*', ' '.join(kw_lines)) if k and len(k) <= 30][:10]
    return out


def extract_meta(row):
    fname = Path(row['path']).stem
    text = row.get('text') or ''
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    meta = {'confidence': 'low', 'lang': 'zh' if len(_CJK_RE.findall(fname + text[:800])) > 20 else 'en'}
    # Anna's Archive 长文件名：Title -- Author -- Publisher -- doi ... -- hash
    if ' -- ' in fname:
        segs = [s.strip() for s in fname.split(' -- ')]
        meta['title'] = segs[0].replace('_', ' ').strip(' _')[:300]
        if len(segs) > 1:
            meta['authors'] = [re.sub(r'\(.*', '', segs[1]).strip()][:4]
        for seg in segs:
            m = re.search(r'10_\d{4,9}_\S+', seg)
            if m:
                d = m.group(0)
                d = d.replace('_', '.', 1).replace('_', '/', 1).replace('_', '.')
                meta['doi'] = d
        meta['confidence'] = 'medium'
    elif '_' in fname:
        parts = fname.split('_')
        if len(parts) >= 2 and re.fullmatch(r'[\u4e00-\u9fff·]{2,4}', parts[1]):
            meta['title'] = parts[0][:300]
            meta['authors'] = parts[1:2]
            meta['confidence'] = 'medium'
    if lines:
        parsed = _zh_meta(lines) if meta['lang'] == 'zh' else _en_meta(lines, row.get('pdf_title'))
        if parsed.get('title'):
            meta['title'] = parsed['title']
            meta['confidence'] = 'high' if parsed.get('abstract') else 'medium'
        for key in ('authors', 'abstract', 'keywords', 'journal'):
            if parsed.get(key) and not meta.get(key):
                meta[key] = parsed[key]
    m = _DOI_RE.search(text)
    if m:
        meta['doi'] = m.group(0).rstrip('.')
    y = _YEAR_RE.search(fname) or _YEAR_RE.search(text[:1500])
    if y:
        meta['year'] = int(y.group(1))
    if not meta.get('title'):
        meta['title'] = re.sub(r'[_\-]+', ' ', fname)[:300]
    return meta


def category_of(row, topics):
    zip_of, path = row['zip'], row['path']
    parts = path.split('/')
    if zip_of.startswith('01_02_03'):
        for p in parts[1:3]:
            if p.startswith('01'):
                return '01 缅甸研究文献', p
            if p.startswith('02'):
                return '02 地缘理论与方法', p
            if p.startswith('03'):
                return '03 历史事件与数据', p
        return '01 缅甸研究文献', ''
    if zip_of.startswith('难民数据'):
        return '03 历史事件与数据', 'MIMU 地图'
    if zip_of.startswith('相关文献'):
        return '02 地缘理论与方法', '参考文献'
    theory = {'地缘环境理论与总论', '空间统计与空间自相关', '地缘关系网络分析', '地缘位势与影响力测算'}
    if theory & set(topics):
        return '02 地缘理论与方法', parts[1] if len(parts) > 2 else ''
    return '01 缅甸研究文献', parts[1] if len(parts) > 2 else ''


def phase_metadata():
    from analyzer import knowledge_base as kb
    from utils.config import get_knowledge_base_config
    cfg = get_knowledge_base_config()
    base = kb.build_index_from_xlsx(cfg['source_xlsx'])['records']
    by_title, by_doi = {}, {}
    for r in base:
        by_title.setdefault(norm_title(r['title']), r['id'])
        if r.get('title_zh'):
            by_title.setdefault(norm_title(r['title_zh']), r['id'])
        if r.get('doi'):
            by_doi.setdefault(r['doi'].strip().lower(), r['id'])
    rows = [json.loads(l) for l in open(DEST / 'inventory.jsonl', encoding='utf-8') if l.strip()]
    uniq = [r for r in rows if not r['dup_of']]
    holdings, new_records, report = {}, [], Counter()
    seq = max(r.get('seq', 0) for r in base)
    for row in uniq:
        if row['kind'] not in ('pdf', 'epub'):
            report['skipped_' + row['kind']] += 1
            continue
        if row['kind'] == 'epub':
            meta = {'title': row.get('title') or Path(row['path']).stem, 'confidence': 'medium',
                    'lang': 'zh' if len(_CJK_RE.findall(row.get('title') or '')) > 3 else 'en'}
            if row.get('creator'):
                meta['authors'] = [row['creator']]
            if row.get('date'):
                y = _YEAR_RE.search(row['date'])
                if y:
                    meta['year'] = int(y.group(1))
            if row.get('publisher'):
                meta['journal'] = row['publisher']
        else:
            meta = extract_meta(row)
        topics = assign_topics(' '.join([meta.get('title', ''), meta.get('abstract', ''),
                                         ' '.join(meta.get('keywords', []))]))
        cat, sub = category_of(row, topics)
        doi = (meta.get('doi') or '').strip().lower()
        bid = by_doi.get(doi) or by_title.get(norm_title(meta.get('title', '')))
        hold = {'sha256': row['sha256'], 'path': row['path'], 'zip': row['zip']}
        if bid:
            entry = holdings.setdefault(bid, {'holdings': [], 'fill': {}})
            entry['holdings'].append(hold)
            base_rec = next(r for r in base if r['id'] == bid)
            for key in ('abstract', 'doi', 'year', 'journal', 'keywords'):
                if not base_rec.get(key) and meta.get(key):
                    entry['fill'][key] = meta[key]
            report['matched'] += 1
            continue
        seq += 1
        modules = sorted({kb.TOPIC_TO_MODULE[t] for t in topics if t in kb.TOPIC_TO_MODULE})
        indicators = sorted({i for t in topics for i in kb.TOPIC_TO_INDICATORS.get(t, [])})
        new_records.append({
            'id': f'lit-{seq:04d}', 'seq': seq, 'category': cat, 'subcategory': sub,
            'title': meta.get('title', ''), 'title_zh': meta.get('title', '') if meta['lang'] == 'zh' else '',
            'authors': meta.get('authors', []), 'institutions': [], 'year': meta.get('year'),
            'journal': meta.get('journal', ''), 'abstract': meta.get('abstract', ''),
            'keywords': meta.get('keywords', []), 'doi': meta.get('doi', ''),
            'source_db': '团队交付2026-11-16（自动抽取题录）', 'relevance': '需人工核查', 'relevance_rank': 1,
            'core_status': '需人工核查', 'is_core': False, 'topics': topics, 'modules': modules,
            'indicators': indicators, 'reason': f"自动抽取（{meta['confidence']}），主题由关键词规则映射",
            'notes': f"来源包：{row['zip']}；置信度 {meta['confidence']}；本地馆藏不公开全文",
            'kb_file': row['path'], 'holding_sha256': row['sha256'],
            'extract_confidence': meta['confidence'], 'lang': meta['lang'],
        })
        report['new'] += 1
        report['conf_' + meta['confidence']] += 1
        if row.get('error'):
            report['with_pdf_error'] += 1
    with open(DEST / 'delivery_records.jsonl', 'w', encoding='utf-8') as f:
        for r in new_records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    (DEST / 'holdings_map.json').write_text(json.dumps(holdings, ensure_ascii=False, indent=1), encoding='utf-8')
    report['base_records'] = len(base)
    report['matched_base_ids'] = len(holdings)
    (DEST / 'match_report.json').write_text(json.dumps(dict(report), ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(dict(report), ensure_ascii=False), flush=True)


# ------------------------------------------------------------------
# refugees：结构化数据进待审隔离层（eligible_for_scoring 恒 0）
# ------------------------------------------------------------------
def stage_refugee_rows(db_path, candidates, apply=False):
    """candidates: [{asset_id, path, sha256, payload, location}]；幂等：同 sha 不重复插。"""
    import sqlite3
    from datetime import datetime, timezone
    conn = sqlite3.connect(str(db_path))
    conn.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute("CREATE TABLE IF NOT EXISTS pending_records (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id), kind TEXT NOT NULL, location TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending_review','quarantined','missing')), eligible_for_scoring INTEGER NOT NULL CHECK(eligible_for_scoring=0), payload TEXT NOT NULL)")
    added = 0
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for cand in candidates:
        exists = conn.execute('select 1 from assets where id=?', (cand['asset_id'],)).fetchone()
        if exists:
            continue
        if apply:
            conn.execute('insert into assets values (?,?,?,?,?,?)',
                         (cand['asset_id'], cand['path'], cand['sha256'], 'displacement_dataset',
                          json.dumps(cand['payload'], ensure_ascii=False), now))
            conn.execute('insert into pending_records values (?,?,?,?,?,0,?)',
                         (cand['asset_id'] + '-review', cand['asset_id'], 'displacement_candidate',
                          cand['location'], 'pending_review', json.dumps(cand['payload'], ensure_ascii=False)))
        added += 1
    if apply:
        conn.commit()
    conn.close()
    return added


def phase_refugees(apply):
    import csv
    import io
    from utils.config import get_data_paths
    rows = [json.loads(l) for l in open(DEST / 'inventory.jsonl', encoding='utf-8') if l.strip()]
    cands = []
    for row in rows:
        if row['dup_of'] or not row['zip'].startswith('难民数据'):
            continue
        if row['kind'] != 'data' or row['ext'] not in ('.csv', '.xlsx', '.json'):
            continue
        if '_scripts' in row['path']:
            continue
        payload = {'file': Path(row['path']).name, 'bytes': row['bytes']}
        full = DEST / row['path']
        if row['ext'] == '.csv':
            with open(lp(full), encoding='utf-8-sig', errors='replace') as f:
                reader = csv.reader(f)
                header = next(reader, [])
                n = sum(1 for _ in reader)
            payload.update(rows=n, columns=header[:16])
        cands.append({'asset_id': 'refugee-' + row['sha256'][:16], 'path': row['path'],
                      'sha256': row['sha256'], 'location': row['path'], 'payload': payload})
    db = Path(get_data_paths()['external']) / 'team_delivery_review' / 'review.sqlite3'
    added = stage_refugee_rows(db, cands, apply)
    print(json.dumps({'candidates': len(cands), 'new': added, 'apply': apply}, ensure_ascii=False), flush=True)


# ------------------------------------------------------------------
# rebuild：总表 + 交付题录合并重建索引缓存
# ------------------------------------------------------------------
def phase_rebuild():
    from analyzer import knowledge_base as kb
    from utils.config import get_knowledge_base_config
    cfg = get_knowledge_base_config()
    index = kb.build_index_from_xlsx(cfg['source_xlsx'])
    index = kb.merge_delivery(index, cfg.get('extra_records_jsonl'), cfg.get('holdings_map_json'))
    kb.save_index(index, cfg['index_path'])
    print(json.dumps({'records': index['record_count'], 'core': index['core_count'],
                      'path': cfg['index_path']}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['extract', 'inventory', 'metadata', 'refugees', 'rebuild'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.phase == 'extract':
        phase_extract()
    elif args.phase == 'inventory':
        phase_inventory()
    elif args.phase == 'metadata':
        phase_metadata()
    elif args.phase == 'refugees':
        phase_refugees(args.apply)
    elif args.phase == 'rebuild':
        phase_rebuild()


if __name__ == '__main__':
    main()
