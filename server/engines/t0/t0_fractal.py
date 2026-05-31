"""T+0 有边界做T — 1分钟右侧分型过滤器。

纯数学计算，确定性，零 LLM 依赖。
基于缠论分型定义，判断 1M K 线是否形成底/顶分型。
"""
from __future__ import annotations

import math


def validate_1m_bottom_fractal(klines_1m: list[dict]) -> dict:
    """判断 1 分钟底分型（右侧确认）。

    分型判定逻辑（蓝图 §三）：
    1. 几何判定：K_mid.low < K_left.low AND K_mid.low < K_right.low
    2. 包容排除：K_mid.high < max(K_left.high, K_right.high)
    3. 右侧确认：K_right.close > K_mid.high（放量逆袭）

    Args:
        klines_1m: 最近 N 根 CLOSED 1M K线，时间升序排列。
            每根 K线格式：{ time, open, high, low, close, volume }

    Returns:
        { confirmed: bool, fractal_low: float|None, fractal_bar: dict|None, reason: str }
    """
    if len(klines_1m) < 3:
        return {
            "confirmed": False,
            "fractal_low": None,
            "fractal_bar": None,
            "reason": "K线不足3根，无法判断分型",
        }

    # 取最后三根：left, mid, right
    k_left = klines_1m[-3]
    k_mid = klines_1m[-2]
    k_right = klines_1m[-1]

    # 1. 几何判定
    if not (k_mid["low"] < k_left["low"] and k_mid["low"] < k_right["low"]):
        return {
            "confirmed": False,
            "fractal_low": None,
            "fractal_bar": None,
            "reason": f"底分型几何不成立: mid.low={k_mid['low']} left.low={k_left['low']} right.low={k_right['low']}",
        }

    # 2. 包容关系排除
    if k_mid["high"] >= max(k_left["high"], k_right["high"]):
        return {
            "confirmed": False,
            "fractal_low": None,
            "fractal_bar": None,
            "reason": f"包容关系排除: mid.high={k_mid['high']} >= max(left/right.high)",
        }

    # 3. 右侧确认：右 K 收盘突破中间 K 高点
    if k_right["close"] <= k_mid["high"]:
        return {
            "confirmed": False,
            "fractal_low": None,
            "fractal_bar": None,
            "reason": f"右侧未确认: right.close={k_right['close']} <= mid.high={k_mid['high']}",
        }

    return {
        "confirmed": True,
        "fractal_low": k_mid["low"],
        "fractal_bar": k_mid,
        "reason": f"底分型确认: low={k_mid['low']} right.close={k_right['close']} > mid.high={k_mid['high']}",
    }


def validate_1m_top_fractal(klines_1m: list[dict]) -> dict:
    """判断 1 分钟顶分型（右侧确认）。

    逻辑与底分型镜像：
    1. K_mid.high > K_left.high AND K_mid.high > K_right.high
    2. K_mid.low > min(K_left.low, K_right.low)
    3. K_right.close < K_mid.low（右侧确认向下破）

    Args:
        klines_1m: 最近 N 根 CLOSED 1M K线，时间升序排列。

    Returns:
        { confirmed: bool, fractal_high: float|None, fractal_bar: dict|None, reason: str }
    """
    if len(klines_1m) < 3:
        return {
            "confirmed": False,
            "fractal_high": None,
            "fractal_bar": None,
            "reason": "K线不足3根，无法判断分型",
        }

    k_left = klines_1m[-3]
    k_mid = klines_1m[-2]
    k_right = klines_1m[-1]

    # 1. 几何判定
    if not (k_mid["high"] > k_left["high"] and k_mid["high"] > k_right["high"]):
        return {
            "confirmed": False,
            "fractal_high": None,
            "fractal_bar": None,
            "reason": f"顶分型几何不成立: mid.high={k_mid['high']} left.high={k_left['high']} right.high={k_right['high']}",
        }

    # 2. 包容关系排除
    if k_mid["low"] <= min(k_left["low"], k_right["low"]):
        return {
            "confirmed": False,
            "fractal_high": None,
            "fractal_bar": None,
            "reason": f"包容关系排除: mid.low={k_mid['low']} <= min(left/right.low)",
        }

    # 3. 右侧确认：右 K 收盘跌破中间 K 低点
    if k_right["close"] >= k_mid["low"]:
        return {
            "confirmed": False,
            "fractal_high": None,
            "fractal_bar": None,
            "reason": f"右侧未确认: right.close={k_right['close']} >= mid.low={k_mid['low']}",
        }

    return {
        "confirmed": True,
        "fractal_high": k_mid["high"],
        "fractal_bar": k_mid,
        "reason": f"顶分型确认: high={k_mid['high']} right.close={k_right['close']} < mid.low={k_mid['low']}",
    }


def calculate_atr_1m(klines_1m: list[dict], period: int = 15) -> float:
    """计算 1 分钟级 ATR（Wilder's method）。

    用于双风挡缓冲线计算：
    - 结构止损 = ZD - 1.2 × ATR_1m
    - 灾难止损 = 入场价 × 0.97

    Args:
        klines_1m: CLOSED 1M K线列表（时间升序），至少 period+1 根。
        period: ATR 周期（默认 15）

    Returns:
        ATR 值（元），数据不足时返回 0.0
    """
    if len(klines_1m) < 2:
        return 0.0

    # 计算 True Range 序列
    trs = []
    for i in range(1, len(klines_1m)):
        prev_close = klines_1m[i - 1]["close"]
        curr = klines_1m[i]
        tr = max(
            curr["high"] - curr["low"],
            abs(curr["high"] - prev_close),
            abs(curr["low"] - prev_close),
        )
        trs.append(tr)

    if not trs:
        return 0.0

    # Wilder's smoothing：先取前 period 根的简单平均，再逐步平滑
    use_trs = trs[-max(period * 2, len(trs)):]  # 使用足够多数据
    if len(use_trs) < period:
        return round(sum(use_trs) / len(use_trs), 4)

    # 初始 ATR = 前 period 根的简单平均
    atr = sum(use_trs[:period]) / period
    for tr in use_trs[period:]:
        atr = (atr * (period - 1) + tr) / period

    return round(atr, 4)
