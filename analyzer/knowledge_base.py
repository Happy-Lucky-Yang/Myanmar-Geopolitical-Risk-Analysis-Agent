# -*- coding: utf-8 -*-
"""
analyzer.knowledge_base - 文献知识库（一期：元数据索引层）

数据源：组长团队整理的《缅甸地缘文献库·区域国别地缘环境计算·文献总表.xlsx》。
该源表属团队私密资料，位于仓库外私密目录，**不入库**；本模块只读解析、把结构化
索引缓存写入 gitignored 路径（默认 DATA_ROOT/knowledge_base/literature_index.json）。
源表路径通过环境变量 KB_SOURCE_XLSX 或 .env 提供（避免把本地私密路径写进开源的 config.yaml）。

一期能力（元数据层）：
  1. read_xlsx      —— stdlib zipfile+XML 解析（环境无 openpyxl），带 DTD/实体防护
  2. parse_literature_rows —— 19 列题录 → 规范化记录（作者/关键词切分、脏主题标签清洗、年份提取）
  3. build_index    —— 结构化索引 + facet 聚合 + 主题→四模块/九指标映射
  4. search         —— 题名/摘要/关键词/作者检索（字段加权 TF 打分 + facet 过滤）
  5. relevant_literature —— 按模块/指标/主题召回支撑文献（把文科文献接到技术模块）
  6. save_index / load_index —— JSON 持久化

职责边界：
  - 一期仅元数据（题录+摘要+关键词），**不含 PDF 全文**；全文层待 PDF 到位后二期接入。
  - 私密源表与其派生索引均不进入版本控制（见 .gitignore 的 data/knowledge_base/）。
  - 主题→模块映射基于源表「研究主题类别」列的规范值，未命中主题优雅降级（module 为空）。
"""
import json
import logging
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "kb-index-v1"

# ------------------------------------------------------------------
# xlsx（OOXML）命名空间
# ------------------------------------------------------------------
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# 合法 xlsx 部件（sheet/sharedStrings/workbook/rels）从不含 DTD/实体声明；
# 出现即视为不可信 XML，拒绝解析以防 XXE 与实体膨胀（billion laughs）攻击。
_FORBIDDEN_XML_TOKENS = (b"<!DOCTYPE", b"<!ENTITY")


def _safe_fromstring(data: bytes):
    """带 DTD/实体防护的 XML 解析（stdlib，无 defusedxml 依赖）。"""
    for token in _FORBIDDEN_XML_TOKENS:
        if token in data:
            raise ValueError("拒绝解析含 DTD/实体声明的 XML（防 XXE/实体膨胀）；合法 xlsx 部件不含这些声明")
    return ET.fromstring(data)


