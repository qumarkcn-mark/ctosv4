import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import database
from server.api.trade_imports import (
    ConfirmRequest,
    DraftUpdate,
    _create_batch_and_drafts,
    confirm_import_batch,
    delete_import_draft,
    update_import_draft,
)


def _init_tmp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "ctos.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    database.ensure_default_user()
    return db_path


def test_create_batch_marks_unresolved_symbol_blocked(monkeypatch, tmp_path):
    _init_tmp_db(monkeypatch, tmp_path)

    batch = _create_batch_and_drafts(
        "batch1",
        1,
        "2026-05-06",
        "/tmp/fake.jpg",
        "sha",
        {
            "rows": [
                {"name": "未知股票", "direction": "BUY", "price": 10.0, "quantity": 100, "amount": 1000, "confidence": 0.7}
            ],
            "raw": {},
        },
        {"未知股票": {"status": "BLOCKED", "symbol": None, "candidates": []}},
    )

    assert batch["drafts"][0]["status"] == "BLOCKED"
    assert "待匹配股票代码" in batch["drafts"][0]["warnings"]
    assert "image_path" not in batch["batch"]


def test_confirm_batch_writes_imported_trade_and_recalculates_position(monkeypatch, tmp_path):
    _init_tmp_db(monkeypatch, tmp_path)

    _create_batch_and_drafts(
        "batch2",
        1,
        "2026-05-06",
        "/tmp/fake.jpg",
        "sha",
        {
            "rows": [
                {"name": "中国卫星", "direction": "BUY", "price": 106.99, "quantity": 200, "amount": 21398, "confidence": 0.9}
            ],
            "raw": {},
        },
        {"中国卫星": {"status": "MATCHED", "symbol": "sh600118", "candidates": []}},
    )

    result = asyncio.run(confirm_import_batch("batch2", ConfirmRequest(), current_user_id=1))

    conn = database.get_connection()
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id=?", (result["trade_ids"][0],)).fetchone()
        position = conn.execute("SELECT * FROM positions WHERE user_id=1 AND symbol='sh600118'").fetchone()
    finally:
        conn.close()

    assert trade["source"] == "THS_DAILY_SUMMARY_SCREENSHOT"
    assert trade["broker"] == "THS"
    assert trade["is_aggregated"] == 1
    assert position["quantity"] == 200
    assert position["avg_cost"] == 106.99


def test_confirm_batch_is_idempotent(monkeypatch, tmp_path):
    _init_tmp_db(monkeypatch, tmp_path)

    _create_batch_and_drafts(
        "batch-idempotent",
        1,
        "2026-05-06",
        "/tmp/fake.jpg",
        "sha",
        {
            "rows": [
                {"name": "中国卫星", "direction": "BUY", "price": 106.99, "quantity": 200, "amount": 21398, "confidence": 0.9}
            ],
            "raw": {},
        },
        {"中国卫星": {"status": "MATCHED", "symbol": "sh600118", "candidates": []}},
    )

    first = asyncio.run(confirm_import_batch("batch-idempotent", ConfirmRequest(), current_user_id=1))
    second = asyncio.run(confirm_import_batch("batch-idempotent", ConfirmRequest(), current_user_id=1))

    conn = database.get_connection()
    try:
        trade_count = conn.execute(
            "SELECT COUNT(*) AS count FROM trades WHERE import_batch_id='batch-idempotent'"
        ).fetchone()["count"]
        position = conn.execute("SELECT * FROM positions WHERE user_id=1 AND symbol='sh600118'").fetchone()
    finally:
        conn.close()

    assert second["trade_ids"] == first["trade_ids"]
    assert trade_count == 1
    assert position["quantity"] == 200


def test_partial_confirm_keeps_batch_pending_until_all_drafts_confirmed(monkeypatch, tmp_path):
    _init_tmp_db(monkeypatch, tmp_path)

    batch = _create_batch_and_drafts(
        "batch-partial",
        1,
        "2026-05-06",
        "/tmp/fake.jpg",
        "sha",
        {
            "rows": [
                {"name": "中国卫星", "direction": "BUY", "price": 106.99, "quantity": 200, "amount": 21398, "confidence": 0.9},
                {"name": "贵州茅台", "direction": "BUY", "price": 1500, "quantity": 100, "amount": 150000, "confidence": 0.9},
            ],
            "raw": {},
        },
        {
            "中国卫星": {"status": "MATCHED", "symbol": "sh600118", "candidates": []},
            "贵州茅台": {"status": "MATCHED", "symbol": "sh600519", "candidates": []},
        },
    )

    first_draft_id = batch["drafts"][0]["id"]
    result = asyncio.run(
        confirm_import_batch("batch-partial", ConfirmRequest(draft_ids=[first_draft_id]), current_user_id=1)
    )

    assert result["batch"]["batch"]["status"] == "PENDING"
    assert [draft["status"] for draft in result["batch"]["drafts"]] == ["CONFIRMED", "DRAFT"]


def test_delete_import_draft_removes_non_stock_row(monkeypatch, tmp_path):
    _init_tmp_db(monkeypatch, tmp_path)

    batch = _create_batch_and_drafts(
        "batch3",
        1,
        "2026-05-10",
        "/tmp/fake.jpg",
        "sha",
        {
            "rows": [
                {"name": "GC001", "direction": "SELL", "price": 1.485, "quantity": 17200, "amount": 25542, "confidence": 0.8},
                {"name": "中国卫星", "direction": "BUY", "price": 106.99, "quantity": 2000, "amount": 213980, "confidence": 0.8},
            ],
            "raw": {},
        },
        {
            "GC001": {"status": "BLOCKED", "symbol": None, "candidates": []},
            "中国卫星": {"status": "MATCHED", "symbol": "sh600118", "candidates": []},
        },
    )

    gc_draft_id = batch["drafts"][0]["id"]
    result = asyncio.run(delete_import_draft(gc_draft_id, current_user_id=1))

    assert [draft["name"] for draft in result["drafts"]] == ["中国卫星"]
    assert result["summary"][0]["name"] == "中国卫星"


def test_update_import_draft_can_clear_amount_and_unack_duplicate(monkeypatch, tmp_path):
    _init_tmp_db(monkeypatch, tmp_path)

    batch = _create_batch_and_drafts(
        "batch-update",
        1,
        "2026-05-06",
        "/tmp/fake.jpg",
        "sha",
        {
            "rows": [
                {"name": "中国卫星", "direction": "BUY", "price": 106.99, "quantity": 200, "amount": 21398, "confidence": 0.9}
            ],
            "raw": {},
        },
        {"中国卫星": {"status": "MATCHED", "symbol": "sh600118", "candidates": []}},
    )
    draft_id = batch["drafts"][0]["id"]

    acknowledged = asyncio.run(
        update_import_draft(draft_id, DraftUpdate(duplicate_ack=True), current_user_id=1)
    )["draft"]
    cleared = asyncio.run(
        update_import_draft(draft_id, DraftUpdate(amount=None, duplicate_ack=False), current_user_id=1)
    )["draft"]

    assert acknowledged["duplicate_ack"] is True
    assert cleared["duplicate_ack"] is False
    assert cleared["amount"] is None
