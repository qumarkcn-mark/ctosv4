"""SQLite persistence helpers for paper trading experiments."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from typing import Any

from server.engines.execution.paper_models import PaperAccount, PaperFill, PaperIntent, PaperPosition
from server.engines.execution.paper_replay import ReplayResult


def save_paper_account(conn: sqlite3.Connection, account: PaperAccount, metadata: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO paper_accounts (
            paper_account_id, user_id, cash, realized_pnl, trade_count, metadata_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(paper_account_id) DO UPDATE SET
            cash=excluded.cash,
            realized_pnl=excluded.realized_pnl,
            trade_count=excluded.trade_count,
            metadata_json=excluded.metadata_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            account.paper_account_id,
            account.user_id,
            account.cash,
            account.realized_pnl,
            account.trade_count,
            _json(metadata or {}),
        ),
    )
    for position in account.positions.values():
        save_paper_position(conn, account.paper_account_id, position)


def save_paper_position(conn: sqlite3.Connection, paper_account_id: str, position: PaperPosition) -> None:
    conn.execute(
        """
        INSERT INTO paper_positions (
            paper_account_id, symbol, total_qty, available_qty, protected_base_qty,
            avg_cost, last_price, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(paper_account_id, symbol) DO UPDATE SET
            total_qty=excluded.total_qty,
            available_qty=excluded.available_qty,
            protected_base_qty=excluded.protected_base_qty,
            avg_cost=excluded.avg_cost,
            last_price=excluded.last_price,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            paper_account_id,
            position.symbol,
            position.total_qty,
            position.available_qty,
            position.protected_base_qty,
            position.avg_cost,
            position.last_price,
        ),
    )


def create_paper_replay_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    account: PaperAccount,
    symbol: str,
    strategy_id: str = "intraday_t_base_position",
    config: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO paper_replay_runs (
            run_id, paper_account_id, user_id, symbol, strategy_id, config_json, status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'RUNNING')
        """,
        (
            run_id,
            account.paper_account_id,
            account.user_id,
            symbol,
            strategy_id,
            _json(config or {}),
        ),
    )


def complete_paper_replay_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    metrics: dict[str, Any],
    status: str = "COMPLETED",
) -> None:
    conn.execute(
        """
        UPDATE paper_replay_runs
           SET ended_at=CURRENT_TIMESTAMP,
               metrics_json=?,
               status=?
         WHERE run_id=?
        """,
        (_json(metrics), status, run_id),
    )


def save_paper_intent(
    conn: sqlite3.Connection,
    intent: PaperIntent,
    *,
    run_id: str | None = None,
) -> None:
    intent_id = _run_scoped_id(run_id, intent.intent_id)
    linked_intent_id = _run_scoped_id(run_id, intent.linked_intent_id)
    idempotency_key = _run_scoped_id(run_id, intent.idempotency_key)
    conn.execute(
        """
        INSERT OR IGNORE INTO paper_intents (
            intent_id, run_id, paper_account_id, user_id, symbol, side, quantity,
            status, idempotency_key, strategy_id, strategy_version, linked_intent_id,
            created_at, price_policy_json, reason_json, risk_checks_json, simulator, dry_run
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent_id,
            run_id,
            intent.paper_account_id,
            intent.user_id,
            intent.symbol,
            intent.side,
            intent.quantity,
            intent.status,
            idempotency_key,
            intent.strategy_id,
            intent.strategy_version,
            linked_intent_id,
            intent.created_at,
            _json(intent.price_policy),
            _json(intent.reason),
            _json(intent.risk_checks),
            1 if intent.simulator else 0,
            1 if intent.dry_run else 0,
        ),
    )


def save_paper_decision(
    conn: sqlite3.Connection,
    decision,
    *,
    account: PaperAccount,
    run_id: str | None = None,
    index: int = 0,
) -> None:
    symbol = str((decision.evidence or {}).get("symbol") or "")
    if not symbol and decision.intent is not None:
        symbol = decision.intent.symbol
    if not symbol:
        symbol = str((decision.evidence or {}).get("features", {}).get("symbol") or "")
    as_of = str((decision.evidence or {}).get("as_of") or "")
    if not as_of and decision.intent is not None:
        as_of = decision.intent.created_at
    decision_id = f"{run_id or 'paper_run'}:decision:{index:06d}"
    conn.execute(
        """
        INSERT OR IGNORE INTO paper_decisions (
            decision_id, run_id, paper_account_id, user_id, symbol, as_of,
            decision, decision_status, reason, intent_id, evidence_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            run_id,
            account.paper_account_id,
            account.user_id,
            symbol,
            as_of,
            decision.decision,
            decision.status,
            decision.reason,
            _run_scoped_id(run_id, decision.intent.intent_id) if decision.intent else None,
            _json(decision.evidence),
        ),
    )


def save_paper_fill(
    conn: sqlite3.Connection,
    fill: PaperFill,
    *,
    account: PaperAccount,
    run_id: str | None = None,
) -> None:
    fill_id = _run_scoped_id(run_id, fill.fill_id)
    intent_id = _run_scoped_id(run_id, fill.intent_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO paper_fills (
            fill_id, intent_id, run_id, paper_account_id, user_id, symbol, side,
            quantity, fill_price, amount, commission, stamp_tax, transfer_fee,
            slippage, price_source, fill_status, reason, filled_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fill_id,
            intent_id,
            run_id,
            account.paper_account_id,
            account.user_id,
            fill.symbol,
            fill.side,
            fill.quantity,
            fill.fill_price,
            fill.amount,
            fill.commission,
            fill.stamp_tax,
            fill.transfer_fee,
            fill.slippage,
            fill.price_source,
            fill.status,
            fill.reason,
            fill.filled_at,
        ),
    )


def save_replay_result(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    start_account: PaperAccount,
    result: ReplayResult,
    symbol: str,
    config: dict | None = None,
) -> None:
    save_paper_account(conn, start_account)
    create_paper_replay_run(conn, run_id=run_id, account=start_account, symbol=symbol, config=config)

    for decision in result.decisions:
        if decision.intent is not None:
            save_paper_intent(conn, decision.intent, run_id=run_id)
    for idx, decision in enumerate(result.decisions):
        save_paper_decision(conn, decision, account=start_account, run_id=run_id, index=idx)
    for fill in result.fills:
        save_paper_fill(conn, fill, account=start_account, run_id=run_id)

    save_paper_account(conn, result.account)
    complete_paper_replay_run(conn, run_id=run_id, metrics=result.metrics)
    conn.commit()


def _json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _run_scoped_id(run_id: str | None, value: str | None) -> str | None:
    if not value or not run_id:
        return value
    if value.startswith(f"{run_id}:"):
        return value
    return f"{run_id}:{value}"
