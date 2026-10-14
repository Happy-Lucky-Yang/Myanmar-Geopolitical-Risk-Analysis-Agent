"""九指标派生层（analyzer.geo_indicators）离线契约测试。

覆盖组长《九项指标的测度与计算方法》《编码规则》的核心口径：
Goldstein 按 NumMentions 加权净值、方向统一、中美博弈/国际制裁计数强度、
稀疏样本置 missing、FIPS 与 ISO3 双编码归一、九项全局权重之和为 1。
"""
from analyzer.geo_indicators import (DEFAULT_WEIGHTS_V3, canon_country, china_us_rivalry,
                                     compute_geo_indicators, conflict_and_severity,
                                     intl_sanction, to_scorer_input, weighted_goldstein_net,
                                     _relation_to_risk)


def ev(**kw):
    base = {"source": "gdelt", "run_kind": "live", "event_id": "e", "date": "20260101",
            "root_code": "19", "quad_class": "4", "goldstein": -10.0, "num_mentions": 1,
            "actor1_country": "BM", "actor2_country": "CH"}
    base.update(kw)
    return base


def test_canon_country_fips_and_iso3():
    assert canon_country("BM") == "MMR" and canon_country("MMR") == "MMR"
    assert canon_country("CH") == "CHN" and canon_country("CHN") == "CHN"
    assert canon_country("GM") == "DEU"          # FIPS 德国
    assert canon_country("EEC") == "EUU"         # 欧盟集体行为体
    assert canon_country("") is None and canon_country("ZZ") is None and canon_country(None) is None


def test_weighted_goldstein_net_by_mentions():
    # R = (-10×1 + 6×3)/(1+3) = 2.0
    events = [ev(actor1_country="BM", actor2_country="CH", goldstein=-10.0, num_mentions=1),
              ev(actor1_country="CH", actor2_country="BM", goldstein=6.0, num_mentions=3)]
    net, n = weighted_goldstein_net(events, {"CHN"})
    assert round(net, 4) == 2.0 and n == 2
    # 无关国家对不计入
    assert weighted_goldstein_net([ev(actor1_country="TH", actor2_country="LA")], {"CHN"}) == (None, 0)
    # goldstein 缺失的事件跳过
    assert weighted_goldstein_net([ev(goldstein=None)], {"CHN"}) == (None, 0)


def test_relation_direction_unified():
    assert _relation_to_risk(10.0) == 0.0    # 最友好 → 风险 0
    assert _relation_to_risk(-10.0) == 1.0   # 最敌对 → 风险 1
    assert _relation_to_risk(2.0) == 0.4
    assert _relation_to_risk(None) is None


def test_china_us_rivalry_only_negative_goldstein():
    events = [ev(actor1_country="CH", actor2_country="US", goldstein=-8.0, num_mentions=1),
              ev(actor1_country="US", actor2_country="CH", goldstein=5.0, num_mentions=9),  # 合作型不计
              ev(actor1_country="BM", actor2_country="US", goldstein=-9.0)]                 # 非中美对不计
    z, bg, n = china_us_rivalry(events)
    assert n == 1 and round(bg, 4) == 8.0     # |−8|·(1+ln1)=8
    assert round(z, 4) == round(min(1.0, 8.0 / 50.0), 4)
    assert china_us_rivalry([ev(actor1_country="BM", actor2_country="CH")]) == (None, 0.0, 0)


def test_intl_sanction_cameo17_great_power():
    events = [ev(root_code="17", actor1_country="US", actor2_country="BM"),   # 计
              ev(root_code="17", actor1_country="BM", actor2_country="BM"),    # 无大国，不计
              ev(root_code="19", actor1_country="US", actor2_country="BM")]    # 非 17，不计
    z, s, n = intl_sanction(events)
    assert s == 1 and n == 1 and round(z, 4) == 0.1


def test_conflict_frequency_and_severity():
    events = [ev(root_code="19", quad_class="4"), ev(root_code="18", quad_class="4"),
              ev(root_code="06", quad_class="1"), ev(root_code="99", quad_class="")]
    cf, sev, n_conflict, n_classified = conflict_and_severity(events)
    assert n_classified == 3 and n_conflict == 2       # 99 未分类
    assert round(cf, 4) == round(2 / 3, 4)
    assert round(sev, 4) == round((0.88 + 0.78) / 2, 4)  # 根码 19/18 烈度权重均值


