"""Multi-level divergence context for AI Native Radar."""

from __future__ import annotations

from server.engines.ai_native.schemas import (
    BuySellCandidate,
    DivergenceChainStep,
    DivergenceContext,
    DivergenceSignal,
)


LOWER_LEVELS = ("1", "5", "15")
PIVOT_LEVELS = ("30", "60")


def build_divergence_context(radar_contract: dict) -> DivergenceContext:
    """把多级别背驰线索压缩成 AI 推演可消费的联动状态。

    第一版只使用现有结构事实，不重新计算缠论结构，避免和 Kline/Adapter 产生第二套真相。
    """
    structure = radar_contract.get("structure") or {}
    raw_levels = structure.get("levels") or {}
    algorithm = radar_contract.get("algorithm_v2") or {}
    pivot_key, pivot = _select_pivot_level(raw_levels)
    pivot_position = _pivot_position(pivot)
    lower_signals, pivot_signals = _collect_signals(raw_levels, pivot_key, pivot_position)
    macro_bias = _macro_bias(raw_levels, algorithm)
    alignment = _alignment(
        lower_signals=lower_signals,
        pivot_signals=pivot_signals,
        macro_bias=macro_bias,
        pivot_position=pivot_position,
    )
    zd = _num(pivot.get("zd") or (pivot.get("active_zhongshu") or {}).get("zd"))
    dd = _num(pivot.get("dd") or (pivot.get("active_zhongshu") or {}).get("dd"))
    zg = _num(pivot.get("zg") or (pivot.get("active_zhongshu") or {}).get("zg"))
    chain_direction = _chain_direction(lower_signals, pivot_signals)
    chain_status = _chain_status(alignment)
    buy_sell_candidate = _buy_sell_candidate(
        raw_levels=raw_levels,
        pivot_key=pivot_key,
        chain_direction=chain_direction,
        chain_status=chain_status,
        pivot_position=pivot_position,
        lower_signals=lower_signals,
        pivot_signals=pivot_signals,
        zd=zd,
        zg=zg,
        dd=dd,
    )

    return DivergenceContext(
        macro_bias=macro_bias,
        pivot_level=pivot_key,
        pivot_position=pivot_position,
        chain_direction=chain_direction,
        chain_status=chain_status,
        alignment=alignment,
        lower_level_signals=lower_signals,
        pivot_signals=pivot_signals,
        chain=_build_chain(
            raw_levels=raw_levels,
            pivot_key=pivot_key,
            pivot_position=pivot_position,
            pivot=pivot,
            macro_bias=macro_bias,
            lower_signals=lower_signals,
            pivot_signals=pivot_signals,
            alignment=alignment,
            zd=zd,
            zg=zg,
            dd=dd,
        ),
        buy_sell_candidate=buy_sell_candidate,
        upgrade_condition=_upgrade_condition(alignment, pivot_key, zd, zg),
        failure_condition=_failure_condition(alignment, pivot_key, zd, dd),
    )


def _select_pivot_level(raw_levels: dict) -> tuple[str, dict]:
    for key in PIVOT_LEVELS:
        level = raw_levels.get(key)
        if isinstance(level, dict) and _has_pivot_data(level):
            return key, level
    return "30", {}


def _has_pivot_data(level: dict) -> bool:
    active = level.get("active_zhongshu") or {}
    return any(_num(level.get(key) or active.get(key)) > 0 for key in ("zd", "zg", "dd", "gg"))


