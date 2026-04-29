"""Radar API compatibility shell.

第一版 Radar 先包住现有 matrix 引擎，输出新 contract 形状。
后续再把 structure/decision 拆到独立 engines。
"""

import asyncio
import copy
import datetime as dt
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
from server.engines.structure.chan_adapter import analyze_structure
from server.services.price_service import get_current_price

router = APIRouter()
logger = logging.getLogger(__name__)

DISCLAIMER = "仅供参考，不构成投资建议"
RADAR_API_VERSION = "radar.v1"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
STRUCTURE_CACHE_TTL_SECONDS = 120
STRUCTURE_CACHE_MAX_ITEMS = 64

_structure_cache: dict[tuple, dict] = {}
_structure_cache_locks: dict[tuple, asyncio.Lock] = {}
HEALTH_LEVELS = ("day", "60", "30", "15", "5")
HEALTH_MAX_LAG_DAYS = 7


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
            "historical_high": _historical_high_from_klines(data.get("klines", [])),
        }

    merged = dict(legacy_levels)
    merged.update(result)
    return merged


def _historical_high_from_klines(klines: list[dict]) -> dict:
    best = {}
    for kline in klines or []:
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
    return {
        "price": best.get("high"),
        "time": best.get("time") or best.get("date") or "",
    }


def _decision_levels_from_adapter_only(adapter_result: dict) -> dict:
    return _decision_levels_from_adapter(adapter_result, {})


def _legacy_level_key(public_level: str) -> str:
    if public_level in ("day", "week"):
        return public_level
    return f"m{public_level}"


def _build_data_source_from_adapter(adapter_result: dict) -> dict:
    structure = (adapter_result.get("data_source") or {}).get("structure") or {}
    return {
        "structure": {
            "provider": structure.get("provider", "baostock"),
            "adjustflag": structure.get("adjustflag", "2"),
            "levels": ["week", "day", "60", "30", "15", "5"],
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
    """Load current Tencent quote for display/coaching only.

    结构推演仍使用 CChan K 线切片；实时价只进入持仓盈亏和教练提示。
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


async def _load_adapter_structure(symbol: str) -> dict:
    """Load formal structure through chan_adapter.

    Kept as a small wrapper so tests and later feature flags can isolate this
    migration point.
    """
    return await analyze_structure(
        symbol,
        levels=["week", "day", "60", "30", "15", "5"],
        count=800,
    )


def _structure_cache_key(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 800,
    cchan_preset: str = "live_tolerant",
) -> tuple:
    return (
        symbol,
        tuple(levels or ["week", "day", "60", "30", "15", "5"]),
        count,
        cchan_preset,
    )


def _clear_structure_cache() -> None:
    """测试和运维用：清空 Radar 结构短缓存。"""
    _structure_cache.clear()
    _structure_cache_locks.clear()


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
    key = _structure_cache_key(symbol)
    now = time.monotonic()
    cached = _structure_cache.get(key)
    if cached and now - cached["cached_at"] < STRUCTURE_CACHE_TTL_SECONDS:
        logger.info("Radar structure cache hit: symbol=%s", symbol)
        return copy.deepcopy(cached["result"])

    lock = _structure_cache_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _structure_cache.get(key)
        if cached and now - cached["cached_at"] < STRUCTURE_CACHE_TTL_SECONDS:
            logger.info("Radar structure cache hit-after-wait: symbol=%s", symbol)
            return copy.deepcopy(cached["result"])

        result = await _load_adapter_structure(symbol)
        _structure_cache[key] = {
            "cached_at": time.monotonic(),
            "result": copy.deepcopy(result),
        }
        _trim_structure_cache(time.monotonic())
        logger.info("Radar structure cache miss: symbol=%s", symbol)
        return result


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
):
    """获取 Radar contract 形态的单票缠论分析。"""
    symbol_bs = _normalize_symbol(symbol)
    user_id = _query_optional_int(user_id)
    account_value = _query_float(account_value, 0.0)
    risk_pct = _query_float(risk_pct, 0.01)
    atr = _query_float(atr, 0.0)
    include_structure = _query_bool(include_structure, False)
    holding = _load_holding_from_position(user_id, symbol_bs)
    if holding is None and cost > 0 and qty > 0:
        holding = {"cost": cost, "qty": qty, "strategy_type": "未知", "entry_thesis": {}}
    mode = _mode_from_holding(holding)

    adapter_result = {}
    try:
        adapter_result = await _load_cached_adapter_structure(symbol_bs)
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
    position_context = build_position_context(
        holding,
        algorithm_v2,
        account_value=account_value,
        quote=quote,
    )
    coach_action = build_coach_action(position_context, algorithm_v2, disclaimer=DISCLAIMER)

    data = {
        "api_version": RADAR_API_VERSION,
        "symbol": symbol_bs,
        "mode": mode,
        "user_id": user_id,
        "as_of": _now_iso(),
        "data_source": data_source,
        "quote": quote,
        "structure_config": _structure_config_from_adapter(adapter_result),
        "freshness": freshness,
        "strategy": strategy,
        "entry_plan": entry_plan,
        "holding_plan": holding_plan,
        "position_context": position_context,
        "coach_action": coach_action,
        "deduction": deduction,
        "algorithm_v2": algorithm_v2 if include_structure else _compact_algorithm_for_display(algorithm_v2),
        "plans": plans,
        "alerts": [],
        "narrative": None,
        "legacy_refs": legacy_refs,
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