def test_compute_geo_indicators_sparsity_and_external():
    events = [ev(root_code="19", actor1_country="BM", actor2_country="CH", goldstein=-10.0, num_mentions=2)]
    external = {"nightlight_change": {"value": -0.3, "quality": "observed"}}  # 夜光下降 → 风险升
    result = compute_geo_indicators(events, external)
    ind = result["indicators"]
    assert result["algorithm_version"] == "geo-indicators-v1"
    # 有中缅样本 → observed/derived；无周边/西方/中美/制裁样本 → missing
    assert ind["china_myanmar_relation"]["quality"] in {"observed", "derived"}
    assert ind["china_myanmar_relation"]["value"] == 1.0     # R=-10 → (10-(-10))/20=1
    for key in ("neighbor_relation", "western_relation", "china_us_rivalry", "intl_sanction"):
        assert ind[key]["quality"] == "missing" and ind[key]["value"] is None
    # 夜光显式 observed 且负向翻转：-(-0.3)=0.3
    assert ind["nightlight_change"]["quality"] == "observed" and ind["nightlight_change"]["value"] == 0.3
    # 流离失所未提供 → missing
    assert ind["displacement"]["quality"] == "missing"

    values, quality = to_scorer_input(result)
    assert "china_myanmar_relation" in values and "nightlight_change" in values
    assert "neighbor_relation" not in values           # missing 不进评分，交由动态归一
    assert quality["intl_sanction"] == "missing"


def test_nine_weights_sum_to_one():
    """组长权重分配表不变量：九项全局权重之和为 1（容差 1e-6）。"""
    assert abs(sum(DEFAULT_WEIGHTS_V3.values()) - 1.0) < 1e-6
    assert len(DEFAULT_WEIGHTS_V3) == 9


def test_geo_risk_scorer_end_to_end():
    """risk-v3：九指标加权合成、稀疏缺测动态归一、贡献可加性（组长三层体系）。"""
    from analyzer.risk_scorer import get_geo_risk_scorer
    scorer = get_geo_risk_scorer()
    # 九项全局权重之和为 1
    assert len(scorer._weights) == 9 and abs(sum(scorer._weights.values()) - 1.0) < 1e-6

    events = [
        ev(event_id="1", root_code="19", quad_class="4", goldstein=-9.5, num_mentions=12,
           actor1_country="BM", actor2_country="BM"),                 # 缅境内武装冲突
        ev(event_id="2", root_code="17", quad_class="3", goldstein=-7.0, num_mentions=5,
           actor1_country="US", actor2_country="BM"),                 # 美国对缅制裁（CAMEO 17）
        ev(event_id="3", root_code="06", quad_class="1", goldstein=6.0, num_mentions=3,
           actor1_country="CH", actor2_country="BM"),                 # 中缅实质合作
    ]
    result = scorer.compute_event_risk(
        events, {"nightlight_change": {"value": -0.2, "quality": "observed"}})

    assert result["algorithm_version"] == "risk-v3"
    assert result["event_count"] == 3 and result["classified_count"] == 3
    ind = result["geo_indicators"]

    # 缺测指标（周边/中美博弈/流离失所）不得以 0 冒充“关系良好”，贡献为 None 且拉低覆盖率
    for key in ("neighbor_relation", "china_us_rivalry", "displacement"):
        assert ind[key]["quality"] == "missing"
        assert result["indicator_scores"][key]["contribution"] is None
    assert result["data_status"] == "partial" and result["weight_rebalanced"] is True
    assert 0 < result["indicator_coverage"] < 1

    # 有效权重按覆盖率动态归一：覆盖率 == 非缺测指标的原始权重之和
    covered = sum(scorer._weights[k] for k in scorer._weights
                  if ind[k]["quality"] in {"observed", "derived"})
    assert abs(result["indicator_coverage"] - covered) < 1e-4

    # 贡献可加性：Σ贡献 × 100 == 风险分
    total = sum((d["contribution"] or 0) for d in result["indicator_scores"].values())
    assert abs(round(total * 100, 2) - result["risk_score"]) < 0.01

    # 方向统一校验：中缅合作(R=6)→关系风险低(0.2)；西方制裁(R=-7)→关系风险高(0.85)
    assert ind["china_myanmar_relation"]["value"] == 0.2
    assert ind["western_relation"]["value"] == 0.85
    # 夜光下降(-0.2)为负向指标，翻转为风险 0.2
    assert ind["nightlight_change"]["value"] == 0.2
