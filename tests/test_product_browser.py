"""离线真实浏览器验收；私人工作区响应为夹具，不代表数据库集成通过。"""
import json
import pytest
from tests.test_browser_contracts import page
from tests.test_product_features import catalog

pytestmark=pytest.mark.browser


def test_literature_pages_links_empty_and_mobile(page,catalog,monkeypatch):
    monkeypatch.setenv('ENABLE_LITERATURE','true')
    monkeypatch.setattr('analyzer.knowledge_base.get_knowledge_base',lambda:catalog)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://offline.test/literature')
    page.wait_for_function("document.querySelectorAll('#catalog-results article').length===20")
    page.click('#catalog-next')
    page.wait_for_function("document.querySelector('#catalog-page').textContent.includes('21')")
    page.click('#catalog-next')
    page.wait_for_function("document.querySelectorAll('#catalog-results article').length===5")
    page.fill('#catalog-q','不存在的文章xyz')
    page.click('#catalog-form button')
    page.wait_for_function("document.querySelector('#catalog-results').textContent.includes('没有匹配')")
    page.goto('http://offline.test/literature?record=1')
    page.wait_for_function("document.querySelectorAll('#catalog-results article').length===1")
    page.click('#catalog-results summary')
    assert '暂无已审核开放全文' in page.locator('#catalog-results').inner_text()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
    assert not errors


def test_product_disabled_states_and_help(page):
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://offline.test/literature')
    page.wait_for_function("document.querySelector('#catalog-summary').textContent.includes('未启用')")
    page.goto('http://offline.test/workspace')
    page.wait_for_function("document.querySelector('#workspace-status').textContent.includes('共享模式')")
    assert page.locator('#workspace-new').is_disabled()
    page.goto('http://offline.test/guide')
    assert 'MMR' in page.locator('main').inner_text()
    assert not errors


def test_literature_out_of_order_and_error(page,catalog,monkeypatch):
    monkeypatch.setenv('ENABLE_LITERATURE','true')
    monkeypatch.setattr('analyzer.knowledge_base.get_knowledge_base',lambda:catalog)
    page.goto('http://offline.test/literature')
    page.wait_for_function("document.querySelectorAll('#catalog-results article').length===20")
    page.evaluate("""() => {window.pending=[];window.fetchJSON=()=>new Promise(r=>pending.push(r));
        const form=document.querySelector('#catalog-form');
        form.dispatchEvent(new Event('submit',{cancelable:true}));
        form.dispatchEvent(new Event('submit',{cancelable:true}));
        const result=title=>({success:true,data:{results:[{title}],total:1,overview:{record_count:1,core_count:0,fulltext_count:0}}});
        pending[1](result('较新响应'));pending[0](result('过期响应'));}""")
    page.wait_for_function("document.querySelector('#catalog-results').textContent.includes('较新响应')")
    assert '过期响应' not in page.locator('#catalog-results').inner_text()
    page.evaluate("""() => {window.fetchJSON=async()=>{throw new Error('模拟失败')};document.querySelector('#catalog-form').dispatchEvent(new Event('submit',{cancelable:true}));}""")
    page.wait_for_function("document.querySelector('#catalog-summary').textContent.includes('模拟失败')")
    assert page.locator('#catalog-next').is_disabled()


def test_grouped_navigation_visual_hierarchy_and_mobile_overflow(page):
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://offline.test/dashboard')
    page.wait_for_function('homeSnapshot !== null')
    assert page.locator('.nav-links > li').count()==4
    assert page.locator('.nav-links').inner_text().splitlines()==['态势总览','时空分析','分析工作台','研究资料']
    assert page.locator('.sub-nav').count()==0
    assert page.locator('#research-details').count()==0
    assert page.locator('main .home-card').count()==4
    assert page.evaluate("getComputedStyle(document.body).backgroundColor !== 'rgb(15, 17, 23)'")
    page.goto('http://offline.test/graph?days=30&end_date=2026-03-31')
    page.wait_for_function("document.querySelector('#end_date-filter').value === '2026-03-31'")
    assert page.locator('.section-sidebar a').count()==5
    assert page.locator('.section-sidebar a.active').get_attribute('aria-label')=='关系图谱'
    page.set_viewport_size({'width':390,'height':844})
    for path in ('/dashboard','/graph?days=30&end_date=2026-03-31','/literature'):
        page.goto('http://offline.test'+path)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
    assert not errors


def test_workspace_fixture_context_feedback_logout(page):
    state={'user':{'id':'alice','username':'Alice','role':'analyst'},'feedback':None,'deleted':False}
    def fulfill(route,data):route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':True,'data':data}))
    page.route('**/api/session',lambda route:fulfill(route,{'shared_mode':True,'user':state['user'],'csrf_token':'fixture'}))
    def serve(route):
        path=route.request.url.split('/api/workspace')[1]
        method=route.request.method
        if '/feedback' in path:
            state['feedback']=route.request.post_data_json if method=='PUT' else None
            return fulfill(route,{})
        if path=='/preferences':return fulfill(route,{'region':'MMR','scenario':'research','verbosity':'brief','suggestions':False})
        if path.startswith('/conversations?'):return fulfill(route,[] if state['deleted'] else [{'id':'a','title':'我的测试会话'}])
        if path=='/conversations/a':
            if method=='DELETE':state['deleted']=True;return fulfill(route,{})
            return fulfill(route,{'id':'a','title':'我的测试会话','requests':[],'feedback':[],
                'messages':[{'id':'m','role':'assistant','content':'仅Alice可见','payload':{'status':'ok','evidence':{'records':[]}}}]})
        return fulfill(route,{})
    page.route('**/api/workspace/**',serve)
    page.goto('http://offline.test/workspace')
    page.wait_for_selector('#workspace-history button')
    page.click('#workspace-history button')
    page.wait_for_function("document.querySelector('#workspace-messages').textContent.includes('仅Alice可见')")
    page.locator('#workspace-messages button:has-text("保存评价")').click()
    page.wait_for_function("document.querySelector('#workspace-status').textContent.includes('评价已保存')")
    assert state['feedback']['rating']==5
    page.locator('#workspace-messages button:has-text("撤回评价")').click()
    page.wait_for_function("document.querySelector('#workspace-status').textContent.includes('已撤回')")
    assert state['feedback'] is None
    state['user']={'id':'bob','username':'Bob','role':'analyst'}
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.querySelector('#workspace-messages').textContent==='' ")
    assert page.locator('#workspace-history').inner_text()==''
    assert page.locator('#workspace-user').inner_text()==''
    assert page.locator('#workspace-export').is_hidden()
    assert page.locator('#workspace-export').get_attribute('href') is None


def test_multisource_fold_read_only_and_responsive(page,monkeypatch,tmp_path):
    from storage.risk_store import RiskStore
    from tests.test_risk_multisource import synthetic_panel, score_panel
    monkeypatch.setenv('ENABLE_RISK_PANELS','true')
    RiskStore().save('risk_panels',[score_panel(synthetic_panel())])
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://offline.test/dashboard/indicators?end_date=2026-01-31')
    assert not page.locator('#risk-panel-section').get_attribute('open')
    page.click('#risk-panel-section > summary')
    page.wait_for_function("document.querySelector('#risk-panel-body').textContent.includes('正式分未合成')")
    assert page.locator('#risk-panel-body tbody tr').count()==12
    for width in (1440,1024,390):
        page.set_viewport_size({'width':width,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
    page.select_option('#panel-frequency','annual')
    page.wait_for_function("document.querySelectorAll('#risk-panel-body tbody tr').length===5")
    assert not errors
