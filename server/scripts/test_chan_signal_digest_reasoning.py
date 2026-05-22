"""A/B test second-stage reasoning with and without chan_signal_digest.

Usage:
    cd /Users/markqu/Desktop/ct-os-v4
    TEST_SYMBOLS=sh.603986,sz.300394,sh.688008,sh.600790 ./venv/bin/python -m server.scripts.test_chan_signal_digest_reasoning
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from server import config
from server.db.database import get_connection
from server.engines.ai_native.unified_reasoning_service import SYSTEM_PROMPT, build_unified_reasoning_input
from server.services.llm_service import AIModelRoute, LLMService


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "data" / "test_chan_signal_digest_reasoning_results.json"
DEFAULT_SYMBOLS = ["sh.603986", "sz.300394", "sh.688008", "sh.600790"]


async def main() -> None:
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("LLM_API_KEY", "") or _user_deepseek_api_key(user_id=1)
    if not api_key:
        raise SystemExit("LLM_API_KEY 未设置，且用户设置里没有 deepseek_api_key")
    os.environ["LLM_API_KEY"] = api_key

    symbols = _symbols_from_env() or DEFAULT_SYMBOLS
    service = LLMService()
    route = AIModelRoute(
        thinking_enabled=getattr(config, "AI_NATIVE_THINKING_ENABLED", True),
        reasoning_effort=getattr(config, "AI_NATIVE_REASONING_EFFORT", "high"),
        timeout_seconds=max(float(getattr(config, "AI_NATIVE_LLM_TIMEOUT", 150)), 150),
        max_tokens=max(int(getattr(config, "AI_NATIVE_MAX_TOKENS", 4096)), 4096),
    )
    records = []
    for symbol in symbols:
        payload = build_unified_reasoning_input(user_id=1, symbol=symbol, levels=["week", "day", "30", "5"])["input"]
        without_digest = _drop_keys(payload, {"chan_signal_digest"})
        with_digest = payload
        print(f"\n--- {symbol} ---")
        print("digest_summary:", json.dumps((with_digest.get("chan_signal_digest") or {}).get("summary") or [], ensure_ascii=False)[:1200])
        before = await _call_reasoning(service, route, symbol=symbol, payload=without_digest, label="A 无 chan_signal_digest")
        print("\n[A 无 digest]\n" + before[:1800])
        after = await _call_reasoning(service, route, symbol=symbol, payload=with_digest, label="B 有 chan_signal_digest")
        print("\n[B 有 digest]\n" + after[:1800])
        records.append(
            {
                "symbol": symbol,
                "data_as_of": payload.get("data_as_of"),
                "digest_summary": (with_digest.get("chan_signal_digest") or {}).get("summary") or [],
                "without_digest_output": before,
                "with_digest_output": after,
            }
        )
        await asyncio.sleep(2)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUTPUT_PATH}")


async def _call_reasoning(
    service: LLMService,
    route: AIModelRoute,
    *,
    symbol: str,
    payload: dict[str, Any],
    label: str,
) -> str:
    user_message = (
        f"以下是 {symbol} 的第二阶段输入数据（{label}），请给出综合推演；"
        "重点说明当前走势、级别冲突、关键价格、下一笔最需要验证的地方：\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    text = await service.infer_ai_native_markdown(
        SYSTEM_PROMPT,
        user_message,
        user_id=1,
        model_route=route,
    )
    return str(text or "").strip()


def _drop_keys(payload: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in keys}


def _symbols_from_env() -> list[str]:
    raw = os.environ.get("TEST_SYMBOLS", "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else []


def _user_deepseek_api_key(user_id: int) -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT settings_json FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row or not row["settings_json"]:
            return ""
        settings = json.loads(row["settings_json"] or "{}")
        return str(settings.get("deepseek_api_key") or "")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
