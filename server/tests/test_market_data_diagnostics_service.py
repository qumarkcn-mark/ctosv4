from server.services import market_data_diagnostics_service as svc


def test_diagnostics_routes_to_intraday_when_active(monkeypatch):
    monkeypatch.setattr(
        svc,
        "query_intraday_bars",
        lambda *args, include_replaced=False, **kwargs: [
            {
                "bar_time": "2026-06-02 10:01:00",
                "quality": "full",
                "bar_status": "FORMING",
                "replaced_by_official": 0,
            }
        ],
    )
    monkeypatch.setattr(
        svc,
        "query_klines",
        lambda *args, **kwargs: [{"date": "2026-06-02 15:00:00"}],
    )
    monkeypatch.setattr(
        svc,
        "_structure_summary",
        lambda _symbol: {"levels": {"day": {"source": "tdx", "storage": "adjusted_bars", "dataset": "tdx_qfq"}}},
    )

    result = svc.diagnose_market_data_symbol(
        "sh.600790",
        sampler_status={"bridge_enabled": True, "last_error": ""},
        trade_date="2026-06-02",
    )

    assert result["routing"]["m1_display_primary"] == "intraday_bars"
    assert result["routing"]["ai_intraday_snapshot_primary"] == "intraday_bars"
    assert result["readiness"]["status"] == "ready"


def test_diagnostics_waiting_when_quotes_are_after_close(monkeypatch):
    monkeypatch.setattr(svc, "query_intraday_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "query_klines", lambda *args, **kwargs: [{"date": "2026-06-02 15:00:00"}])
    monkeypatch.setattr(
        svc,
        "_structure_summary",
        lambda _symbol: {"levels": {"day": {"source": "tdx", "storage": "adjusted_bars", "dataset": "tdx_qfq"}}},
    )

    result = svc.diagnose_market_data_symbol(
        "sh.600790",
        sampler_status={"bridge_enabled": True, "last_error": "NO_VALID_TRADING_MINUTE_QUOTES"},
        trade_date="2026-06-02",
    )

    assert result["routing"]["m1_display_primary"] == "tdx_lake"
    assert result["readiness"] == {"status": "waiting", "reason": "NO_VALID_TRADING_MINUTE_QUOTES"}


def test_diagnostics_blocks_when_bridge_disabled(monkeypatch):
    monkeypatch.setattr(svc, "query_intraday_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "query_klines", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_structure_summary", lambda _symbol: {"levels": {}})

    result = svc.diagnose_market_data_symbol(
        "sh.600790",
        sampler_status={"bridge_enabled": False, "last_error": "TDX_BRIDGE_URL_NOT_CONFIGURED"},
        trade_date="2026-06-02",
    )

    assert result["routing"]["m1_display_primary"] == "missing"
    assert result["readiness"] == {"status": "blocked", "reason": "TDX_BRIDGE_DISABLED"}


def test_batch_diagnostics_summarizes_routes(monkeypatch):
    def fake_one(symbol, sampler_status=None, trade_date=None):
        if symbol == "sh.600790":
            return {
                "symbol": symbol,
                "readiness": {"status": "ready"},
                "routing": {
                    "m1_display_primary": "intraday_bars",
                    "formal_czsc_primary": "tdx:adjusted_bars:tdx_qfq",
                },
            }
        return {
            "symbol": symbol,
            "readiness": {"status": "waiting"},
            "routing": {
                "m1_display_primary": "tdx_lake",
                "formal_czsc_primary": "tdx:adjusted_bars:tdx_qfq",
            },
        }

    monkeypatch.setattr(svc, "diagnose_market_data_symbol", fake_one)

    result = svc.diagnose_market_data_symbols(
        ["sh.600790", "sh600790", "sz.300394", "bad"],
        sampler_status={"bridge_enabled": True},
        limit=10,
    )

    assert result["count"] == 2
    assert [item["symbol"] for item in result["items"]] == ["sh.600790", "sz.300394"]
    assert result["summary"]["readiness"] == {"ready": 1, "waiting": 1}
    assert result["summary"]["m1_display_primary"] == {"intraday_bars": 1, "tdx_lake": 1}
    assert result["summary"]["formal_czsc_primary"] == {"tdx:adjusted_bars:tdx_qfq": 2}
