"""Radar path classification v2.

本模块只消费 chan_adapter 已经产出的结构事实，不查库、不取行情、不调用 AI。
目标是先把多级别走势归入可测试的主路径，后续边界与三场景推演再消费这里的输出。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from server.engines.decision.radar_patterns import build_transition, detect_patterns
from server.engines.structure.divergence import build_momentum_compare


ALGORITHM_VERSION = "radar_algorithm.v2.phase1"
DISCLAIMER = "仅供参考，不构成投资建议"

DEFAULT_LEVEL_CHAIN = {
    "L0": "day",
    "L1": "30",
    "L2": "5",
}

BUY_BSP_TYPES = {
    "1": ("B1", "FIRST_BUY", "一买", "CChan BSP_TYPE.T1"),
    "1p": ("B1P", "FIRST_BUY", "类一买", "CChan BSP_TYPE.T1P"),
    "2": ("B2", "SECOND_BUY", "二买", "CChan BSP_TYPE.T2"),
    "2s": ("B2S", "SECOND_BUY", "类二买", "CChan BSP_TYPE.T2S"),
    "3a": ("B3A", "THIRD_BUY", "三买A", "CChan BSP_TYPE.T3A"),
    "3b": ("B3B", "THIRD_BUY", "三买B", "CChan BSP_TYPE.T3B"),
}

SELL_BSP_TYPES = {
    key: (code.replace("B", "S", 1), family.replace("BUY", "SELL"), name.replace("买", "卖"), source)
    for key, (code, family, name, source) in BUY_BSP_TYPES.items()
}

UP_RAW_STATES = {"UPWARD_LEAVING", "THIRD_BUY_CONFIRMED"}
DOWN_RAW_STATES = {"DOWNWARD_LEAVING", "THIRD_SELL_CONFIRMED"}
SELL_PATTERNS = ("顶背驰", "一卖", "1卖", "二卖", "三卖", "三卖确认")
BUY_PATTERNS = ("底背驰", "一买", "二买", "三买", "类一买", "类二买")


@dataclass(frozen=True)
class Center:
    zd: float = 0.0
    zg: float = 0.0
    dd: float = 0.0
    gg: float = 0.0
    start: str = ""
    end: str = ""
    is_valid: bool = False


@dataclass(frozen=True)
class BspEvent:
    raw_type: str
    code: str
    family: str
    name: str
    display: str
    source: str
    is_buy: bool
    time: str = ""
    price: float = 0.0
    is_current: bool = False
    center_binding: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Divergence:
    type: str = ""
    direction: str = ""
    severity: str = ""
    is_valid: bool = False


@dataclass(frozen=True)
class LevelAtom:
    level: str
    public_level: str
    price: float = 0.0
    raw_state: str = "UNKNOWN"
    position_state: str = "UNKNOWN"
    center: Center = field(default_factory=Center)
    previous_center: Center = field(default_factory=Center)
    historical_centers: list[Center] = field(default_factory=list)
    center_relation: str = "UNKNOWN"
    leave_return_status: dict = field(default_factory=dict)
    last_bi_dir: str = "unknown"
    buy_events: list[BspEvent] = field(default_factory=list)
    sell_events: list[BspEvent] = field(default_factory=list)
    event_sequence: list[dict] = field(default_factory=list)
    center_binding: dict = field(default_factory=dict)
    momentum_compare: dict = field(default_factory=dict)
    historical_high: dict = field(default_factory=dict)
    divergence: Divergence = field(default_factory=Divergence)
    tags: list[str] = field(default_factory=list)
    quality: str = "OK"


def build_radar_algorithm_v2(
    levels: dict,
    freshness: Optional[dict] = None,
    level_chain: Optional[dict] = None,
    disclaimer: str = DISCLAIMER,
) -> dict:
    """Build phase-1 Radar v2 output from public/legacy level dictionaries."""
    chain = dict(DEFAULT_LEVEL_CHAIN)
    if level_chain:
        chain.update(level_chain)

    atoms = {
        role: build_level_atom(_level(levels, public_level), public_level)
        for role, public_level in chain.items()
    }
    classification = classify_path_v2(atoms, freshness=freshness or {})
    center_nesting = build_center_nesting(atoms)
    patterns = detect_patterns(atoms, classification, center_nesting=center_nesting)
    boundaries = build_boundaries_v2(atoms, classification, patterns=patterns)
    scenarios = build_scenarios_v2(classification, boundaries)
    transition = build_transition(patterns, classification, scenarios)
    output = compose_radar_output_v2(classification, boundaries, scenarios, atoms, freshness or {}, patterns)

    return {
        "version": ALGORITHM_VERSION,
        "disclaimer": disclaimer,
        "level_chain": chain,
        "atoms": {role: _atom_to_dict(atom) for role, atom in atoms.items()},
        "center_nesting": center_nesting,
        "patterns": patterns,
        "transition": transition,
        "boundaries": boundaries,
        "scenarios": scenarios,
        **output,
        **classification,
    }


def build_level_atom(level: dict, public_level: str) -> LevelAtom:
    """把单级别 CChan 结构压成一个稳定原子，避免路径层直接读散字段。"""
    level = level or {}
    price = _num(level.get("price"))
    center = _center_from_level(level)
    previous_center = _previous_center_from_level(level)
    historical_centers = _historical_centers_from_level(level)
    raw_state = str(level.get("state") or "UNKNOWN")
    last_bi_dir = str(level.get("last_bi_dir") or "unknown").lower()
    position_state = _position_state(price, center, raw_state, last_bi_dir)
    buy_events, sell_events = _events_from_level(level, center, previous_center)
    event_sequence = _event_sequence(buy_events, sell_events)
    center_binding = _center_binding_index(buy_events, sell_events)
    leave_return_status = _leave_return_status(price, center, buy_events, sell_events)
    momentum_compare = _momentum_compare_from_level(level, last_bi_dir)
    historical_high = _historical_high_from_level(level, price)
    divergence = _divergence_from_level(level)
    tags = _tags_from_level(level, raw_state, buy_events, sell_events, divergence)
    quality = "OK" if price > 0 and center.is_valid else "MISSING_STRUCTURE"

    return LevelAtom(
        level=_legacy_level_key(public_level),
        public_level=public_level,
        price=price,
        raw_state=raw_state,
        position_state=position_state,
        center=center,
        previous_center=previous_center,
        historical_centers=historical_centers,
        center_relation=_center_relation(previous_center, center),
        leave_return_status=leave_return_status,
        last_bi_dir=last_bi_dir,
        buy_events=buy_events,
        sell_events=sell_events,
        event_sequence=event_sequence,
        center_binding=center_binding,
        momentum_compare=momentum_compare,
        historical_high=historical_high,
        divergence=divergence,
        tags=tags,
        quality=quality,
    )


def build_center_nesting(atoms: dict) -> dict:
    """派生相邻级别中枢位置关系，用来识别大中枢上小中枢等结构模板。"""
    return {
        "L0_L1": _center_nesting(atoms.get("L0"), atoms.get("L1")),
        "L1_L2": _center_nesting(atoms.get("L1"), atoms.get("L2")),
    }


def classify_path_v2(atoms: dict, freshness: Optional[dict] = None) -> dict:
    freshness = freshness or {}
    l0 = atoms.get("L0")
    l1 = atoms.get("L1")
    l2 = atoms.get("L2")

    if _is_stale(freshness):
        return _classification(
            path="NO_EDGE",
            relation="CONFLICT_OR_UNKNOWN",
            confidence="STALE",
            reason_codes=["FRESHNESS_STALE"],
            blocking_reasons=[freshness.get("stale_reason") or "结构数据过期"],
            warnings=[],
            candidates=[],
            requires_no_edge=True,
        )

    missing = [
        role
        for role, atom in (("L0", l0), ("L1", l1), ("L2", l2))
        if atom is None or atom.quality != "OK"
    ]
    if missing:
        return _classification(
            path="NO_EDGE",
            relation="CONFLICT_OR_UNKNOWN",
            confidence="LOW",
            reason_codes=["MISSING_LEVEL_ATOM"],
            blocking_reasons=[f"{'/'.join(missing)} 缺少价格或中枢结构"],
            warnings=[],
            candidates=[],
            requires_no_edge=True,
        )

    assert l0 is not None and l1 is not None and l2 is not None
    warnings = _path_warnings(l0, l1, l2)
    candidates = []

    if _is_down_bias(l0) and _is_down_bias(l1) and _is_down_bias(l2):
        return _classification(
            path="DOWNWARD_DEFENSE",
            relation="ALIGN_DOWN",
            confidence="HIGH",
            reason_codes=["L0_L1_L2_ALIGN_DOWN"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["BOTTOM_REPAIR"],
        )

    if _is_up_bias(l0) and _is_up_bias(l1) and _is_up_extension(l2) and not _has_active_sell_risk(l2):
        return _classification(
            path="UPWARD_MAJOR_WAVE",
            relation="ALIGN_UP",
            confidence="HIGH" if not warnings else "MEDIUM",
            reason_codes=["L0_L1_L2_ALIGN_UP"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["HIGH_VOLATILITY_OSCILLATION", "PULLBACK_IN_UPTREND"],
        )

    if _is_up_bias(l0) and _is_up_bias(l1) and _is_low_level_pullback(l2):
        return _classification(
            path="PULLBACK_IN_UPTREND",
            relation="HIGH_UP_LOW_WEAK",
            confidence="MEDIUM",
            reason_codes=["HIGH_LEVEL_UP_LOW_LEVEL_PULLBACK"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["UPWARD_MAJOR_WAVE", "HIGH_VOLATILITY_OSCILLATION", "DOWNWARD_DEFENSE"],
        )

    if _is_up_bias(l0) and _is_up_bias(l1) and _is_micro_conversion(l2):
        return _classification(
            path="PULLBACK_IN_UPTREND",
            phase="MICRO_CONVERSION",
            relation="HIGH_UP_LOW_CONTEST",
            confidence="MEDIUM",
            reason_codes=["HIGH_LEVEL_UP_LOW_LEVEL_CONTEST"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["UPWARD_MAJOR_WAVE", "HIGH_VOLATILITY_OSCILLATION", "DOWNWARD_DEFENSE"],
        )

    if _is_center_upper_contest(l0, l1, l2):
        return _classification(
            path="HIGH_VOLATILITY_OSCILLATION",
            phase="CENTER_UPPER_CONTEST",
            relation="CENTER_UPPER_SELL_PRESSURE",
            confidence="MEDIUM",
            reason_codes=["CENTER_UPPER_CONTEST_WITH_SELL_PRESSURE"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["UPWARD_MAJOR_WAVE", "PULLBACK_IN_UPTREND", "DOWNWARD_DEFENSE"],
        )

    if _is_up_bias(l0) and _is_up_bias(l1) and (_is_high_volatile(l2) or _has_active_sell_risk(l2)):
        return _classification(
            path="HIGH_VOLATILITY_OSCILLATION",
            relation="HIGH_UP_HIGH_VOLATILE",
            confidence="MEDIUM",
            reason_codes=["HIGH_LEVEL_UP_LOW_LEVEL_VOLATILE"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["UPWARD_MAJOR_WAVE", "DOWNWARD_DEFENSE"],
        )

    if _is_up_bias(l0) and _is_up_bias(l1) and _is_low_level_weak(l2):
        return _classification(
            path="PULLBACK_IN_UPTREND",
            relation="HIGH_UP_LOW_WEAK",
            confidence="MEDIUM",
            reason_codes=["HIGH_LEVEL_UP_LOW_LEVEL_PULLBACK"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["UPWARD_MAJOR_WAVE", "HIGH_VOLATILITY_OSCILLATION", "DOWNWARD_DEFENSE"],
        )

    if _is_down_bias(l0) and (_has_bottom_repair(l2) or _has_bottom_repair(l1)):
        return _classification(
            path="BOTTOM_REPAIR",
            relation="HIGH_DOWN_LOW_REPAIR",
            confidence="MEDIUM",
            reason_codes=["DOWN_CONTEXT_WITH_BOTTOM_REPAIR"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["DOWNWARD_DEFENSE", "CENTER_REBOUND"],
        )

    if _is_center_repair(l0, l1, l2):
        return _classification(
            path="CENTER_REBOUND",
            relation="CENTER_REPAIR",
            confidence="MEDIUM",
            reason_codes=["CENTER_REPAIR_OR_REBOUND"],
            blocking_reasons=[],
            warnings=warnings,
            candidates=["PULLBACK_IN_UPTREND", "DOWNWARD_DEFENSE"],
        )

    return _classification(
        path="NO_EDGE",
        relation="CONFLICT_OR_UNKNOWN",
        confidence="LOW",
        reason_codes=["NO_DOMINANT_PATH"],
        blocking_reasons=["多级别状态没有形成可归类的优势路径"],
        warnings=warnings,
        candidates=candidates,
        requires_no_edge=True,
    )


def build_boundaries_v2(atoms: dict, classification: dict, patterns: Optional[list[dict]] = None) -> dict:
    """Build structured path boundaries from classified LevelAtoms."""
    l0 = atoms.get("L0")
    l1 = atoms.get("L1")
    l2 = atoms.get("L2")
    empty = {"confirm": [], "maintain": [], "invalidate": [], "pressure": [], "support": []}
    if not l0 or not l1 or not l2:
        return empty

    path = classification.get("path")
    phase = classification.get("phase")

    if path == "PULLBACK_IN_UPTREND" and phase == "MICRO_CONVERSION":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _boundary("L2", l2, "ZG", l2.center.zg, "break_above", "突破5分钟中枢上沿，多空转换转强"),
                _recent_event_boundary("L2", l2, "sell", "break_above", "突破最近5分钟卖点压力"),
            ],
            "maintain": [
                _boundary("L2", l2, "ZD", l2.center.zd, "hold_above", "守住5分钟震荡下沿，转换点继续有效"),
            ],
            "invalidate": [
                _boundary("L2", l2, "ZD", l2.center.zd, "break_below", "跌破5分钟震荡下沿，转换尝试失败"),
                _boundary("L1", l1, "ZD", l1.center.zd, "break_below", "跌破30分钟结构下沿，转入防守路径"),
            ],
            "pressure": [
                _boundary("L2", l2, "ZG", l2.center.zg, "watch", "5分钟上沿压力"),
                _recent_event_boundary("L2", l2, "sell", "watch", "最近5分钟卖点压力"),
            ],
            "support": [
                _boundary("L2", l2, "ZD", l2.center.zd, "watch", "5分钟震荡下沿"),
                _boundary("L1", l1, "ZD", l1.center.zd, "watch", "30分钟结构下沿"),
            ],
        }), atoms, patterns or [])

    if path == "HIGH_VOLATILITY_OSCILLATION" and phase == "CENTER_UPPER_CONTEST":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _boundary("L1", l1, "ZG", l1.center.zg, "break_above", "站上30分钟中枢上沿，上沿争夺转强"),
                _recent_event_boundary("L2", l2, "sell", "break_above", "化解5分钟卖点压力"),
            ],
            "maintain": [
                _boundary("L1", l1, "ZD", l1.center.zd, "hold_above", "守住30分钟中枢下沿，上沿争夺仍可延续"),
            ],
            "invalidate": [
                _boundary("L1", l1, "ZD", l1.center.zd, "break_below", "跌破30分钟中枢下沿，上沿尝试失败"),
                _boundary("L2", l2, "ZD", l2.center.zd, "break_below", "跌破5分钟中枢下沿，短线转弱"),
            ],
            "pressure": [
                _boundary("L1", l1, "ZG", l1.center.zg, "watch", "30分钟中枢上沿"),
                _recent_event_boundary("L1", l1, "sell", "watch", "30分钟卖点压力"),
                _recent_event_boundary("L2", l2, "sell", "watch", "5分钟卖点压力"),
            ],
            "support": [
                _boundary("L1", l1, "ZD", l1.center.zd, "watch", "30分钟中枢下沿"),
                _boundary("L2", l2, "ZD", l2.center.zd, "watch", "5分钟中枢下沿"),
            ],
        }), atoms, patterns or [])

    if path == "UPWARD_MAJOR_WAVE":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _recent_event_boundary("L2", l2, "sell", "break_above", "继续突破短线卖点压力，主升延续"),
            ],
            "maintain": [
                _boundary("L2", l2, "ZG", l2.center.zg, "hold_above", "守住5分钟中枢上沿，主升路径维持"),
                _boundary("L1", l1, "ZG", l1.center.zg, "hold_above", "守住30分钟中枢上沿，中级别仍强"),
            ],
            "invalidate": [
                _boundary("L2", l2, "ZG", l2.center.zg, "break_below", "跌回5分钟中枢上沿下方，主升转入震荡"),
                _boundary("L1", l1, "ZG", l1.center.zg, "break_below", "跌回30分钟中枢上沿下方，主升路径降级"),
            ],
            "pressure": [
                _recent_event_boundary("L2", l2, "sell", "watch", "短线卖点压力"),
            ],
            "support": [
                _boundary("L2", l2, "ZG", l2.center.zg, "watch", "5分钟中枢上沿"),
                _boundary("L1", l1, "ZG", l1.center.zg, "watch", "30分钟中枢上沿"),
            ],
        }), atoms, patterns or [])

    if path == "HIGH_VOLATILITY_OSCILLATION":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _recent_event_boundary("L2", l2, "sell", "break_above", "突破最近5分钟卖点压力，震荡后转强"),
                _boundary("L2", l2, "ZG", l2.center.zg, "break_above", "站上5分钟中枢上沿，短线转强"),
            ],
            "maintain": [
                _boundary("L2", l2, "ZD", l2.center.zd, "hold_above", "守住5分钟中枢下沿，高波动震荡维持"),
            ],
            "invalidate": [
                _boundary("L2", l2, "ZD", l2.center.zd, "break_below", "跌破5分钟中枢下沿，震荡转弱"),
                _boundary("L1", l1, "ZG", l1.center.zg, "break_below", "跌回30分钟中枢上沿下方，中级别转弱"),
            ],
            "pressure": [
                _recent_event_boundary("L2", l2, "sell", "watch", "最近5分钟卖点压力"),
                _boundary("L2", l2, "ZG", l2.center.zg, "watch", "5分钟中枢上沿"),
            ],
            "support": [
                _boundary("L2", l2, "ZD", l2.center.zd, "watch", "5分钟中枢下沿"),
                _boundary("L1", l1, "ZG", l1.center.zg, "watch", "30分钟中枢上沿"),
            ],
        }), atoms, patterns or [])

    if path == "PULLBACK_IN_UPTREND":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _boundary("L2", l2, "ZD", l2.center.zd, "break_above", "重新站回5分钟中枢，回落验证转强"),
                _boundary("L2", l2, "ZG", l2.center.zg, "break_above", "突破5分钟中枢上沿，回到上涨延续"),
            ],
            "maintain": [
                _boundary("L1", l1, "ZD", l1.center.zd, "hold_above", "守住30分钟中枢下沿，上升回落仍有效"),
            ],
            "invalidate": [
                _boundary("L1", l1, "ZD", l1.center.zd, "break_below", "跌破30分钟结构下沿，回落验证失败"),
                _latest_buy_boundary("L1", l1, "break_below", "跌破30分钟最近买点，趋势回落失败"),
            ],
            "pressure": [
                _boundary("L2", l2, "ZD", l2.center.zd, "watch", "5分钟中枢下沿反压"),
                _boundary("L2", l2, "ZG", l2.center.zg, "watch", "5分钟中枢上沿压力"),
            ],
            "support": [
                _boundary("L1", l1, "ZD", l1.center.zd, "watch", "30分钟结构下沿"),
                _latest_buy_boundary("L1", l1, "watch", "30分钟最近买点"),
            ],
        }), atoms, patterns or [])

    if path == "DOWNWARD_DEFENSE":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _boundary("L2", l2, "ZD", l2.center.zd, "fail_below", "反弹不能站回5分钟中枢，下跌延续"),
            ],
            "maintain": [
                _boundary("L2", l2, "ZD", l2.center.zd, "stay_below", "仍在5分钟中枢下方，防守路径维持"),
            ],
            "invalidate": [
                _boundary("L2", l2, "ZG", l2.center.zg, "break_above", "站回5分钟中枢上沿，短线进入修复"),
                _boundary("L1", l1, "ZD", l1.center.zd, "break_above", "站回30分钟中枢下沿，防守路径降级为修复观察"),
            ],
            "pressure": [
                _boundary("L2", l2, "ZD", l2.center.zd, "watch", "5分钟中枢下沿反压"),
                _boundary("L1", l1, "ZD", l1.center.zd, "watch", "30分钟中枢下沿反压"),
            ],
            "support": [
                _latest_buy_boundary("L2", l2, "watch", "5分钟最近买点低点"),
            ],
        }), atoms, patterns or [])

    if path == "BOTTOM_REPAIR":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _boundary("L2", l2, "ZG", l2.center.zg, "break_above", "突破5分钟中枢上沿，底部修复转强"),
                _recent_event_boundary("L2", l2, "buy", "hold_above", "守住最近5分钟买点，修复确认"),
            ],
            "maintain": [
                _boundary("L2", l2, "ZD", l2.center.zd, "hold_above", "守住5分钟中枢下沿，底部修复仍有效"),
            ],
            "invalidate": [
                _boundary("L2", l2, "ZD", l2.center.zd, "break_below", "跌破5分钟中枢下沿，底部修复失败"),
                _latest_buy_boundary("L2", l2, "break_below", "跌破最近5分钟买点，修复失败"),
            ],
            "pressure": [
                _boundary("L2", l2, "ZG", l2.center.zg, "watch", "5分钟中枢上沿"),
                _boundary("L1", l1, "ZD", l1.center.zd, "watch", "30分钟中枢下沿反压"),
            ],
            "support": [
                _boundary("L2", l2, "ZD", l2.center.zd, "watch", "5分钟中枢下沿"),
                _latest_buy_boundary("L2", l2, "watch", "最近5分钟买点"),
            ],
        }), atoms, patterns or [])

    if path == "CENTER_REBOUND":
        return _apply_pattern_boundaries(_boundary_set({
            "confirm": [
                _boundary("L2", l2, "ZG", l2.center.zg, "break_above", "突破5分钟中枢上沿，反弹转强"),
                _boundary("L1", l1, "ZG", l1.center.zg, "break_above", "突破30分钟中枢上沿，修复升级"),
            ],
            "maintain": [
                _boundary("L1", l1, "ZD", l1.center.zd, "hold_above", "守住30分钟中枢下沿，修复仍有效"),
            ],
            "invalidate": [
                _boundary("L1", l1, "ZD", l1.center.zd, "break_below", "跌破30分钟中枢下沿，修复失败"),
            ],
            "pressure": [
                _boundary("L2", l2, "ZG", l2.center.zg, "watch", "5分钟中枢上沿"),
                _boundary("L1", l1, "ZG", l1.center.zg, "watch", "30分钟中枢上沿"),
            ],
            "support": [
                _boundary("L2", l2, "ZD", l2.center.zd, "watch", "5分钟中枢下沿"),
                _boundary("L1", l1, "ZD", l1.center.zd, "watch", "30分钟中枢下沿"),
            ],
        }), atoms, patterns or [])

    return empty


def build_scenarios_v2(classification: dict, boundaries: dict) -> list[dict]:
    """Build A/B/C complete-classification scenarios from path boundaries."""
    path = classification.get("path") or "NO_EDGE"
    phase = classification.get("phase") or "STANDARD"
    if path == "NO_EDGE":
        return [
            _scenario(
                "A",
                "等待结构明确",
                "confirm",
                ["数据或结构重新形成可判定优势路径"],
                "当前缺少优势路径，A 不是买点，只是等待结构重新清晰。",
                [],
                "WAITING",
            ),
            _scenario(
                "B",
                "继续无优势",
                "maintain",
                ["多级别仍然互相冲突或数据不完整"],
                "继续保持无优势状态，不进入执行前复核。",
                [],
                "CURRENT",
            ),
            _scenario(
                "C",
                "风险扩大",
                "invalidate",
                ["关键级别继续转弱或数据继续失效"],
                "无优势状态下风险扩大，继续回避。",
                [],
                "RISK",
            ),
        ]

    return [
        _scenario(
            "A",
            _scenario_name(path, phase, "A"),
            "confirm",
            _boundary_triggers(boundaries.get("confirm") or []),
            _scenario_meaning(path, phase, "A"),
            boundaries.get("confirm") or [],
            "PENDING",
        ),
        _scenario(
            "B",
            _scenario_name(path, phase, "B"),
            "maintain",
            _boundary_triggers(boundaries.get("maintain") or []),
            _scenario_meaning(path, phase, "B"),
            boundaries.get("maintain") or [],
            "CURRENT",
        ),
        _scenario(
            "C",
            _scenario_name(path, phase, "C"),
            "invalidate",
            _boundary_triggers(boundaries.get("invalidate") or []),
            _scenario_meaning(path, phase, "C"),
            boundaries.get("invalidate") or [],
            "RISK",
        ),
    ]


def compose_radar_output_v2(
    classification: dict,
    boundaries: dict,
    scenarios: list[dict],
    atoms: dict,
    freshness: dict,
    patterns: Optional[list[dict]] = None,
) -> dict:
    path = classification.get("path") or "NO_EDGE"
    phase = classification.get("phase") or "STANDARD"
    current_scenario_id = _current_scenario_id(path, classification)
    confirmation = _confirmation_status(boundaries, atoms)
    return {
        "current_scenario_id": current_scenario_id,
        "confirmation": confirmation,
        "boundary_groups": _boundary_groups_for_trading(boundaries, atoms),
        "trigger_playbook": _trigger_playbook(boundaries, confirmation, atoms),
        "a_state": confirmation.get("state"),
        "action_bias": _action_bias(path, phase),
        "risk_level": _risk_level(path, phase, classification),
        "summary": _output_summary(path, phase, boundaries, confirmation, patterns or [], classification),
        "next_watch": _next_watch(boundaries, scenarios),
        "data_notes": _data_notes(atoms, freshness),
    }


def _classification(
    path: str,
    relation: str,
    confidence: str,
    reason_codes: list[str],
    blocking_reasons: list[str],
    warnings: list[str],
    candidates: list[str],
    phase: str = "STANDARD",
    requires_no_edge: bool = False,
) -> dict:
    return {
        "path": path,
        "phase": phase,
        "confidence": confidence,
        "relation": relation,
        "reason_codes": reason_codes,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "path_candidates": candidates,
        "requires_no_edge": requires_no_edge,
    }


def _current_scenario_id(path: str, classification: dict) -> str:
    if path == "NO_EDGE":
        return "B"
    if path == "DOWNWARD_DEFENSE":
        return "B"
    return "B"


def _action_bias(path: str, phase: str) -> str:
    if path == "NO_EDGE":
        return "WAIT_STRUCTURE"
    if path == "DOWNWARD_DEFENSE":
        return "DEFENSIVE"
    if path == "UPWARD_MAJOR_WAVE":
        return "HOLD_OR_TRAIL"
    if path == "PULLBACK_IN_UPTREND" and phase == "MICRO_CONVERSION":
        return "WAIT_BREAKOUT"
    if path == "PULLBACK_IN_UPTREND":
        return "WAIT_RECLAIM"
    if path == "HIGH_VOLATILITY_OSCILLATION" and phase == "CENTER_UPPER_CONTEST":
        return "WAIT_UPPER_BREAK"
    if path == "HIGH_VOLATILITY_OSCILLATION":
        return "REDUCE_CHASING"
    if path == "CENTER_REBOUND":
        return "WATCH_REBOUND"
    if path == "BOTTOM_REPAIR":
        return "WAIT_CONFIRMATION"
    return "WATCH"


def _risk_level(path: str, phase: str, classification: dict) -> str:
    confidence = classification.get("confidence")
    if confidence == "STALE" or path == "NO_EDGE":
        return "HIGH"
    if path == "DOWNWARD_DEFENSE":
        return "HIGH"
    if path == "HIGH_VOLATILITY_OSCILLATION":
        return "HIGH" if phase == "STANDARD" else "MEDIUM_HIGH"
    if path == "PULLBACK_IN_UPTREND":
        return "MEDIUM"
    if path == "UPWARD_MAJOR_WAVE":
        return "MEDIUM"
    if path == "CENTER_REBOUND":
        return "MEDIUM"
    return "MEDIUM"


def _output_summary(
    path: str,
    phase: str,
    boundaries: dict,
    confirmation: Optional[dict] = None,
    patterns: Optional[list[dict]] = None,
    classification: Optional[dict] = None,
) -> str:
    if (classification or {}).get("confidence") == "STALE":
        reason = ((classification or {}).get("blocking_reasons") or ["结构数据过期"])[0]
        return f"数据健康异常，暂停走势推演：{reason}。"
    confirm = boundaries.get("confirm") or []
    invalidate = boundaries.get("invalidate") or []
    first_confirm = _boundary_short(confirm[0]) if confirm else ""
    first_invalidate = _boundary_short(invalidate[0]) if invalidate else ""
    state = (confirmation or {}).get("state") or ""
    pattern_codes = {pattern.get("code") for pattern in (patterns or [])}
    base = {
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION"): "5分钟多空转换点",
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST"): "中枢上沿争夺",
        ("UPWARD_MAJOR_WAVE", "STANDARD"): "主升路径维持观察",
        ("PULLBACK_IN_UPTREND", "STANDARD"): "上升趋势中的回落验证",
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD"): "高波动震荡",
        ("DOWNWARD_DEFENSE", "STANDARD"): "多级别防守状态",
        ("CENTER_REBOUND", "STANDARD"): "中枢修复反弹",
        ("BOTTOM_REPAIR", "STANDARD"): "底部修复观察",
        ("NO_EDGE", "STANDARD"): "当前没有优势路径",
    }.get((path, phase), "结构路径观察")
    if path == "DOWNWARD_DEFENSE":
        if first_confirm and first_invalidate:
            return f"{base}，{first_confirm}下跌延续，{first_invalidate}转修复。"
        if first_confirm:
            return f"{base}，{first_confirm}下跌延续。"
        if first_invalidate:
            return f"{base}，{first_invalidate}转修复。"
    if "THIRD_BUY_RETEST_UP" in pattern_codes:
        if state in {"A_FULL_TRIGGERED", "A_PARTIAL_TRIGGERED"}:
            return "三买回踩向上，A 路径已确认。"
        return "三买回踩向上，等待确认后的持有管理。"
    if state == "C_TRIGGERED":
        return f"{base}，当前已进入失效路径。"
    if state == "A_FULL_TRIGGERED":
        return f"{base}后转强，A 路径已确认。"
    if state == "A_PARTIAL_TRIGGERED":
        return f"{base}已半确认，等待剩余确认边界。"
    if state == "B_MAINTAINED":
        return f"{base}，当前维持观察。"
    if first_confirm and first_invalidate:
        return f"{base}，{first_confirm}转强，{first_invalidate}转弱。"
    if first_confirm:
        return f"{base}，{first_confirm}转强。"
    if first_invalidate:
        return f"{base}，{first_invalidate}转弱。"
    return f"{base}。"


def _next_watch(boundaries: dict, scenarios: list[dict]) -> list[str]:
    watch = []
    for item in (boundaries.get("confirm") or [])[:2]:
        text = _boundary_watch_text(item)
        if text:
            watch.append(text)
    for item in (boundaries.get("invalidate") or [])[:2]:
        text = _boundary_watch_text(item)
        if text:
            watch.append(text)
    if watch:
        return watch
    for scenario in scenarios:
        watch.extend((scenario.get("trigger_if") or [])[:1])
    return watch[:4]


def _confirmation_status(boundaries: dict, atoms: dict) -> dict:
    quote = _current_quote_from_atoms(atoms)
    invalidates = [
        item for item in boundaries.get("invalidate") or []
        if _boundary_triggered_now(item, quote)
    ]
    if invalidates:
        return _confirmation_result(
            "C_TRIGGERED",
            "路径失效边界已经被当前价格触发。",
            invalidates,
            [],
            boundaries.get("confirm") or [],
        )

    confirms = boundaries.get("confirm") or []
    matched = [
        item for item in confirms
        if _boundary_triggered_now(item, quote)
    ]
    unmatched = [item for item in confirms if item not in matched]
    if matched:
        if len(matched) == len(confirms):
            return _confirmation_result(
                "A_FULL_TRIGGERED",
                "A 路径的确认边界已经全部触发。",
                matched,
                unmatched,
                confirms,
            )
        return _confirmation_result(
            "A_PARTIAL_TRIGGERED",
            "A 路径已经半确认，但仍有关键确认边界没有触发。",
            matched,
            unmatched,
            confirms,
        )

    maintains = [
        item for item in boundaries.get("maintain") or []
        if _boundary_triggered_now(item, quote)
    ]
    if maintains:
        return _confirmation_result(
            "B_MAINTAINED",
            "当前仍停留在 B 路径维持状态。",
            maintains,
            confirms,
            confirms,
        )

    return _confirmation_result(
        "A_NOT_TRIGGERED",
        "A 路径尚未触发，继续等待确认边界。",
        [],
        confirms,
        confirms,
    )


def _confirmation_result(
    state: str,
    meaning: str,
    matched: list[dict],
    unmatched: list[dict],
    confirms: list[dict],
) -> dict:
    total = len(confirms)
    matched_confirm_count = len([item for item in matched if item in confirms])
    return {
        "state": state,
        "progress": round(matched_confirm_count / total, 4) if total else 0.0,
        "matched": matched,
        "unmatched": unmatched,
        "meaning": meaning,
    }


def _boundary_groups_for_trading(boundaries: dict, atoms: dict) -> list[dict]:
    """把算法边界翻译成实盘身份，避免只暴露 5ZD/30ZD 这类裸代码。"""
    groups = [
        _boundary_group(
            "short_execution",
            "短线执行线",
            "5分钟触发与盘中修复边界，服务盯盘动作。",
            _boundary_filter(
                (boundaries.get("confirm") or []) + (boundaries.get("pressure") or []) + (boundaries.get("support") or []),
                level_role="L2",
            ),
        ),
        _boundary_group(
            "upside_confirm",
            "上方确认线",
            "突破后，当前推演从观察转入 A 路径确认。",
            boundaries.get("confirm") or [],
        ),
        _boundary_group(
            "mid_defense",
            "中级别防线",
            "判断 30分钟结构是否继续支持当前推演。",
            _boundary_filter(
                (boundaries.get("maintain") or []) + (boundaries.get("support") or []) + (boundaries.get("invalidate") or []),
                level_role="L1",
            ),
        ),
        _boundary_group(
            "invalidation",
            "失效线",
            "跌破或反向触发后，当前主推演作废或降级。",
            boundaries.get("invalidate") or [],
        ),
    ]
    if not any(group["items"] for group in groups):
        return []
    return [
        {**group, "items": _dedupe_boundary_prices(group["items"])[:4]}
        for group in groups
        if group["items"]
    ]


def _boundary_group(group_id: str, label: str, purpose: str, items: list[dict]) -> dict:
    return {
        "id": group_id,
        "label": label,
        "purpose": purpose,
        "items": [_boundary_with_identity(item, group_id) for item in items if item],
    }


def _trigger_playbook(boundaries: dict, confirmation: Optional[dict] = None, atoms: Optional[dict] = None) -> list[dict]:
    """生成“接下来如果发生”的条件剧本，直接服务走势推演。"""
    confirmation = confirmation or {}
    quote = _current_quote_from_atoms(atoms or {})
    quote_price = _num(quote.get("last_price"))
    matched_confirm_keys = {
        _boundary_key(item)
        for item in confirmation.get("matched") or []
        if item in (boundaries.get("confirm") or [])
    }
    a_confirmed = confirmation.get("state") in {"A_FULL_TRIGGERED", "A_PARTIAL_TRIGGERED"}
    entries = []
    for item in boundaries.get("confirm") or []:
        # 已经发生的确认条件属于“执行确认”，不再放进未来剧本。
        if _boundary_key(item) in matched_confirm_keys or _boundary_triggered_now(item, quote):
            continue
        entries.append(_trigger_entry(item, "A", "转强确认", "confirm"))
    for item in boundaries.get("maintain") or []:
        if a_confirmed:
            entries.append(_trigger_entry(item, "A", "确认后防守", "maintain"))
        else:
            entries.append(_trigger_entry(item, "B", "维持观察", "maintain"))
    for item in boundaries.get("invalidate") or []:
        entries.append(_trigger_entry(item, "C", "推演失效", "invalidate"))

    deduped = []
    seen = set()
    for entry in entries:
        boundary = entry["boundary"]
        key = (
            entry["path"],
            boundary.get("level_role"),
            boundary.get("level"),
            boundary.get("field"),
            boundary.get("value"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped[:8]


def _boundary_key(item: dict) -> tuple:
    return (
        item.get("level_role"),
        item.get("level"),
        item.get("field"),
        item.get("value"),
        item.get("trigger"),
    )


def _trigger_entry(item: dict, path: str, title: str, tone: str) -> dict:
    return {
        "path": path,
        "title": title,
        "tone": tone,
        "condition": _trigger_condition_text(item),
        "then": item.get("meaning") or "",
        "boundary": item,
    }


def _trigger_condition_text(item: dict) -> str:
    trigger = _trigger_label(item.get("trigger"))
    if item.get("field") == "ATH":
        value = item.get("value")
        return f"{trigger}历史前高 {value:g}" if trigger and value else item.get("meaning", "")
    short = _boundary_short(item)
    return f"{trigger}{short}" if trigger and short else short or item.get("meaning", "")


def _boundary_filter(items: list[dict], level_role: str) -> list[dict]:
    return [item for item in items if item.get("level_role") == level_role]


def _dedupe_boundary_prices(items: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for item in items:
        key = (
            item.get("level_role"),
            item.get("level"),
            item.get("field"),
            item.get("value"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _boundary_with_identity(item: dict, group_id: str) -> dict:
    source = _boundary_source_label(item)
    return {
        **item,
        "identity": group_id,
        "source_label": source,
        "display_label": _boundary_display_label(item, group_id),
    }


def _boundary_source_label(item: dict) -> str:
    role = item.get("level_role")
    level = item.get("level")
    if role == "L2":
        return f"{level}分钟触发结构" if str(level).isdigit() else f"{level}触发结构"
    if role == "L1":
        return f"{level}分钟中级别结构" if str(level).isdigit() else f"{level}中级别结构"
    if role == "L0":
        return f"{level}背景结构"
    return "结构边界"


def _boundary_display_label(item: dict, group_id: str) -> str:
    level = item.get("level")
    field = item.get("field")
    prefix = {
        "short_execution": "短线",
        "upside_confirm": "确认",
        "mid_defense": "防线",
        "invalidation": "失效",
    }.get(group_id, "边界")
    return f"{prefix} {level}{field}"


def _current_quote_from_atoms(atoms: dict) -> dict:
    for role in ("L2", "L1", "L0"):
        atom = atoms.get(role)
        price = _num(getattr(atom, "price", 0))
        if price > 0:
            return {"last_price": price, "high": price, "low": price}
    return {"last_price": 0.0, "high": 0.0, "low": 0.0}


def _boundary_triggered_now(boundary: dict, quote: dict) -> bool:
    value = _num(boundary.get("value"))
    if value <= 0:
        return False
    trigger = str(boundary.get("trigger") or "")
    last_price = _num(quote.get("last_price"))
    high = _num(quote.get("high")) or last_price
    low = _num(quote.get("low")) or last_price

    if trigger == "break_above":
        return last_price > value
    if trigger == "break_below":
        return last_price < value
    if trigger == "hold_above":
        return low >= value or last_price >= value
    if trigger == "stay_below":
        return high < value and last_price < value
    if trigger == "fail_below":
        return high < value
    return False


def _data_notes(atoms: dict, freshness: dict) -> dict:
    missing = [
        role
        for role, atom in atoms.items()
        if atom.quality != "OK"
    ]
    return {
        "source": freshness.get("source") or "unknown",
        "adjustflag": freshness.get("adjustflag") or "",
        "checked_at": freshness.get("checked_at") or "",
        "last_bar_at": freshness.get("last_bar_at") or "",
        "is_stale": bool(freshness.get("is_stale")),
        "stale_reason": freshness.get("stale_reason") or "",
        "levels": freshness.get("levels") or {},
        "missing_or_weak_levels": missing,
    }


def _boundary_short(item: dict) -> str:
    level = item.get("level")
    field = item.get("field")
    value = item.get("value")
    if not value:
        return ""
    return f"{level}{field} {value:g}"


def _boundary_watch_text(item: dict) -> str:
    short = _boundary_short(item)
    meaning = item.get("meaning") or ""
    if not short:
        return meaning
    if meaning:
        return f"{short}：{meaning}"
    return short


def _scenario(
    scenario_id: str,
    name: str,
    role: str,
    trigger_if: list[str],
    meaning: str,
    source_boundaries: list[dict],
    state: str,
) -> dict:
    return {
        "id": scenario_id,
        "name": name,
        "role": role,
        "state": state,
        "trigger_if": trigger_if,
        "meaning": meaning,
        "source_boundaries": source_boundaries,
    }


def _boundary_triggers(items: list[dict]) -> list[str]:
    triggers = []
    for item in items:
        level = item.get("level")
        field = item.get("field")
        value = item.get("value")
        trigger = _trigger_label(item.get("trigger"))
        meaning = item.get("meaning")
        if value:
            triggers.append(f"{trigger}{level}{field} {value:g}：{meaning}")
        elif meaning:
            triggers.append(str(meaning))
    return triggers


def _trigger_label(trigger: str) -> str:
    return {
        "break_above": "突破",
        "hold_above": "守住",
        "break_below": "跌破",
        "stay_below": "仍在下方",
        "fail_below": "反弹不回",
        "risk_event": "确认风险",
        "watch": "观察",
    }.get(str(trigger or ""), "")


def _scenario_name(path: str, phase: str, scenario_id: str) -> str:
    names = {
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION", "A"): "转换点向上确认",
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION", "B"): "继续多空缠斗",
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION", "C"): "转换失败转弱",
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST", "A"): "上沿争夺转强",
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST", "B"): "中枢上沿震荡",
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST", "C"): "上沿尝试失败",
        ("UPWARD_MAJOR_WAVE", "STANDARD", "A"): "主升继续延伸",
        ("UPWARD_MAJOR_WAVE", "STANDARD", "B"): "主升维持",
        ("UPWARD_MAJOR_WAVE", "STANDARD", "C"): "主升降级",
        ("PULLBACK_IN_UPTREND", "STANDARD", "A"): "回落验证转强",
        ("PULLBACK_IN_UPTREND", "STANDARD", "B"): "回落验证维持",
        ("PULLBACK_IN_UPTREND", "STANDARD", "C"): "回落验证失败",
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD", "A"): "震荡后转强",
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD", "B"): "高波动震荡维持",
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD", "C"): "震荡转弱",
        ("DOWNWARD_DEFENSE", "STANDARD", "A"): "防守中继确认",
        ("DOWNWARD_DEFENSE", "STANDARD", "B"): "防守状态维持",
        ("DOWNWARD_DEFENSE", "STANDARD", "C"): "防守解除转修复",
        ("BOTTOM_REPAIR", "STANDARD", "A"): "底部修复转强",
        ("BOTTOM_REPAIR", "STANDARD", "B"): "底部修复维持",
        ("BOTTOM_REPAIR", "STANDARD", "C"): "底部修复失败",
        ("CENTER_REBOUND", "STANDARD", "A"): "中枢修复转强",
        ("CENTER_REBOUND", "STANDARD", "B"): "中枢修复维持",
        ("CENTER_REBOUND", "STANDARD", "C"): "中枢修复失败",
    }
    return names.get((path, phase, scenario_id), {"A": "路径确认", "B": "路径维持", "C": "路径失效"}[scenario_id])


def _scenario_meaning(path: str, phase: str, scenario_id: str) -> str:
    meanings = {
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION", "A"): "5分钟多空转换点向上突破，回到上升延续观察。",
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION", "B"): "守住转换点下沿，继续等待方向选择。",
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION", "C"): "跌破转换点下沿，小级别转弱并拖累上升回落路径。",
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST", "A"): "站上结构上沿并化解卖点压力，上沿争夺转强。",
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST", "B"): "守住中枢下沿，继续围绕上沿争夺。",
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST", "C"): "跌破中枢下沿，上沿尝试失败并转入防守。",
        ("UPWARD_MAJOR_WAVE", "STANDARD", "A"): "短级别继续突破压力，主升延伸。",
        ("UPWARD_MAJOR_WAVE", "STANDARD", "B"): "守住关键中枢上沿，主升路径仍维持。",
        ("UPWARD_MAJOR_WAVE", "STANDARD", "C"): "跌回关键中枢上沿下方，主升路径降级为震荡或回落。",
        ("PULLBACK_IN_UPTREND", "STANDARD", "A"): "重新站回短级别中枢，回落验证转强。",
        ("PULLBACK_IN_UPTREND", "STANDARD", "B"): "守住中级别结构下沿，回落仍属于上升途中的验证。",
        ("PULLBACK_IN_UPTREND", "STANDARD", "C"): "跌破中级别结构下沿，回落验证失败。",
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD", "A"): "震荡后突破压力，重新转强。",
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD", "B"): "继续高波动震荡，不进入明确方向。",
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD", "C"): "跌破关键边界，高波动转为防守。",
        ("DOWNWARD_DEFENSE", "STANDARD", "A"): "反弹仍不能回中枢，下跌延续确认。",
        ("DOWNWARD_DEFENSE", "STANDARD", "B"): "仍在关键中枢下方，防守优先。",
        ("DOWNWARD_DEFENSE", "STANDARD", "C"): "重新站回关键中枢，防守路径降级为修复观察。",
        ("BOTTOM_REPAIR", "STANDARD", "A"): "突破短级别中枢上沿，底部修复转强。",
        ("BOTTOM_REPAIR", "STANDARD", "B"): "守住短级别中枢下沿，底部修复仍有效。",
        ("BOTTOM_REPAIR", "STANDARD", "C"): "跌破短级别中枢下沿，底部修复失败。",
        ("CENTER_REBOUND", "STANDARD", "A"): "突破中枢上沿，修复升级。",
        ("CENTER_REBOUND", "STANDARD", "B"): "守住中枢下沿，修复继续。",
        ("CENTER_REBOUND", "STANDARD", "C"): "跌破中枢下沿，修复失败。",
    }
    return meanings.get((path, phase, scenario_id), "等待结构事件决定路径。")


def _boundary(role: str, atom: LevelAtom, field: str, value: float, trigger: str, meaning: str) -> dict:
    return _clean_boundary({
        "level_role": role,
        "level": atom.public_level,
        "field": field,
        "value": _round_price(value),
        "trigger": trigger,
        "meaning": meaning,
    })


def _boundary_set(groups: dict) -> dict:
    return {
        key: [item for item in (groups.get(key) or []) if item]
        for key in ("confirm", "maintain", "invalidate", "pressure", "support")
    }


def _apply_pattern_boundaries(boundaries: dict, atoms: dict, patterns: list[dict]) -> dict:
    """Merge pattern-specific boundaries into path boundaries."""
    result = {key: list(boundaries.get(key) or []) for key in ("confirm", "maintain", "invalidate", "pressure", "support")}
    pattern_codes = {pattern.get("code") for pattern in patterns}
    l0 = atoms.get("L0")
    l1 = atoms.get("L1")
    l2 = atoms.get("L2")

    if "BIG_CENTER_SMALL_CENTER_UP_BREAK" in pattern_codes and l0 and l1:
        _extend_boundaries(result, {
            "maintain": [
                _boundary("L0", l0, "ZG", l0.center.zg, "hold_above", "守住大中枢上沿，大中枢上小中枢结构维持"),
                _boundary("L1", l1, "ZG", l1.center.zg, "hold_above", "守住小中枢上沿，强势一笔维持"),
            ],
            "invalidate": [
                _boundary("L0", l0, "ZG", l0.center.zg, "break_below", "跌回大中枢内部，大中枢上小中枢主升降级"),
            ],
            "support": [
                _boundary("L0", l0, "ZG", l0.center.zg, "watch", "大中枢上沿"),
                _boundary("L1", l1, "ZG", l1.center.zg, "watch", "小中枢上沿"),
            ],
        })

    if "THIRD_BUY_FAST_SELL_RISK" in pattern_codes and l1:
        first_sell = _event_boundary_by_codes("L1", l1, {"S1", "S1P"}, "watch", "三买后第一卖点压力")
        second_sell = _event_boundary_by_codes("L1", l1, {"S2", "S2S"}, "risk_event", "二卖未过前高，三买后一笔转风险")
        _extend_boundaries(result, {
            "invalidate": [second_sell],
            "pressure": [first_sell, second_sell],
        })

    if "BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR" in pattern_codes and l1 and l2:
        _extend_boundaries(result, {
            "confirm": [
                _boundary("L2", l2, "ZG", l2.center.zg, "break_above", "重新站回小级别中枢上沿，小转大拉回成立"),
                _recent_event_boundary("L2", l2, "sell", "break_above", "突破前卖点压力，修复完整转强"),
            ],
            "maintain": [
                _boundary("L1", l1, "ZG", l1.center.zg, "hold_above", "守住原中枢上沿，拉回修复维持"),
            ],
            "invalidate": [
                _boundary("L1", l1, "ZG", l1.center.zg, "break_below", "再次跌回原中枢内部，拉回修复失败"),
            ],
            "support": [
                _boundary("L1", l1, "ZG", l1.center.zg, "watch", "原中枢上沿"),
            ],
        }, prefer_front={"confirm"})

    if "THIRD_BUY_RETEST_UP" in pattern_codes and l1 and l2:
        _extend_boundaries(result, {
            "maintain": [
                _boundary("L2", l2, "ZG", l2.center.zg, "hold_above", "守住短级别旧中枢上沿，三买回踩向上有效"),
                _boundary("L2", l2, "ZD", l2.center.zd, "hold_above", "守住短级别中枢下沿，上攻恢复不破坏"),
            ],
            "invalidate": [
                _boundary("L2", l2, "ZG", l2.center.zg, "break_below", "跌回短级别旧中枢内部，三买回踩失败"),
                _boundary("L1", l1, "ZG", l1.center.zg, "break_below", "跌回中级别中枢上沿下方，上攻恢复降级"),
            ],
            "support": [
                _boundary("L2", l2, "ZG", l2.center.zg, "watch", "短级别旧中枢上沿"),
                _boundary("L1", l1, "ZG", l1.center.zg, "watch", "中级别中枢上沿"),
            ],
        }, prefer_front={"maintain", "invalidate"})

    _extend_boundaries(result, {
        "pressure": [_historical_high_pressure_boundary(l0)],
    }, prefer_front={"pressure"})

    return {
        key: _dedupe_boundaries(items)
        for key, items in result.items()
    }


def _historical_high_pressure_boundary(atom: Optional[LevelAtom]) -> Optional[dict]:
    if not atom or atom.public_level != "day":
        return None
    high = atom.historical_high or {}
    price = _num(high.get("price"))
    distance_pct = _num(high.get("distance_pct"))
    if price <= 0:
        return None
    if high.get("is_breakout"):
        meaning = "已经突破历史前高，进入新高后的延伸/背驰观察"
        trigger = "watch"
    elif high.get("is_near"):
        meaning = f"距离历史前高约 {distance_pct * 100:.1f}%，注意放量滞涨、顶背驰或突破确认"
        trigger = "watch"
    else:
        return None
    return _clean_boundary({
        "level_role": "L0",
        "level": atom.public_level,
        "field": "ATH",
        "value": _round_price(price),
        "trigger": trigger,
        "meaning": meaning,
        "time": high.get("time") or "",
        "distance_pct": distance_pct,
    })


def _extend_boundaries(result: dict, groups: dict, prefer_front: Optional[set[str]] = None) -> None:
    prefer_front = prefer_front or set()
    for key, items in groups.items():
        clean_items = [item for item in items if item]
        if key in prefer_front:
            result[key] = clean_items + result.get(key, [])
        else:
            result.setdefault(key, []).extend(clean_items)


def _dedupe_boundaries(items: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for item in items:
        key = (
            item.get("level_role"),
            item.get("level"),
            item.get("field"),
            item.get("value"),
            item.get("trigger"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _recent_event_boundary(role: str, atom: LevelAtom, side: str, trigger: str, meaning: str) -> dict:
    events = atom.sell_events if side == "sell" else atom.buy_events
    current = [event for event in events if event.is_current and event.price > 0]
    event = current[-1] if current else None
    if not event:
        return {}
    return _clean_boundary({
        "level_role": role,
        "level": atom.public_level,
        "field": event.code,
        "value": _round_price(event.price),
        "trigger": trigger,
        "meaning": meaning,
        "event": {
            "code": event.code,
            "name": event.name,
            "time": event.time,
        },
    })


def _event_boundary_by_codes(role: str, atom: LevelAtom, codes: set[str], trigger: str, meaning: str) -> dict:
    events = [event for event in atom.sell_events + atom.buy_events if event.code in codes and event.price > 0]
    event = events[-1] if events else None
    if not event:
        return {}
    return _clean_boundary({
        "level_role": role,
        "level": atom.public_level,
        "field": event.code,
        "value": _round_price(event.price),
        "trigger": trigger,
        "meaning": meaning,
        "event": {
            "code": event.code,
            "name": event.name,
            "time": event.time,
        },
    })


def _latest_buy_boundary(role: str, atom: LevelAtom, trigger: str, meaning: str) -> dict:
    current = [event for event in atom.buy_events if event.price > 0]
    event = current[-1] if current else None
    if not event:
        return {}
    return _clean_boundary({
        "level_role": role,
        "level": atom.public_level,
        "field": event.code,
        "value": _round_price(event.price),
        "trigger": trigger,
        "meaning": meaning,
        "event": {
            "code": event.code,
            "name": event.name,
            "time": event.time,
        },
    })


def _clean_boundary(item: dict) -> dict:
    value = _num(item.get("value"))
    if value <= 0:
        return {}
    return item


def _round_price(value: float) -> float:
    value = _num(value)
    if value <= 0:
        return 0.0
    return round(value, 4)


def _round_delta(value: float) -> float:
    return round(_num(value), 4)


def _position_state(price: float, center: Center, raw_state: str, last_bi_dir: str) -> str:
    if price <= 0 or not center.is_valid:
        return "UNKNOWN"
    if price > center.zg:
        if last_bi_dir in ("down", "向下", "向下（未确认）"):
            return "UP_RETEST"
        return "UP_LEAVING"
    if price < center.zd:
        if last_bi_dir in ("up", "向上", "向上（未确认）"):
            return "DOWN_PULLBACK"
        return "DOWN_LEAVING"
    if raw_state == "THIRD_BUY_CONFIRMED":
        return "UP_RETEST"
    if raw_state == "THIRD_SELL_CONFIRMED":
        return "DOWN_PULLBACK"
    return "CENTER_INSIDE"


def _is_up_bias(atom: LevelAtom) -> bool:
    return atom.position_state in {"UP_LEAVING", "UP_RETEST"} or atom.raw_state in UP_RAW_STATES


def _is_down_bias(atom: LevelAtom) -> bool:
    return atom.position_state in {"DOWN_LEAVING", "DOWN_PULLBACK"} or atom.raw_state in DOWN_RAW_STATES


def _is_up_extension(atom: LevelAtom) -> bool:
    if atom.position_state == "UP_LEAVING":
        return True
    return atom.position_state == "UP_RETEST" and atom.raw_state == "THIRD_BUY_CONFIRMED" and atom.price > atom.center.zg


def _is_low_level_weak(atom: LevelAtom) -> bool:
    return atom.position_state in {"DOWN_LEAVING", "DOWN_PULLBACK", "UP_RETEST", "CENTER_INSIDE"}


def _is_low_level_pullback(atom: LevelAtom) -> bool:
    return atom.position_state in {"DOWN_LEAVING", "DOWN_PULLBACK"}


def _is_micro_conversion(atom: LevelAtom) -> bool:
    if atom.position_state != "CENTER_INSIDE":
        return False
    has_current_buy = any(event.is_current for event in atom.buy_events)
    has_current_sell = any(event.is_current for event in atom.sell_events)
    if not (has_current_buy and has_current_sell):
        return False
    if atom.center.zd <= 0 or atom.center.zg <= 0 or atom.price <= 0:
        return False
    width = atom.center.zg - atom.center.zd
    if width <= 0:
        return False
    relative_width = width / atom.price
    edge_distance = min(abs(atom.price - atom.center.zd), abs(atom.price - atom.center.zg))
    return relative_width <= 0.02 and edge_distance <= width


def _is_high_volatile(atom: LevelAtom) -> bool:
    if atom.position_state in {"DOWN_LEAVING", "DOWN_PULLBACK"}:
        return False
    if atom.position_state == "CENTER_INSIDE" and (_has_active_sell_risk(atom) or "TOP_DIVERGENCE" in atom.tags):
        return True
    if atom.position_state == "UP_RETEST" and (_has_active_sell_risk(atom) or "TOP_DIVERGENCE" in atom.tags):
        return True
    return False


def _is_center_upper_contest(l0: LevelAtom, l1: LevelAtom, l2: LevelAtom) -> bool:
    if l0.position_state not in {"CENTER_INSIDE", "UP_RETEST"}:
        return False
    if l1.position_state != "CENTER_INSIDE":
        return False
    if not _near_center_upper(l1):
        return False
    if not (_has_bottom_repair(l2) or "THIRD_BUY" in l2.tags or l2.position_state == "UP_RETEST"):
        return False
    return _has_active_sell_risk(l1) or _has_active_sell_risk(l2)


def _near_center_upper(atom: LevelAtom) -> bool:
    if atom.center.zd <= 0 or atom.center.zg <= 0 or atom.price <= 0:
        return False
    width = atom.center.zg - atom.center.zd
    if width <= 0:
        return False
    return atom.price >= atom.center.zg - width * 0.25


def _has_bottom_repair(atom: LevelAtom) -> bool:
    return "BOTTOM_DIVERGENCE" in atom.tags or any(event.is_current for event in atom.buy_events)


def _has_current_sell(atom: LevelAtom) -> bool:
    return any(event.is_current for event in atom.sell_events) or "TOP_DIVERGENCE" in atom.tags


def _has_active_sell_risk(atom: LevelAtom) -> bool:
    if "TOP_DIVERGENCE" in atom.tags:
        return True
    current_sells = [event for event in atom.sell_events if event.is_current]
    if not current_sells:
        return False
    if atom.position_state in {"CENTER_INSIDE", "DOWN_LEAVING", "DOWN_PULLBACK"}:
        return True
    # 关键逻辑：价格已经重新站上卖点价时，该卖点只保留为风险标签，不直接改变主路径。
    return any(event.price <= 0 or atom.price <= event.price for event in current_sells)


def _is_center_repair(l0: LevelAtom, l1: LevelAtom, l2: LevelAtom) -> bool:
    states = {l0.position_state, l1.position_state, l2.position_state}
    if "CENTER_INSIDE" in states and not (_is_down_bias(l0) and _is_down_bias(l1)):
        return True
    return l1.position_state == "CENTER_INSIDE" and _has_bottom_repair(l2)


def _path_warnings(l0: LevelAtom, l1: LevelAtom, l2: LevelAtom) -> list[str]:
    warnings = []
    for role, atom in (("L0", l0), ("L1", l1), ("L2", l2)):
        if "TOP_DIVERGENCE" in atom.tags:
            warnings.append(f"{role} 出现顶背驰风险")
        if any(event.is_current for event in atom.sell_events):
            warnings.append(f"{role} 最新窗口出现卖点事件")
    return warnings


def _center_from_level(level: dict) -> Center:
    active = level.get("active_zhongshu") or {}
    if not active:
        zhongshus = level.get("bi_zhongshus") or level.get("zhongshus") or []
        active = zhongshus[-1] if zhongshus else {}
    return _center(active, fallback=level)


def _previous_center_from_level(level: dict) -> Center:
    zhongshus = level.get("bi_zhongshus") or level.get("zhongshus") or []
    if len(zhongshus) >= 2:
        return _center(zhongshus[-2])
    return Center()


def _historical_centers_from_level(level: dict, limit: int = 5) -> list[Center]:
    zhongshus = level.get("bi_zhongshus") or level.get("zhongshus") or []
    centers = [_center(item) for item in zhongshus[-limit:]]
    return [center for center in centers if center.is_valid]


def _center(data: dict, fallback: Optional[dict] = None) -> Center:
    fallback = fallback or {}
    zd = _num(data.get("zd", fallback.get("zd") or fallback.get("zs_operative_zd")))
    zg = _num(data.get("zg", fallback.get("zg") or fallback.get("zs_operative_zg")))
    dd = _num(data.get("dd") or data.get("low") or fallback.get("dd"))
    gg = _num(data.get("gg") or data.get("high") or fallback.get("gg"))
    return Center(
        zd=zd,
        zg=zg,
        dd=dd,
        gg=gg,
        start=str(data.get("begin_date") or data.get("start") or ""),
        end=str(data.get("end_date") or data.get("end") or ""),
        is_valid=zd > 0 and zg > 0 and zd <= zg,
    )


def _center_relation(previous: Center, current: Center) -> str:
    if not previous.is_valid or not current.is_valid:
        return "UNKNOWN"
    if current.zd > previous.zg:
        return "UP_NEWBORN"
    if current.zg < previous.zd:
        return "DOWN_NEWBORN"
    if current.zd <= previous.zg and current.zg >= previous.zd:
        return "EXPANSION"
    return "EXTENSION"


def _center_nesting(parent_atom: Optional[LevelAtom], child_atom: Optional[LevelAtom]) -> dict:
    parent = parent_atom.center if parent_atom else Center()
    child = child_atom.center if child_atom else Center()
    if not parent.is_valid or not child.is_valid:
        return {
            "relation": "UNKNOWN",
            "parent_role": getattr(parent_atom, "level", "") if parent_atom else "",
            "child_role": getattr(child_atom, "level", "") if child_atom else "",
            "parent_level": getattr(parent_atom, "public_level", "") if parent_atom else "",
            "child_level": getattr(child_atom, "public_level", "") if child_atom else "",
            "parent_zd": 0.0,
            "parent_zg": 0.0,
            "child_zd": 0.0,
            "child_zg": 0.0,
            "gap_to_parent_zg": 0.0,
        }

    if child.zd > parent.zg:
        relation = "CHILD_ABOVE_PARENT"
    elif child.zg < parent.zd:
        relation = "CHILD_BELOW_PARENT"
    elif child.zd >= parent.zd and child.zg <= parent.zg:
        relation = "CHILD_INSIDE_PARENT"
    elif parent.zd >= child.zd and parent.zg <= child.zg:
        relation = "PARENT_INSIDE_CHILD"
    else:
        relation = "OVERLAP"

    return {
        "relation": relation,
        "parent_role": parent_atom.level,
        "child_role": child_atom.level,
        "parent_level": parent_atom.public_level,
        "child_level": child_atom.public_level,
        "parent_zd": _round_price(parent.zd),
        "parent_zg": _round_price(parent.zg),
        "child_zd": _round_price(child.zd),
        "child_zg": _round_price(child.zg),
        "gap_to_parent_zg": _round_delta(child.zd - parent.zg),
    }


def _events_from_level(level: dict, center: Center, previous_center: Center) -> tuple[list[BspEvent], list[BspEvent]]:
    buy_events: list[BspEvent] = []
    sell_events: list[BspEvent] = []
    for bsp in level.get("bsps") or []:
        event = _event_from_bsp(level, bsp, center, previous_center)
        if not event:
            continue
        if event.is_buy:
            buy_events.append(event)
        else:
            sell_events.append(event)
    return buy_events, sell_events


def _event_sequence(buy_events: list[BspEvent], sell_events: list[BspEvent]) -> list[dict]:
    events = []
    for event in buy_events + sell_events:
        events.append({
            "time": event.time,
            "side": "buy" if event.is_buy else "sell",
            "code": event.code,
            "family": event.family,
            "name": event.name,
            "display": event.display,
            "price": _round_price(event.price),
            "is_current": event.is_current,
            "center_binding": event.center_binding,
        })
    return sorted(events, key=lambda item: (str(item.get("time") or ""), str(item.get("side") or ""), str(item.get("code") or "")))


def _event_from_bsp(level: dict, bsp: dict, center: Center, previous_center: Center) -> Optional[BspEvent]:
    raw_type = _normalize_bsp_type(bsp.get("type"))
    is_buy = bool(bsp.get("is_buy"))
    meta = BUY_BSP_TYPES.get(raw_type) if is_buy else SELL_BSP_TYPES.get(raw_type)
    if not meta:
        return None
    code, family, name, source = meta
    return BspEvent(
        raw_type=raw_type,
        code=code,
        family=family,
        name=name,
        display=f"{code} {name}",
        source=source,
        is_buy=is_buy,
        time=_bsp_time(bsp),
        price=_num(bsp.get("price")),
        is_current=_is_bsp_in_latest_window(level, bsp),
        center_binding=_event_center_binding(_num(bsp.get("price")), center, previous_center),
    )


def _center_binding_index(buy_events: list[BspEvent], sell_events: list[BspEvent]) -> dict:
    result = {}
    for event in buy_events + sell_events:
        key = _event_binding_key(event)
        result[key] = event.center_binding
    return result


def _event_binding_key(event: BspEvent) -> str:
    suffix = event.time or f"{event.price:g}"
    return f"{event.code}@{suffix}"


def _event_center_binding(price: float, center: Center, previous_center: Center) -> dict:
    current = _binding_to_center(price, center)
    previous = _binding_to_center(price, previous_center)
    primary = "current" if current.get("status") != "unknown" else "previous" if previous.get("status") != "unknown" else ""
    return {
        "primary": primary,
        "current": current,
        "previous": previous,
    }


def _leave_return_status(
    price: float,
    center: Center,
    buy_events: list[BspEvent],
    sell_events: list[BspEvent],
) -> dict:
    if price <= 0 or not center.is_valid:
        return _leave_return_result("UNKNOWN", "", price, center, 0.0)

    event_prices = [event.price for event in buy_events + sell_events if event.price > 0]
    up_extreme = max([price] + event_prices)
    down_extreme = min([price] + event_prices)
    has_up_leave = up_extreme > center.zg
    has_down_leave = down_extreme < center.zd

    if has_up_leave:
        if price > center.zg:
            return _leave_return_result("UP_LEAVING", "up", price, center, up_extreme)
        if price >= center.zd:
            return _leave_return_result("UP_RETURNED_TO_CENTER", "up", price, center, up_extreme)
        return _leave_return_result("UP_RETURN_BROKEN", "up", price, center, up_extreme)

    if has_down_leave:
        if price < center.zd:
            return _leave_return_result("DOWN_LEAVING", "down", price, center, down_extreme)
        if price <= center.zg:
            return _leave_return_result("DOWN_RETURNED_TO_CENTER", "down", price, center, down_extreme)
        return _leave_return_result("DOWN_RETURN_BROKEN", "down", price, center, down_extreme)

    return _leave_return_result("NO_LEAVE", "", price, center, price)


def _leave_return_result(status: str, direction: str, price: float, center: Center, extreme: float) -> dict:
    returned = status in {"UP_RETURNED_TO_CENTER", "DOWN_RETURNED_TO_CENTER"}
    broken = status in {"UP_RETURN_BROKEN", "DOWN_RETURN_BROKEN"}
    return {
        "status": status,
        "direction": direction,
        "has_left": status not in {"UNKNOWN", "NO_LEAVE"},
        "has_returned": returned,
        "is_broken": broken,
        "price": _round_price(price),
        "center_zd": _round_price(center.zd),
        "center_zg": _round_price(center.zg),
        "leave_extreme": _round_price(extreme),
    }


def _binding_to_center(price: float, center: Center) -> dict:
    if price <= 0 or not center.is_valid:
        return {
            "status": "unknown",
            "zd": 0.0,
            "zg": 0.0,
            "distance_to_zd": 0.0,
            "distance_to_zg": 0.0,
        }
    if price > center.zg:
        status = "above_zg"
    elif price < center.zd:
        status = "below_zd"
    else:
        status = "inside"
    return {
        "status": status,
        "zd": _round_price(center.zd),
        "zg": _round_price(center.zg),
        "distance_to_zd": _round_delta(price - center.zd),
        "distance_to_zg": _round_delta(price - center.zg),
    }


def _is_bsp_in_latest_window(level: dict, bsp: dict) -> bool:
    bsp_time = _bsp_time(bsp)
    if not bsp_time:
        return False
    active_center = level.get("active_zhongshu") or {}
    center_begin = active_center.get("begin_date")
    if center_begin:
        return str(bsp_time) >= str(center_begin)
    bis = level.get("detail_bis") or level.get("recent_bis") or level.get("bis") or []
    if len(bis) <= 3:
        return True
    starts = [
        item.get("x0") or item.get("start_date")
        for item in bis[-3:]
        if item.get("x0") or item.get("start_date")
    ]
    return bool(starts) and str(bsp_time) >= str(min(starts))


def _divergence_from_level(level: dict) -> Divergence:
    div_info = level.get("div_info") or {}
    div_type = str(div_info.get("type") or "")
    patterns = " ".join(str(item) for item in level.get("patterns") or [])
    if not div_type:
        if "底背驰" in patterns:
            div_type = "底背驰"
        elif "顶背驰" in patterns:
            div_type = "顶背驰"
    direction = "bottom" if div_type == "底背驰" else "top" if div_type == "顶背驰" else ""
    return Divergence(
        type=div_type,
        direction=direction,
        severity=str(div_info.get("severity") or div_info.get("classification") or ""),
        is_valid=bool(direction),
    )


def _momentum_compare_from_level(level: dict, last_bi_dir: str) -> dict:
    bis = level.get("detail_bis") or level.get("recent_bis") or level.get("bis") or []
    is_up = None
    if last_bi_dir in ("up", "向上", "向上（未确认）"):
        is_up = True
    elif last_bi_dir in ("down", "向下", "向下（未确认）"):
        is_up = False
    return build_momentum_compare(bis, is_up=is_up)


def _historical_high_from_level(level: dict, current_price: float) -> dict:
    explicit = level.get("historical_high") or {}
    if explicit:
        high_price = _num(explicit.get("price") or explicit.get("high"))
        high_time = str(explicit.get("time") or explicit.get("date") or "")
    else:
        klines = level.get("klines") or level.get("recent_klines") or []
        best = {}
        for kline in klines:
            high = _num(kline.get("high"))
            if high > _num(best.get("high")):
                best = kline
        high_price = _num(best.get("high"))
        high_time = str(best.get("time") or best.get("date") or "")
    if high_price <= 0 or current_price <= 0:
        return {}
    distance_pct = round((high_price - current_price) / current_price, 4)
    return {
        "price": _round_price(high_price),
        "time": high_time,
        "distance_pct": distance_pct,
        "is_near": 0 <= distance_pct <= 0.08,
        "is_breakout": current_price >= high_price,
    }


def _tags_from_level(
    level: dict,
    raw_state: str,
    buy_events: list[BspEvent],
    sell_events: list[BspEvent],
    divergence: Divergence,
) -> list[str]:
    tags: list[str] = []
    patterns = " ".join(str(item) for item in level.get("patterns") or [])
    if any(event.is_current for event in buy_events) or any(word in patterns for word in BUY_PATTERNS):
        tags.append("BUY_SIGNAL")
    if any(event.is_current for event in sell_events) or any(word in patterns for word in SELL_PATTERNS):
        tags.append("SELL_SIGNAL")
    if raw_state == "THIRD_BUY_CONFIRMED" or any(event.family == "THIRD_BUY" for event in buy_events):
        tags.append("THIRD_BUY")
    if raw_state == "THIRD_SELL_CONFIRMED" or any(event.family == "THIRD_SELL" for event in sell_events):
        tags.append("THIRD_SELL")
    if divergence.direction == "bottom":
        tags.append("BOTTOM_DIVERGENCE")
    if divergence.direction == "top":
        tags.append("TOP_DIVERGENCE")
    return sorted(set(tags))


def _level(levels: dict, public_level: str) -> dict:
    return (
        levels.get(public_level)
        or levels.get(_legacy_level_key(public_level))
        or levels.get(f"m{public_level}")
        or {}
    )


def _legacy_level_key(public_level: str) -> str:
    if public_level in ("day", "week"):
        return public_level
    return f"m{public_level}"


def _is_stale(freshness: dict) -> bool:
    if freshness.get("is_stale"):
        return True
    for item in (freshness.get("levels") or {}).values():
        if isinstance(item, dict) and item.get("is_stale"):
            return True
    return False


def _normalize_bsp_type(raw_type) -> str:
    if isinstance(raw_type, (list, tuple)):
        raw_type = raw_type[0] if raw_type else ""
    return str(raw_type or "").strip().lower()


def _bsp_time(bsp: dict) -> str:
    return str(bsp.get("time") or bsp.get("date") or bsp.get("x") or "")


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _atom_to_dict(atom: LevelAtom) -> dict:
    result = asdict(atom)
    return result
