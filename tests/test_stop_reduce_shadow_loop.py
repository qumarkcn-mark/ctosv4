import os
import json
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import SCHEMA
from server.engines.ai_native.stop_reduce_training import (
    RebalanceIntent,
    StopReduceCondition,
    StopReduceConditions,
    build_stop_reduce_idempotency_key,
)
from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    AIReasoningResponse,
    GateResult,
    PositionContext,
    StructureTranscript,
)
from server.engines.execution.paper_models import PaperAccount, PaperPosition
from server.scripts.stop_reduce_shadow_loop import (
    StopReduceCliConfig,
    build_historical_stop_reduce_sample,
    build_historical_stop_reduce_samples,
    build_stop_reduce_training_report,
    build_shadow_account_from_reasoning_row,
    candidate_from_ai_reasoning_row,
    enqueue_stop_reduce_pending_intents,
    load_daily_settlement_prices,
    load_ai_reasoning_rows_for_stop_reduce,
    parse_args,
    render_persisted_calibration_summary,
    render_stop_reduce_training_report,
    run_stop_reduce_shadow_cli,
    run_stop_reduce_shadow_batch,
    run_stop_reduce_shadow_sample,
    StopReduceHistoricalCandidate,
    StopReduceShadowSample,
)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'dev_user', '开发者')")
    return conn


def account():
    return PaperAccount(
        paper_account_id="paper_stop_reduce_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=600,
                protected_base_qty=200,
                avg_cost=12.5,
                last_price=12.0,
            )
        },
    )


def intent(action="REDUCE", suffix=""):
    as_of = "2026-05-02T10:30:00+08:00"
    run_id = f"123{suffix}"
    return RebalanceIntent(
        intent_type="STOP_REDUCE",
        intent_id=f"stop_reduce:1:sh.603893:2026-05-02T10:30:00+08:00{suffix}",
        idempotency_key=build_stop_reduce_idempotency_key(
            user_id=1,
            symbol="sh.603893",
            as_of=as_of,
            technical_run_id=run_id,
            primary_condition_id="close_below_stop",
        ),
        user_id=1,
        symbol="sh.603893",
        action=action,
        current_weight_pct=12.0,
        target_weight_pct=6.0 if action == "REDUCE" else 12.0,
        quantity_policy="reduce_to_target",
        as_of=as_of,
        conditions=StopReduceConditions(
            activate_if=[
                StopReduceCondition("close_below_stop", "daily_close", "close", "<=", 11.0, "2026-05-02")
            ],
            cancel_if=[
                StopReduceCondition("close_above_repair", "daily_close", "close", ">=", 13.0, "2026-05-02")
            ],
        ),
        reason={"technical": "30m破位", "fundamental": "中性"},
        evidence_refs={"technical_run_id": run_id},
    )


def test_shadow_sample_activates_executes_scores_and_persists_case():
    conn = make_conn()

    result = run_stop_reduce_shadow_sample(
        run_id="stop_reduce_run_1",
        account=account(),
        intent=intent("HOLD"),
        activation_close={"date": "2026-05-02", "close": 10.9},
        next_bar={"date": "2026-05-04", "open": 10.8, "high": 11.0, "low": 10.4, "close": 10.6, "volume": 10000},
        settlement_prices=[
            {"date": "2026-05-04", "close": 10.9},
            {"date": "2026-05-06", "close": 10.3},
            {"date": "2026-05-11", "close": 9.9},
        ],
        persist_conn=conn,
    )

    assert result.condition_status == "ACTIVATED"
    assert result.paper_fill is None
    assert result.score.lesson_candidate is True
    assert result.case_stored is True

    assert conn.execute("SELECT COUNT(*) FROM ai_rebalance_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_rebalance_intents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_stop_reduce_scores").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_case_memory").fetchone()[0] == 1
    row = conn.execute("SELECT settlement_prices_json FROM ai_stop_reduce_scores").fetchone()
    assert "2026-05-11" in row["settlement_prices_json"]


def test_shadow_sample_reduce_maps_to_paper_fill_and_skips_case_for_good_score():
    conn = make_conn()

    result = run_stop_reduce_shadow_sample(
        run_id="stop_reduce_run_2",
        account=account(),
        intent=intent("REDUCE"),
        activation_close={"date": "2026-05-02", "close": 10.9},
        next_bar={"date": "2026-05-04", "open": 10.8, "high": 11.0, "low": 10.4, "close": 10.6, "volume": 10000},
        settlement_prices=[
            {"date": "2026-05-04", "close": 10.9},
            {"date": "2026-05-06", "close": 10.3},
            {"date": "2026-05-11", "close": 9.9},
        ],
        persist_conn=conn,
    )

    assert result.paper_fill is not None
    assert result.paper_fill.status == "FILLED"
    assert result.score.lesson_candidate is False
    assert result.case_stored is False
    assert conn.execute("SELECT COUNT(*) FROM paper_intents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_case_memory").fetchone()[0] == 0