def _col_to_idx(ref: str) -> int:
    """单元格引用 A1→0、B1→1、AA1→26。"""
    letters = "".join(ch for ch in ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def read_xlsx(path: str) -> dict:
    """用 stdlib 读取 .xlsx，返回 {sheet_name: [[cell, ...], ...]}（openpyxl 缺失时的通道）。

    - 合并 sharedStrings 富文本 run；支持 inlineStr；缺省单元格填空串。
    - 所有 XML 部件经 _safe_fromstring 防护。
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = _safe_fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        wb = _safe_fromstring(z.read("xl/workbook.xml"))
        rels = _safe_fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {r.get("Id"): r.get("Target") for r in rels.findall(f"{_PKG}Relationship")}
        sheet_specs = []
        sheets_node = wb.find(f"{_NS}sheets")
        for sh in (sheets_node.findall(f"{_NS}sheet") if sheets_node is not None else []):
            target = rid_to_target.get(sh.get(f"{_RNS}id"), "")
            if target.startswith("/"):
                target = target[1:]
            elif not target.startswith("xl/"):
                target = "xl/" + target
            sheet_specs.append((sh.get("name"), target))

        def parse_sheet(target):
            root = _safe_fromstring(z.read(target))
            data = root.find(f"{_NS}sheetData")
            rows = []
            for r in (data.findall(f"{_NS}row") if data is not None else []):
                cells = {}
                for c in r.findall(f"{_NS}c"):
                    ref = c.get("r") or ""
                    ci = _col_to_idx(ref) if ref else len(cells)
                    t = c.get("t")
                    v = c.find(f"{_NS}v")
                    is_node = c.find(f"{_NS}is")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)] if v.text and v.text.isdigit() else ""
                    elif t == "inlineStr" and is_node is not None:
                        val = "".join(x.text or "" for x in is_node.iter(f"{_NS}t"))
                    elif v is not None:
                        val = v.text or ""
                    else:
                        val = ""
                    cells[ci] = val
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
            return rows

        book = {}
        for name, target in sheet_specs:
            try:
                book[name] = parse_sheet(target)
            except KeyError:
                book[name] = []
        return book


# ------------------------------------------------------------------
# 文献总表 19 列 schema（源表表头，见 docs 升级说明）
# ------------------------------------------------------------------
LITERATURE_COLUMNS = {
    0: "seq", 1: "category", 2: "subcategory", 3: "title", 4: "title_zh",
    5: "authors", 6: "institutions", 7: "year", 8: "journal", 9: "abstract",
    10: "keywords", 11: "doi", 12: "source_db", 13: "relevance", 14: "core_status",
    15: "topics", 16: "reason", 17: "notes", 18: "kb_file",
}

# 相关性等级（用于 min_relevance 过滤与排序）
RELEVANCE_RANK = {"高度相关": 4, "相关": 3, "边缘相关": 2, "需人工核查": 1, "不相关": 0}

# 四模块中英文标签（主题→模块映射的取值域）
MODULE_LABELS = {
    "geo_potential": "模块一·地缘位势",
    "spatial": "模块二·空间自相关",
    "network": "模块三·关系网络",
    "diagnostic": "模块四·诊断归因",
}

# 九项指标中英文标签（与 config.yaml risk_weights_v3 键一致）
INDICATOR_LABELS = {
    "conflict_frequency": "冲突频次", "china_myanmar_relation": "中缅关系",
    "china_us_rivalry": "中美博弈", "event_severity": "事件严重度",
    "nightlight_change": "夜光变化", "neighbor_relation": "周边邻国关系",
    "displacement": "流离失所", "western_relation": "西方(美缅)关系",
    "intl_sanction": "国际制裁",
}

# 「研究主题类别」规范值 → 智能计算四模块（四个精确 1:1 对应）
TOPIC_TO_MODULE = {
    "地缘位势与影响力测算": "geo_potential",
    "空间统计与空间自相关": "spatial",
    "地缘关系网络分析": "network",
    "地缘风险·脆弱性与归因": "diagnostic",
}

# 「研究主题类别」规范值 → 九项指标（可多对多）
TOPIC_TO_INDICATORS = {
    "缅甸·军方与安全事务": ["conflict_frequency", "event_severity"],
    "缅甸·民族政治与族群冲突": ["conflict_frequency", "event_severity"],
    "缅甸·对外关系与中缅双边": ["china_myanmar_relation", "neighbor_relation"],
    "大国博弈与全球地缘": ["china_us_rivalry", "western_relation", "intl_sanction"],
    "缅甸·社会人道与治理": ["displacement"],
    "缅甸·资源能源与农业": ["nightlight_change"],
    "地缘风险·脆弱性与归因": ["event_severity"],
}

_TOPIC_PREFIX_RE = re.compile(r"^\s*\d+\s*")
_YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")
_SPLIT_RE = re.compile(r"[；;]")


def _cell(row, idx):
    return (row[idx] or "").strip() if len(row) > idx and isinstance(row[idx], str) else ("" if len(row) <= idx else str(row[idx] or "").strip())


def _split_multi(text):
    return [p.strip() for p in _SPLIT_RE.split(text or "") if p.strip()]


def normalize_topic(raw):
    """清洗脏主题标签：去掉形如「12 」「15 」的编号前缀，返回规范主题名。"""
    return _TOPIC_PREFIX_RE.sub("", (raw or "").strip()).strip()


def _parse_year(text):
    m = _YEAR_RE.search(text or "")
    return int(m.group(1)) if m else None


def parse_literature_rows(rows):
    """把文献总表的数据行（不含表头）解析为规范化记录列表。

    - 作者/关键词/机构按中英文分号切分；主题按分号切分并清洗编号前缀后去重。
    - is_core 由「是否候选核心文献」列（是/否/需人工核查）派生。
    - modules/indicators 由规范主题经 TOPIC_TO_MODULE / TOPIC_TO_INDICATORS 派生。
    """
    records = []
    for i, row in enumerate(rows):
        if not row or not any((c or "").strip() for c in row if isinstance(c, str)):
            continue
        get = lambda idx: _cell(row, idx)  # noqa: E731
        title = get(3) or get(4)
        if not title:  # 无题名的空行跳过
            continue
        seq_raw = get(0)
        seq = int(seq_raw) if seq_raw.isdigit() else (i + 1)
        topics, seen = [], set()
        for t in _split_multi(get(15)):
            nt = normalize_topic(t)
            if nt and nt not in seen:
                seen.add(nt)
                topics.append(nt)
        modules = sorted({TOPIC_TO_MODULE[t] for t in topics if t in TOPIC_TO_MODULE})
        indicators, iseen = [], set()
        for t in topics:
            for ind in TOPIC_TO_INDICATORS.get(t, []):
                if ind not in iseen:
                    iseen.add(ind)
                    indicators.append(ind)
        core_status = get(14)
        records.append({
            "id": f"lit-{seq:04d}",
            "seq": seq,
            "category": get(1),
            "subcategory": get(2),
            "title": title,
            "title_zh": get(4),
            "authors": _split_multi(get(5)),
            "institutions": _split_multi(get(6)),
            "year": _parse_year(get(7)),
            "journal": get(8),
            "abstract": get(9),
            "keywords": _split_multi(get(10)),
            "doi": get(11),
            "source_db": get(12),
            "relevance": get(13),
            "relevance_rank": RELEVANCE_RANK.get(get(13), -1),
            "core_status": core_status,
            "is_core": core_status == "是",
            "topics": topics,
            "modules": modules,
            "indicators": indicators,
            "reason": get(16),
            "notes": get(17),
            "kb_file": get(18),
        })
    return records


def _facet(counter, by_key=False, top=None):
    items = sorted(counter.items(), key=(lambda kv: kv[0]) if by_key else (lambda kv: (-kv[1], str(kv[0]))))
    if top:
        items = items[:top]
    return {str(k): v for k, v in items}


def build_index(records, source_name="", stats=None):
    """由规范化记录构建结构化索引（含 facet 聚合与元信息）。"""
    rec_core = sum(1 for r in records if r["is_core"])
    facets = {
        "category": _facet(Counter(r["category"] for r in records if r["category"])),
        "subcategory": _facet(Counter(r["subcategory"] for r in records if r["subcategory"])),
        "topic": _facet(Counter(t for r in records for t in r["topics"])),
        "relevance": _facet(Counter(r["relevance"] for r in records if r["relevance"])),
        "core_status": _facet(Counter(r["core_status"] for r in records if r["core_status"])),
        "module": _facet(Counter(m for r in records for m in r["modules"])),
        "indicator": _facet(Counter(i for r in records for i in r["indicators"])),
        "year": _facet(Counter(r["year"] for r in records if r["year"]), by_key=True),
        "journal": _facet(Counter(r["journal"] for r in records if r["journal"]), top=50),
    }
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "layer": "metadata",
        "source_name": source_name,
        "record_count": len(records),
        "core_count": rec_core,
        "facets": facets,
        "stats": stats or {},
        "module_labels": MODULE_LABELS,
        "indicator_labels": INDICATOR_LABELS,
        "records": records,
    }


def empty_index(reason="unavailable"):
    """空索引（源表未配置/缺失时的优雅降级）。"""
    return {"algorithm_version": ALGORITHM_VERSION, "layer": "metadata", "source_name": "",
            "record_count": 0, "core_count": 0, "status": reason, "facets": {}, "stats": {},
            "module_labels": MODULE_LABELS, "indicator_labels": INDICATOR_LABELS, "records": []}


def _pick_main_sheet(book):
    """选定文献主表：表头首列为「序号」且列数≥15 的工作表（优先「文献总表」）。"""
    candidates = []
    for name, rows in book.items():
        if rows and len(rows[0]) >= 15 and (_cell(rows[0], 0) == "序号"):
            candidates.append((name, rows))
    if not candidates:
        return None, []
    candidates.sort(key=lambda nr: (0 if "总表" in nr[0] else 1, -len(nr[1])))
    return candidates[0]


def _parse_stats_sheet(book):
    """解析「统计与说明」表为 key→value（首列作键、次列作值）。"""
    stats = {}
    for name, rows in book.items():
        if "统计" in name or "说明" in name:
            for r in rows:
                k = _cell(r, 0)
                if k and len(r) > 1 and _cell(r, 1):
                    stats[k] = _cell(r, 1)
            break
    return stats


def build_index_from_xlsx(path):
    """读取文献总表 xlsx → 构建结构化索引。"""
    book = read_xlsx(path)
    sheet_name, rows = _pick_main_sheet(book)
    if not rows:
        raise ValueError(f"未在 {os.path.basename(path)} 中找到文献主表（表头首列应为「序号」）")
    records = parse_literature_rows(rows[1:])
    index = build_index(records, source_name=os.path.basename(path), stats=_parse_stats_sheet(book))
    index["sheet_name"] = sheet_name
    return index


# ------------------------------------------------------------------
# 检索
# ------------------------------------------------------------------
_FIELD_WEIGHTS = (("title", 5.0), ("title_zh", 4.0), ("keywords_joined", 4.0),
                  ("topics_joined", 2.5), ("abstract", 2.0), ("authors_joined", 2.0), ("journal", 1.0))


def _tokenize(query):
    """查询分词：优先 jieba（仓库已装），失败则退化为按空白/标点切分 + CJK 二字gram。"""
    q = (query or "").strip()
    if not q:
        return []
    try:
        import jieba
        toks = [t.strip().lower() for t in jieba.cut(q) if t.strip()]
        toks = [t for t in toks if not re.fullmatch(r"[\s\W]+", t)]
        if toks:
            return toks
    except Exception:
        pass
    parts = [p.lower() for p in re.split(r"[\s,，、/\\|]+", q) if p]
    out = []
    for p in parts:
        out.append(p)
        cjk = re.findall(r"[\u4e00-\u9fff]+", p)
        for seg in cjk:
            out.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return [t for t in dict.fromkeys(out) if t]


def _searchable(rec):
    return {
        "title": rec.get("title", "").lower(),
        "title_zh": rec.get("title_zh", "").lower(),
        "keywords_joined": " ".join(rec.get("keywords", [])).lower(),
        "topics_joined": " ".join(rec.get("topics", [])).lower(),
        "abstract": rec.get("abstract", "").lower(),
        "authors_joined": " ".join(rec.get("authors", [])).lower(),
        "journal": rec.get("journal", "").lower(),
    }


def _passes(rec, topics, category, subcategory, module, indicator, min_relevance, core_only, year_from, year_to):
    if core_only and not rec.get("is_core"):
        return False
    if category and rec.get("category") != category:
        return False
    if subcategory and rec.get("subcategory") != subcategory:
        return False
    if module and module not in rec.get("modules", []):
        return False
    if indicator and indicator not in rec.get("indicators", []):
        return False
    if topics:
        want = {normalize_topic(t) for t in topics}
        if not (want & set(rec.get("topics", []))):
            return False
    if min_relevance is not None:
        floor = RELEVANCE_RANK.get(min_relevance, 0) if isinstance(min_relevance, str) else int(min_relevance)
        if rec.get("relevance_rank", -1) < floor:
            return False
    year = rec.get("year")
    if year_from is not None and (year is None or year < int(year_from)):
        return False
    if year_to is not None and (year is None or year > int(year_to)):
        return False
    return True


def search(index, query="", topics=None, category=None, subcategory=None, module=None,
           indicator=None, min_relevance=None, core_only=False, year_from=None, year_to=None, limit=20):
    """字段加权 TF 检索 + facet 过滤。返回精简命中列表（含 abstract 摘要片段）。"""
    records = index.get("records", [])
    toks = _tokenize(query)
    results = []
    for rec in records:
        if not _passes(rec, topics, category, subcategory, module, indicator,
                       min_relevance, core_only, year_from, year_to):
            continue
        score, matched = 0.0, []
        if toks:
            fields = _searchable(rec)
            for term in toks:
                hit = False
                for fname, weight in _FIELD_WEIGHTS:
                    if term in fields[fname]:
                        score += weight
                        hit = True
                        if fname not in matched:
                            matched.append(fname)
            if not score:  # 有查询词但全字段未命中 → 不返回
                continue
        else:
            score = float(rec.get("relevance_rank", 0))  # 无查询词时按相关性排序浏览
        results.append({
            "id": rec["id"], "title": rec["title"], "year": rec.get("year"),
            "journal": rec.get("journal"), "authors": rec.get("authors", []),
            "relevance": rec.get("relevance"), "is_core": rec.get("is_core"),
            "topics": rec.get("topics", []), "modules": rec.get("modules", []),
            "indicators": rec.get("indicators", []),
            "score": round(score, 3), "matched_fields": matched,
            "abstract_snippet": (rec.get("abstract", "") or "")[:160],
        })
    results.sort(key=lambda r: (-r["score"], -(r["year"] or 0), r["id"]))
    return {"algorithm_version": ALGORITHM_VERSION, "query": query, "filters": {
        "topics": topics, "category": category, "subcategory": subcategory, "module": module,
        "indicator": indicator, "min_relevance": min_relevance, "core_only": core_only,
        "year_from": year_from, "year_to": year_to},
        "total_matched": len(results), "returned": min(limit, len(results)),
        "results": results[:limit] if limit else results}


def get_record(index, record_id):
    """按 id 取完整记录（含全文摘要与判断理由）。"""
    for rec in index.get("records", []):
        if rec.get("id") == record_id:
            return rec
    return None


def relevant_literature(index, module=None, indicator=None, topic=None, limit=10, min_relevance="相关"):
    """召回支撑某模块/指标/主题的文献，按相关性等级、年份排序（把文科文献接到技术模块）。"""
    records = [r for r in index.get("records", [])
               if _passes(r, [topic] if topic else None, None, None, module, indicator, min_relevance, False, None, None)]
    records.sort(key=lambda r: (-r.get("relevance_rank", -1), -(r.get("year") or 0), r["id"]))
    return {"algorithm_version": ALGORITHM_VERSION, "module": module, "module_label": MODULE_LABELS.get(module),
            "indicator": indicator, "indicator_label": INDICATOR_LABELS.get(indicator), "topic": topic,
            "count": len(records),
            "results": [{"id": r["id"], "title": r["title"], "year": r.get("year"), "journal": r.get("journal"),
                         "relevance": r.get("relevance"), "is_core": r.get("is_core"), "topics": r.get("topics", []),
                         "reason": r.get("reason", "")} for r in records[:limit]]}


# ------------------------------------------------------------------
# 持久化 + 配置解析 + 单例
# ------------------------------------------------------------------
def save_index(index, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    return path


def load_index(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_config():
    try:
        from utils.config import get_knowledge_base_config
        return get_knowledge_base_config()
    except Exception:
        return {"enabled": True, "source_xlsx": os.environ.get("KB_SOURCE_XLSX", ""),
                "index_path": os.environ.get("KB_INDEX_PATH", "")}


def load_or_build_index(cfg=None):
    """优先读缓存索引；无缓存但配置了源表则现场构建并落盘；皆无则返回空索引。"""
    cfg = cfg or _resolve_config()
    index_path = cfg.get("index_path")
    if index_path and os.path.exists(index_path):
        try:
            idx = load_index(index_path)
            idx["status"] = "cached"
            return idx
        except Exception as exc:  # 缓存损坏 → 尝试重建
            logger.warning("知识库索引缓存读取失败，尝试重建: %s", exc)
    source = cfg.get("source_xlsx")
    if source and os.path.exists(source):
        idx = build_index_from_xlsx(source)
        idx["status"] = "built"
        if index_path:
            try:
                save_index(idx, index_path)
            except Exception as exc:
                logger.warning("知识库索引写缓存失败（不影响本次使用）: %s", exc)
        return idx
    return empty_index("unavailable" if not source else "source_missing")


class KnowledgeBase:
    """文献知识库门面：惰性加载索引，暴露检索/召回/facet/状态。"""

    def __init__(self, index=None, cfg=None):
        self._cfg = cfg
        self._index = index

    @property
    def index(self):
        if self._index is None:
            self._index = load_or_build_index(self._cfg)
        return self._index

    def reload(self):
        self._index = load_or_build_index(self._cfg)
        return self._index

    def status(self):
        idx = self.index
        return {"algorithm_version": ALGORITHM_VERSION, "layer": "metadata", "status": idx.get("status", "ok"),
                "enabled": bool((self._cfg or _resolve_config()).get("enabled", True)),
                "source_name": idx.get("source_name", ""), "record_count": idx.get("record_count", 0),
                "core_count": idx.get("core_count", 0), "sheet_name": idx.get("sheet_name"),
                "stats": idx.get("stats", {}), "facets": idx.get("facets", {})}

    def search(self, **kwargs):
        return search(self.index, **kwargs)

    def relevant_literature(self, **kwargs):
        return relevant_literature(self.index, **kwargs)

    def get_record(self, record_id):
        return get_record(self.index, record_id)


_kb_singleton = None


def get_knowledge_base(force_reload=False):
    """知识库单例（惰性加载索引缓存）。"""
    global _kb_singleton
    if _kb_singleton is None or force_reload:
        _kb_singleton = KnowledgeBase()
    return _kb_singleton
