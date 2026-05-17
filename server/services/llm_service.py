import os
import json
import re
from dataclasses import dataclass
from pydantic import BaseModel, Field
from typing import List, Optional
from openai import AsyncOpenAI
import logging
from server import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIModelRoute:
    model_name: str = ""
    thinking_enabled: bool = False
    reasoning_effort: str = "high"
    timeout_seconds: float = 150
    max_tokens: int = 4096


def _safe_qwen_base_url(candidate: Optional[str]) -> str:
    """只允许官方 DashScope OpenAI-compatible endpoint，避免用户设置劫持 API key。"""
    default = config.QWEN_BASE_URL.rstrip("/")
    normalized = str(candidate or default).strip().rstrip("/")
    allowed = set(config.QWEN_ALLOWED_BASE_URLS)
    if normalized not in allowed:
        logger.warning("忽略不受信任的 Qwen Base URL: %s", normalized)
        normalized = default if default in allowed else "https://dashscope.aliyuncs.com/compatible-mode/v1"
    return normalized + "/"


def _loads_lenient_json_object(raw_content: str) -> dict:
    """Parse model JSON with small repairs for common response_format drift."""
    raw_content = raw_content or ""
    try:
        return json.loads(raw_content, object_pairs_hook=_merge_duplicate_json_pairs)
    except json.JSONDecodeError:
        pass

    content = raw_content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]

    repairs = [
        content,
        re.sub(r",(\s*[}\]])", r"\1", content),
    ]
    repairs.append(re.sub(r":\s*([+-]?\d+(?:\.\d+)?)\s*%", r': "\1%"', repairs[-1]))
    repairs.append(repairs[-1].replace(": None", ": null").replace(": True", ": true").replace(": False", ": false"))

    last_error: Optional[Exception] = None
    for candidate in repairs:
        try:
            parsed = json.loads(candidate, object_pairs_hook=_merge_duplicate_json_pairs)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError as exc:
            last_error = exc
    raise last_error or ValueError("Unable to parse model JSON")


def _message_content_text(message) -> str:
    """Extract final assistant content, excluding provider reasoning fields."""
    content = getattr(message, "content", None) or ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or getattr(item, "content", "") or ""))
        content = "".join(parts)
    if str(content).strip():
        return str(content)
    return ""


def _message_reasoning_text(message) -> str:
    """Extract provider reasoning text for telemetry/debug, never for JSON parsing."""
    for attr in ("reasoning_content", "reasoning", "text"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _merge_duplicate_json_pairs(pairs: list) -> dict:
    """Keep useful model output when JSON contains duplicate keys such as rows then rows: []."""
    merged = {}
    for key, value in pairs:
        if key in merged and _has_content(merged[key]) and not _has_content(value):
            continue
        merged[key] = value
    return merged


def _has_content(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple, set)):
        return bool(value)
    return True


class ScenarioNode(BaseModel):
    type: str = Field(..., description="Scenario type identifier e.g. right_side_major_wave")
    name: str = Field(..., description="Human readable name e.g. 右侧主升浪")
    probability: float = Field(..., description="Probability of this scenario happening (0-100)")
    price_target_upper: float = Field(..., description="Upper price target boundary")
    price_target_lower: float = Field(..., description="Lower price target boundary")
    periods: int = Field(..., description="Estimated K-line periods for this scenario")
    action_rule: str = Field(..., description="Combat action rule according to Commander")

class CommanderInferenceResult(BaseModel):
    reasoning: str = Field(..., description="Chain of thought reasoning linking structural context to scenarios.")
    window_d: str = Field(..., description="Window D veto check status")
    window_c: str = Field(..., description="Window C macro check status")
    window_a: str = Field(..., description="Window A point mapping status")
    window_b: str = Field(..., description="Window B stop-loss floor line")
    scenarios: List[ScenarioNode] = Field(..., description="Exactly 3 scenarios")

class Scenario(BaseModel):
    trigger: str = Field(..., description="触发条件")
    action: str = Field(..., description="具体操作")
    position: str = Field(..., description="仓位建议")
    stop_loss: str = Field(..., description="止损价或止损条件")

