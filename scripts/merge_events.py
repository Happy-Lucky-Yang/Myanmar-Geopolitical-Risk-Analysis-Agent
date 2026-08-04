# -*- coding: utf-8 -*-
"""
历史事件批次一键入库工具
========================
用法:
  python scripts/merge_events.py 历史事件_批次2.json            # 正式入库
  python scripts/merge_events.py 历史事件_批次2.json --dry-run  # 只预演不落库

流程: 格式检查 -> 机械性自动修复 -> 字段合规校验 -> 与库查重 -> 合并排序写回
      -> 原批次文件归档到私密标注数据目录(文件名追加 _已入库)
"""
import argparse
import io
import json
import os
import re
import shutil
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.path.join(PROJECT_ROOT, "data", "raw", "historical_events.json")

EVENT_TYPES = {
    "军事冲突", "军事", "武装", "政变", "政治", "选举", "抗议", "暴力镇压",
    "外交", "经济", "经济制裁", "人道危机", "公共卫生", "自然灾害",
}
SOURCES = {"官方", "新闻报道", "全球新闻", "国际组织", "研究报告"}


def auto_fix(raw):
    """机械性修复: 只改引号外的结构性标点(引号内的正文内容一字不动)。
    返回(修复后文本, 修复说明列表)"""
    notes = set()
    out = []
    in_str = False
    esc = False
    half = str.maketrans("０１２３４５６７８９", "0123456789")
    for ch in raw:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            out.append(ch)
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
            elif ch == "，":
                notes.add("中文逗号 -> 英文逗号")
                out.append(",")
            elif ch == "：":
                notes.add("中文冒号 -> 英文冒号")
                out.append(":")
            elif ch in "０１２３４５６７８９":
                notes.add("全角数字 -> 半角数字")
                out.append(ch.translate(half))
            else:
                out.append(ch)
    fixed = "".join(out)
    if re.search(r",\s*\]", fixed):
        notes.add("删除末尾多余逗号")
        fixed = re.sub(r",\s*\]", "]", fixed)
    return fixed, sorted(notes)


def normalize_records(events):
    """解析后的记录级规整: 日期补零。返回修复说明列表"""
    notes = set()
    for e in events:
        date = str(e.get("date", ""))
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", date)
        if m:
            norm = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
            if norm != date:
                e["date"] = norm
                notes.add("日期补零: %s -> %s" % (date, norm))
    return sorted(notes)


def validate(events):
    """字段合规校验, 返回问题列表(空=全部合规)"""
    problems = []
    for i, e in enumerate(events, 1):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e.get("date", ""))):
            problems.append("第%d条: 日期格式非法 '%s'" % (i, e.get("date")))
        if e.get("event_type") not in EVENT_TYPES:
            problems.append("第%d条: event_type '%s' 不在14选1清单" % (i, e.get("event_type")))
        if e.get("source") not in SOURCES:
            problems.append("第%d条: source '%s' 不在5选1清单" % (i, e.get("source")))
        sev = e.get("severity")
        if not isinstance(sev, int) or not (1 <= sev <= 5):
            problems.append("第%d条: severity 必须是1~5整数" % i)
        if not e.get("description"):
            problems.append("第%d条: 缺少 description" % i)
        if not isinstance(e.get("actors"), list) or not e["actors"]:
            problems.append("第%d条: actors 必须是非空列表" % i)
    return problems


def get_archive_dir():
    """归档目录: DATA_ROOT 的同级 '标注数据' 文件夹; 取不到时回退项目内"""
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from utils.config import get_data_paths
        root = get_data_paths()["root"]
        return os.path.join(os.path.dirname(root), "标注数据")
    except Exception:
        return os.path.join(PROJECT_ROOT, "标注归档")


def main():
    parser = argparse.ArgumentParser(description="历史事件批次一键入库")
    parser.add_argument("batch", help="批次 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只预演, 不写库不归档")
    parser.add_argument("--force", action="store_true", help="字段校验有警告时仍强制入库")
    args = parser.parse_args()

    batch_path = os.path.abspath(args.batch)
    if not os.path.isfile(batch_path):
        print("[失败] 找不到文件: %s" % batch_path)
        sys.exit(1)

    print("=== 1. 读取与自动修复 ===")
    raw = open(batch_path, encoding="utf-8").read()
    fixed, notes = auto_fix(raw)
    if notes:
        print("  自动修复: " + "; ".join(notes))
    else:
        print("  无需修复, 格式干净")

    try:
        batch = json.loads(fixed)
    except Exception as e:
        print("[失败] 修复后仍无法解析: %s" % e)
        sys.exit(1)
    if not isinstance(batch, list) or not batch:
        print("[失败] 文件必须是包含至少一条记录的数组")
        sys.exit(1)
    print("  共 %d 条记录" % len(batch))
    rec_notes = normalize_records(batch)
    for n in rec_notes:
        print("  自动修复: " + n)

    print("=== 2. 字段合规校验 ===")
    problems = validate(batch)
    if problems:
        for p in problems:
            print("  [问题] " + p)
        if not args.force:
            print("[中止] 存在合规问题, 请退回去让标注同学修改 (确要强行入库请加 --force)")
            sys.exit(2)
    else:
        print("  全部合规")

    print("=== 3. 与库查重 ===")
    library = json.load(open(LIBRARY, encoding="utf-8"))
    seen = {(e["date"], e["description"]) for e in library}
    new_events = []
    for e in batch:
        key = (e["date"], e["description"])
        if key in seen:
            print("  [跳过重复] %s %s" % (e["date"], e["description"][:25]))
        else:
            new_events.append(e)
            seen.add(key)

    if not new_events:
        print("[完成] 本批全部是重复事件, 库未变更")
        sys.exit(0)

    print("=== 4. 合并写库%s ===" % ("(预演,不落盘)" if args.dry_run else ""))
    merged = sorted(library + new_events, key=lambda x: x["date"])
    if not args.dry_run:
        with open(LIBRARY, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    print("  库: %d -> %d 条 (新增 %d)" % (len(library), len(merged), len(new_events)))

    if not args.dry_run:
        print("=== 5. 归档原批次 ===")
        archive_dir = get_archive_dir()
        os.makedirs(archive_dir, exist_ok=True)
        base, ext = os.path.splitext(os.path.basename(batch_path))
        dest = os.path.join(archive_dir, base + "_已入库" + ext)
        shutil.move(batch_path, dest)
        print("  已归档: %s" % dest)

    print("\n全部完成%s" % ("(预演模式, 未做任何修改)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
