#!/usr/bin/env python3
"""Offline runner primitives for AI stop/reduce shadow training V1.

This script intentionally starts with one-sample deterministic execution. Batch
selection can be layered on top after the contract proves useful.
"""

from __future__ import annotations

import sys
import json
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.database import get_connection
from server.db.kline_lake import query_klines
from server.engines.ai_native.schemas import (
    AllowedPrice,
    AIReasoningOutput,
    AIReasoningResponse,
    GateResult,
    GateViolation,
    ModelRoute,
    StructureTranscript,
)
from server.engines.ai_native.stop_reduce_adapter import build_stop_reduce_intent_from_ai_response
from server.engines.ai_native.stop_reduce_store import (
    load_calibration_stats,
    load_latest_case,
    save_case_memory,
    save_rebalance_intent,
    save_rebalance_run,
    save_stop_reduce_score,
    upsert_calibration_stats,
)
from server.engines.ai_native.stop_reduce_training import (
    ConditionStatus,
    FundamentalVerdict,
    RebalanceIntent,
    StopReduceScore,
    evaluate_stop_reduce_conditions,
    map_stop_reduce_to_paper_intent,
    render_calibration_summary,
    score_stop_reduce_outcome,
)
from server.engines.execution.paper_adapter import simulate_next_bar_fill
from server.engines.execution.paper_models import PaperAccount, PaperFill, PaperKline, PaperPosition, PaperRiskConfig
from server.engines.execution.paper_store import save_paper_account, save_paper_fill, save_paper_intent


@dataclass(frozen=True)
class StopReduceShadowResult:
    run_id: str
    condition_status: ConditionStatus
    paper_fill: PaperFill | None
    score: StopReduceScore
    case_stored: bool
    next_account: PaperAccount
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StopReduceShadowSample:
    run_id: str
    account: PaperAccount
    intent: RebalanceIntent
    activation_close: dict[str, Any] | None
    next_bar: PaperKline | dict[str, Any] | None
    settlement_prices: list[dict[str, Any]]
    case_key: str = "holding:loss:structure_breakdown:near_stop"


@dataclass(frozen=True)
class StopReduceShadowBatchResult:
    results: list[StopReduceShadowResult]
    calibration: dict[str, dict[str, Any]]
    summaries: dict[str, str]


@dataclass(frozen=True)
class StopReduceHistoricalCandidate:
    user_id: int
    symbol: str
    account: PaperAccount
    response: AIReasoningResponse
    as_of: str
    fundamental_verdict: FundamentalVerdict = "中性"
    case_key: str = "holding:loss:structure_breakdown:near_stop"


@dataclass(frozen=True)
class StopReduceHistoricalBuildResult:
    samples: list[StopReduceShadowSample]
    skipped: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class StopReduceCliConfig:
    user_id: int
    symbol: str | None = None
    limit: int = 20
    persist: bool = False
    enqueue_pending: bool = False
    report: bool = True
    settlement_limit: int = 5
    daily_window_limit: int = 260
    fundamental_verdict: FundamentalVerdict = "中性"
    initial_cash: float = 100000.0
    protected_base_qty: int = 0
    output_path: str = ""


