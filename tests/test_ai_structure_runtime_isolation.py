import sys
from pathlib import Path

from fastapi.testclient import TestClient

from server import config
from server.app import app
from server.workers.kline_sync_worker import enqueue_structure_jobs_for_changes


def test_legacy_runtime_switch_is_removed():
    assert not hasattr(config, "ENABLE_LEGACY_CHAN_RADAR")


def test_default_app_runtime_does_not_register_legacy_structure_routes():
    paths = {route.path for route in app.routes}

    assert any(path.startswith("/api/ai-structure") for path in paths)
    assert not any(path.startswith("/api/scan") for path in paths)
    assert not any(path.startswith("/api/chan") for path in paths)
    assert not any(path.startswith("/api/radar") for path in paths)
    assert not any(path.startswith("/api/agent") for path in paths)
    assert not any(path.startswith("/api/sand-table") for path in paths)
    assert not any(path.startswith("/api/structure") for path in paths)
    assert not any(path.startswith("/api/playbook") for path in paths)
    assert not any(path.startswith("/api/rotation") for path in paths)
    assert not any(path.startswith("/api/multiverse") for path in paths)


def test_default_app_runtime_does_not_import_legacy_structure_modules():
    for module_name in (
        "server.api.radar",
        "server.api.chan",
        "server.api.agent",
        "server.api.playbook",
        "server.api.rotation",
        "server.api.sand_table",
        "server.api.structure",
        "server.services.chan_service",
        "server.services.chan_detail_service",
    ):
        assert module_name not in sys.modules


def test_default_kline_sync_enqueues_only_czsc_v5_jobs(monkeypatch):
    def fake_prewarm(**kwargs):
        return {
            "count": len(kwargs["levels"]),
            "items": [
                {"symbol": kwargs["symbols"][0], "level": level, "status": "PENDING", "engine": "czsc", "enqueued": True}
                for level in kwargs["levels"]
            ],
        }

    monkeypatch.setattr(
        "server.engines.ai_native.czsc_snapshot_service.prewarm_structure_snapshots",
        fake_prewarm,
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.list_interested_user_ids_for_symbol",
        lambda _symbol: [],
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.has_active_position_for_symbol",
        lambda _symbol: False,
    )

    result = enqueue_structure_jobs_for_changes(
        [{"symbol": "sh600519", "freq": "day", "written": 1}],
        reason="test",
    )

    assert result["engine"] == "czsc"
    assert result["count"] == 1
    assert result["items"][0]["status"] == "PENDING"
    assert "server.engines.structure.structure_jobs" not in sys.modules
    assert "server.engines.structure.snapshot_query" not in sys.modules


def test_default_scanner_runtime_is_removed():
    client = TestClient(app)

    response = client.post("/api/scan/run")

    assert response.status_code == 404
    assert "server.workers.scanner" not in sys.modules
    assert "server.services.chan_scanner" not in sys.modules


def test_v5_frontend_default_bundle_has_no_legacy_radar_or_chan_refs():
    frontend_files = [
        path
        for path in Path("web/src").rglob("*")
        if path.is_file() and path.suffix in {".js", ".jsx", ".css"}
    ]
    forbidden = (
        "/api/radar",
        "/api/chan",
        "/api/agent",
        "/api/scan/run",
        "ai-native-radar",
        "ai_native_radar",
        "RadarPanel",
        "useRadarData",
        "chanOverlay",
        "KlineChart",
        "LayerPanel",
        "DailyPlaybook",
        "ChanView",
        "SandTable",
        "AITrainingReportPanel",
        "Scanner.jsx",
        "ScanCard",
        "klineData",
    )

    for path in frontend_files:
        text = path.read_text()
        for snippet in forbidden:
            assert snippet not in text, f"{path} must not reference legacy frontend path {snippet}"
