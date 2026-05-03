import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import SCHEMA
from server.engines.ai_native.holding_plan import build_holding_plan_from_ai_response
from server.engines.ai_native.holding_plan_store import save_holding_plan, load_latest_holding_plan
from server.engines.ai_native.schemas import AIReasoningResponse, AllowedPrice, PositionContext, ReasoningBoundaries


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'dev_user', '开发者')")
    return conn


def response(current_price=10.8, stop_price=11.0, pnl_percentage=-13.6, risk_flags=None):
    return AIReasoningResponse(
        gate_status="PASS",
        gate_score=90,
        generated_at="2026-05-02T10:30:00+08:00",
        coach_filtered_md="仅供参考，不构成投资建议。",
        key_boundaries=ReasoningBoundaries(
            confirm=[AllowedPrice(label="repair", value=12.2, source="structure", level="30")]
        ),
        position_context=PositionContext(
            is_holding=True,
            state="LOSS_HOLDING",
            avg_cost=12.5,
            quantity=1000,
            current_price=current_price,
            pnl_percentage=pnl_percentage,
            position_value=10800,
            weight_pct=12.0,
            risk_flags=["STRUCTURE_AGAINST_POSITION"] if risk_flags is None else risk_flags,
            nearest_risk_line={"type": "structure_invalidation", "label": "30m结构失效", "value": stop_price},
            risk_lines=[{"type": "structure_invalidation", "label": "30m结构失效", "value": stop_price}],
            coach_summary="近端结构防线被击穿。",
            coach_focus="先处理风险",
        ),
        run_id=901,
    )


def test_build_holding_plan_reduce_alert_from_broken_defense():
    plan = build_holding_plan_from_ai_response(
        user_id=1,
        symbol="sh603893",
        response=response(current_price=10.8, stop_price=11.0),
        as_of="2026-05-02T10:30:00+08:00",
    )

    assert plan is not None
    assert plan.plan_status == "REDUCE_ALERT"
    assert plan.target_weight_pct == 6.0
    assert plan.defense_line == 11.0
    assert plan.repair_line == 12.2
    assert plan.trigger_conditions[0].condition_id == "close_below_structure_invalidation"
    assert plan.trade_date == "2026-05-02"
    assert plan.plan_id == "holding_plan:1:sh603893:2026-05-02"


def test_build_holding_plan_hold_when_far_from_defense():
    plan = build_holding_plan_from_ai_response(
        user_id=1,
        symbol="sh603893",
        response=response(current_price=12.0, stop_price=10.0, pnl_percentage=2.0, risk_flags=[]),
        as_of="2026-05-02T10:30:00+08:00",
    )

    assert plan.plan_status == "HOLD"
    assert plan.target_weight_pct == 12.0


def test_save_holding_plan_upserts_by_user_symbol_trade_date():
    conn = make_conn()
    first = build_holding_plan_from_ai_response(
        user_id=1,
        symbol="sh603893",
        response=response(current_price=12.0, stop_price=10.0, pnl_percentage=2.0, risk_flags=[]),
        as_of="2026-05-02T10:30:00+08:00",
    )
    second = build_holding_plan_from_ai_response(
        user_id=1,
        symbol="sh603893",
        response=response(current_price=10.8, stop_price=11.0),
        as_of="2026-05-02T14:30:00+08:00",
    )

    save_holding_plan(conn, first)
    save_holding_plan(conn, second)
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM ai_holding_plans").fetchone()[0] == 1
    latest = load_latest_holding_plan(conn, user_id=1, symbol="sh603893")
    assert latest["plan_status"] == "REDUCE_ALERT"
    assert latest["as_of"] == "2026-05-02T14:30:00+08:00"
    assert latest["plan_id"] == first.plan_id
