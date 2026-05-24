"""Dry-run the intraday chat context payload without calling the LLM.

Usage:
    ./venv/bin/python -m server.scripts.test_intraday_chat_context \
      --symbol sh600790 --symbol sh688008 --symbol sz300394 \
      --question "现在算突破吗？" \
      --mock-price sh688008=278.88
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from server.domain.symbols import normalize_symbol
from server.engines.ai_native.reasoning_continuity_service import build_reasoning_continuity_context
from server.engines.ai_native.structure_chat_service import (
    _build_chat_context_pack,
    _chat_current_price,
    _chat_intraday_observation,
    _chat_runtime_context,
    _context_data_status,
    classify_intent,
)
from server.engines.ai_native.structure_context_service import get_latest_ai_structure_context
from server.engines.ai_native.structure_evidence_service import chart_focus_for_intent
from server.engines.ai_native.unified_reasoning_service import ALL_UNIFIED_FULL_TEXT_VERSIONS
from server.services.intraday_observation_service import get_intraday_observation_snapshot


DEFAULT_SYMBOLS = ["sh600790", "sh688008", "sz300394"]
DEFAULT_QUESTIONS = [
    "现在算突破吗？",
    "5分钟MACD这样怎么看？",
    "这里是不是回踩不破？",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect intraday chat_context payloads without LLM calls.")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--question", action="append", default=[])
    parser.add_argument("--summary", action="store_true", help="Print a compact table-like summary instead of full JSON.")
    parser.add_argument(
        "--mock-price",
        action="append",
        default=[],
        help="Override quote price for a symbol, e.g. sh688008=278.88. Can repeat.",
    )
    args = parser.parse_args()

    symbols = args.symbol or DEFAULT_SYMBOLS
    questions = args.question or DEFAULT_QUESTIONS
    mock_prices = _parse_mock_prices(args.mock_price)
    payload = {
        "version": "intraday_chat_context_dry_run.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "This dry-run does not call the LLM. It shows the factual payload used by short chat answers.",
        "items": [
            _build_item(
                user_id=args.user_id,
                symbol=symbol,
                questions=questions,
                mock_price=mock_prices.get(_normalize_symbol_arg(symbol)),
            )
            for symbol in symbols
        ],
    }
    if args.summary:
        print(_format_summary(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _build_item(*, user_id: int, symbol: str, questions: list[str], mock_price: float | None) -> dict[str, Any]:
    canonical = _normalize_symbol_arg(symbol)
    context = get_latest_ai_structure_context(user_id=user_id, symbol=canonical)
    if not context:
        return {"symbol": canonical, "error": "NO_AI_STRUCTURE_CONTEXT"}

    intraday = _mock_intraday_observation(canonical, mock_price) if mock_price else _chat_intraday_observation(canonical)
    data_status = _context_data_status(user_id=user_id, symbol=canonical, context=context)
    answers = []
    for question in questions:
        conversation_context = {
            "version": "ai_structure_conversation_context.v1",
            "turn_count": 0,
            "recent_turns": [],
        }
        intent = classify_intent(question, conversation_context=conversation_context)
        chart_focus = chart_focus_for_intent(context, intent)
        runtime_context = _chat_runtime_context(context=context, data_status=data_status, chart_focus=chart_focus)
        current_price = _chat_current_price(runtime_context, intraday)
        runtime_context["chat_current_price"] = current_price
        runtime_context["chat_current_price_source"] = (
            "mock_intraday_quote"
            if mock_price
            else ("intraday_quote" if _num((intraday.get("quote") or {}).get("price")) > 0 else runtime_context.get("price_source"))
        )
        continuity = build_reasoning_continuity_context(
            user_id=user_id,
            symbol=canonical,
            current_price=current_price,
            intraday_observation=intraday,
            prompt_versions=ALL_UNIFIED_FULL_TEXT_VERSIONS,
        )
        chat_context = _build_chat_context_pack(
            question=question,
            intent_type=intent,
            intraday_observation=intraday,
            reasoning_continuity_context=continuity,
            conversation_context=conversation_context,
            runtime_context=runtime_context,
        )
        answers.append(
            {
                "question": question,
                "intent_type": intent,
                "focus_level": chart_focus.get("level") or "",
                "chat_context": chat_context,
                "trigger_status_since_last_run": continuity.get("trigger_status_since_last_run") or [],
            }
        )

    return {
        "symbol": canonical,
        "context_id": context.get("context_id") or "",
        "prompt_version": context.get("prompt_version") or "",
        "main_level": context.get("main_level") or "",
        "trigger_level": context.get("trigger_level") or "",
        "intraday_summary": _intraday_summary(intraday),
        "questions": answers,
    }


def _mock_intraday_observation(symbol: str, price: float) -> dict[str, Any]:
    quote = {
        "price": price,
        "trade_datetime": "2026-05-22 14:30:00",
        "quote_time": "14:30:00",
        "source": "mock_intraday_quote",
    }
    return get_intraday_observation_snapshot(symbol, quote=quote)


def _intraday_summary(payload: dict[str, Any]) -> dict[str, Any]:
    levels = payload.get("levels") or {}
    return {
        "as_of": payload.get("as_of") or "",
        "coverage": payload.get("coverage") or {},
        "quote": payload.get("quote") or {},
        "levels": {
            key: {
                "last_bar_at": value.get("last_bar_at") or "",
                "last_bar_status": value.get("last_bar_status") or "",
                "last_close": value.get("last_close"),
                "closed_macd": (value.get("macd_closed_only") or {}).get("macd_momentum"),
                "forming_macd": (value.get("macd_with_forming") or {}).get("macd_momentum"),
            }
            for key, value in levels.items()
            if key in {"1m", "5m", "30m"} and isinstance(value, dict)
        },
    }


def _parse_mock_prices(values: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"invalid --mock-price value: {item!r}; expected SYMBOL=PRICE")
        symbol, price = item.split("=", 1)
        result[_normalize_symbol_arg(symbol)] = float(price)
    return result


def _normalize_symbol_arg(symbol: str) -> str:
    value = str(symbol or "").strip()
    if "." in value and value.upper().endswith((".SH", ".SZ", ".BJ")):
        code, market = value.split(".", 1)
        return normalize_symbol(f"{market.lower()}{code}")
    return normalize_symbol(value)


def _format_summary(payload: dict[str, Any]) -> str:
    lines = [
        f"version: {payload.get('version')}",
        f"generated_at: {payload.get('generated_at')}",
    ]
    for item in payload.get("items") or []:
        if item.get("error"):
            lines.append(f"\n{item.get('symbol')}: {item.get('error')}")
            continue
        quote = (item.get("intraday_summary") or {}).get("quote") or {}
        coverage = (item.get("intraday_summary") or {}).get("coverage") or {}
        lines.append(
            "\n"
            f"{item.get('symbol')} | context={item.get('prompt_version')} | "
            f"price={quote.get('price')} | coverage={coverage.get('quality')}"
        )
        for answer in item.get("questions") or []:
            ctx = answer.get("chat_context") or {}
            live_tape = ctx.get("live_tape") or {}
            trigger_state = ctx.get("trigger_state") or {}
            crossed = trigger_state.get("crossed") or []
            nearest = trigger_state.get("nearest") or []
            lines.append(
                f"  Q: {answer.get('question')} | intent={answer.get('intent_type')} | "
                f"focus={answer.get('focus_level')} | tape_price={live_tape.get('price')}"
            )
            if crossed:
                for trigger in crossed:
                    lines.append(
                        "    crossed: "
                        f"{trigger.get('type')} {trigger.get('level')} "
                        f"dist={trigger.get('distance_pct')}% "
                        f"{trigger.get('message_on_trigger') or ''}"
                    )
            elif nearest:
                top = nearest[0]
                lines.append(
                    "    nearest: "
                    f"{top.get('type')} {top.get('level')} "
                    f"dist={top.get('distance_pct')}% "
                    f"{top.get('message_on_trigger') or ''}"
                )
            else:
                lines.append("    trigger: none")
    return "\n".join(lines)


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