class KeyLevels(BaseModel):
    zg: float = Field(..., description="中枢上沿")
    zd: float = Field(..., description="中枢下沿")
    gg: float = Field(..., description="最高点")
    dd: float = Field(..., description="最低点")
    ex_support: float = Field(..., description="短期支撑")
    ex_pressure: float = Field(..., description="短期压力")

# V4.3 拟人化推演格式
class ThinkingStep(BaseModel):
    level: str = Field(..., description="级别: week/day/m30/m5")
    icon: str = Field(..., description="图标 emoji")
    say: str = Field(..., description="自然语言描述")

class DecisionBranch(BaseModel):
    """if/then 决策分支"""
    if_: str = Field(..., alias="if", description="价格条件")
    then: str = Field(..., description="对应操作")
    type: str = Field(..., description="buy/sell/wait")

    model_config = {"populate_by_name": True}

class ClassificationBranch(BaseModel):
    """完全分类分支"""
    id: str = Field(..., description="分类ID: A/B/C")
    name: str = Field(..., description="分类名称")
    condition: str = Field(..., description="触发条件")
    action: str = Field(..., description="操作建议")
    stopLoss: Optional[str] = Field(None, description="止损价位和原因")
    is_current: bool = Field(False, description="是否为当前所在分类")

    model_config = {"populate_by_name": True}

class WatchPrice(BaseModel):
    price: float = Field(..., description="价位")
    role: str = Field(..., description="价位角色")

class TradeParseResult(BaseModel):
    """语音/文本交易解析结果"""
    direction: str = Field(..., description="BUY 或 SELL")
    name: Optional[str] = Field(None, description="股票名称，如 贵州茅台")
    symbol_hint: Optional[str] = Field(None, description="股票代码提示，如 sh600519（可能不准）")
    price: Optional[float] = Field(None, description="成交价格")
    quantity: Optional[int] = Field(None, description="成交数量（股）")
    confidence: float = Field(0.0, description="解析置信度 0-1")
    raw_text: str = Field("", description="原始输入文本")


