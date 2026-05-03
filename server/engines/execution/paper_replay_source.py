"""Historical data source for paper replay.

The source builds features as-of each replay bar.  It is deliberately separate
from `paper_replay.py`: replay executes strategy state, this module prepares the
time-sliced market/structure inputs.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from server.db.kline_lake import query_klines
from server.engines.decision.intraday_t_features import (
    IntradayTFeatures,
    extract_intraday_t_features,
)
from server.engines.decision.radar_algorithm_v2 import build_radar_algorithm_v2
from server.engines.execution.paper_feature_cache import (
    ReplayFeatureCache,
    replay_feature_cache_key,
)
from server.engines.execution.paper_models import PaperKline
from server.engines.execution.paper_replay import ReplayStep
from server.engines.structure.derived_facts import enrich_level
from server.services.chan_detail_service import get_chan_detail


DetailLoader = Callable[..., Awaitable[dict[str, Any]]]
KlineLoader = Callable[..., list[dict[str, Any]]]


DEFAULT_INTRADAY_T_LEVEL_CHAIN = {"L0": "30", "L1": "5", "L2": "1"}
TDX_1M_REPLAY_SOURCE = "tdx_1m_replay"


async def build_replay_step_from_history(
    *,
    symbol: str,
    as_of: str,
    next_bar: dict[str, Any] | PaperKline | None = None,
    level_chain: dict[str, str] | None = None,
    count: int = 500,
    cchan_preset: str = "live_tolerant",
    detail_loader: DetailLoader = get_chan_detail,
    feature_cache: ReplayFeatureCache | None = None,
    detail_source: str | None = None,
) -> ReplayStep:
    """Build one ReplayStep without letting future bars into feature extraction."""
    features = await build_intraday_t_features_from_history(
        symbol=symbol,
        as_of=as_of,
        level_chain=level_chain or DEFAULT_INTRADAY_T_LEVEL_CHAIN,
        count=count,
        cchan_preset=cchan_preset,
        detail_loader=detail_loader,
        feature_cache=feature_cache,
        detail_source=detail_source,
    )
    return ReplayStep(features=features, next_bar=next_bar)


async def build_intraday_t_features_from_history(
    *,
    symbol: str,
    as_of: str,
    level_chain: dict[str, str] | None = None,
    count: int = 500,
    cchan_preset: str = "live_tolerant",
    detail_loader: DetailLoader = get_chan_detail,
    feature_cache: ReplayFeatureCache | None = None,
    detail_source: str | None = None,
) -> IntradayTFeatures:
    """Build Radar-derived features for one historical timestamp.

    The critical invariant: every structure request receives `end_date=as_of`.
    The caller may pass a future `next_bar` to the replay step, but it is not
    available here.
    """
    chain = level_chain or DEFAULT_INTRADAY_T_LEVEL_CHAIN
    if feature_cache is not None:
        key = replay_feature_cache_key(
            symbol=symbol,
            as_of=as_of,
            level_chain=chain,
            count=count,
            cchan_preset=cchan_preset,
            detail_source=detail_source,
        )
        return await feature_cache.get_or_build(
            key,
            lambda: _build_intraday_t_features_uncached(
                symbol=symbol,
                as_of=as_of,
                level_chain=chain,
                count=count,
                cchan_preset=cchan_preset,
                detail_loader=detail_loader,
                detail_source=detail_source,
            ),
        )
    return await _build_intraday_t_features_uncached(
        symbol=symbol,
        as_of=as_of,
        level_chain=chain,
        count=count,
        cchan_preset=cchan_preset,
        detail_loader=detail_loader,
        detail_source=detail_source,
    )


async def _build_intraday_t_features_uncached(
    *,
    symbol: str,
    as_of: str,
    level_chain: dict[str, str],
    count: int,
    cchan_preset: str,
    detail_loader: DetailLoader,
    detail_source: str | None = None,
) -> IntradayTFeatures:
    chain = level_chain
    levels: dict[str, dict] = {}
    freshness_levels: dict[str, dict] = {}

    public_levels = _ordered_public_levels(chain)
    detail_results = await asyncio.gather(
        *[
            _load_detail_for_level(
                symbol=symbol,
                public_level=public_level,
                count=count,
                as_of=as_of,
                cchan_preset=cchan_preset,
                detail_loader=detail_loader,
                detail_source=detail_source,
            )
            for public_level in public_levels
        ]
    )
    for public_level, detail in zip(public_levels, detail_results):
        level_data = _level_from_detail(detail, public_level)
        levels[_legacy_level_key(public_level)] = level_data
        freshness_levels[public_level] = {
            "last_bar_at": _last_bar_at(level_data),
            "is_stale": bool(detail.get("error")),
            "stale_reason": str(detail.get("error") or ""),
        }

    freshness = {
        "source": "baostock",
        "adjustflag": "2",
        "checked_at": as_of,
        "last_bar_at": max((item.get("last_bar_at") or "" for item in freshness_levels.values()), default=""),
        "is_stale": any(item.get("is_stale") for item in freshness_levels.values()),
        "stale_reason": ", ".join(item["stale_reason"] for item in freshness_levels.values() if item.get("stale_reason")),
        "levels": freshness_levels,
    }
    radar = build_radar_algorithm_v2(levels, freshness=freshness, level_chain=chain)
    trigger_level = chain.get("L2", "1")
    trigger_data = levels.get(_legacy_level_key(trigger_level), {})
    return extract_intraday_t_features(
        radar,
        symbol=symbol,
        as_of=as_of,
        trigger_klines=trigger_data.get("klines") or [],
    )


async def _load_detail_for_level(
    *,
    symbol: str,
    public_level: str,
    count: int,
    as_of: str,
    cchan_preset: str,
    detail_loader: DetailLoader,
    detail_source: str | None = None,
) -> dict[str, Any]:
    loader_kwargs = _detail_loader_kwargs(
        public_level=public_level,
        count=count,
        as_of=as_of,
        cchan_preset=cchan_preset,
        detail_source=detail_source,
    )
    return await detail_loader(symbol, public_level, **loader_kwargs)


async def build_replay_steps_from_klines(
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    trigger_freq: str = "1",
    level_chain: dict[str, str] | None = None,
    limit: int = 240,
    detail_loader: DetailLoader = get_chan_detail,
    kline_loader: KlineLoader = query_klines,
    feature_cache: ReplayFeatureCache | None = None,
    detail_source: str | None = None,
) -> list[ReplayStep]:
    """Build replay steps for one symbol/date range from historical K-lines."""
    bars = kline_loader(symbol, trigger_freq, start_date=start_date, end_date=end_date, limit=limit)
    steps: list[ReplayStep] = []
    for idx, bar in enumerate(bars[:-1]):
        as_of = str(bar.get("date") or bar.get("time") or "")
        if not as_of:
            continue
        steps.append(
            await build_replay_step_from_history(
                symbol=symbol,
                as_of=as_of,
                next_bar=bars[idx + 1],
                level_chain=level_chain,
                detail_loader=detail_loader,
                feature_cache=feature_cache,
                detail_source=detail_source,
            )
        )
    return steps


def _detail_loader_kwargs(
    *,
    public_level: str,
    count: int,
    as_of: str,
    cchan_preset: str,
    detail_source: str | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "count": count,
        "end_date": as_of,
        "cchan_preset": cchan_preset,
        "max_compute_bars": count,
    }
    if detail_source == TDX_1M_REPLAY_SOURCE and public_level == "1":
        kwargs["kline_source"] = "qmt"
        kwargs["adjustflag"] = "3"
    return kwargs


def next_bar_after(
    symbol: str,
    *,
    as_of: str,
    freq: str = "1",
    kline_loader: KlineLoader = query_klines,
) -> PaperKline | None:
    rows = kline_loader(symbol, freq, start_date=as_of, limit=5)
    for row in rows:
        row_time = str(row.get("date") or row.get("time") or "")
        if row_time > as_of:
            return PaperKline.from_dict(row)
    return None


def _level_from_detail(detail: dict[str, Any], public_level: str) -> dict[str, Any]:
    if detail.get("error"):
        return {"level": _legacy_level_key(public_level), "error": detail.get("error")}

    level_data = {
        **detail,
        "level": public_level,
        "price": _last_price(detail),
        "source": {
            "provider": "baostock",
            "adjustflag": "2",
            "engine": "chan.py",
            "adapter": "server.services.chan_detail_service",
        },
    }
    try:
        return enrich_level(level_data)
    except Exception:
        return level_data


def _ordered_public_levels(level_chain: dict[str, str]) -> list[str]:
    ordered = []
    for role in ("L0", "L1", "L2"):
        level = level_chain.get(role)
        if level and level not in ordered:
            ordered.append(level)
    return ordered


def _legacy_level_key(public_level: str) -> str:
    if public_level in ("day", "week"):
        return public_level
    return f"m{public_level}"


def _last_price(detail: dict[str, Any]) -> float:
    klines = detail.get("klines") or []
    if not klines:
        return 0.0
    try:
        return float(klines[-1].get("close") or 0)
    except (TypeError, ValueError):
        return 0.0


def _last_bar_at(level_data: dict[str, Any]) -> str:
    klines = level_data.get("klines") or []
    if not klines:
        return ""
    return str(klines[-1].get("time") or klines[-1].get("date") or "")
