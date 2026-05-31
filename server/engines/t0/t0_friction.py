"""T+0 有边界做T — 高精度双向摩擦成本模型。

纯函数，零外部依赖。精确计算 A 股交易摩擦成本，用于做T可行性判断。
"""
from __future__ import annotations


def calculate_friction(
    price: float,
    qty: int,
    direction: str,  # "BUY" or "SELL"
    *,
    commission_rate: float = 0.00015,
    min_commission: float = 5.0,
    stamp_duty_rate: float = 0.0005,   # 印花税仅 SELL
    transfer_fee_rate: float = 0.00001,
    slippage_ticks: int = 1,
    tick_size: float = 0.01,
) -> dict:
    """计算单向交易摩擦成本。

    Args:
        price: 成交价
        qty: 成交数量（股）
        direction: "BUY" 或 "SELL"
        commission_rate: 佣金费率（默认万1.5）
        min_commission: 最低佣金（默认 5 元）
        stamp_duty_rate: 印花税率（仅 SELL，默认千0.5）
        transfer_fee_rate: 过户费率（默认万0.1）
        slippage_ticks: 滑点 tick 数
        tick_size: 最小价格单位

    Returns:
        { commission, transfer_fee, stamp_duty, slippage, total }
    """
    amount = price * qty

    # 佣金：最低 5 元
    commission = max(min_commission, amount * commission_rate)

    # 过户费（买卖双向）
    transfer_fee = amount * transfer_fee_rate

    # 印花税：仅卖出方向
    stamp_duty = amount * stamp_duty_rate if direction.upper() == "SELL" else 0.0

    # 滑点
    slippage = slippage_ticks * tick_size * qty

    total = commission + transfer_fee + stamp_duty + slippage

    return {
        "commission": round(commission, 4),
        "transfer_fee": round(transfer_fee, 4),
        "stamp_duty": round(stamp_duty, 4),
        "slippage": round(slippage, 4),
        "total": round(total, 4),
    }


def calculate_round_trip_friction(
    price: float,
    qty: int,
    **kwargs,
) -> dict:
    """计算买+卖完整往返摩擦成本。

    Returns:
        { buy_cost, sell_cost, total_cost, cost_per_share }
    """
    buy = calculate_friction(price, qty, "BUY", **kwargs)
    sell = calculate_friction(price, qty, "SELL", **kwargs)
    total_cost = buy["total"] + sell["total"]
    return {
        "buy_cost": buy["total"],
        "sell_cost": sell["total"],
        "total_cost": round(total_cost, 4),
        "cost_per_share": round(total_cost / qty, 6) if qty > 0 else 0.0,
    }


def is_grid_viable(
    grid_spread: float,
    friction_per_share: float,
    min_ratio: float = 3.0,
) -> bool:
    """判断做T网格是否可行：ΔP_grid >= min_ratio × F_share。

    Args:
        grid_spread: 网格价差（ZG - ZD，元/股）
        friction_per_share: 往返摩擦成本（元/股）
        min_ratio: 最低收益/成本比（默认 3x）

    Returns:
        True 表示有足够摩擦空间，可以做T
    """
    if friction_per_share <= 0:
        return False
    return grid_spread >= min_ratio * friction_per_share


def min_viable_spread(
    price: float,
    qty: int,
    min_ratio: float = 3.0,
    **kwargs,
) -> float:
    """计算最小盈利价差 = min_ratio × cost_per_share。

    Args:
        price: 参考价格
        qty: 做T数量（股）
        min_ratio: 最低收益/成本比

    Returns:
        最小盈利价差（元/股）
    """
    rt = calculate_round_trip_friction(price, qty, **kwargs)
    return round(min_ratio * rt["cost_per_share"], 4)
