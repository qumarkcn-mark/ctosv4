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
