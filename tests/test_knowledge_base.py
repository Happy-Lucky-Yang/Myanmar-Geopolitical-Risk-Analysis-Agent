# -*- coding: utf-8 -*-
"""文献知识库（analyzer.knowledge_base）契约测试。

用合成 fixture（不触碰私密源表）覆盖：xlsx 解析（手工构造最小 xlsx）、19 列规范化、
脏主题标签清洗、主题→四模块/九指标映射、facet 聚合、字段加权检索与过滤、
支撑文献召回、XML 安全守卫、持久化往返、无源表时的优雅降级。
"""
import json
import zipfile

import pytest

from analyzer import knowledge_base as kb


# ------------------------------------------------------------------
# 合成 19 列文献行（贴合源表 schema）
# ------------------------------------------------------------------
def row(seq, cat, subcat, title, title_zh, authors, insts, year, journal, abstract,
        kw, doi, srcdb, rel, core, topics, reason="", notes="", kbfile=""):
    return [str(seq), cat, subcat, title, title_zh, authors, insts, str(year), journal,
            abstract, kw, doi, srcdb, rel, core, topics, reason, notes, kbfile]


HEADER = ["序号", "类别", "子类", "题名", "题名中文（英文文献翻译）", "作者", "作者机构", "年份",
          "期刊/出版物", "摘要", "关键词", "DOI", "来源数据库", "相关性", "是否候选核心文献",
          "研究主题类别", "判断理由", "备注", "知识库文件标题"]

ROWS = [
    row(1, "01 缅甸研究文献", "军方与安全事务", "缅甸武装冲突时空演变及对中缅经济走廊影响", "",
        "杨璇；巴锐", "中国人民公安大学", 2026, "地理与地理信息科学", "武装冲突影响中缅一带一路推进",
        "缅甸武装冲突；中缅经济走廊", "10.1/a", "CNKI", "高度相关", "是",
        "空间统计与空间自相关；缅甸·经济走廊与互联互通", "支撑板块二"),
    row(2, "01 缅甸研究文献", "资源能源与农业", "缅甸能源与脆弱性研究", "",
        "李逸；赵媛", "南京师范大学", 2024, "自然资源学报", "构建能源安全指标体系并用熵值法定权",
        "能源；脆弱性", "10.1/b", "CNKI", "相关", "否",
        "12 缅甸·资源能源与农业；5 地缘风险·脆弱性与归因", "支撑板块四"),
    row(3, "02 地缘理论与方法", "地缘关系解构", "关系网络视角下的地缘结构", "",
        "王五", "云南师范大学", 2023, "地理学报", "用社会网络分析刻画国家间关系结构",
        "关系网络；地缘结构", "10.1/c", "CNKI", "边缘相关", "否", "地缘关系网络分析"),
    row(4, "02 地缘理论与方法", "大国博弈与全球地缘", "Geopolitics of Myanmar in Great-Power Rivalry",
        "缅甸地缘政治与大国博弈", "Smith; Jones", "Oxford", 2025, "Political Geography",
        "Great-power competition shapes Myanmar foreign relations", "Myanmar; geopolitics; rivalry",
        "10.1/d", "Scopus", "高度相关", "是", "大国博弈与全球地缘"),
    row(5, "01 缅甸研究文献", "地理环境与地缘结构", "缅甸地缘位势测度", "",
        "赵六", "西南大学", "约2019年", "地域研究与开发", "以距离加权测度各省邦地缘位势",
        "地缘位势；影响力", "10.1/e", "CNKI", "需人工核查", "需人工核查", "地缘位势与影响力测算"),
    row(6, "01 缅甸研究文献", "民族政治与族群冲突", "缅北族群武装冲突研究", "",
        "孙七", "中央民族大学", 2021, "东南亚研究", "族群政治与武装冲突的时空格局",
        "族群冲突；缅北", "10.1/f", "CNKI", "不相关", "否",
        "缅甸·军方与安全事务；缅甸·民族政治与族群冲突"),
]


@pytest.fixture(scope="module")
def index():
    return kb.build_index(kb.parse_literature_rows(ROWS), source_name="fixture.xlsx")


