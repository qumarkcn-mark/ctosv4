"""Virtual account ledger for paper execution."""

from __future__ import annotations

from dataclasses import replace

from server.engines.execution.paper_models import PaperAccount, PaperFill, PaperPosition


def apply_paper_fill(account: PaperAccount, fill: PaperFill) -> PaperAccount:
    """Apply one simulated fill and return the updated account.

    The ledger is immutable: callers get a new account object, which makes replay
    tests deterministic and avoids hidden state between bars.
    """
    if fill.status == "NOT_FILLED" or fill.quantity <= 0:
        return account

    position = account.positions.get(fill.symbol)
    positions = dict(account.positions)
    cash = account.cash
    realized_pnl = account.realized_pnl

    if fill.side == "BUY":
        cash -= fill.amount + fill.total_cost
        if position is None:
            position = PaperPosition(
                symbol=fill.symbol,
                total_qty=0,
                available_qty=0,
                protected_base_qty=0,
                avg_cost=0.0,
            )
        new_qty = position.total_qty + fill.quantity
        new_cost = (position.avg_cost * position.total_qty) + fill.amount + fill.total_cost
        avg_cost = new_cost / new_qty if new_qty > 0 else 0.0
        positions[fill.symbol] = replace(
            position,
            total_qty=new_qty,
            avg_cost=round(avg_cost, 4),
            last_price=fill.fill_price,
        )
    else:
        if position is None:
            raise ValueError(f"cannot sell missing paper position: {fill.symbol}")
        if fill.quantity > position.available_qty:
            raise ValueError("paper sell quantity exceeds available quantity")
        cash += fill.amount - fill.total_cost
        realized_pnl += (fill.fill_price - position.avg_cost) * fill.quantity - fill.total_cost
        new_qty = position.total_qty - fill.quantity
        positions[fill.symbol] = replace(
            position,
            total_qty=new_qty,
            available_qty=max(0, position.available_qty - fill.quantity),
            last_price=fill.fill_price,
        )

    return replace(
        account,
        cash=round(cash, 4),
        positions=positions,
        realized_pnl=round(realized_pnl, 4),
        trade_count=account.trade_count + 1,
        used_idempotency_keys=account.used_idempotency_keys | frozenset([fill.intent_id]),
    )


def mark_intent_key_used(account: PaperAccount, idempotency_key: str) -> PaperAccount:
    return replace(
        account,
        used_idempotency_keys=account.used_idempotency_keys | frozenset([idempotency_key]),
    )