class LLMService:
    def __init__(self):
        self.base_url = "https://api.deepseek.com"

    def _get_user_deepseek_api_key(self, user_id: int = 1) -> str:
        from server.db.database import get_connection

        api_key = ""
        try:
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
                if row and row["settings_json"]:
                    settings = json.loads(row["settings_json"])
                    api_key = settings.get("deepseek_api_key") or ""
            finally:
                db_conn.close()
        except Exception:
            logger.debug("读取用户 DeepSeek API Key 失败", exc_info=True)
        return api_key or os.environ.get("LLM_API_KEY", "dummy_key_replace_in_prod")

    def _get_user_ai_native_provider_settings(self, user_id: int = 1) -> dict:
        from server.db.database import get_connection

        settings = {}
        try:
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
                if row and row["settings_json"]:
                    loaded = json.loads(row["settings_json"])
                    settings = loaded if isinstance(loaded, dict) else {}
            finally:
                db_conn.close()
        except Exception:
            logger.debug("读取用户 AI Native 模型设置失败", exc_info=True)
        return settings

    def _ai_native_client_config(self, user_id: int, model_route=None) -> dict:
        settings = self._get_user_ai_native_provider_settings(user_id)
        provider = str(settings.get("ai_native_provider") or "deepseek").strip().lower()
        if provider == "gemini":
            model_name = str(settings.get("gemini_model") or config.GEMINI_MODEL or "gemini-2.5-pro").strip()
            if model_route and model_route.model_name:
                model_name = model_route.model_name
            return {
                "provider": "gemini",
                "api_key": settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY,
                "base_url": (settings.get("gemini_base_url") or os.environ.get("GEMINI_BASE_URL") or config.GEMINI_BASE_URL).rstrip("/") + "/",
                "model_name": model_name,
                "thinking_enabled": False,
                "reasoning_effort": "high",
            }

        model_name = config.AI_NATIVE_MODEL
        thinking_enabled = config.AI_NATIVE_THINKING_ENABLED
        reasoning_effort = config.AI_NATIVE_REASONING_EFFORT
        if settings.get("ai_native_model"):
            model_name = settings.get("ai_native_model")
        if isinstance(settings.get("ai_native_thinking_enabled"), bool):
            thinking_enabled = settings["ai_native_thinking_enabled"]
        reasoning_effort = settings.get("ai_native_reasoning_effort") or reasoning_effort
        if model_route:
            model_name = model_route.model_name or model_name
            thinking_enabled = model_route.thinking_enabled
            reasoning_effort = model_route.reasoning_effort
        return {
            "provider": "deepseek",
            "api_key": settings.get("deepseek_api_key") or os.environ.get("LLM_API_KEY", "dummy_key_replace_in_prod"),
            "base_url": self.base_url,
            "model_name": model_name,
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
        }
    
    async def infer_czsc_scenarios(self, system_prompt: str, context_json: str) -> dict:
        """
        Sends the CZSC snapshot to DeepSeek to generate the exact 3 scenarios.
        Uses DeepSeek V3 (deepseek-chat).
        """
        from server.db.database import get_connection
        try:
            # 1. First try to get it from users.settings_json
            api_key = None
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id=1").fetchone()
                if row and row["settings_json"]:
                    settings = json.loads(row["settings_json"])
                    api_key = settings.get("deepseek_api_key")
            finally:
                db_conn.close()
                
            # 2. Fallback to env var
            if not api_key:
                api_key = os.environ.get("LLM_API_KEY")
                
            if not api_key:
                api_key = "dummy_key_replace_in_prod"
            
            # Recreate client with the latest key
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.base_url
            )
            
            response = await client.chat.completions.create(
                model="deepseek-chat", # deepseek-chat represents V3
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Here is the CZSC Context:\n{context_json}"}
                ],
                temperature=0.3, # Low temperature for analytical consistency
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            # Validate with pydantic
            parsed_data = json.loads(raw_content)
            validated_data = CommanderInferenceResult(**parsed_data)
            return validated_data.model_dump()
        except Exception as e:
            logger.error(f"LLM Inference failed: {e}")
            # Fallback mock for UI robustness when API key is missing
            return {
                "reasoning": f"Inference engine off-line: {e}",
                "window_d": "未知",
                "window_c": "未知",
                "window_a": "未知",
                "window_b": "未知",
                "scenarios": [
                    {"type": "mock", "name": "API Offline (Mock Up)", "probability": 33, "price_target_upper": 1, "price_target_lower": 0, "periods": 10, "action_rule": "-"},
                    {"type": "mock", "name": "API Offline (Mock Osc)", "probability": 33, "price_target_upper": 1, "price_target_lower": 0, "periods": 10, "action_rule": "-"},
                    {"type": "mock", "name": "API Offline (Mock Down)", "probability": 34, "price_target_upper": 1, "price_target_lower": 0, "periods": 10, "action_rule": "-"}
                ]
            }

    async def infer_portfolio_strategy(self, system_prompt: str, context_json: str) -> str:
        """
        Sends the portfolio summary to generate a markdown strategy report.
        """
        from server.db.database import get_connection
        try:
            api_key = None
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id=1").fetchone()
                if row and row["settings_json"]:
                    settings = json.loads(row["settings_json"])
                    api_key = settings.get("deepseek_api_key")
            finally:
                db_conn.close()
                
            if not api_key:
                api_key = os.environ.get("LLM_API_KEY", "dummy_key_replace_in_prod")
            
            client = AsyncOpenAI(api_key=api_key, base_url=self.base_url)
            
            response = await client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Account Context:\n{context_json}"}
                ],
                temperature=0.6
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Portfolio LLM Inference failed: {e}")
            return f"❌ 生成全局战略失败，请检查网络或大模型 API Key。错误详情: {e}"

    async def infer_ai_native_json(self, system_prompt: str, context_json: str, *, user_id: int = 1, model_route=None) -> dict:
        """AI Native JSON 推理。调用方负责结构上下文和安全边界。"""
        from server.db.database import get_connection

        api_key = None
        model_name = config.AI_NATIVE_MODEL
        thinking_enabled = config.AI_NATIVE_THINKING_ENABLED
        reasoning_effort = config.AI_NATIVE_REASONING_EFFORT
        try:
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
                if row and row["settings_json"]:
                    settings = json.loads(row["settings_json"])
                    api_key = settings.get("deepseek_api_key")
                    model_name = settings.get("ai_native_model") or model_name
                    if isinstance(settings.get("ai_native_thinking_enabled"), bool):
                        thinking_enabled = settings["ai_native_thinking_enabled"]
                    reasoning_effort = settings.get("ai_native_reasoning_effort") or reasoning_effort
            finally:
                db_conn.close()
        except Exception:
            logger.debug("读取用户 DeepSeek API Key 失败", exc_info=True)

        if not api_key:
            api_key = os.environ.get("LLM_API_KEY", "dummy_key_replace_in_prod")

        if model_route:
            model_name = model_route.model_name or model_name
            thinking_enabled = model_route.thinking_enabled
            reasoning_effort = model_route.reasoning_effort
            timeout = model_route.timeout_seconds
            max_tokens = model_route.max_tokens
        else:
            timeout = config.AI_NATIVE_LLM_TIMEOUT
            max_tokens = config.AI_NATIVE_MAX_TOKENS

        client = AsyncOpenAI(api_key=api_key, base_url=self.base_url, timeout=timeout)
        request = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"AI Native Context:\n{context_json}"},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
        }
        if thinking_enabled:
            request["reasoning_effort"] = reasoning_effort if reasoning_effort in {"high", "max"} else "high"
            request["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            request["temperature"] = 0.3
            request["extra_body"] = {"thinking": {"type": "disabled"}}

        response = await client.chat.completions.create(**request)
        message = response.choices[0].message
        raw_content = _message_content_text(message)
        try:
            return _loads_lenient_json_object(raw_content)
        except Exception as first_exc:
            if not thinking_enabled:
                raise
            reasoning_text = _message_reasoning_text(message)
            logger.warning(
                "AI Native JSON content parse failed with thinking enabled; retrying without thinking: %s; content_len=%s reasoning_len=%s",
                first_exc,
                len(raw_content or ""),
                len(reasoning_text or ""),
            )
            retry_request = dict(request)
            retry_request.pop("reasoning_effort", None)
            retry_request["temperature"] = 0.3
            retry_request["extra_body"] = {"thinking": {"type": "disabled"}}
            retry_response = await client.chat.completions.create(**retry_request)
            retry_content = _message_content_text(retry_response.choices[0].message)
            return _loads_lenient_json_object(retry_content)

    async def infer_ai_native_markdown(self, system_prompt: str, context_json: str, *, user_id: int = 1, model_route=None) -> str:
        """AI Native Markdown 推演。调用方负责语义过滤和确定性门禁。"""
        client_config = self._ai_native_client_config(user_id, model_route=model_route)
        api_key = client_config["api_key"]
        model_name = client_config["model_name"]
        timeout = config.AI_NATIVE_LLM_TIMEOUT
        max_tokens = config.AI_NATIVE_MAX_TOKENS
        thinking_enabled = client_config["thinking_enabled"]
        reasoning_effort = client_config["reasoning_effort"]

        if model_route:
            timeout = model_route.timeout_seconds
            max_tokens = model_route.max_tokens

        if not api_key:
            raise RuntimeError(f"{client_config['provider']} API Key 未配置")

        client = AsyncOpenAI(api_key=api_key, base_url=client_config["base_url"], timeout=timeout)
        request = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context_json},
            ],
            "max_tokens": max_tokens,
        }
        if client_config["provider"] == "gemini":
            request["temperature"] = 0.3
        elif thinking_enabled:
            request["reasoning_effort"] = reasoning_effort if reasoning_effort in {"high", "max"} else "high"
            request["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            request["temperature"] = 0.3
            request["extra_body"] = {"thinking": {"type": "disabled"}}

        response = await client.chat.completions.create(**request)
        return response.choices[0].message.content or ""

    def _get_user_qwen_settings(self, user_id: int = 1) -> dict:
        from server.db.database import get_connection

        try:
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
                if row and row["settings_json"]:
                    loaded = json.loads(row["settings_json"])
                    return loaded if isinstance(loaded, dict) else {}
            finally:
                db_conn.close()
        except Exception:
            logger.debug("读取用户 Qwen API 设置失败", exc_info=True)
        return {}

    async def parse_trade_from_text(self, text: str, user_id: int = 1) -> dict:
        """
        从自然语言中提取交易信息（语音录入核心）。
        使用 Qwen 做事实抽取，temperature=0.1 确保精度。
        失败时降级到正则解析。
        """
        import re

        settings = self._get_user_qwen_settings(user_id)
        api_key = settings.get("qwen_api_key") or os.environ.get("QWEN_API_KEY") or config.QWEN_API_KEY
        base_url = _safe_qwen_base_url(settings.get("qwen_base_url") or os.environ.get("QWEN_BASE_URL"))
        model_name = (
            settings.get("qwen_trade_parse_model")
            or settings.get("qwen_default_model")
            or os.environ.get("QWEN_TRADE_PARSE_MODEL")
            or config.QWEN_TRADE_PARSE_MODEL
        )

        # ── LLM 解析 ──
        if api_key and api_key != "dummy_key_replace_in_prod":
            try:
                client = AsyncOpenAI(api_key=api_key, base_url=base_url)
                system_prompt = """你是A股交易记录解析助手。从用户输入的自然语言中提取交易信息，返回JSON。
规则：
- direction: 买/买入/B/buy → "BUY"；卖/卖出/S/sell → "SELL"
- name: 股票名称（如 贵州茅台、宁德时代）
- symbol_hint: 若能识别股票代码则填写（如 sh600519），否则 null
- price: 成交价格（元/股），若无则 null
- quantity: 成交股数（注意：A股最小100股，若用户说"1手"=100股）
- confidence: 解析置信度 0.0-1.0

只返回JSON，不要解释。示例：
输入: "买了100股茅台1780块"
输出: {"direction":"BUY","name":"贵州茅台","symbol_hint":"sh600519","price":1780.0,"quantity":100,"confidence":0.95}"""

                response = await client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
                raw = _normalize_trade_parse_payload(_loads_lenient_json_object(response.choices[0].message.content or "{}"))
                result = TradeParseResult(raw_text=text, **raw)
                return result.model_dump()
            except Exception as e:
                logger.warning(f"LLM trade parse failed, falling back to regex: {e}")

        # ── 正则降级 ──
        result = {
            "direction": "BUY",
            "name": None,
            "symbol_hint": None,
            "price": None,
            "quantity": None,
            "confidence": 0.4,
            "raw_text": text,
        }
        # 判断方向
        if re.search(r"卖|卖出|sell|SELL", text, re.IGNORECASE):
            result["direction"] = "SELL"
        # 提取数量（N股 / N手）
        qty_match = re.search(r"(\d+)\s*(?:股|手|shares?)", text)
        if qty_match:
            qty = int(qty_match.group(1))
            if "手" in text[qty_match.start():qty_match.end()]:
                qty *= 100
            result["quantity"] = qty
        # 提取价格（N元/N块/¥N/@N）
        price_match = re.search(r"(?:@|¥|价格?|元|块|成本)?(\d+(?:\.\d+)?)\s*(?:元|块|¥)?", text)
        if price_match:
            result["price"] = float(price_match.group(1))
        return result


def _normalize_trade_parse_payload(raw: dict) -> dict:
    payload = dict(raw or {})
    confidence = payload.get("confidence")
    if isinstance(confidence, str):
        stripped = confidence.strip()
        if stripped.endswith("%"):
            try:
                payload["confidence"] = max(0.0, min(1.0, float(stripped[:-1].strip()) / 100))
            except ValueError:
                payload["confidence"] = 0.0
    elif isinstance(confidence, (int, float)) and confidence > 1:
        payload["confidence"] = max(0.0, min(1.0, float(confidence) / 100))
    return payload
