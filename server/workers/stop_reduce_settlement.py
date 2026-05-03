"""Post-close settlement worker for AI stop/reduce shadow training."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Any

from server.db.database import get_connection
from server.db.kline_lake import query_klines
from server.engines.ai_native.stop_reduce_store import (
    intent_from_row,
    load_pending_rebalance_intents,
)
from server.engines.ai_native.stop_reduce_training import RebalanceIntent
from server.engines.execution.paper_models import PaperAccount, PaperPosition, PaperRiskConfig
from server.scripts.stop_reduce_shadow_loop import (
    build_historical_stop_reduce_sample,
    run_stop_reduce_shadow_sample,
)


DEFAULT_CASE_KEY = "holding:loss:structure_breakdown:near_stop"


@dataclass(frozen=True)
class StopReduceSettlementConfig:
    user_id: int | None = None
    symbol: str | None = None
    limit: int = 50
    settlement_limit: int = 5
    daily_window_limit: int = 260
    persist: bool = True
    case_key: str = DEFAULT_CASE_KEY


@dataclass(frozen=True)
class StopReduceSettlementReport:
    scanned: int
    settled: int
    waiting: int
    skipped: int
    case_memory_writes: int
    rows: list[dict[str, Any]] = field(default_factory=list)


def run_stop_reduce_settlement(
    *,
    conn=None,
    config: StopReduceSettlementConfig | None = None,
    kline_loader=None,
    risk_config: PaperRiskConfig | None = None,
) -> StopReduceSettlementReport:
    """Settle pending stop/reduce intents when enough future daily closes exist."""
    config = config or StopReduceSettlementConfig()
    loader = kline_loader or query_klines
    owns_conn = conn is None
    conn = conn or get_connection()
    rows: list[dict[str, Any]] = []
    try:
        pending = load_pending_rebalance_intents(
            conn,
            user_id=config.user_id,
            symbol=config.symbol,
            limit=config.limit,
        )
        for row in pending:
            result = settle_pending_rebalance_intent(
                conn=conn,
                row=row,
                settlement_limit=config.settlement_limit,
                daily_window_limit=config.daily_window_limit,
                persist=config.persist,
                case_key=config.case_key,
                kline_loader=loader,
                risk_config=risk_config,
            )
            rows.append(result)
        return StopReduceSettlementReport(
            scanned=len(pending),
            settled=sum(1 for item in rows if item["status"] == "SETTLED"),
            waiting=sum(1 for item in rows if item["status"] == "WAITING"),
            skipped=sum(1 for item in rows if item["status"] == "SKIPPED"),
            case_memory_writes=sum(1 for item in rows if item.get("case_stored")),
            rows=rows,
        )
    finally:
        if owns_conn:
            conn.close()


def settle_pending_rebalance_intent(
    *,
    conn,
    row: dict[str, Any],
    settlement_limit: int = 5,
    daily_window_limit: int = 260,
    persist: bool = True,
    case_key: str = DEFAULT_CASE_KEY,
    kline_loader=None,
    risk_config: PaperRiskConfig | None = None,
) -> dict[str, Any]:
    intent = intent_from_row(row)
    loader = kline_loader or query_klines
    daily_rows = _load_daily_rows(intent.symbol, loader=loader, limit=max(daily_window_limit, settlement_limit + 2))
    if not daily_rows:
        return _status(row, "WAITING", "NO_DAILY_ROWS")

    as_of_date = intent.as_of[:10]
    as_of_index = _as_of_bar_index(daily_rows, as_of_date)
    if as_of_index < 0:
        return _status(row, "WAITING", "NO_AS_OF_DAILY_ROW")
    future_rows = daily_rows[as_of_index + 1 : as_of_index + 1 + settlement_limit]
    if len(future_rows) < settlement_limit:
        return _status(
            row,
            "WAITING",
            "WAITING_FOR_SETTLEMENT_PRICES",
            available_settlement_bars=len(future_rows),
            required_settlement_bars=settlement_limit,
        )

    account = _synthetic_account(intent, activation_close=daily_rows[as_of_index])
    sample = build_historical_stop_reduce_sample(
        run_id=str(row.get("run_id") or f"settle:{intent.intent_id}"),
        account=account,
        intent=intent,
        daily_rows=daily_rows,
        as_of_date=as_of_date,
        settlement_limit=settlement_limit,
        case_key=case_key,
    )
    result = run_stop_reduce_shadow_sample(
        run_id=sample.run_id,
        account=sample.account,
        intent=sample.intent,
        activation_close=sample.activation_close,
        next_bar=sample.next_bar,
        settlement_prices=sample.settlement_prices,
        risk_config=risk_config,
        case_key=case_key,
        persist_conn=conn if persist else None,
    )
    return {
        **_status(row, "SETTLED", result.condition_status),
        "final_score": result.score.final_score,
        "lesson_candidate": result.score.lesson_candidate,
        "case_stored": result.case_stored,
        "tags": result.score.tags,
        "settlement_window": result.score.settlement_window,
    }


def render_stop_reduce_settlement_report(report: StopReduceSettlementReport) -> str:
    lines = [
        "# AI Stop/Reduce Settlement Report",
        "",
        "交易相关内容仅供参考，不构成投资建议。",
        "",
        f"- Scanned: {report.scanned}",
        f"- Settled: {report.settled}",
        f"- Waiting: {report.waiting}",
        f"- Skipped: {report.skipped}",
        f"- Case memory writes: {report.case_memory_writes}",
    ]
    if report.rows:
        lines.extend(["", "## Rows", ""])
        for row in report.rows:
            lines.append(
                f"- {row.get('symbol')} {row.get('intent_id')}: "
                f"{row.get('status')} / {row.get('reason')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> StopReduceSettlementConfig:
    parser = argparse.ArgumentParser(description="Settle pending AI stop/reduce shadow intents")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--settlement-limit", type=int, default=5)
    parser.add_argument("--daily-window-limit", type=int, default=260)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return StopReduceSettlementConfig(
        user_id=args.user_id,
        symbol=args.symbol,
        limit=args.limit,
        settlement_limit=args.settlement_limit,
        daily_window_limit=args.daily_window_limit,
        persist=not args.dry_run,
    )


def _synthetic_account(intent: RebalanceIntent, *, activation_close: dict[str, Any]) -> PaperAccount:
    price = _num(activation_close.get("close")) or 1.0
    qty = 10000
    position_value = qty * price
    if intent.current_weight_pct > 0:
        cash = max(0.0, position_value * (100.0 - intent.current_weight_pct) / intent.current_weight_pct)
    else:
        cash = 100000.0
    return PaperAccount(
        paper_account_id=f"paper_settle_{intent.user_id}_{intent.symbol.replace('.', '')}",
        user_id=intent.user_id,
        cash=round(cash, 2),
        positions={
            intent.symbol: PaperPosition(
                symbol=intent.symbol,
                total_qty=qty,
                available_qty=qty,
                protected_base_qty=0,
                avg_cost=price,
                last_price=price,
            )
        },
    )


def _load_daily_rows(symbol: str, *, loader, limit: int) -> list[dict[str, Any]]:
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


def _status(row: dict[str, Any], status: str, reason: str, **extra) -> dict[str, Any]:
    return {
        "intent_id": row.get("intent_id"),
        "run_id": row.get("run_id"),
        "user_id": row.get("user_id"),
        "symbol": row.get("symbol"),
        "as_of": row.get("as_of"),
        "status": status,
        "reason": reason,
        **extra,
    }


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
    report = run_stop_reduce_settlement(config=config)
    print(render_stop_reduce_settlement_report(report))
    print(json.dumps(report.rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
