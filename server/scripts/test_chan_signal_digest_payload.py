"""Offline comparison for practical CZSC signal digest.

Usage:
    cd /Users/markqu/Desktop/ct-os-v4
    TEST_SYMBOLS=sh.603986,sz.300394 ./venv/bin/python -m server.scripts.test_chan_signal_digest_payload
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from server.engines.ai_native.chan_signal_digest import build_chan_signal_digest
from server.engines.ai_native.czsc_snapshot_service import DEFAULT_COMPUTE_PROFILE, DEFAULT_LEVELS
from server.engines.ai_native.unified_reasoning_service import build_unified_reasoning_input
from server.engines.structure.canonical_structure_service import get_latest_structure
from server.engines.structure.structure_key import normalize_freq


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "data" / "test_chan_signal_digest_payload_results.json"
LEVEL_NAMES = {"week": "周线", "day": "日线", "30": "30分钟", "5": "5分钟"}
DEFAULT_SYMBOLS = ["sh.603986", "sz.300394", "sh.688008", "sh.600790"]


def main() -> None:
    records = []
    for symbol in _symbols_from_env() or DEFAULT_SYMBOLS:
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
        digest = build_chan_signal_digest(snapshots, level_names=LEVEL_NAMES)
        record = {
            "symbol": symbol,
            "data_as_of": current.get("data_as_of"),
            "current_chan_signals": current.get("chan_signals") or {},
            "chan_signal_digest": digest,
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
    snapshots = {}
    for level in DEFAULT_LEVELS:
        normalized = normalize_freq(level)
        row = get_latest_structure(symbol=symbol, level=normalized, min_profile=DEFAULT_COMPUTE_PROFILE)
        if row:
            snapshots[normalized] = row
    return snapshots


def _print_record(record: dict[str, Any]) -> None:
    print(f"\n--- {record['symbol']} data_as_of={record.get('data_as_of')} ---")
    print("current_chan_signal_levels:", list((record.get("current_chan_signals") or {}).keys()))
    digest = record["chan_signal_digest"]
    for level, categories in (digest.get("by_level") or {}).items():
        parts = []
        for category, items in categories.items():
            values = " | ".join(item["value"] for item in items[:2])
            parts.append(f"{category}: {values}")
        print(f"{level}: " + " ; ".join(parts))
    print("summary:", digest.get("summary") or [])


if __name__ == "__main__":
    main()
