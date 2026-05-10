"""Radar API compatibility shell.

第一版 Radar 先包住现有 matrix 引擎，输出新 contract 形状。
后续再把 structure/decision 拆到独立 engines。
"""

import asyncio
import copy
import datetime as dt
import inspect
import json
import logging
import time
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from server.db.database import get_connection
from server.db.kline_lake import get_lake_connection
from server.domain.symbols import normalize_symbol, symbol_aliases
from server.engines.decision.level_chain_deduction import build_level_chain_deduction
from server.engines.decision.position_coach import build_coach_action, build_position_context
from server.engines.decision.radar_algorithm_v2 import build_radar_algorithm_v2
from server.engines.decision.radar_planner import build_radar_decision
from server.engines.signal import build_signal_v2
from server.engines.structure.chan_adapter import analyze_structure
from server.engines.structure.kernel import build_structure_data_signature, build_structure_kernel
from server.engines.structure.kernel_cache import load_structure_kernel_cache, save_structure_kernel_cache
from server.services.price_service import get_current_price

router = APIRouter()
logger = logging.getLogger(__name__)

DISCLAIMER = "仅供参考，不构成投资建议"
RADAR_API_VERSION = "radar.v1"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
STRUCTURE_CACHE_TTL_SECONDS = 120
STRUCTURE_CACHE_MAX_ITEMS = 64
RADAR_PROFILE_LEVELS = {
    "auto": ["day", "30", "5"],
    "fast": ["day", "30", "5"],
    "full": ["week", "day", "60", "30", "15", "5"],
}
RADAR_PROFILE_COMPUTE_PROFILE = {
    "auto": "radar_tactical_v1",
    "fast": "radar_tactical_v1",
    "full": "chart_standard_v1",
}

_structure_cache: dict[tuple, dict] = {}
_structure_cache_locks: dict[tuple, asyncio.Lock] = {}
HEALTH_LEVELS = ("day", "60", "30", "15", "5")
HEALTH_MAX_LAG_DAYS = 0


def _normalize_symbol(symbol: str) -> str:
    """兼容旧前端的 symbol 输入，内部统一为 sh.600519 形态。"""
    return normalize_symbol(symbol)


def _now_iso() -> str:
    return dt.datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")


def _query_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(getattr(value, "default", default))
        except (TypeError, ValueError):
            return default


def _query_optional_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        default = getattr(value, "default", None)
        return _query_optional_int(default)


def _query_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        query_default = getattr(value, "default", default)
        if query_default is not value:
            return _query_int(query_default, default)
        return default


def _query_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    query_default = getattr(value, "default", None)
    if query_default is not None and query_default is not value:
        return _query_bool(query_default, default)
    return bool(value)


def _query_profile(value, default: str = "fast") -> str:
    text = str(value or "").strip().lower()
    if text in RADAR_PROFILE_LEVELS:
        return text
    query_default = getattr(value, "default", None)
    if query_default is not None and query_default is not value:
        return _query_profile(query_default, default)
    return default


def _adapter_profile_for_request(profile: str) -> str:
    """auto 先复用 fast 结构，必要时在 get_radar 中升级到 full。"""
    return "fast" if profile == "auto" else profile


def _mode_from_holding(holding: Optional[dict]) -> str:
    return "HOLDING" if holding else "EMPTY"