def _collect_signals(raw_levels: dict, pivot_key: str, pivot_position: str) -> tuple[list[DivergenceSignal], list[DivergenceSignal]]:
    lower_signals: list[DivergenceSignal] = []
    pivot_signals: list[DivergenceSignal] = []
    for key, level in raw_levels.items():
        level_key = str(key)
        if not isinstance(level, dict) or level_key not in (*LOWER_LEVELS, pivot_key):
            continue
        signal_type = _signal_type(level)
        if not signal_type:
            continue
        quality = _signal_quality(str(key), signal_type, pivot_position)
        evidence = _signal_evidence(level)
        status = _signal_status(
            signal_type=signal_type,
            pivot_position=pivot_position,
            evidence=evidence,
        )
        signal = DivergenceSignal(
            level=level_key,
            type=signal_type,
            status=status,
            quality=quality,
            evidence=evidence,
        )
        if level_key in LOWER_LEVELS:
            lower_signals.append(signal)
        else:
            pivot_signals.append(signal)
    return lower_signals, pivot_signals


def _alignment(
    *,
    lower_signals: list[DivergenceSignal],
    pivot_signals: list[DivergenceSignal],
    macro_bias: str,
    pivot_position: str,
) -> str:
    signals = lower_signals + pivot_signals
    if not signals:
        return "NO_DIVERGENCE"
    if any(signal.status == "FAILED" for signal in signals):
        return "FAILED_DIVERGENCE"
    has_bottom = any(signal.type == "BOTTOM" for signal in signals)
    has_top = any(signal.type == "TOP" for signal in signals)
    lower_bottom = any(signal.type == "BOTTOM" for signal in lower_signals)
    if has_top and pivot_position in {"ABOVE_GG", "ABOVE_ZG_WITHIN_GG", "IN_CENTER", "NEAR_ZD", "NEAR_DD"}:
        return "COUNTER_TREND_RISK"
    if has_top and macro_bias in {"DOWN", "WEAK"}:
        return "COUNTER_TREND_RISK"
    if has_bottom and pivot_position in {"ABOVE_ZG_WITHIN_GG", "IN_CENTER"}:
        return "CONFIRMED_SUPPORT"
    if lower_bottom and pivot_position in {"NEAR_ZD", "NEAR_DD"}:
        return "ALIGNING"
    if lower_bottom and pivot_position in {"BELOW_DD", "BELOW_ZD"}:
        return "LOW_LEVEL_ONLY"
    if has_bottom:
        return "ALIGNING"
    return "NO_DIVERGENCE"


def _chain_direction(lower_signals: list[DivergenceSignal], pivot_signals: list[DivergenceSignal]) -> str:
    signals = lower_signals + pivot_signals
    if any(signal.type == "TOP" for signal in signals):
        return "TOP"
    if any(signal.type == "BOTTOM" for signal in signals):
        return "BOTTOM"
    if any(signal.type == "GENERIC" for signal in signals):
        return "GENERIC"
    return "UNKNOWN"


def _chain_status(alignment: str) -> str:
    return {
        "NO_DIVERGENCE": "NO_CHAIN",
        "LOW_LEVEL_ONLY": "LOWER_ONLY",
        "ALIGNING": "ALIGNING",
        "CONFIRMED_SUPPORT": "CONFIRMED",
        "FAILED_DIVERGENCE": "FAILED",
        "COUNTER_TREND_RISK": "COUNTER_RISK",
    }.get(alignment, "NO_CHAIN")


def _build_chain(
    *,
    raw_levels: dict,
    pivot_key: str,
    pivot_position: str,
    pivot: dict,
    macro_bias: str,
    lower_signals: list[DivergenceSignal],
    pivot_signals: list[DivergenceSignal],
    alignment: str,
    zd: float,
    zg: float,
    dd: float,
) -> list[DivergenceChainStep]:
    direction = _chain_direction(lower_signals, pivot_signals)
    return [
        _macro_chain_step(raw_levels, macro_bias, direction),
        _pivot_chain_step(pivot_key, pivot_position, pivot_signals, direction, zd, zg, dd),
        _trigger_chain_step(lower_signals, direction),
        _confirmation_chain_step(alignment, pivot_key, direction, zd, zg, dd),
    ]


