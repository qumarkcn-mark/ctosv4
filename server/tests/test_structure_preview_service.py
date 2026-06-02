from server.engines.ai_native import structure_preview_service as preview


def test_structure_preview_queues_snapshot_on_cache_miss(monkeypatch):
    monkeypatch.setattr(preview, "get_latest_structure", lambda **_kwargs: None)
    monkeypatch.setattr(
        preview,
        "signature_for_level",
        lambda **_kwargs: {
            "signature": "sig-preview",
            "last_date": "2026-05-29 15:00:00",
        },
    )

    captured = {}

    def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {
            "job_id": "v5snapjob_test",
            "status": "PENDING",
            "enqueued": True,
            "bumped": False,
        }

    monkeypatch.setattr(preview, "enqueue_snapshot_job", fake_enqueue)

    result = preview.get_structure_preview(symbol="sh.600790", level="30")

    assert result["status"] == "queued"
    assert result["job"]["job_id"] == "v5snapjob_test"
    assert captured["symbol"] == "sh.600790"
    assert captured["level"] == "30"
    assert captured["data_signature"] == "sig-preview"
    assert captured["reason"] == "kline_preview_cache_miss"


def test_structure_preview_does_not_enqueue_without_signature(monkeypatch):
    monkeypatch.setattr(preview, "get_latest_structure", lambda **_kwargs: None)
    monkeypatch.setattr(preview, "signature_for_level", lambda **_kwargs: {"signature": ""})

    def fail_enqueue(**_kwargs):
        raise AssertionError("preview should not enqueue without a data signature")

    monkeypatch.setattr(preview, "enqueue_snapshot_job", fail_enqueue)

    result = preview.get_structure_preview(symbol="sh.600790", level="30")

    assert result["status"] == "missing_data"
