from server.engines.ai_native.chan_signal_digest import build_chan_signal_digest


def test_chan_signal_digest_collects_four_practical_categories():
    snapshots = {
        "day": {
            "snapshot": {
                "signals": {
                    "日线_D1_三买辅助V230228": "三买_6笔_任意_0",
                    "日线_D1B_SELL1": "一卖_15笔_任意_0",
                    "日线_D1_中枢共振V221221": "看多_任意_任意_0",
                    "日线_D1七笔_形态V230620": "向上中枢完成_任意_任意_0",
                    "日线_D1_无关": "其他_其他_任意_0",
                }
            }
        }
    }

    result = build_chan_signal_digest(snapshots, level_names={"day": "日线"})

    day = result["by_level"]["日线"]
    assert day["third_buy_sell"][0]["value"] == "三买_6笔_任意_0"
    assert day["third_buy_sell"][0]["polarity"] == "bullish"
    assert day["first_buy_sell"][0]["polarity"] == "bearish"
    assert day["zhongshu_resonance"][0]["key"] == "日线_D1_中枢共振V221221"
    assert day["trend_pullback_rebound"][0]["value"] == "向上中枢完成_任意_任意_0"
    assert len(result["summary"]) == 4


def test_chan_signal_digest_ignores_empty_other_signals():
    snapshots = {
        "5": {
            "snapshot": {
                "signals": {
                    "5分钟_D1_三买辅助V230228": "其他_其他_任意_0",
                    "5分钟_D1B_BUY1": "其他_任意_任意_0",
                }
            }
        }
    }

    result = build_chan_signal_digest(snapshots, level_names={"5": "5分钟"})

    assert result["by_level"] == {}
    assert result["summary"] == []
