"""Radar API compatibility shell.

第一版 Radar 先包住现有 matrix 引擎，输出新 contract 形状。
后续再把 structure/decision 拆到独立 engines。
"""

import datetime as dt
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from server.domain.symbols import normalize_symbol
from server.engines.decision.radar_planner import build_radar_decision
from server.engines.structure.chan_adapter import analyze_structure

router = APIRouter()
logger = logging.getLogger(__name__)

DISCLAIMER = "仅供参考，不构成投资建议"
RADAR_API_VERSION = "radar.v1"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


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


def _mode_from_holding(holding: Optional[dict]) -> str:
    return "HOLDING" if holding else "EMPTY"


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
        }

    merged = dict(legacy_levels)
    merged.update(result)
    return merged


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


@router.get("/{symbol}")
async def get_radar(
    symbol: str,
    user_id: Optional[int] = Query(default=None, description="用户ID，第一版仅预留"),
    cost: float = Query(default=0.0, description="持仓成本价，0=空仓"),
    qty: int = Query(default=0, description="持仓数量，0=空仓"),
    account_value: float = Query(default=0.0, description="账户资金，用于空仓仓位测算"),
    risk_pct: float = Query(default=0.01, description="单笔最大风险比例"),
    atr: float = Query(default=0.0, description="ATR，用于空仓止损合理性校验"),
):
    """获取 Radar contract 形态的单票缠论分析。"""
    symbol_bs = _normalize_symbol(symbol)
    account_value = _query_float(account_value, 0.0)
    risk_pct = _query_float(risk_pct, 0.01)
    atr = _query_float(atr, 0.0)
    holding = {"cost": cost, "qty": qty} if cost > 0 and qty > 0 else None
    mode = _mode_from_holding(holding)

    adapter_result = {}
    try:
        adapter_result = await _load_adapter_structure(symbol_bs)
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

    return {
        "status": "success",
        "data": {
            "api_version": RADAR_API_VERSION,
            "symbol": symbol_bs,
            "mode": mode,
            "user_id": user_id,
            "as_of": _now_iso(),
            "data_source": data_source,
            "freshness": freshness,
            "structure": structure,
            "strategy": strategy,
            "entry_plan": entry_plan,
            "holding_plan": holding_plan,
            "plans": plans,
            "alerts": [],
            "narrative": None,
            "legacy_refs": legacy_refs,
            "disclaimer": DISCLAIMER,
        },
    }

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
            "disclaimer": DISCLAIMER,
        },
    }
