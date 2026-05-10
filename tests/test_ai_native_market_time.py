from datetime import datetime, timezone, timedelta

from server.engines.ai_native.market_time import latest_closed_data_slice, valid_until_for_refresh_trigger
from server.engines.ai_native.rebalance_engine import build_rebalance_contract


CN_TZ = timezone(timedelta(hours=8))


def dt(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=CN_TZ)


def test_next_30m_close_uses_a_share_bar_boundaries():
    assert valid_until_for_refresh_trigger(dt(2026, 5, 4, 9, 10), "NEXT_30M_CLOSE").endswith("10:00:00+08:00")
    assert valid_until_for_refresh_trigger(dt(2026, 5, 4, 10, 0), "NEXT_30M_CLOSE").endswith("10:30:00+08:00")
    assert valid_until_for_refresh_trigger(dt(2026, 5, 4, 11, 45), "NEXT_30M_CLOSE").endswith("13:30:00+08:00")


def test_next_5m_close_respects_afternoon_reopen():
    assert valid_until_for_refresh_trigger(dt(2026, 5, 4, 13, 2), "NEXT_5M_CLOSE").endswith("13:05:00+08:00")


def test_after_close_rolls_to_next_weekday_session():
    result = valid_until_for_refresh_trigger(dt(2026, 5, 8, 15, 1), "NEXT_30M_CLOSE")

    assert result == "2026-05-11T10:00:00+08:00"


def test_daily_close_rolls_after_session_close():
    same_day = valid_until_for_refresh_trigger(dt(2026, 5, 4, 14, 59), "NEXT_DAILY_CLOSE")
    next_day = valid_until_for_refresh_trigger(dt(2026, 5, 4, 15, 0), "NEXT_DAILY_CLOSE")

    assert same_day == "2026-05-04T15:00:00+08:00"
    assert next_day == "2026-05-05T15:00:00+08:00"


def test_latest_closed_data_slice_maps_after_close_to_same_day_close():
    assert latest_closed_data_slice(dt(2026, 5, 6, 19, 40)).isoformat() == "2026-05-06T15:00:00+08:00"


def test_latest_closed_data_slice_uses_previous_trading_day_before_open():
    assert latest_closed_data_slice(dt(2026, 5, 11, 9, 10)).isoformat() == "2026-05-08T15:00:00+08:00"


def test_rebalance_contract_valid_until_uses_generated_at_and_refresh_trigger():
    contract = build_rebalance_contract(
        [],
        user_id=1,
        generated_at="2026-05-05T10:01:00+08:00",
        refresh_trigger="NEXT_30M_CLOSE",
    )

    assert contract.valid_until == "2026-05-05T10:30:00+08:00"
