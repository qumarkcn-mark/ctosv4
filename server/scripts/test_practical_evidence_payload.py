"""Offline comparison for first-batch practical evidence.

Usage:
    cd /Users/markqu/Desktop/ct-os-v4
    TEST_SYMBOLS=sh.603986,sz.300394 ./venv/bin/python -m server.scripts.test_practical_evidence_payload

The script does not call the LLM. It compares the current second-stage payload
with the proposed practical_evidence block so we can inspect value before
feeding it into production reasoning.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from server.engines.ai_native.czsc_snapshot_service import DEFAULT_COMPUTE_PROFILE, DEFAULT_LEVELS
from server.engines.ai_native.practical_evidence_hydrator import hydrate_practical_evidence
from server.engines.ai_native.unified_reasoning_service import (
    _add_pressure_support_semantics,
    _compute_pressure_support,
    _hydrate_structure_geometry,
    build_unified_reasoning_input,
)
from server.engines.structure.canonical_structure_service import get_latest_structure
from server.engines.structure.structure_key import normalize_freq


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "data" / "test_practical_evidence_payload_results.json"
LEVEL_NAMES = {"week": "周线", "day": "日线", "30": "30分钟", "5": "5分钟"}
DEFAULT_SYMBOLS = ["sh.603986", "sz.300394", "sh.688008", "sh.600790"]


def main() -> None:
    symbols = _symbols_from_env() or DEFAULT_SYMBOLS
    records = []
    for symbol in symbols:
        snapshots = _load_snapshots(symbol)
        if not snapshots:
            print(f"\n--- {symbol} ---\n无 snapshot，跳过")
            continue
        current = build_unified_reasoning_input(
            user_id=1,
            symbol=symbol,
            levels=list(snapshots.keys()),
            compute_profile=DEFAULT_COMPUTE_PROFILE,
        )["input"]
        practical = _build_practical_evidence(snapshots)
        record = {
            "symbol": symbol,
            "data_as_of": current.get("data_as_of"),
            "before_keys": sorted(k for k in current.keys() if k not in {"structure", "pressure_support", "my_position"}),
            "practical_evidence": practical,
            "compact_comparison": _compact_comparison(current, practical),
        }
        records.append(record)
        _print_record(record)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUTPUT_PATH}")


def _symbols_from_env() -> list[str]:
    raw = os.environ.get("TEST_SYMBOLS", "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else []


def _load_snapshots(symbol: str) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for level in DEFAULT_LEVELS:
        normalized = normalize_freq(level)
        row = get_latest_structure(symbol=symbol, level=normalized, min_profile=DEFAULT_COMPUTE_PROFILE)
        if row:
            snapshots[normalized] = row
    return snapshots


def _build_practical_evidence(snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    geometry = {
        LEVEL_NAMES.get(level, level): _hydrate_structure_geometry(row)
        for level, row in snapshots.items()
    }
    pressure_support = _add_pressure_support_semantics(_compute_pressure_support(snapshots), geometry)
    return hydrate_practical_evidence(
        snapshots,
        pressure_support=pressure_support,
        level_names=LEVEL_NAMES,
    )


def _compact_comparison(current: dict[str, Any], practical: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_has": {
            "structure_geometry": bool(current.get("structure_geometry")),
            "momentum_dynamics": bool(current.get("momentum_dynamics")),
            "nearby_pressure_support": len(current.get("nearby_pressure_support") or []),
            "chan_signals_levels": list((current.get("chan_signals") or {}).keys()),
        },
        "new_adds": {
            "levels": list((practical.get("by_level") or {}).keys()),
            "fields": ["bi_completion", "divergence_evidence", "fx_quality", "level_interaction"],
        },
    }


def _print_record(record: dict[str, Any]) -> None:
    print(f"\n--- {record['symbol']} data_as_of={record.get('data_as_of')} ---")
    print("before_keys:", ", ".join(record["before_keys"]))
    practical = record["practical_evidence"]
    for level, item in (practical.get("by_level") or {}).items():
        bi = item.get("bi_completion") or {}
        div = item.get("divergence_evidence") or {}
        fx = item.get("fx_quality") or {}
        print(
            f"{level}: bi={bi.get('direction')}/{bi.get('completion_hint') or bi.get('status')} "
            f"div={div.get('hint') or div.get('status')} fx={fx.get('value') or fx.get('status')}"
        )
    interaction = practical.get("level_interaction") or {}
    print("nearest_pressure:", interaction.get("nearest_pressure"))
    print("nearest_support:", interaction.get("nearest_support"))


if __name__ == "__main__":
    main()