def test_shadow_sample_cancelled_does_not_create_paper_fill():
    result = run_stop_reduce_shadow_sample(
        run_id="stop_reduce_run_3",
        account=account(),
        intent=intent("REDUCE"),
        activation_close={"date": "2026-05-02", "close": 13.2},
        next_bar={"date": "2026-05-04", "open": 13.1, "high": 13.5, "low": 12.9, "close": 13.3, "volume": 10000},
        settlement_prices=[{"date": "2026-05-04", "close": 13.3}],
    )

    assert result.condition_status == "CANCELLED"
    assert result.paper_fill is None
    assert "CONDITION_CANCELLED" in result.score.tags


def test_load_daily_settlement_prices_uses_day_freq():
    calls = []

    def fake_loader(symbol, freq, start_date=None, limit=5):
        calls.append((symbol, freq, start_date, limit))
        return [
            {"date": "2026-05-04", "close": 10.9},
            {"date": "2026-05-05", "close": 10.7},
        ]

    rows = load_daily_settlement_prices("sh.603893", start_date="2026-05-04", limit=2, kline_loader=fake_loader)

    assert calls == [("sh.603893", "day", "2026-05-04", 2)]
    assert rows == [
        {"date": "2026-05-04", "close": 10.9},
        {"date": "2026-05-05", "close": 10.7},
    ]


def test_build_historical_sample_slices_as_of_execution_and_future_settlement():
    sample = build_historical_stop_reduce_sample(
        run_id="hist_run_1",
        account=account(),
        intent=intent("REDUCE"),
        as_of_date="2026-05-02",
        daily_rows=[
            {"date": "2026-04-30", "open": 11.6, "high": 11.8, "low": 11.3, "close": 11.4, "volume": 10000},
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 11000},
            {"date": "2026-05-04", "open": 10.8, "high": 11.0, "low": 10.4, "close": 10.6, "volume": 12000},
            {"date": "2026-05-05", "open": 10.5, "high": 10.7, "low": 10.0, "close": 10.2, "volume": 13000},
            {"date": "2026-05-06", "open": 10.1, "high": 10.3, "low": 9.8, "close": 9.9, "volume": 14000},
        ],
        settlement_limit=2,
    )

    assert sample.activation_close == {"date": "2026-05-02", "close": 10.9}
    assert sample.next_bar["date"] == "2026-05-04"
    assert sample.next_bar["open"] == 10.8
    assert sample.settlement_prices == [
        {"date": "2026-05-04", "close": 10.6},
        {"date": "2026-05-05", "close": 10.2},
    ]
    assert "2026-05-04" not in sample.intent.idempotency_key


def test_build_historical_sample_uses_last_bar_before_as_of_without_peeking():
    sample = build_historical_stop_reduce_sample(
        run_id="hist_run_2",
        account=account(),
        intent=intent("REDUCE"),
        as_of_date="2026-05-03",
        daily_rows=[
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 11000},
            {"date": "2026-05-04", "open": 9.5, "high": 9.8, "low": 9.1, "close": 9.2, "volume": 12000},
        ],
    )

    assert sample.activation_close == {"date": "2026-05-02", "close": 10.9}
    assert sample.next_bar["date"] == "2026-05-04"
    assert sample.settlement_prices == [{"date": "2026-05-04", "close": 9.2}]


def test_build_historical_samples_builds_intent_before_loading_future_rows():
    calls = []

    def future_loader(symbol, freq, limit=260):
        calls.append((symbol, freq, limit))
        return [
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 11000},
            {"date": "2026-05-04", "open": 99.0, "high": 100.0, "low": 98.0, "close": 99.0, "volume": 12000},
        ]

    result = build_historical_stop_reduce_samples(
        [
            StopReduceHistoricalCandidate(
                user_id=1,
                symbol="sh.603893",
                account=account(),
                response=historical_response(current_price=10.8, stop_price=11.0),
                as_of="2026-05-02T10:30:00+08:00",
            )
        ],
        settlement_limit=1,
        kline_loader=future_loader,
    )

    assert calls == [("sh.603893", "day", 260)]
    assert len(result.samples) == 1
    sample = result.samples[0]
    assert sample.intent.action == "REDUCE"
    assert sample.intent.target_weight_pct == 6.0
    assert sample.intent.conditions.activate_if[0].value == 11.0
    assert sample.settlement_prices == [{"date": "2026-05-04", "close": 99.0}]
    assert result.skipped == []