# ------------------------------------------------------------------
# 手工构造最小 xlsx，验证 read_xlsx（含 sharedStrings 解析）
# ------------------------------------------------------------------
def _colname(idx):
    s, idx = "", idx + 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _write_min_xlsx(path, sheet_name, rows):
    shared, sindex = [], {}

    def si(v):
        v = "" if v is None else str(v)
        if v not in sindex:
            sindex[v] = len(shared)
            shared.append(v)
        return sindex[v]

    sheet_rows = []
    for ri, r in enumerate(rows, start=1):
        cells = "".join(f'<c r="{_colname(ci)}{ri}" t="s"><v>{si(v)}</v></c>' for ci, v in enumerate(r))
        sheet_rows.append(f'<row r="{ri}">{cells}</row>')
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{main_ns}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    sst_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="{main_ns}" count="{len(shared)}" uniqueCount="{len(shared)}">' + "".join(f"<si><t>{_esc(s)}</t></si>" for s in shared) + "</sst>"
    wb_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{main_ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{_esc(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    wb_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' \
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' \
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>'
    root_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' \
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    content_types = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' \
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' \
        '<Default Extension="xml" ContentType="application/xml"/>' \
        f'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' \
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' \
        f'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", wb_xml)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/sharedStrings.xml", sst_xml)


# ------------------------------------------------------------------
# 解析与清洗
# ------------------------------------------------------------------
def test_parse_normalizes_fields_and_splits(index):
    r1 = next(r for r in index["records"] if r["id"] == "lit-0001")
    assert r1["authors"] == ["杨璇", "巴锐"]
    assert r1["keywords"] == ["缅甸武装冲突", "中缅经济走廊"]
    assert r1["year"] == 2026 and r1["is_core"] is True and r1["relevance_rank"] == 4


def test_dirty_topic_prefix_is_cleaned(index):
    r2 = next(r for r in index["records"] if r["id"] == "lit-0002")
    # "12 缅甸·资源能源与农业" / "5 地缘风险·脆弱性与归因" → 去编号前缀
    assert r2["topics"] == ["缅甸·资源能源与农业", "地缘风险·脆弱性与归因"]
    assert kb.normalize_topic("15 其他") == "其他"


def test_messy_year_extracted(index):
    r5 = next(r for r in index["records"] if r["id"] == "lit-0005")
    assert r5["year"] == 2019                       # "约2019年" → 2019
    assert r5["core_status"] == "需人工核查" and r5["is_core"] is False


def test_english_title_and_translation(index):
    r4 = next(r for r in index["records"] if r["id"] == "lit-0004")
    assert r4["title"].startswith("Geopolitics") and r4["title_zh"] == "缅甸地缘政治与大国博弈"
    assert r4["authors"] == ["Smith", "Jones"]


# ------------------------------------------------------------------
# 主题 → 四模块 / 九指标映射
# ------------------------------------------------------------------
def test_four_modules_exact_topic_mapping():
    assert kb.TOPIC_TO_MODULE == {"地缘位势与影响力测算": "geo_potential", "空间统计与空间自相关": "spatial",
                                  "地缘关系网络分析": "network", "地缘风险·脆弱性与归因": "diagnostic"}
    assert set(kb.MODULE_LABELS) == {"geo_potential", "spatial", "network", "diagnostic"}


def test_module_and_indicator_derivation(index):
    by = {r["id"]: r for r in index["records"]}
    assert by["lit-0001"]["modules"] == ["spatial"]                    # 空间统计→模块二
    assert by["lit-0002"]["modules"] == ["diagnostic"]                 # 脆弱性与归因→模块四
    assert by["lit-0002"]["indicators"] == ["nightlight_change", "event_severity"]
    assert by["lit-0003"]["modules"] == ["network"]                    # 关系网络→模块三
    assert by["lit-0005"]["modules"] == ["geo_potential"]              # 位势测度→模块一
    assert set(by["lit-0004"]["indicators"]) == {"china_us_rivalry", "western_relation", "intl_sanction"}
    assert by["lit-0006"]["indicators"] == ["conflict_frequency", "event_severity"]


def test_facets_aggregate(index):
    assert index["record_count"] == 6 and index["core_count"] == 2
    assert index["facets"]["module"]["spatial"] == 1
    assert index["facets"]["relevance"]["高度相关"] == 2
    assert index["facets"]["topic"]["大国博弈与全球地缘"] == 1
    assert index["facets"]["year"]["2026"] == 1


# ------------------------------------------------------------------
# 检索与过滤
# ------------------------------------------------------------------
def test_search_matches_title_and_keyword(index):
    res = kb.search(index, query="武装冲突")
    assert res["results"] and res["results"][0]["id"] == "lit-0001"
    assert res["results"][0]["score"] > 0


def test_search_english_query(index):
    res = kb.search(index, query="Myanmar geopolitics")
    assert any(r["id"] == "lit-0004" for r in res["results"])


def test_search_filters(index):
    assert {r["id"] for r in kb.search(index, module="network")["results"]} == {"lit-0003"}
    assert {r["id"] for r in kb.search(index, core_only=True)["results"]} == {"lit-0001", "lit-0004"}
    # min_relevance="相关"(floor 3) 只留 高度相关/相关：1,2,4
    assert {r["id"] for r in kb.search(index, min_relevance="相关")["results"]} == {"lit-0001", "lit-0002", "lit-0004"}
    assert {r["id"] for r in kb.search(index, year_from=2026)["results"]} == {"lit-0001"}
    # 过滤按主题时脏前缀也会被规范化后匹配
    assert {r["id"] for r in kb.search(index, topics=["12 缅甸·资源能源与农业"])["results"]} == {"lit-0002"}


def test_search_empty_query_browses_all(index):
    res = kb.search(index, query="", limit=100)
    assert res["total_matched"] == 6
    assert res["results"][0]["relevance"] == "高度相关"          # 无查询词按相关性排序


def test_relevant_literature_by_module_and_indicator(index):
    mod = kb.relevant_literature(index, module="spatial")
    assert mod["module_label"] == "模块二·空间自相关" and [r["id"] for r in mod["results"]] == ["lit-0001"]
    ind = kb.relevant_literature(index, indicator="conflict_frequency", min_relevance="不相关")
    assert ind["indicator_label"] == "冲突频次" and any(r["id"] == "lit-0006" for r in ind["results"])
    # 默认 min_relevance=相关 时不相关的 lit-0006 被挡掉
    assert kb.relevant_literature(index, indicator="conflict_frequency")["count"] == 0


# ------------------------------------------------------------------
# read_xlsx / 安全 / 持久化 / 降级
# ------------------------------------------------------------------
def test_read_xlsx_roundtrip(tmp_path):
    path = tmp_path / "lit.xlsx"
    _write_min_xlsx(str(path), "文献总表", [HEADER] + ROWS)
    book = kb.read_xlsx(str(path))
    assert "文献总表" in book and book["文献总表"][0][0] == "序号"
    idx = kb.build_index_from_xlsx(str(path))
    assert idx["record_count"] == 6 and idx["sheet_name"] == "文献总表"
    assert idx["facets"]["module"]["network"] == 1


def test_xml_dtd_guard_rejects_doctype():
    with pytest.raises(ValueError):
        kb._safe_fromstring(b'<!DOCTYPE foo [<!ENTITY x "y">]><foo/>')
    with pytest.raises(ValueError):
        kb._safe_fromstring(b'<?xml version="1.0"?><!ENTITY a "b"><foo/>')


def test_save_load_roundtrip(tmp_path, index):
    p = tmp_path / "idx.json"
    kb.save_index(index, str(p))
    loaded = kb.load_index(str(p))
    assert loaded["record_count"] == index["record_count"]
    assert json.loads(p.read_text(encoding="utf-8"))["records"][0]["id"] == "lit-0001"


def test_load_or_build_degrades_and_caches(tmp_path):
    # 无源表 → 空索引优雅降级
    empty = kb.load_or_build_index({"source_xlsx": "", "index_path": str(tmp_path / "none.json")})
    assert empty["status"] == "unavailable" and empty["record_count"] == 0
    # 有源表 → 现场构建并落盘，二次读取命中缓存
    src = tmp_path / "lit.xlsx"
    _write_min_xlsx(str(src), "文献总表", [HEADER] + ROWS)
    cache = str(tmp_path / "kb" / "literature_index.json")
    built = kb.load_or_build_index({"source_xlsx": str(src), "index_path": cache})
    assert built["status"] == "built" and built["record_count"] == 6
    cached = kb.load_or_build_index({"source_xlsx": str(src), "index_path": cache})
    assert cached["status"] == "cached"


def test_tokenize_nonempty_and_facade_status(tmp_path):
    assert kb._tokenize("缅甸 武装冲突")
    assert kb._tokenize("   ") == []
    src = tmp_path / "lit.xlsx"
    _write_min_xlsx(str(src), "文献总表", [HEADER] + ROWS)
    facade = kb.KnowledgeBase(cfg={"source_xlsx": str(src), "index_path": str(tmp_path / "i.json")})
    st = facade.status()
    assert st["record_count"] == 6 and st["status"] == "built" and st["layer"] == "metadata"
    assert facade.search(query="能源")["results"]


# ------------------------------------------------------------------
# Flask 端点集成（合成索引入单例，不依赖私密源表）
# ------------------------------------------------------------------
def test_api_endpoints(index, monkeypatch):
    from app import app
    monkeypatch.setattr(kb, "_kb_singleton", kb.KnowledgeBase(index=index))
    client = app.test_client()

    st = client.get("/api/knowledge_base/status")
    assert st.status_code == 200 and st.json["success"] is True
    assert st.json["data"]["record_count"] == 6 and st.json["data"]["core_count"] == 2

    r = client.get("/api/knowledge_base/search?q=Geopolitics&limit=5")
    assert r.status_code == 200 and r.json["success"] is True
    assert any(x["id"] == "lit-0004" for x in r.json["data"]["results"])

    rel = client.get("/api/knowledge_base/relevant?module=spatial")
    assert rel.status_code == 200 and rel.json["data"]["module_label"] == "模块二·空间自相关"
    assert [x["id"] for x in rel.json["data"]["results"]] == ["lit-0001"]

    core = client.get("/api/knowledge_base/search?core_only=true")
    assert {x["id"] for x in core.json["data"]["results"]} == {"lit-0001", "lit-0004"}

    rec = client.get("/api/knowledge_base/record/lit-0001")
    assert rec.status_code == 200 and rec.json["data"]["id"] == "lit-0001"
    assert client.get("/api/knowledge_base/record/lit-9999").status_code == 404
