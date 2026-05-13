from server.engines.structure import engine_router


def test_engine_router_defaults_to_chan_py(monkeypatch):
    monkeypatch.setattr(
        engine_router.chan_adapter,
        "analyze_structure_sync",
        lambda symbol, levels, count, cchan_preset, compute_profile: {
            "symbol": "sh.600519",
            "adapter_version": "chan_adapter.v1",
            "levels": {},
        },
    )

    result = engine_router.analyze_structure_with_engine_sync("sh600519", levels=["day"], count=50)

    assert result["engine"] == "chan_py"
    assert result["engine_mode"] == "chan_py"
    assert result["adapter_version"] == "chan_adapter.v1"


def test_engine_router_dual_attaches_shadow_and_comparison(monkeypatch):
    monkeypatch.setattr(
        engine_router.chan_adapter,
        "analyze_structure_sync",
        lambda symbol, levels, count, cchan_preset, compute_profile: {
            "symbol": "sh.600519",
            "levels": {
                "day": {
                    "bi_zhongshus": [{"zg": 11, "zd": 10, "gg": 12, "dd": 9}],
                    "bis": [{"is_up": True}],
                }
            },
        },
    )
    monkeypatch.setattr(
        engine_router,
        "analyze_czsc_structure_sync",
        lambda symbol, levels, count, compute_profile: {
            "engine": "czsc",
            "symbol": "sh.600519",
            "levels": {
                "day": {
                    "active_zhongshu": {"zg": 11.01, "zd": 10.01, "gg": 12, "dd": 9},
                    "stats": {"bi_count": 1, "bi_zs_count": 1},
                    "state_hint": "inside_center",
                }
            },
            "error": "",
        },
    )

    result = engine_router.analyze_structure_with_engine_sync("sh600519", levels=["day"], count=50, structure_engine="dual")

    assert result["engine"] == "chan_py"
    assert result["engine_mode"] == "dual"
    assert result["shadow_structure"]["engine"] == "czsc"
    assert result["structure_engine_comparison"]["levels"]["day"]["latest_center_match"] is True