def test_build_historical_samples_skips_empty_position_without_loading_klines():
    calls = []
    response = historical_response(current_price=10.8, stop_price=11.0)
    response.position_context.is_holding = False

    result = build_historical_stop_reduce_samples(
        [
            StopReduceHistoricalCandidate(
                user_id=1,
                symbol="sh.603893",
                account=account(),
                response=response,
                as_of="2026-05-02T10:30:00+08:00",
            )
        ],
        kline_loader=lambda *args, **kwargs: calls.append((args, kwargs)) or [],
    )

    assert result.samples == []
    assert result.skipped[0]["reason"] == "NO_INTENT"
    assert calls == []


def test_load_ai_reasoning_rows_and_restore_candidate_from_persisted_run():
    conn = make_conn()
    transcript = StructureTranscript(
        symbol="sh.603893",
        mode="HOLDING",
        generated_at="2026-05-02T10:30:00+08:00",
        fingerprint_version="test.v1",
        structure_fingerprint="fp_stop_reduce",
        position_context=historical_response().position_context,
    )
    output = AIReasoningOutput(
        raw_reasoning_md="raw",
        coach_filtered_md="跌破结构线后减仓。仅供参考，不构成投资建议。",
    )
    gate = GateResult(status="PASS", score=88, violations=[])
    conn.execute(
        """
        INSERT INTO ai_reasoning_runs (
            id, user_id, symbol, mode, created_at, prompt_version, model_name,
            structure_fingerprint, transcript_json, memory_context_json,
            ai_output_json, gate_result_json, gate_status, model_route_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            777,
            1,
            "sh.603893",
            "HOLDING",
            "2026-05-02T10:30:00+08:00",
            "test",
            "mock",
            "fp_stop_reduce",
            transcript.model_dump_json(),
            "{}",
            output.model_dump_json(),
            gate.model_dump_json(),
            "PASS",
            json.dumps({}),
        ),
    )

    rows = load_ai_reasoning_rows_for_stop_reduce(conn, user_id=1, symbol="sh.603893")
    candidate = candidate_from_ai_reasoning_row(rows[0], account=account())

    assert len(rows) == 1
    assert candidate.user_id == 1
    assert candidate.symbol == "sh.603893"
    assert candidate.as_of == "2026-05-02T10:30:00+08:00"
    assert candidate.response.run_id == 777
    assert candidate.response.position_context.nearest_risk_line["value"] == 11.0


def test_load_ai_reasoning_rows_accepts_symbol_variants_and_legacy_output():
    conn = make_conn()
    transcript = StructureTranscript(
        symbol="sh688008",
        mode="HOLDING",
        generated_at="2026-05-02T10:30:00+08:00",
        fingerprint_version="test.v1",
        structure_fingerprint="fp_stop_reduce",
        position_context=historical_response().position_context,
    )
    conn.execute(
        """
        INSERT INTO ai_reasoning_runs (
            id, user_id, symbol, mode, created_at, prompt_version, model_name,
            structure_fingerprint, transcript_json, memory_context_json,
            ai_output_json, gate_result_json, gate_status, model_route_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            778,
            1,
            "sh688008",
            "HOLDING",
            "2026-05-02T10:30:00+08:00",
            "test",
            "mock",
            "fp_stop_reduce",
            transcript.model_dump_json(),
            "{}",
            json.dumps({"diagnosis": "旧格式诊断", "coach_talk": "旧格式教练话术", "disclaimer": "仅供参考，不构成投资建议"}, ensure_ascii=False),
            GateResult(status="PASS", score=88, violations=[]).model_dump_json(),
            "PASS",
            json.dumps({}),
        ),
    )

    rows = load_ai_reasoning_rows_for_stop_reduce(conn, user_id=1, symbol="sh.688008")
    candidate = candidate_from_ai_reasoning_row(rows[0], account=account())

    assert len(rows) == 1
    assert candidate.symbol == "sh688008"
    assert candidate.response.coach_filtered_md == "旧格式教练话术"


