# -*- coding: utf-8 -*-
"""
标注文件自检工具（文科同学专用）
================================
交作业前，把你要交的 JSON 文件拖到本脚本图标上（或在命令行运行），
它会自动检查格式问题并用大白话告诉你哪里要改。

支持两种文件：
  - 历史事件文件（含 date / severity 栏目）
  - NER 圈词标注文件（含 text / entities 栏目）

无需安装任何东西，Windows 自带功能即可运行。
"""
import json
import os
import re
import sys

# ---------- 规范清单（与 docs/annotation_guide.md 保持一致） ----------
EVENT_TYPES = {
    "军事冲突", "军事", "武装", "政变", "政治", "选举", "抗议", "暴力镇压",
    "外交", "经济", "经济制裁", "人道危机", "公共卫生", "自然灾害",
}
SOURCES = {"官方", "新闻报道", "全球新闻", "国际组织", "研究报告"}
EVENT_WORDS = {
    "冲突", "战斗", "空袭", "武装", "交火", "爆炸", "袭击", "制裁",
    "政变", "选举", "抗议", "暴动", "难民", "停火", "和谈",
    "贸易", "投资", "管道", "港口", "铁路", "经济走廊",
}
ENTITY_FIELDS = ("locations", "organizations", "persons", "events")

errors = []    # 必须改的硬伤
warnings = []  # 建议改的小问题


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def auto_fix(raw):
    """尝试机械性修复，返回修复后的文本（仅用于继续检查内容）"""
    fixed = raw.replace("，", ",").replace("：", ":")
    fixed = re.sub(r",\s*\]", "]", fixed)
    fixed = re.sub(r",\s*\}", "}", fixed)
    fixed = re.sub(
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        lambda m: "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))),
        fixed,
    )
    return fixed


def check_historical_event(record, idx):
    """检查一条历史事件记录"""
    label = "第%d条" % idx

    # 日期
    date = record.get("date", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)):
        err("%s 日期'%s'格式不对，应写成 2026-03-01 这种（月、日要补零）" % (label, date))

    # 事件类型
    etype = record.get("event_type", "")
    if etype not in EVENT_TYPES:
        err("%s 事件类型'%s'不在14选1清单里（见标注指南第三节）" % (label, etype))

    # 参与方
    actors = record.get("actors", [])
    if not isinstance(actors, list) or not actors:
        err("%s 参与方 actors 要写成 [\"缅甸国防军\", \"中国\"] 这种列表" % label)

    # 严重度
    sev = record.get("severity")
    if not isinstance(sev, int) or not (1 <= sev <= 5):
        err("%s 严重程度 severity 必须是 1~5 的数字（不要加引号）" % label)

    # 描述
    desc = record.get("description", "")
    if not desc:
        err("%s 缺少描述 description" % label)
    elif len(desc) > 60:
        warn("%s 描述有%d个字，建议压缩到50字以内" % (label, len(desc)))

    # 来源
    source = record.get("source", "")
    if source not in SOURCES:
        err("%s 资料来源'%s'不在5选1清单：官方/新闻报道/全球新闻/国际组织/研究报告" % (label, source))


def check_ner_record(record, idx):
    """检查一条 NER 圈词记录"""
    label = "第%d条" % idx

    text = record.get("text", "")
    if not text or len(text) < 30:
        err("%s 原文 text 太短或缺失，请粘贴新闻全文" % label)

    entities = record.get("entities")
    if not isinstance(entities, dict):
        err("%s 缺少 entities（圈出的词）部分" % label)
        return

    for field in ENTITY_FIELDS:
        values = entities.get(field, [])
        if not isinstance(values, list):
            err("%s 的 %s 要写成列表，如 [\"缅甸\", \"掸邦\"]" % (label, field))
            continue
        for v in values:
            if not isinstance(v, str) or not v.strip():
                err("%s 的 %s 里有空的或非文字的条目" % (label, field))

    # 事件词必须在词表内
    for word in entities.get("events", []):
        if isinstance(word, str) and word not in EVENT_WORDS:
            warn("%s 事件词'%s'不在21词词表内：请先不圈，记入备注反馈给技术同学" % (label, word))


def main():
    print("=" * 56)
    print("        标注文件自检工具（交作业前跑一下）")
    print("=" * 56)

    # 获取文件路径：拖拽/命令行参数/手动输入
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = input("请把要检查的 JSON 文件路径粘贴到这里（或直接把文件拖到本脚本上）：\n> ").strip().strip('"')

    if not os.path.isfile(path):
        print("\n[找不到文件] 没找到'%s'，请确认路径对不对。" % path)
        input("\n按回车键退出...")
        return

    raw = open(path, encoding="utf-8").read()
    print("\n正在检查：%s" % os.path.basename(path))
    
    # ---- 第一步：找最常见的“隐形杀手” ----
    # 先把所有引号里的内容（正文/描述）挖空再检查：
    # 引号内的中文标点是合法的，只有引号外的中文标点才会弄坏文件
    structural = re.sub(r'"(?:[^"\\]|\\.)*"', '""', raw)
    for i, line in enumerate(structural.splitlines(), 1):
        if "，" in line:
            err("第%d行结构里用了中文逗号'，'，要换成英文逗号','（注意：只改引号外的标点，新闻正文里的不用改）" % i)
        if "：" in line:
            err("第%d行结构里用了中文冒号'：'，要换成英文冒号':'" % i)
        if re.search(r"[０-９]", line):
            err("第%d行出现了全角数字（如'６'），要用英文半角数字" % i)
    if re.search(r",\s*\]", structural):
        err("最后一个条目后面多了一个逗号，删掉它")

    # ---- 第二步：解析 JSON ----
    try:
        records = json.loads(raw)
        print("[语法] 文件能正常打开")
    except Exception:
        err("文件目前打不开（JSON 语法错误），先把上面列的标点问题改掉再检查")
        records = None
        try:
            records = json.loads(auto_fix(raw))
            if records is not None:
                print("[语法] （我帮你模拟修正标点后能打开，以下内容检查结果基于修正后的版本）")
        except Exception:
            records = None
            err("修正标点后仍打不开：可能存在括号不配对，建议对照指南第二节的卡片结构逐行检查")

    # ---- 第三步：按任务类型逐条检查 ----
    if isinstance(records, list) and records:
        first = records[0]
        if "date" in first and "severity" in first:
            task = "A（历史事件）"
            for i, r in enumerate(records, 1):
                check_historical_event(r, i)
        elif "text" in first and "entities" in first:
            task = "B（NER 圈词）"
            for i, r in enumerate(records, 1):
                check_ner_record(r, i)
        else:
            task = None
            err("认不出这是哪种文件：既没有 date/severity（任务A），也没有 text/entities（任务B）")
        if task:
            print("[类型] 识别为任务 %s，共 %d 条记录" % (task, len(records)))
    elif isinstance(records, list):
        err("文件是空的（一条记录都没有）")
    else:
        err("最外层要用方括号 [ ] 包住所有记录（一摞卡片）")

    # ---- 结果汇总 ----
    print("\n" + "=" * 56)
    if not errors and not warnings:
        print("  全部通过！可以放心交作业了")
    else:
        if errors:
            print("  必须修改的问题（%d 个）：" % len(errors))
            for m in errors:
                print("    [要改] " + m)
        if warnings:
            print("  建议留意的问题（%d 个）：" % len(warnings))
            for m in warnings:
                print("    [建议] " + m)
        if not errors:
            print("  没有硬伤，改完建议项即可交付")
    print("=" * 56)
    input("\n按回车键退出...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n脚本自己出了点问题：%s" % e)
        input("\n按回车键退出...")
