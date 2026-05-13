"""Feature cache for historical paper replay.

模拟盘回放会在同一个 as_of 上反复构造多级别结构。这里先做一个小而明确
的 cache 边界：调用方只关心 IntradayTFeatures，cache 负责避免重复计算。
后续如果要做 SQLite/文件持久化，可以沿用同一个接口。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Hashable

from server.engines.decision.intraday_t_features import IntradayTFeatures


FeatureFactory = Callable[[], Awaitable[IntradayTFeatures]]
FEATURE_CACHE_VERSION = "intraday_t_features:v2"


@dataclass
class ReplayFeatureCache:
    """In-memory cache keyed by replay feature identity."""

    values: dict[tuple[Hashable, ...], IntradayTFeatures] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    async def get_or_build(self, key: tuple[Hashable, ...], factory: FeatureFactory) -> IntradayTFeatures:
        if key in self.values:
            self.hits += 1
            return self.values[key]
        self.misses += 1
        features = await factory()
        self.values[key] = features
        return features

    @property
    def size(self) -> int:
        return len(self.values)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": self.size}


def replay_feature_cache_key(
    *,
    symbol: str,
    as_of: str,
    level_chain: dict[str, str],
    count: int,
    engine_preset: str,
    detail_source: str | None = None,
) -> tuple[Hashable, ...]:
    chain_key = tuple(sorted(level_chain.items()))
    return (symbol, as_of, chain_key, count, engine_preset, detail_source or "")


@dataclass
class SQLiteReplayFeatureCache(ReplayFeatureCache):
    """SQLite-backed feature cache with in-memory read-through."""

    conn: sqlite3.Connection | None = None
    disk_hits: int = 0
    writes: int = 0

    async def get_or_build(self, key: tuple[Hashable, ...], factory: FeatureFactory) -> IntradayTFeatures:
        if key in self.values:
            self.hits += 1
            return self.values[key]

        cache_key = _cache_key_digest(key)
        cached = self._load(cache_key)
        if cached is not None:
            self.disk_hits += 1
            self.hits += 1
            self.values[key] = cached
            return cached

        self.misses += 1
        features = await factory()
        self.values[key] = features
        self._save(cache_key, key, features)
        return features

    def stats(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "size": self.size,
            "disk_hits": self.disk_hits,
            "writes": self.writes,
        }

    def _load(self, cache_key: str) -> IntradayTFeatures | None:
        if self.conn is None:
            return None
        row = self.conn.execute(
            """
            SELECT features_json
              FROM paper_feature_cache
             WHERE cache_key = ?
               AND cache_version = ?
            """,
            (cache_key, FEATURE_CACHE_VERSION),
        ).fetchone()
        if row is None:
            return None
        return _features_from_json(row["features_json"])

    def _save(self, cache_key: str, key: tuple[Hashable, ...], features: IntradayTFeatures) -> None:
        if self.conn is None:
            return
        symbol, as_of, chain_key, count, engine_preset, *_rest = key
        self.conn.execute(
            """
            INSERT INTO paper_feature_cache (
                cache_key, cache_version, symbol, as_of, level_chain_json,
                count, engine_preset, features_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(cache_key) DO UPDATE SET
                cache_version=excluded.cache_version,
                level_chain_json=excluded.level_chain_json,
                count=excluded.count,
                engine_preset=excluded.engine_preset,
                features_json=excluded.features_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                cache_key,
                FEATURE_CACHE_VERSION,
                str(symbol),
                str(as_of),
                _json(dict(chain_key)),  # type: ignore[arg-type]
                int(count),
                str(engine_preset),
                _json(asdict(features)),
            ),
        )
        self.conn.commit()
        self.writes += 1


def _cache_key_digest(key: tuple[Hashable, ...]) -> str:
    payload = _json(key)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _features_from_json(value: str) -> IntradayTFeatures | None:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return IntradayTFeatures(
        symbol=str(payload.get("symbol") or ""),
        as_of=str(payload.get("as_of") or ""),
        level_chain=dict(payload.get("level_chain") or {}),
        paths=dict(payload.get("paths") or {}),
        pattern_tags=list(payload.get("pattern_tags") or []),
        position_to_center=dict(payload.get("position_to_center") or {}),
        latest_event=dict(payload.get("latest_event") or {}),
        divergence=dict(payload.get("divergence") or {}),
        momentum=dict(payload.get("momentum") or {}),
        volatility=dict(payload.get("volatility") or {}),
        freshness=dict(payload.get("freshness") or {}),
        parent_context=dict(payload.get("parent_context") or {}),
        current_price=float(payload.get("current_price") or 0.0),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
