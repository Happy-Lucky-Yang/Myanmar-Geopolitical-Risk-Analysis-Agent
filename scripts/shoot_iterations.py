"""历代前端迭代截屏：V1-V4 复刻页（file://）+ V5 当前版本（运行中服务）。

用法: venv312\\Scripts\\python.exe scripts/shoot_iterations.py
输出: docs/screenshots/iterations/iteration_v*.png
"""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "screenshots" / "iterations" / "src"
OUT = ROOT / "docs" / "screenshots" / "iterations"
OUT.mkdir(parents=True, exist_ok=True)

MOCKUPS = [
    ("v1_initial.html", "iteration_v1_initial.png"),
    ("v2_dark_console.html", "iteration_v2_dark_console.png"),
    ("v3_mist_flat.html", "iteration_v3_mist_flat.png"),
    ("v4_sidebar_two_level.html", "iteration_v4_sidebar_two_level.png"),
]

LIVE_PAGES = [
    ("http://127.0.0.1:5000/dashboard", "iteration_v5_current_dashboard.png"),
    ("http://127.0.0.1:5000/remote-sensing", "iteration_v5_current_remote_sensing.png"),
]


def main():
    with sync_playwright() as p:
        options = {'headless': True}
        executable = os.environ.get('PLAYWRIGHT_BROWSER_EXECUTABLE')
        if executable:
            options['executable_path'] = executable
        elif sys.platform == 'win32':
            options['channel'] = 'msedge'
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for html, png in MOCKUPS:
            page.goto((SRC / html).as_uri())
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / png), full_page=True)
            print("saved", png)
        for url, png in LIVE_PAGES:
            page.goto(url)
            if 'remote-sensing' in url:
                # VIIRS 渲染耗时较长，等待主图 img 落地再截屏
                page.wait_for_selector('.rs-image-container img', timeout=120000)
                page.wait_for_timeout(1500)
            else:
                page.wait_for_timeout(4000)  # 等待地图 iframe 加载
            page.screenshot(path=str(OUT / png), full_page=False)
            print("saved", png)
        browser.close()
    print("done:", OUT)


if __name__ == "__main__":
    main()
