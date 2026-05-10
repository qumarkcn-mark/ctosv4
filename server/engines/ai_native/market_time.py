"""A-share market-time helpers for AI Native contracts."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from server.engines.ai_native.trading_calendar import is_trading_day


CN_TZ = timezone(timedelta(hours=8))

MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)

FIVE_MINUTE_CLOSES = [
    time(hour, minute)
    for hour, minutes in (
        (9, range(35, 60, 5)),
        (10, range(0, 60, 5)),
        (11, range(0, 31, 5)),
        (13, range(5, 60, 5)),
        (14, range(0, 60, 5)),
        (15, [0]),
    )
    for minute in minutes
]

THIRTY_MINUTE_CLOSES = [
    time(10, 0),
    time(10, 30),
    time(11, 0),
    time(11, 30),
    time(13, 30),
    time(14, 0),
    time(14, 30),
    time(15, 0),
]


def valid_until_for_refresh_trigger(now: datetime, trigger: str) -> str:
    """Return the next contract expiry time for an AI Native refresh trigger."""
    local_now = _localize(now)
    if trigger == "NEXT_DAILY_CLOSE":
        return _next_daily_close(local_now).isoformat(timespec="seconds")
    if trigger == "NEXT_5M_CLOSE":
        return _next_bar_close(local_now, FIVE_MINUTE_CLOSES).isoformat(timespec="seconds")
    if trigger == "NEXT_30M_CLOSE":
        return _next_bar_close(local_now, THIRTY_MINUTE_CLOSES).isoformat(timespec="seconds")
    if trigger == "PRICE_TOUCH":
        return (local_now + timedelta(minutes=30)).isoformat(timespec="seconds")
    if trigger in {"MANUAL_REFRESH", "POSITION_CHANGE"}:
        return (local_now + timedelta(minutes=30)).isoformat(timespec="seconds")
    return _next_bar_close(local_now, THIRTY_MINUTE_CLOSES).isoformat(timespec="seconds")


def latest_closed_data_slice(now: datetime, closes: list[time] | None = None) -> datetime:
    """Return the latest closed A-share data slice for an analysis generated at ``now``."""
    local_now = _localize(now)
    close_times = closes or THIRTY_MINUTE_CLOSES
    day = local_now.date()
    if is_trading_day(day):
        for close_time in sorted(close_times, reverse=True):
            candidate = datetime.combine(day, close_time, tzinfo=CN_TZ)
            if candidate <= local_now:
                return candidate
    day = _previous_calendar_day(day)
    while True:
        if is_trading_day(day):
            return datetime.combine(day, sorted(close_times)[-1], tzinfo=CN_TZ)
        day = _previous_calendar_day(day)


def _localize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)


def _next_bar_close(now: datetime, closes: list[time]) -> datetime:
    day = now.date()
    while True:
        if is_trading_day(day):
            for close_time in closes:
                candidate = datetime.combine(day, close_time, tzinfo=CN_TZ)
                if candidate > now:
                    return candidate
        day = _next_calendar_day(day)
        now = datetime.combine(day, time(0, 0), tzinfo=CN_TZ)


def _next_daily_close(now: datetime) -> datetime:
    day = now.date()
    while True:
        if is_trading_day(day):
            candidate = datetime.combine(day, AFTERNOON_END, tzinfo=CN_TZ)
            if candidate > now:
                return candidate
        day = _next_calendar_day(day)
        now = datetime.combine(day, time(0, 0), tzinfo=CN_TZ)


def _next_calendar_day(day):
    return day + timedelta(days=1)


def _previous_calendar_day(day):
    return day - timedelta(days=1)
