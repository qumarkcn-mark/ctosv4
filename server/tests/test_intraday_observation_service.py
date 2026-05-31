from datetime import datetime as RealDateTime

from server.services import intraday_observation_service as svc


class FixedDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 5, 28, 10, 0, 0, tzinfo=tz)


def _row(date: str, close: float) -> dict:
    return {
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
        "amount": 1000,
    }


def test_today_lake_1m_rows_merges_tdx_and_qmt_without_masking(monkeypatch):
    def fake_query_klines(symbol, freq, start_date=None, limit=360, adjustflag="2", source=None):
        assert symbol == "sh.600790"
        assert freq == "1"
        if source == "tdx" and adjustflag == "2":
            return [_row("2026-05-28 09:31:00", 10.1), _row("2026-05-28 09:32:00", 10.2)]
        if source == "qmt" and adjustflag == "3":
            return [_row("2026-05-28 09:31:00", 9.9), _row("2026-05-28 09:33:00", 10.3)]
        return []

    monkeypatch.setattr(svc, "datetime", FixedDateTime)
    monkeypatch.setattr(svc, "query_klines", fake_query_klines)

    rows = svc._today_lake_1m_rows("sh.600790")

    assert [row["date"] for row in rows] == [
        "2026-05-28 09:31:00",
        "2026-05-28 09:32:00",
        "2026-05-28 09:33:00",
    ]
    assert rows[0]["close"] == 10.1
    assert rows[0]["source"] == "tdx_lake_1m"
    assert rows[-1]["close"] == 10.3
    assert rows[-1]["source"] == "qmt_lake_1m"


def test_today_lake_1m_rows_prefers_qfq_tdx_over_raw_for_same_minute(monkeypatch):
    def fake_query_klines(symbol, freq, start_date=None, limit=360, adjustflag="2", source=None):
        if source == "tdx" and adjustflag == "3":
            return [_row("2026-05-28 09:31:00", 9.8)]
        if source == "tdx" and adjustflag == "2":
            return [_row("2026-05-28 09:31:00", 10.1)]
        return []

    monkeypatch.setattr(svc, "datetime", FixedDateTime)
    monkeypatch.setattr(svc, "query_klines", fake_query_klines)

    rows = svc._today_lake_1m_rows("sh.600790")

    assert len(rows) == 1
    assert rows[0]["close"] == 10.1
    assert rows[0]["adjustflag"] == "2"
