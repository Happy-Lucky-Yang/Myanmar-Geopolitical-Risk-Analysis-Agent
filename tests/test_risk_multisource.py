"""多源面板手算与异常契约。数据均为合成，不触发外网或业务库。"""
import copy
import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from utils.data_contract import fingerprint
from utils.risk_contract import batch, periods, official_url
from data.risk_sources import parse_acled, parse_idmc, parse_viirs, parse_ofac_actions, parse_sdn, ACLEDAdapter, SourceHTTP
from analyzer.risk_panels import raw_panel, fit_calibration, score_panel, metric, DEFAULT_WEIGHTS_V3


def manifest(source='acled', **changes):
    hosts={'acled':'acleddata.com','ofac':'ofac.treasury.gov','idmc':'www.internal-displacement.org','viirs':'ladsweb.modaps.eosdis.nasa.gov'}
    return {'start':'2024-01-01','end':'2027-01-01','complete':True,'region':'MMR',
            'dataset_version':'fixture-1','license':'test only','url':'https://'+hosts[source]+'/data',**changes}


def acled(**changes):
    return {'event_id_cnty':'MMR1','event_date':'2026-01-05','event_type':'Battles','country':'Myanmar',
            'fatalities':3,'latitude':16.8,'longitude':96.2,'geo_precision':1,'time_precision':1,**changes}


def panel(inputs=(), gdelt=(), params=None, mode='formal', freq='monthly', start='2026-01-01', end='2026-02-01', region='MMR'):
    return raw_panel(start,end,freq,region,list(inputs),list(gdelt),params,mode)


def test_periods_do_not_include_partial_month_or_year():
    assert periods('monthly',1,'2026-02-14')==[('2026-01-01','2026-02-01')]
    assert periods('monthly',1,'2024-02-29')==[('2024-02-01','2024-03-01')]
    assert periods('annual',1,'2026-10-16')==[('2025-01-01','2026-01-01')]
    for freq,count,end in [('daily',1,'2026-01-01'),('monthly',True,'2026-01-01'),('monthly',121,'2026-01-01')]:
        with pytest.raises(ValueError):periods(freq,count,end)


@pytest.mark.parametrize('url',['https://evil.test/','http://acleddata.com/','https://u:p@acleddata.com/','https://acleddata.com\\evil','https://acleddata.com/\n'])
def test_official_url_allowlist(url):
    with pytest.raises(ValueError):official_url(url,'acled')


def test_acled_count_severity_mean_and_missing_coefficients():
    data=parse_acled([acled(),acled(event_id_cnty='MMR2',fatalities=7),
                     acled(event_id_cnty='MMR3',event_type='Protests')],manifest())
    p=panel([data])
    assert p['indicators']['conflict_frequency']['raw']==2
    assert 'blocked_parameters' in p['indicators']['event_severity']['reason']
    parameters={'approved_version':'test-reviewed','lambda':{'Battles':2,'Explosions/Remote violence':3,'Violence against civilians':1}}
    p=panel([data],params=parameters)
    import math
    assert p['indicators']['event_severity']['raw']==pytest.approx((2*math.log(4)+2*math.log(8))/2)
    data=parse_acled([acled(fatalities='')],manifest())
    assert panel([data],params=parameters)['indicators']['event_severity']['raw'] is None
    with pytest.raises(ValueError):parse_acled([acled(fatalities='NaN')],manifest())
    with pytest.raises(ValueError):parse_acled([acled(),acled(fatalities=9)],manifest())
    assert panel([parse_acled([],manifest())])['indicators']['conflict_frequency']['raw']==0
    assert panel([parse_acled([],manifest(complete=False))])['indicators']['conflict_frequency']['raw'] is None


def gdelt(target='THA',goldstein=8,mentions=1,id='1',**changes):
    return {'event_id':id,'date':'2026-01-05','source':'gdelt','root_code':'19','actor1_country':'BM',
            'actor2_country':target,'goldstein':goldstein,'num_mentions':mentions,**changes}