def _load_holding_from_position(user_id: Optional[int], symbol: str) -> Optional[dict]:
    if not user_id:
        return None
    aliases = symbol_aliases(symbol)
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT quantity, avg_cost, current_price, stop_loss_price,
                   trailing_stop_price, entry_date,
                   strategy_type, m5_entry_zg, entry_thesis_json
              FROM positions
             WHERE user_id = ? AND symbol IN (?, ?, ?) AND quantity > 0
             ORDER BY CASE symbol
                      WHEN ? THEN 0
                      WHEN ? THEN 1
                      ELSE 2
                      END
             LIMIT 1
            """,
            (user_id, *aliases, aliases[0], aliases[1]),
        ).fetchone()
        if not row:
            return None
        thesis = {}
        if row["entry_thesis_json"]:
            try:
                thesis = json.loads(row["entry_thesis_json"])
            except Exception:
                thesis = {"schema_version": 1, "strategy_type": row["strategy_type"] or "未知"}
        return {
            "cost": row["avg_cost"],
            "avg_cost": row["avg_cost"],
            "qty": row["quantity"],
            "quantity": row["quantity"],
            "current_price": row["current_price"],
            "stop_loss_price": row["stop_loss_price"],
            "trailing_stop_price": row["trailing_stop_price"],
            "entry_date": row["entry_date"],
            "strategy_type": row["strategy_type"] or thesis.get("strategy_type") or "未知",
            "m5_entry_zg": row["m5_entry_zg"],
            "entry_thesis": thesis,
        }
    finally:
        conn.close()


def _public_level_name(level: str) -> str:
    return level[1:] if level.startswith("m") else level


def _build_structure_from_adapter(adapter_result: dict) -> dict:
    """Build Radar structure from chan_adapter output.

    Decision fields still come from the legacy matrix during migration. Formal
    basic structure fields should come from chan_adapter whenever available.
    """
    adapter_levels = adapter_result.get("levels") or {}
    levels = {}
    for raw_level, data in adapter_levels.items():
        public_level = data.get("level") or _public_level_name(str(raw_level))
        levels[public_level] = {
            "level": public_level,
            "price": data.get("price", _level_last_price(data)),
            "state": data.get("state", "UNKNOWN"),
            "data_status": "ok" if not data.get("error") else "error",
            "bi_count": data.get("stats", {}).get("bi_count", len(data.get("bis", []))),
            "seg_count": data.get("stats", {}).get("seg_count", len(data.get("segs", []))),
            "zhongshu_count": data.get("stats", {}).get(
                "bi_zs_count",
                len(data.get("bi_zhongshus", data.get("zhongshus", []))),
            ),
            "zg": data.get("zg", 0),
            "zd": data.get("zd", 0),
            "zs_operative_zg": data.get("zs_operative_zg", data.get("zg", 0)),
            "zs_operative_zd": data.get("zs_operative_zd", data.get("zd", 0)),
            "last_bi_dir": data.get("last_bi_dir", "unknown"),
            "active_zhongshu": data.get("active_zhongshu") or _latest_zhongshu(data),
            "bis": data.get("bis", []),
            "segs": data.get("segs", []),
            "bi_zhongshus": data.get("bi_zhongshus", data.get("zhongshus", [])),
            "seg_zhongshus": data.get("seg_zhongshus", []),
            "bsps": data.get("bsps", []),
            "recent_bis": data.get("bis", [])[-8:],
            "detail_bis": data.get("bis", [])[-8:],
            "recent_klines": data.get("klines", [])[-120:],
            "patterns": data.get("patterns", []),
            "zoushi_type": data.get("zoushi_type", {}),
            "classifications": data.get("classifications", []),
            "derived": {
                "recent_ex": data.get("recent_ex", {}),
            },
            "source": data.get("source", adapter_result.get("data_source", {}).get("structure", {})),
        }

    day = levels.get("day", {})
    return {
        "levels": levels,
        "systems": {
            "short_term": {
                "name": "day_30_5",
                "levels": ["day", "30", "5"],
                "interval_nesting": (
                    adapter_result.get("interval_nesting", {}).get("short_term")
                    or adapter_result.get("level_relations", {})
                ),
            },
            "swing": {
                "name": "day_60_15",
                "levels": ["day", "60", "15"],
                "interval_nesting": (
                    adapter_result.get("interval_nesting", {}).get("swing")
                    or adapter_result.get("level_relations", {})
                ),
            },
        },
        "summary": {
            "primary_level": "day",
            "trend_state": day.get("state", "UNKNOWN"),
            "risk_state": "NORMAL",
            "key_levels": {
                "support": _support_levels(day),
                "resistance": _resistance_levels(day),
            },
        },
    }


def _level_last_price(level_data: dict) -> float:
    klines = level_data.get("klines") or []
    if not klines:
        return 0
    try:
        return float(klines[-1].get("close", 0))
    except Exception:
        return 0


def _latest_zhongshu(level_data: dict) -> dict:
    zhongshus = level_data.get("bi_zhongshus") or level_data.get("zhongshus") or []
    if not zhongshus:
        return {}
    return zhongshus[-1]


def _support_levels(day: dict) -> list:
    active = day.get("active_zhongshu") or {}
    candidates = [active.get("zd")]
    return [value for value in candidates if value]


def _resistance_levels(day: dict) -> list:
    active = day.get("active_zhongshu") or {}
    candidates = [active.get("zg")]
    return [value for value in candidates if value]


def _adapter_structure_ready(adapter_result: dict) -> bool:
    return bool(
        adapter_result
        and not adapter_result.get("error")
        and adapter_result.get("levels")
    )


def _decision_levels_from_adapter(adapter_result: dict, legacy_levels: dict) -> dict:
    if not _adapter_structure_ready(adapter_result):
        return legacy_levels

    result = {}
    for raw_level, data in (adapter_result.get("levels") or {}).items():
        public_level = data.get("level") or _public_level_name(str(raw_level))
        legacy_key = _legacy_level_key(public_level)
        result[legacy_key] = {
            **data,
            "level": legacy_key,
            "price": data.get("price", _level_last_price(data)),
            "zg": data.get("zg", 0),
            "zd": data.get("zd", 0),
            "zs_operative_zg": data.get("zs_operative_zg", data.get("zg", 0)),
            "zs_operative_zd": data.get("zs_operative_zd", data.get("zd", 0)),
            "detail_bis": data.get("bis", [])[-8:],
            "recent_klines": data.get("klines", [])[-120:],
            "patterns": data.get("patterns", []),
            "zoushi_type": data.get("zoushi_type", {}),
            "classifications": data.get("classifications", []),
            "last_bi_dir": data.get("last_bi_dir", "unknown"),
            "historical_high": _historical_high_from_level_data(data),
        }

    merged = dict(legacy_levels)
    merged.update(result)
    return merged


def _historical_high_from_level_data(level_data: dict) -> dict:
    """取当前主动走势之前的结构前高，避免拿同一笔正在上攻的高点当压力。"""
    klines = list(level_data.get("klines") or [])
    current_price = _level_last_price(level_data)
    structural_high = _historical_high_from_bis(level_data.get("bis") or [], current_price)
    if structural_high:
        current_bar = klines[-1] if klines else {}
        high_price = _safe_float(structural_high.get("price"))
        current_high = _safe_float(current_bar.get("high"))
        return {
            **structural_high,
            "current_bar_high": current_high,
            "current_bar_time": current_bar.get("time") or current_bar.get("date") or "",
            "is_current_bar_new_high": current_high > high_price > 0,
        }
    return _historical_high_from_klines(klines)


def _historical_high_from_bis(bis: list[dict], current_price: float) -> dict:
    if not bis:
        return {}

    eligible = list(bis)
    last_bi = bis[-1]
    last_end = _safe_float(last_bi.get("y1"))

    # 当前价已越过最后一根确认向上笔的终点时，这个高点属于同一笔/同一段延伸，
    # 不能再作为“前高压力”；向前找已经确认过的旧结构高点。
    if bool(last_bi.get("is_up")) and current_price > last_end > 0:
        eligible = bis[:-1]

    best = {}
    for bi in eligible:
        if bool(bi.get("is_up")):
            price = _safe_float(bi.get("y1"))
            time_value = bi.get("x1") or ""
        else:
            price = _safe_float(bi.get("y0"))
            time_value = bi.get("x0") or ""
        if price > _safe_float(best.get("price")):
            best = {
                "price": price,
                "time": time_value,
                "source": "confirmed_bi",
            }
    return best


def _historical_high_from_klines(klines: list[dict]) -> dict:
    klines = list(klines or [])
    if not klines:
        return {}

    # 历史前高必须是“当前 K 线之前”的价格记忆。
    # 若今天盘中/当日 K 线刚创出新高，它应被解释为“突破尝试”，不能反过来成为新的压力位。
    current_bar = klines[-1]
    prior_klines = klines[:-1]
    if not prior_klines:
        return {}

    best = {}
    for kline in prior_klines:
        try:
            high = float(kline.get("high") or 0)
        except (TypeError, ValueError):
            high = 0
        try:
            best_high = float(best.get("high") or 0)
        except (TypeError, ValueError):
            best_high = 0
        if high > best_high:
            best = kline
    if not best:
        return {}
    current_high = _safe_float(current_bar.get("high"))
    best_high = _safe_float(best.get("high"))
    return {
        "price": best.get("high"),
        "time": best.get("time") or best.get("date") or "",
        "current_bar_high": current_high,
        "current_bar_time": current_bar.get("time") or current_bar.get("date") or "",
        "is_current_bar_new_high": current_high > best_high > 0,
    }


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _decision_levels_from_adapter_only(adapter_result: dict) -> dict:
    return _decision_levels_from_adapter(adapter_result, {})


def _legacy_level_key(public_level: str) -> str:
    if public_level in ("day", "week"):
        return public_level
    return f"m{public_level}"


def _build_data_source_from_adapter(adapter_result: dict) -> dict:
    structure = (adapter_result.get("data_source") or {}).get("structure") or {}
    levels = list((adapter_result.get("levels") or {}).keys()) or RADAR_PROFILE_LEVELS["fast"]
    return {
        "structure": {
            "provider": structure.get("provider", "baostock"),
            "adjustflag": structure.get("adjustflag", "2"),
            "levels": levels,
            "profile": adapter_result.get("_profile") or "fast",
            "engine": structure.get("engine", "chan.py"),
            "adapter": structure.get("adapter", "server.engines.structure.chan_adapter"),
            "compatibility_mode": False,
        },
        "quote": {
            "provider": "tencent",
            "purpose": "current_price_display_only",
        },
    }


def _structure_config_from_adapter(adapter_result: dict) -> dict:
    config = adapter_result.get("structure_config") or {}
    if not config:
        return {}
    return {
        "preset": config.get("preset"),
        "label": config.get("label"),
        "version": config.get("version"),
    }


def _build_freshness_from_adapter(adapter_result: dict) -> dict:
    freshness = dict(adapter_result.get("freshness") or {})
    freshness.setdefault("source", "baostock")
    freshness.setdefault("adjustflag", "2")
    freshness.setdefault("checked_at", _now_iso())
    freshness.setdefault("is_stale", False)
    freshness.setdefault("stale_reason", "")
    return freshness


def _build_adapter_refs() -> dict:
    return {
        "source_endpoint": None,
        "structure_adapter": "server.engines.structure.chan_adapter",
        "decision_planner": "server.engines.decision.radar_planner",
        "compatibility_mode": False,
    }


def _compact_algorithm_for_display(algorithm: dict) -> dict:
    """压缩前端展示用算法输出，避免把完整事件链随雷达响应传给浏览器。"""
    result = dict(algorithm)
    result["atoms"] = {
        role: _compact_atom_for_display(atom)
        for role, atom in (algorithm.get("atoms") or {}).items()
    }
    return result


def _compact_atom_for_display(atom: dict) -> dict:
    return {
        "level": atom.get("level"),
        "public_level": atom.get("public_level"),
        "price": atom.get("price"),
        "raw_state": atom.get("raw_state"),
        "position_state": atom.get("position_state"),
        "center": atom.get("center", {}),
        "center_relation": atom.get("center_relation"),
        "leave_return_status": atom.get("leave_return_status", {}),
        "last_bi_dir": atom.get("last_bi_dir"),
        "center_binding": _compact_center_binding(atom.get("center_binding") or {}),
        "momentum_compare": atom.get("momentum_compare", {}),
        "historical_high": atom.get("historical_high", {}),
        "tags": atom.get("tags", []),
        "quality": atom.get("quality"),
    }


def _compact_center_binding(binding: dict) -> dict:
    return {
        "role": binding.get("role"),
        "level": binding.get("level"),
        "center": binding.get("center", {}),
        "relation": binding.get("relation"),
    }


async def _load_realtime_quote(symbol: str) -> dict:
    """Load current Tencent quote for intraday display and provisional path overlay.

    正式结构仍使用 CChan K 线切片；实时价只允许做盘中 provisional
    A/B/C 覆盖提示，不能替代闭合 K 线结构。
    """
    try:
        quote = await get_current_price(symbol)
    except Exception as exc:
        logger.warning("Radar quote load failed: symbol=%s error=%s", symbol, exc)
        return {"provider": "tencent", "available": False, "error": str(exc)}
    if not quote:
        return {"provider": "tencent", "available": False}
    return {
        "provider": "tencent",
        "available": True,
        "symbol": quote.get("symbol"),
        "name": quote.get("name"),
        "price": quote.get("price"),
        "change": quote.get("change"),
        "change_pct": quote.get("change_pct"),
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "prev_close": quote.get("prev_close"),
        "time": quote.get("time") or quote.get("datetime") or "",
        "purpose": "position_context_only",
    }


def _apply_intraday_quote_overlay(algorithm: dict, quote: dict) -> dict:
    """Use latest quote to prevent stale closed-bar A/B/C from misleading live view."""
    price = _query_float((quote or {}).get("price"), 0.0)
    if price <= 0:
        return algorithm

    structure_price = _algorithm_structure_price(algorithm)
    if structure_price <= 0:
        return algorithm

    gap_pct = abs(price - structure_price) / structure_price * 100
    if gap_pct < 3:
        return algorithm

    boundaries = algorithm.get("boundaries") or {}
    realtime_quote = {"last_price": price, "high": price, "low": price}
    invalidates = [
        item for item in boundaries.get("invalidate") or []
        if _quote_triggers_boundary(item, realtime_quote)
    ]
    confirms = boundaries.get("confirm") or []
    matched_confirms = [
        item for item in confirms
        if _quote_triggers_boundary(item, realtime_quote)
    ]
    maintains = [
        item for item in boundaries.get("maintain") or []
        if _quote_triggers_boundary(item, realtime_quote)
    ]

    if invalidates:
        scenario_id = "C"
        state = "C_INTRADAY_TRIGGERED"
        meaning = "盘中实时价触发 C 路径失效边界，等待分钟K线闭合确认。"
        matched = invalidates
        unmatched = confirms
        progress = 0.0
        summary = "盘中实时价触发失效边界，等待分钟结构确认。"
    elif matched_confirms:
        scenario_id = "A"
        state = "A_INTRADAY_FULL_TRIGGERED" if len(matched_confirms) == len(confirms) else "A_INTRADAY_PARTIAL_TRIGGERED"
        meaning = "盘中实时价触发 A 路径确认边界，等待分钟K线闭合确认。"
        matched = matched_confirms
        unmatched = [item for item in confirms if item not in matched_confirms]
        progress = round(len(matched_confirms) / len(confirms), 4) if confirms else 1.0
        summary = "盘中实时价已触发转强边界，等待分钟结构回写确认。"
    elif maintains:
        scenario_id = "B"
        state = "B_INTRADAY_MAINTAINED"
        meaning = "盘中实时价仍满足 B 路径维持边界，等待分钟K线闭合确认。"
        matched = maintains
        unmatched = confirms
        progress = 1.0
        summary = "盘中实时价仍维持当前路径，等待分钟结构确认。"
    else:
        return algorithm

    result = copy.deepcopy(algorithm)
    overlay = {
        "source": "realtime_quote",
        "provider": (quote or {}).get("provider") or "",
        "price": price,
        "structure_price": structure_price,
        "gap_pct": round(gap_pct, 2),
        "scenario_id": scenario_id,
        "state": state,
        "meaning": meaning,
        "summary": summary,
        "matched": matched,
        "unmatched": unmatched,
        "is_provisional": True,
    }
    result["intraday_overlay"] = overlay
    result["current_scenario_id"] = scenario_id
    result["a_state"] = state
    result["confirmation"] = {
        "state": state,
        "progress": progress,
        "matched": matched,
        "unmatched": unmatched,
        "meaning": meaning,
        "source": "realtime_quote",
        "is_provisional": True,
    }
    result["scenarios"] = _scenarios_with_intraday_state(result.get("scenarios") or [], scenario_id)
    result["summary"] = summary
    return result


def _algorithm_structure_price(algorithm: dict) -> float:
    for role in ("L2", "L1", "L0"):
        price = _query_float(((algorithm.get("atoms") or {}).get(role) or {}).get("price"), 0.0)
        if price > 0:
            return price
    return 0.0


def _quote_triggers_boundary(boundary: dict, quote: dict) -> bool:
    value = _query_float((boundary or {}).get("value"), 0.0)
    if value <= 0:
        return False
    trigger = str((boundary or {}).get("trigger") or "")
    last_price = _query_float(quote.get("last_price"), 0.0)
    high = _query_float(quote.get("high"), last_price) or last_price
    low = _query_float(quote.get("low"), last_price) or last_price

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


def _scenarios_with_intraday_state(scenarios: list[dict], current_id: str) -> list[dict]:
    adjusted = []
    for scenario in scenarios:
        item = dict(scenario)
        sid = item.get("id")
        if sid == current_id:
            item["state"] = "CURRENT"
        elif current_id == "A" and sid == "B":
            item["state"] = "CONFIRMED"
        elif current_id == "A" and sid == "C":
            item["state"] = "PENDING"
        elif current_id == "C" and sid == "A":
            item["state"] = "BLOCKED"
        elif current_id == "C" and sid == "B":
            item["state"] = "FAILED"
        else:
            item["state"] = item.get("state") or "PENDING"
        adjusted.append(item)
    return adjusted


async def _load_adapter_structure(symbol: str, *, profile: str = "full") -> dict:
    """Load formal structure through chan_adapter.

    Kept as a small wrapper so tests and later feature flags can isolate this
    migration point.
    """
    levels = RADAR_PROFILE_LEVELS.get(profile, RADAR_PROFILE_LEVELS["fast"])
    compute_profile = RADAR_PROFILE_COMPUTE_PROFILE.get(profile, "radar_tactical_v1")
    return await analyze_structure(
        symbol,
        levels=levels,
        count=1200,
        compute_profile=compute_profile,
    )


def _structure_cache_key(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 1200,
    cchan_preset: str = "live_tolerant",
    compute_profile: str = "radar_tactical_v1",
) -> tuple:
    return (
        symbol,
        tuple(levels or ["week", "day", "60", "30", "15", "5"]),
        count,
        cchan_preset,
        compute_profile,
    )


def _clear_structure_cache() -> None:
    """测试和运维用：清空 Radar 结构短缓存。"""
    _structure_cache.clear()
    _structure_cache_locks.clear()


def _profiled_structure_data_signature(symbol: str, levels: list[str], compute_profile: str) -> str:
    signature = build_structure_data_signature(symbol, levels)
    if not signature:
        return ""
    return f"{signature}:compute_profile={compute_profile}"


def _trim_structure_cache(now: float) -> None:
    expired = [
        key for key, value in _structure_cache.items()
        if now - value["cached_at"] >= STRUCTURE_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _structure_cache.pop(key, None)

    while len(_structure_cache) > STRUCTURE_CACHE_MAX_ITEMS:
        oldest_key = min(
            _structure_cache,
            key=lambda item: _structure_cache[item]["cached_at"],
        )
        _structure_cache.pop(oldest_key, None)


async def _load_cached_adapter_structure(symbol: str) -> dict:
    return await _load_cached_adapter_structure_for_profile(symbol, profile="full")


async def _load_cached_adapter_structure_for_profile(symbol: str, *, profile: str = "fast") -> dict:
    levels = RADAR_PROFILE_LEVELS.get(profile, RADAR_PROFILE_LEVELS["fast"])
    cchan_preset = "live_tolerant"
    compute_profile = RADAR_PROFILE_COMPUTE_PROFILE.get(profile, "radar_tactical_v1")
    key = _structure_cache_key(symbol, levels=levels, compute_profile=compute_profile)
    started = time.perf_counter()
    now = time.monotonic()
    cached = _structure_cache.get(key)
    if cached and now - cached["cached_at"] < STRUCTURE_CACHE_TTL_SECONDS:
        logger.info("Radar structure cache hit: symbol=%s profile=%s", symbol, profile)
        result = copy.deepcopy(cached["result"])
        result["_cache_hit"] = True
        result["_profile"] = profile
        result["_structure_ms"] = round((time.perf_counter() - started) * 1000)
        return result

    lock = _structure_cache_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _structure_cache.get(key)
        if cached and now - cached["cached_at"] < STRUCTURE_CACHE_TTL_SECONDS:
            logger.info("Radar structure cache hit-after-wait: symbol=%s profile=%s", symbol, profile)
            result = copy.deepcopy(cached["result"])
            result["_cache_hit"] = True
            result["_profile"] = profile
            result["_structure_ms"] = round((time.perf_counter() - started) * 1000)
            return result

        data_signature = _profiled_structure_data_signature(symbol, levels, compute_profile)
        use_persistent_cache = _adapter_loader_is_native()
        if use_persistent_cache:
            persisted = load_structure_kernel_cache(
                symbol=symbol,
                profile=profile,
                cchan_preset=cchan_preset,
                data_signature=data_signature,
            )
            if persisted:
                result = copy.deepcopy(persisted)
                result["_cache_hit"] = True
                result["_persistent_cache_hit"] = True
                result["_profile"] = profile
                result["_structure_ms"] = round((time.perf_counter() - started) * 1000)
                _structure_cache[key] = {
                    "cached_at": time.monotonic(),
                    "result": copy.deepcopy(result),
                }
                _trim_structure_cache(time.monotonic())
                logger.info("Radar structure persistent cache hit: symbol=%s profile=%s", symbol, profile)
                return result

        result = await _call_load_adapter_structure(symbol, profile=profile)
        if not data_signature:
            data_signature = _profiled_structure_data_signature(symbol, levels, compute_profile)
        kernel = build_structure_kernel(
            symbol=symbol,
            profile=profile,
            levels=levels,
            adapter_result=result,
            data_signature=data_signature,
        )
        result["structure_kernel"] = kernel
        _structure_cache[key] = {
            "cached_at": time.monotonic(),
            "result": copy.deepcopy(result),
        }
        if use_persistent_cache:
            save_structure_kernel_cache(
                symbol=symbol,
                profile=profile,
                cchan_preset=cchan_preset,
                data_signature=data_signature,
                structure_fingerprint=kernel["structure_fingerprint"],
                result=result,
            )
        _trim_structure_cache(time.monotonic())
        logger.info("Radar structure cache miss: symbol=%s profile=%s", symbol, profile)
        result["_cache_hit"] = False
        result["_persistent_cache_hit"] = False
        result["_profile"] = profile
        result["_structure_ms"] = round((time.perf_counter() - started) * 1000)
        return result


def _adapter_loader_is_native() -> bool:
    """Avoid persistent cache when tests or callers monkeypatch the loader."""
    return getattr(_load_adapter_structure, "__module__", __name__) == __name__


async def _call_load_adapter_structure(symbol: str, *, profile: str) -> dict:
    """Call the adapter wrapper while preserving old test monkeypatch signatures."""
    try:
        signature = inspect.signature(_load_adapter_structure)
        accepts_profile = (
            "profile" in signature.parameters
            or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        )
    except (TypeError, ValueError):
        accepts_profile = True

    if accepts_profile:
        return await _load_adapter_structure(symbol, profile=profile)
    return await _load_adapter_structure(symbol)


def _auto_profile_upgrade_reason(
    *,
    algorithm_v2: dict,
    freshness: dict,
    coach_action: dict,
) -> Optional[str]:
    """Decide whether auto should pay for full multi-level structure.

    结构雷达默认只算 day/30/5。只有当 fast 无法给用户一个清楚门禁，
    或持仓已接近风险线时，才升级到 full。
    """
    if not algorithm_v2:
        return "LOW_CONFIDENCE"
    if freshness.get("is_stale"):
        return "LOW_CONFIDENCE"

    confidence = str(algorithm_v2.get("confidence") or "").upper()
    path = str(algorithm_v2.get("path") or "").upper()
    relation = str(algorithm_v2.get("relation") or "").upper()
    if confidence in {"LOW", "STALE", "UNKNOWN"} or path == "NO_EDGE":
        return "LOW_CONFIDENCE"
    if relation == "CONFLICT_OR_UNKNOWN":
        return "FAST_CONFLICT"

    nearest = coach_action.get("nearest_risk_line") or {}
    distance_pct = nearest.get("distance_pct")
    try:
        distance = abs(float(distance_pct))
    except (TypeError, ValueError):
        distance = None
    if distance is not None and distance <= 2:
        return "RISK_LINE_NEAR"

    return None


@router.get("/health/watchlist")
async def get_watchlist_data_health(user_id: int = Query(default=1)):
    """扫描自选股多级别 K 线新鲜度，不运行 CChan。"""
    symbols = _load_watchlist_symbols(user_id)
    items = [_symbol_data_health(symbol) for symbol in symbols]
    stale_count = sum(1 for item in items if item["is_stale"])
    return {
        "status": "success",
        "data": {
            "user_id": user_id,
            "checked_at": _now_iso(),
            "levels": list(HEALTH_LEVELS),
            "count": len(items),
            "stale_count": stale_count,
            "items": items,
            "disclaimer": DISCLAIMER,
        },
    }


@router.get("/{symbol}")
async def get_radar(
    symbol: str,
    user_id: Optional[int] = Query(default=None, description="用户ID，第一版仅预留"),
    cost: float = Query(default=0.0, description="持仓成本价，0=空仓"),
    qty: int = Query(default=0, description="持仓数量，0=空仓"),
    account_value: float = Query(default=0.0, description="账户资金，用于空仓仓位测算"),
    risk_pct: float = Query(default=0.01, description="单笔最大风险比例"),
    atr: float = Query(default=0.0, description="ATR，用于空仓止损合理性校验"),
    include_structure: bool = Query(default=False, description="调试用：返回完整多级别结构大对象"),
    profile: str = Query(default="auto", description="结构计算档位: auto=必要时升级, fast=day/30/5, full=六级别"),
):
    """获取 Radar contract 形态的单票缠论分析。"""
    symbol_bs = _normalize_symbol(symbol)
    user_id = _query_optional_int(user_id)
    cost = _query_float(cost, 0.0)
    qty = _query_int(qty, 0)
    account_value = _query_float(account_value, 0.0)
    risk_pct = _query_float(risk_pct, 0.01)
    atr = _query_float(atr, 0.0)
    include_structure = _query_bool(include_structure, False)
    requested_profile = _query_profile(profile, "auto")
    resolved_profile = _adapter_profile_for_request(requested_profile)
    upgrade_reason = None
    holding = _load_holding_from_position(user_id, symbol_bs)
    if holding is None and cost > 0 and qty > 0:
        holding = {"cost": cost, "qty": qty, "strategy_type": "未知", "entry_thesis": {}}
    mode = _mode_from_holding(holding)

    adapter_result = {}
    try:
        adapter_result = await _load_cached_adapter_structure_for_profile(symbol_bs, profile=resolved_profile)
    except Exception as exc:
        logger.error("Radar chan_adapter failed: symbol=%s error=%s", symbol_bs, exc, exc_info=True)
        adapter_result = _adapter_exception_result(symbol_bs, str(exc))

    if not _adapter_structure_ready(adapter_result):
        return _adapter_error_response(symbol_bs, mode, user_id, adapter_result)

    matrix_data = {}
    structure = _build_structure_from_adapter(adapter_result)
    freshness = _build_freshness_from_adapter(adapter_result)
    data_source = _build_data_source_from_adapter(adapter_result)
    legacy_refs = _build_adapter_refs()
    levels = _decision_levels_from_adapter_only(adapter_result)

    strategy, entry_plan, holding_plan, plans = build_radar_decision(
        matrix_data,
        levels,
        holding,
        DISCLAIMER,
        account_value=account_value,
        risk_pct=risk_pct,
        atr=atr,
    )
    deduction = build_level_chain_deduction(
        levels,
        freshness=freshness,
        mode=mode,
        disclaimer=DISCLAIMER,
    )
    algorithm_v2 = build_radar_algorithm_v2(
        levels,
        freshness=freshness,
        disclaimer=DISCLAIMER,
    )
    quote = await _load_realtime_quote(symbol_bs)
    algorithm_v2 = _apply_intraday_quote_overlay(algorithm_v2, quote)
    position_context = build_position_context(
        holding,
        algorithm_v2,
        account_value=account_value,
        quote=quote,
    )
    coach_action = build_coach_action(position_context, algorithm_v2, disclaimer=DISCLAIMER)
    signals_v2 = build_signal_v2(
        algorithm_v2,
        symbol=symbol_bs,
        quote=quote,
        position_context=position_context,
        disclaimer=DISCLAIMER,
    )

    if requested_profile == "auto" and resolved_profile == "fast":
        upgrade_reason = _auto_profile_upgrade_reason(
            algorithm_v2=algorithm_v2,
            freshness=freshness,
            coach_action=coach_action,
        )
        if upgrade_reason:
            try:
                full_adapter_result = await _load_cached_adapter_structure_for_profile(symbol_bs, profile="full")
            except Exception as exc:
                logger.warning(
                    "Radar auto full upgrade failed: symbol=%s reason=%s error=%s",
                    symbol_bs,
                    upgrade_reason,
                    exc,
                    exc_info=True,
                )
                upgrade_reason = f"{upgrade_reason}_FULL_FAILED"
            else:
                if _adapter_structure_ready(full_adapter_result):
                    resolved_profile = "full"
                    adapter_result = full_adapter_result
                    structure = _build_structure_from_adapter(adapter_result)
                    freshness = _build_freshness_from_adapter(adapter_result)
                    data_source = _build_data_source_from_adapter(adapter_result)
                    levels = _decision_levels_from_adapter_only(adapter_result)
                    strategy, entry_plan, holding_plan, plans = build_radar_decision(
                        matrix_data,
                        levels,
                        holding,
                        DISCLAIMER,
                        account_value=account_value,
                        risk_pct=risk_pct,
                        atr=atr,
                    )
                    deduction = build_level_chain_deduction(
                        levels,
                        freshness=freshness,
                        mode=mode,
                        disclaimer=DISCLAIMER,
                    )
                    algorithm_v2 = build_radar_algorithm_v2(
                        levels,
                        freshness=freshness,
                        disclaimer=DISCLAIMER,
                    )
                    algorithm_v2 = _apply_intraday_quote_overlay(algorithm_v2, quote)
                    position_context = build_position_context(
                        holding,
                        algorithm_v2,
                        account_value=account_value,
                        quote=quote,
                    )
                    coach_action = build_coach_action(position_context, algorithm_v2, disclaimer=DISCLAIMER)
                    signals_v2 = build_signal_v2(
                        algorithm_v2,
                        symbol=symbol_bs,
                        quote=quote,
                        position_context=position_context,
                        disclaimer=DISCLAIMER,
                    )

    data = {
        "api_version": RADAR_API_VERSION,
        "symbol": symbol_bs,
        "mode": mode,
        "user_id": user_id,
        "as_of": _now_iso(),
        "data_source": data_source,
        "quote": quote,
        "structure_config": _structure_config_from_adapter(adapter_result),
        "structure_kernel": adapter_result.get("structure_kernel") or {},
        "freshness": freshness,
        "strategy": strategy,
        "entry_plan": entry_plan,
        "holding_plan": holding_plan,
        "position_context": position_context,
        "coach_action": coach_action,
        "deduction": deduction,
        "algorithm_v2": algorithm_v2 if include_structure else _compact_algorithm_for_display(algorithm_v2),
        "signals_v2": signals_v2,
        "plans": plans,
        "alerts": [],
        "narrative": None,
        "legacy_refs": legacy_refs,
        "diagnostics": {
            "requested_profile": requested_profile,
            "resolved_profile": resolved_profile,
            "upgrade_reason": upgrade_reason,
            "structure_profile": resolved_profile,
            "structure_levels": list((adapter_result.get("levels") or {}).keys()),
            "structure_ms": adapter_result.get("_structure_ms"),
            "structure_cache_hit": bool(adapter_result.get("_cache_hit")),
            "structure_persistent_cache_hit": bool(adapter_result.get("_persistent_cache_hit")),
            "structure_fingerprint": ((adapter_result.get("structure_kernel") or {}).get("structure_fingerprint")),
        },
        "disclaimer": DISCLAIMER,
    }
    if include_structure:
        data["structure"] = structure

    return {
        "status": "success",
        "data": data,
    }


def _load_watchlist_symbols(user_id: int) -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT wi.symbol
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             WHERE wg.user_id = ?
             ORDER BY wi.symbol
            """,
            (user_id,),
        ).fetchall()
        return [_normalize_symbol(row["symbol"]) for row in rows]
    finally:
        conn.close()


