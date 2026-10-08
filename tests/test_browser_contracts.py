"""使用已安装浏览器和拦截路由的离线画布回归，不启动服务器或下载浏览器。"""
import os
import sys
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.browser


@pytest.fixture
def page(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as api:
        options = {'headless': True, 'args': ['--disable-background-networking'],
                   'env': {**os.environ, 'TEMP': str(tmp_path), 'TMP': str(tmp_path)}}
        executable = os.environ.get('PLAYWRIGHT_BROWSER_EXECUTABLE')
        if executable:
            options['executable_path'] = executable
        elif sys.platform == 'win32':
            options['channel'] = 'msedge'
        try:
            browser = api.chromium.launch(**options)
        except playwright.Error as exc:
            if "doesn't exist" in str(exc) or 'not found' in str(exc):
                pytest.skip(f'没有现有浏览器，未下载或安装：{exc}')
            raise
        context = browser.new_context(viewport={'width': 1280, 'height': 900}, service_workers='block')
        from app import app
        client = app.test_client()
        def serve(route):
            url = urlsplit(route.request.url)
            if url.hostname != 'offline.test':
                route.abort()
                return
            response = client.get(url.path + ('?' + url.query if url.query else ''))
            route.fulfill(status=response.status_code, body=response.data,
                          headers={key: value for key, value in response.headers.items()
                                   if key.lower() not in {'content-length', 'content-encoding'}})
        context.route('**/*', serve)
        page = context.new_page()
        yield page
        context.close()
        browser.close()


def test_trend_canvas_windows_empty_and_report_filters(page, tmp_path):
    from analyzer.data_loader import get_data_loader
    from datetime import date, timedelta
    loader = get_data_loader()
    for offset in range(60):
        day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
        loader.append_risk_score(day, 20 + offset / 2, '低风险', run_kind='existing', sources=['fixture'])
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://offline.test/trend?days=30&end_date=2026-03-01')
    page.wait_for_function("trendChart && trendChart.getOption().xAxis[0].data.length === 37")
    for days in [60, 90, 30]:
        page.select_option('#days-select', str(days))
        page.wait_for_function('(n) => trendChart.getOption().xAxis[0].data.length === n',
                               arg=days + (7 if days != 90 else 0))
        assert page.locator('#trend-chart canvas').count() > 0
        assert page.evaluate("""() => {
            const c = document.querySelector('#trend-chart canvas');
            return c.width > 0 && Array.from(c.getContext('2d').getImageData(0,0,c.width,c.height).data).some(v => v > 0);
        }""")
    href = page.locator('[data-report-format=html]').get_attribute('href')
    assert 'days=30' in href and 'end_date=2026-03-01' in href and 'revision=' in href
    page.locator('#trend-chart').screenshot(path=str(tmp_path / 'trend-canvas.png'))
    page.fill('#end_date-filter', '2025-01-01')
    page.click('button:has-text("刷新")')
    page.wait_for_selector('.chart-empty-state')
    assert page.evaluate('trendChart.getOption().series.length') == 0
    assert not errors


def test_trend_latest_response_wins_and_failure_disables_export(page):
    page.goto('http://offline.test/trend?days=30&end_date=2026-01-01')
    page.wait_for_function("document.querySelector('[data-report-format]').getAttribute('aria-disabled') === 'false'")
    page.evaluate("""() => {
        window.pendingTrend = [];
        window.fetchJSON = () => new Promise(resolve => pendingTrend.push(resolve));
        document.querySelector('#days-select').value = '60'; loadTrend();
        document.querySelector('#days-select').value = '90'; loadTrend();
    }""")
    assert page.locator('[data-report-format=html]').get_attribute('href') is None
    page.evaluate("""() => {
        const result = n => ({success:true, data:{revision:'rev-'+n, report_available:true,
            filters:{days:n,end_date:'2026-01-01',region:'MMR',source:null},
            history:[],forecast:[],metadata:{},trend_analysis:{},anomalies:[],chart_data:null}});
        pendingTrend[1](result(90));
        pendingTrend[0](result(60));
    }""")
    page.wait_for_function("document.querySelector('[data-report-format]').getAttribute('href').includes('days=90')")
    assert 'rev-90' in page.locator('[data-report-format=html]').get_attribute('href')
    page.evaluate("() => { window.fetchJSON = async () => {throw new Error('模拟断网');}; loadTrend(); }")
    page.wait_for_function("document.querySelector('#summary-cards').textContent.includes('请求失败')")
    assert page.locator('[data-report-format=html]').get_attribute('href') is None


def test_dashboard_snapshot_research_canvas_and_source_filters(page, tmp_path, monkeypatch):
    from analyzer.data_loader import get_data_loader
    from analyzer.multimodal_aligner import MultimodalAligner
    from data.event_store import get_event_store
    loader = get_data_loader()
    loader.append_risk_score('2026-03-31', 0, '低风险', run_kind='existing', sources=['fixture'])
    get_event_store().append([{'event_id': '1', 'date': '20260301', 'root_code': '19',
                              'source': 'fixture', 'description': '夹具事件', 'lat': 16.85, 'lon': 96.2}])
    monkeypatch.setattr(MultimodalAligner, 'observations', staticmethod(lambda: [{
        'id': 'fixture-light', 'indicator': 'nightlight', 'frequency': 'monthly', 'quality': 'observed',
        'region': 'MMR', 'source': 'fixture', 'unit': 'nW/cm²/sr', 'value': 0,
        'period_start': '2026-03-01', 'period_end': '2026-04-01'}]))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://offline.test/dashboard?days=90&end_date=2026-03-31')
    page.wait_for_function('dashboardSnapshot !== null')
    assert not page.locator('#research-details').get_attribute('open')
    assert '0.0' in page.locator('#overview-body').inner_text()
    assert '夹具事件' in page.locator('#focus-events').inner_text()
    href = page.locator('[data-report-format=html]').get_attribute('href')
    assert 'days=90' in href and 'revision=' in href
    page.locator('#research-details > summary').click()
    page.wait_for_function('mmChart && mmChart.getOption().series.length === 3')
    assert page.evaluate('mmChart.getOption().series[0].data[2]') == 0
    assert page.evaluate('mmChart.getWidth()') > 200
    page.locator('#multimodal-chart').screenshot(path=str(tmp_path / 'dashboard-multimodal.png'))
    page.locator('#correlation-body summary').click()
    assert '2026-04-01' in page.locator('#correlation-body').inner_text()
    assert 'nW/cm²/sr' in page.locator('#correlation-body').inner_text()
    page.fill('#source-filter', 'fixture')
    page.locator('button:has-text("刷新全部")').click()
    page.wait_for_function("dashboardSnapshot && dashboardSnapshot.filters.source === 'fixture'")
    assert '无当前观测' in page.locator('#overview-body').inner_text()
    assert 'source=fixture' in page.locator('[data-report-format=html]').get_attribute('href')
    page.locator('.nav-links a:has-text("对话分析")').click()
    assert 'source=fixture' in page.url and 'days=90' in page.url
    assert not errors


def test_dashboard_annual_values_remain_visible_without_monthly_data(page, monkeypatch):
    from analyzer.multimodal_aligner import MultimodalAligner
    row = {'id': 'annual-fixture', 'indicator': 'gdp_growth', 'frequency': 'annual',
           'quality': 'observed', 'run_kind': 'existing', 'dataset_version': 'fixture-v1',
           'region': 'MMR', 'source': 'fixture', 'unit': '%', 'value': 0,
           'period_start': '2025-01-01', 'period_end': '2026-01-01'}
    monkeypatch.setattr(MultimodalAligner, 'observations', staticmethod(lambda: [row]))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://offline.test/dashboard?days=365&end_date=2025-12-31&source=fixture')
    page.wait_for_function('dashboardSnapshot !== null')
    page.locator('#research-details > summary').click()
    page.wait_for_selector('#annual-observations tbody tr')
    assert '0.0000 / %' in page.locator('#annual-observations').inner_text()
    assert 'fixture-v1' in page.locator('#annual-observations').inner_text()
    assert '没有完整月份的有效多模态观测' in page.locator('#correlation-body').inner_text()
    assert page.evaluate('!mmChart || mmChart.getOption().series.length === 0')
    page.fill('#source-filter', 'other')
    page.locator('button:has-text("刷新全部")').click()
    page.wait_for_function("document.querySelector('#annual-observations')?.textContent.includes('无完整年度')")
    assert page.locator('#annual-observations tbody tr').count() == 0
    page.fill('#source-filter', 'fixture')
    page.locator('button:has-text("刷新全部")').click()
    page.wait_for_selector('#annual-observations tbody tr')
    page.fill('#end_date-filter', '2025-12-30')
    page.locator('button:has-text("刷新全部")').click()
    page.wait_for_function("dashboardSnapshot && dashboardSnapshot.filters.end_date === '2025-12-30'")
    page.wait_for_function("document.querySelector('#annual-observations')?.textContent.includes('无完整年度')")
    assert page.locator('#annual-observations tbody tr').count() == 0
    assert not errors


def test_dashboard_latest_snapshot_wins_and_failure_clears_exports(page):
    page.goto('http://offline.test/dashboard?days=30&end_date=2026-03-31')
    page.wait_for_function('dashboardSnapshot !== null')
    page.evaluate('''() => {
        window.initialSnapshot = dashboardSnapshot;
        window.pendingDashboard = [];
        window.fetchJSON = () => new Promise(resolve => pendingDashboard.push(resolve));
        document.querySelector('#days-select').value = '60'; loadAll();
        document.querySelector('#days-select').value = '90'; loadAll();
        const result = n => ({success:true, data:{...initialSnapshot, revision:'rev-'+n,
            filters:{...initialSnapshot.filters, days:n, source:'fixture'}}});
        pendingDashboard[1](result(90)); pendingDashboard[0](result(60));
    }''')
    page.wait_for_function("dashboardSnapshot && dashboardSnapshot.revision === 'rev-90'")
    assert 'days=90' in page.locator('[data-report-format=html]').get_attribute('href')
    page.evaluate("() => {window.fetchJSON = async () => {throw Error('模拟失败');}; loadAll();}")
    page.wait_for_function("document.querySelector('#overview-body').textContent.includes('模拟失败')")
    assert page.locator('[data-report-format=html]').get_attribute('href') is None


def test_map_windows_current_frame_and_kde_recovery(page, tmp_path):
    from data.event_store import get_event_store
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://offline.test/map?days=30&end_date=2026-03-31')
    page.wait_for_function('window._mmLayers && window._mmMap')
    assert page.locator('#lyr-kde').is_disabled()
    get_event_store().append([{'event_id': str(i), 'date': '20260315', 'root_code': '19',
                              'source': 'fixture', 'lat': 16.85 + .05*i, 'lon': 96.2 + .05*i} for i in range(8)])
    for days in [60, 90, 30]:
        page.select_option('#days-select', str(days))
        page.wait_for_function('(n) => window._mmLayers && !!window._mmLayers.kde && !document.querySelector("#lyr-kde").disabled && location.search.includes("days="+n)', arg=days)
        assert not page.locator('#lyr-kde').is_disabled()
        assert page.evaluate("window._mmMap === document.querySelector('#map-container iframe').contentWindow._mmMap")
    page.check('#lyr-kde')
    assert page.evaluate('window._mmMap.hasLayer(window._mmLayers.kde)')
    page.locator('#map-container').screenshot(path=str(tmp_path / 'map-current-layers.png'))
    assert 'revision=' in page.locator('[data-report-format=html]').get_attribute('href')
    assert not errors


def test_map_latest_response_wins_and_failure_disables_export(page):
    page.goto('http://offline.test/map?days=30&end_date=2026-01-01')
    page.wait_for_function("document.querySelector('[data-report-format]').getAttribute('aria-disabled') === 'false'")
    page.evaluate('''() => {
        window.pendingMap = [];
        window.fetch = () => new Promise(resolve => pendingMap.push(resolve));
        document.querySelector('#days-select').value = '60'; loadMap();
        document.querySelector('#days-select').value = '90'; loadMap();
        const result = n => new Response('<div class="fixture-map">窗口'+n+' '+'.'.repeat(60)+'</div>',
            {headers:{'X-Data-Revision':'rev-'+n}});
        pendingMap[1](result(90)); pendingMap[0](result(60));
    }''')
    page.wait_for_function("document.querySelector('#map-container').textContent.includes('窗口90')")
    assert 'rev-90' in page.locator('[data-report-format=html]').get_attribute('href')
    page.evaluate("() => {window.fetch = async () => {throw Error('模拟失败');}; loadMap();}")
    page.wait_for_function("document.querySelector('#map-query-status').textContent.includes('请求失败')")
    assert page.locator('[data-report-format=html]').get_attribute('href') is None
    assert page.locator('#layer-panel input:enabled').count() == 0


@pytest.mark.parametrize('primary_ok,fallback_ok', [(True, True), (False, True), (True, False), (False, False)])
def test_basemap_failover_pixels_and_repeated_zoom(page, tmp_path, primary_ok, fallback_ok):
    import io
    import re
    import folium
    from PIL import Image
    from visualization.map_gen import _add_basemap, _localize_cdn
    map_object = folium.Map(location=[19, 96], zoom_start=6, tiles=None, max_zoom=20)
    _add_basemap(map_object)
    html = _localize_cdn(map_object.get_root().render())
    page.route('**/map-fixture', lambda route: route.fulfill(body=html, content_type='text/html'))
    colors = {'primary': (24, 180, 72), 'fallback': (24, 72, 180)}
    images = {}
    for name, color in colors.items():
        stream = io.BytesIO()
        Image.new('RGB', (256, 256), color).save(stream, format='PNG')
        images[name] = stream.getvalue()
    def tiles(route):
        primary = 'cartocdn' in route.request.url
        if primary_ok if primary else fallback_ok:
            route.fulfill(body=images['primary' if primary else 'fallback'], content_type='image/png')
        else:
            route.abort()
    page.route(re.compile(r'https://.*(?:cartocdn\.com|arcgisonline\.com)/.*'), tiles)
    page.goto('http://offline.test/map-fixture')
    variable = map_object.get_name()
    assert page.evaluate(f'''() => Object.values({variable}._layers)
        .filter(l => l instanceof L.TileLayer).every(l => l.options.errorTileUrl !== L.Util.emptyImageUrl)''')
    for zoom in [6, 10, 5, 20, 6]:
        page.evaluate(f'{variable}.setZoom({zoom}, {{animate: false}})')
        def state():
            return page.evaluate(f'''() => Object.values({variable}._layers)
                .filter(l => l instanceof L.TileLayer).map(l => ({{url:l._url, loading:l._loading,
                    tiles:Object.values(l._tiles).slice(0,4).map(t => ({{src:t.el.src,
                        width:t.el.naturalWidth, active:t.active, loaded:t.loaded,
                        opacity:getComputedStyle(t.el).opacity, display:getComputedStyle(t.el).visibility}}))}}))''')
        try:
            page.wait_for_function(f'''() => {{
                const layers = Object.values({variable}._layers).filter(l => l instanceof L.TileLayer);
                return layers.length === 2 && layers.every(l => !l._loading
                    && Object.values(l._tiles).filter(t => t.current).every(t => t.active && getComputedStyle(t.el).opacity === '1'))
                    && Array.from(document.querySelectorAll('.leaflet-tile')).every(t => t.complete && t.naturalWidth > 0);
            }}''')
        except Exception as exc:
            page.screenshot(path=str(tmp_path / f'basemap-failure-{zoom}.png'))
            raise AssertionError(f'瓦片未完成解码：{state()}') from exc
        png = page.screenshot(path=str(tmp_path / f'basemap-zoom-{zoom}.png'))
        screenshot = Image.open(io.BytesIO(png)).convert('RGB')
        counts = dict((color, count) for count, color in screenshot.getcolors(1280 * 900))
        if primary_ok or fallback_ok:
            expected = colors['primary' if primary_ok else 'fallback']
            assert counts.get(expected, 0) > 10000, f'缩放{zoom}：{state()}；主色：{sorted(counts.items(), key=lambda p: p[1], reverse=True)[:5]}'
        else:
            assert counts.get(colors['primary'], 0) == counts.get(colors['fallback'], 0) == 0
            assert '底图不可用' in page.locator('body').inner_text()
    page.screenshot(path=str(tmp_path / f'basemap-{primary_ok}-{fallback_ok}.png'))
