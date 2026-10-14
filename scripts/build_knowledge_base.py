# -*- coding: utf-8 -*-
"""构建文献知识库元数据索引（一期）。

从组长团队整理的《文献总表.xlsx》（私密源表，路径经 KB_SOURCE_XLSX / .env 提供）解析
645 篇题录 → 规范化 + 主题清洗 + 主题→四模块/九指标映射 + facet 聚合 → 结构化索引 JSON，
落盘到 DATA_ROOT/knowledge_base/literature_index.json（已 gitignore，不入版本控制）。

用法：
    python scripts/build_knowledge_base.py                 # 用 .env/config 的源表路径
    python scripts/build_knowledge_base.py --source X.xlsx --output idx.json
    python scripts/build_knowledge_base.py --print-facets   # 附带打印 facet 概览
退出码：0 成功；2 源表缺失/不可读。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyzer.knowledge_base import build_index_from_xlsx, save_index  # noqa: E402
from utils.config import get_knowledge_base_config  # noqa: E402


def _summarize(index):
    lines = [
        f"algorithm_version = {index.get('algorithm_version')}",
        f"source_name       = {index.get('source_name')}  (sheet: {index.get('sheet_name')})",
        f"record_count      = {index.get('record_count')}   core_count = {index.get('core_count')}",
    ]
    facets = index.get("facets", {})
    years = facets.get("year", {})
    if years:
        ys = sorted(int(y) for y in years if str(y).isdigit())
        lines.append(f"year_range        = {ys[0]}~{ys[-1]}  (distinct {len(ys)})")
    for name in ("category", "relevance", "module", "indicator"):
        f = facets.get(name, {})
        preview = ", ".join(f"{k}:{v}" for k, v in list(f.items())[:8])
        lines.append(f"facet[{name}] = {preview}")
    stats = index.get("stats", {})
    if stats:
        lines.append("stats(说明表):")
        for k, v in list(stats.items())[:8]:
            lines.append(f"    {k} = {v[:60]}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="构建文献知识库元数据索引（一期）")
    parser.add_argument("--source", help="文献总表 xlsx 路径（覆盖 KB_SOURCE_XLSX/config）")
    parser.add_argument("--output", help="索引 JSON 输出路径（覆盖 KB_INDEX_PATH/config）")
    parser.add_argument("--print-facets", action="store_true", help="打印 facet 概览")
    args = parser.parse_args(argv)

    cfg = get_knowledge_base_config()
    source = args.source or cfg.get("source_xlsx")
    output = args.output or cfg.get("index_path")
    if not source or not Path(source).exists():
        print(f"[KB] 源表未配置或不存在: {source or '(空)'}\n"
              f"     请在 .env 设置 KB_SOURCE_XLSX 指向《文献总表.xlsx》，或用 --source 传入。", file=sys.stderr)
        return 2
    index = build_index_from_xlsx(source)
    if output:
        save_index(index, output)
        print(f"[KB] 索引已写入: {output}")
    else:
        print("[KB] 未配置输出路径（KB_INDEX_PATH/config index_path 为空），仅构建未落盘。")
    print(_summarize(index))
    if args.print_facets:
        import json
        print("\n[KB] facets(topic top20):")
        print(json.dumps(dict(list(index.get("facets", {}).get("topic", {}).items())[:20]),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
