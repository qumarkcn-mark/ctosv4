from server.engines.ai_native import unified_reasoning_service as svc


def _snapshot(level: str) -> dict:
    return {
        "snapshot_id": f"snap_{level}",
        "level": level,
        "data_as_of": "2026-05-28 15:00:00",
        "updated_at": "2026-05-28 20:30:00",
        "data_signature": f"sig_{level}",
        "compute_profile": "tactical_v1",
        "snapshot": {
            "price": 10.0,
            "source": {"provider": "tdx", "adjustflag": "2"},
            "klines": [],
        },
    }


def _patch_lightweight_dependencies(monkeypatch):
    monkeypatch.setattr(svc, "hydrate_dynamics", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(svc, "hydrate_practical_evidence", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(svc, "build_reasoning_continuity_context", lambda **_kwargs: {})
    monkeypatch.setattr(svc, "hydrate_market_task_context", lambda **_kwargs: {})
    monkeypatch.setattr(svc, "_index_sector_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(svc, "build_chan_signal_digest", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(svc, "_collect_chan_signals", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(svc, "_position_context", lambda **_kwargs: {"holding": False})
    monkeypatch.setattr(svc, "_compute_pressure_support", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(svc, "_add_pressure_support_semantics", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(svc, "_compute_resonance_evidence", lambda *_args, **_kwargs: {})


def test_unified_reasoning_reads_snapshots_without_bootstrap(monkeypatch):
    calls = []

    def fake_get_latest_structure(**kwargs):
        calls.append(kwargs)
        return _snapshot(kwargs["level"])

    _patch_lightweight_dependencies(monkeypatch)
    monkeypatch.setattr(svc, "get_latest_structure", fake_get_latest_structure)
    monkeypatch.setattr(
        svc,
        "_intraday_observation",
        lambda _symbol: {
            "source": "tdx_quote_aggregation",
            "usage": "intraday_preview",
            "as_of": "2026-05-29 10:00:00",
            "quote": {"price": 10.8},
            "coverage": {"quality": "partial"},
            "levels": {},
        },
    )

    payload = svc.build_unified_reasoning_input(
        user_id=1,
        symbol="sh.600790",
        levels=["day"],
        compute_profile="tactical_v1",
    )

    assert calls
    assert calls[0]["allow_bootstrap"] is False
    assert payload["input"]["current_price"] == 10.8
    assert payload["input"]["current_price_source"] == "intraday_observation"
    assert payload["input"]["data_freshness"]["structure_basis"] == "fresh_snapshot_read_only"
    assert payload["input"]["data_freshness"]["intraday_basis"]["coverage"]["quality"] == "partial"
    assert payload["input"]["structure_snapshot"]["levels"]["day"]["snapshot_id"] == "snap_day"


def test_unified_reasoning_reports_missing_snapshot_levels(monkeypatch):
    def fake_get_latest_structure(**kwargs):
        if kwargs["level"] == "day":
            return _snapshot("day")
        return None

    _patch_lightweight_dependencies(monkeypatch)
    monkeypatch.setattr(svc, "get_latest_structure", fake_get_latest_structure)
    monkeypatch.setattr(svc, "_intraday_observation", lambda _symbol: {})

    payload = svc.build_unified_reasoning_input(
        user_id=1,
        symbol="sh.600790",
        levels=["day", "30"],
        compute_profile="tactical_v1",
    )

    assert payload["missing_levels"] == ["30"]
    assert payload["input"]["data_freshness"]["missing_structure_levels"] == ["30"]
    assert payload["input"]["current_price_source"] == "structure_snapshot"
