from __future__ import annotations

"""交易截图导入 API。

AI 识别只生成草稿；用户确认后才写入交易账本。
"""

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from server import config
from server.api.auth import get_current_user_id
from server.db.database import get_connection
from server.domain.symbols import parse_symbol, to_tencent_symbol
from server.services.position_calc import recalculate_all_positions
from server.services.stock_search import match_stock_name
from server.services.trade_screenshot_importer import (
    ScreenshotImportError,
    build_row_fingerprint,
    dumps_json,
    extract_ths_daily_summary,
    image_sha256,
    validate_image_upload,
)

router = APIRouter()


class DraftUpdate(BaseModel):
    symbol: Optional[str] = None
    name: Optional[str] = None
    direction: Optional[str] = None
    price: Optional[float] = Field(None, gt=0)
    quantity: Optional[int] = Field(None, gt=0)
    amount: Optional[float] = Field(None, gt=0)
    duplicate_ack: Optional[bool] = None


class ConfirmRequest(BaseModel):
    draft_ids: Optional[list[int]] = None


@router.post("/ths-summary")
async def import_ths_summary_screenshot(
    file: UploadFile = File(...),
    trade_date: Optional[str] = None,
    current_user_id: int = Depends(get_current_user_id),
):
    """上传同花顺当日成交汇总截图，返回待确认草稿。"""
    user_id = current_user_id
    effective_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    content = await file.read()
    try:
        mime_type = validate_image_upload(content, file.content_type)
        extracted = await extract_ths_daily_summary(content, mime_type, effective_date, user_id=user_id)
    except ScreenshotImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"截图识别失败: {exc}") from exc

    unique_names = sorted({row["name"] for row in extracted["rows"]})
    matches = {name: await match_stock_name(name) for name in unique_names}
    batch_id = uuid.uuid4().hex
    sha = image_sha256(content)
    image_path = await run_in_threadpool(_save_image, user_id, batch_id, file.filename, content, mime_type)
    batch = await run_in_threadpool(
        _create_batch_and_drafts,
        batch_id,
        user_id,
        effective_date,
        image_path,
        sha,
        extracted,
        matches,
    )
    return batch


@router.get("/{batch_id}")
def get_import_batch(batch_id: str, current_user_id: int = Depends(get_current_user_id)):
    user_id = current_user_id
    return _load_batch(batch_id, user_id)


