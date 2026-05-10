"""Compile Radar algorithm_v2 atoms into Semantic Signal V2 short codes."""

from __future__ import annotations

from server.engines.signal.models import SignalCode


LEVEL_CODE = {
    "week": "w",
    "w": "w",
    "day": "d1",
    "d1": "d1",
    "60": "m60",
    "m60": "m60",
    "30": "m30",
    "m30": "m30",
    "15": "m15",
    "m15": "m15",
    "5": "m5",
    "m5": "m5",
}

BSP_PATTERN = {
    "B1": "bs1",
    "B1P": "bs1",
    "B2": "bs2",
    "B2S": "bs2",
    "B3A": "bs3",
    "B3B": "bs3",
    "S1": "ss1",
    "S1P": "ss1",
    "S2": "ss2",
    "S2S": "ss2",
    "S3A": "ss3",
    "S3B": "ss3",
}


def compile_signal(atom: dict, algorithm_v2: dict | None = None) -> SignalCode:
    """Compile one LevelAtom dict into a semantic short-code object.

    algorithm_v2 只作为路径兜底输入；结构事实优先来自 atom 本身。
    """
    algorithm_v2 = algorithm_v2 or {}
    return SignalCode(
        level=_level_to_code(str(atom.get("public_level") or atom.get("level") or "")),
        position=_position_from_atom(atom),
        pattern=_pattern_from_atom(atom, algorithm_v2),
        strength=_strength_from_atom(atom),
    )


def choose_primary_atom(algorithm_v2: dict) -> tuple[str, dict]:
    """Pick the atom whose signal should anchor the Radar card."""
    atoms = algorithm_v2.get("atoms") or {}
    candidates = [(role, atom) for role, atom in atoms.items() if isinstance(atom, dict)]
    if not candidates:
        return "", {}

    def score(item: tuple[str, dict]) -> tuple[int, int]:
        role, atom = item
        pattern = _pattern_from_atom(atom, algorithm_v2)
        role_rank = {"L0": 3, "L1": 2, "L2": 1}.get(role, 0)
        if pattern in {"bs1", "bs2", "bs3", "ss1", "ss2", "ss3"}:
            return (40, role_rank)
        if pattern in {"top_div", "bot_div"}:
            return (30, role_rank)
        if pattern in {"breakout", "pullback"}:
            return (20, role_rank)
        if pattern in {"trend_up", "trend_down"}:
            return (10, role_rank)
        return (0, role_rank)

    return max(candidates, key=score)


def calc_strength(macd_area_ratio: float | int | None) -> str:
    """Map MACD area ratio to strength."""
    try:
        ratio = float(macd_area_ratio)
    except (TypeError, ValueError):
        return "weak"
    if ratio <= 0:
        return "weak"
    if ratio < 0.5:
        return "strong"
    if ratio < 0.8:
        return "medium"
    return "weak"


def _level_to_code(level: str) -> str:
    return LEVEL_CODE.get(level.lower(), "unknown")


def _position_from_atom(atom: dict) -> str:
    state = str(atom.get("position_state") or "").upper()
    if state in {"UP_LEAVING", "UP_RETEST"}:
        return "zs_above"
    if state in {"DOWN_LEAVING", "DOWN_PULLBACK"}:
        return "zs_below"
    if state == "CENTER_INSIDE":
        return "zs_inside"

    price = _num(atom.get("price"))
    center = atom.get("center") or {}
    zd = _num(center.get("zd"))
    zg = _num(center.get("zg"))
    if price > 0 and zd > 0 and zg > 0:
        if price > zg:
            return "zs_above"
        if price < zd:
            return "zs_below"
        return "zs_inside"
    return "unknown"


def _pattern_from_atom(atom: dict, algorithm_v2: dict) -> str:
    latest = _latest_event(atom)
    if latest:
        code = str(latest.get("code") or "").upper()
        if code in BSP_PATTERN:
            return BSP_PATTERN[code]

    divergence = atom.get("divergence") or {}
    direction = str(divergence.get("direction") or "")
    if direction == "top":
        return "top_div"
    if direction == "bottom":
        return "bot_div"

    path = str(algorithm_v2.get("path") or "")
    phase = str(algorithm_v2.get("phase") or "")
    if path == "UPWARD_MAJOR_WAVE" and phase == "BREAKOUT_EXTENSION":
        return "breakout"
    if path == "UPWARD_MAJOR_WAVE":
        return "trend_up"
    if path == "PULLBACK_IN_UPTREND":
        return "pullback"
    if path == "DOWNWARD_DEFENSE":
        return "trend_down"
    if path in {"HIGH_VOLATILITY_OSCILLATION", "CENTER_REBOUND", "NO_EDGE"}:
        return "range_osc"
    return "unknown"


def _strength_from_atom(atom: dict) -> str:
    momentum = atom.get("momentum_compare") or {}
    ratio = momentum.get("area_ratio")
    if ratio is not None:
        return calc_strength(ratio)
    divergence = atom.get("divergence") or {}
    severity = str(divergence.get("severity") or "").lower()
    if "强" in severity or "strong" in severity:
        return "strong"
    if "中" in severity or "medium" in severity:
        return "medium"
    return "weak"


def _latest_event(atom: dict) -> dict:
    events = [event for event in atom.get("event_sequence") or [] if isinstance(event, dict)]
    if events:
        current = [event for event in events if event.get("is_current")]
        return (current or events)[-1]
    combined = []
    combined.extend(event for event in atom.get("buy_events") or [] if isinstance(event, dict))
    combined.extend(event for event in atom.get("sell_events") or [] if isinstance(event, dict))
    if not combined:
        return {}
    current = [event for event in combined if event.get("is_current")]
    return (current or combined)[-1]


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