def test_country_equal_weight_not_report_pooling_and_proxy():
    p=panel(gdelt=[gdelt(),gdelt('IND',-8,100,'2')],mode='proxy')
    assert p['indicators']['neighbor_relation']['raw']==0
    assert p['indicators']['conflict_frequency']['raw']==2
    assert p['indicators']['conflict_frequency']['quality']=='proxy'
    assert panel(gdelt=[],mode='proxy')['indicators']['conflict_frequency']['raw'] is None
    assert panel(gdelt=[gdelt(goldstein=100)])['indicators']['neighbor_relation']['raw'] is None


def test_ofac_legal_action_not_designated_object_count():
    row={'action_id':'EO-test','effective_date':'2026-01-03','program':'BURMA-EO14014','change':'addition',
         'measure_type':'asset_freeze','url':'https://ofac.treasury.gov/recent-actions/20260103','reviewed':True}
    data=parse_ofac_actions([row,{**row,'objects':['x','y']}],manifest('ofac'))
    assert len(data['records'])==1
    assert panel([data])['indicators']['intl_sanction']['raw'] is None
    p=panel([data],params={'approved_version':'test-reviewed','mu':{'asset_freeze':3}})
    assert p['indicators']['intl_sanction']['raw']==3
    assert '欧盟' in p['indicators']['intl_sanction']['reason']
    data=parse_ofac_actions([{**row,'effective_date':None}],manifest('ofac'))
    assert panel([data],params={'approved_version':'test-reviewed','mu':{'asset_freeze':3}})['indicators']['intl_sanction']['raw'] is None
    assert len(parse_sdn(b'<sdnList><sdnEntry uid="1"><program>BURMA-EO14014</program></sdnEntry></sdnList>'))==1
    for xml in (b'<!DOCTYPE x [<!ENTITY a "x">]><x>&a;</x>', '<x/>'.encode('utf-16')):
        with pytest.raises(ValueError):parse_sdn(xml)


def test_idmc_stock_difference_not_flows_and_no_province_month_copy():
    rows=[{'ISO3':'MMR','Year':y,'Conflict Stock Displacement':s,'Conflict Internal Displacements':99} for y,s in [(2024,200),(2025,120)]]
    data=parse_idmc(rows,manifest('idmc'))
    assert len(data['records'])==4
    p=panel([data],freq='annual',start='2025-01-01',end='2026-01-01')
    assert p['indicators']['displacement']['raw']==-80
    assert panel([data])['indicators']['displacement']['raw'] is None
    assert panel([data],freq='annual',start='2025-01-01',end='2026-01-01',region='MMR.1_1')['indicators']['displacement']['raw'] is None
    with pytest.raises(ValueError):parse_idmc([{'year':2025}],manifest('idmc'))


def test_viirs_yoy_requires_same_product_real_period_and_positive_baseline():
    def obs(year,value):
        return {'source':'viirs','indicator':'nightlight','region':'MMR','frequency':'monthly',
                'period_start':f'{year}-01-01','period_end':f'{year}-02-01','value':value,
                'unit':'nW/cm²/sr','product':'VNP46A3','dataset_version':'fixture-1','quality':'observed','run_kind':'existing'}
    data=parse_viirs([obs(2025,10),obs(2026,5)],manifest('viirs'))
    assert panel([data])['indicators']['nightlight_change']['raw']==-.5
    data=parse_viirs([obs(2025,0),obs(2026,5)],manifest('viirs'))
    assert panel([data])['indicators']['nightlight_change']['raw'] is None
    with pytest.raises(ValueError):parse_viirs([{**obs(2026,5),'quality':'proxy'}],manifest('viirs'))


def synthetic_panel(value=2, mode='formal'):
    p=panel(mode=mode)
    p['indicators']={k:metric(value,'fixture','formula','units','observed',version='1') for k in DEFAULT_WEIGHTS_V3}
    return p


