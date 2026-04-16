import os
import json
from pydantic import BaseModel, Field
from typing import List, Optional
from openai import AsyncOpenAI
import logging

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

class RadarInferenceResult(BaseModel):
    thinking: List[ThinkingStep] = Field(..., description="逐级别看盘思维过程")
    position: str = Field(..., description="当前定位一句话")
    # V4.5: classifications 替代 decisions
    classifications: List[ClassificationBranch] = Field(default=[], description="完全分类")
    watch_prices: List[WatchPrice] = Field(default=[], description="关键价位")
    interval_nesting: str = Field(default="无", description="区间套状态")
    veto: Optional[str] = Field(default=None, description="大级别否决")
    # 保留兼容
    decisions: List[DecisionBranch] = Field(default=[], description="条件决策树(旧)")
    red_line: str = Field(default="N/A", description="物理止损红线")

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
                "thinking": [
                    {"level": "week", "icon": "🔭", "say": "⚠️ 推演引擎离线，无法读取周线"},
                    {"level": "day", "icon": "📊", "say": f"系统异常: {str(e)[:50]}"},
                    {"level": "m30", "icon": "🔍", "say": "等待引擎恢复后重试"},
                    {"level": "m5", "icon": "🎯", "say": "当前数据仅供参考"}
                ],
                "position": "推演引擎离线",
                "decisions": [
                    {"if": "引擎恢复", "then": "重新推演", "type": "wait"}
                ],
                "red_line": "N/A"
            }
