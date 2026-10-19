"""新面板真实PostGIS验收：无独立*_test实例必须跳过。"""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.test_postgis_integration import repo
from storage.risk_store import RiskStore
from tests.test_risk_multisource import acled, manifest, synthetic_panel, score_panel
from data.risk_sources import parse_acled

pytestmark=pytest.mark.postgis


def test_risk_migration_and_immutable_parallel_inputs(repo):
    store=RiskStore(repo)
    assert store.ready()
    value=parse_acled([acled()],manifest())
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _:store.save_batch(value),range(4)))==[1]*4
    assert len(store.load('risk_source_batches'))==1
    altered=parse_acled([acled(fatalities=42)],manifest())
    with pytest.raises(ValueError):store.save_batch(altered)
    assert store.load('risk_source_batches')[0]['records'][0]['fatalities']==3
    p=score_panel(synthetic_panel())
    store.save('risk_panels',[p])
    store.save('risk_panels',[p])
    assert len(store.load('risk_panels'))==1
    changed=copy.deepcopy(p);changed['score']=99
    with pytest.raises(ValueError):store.save('risk_panels',[changed])


def test_risk_writes_reject_lost_parent_lease(repo):
    import threading
    from storage.repository import task_scope
    from storage.jobs import LeaseLost
    from types import SimpleNamespace
    def owned(conn,job,lost):
        if lost.is_set():raise LeaseLost('fixture')
    queue=SimpleNamespace(repo=repo,assert_owned=owned)
    lost=threading.Event();lost.set()
    with task_scope(queue,{'id':'fixture'},lost):
        with pytest.raises(LeaseLost):RiskStore(repo).save_batch(parse_acled([acled()],manifest()))
    assert RiskStore(repo).load('risk_source_batches')==[]
