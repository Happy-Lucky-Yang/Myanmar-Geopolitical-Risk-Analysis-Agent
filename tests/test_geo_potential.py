"""地缘位势模块（analyzer.geo_potential）离线契约测试。

覆盖组长《地缘环境智能计算四模块实施说明》模块一：
战略中心权重三档（3:2:1）归一且和为 1、位势非负且风险为零时位势为零、
等风险下靠近一档中心的省邦位势更高、Spearman 秩相关工具与距离口径敏感性框架。
用合成省份坐标隔离 GADM 边界依赖，保证确定性与离线可跑。
"""
import pytest

from analyzer.geo_potential import (CENTER_WEIGHT_TOLERANCE, STRATEGIC_CENTERS,
                                    GeoPotentialAnalyzer, _average_ranks,
                                    spearman_rho, validate_center_weights)


def test_center_weights_sum_to_one():
    """验收断言(1)：五中心权重之和为 1（容差 1e-6），且分档取值为 3:2:1。"""
    total = sum(c["weight"] for c in STRATEGIC_CENTERS.values())
    assert abs(total - 1.0) <= CENTER_WEIGHT_TOLERANCE
    assert validate_center_weights() == total
    tiers = {}
    for c in STRATEGIC_CENTERS.values():
        tiers.setdefault(c["tier"], set()).add(round(c["weight"], 6))
    assert tiers[1] == {0.30} and tiers[2] == {0.20} and tiers[3] == {0.10}


def test_validate_center_weights_rejects_bad():
    """权重和不为 1 时须中止（抛 AssertionError），不得静默输出。"""
    with pytest.raises(AssertionError):
        validate_center_weights({"A": {"weight": 0.5}, "B": {"weight": 0.4}})


def test_average_ranks_handles_ties():
    assert _average_ranks([10, 20, 30]) == [1.0, 2.0, 3.0]
    assert _average_ranks([5, 5, 9]) == [1.5, 1.5, 3.0]      # 并列取平均秩


def test_spearman_rho_perfect_inverse_and_degenerate():
    assert round(spearman_rho([1, 2, 3, 4], [10, 20, 30, 40]), 6) == 1.0
    assert round(spearman_rho([1, 2, 3, 4], [40, 30, 20, 10]), 6) == -1.0
    assert spearman_rho([1, 1, 1], [1, 2, 3]) is None         # 零方差
    assert spearman_rho([1, 2], [1]) is None                  # 长度不等


def _analyzer_with(provinces, risk):
    analyzer = GeoPotentialAnalyzer()
    analyzer._get_provinces = lambda: provinces
    analyzer._build_province_risk = lambda: risk
    return analyzer


def test_potential_nonnegative_and_zero_when_risk_zero():
    """验收断言(2)：位势非负，且风险指数为零时位势为零。"""
    analyzer = _analyzer_with({"甲": (20.0, 96.0), "乙": (17.0, 97.0)},
                              {"甲": 60.0, "乙": 0.0})
    result = analyzer.compute_potential()
    by = {p["province"]: p for p in result["provinces"]}
    assert result["algorithm_version"] == "potential-v3"
    assert by["甲"]["potential"] > 0
    assert by["乙"]["potential"] == 0.0                        # 风险 0 → 位势 0
    assert all(p["potential"] >= 0 for p in result["provinces"])


def test_potential_border_beats_inland_at_equal_risk():
    """验收断言(3)：等风险下，靠近一档中心（瑞丽/皎漂港）的省邦位势高于远内陆省邦。"""
    near = _analyzer_with({"近边境": (23.5, 97.5)}, {"近边境": 50.0})   # 紧贴瑞丽(24.01,97.85)
    inland = _analyzer_with({"深内陆": (21.0, 99.5)}, {"深内陆": 50.0})  # 远离全部一档中心
    p_near = near.compute_potential()["provinces"][0]["potential"]
    p_inland = inland.compute_potential()["provinces"][0]["potential"]
    assert p_near > p_inland


def test_distance_sensitivity_unavailable_without_alt():
    """对照陆路里程需路网数据；未提供时返回 unavailable，不臆造。"""
    analyzer = _analyzer_with({"甲": (20.0, 96.0), "乙": (17.0, 97.0)},
                              {"甲": 60.0, "乙": 40.0})
    result = analyzer.distance_sensitivity()
    assert result["status"] == "unavailable" and result["spearman"] is None
    assert result["threshold"] == 0.8 and result["baseline_order"]


def test_distance_sensitivity_with_monotonic_alt_distance():
    """对照口径为基准的单调放大（1.3×）时排序不变，Spearman=1 → 判定不敏感。"""
    analyzer = _analyzer_with({"甲": (20.0, 96.0), "乙": (17.0, 97.0), "丙": (19.0, 95.0)},
                              {"甲": 60.0, "乙": 40.0, "丙": 50.0})

    def alt(lat1, lon1, lat2, lon2):
        return GeoPotentialAnalyzer._haversine(lat1, lon1, lat2, lon2) * 1.3

    result = analyzer.distance_sensitivity(alt_distance=alt)
    assert result["status"] == "ok" and result["spearman"] is not None
    assert round(result["spearman"], 6) == 1.0
    assert result["insensitive"] is (result["spearman"] >= 0.8)
