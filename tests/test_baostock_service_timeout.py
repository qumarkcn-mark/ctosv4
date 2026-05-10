import socket
import time

import baostock.util.socketutil as bs_socket_util

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
