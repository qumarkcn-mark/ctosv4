import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config
from server.services import trade_screenshot_importer as importer
from server.services.trade_screenshot_importer import (
    ScreenshotImportError,
    build_row_fingerprint,
    extract_ths_daily_summary,
    normalize_extraction,
    validate_image_upload,
)


class FakeQwenCompletions:
    def __init__(self):
        self.request = None
        self.requests = []

    async def create(self, **kwargs):
        self.request = kwargs
        self.requests.append(kwargs)

        class Message:
            content = '{"rows":[{"name":"天孚通信","direction":"BUY","price":325.518,"quantity":500,"amount":162759,"confidence":0.91}]}'

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        return Response()


class FakeQwenClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.completions = FakeQwenCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()
        FakeQwenClient.instances.append(self)


class RetryQwenCompletions:
    def __init__(self):
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)

        class Message:
            content = '{"rows":[]}'

        if len(self.requests) == 2:
            Message.content = '{"rows":[{"name":"飞荣达","direction":"BUY","price":36.68,"quantity":2000,"amount":73360,"confidence":0.8,"raw_text":"飞荣达 36.680 2000 卖出 73360.000"}]}'

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        return Response()


class RetryQwenClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.completions = RetryQwenCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()
        RetryQwenClient.instances.append(self)


class InvalidJsonRetryQwenCompletions:
    def __init__(self):
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)

        class Message:
            content = "{rows: ["

        if len(self.requests) == 2:
            Message.content = '{"rows":[{"name":"中国卫星","direction":"BUY","price":106.99,"quantity":2000,"amount":213980,"confidence":0.8}]}'

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        return Response()


class InvalidJsonRetryQwenClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.completions = InvalidJsonRetryQwenCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()
        InvalidJsonRetryQwenClient.instances.append(self)


def test_validate_image_upload_rejects_non_image():
    with pytest.raises(ScreenshotImportError):
        validate_image_upload(b"not-image", "text/plain")


def test_validate_image_upload_accepts_jpeg_alias():
    assert validate_image_upload(b"fake", "image/jpg") == "image/jpeg"


def test_normalize_extraction_parses_ths_rows():
    result = normalize_extraction(
        {
            "rows": [
                {
                    "name": "天孚通信",
                    "direction": "买入",
                    "price": "325.518",
                    "quantity": "500",
                    "amount": "162759.000",
                    "confidence": 0.91,
                    "raw_text": "买 天孚通信 325.518 500 买入 162759.000",
                },
                {
                    "name": "飞荣达",
                    "direction": "SELL",
                    "price": 36.68,
                    "quantity": 2000,
                    "confidence": 0.88,
                },
            ]
        },
        trade_date="2026-05-06",
    )

    assert result["broker"] == "THS"
    assert result["rows"][0]["direction"] == "BUY"
    assert result["rows"][0]["amount"] == 162759.0
    assert result["rows"][1]["direction"] == "SELL"
    assert result["rows"][1]["amount"] == 73360.0


def test_normalize_extraction_recovers_direction_from_amount_or_raw_text():
    result = normalize_extraction(
        {
            "rows": [
                {
                    "name": "飞荣达",
                    "direction": "BUY",
                    "price": "36.680",
                    "quantity": "2000",
                    "amount": "卖出",
                    "confidence": "",
                    "raw_text": "飞荣达 36.680 2000 卖出",
                },
                {
                    "name": "中国卫星",
                    "direction": "UNKNOWN",
                    "price": "106.990",
                    "quantity": "2000",
                    "amount": "",
                    "confidence": "",
                    "raw_text": "中国卫星 106.990 2000 买入",
                },
            ]
        },
        trade_date="2026-05-10",
    )

    assert result["rows"][0]["direction"] == "SELL"
    assert result["rows"][0]["amount"] == 73360.0
    assert result["rows"][1]["direction"] == "BUY"
    assert result["rows"][1]["amount"] == 213980.0


