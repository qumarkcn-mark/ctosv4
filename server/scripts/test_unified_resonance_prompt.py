"""A/B test unified reasoning with resonance evidence and minimal prompt framing.

Usage:
    cd /Users/markqu/Desktop/ct-os-v4
    TEST_SYMBOLS=sz.002138,sh.600790 ./venv/bin/python -m server.scripts.test_unified_resonance_prompt

The script does not change production prompts or snapshots. It loads persisted
CZSC snapshots, builds the current enhanced payload, adds lightweight resonance
evidence and any available selected chan signals, then compares LLM outputs.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from server.domain.symbols import normalize_symbol, symbol_aliases
from server.engines.ai_native.unified_reasoning_service import (
    _collect_chan_signals,
    _compute_resonance_evidence,
)
from server.scripts.test_unified_enhanced_payload import (
    DB_PATH,
    LEVEL_NAMES,
    ROOT,
    _build_enhanced_payload,
    _load_snapshots,
    _num,
    _symbols_from_env,
    _user_deepseek_api_key,
)


OUTPUT_PATH = ROOT / "data" / "test_unified_resonance_prompt_results.json"
DEFAULT_SYMBOLS = ["sz.002138", "sh.600790", "sz.000938"]

BASE_PROMPT = """你是用户的缠论盯盘搭档。

基于输入数据做第二阶段综合推演，说清当前走势在做什么、关键变化在哪里、接下来盯什么。

仅供参考，不构成投资建议。"""

RESONANCE_PROMPT = """你是用户的缠论盯盘搭档。

基于输入数据做第二阶段综合推演，说清当前走势在做什么、关键变化在哪里、接下来盯什么。

如果输入包含 resonance_evidence 或 chan_signals，把它们作为证据，不是交易指令；若它们与结构、动力、压力支撑或持仓状态冲突，说明冲突点。

仅供参考，不构成投资建议。"""


def main() -> None:
    load_dotenv(ROOT / ".env")
    if not DB_PATH.exists():
        raise SystemExit(f"数据库不存在: {DB_PATH}")

    api_key = os.environ.get("LLM_API_KEY", "") or _user_deepseek_api_key(user_id=1)
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("AI_NATIVE_MODEL", "deepseek-v4-pro")
    symbols = [normalize_symbol(item) for item in (_symbols_from_env() or DEFAULT_SYMBOLS)]

    print(f"DB: {DB_PATH}")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"LLM: {'enabled' if api_key else 'disabled'}")
    print(f"Output: {OUTPUT_PATH}")

    results: list[dict[str, Any]] = []
    for symbol in symbols:
        snapshots = _load_snapshots(symbol)
        if not snapshots:
            print(f"\n--- {symbol} ---\n无四级别 snapshot，跳过")
            continue

        base_payload = _build_enhanced_payload(symbol, snapshots)
        base_payload["position_context"] = _position_context(symbol, base_payload.get("current_price"))
        resonance_payload = _build_resonance_payload(base_payload, snapshots)
        record: dict[str, Any] = {
            "symbol": symbol,
            "base_payload": base_payload,
            "resonance_payload": resonance_payload,
            "llm_enabled": bool(api_key),
        }

        print(f"\n--- {symbol} ---")
        _print_test_summary(resonance_payload)

        if api_key:
            record["base_output"] = _call_llm(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=BASE_PROMPT,
                payload=base_payload,
            )
            time.sleep(2)
            record["resonance_output"] = _call_llm(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=RESONANCE_PROMPT,
                payload=resonance_payload,
            )
            print("\n[Base]\n" + record["base_output"][:2200])
            print("\n[Resonance]\n" + record["resonance_output"][:2200])
            time.sleep(2)

        results.append(record)

    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUTPUT_PATH}")


def _build_resonance_payload(base_payload: dict[str, Any], snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = json.loads(json.dumps(base_payload, ensure_ascii=False))
    pressure_support = payload.get("nearby_pressure_support") or []
    payload["resonance_evidence"] = _compute_resonance_evidence(
        current_price=_num(payload.get("current_price")),
        structure_geometry=payload.get("structure_geometry") or {},
        pressure_support=pressure_support,
    )
    payload["chan_signals"] = _collect_chan_signals(snapshots, LEVEL_NAMES)
    return payload


def _position_context(symbol: str, current_price: Any) -> dict[str, Any]:
    aliases = symbol_aliases(symbol)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"""
            SELECT quantity, avg_cost, current_price
              FROM positions
             WHERE user_id = 1 AND symbol IN ({",".join("?" for _ in aliases)})
             ORDER BY updated_at DESC LIMIT 1
            """,
            aliases,
        ).fetchone()
    finally:
        conn.close()
    if row and _num(row["quantity"]) > 0:
        price = _num(current_price) or _num(row["current_price"])
        cost = _num(row["avg_cost"])
        return {
            "holding": True,
            "shares": _num(row["quantity"]),
            "cost": cost,
            "current_pnl_pct": round((price - cost) / cost * 100, 2) if price > 0 and cost > 0 else None,
            "source": "database",
        }
    return {"holding": False, "shares": 0, "cost": 0, "source": "database"}


def _call_llm(*, api_key: str, base_url: str, model: str, system_prompt: str, payload: dict[str, Any]) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        "temperature": 0.2,
        "max_tokens": 3500,
    }
    with httpx.Client(timeout=150) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
    return str(data["choices"][0]["message"]["content"]).strip()


def _print_test_summary(payload: dict[str, Any]) -> None:
    print(f"current_price: {payload.get('current_price')}")
    print(f"position: {payload.get('position_context')}")
    print(f"chan_signals levels: {list((payload.get('chan_signals') or {}).keys())}")
    print("resonance_evidence:")
    print(json.dumps(payload.get("resonance_evidence") or {}, ensure_ascii=False, indent=2)[:1600])


if __name__ == "__main__":
    main()
