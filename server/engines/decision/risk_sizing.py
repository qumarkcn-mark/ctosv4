"""Entry risk checks and sizing rules."""


def check_stop_atr(current_price: float, stop_price: float, atr: float) -> dict:
    """校验结构止损距离是否落在 1~2.5 ATR 的合理区间。"""
    if atr is None or atr <= 0 or current_price <= 0:
        return {
            "stop_price": stop_price,
            "atr": atr or 0,
            "stop_distance_pct": 0,
            "atr_multiple": 0,
            "valid": False,
            "verdict": "数据不足",
            "note": "ATR或价格数据缺失，无法校验止损合理性",
        }

    stop_distance = current_price - stop_price
    if stop_distance <= 0:
        return {
            "stop_price": stop_price,
            "atr": atr,
            "stop_distance_pct": 0,
            "atr_multiple": 0,
            "valid": False,
            "verdict": "止损价高于当前价",
            "note": f"止损价 {stop_price:.2f} 高于当前价 {current_price:.2f}，数据异常",
        }

    atr_multiple = stop_distance / atr
    stop_distance_pct = stop_distance / current_price

    if atr_multiple < 1.0:
        verdict = "止损太紧"
        valid = False
        note = (
            f"止损距离 {stop_distance:.2f}（{stop_distance_pct:.1%}）仅 {atr_multiple:.1f}×ATR，"
            "正常波动即被扫出，建议等价格回调到更好的入场点。仅供参考。"
        )
    elif atr_multiple > 2.5:
        verdict = "止损太宽"
        valid = False
        note = (
            f"止损距离 {stop_distance:.2f}（{stop_distance_pct:.1%}）达 {atr_multiple:.1f}×ATR，"
            "单笔亏损超标，建议等价格回调压缩止损距离后入场。仅供参考。"
        )
    else:
        verdict = "合理"
        valid = True
        note = (
            f"止损距离 {stop_distance:.2f}（{stop_distance_pct:.1%}），"
            f"{atr_multiple:.1f}×ATR，在合理范围（1~2.5×ATR）内。"
        )

    return {
        "stop_price": round(stop_price, 2),
        "atr": round(atr, 3),
        "stop_distance_pct": round(stop_distance_pct, 4),
        "atr_multiple": round(atr_multiple, 2),
        "valid": valid,
        "verdict": verdict,
        "note": note,
    }


def calculate_position_size(
    account_value: float,
    current_price: float,
    stop_price: float,
    risk_pct: float = 0.01,
) -> dict:
    """按固定风险比例计算建议仓位，结果仅供参考。"""
    if account_value <= 0 or current_price <= 0 or stop_price <= 0:
        return {"error": "参数无效", "suggested_shares": 0, "suggested_amount": 0}

    stop_distance = current_price - stop_price
    if stop_distance <= 0:
        return {"error": "止损价高于入场价", "suggested_shares": 0, "suggested_amount": 0}

    max_loss_amount = account_value * risk_pct
    raw_shares = max_loss_amount / stop_distance
    suggested_shares = max(100, int(raw_shares / 100) * 100)
    suggested_amount = suggested_shares * current_price
    position_pct = suggested_amount / account_value

    return {
        "risk_pct": risk_pct,
        "max_loss_amount": round(max_loss_amount, 2),
        "stop_distance": round(stop_distance, 2),
        "suggested_shares": suggested_shares,
        "suggested_amount": round(suggested_amount, 2),
        "position_pct": round(position_pct, 4),
        "note": (
            f"账户{account_value:.0f}元，单笔最大风险{risk_pct:.0%}="
            f"{max_loss_amount:.0f}元，建议{suggested_shares}股（仅供参考）"
            f"（约{suggested_amount:.0f}元，占仓{position_pct:.0%}）"
        ),
    }
