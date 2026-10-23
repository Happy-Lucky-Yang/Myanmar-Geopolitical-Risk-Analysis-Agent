"""首页分层与导航契约：合成快照的离线浏览器验收，不写真实数据。"""
import json
from urllib.parse import parse_qs, urlsplit
import pytest
from tests.test_browser_contracts import page


PAGES = ['/dashboard', '/dashboard/indicators', '/dashboard/events', '/dashboard/data',
         '/spatial', '/multimodal', '/analysis/risk', '/research/methodology', '/research/history']


def test_new_pages_and_legacy_query_redirect():
    from app import app
    client = app.test_client()
    for path in PAGES:
        response = client.get(path)
        assert response.status_code == 200
        assert 'section-sidebar' in response.get_data(as_text=True)
    response = client.get('/dashboard?days=90&source=fixture&end_date=2026-03-31')
    assert response.status_code == 302
    target = urlsplit(response.headers['Location'])
    assert target.path == '/dashboard/indicators'
    assert parse_qs(target.query)['source'] == ['fixture']
    assert client.get('/').status_code == 200


def test_home_does_not_mount_advanced_controls():
    from app import app
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(app.test_client().get('/dashboard').data, 'html.parser')
    assert len(soup.select('main .home-card')) == 4
    assert len(soup.select('main .btn-primary')) == 1
    assert not soup.select('main select, main input, #risk-panel-section, #research-details, [data-report-format]')
    scripts = [s.get('src', '') for s in soup.find_all('script')]
    assert not any('risk_explain' in s or 'briefing.js' in s or 'risk_panels.js' in s for s in scripts)
    texture=soup.select_one('.home-boundary-texture')
    assert texture and texture.get('aria-hidden')=='true' and texture.select('path')


def test_compact_map_no_kde_computation_or_scatter(monkeypatch):
    import app as module
    from analyzer.event_density import EventDensityAnalyzer
    monkeypatch.setattr(EventDensityAnalyzer, 'compute', lambda *a, **kw: pytest.fail('首页不计算KDE'))
    monkeypatch.setattr(module, '_build_province_risk_data', lambda: [
        {'province': '若开邦', 'risk_score': 70, 'sample_count': 2}])
    response = module.app.test_client().get('/api/map/unified?compact=1&days=30')
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert 'home-map-legend' in text
    # Folium将GeoJSON属性编码为ASCII JSON，中文以Unicode转义存储。
    assert '样本偏少' in text or r'\u6837\u672c\u504f\u5c11' in text
    assert 'circleMarker(' not in text
    assert '_mmLayers' not in text
    assert response.headers.get('X-Data-Revision')


@pytest.fixture
def snapshot():
    def record(day, value):
        return {'date':day,'risk_score':value,'algorithm_version':'risk-v2','sources':['fixture'],
                'news_count':8,'details':{'indicator_coverage':.4,'weights_used':{'sentiment_avg':1}}}
    return {'revision':'fixture-revision','snapshot_id':'fixture-snapshot',
            'filters':{'days':30,'end_date':'2026-10-22','region':'MMR','source':None},
            'history':[record('2026-10-21',40),record('2026-10-22',42)],
            'metadata':{'valid_days':2,'coverage':2/30,'algorithm_version':'risk-v2'},'warnings':[],
            'events':[{'event_id':str(i),'date':'2026-10-22','root_code':'19' if i==3 else '04',
                       'location':'若开邦','source':'gdelt','description':'夹具记录'} for i in range(5)]}


def freeze_home_day(page):
    from datetime import datetime, timezone
    page.clock.set_fixed_time(datetime(2026,10,22,12,tzinfo=timezone.utc))


