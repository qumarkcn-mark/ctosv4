"""Pattern facts derived from bsps, state, and divergence."""

from typing import Optional


def derive_patterns(bsps: list, state: str, div_info: Optional[dict] = None) -> list[str]:
    patterns = []
    for bsp in bsps[-6:]:
        raw_type = str(bsp.get("type", ""))
        is_buy = bool(bsp.get("is_buy"))
        if is_buy:
            if raw_type.startswith("1"):
                patterns.append("一买")
            elif raw_type.startswith("2"):
                patterns.append("二买")
            elif raw_type.startswith("3"):
                patterns.append("三买")
        else:
            if raw_type.startswith("1"):
                patterns.append("1卖")
            elif raw_type.startswith("2"):
                patterns.append("二卖")
            elif raw_type.startswith("3"):
                patterns.append("三卖")

    if state == "THIRD_BUY_CONFIRMED":
        patterns.append("三买确认")
    elif state == "THIRD_SELL_CONFIRMED":
        patterns.append("三卖确认")

    if div_info:
        if div_info.get("type") == "顶背驰":
            patterns.append("趋势顶背驰")
        elif div_info.get("type") == "底背驰":
            patterns.append("趋势底背驰")

    return _dedupe(patterns)


def _dedupe(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
