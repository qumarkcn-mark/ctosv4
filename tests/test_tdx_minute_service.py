import os
import struct
import sys

from fastapi.testclient import TestClient
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import data as data_api
from server.services import tdx_minute_service
from server.services.tdx_minute_service import (
    RECORD_FMT,
    encode_lc1_date,
    read_tdx_1m_klines,
    tdx_minute_file_path,
    tdx_minute_status,
)


def write_lc1(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as file:
        for row in rows:
            file.write(struct.pack(RECORD_FMT, *row))


def lc1_row(year, month, day, hour, minute, open_, high, low, close, amount=1000.0, volume=100):
    return (
        encode_lc1_date(year, month, day),
        hour * 60 + minute,
        float(open_),
        float(high),
        float(low),
        float(close),
        float(amount),
        int(volume),
        0,
    )


def test_tdx_minute_file_path_uses_minline_lc1(tmp_path):
    path = tdx_minute_file_path("sh.600519", vipdoc=str(tmp_path))

    assert path.endswith("sh/minline/sh600519.lc1")


def test_read_tdx_1m_klines_parses_lc1_records(tmp_path):
    file_path = tmp_path / "sh" / "minline" / "sh600519.lc1"
    write_lc1(
        file_path,
        [
            lc1_row(2026, 4, 28, 9, 31, 10, 10.2, 9.9, 10.1),
            lc1_row(2026, 4, 28, 9, 32, 10.1, 10.3, 10.0, 10.2),
        ],
    )

    rows = read_tdx_1m_klines("sh600519", vipdoc=str(tmp_path), limit=10)

    assert len(rows) == 2
    assert rows[0]["symbol"] == "sh.600519"
    assert rows[0]["date"] == "2026-04-28 09:31:00"
    assert rows[0]["freq"] == "1"
    assert rows[0]["bar_status"] == "CLOSED"
    assert rows[0]["source"] == "tdx_local_1m"


def test_read_tdx_1m_klines_respects_limit(tmp_path):
    file_path = tmp_path / "sz" / "minline" / "sz000001.lc1"
    write_lc1(
        file_path,
        [
            lc1_row(2026, 4, 28, 9, 31, 10, 11, 9, 10),
            lc1_row(2026, 4, 28, 9, 32, 11, 12, 10, 11),
            lc1_row(2026, 4, 28, 9, 33, 12, 13, 11, 12),
        ],
    )

    rows = read_tdx_1m_klines("sz.000001", vipdoc=str(tmp_path), limit=2)

    assert [row["date"] for row in rows] == [
        "2026-04-28 09:32:00",
        "2026-04-28 09:33:00",
    ]


def test_tdx_minute_status_reports_missing_file(tmp_path):
    payload = tdx_minute_status("sh.600519", vipdoc=str(tmp_path))

    assert payload["available"] is False
    assert payload["reason"] == "MINUTE_FILE_NOT_FOUND"


def test_tdx_minute_api_uses_display_replay_only_contract(monkeypatch):
    monkeypatch.setattr(
        data_api,
        "read_tdx_1m_klines",
        lambda symbol, limit=240, end_date=None: [
            {
                "symbol": "sh.600519",
                "freq": "1",
                "date": "2026-04-28 09:31:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 100,
                "amount": 1000,
                "adjustflag": "3",
                "bar_status": "CLOSED",
                "source": "tdx_local_1m",
            }
        ],
    )
    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.get("/tdx/minute/sh600519")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "tdx_local_1m"
    assert payload["usage"] == "display_replay_only"