def test_build_shadow_account_from_reasoning_row_uses_response_when_no_live_position(monkeypatch):
    monkeypatch.setattr("server.scripts.stop_reduce_shadow_loop._load_current_position", lambda **kwargs: None)
    row = {
        "id": 777,
        "user_id": 1,
        "symbol": "sh.603893",
        "created_at": "2026-05-02T10:30:00+08:00",
        "transcript_json": StructureTranscript(
            symbol="sh.603893",
            mode="HOLDING",
            generated_at="2026-05-02T10:30:00+08:00",
            fingerprint_version="test.v1",
            structure_fingerprint="fp_stop_reduce",
            position_context=historical_response().position_context,
        ).model_dump_json(),
        "ai_output_json": AIReasoningOutput(coach_filtered_md="仅供参考，不构成投资建议。").model_dump_json(),
        "gate_result_json": GateResult(status="PASS", score=88, violations=[]).model_dump_json(),
        "model_route_json": "{}",
    }

    paper = build_shadow_account_from_reasoning_row(row)

    assert paper.paper_account_id == "paper_stop_reduce_1_sh603893_777"
    assert paper.positions["sh.603893"].total_qty == 1000
    assert paper.positions["sh.603893"].avg_cost == 12.5
    assert paper.positions["sh.603893"].last_price == 10.8


def test_parse_args_builds_cli_config():
    config = parse_args([
        "--user-id",
        "1",
        "--symbol",
        "sh.603893",
        "--limit",
        "5",
        "--persist",
        "--fundamental-verdict",
        "回避",
        "--output",
        "report.md",
    ])

    assert config == StopReduceCliConfig(
        user_id=1,
        symbol="sh.603893",
        limit=5,
        persist=True,
        enqueue_pending=False,
        report=True,
        settlement_limit=5,
        daily_window_limit=260,
        fundamental_verdict="回避",
        initial_cash=100000.0,
        protected_base_qty=0,
        output_path="report.md",
    )