def _symbol_data_health(symbol: str) -> dict:
    conn = get_lake_connection("baostock")
    level_rows = {}
    for level in HEALTH_LEVELS:
        row = conn.execute(
            """
            SELECT MAX(date) AS last_bar_at, COUNT(*) AS row_count
              FROM klines
             WHERE symbol = ? AND freq = ? AND adjustflag = '2'
            """,
            (symbol, level),
        ).fetchone()
        level_rows[level] = {
            "last_bar_at": row["last_bar_at"] or "",
            "row_count": int(row["row_count"] or 0),
        }

    latest_day = max((_date_part(item["last_bar_at"]) for item in level_rows.values()), default="")
    levels = {}
    reasons = []
    for level, item in level_rows.items():
        lag_days = _lag_days(_date_part(item["last_bar_at"]), latest_day)
        is_missing = item["row_count"] == 0
        is_lagging = lag_days > HEALTH_MAX_LAG_DAYS
        reason = "NO_DATA" if is_missing else "LEVEL_STALE" if is_lagging else ""
        levels[level] = {
            **item,
            "lag_days": lag_days,
            "is_stale": bool(reason),
            "stale_reason": reason,
        }
        if reason:
            reasons.append(f"{level}:{reason}")

    return {
        "symbol": symbol,
        "last_bar_at": max((item["last_bar_at"] for item in level_rows.values()), default=""),
        "is_stale": bool(reasons),
        "stale_reason": ", ".join(reasons),
        "levels": levels,
    }


