"""股票搜索服务。

供前端搜索和截图导入共用，避免名称匹配逻辑分叉。
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://smartbox.gtimg.cn/s3/?v=2&t=all&c=1&q={query}"


async def search_stocks(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """搜索 A 股/港股股票和指数。"""
    q = (query or "").strip()
    if not q:
        return []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_SEARCH_URL.format(query=q))
            resp.raise_for_status()
            text = resp.text
            try:
                text = text.encode("utf-8").decode("unicode_escape")
            except Exception:
                pass
    except Exception as exc:
        logger.warning("股票搜索失败: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    if 'v_hint="' not in text:
        return results
    data_str = text.split('v_hint="', 1)[1].rstrip('";\n')
    for item in data_str.split("^"):
        if not item.strip():
            continue
        parts = item.split("~")
        if len(parts) < 5:
            continue
        market, code, name, _, stock_type = parts[:5]
        if market not in ("sh", "sz", "hk"):
            continue
        if not stock_type.startswith(("GP", "ZS")):
            continue
        market_label = {"sh": "沪", "sz": "深", "hk": "港"}[market]
        results.append({
            "symbol": f"{market}{code}",
            "name": name,
            "market": market_label,
            "type": "GP" if stock_type.startswith("GP") else "ZS",
        })
        if len(results) >= limit:
            break
    return results


async def match_stock_name(name: str) -> dict[str, Any]:
    """按截图中的股票名称匹配代码；只有唯一精确匹配才自动通过。"""
    candidates = await search_stocks(name, limit=20)
    exact = [item for item in candidates if item.get("name") == name]
    if len(exact) == 1:
        return {
            "status": "MATCHED",
            "symbol": exact[0]["symbol"],
            "candidates": candidates,
        }
    return {
        "status": "BLOCKED",
        "symbol": None,
        "candidates": candidates,
    }
