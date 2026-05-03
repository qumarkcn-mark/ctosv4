import socket

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