def _macro_chain_step(raw_levels: dict, macro_bias: str, direction: str) -> DivergenceChainStep:
    level = "week" if isinstance(raw_levels.get("week"), dict) else "day"
    macro_level = raw_levels.get(level) if isinstance(raw_levels.get(level), dict) else {}
    status = "WAITING"
    if direction == "BOTTOM" and macro_bias == "UP":
        status = "SUPPORTS"
    elif direction == "TOP" and macro_bias in {"DOWN", "WEAK"}:
        status = "SUPPORTS"
    elif direction in {"BOTTOM", "TOP"} and macro_bias not in {"UNKNOWN", "RANGE"}:
        status = "BLOCKS"
    evidence = [f"macro_bias={macro_bias}"]
    state = macro_level.get("state")
    if state:
        evidence.append(f"{level} state={state}")
    return DivergenceChainStep(
        role="macro",
        level=level,
        direction=direction if direction in {"BOTTOM", "TOP", "GENERIC"} else "UNKNOWN",
        status=status,
        evidence=evidence,
        note="先看大级别环境是否支持该背驰方向",
    )


def _pivot_chain_step(
    pivot_key: str,
    pivot_position: str,
    pivot_signals: list[DivergenceSignal],
    direction: str,
    zd: float,
    zg: float,
    dd: float,
) -> DivergenceChainStep:
    status = "WAITING"
    if any(signal.status == "FAILED" for signal in pivot_signals):
        status = "FAILED"
    elif direction == "BOTTOM" and pivot_position in {"IN_CENTER", "ABOVE_ZG_WITHIN_GG"}:
        status = "CONFIRMED"
    elif direction == "BOTTOM" and pivot_position in {"NEAR_ZD", "NEAR_DD"}:
        status = "SUPPORTS"
    elif direction == "BOTTOM" and pivot_position in {"BELOW_DD", "BELOW_ZD"}:
        status = "BLOCKS"
    elif direction == "TOP" and pivot_position in {"ABOVE_GG", "ABOVE_ZG_WITHIN_GG", "IN_CENTER"}:
        status = "SUPPORTS"
    boundary = _first_positive(zd, dd, zg)
    evidence = [f"{pivot_key} position={pivot_position}"]
    evidence.extend(_first_signal_evidence(pivot_signals))
    return DivergenceChainStep(
        role="pivot",
        level=pivot_key,
        direction=direction if direction in {"BOTTOM", "TOP", "GENERIC"} else "UNKNOWN",
        status=status,
        evidence=evidence,
        boundary=boundary,
        note="枢纽级别决定背驰是线索、确认，还是失效",
    )


def _trigger_chain_step(lower_signals: list[DivergenceSignal], direction: str) -> DivergenceChainStep:
    if not lower_signals:
        return DivergenceChainStep(
            role="trigger",
            direction=direction if direction in {"BOTTOM", "TOP", "GENERIC"} else "UNKNOWN",
            status="WAITING",
            evidence=["no_lower_level_signal"],
            note="等待 5/15 分钟级别给出触发线索",
        )
    signal = max(lower_signals, key=lambda item: item.quality)
    status = "FAILED" if signal.status == "FAILED" else "SUPPORTS"
    return DivergenceChainStep(
        role="trigger",
        level=signal.level,
        direction=signal.type,
        status=status,
        evidence=signal.evidence,
        note="小级别背驰只能作为触发线索，必须回到枢纽边界验证",
    )


