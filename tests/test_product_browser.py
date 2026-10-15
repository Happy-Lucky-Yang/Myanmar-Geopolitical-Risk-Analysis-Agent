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
