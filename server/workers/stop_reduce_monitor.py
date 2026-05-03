"""Monitor current holdings and enqueue AI stop/reduce shadow intents."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from server.db.database import get_connection
from server.engines.ai_native.schemas import AIReasoningResponse
from server.engines.ai_native.holding_plan import build_holding_plan_from_ai_response
from server.engines.ai_native.holding_plan_store import save_holding_plan
from server.engines.ai_native.stop_reduce_adapter import build_stop_reduce_intent_from_ai_response
from server.engines.ai_native.stop_reduce_store import save_rebalance_intent, save_rebalance_run
from server.engines.execution.paper_models import PaperAccount, PaperPosition


ReasoningBuilder = Callable[..., Awaitable[AIReasoningResponse]]


@dataclass(frozen=True)
class StopReduceMonitorConfig:
    user_id: int = 1
    symbol: str | None = None
    limit: int = 20
    fundamental_verdict: str = "中性"
    dry_run: bool = False


@dataclass(frozen=True)
class StopReduceMonitorReport:
    scanned_positions: int
    reasoning_runs: int
    saved_plans: int
    enqueued_intents: int
    skipped: int
    rows: list[dict[str, Any]] = field(default_factory=list)


async def run_stop_reduce_monitor(
    *,
    conn=None,
    config: StopReduceMonitorConfig | None = None,
    reasoning_builder: ReasoningBuilder | None = None,
) -> StopReduceMonitorReport:
    """Generate pending stop/reduce intents from current positions.

    This is coach-only: it never sends orders and never mutates real positions.
    """
    config = config or StopReduceMonitorConfig()
    builder = reasoning_builder or _default_reasoning_builder
    owns_conn = conn is None
    conn = conn or get_connection()
    rows: list[dict[str, Any]] = []
    try:
        positions = load_monitor_positions(
            conn,
            user_id=config.user_id,
            symbol=config.symbol,
            limit=config.limit,
        )
        for position in positions:
            symbol = str(position["symbol"])
            try:
                response = await builder(symbol=symbol, user_id=config.user_id, mode="HOLDING")
                account = paper_account_from_position(config.user_id, position)
                as_of = response.generated_at or _now_iso()
                plan = build_holding_plan_from_ai_response(
                    user_id=config.user_id,
                    symbol=symbol,
                    response=response,
                    as_of=as_of,
                    fundamental_verdict=config.fundamental_verdict,  # type: ignore[arg-type]
                )
                if plan is None:
                    rows.append(_row(position, "SKIPPED", "NO_PLAN"))
                    continue
                if not config.dry_run:
                    save_holding_plan(conn, plan)
                    conn.commit()
                if plan.plan_status not in {"REDUCE_ALERT", "EXIT_ALERT"}:
                    rows.append(
                        {
                            **_row(position, "PLANNED", plan.plan_status),
                            "plan_id": plan.plan_id,
                            "target_weight_pct": plan.target_weight_pct,
                        }
                    )
                    continue
                intent = build_stop_reduce_intent_from_ai_response(
                    user_id=config.user_id,
                    symbol=symbol,
                    response=response,
                    as_of=as_of,
                    fundamental_verdict=config.fundamental_verdict,  # type: ignore[arg-type]
                )
                if intent is None:
                    rows.append(_row(position, "SKIPPED", "NO_INTENT"))
                    continue
                run_id = f"monitor_stop_reduce:{config.user_id}:{symbol}:{as_of}:{response.run_id or 'manual'}"
                if not config.dry_run:
                    save_rebalance_run(
                        conn,
                        run_id=run_id,
                        user_id=intent.user_id,
                        symbol=intent.symbol,
                        as_of=intent.as_of,
                        radar_run_id=_optional_int(response.run_id),
                        technical_view=intent.reason,
                        status="WAITING_SETTLEMENT",
                    )
                    save_rebalance_intent(conn, intent, run_id=run_id)
                    conn.execute(
                        "UPDATE ai_rebalance_intents SET source_plan_id = ? WHERE intent_id = ?",
                        (plan.plan_id, intent.intent_id),
                    )
                    conn.commit()
                rows.append(
                    {
                        **_row(position, "ENQUEUED", intent.action),
                        "plan_id": plan.plan_id,
                        "run_id": run_id,
                        "intent_id": intent.intent_id,
                        "idempotency_key": intent.idempotency_key,
                        "target_weight_pct": intent.target_weight_pct,
                        "paper_account_id": account.paper_account_id,
                    }
                )
            except Exception as exc:
                rows.append(_row(position, "SKIPPED", f"ERROR:{str(exc)[:120]}"))

        return StopReduceMonitorReport(
            scanned_positions=len(positions),
            reasoning_runs=sum(1 for item in rows if not str(item.get("reason", "")).startswith("ERROR")),
            saved_plans=sum(1 for item in rows if item["status"] in {"PLANNED", "ENQUEUED"}),
            enqueued_intents=sum(1 for item in rows if item["status"] == "ENQUEUED"),
            skipped=sum(1 for item in rows if item["status"] == "SKIPPED"),
            rows=rows,
        )
    finally:
        if owns_conn:
            conn.close()


def load_monitor_positions(conn, *, user_id: int, symbol: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    clauses = ["user_id = ?", "quantity > 0"]
    params: list[Any] = [user_id]
    if symbol:
        variants = _symbol_variants(symbol)
        clauses.append(f"symbol IN ({','.join('?' for _ in variants)})")
        params.extend(variants)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT user_id, symbol, name, quantity, avg_cost, current_price
          FROM positions
         WHERE {' AND '.join(clauses)}
         ORDER BY updated_at DESC, id DESC
         LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def paper_account_from_position(user_id: int, position: dict[str, Any]) -> PaperAccount:
    symbol = str(position["symbol"])
    quantity = max(0, int(position.get("quantity") or 0))
    avg_cost = _num(position.get("avg_cost"))
    current_price = _num(position.get("current_price")) or avg_cost
    return PaperAccount(
        paper_account_id=f"paper_monitor_{user_id}_{symbol.replace('.', '')}",
        user_id=user_id,
        cash=100000.0,
        positions={
            symbol: PaperPosition(
                symbol=symbol,
                total_qty=quantity,
                available_qty=quantity,
                protected_base_qty=0,
                avg_cost=avg_cost or current_price,
                last_price=current_price or avg_cost,
            )
        },
    )


def render_stop_reduce_monitor_report(report: StopReduceMonitorReport) -> str:
    lines = [
        "# AI Stop/Reduce Monitor Report",
        "",
        "交易相关内容仅供参考，不构成投资建议。",
        "",
        f"- Scanned positions: {report.scanned_positions}",
        f"- Reasoning runs: {report.reasoning_runs}",
        f"- Saved plans: {report.saved_plans}",
        f"- Enqueued intents: {report.enqueued_intents}",
        f"- Skipped: {report.skipped}",
    ]
    if report.rows:
        lines.extend(["", "## Rows", ""])
        for row in report.rows:
            lines.append(f"- {row.get('symbol')}: {row.get('status')} / {row.get('reason')}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> StopReduceMonitorConfig:
    parser = argparse.ArgumentParser(description="Monitor holdings and enqueue AI stop/reduce shadow intents")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--fundamental-verdict", choices=["支持", "中性", "回避"], default="中性")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return StopReduceMonitorConfig(
        user_id=args.user_id,
        symbol=args.symbol,
        limit=args.limit,
        fundamental_verdict=args.fundamental_verdict,
        dry_run=args.dry_run,
    )


async def _default_reasoning_builder(**kwargs) -> AIReasoningResponse:
    from server.engines.ai_native.reasoning_orchestrator import build_ai_native_reasoning

    return await build_ai_native_reasoning(**kwargs)


def _row(position: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "user_id": position.get("user_id"),
        "symbol": position.get("symbol"),
        "name": position.get("name"),
        "status": status,
        "reason": reason,
    }


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


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    report = asyncio.run(run_stop_reduce_monitor(config=config))
    print(render_stop_reduce_monitor_report(report))
    print(json.dumps(report.rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