def _confirmation_chain_step(
    alignment: str,
    pivot_key: str,
    direction: str,
    zd: float,
    zg: float,
    dd: float,
) -> DivergenceChainStep:
    if alignment == "CONFIRMED_SUPPORT":
        status = "CONFIRMED"
        boundary = zg or zd
        note = "背驰线索已和枢纽边界完成联动"
    elif alignment == "FAILED_DIVERGENCE":
        status = "FAILED"
        boundary = dd or zd
        note = "背驰线索已经被价格行为破坏"
    elif alignment in {"LOW_LEVEL_ONLY", "ALIGNING"}:
        status = "WAITING"
        boundary = zd or dd
        note = "等待重新站回枢纽边界后再升级"
    elif alignment == "COUNTER_TREND_RISK":
        status = "SUPPORTS"
        boundary = zg or zd
        note = "压力区反向背驰优先作为风险链条处理"
    else:
        status = "WAITING"
        boundary = None
        note = "没有形成可追踪的背驰链条"
    return DivergenceChainStep(
        role="confirmation",
        level=pivot_key,
        direction=direction if direction in {"BOTTOM", "TOP", "GENERIC"} else "UNKNOWN",
        status=status,
        evidence=[f"alignment={alignment}"],
        boundary=boundary,
        note=note,
    )


def _buy_sell_candidate(
    *,
    raw_levels: dict,
    pivot_key: str,
    chain_direction: str,
    chain_status: str,
    pivot_position: str,
    lower_signals: list[DivergenceSignal],
    pivot_signals: list[DivergenceSignal],
    zd: float,
    zg: float,
    dd: float,
) -> BuySellCandidate:
    if chain_direction == "BOTTOM":
        return _bottom_buy_candidate(
            raw_levels=raw_levels,
            pivot_key=pivot_key,
            chain_status=chain_status,
            pivot_position=pivot_position,
            lower_signals=lower_signals,
            pivot_signals=pivot_signals,
            zd=zd,
            zg=zg,
            dd=dd,
        )
    if chain_direction == "TOP":
        return _top_sell_candidate(
            raw_levels=raw_levels,
            pivot_key=pivot_key,
            chain_status=chain_status,
            pivot_position=pivot_position,
            lower_signals=lower_signals,
            pivot_signals=pivot_signals,
            zd=zd,
            zg=zg,
        )
    return BuySellCandidate(note="没有形成买卖点候选")


def _bottom_buy_candidate(
    *,
    raw_levels: dict,
    pivot_key: str,
    chain_status: str,
    pivot_position: str,
    lower_signals: list[DivergenceSignal],
    pivot_signals: list[DivergenceSignal],
    zd: float,
    zg: float,
    dd: float,
) -> BuySellCandidate:
    patterns = _candidate_patterns(raw_levels, pivot_key)
    recent_bsps = _recent_bsps(raw_levels, is_buy=True)
    evidence = _candidate_evidence(lower_signals, pivot_signals, patterns, recent_bsps)
    latest_type = _latest_bsp_type(recent_bsps)
    if _has_pattern(patterns, "三买确认") or (
        chain_status == "CONFIRMED" and (_has_pattern(patterns, "三买") or latest_type.startswith("3"))
    ):
        return BuySellCandidate(
            side="BUY",
            kind="THIRD_CONFIRM",
            status="CONFIRMED",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zg or zd,
            invalidation_boundary=zd or dd,
            note="三买候选来自已存在的买卖点/结构事实，不由 AI 重新判定",
        )
    if _has_pattern(patterns, "二买") or latest_type.startswith("2"):
        return BuySellCandidate(
            side="BUY",
            kind="SECOND_WAIT",
            status="WAITING_CONFIRM" if chain_status != "CONFIRMED" else "CONFIRMED",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zg or zd,
            invalidation_boundary=dd or zd,
            note="二买候选需要确认回踩不破前低或重新站稳枢纽边界",
        )
    if chain_status == "CONFIRMED":
        return BuySellCandidate(
            side="BUY",
            kind="FIRST_CANDIDATE",
            status="WAITING_CONFIRM",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zg or zd,
            invalidation_boundary=dd or zd,
            note="背驰链条确认，只能标记为一买候选，仍需后续二买/三买转换验证",
        )
    if chain_status in {"LOWER_ONLY", "ALIGNING"}:
        return BuySellCandidate(
            side="BUY",
            kind="FIRST_CANDIDATE",
            status="SIGNAL_ONLY",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zd or dd,
            invalidation_boundary=dd or zd,
            note="小级别底背驰只是候选线索，必须回到枢纽边界验证",
        )
    if chain_status == "FAILED":
        return BuySellCandidate(
            side="BUY",
            kind="FIRST_CANDIDATE",
            status="INVALID",
            level=pivot_key,
            evidence=evidence,
            invalidation_boundary=dd or zd,
            note="底背驰候选已被价格行为破坏",
        )
    return BuySellCandidate(note=f"pivot_position={pivot_position} 未形成买点候选")