def run_stop_reduce_shadow_sample(
    *,
    run_id: str,
    account: PaperAccount,
    intent: RebalanceIntent,
    activation_close: dict[str, Any] | None,
    next_bar: PaperKline | dict[str, Any] | None,
    settlement_prices: list[dict[str, Any]],
    risk_config: PaperRiskConfig | None = None,
    case_key: str = "holding:loss:structure_breakdown:near_stop",
    persist_conn=None,
) -> StopReduceShadowResult:
    """Run one stop/reduce intent through condition, paper execution, and score."""
    risk_config = risk_config or PaperRiskConfig()
    condition_status = evaluate_stop_reduce_conditions(
        intent.conditions,
        activation_close,
        today=str((activation_close or {}).get("date") or "")[:10],
    )
    current_account = account
    paper_intent = None
    fill = None
    process_violations: list[str] = []

    if condition_status == "ACTIVATED":
        paper_intent = map_stop_reduce_to_paper_intent(
            current_account,
            intent,
            account_value=_account_value(current_account),
            created_at=intent.as_of,
        )
        if paper_intent and next_bar is not None:
            current_account, fill = simulate_next_bar_fill(current_account, paper_intent, next_bar, risk_config)
            if fill.status != "FILLED":
                process_violations.append(f"PAPER_FILL_{fill.reason or fill.status}")
        elif intent.action in {"REDUCE", "EXIT"}:
            process_violations.append("PAPER_INTENT_NOT_CREATED")
    elif condition_status in {"CANCELLED", "DATA_MISSING", "EXPIRED"}:
        process_violations.append(f"CONDITION_{condition_status}")

    action_taken = intent.action if fill and fill.status == "FILLED" else "HOLD"
    entry_price = _entry_price(fill, activation_close, account, intent.symbol)
    score = score_stop_reduce_outcome(
        intent,
        action_taken=action_taken,
        entry_price=entry_price,
        settlement_prices=settlement_prices,
        stop_broken=condition_status == "ACTIVATED",
        process_violations=process_violations,
    )
    case_stored = False
    if persist_conn is not None:
        _persist_result(
            persist_conn,
            run_id=run_id,
            start_account=account,
            next_account=current_account,
            intent=intent,
            paper_intent=paper_intent,
            fill=fill,
            score=score,
            case_key=case_key,
        )
        case_stored = _maybe_store_case(persist_conn, intent=intent, score=score, case_key=case_key)
        persist_conn.commit()

    return StopReduceShadowResult(
        run_id=run_id,
        condition_status=condition_status,
        paper_fill=fill,
        score=score,
        case_stored=case_stored,
        next_account=current_account,
        summary={
            "condition_status": condition_status,
            "fill_status": fill.status if fill else "NO_FILL",
            "final_score": score.final_score,
            "lesson_candidate": score.lesson_candidate,
        },
    )


def run_stop_reduce_shadow_batch(
    samples: list[StopReduceShadowSample],
    *,
    risk_config: PaperRiskConfig | None = None,
    persist_conn=None,
) -> StopReduceShadowBatchResult:
    """Run many deterministic samples and aggregate calibration by case_key."""
    results = []
    buckets: dict[str, list[StopReduceShadowResult]] = {}
    for sample in samples:
        result = run_stop_reduce_shadow_sample(
            run_id=sample.run_id,
            account=sample.account,
            intent=sample.intent,
            activation_close=sample.activation_close,
            next_bar=sample.next_bar,
            settlement_prices=sample.settlement_prices,
            risk_config=risk_config,
            case_key=sample.case_key,
            persist_conn=persist_conn,
        )
        results.append(result)
        buckets.setdefault(sample.case_key, []).append(result)

    calibration = {
        case_key: _aggregate_bucket(case_key, bucket, user_id=samples[0].intent.user_id if samples else 0)
        for case_key, bucket in buckets.items()
    }
    if persist_conn is not None:
        for case_key, stats in calibration.items():
            upsert_calibration_stats(persist_conn, calibration_key=case_key, **stats)
        persist_conn.commit()

    summaries = {}
    if persist_conn is not None:
        for case_key in calibration:
            summaries[case_key] = render_persisted_calibration_summary(
                persist_conn,
                user_id=samples[0].intent.user_id if samples else 0,
                case_key=case_key,
            )
    else:
        summaries = {
            case_key: render_calibration_summary(stats)
            for case_key, stats in calibration.items()
        }
    return StopReduceShadowBatchResult(results=results, calibration=calibration, summaries=summaries)


