import os
import asyncio
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import SCHEMA
from server.engines.ai_native.schemas import AIReasoningResponse, PositionContext
from server.workers.stop_reduce_monitor import (
    StopReduceMonitorConfig,
    load_monitor_positions,
    paper_account_from_position,
    parse_args,
    render_stop_reduce_monitor_report,
    run_stop_reduce_monitor,
)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'dev_user', '开发者')")
    conn.execute(
        """
        INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, current_price, updated_at)
        VALUES (1, 'sh603893', '瑞芯微', 1000, 12.5, 10.8, '2026-05-02T10:00:00+08:00')
        """
    )
    return conn


def response(symbol="sh603893", current_price=10.8, stop_price=11.0):
    return AIReasoningResponse(
        gate_status="PASS",
        gate_score=90,
        generated_at="2026-05-02T10:30:00+08:00",
        coach_filtered_md="跌破结构线后减仓。仅供参考，不构成投资建议。",
        position_context=PositionContext(
            is_holding=True,
            state="LOSS_HOLDING",
            label="亏损持仓",
            avg_cost=12.5,
            quantity=1000,
            current_price=current_price,
            pnl_percentage=-13.6,
            position_value=10800,
            weight_pct=12.0,
            risk_flags=["STRUCTURE_AGAINST_POSITION"],
            risk_lines=[
                {
                    "type": "structure_invalidation",
                    "label": "30m结构失效",
                    "value": stop_price,
                    "side": "below",
                    "distance_pct": 1.8,
                }
            ],
            nearest_risk_line={
                "type": "structure_invalidation",
                "label": "30m结构失效",
                "value": stop_price,
                "side": "below",
                "distance_pct": 1.8,
            },
            coach_summary="近端结构防线被击穿。",
        ),
        run_id=901,
    )


def hold_response(symbol="sh603893"):
    item = response(symbol=symbol, current_price=12.0, stop_price=10.0)
    item.position_context.pnl_percentage = 2.0
    item.position_context.risk_flags = []
    return item


def test_monitor_enqueues_pending_intent_from_current_position():
    conn = make_conn()

    async def fake_builder(**kwargs):
        return response(kwargs["symbol"])

    report = asyncio.run(
        run_stop_reduce_monitor(
            conn=conn,
            config=StopReduceMonitorConfig(user_id=1, symbol="sh.603893"),
            reasoning_builder=fake_builder,
        )
    )

    assert report.scanned_positions == 1
    assert report.saved_plans == 1
    assert report.enqueued_intents == 1
    assert report.rows[0]["reason"] == "REDUCE"
    assert conn.execute("SELECT status FROM ai_rebalance_runs").fetchone()[0] == "WAITING_SETTLEMENT"
    assert conn.execute("SELECT plan_status FROM ai_holding_plans").fetchone()[0] == "REDUCE_ALERT"
    intent = conn.execute("SELECT action, symbol FROM ai_rebalance_intents").fetchone()
    assert dict(intent) == {"action": "REDUCE", "symbol": "sh603893"}
    assert conn.execute("SELECT COUNT(*) FROM ai_stop_reduce_scores").fetchone()[0] == 0


def test_monitor_dry_run_does_not_write_pending_intent():
    conn = make_conn()

    async def fake_builder(**kwargs):
        return response(kwargs["symbol"])

    report = asyncio.run(
        run_stop_reduce_monitor(
            conn=conn,
            config=StopReduceMonitorConfig(user_id=1, dry_run=True),
            reasoning_builder=fake_builder,
        )
    )

    assert report.enqueued_intents == 1
    assert report.saved_plans == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_rebalance_intents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ai_holding_plans").fetchone()[0] == 0


def test_monitor_saves_daily_plan_without_action_when_status_is_hold():
    conn = make_conn()

    async def fake_builder(**kwargs):
        return hold_response(kwargs["symbol"])

    report = asyncio.run(
        run_stop_reduce_monitor(
            conn=conn,
            config=StopReduceMonitorConfig(user_id=1),
            reasoning_builder=fake_builder,
        )
    )

    assert report.saved_plans == 1
    assert report.enqueued_intents == 0
    assert report.rows[0]["status"] == "PLANNED"
    assert report.rows[0]["reason"] == "HOLD"
    assert conn.execute("SELECT plan_status FROM ai_holding_plans").fetchone()[0] == "HOLD"
    assert conn.execute("SELECT COUNT(*) FROM ai_rebalance_intents").fetchone()[0] == 0


def test_monitor_skips_when_adapter_cannot_build_intent():
    conn = make_conn()

    async def fake_builder(**kwargs):
        item = response(kwargs["symbol"])
        item.position_context.is_holding = False
        return item

    report = asyncio.run(
        run_stop_reduce_monitor(
            conn=conn,
            config=StopReduceMonitorConfig(user_id=1),
            reasoning_builder=fake_builder,
        )
    )

    assert report.enqueued_intents == 0
    assert report.skipped == 1
    assert report.rows[0]["reason"] == "NO_PLAN"


def test_load_monitor_positions_accepts_symbol_variants():
    conn = make_conn()

    rows = load_monitor_positions(conn, user_id=1, symbol="sh.603893")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "sh603893"


def test_paper_account_from_position_uses_current_price():
    account = paper_account_from_position(
        1,
        {"symbol": "sh603893", "quantity": 1000, "avg_cost": 12.5, "current_price": 10.8},
    )

    assert account.positions["sh603893"].total_qty == 1000
    assert account.positions["sh603893"].last_price == 10.8


def test_parse_args_and_render_report():
    config = parse_args(["--user-id", "1", "--symbol", "sh.603893", "--limit", "5", "--dry-run"])

    assert config == StopReduceMonitorConfig(user_id=1, symbol="sh.603893", limit=5, dry_run=True)
    rendered = render_stop_reduce_monitor_report(
        report=type(
            "Report",
            (),
            {
                "scanned_positions": 1,
                "reasoning_runs": 1,
                "saved_plans": 1,
                "enqueued_intents": 1,
                "skipped": 0,
                "rows": [{"symbol": "sh603893", "status": "ENQUEUED", "reason": "REDUCE"}],
            },
        )()
    )
    assert "AI Stop/Reduce Monitor Report" in rendered
    assert "仅供参考" in rendered
