from server.engines.structure import source_policy


def test_source_policy_prefers_fresh_tdx_native(monkeypatch):
    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        if source == "tdx" and freq == "day":
            return [{"date": "2026-05-22", "open": 1, "high": 1, "low": 1, "close": 1}]
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
        if source == "tdx" and freq == "day":
            return [{"date": "2026-05-22", "open": 1, "high": 1, "low": 1, "close": 1}]
        if source == "tdx":
            return [{"date": "2026-05-22", "open": 1, "high": 1, "low": 1, "close": 1}] * 40
        return [{"date": "2026-05-15", "open": 1, "high": 1, "low": 1, "close": 1}] * 40

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    policy = source_policy.resolve_structure_source_policy(symbol="sh600790", level="week", limit=1200)

    assert policy["selected"]["source"] == "tdx"
    assert policy["selected"]["adjustflag"] == "2"


def test_source_policy_rejects_tdx_local_raw_for_formal_structure(monkeypatch):
    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        if source == "tdx" and adjustflag == "2":
            return []
        if source == "tdx" and adjustflag == "3":
            return [{"date": "2026-05-22", "open": 1, "high": 1, "low": 1, "close": 1}] * 40
        return [{"date": "2026-05-21", "open": 1, "high": 1, "low": 1, "close": 1}] * 40

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    policy = source_policy.resolve_structure_source_policy(symbol="sz301078", level="week", limit=1200)

    assert policy["selected"]["source"] == "baostock"
    assert policy["selected"]["adjustflag"] == "2"
    assert policy["selected"]["usable"] is True
    assert policy["candidates"][0]["adjustflag"] == "2"
    assert all(item["adjustflag"] != "3" for item in policy["candidates"])


def test_source_policy_rejects_orphan_tdx_qfq_without_day_factor(monkeypatch):
    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        if source == "tdx" and freq == "day":
            return []
        if source == "tdx":
            return [
                {"date": "2026-05-22 14:30:00", "open": 1, "high": 1, "low": 1, "close": 1},
                {"date": "2026-05-22 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1},
            ] * 20
        return [{"date": "2026-05-21 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1}] * 40

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    policy = source_policy.resolve_structure_source_policy(symbol="sz301076", level="30", limit=1200)

    assert policy["candidates"][0]["source"] == "tdx"
    assert policy["candidates"][0]["usable"] is False
    assert policy["candidates"][0]["reject_reason"] == "MISSING_TDX_DAY_FACTOR"
    assert policy["selected"]["source"] == "baostock"


def test_source_policy_rejects_stale_tdx_qfq_vs_raw(monkeypatch):
    def fake_query(symbol, freq, limit=260, adjustflag="2", source="baostock", **kwargs):
        if source == "tdx" and freq == "day" and adjustflag == "2":
            return [{"date": "2026-05-22", "open": 1, "high": 1, "low": 1, "close": 1}]
        if source == "tdx" and adjustflag == "2":
            return [
                {"date": "2026-05-21 14:30:00", "open": 1, "high": 1, "low": 1, "close": 1},
                {"date": "2026-05-21 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1},
            ] * 20
        if source == "tdx" and adjustflag == "3":
            return [{"date": "2026-05-22 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1}]
        return [{"date": "2026-05-21 15:00:00", "open": 1, "high": 1, "low": 1, "close": 1}] * 40

    monkeypatch.setattr(source_policy, "query_klines", fake_query)

    policy = source_policy.resolve_structure_source_policy(symbol="sh600790", level="30", limit=1200)

    assert policy["candidates"][0]["source"] == "tdx"
    assert policy["candidates"][0]["usable"] is False
    assert policy["candidates"][0]["reject_reason"] == "STALE_VS_TDX_RAW"
    assert policy["selected"]["source"] == "baostock"
