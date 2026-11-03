"""把演讲稿 HTML 渲染为 A4 PDF，并在渲染前做结构校验。

用法：
    python scripts/render_speech_pdf.py [输入.html] [输出.pdf]
默认读 docs/speech_script.html，写 docs/组会演讲稿_逐块原理与优化路线.pdf。
"""
from __future__ import annotations

import html.parser
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 允许自闭合/空元素，其余标签必须配对
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'param', 'source', 'track', 'wbr'}


class Checker(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f'第 {self.getpos()[0]} 行多余闭合 </{tag}>')
            return
        open_tag, line = self.stack.pop()
        if open_tag != tag:
            self.errors.append(f'第 {line} 行 <{open_tag}> 与第 {self.getpos()[0]} 行 </{tag}> 不配对')


def check(path: pathlib.Path) -> list:
    parser = Checker()
    parser.feed(path.read_text(encoding='utf-8'))
    parser.close()
    for tag, line in parser.stack:
        parser.errors.append(f'未闭合 <{tag}>（起于第 {line} 行）')
    return parser.errors


def render(source: pathlib.Path, target: pathlib.Path) -> None:
    style = "font-size:7pt; color:#8aa; font-family:'Microsoft YaHei',sans-serif;"
    header = (f'<div style="{style} width:100%; text-align:right; padding-right:4mm;">'
              '缅甸地缘风险智能分析系统 · 组会演讲稿</div>')
    footer = (f'<div style="{style} width:100%; text-align:center;">'
              '第 <span class="pageNumber"></span> / <span class="totalPages"></span> 页</div>')
    with sync_playwright() as api:
        browser = api.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page()
        page.goto(source.as_uri(), wait_until='load')
        # 等字体就绪，避免中文先渲染成后备字体
        page.evaluate('document.fonts.ready.then(() => true)')
        page.pdf(path=str(target), format='A4', print_background=True,
                 margin={'top': '16mm', 'bottom': '16mm', 'left': '15mm', 'right': '15mm'},
                 display_header_footer=True, header_template=header, footer_template=footer)
        browser.close()


def page_furniture(path: pathlib.Path) -> list:
    """回读 PDF 文本，确认每页只有一行页码（防止页脚重复）。"""
    from pypdf import PdfReader
    import re
    problems = []
    reader = PdfReader(path)
    for number, page in enumerate(reader.pages, 1):
        lines = [item for item in (page.extract_text() or '').split('\n')
                 if re.fullmatch(r'\s*第\s*\d+\s*/\s*\d+\s*页\s*', item)
                 or re.fullmatch(r'\s*第\s*\d+\s*页\s*', item)]
        if len(lines) > 1:
            problems.append(f'第 {number} 页有 {len(lines)} 行页码：{lines}')
    return problems


def main() -> int:
    source = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'docs' / 'speech_script.html'
    target = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / 'docs' / '组会演讲稿_逐块原理与优化路线.pdf'
    problems = check(source)
    if problems:
        print('HTML 结构问题，已中止渲染：')
        for item in problems[:20]:
            print('  ', item)
        return 1
    render(source, target)
    duplicated = page_furniture(target)
    print('结构校验通过')
    if duplicated:
        print('页码重复，请检查页眉页脚配置：')
        for item in duplicated[:5]:
            print('  ', item)
        return 1
    print('页码唯一性检查通过')
    print(f'PDF：{target}')
    print(f'大小：{target.stat().st_size / 1024:.0f} KB')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