@router.patch("/drafts/{draft_id}")
async def update_import_draft(
    draft_id: int,
    update: DraftUpdate,
    current_user_id: int = Depends(get_current_user_id),
):
    user_id = current_user_id
    def _update():
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM trade_import_drafts WHERE id=? AND user_id=?",
                (draft_id, user_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "导入草稿不存在")
            current = dict(row)
            update_fields = update.model_fields_set if hasattr(update, "model_fields_set") else getattr(update, "__fields_set__", set())
            next_values = {
                "symbol": update.symbol if "symbol" in update_fields else current["symbol"],
                "name": update.name if "name" in update_fields else current["name"],
                "direction": update.direction if "direction" in update_fields else current["direction"],
                "price": update.price if "price" in update_fields else current["price"],
                "quantity": update.quantity if "quantity" in update_fields else current["quantity"],
                "amount": update.amount if "amount" in update_fields else current["amount"],
                "duplicate_ack": (1 if update.duplicate_ack else 0) if "duplicate_ack" in update_fields else (current["duplicate_ack"] or 0),
            }
            warnings = _draft_warnings(next_values)
            status = _status_for_warnings(warnings, current["status"], next_values["duplicate_ack"])
            if next_values["symbol"]:
                next_values["symbol"] = to_tencent_symbol(next_values["symbol"])
            conn.execute(
                """
                UPDATE trade_import_drafts
                   SET symbol=?, name=?, direction=?, price=?, quantity=?, amount=?,
                       warnings_json=?, status=?, duplicate_ack=?, updated_at=CURRENT_TIMESTAMP
                 WHERE id=? AND user_id=?
                """,
                (
                    next_values["symbol"],
                    next_values["name"],
                    next_values["direction"],
                    next_values["price"],
                    next_values["quantity"],
                    next_values["amount"],
                    dumps_json(warnings),
                    status,
                    next_values["duplicate_ack"],
                    draft_id,
                    user_id,
                ),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM trade_import_drafts WHERE id=?", (draft_id,)).fetchone())
        finally:
            conn.close()

    draft = await run_in_threadpool(_update)
    return {"draft": _serialize_draft(draft)}


@router.delete("/drafts/{draft_id}")
async def delete_import_draft(draft_id: int, current_user_id: int = Depends(get_current_user_id)):
    """从当前导入批次中忽略一条草稿。用于剔除国债逆回购等非股票成交。"""
    user_id = current_user_id
    def _delete():
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT batch_id, status FROM trade_import_drafts WHERE id=? AND user_id=?",
                (draft_id, user_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "导入草稿不存在")
            if row["status"] == "CONFIRMED":
                raise HTTPException(400, "已入账草稿不能删除")
            batch_id = row["batch_id"]
            conn.execute("DELETE FROM trade_import_drafts WHERE id=? AND user_id=?", (draft_id, user_id))
            conn.commit()
            return _load_batch_with_conn(conn, batch_id, user_id)
        finally:
            conn.close()

    return await run_in_threadpool(_delete)


@router.post("/{batch_id}/confirm")
async def confirm_import_batch(
    batch_id: str,
    req: Optional[ConfirmRequest] = None,
    current_user_id: int = Depends(get_current_user_id),
):
    user_id = current_user_id
    def _confirm():
        conn = get_connection()
        try:
            batch = conn.execute(
                "SELECT * FROM trade_import_batches WHERE batch_id=? AND user_id=?",
                (batch_id, user_id),
            ).fetchone()
            if not batch:
                raise HTTPException(404, "导入批次不存在")
            query = "SELECT * FROM trade_import_drafts WHERE batch_id=? AND user_id=?"
            params: list = [batch_id, user_id]
            if req and req.draft_ids:
                placeholders = ",".join("?" for _ in req.draft_ids)
                query += f" AND id IN ({placeholders})"
                params.extend(req.draft_ids)
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
            if not rows:
                raise HTTPException(400, "没有可确认的草稿")

            existing_trade_ids = [int(row["trade_id"]) for row in rows if row["status"] == "CONFIRMED" and row.get("trade_id")]
            rows_to_confirm = [row for row in rows if row["status"] != "CONFIRMED"]
            if not rows_to_confirm:
                return {"ok": True, "trade_ids": existing_trade_ids, "batch": _load_batch_with_conn(conn, batch_id, user_id)}

            blocking = []
            for row in rows_to_confirm:
                warnings = _draft_warnings(row)
                if row["status"] == "POSSIBLE_DUPLICATE" and not row["duplicate_ack"]:
                    warnings.append("疑似重复，需先确认")
                if warnings:
                    blocking.append({"id": row["id"], "warnings": warnings})
            if blocking:
                raise HTTPException(400, {"message": "存在未解决草稿", "rows": blocking})

            _validate_sells(conn, user_id, rows_to_confirm)

            trade_ids = list(existing_trade_ids)
            for row in rows_to_confirm:
                amount = row["amount"] if row["amount"] is not None else row["price"] * row["quantity"]
                cursor = conn.execute(
                    """
                    INSERT INTO trades (
                        user_id, symbol, name, direction, price, quantity, amount,
                        source, broker, is_aggregated, import_batch_id, import_draft_id,
                        plan_relationship, traded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'THS_DAILY_SUMMARY_SCREENSHOT',
                            'THS', 1, ?, ?, 'UNKNOWN', ?)
                    """,
                    (
                        user_id,
                        to_tencent_symbol(row["symbol"]),
                        row["name"],
                        row["direction"],
                        row["price"],
                        row["quantity"],
                        amount,
                        batch_id,
                        row["id"],
                        f"{batch['trade_date']}T15:00:00",
                    ),
                )
                trade_id = cursor.lastrowid
                trade_ids.append(trade_id)
                conn.execute(
                    "UPDATE trade_import_drafts SET status='CONFIRMED', trade_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (trade_id, row["id"]),
                )

            recalculate_all_positions(conn, user_id)
            remaining = conn.execute(
                """
                SELECT COUNT(*) AS count
                  FROM trade_import_drafts
                 WHERE batch_id=? AND user_id=? AND status <> 'CONFIRMED'
                """,
                (batch_id, user_id),
            ).fetchone()["count"]
            if remaining == 0:
                conn.execute(
                    "UPDATE trade_import_batches SET status='CONFIRMED', confirmed_at=CURRENT_TIMESTAMP WHERE batch_id=?",
                    (batch_id,),
                )
            conn.commit()
            return {"ok": True, "trade_ids": trade_ids, "batch": _load_batch_with_conn(conn, batch_id, user_id)}
        finally:
            conn.close()

    return await run_in_threadpool(_confirm)


def _save_image(user_id: int, batch_id: str, filename: str | None, content: bytes, mime_type: str) -> str:
    ext = { "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp" }[mime_type]
    directory = Path(config.DATA_DIR) / "trade_imports" / str(user_id) / batch_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"original{ext}"
    with path.open("wb") as handle:
        shutil.copyfileobj(_BytesReader(content), handle)
    return str(path)


def _create_batch_and_drafts(batch_id, user_id, trade_date, image_path, sha, extracted, matches):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO trade_import_batches (
                batch_id, user_id, broker, import_type, trade_date,
                status, image_path, image_sha256, raw_vision_json
            )
            VALUES (?, ?, 'THS', 'DAILY_SUMMARY_SCREENSHOT', ?, 'PENDING', ?, ?, ?)
            """,
            (batch_id, user_id, trade_date, image_path, sha, dumps_json(extracted.get("raw", extracted))),
        )
        for index, row in enumerate(extracted["rows"]):
            match = matches.get(row["name"], {"status": "BLOCKED", "symbol": None, "candidates": []})
            fingerprint = build_row_fingerprint(row, trade_date)
            duplicate = conn.execute(
                """
                SELECT id FROM trade_import_drafts
                 WHERE user_id=? AND row_fingerprint=? AND batch_id<>?
                 LIMIT 1
                """,
                (user_id, fingerprint, batch_id),
            ).fetchone()
            warnings = []
            symbol = match.get("symbol")
            if not symbol:
                warnings.append("待匹配股票代码")
            if duplicate:
                warnings.append("疑似重复")
            status = "BLOCKED" if not symbol else ("POSSIBLE_DUPLICATE" if duplicate else "DRAFT")
            conn.execute(
                """
                INSERT OR IGNORE INTO trade_import_drafts (
                    batch_id, user_id, row_index, symbol, name, direction, price, quantity,
                    amount, confidence, status, warnings_json, raw_text, row_fingerprint,
                    matched_candidates_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    user_id,
                    index,
                    symbol,
                    row["name"],
                    row["direction"],
                    row["price"],
                    row["quantity"],
                    row["amount"],
                    row["confidence"],
                    status,
                    dumps_json(warnings),
                    row.get("raw_text"),
                    fingerprint,
                    dumps_json(match.get("candidates", [])),
                ),
            )
        conn.commit()
        return _load_batch_with_conn(conn, batch_id, user_id)
    finally:
        conn.close()


