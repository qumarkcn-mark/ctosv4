from server.engines.structure import source_policy


def test_source_policy_prefers_fresh_tdx_native(monkeypatch):
    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        if source == "tdx":
            return [
                {"date": "2026-05-22 14:30:00", "open": 1, "high": 1, "low": 1, "close": 1},
                {"date": "2026-05-22 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1},
            ] * 20
        return [
            {"date": "2026-05-21 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1},
        ] * 40

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    policy = source_policy.resolve_structure_source_policy(symbol="sh600790", level="30", limit=1200)

    assert policy["selected"]["source"] == "tdx"
    assert policy["selected"]["adjustflag"] == "2"
    assert policy["selected"]["usable"] is True


def test_source_policy_rejects_stale_tdx_vs_baostock(monkeypatch):
    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        if source == "tdx":
            return [{"date": "2026-04-30", "open": 1, "high": 1, "low": 1, "close": 1}] * 40
        return [{"date": "2026-05-21", "open": 1, "high": 1, "low": 1, "close": 1}] * 40

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    policy = source_policy.resolve_structure_source_policy(symbol="sh600790", level="day", limit=1200)

    assert policy["candidates"][0]["source"] == "tdx"
    assert policy["candidates"][0]["usable"] is False
    assert policy["candidates"][0]["reject_reason"] == "STALE_VS_FALLBACK"
    assert policy["selected"]["source"] == "baostock"


def test_query_structure_klines_uses_selected_source(monkeypatch):
    calls = {}

    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        calls.update({"symbol": symbol, "freq": freq, "limit": limit, "adjustflag": adjustflag, "source": source})
        return [{"date": "2026-05-22 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1}]

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    rows = source_policy.query_structure_klines(
        symbol="sh600790",
        level="30",
        limit=1200,
        policy={"selected": {"source": "tdx", "adjustflag": "2"}},
    )

    assert rows
    assert calls == {"symbol": "sh.600790", "freq": "30", "limit": 1200, "adjustflag": "2", "source": "tdx"}


def test_source_policy_prefers_fresh_tdx_week(monkeypatch):
    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        if source == "tdx":
            return [{"date": "2026-05-22", "open": 1, "high": 1, "low": 1, "close": 1}] * 40
        return [{"date": "2026-05-15", "open": 1, "high": 1, "low": 1, "close": 1}] * 40

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    policy = source_policy.resolve_structure_source_policy(symbol="sh600790", level="week", limit=1200)

    assert policy["selected"]["source"] == "tdx"
    assert policy["selected"]["adjustflag"] == "2"
