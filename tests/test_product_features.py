"""新产品能力的离线契约；测试题录和网页均为合成夹具。"""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from analyzer.knowledge_base import KnowledgeBase
from analyzer.literature_catalog import browse, public_record, safe_link, overview
from analyzer.risk_assistant import auto_eligibility, validated_answer, generate_answer, SECTIONS
from data.un_news_crawler import parse_article, article_links, allowed_url, UNNewsCrawler


@pytest.fixture
def catalog():
    rows = [{'id':str(i), 'title':'缅甸研究'+str(i), 'authors':['研究者'], 'year':2020+i%3,
             'topics':['地缘位势'], 'modules':['geo_potential'], 'indicators':['conflict_frequency'],
             'abstract':'研究摘要', 'is_core':i%2==0, 'relevance_rank':2, 'journal':'测试期刊',
             'notes':'内部备注', 'kb_file':'C:\\private\\paper.pdf'} for i in range(45)]
    return KnowledgeBase(index={'records':rows, 'status':'ok', 'facets':{'topic':{'地缘位势':45}}})


def test_catalog_pagination_safe_fields_and_filters(catalog):
    pages = [browse(catalog, offset=i)['results'] for i in (0,20,40)]
    assert [len(p) for p in pages] == [20,20,5]
    assert len({r['id'] for p in pages for r in p}) == 45
    assert all('notes' not in r and 'kb_file' not in r for p in pages for r in p)
    assert browse(catalog, core_only=True)['total'] == 23
    assert browse(catalog, module='unknown')['total'] == 0
    assert browse(catalog, query='研究者')['total'] == 45
    assert overview(catalog)['fulltext_count'] == 0


@pytest.mark.parametrize('offset,limit',[(-1,20),(0,101),(True,20),(0,0)])
def test_catalog_pagination_rejects_invalid(catalog,offset,limit):
    with pytest.raises(ValueError): browse(catalog,offset=offset,limit=limit)


@pytest.mark.parametrize('link',['javascript:alert(1)','file:///private/a.pdf','//evil.test','https://u:p@x.test','https://x.test/\n','https://x.test\\evil'])
def test_public_links_reject_unsafe(link):
    assert safe_link(link) is None
    assert not public_record({'open_access':{'reviewed':True,'license':'CC BY','url':link}})['fulltext_available']


def test_doi_is_not_download_and_oa_requires_review():
    record={'doi':'10.1234/example','open_access':{'url':'https://example.org/paper','license':'CC BY'}}
    assert not public_record(record)['fulltext_available']
    record['open_access']['reviewed']=True
    assert public_record(record)['fulltext_available']
    assert len(public_record(record)['links'])==2


def test_product_routes_and_default_switches(monkeypatch,catalog):
    from app import app
    monkeypatch.setenv('ENABLE_LITERATURE','false')
    client=app.test_client()
    for path in ('/guide','/literature','/workspace'):
        assert client.get(path).status_code==200
    assert client.get('/api/literature').status_code==503
    assert client.get('/api/workspace/conversations').status_code==503
    monkeypatch.setenv('ENABLE_LITERATURE','true')
    monkeypatch.setattr('analyzer.knowledge_base.get_knowledge_base',lambda:catalog)
    result=client.get('/api/literature?offset=40').get_json()
    assert result['success'] and len(result['data']['results'])==5
    assert client.get('/api/literature?limit=101').status_code==400
    assert client.get('/api/literature/missing').status_code==404
    assert 'kb_file' not in client.get('/api/literature/1').get_json()['data']


URL='https://news.un.org/zh/story/2023/01/1111111'
HTML='<h1>缅甸人道援助</h1><time datetime="2023-01-02"></time><div class="field--name-body">'+('联合国呼吁保护缅甸平民，提供人道主义援助。'*5)+'</div>'


def test_un_news_preserves_publication_and_excludes_topic():
    row=parse_article(HTML,URL)
    assert row['pub_time']=='2023-01-02' and row['date_quality']=='observed'
    assert row['source']=='UN News 中文'
    with pytest.raises(ValueError): parse_article(HTML,'https://news.un.org/zh/focus/miandian')
    with pytest.raises(ValueError): parse_article(HTML.replace('2023-01-02','invalid'),URL)
    assert article_links(f'<a href="{URL}">a</a><a href="{URL}">b</a><a href="https://evil.test/zh/story/2023/01/111">bad</a>')==[URL]
    assert not allowed_url('https://news.un.org.evil.test/')


def test_un_news_verified_dom_and_distinct_publication_dates():
    html = ('<h1>缅甸新闻</h1><meta property="article:published_time" content="2022-12-22T09:54:57-05:00">'
            '<div class="views-field-field-news-date"><time datetime="2022-12-21T12:00:00Z"></time></div>'
            '<div id="block-views-block-content-fields-block-body"><div class="field--name-field-text-column">'
            + '关于缅甸人道局势的合成正文。'*6 + '</div></div>')
    row=parse_article(html,URL)
    assert row['pub_time']=='2022-12-21'
    assert row['publication_date_conflict'] and row['publication_metadata'].startswith('2022-12-22')


