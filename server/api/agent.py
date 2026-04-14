import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import traceback

from server.services.chan_detail_service import get_chan_detail
from server.services.llm_service import LLMService
from server.prompts.czsc_agent import CZSC_SYSTEM_PROMPT
from chan_engine.phantom import generate_phantom_klines

logger = logging.getLogger("AgentAPI")
router = APIRouter()

_llm_service = LLMService()

class InferenceRequest(BaseModel):
    symbol: str

@router.post("/infer_scenarios")
async def infer_scenarios(request: InferenceRequest):
    """
    1. Fetch recent CZSC structures for 30M and 5M levels using V4 chan_detail_service.
    2. Package into a clean JSON digest.
    3. Feed to Commander DeepSeek Agent running V4 Antifragile guidelines.
    """
    symbol = request.symbol
    try:
        # Fetch data for 30M (Macro) and 5M (Assault point)
        res_30m = await get_chan_detail(symbol, "30", count=300)
        res_5m = await get_chan_detail(symbol, "5", count=300)

        # Compress context: We only need the latest structures to feed the LLM
        def snapshot(chan_data):
            return {
                "bis_last_5": chan_data.get("bis", [])[-5:] if chan_data.get("bis") else [],
                "segs_last_3": chan_data.get("segs", [])[-3:] if chan_data.get("segs") else [],
                "zhongshus_last_2": [
                    {
                        "zg": z["zg"], "zd": z["zd"], 
                        "high": z["gg"], "low": z["dd"]
                    } for z in chan_data.get("zhongshus", [])[-2:]
                ] if chan_data.get("zhongshus") else []
            }

        context = {
            "symbol": symbol,
            "macro_30m": snapshot(res_30m),
            "micro_5m": snapshot(res_5m)
        }
        
        context_json = json.dumps(context, ensure_ascii=False)
        
        # Call the LLM Agent
        result = await _llm_service.infer_czsc_scenarios(CZSC_SYSTEM_PROMPT, context_json)
        
        # Calculate recent ATR for ghost generation using 5m Klines
        raw_klines = res_5m.get("klines", [])
        current_close = raw_klines[-1].get("close", 0) if raw_klines else 0
        
        # In V4, time is already timestamp or string, we need a numeric value for phantom.py
        # 必须使用 UTC+8 时区，与前端 toTimestamp() 保持一致
        current_ts = raw_klines[-1].get("time") if raw_klines else 0
        if isinstance(current_ts, str):
            import pandas as pd
            current_ts = int(pd.Timestamp(current_ts, tz="Asia/Shanghai").timestamp() * 1000)

        atr = 0
        if len(raw_klines) > 14:
            tr_list = []
            for i in range(1, 15):
                prev = raw_klines[-(i+1)]
                curr = raw_klines[-i]
                tr = max(curr["high"] - curr["low"], abs(curr["high"] - prev["close"]), abs(curr["low"] - prev["close"]))
                tr_list.append(tr)
            atr = sum(tr_list) / len(tr_list)

        # Enhance scenarios with phantom geometry
        scenarios = result.get("scenarios", [])
        period_ms = 5 * 60 * 1000 # 5M base
        if scenarios and current_close > 0:
            result["scenarios"] = generate_phantom_klines(
                current_close=current_close,
                current_timestamp=current_ts,
                period_ms=period_ms,
                scenarios=scenarios,
                atr=atr
            )

        return result

    except Exception as e:
        logger.error(f"Inference failed for {symbol}: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent 推演失败: {str(e)}"
        )
