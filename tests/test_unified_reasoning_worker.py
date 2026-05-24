import asyncio

from server.workers import unified_reasoning_worker as worker_module


def test_unified_reasoning_worker_uses_watchboard_universe(monkeypatch):
    calls = []

    monkeypatch.setattr(worker_module, "list_watchboard_user_ids", lambda limit=None: [1])
    monkeypatch.setattr(
        worker_module,
        "resolve_watchboard_universe",
        lambda user_id: [
            {"symbol": "sh.600519", "sources": ["positions"], "priority": 100},
            {"symbol": "sh.688008", "sources": ["watchboard"], "priority": 60},
            {"symbol": "sh.600000", "sources": ["watchboard"], "priority": 60},
        ],
    )
    monkeypatch.setattr(worker_module.config, "AI_UNIFIED_REASONING_SYMBOLS_PER_USER", 2)

    async def fake_request(**kwargs):
        calls.append(kwargs)
        return {"symbol": kwargs["symbol"], "trigger": {"decision": "generated"}}

    monkeypatch.setattr(worker_module, "request_ai_reasoning", fake_request)

    worker = worker_module.UnifiedReasoningWorker(interval_seconds=1)
    result = asyncio.run(worker.tick())

    assert result == {"generated": 2, "errors": []}
    assert [call["symbol"] for call in calls] == ["sh.600519", "sh.688008"]
    assert all(call["user_id"] == 1 for call in calls)
    assert {call["trigger_reason"] for call in calls} == {"watchboard_worker"}
