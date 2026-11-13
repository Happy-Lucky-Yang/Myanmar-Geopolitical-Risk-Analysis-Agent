"""操作瘦身审计：统计页面/模板中"用户可见的交互操作"数量并分级汇总。

零依赖（仅标准库 html.parser）。支持 Jinja 模板与静态 HTML；
对 display:none / hidden / type=hidden / <template> 内容自动排除。

用法:
    python count_actions.py templates/                 # 目录递归（默认 *.html）
    python count_actions.py a.html b.html --glob *.html
    python count_actions.py templates/ -o report.md    # 输出 Markdown 报告

计数口径（"可见操作"= 用户需要决策的交互点）:
    button   : <button>、<input type=submit|button|reset>
    action   : 扮演按钮的链接（class 含 btn/button 或 role=button）
    select   : <select>（每个下拉是一次决策）
    disclose : <summary>（折叠Disclosure，算半个操作，单独列）
    nav      : 导航链接（<nav> 内的 <a>），单独列不计入操作总数
    input    : 文本输入/上传（textarea、input text/file），单独列
"""
import argparse
import sys
from html.parser import HTMLParser
from pathlib import Path

HIDDEN_STYLE = ("display:none", "display: none", "visibility:hidden", "visibility: hidden")
BUTTON_INPUTS = {"submit", "button", "reset"}
ACTION_CLASS_HINTS = ("btn", "button")
# _void 元素无闭合标签：隐藏时不能推入 hidden_depth，否则深度永不恢复
VOID_TAGS = {"input", "img", "br", "hr", "meta", "link", "source", "track",
             "wbr", "col", "embed", "area", "base", "param"}


class ActionCounter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.counts = {"button": 0, "action": 0, "select": 0, "disclose": 0,
                       "nav": 0, "input": 0}
        self._hidden_stack = []     # 每层元素的隐藏标记栈（any=True 即处于隐藏容器内）
        self._nav_depth = 0         # 处于 <nav> 内的深度
        self._template_depth = 0    # <template> 内不计
        self._details_stack = []    # 每层 <details> 是否闭合（闭合=内容首屏不可见）
        self._closed_depth = 0      # 闭合 details 嵌套深度
        self._await_summary = False # 闭合 details 的 summary 本身仍可见
        self._skip_tags = {"script", "style"}
        self._skip_depth = 0

    @staticmethod
    def _is_hidden(attrs):
        d = dict(attrs)
        if "hidden" in d:
            return True
        style = d.get("style", "")
        return any(h in style.replace(" ", "") for h in
                   ("display:none", "visibility:hidden"))

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag in self._skip_tags:
            self._skip_depth += 1
            return
        if tag == "template":
            self._template_depth += 1
            return
        if self._skip_depth or self._template_depth:
            return
        if tag == "nav":
            self._nav_depth += 1
        outer_hidden = any(self._hidden_stack)
        if self._is_hidden(d):
            if tag not in VOID_TAGS:
                self._hidden_stack.append(True)
            return  # void 元素隐藏时只自己不计数，不影响后续兄弟节点
        if tag not in VOID_TAGS:
            self._hidden_stack.append(False)
        if outer_hidden:
            return
        if tag == "details":
            closed = "open" not in d
            self._details_stack.append(closed)
            if closed:
                self._closed_depth += 1
                self._await_summary = True
            return
        if tag == "summary":
            # 展开态的 summary、以及闭合 details 自身的 summary 都可见
            if self._closed_depth == 0 or (self._await_summary and self._closed_depth == 1):
                self.counts["disclose"] += 1
            self._await_summary = False
            return
        if self._closed_depth:
            return  # 闭合折叠内的控件首屏不可见，不计入
        cls = d.get("class", "")
        role = d.get("role", "")
        if tag == "button":
            self.counts["button"] += 1
        elif tag == "input" and d.get("type", "text") in BUTTON_INPUTS:
            self.counts["button"] += 1
        elif tag in ("textarea",) or (tag == "input" and d.get("type", "text")
                                      in ("text", "file", "search", "password")):
            self.counts["input"] += 1
        elif tag == "select":
            self.counts["select"] += 1
        elif tag == "summary":
            self.counts["disclose"] += 1
        elif tag == "a":
            if self._nav_depth:
                self.counts["nav"] += 1
            elif role == "button" or any(h in cls for h in ACTION_CLASS_HINTS):
                self.counts["action"] += 1
        # onclick 内联脚本也视为一个操作点（老代码常见）
        if "onclick" in d and tag not in ("a", "button"):
            self.counts["action"] += 1

    def handle_endtag(self, tag):
        if tag in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "template" and self._template_depth:
            self._template_depth -= 1
            return
        if tag == "details":
            if self._details_stack and self._details_stack.pop():
                self._closed_depth -= 1
            self._await_summary = False
        if tag == "nav" and self._nav_depth:
            self._nav_depth -= 1
        if tag not in VOID_TAGS and self._hidden_stack:
            self._hidden_stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1