def test_un_news_dedup_limit_and_missing_date(monkeypatch):
    crawler=UNNewsCrawler(sleep=lambda _:None)
    urls=[URL+str(i) for i in range(25)]
    def get(url,robots=False):
        if robots:return 'User-agent: *\nAllow: /'
        if '/tags/' in url or '/focus/' in url:return ''.join(f'<a href="{u}">文</a>' for u in urls)
        return HTML.replace('2023-01-02','invalid') if url==urls[24] else HTML
    monkeypatch.setattr(crawler,'_get',get)
    rows=crawler.crawl(known_urls=[urls[0]],limit=30)
    assert len(rows)==19 and crawler.review_required==[urls[24]]
    assert all(r['url']!=urls[0] for r in rows)
    # 上限内优先取文章号最大（最新）的报道，最旧的留待下轮
    assert urls[1] not in {r['url'] for r in rows} and urls[23] in {r['url'] for r in rows}


def test_un_news_redirect_block_and_rate_limit():
    class Response:
        headers={'Location':'https://evil.test/'}
        status_code=302
        def __enter__(self):return self
        def __exit__(self,*args):pass
    calls=[]
    session=SimpleNamespace(get=lambda *a,**kw: calls.append(a[0]) or Response())
    crawler=UNNewsCrawler(session=session,sleep=lambda _:None)
    with pytest.raises(ValueError):crawler._get(URL,robots=True)
    assert calls==[URL]
    Response.status_code=429
    calls.clear()
    with pytest.raises(RuntimeError):crawler._get(URL,robots=True)
    assert len(calls)==3


def history():
    return [{'date':(date(2026,1,14)-timedelta(days=i)).isoformat(), 'risk_score':60 if i<7 else 20,
             'algorithm_version':'risk-v2','details':{'indicator_coverage':.8,'weights_used':{'a':1}}} for i in range(14)]


def test_auto_thresholds_reject_stale_sparse_or_mixed():
    rows=history()
    assert auto_eligibility(rows,date(2026,1,14))[0]
    assert not auto_eligibility(rows,date(2026,1,18))[0]
    assert not auto_eligibility(rows[:5],date(2026,1,14))[0]
    rows[0]['algorithm_version']='risk-v3'
    assert not auto_eligibility(rows,date(2026,1,14))[0]
    rows[0]['algorithm_version']='risk-v2';rows[0]['details']['weights_used']={'b':1}
    assert not auto_eligibility(rows,date(2026,1,14))[0]


def evidence():
    return {'records':[{'id':'R1','title':'风险记录','text':'分数20'}, {'id':'N1','title':'新闻','text':'背景材料'}], 'revision':'999'}


def answer(text='分数20',refs=None):
    result={k:[] for k in SECTIONS}
    result['observed_changes']=[{'text':text,'evidence_ids':['R1'] if refs is None else refs}]
    return result


@pytest.mark.parametrize('text,refs',[('分数999',['R1']),('分数20',['N1']),('冲突导致上升',['N1']),('看https://evil.test',['R1']),('分数20',['missing']),('分数20',[])])
def test_assistant_rejects_unbacked_claims(text,refs):
    with pytest.raises(ValueError):validated_answer(answer(text,refs),evidence())


def test_assistant_bound_context_repair_and_timeout():
    calls=[]
    def generate(system,body):
        calls.append(json.loads(body))
        return answer('虚构999') if len(calls)==1 else answer()
    llm=SimpleNamespace(generate_evidence_json=generate)
    context=[{'role':'user','content':'背景'*600} for _ in range(30)]
    result=generate_answer('解释',evidence(),context,llm=llm)
    assert result['status']=='ok' and result['generation_attempts']==2 and result['context_truncated']
    assert len(calls[0]['history'])<=12
    assert len(json.dumps(calls[0],ensure_ascii=False))<=12000
    def timeout(*args):raise TimeoutError()
    assert generate_answer('解释',evidence(),llm=SimpleNamespace(generate_evidence_json=timeout))['status']=='failed'
    assert generate_answer('解释',{'records':[]},llm=llm)['status']=='insufficient'


def test_evidence_filters_demo_and_invalid_news(monkeypatch):
    from analyzer.risk_assistant import build_evidence
    rows=[{'title':'正式新闻','content':'新闻内容','run_kind':'live','pub_time':'2026-01-02','url':'https://example.org/a'},
          {'title':'演示新闻','run_kind':'demo','pub_time':'2026-01-02'},
          {'title':'无日期','run_kind':'live'}]
    rows.append({**rows[0], 'url':'https://example.org/reprint'})
    loader=SimpleNamespace(revision=lambda:'same',load_risk_history=lambda **kw:[],load_raw_news=lambda:rows)
    monkeypatch.setattr('analyzer.data_loader.get_data_loader',lambda:loader)
    monkeypatch.setenv('ENABLE_LITERATURE','false')
    result=build_evidence('解释',30,'2026-01-03')
    assert result['risk_status']=='missing'
    assert [r['title'] for r in result['records']]==['正式新闻']
