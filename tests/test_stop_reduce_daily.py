import os
import asyncio
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import SCHEMA
from server.engines.ai_native.schemas import AIReasoningResponse, PositionContext
from server.workers.stop_reduce_daily import (
    StopReduceDailyConfig,
    parse_args,
    render_stop_reduce_daily_report,
    run_stop_reduce_daily,
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


def response():
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
            current_price=10.8,
            pnl_percentage=-13.6,
            position_value=10800,
            weight_pct=12.0,
            risk_flags=["STRUCTURE_AGAINST_POSITION"],
            risk_lines=[
                {
                    "type": "structure_invalidation",
                    "label": "30m结构失效",
                    "value": 11.0,
                    "side": "below",
                    "distance_pct": 1.8,
                }
            ],
            nearest_risk_line={
                "type": "structure_invalidation",
                "label": "30m结构失效",
                "value": 11.0,
                "side": "below",
                "distance_pct": 1.8,
            },
            coach_summary="近端结构防线被击穿。",
        ),
        run_id=901,
    )


def test_daily_loop_generates_plan_enqueues_intent_and_settles(monkeypatch):
    conn = make_conn()

    async def fake_builder(**kwargs):
        return response()

    monkeypatch.setattr("server.workers.stop_reduce_monitor.get_connection", lambda: conn)
    monkeypatch.setattr("server.workers.stop_reduce_settlement.get_connection", lambda: conn)

    report = asyncio.run(
        run_stop_reduce_daily(
            conn=conn,
            config=StopReduceDailyConfig(user_id=1, symbol="sh.603893", settlement_limit=2),
            reasoning_builder=fake_builder,
            kline_loader=lambda symbol, freq, limit=260: [
                {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 10000},
                {"date": "2026-05-04", "open": 10.7, "high": 10.9, "low": 10.1, "close": 10.2, "volume": 10000},
                {"date": "2026-05-05", "open": 10.1, "high": 10.2, "low": 9.8, "close": 9.9, "volume": 10000},
            ],
        )
    )

    assert report.plans == 1
    assert report.intents == 1
    assert report.settled == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_holding_plans").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_rebalance_intents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_stop_reduce_scores").fetchone()[0] == 1
    assert conn.execute("SELECT source_plan_id FROM ai_rebalance_intents").fetchone()[0] == "holding_plan:1:sh603893:2026-05-02"

    rendered = render_stop_reduce_daily_report(report)
    assert "AI Stop/Reduce Daily Report" in rendered
    assert "Plans saved: 1" in rendered
    assert "Intents settled: 1" in rendered
    assert "仅供参考" in rendered


def test_daily_dry_run_does_not_persist(monkeypatch):
    conn = make_conn()

    async def fake_builder(**kwargs):
        return response()

    monkeypatch.setattr("server.workers.stop_reduce_monitor.get_connection", lambda: conn)
    monkeypatch.setattr("server.workers.stop_reduce_settlement.get_connection", lambda: conn)

    report = asyncio.run(
        run_stop_reduce_daily(
            conn=conn,
            config=StopReduceDailyConfig(user_id=1, dry_run=True, skip_settlement=True),
            reasoning_builder=fake_builder,
        )
    )

    assert report.plans == 1
    assert report.intents == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_holding_plans").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ai_rebalance_intents").fetchone()[0] == 0


def test_parse_args_supports_daily_flags():
    config = parse_args([
        "--user-id",
        "2",
        "--symbol",
        "sz300124",
        "--limit",
        "5",
        "--settlement-limit",
        "3",
        "--dry-run",
        "--skip-settlement",
        "--output",
        "reports/daily.md",
    ])

    assert config == StopReduceDailyConfig(
        user_id=2,
        symbol="sz300124",
        limit=5,
        settlement_limit=3,
        daily_window_limit=260,
        fundamental_verdict="中性",
        dry_run=True,
        skip_monitor=False,
        skip_settlement=True,
        output_path="reports/daily.md",
    )