def mount_home(page, snapshot):
    freeze_home_day(page)
    page.route('**/api/report?*', lambda route: route.fulfill(status=200, content_type='application/json',
               body=json.dumps({'success':True,'data':snapshot})))
    page.route('**/api/map/unified?*', lambda route: route.fulfill(status=200,
               headers={'Content-Type':'text/html','X-Data-Revision':snapshot['revision']},
               body='<iframe srcdoc="<p>地图夹具（非真实数据）</p>"></iframe>'))
    page.goto('http://offline.test/dashboard')
    page.wait_for_function('homeSnapshot !== null')
    page.wait_for_selector('#home-map iframe')


@pytest.mark.browser
def test_home_four_modules_default_queries_no_writes_and_layout(page, snapshot):
    calls = []
    errors = []
    page.on('request', lambda request: calls.append((request.method, request.url)))
    page.on('pageerror', lambda error: errors.append(str(error)))
    mount_home(page, snapshot)
    assert '+2.0分' in page.locator('#home-risk').inner_text()
    assert page.locator('#home-events .home-event').count() == 3
    assert 'event_id=3' in page.locator('#home-events a').first.get_attribute('href')
    assert 'revision=fixture-revision' in page.locator('#home-map-link').get_attribute('href')
    assert page.locator('.section-sidebar a.active').get_attribute('aria-label') == '综合态势'
    for width, height in [(1440,900),(1280,800),(1024,900),(768,900),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        if width >= 1280:
            assert page.locator('.home-grid').bounding_box()['y'] + page.locator('.home-grid').bounding_box()['height'] <= height
    apis = [url for method,url in calls if '/api/' in url]
    assert all(method == 'GET' for method,url in calls)
    assert all('/api/report?' in url or '/api/map/unified?' in url or '/api/sources/health' in url for url in apis)
    query = parse_qs(urlsplit(next(url for url in apis if '/api/report?' in url)).query)
    assert query['days'] == ['30'] and query['region'] == ['MMR']
    assert not errors


@pytest.mark.browser
def test_home_missing_current_and_uncomparable_versions(page, snapshot):
    snapshot['history'][-1]['algorithm_version'] = 'different-version'
    mount_home(page, snapshot)
    assert '无法同口径比较' in page.locator('#home-risk').inner_text()
    page.evaluate("homeSnapshot.history.pop(); renderHomeSnapshot(homeSnapshot)")
    assert page.locator('.home-value').inner_text() == '—'
    assert '历史参考' in page.locator('#home-risk').inner_text()
    assert '截止日缺测' in page.locator('#home-data').inner_text()
    assert page.locator('.home-value').evaluate('(node) => node.style.color') == ''


@pytest.mark.browser
def test_mobile_menu_keyboard_and_details_navigation(page, snapshot):
    mount_home(page, snapshot)
    page.set_viewport_size({'width':390,'height':844})
    page.click('.nav-menu-toggle')
    assert page.locator('#site-menu').get_attribute('open') is not None
    page.keyboard.press('Escape')
    assert page.locator('#site-menu').get_attribute('open') is None
    assert page.locator('.nav-menu-toggle').evaluate('(node) => node === document.activeElement')
    page.click('.nav-menu-toggle')
    page.locator('#site-menu a:has-text("风险与指标")').click()
    assert urlsplit(page.url).path == '/dashboard/indicators'


@pytest.mark.browser
def test_home_map_failure_and_source_failure_are_independent(page, snapshot):
    mount_home(page, snapshot)
    page.route('**/api/map/unified?*', lambda route: route.fulfill(status=503,content_type='application/json',body='{}'))
    page.evaluate('loadHomeMap(homeSnapshot,homeSequence)')
    page.wait_for_function("document.querySelector('#home-map').textContent.includes('暂不可用')")
    assert '42.0' in page.locator('#home-risk').inner_text()
    assert page.locator('#home-events .home-event').count() == 3
    page.route('**/api/sources/health', lambda route: route.fulfill(status=503,content_type='application/json',body='{}'))
    page.evaluate('loadHomeSources()')
    page.wait_for_function("document.querySelector('#home-sources').textContent.includes('暂不可用')")
    assert '42.0' in page.locator('#home-risk').inner_text()


@pytest.mark.browser
def test_home_latest_response_wins(page, snapshot):
    mount_home(page, snapshot)
    page.evaluate("() => {window.pendingHome=[]; window.fetchJSON=()=>new Promise(r=>pendingHome.push(r)); loadHomeSnapshot(); loadHomeSnapshot();}")
    newer = {**snapshot, 'revision':'newer'}
    older = {**snapshot, 'revision':'older'}
    page.evaluate('([newer,older]) => {pendingHome[1]({success:true,data:newer});pendingHome[0]({success:true,data:older});}', [newer,older])
    page.wait_for_function("homeSnapshot?.revision === 'newer'")
    assert 'revision=newer' in page.locator('#home-risk-link').get_attribute('href')


def test_source_manifest_counts_unrun_sources_without_faking_success(monkeypatch):
    from types import SimpleNamespace
    import app as module
    monkeypatch.setattr(module, '_active_source_names', lambda: {'a','b'})
    monkeypatch.setattr('data.source_health.get_source_health_tracker', lambda: SimpleNamespace(get_health=lambda: {
        'a':{'status':'healthy','records':[{'ok':True,'count':0,'time':'2026-10-22T00:00:00Z'}]},
        'removed':{'status':'dead'}}))
    result = module.app.test_client().get('/api/sources/health').get_json()
    assert result['active_count'] == 2 and set(result['data']) == {'a'}
    assert 'b' not in result['data']  # 无运行记录不伪造成功或失败结果


def test_shared_roles_preserved_without_real_model_or_database_writes(monkeypatch):
    import app as module
    import utils.security as security
    monkeypatch.setitem(module.app.config, 'SHARED_MODE', True)
    monkeypatch.setattr(security, 'user_by_id', lambda uid: {'id':uid,'username':uid,'role':uid})
    monkeypatch.setattr(security, 'rate_allowed', lambda *a, **kw: True)
    calls = []
    monkeypatch.setattr('analyzer.risk_explainer.explain_risk', lambda **kw: calls.append(kw) or {'status':'ok','answer':{}})
    for role, status in [('reader',403),('analyst',403),('admin',200)]:
        client = module.app.test_client()
        with client.session_transaction() as session:
            session['uid'] = role
            session['csrf'] = 'fixture-token'
        response = client.post('/api/risk/explain', json={}, headers={'X-CSRF-Token':'fixture-token'})
        assert response.status_code == status
    assert len(calls) == 1  # 仅管理员进入既有服务端生成路径，模型已被夹具替换


@pytest.mark.browser
def test_sidebar_focus_tooltip_exact_breakpoints_and_focus_trap(page, snapshot):
    mount_home(page,snapshot)
    for width in (1440,1280,1279,1024,1023,768,767,390):
        page.set_viewport_size({'width':width,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        if width >= 1024:
            assert page.locator('.section-sidebar').is_visible()
            assert page.locator('.section-sidebar').bounding_box()['width'] == (208 if width >= 1280 else 72)
        else:
            assert page.locator('.section-sidebar').is_hidden()
        if width < 768: assert page.locator('.nav-links').is_hidden()
        if 768 <= width < 1024: assert page.locator('.nav-menu-toggle').inner_text() == '本组菜单'
    page.set_viewport_size({'width':1024,'height':900})
    page.locator('.section-sidebar a').nth(1).focus()
    assert page.locator('#nav-label-hint').is_visible()
    assert page.locator('#nav-label-hint').inner_text() == '风险与指标'
    page.set_viewport_size({'width':390,'height':844})
    page.click('.nav-menu-toggle')
    for _ in range(22):
        page.keyboard.press('Tab')
        assert page.evaluate("document.querySelector('#site-menu').contains(document.activeElement)"), page.evaluate("({active:document.activeElement.outerHTML, targets:Array.from(document.querySelectorAll('#site-menu button,#site-menu a,#site-menu summary')).map(n=>({text:n.textContent,tab:n.tabIndex,rects:n.getClientRects().length,parent:n.closest('details')?.open}))})")
    page.keyboard.press('Escape')
    assert page.locator('.nav-menu-toggle').evaluate('(n)=>n===document.activeElement')


@pytest.mark.browser
def test_revision_mismatch_is_rejected_and_retry_uses_new_snapshot(page,snapshot):
    mount_home(page,snapshot)
    page.route('**/api/map/unified?*',lambda route:route.fulfill(status=409,content_type='application/json',body='{}'))
    page.evaluate('loadHomeMap(homeSnapshot,homeSequence)')
    assert page.locator('#home-map iframe').count() == 0
    assert '版本已更新' in page.locator('#home-map').inner_text()
    assert '42.0' in page.locator('#home-risk').inner_text()
    newer = {**snapshot,'revision':'next-revision'}
    page.route('**/api/report?*',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'data':newer})))
    page.route('**/api/map/unified?*',lambda route:route.fulfill(status=200,headers={'Content-Type':'text/html','X-Data-Revision':'next-revision'},body='<iframe srcdoc="<p>新地图夹具</p>"></iframe>'))
    page.locator('#home-map button').click()
    page.wait_for_function("homeSnapshot?.revision==='next-revision'")
    page.wait_for_selector('#home-map iframe')
    assert 'revision=next-revision' in page.locator('#home-map-link').get_attribute('href')