def test_minmax_direction_frozen_reference_and_missing_formal_score():
    lower,higher=synthetic_panel(0),synthetic_panel(4)
    c=fit_calibration([lower,higher],'test reference')
    scored=score_panel(synthetic_panel(2),c)
    assert scored['score']==50
    assert sum(scored['weights_used'].values())==pytest.approx(1)
    assert sum(i['contribution_points'] for i in scored['indicators'].values())==pytest.approx(50)
    p=synthetic_panel(0)
    s=score_panel(p,c)
    assert s['indicators']['nightlight_change']['value']==1
    p['indicators']['event_severity']['raw']=None
    assert score_panel(p,c)['score'] is None
    c0=fit_calibration([lower,copy.deepcopy(lower)],'constant')
    assert score_panel(lower,c0)['score'] is None
    proxy=synthetic_panel(2,'proxy')
    cp=fit_calibration([synthetic_panel(0,'proxy'),synthetic_panel(4,'proxy')],'proxy ref')
    proxy['indicators']['event_severity']['raw']=None
    result=score_panel(proxy,cp)
    assert result['score'] is not None and result['coverage']<1
    with pytest.raises(ValueError):score_panel(proxy,c)


def test_file_store_idempotency_revision_conflict_and_corrupt(tmp_path):
    from storage.risk_store import RiskStore
    store=RiskStore()
    b=parse_acled([acled()],manifest())
    store.save_batch(b)
    rev=store.revision()
    store.save_batch({**b,'acquired_at':'other'})
    assert store.revision()==rev
    with pytest.raises(ValueError):store.save_batch(parse_acled([acled(fatalities=50)],manifest()))
    assert len(store.load('risk_source_batches'))==1
    store.path.write_text('broken',encoding='utf-8')
    with pytest.raises(ValueError):store.save_batch(b)


def test_api_defaults_do_not_compute_or_disclose_inputs(monkeypatch):
    from app import app
    client=app.test_client()
    assert client.get('/api/risk_panels').status_code==503
    monkeypatch.setenv('ENABLE_RISK_PANELS','true')
    result=client.get('/api/risk_panels?count=1&end_date=2026-01-31').get_json()
    assert result['success'] and result['data']['panels'][0]['status']=='not_computed'
    assert 'records' not in client.get('/api/risk_sources/status').get_data(as_text=True)
    assert client.get('/api/risk_panels?count=121').status_code==400
    assert client.post('/api/risk_panels/build',json={}).status_code==503


def test_acled_oauth_refresh_once_and_complete_page(monkeypatch):
    monkeypatch.setenv('ENABLE_ACLED','true')
    monkeypatch.setenv('ACLED_USERNAME','fixture')
    monkeypatch.setenv('ACLED_PASSWORD','test-only')
    calls=[]
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        if method=='POST':return json.dumps({'access_token':'test','refresh_token':'test','expires_in':60}).encode()
        if sum(c[0]=='GET' for c in calls)==1:raise PermissionError('401')
        return json.dumps({'success':True,'data':[acled()]}).encode()
    adapter=ACLEDAdapter(SimpleNamespace(request=request))
    value=adapter.fetch(manifest())
    assert value['manifest']['complete'] and len(value['records'])==1
    assert sum(c[0]=='POST' for c in calls)==2
    assert calls[-1][2]['params']['event_date_where']=='BETWEEN'


def test_source_http_rejects_redirect_and_retries_rate_limit():
    response=Mock(status_code=302,headers={'Location':'https://evil.test'})
    session=Mock()
    session.request.return_value=response
    with pytest.raises(ValueError):SourceHTTP('acled',session,lambda _:None).request('GET','https://acleddata.com/data')
    assert session.request.call_count==1
    response.status_code=429
    session.reset_mock()
    with pytest.raises(RuntimeError):SourceHTTP('acled',session,lambda _:None).request('GET','https://acleddata.com/data')
    assert session.request.call_count==3


