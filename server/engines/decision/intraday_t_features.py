"""Feature extraction for the intraday T paper simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IntradayTFeatures:
    symbol: str
    as_of: str
    level_chain: dict[str, str]
    paths: dict[str, str]
    pattern_tags: list[str] = field(default_factory=list)
    position_to_center: dict[str, Any] = field(default_factory=dict)
    latest_event: dict[str, Any] = field(default_factory=dict)
    divergence: dict[str, Any] = field(default_factory=dict)
    momentum: dict[str, Any] = field(default_factory=dict)
    volatility: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)
    parent_context: dict[str, Any] = field(default_factory=dict)
    current_price: float = 0.0

    @property
    def is_stale(self) -> bool:
        return bool(self.freshness.get("is_stale", False))

    @property
    def latest_event_side(self) -> str:
        return str(self.latest_event.get("side") or "")

    @property
    def bars_since_event(self) -> int:
        value = self.latest_event.get("bars_since_event")
        if value is None:
            return 999999
        return int(value)

    @property
    def divergence_direction(self) -> str:
        return str(self.divergence.get("direction") or "")

    @property
    def divergence_strength(self) -> float:
        return float(self.divergence.get("strength", 0) or 0)


def extract_intraday_t_features(
    radar_output: dict[str, Any],
    *,
    symbol: str,
    as_of: str,
    trigger_klines: list[dict[str, Any]] | None = None,
) -> IntradayTFeatures:
    """Compress Radar v2 output into stable factors for a paper strategy."""
    atoms = radar_output.get("atoms") or {}
    level_chain = dict(radar_output.get("level_chain") or {})
    trigger_atom = atoms.get("L2") or atoms.get("trigger") or {}
    structure_atom = atoms.get("L1") or {}

    divergence = _divergence(trigger_atom)
    latest_event = _latest_event(trigger_atom, trigger_klines or [], divergence.get("direction", ""))
    momentum = dict(trigger_atom.get("momentum_compare") or {})
    volatility = _volatility(trigger_klines or [])

    return IntradayTFeatures(
        symbol=symbol,
        as_of=as_of,
        level_chain=level_chain,
        paths=_paths(radar_output, atoms),
        pattern_tags=_pattern_tags(radar_output),
        position_to_center=_position_to_center(structure_atom),
        latest_event=latest_event,
        divergence=divergence,
        momentum=momentum,
        volatility=volatility,
        freshness=dict(radar_output.get("freshness") or {}),
        parent_context=_parent_context(symbol, as_of, level_chain, atoms),
        current_price=_current_price(trigger_klines or []),
    )


def _paths(radar_output: dict[str, Any], atoms: dict[str, Any]) -> dict[str, str]:
    paths = {"main": str(radar_output.get("path") or "NO_EDGE")}
    for role, atom in atoms.items():
        paths[role] = str(atom.get("position_state") or atom.get("raw_state") or "UNKNOWN")
    return paths


def _pattern_tags(radar_output: dict[str, Any]) -> list[str]:
    patterns = radar_output.get("patterns") or []
    tags: list[str] = []
    for item in patterns:
        if isinstance(item, str):
            tags.append(item)
        elif isinstance(item, dict):
            code = item.get("pattern") or item.get("pattern_id") or item.get("code")
            if code:
                tags.append(str(code))
    return tags


def _position_to_center(atom: dict[str, Any]) -> dict[str, Any]:
    center = atom.get("center") or {}
    price = _num(atom.get("price"))
    zg = _num(center.get("zg"))
    zd = _num(center.get("zd"))
    atr = max(abs(zg - zd), 0.0001)
    return {
        "position_state": atom.get("position_state") or "UNKNOWN",
        "price": price,
        "distance_to_zg_atr": round((price - zg) / atr, 4) if price and zg else 0.0,
        "distance_to_zd_atr": round((price - zd) / atr, 4) if price and zd else 0.0,
        "zg": zg,
        "zd": zd,
    }


def _latest_event(atom: dict[str, Any], trigger_klines: list[dict[str, Any]], divergence_direction: str = "") -> dict[str, Any]:
    events = list(atom.get("event_sequence") or [])
    if not events:
        return {}
    preferred_side = ""
    if divergence_direction == "bottom":
        preferred_side = "buy"
    elif divergence_direction == "top":
        preferred_side = "sell"
    if preferred_side:
        preferred = [event for event in events if event.get("side") == preferred_side]
        if preferred:
            events = preferred
    event = dict(events[-1])
    event_time = str(event.get("time") or "")
    event["bars_since_event"] = _bars_since(trigger_klines, event_time)
    return event


def _bars_since(klines: list[dict[str, Any]], event_time: str) -> int:
    if not klines or not event_time:
        return 999999
    times = [str(k.get("time") or k.get("date") or "") for k in klines]
    try:
        return max(0, len(times) - 1 - times.index(event_time))
    except ValueError:
        return 999999


def _divergence(atom: dict[str, Any]) -> dict[str, Any]:
    raw = dict(atom.get("divergence") or {})
    momentum = dict(atom.get("momentum_compare") or {})
    direction = str(raw.get("direction") or "")
    if not direction and momentum.get("is_weaker"):
        direction = "top" if momentum.get("direction") == "up" else "bottom"
    strength = raw.get("strength")
    if strength is None:
        strength = momentum.get("combined_score", 0)
    return {
        "direction": direction,
        "strength": float(strength or 0),
        "is_valid": bool(raw.get("is_valid", False) or momentum.get("is_weaker", False)),
    }


def _volatility(klines: list[dict[str, Any]]) -> dict[str, Any]:
    if len(klines) < 2:
        return {"atr": 0.0, "atr_percentile": 0.0}
    ranges = [_num(k.get("high")) - _num(k.get("low")) for k in klines[-20:]]
    ranges = [r for r in ranges if r >= 0]
    atr = sum(ranges) / len(ranges) if ranges else 0.0
    if not ranges or atr <= 0:
        percentile = 0.0
    else:
        percentile = sum(1 for r in ranges if r <= ranges[-1]) / len(ranges)
    return {"atr": round(atr, 4), "atr_percentile": round(percentile, 4)}


def _current_price(klines: list[dict[str, Any]]) -> float:
    if not klines:
        return 0.0
    return _num((klines[-1] or {}).get("close"))


def _parent_context(symbol: str, as_of: str, level_chain: dict[str, str], atoms: dict[str, Any]) -> dict[str, Any]:
    atom = atoms.get("L0") or {}
    parent_level = str(level_chain.get("L0") or atom.get("public_level") or "")
    last_bi_dir = _normalize_bi_dir(atom.get("last_bi_dir"))
    if last_bi_dir == "down":
        parent_task = "DOWN_LEG"
        allowed_first_side = "SELL"
    elif last_bi_dir == "up":
        parent_task = "UP_LEG"
        allowed_first_side = "BUY"
    else:
        parent_task = ""
        allowed_first_side = ""

    last_bi = dict(atom.get("last_bi") or {})
    leg_id = _parent_leg_id(symbol, parent_level, last_bi_dir, last_bi, atom, as_of)
    return {
        "parent_level": parent_level,
        "parent_task": parent_task,
        "parent_leg_id": leg_id,
        "allowed_first_side": allowed_first_side,
        "last_bi_dir": last_bi_dir,
        "last_bi": last_bi,
    }


def _parent_leg_id(
    symbol: str,
    parent_level: str,
    last_bi_dir: str,
    last_bi: dict[str, Any],
    atom: dict[str, Any],
    as_of: str,
) -> str:
    x0 = str(last_bi.get("x0") or "")
    x1 = str(last_bi.get("x1") or "")
    if x0 or x1:
        return f"{symbol}:{parent_level}:{last_bi_dir}:{x0}:{x1}"
    center = atom.get("center") or {}
    center_end = str(center.get("end") or center.get("end_date") or "")
    if center_end:
        return f"{symbol}:{parent_level}:{last_bi_dir}:center:{center_end}"
    return f"{symbol}:{parent_level}:{last_bi_dir}:{as_of}"


def _normalize_bi_dir(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"up", "向上", "向上（未确认）"}:
        return "up"
    if text in {"down", "向下", "向下（未确认）"}:
        return "down"
    return ""


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