@pytest.mark.browser
def test_secondary_filters_exports_and_daily_records(page):
    from analyzer.data_loader import get_data_loader
    get_data_loader().append_risk_score('2026-03-31',0,'低风险',run_kind='existing',sources=['fixture'])
    page.goto('http://offline.test/dashboard/indicators?days=30&end_date=2026-03-31')
    page.wait_for_function('dashboardSnapshot!==null')
    assert page.locator('#daily-records tbody tr').count()==30
    assert page.locator('#daily-records').is_hidden()
    page.locator('summary:has-text("逐日观测记录")').click()
    assert page.locator('#daily-records tbody tr').last.locator('td').nth(1).inner_text()=='0.0'
    assert page.locator('#daily-records tbody tr').first.locator('td').nth(1).inner_text()=='—'
    assert page.locator('[data-report-format]').first.is_hidden()
    for path in ['/trend','/graph']:
        page.goto('http://offline.test'+path)
        assert page.locator('#days-select').is_hidden()
        page.locator('.research-filter > summary').first.click()
        assert page.locator('#days-select').is_visible()
    page.goto('http://offline.test/workspace')
    assert page.locator('main .btn-primary').count()==1
    assert page.locator('#workspace-export').is_hidden()


@pytest.mark.browser
@pytest.mark.parametrize('role,allowed',[('reader',False),('analyst',False),('admin',True)])
def test_risk_controls_permissions_and_visible_costs(page,role,allowed):
    auth={'shared_mode':True,'user':{'id':'fixture','username':'fixture','role':role},'csrf_token':'fixture'}
    page.route('**/api/session',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'data':auth})))
    calls=[]
    page.on('request',lambda request:calls.append(request))
    page.goto('http://offline.test/analysis/risk')
    page.wait_for_function("!document.querySelector('#risk-call-status').textContent.includes('正在检查')")
    assert page.locator('#risk-explain-btn').is_enabled()==allowed
    assert page.locator('#explain-model').is_hidden()
    assert not any(r.method=='POST' for r in calls)
    if allowed:
        page.locator('summary:has-text("模型设置")').click()
        page.select_option('#explain-model','deepseek-chat')
        page.locator('summary:has-text("模型设置")').click()
        assert '可能产生API费用' in page.locator('#risk-call-status').inner_text()
        page.route('**/api/risk/explain',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'data':{'date':'2026-10-22','status':'ok','model_version':'deepseek-chat','answer':{'summary':'模型回答夹具'}}})))
        page.click('#risk-explain-btn')
        page.wait_for_function("document.querySelector('#risk-explain-body').textContent.includes('DeepSeek-chat')")
        page.route('**/api/risk/explain',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'data':{'date':'2026-10-22','status':'degraded','model_version':'rule-based','answer':{'summary':'规则降级夹具'}}})))
        page.click('#risk-explain-btn')
        page.wait_for_function("document.querySelector('#risk-explain-body').textContent.includes('规则解读（未调用大模型）')")


