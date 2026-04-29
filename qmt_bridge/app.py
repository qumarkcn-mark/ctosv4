"""Standalone read-only QMT bridge service."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from qmt_bridge.provider import MarketDataProvider, provider_from_env


class SubscribeRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=lambda: ["5m"])


def create_app(provider: MarketDataProvider | None = None) -> FastAPI:
    data_provider = provider or provider_from_env()
    app = FastAPI(
        title="CT-OS QMT Bridge",
        description="Read-only QMT/XtQuant market data bridge for CT-OS Radar.",
        version="0.1.0",
    )

    @app.get("/health")
    def health():
        return data_provider.health()

    @app.get("/symbols")
    def symbols():
        return {"status": "ok", "symbols": []}

    @app.post("/subscribe")
    def subscribe(request: SubscribeRequest):
        try:
            return data_provider.subscribe(request.symbols, request.periods)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/quotes")
    def quotes(symbols: str = Query(..., description="Comma-separated CT-OS symbols")):
        symbol_list = [item.strip() for item in symbols.split(",") if item.strip()]
        try:
            return {"status": "ok", "quotes": data_provider.quotes(symbol_list)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/klines")
    def klines(symbol: str, period: str = "5m", limit: int = 240):
        try:
            rows = data_provider.klines(symbol, period, limit)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "status": "ok",
            "symbol": rows[-1]["symbol"] if rows else symbol,
            "period": period,
            "count": len(rows),
            "klines": rows,
        }

    @app.get("/klines/latest")
    def latest_kline(symbol: str, period: str = "5m"):
        try:
            rows = data_provider.klines(symbol, period, 1)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "status": "ok",
            "symbol": rows[-1]["symbol"] if rows else symbol,
            "period": period,
            "kline": rows[-1] if rows else None,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("qmt_bridge.app:app", host="127.0.0.1", port=8765, reload=False)

