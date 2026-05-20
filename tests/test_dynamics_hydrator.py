from server.engines.ai_native.dynamics_hydrator import hydrate_dynamics


def make_klines(count=80, *, start=10.0, step=0.1, volume=1000):
    rows = []
    price = start
    for index in range(count):
        price += step
        rows.append(
            {
                "close": round(price, 4),
                "high": round(price + 0.2, 4),
                "low": round(price - 0.2, 4),
                "volume": volume + index * 10,
            }
        )
    return rows


def test_hydrate_dynamics_returns_insufficient_status_for_short_series():
    result = hydrate_dynamics(make_klines(20))

    assert result == {"status": "insufficient_bars", "bar_count": 20}


def test_hydrate_dynamics_detects_bullish_alignment_and_volume_expansion():
    rows = make_klines(80, start=10, step=0.08, volume=1000)
    for index, row in enumerate(rows[-5:]):
        row["volume"] = 3000 + index * 100

    result = hydrate_dynamics(rows)

    assert result["macd_state"] in {"above_zero", "golden_cross"}
    assert result["volume_state"] in {"expanding", "abnormal_spike"}
    assert result["volume_ratio_5_20"] > 1
    assert result["ma_posture"] == "bullish_alignment"
    assert result["atr_volatility"] in {"compressed", "normal", "expanded", "unknown"}


def test_hydrate_dynamics_detects_bearish_alignment():
    result = hydrate_dynamics(make_klines(80, start=20, step=-0.08, volume=1000))

    assert result["macd_state"] in {"below_zero", "dead_cross"}
    assert result["ma_posture"] == "bearish_alignment"
