"""Build the Radar response payload for Semantic Signal V2."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from server.engines.signal.compiler import choose_primary_atom, compile_signal
from server.engines.signal.kronos_extractor import extract_envelope, extract_timeline
from server.engines.signal.models import DISCLAIMER, SignalContext
from server.engines.signal.translator import translate_signal


SHANGHAI_TZ = timezone(timedelta(hours=8))


def build_signal_v2(
    algorithm_v2: dict,
    *,
    symbol: str,
    quote: dict | None = None,
    position_context: dict | None = None,
    kronos_forecast: dict | None = None,
    disclaimer: str = DISCLAIMER,
) -> dict:
    """Build the `signals_v2` Radar response block.

    失败时返回 error 状态，不影响 algorithm_v2 主结构雷达。
    """
    try:
        role, atom = choose_primary_atom(algorithm_v2 or {})
        if not atom:
            return _empty_signal(disclaimer)

        parts = compile_signal(atom, algorithm_v2)
        translated = translate_signal(parts)
        resonance = _resonance(algorithm_v2, role)
        context = _build_context(
            parts.code,
            atom=atom,
            role=role,
            algorithm_v2=algorithm_v2,
            symbol=symbol,
            quote=quote or {},
            position_context=position_context or {},
            kronos_forecast=kronos_forecast,
            resonance=[item["code"] for item in resonance],
            disclaimer=disclaimer,
        )
        state = "stale" if _is_stale(algorithm_v2) else "success"
        action = _action_text(translated["action_bias"], context.stop_loss_price, context.risk_reward_ratio, state)

        # Kronos 信封如果给出了执行区间，补充到 action 文本
        envelope_hint = _envelope_action_hint(context)

        return {
            "version": "semantic_signal.v2",
            "state": state,
            "primary": {
                "code": parts.code,
                "label_expert": translated["label_expert"],
                "label_plain": translated["label_plain"],
                "action": f"{action}{envelope_hint}" if envelope_hint else action,
                "level": parts.level,
                "pattern": parts.pattern,
                "strength": parts.strength,
            },
            "context": context.to_dict(),
            "resonance": resonance,
            "classification": [],
            "disclaimer": disclaimer,
        }
    except Exception as exc:
        return {
            "version": "semantic_signal.v2",
            "state": "error",
            "primary": {
                "code": "",
                "label_expert": "语义层不可用",
                "label_plain": "语义层不可用，保留结构雷达判断",
                "action": "继续查看下方结构边界",
            },
            "context": {},
            "resonance": [],
            "classification": [],
            "error": str(exc)[:120],
            "disclaimer": disclaimer,
        }


def _build_context(
    signal_code: str,
    *,
    atom: dict,
    role: str,
    algorithm_v2: dict,
    symbol: str,
    quote: dict,
    position_context: dict,
    kronos_forecast: dict | None = None,
    resonance: list[str],
    disclaimer: str,
) -> SignalContext:
    now = datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
    center = atom.get("center") or {}
    boundary_state, key_price = _boundary_anchor(algorithm_v2)
    stop_loss = _stop_loss_price(algorithm_v2, position_context)
    risk_reward = _risk_reward_ratio(key_price, stop_loss, algorithm_v2)
    momentum = atom.get("momentum_compare") or {}
    action_rule = _action_rule(signal_code, stop_loss, risk_reward)

    # Kronos 确定性提取（不经过 LLM）
    kronos_timeline = None
    kronos_envelope = None
    if kronos_forecast:
        try:
            kronos_timeline = extract_timeline(
                kronos_forecast,
                signal_code=signal_code,
            )
        except Exception:
            kronos_timeline = None  # Kronos 提取失败不影响主信号
        try:
            kronos_envelope = extract_envelope(
                kronos_forecast,
                signal_code=signal_code,
            )
        except Exception:
            kronos_envelope = None

    return SignalContext(
        signal_code=signal_code,
        signal_id=_signal_id(symbol, signal_code, role, algorithm_v2),
        timestamp=now,
        symbol=symbol,
        level=str(atom.get("public_level") or atom.get("level") or ""),
        zhongshu={
            "zg": _num(center.get("zg")),
            "zd": _num(center.get("zd")),
            "gg": _num(center.get("gg")),
            "dd": _num(center.get("dd")),
        },
        key_price=key_price or _num(quote.get("price")) or _num(atom.get("price")),
        boundary_state=boundary_state,
        macd_area_ratio=_num(momentum.get("area_ratio")),
        volume_ratio=0.0,
        atr_stop_distance=_atr_stop_distance(_num(quote.get("price")) or _num(atom.get("price")), stop_loss),
        deterministic_scenarios=algorithm_v2.get("scenarios") or [],
        classification=[],
        action_rule=action_rule,
        risk_reward_ratio=risk_reward,
        stop_loss_price=stop_loss,
        historical_win_rate=0.0,
        similar_signals_count=0,
        resonance=resonance,
        kronos_timeline=kronos_timeline,
        kronos_envelope=kronos_envelope,
        disclaimer=disclaimer,
    )


def _resonance(algorithm_v2: dict, primary_role: str) -> list[dict[str, Any]]:
    result = []
    atoms = algorithm_v2.get("atoms") or {}
    for role, atom in atoms.items():
        if role == primary_role or not isinstance(atom, dict):
            continue
        parts = compile_signal(atom, algorithm_v2)
        translated = translate_signal(parts)
        if parts.pattern == "unknown":
            continue
        result.append(
            {
                "level": parts.level,
                "code": parts.code,
                "label_plain": translated["label_plain"],
                "label_expert": translated["label_expert"],
            }
        )
    return result[:3]


def _boundary_anchor(algorithm_v2: dict) -> tuple[str, float]:
    boundaries = algorithm_v2.get("boundaries") or {}
    for state, key in (("confirm", "confirm"), ("observe", "maintain"), ("invalidate", "invalidate")):
        items = boundaries.get(key) or []
        if items:
            return state, _num(items[0].get("value"))
    return "observe", 0.0


def _stop_loss_price(algorithm_v2: dict, position_context: dict) -> float:
    nearest = position_context.get("nearest_risk_line") or {}
    if nearest.get("value"):
        return _num(nearest.get("value"))
    for item in (algorithm_v2.get("boundaries") or {}).get("invalidate") or []:
        value = _num(item.get("value"))
        if value > 0:
            return value
    return 0.0


def _risk_reward_ratio(key_price: float, stop_loss: float, algorithm_v2: dict) -> float:
    if key_price <= 0 or stop_loss <= 0 or key_price == stop_loss:
        return 0.0
    target = 0.0
    for item in (algorithm_v2.get("boundaries") or {}).get("pressure") or []:
        value = _num(item.get("value"))
        if value > key_price:
            target = value
            break
    if target <= 0:
        return 0.0
    risk = abs(key_price - stop_loss)
    if risk <= 0:
        return 0.0
    return round(abs(target - key_price) / risk, 2)


def _action_text(action_bias: str, stop_loss: float, risk_reward: float, state: str) -> str:
    if state == "stale":
        return "等待刷新确认"
    if risk_reward and risk_reward < 1.5:
        return "赔率不足，建议观望"
    stop = f"，止损 {stop_loss:.2f}" if stop_loss > 0 else ""
    return f"建议{action_bias}{stop}"


def _action_rule(signal_code: str, stop_loss: float, risk_reward: float) -> str:
    if risk_reward and risk_reward < 1.5:
        return f"由于出现信号 {signal_code}，但风险收益比低于 1.5，根据纪律规则，建议观望。"
    stop = f"，止损 {stop_loss:.2f}" if stop_loss > 0 else ""
    return f"由于出现信号 {signal_code}，根据语义信号规则，建议按结构边界执行{stop}。"


def _atr_stop_distance(price: float, stop_loss: float) -> float:
    if price <= 0 or stop_loss <= 0:
        return 0.0
    return round(abs(price - stop_loss) / price * 100, 2)


def _is_stale(algorithm_v2: dict) -> bool:
    notes = algorithm_v2.get("data_notes") or {}
    if notes.get("is_stale"):
        return True
    levels = notes.get("levels") or {}
    return any(bool(item.get("is_stale")) for item in levels.values() if isinstance(item, dict))


def _empty_signal(disclaimer: str) -> dict:
    return {
        "version": "semantic_signal.v2",
        "state": "empty",
        "primary": {
            "code": "",
            "label_expert": "暂无优势信号",
            "label_plain": "结构未给出优势信号，继续观察边界",
            "action": "继续观察",
        },
        "context": {},
        "resonance": [],
        "classification": [],
        "disclaimer": disclaimer,
    }


def _signal_id(symbol: str, signal_code: str, role: str, algorithm_v2: dict) -> str:
    fingerprint = "|".join(
        [
            symbol,
            signal_code,
            role,
            str(algorithm_v2.get("current_scenario_id") or ""),
            str(algorithm_v2.get("a_state") or ""),
        ]
    )
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _envelope_action_hint(context: SignalContext) -> str:
    """如果 Kronos 信封给出了执行区间，生成补充文本。"""
    envelope = context.kronos_envelope
    if not envelope:
        return ""
    high = envelope.get("envelope_high", 0)
    low = envelope.get("envelope_low", 0)
    if high <= 0 or low <= 0:
        return ""
    validation = envelope.get("validation") or ""
    if validation.startswith("CONFLICT"):
        return f" | ⚠️ 日线预测区间 {low:.2f}-{high:.2f} 与操作点存在偏差"
    return f" | 今日执行区间参考: {low:.2f}-{high:.2f}"


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
