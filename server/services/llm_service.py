import os
import json
from pydantic import BaseModel, Field
from typing import List, Optional
from openai import AsyncOpenAI
import logging
from server import config

logger = logging.getLogger(__name__)

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

class AccountStatus(BaseModel):
    is_holding: bool
    cost: Optional[float] = None
    pnl_percentage: Optional[float] = None

class PrePlan(BaseModel):
    plan_name: str
    trigger: str
    deduction: str
    machine_action: str
    color: str

class RadarInferenceResult(BaseModel):
    diagnosis: str
    account_status: Optional[AccountStatus] = None
    pre_plans: List[PrePlan] = Field(default=[])
    core_defense: Optional[str] = None
    market_context_verdict: Optional[str] = None

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

    async def infer_radar_deduction(self, system_prompt: str, context_json: str) -> dict:
        """
        Sends the TRadar matrix snapshot to generate deductuon narratives.
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
                    {"role": "user", "content": f"Matrix Context:\n{context_json}"}
                ],
                temperature=0.4,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            parsed_data = json.loads(raw_content)
            validated_data = RadarInferenceResult(**parsed_data)
            return validated_data.model_dump()
        except Exception as e:
            logger.error(f"Radar LLM Inference failed: {e}")
            return {
                "diagnosis": f"推演引擎异常: {str(e)[:50]}",
                "account_status": {"is_holding": False},
                "pre_plans": [
                    {
                        "plan_name": "系统故障",
                        "trigger": "后台报错",
                        "deduction": "请检查终端日志或大模型API Key",
                        "machine_action": "等待修复",
                        "color": "🟡"
                    }
                ],
                "core_defense": "N/A",
                "market_context_verdict": "未知"
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

    async def infer_ai_native_radar(self, system_prompt: str, context_json: str, *, user_id: int = 1) -> dict:
        """AI Native Radar 影子系统推理。调用方负责 verifier 和 fallback。"""
        from server.db.database import get_connection

        api_key = None
        try:
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
                if row and row["settings_json"]:
                    settings = json.loads(row["settings_json"])
                    api_key = settings.get("deepseek_api_key")
            finally:
                db_conn.close()
        except Exception:
            logger.debug("读取用户 DeepSeek API Key 失败", exc_info=True)

        if not api_key:
            api_key = os.environ.get("LLM_API_KEY", "dummy_key_replace_in_prod")

        client = AsyncOpenAI(api_key=api_key, base_url=self.base_url)
        response = await client.chat.completions.create(
            model=config.AI_NATIVE_RADAR_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"AI Native Radar Context:\n{context_json}"},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        raw_content = response.choices[0].message.content
        return json.loads(raw_content)

    async def parse_trade_from_text(self, text: str) -> dict:
        """
        从自然语言中提取交易信息（语音录入核心）。
        使用 DeepSeek V3，temperature=0.1 确保精度。
        失败时降级到正则解析。
        """
        import re
        from server.db.database import get_connection

        # ── 获取 API Key ──
        api_key = None
        try:
            db_conn = get_connection()
            try:
                row = db_conn.execute("SELECT settings_json FROM users WHERE id=1").fetchone()
                if row and row["settings_json"]:
                    settings = json.loads(row["settings_json"])
                    api_key = settings.get("deepseek_api_key")
            finally:
                db_conn.close()
        except Exception:
            pass

        if not api_key:
            api_key = os.environ.get("LLM_API_KEY")

        # ── LLM 解析 ──
        if api_key and api_key != "dummy_key_replace_in_prod":
            try:
                client = AsyncOpenAI(api_key=api_key, base_url=self.base_url)
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
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
                raw = json.loads(response.choices[0].message.content)
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
