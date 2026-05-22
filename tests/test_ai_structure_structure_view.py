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
            "segs": [
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-10",
                    "x1": "2026-05-12",
                    "y0": 10.0,
                    "y1": 10.8,
                    "bar_count": 3,
                    "source": "czsc_object",
                    "start_bi_index": 0,
                    "end_bi_index": 1,
                }
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
    assert view["segments"][0]["start_index"] == 0
    assert view["segments"][0]["end_index"] == 2
    assert view["segments"][0]["source"] == "czsc_object"
    assert view["segments"][0]["start_bi_index"] == 0
    assert view["capabilities"]["segment_source"] == "czsc_object"
    assert view["capabilities"]["segments"] is True
    assert view["capabilities"]["segment_status"] == "ready"
    assert view["centers"][0]["active"] is True
    assert view["centers"][0]["end_index"] == 2
    assert view["centers"][0]["exit_status"] == "open"
    assert view["active_center"]["begin_index"] == 0
    assert view["active_center"]["end_index"] == 2
    assert view["active_center"]["begin_bar_time"] == "2026-05-10"
    assert view["active_center"]["end_bar_time"] == "2026-05-12"
    assert view["active_center"]["exit_status"] == "open"
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
                {"time": "2026-05-14", "open": 10.7, "high": 10.8, "low": 10.4, "close": 10.7},
                {"time": "2026-05-15", "open": 10.7, "high": 11.2, "low": 10.7, "close": 11.0},
                {"time": "2026-05-16", "open": 11.0, "high": 11.5, "low": 11.0, "close": 11.4},
            ],
            "bis": [
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-11",
                    "x1": "2026-05-13",
                    "y0": 10.0,
                    "y1": 10.7,
                },
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-14",
                    "x1": "2026-05-16",
                    "y0": 10.7,
                    "y1": 11.4,
                },
            ],
            "bi_zhongshus": [
                {
                    "begin_date": "2026-05-13",
                    "end_date": "2026-05-16",
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
    assert center["raw_begin_index"] == 3
    assert center["raw_end_index"] == 6
    assert center["begin_index"] == 1
    assert center["end_index"] == 6
    assert center["begin_bar_time"] == "2026-05-11"
    assert center["end_bar_time"] == "2026-05-16"
    assert center["exit_status"] == "closed"


def test_structure_view_splits_legacy_unfinished_bi_from_confirmed_sequence(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-unfinished-bi",
        data_as_of="2026-05-16",
        snapshot_payload={
            "level": "day",
            "price": 10.6,
            "klines": [
                {"time": "2026-05-10", "open": 10, "high": 11, "low": 9, "close": 10.5},
                {"time": "2026-05-11", "open": 10.5, "high": 12, "low": 10, "close": 11.5},
                {"time": "2026-05-12", "open": 11.5, "high": 12, "low": 10.6, "close": 10.8},
                {"time": "2026-05-13", "open": 10.8, "high": 11, "low": 10.3, "close": 10.6},
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
                },
                {
                    "direction": "down",
                    "is_up": False,
                    "is_sure": False,
                    "x0": "2026-05-11",
                    "x1": "2026-05-13",
                    "y0": 11.5,
                    "y1": 10.3,
                    "source": "czsc_ubi",
                    "status": "ongoing",
                },
            ],
            "bi_zhongshus": [],
            "active_zhongshu": {},
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )

    view = get_structure_view(symbol="sh600519", level="day")

    assert len(view["bis"]) == 1
    assert view["bis"][0]["is_sure"] is True
    assert view["unfinished_bi"]["is_sure"] is False
    assert view["unfinished_bi"]["source"] == "czsc_ubi"


def test_structure_view_active_center_extends_to_latest_bar_when_not_fully_left(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-active-center-open",
        data_as_of="2026-05-16",
        snapshot_payload={
            "level": "day",
            "price": 10.7,
            "klines": [
                {"time": "2026-05-10", "open": 9.8, "high": 10.1, "low": 9.7, "close": 10.0},
                {"time": "2026-05-11", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.3},
                {"time": "2026-05-12", "open": 10.3, "high": 10.8, "low": 10.2, "close": 10.6},
                {"time": "2026-05-13", "open": 10.6, "high": 10.9, "low": 10.4, "close": 10.7},
                {"time": "2026-05-14", "open": 10.7, "high": 11.1, "low": 10.6, "close": 10.8},
            ],
            "bis": [
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-10",
                    "x1": "2026-05-13",
                    "y0": 10.0,
                    "y1": 10.7,
                }
            ],
            "bi_zhongshus": [],
            "active_zhongshu": {
                "begin_date": "2026-05-13",
                "end_date": "2026-05-13",
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

    view = get_structure_view(symbol="sh600519", level="day")

    center = view["active_center"]
    assert center["begin_index"] == 1
    assert center["end_index"] == 4
    assert center["begin_bar_time"] == "2026-05-11"
    assert center["end_bar_time"] == "2026-05-14"
    assert center["exit_status"] == "open"


def test_structure_view_replaces_duplicate_center_with_open_active_center(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-active-center-replace",
        data_as_of="2026-05-14",
        snapshot_payload={
            "level": "day",
            "price": 10.8,
            "klines": [
                {"time": "2026-05-10", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
                {"time": "2026-05-11", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.6},
                {"time": "2026-05-12", "open": 10.6, "high": 10.9, "low": 10.2, "close": 10.5},
                {"time": "2026-05-13", "open": 10.5, "high": 10.9, "low": 10.3, "close": 10.7},
                {"time": "2026-05-14", "open": 10.7, "high": 10.8, "low": 10.4, "close": 10.6},
            ],
            "bis": [
                {"direction": "up", "is_up": True, "is_sure": True, "x0": "2026-05-10", "x1": "2026-05-11", "y0": 10.2, "y1": 10.6},
                {"direction": "down", "is_up": False, "is_sure": True, "x0": "2026-05-11", "x1": "2026-05-12", "y0": 10.6, "y1": 10.5},
                {"direction": "up", "is_up": True, "is_sure": True, "x0": "2026-05-12", "x1": "2026-05-13", "y0": 10.5, "y1": 10.7},
            ],
            "bi_zhongshus": [
                {
                    "begin_date": "2026-05-10",
                    "end_date": "2026-05-13",
                    "zd": 10.2,
                    "zg": 10.8,
                    "zz": 10.5,
                    "bi_count": 3,
                    "is_valid": True,
                }
            ],
            "active_zhongshu": {
                "begin_date": "2026-05-10",
                "end_date": "2026-05-13",
                "zd": 10.2,
                "zg": 10.8,
                "zz": 10.5,
                "bi_count": 3,
                "is_valid": True,
            },
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )

    view = get_structure_view(symbol="sh600519", level="day")

    assert len(view["centers"]) == 1
    assert view["centers"][0]["active"] is True
    assert view["centers"][0]["end_index"] == 4
    assert view["centers"][0]["end_bar_time"] == "2026-05-14"
    assert view["centers"][0]["exit_status"] == "open"


def test_structure_view_does_not_mark_closed_latest_center_as_active(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-active-center-closed",
        data_as_of="2026-05-15",
        snapshot_payload={
            "level": "day",
            "price": 11.5,
            "klines": [
                {"time": "2026-05-10", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
                {"time": "2026-05-11", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.6},
                {"time": "2026-05-12", "open": 10.6, "high": 10.9, "low": 10.2, "close": 10.5},
                {"time": "2026-05-13", "open": 10.5, "high": 10.9, "low": 10.3, "close": 10.7},
                {"time": "2026-05-14", "open": 11.0, "high": 11.3, "low": 11.0, "close": 11.2},
                {"time": "2026-05-15", "open": 11.2, "high": 11.6, "low": 11.1, "close": 11.5},
            ],
            "bis": [
                {"direction": "up", "is_up": True, "is_sure": True, "x0": "2026-05-10", "x1": "2026-05-11", "y0": 10.2, "y1": 10.6},
                {"direction": "down", "is_up": False, "is_sure": True, "x0": "2026-05-11", "x1": "2026-05-12", "y0": 10.6, "y1": 10.5},
                {"direction": "up", "is_up": True, "is_sure": True, "x0": "2026-05-12", "x1": "2026-05-15", "y0": 10.5, "y1": 11.5},
            ],
            "bi_zhongshus": [
                {
                    "begin_date": "2026-05-10",
                    "end_date": "2026-05-13",
                    "zd": 10.2,
                    "zg": 10.8,
                    "zz": 10.5,
                    "bi_count": 3,
                    "is_valid": True,
                }
            ],
            "active_zhongshu": {
                "begin_date": "2026-05-10",
                "end_date": "2026-05-13",
                "zd": 10.2,
                "zg": 10.8,
                "zz": 10.5,
                "bi_count": 3,
                "is_valid": True,
            },
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )

    view = get_structure_view(symbol="sh600519", level="day")

    assert view["active_center"] is None
    assert view["centers"][0]["active"] is False
    assert view["centers"][0]["exit_status"] == "closed"


def test_structure_view_api_returns_public_snapshot_view(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_sample_snapshot()

    response = make_client().get("/api/ai-structure/structure-view/sh600519?level=day")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["snapshot_id"].startswith("czsc_snapshot_")
    assert len(data["bis"]) == 2
    assert len(data["segments"]) == 1
    assert data["active_center"]["zd"] == 10.2


def test_structure_view_marks_segments_unavailable_without_fallback(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-no-segments",
        data_as_of="2026-05-16",
        snapshot_payload={
            "level": "day",
            "price": 10.8,
            "klines": [
                {"time": "2026-05-10", "open": 10, "high": 11, "low": 9, "close": 10.5},
                {"time": "2026-05-11", "open": 10.5, "high": 12, "low": 10, "close": 11.5},
            ],
            "bis": [],
            "segs": [],
            "bi_zhongshus": [],
            "active_zhongshu": {},
            "metadata": {"unsupported_fields": ["segs", "seg_zhongshus", "bsps"]},
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )

    view = get_structure_view(symbol="sh600519", level="day")

    assert view["segments"] == []
    assert view["capabilities"]["segment_status"] == "unavailable"
    assert view["capabilities"]["segment_reason"] == "czsc_object_does_not_expose_segments"


def test_structure_view_does_not_derive_segments_from_existing_snapshot_bis(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    points = [
        ("2026-05-01", 10.0),
        ("2026-05-02", 12.0),
        ("2026-05-03", 11.0),
        ("2026-05-04", 13.0),
        ("2026-05-05", 10.0),
        ("2026-05-06", 11.0),
        ("2026-05-07", 9.0),
        ("2026-05-08", 10.0),
    ]
    klines = [
        {"time": time, "open": price, "high": price + 0.2, "low": price - 0.2, "close": price}
        for time, price in points
    ]
    bis = []
    for (start_time, start_price), (end_time, end_price) in zip(points, points[1:]):
        bis.append(
            {
                "direction": "up" if end_price >= start_price else "down",
                "is_up": end_price >= start_price,
                "is_sure": True,
                "x0": start_time,
                "x1": end_time,
                "y0": start_price,
                "y1": end_price,
                "start_price": start_price,
                "end_price": end_price,
                "bar_count": 1,
            }
        )
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-no-derived-segments",
        data_as_of="2026-05-08",
        snapshot_payload={
            "level": "day",
            "price": 10.0,
            "klines": klines,
            "bis": bis,
            "segs": [],
            "bi_zhongshus": [],
            "active_zhongshu": {},
            "metadata": {"unsupported_fields": ["segs"], "segment_source": "unavailable_in_czsc_object"},
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )

    view = get_structure_view(symbol="sh600519", level="day")

    assert view["segments"] == []
    assert view["capabilities"]["segment_status"] == "unavailable"
    assert view["capabilities"]["segment_source"] == "unavailable_in_czsc_object"
    assert view["capabilities"]["segment_reason"] == "czsc_object_does_not_expose_segments"


def test_structure_view_api_404_when_missing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)

    response = make_client().get("/api/ai-structure/structure-view/sh600519?level=day")

    assert response.status_code == 404