def _load_batch(batch_id: str, user_id: int):
    conn = get_connection()
    try:
        return _load_batch_with_conn(conn, batch_id, user_id)
    finally:
        conn.close()


def _load_batch_with_conn(conn, batch_id: str, user_id: int):
    batch = conn.execute(
        "SELECT * FROM trade_import_batches WHERE batch_id=? AND user_id=?",
        (batch_id, user_id),
    ).fetchone()
    if not batch:
        raise HTTPException(404, "导入批次不存在")
    drafts = conn.execute(
        "SELECT * FROM trade_import_drafts WHERE batch_id=? AND user_id=? ORDER BY row_index, id",
        (batch_id, user_id),
    ).fetchall()
    return {
        "batch": _serialize_batch(dict(batch)),
        "drafts": [_serialize_draft(dict(row)) for row in drafts],
        "summary": _summary([dict(row) for row in drafts]),
    }


def _serialize_batch(row: dict) -> dict:
    out = dict(row)
    out.pop("image_path", None)
    out.pop("raw_vision_json", None)
    return out


def _serialize_draft(row: dict) -> dict:
    out = dict(row)
    for key, fallback in (("warnings_json", []), ("matched_candidates_json", [])):
        try:
            out[key.replace("_json", "")] = json.loads(out.get(key) or "[]")
        except json.JSONDecodeError:
            out[key.replace("_json", "")] = fallback
    out["duplicate_ack"] = bool(out.get("duplicate_ack"))
    return out