def _top_sell_candidate(
    *,
    raw_levels: dict,
    pivot_key: str,
    chain_status: str,
    pivot_position: str,
    lower_signals: list[DivergenceSignal],
    pivot_signals: list[DivergenceSignal],
    zd: float,
    zg: float,
) -> BuySellCandidate:
    patterns = _candidate_patterns(raw_levels, pivot_key)
    recent_bsps = _recent_bsps(raw_levels, is_buy=False)
    evidence = _candidate_evidence(lower_signals, pivot_signals, patterns, recent_bsps)
    latest_type = _latest_bsp_type(recent_bsps)
    if (_has_pattern(patterns, "三卖") or latest_type.startswith("3")) and pivot_position in {"BELOW_ZD", "BELOW_DD"}:
        return BuySellCandidate(
            side="SELL",
            kind="THIRD_SELL_CONFIRM",
            status="CONFIRMED",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zd or zg,
            invalidation_boundary=zg or zd,
            note="三卖候选来自已存在的买卖点/结构事实",
        )
    if _has_pattern(patterns, "三卖") or latest_type.startswith("3"):
        return BuySellCandidate(
            side="SELL",
            kind="FIRST_SELL_RISK",
            status="WAITING_CONFIRM",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zd or zg,
            invalidation_boundary=zg or zd,
            note="出现三卖类事件/标签，但价格未处于标准三卖位置，只能作为卖点风险线索",
        )
    if _has_pattern(patterns, "二卖") or latest_type.startswith("2"):
        return BuySellCandidate(
            side="SELL",
            kind="SECOND_SELL_WAIT",
            status="WAITING_CONFIRM",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zd or zg,
            invalidation_boundary=zg or zd,
            note="二卖候选需要后续反抽不重新转强验证",
        )
    if chain_status in {"CONFIRMED", "COUNTER_RISK"}:
        return BuySellCandidate(
            side="SELL",
            kind="FIRST_SELL_RISK",
            status="WAITING_CONFIRM",
            level=pivot_key,
            evidence=evidence,
            trigger_boundary=zd or zg,
            invalidation_boundary=zg or zd,
            note="顶背驰链条只标记为卖点风险候选，不等于操作命令",
        )
    return BuySellCandidate(note=f"pivot_position={pivot_position} 未形成卖点候选")


def _macro_bias(raw_levels: dict, algorithm: dict) -> str:
    text = " ".join(
        str(item or "")
        for item in (
            algorithm.get("path"),
            algorithm.get("phase"),
            (raw_levels.get("week") or {}).get("state") if isinstance(raw_levels.get("week"), dict) else "",
            (raw_levels.get("day") or {}).get("state") if isinstance(raw_levels.get("day"), dict) else "",
        )
    ).upper()
    words = set(text.replace("-", "_").replace("/", "_").split("_"))
    if any(token in text for token in ("下跌", "转弱")) or words & {"DOWN", "DOWNWARD", "DOWNTREND", "WEAK"}:
        return "DOWN"
    if any(token in text for token in ("上升", "上涨", "向上")) or words & {"UPWARD", "UPTREND", "BREAKOUT", "BULL"}:
        return "UP"
    if any(token in text for token in ("OSC", "震荡", "CENTER")):
        return "RANGE"
    return "UNKNOWN"


