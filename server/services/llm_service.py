import os
import json
from pydantic import BaseModel, Field
from typing import List
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

class RadarInferenceResult(BaseModel):
    summary: str = Field(..., description="一句高度提炼的核心结论")
    deduction_process: List[str] = Field(..., description="分段的拆解剖析流")

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
                "summary": f"⚠️ 雷达核心损坏: {e}",
                "deduction_process": [
                    "推演系统离线，无法获取到有效推演回应。",
                    "可能原因：API Key 错误、余额不足，或大模型要求格式异常。"
                ]
            }
