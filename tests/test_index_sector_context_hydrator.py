from server.engines.ai_native import index_sector_context_hydrator as hydrator


def test_index_sector_context_marks_manual_mapping(monkeypatch):
    monkeypatch.setattr(hydrator, "_level_context", lambda symbol, benchmark, freq: _fake_level(freq))
    monkeypatch.setattr(hydrator, "get_tdx_sector_context", lambda symbol: {})

    payload = hydrator.hydrate_index_sector_context("sh.688008")

    assert payload["usage"] == "background_evidence_only"
    assert payload["sector_context"]["sector"] == "半导体 / 存储"
    assert payload["sector_context"]["benchmark"] == "sh.000688"
    assert payload["sector_context"]["mapping_source"] == "manual_v0"
    assert payload["relative_strength"]["vs_benchmark"] == "stronger_than_benchmark"
    assert payload["market_context"]["benchmark_name"] == "科创50"
    assert "最近5根日K累计" in payload["relative_strength"]["evidence"][0]


def test_index_sector_context_marks_default_proxy_mapping(monkeypatch):
    monkeypatch.setattr(hydrator, "_level_context", lambda symbol, benchmark, freq: _fake_level(freq))
    monkeypatch.setattr(hydrator, "get_tdx_sector_context", lambda symbol: {})

    payload = hydrator.hydrate_index_sector_context("sh.688999")

    assert payload["sector_context"]["sector"] == "科创成长"
    assert payload["sector_context"]["benchmark"] == "sh.000688"
    assert payload["sector_context"]["mapping_source"] == "default_proxy_v0"


def test_index_sector_context_prefers_tdx_exact_sector(monkeypatch):
    monkeypatch.setattr(hydrator, "_level_context", lambda symbol, benchmark, freq: _fake_level(freq))
    monkeypatch.setattr(
        hydrator,
        "get_tdx_sector_context",
        lambda symbol: {
            "source": "tdx_hq_cache",
            "primary_sector": {
                "name": "工业控制设备",
                "index_code": "881315",
                "path": ["机械设备", "自动化设备", "工业控制设备"],
            },
            "tdx_industry": {"path": ["电气设备"]},
            "concept_themes": [
                {"name": "人工智能", "index_code": "880900", "source": "tdx_infoharbor_block"},
                {"name": "人形机器", "index_code": "880901", "source": "tdx_infoharbor_block"},
            ],
            "daily_stats": {"ret_1": 2.25, "ret_5": 1.42, "ret_20": 4.06},
        },
    )

    payload = hydrator.hydrate_index_sector_context("sh.688698")

    assert payload["sector_context"]["sector"] == "工业控制设备"
    assert payload["sector_context"]["sector_index"] == "881315"
    assert payload["sector_context"]["mapping_source"] == "tdx_hq_cache"
    assert payload["sector_context"]["sector_path"] == ["机械设备", "自动化设备", "工业控制设备"]
    assert payload["concept_context"]["themes"] == ["人工智能", "人形机器"]
    assert payload["concept_context"]["source"] == "tdx_infoharbor_block"
    assert payload["relative_strength"]["vs_sector_daily"] == "stronger_than_benchmark"
    assert payload["relative_strength"]["evidence"][0].startswith("所属板块日线")
    assert payload["relative_strength"]["evidence"][1] == "概念主题: 人工智能, 人形机器"


def _fake_level(freq: str) -> dict:
    unit = "日K" if freq == "day" else f"{freq}分钟K"
    return {
        "freq": freq,
        "stock": {
            "rows": 30,
            "last_time": "2026-05-22",
            "stats": {
                "ret_5": 5.0,
                "ret_20": 12.0,
                "returns": {"freq": freq, "bar_unit": unit, "last_5_bars_pct": 5.0},
                "return_labels": {"last_5_bars_pct": f"最近5根{unit}累计涨跌幅"},
                "ma20_gap": 3.0,
                "phase": "uptrend_or_breakout",
            },
        },
        "benchmark": {
            "symbol": "sh.000688",
            "name": "科创50",
            "rows": 30,
            "last_time": "2026-05-22",
            "stats": {
                "ret_5": 1.0,
                "ret_20": 5.0,
                "returns": {"freq": freq, "bar_unit": unit, "last_5_bars_pct": 1.0},
                "return_labels": {"last_5_bars_pct": f"最近5根{unit}累计涨跌幅"},
                "ma20_gap": 2.0,
                "phase": "uptrend_or_breakout",
            },
        },
        "spread": {
            "ret_1_spread": 1.0,
            "ret_5_spread": 4.0,
            "ret_20_spread": 7.0,
            "return_spreads": {"freq": freq, "bar_unit": unit, "last_5_bars_spread_pct": 4.0},
            "return_spread_labels": {"last_5_bars_spread_pct": f"个股相对基准最近5根{unit}累计涨跌差"},
            "label": "stronger_than_benchmark",
        },
    }
