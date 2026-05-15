from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import ai_structure
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native.structure_view_service import get_structure_view


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(ai_structure.router, prefix="/api/ai-structure")
    return TestClient(app)


def save_sample_snapshot():
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-structure-view",
        data_as_of="2026-05-14",
        snapshot_payload={
            "level": "day",
            "price": 10.8,
            "klines": [
                {"time": "2026-05-10", "open": 10, "high": 11, "low": 9, "close": 10.5},
                {"time": "2026-05-11", "open": 10.5, "high": 12, "low": 10, "close": 11.5},
                {"time": "2026-05-12", "open": 11.5, "high": 12, "low": 10.6, "close": 10.8},
            ],
            "bis": [
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-10",
                    "x1": "2026-05-11",
                    "y0": 10.0,
                    "y1": 11.5,
                    "bar_count": 2,
                },
                {
                    "direction": "down",
                    "is_up": False,
                    "is_sure": True,
                    "x0": "2026-05-11",
                    "x1": "2026-05-12",
                    "y0": 11.5,
                    "y1": 10.8,
                    "bar_count": 2,
                },
            ],
            "bi_zhongshus": [
                {
                    "begin_date": "2026-05-10",
                    "end_date": "2026-05-12",
                    "zd": 10.2,
                    "zg": 10.9,
                    "zz": 10.55,
                    "bi_count": 3,
                    "is_valid": True,
                }
            ],
            "active_zhongshu": {
                "begin_date": "2026-05-10",
                "end_date": "2026-05-12",
                "zd": 10.2,
                "zg": 10.9,
                "zz": 10.55,
                "bi_count": 3,
                "is_valid": True,
            },
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )


def test_structure_view_service_returns_chart_ready_geometry(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_sample_snapshot()

    view = get_structure_view(symbol="sh600519", level="day")

    assert view["symbol"] == "sh.600519"
    assert view["version"] == "structure_view.v1"
    assert view["bar_axis"]["count"] == 3
    assert view["bis"][0]["start_index"] == 0
    assert view["bis"][0]["end_index"] == 1
    assert view["centers"][0]["active"] is True
    assert view["active_center"]["begin_index"] == 0
    assert view["active_center"]["end_index"] == 2
    assert view["active_center"]["raw_begin_index"] == 0
    assert view["active_center"]["raw_end_index"] == 2


def test_structure_view_center_uses_entry_and_exit_bar_boundaries(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-center-entry-exit",
        data_as_of="2026-05-15",
        snapshot_payload={
            "level": "day",
            "price": 11.2,
            "klines": [
                {"time": "2026-05-10", "open": 9.7, "high": 10.1, "low": 9.6, "close": 10.0},
                {"time": "2026-05-11", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.3},
                {"time": "2026-05-12", "open": 10.3, "high": 10.8, "low": 10.2, "close": 10.6},
                {"time": "2026-05-13", "open": 10.6, "high": 10.9, "low": 10.4, "close": 10.7},
                {"time": "2026-05-14", "open": 10.7, "high": 11.3, "low": 10.6, "close": 11.2},
                {"time": "2026-05-15", "open": 11.2, "high": 11.5, "low": 11.0, "close": 11.4},
            ],
            "bis": [
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-10",
                    "x1": "2026-05-14",
                    "y0": 10.0,
                    "y1": 11.2,
                }
            ],
            "bi_zhongshus": [
                {
                    "begin_date": "2026-05-10",
                    "end_date": "2026-05-14",
                    "zd": 10.2,
                    "zg": 10.9,
                    "zz": 10.55,
                    "bi_count": 3,
                    "is_valid": True,
                }
            ],
            "active_zhongshu": {},
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )

    view = get_structure_view(symbol="sh600519", level="day")

    center = view["centers"][0]
    assert center["raw_begin_index"] == 0
    assert center["raw_end_index"] == 4
    assert center["begin_index"] == 1
    assert center["end_index"] == 4


def test_structure_view_api_returns_public_snapshot_view(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_sample_snapshot()

    response = make_client().get("/api/ai-structure/structure-view/sh600519?level=day")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["snapshot_id"].startswith("czsc_snapshot_")
    assert len(data["bis"]) == 2
    assert data["active_center"]["zd"] == 10.2


def test_structure_view_api_404_when_missing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)

    response = make_client().get("/api/ai-structure/structure-view/sh600519?level=day")

    assert response.status_code == 404
