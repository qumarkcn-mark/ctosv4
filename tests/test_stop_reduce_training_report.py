import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent
from server.db.database import SCHEMA
from server.engines.ai_native.stop_reduce_report import build_stop_reduce_training_report


def make_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'dev_user', '开发者')")
    return conn


def seed_plan(conn):
    conn.execute(
        """
        INSERT INTO ai_holding_plans (
            plan_id, user_id, symbol, trade_date, as_of, radar_run_id, plan_status,
            current_script, target_weight_pct, max_position_pct, defense_line,
            repair_line, trigger_conditions_json, cancel_conditions_json,
            observation_focus_json, evidence_refs_json, raw_plan_json, disclaimer
        )
        VALUES (
            'holding_plan:1:sh603893:2026-05-02', 1, 'sh603893', '2026-05-02',
            '2026-05-02T10:30:00+08:00', 901, 'REDUCE_ALERT',
            '防守线已触发或接近触发，计划转为减仓复核。', 6.0, 12.0, 11.0,
            12.2, '[{"condition_id":"close_below_stop"}]', 'bad-json',
            '["复核防守线是否触发"]', '{"gate_score":90}', '{}',
            '仅供参考，不构成投资建议'
        )
        """
    )


def seed_intent(conn):
    conn.execute(
        """
        INSERT INTO ai_rebalance_runs (
            run_id, user_id, symbol, as_of, mode, radar_run_id,
            technical_view_json, fundamental_snapshot_id, calibration_summary_json, status
        )
        VALUES (
            'run-1', 1, 'sh603893', '2026-05-02T10:30:00+08:00', 'STOP_REDUCE', 901,
            '{}', '', '{}', 'CREATED'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ai_rebalance_intents (
            intent_id, run_id, source_plan_id, user_id, symbol, intent_type, action,
            current_weight_pct, target_weight_pct, quantity_policy, idempotency_key,
            as_of, conditions_json, reason_json, evidence_refs_json, disclaimer
        )
        VALUES (
            'intent-1', 'run-1', 'holding_plan:1:sh603893:2026-05-02', 1, 'sh603893',
            'STOP_REDUCE', 'REDUCE', 12.0, 6.0, 'reduce_to_target',
            '1:sh603893:stop_reduce:2026-05-02T10:30:901:close_below_stop',
            '2026-05-02T10:30:00+08:00',
            '{"activate_if":[{"condition_id":"close_below_stop"}]}',
            '{"technical":"30m破位"}',
            '{"technical_run_id":901}',
            '仅供参考，不构成投资建议'
        )
        """
    )


def seed_score_and_memory(conn):
    conn.execute(
        """
        INSERT INTO ai_stop_reduce_scores (
            score_id, intent_id, user_id, symbol, outcome_score, process_score,
            final_score, settlement_window, settlement_source, settlement_prices_json,
            tags_json, lesson_candidate, notes
        )
        VALUES (
            'score:intent-1', 'intent-1', 1, 'sh603893', 35, 100, 61, 'T+3',
            'kline_lake.day', '[{"date":"2026-05-04","close":10.6}]',
            '["REDUCE_TOO_EARLY"]', 1, '减仓后价格修复，可能偏早。'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ai_case_memory (
            case_id, case_key, user_id, symbol, intent_id, mistake_type,
            original_action, better_action, outcome, loss_delta_pct, lesson,
            context_hint, metadata_json
        )
        VALUES (
            'case-1', 'holding:loss:structure_breakdown:near_stop', 1, 'sh603893',
            'intent-1', 'REDUCE_TOO_EARLY', 'REDUCE', 'HOLD',
            '减仓后快速修复', -1.2, '同类结构需要等待日线确认。',
            '30m结构失效附近', 'not-json'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ai_calibration_stats (
            calibration_key, user_id, total_count, mistake_count,
            avg_loss_if_hold_pct, avg_benefit_if_reduce_pct, latest_mistake_case_id
        )
        VALUES (
            'holding:loss:structure_breakdown:near_stop', 1, 3, 1, -2.4, 1.1, 'case-1'
        )
        """
    )


def test_stop_reduce_training_report_empty_shape():
    conn = make_conn()

    report = build_stop_reduce_training_report(conn, user_id=1)

    assert report["overview"] == {
        "plans": 0,
        "alert_plans": 0,
        "intents": 0,
        "settled": 0,
        "waiting": 0,
        "case_memory_writes": 0,
        "lesson_candidates": 0,
        "avg_final_score": None,
    }
    assert report["plans"] == []
    assert report["intents"] == []
    assert report["case_memory"] == []
    assert "仅供参考" in report["disclaimer"]


def test_stop_reduce_training_report_waiting_intent_and_bad_json_are_safe():
    conn = make_conn()
    seed_plan(conn)
    seed_intent(conn)

    report = build_stop_reduce_training_report(conn, user_id=1, symbol="sh.603893")

    assert report["overview"]["plans"] == 1
    assert report["overview"]["alert_plans"] == 1
    assert report["overview"]["waiting"] == 1
    assert report["plans"][0]["trigger_conditions"] == [{"condition_id": "close_below_stop"}]
    assert report["plans"][0]["cancel_conditions"] == []
    assert report["intents"][0]["settlement_status"] == "WAITING"
    assert report["intents"][0]["score"] is None


def test_stop_reduce_training_report_settled_score_case_memory_and_calibration():
    conn = make_conn()
    seed_plan(conn)
    seed_intent(conn)
    seed_score_and_memory(conn)

    report = build_stop_reduce_training_report(conn, user_id=1, symbol="sh603893")

    assert report["overview"]["settled"] == 1
    assert report["overview"]["lesson_candidates"] == 1
    assert report["overview"]["avg_final_score"] == 61.0
    assert report["intents"][0]["settlement_status"] == "SETTLED"
    assert report["intents"][0]["score"]["tags"] == ["REDUCE_TOO_EARLY"]
    assert report["case_memory"][0]["metadata"] == {}
    assert report["calibration"][0]["mistake_count"] == 1


def test_stop_reduce_training_report_api(monkeypatch):
    conn = make_conn()
    seed_plan(conn)

    monkeypatch.setattr(agent, "get_connection", lambda: conn)

    response = __import__("asyncio").run(
        agent.stop_reduce_training_report(user_id=1, symbol="sh603893", limit=20)
    )

    assert response["status"] == "success"
    assert response["data"]["overview"]["plans"] == 1
    assert "仅供参考" in response["data"]["disclaimer"]
