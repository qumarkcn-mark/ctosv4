import struct

from server.services import tdx_daily_sync_service as svc


def _write_day(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as file:
        for date_int, close in rows:
            file.write(struct.pack(
                svc.RECORD_FMT,
                date_int,
                int((close - 0.1) * 100),
                int((close + 0.2) * 100),
                int((close - 0.2) * 100),
                int(close * 100),
                100000.0,
                1000,
                0,
            ))


def test_vipdoc_status_counts_a_share_day_files(tmp_path, monkeypatch):
    root = tmp_path / "vipdoc"
    _write_day(root / "sh" / "lday" / "sh600001.day", [(20260101, 10.0)])
    _write_day(root / "sh" / "lday" / "sh000001.day", [(20260101, 10.0)])
    _write_day(root / "sz" / "lday" / "sz300001.day", [(20260101, 20.0)])

    monkeypatch.setattr(svc, "get_lake_path", lambda source: str(tmp_path / f"{source}.db"))

    status = svc.vipdoc_status(str(root))

    assert status["available"] is True
    assert status["a_share_day_files"] == 2
    assert status["records_estimate"] == 2


def test_resolve_vipdoc_accepts_root_with_nested_vipdoc(tmp_path):
    root = tmp_path / "tdx_mount"
    (root / "vipdoc" / "sh" / "lday").mkdir(parents=True)
    (root / "vipdoc" / "sz" / "lday").mkdir(parents=True)

    assert svc.resolve_vipdoc(str(root)) == str(root / "vipdoc")


def test_read_tdx_day_klines_reads_one_symbol_from_local_file(tmp_path):
    root = tmp_path / "vipdoc"
    _write_day(
        root / "sz" / "lday" / "sz301078.day",
        [(20260521, 9.51), (20260522, 9.54)],
    )
    (root / "sh" / "lday").mkdir(parents=True)

    rows = svc.read_tdx_day_klines("sz301078", vipdoc=str(root), limit=10)

    assert [row["date"] for row in rows] == ["2026-05-21", "2026-05-22"]
    assert rows[-1]["close"] == 9.53


def test_read_tdx_week_klines_aggregates_local_day_rows(tmp_path):
    root = tmp_path / "vipdoc"
    _write_day(
        root / "sz" / "lday" / "sz301078.day",
        [
            (20260518, 9.1),
            (20260519, 9.3),
            (20260522, 9.5),
            (20260525, 9.7),
        ],
    )
    (root / "sh" / "lday").mkdir(parents=True)

    rows = svc.read_tdx_week_klines("sz301078", vipdoc=str(root), limit=10)

    assert [row["date"] for row in rows] == ["2026-05-22", "2026-05-25"]
    assert rows[0]["open"] == 9.0
    assert rows[0]["close"] == 9.5
    assert rows[0]["volume"] == 3000


def test_sync_daily_files_writes_tdx_lake(tmp_path, monkeypatch):
    root = tmp_path / "vipdoc"
    db_path = tmp_path / "tdx_lake.db"
    _write_day(
        root / "sh" / "lday" / "sh600001.day",
        [(20260101, 10.0), (20260102, 10.5)],
    )
    _write_day(root / "sz" / "lday" / "sz300001.day", [(20260102, 20.0)])
    _write_day(root / "sh" / "lday" / "sh000001.day", [(20260102, 30.0)])

    monkeypatch.setattr(svc, "get_lake_path", lambda source: str(db_path))
    monkeypatch.setattr(svc, "init_lake", lambda: None)

    result = svc.sync_daily_files(root, mode="full", reset=True)

    assert result["total_files"] == 2
    assert result["synced_symbols"] == 2
    assert result["written_rows"] == 3
