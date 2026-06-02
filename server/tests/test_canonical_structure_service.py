import sqlite3

from server.engines.structure import canonical_structure_service as canonical


STRUCTURE_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS structure_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    level TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'czsc' CHECK(engine = 'czsc'),
    engine_version TEXT NOT NULL DEFAULT '',
    adapter_version TEXT NOT NULL DEFAULT '',
    compute_profile TEXT NOT NULL,
    data_signature TEXT NOT NULL,
    data_as_of TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL,
    raw_bi_context_json TEXT NOT NULL DEFAULT '{}',
    structure_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'fresh',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, level, engine, compute_profile, data_signature)
);
"""


def _setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "canonical.db"
    monkeypatch.setenv("CT_OS_DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(STRUCTURE_SNAPSHOT_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _patch_signatures(monkeypatch):
    def fake_signature(*, symbol, level, compute_profile, allow_bootstrap=True):
        return {
            "signature": f"sig-{compute_profile}",
            "last_date": "2026-05-29 15:00:00",
            "source": "tdx",
            "row_count": 1200,
        }

    monkeypatch.setattr(canonical, "signature_for_level", fake_signature)


def _snapshot_payload(price=10):
    return {
        "price": price,
        "klines": [{"date": "2026-05-29 15:00:00", "close": price}],
        "bis": [],
        "active_zhongshu": {},
    }


def test_shallow_preview_reuses_deeper_fresh_snapshot(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _patch_signatures(monkeypatch)
    canonical.save_canonical_snapshot(
        symbol="sh.600790",
        level="30",
        compute_profile="tactical_v1",
        data_signature="sig-tactical_v1",
        data_as_of="2026-05-29 15:00:00",
        snapshot_payload=_snapshot_payload(11),
        raw_bi_context={},
        engine_version="test",
        adapter_version="test",
    )

    def fail_compute(**_kwargs):
        raise AssertionError("preview should reuse the deeper row")

    monkeypatch.setattr(canonical, "_compute_and_serialize", fail_compute)

    row = canonical.get_or_compute_structure(
        symbol="sh.600790",
        level="30",
        min_profile="chart_standard_v1",
        write_policy="read_through",
    )

    assert row["compute_profile"] == "tactical_v1"
    assert row["snapshot"]["price"] == 11
    assert row["canonical_cache_status"] == "hit"


def test_ai_depth_does_not_read_shallow_snapshot(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _patch_signatures(monkeypatch)
    canonical.save_canonical_snapshot(
        symbol="sh.600790",
        level="30",
        compute_profile="chart_standard_v1",
        data_signature="sig-chart_standard_v1",
        data_as_of="2026-05-29 15:00:00",
        snapshot_payload=_snapshot_payload(10),
        raw_bi_context={},
        engine_version="test",
        adapter_version="test",
    )

    calls = []

    def fake_compute(*, symbol, level, compute_profile):
        calls.append((symbol, level, compute_profile))
        return {
            "snapshot_payload": _snapshot_payload(12),
            "raw_bi_context": {},
            "engine_version": "test",
            "adapter_version": "test",
        }

    monkeypatch.setattr(canonical, "_compute_and_serialize", fake_compute)

    row = canonical.get_or_compute_structure(
        symbol="sh.600790",
        level="30",
        min_profile="tactical_v1",
        write_policy="read_through",
    )

    assert calls == [("sh.600790", "30", "tactical_v1")]
    assert row["compute_profile"] == "tactical_v1"
    assert row["snapshot"]["price"] == 12


def test_read_through_computes_and_persists_min_profile_on_cold_cache(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _patch_signatures(monkeypatch)
    calls = []

    def fake_compute(*, symbol, level, compute_profile):
        calls.append((symbol, level, compute_profile))
        return {
            "snapshot_payload": _snapshot_payload(13),
            "raw_bi_context": {},
            "engine_version": "test",
            "adapter_version": "test",
        }

    monkeypatch.setattr(canonical, "_compute_and_serialize", fake_compute)

    row = canonical.get_or_compute_structure(
        symbol="sh.600790",
        level="30",
        min_profile="chart_standard_v1",
        write_policy="read_through",
    )
    second = canonical.get_or_compute_structure(
        symbol="sh.600790",
        level="30",
        min_profile="chart_standard_v1",
        write_policy="read_through",
    )

    assert calls == [("sh.600790", "30", "chart_standard_v1")]
    assert row["compute_profile"] == "chart_standard_v1"
    assert second["snapshot_id"] == row["snapshot_id"]
    assert second["canonical_cache_status"] == "hit"


def test_latest_structure_reads_deeper_snapshot_without_computing(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _patch_signatures(monkeypatch)
    canonical.save_canonical_snapshot(
        symbol="sh.600790",
        level="30",
        compute_profile="deep_audit_v1",
        data_signature="sig-deep_audit_v1",
        data_as_of="2026-05-29 15:00:00",
        snapshot_payload=_snapshot_payload(15),
        raw_bi_context={},
        engine_version="test",
        adapter_version="test",
    )

    def fail_compute(**_kwargs):
        raise AssertionError("read-only latest lookup must not compute")

    monkeypatch.setattr(canonical, "_compute_and_serialize", fail_compute)

    row = canonical.get_latest_structure(
        symbol="sh.600790",
        level="30",
        min_profile="tactical_v1",
    )

    assert row["compute_profile"] == "deep_audit_v1"
    assert row["snapshot"]["price"] == 15
    assert row["canonical_cache_status"] == "hit"


def test_latest_structure_does_not_return_shallow_snapshot_for_ai_depth(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _patch_signatures(monkeypatch)
    canonical.save_canonical_snapshot(
        symbol="sh.600790",
        level="30",
        compute_profile="chart_standard_v1",
        data_signature="sig-chart_standard_v1",
        data_as_of="2026-05-29 15:00:00",
        snapshot_payload=_snapshot_payload(16),
        raw_bi_context={},
        engine_version="test",
        adapter_version="test",
    )

    row = canonical.get_latest_structure(
        symbol="sh.600790",
        level="30",
        min_profile="tactical_v1",
    )

    assert row is None


def test_latest_structure_can_skip_tdx_bootstrap_on_preview_read(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    calls = []

    def fake_signature(*, symbol, level, compute_profile, allow_bootstrap=True):
        calls.append(allow_bootstrap)
        return {
            "signature": "",
            "last_date": "",
            "source": "tdx",
            "row_count": 0,
        }

    def fail_bootstrap(*_args, **_kwargs):
        raise AssertionError("preview read must not bootstrap TDX/qfq")

    monkeypatch.setattr(canonical, "signature_for_level", fake_signature)
    monkeypatch.setattr(canonical, "_bootstrap_tdx_qfq_for_structure", fail_bootstrap)

    row = canonical.get_latest_structure(
        symbol="sh.600790",
        level="30",
        min_profile="chart_standard_v1",
        allow_bootstrap=False,
    )

    assert row is None
    assert calls == [False, False, False]
