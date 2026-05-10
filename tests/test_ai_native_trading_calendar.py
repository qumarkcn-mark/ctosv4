import json
from datetime import date

from server.engines.ai_native import trading_calendar
from server.engines.ai_native.market_time import valid_until_for_refresh_trigger
from tests.test_ai_native_market_time import dt


def test_trading_calendar_falls_back_to_weekday_without_file(monkeypatch, tmp_path):
    monkeypatch.setattr(trading_calendar.config, "AI_NATIVE_TRADING_CALENDAR_PATH", str(tmp_path / "missing.json"))
    trading_calendar.reset_trading_calendar_cache()

    assert trading_calendar.is_trading_day(date(2026, 5, 4)) is True
    assert trading_calendar.is_trading_day(date(2026, 5, 9)) is False


def test_trading_calendar_supports_holidays_and_extra_trading_days(monkeypatch, tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "holidays": ["2026-05-04"],
                "extra_trading_days": ["2026-05-09"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trading_calendar.config, "AI_NATIVE_TRADING_CALENDAR_PATH", str(path))
    trading_calendar.reset_trading_calendar_cache()

    assert trading_calendar.is_trading_day(date(2026, 5, 4)) is False
    assert trading_calendar.is_trading_day(date(2026, 5, 9)) is True


def test_market_time_skips_configured_holiday(monkeypatch, tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"holidays": ["2026-05-04"]}), encoding="utf-8")
    monkeypatch.setattr(trading_calendar.config, "AI_NATIVE_TRADING_CALENDAR_PATH", str(path))
    trading_calendar.reset_trading_calendar_cache()

    result = valid_until_for_refresh_trigger(dt(2026, 5, 4, 9, 10), "NEXT_30M_CLOSE")

    assert result == "2026-05-05T10:00:00+08:00"
