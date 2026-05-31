"""Check whether watchboard, K-line, and chat payload use the same intraday facts.

Usage:
    ./venv/bin/python -m server.scripts.check_intraday_data_consistency
    ./venv/bin/python -m server.scripts.check_intraday_data_consistency --symbol sh688008 --symbol sz300327
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from typing import Any

from server.api.ai_structure import _load_watchboard_groups, _price_for_symbol
from server.api.data import query_klines
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.intraday_snapshot_hydrator import hydrate_intraday_snapshot
from server.scripts.test_intraday_chat_context import _build_item
from server.services.price_service import get_batch_prices


DEFAULT_QUESTION = "现在这个位置是反抽还是买点转化？"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check intraday data consistency across UI/AI layers.")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--symbol", action="append", default=[], help="Symbol, e.g. sh688008 or 300327.SZ")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--count", type=int, default=240)
    args = parser.parse_args()

    result = asyncio.run(
        check_consistency(
            user_id=args.user_id,
            symbols=[_normalize_symbol_arg(item) for item in args.symbol],
            question=args.question,
            count=args.count,
        )
    )
    print(_format_report(result))


async def check_consistency(*, user_id: int, symbols: list[str], question: str, count: int) -> dict[str, Any]:
    watch_items = _watchboard_items(user_id)
    selected_symbols = symbols or [item["symbol"] for item in watch_items]
    selected_symbols = list(dict.fromkeys(selected_symbols))
    prices = await get_batch_prices(selected_symbols)
    items_by_symbol = {item["symbol"]: item for item in watch_items}
    rows = []
    for symbol in selected_symbols:
        item = _with_price(items_by_symbol.get(symbol) or {"symbol": symbol}, prices)
        rows.append(await _check_symbol(user_id=user_id, symbol=symbol, item=item, question=question, count=count))
    return {
        "version": "intraday_data_consistency.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id,
        "symbols": rows,
    }


async def _check_symbol(*, user_id: int, symbol: str, item: dict[str, Any], question: str, count: int) -> dict[str, Any]:
    watch_price = _num(item.get("price"))
    watch_time = ((item.get("price_data") or {}).get("quote_time") or "")
    kline = await _kline_summary(symbol, count=count)
    chat = _chat_summary(user_id=user_id, symbol=symbol, question=question, mock_price=watch_price or None)
    snapshot = hydrate_intraday_snapshot(symbol, recent_bar_count=20)
    prices = {
        "watchboard": watch_price,
        "kline_m1": _num(kline.get("last_close")),
        "chat_live": _num(((chat.get("live_tape") or {}).get("price"))),
        "postmarket_1m": _num(((chat.get("postmarket_1m") or {}).get("close"))),
    }
    return {
        "symbol": symbol,
        "name": item.get("name") or "",
        "status": _status(prices),
        "watchboard": {
            "price": watch_price,
            "quote_time": watch_time,
            "freshness": (item.get("reasoning_freshness") or {}).get("status") or "",
        },
        "kline_m1": kline,
        "chat": chat,
        "intraday_snapshot": {
            "available": bool(snapshot.get("available")),
            "date": snapshot.get("date") or "",
            "coverage": snapshot.get("coverage") or {},
            "close": ((snapshot.get("price") or {}).get("close")),
        },
    }


async def _kline_summary(symbol: str, *, count: int) -> dict[str, Any]:
    try:
        payload = await query_klines(symbol, interval="m1", count=max(10, count))
    except Exception as exc:
        return {"available": False, "error": str(exc)[:200]}
    rows = payload.get("klines") or []
    last = rows[-1] if rows else {}
    return {
        "available": bool(rows),
        "count": len(rows),
        "last_at": last.get("date") or "",
        "last_close": last.get("close"),
        "last_source": last.get("source") or "",
        "last_status": last.get("bar_status") or "",
    }


def _chat_summary(*, user_id: int, symbol: str, question: str, mock_price: float | None) -> dict[str, Any]:
    item = _build_item(user_id=user_id, symbol=symbol, questions=[question], mock_price=mock_price)
    if item.get("error"):
        return {"available": False, "error": item["error"]}
    answer = (item.get("questions") or [{}])[0]
    ctx = answer.get("chat_context") or {}
    live = ctx.get("live_tape") or {}
    post = ctx.get("postmarket_1m_snapshot") or {}
    return {
        "available": True,
        "context_id": item.get("context_id") or "",
        "live_tape": {
            "price": live.get("price"),
            "as_of": live.get("as_of") or "",
            "coverage": (live.get("coverage") or {}).get("quality") or "",
            "price_source": live.get("price_source") or "",
        },
        "postmarket_1m": {
            "available": bool(post.get("available")),
            "date": post.get("date") or "",
            "coverage": (post.get("coverage") or {}).get("quality") or "",
            "close": ((post.get("price") or {}).get("close")),
        },
    }


def _watchboard_items(user_id: int) -> list[dict[str, Any]]:
    groups = _load_watchboard_groups(user_id)
    return [item for group in groups for item in group.get("items") or []]


def _with_price(item: dict[str, Any], prices: dict[str, dict]) -> dict[str, Any]:
    result = dict(item)
    price = _price_for_symbol(prices, result["symbol"])
    if price:
        result["price"] = price.get("price") or result.get("price") or 0
        result["change_pct"] = price.get("change_pct") or result.get("change_pct") or 0
        result["price_data"] = price
        result["name"] = result.get("name") or price.get("name") or result["symbol"]
    return result


def _status(prices: dict[str, float]) -> str:
    available = {key: value for key, value in prices.items() if value > 0}
    if len(available) < 2:
        return "MISSING"
    reference = available.get("watchboard") or next(iter(available.values()))
    mismatches = [
        key
        for key, value in available.items()
        if reference > 0 and abs(value - reference) / reference > 0.002
    ]
    return "MISMATCH" if mismatches else "OK"


def _format_report(payload: dict[str, Any]) -> str:
    lines = [
        f"version: {payload.get('version')}",
        f"generated_at: {payload.get('generated_at')}",
        f"user_id: {payload.get('user_id')}",
    ]
    for item in payload.get("symbols") or []:
        watch = item.get("watchboard") or {}
        kline = item.get("kline_m1") or {}
        chat = item.get("chat") or {}
        live = chat.get("live_tape") or {}
        post = chat.get("postmarket_1m") or {}
        lines.append(
            "\n"
            f"{item.get('status')} {item.get('symbol')} {item.get('name') or ''}\n"
            f"  watchboard: price={watch.get('price')} time={watch.get('quote_time') or '-'} freshness={watch.get('freshness') or '-'}\n"
            f"  kline m1:   price={kline.get('last_close')} at={kline.get('last_at') or '-'} "
            f"source={kline.get('last_source') or '-'} status={kline.get('last_status') or '-'} count={kline.get('count') or 0}\n"
            f"  chat live:  price={live.get('price')} as_of={live.get('as_of') or '-'} "
            f"coverage={live.get('coverage') or '-'} source={live.get('price_source') or '-'}\n"
            f"  chat 1m:    close={post.get('close')} date={post.get('date') or '-'} coverage={post.get('coverage') or '-'}"
        )
        if kline.get("error"):
            lines.append(f"  kline error: {kline.get('error')}")
        if chat.get("error"):
            lines.append(f"  chat error: {chat.get('error')}")
    return "\n".join(lines)


def _normalize_symbol_arg(symbol: str) -> str:
    value = str(symbol or "").strip()
    if "." in value and value.upper().endswith((".SH", ".SZ", ".BJ")):
        code, market = value.split(".", 1)
        return normalize_symbol(f"{market.lower()}{code}")
    return normalize_symbol(value)


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