def test_build_row_fingerprint_is_stable_and_sensitive():
    row = {"name": "中国卫星", "direction": "BUY", "price": 106.99, "quantity": 2000, "amount": 213980}
    same = {"name": "中国卫星", "direction": "BUY", "price": "106.990", "quantity": "2000", "amount": "213980.000"}
    other = {**row, "direction": "SELL"}

    assert build_row_fingerprint(row, "2026-05-06") == build_row_fingerprint(same, "2026-05-06")
    assert build_row_fingerprint(row, "2026-05-06") != build_row_fingerprint(other, "2026-05-06")


def test_extract_ths_daily_summary_uses_qwen_ocr(monkeypatch):
    FakeQwenClient.instances = []
    monkeypatch.setattr(config, "QWEN_API_KEY", "sk-qwen")
    monkeypatch.setattr(config, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(config, "QWEN_SCREENSHOT_OCR_MODEL", "qwen-vl-ocr-latest")
    monkeypatch.setattr(importer, "_get_user_qwen_settings", lambda user_id=1: {})
    monkeypatch.setattr(importer, "AsyncOpenAI", FakeQwenClient)

    result = asyncio.run(extract_ths_daily_summary(b"fake-image", "image/png", "2026-05-06", user_id=1))

    client = FakeQwenClient.instances[0]
    request = client.completions.request
    assert client.kwargs["api_key"] == "sk-qwen"
    assert client.kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/"
    assert request["model"] == "qwen-vl-ocr-latest"
    assert request["response_format"] == {"type": "json_object"}
    assert request["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result["rows"][0]["name"] == "天孚通信"


def test_extract_ths_daily_summary_retries_with_fallback_prompt(monkeypatch):
    RetryQwenClient.instances = []
    monkeypatch.setattr(config, "QWEN_API_KEY", "sk-qwen")
    monkeypatch.setattr(config, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(config, "QWEN_SCREENSHOT_OCR_MODEL", "qwen-vl-ocr-latest")
    monkeypatch.setattr(importer, "_get_user_qwen_settings", lambda user_id=1: {})
    monkeypatch.setattr(importer, "AsyncOpenAI", RetryQwenClient)

    result = asyncio.run(extract_ths_daily_summary(b"fake-image", "image/png", "2026-05-10", user_id=1))

    client = RetryQwenClient.instances[0]
    assert len(client.completions.requests) == 2
    assert result["rows"][0]["name"] == "飞荣达"
    assert result["rows"][0]["direction"] == "SELL"


def test_extract_ths_daily_summary_retries_after_invalid_json(monkeypatch):
    InvalidJsonRetryQwenClient.instances = []
    monkeypatch.setattr(config, "QWEN_API_KEY", "sk-qwen")
    monkeypatch.setattr(config, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(config, "QWEN_SCREENSHOT_OCR_MODEL", "qwen-vl-ocr-latest")
    monkeypatch.setattr(importer, "_get_user_qwen_settings", lambda user_id=1: {})
    monkeypatch.setattr(importer, "AsyncOpenAI", InvalidJsonRetryQwenClient)

    result = asyncio.run(extract_ths_daily_summary(b"fake-image", "image/png", "2026-05-10", user_id=1))

    client = InvalidJsonRetryQwenClient.instances[0]
    assert len(client.completions.requests) == 2
    assert result["rows"][0]["name"] == "中国卫星"


def test_extract_ths_daily_summary_rejects_untrusted_qwen_base_url(monkeypatch):
    FakeQwenClient.instances = []
    monkeypatch.setattr(config, "QWEN_API_KEY", "sk-qwen")
    monkeypatch.setattr(config, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(config, "QWEN_SCREENSHOT_OCR_MODEL", "qwen-vl-ocr-latest")
    monkeypatch.setattr(importer, "_get_user_qwen_settings", lambda user_id=1: {"qwen_base_url": "https://evil.example/v1"})
    monkeypatch.setattr(importer, "AsyncOpenAI", FakeQwenClient)

    asyncio.run(extract_ths_daily_summary(b"fake-image", "image/png", "2026-05-06", user_id=1))

    assert FakeQwenClient.instances[0].kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/"
