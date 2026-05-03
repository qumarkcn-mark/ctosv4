import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.execution.paper_adapter import evaluate_paper_risk, simulate_next_bar_fill
from server.engines.execution.paper_models import (
    PaperAccount,
    PaperIntent,
    PaperKline,
    PaperPosition,
    PaperRiskConfig,
)


def account():
    return PaperAccount(
        paper_account_id="paper_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=400,
                protected_base_qty=300,
                avg_cost=10.0,
                last_price=12.0,
            )
        },
    )


def intent(side="SELL", quantity=100, key="k1"):
    return PaperIntent(
        intent_id="intent_1",
        idempotency_key=key,
        user_id=1,
        paper_account_id="paper_1",
        symbol="sh.603893",
        side=side,
        quantity=quantity,
        created_at="2026-04-29 10:30:00",
    )


def test_paper_fill_uses_next_bar_open_with_slippage_and_fees():
    next_account, fill = simulate_next_bar_fill(
        account(),
        intent("SELL"),
        PaperKline(time="2026-04-29 10:31:00", open=12.0, high=12.2, low=11.8, close=12.1, volume=10000),
        PaperRiskConfig(),
    )

    assert fill.status == "FILLED"
    assert fill.fill_price == 11.994
    assert fill.commission == 5.0
    assert fill.stamp_tax > 0
    assert next_account.cash > account().cash
    assert next_account.realized_pnl > 0


def test_duplicate_idempotency_key_does_not_duplicate_fill():
    first_account, first_fill = simulate_next_bar_fill(
        account(),
        intent("SELL", key="dup"),
        PaperKline(time="2026-04-29 10:31:00", open=12.0, high=12.2, low=11.8, close=12.1, volume=10000),
        PaperRiskConfig(),
    )
    second_account, second_fill = simulate_next_bar_fill(
        first_account,
        intent("SELL", key="dup"),
        PaperKline(time="2026-04-29 10:32:00", open=12.0, high=12.2, low=11.8, close=12.1, volume=10000),
        PaperRiskConfig(),
    )

    assert first_fill.status == "FILLED"
    assert second_fill.status == "NOT_FILLED"
    assert second_fill.reason == "RISK_BLOCKED"
    assert second_account.trade_count == first_account.trade_count


def test_limit_state_blocks_fill():
    next_account, fill = simulate_next_bar_fill(
        account(),
        intent("BUY"),
        PaperKline(time="2026-04-29 10:31:00", open=12.0, high=12.0, low=12.0, close=12.0, volume=10000, limit_up=True),
        PaperRiskConfig(),
    )

    assert fill.status == "NOT_FILLED"
    assert fill.reason == "LIMIT_UP_BLOCKS_BUY"
    assert next_account.cash == account().cash


def test_base_position_guard_blocks_sell_quantity():
    checked = evaluate_paper_risk(account(), intent("SELL", quantity=200), PaperRiskConfig())

    assert checked.status == "REJECTED"
    base_check = next(c for c in checked.risk_checks if c["check_id"] == "base_position_guard")
    assert base_check["status"] == "FAIL"