def _pivot_position(level: dict) -> str:
    explicit = str(level.get("price_relation") or level.get("position_state") or level.get("center_relation") or "").upper()
    for token in ("BELOW_DD", "BELOW_ZD", "NEAR_DD", "NEAR_ZD", "IN_CENTER", "ABOVE_ZG_WITHIN_GG", "ABOVE_GG"):
        if token in explicit:
            return token
    price = _num(level.get("price"))
    active = level.get("active_zhongshu") or {}
    zd = _num(level.get("zd") or active.get("zd"))
    zg = _num(level.get("zg") or active.get("zg"))
    dd = _num(level.get("dd") or active.get("dd"))
    gg = _num(level.get("gg") or active.get("gg"))
    if price <= 0:
        return explicit or "UNKNOWN"
    if zd > 0 and _near(price, zd):
        return "NEAR_ZD"
    if dd > 0 and _near(price, dd):
        return "NEAR_DD"
    if dd > 0 and price < dd:
        return "BELOW_DD"
    if zd > 0 and price < zd:
        return "BELOW_ZD"
    if zd > 0 and zg > 0 and zd <= price <= zg:
        return "IN_CENTER"
    if zg > 0 and gg > 0 and zg < price <= gg:
        return "ABOVE_ZG_WITHIN_GG"
    if gg > 0 and price > gg:
        return "ABOVE_GG"
    return explicit or "UNKNOWN"


def _signal_type(level: dict) -> str | None:
    explicit_bsp = _latest_explicit_bsp_direction(level)
    if explicit_bsp:
        return explicit_bsp

    text = " ".join(str(item) for item in _signal_evidence(level))
    if any(token in text for token in ("底背驰", "一买", "二买", "三买", "bottom_divergence", "BOTTOM_DIVERGENCE")):
        return "BOTTOM"
    if any(token in text for token in ("顶背驰", "1卖", "一卖", "二卖", "三卖", "top_divergence", "TOP_DIVERGENCE")):
        return "TOP"
    if "背驰" in text or "divergence" in text.lower():
        return "GENERIC"
    return None


def _signal_evidence(level: dict) -> list[str]:
    evidence: list[str] = []
    for key in ("patterns", "signals", "tags"):
        value = level.get(key)
        if isinstance(value, list):
            evidence.extend(str(item) for item in value[:5])
        elif value:
            evidence.append(str(value))
    for key in ("divergence", "divergence_state"):
        value = level.get(key)
        if value:
            evidence.append(str(value))
    for bsp in _explicit_bsps(level)[-3:]:
        side = "买点候选" if bsp.get("is_buy") else "卖点风险"
        evidence.append(f"{side}:{bsp.get('type')}@{bsp.get('price')}")
    return evidence


def _latest_explicit_bsp_direction(level: dict) -> str | None:
    bsps = _explicit_bsps(level)
    if not bsps:
        return None
    return "BOTTOM" if bsps[-1].get("is_buy") else "TOP"


def _explicit_bsps(level: dict) -> list[dict]:
    return [
        bsp
        for bsp in level.get("bsps") or []
        if isinstance(bsp, dict) and isinstance(bsp.get("is_buy"), bool)
    ]


def _signal_status(signal_type: str, pivot_position: str, evidence: list[str]) -> str:
    joined = " ".join(evidence).upper()
    if any(token in joined for token in ("FAILED", "INVALID", "失效", "破坏")):
        return "FAILED"
    if signal_type == "BOTTOM" and pivot_position in {"IN_CENTER", "ABOVE_ZG_WITHIN_GG"}:
        return "CONFIRMED"
    if signal_type == "TOP" and pivot_position in {"ABOVE_GG", "ABOVE_ZG_WITHIN_GG"}:
        return "CONFIRMED"
    return "SUSPECTED"


