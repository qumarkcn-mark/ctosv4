import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import SCHEMA
from server.engines.ai_native.stop_reduce_store import save_rebalance_intent, save_rebalance_run
from server.engines.ai_native.stop_reduce_training import (
    RebalanceIntent,
    StopReduceCondition,
    StopReduceConditions,
    build_stop_reduce_idempotency_key,
)
from server.workers.stop_reduce_settlement import (
    StopReduceSettlementConfig,
    parse_args,
    render_stop_reduce_settlement_report,
    run_stop_reduce_settlement,
)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'dev_user', '开发者')")
    return conn


def intent(action="HOLD"):
    as_of = "2026-05-02T10:30:00+08:00"
    return RebalanceIntent(
        intent_type="STOP_REDUCE",
        intent_id=f"stop_reduce:1:sh.603893:{as_of}:{action}",
        idempotency_key=build_stop_reduce_idempotency_key(
            user_id=1,
            symbol="sh.603893",
            as_of=as_of,
            technical_run_id=f"run_{action}",
            primary_condition_id="close_below_stop",
        ),
        user_id=1,
        symbol="sh.603893",
        action=action,
        current_weight_pct=12.0,
        target_weight_pct=12.0 if action == "HOLD" else 6.0,
        quantity_policy="observe" if action == "HOLD" else "reduce_to_target",
        as_of=as_of,
        conditions=StopReduceConditions(
            activate_if=[
                StopReduceCondition("close_below_stop", "daily_close", "close", "<=", 11.0, "2026-05-02")
            ],
            cancel_if=[
                StopReduceCondition("close_above_repair", "daily_close", "close", ">=", 13.0, "2026-05-02")
            ],
        ),
        reason={"technical": "30m破位"},
        evidence_refs={"technical_run_id": f"run_{action}"},
    )


def persist_intent(conn, rebalance_intent):
    save_rebalance_run(
        conn,
        run_id="settle_run_1",
        user_id=rebalance_intent.user_id,
        symbol=rebalance_intent.symbol,
        as_of=rebalance_intent.as_of,
        status="WAITING_SETTLEMENT",
    )
    save_rebalance_intent(conn, rebalance_intent, run_id="settle_run_1")
    conn.commit()


def test_settlement_waits_until_required_future_daily_closes_exist():
    conn = make_conn()
    persist_intent(conn, intent("HOLD"))

    report = run_stop_reduce_settlement(
        conn=conn,
        config=StopReduceSettlementConfig(user_id=1, settlement_limit=3),
        kline_loader=lambda symbol, freq, limit=260: [
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 10000},
            {"date": "2026-05-04", "open": 10.7, "high": 10.9, "low": 10.1, "close": 10.2, "volume": 10000},
        ],
    )

    assert report.scanned == 1
    assert report.waiting == 1
    assert report.rows[0]["reason"] == "WAITING_FOR_SETTLEMENT_PRICES"
    assert report.rows[0]["available_settlement_bars"] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_stop_reduce_scores").fetchone()[0] == 0


def test_settlement_scores_and_stores_sparse_case_memory():
    conn = make_conn()
    persist_intent(conn, intent("HOLD"))

    report = run_stop_reduce_settlement(
        conn=conn,
        config=StopReduceSettlementConfig(user_id=1, settlement_limit=3),
        kline_loader=lambda symbol, freq, limit=260: [
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 10000},
            {"date": "2026-05-04", "open": 10.7, "high": 10.9, "low": 10.1, "close": 10.2, "volume": 10000},
            {"date": "2026-05-05", "open": 10.1, "high": 10.2, "low": 9.8, "close": 9.9, "volume": 10000},
            {"date": "2026-05-06", "open": 9.8, "high": 9.9, "low": 9.2, "close": 9.3, "volume": 10000},
        ],
    )

    assert report.scanned == 1
    assert report.settled == 1
    assert report.case_memory_writes == 1
    assert report.rows[0]["status"] == "SETTLED"
    assert "AI_HELD_AFTER_STOP_BROKEN" in report.rows[0]["tags"]
    assert conn.execute("SELECT COUNT(*) FROM ai_stop_reduce_scores").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_case_memory").fetchone()[0] == 1
    rendered = render_stop_reduce_settlement_report(report)
    assert "Settled: 1" in rendered
    assert "仅供参考" in rendered


def test_settlement_skips_already_scored_intents():
    conn = make_conn()
    persist_intent(conn, intent("HOLD"))
    run_stop_reduce_settlement(
        conn=conn,
        config=StopReduceSettlementConfig(user_id=1, settlement_limit=1),
        kline_loader=lambda symbol, freq, limit=260: [
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 10000},
            {"date": "2026-05-04", "open": 10.7, "high": 10.9, "low": 10.1, "close": 10.2, "volume": 10000},
        ],
    )

    second = run_stop_reduce_settlement(
        conn=conn,
        config=StopReduceSettlementConfig(user_id=1, settlement_limit=1),
        kline_loader=lambda symbol, freq, limit=260: [],
    )

    assert second.scanned == 0


def test_settlement_symbol_filter_accepts_canonical_variant():
    conn = make_conn()
    persist_intent(conn, intent("HOLD"))

    report = run_stop_reduce_settlement(
        conn=conn,
        config=StopReduceSettlementConfig(user_id=1, symbol="sh603893", settlement_limit=1),
        kline_loader=lambda symbol, freq, limit=260: [
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 10000},
            {"date": "2026-05-04", "open": 10.7, "high": 10.9, "low": 10.1, "close": 10.2, "volume": 10000},
        ],
    )

    assert report.scanned == 1


def test_parse_args_supports_dry_run():
    config = parse_args(["--user-id", "1", "--symbol", "sh.603893", "--limit", "10", "--dry-run"])

    assert config == StopReduceSettlementConfig(
        user_id=1,
        symbol="sh.603893",
        limit=10,
        settlement_limit=5,
        daily_window_limit=260,
        persist=False,
    )