@pytest.mark.browser
def test_home_map_keyboard_no_data_and_contrast(page,monkeypatch,snapshot):
    import app as module
    from analyzer.data_loader import get_data_loader
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    snapshot['revision']=get_data_loader().revision()
    monkeypatch.setattr(module,'_build_province_risk_data',lambda:[])
    freeze_home_day(page)
    page.route('**/api/report?*',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'data':snapshot})))
    page.goto('http://offline.test/dashboard')
    frame=page.frame_locator('#home-map iframe')
    target=frame.locator('path[role="button"]').first
    target.wait_for()
    target.focus()
    assert '无有效观测' in frame.locator('.leaflet-tooltip').inner_text()
    target.press('Enter')
    assert not errors, errors
    assert frame.locator('.leaflet-popup').count(), target.evaluate("n=>Object.keys(window).filter(k=>k.startsWith('geo_json_')).map(k=>{const root=window[k],items=[];root.eachLayer?.(layer=>{if(layer.getElement?.()===n)items.push({popup:!!layer.getPopup?.(),rootPopup:!!root.getPopup?.(),events:Object.keys(layer._events||{}),rootEvents:Object.keys(root._events||{})});});return items;})")
    assert '无有效观测' in frame.locator('.leaflet-popup').inner_text()
    assert frame.locator('.leaflet-control-zoom-in').bounding_box()['height']>=44
    assert '记录' in page.locator('#home-map').get_attribute('aria-label')
    assert '无有效观测' in frame.locator('.home-map-legend').inner_text()
    ratios=page.evaluate('''() => {
        function lum(rgb){const v=rgb.match(/[\\d.]+/g).slice(0,3).map(n=>{n=Number(n)/255;return n<=.04045?n/12.92:Math.pow((n+.055)/1.055,2.4);});return .2126*v[0]+.7152*v[1]+.0722*v[2];}
        return ['.home-score-meta','.home-note','.home-warning'].map(s=>{const n=document.querySelector(s);const x=lum(getComputedStyle(n).color);return (1.05)/(x+.05);});
    }''')
    assert min(ratios)>=4.5


