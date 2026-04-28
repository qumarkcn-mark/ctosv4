"""Client for the read-only Windows QMT bridge."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from qmt_bridge.symbols import to_ctos_freq
from server.config import QMT_BRIDGE_TIMEOUT, QMT_BRIDGE_URL
from server.db.kline_lake import upsert_klines
from server.domain.symbols import normalize_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QmtBridgeClient:
    """Small HTTP client for qmt_bridge.

    The bridge is allowed to fail. Radar and data APIs should return a clear
    unavailable state instead of blocking official BaoStock/Tencent flows.
    """

    base_url: str = QMT_BRIDGE_URL
    timeout: float = QMT_BRIDGE_TIMEOUT

    async def health(self) -> dict:
        return await self._get_json("/health")

    async def stream_probe(self, symbol: str, period: str = "tick", seconds: float = 3.0) -> dict:
        """Probe SSE stream compatibility without waiting for live ticks forever."""
        qmt_code = _to_qmt_gateway_code(symbol)
        url = f"{self.base_url.rstrip('/')}/stream"
        events = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(seconds, connect=self.timeout)) as client:
            async with client.stream("GET", url, params={"codes": qmt_code, "period": period}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        events.append(line.removeprefix("data: "))
                    if len(events) >= 3:
                        break
        return {
            "status": "ok",
            "symbol": normalize_symbol(symbol),
            "qmt_code": qmt_code,
            "period": period,
            "events": events,
        }

    async def get_quotes(self, symbols: list[str]) -> list[dict]:
        payload = await self._get_json("/quotes", params={"symbols": ",".join(symbols)})
        return payload.get("quotes") or []

    async def get_klines(self, symbol: str, period: str = "5m", limit: int = 240) -> list[dict]:
        payload = await self._get_json(
            "/klines",
            params={"symbol": normalize_symbol(symbol), "period": period, "limit": limit},
        )
        return payload.get("klines") or []

    async def _get_json(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()


async def qmt_health(client: Optional[QmtBridgeClient] = None) -> dict:
    """Return bridge health with a stable unavailable shape on failure."""
    bridge = client or QmtBridgeClient()
    try:
        payload = await bridge.health()
        return _normalize_health_payload(payload)
    except Exception as exc:
        logger.info("QMT bridge unavailable: %s", exc)
        return {
            "available": False,
            "status": "unavailable",
            "provider": "qmt_bridge",
            "error": str(exc),
        }


async def qmt_stream_probe(
    symbol: str,
    period: str = "tick",
    client: Optional[QmtBridgeClient] = None,
) -> dict:
    """Probe Windows SSE gateway. This is diagnostic, not a structure source."""
    bridge = client or QmtBridgeClient()
    return await bridge.stream_probe(symbol, period=period)


async def fetch_qmt_klines(
    symbol: str,
    period: str = "5m",
    limit: int = 240,
    cache_closed: bool = True,
    client: Optional[QmtBridgeClient] = None,
) -> dict:
    """Fetch QMT klines and optionally cache only CLOSED bars into qmt_lake."""
    canonical = normalize_symbol(symbol)
    bridge = client or QmtBridgeClient()
    rows = await bridge.get_klines(canonical, period=period, limit=limit)
    closed_rows = [row for row in rows if row.get("bar_status") == "CLOSED"]
    written = 0

    if cache_closed and closed_rows:
        written = upsert_klines(
            canonical,
            to_ctos_freq(period),
            closed_rows,
            adjustflag="3",
            source="qmt",
        )

    return {
        "status": "ok",
        "symbol": canonical,
        "period": period,
        "count": len(rows),
        "closed_count": len(closed_rows),
        "cached_count": written,
        "source": "qmt_bridge",
        "klines": rows,
    }


def _normalize_health_payload(payload: dict) -> dict:
    if "available" in payload:
        return payload
    if "ok" in payload:
        return {
            "available": bool(payload.get("ok")),
            "status": "ok" if payload.get("ok") else "unavailable",
            "provider": "qmt_sse_gateway",
            **payload,
        }
    return {"available": True, **payload}


def _to_qmt_gateway_code(symbol: str) -> str:
    parsed = normalize_symbol(symbol)
    market, code = parsed.split(".", 1)
    return f"{code}.{market.upper()}"
