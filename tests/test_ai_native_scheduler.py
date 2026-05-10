import asyncio
from datetime import datetime, timezone, timedelta

from server.workers import ai_native_scheduler as scheduler_module
from server.engines.ai_native import trading_calendar
from server.workers.ai_native_scheduler import AINativeScheduler


CN_TZ = timezone(timedelta(hours=8))


def dt(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=CN_TZ)


def test_scheduler_runs_premarket_once(monkeypatch):
    calls = []

    async def fake_premarket(scheduler):
        calls.append(("premarket", scheduler.user_id))

    monkeypatch.setattr(scheduler_module, "_run_premarket_playbook", fake_premarket)
    scheduler = AINativeScheduler(enabled=True, user_id=7)

    first = asyncio.run(scheduler.tick(dt(2026, 5, 4, 9, 5)))
    second = asyncio.run(scheduler.tick(dt(2026, 5, 4, 9, 6)))

    assert first == ["2026-05-04:premarket_playbook"]
    assert second == []
    assert calls == [("premarket", 7)]


def test_scheduler_runs_30m_rebalance_slot_once(monkeypatch):
    calls = []

    async def fake_rebalance(scheduler):
        calls.append(("rebalance", scheduler.max_rebalance_items))

    monkeypatch.setattr(scheduler_module, "_run_rebalance_refresh", fake_rebalance)
    scheduler = AINativeScheduler(enabled=True, max_rebalance_items=3)

    first = asyncio.run(scheduler.tick(dt(2026, 5, 4, 10, 0, 30)))
    second = asyncio.run(scheduler.tick(dt(2026, 5, 4, 10, 2, 59)))
    third = asyncio.run(scheduler.tick(dt(2026, 5, 4, 10, 3, 1)))

    assert first == ["2026-05-04:rebalance:1000"]
    assert second == []
    assert third == []
    assert calls == [("rebalance", 3)]


def test_scheduler_runs_postmarket_report_once(monkeypatch):
    calls = []

    async def fake_report(scheduler):
        calls.append(("report", scheduler.user_id))

    monkeypatch.setattr(scheduler_module, "_run_postmarket_report", fake_report)
    scheduler = AINativeScheduler(enabled=True, user_id=1)

    executed = asyncio.run(scheduler.tick(dt(2026, 5, 4, 15, 10)))
    repeated = asyncio.run(scheduler.tick(dt(2026, 5, 4, 15, 12)))

    assert executed == ["2026-05-04:postmarket_report"]
    assert repeated == []
    assert calls == [("report", 1)]


def test_scheduler_skips_weekends(monkeypatch):
    calls = []

    async def fake_premarket(scheduler):
        calls.append("premarket")

    monkeypatch.setattr(scheduler_module, "_run_premarket_playbook", fake_premarket)
    scheduler = AINativeScheduler(enabled=True)

    executed = asyncio.run(scheduler.tick(dt(2026, 5, 9, 9, 5)))

    assert executed == []
    assert calls == []


def test_scheduler_skips_configured_holiday(monkeypatch, tmp_path):
    calls = []
    path = tmp_path / "calendar.json"
    path.write_text('{"holidays":["2026-05-04"]}', encoding="utf-8")
    monkeypatch.setattr(trading_calendar.config, "AI_NATIVE_TRADING_CALENDAR_PATH", str(path))
    trading_calendar.reset_trading_calendar_cache()

    async def fake_premarket(scheduler):
        calls.append("premarket")

    monkeypatch.setattr(scheduler_module, "_run_premarket_playbook", fake_premarket)
    scheduler = AINativeScheduler(enabled=True)

    executed = asyncio.run(scheduler.tick(dt(2026, 5, 4, 9, 5)))

    assert executed == []
    assert calls == []