@pytest.mark.parametrize('lost_at',[0,1,None,'web'])
def test_reserve_uses_existing_lease_transaction(lost_at):
    from storage.repository import PostgresRepository, task_scope
    from storage.workspace import WorkspaceStore
    from storage.jobs import LeaseLost
    from storage import schema
    repo=PostgresRepository.__new__(PostgresRepository)
    repo.schema=schema
    conn=Mock()
    repo.engine=Mock()
    conn.engine=repo.engine
    @contextmanager
    def begin():yield conn
    repo.engine.begin=begin
    old={'id':'existing','payload':{'question':'q','evidence':{}},'conversation_id':None}
    conn.execute.return_value.mappings.return_value.first.return_value=old
    seen=[]
    def owned(*args):
        seen.append(True)
        if lost_at is not None and len(seen)==lost_at+1:raise LeaseLost('fixture')
    queue=SimpleNamespace(repo=repo,assert_owned=owned)
    with (task_scope(queue,{'id':'fixture'},None) if lost_at!='web' else nullcontext()):
        if lost_at not in (None,'web'):
            with pytest.raises(LeaseLost):WorkspaceStore(repo).reserve(None,None,'q',{},'key','auto')
        else:
            assert WorkspaceStore(repo).reserve(None,None,'q',{},'key','auto')=='existing'
    assert len(seen)==(0 if lost_at=='web' else lost_at+1 if lost_at is not None else 2)


def test_event_candidates_are_not_independent_reports():
    from analyzer.event_links import candidates, review, annotate
    a={'source':'acled','event_id':'a','date':'2026-01-05','region':'r','time_precision':1,'geo_precision':1,
       'event_type':'Battles','lat':16.8,'lon':96.2,'canonical_actors':['army','group']}
    b={**a,'source':'gdelt','event_id':'b','date':'20260105','root_code':'19'}
    rows=candidates([a],[b])
    assert len(rows)==1 and rows[0]['status']=='candidate'
    assert not annotate([b],rows)[0]['evidence_verified']
    origins=[{'source':'paper','url':'https://example.org/a','independence_verified':True,'independence_group':'same'},
             {'source':'reprint','url':'https://example.org/b','independence_verified':True,'independence_group':'same'}]
    link=review(rows[0],'confirmed','admin',{'rationale':'same event','origins':origins})
    assert not annotate([b],[link])[0]['evidence_verified']
    origins[1]['independence_group']='other'
    link=review(rows[0],'confirmed','admin',{'rationale':'independent evidence','origins':origins})
    assert annotate([{**b,'date':'2026-01-05'}],[link])[0]['evidence_verified']
    assert not annotate([{**b,'lon':96.3}],[link])[0]['evidence_verified']
    assert not candidates([a],[{**b,'geo_precision':2}])
    assert len(annotate([b],[link]))==1


def test_source_import_cli_is_dry_run_and_confirmed(tmp_path,monkeypatch):
    from scripts.manage_risk_data import run
    from storage.risk_store import RiskStore
    from utils.config import get_data_paths
    path=tmp_path/'source.json'
    path.write_text(json.dumps({'source':'acled','manifest':manifest(),'records':[acled()]}),encoding='utf-8')
    args=SimpleNamespace(action='import',input=str(path),manifest=None,source=None,sheet=None,apply=False,confirm=None)
    assert run(args)['status']=='dry_run'
    assert not RiskStore().path.exists()
    args.apply=True
    with pytest.raises(ValueError):run(args)
    args.confirm=get_data_paths()['root']
    monkeypatch.setenv('ENABLE_RISK_PANELS','true')
    assert run(args)['status']=='saved'
    assert len(RiskStore().load('risk_source_batches'))==1


def test_wb_latest_year_order_is_not_column_order(monkeypatch):
    import pandas as pd
    import wbgapi as wb
    from data.nightlight_crawler import NightlightCrawler
    def frame(code,country,**kwargs):
        assert kwargs.get('mrv')==5
        return pd.DataFrame({'YR2025':[80],'YR2020':[40]},index=['MMR'])
    monkeypatch.setattr(wb.data,'DataFrame',frame)
    crawler=NightlightCrawler()
    r=crawler._fetch_worldbank_nightlight()
    assert r['data_year']==2025 and r['raw_values']['electricity_access']==80
    assert r['quality']=='proxy' and not r['monthly_series']


def test_wb_missing_does_not_return_fake_point(monkeypatch):
    from data.nightlight_crawler import NightlightCrawler
    crawler=NightlightCrawler()
    monkeypatch.setattr(crawler,'_fetch_worldbank_nightlight',lambda:None)
    assert crawler.get_nightlight_change() is None
    assert crawler.force_refresh()['quality']=='missing'
    assert crawler.get_monthly_series()==[]
