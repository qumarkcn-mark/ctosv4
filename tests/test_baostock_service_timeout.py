import socket
import time
from types import SimpleNamespace

import baostock.util.socketutil as bs_socket_util

from server.services import qfq_normalizer
from server.services import baostock_service


def test_baostock_socket_timeout_is_scoped_to_baostock():
    assert socket.getdefaulttimeout() is None
    assert baostock_service.BAOSTOCK_SOCKET_TIMEOUT_SECONDS > 0
    assert bs_socket_util.SocketUtil.connect.__module__ == baostock_service.__name__


def test_baostock_socket_timeout_falls_back_for_invalid_env(monkeypatch):
    monkeypatch.setenv("BAOSTOCK_SOCKET_TIMEOUT_SECONDS", "bad")

    assert baostock_service._load_baostock_socket_timeout_seconds() == 4.0

    monkeypatch.setenv("BAOSTOCK_SOCKET_TIMEOUT_SECONDS", "0")

    assert baostock_service._load_baostock_socket_timeout_seconds() == 4.0


def test_baostock_circuit_window_falls_back_for_invalid_env(monkeypatch):
    monkeypatch.setenv("BAOSTOCK_CIRCUIT_OPEN_SECONDS", "bad")

    assert baostock_service._load_baostock_circuit_open_seconds() == 60.0

    monkeypatch.setenv("BAOSTOCK_CIRCUIT_OPEN_SECONDS", "-1")

    assert baostock_service._load_baostock_circuit_open_seconds() == 60.0


def test_baostock_login_circuit_skips_recent_failures(monkeypatch):
    monkeypatch.setattr(baostock_service, "_session_active", False)
    monkeypatch.setattr(baostock_service, "_last_login_failure_at", time.monotonic())

    def fail_if_called():
        raise AssertionError("bs.login should be skipped while circuit is open")

    monkeypatch.setattr(baostock_service.bs, "login", fail_if_called)

    try:
        baostock_service._ensure_session()
    except ConnectionError as exc:
        assert "熔断" in str(exc)
    else:
        raise AssertionError("expected circuit breaker ConnectionError")


def test_refresh_symbol_qfq_falls_back_to_full_sync_when_cache_missing(monkeypatch):
    calls = []

    monkeypatch.setattr(baostock_service, "get_last_sync_date", lambda _symbol, _freq: None)

    def fake_full_sync(symbol, freq, **kwargs):
        calls.append((symbol, freq, kwargs))
        return 1200

    monkeypatch.setattr(baostock_service, "fetch_klines_sync", fake_full_sync)

    written = baostock_service.refresh_symbol_qfq("sh.688256", "30")

    assert written == 1200
    assert calls == [("sh.688256", "30", {"end_date": None, "adjustflag": "2"})]


def test_refresh_symbol_qfq_uses_recent_overlap_window_for_existing_cache(monkeypatch):
    seen = {}
    last_dates = iter(["2026-05-19 15:00:00", "2026-05-20 15:00:00"])

    monkeypatch.setattr(baostock_service, "get_last_sync_date", lambda _symbol, _freq: next(last_dates))
    monkeypatch.setattr(
        baostock_service,
        "query_klines",
        lambda *_args, **_kwargs: [{"date": "2026-05-19 15:00:00", "close": 100.0}],
    )

    def fake_rebuild(symbol, **kwargs):
        seen["symbol"] = symbol
        seen.update(kwargs)
        return SimpleNamespace(day_rows=0, week_rows=0, minute_rows={"30": 64})

    monkeypatch.setattr(qfq_normalizer, "rebuild_symbol_qfq", fake_rebuild)

    written = baostock_service.refresh_symbol_qfq("sh.688256", "30")

    assert written == 64
    assert seen == {
        "symbol": "sh.688256",
        "start_date": "2026-05-09",
        "end_date": None,
        "include_minutes": True,
        "target_freqs": ["30"],
    }


def test_refresh_symbol_qfq_does_not_report_change_when_sync_date_unchanged(monkeypatch):
    last_dates = iter(["2026-05-19 15:00:00", "2026-05-19 15:00:00"])

    monkeypatch.setattr(baostock_service, "get_last_sync_date", lambda _symbol, _freq: next(last_dates))
    monkeypatch.setattr(baostock_service, "query_klines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        qfq_normalizer,
        "rebuild_symbol_qfq",
        lambda *_args, **_kwargs: SimpleNamespace(day_rows=0, week_rows=0, minute_rows={"5": 32}),
    )

    assert baostock_service.refresh_symbol_qfq("sh.688256", "5") == 0


def test_refresh_symbol_qfq_escalates_when_qfq_scale_drifts(monkeypatch):
    last_dates = iter(["2026-05-19", "2026-05-20"])
    query_results = iter([
        [{"date": "2026-05-10", "close": 100.0}],
        [{"date": "2026-05-10", "close": 96.0}],
    ])
    full_sync_calls = []

    monkeypatch.setattr(baostock_service, "get_last_sync_date", lambda _symbol, _freq: next(last_dates))
    monkeypatch.setattr(baostock_service, "query_klines", lambda *_args, **_kwargs: next(query_results))
    monkeypatch.setattr(
        qfq_normalizer,
        "rebuild_symbol_qfq",
        lambda *_args, **_kwargs: SimpleNamespace(day_rows=20, week_rows=0, minute_rows={}),
    )

    def fake_full_sync(symbol, freq, **kwargs):
        full_sync_calls.append((symbol, freq, kwargs))
        return 2761

    monkeypatch.setattr(baostock_service, "fetch_klines_sync", fake_full_sync)

    written = baostock_service.refresh_symbol_qfq("sh.688256", "day")

    assert written == 2761
    assert full_sync_calls == [("sh.688256", "day", {"end_date": None, "adjustflag": "2"})]