def _signal_quality(level: str, signal_type: str, pivot_position: str) -> float:
    quality = 0.35
    if level in {"5", "15", "30"}:
        quality += 0.1
    if signal_type == "BOTTOM" and pivot_position in {"NEAR_ZD", "NEAR_DD", "IN_CENTER"}:
        quality += 0.15
    if signal_type == "BOTTOM" and pivot_position in {"BELOW_DD", "BELOW_ZD"}:
        quality += 0.05
    if signal_type == "TOP" and pivot_position in {"ABOVE_GG", "ABOVE_ZG_WITHIN_GG"}:
        quality += 0.15
    return round(min(quality, 0.8), 2)


def _upgrade_condition(alignment: str, level: str, zd: float, zg: float) -> str:
    if alignment in {"LOW_LEVEL_ONLY", "ALIGNING"} and zd > 0:
        return f"小级别背驰必须重新站回 {level} 分钟中枢下沿 {zd:.2f}，才从线索升级为支撑确认。"
    if alignment == "CONFIRMED_SUPPORT" and zg > 0:
        return f"已进入支撑确认观察，下一步看能否继续收复 {level} 分钟中枢上沿 {zg:.2f}。"
    return "没有明确背驰联动，先等待小级别止跌结构和中枢边界重新确认。"


def _failure_condition(alignment: str, level: str, zd: float, dd: float) -> str:
    if dd > 0:
        return f"若继续跌破 {level} 分钟中枢低点 {dd:.2f} 且不能快速收回，背驰线索按失效处理。"
    if zd > 0:
        return f"若不能重新站回 {level} 分钟中枢下沿 {zd:.2f}，小级别背驰只按弱线索处理。"
    if alignment == "NO_DIVERGENCE":
        return "没有背驰证据时，不把下跌放缓自动当成止跌。"
    return "若后续价格行为不能回到更高级别中枢边界内，背驰线索降级。"


def _near(price: float, boundary: float) -> bool:
    if boundary <= 0:
        return False
    return abs(price - boundary) / boundary <= 0.01


def _num(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_positive(*values: float) -> float | None:
    return next((value for value in values if value > 0), None)


def _first_signal_evidence(signals: list[DivergenceSignal]) -> list[str]:
    for signal in signals:
        if signal.evidence:
            return signal.evidence[:3]
    return []


def _all_patterns(raw_levels: dict) -> list[str]:
    patterns: list[str] = []
    for level in raw_levels.values():
        if isinstance(level, dict) and isinstance(level.get("patterns"), list):
            patterns.extend(str(item) for item in level.get("patterns") or [])
    return patterns


def _candidate_patterns(raw_levels: dict, pivot_key: str) -> list[str]:
    patterns: list[str] = []
    for level_key in (*LOWER_LEVELS, pivot_key):
        level = raw_levels.get(level_key)
        if isinstance(level, dict) and isinstance(level.get("patterns"), list):
            patterns.extend(str(item) for item in level.get("patterns") or [])
    return patterns


def _recent_bsps(raw_levels: dict, *, is_buy: bool) -> list[dict]:
    result = []
    for level_key, level in raw_levels.items():
        if not isinstance(level, dict):
            continue
        for bsp in level.get("bsps") or []:
            if isinstance(bsp, dict) and bool(bsp.get("is_buy")) is is_buy:
                result.append({"level": str(level_key), **bsp})
    return result[-6:]


def _latest_bsp_type(bsps: list[dict]) -> str:
    if not bsps:
        return ""
    return str(bsps[-1].get("type") or "")


def _has_pattern(patterns: list[str], token: str) -> bool:
    return any(token in pattern for pattern in patterns)


def _candidate_evidence(
    lower_signals: list[DivergenceSignal],
    pivot_signals: list[DivergenceSignal],
    patterns: list[str],
    recent_bsps: list[dict],
) -> list[str]:
    evidence = []
    for signal in lower_signals + pivot_signals:
        evidence.extend(signal.evidence[:2])
    evidence.extend(patterns[-4:])
    for bsp in recent_bsps[-2:]:
        evidence.append(f"{bsp.get('level')} BSP {bsp.get('type')} {bsp.get('price')}")
    return evidence[:8]
