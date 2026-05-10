from __future__ import annotations

"""同花顺成交截图识别服务。

只负责把图片变成可信度可审查的交易草稿数据；不写入 trades。
"""

import base64
import hashlib
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from server import config
from server.services.llm_service import _loads_lenient_json_object, _safe_qwen_base_url

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class ScreenshotImportError(ValueError):
    """截图导入输入或模型输出不可用。"""


def _get_user_qwen_settings(user_id: int = 1) -> dict[str, Any]:
    from server.db.database import get_connection

    try:
        conn = get_connection()
        try:
            row = conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
            if row and row["settings_json"]:
                loaded = json.loads(row["settings_json"])
                return loaded if isinstance(loaded, dict) else {}
        finally:
            conn.close()
    except Exception:
        logger.debug("读取用户 Qwen OCR 设置失败", exc_info=True)
    return {}


def validate_image_upload(content: bytes, content_type: str | None) -> str:
    """校验图片类型和大小，返回规范 MIME。"""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ScreenshotImportError("仅支持 JPG/PNG/WebP 截图")
    if not content:
        raise ScreenshotImportError("截图文件为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise ScreenshotImportError("截图超过 10MB，请裁剪后再上传")
    return mime


def image_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_row_fingerprint(row: dict[str, Any], trade_date: str, broker: str = "THS") -> str:
    """生成稳定行指纹。重复提示是 advisory，不能静默吞行。"""
    parts = [
        broker,
        trade_date,
        str(row.get("name") or "").strip(),
        str(row.get("direction") or "").strip().upper(),
        _normalize_decimal(row.get("price")),
        str(int(row.get("quantity") or 0)),
        _normalize_decimal(row.get("amount")),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


async def extract_ths_daily_summary(
    image_bytes: bytes,
    mime_type: str,
    trade_date: str,
    user_id: int = 1,
) -> dict[str, Any]:
    """调用视觉模型识别同花顺当日成交汇总截图。"""
    settings = _get_user_qwen_settings(user_id)
    api_key = settings.get("qwen_api_key") or config.QWEN_API_KEY
    if not api_key:
        raise ScreenshotImportError("未配置 QWEN_API_KEY，无法识别截图")
    base_url = _safe_qwen_base_url(settings.get("qwen_base_url"))
    model_name = (
        settings.get("qwen_screenshot_ocr_model")
        or settings.get("qwen_default_model")
        or config.QWEN_SCREENSHOT_OCR_MODEL
    )

    payload = base64.b64encode(image_bytes).decode("ascii")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    prompt = f"""
你是 CT-OS 交易账本的截图识别器。图片来自同花顺 App 的“当日成交”页面，且可能开启“成交汇总功能”。

只提取表格里的成交汇总行，不要提取顶部导航、账户号、状态栏。
输出严格 JSON object，不要 markdown。

字段要求：
- broker 固定为 THS
- import_type 固定为 DAILY_SUMMARY_SCREENSHOT
- trade_date 固定为 {trade_date}
- rows 是数组，每行包含 name, direction, price, quantity, amount, confidence, raw_text
- direction 只能是 BUY 或 SELL。红色/“买/买入”是 BUY，蓝色/“卖/卖出”是 SELL
- price/amount 用数字，quantity 用整数股数
- 如果某行字段不完整，不要猜，降低 confidence 并在 raw_text 保留原始文本

这不是交易建议，只是事实抽取。
"""
    fallback_prompt = f"""
你是 CT-OS 交易账本的 OCR。请读取图片中的同花顺“当日成交”表格。
只输出 JSON object，不要 markdown。格式：
{{"rows":[{{"name":"股票名称","direction":"BUY或SELL","price":成交均价数字,"quantity":成交数量整数,"amount":成交金额数字,"confidence":0.8,"raw_text":"原始行文本"}}]}}
规则：红色买/买入=BUY，蓝色卖/卖出=SELL。只提取股票名称、成交均价、成交数量、成交金额、买入/卖出这些表格行。
如果截图外层还包含 CT-OS 页面或手机预览，也要只识别手机预览里的成交表格。
trade_date={trade_date}。
"""

    last_error: Exception | None = None
    for current_prompt in (prompt, fallback_prompt):
        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": current_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{payload}",
                            },
                        },
                    ],
                }
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        try:
            raw_content = response.choices[0].message.content or "{}"
            parsed = _loads_lenient_json_object(raw_content)
            return normalize_extraction(parsed, trade_date=trade_date)
        except Exception as exc:
            last_error = exc
            logger.warning("Qwen OCR 未生成有效成交行，尝试备用提示词: %s", exc)
    raise ScreenshotImportError(f"未识别到有效成交行: {last_error}") if last_error else ScreenshotImportError("未识别到有效成交行")


def normalize_extraction(parsed: dict[str, Any], trade_date: str) -> dict[str, Any]:
    """清洗模型输出，保证 API 层只处理结构化草稿。"""
    rows = parsed.get("rows")
    if not isinstance(rows, list):
        raise ScreenshotImportError("识别结果缺少 rows")

    normalized_rows: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        direction = _normalize_row_direction(item)
        price = _to_float(item.get("price"))
        quantity = _to_int(item.get("quantity"))
        amount = _to_float(item.get("amount"))
        confidence = max(0.0, min(1.0, _to_float(item.get("confidence"), default=0.0) or 0.0))
        if not name or direction not in {"BUY", "SELL"} or not price or not quantity:
            continue
        if amount is None:
            amount = round(price * quantity, 3)
        normalized_rows.append({
            "name": name,
            "direction": direction,
            "price": price,
            "quantity": quantity,
            "amount": amount,
            "confidence": confidence,
            "raw_text": str(item.get("raw_text") or item.get("raw") or ""),
        })

    if not normalized_rows:
        raise ScreenshotImportError("未识别到有效成交行")

    return {
        "broker": "THS",
        "import_type": "DAILY_SUMMARY_SCREENSHOT",
        "trade_date": trade_date,
        "rows": normalized_rows,
        "raw": parsed,
    }


def _normalize_direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "B"} or "买" in text:
        return "BUY"
    if text in {"SELL", "S"} or "卖" in text:
        return "SELL"
    return text


def _normalize_row_direction(item: dict[str, Any]) -> str:
    """Qwen OCR 有时会把买卖方向识别进 amount/raw_text，需要二次兜底。"""
    candidates = (
        item.get("direction"),
        item.get("side"),
        item.get("action"),
        item.get("amount"),
        item.get("raw_text"),
        item.get("raw"),
    )
    normalized = [_normalize_direction(value) for value in candidates]
    for value in normalized[1:]:
        if value in {"BUY", "SELL"}:
            return value
    return normalized[0] if normalized else ""


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _to_int(value: Any) -> int | None:
    num = _to_float(value)
    return int(num) if num is not None else None


def _normalize_decimal(value: Any) -> str:
    num = _to_float(value, default=0.0) or 0.0
    return f"{num:.6f}".rstrip("0").rstrip(".")


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