def audit_file(path: Path) -> dict:
    parser = ActionCounter()
    try:
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        parser.close()
    except Exception as exc:  # 解析失败不中断整批审计
        return {"file": str(path), "error": str(exc)}
    c = parser.counts
    total = c["button"] + c["action"] + c["select"] + c["input"]
    return {"file": str(path), **c, "total": total}


LIVE_JS = """() => {
    const sel = 'button, input[type=submit], input[type=button], select, textarea,' +
        ' input:not([type=hidden]), summary, a[role=button], a[class*=btn], label[role=button]';
    const byKind = {}; let visible = 0, disabled = 0;
    for (const n of document.querySelectorAll(sel)) {
        if (n.type === 'hidden' || !n.checkVisibility || !n.checkVisibility()) continue;
        const r = n.getBoundingClientRect();
        if (r.height <= 0 || r.top >= 900) continue;
        if (n.closest('summary') && n.tagName !== 'SUMMARY') continue;
        visible += 1;
        if (n.disabled) disabled += 1;
        const k = n.tagName.toLowerCase() + (n.type ? ':' + n.type : '');
        byKind[k] = (byKind[k] || 0) + 1;
    }
    return {visible, disabled, byKind};
}"""


def report_live(urls, output=None):
    """Playwright 实跑首屏审计：JS 渲染的控件也计入，比静态解析更准。"""
    import os
    from playwright.sync_api import sync_playwright
    lines = ["| 页面 | 首屏可见操作 | 其中禁用 | 构成 |",
             "|------|-------------|---------|------|"]
    with sync_playwright() as p:
        options = {"headless": True}
        executable = os.environ.get("PLAYWRIGHT_BROWSER_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        elif sys.platform == "win32":
            options["channel"] = "msedge"
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for url in urls:
            page.goto(url)
            page.wait_for_timeout(3500)
            c = page.evaluate(LIVE_JS)
            kinds = ", ".join(f"{k}×{v}" for k, v in sorted(c["byKind"].items()))
            lines.append(f"| {url} | **{c['visible']}** | {c['disabled']} | {kinds} |")
        browser.close()
    lines.append("")
    lines.append("预算：首屏可见操作 ≤ 7±2；主操作每屏 1 个。")
    report = "\n".join(lines)
    print(report)
    if output:
        Path(output).write_text(report + "\n", encoding="utf-8")
        print("saved:", output)
    return 0


def main():
    ap = argparse.ArgumentParser(description="UI 操作瘦身审计（可见交互点计数）")
    ap.add_argument("paths", nargs="*", help="HTML 文件或目录（静态模式）")
    ap.add_argument("--glob", default="*.html")
    ap.add_argument("-o", "--output", help="Markdown 报告输出路径")
    ap.add_argument("--live", nargs="+", metavar="URL",
                    help="Playwright 实跑：统计首屏(900px)可见操作，JS 渲染控件也计入")
    args = ap.parse_args()

    if args.live:
        return report_live(args.live, args.output)

    files = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(path.rglob(args.glob)))
        elif path.is_file():
            files.append(path)
    rows = [audit_file(f) for f in files]
    rows = [r for r in rows if r.get("total", 0) or r.get("error")]
    rows.sort(key=lambda r: -r.get("total", 0))

    lines = ["| 文件 | 按钮 | 按钮化链接 | 下拉 | 输入 | 折叠 | 导航链接 | 可见操作合计 |",
             "|------|-----|-----------|------|------|------|---------|-------------|"]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['file']} | 解析失败: {r['error']} | | | | | | |")
            continue
        lines.append(f"| {Path(r['file']).name} | {r['button']} | {r['action']} | "
                     f"{r['select']} | {r['input']} | {r['disclose']} | {r['nav']} | "
                     f"**{r['total']}** |")
    worst = rows[0] if rows and "total" in rows[0] else None
    lines.append("")
    lines.append("预算规则：单屏可见操作 ≤ 7±2；主操作每屏 1 个；超标页面按 "
                 "references/reduction-playbook.md 瘦身。")
    if worst:
        lines.append(f"当前最高：{Path(worst['file']).name} = {worst['total']} 个可见操作。")
    report = "\n".join(lines)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
        print("saved:", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
