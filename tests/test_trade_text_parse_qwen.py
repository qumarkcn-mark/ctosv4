import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config
from server.services import llm_service
from server.services.llm_service import LLMService


class FakeQwenCompletions:
    response_content = '{"direction":"BUY","name":"贵州茅台","symbol_hint":"sh600519","price":1780.0,"quantity":100,"confidence":0.95}'

    def __init__(self):
        self.request = None

    async def create(self, **kwargs):
        self.request = kwargs

        class Message:
            content = FakeQwenCompletions.response_content

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


def test_parse_trade_from_text_uses_qwen(monkeypatch):
    FakeQwenClient.instances = []
    FakeQwenCompletions.response_content = '{"direction":"BUY","name":"贵州茅台","symbol_hint":"sh600519","price":1780.0,"quantity":100,"confidence":0.95}'
    monkeypatch.setattr(llm_service, "AsyncOpenAI", FakeQwenClient)
    monkeypatch.setattr(
        LLMService,
        "_get_user_qwen_settings",
        lambda self, user_id=1: {
            "qwen_api_key": "sk-qwen",
            "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen_trade_parse_model": "qwen-flash",
        },
    )

    result = asyncio.run(LLMService().parse_trade_from_text("买了100股茅台1780块", user_id=1))

    client = FakeQwenClient.instances[0]
    request = client.completions.request
    assert client.kwargs["api_key"] == "sk-qwen"
    assert client.kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/"
    assert request["model"] == "qwen-flash"
    assert request["response_format"] == {"type": "json_object"}
    assert "JSON" in request["messages"][0]["content"]
    assert result["direction"] == "BUY"
    assert result["quantity"] == 100


def test_parse_trade_from_text_rejects_untrusted_qwen_base_url(monkeypatch):
    FakeQwenClient.instances = []
    FakeQwenCompletions.response_content = '{"direction":"BUY","name":"贵州茅台","symbol_hint":"sh600519","price":1780.0,"quantity":100,"confidence":0.95}'
    monkeypatch.setattr(llm_service, "AsyncOpenAI", FakeQwenClient)
    monkeypatch.setattr(config, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(
        LLMService,
        "_get_user_qwen_settings",
        lambda self, user_id=1: {
            "qwen_api_key": "sk-qwen",
            "qwen_base_url": "https://evil.example/v1",
            "qwen_trade_parse_model": "qwen-flash",
        },
    )

    asyncio.run(LLMService().parse_trade_from_text("买了100股茅台1780块", user_id=1))

    assert FakeQwenClient.instances[0].kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/"


def test_parse_trade_from_text_repairs_qwen_json_drift(monkeypatch):
    FakeQwenClient.instances = []
    FakeQwenCompletions.response_content = """```json
    {"direction":"SELL","name":"飞荣达","price":36.68,"quantity":200,"confidence":95%,}
    ```"""
    monkeypatch.setattr(llm_service, "AsyncOpenAI", FakeQwenClient)
    monkeypatch.setattr(
        LLMService,
        "_get_user_qwen_settings",
        lambda self, user_id=1: {
            "qwen_api_key": "sk-qwen",
            "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen_trade_parse_model": "qwen-flash",
        },
    )

    result = asyncio.run(LLMService().parse_trade_from_text("卖出2手飞荣达36.68", user_id=1))

    assert result["direction"] == "SELL"
    assert result["quantity"] == 200
    assert result["confidence"] == 0.95


def test_parse_trade_from_text_falls_back_to_regex_without_qwen(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setattr(config, "QWEN_API_KEY", "")
    monkeypatch.setattr(LLMService, "_get_user_qwen_settings", lambda self, user_id=1: {})

    result = asyncio.run(LLMService().parse_trade_from_text("卖出2手飞荣达36.68", user_id=1))

    assert result["direction"] == "SELL"
    assert result["quantity"] == 200
    assert result["confidence"] == 0.4