def test_cli_runs_historical_rows_and_renders_report(monkeypatch):
    conn = make_conn()
    transcript = StructureTranscript(
        symbol="sh.603893",
        mode="HOLDING",
        generated_at="2026-05-02T10:30:00+08:00",
        fingerprint_version="test.v1",
        structure_fingerprint="fp_stop_reduce",
        position_context=historical_response().position_context,
    )
    output = AIReasoningOutput(coach_filtered_md="跌破结构线后减仓。仅供参考，不构成投资建议。")
    gate = GateResult(status="PASS", score=88, violations=[])
    conn.execute(
        """
        INSERT INTO ai_reasoning_runs (
            id, user_id, symbol, mode, created_at, prompt_version, model_name,
            structure_fingerprint, transcript_json, memory_context_json,
            ai_output_json, gate_result_json, gate_status, model_route_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            888,
            1,
            "sh.603893",
            "HOLDING",
            "2026-05-02T10:30:00+08:00",
            "test",
            "mock",
            "fp_stop_reduce",
            transcript.model_dump_json(),
            "{}",
            output.model_dump_json(),
            gate.model_dump_json(),
            "PASS",
            json.dumps({}),
        ),
    )
    monkeypatch.setattr("server.scripts.stop_reduce_shadow_loop.get_connection", lambda: conn)
    monkeypatch.setattr("server.scripts.stop_reduce_shadow_loop._load_current_position", lambda **kwargs: None)
    monkeypatch.setattr(
        "server.scripts.stop_reduce_shadow_loop.query_klines",
        lambda symbol, freq, limit=260: [
            {"date": "2026-05-02", "open": 11.2, "high": 11.5, "low": 10.8, "close": 10.9, "volume": 11000},
            {"date": "2026-05-04", "open": 10.8, "high": 11.0, "low": 10.4, "close": 10.6, "volume": 12000},
            {"date": "2026-05-05", "open": 10.5, "high": 10.7, "low": 10.0, "close": 10.2, "volume": 13000},
        ],
    )

    _, report, markdown = run_stop_reduce_shadow_cli(StopReduceCliConfig(user_id=1, symbol="sh.603893", limit=5))

    assert report["total_samples"] == 1
    assert report["activated_samples"] == 1
    assert report["filled_samples"] == 1
    assert "Total samples: 1" in markdown
    assert "AI Stop/Reduce Shadow Training Report" in markdown


def test_enqueue_pending_persists_runs_and_intents_without_scores():
    conn = make_conn()
    sample = StopReduceShadowSample(
        run_id="pending_run_1",
        account=account(),
        intent=intent("REDUCE", suffix="pending"),
        activation_close={"date": "2026-05-02", "close": 10.9},
        next_bar=None,
        settlement_prices=[],
    )

    count = enqueue_stop_reduce_pending_intents(conn, [sample])

    assert count == 1
    assert conn.execute("SELECT status FROM ai_rebalance_runs WHERE run_id='pending_run_1'").fetchone()[0] == "WAITING_SETTLEMENT"
    assert conn.execute("SELECT COUNT(*) FROM ai_rebalance_intents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_stop_reduce_scores").fetchone()[0] == 0


def test_shadow_batch_aggregates_calibration_and_keeps_memory_sparse():
    conn = make_conn()
    samples = [
        StopReduceShadowSample(
            run_id="batch_run_1",
            account=account(),
            intent=intent("HOLD", suffix="a"),
            activation_close={"date": "2026-05-02", "close": 10.9},
            next_bar=None,
            settlement_prices=[
                {"date": "2026-05-04", "close": 10.9},
                {"date": "2026-05-06", "close": 10.3},
                {"date": "2026-05-11", "close": 9.9},
            ],
        ),
        StopReduceShadowSample(
            run_id="batch_run_2",
            account=account(),
            intent=intent("REDUCE", suffix="b"),
            activation_close={"date": "2026-05-02", "close": 10.9},
            next_bar={
                "date": "2026-05-04",
                "open": 10.8,
                "high": 11.0,
                "low": 10.4,
                "close": 10.6,
                "volume": 10000,
            },
            settlement_prices=[
                {"date": "2026-05-04", "close": 10.9},
                {"date": "2026-05-06", "close": 10.3},
                {"date": "2026-05-11", "close": 9.9},
            ],
        ),
    ]

    result = run_stop_reduce_shadow_batch(samples, persist_conn=conn)
    key = "holding:loss:structure_breakdown:near_stop"

    assert len(result.results) == 2
    assert result.calibration[key]["total_count"] == 2
    assert result.calibration[key]["mistake_count"] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_case_memory").fetchone()[0] == 1
    row = conn.execute("SELECT total_count, mistake_count FROM ai_calibration_stats WHERE calibration_key=?", (key,)).fetchone()
    assert dict(row) == {"total_count": 2, "mistake_count": 1}
    assert "过去 2 次" in result.summaries[key]
    assert "最近一次错误" in result.summaries[key]


def test_training_report_summarizes_batch_and_skipped_samples():
    batch = run_stop_reduce_shadow_batch(
        [
            StopReduceShadowSample(
                run_id="report_run_1",
                account=account(),
                intent=intent("HOLD", suffix="report_a"),
                activation_close={"date": "2026-05-02", "close": 10.9},
                next_bar=None,
                settlement_prices=[
                    {"date": "2026-05-04", "close": 10.9},
                    {"date": "2026-05-06", "close": 10.3},
                    {"date": "2026-05-11", "close": 9.9},
                ],
            ),
            StopReduceShadowSample(
                run_id="report_run_2",
                account=account(),
                intent=intent("REDUCE", suffix="report_b"),
                activation_close={"date": "2026-05-02", "close": 10.9},
                next_bar={
                    "date": "2026-05-04",
                    "open": 10.8,
                    "high": 11.0,
                    "low": 10.4,
                    "close": 10.6,
                    "volume": 10000,
                },
                settlement_prices=[
                    {"date": "2026-05-04", "close": 10.9},
                    {"date": "2026-05-06", "close": 10.3},
                    {"date": "2026-05-11", "close": 9.9},
                ],
            ),
        ]
    )

    report = build_stop_reduce_training_report(batch, skipped=[{"reason": "NO_INTENT"}])
    rendered = render_stop_reduce_training_report(report)

    assert report["total_samples"] == 2
    assert report["skipped_samples"] == 1
    assert report["activated_samples"] == 2
    assert report["filled_samples"] == 1
    assert report["lesson_candidates"] == 1
    assert report["skipped_reasons"] == {"NO_INTENT": 1}
    assert "AI Stop/Reduce Shadow Training Report" in rendered
    assert "- REDUCE_WAS_CORRECT: 1" in rendered
    assert "仅供参考" in rendered


def test_render_persisted_calibration_summary_handles_empty_stats():
    conn = make_conn()

    summary = render_persisted_calibration_summary(conn, user_id=1, case_key="missing")

    assert "过去 0 次" in summary


def historical_response(current_price=10.8, stop_price=11.0):
    return AIReasoningResponse(
        gate_status="PASS",
        gate_score=88,
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
            coach_focus="先处理风险",
        ),
        run_id=777,
    )
