"""Trading calendar source for AI Native scheduling.

The calendar file is optional. When absent, CT-OS falls back to regular
Monday-Friday A-share sessions.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from server import config


def is_trading_day(value: date | datetime) -> bool:
    day = value.date() if isinstance(value, datetime) else value
    calendar = load_trading_calendar(config.AI_NATIVE_TRADING_CALENDAR_PATH)
    day_text = day.isoformat()
    if day_text in calendar.get("extra_trading_days", set()):
        return True
    if day_text in calendar.get("holidays", set()):
        return False
    return day.weekday() < 5


@lru_cache(maxsize=8)
def load_trading_calendar(path: str | None = None) -> dict[str, set[str]]:
    source = Path(path or config.AI_NATIVE_TRADING_CALENDAR_PATH)
    if not source.exists():
        return {"holidays": set(), "extra_trading_days": set()}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return {"holidays": set(), "extra_trading_days": set()}
    return {
        "holidays": _date_set(payload.get("holidays")),
        "extra_trading_days": _date_set(payload.get("extra_trading_days")),
    }


def reset_trading_calendar_cache() -> None:
    load_trading_calendar.cache_clear()


def _date_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    result = set()
    for item in value:
        text = str(item)
        try:
            date.fromisoformat(text)
        except ValueError:
            continue
        result.add(text)
    return result
