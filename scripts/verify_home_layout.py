"""只读验收已运行的首页：多尺寸截图、菜单焦点、首屏尺寸与控制台错误。

用法：venv312/Scripts/python.exe scripts/verify_home_layout.py http://127.0.0.1:5001
不启动采集，不点击模型生成或写操作。截图写在工作区logs/home-redesign。
"""
import json
import hashlib
from urllib.parse import urlsplit, parse_qs
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:5001'
OUTPUT = ROOT / 'logs' / 'home-redesign'
SPEC = Path(r'C:\Users\10748\AppData\Roaming\Qoder\SharedClientCache\cache\plans\首页重构_设计方案_5b2f6d3b.md')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = []
    pages = []
    errors = []
    spec_hash = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    with sync_playwright() as api:
        options = {'headless': True, 'args': ['--disable-background-networking']}
        if os.environ.get('PLAYWRIGHT_BROWSER_EXECUTABLE'):
            options['executable_path'] = os.environ['PLAYWRIGHT_BROWSER_EXECUTABLE']
        elif sys.platform == 'win32':
            options['channel'] = 'msedge'
        browser = api.chromium.launch(**options)
        context = browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        calls = []
        page.on('request', lambda request: calls.append((request.method, request.url)))
        page.goto(URL + '/dashboard')
        page.wait_for_function('homeSnapshot !== null', timeout=40000)
        page.wait_for_selector('#home-map iframe', timeout=40000)
        page.wait_for_function("!document.querySelector('#home-sources').textContent.includes('正在读取')", timeout=30000)
        home_calls = list(calls)
        home = page.evaluate('({filters:homeSnapshot.filters,revision:homeSnapshot.revision,primary:document.querySelectorAll("main .btn-primary").length,details:document.querySelectorAll("main .home-detail-link").length})')
        assert home['filters']['days']==30 and home['filters']['region']=='MMR' and not home['filters'].get('source')
        assert home['primary']==1 and home['details']==3
        assert all('/api/report?' in url or '/api/map/unified?' in url or '/api/sources/health' in url for method,url in home_calls if '/api/' in url)
        map_query = parse_qs(urlsplit(next(url for method,url in home_calls if '/api/map/unified?' in url)).query)
        assert map_query['revision']==[home['revision']] and map_query['end_date']==[home['filters']['end_date']]
        for width, height in [(1440,900),(1280,800),(1279,900),(1024,900),(1023,900),(768,900),(767,900),(390,844)]:
            page.set_viewport_size({'width':width,'height':height})
            page.wait_for_timeout(400)
            filename = OUTPUT / f'homepage-{width}.png'
            page.screenshot(path=str(filename), full_page=True)
            result = page.evaluate('''() => ({
                width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
                cards:Array.from(document.querySelectorAll('.home-card')).map(n=>({
                    title:n.querySelector('h2').textContent,bottom:n.getBoundingClientRect().bottom,
                    height:n.getBoundingClientRect().height})),
                score:(()=>{const n=document.querySelector('.home-value');return {scroll:n.scrollHeight,client:n.clientHeight};})()
            })''')
            result['screenshot'] = str(filename)
            results.append(result)
        page.click('.nav-menu-toggle')
        page.screenshot(path=str(OUTPUT / 'mobile-menu.png'), full_page=True)
        page.keyboard.press('Escape')
        page.wait_for_function("!document.querySelector('#site-menu').open")
        page.wait_for_timeout(200)
        focus = page.evaluate("document.activeElement.className")
        page.set_viewport_size({'width':1440,'height':900})
        targets=[('/dashboard/indicators','#overview-body'),('/dashboard/events','#focus-events'),
                 ('/dashboard/data','#source-health-body'),('/map','#map-visible-status'),('/trend','#summary-cards'),
                 ('/graph','#graph-stats'),('/spatial','#spatial-detail'),('/multimodal','#correlation-body'),
                 ('/','#text-model-status'),('/analysis/risk','#risk-call-status'),('/workspace','#workspace-status'),
                 ('/literature','#catalog-summary'),('/research/methodology','main'),('/research/history','#history-body'),('/guide','main')]
        for index,(path,selector) in enumerate(targets):
            page.goto(URL + path)
            page.wait_for_function("selector => { const node=document.querySelector(selector); return node && node.textContent.trim() && !node.querySelector('.loading-spinner') && !/正在检查|正在读取|正在加载|检查账户/.test(node.textContent); }", arg=selector, timeout=40000)
            if path=='/map':
                page.wait_for_function('window._mmMap && window._mmLayers',timeout=40000)
            title=page.locator('.section-sidebar a.active').get_attribute('aria-label')
            assert title, path
            assert page.locator('main .btn-primary').count()<=1,path
            shot=OUTPUT/f'page-{index+1:02}.png'
            page.screenshot(path=str(shot),full_page=True)
            pages.append({'path':path,'current_menu':title,'ready_text':page.locator(selector).inner_text()[:250], 'screenshot':str(shot)})
            if path=='/map':
                assert page.locator('.section-sidebar').count()==1 and page.locator('.map-sidebar').count()==0
                page.locator('.map-toolbar > summary').click()
                assert page.locator('#lyr-kde').is_visible()
                page.locator('.map-toolbar > summary').click()
                assert page.locator('#lyr-kde').is_hidden()
            if path=='/analysis/risk':
                assert page.locator('#explain-model').is_hidden()
                page.locator('summary:has-text("模型设置")').click()
                page.select_option('#explain-model','deepseek-chat')
                page.locator('summary:has-text("模型设置")').click()
                assert 'API费用' in page.locator('#risk-call-status').inner_text()
        report={'spec':str(SPEC),'spec_sha256':spec_hash,'home':home,'viewports':results,'pages':pages,
                'focus_after_escape':focus,'page_errors':errors,'writes':[url for method,url in calls if method not in ('GET','HEAD')],
                'menu_screenshot':str(OUTPUT/'mobile-menu.png')}
        (OUTPUT/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        browser.close()
        assert not errors, errors
        assert 'nav-menu-toggle' in focus, focus
        assert all(r['scrollWidth'] <= r['width'] + 2 for r in results)
        assert all(max(card['bottom'] for card in r['cards']) <= r['height'] for r in results if r['width'] >= 1280), results
        assert not any(method not in ('GET','HEAD') for method,url in calls)
        assert hashlib.sha256(SPEC.read_bytes()).hexdigest()==spec_hash, 'Spec文件不得被修改'


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
