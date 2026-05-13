from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import structure as structure_api


def make_client():
    app = FastAPI()
    app.include_router(structure_api.router, prefix="/api/structure")
    return TestClient(app)


def test_structure_engine_endpoint_routes_dual(monkeypatch):
    calls = {}

    async def fake_analyze(**kwargs):
        calls.update(kwargs)
        return {
            "engine": "chan_py",
            "engine_mode": "dual",
            "symbol": kwargs["symbol"],
            "levels": {},
            "shadow_structure": {"engine": "czsc", "levels": {}},
        }

    monkeypatch.setattr(structure_api, "analyze_structure_with_engine", fake_analyze)
    client = make_client()

    res = client.get(
        "/api/structure/engine/sh600519",
        params={"levels": "day,30", "structure_engine": "dual", "count": 120},
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "success"
    assert payload["data"]["engine_mode"] == "dual"
    assert calls["symbol"] == "sh.600519"
    assert calls["levels"] == ["day", "30"]
    assert calls["structure_engine"] == "dual"
    assert calls["count"] == 120


def test_structure_engine_endpoint_rejects_bad_engine():
    client = make_client()

    res = client.get("/api/structure/engine/sh600519", params={"structure_engine": "nope"})

    assert res.status_code == 400
    assert "unsupported structure engine" in res.json()["detail"]


def test_structure_engine_endpoint_rejects_bad_level():
    client = make_client()

    res = client.get("/api/structure/engine/sh600519", params={"levels": "day,2m"})

    assert res.status_code == 400
    assert "unsupported levels" in res.json()["detail"]
