"""Paper risk checks and next-bar fill simulation."""

from __future__ import annotations

from dataclasses import replace

from server.engines.execution.paper_account import apply_paper_fill, mark_intent_key_used
from server.engines.execution.paper_models import (
    PaperAccount,
    PaperFeesConfig,
    PaperFill,
    PaperIntent,
    PaperKline,
    PaperRiskConfig,
)


def evaluate_paper_risk(
    account: PaperAccount,
    intent: PaperIntent,
    config: PaperRiskConfig,
) -> PaperIntent:
    checks = [
        _check_positive_quantity(intent),
        _check_duplicate_key(account, intent),
        _check_trade_count(account, config),
        _check_single_order_amount(account, intent, config),
        _check_side_capacity(account, intent),
    ]
    blocked = any(check["status"] in ("FAIL", "BLOCKED") for check in checks)
    return replace(intent, status="REJECTED" if blocked else "APPROVED", risk_checks=checks)


def simulate_next_bar_fill(
    account: PaperAccount,
    intent: PaperIntent,
    next_bar: PaperKline | dict,
    config: PaperRiskConfig,
) -> tuple[PaperAccount, PaperFill]:
    """Fill an approved paper intent at next-bar open with conservative costs."""
    if not isinstance(next_bar, PaperKline):
        next_bar = PaperKline.from_dict(next_bar)

    approved = evaluate_paper_risk(account, intent, config)
    if approved.status != "APPROVED":
        fill = _not_filled(approved, next_bar.time, "RISK_BLOCKED")
        return account, fill

    no_fill_reason = _no_fill_reason(approved, next_bar)
    if no_fill_reason:
        fill = _not_filled(approved, next_bar.time, no_fill_reason)
        return mark_intent_key_used(account, approved.idempotency_key), fill

    fees = config.fees
    raw_open = next_bar.open
    slip = raw_open * (fees.slippage_bps / 10000.0)
    fill_price = raw_open + slip if approved.side == "BUY" else raw_open - slip
    amount = fill_price * approved.quantity
    commission = max(amount * fees.commission_rate, fees.min_commission)
    stamp_tax = amount * fees.stamp_tax_rate if approved.side == "SELL" else 0.0
    transfer_fee = amount * fees.transfer_fee_rate

    fill = PaperFill(
        fill_id=f"fill_{approved.intent_id}",
        intent_id=approved.intent_id,
        symbol=approved.symbol,
        side=approved.side,
        quantity=approved.quantity,
        fill_price=round(fill_price, 4),
        filled_at=next_bar.time,
        status="FILLED",
        commission=round(commission, 4),
        stamp_tax=round(stamp_tax, 4),
        transfer_fee=round(transfer_fee, 4),
        slippage=round(slip, 4),
    )
    next_account = apply_paper_fill(account, fill)
    next_account = mark_intent_key_used(next_account, approved.idempotency_key)
    return next_account, fill


def _check_positive_quantity(intent: PaperIntent) -> dict:
    status = "PASS" if intent.quantity > 0 else "FAIL"
    return {"check_id": "positive_quantity", "status": status, "evidence": {"quantity": intent.quantity}}


def _check_duplicate_key(account: PaperAccount, intent: PaperIntent) -> dict:
    is_duplicate = intent.idempotency_key in account.used_idempotency_keys
    return {
        "check_id": "duplicate_idempotency_key",
        "status": "FAIL" if is_duplicate else "PASS",
        "evidence": {"idempotency_key": intent.idempotency_key},
    }


def _check_trade_count(account: PaperAccount, config: PaperRiskConfig) -> dict:
    ok = account.trade_count < config.max_trades_per_day
    return {
        "check_id": "max_trades_per_day",
        "status": "PASS" if ok else "FAIL",
        "evidence": {"trade_count": account.trade_count, "max_trades": config.max_trades_per_day},
    }


def _check_single_order_amount(
    account: PaperAccount,
    intent: PaperIntent,
    config: PaperRiskConfig,
) -> dict:
    position = account.positions.get(intent.symbol)
    ref_price = position.last_price if position and position.last_price > 0 else position.avg_cost if position else 0
    amount = ref_price * intent.quantity
    ok = amount <= config.max_single_order_amount
    return {
        "check_id": "max_single_order_amount",
        "status": "PASS" if ok else "FAIL",
        "evidence": {"amount": round(amount, 4), "max_single_order_amount": config.max_single_order_amount},
    }


def _check_side_capacity(account: PaperAccount, intent: PaperIntent) -> dict:
    position = account.positions.get(intent.symbol)
    if intent.side == "SELL":
        available_t_qty = position.available_t_qty if position else 0
        ok = intent.quantity <= available_t_qty
        return {
            "check_id": "base_position_guard",
            "status": "PASS" if ok else "FAIL",
            "evidence": {"available_t_qty": available_t_qty, "sell_quantity": intent.quantity},
        }

    ref_price = position.last_price if position and position.last_price > 0 else position.avg_cost if position else 0
    required_cash = ref_price * intent.quantity
    return {
        "check_id": "available_cash",
        "status": "PASS" if account.cash >= required_cash else "FAIL",
        "evidence": {"cash": account.cash, "required_cash": round(required_cash, 4)},
    }


def _no_fill_reason(intent: PaperIntent, next_bar: PaperKline) -> str:
    if not next_bar.time:
        return "NO_NEXT_BAR"
    if next_bar.volume <= 0:
        return "ZERO_VOLUME"
    if intent.side == "BUY" and next_bar.limit_up:
        return "LIMIT_UP_BLOCKS_BUY"
    if intent.side == "SELL" and next_bar.limit_down:
        return "LIMIT_DOWN_BLOCKS_SELL"
    return ""


def _not_filled(intent: PaperIntent, filled_at: str, reason: str) -> PaperFill:
    return PaperFill(
        fill_id=f"fill_{intent.intent_id}",
        intent_id=intent.intent_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=0,
        fill_price=0.0,
        filled_at=filled_at,
        status="NOT_FILLED",
        reason=reason,
    )