@pytest.mark.browser
def test_status_screenshots_preserve_disclaimers(page,snapshot):
    from copy import deepcopy
    from pathlib import Path
    output=Path(__file__).resolve().parents[1]/'logs'/'home-redesign'/'states'
    output.mkdir(parents=True,exist_ok=True)
    scenarios={}
    scenarios['normal']=deepcopy(snapshot)
    scenarios['low-sample']=deepcopy(snapshot)
    scenarios['low-sample']['history'][-1]['news_count']=1
    scenarios['stale']=deepcopy(snapshot)
    scenarios['stale']['history'].pop()
    scenarios['no-data']=deepcopy(snapshot)
    scenarios['no-data']['history']=[];scenarios['no-data']['events']=[]
    scenarios['partial-proxy']=deepcopy(snapshot)
    for name,case in scenarios.items():
        mount_home(page,case)
        page.locator('.home-header').evaluate("n=>{const p=document.createElement('p');p.textContent='自动化界面验证夹具，非实时资料';n.append(p);}")
        page.screenshot(path=str(output/(name+'.png')),full_page=True)
        assert '不是发生概率' in page.locator('main').inner_text()
        if name=='low-sample':assert '样本偏少' in page.locator('#home-risk').inner_text()
        if name=='stale':assert '过期' in page.locator('#home-risk').inner_text()
        if name=='no-data':assert page.locator('.home-value').inner_text()=='—'
        if name=='partial-proxy':assert '输入不完整' in page.locator('#home-risk').inner_text()
    page.route('**/api/map/unified?*',lambda route:route.fulfill(status=503,content_type='application/json',body='{}'))
    page.evaluate('loadHomeMap(homeSnapshot,homeSequence)')
    page.screenshot(path=str(output/'map-failure.png'),full_page=True)
    page.route('**/api/sources/health',lambda route:route.fulfill(status=503,content_type='application/json',body='{}'))
    page.evaluate('loadHomeSources()')
    page.screenshot(path=str(output/'source-failure.png'),full_page=True)
    page.evaluate("() => {window.fetchJSON=()=>new Promise(()=>{});loadHomeSnapshot();}")
    page.screenshot(path=str(output/'loading.png'),full_page=True)
    assert '正在读取' in page.locator('#home-risk').inner_text()