def _summary(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.get("name") or row.get("symbol") or "UNKNOWN", row.get("direction") or "UNKNOWN")
        item = grouped.setdefault(key, {"name": key[0], "direction": key[1], "count": 0, "quantity": 0, "amount": 0.0})
        item["count"] += 1
        item["quantity"] += row.get("quantity") or 0
        item["amount"] += row.get("amount") or 0
    for item in grouped.values():
        item["avg_price"] = round(item["amount"] / item["quantity"], 3) if item["quantity"] else None
    return list(grouped.values())


def _draft_warnings(row: dict) -> list[str]:
    warnings: list[str] = []
    if not row.get("symbol"):
        warnings.append("待匹配股票代码")
    if row.get("direction") not in {"BUY", "SELL"}:
        warnings.append("买卖方向无效")
    if not row.get("price") or float(row["price"]) <= 0:
        warnings.append("成交均价无效")
    if not row.get("quantity") or int(row["quantity"]) <= 0:
        warnings.append("成交量无效")
    if row.get("symbol"):
        code = parse_symbol(row["symbol"]).code
        if not (code.startswith("688") or (code.startswith("8") and not code.startswith("68"))):
            if int(row.get("quantity") or 0) % 100 != 0:
                warnings.append("A股最小交易单位为100股")
    return warnings


def _status_for_warnings(warnings: list[str], current_status: str, duplicate_ack: int) -> str:
    if warnings:
        return "BLOCKED"
    if current_status == "POSSIBLE_DUPLICATE" and not duplicate_ack:
        return "POSSIBLE_DUPLICATE"
    return "DRAFT"


def _validate_sells(conn, user_id: int, rows: list[dict]):
    sell_by_symbol: dict[str, int] = {}
    for row in rows:
        if row["direction"] == "SELL":
            symbol = to_tencent_symbol(row["symbol"])
            sell_by_symbol[symbol] = sell_by_symbol.get(symbol, 0) + int(row["quantity"])
    for symbol, sell_qty in sell_by_symbol.items():
        pos = conn.execute(
            "SELECT quantity FROM positions WHERE user_id=? AND symbol=?",
            (user_id, symbol),
        ).fetchone()
        held = pos["quantity"] if pos else 0
        if sell_qty > held:
            raise HTTPException(400, f"{symbol} 持仓仅 {held} 股，导入卖出 {sell_qty} 股超出持仓")


class _BytesReader:
    def __init__(self, content: bytes):
        self._content = content
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._content) - self._offset
        start = self._offset
        end = min(len(self._content), start + size)
        self._offset = end
        return self._content[start:end]