def build_stop_reduce_training_report(
    batch: StopReduceShadowBatchResult,
    *,
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a compact report for one offline stop/reduce training batch."""
    skipped = skipped or []
    results = batch.results
    total = len(results)
    fills = [result for result in results if result.paper_fill and result.paper_fill.status == "FILLED"]
    lessons = [result for result in results if result.score.lesson_candidate]
    scores = [result.score.final_score for result in results]
    condition_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}

    for result in results:
        condition_counts[result.condition_status] = condition_counts.get(result.condition_status, 0) + 1
        action = result.score.tags[0] if result.score.tags else "UNKNOWN"
        action_counts[action] = action_counts.get(action, 0) + 1
        for tag in result.score.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "total_samples": total,
        "skipped_samples": len(skipped),
        "activated_samples": condition_counts.get("ACTIVATED", 0),
        "filled_samples": len(fills),
        "lesson_candidates": len(lessons),
        "case_memory_writes": sum(1 for result in results if result.case_stored),
        "average_final_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "condition_counts": condition_counts,
        "primary_outcome_counts": action_counts,
        "tag_counts": tag_counts,
        "skipped_reasons": _count_by(skipped, "reason"),
        "calibration": batch.calibration,
        "summaries": batch.summaries,
    }


def render_stop_reduce_training_report(report: dict[str, Any]) -> str:
    """Render the report as Markdown for console, docs, or PR notes."""
    lines = [
        "# AI Stop/Reduce Shadow Training Report",
        "",
        "交易相关内容仅供参考，不构成投资建议。",
        "",
        "## Summary",
        "",
        f"- Total samples: {report.get('total_samples', 0)}",
        f"- Skipped samples: {report.get('skipped_samples', 0)}",
        f"- Activated samples: {report.get('activated_samples', 0)}",
        f"- Filled samples: {report.get('filled_samples', 0)}",
        f"- Lesson candidates: {report.get('lesson_candidates', 0)}",
        f"- Case memory writes: {report.get('case_memory_writes', 0)}",
        f"- Average final score: {report.get('average_final_score', 0.0)}",
        "",
        "## Conditions",
        "",
        *_render_counts(report.get("condition_counts") or {}),
        "",
        "## Outcomes",
        "",
        *_render_counts(report.get("primary_outcome_counts") or {}),
        "",
        "## Tags",
        "",
        *_render_counts(report.get("tag_counts") or {}),
    ]
    skipped = report.get("skipped_reasons") or {}
    if skipped:
        lines.extend(["", "## Skipped", "", *_render_counts(skipped)])
    summaries = report.get("summaries") or {}
    if summaries:
        lines.extend(["", "## Calibration", ""])
        for key, summary in summaries.items():
            lines.extend([f"### {key}", "", str(summary), ""])
    return "\n".join(lines).rstrip() + "\n"


def render_persisted_calibration_summary(conn, *, user_id: int, case_key: str) -> str:
    stats = load_calibration_stats(conn, case_key)
    latest = load_latest_case(conn, user_id=user_id, case_key=case_key)
    return render_calibration_summary(stats, latest)


def load_daily_settlement_prices(
    symbol: str,
    *,
    start_date: str,
    limit: int = 5,
    kline_loader=query_klines,
) -> list[dict[str, Any]]:
    """Load closed daily bars used for T+N settlement."""
    rows = kline_loader(symbol, "day", start_date=start_date, limit=max(1, limit))
    return [{"date": row.get("date"), "close": row.get("close")} for row in rows[:limit]]


def build_historical_stop_reduce_sample(
    *,
    run_id: str,
    account: PaperAccount,
    intent: RebalanceIntent,
    daily_rows: list[dict[str, Any]],
    as_of_date: str,
    settlement_limit: int = 5,
    case_key: str = "holding:loss:structure_breakdown:near_stop",
) -> StopReduceShadowSample:
    """Slice historical bars into as-of input, next execution bar, and future scoring.

    The intent must already be built from as-of Radar data. This helper only
    attaches price rows for replay, keeping future settlement data out of the
    intent-generation path.
    """
    if not daily_rows:
        raise ValueError("daily_rows is required")
    ordered = sorted(daily_rows, key=lambda row: str(row.get("date") or ""))
    as_of_index = _as_of_bar_index(ordered, as_of_date)
    if as_of_index < 0:
        raise ValueError("no daily row at or before as_of_date")

    activation_row = ordered[as_of_index]
    next_row = ordered[as_of_index + 1] if as_of_index + 1 < len(ordered) else None
    settlement_rows = ordered[as_of_index + 1 : as_of_index + 1 + max(1, settlement_limit)]
    return StopReduceShadowSample(
        run_id=run_id,
        account=account,
        intent=intent,
        activation_close={"date": activation_row.get("date"), "close": activation_row.get("close")},
        next_bar=next_row,
        settlement_prices=[
            {"date": row.get("date"), "close": row.get("close")}
            for row in settlement_rows
        ],
        case_key=case_key,
    )


def build_historical_stop_reduce_samples(
    candidates: list[StopReduceHistoricalCandidate],
    *,
    settlement_limit: int = 5,
    daily_window_limit: int = 260,
    kline_loader=None,
) -> StopReduceHistoricalBuildResult:
    """Build replay samples from historical AI responses.

    Intent construction happens before loading daily rows for settlement, so a
    future price path cannot influence the stop/reduce decision.
    """
    samples: list[StopReduceShadowSample] = []
    skipped: list[dict[str, Any]] = []
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    loader = kline_loader or query_klines

    for candidate in candidates:
        if candidate.response.position_context is None or not candidate.response.position_context.is_holding:
            skipped.append(_skip(candidate, "NO_INTENT"))
            continue
        rows = rows_by_symbol.get(candidate.symbol)
        if rows is None:
            rows = _load_daily_rows_for_symbol(candidate.symbol, loader=loader, limit=max(2, daily_window_limit))
            rows_by_symbol[candidate.symbol] = rows
        if not rows:
            skipped.append(_skip(candidate, "NO_DAILY_ROWS"))
            continue
        as_of_index = _as_of_bar_index(rows, candidate.as_of[:10])
        if as_of_index < 0:
            skipped.append(_skip(candidate, "NO_AS_OF_DAILY_ROW"))
            continue
        activation_close = {"date": rows[as_of_index].get("date"), "close": rows[as_of_index].get("close")}
        response = _response_with_historical_as_of(
            candidate.response,
            account=candidate.account,
            symbol=candidate.symbol,
            activation_close=activation_close,
        )
        intent = build_stop_reduce_intent_from_ai_response(
            user_id=candidate.user_id,
            symbol=candidate.symbol,
            response=response,
            as_of=candidate.as_of,
            fundamental_verdict=candidate.fundamental_verdict,
        )
        if intent is None:
            skipped.append(_skip(candidate, "NO_INTENT"))
            continue

        try:
            sample = build_historical_stop_reduce_sample(
                run_id=f"hist_stop_reduce:{candidate.user_id}:{candidate.symbol}:{candidate.as_of}:{intent.evidence_refs.get('technical_run_id')}",
                account=candidate.account,
                intent=intent,
                daily_rows=rows,
                as_of_date=candidate.as_of[:10],
                settlement_limit=settlement_limit,
                case_key=candidate.case_key,
            )
        except ValueError as exc:
            skipped.append(_skip(candidate, str(exc), intent_id=intent.intent_id))
            continue
        samples.append(sample)

    return StopReduceHistoricalBuildResult(samples=samples, skipped=skipped)


def run_stop_reduce_shadow_cli(config: StopReduceCliConfig) -> tuple[StopReduceShadowBatchResult, dict[str, Any], str]:
    """Run the offline shadow loop from persisted AI Native Radar rows."""
    if config.persist and config.enqueue_pending:
        raise ValueError("--persist and --enqueue-pending cannot be used together")
    conn = get_connection()
    try:
        rows = load_ai_reasoning_rows_for_stop_reduce(
            conn,
            user_id=config.user_id,
            symbol=config.symbol,
            limit=config.limit,
        )
        candidates = [
            candidate_from_ai_reasoning_row(
                row,
                account=build_shadow_account_from_reasoning_row(
                    row,
                    initial_cash=config.initial_cash,
                    protected_base_qty=config.protected_base_qty,
                ),
                fundamental_verdict=config.fundamental_verdict,
            )
            for row in rows
        ]
        built = build_historical_stop_reduce_samples(
            candidates,
            settlement_limit=config.settlement_limit,
            daily_window_limit=config.daily_window_limit,
        )
        if config.enqueue_pending:
            enqueue_stop_reduce_pending_intents(conn, built.samples)
        batch = run_stop_reduce_shadow_batch(
            built.samples,
            persist_conn=conn if config.persist else None,
        )
        report = build_stop_reduce_training_report(batch, skipped=built.skipped)
        markdown = render_stop_reduce_training_report(report) if config.report else ""
        if config.output_path and markdown:
            Path(config.output_path).write_text(markdown, encoding="utf-8")
        return batch, report, markdown
    finally:
        conn.close()


def load_ai_reasoning_rows_for_stop_reduce(
    conn,
    *,
    user_id: int,
    symbol: str | None = None,
    limit: int = 50,
    mode: str = "HOLDING",
) -> list[dict[str, Any]]:
    """Load persisted AI Native runs that can seed offline stop/reduce replay."""
    limit = max(1, min(limit, 200))
    clauses = ["user_id = ?", "mode = ?"]
    params: list[Any] = [user_id, mode]
    if symbol:
        variants = _symbol_variants(symbol)
        clauses.append(f"symbol IN ({','.join('?' for _ in variants)})")
        params.extend(variants)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT id, user_id, symbol, mode, created_at,
               transcript_json, ai_output_json, gate_result_json, model_route_json
          FROM ai_reasoning_runs
         WHERE {' AND '.join(clauses)}
         ORDER BY created_at ASC, id ASC
         LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def build_shadow_account_from_reasoning_row(
    row: dict[str, Any],
    *,
    initial_cash: float = 100000.0,
    protected_base_qty: int = 0,
) -> PaperAccount:
    """Create a paper account from current DB position or persisted response context."""
    user_id = int(row["user_id"])
    symbol = str(row["symbol"])
    account_id = f"paper_stop_reduce_{user_id}_{symbol.replace('.', '')}_{row['id']}"
    live = _load_current_position(user_id=user_id, symbol=symbol)
    if live:
        return PaperAccount(
            paper_account_id=account_id,
            user_id=user_id,
            cash=initial_cash,
            positions={
                symbol: PaperPosition(
                    symbol=symbol,
                    total_qty=max(0, int(live.get("quantity") or 0)),
                    available_qty=max(0, int(live.get("quantity") or 0)),
                    protected_base_qty=protected_base_qty,
                    avg_cost=_num(live.get("avg_cost")),
                    last_price=_num(live.get("current_price")) or _num(live.get("avg_cost")),
                )
            },
        )

    response = _response_from_ai_reasoning_row(row)
    position = response.position_context
    quantity = int(position.quantity or 0) if position else 0
    avg_cost = _num(position.avg_cost or position.cost) if position else 0.0
    last_price = _num(position.current_price) if position else 0.0
    return PaperAccount(
        paper_account_id=account_id,
        user_id=user_id,
        cash=initial_cash,
        positions={
            symbol: PaperPosition(
                symbol=symbol,
                total_qty=max(0, quantity),
                available_qty=max(0, quantity),
                protected_base_qty=protected_base_qty,
                avg_cost=avg_cost or last_price,
                last_price=last_price or avg_cost,
            )
        } if quantity > 0 else {},
    )


def candidate_from_ai_reasoning_row(
    row: dict[str, Any],
    *,
    account: PaperAccount,
    fundamental_verdict: FundamentalVerdict = "中性",
    case_key: str = "holding:loss:structure_breakdown:near_stop",
) -> StopReduceHistoricalCandidate:
    """Restore one persisted AI Native run into a historical stop/reduce candidate."""
    response = _response_from_ai_reasoning_row(row)
    response.run_id = int(row["id"])
    return StopReduceHistoricalCandidate(
        user_id=int(row["user_id"]),
        symbol=str(row["symbol"]),
        account=account,
        response=response,
        as_of=str(row["created_at"]),
        fundamental_verdict=fundamental_verdict,
        case_key=case_key,
    )


def enqueue_stop_reduce_pending_intents(conn, samples: list[StopReduceShadowSample]) -> int:
    """Persist generated intents without scoring them, so settlement worker can pick them up."""
    count = 0
    for sample in samples:
        save_rebalance_run(
            conn,
            run_id=sample.run_id,
            user_id=sample.intent.user_id,
            symbol=sample.intent.symbol,
            as_of=sample.intent.as_of,
            radar_run_id=_optional_int(sample.intent.evidence_refs.get("technical_run_id")),
            technical_view=sample.intent.reason,
            status="WAITING_SETTLEMENT",
        )
        save_rebalance_intent(conn, sample.intent, run_id=sample.run_id)
        count += 1
    conn.commit()
    return count


def parse_args(argv: list[str] | None = None) -> StopReduceCliConfig:
    parser = argparse.ArgumentParser(description="Run AI stop/reduce shadow training over historical AI Native Radar runs")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--enqueue-pending", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--settlement-limit", type=int, default=5)
    parser.add_argument("--daily-window-limit", type=int, default=260)
    parser.add_argument("--fundamental-verdict", choices=["支持", "中性", "回避"], default="中性")
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    parser.add_argument("--protected-base-qty", type=int, default=0)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    return StopReduceCliConfig(
        user_id=args.user_id,
        symbol=args.symbol,
        limit=args.limit,
        persist=args.persist,
        enqueue_pending=args.enqueue_pending,
        report=not args.no_report,
        settlement_limit=args.settlement_limit,
        daily_window_limit=args.daily_window_limit,
        fundamental_verdict=args.fundamental_verdict,
        initial_cash=args.initial_cash,
        protected_base_qty=args.protected_base_qty,
        output_path=args.output,
    )


def _persist_result(
    conn,
    *,
    run_id: str,
    start_account: PaperAccount,
    next_account: PaperAccount,
    intent: RebalanceIntent,
    paper_intent,
    fill,
    score: StopReduceScore,
    case_key: str,
) -> None:
    save_rebalance_run(
        conn,
        run_id=run_id,
        user_id=intent.user_id,
        symbol=intent.symbol,
        as_of=intent.as_of,
        radar_run_id=_optional_int(intent.evidence_refs.get("technical_run_id")),
        technical_view=intent.reason,
        status="SCORED",
    )
    save_rebalance_intent(conn, intent, run_id=run_id)
    save_paper_account(conn, start_account, metadata={"source": "stop_reduce_shadow_start"})
    if paper_intent is not None:
        save_paper_intent(conn, paper_intent, run_id=None)
    if fill is not None:
        save_paper_fill(conn, fill, account=start_account, run_id=None)
    save_paper_account(conn, next_account, metadata={"source": "stop_reduce_shadow_end"})
    save_stop_reduce_score(conn, score, user_id=intent.user_id, symbol=intent.symbol)
    upsert_calibration_stats(
        conn,
        calibration_key=case_key,
        user_id=intent.user_id,
        total_count=1,
        mistake_count=1 if score.lesson_candidate else 0,
        avg_loss_if_hold_pct=_loss_from_prices(score.settlement_prices),
        latest_mistake_case_id=f"case:{score.intent_id}" if score.lesson_candidate else "",
    )


def _aggregate_bucket(case_key: str, results: list[StopReduceShadowResult], *, user_id: int) -> dict[str, Any]:
    losses = [_loss_from_prices(result.score.settlement_prices) for result in results]
    mistake_results = [result for result in results if result.score.lesson_candidate]
    latest_case_id = f"case:{mistake_results[-1].score.intent_id}" if mistake_results else ""
    return {
        "user_id": user_id,
        "total_count": len(results),
        "mistake_count": len(mistake_results),
        "avg_loss_if_hold_pct": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "avg_benefit_if_reduce_pct": _avg_reduce_benefit(results),
        "latest_mistake_case_id": latest_case_id,
    }


def _avg_reduce_benefit(results: list[StopReduceShadowResult]) -> float:
    benefits = []
    for result in results:
        if "REDUCE_WAS_CORRECT" not in result.score.tags:
            continue
        prices = result.score.settlement_prices
        if not prices:
            continue
        entry = _num(prices[0].get("close"))
        last = _num(prices[-1].get("close"))
        if entry > 0 and last > 0:
            benefits.append(max(0.0, (entry - last) / entry * 100))
    return round(sum(benefits) / len(benefits), 4) if benefits else 0.0


def _maybe_store_case(conn, *, intent: RebalanceIntent, score: StopReduceScore, case_key: str) -> bool:
    if not score.lesson_candidate:
        return False
    mistake_type = next((tag for tag in score.tags if tag.startswith("AI_") or tag.endswith("TOO_EARLY")), "STOP_REDUCE_LESSON")
    save_case_memory(
        conn,
        case_id=f"case:{score.intent_id}",
        case_key=case_key,
        user_id=intent.user_id,
        symbol=intent.symbol,
        intent_id=intent.intent_id,
        mistake_type=mistake_type,
        original_action=intent.action,
        better_action="REDUCE" if "HELD" in mistake_type else "HOLD",
        outcome=score.notes,
        loss_delta_pct=_loss_from_prices(score.settlement_prices),
        lesson=score.notes,
        context_hint="同类结构下优先复核止损/减仓，而不是默认继续持有。",
        metadata={"tags": score.tags},
    )
    return True


def _account_value(account: PaperAccount) -> float:
    value = account.cash
    for position in account.positions.values():
        ref_price = position.last_price or position.avg_cost
        value += position.total_qty * ref_price
    return value


def _entry_price(fill: PaperFill | None, activation_close: dict[str, Any] | None, account: PaperAccount, symbol: str) -> float:
    if fill and fill.fill_price > 0:
        return fill.fill_price
    close = _num((activation_close or {}).get("close"))
    if close > 0:
        return close
    position = account.positions.get(symbol)
    return (position.last_price or position.avg_cost) if position else 0.0


def _loss_from_prices(prices: list[dict[str, Any]]) -> float:
    if len(prices) < 2:
        return 0.0
    first = _num(prices[0].get("close"))
    last = _num(prices[-1].get("close"))
    if first <= 0 or last <= 0:
        return 0.0
    return round((last - first) / first * 100, 4)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count_by(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get(field) or "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _render_counts(counts: dict[str, int]) -> list[str]:
    if not counts:
        return ["- None"]
    return [f"- {key}: {value}" for key, value in sorted(counts.items())]


def _skip(candidate: StopReduceHistoricalCandidate, reason: str, *, intent_id: str = "") -> dict[str, Any]:
    return {
        "user_id": candidate.user_id,
        "symbol": candidate.symbol,
        "as_of": candidate.as_of,
        "reason": reason,
        "intent_id": intent_id,
    }


def _load_current_position(*, user_id: int, symbol: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT symbol, quantity, avg_cost, current_price
              FROM positions
             WHERE user_id = ? AND symbol = ? AND quantity > 0
            """,
            (user_id, symbol),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _response_with_historical_as_of(
    response: AIReasoningResponse,
    *,
    account: PaperAccount,
    symbol: str,
    activation_close: dict[str, Any],
) -> AIReasoningResponse:
    position = response.position_context
    if position is None:
        return response
    enriched = response.model_copy(deep=True)
    enriched_position = enriched.position_context
    if enriched_position is None:
        return enriched

    as_of_close = _num(activation_close.get("close"))
    if as_of_close > 0 and not _num(enriched_position.current_price):
        enriched_position.current_price = as_of_close
    current_price = _num(enriched_position.current_price)
    if current_price > 0 and not _num(enriched_position.weight_pct):
        enriched_position.weight_pct = _weight_from_account(account, symbol=symbol, price=current_price)
    if not enriched_position.nearest_risk_line and not enriched_position.risk_lines:
        risk_line = _risk_line_from_boundaries(enriched.key_boundaries.invalidate, current_price)
        if not risk_line:
            risk_line = _risk_line_from_boundaries(enriched.key_boundaries.observe + enriched.key_boundaries.support, current_price)
        if risk_line:
            enriched_position.nearest_risk_line = risk_line
            enriched_position.risk_lines = [risk_line]
    return enriched


def _risk_line_from_boundaries(boundaries: list[AllowedPrice], current_price: float) -> dict[str, Any]:
    candidates = [
        item for item in boundaries
        if _num(item.value) > 0 and (current_price <= 0 or _num(item.value) <= current_price)
    ]
    if not candidates:
        return {}
    selected = max(candidates, key=lambda item: _num(item.value))
    value = _num(selected.value)
    return {
        "source": selected.source,
        "type": selected.label or "structure_boundary",
        "label": f"{selected.level or ''}{selected.label or 'boundary'}".strip(),
        "value": value,
        "side": "below" if current_price > value else "at_or_below",
        "distance_pct": round((current_price - value) / current_price * 100, 2) if current_price > 0 else None,
        "meaning": "历史 as-of 结构边界补全",
    }


def _weight_from_account(account: PaperAccount, *, symbol: str, price: float) -> float:
    position = account.positions.get(symbol)
    if position is None:
        return 0.0
    position_value = position.total_qty * price
    total_value = account.cash + position_value
    return round(position_value / total_value * 100, 4) if total_value > 0 else 0.0


def _response_from_ai_reasoning_row(row: dict[str, Any]) -> AIReasoningResponse:
    transcript = StructureTranscript.model_validate(_loads(row.get("transcript_json")))
    output_payload = _loads(row.get("ai_output_json"))
    gate = GateResult.model_validate(_loads(row.get("gate_result_json")) or {"status": "PASS", "score": 100, "violations": []})
    route = ModelRoute.model_validate(_loads(row.get("model_route_json")))
    output = _coerce_ai_output(output_payload) if output_payload else None
    violations = [
        item if isinstance(item, GateViolation) else GateViolation.model_validate(item)
        for item in (gate.violations or [])
    ]
    return AIReasoningResponse(
        gate_status=gate.status,
        gate_score=gate.score,
        generated_at=transcript.generated_at,
        raw_reasoning_md=output.raw_reasoning_md if output else "",
        coach_filtered_md=output.coach_filtered_md if output else "",
        semantic_filter_status=output.semantic_filter_status if output else "PASS",
        semantic_filter_violations=violations,
        agent_observations=transcript.agent_observations,
        key_boundaries=transcript.reasoning_boundaries,
        position_context=transcript.position_context,
        model_route=route,
        coach_talk=output.coach_filtered_md if output else "",
        disclaimer=output.disclaimer if output else transcript.disclaimer,
    )


def _coerce_ai_output(payload: dict[str, Any]) -> AIReasoningOutput:
    if "coach_filtered_md" in payload:
        return AIReasoningOutput.model_validate(payload)
    coach_text = str(payload.get("coach_talk") or payload.get("diagnosis") or payload.get("reasoning_boundary") or "")
    raw_text = str(payload.get("raw_reasoning_md") or coach_text)
    return AIReasoningOutput(
        raw_reasoning_md=raw_text,
        coach_filtered_md=coach_text,
        semantic_filter_status=str(payload.get("semantic_filter_status") or "PASS"),
        semantic_filter_violations=payload.get("semantic_filter_violations") or [],
        disclaimer=str(payload.get("disclaimer") or "仅供参考，不构成投资建议"),
    )


def _load_daily_rows_for_symbol(symbol: str, *, loader, limit: int) -> list[dict[str, Any]]:
    for variant in _symbol_variants(symbol):
        rows = loader(variant, "day", limit=limit)
        if rows:
            return rows
    return []


def _symbol_variants(symbol: str) -> list[str]:
    raw = str(symbol or "").strip()
    variants = []
    for item in (
        raw,
        f"{raw[:2].lower()}.{raw[2:]}" if len(raw) == 8 and raw[:2].lower() in {"sh", "sz"} else "",
        raw.replace(".", "") if "." in raw else "",
    ):
        if item and item not in variants:
            variants.append(item)
    return variants or [raw]


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    loaded = json.loads(str(value))
    return loaded if isinstance(loaded, dict) else {}


def _as_of_bar_index(rows: list[dict[str, Any]], as_of_date: str) -> int:
    index = -1
    for i, row in enumerate(rows):
        date = str(row.get("date") or "")[:10]
        if date <= as_of_date:
            index = i
        else:
            break
    return index


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    _, report, markdown = run_stop_reduce_shadow_cli(config)
    if markdown:
        print(markdown)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