def test_method_context_projects_only_public_saved_versions(monkeypatch):
    from utils.research_context import method_context
    from types import SimpleNamespace
    monkeypatch.setenv('ENABLE_RISK_PANELS','true')
    monkeypatch.setattr('utils.config.load_config',lambda:{'risk_weights':{'conflict_frequency':.4,'private':'FAKE_PRIVATE'},'risk_weights_v3':{'event_severity':.1},'llm':{'api_key':'FAKE_PRIVATE'}})
    rows={'risk_panels':[{'parameters':{'approved_version':'fixture-v1','lambda':{'battle':1.0},'mu':{'finance':2.0},'private':'FAKE_PRIVATE'}}],
          'risk_calibrations':[{'id':'a'*64,'frequency':'monthly','mode':'formal','description':'基准夹具','private':'FAKE_PRIVATE'}]}
    monkeypatch.setattr('storage.risk_store.RiskStore',lambda:SimpleNamespace(ready=lambda:True,load=lambda key:rows[key]))
    result=method_context()
    assert result['parameters'][0]['version']=='fixture-v1'
    assert result['parameters'][0]['lambda']=={'battle':1.0}
    assert result['calibrations'][0]['id']=='a'*64
    assert 'FAKE_PRIVATE' not in json.dumps(result)
    monkeypatch.setenv('ENABLE_RISK_PANELS','false')
    result=method_context()
    assert result['parameters']==[] and result['calibrations']==[]
    assert '未启用' in result['parameters_note']


@pytest.mark.browser
def test_method_parameters_readonly_and_sidebar_navigation_history(page):
    page.goto('http://offline.test/research/methodology')
    assert '只读' in page.locator('#method-parameters').inner_text()
    assert '默认λ／μ' in page.locator('#method-parameters').inner_text()
    assert page.locator('#method-parameters input,#method-parameters select,#method-parameters button').count()==0
    page.locator('.section-sidebar a:has-text("历史背景")').click()
    assert urlsplit(page.url).path=='/research/history'
    assert page.locator('.section-sidebar a.active').get_attribute('aria-label')=='历史背景'
    page.go_back()
    assert page.locator('.section-sidebar a.active').get_attribute('aria-label')=='指标与计算方法'
    page.go_forward()
    assert page.locator('.section-sidebar a.active').get_attribute('aria-label')=='历史背景'


@pytest.mark.browser
def test_event_navigation_locates_exact_source_and_id(page,snapshot):
    mount_home(page,snapshot)
    # 同ID但不同来源仍为不同记录，导航应只高亮选中的来源事件。
    snapshot['events']=[{'event_id':'shared-id','source':'first','date':'2026-10-22','root_code':'19','location':'甲'},
                        {'event_id':'shared-id','source':'second','date':'2026-10-22','root_code':'19','location':'乙'}]
    page.goto('http://offline.test/dashboard/events?days=30&end_date=2026-10-22&event_id=shared-id&event_source=second')
    page.wait_for_function('dashboardSnapshot!==null')
    assert page.locator('.evidence-selected').count()==1
    assert '乙' in page.locator('.evidence-selected').inner_text()
    assert 'event_source=second' in page.url
