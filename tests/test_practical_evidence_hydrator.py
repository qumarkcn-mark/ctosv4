from server.engines.ai_native.practical_evidence_hydrator import hydrate_practical_evidence


def _klines(prices: list[float], *, volume_start: float = 1000, volume_step: float = 10):
    rows = []
    for index, price in enumerate(prices):
        rows.append(
            {
                "close": price,
                "high": price + 0.2,
                "low": price - 0.2,
                "volume": volume_start + index * volume_step,
            }
        )
    return rows


def _snapshot(*, price: float, direction: str = "up", signals: dict | None = None):
    prices = [10 + index * 0.08 for index in range(75)]
    prices.extend([16.2, 16.35, 16.45, 16.48, 16.5])
    return {
        "snapshot": {
            "price": price,
            "klines": _klines(prices, volume_start=2000, volume_step=-8),
            "signals": signals or {},
            "bis": [
                {
                    "direction": direction,
                    "is_sure": True,
                    "start_price": 12,
                    "end_price": 15,
                    "high": 15,
                    "low": 12,
                    "bar_count": 8,
                },
                {
                    "direction": "down" if direction == "up" else "up",
                    "is_sure": True,
                    "start_price": 15,
                    "end_price": 13,
                    "high": 15,
                    "low": 13,
                    "bar_count": 6,
                },
                {
                    "direction": direction,
                    "is_sure": False,
                    "source": "czsc_ubi",
                    "status": "ongoing",
                    "start_price": 13,
                    "end_price": price,
                    "high": price,
                    "low": 13,
                    "bar_count": 7,
                },
            ],
        }
    }


def test_practical_evidence_extracts_first_batch_fields():
    snapshots = {
        "day": _snapshot(
            price=16.5,
            signals={"日线_D1F_分型强弱": "强底_有中枢_任意_0"},
        )
    }
    clusters = [
        {
            "zone": [16.45, 16.6],
            "type": "pressure",
            "status": "testing",
            "hit_count": 3,
            "source_levels": ["day", "5"],
            "semantic": "日线:接近中枢上沿ZG，属于离开后回拉观察边界",
        },
        {
            "zone": [15.0, 15.2],
            "type": "support",
            "status": "holding",
            "hit_count": 2,
            "source_levels": ["5"],
        },
    ]

    result = hydrate_practical_evidence(snapshots, pressure_support=clusters, level_names={"day": "日线"})

    day = result["by_level"]["日线"]
    assert day["bi_completion"]["status"] == "ongoing"
    assert day["bi_completion"]["completion_hint"] in {"near_end", "developing", "extending"}
    assert day["divergence_evidence"]["status"] == "ok"
    assert day["divergence_evidence"]["impulse_exhaustion_context"]["exhaustion_reading"] in {
        "upside_fatigue_or_high_level_digesting",
        "upside_force_confirming",
    }
    assert day["fx_quality"]["strength"] == "strong"
    assert day["fx_quality"]["mark"] == "bottom"
    assert result["level_interaction"]["nearest_pressure"]["interaction"] == "testing_pressure"
    assert result["level_interaction"]["nearest_support"]["interaction"] == "support_below"


def test_practical_evidence_degrades_without_optional_inputs():
    result = hydrate_practical_evidence(
        {"5": {"snapshot": {"price": 10, "klines": [], "bis": [], "signals": {}}}},
        pressure_support=[],
        level_names={"5": "5分钟"},
    )

    assert result["by_level"]["5分钟"]["bi_completion"] == {"status": "no_unfinished_bi"}
    assert result["by_level"]["5分钟"]["fx_quality"]["status"] == "unavailable"
    assert result["level_interaction"]["nearest_pressure"] is None


def test_divergence_does_not_compare_confirmed_bi_with_itself():
    snap = _snapshot(price=16.5)
    snap["snapshot"]["bis"][-1]["is_sure"] = True
    snap["snapshot"]["bis"][-1].pop("source", None)
    snap["snapshot"]["bis"][-1].pop("status", None)

    result = hydrate_practical_evidence({"day": snap}, level_names={"day": "日线"})

    assert result["by_level"]["日线"]["divergence_evidence"]["status"] == "ok"
    assert result["by_level"]["日线"]["divergence_evidence"]["previous_extreme"] == 15


def test_practical_evidence_marks_upside_fatigue_context():
    snap = _snapshot(price=16.5)
    snap["snapshot"]["bis"][-1]["bar_count"] = 4
    snap["snapshot"]["klines"] = _klines(
        [10 + index * 0.05 for index in range(75)] + [16.2, 16.3, 16.38, 16.45, 16.5],
        volume_start=2200,
        volume_step=-12,
    )

    result = hydrate_practical_evidence({"30": snap}, level_names={"30": "30分钟"})

    context = result["by_level"]["30分钟"]["divergence_evidence"]["impulse_exhaustion_context"]
    assert context["prior_impulse"] == "upside_extension"
    assert context["exhaustion_reading"] in {"upside_fatigue_or_high_level_digesting", "upside_force_confirming"}


def test_practical_evidence_marks_downside_exhaustion_context():
    prices = [20 - index * 0.05 for index in range(75)] + [14.5, 14.2, 14.0, 13.9, 13.8]
    snap = {
        "snapshot": {
            "price": 13.8,
            "klines": _klines(prices, volume_start=1000, volume_step=20),
            "signals": {},
            "bis": [
                {
                    "direction": "down",
                    "is_sure": True,
                    "start_price": 18,
                    "end_price": 14,
                    "high": 18,
                    "low": 14,
                    "bar_count": 8,
                },
                {
                    "direction": "up",
                    "is_sure": True,
                    "start_price": 14,
                    "end_price": 15,
                    "high": 15,
                    "low": 14,
                    "bar_count": 5,
                },
                {
                    "direction": "down",
                    "is_sure": False,
                    "source": "czsc_ubi",
                    "status": "ongoing",
                    "start_price": 15,
                    "end_price": 13.8,
                    "high": 15,
                    "low": 13.8,
                    "bar_count": 5,
                },
            ],
        }
    }

    result = hydrate_practical_evidence({"5": snap}, level_names={"5": "5分钟"})

    context = result["by_level"]["5分钟"]["divergence_evidence"]["impulse_exhaustion_context"]
    assert context["prior_impulse"] in {"high_volume_selloff", "moderate"}
    assert context["exhaustion_reading"] in {"post_flush_relief", "downside_force_weakening", "mid_impulse"}
