"""Stable keys for shared Chan structure snapshots.

This module deliberately does not call chan.py. It only defines the versioned
identity of a structure result so display preferences do not trigger recompute.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from server.domain.symbols import normalize_symbol


ENGINE_NAME = "chan.py"
ENGINE_VERSION = "vendor_chan_py_current"
ADAPTER_VERSION = "chan_adapter.v1"
SNAPSHOT_SCHEMA_VERSION = "chan_detail_snapshot:v2"
FORMAL_SOURCE = "baostock"
FORMAL_ADJUSTFLAG = "2"
FORMAL_SOURCE_ROLE = "formal_structure"


COMPUTE_PROFILES: dict[str, dict[str, int]] = {
    "chart_standard_v1": {
        "week": 1200,
        "day": 1200,
        "60": 1200,
        "30": 1200,
        "15": 1200,
        "5": 1200,
    },
    "radar_tactical_v1": {
        "week": 0,
        "day": 2500,
        "60": 3000,
        "30": 3000,
        "15": 2500,
        "5": 2000,
    },
    "deep_audit_v1": {
        "week": 1200,
        "day": 2500,
        "60": 3000,
        "30": 3000,
        "15": 2500,
        "5": 2000,
    },
}


FREQ_ALIASES = {
    "w": "week",
    "week": "week",
    "d": "day",
    "day": "day",
    "m60": "60",
    "60m": "60",
    "60": "60",
    "m30": "30",
    "30m": "30",
    "30": "30",
    "m15": "15",
    "15m": "15",
    "15": "15",
    "m5": "5",
    "5m": "5",
    "5": "5",
}


@dataclass(frozen=True)
class StructureKey:
    symbol: str
    freq: str
    source: str
    source_role: str
    adjustflag: str
    cchan_preset: str
    compute_profile: str
    engine: str
    engine_version: str
    adapter_version: str
    schema_version: str
    data_signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "freq": self.freq,
            "source": self.source,
            "source_role": self.source_role,
            "adjustflag": self.adjustflag,
            "cchan_preset": self.cchan_preset,
            "compute_profile": self.compute_profile,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "adapter_version": self.adapter_version,
            "schema_version": self.schema_version,
            "data_signature": self.data_signature,
        }

    @property
    def hash(self) -> str:
        return stable_hash(self.to_dict())


def normalize_freq(freq: str) -> str:
    key = str(freq or "day").strip().lower()
    if key not in FREQ_ALIASES:
        raise ValueError(f"unsupported structure freq: {freq}")
    return FREQ_ALIASES[key]


def resolve_compute_bars(compute_profile: str, freq: str, fallback: int = 500) -> int:
    profile = COMPUTE_PROFILES.get(compute_profile)
    if not profile:
        raise ValueError(f"unsupported compute profile: {compute_profile}")
    normalized = normalize_freq(freq)
    bars = int(profile.get(normalized, 0) or 0)
    return bars if bars > 0 else int(fallback)


def build_structure_key(
    *,
    symbol: str,
    freq: str,
    data_signature: str,
    cchan_preset: str = "live_tolerant",
    compute_profile: str = "chart_standard_v1",
    source: str = FORMAL_SOURCE,
    source_role: str = FORMAL_SOURCE_ROLE,
    adjustflag: str = FORMAL_ADJUSTFLAG,
    engine_version: str = ENGINE_VERSION,
    adapter_version: str = ADAPTER_VERSION,
    schema_version: str = SNAPSHOT_SCHEMA_VERSION,
) -> StructureKey:
    if compute_profile not in COMPUTE_PROFILES:
        raise ValueError(f"unsupported compute profile: {compute_profile}")
    return StructureKey(
        symbol=normalize_symbol(symbol),
        freq=normalize_freq(freq),
        source=source,
        source_role=source_role,
        adjustflag=adjustflag,
        cchan_preset=cchan_preset or "live_tolerant",
        compute_profile=compute_profile,
        engine=ENGINE_NAME,
        engine_version=engine_version,
        adapter_version=adapter_version,
        schema_version=schema_version,
        data_signature=data_signature or "",
    )


def stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