def _date_part(value: str) -> str:
    return str(value or "").split(" ", 1)[0]


def _lag_days(value: str, latest_value: str) -> int:
    if not value or not latest_value:
        return 0
    try:
        current = dt.datetime.strptime(value[:10], "%Y-%m-%d")
        latest = dt.datetime.strptime(latest_value[:10], "%Y-%m-%d")
    except ValueError:
        return 0
    return max(0, (latest - current).days)

def _adapter_exception_result(symbol: str, message: str) -> dict:
    return {
        "symbol": symbol,
        "freshness": {
            "source": "baostock",
            "adjustflag": "2",
            "checked_at": _now_iso(),
            "is_stale": True,
            "stale_reason": "ENGINE_ERROR",
        },
        "error": {
            "code": "ENGINE_ERROR",
            "message": message,
        },
    }


def _adapter_error_response(symbol: str, mode: str, user_id: Optional[int], adapter_result: dict) -> dict:
    error = adapter_result.get("error") or {}
    freshness = _build_freshness_from_adapter(adapter_result)
    freshness["is_stale"] = True
    freshness["stale_reason"] = error.get("code") or freshness.get("stale_reason") or "ENGINE_ERROR"
    deduction = build_level_chain_deduction(
        {},
        freshness=freshness,
        mode=mode,
        disclaimer=DISCLAIMER,
    )
    algorithm_v2 = build_radar_algorithm_v2(
        {},
        freshness=freshness,
        disclaimer=DISCLAIMER,
    )
    return {
        "status": "error",
        "data": {
            "api_version": RADAR_API_VERSION,
            "symbol": symbol,
            "mode": mode,
            "user_id": user_id,
            "error": {
                "code": error.get("code", "ENGINE_ERROR"),
                "message": error.get("message", "structure adapter failed"),
                "fallback_used": False,
            },
            "freshness": freshness,
            "deduction": deduction,
            "algorithm_v2": algorithm_v2,
            "disclaimer": DISCLAIMER,
        },
    }
