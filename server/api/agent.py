import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import traceback
from fastapi.concurrency import run_in_threadpool

from server.services.chan_detail_service import get_chan_detail
from server.db.database import get_connection
from server.services.llm_service import LLMService
from server.prompts.czsc_agent import CZSC_SYSTEM_PROMPT
from server.prompts.chan_radar_prompt import RADAR_SYSTEM_PROMPT
from server.prompts.portfolio_strategy_prompt import PORTFOLIO_STRATEGY_PROMPT
from chan_engine.phantom import generate_phantom_klines
from server.services.chan_service import analyze_matrix_state
from server.services.market_context_service import get_market_context, format_context_for_prompt

logger = logging.getLogger("AgentAPI")
router = APIRouter()

_llm_service = LLMService()

class InferenceRequest(BaseModel):
    symbol: str

class PortfolioStrategyRequest(BaseModel):
    scan_results: list

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

        matrix_state = await analyze_matrix_state(symbol)
        m5_state  = next((x for x in matrix_state.get("matrix_a", []) if x.get("level") == "m5"),  {})
        m30_state = next((x for x in matrix_state.get("matrix_a", []) if x.get("level") == "m30"), {})
        day_state = next((x for x in matrix_state.get("matrix_a", []) if x.get("level") == "day"), {})

        # window_d（大级别否决）数据 — 日线真实状态
        context["day_state"] = {
            "state":        day_state.get("state", "UNKNOWN"),
            "zoushi_type":  day_state.get("zoushi_type", {}),
            "zg":           day_state.get("zg", 0),
            "zd":           day_state.get("zd", 0),
            "patterns":     day_state.get("patterns", []),
        }
        # window_c（宏观中级别）数据 — 30分钟真实状态
        context["m30_state"] = {
            "state":        m30_state.get("state", "UNKNOWN"),
            "zoushi_type":  m30_state.get("zoushi_type", {}),
            "classifications": m30_state.get("classifications", []),
        }
        # window_a（狙击级别）数据 — 5分钟
        context["fsm_state"]       = m5_state.get("state", "UNKNOWN")
        context["zoushi_type"]     = m5_state.get("zoushi_type", {})
        context["classifications"] = m5_state.get("classifications", [])
        # 甲/乙/丙预案（前瞻推演 — 价格位置版）
        context["forward_analysis"] = matrix_state.get("forward_analysis_a", {})
        # 区间套信息
        context["interval_nesting"] = matrix_state.get("interval_nesting_a")

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


@router.post("/radar_deduce")
async def radar_deduce(request: InferenceRequest):
    """
    接收股票代码，并行获取：
    1. 缠论矩阵状态（含甲/乙/丙前瞻推演）
    2. 外部市场语境（资金流向、板块排名、大盘背景）
    然后合并输入大模型，生成含仓位建议的深度推演。
    """
    symbol = request.symbol
    try:
        # 先查持仓——持仓决定 matrix 走哪条推演路径，必须先拿到
        def _get_pos():
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT quantity, avg_cost FROM positions WHERE user_id = ? AND symbol = ?",
                    (1, symbol)
                ).fetchone()
                if row:
                    return {"quantity": row["quantity"], "avg_cost": row["avg_cost"]}
                return None
            finally:
                conn.close()

        pos_row = await run_in_threadpool(_get_pos)

        user_position = None
        holding = None
        if pos_row and pos_row["quantity"] > 0:
            user_position = {
                "shares": pos_row["quantity"],
                "avg_cost": pos_row["avg_cost"]
            }
            # 持仓路径：forward_analysis 切换为止损/减仓叙述
            holding = {"cost": pos_row["avg_cost"], "qty": pos_row["quantity"]}

        # 并行获取结构数据（携带持仓）+ 市场语境
        import asyncio
        matrix_data, market_ctx = await asyncio.gather(
            analyze_matrix_state(symbol, holding=holding),
            get_market_context(symbol),
        )

        # 格式化外部语境文字
        ctx_text = format_context_for_prompt(market_ctx)

        # 组装 Prompt Context：结构数据 + 外部语境 + 用户持仓
        context_json = json.dumps({
            "symbol": symbol,
            "matrix_data": matrix_data,
            "market_context": ctx_text,
            "user_position": user_position,
        }, ensure_ascii=False)

        result = await _llm_service.infer_radar_deduction(RADAR_SYSTEM_PROMPT, context_json)

        # ── 保底填充：LLM 有时因 context 超长或 forward_classes 为空，
        #    仅返回 diagnosis 而不填充其他字段（Pydantic 不报错，但前端空白）
        #    此处直接从结构数据模板生成，确保 AISection 始终有内容可渲染。
        fwd         = (matrix_data.get("forward_analysis_a") or {})
        fwd_classes = fwd.get("forward_classes") or []

        # ① pre_plans 保底：从 forward_classes 1:1 映射
        if not result.get("pre_plans") and fwd_classes:
            PLAN_LABELS  = ["A", "B", "C"]
            _BEAR_KW = {"空仓", "离场", "减仓", "出局", "止损", "砍仓", "清仓", "卖出", "🔴"}
            _BULL_KW = {"入场", "加仓", "持仓", "买入", "持有", "三买", "二买", "一买", "🟢"}

            def _color(action_str: str) -> str:
                for kw in _BEAR_KW:
                    if kw in action_str:
                        return "🔴"
                for kw in _BULL_KW:
                    if kw in action_str:
                        return "🟢"
                return "🟡"

            result["pre_plans"] = [
                {
                    "plan_name":     f"预案{PLAN_LABELS[i] if i < 3 else str(i+1)}："
                                     f"{fc.get('id', '')} {fc.get('name', '')}",
                    "trigger":       fc.get("condition", "—"),
                    "deduction":     fc.get("deduction", fc.get("meaning", fc.get("name", "结构推演进行中"))),
                    "machine_action": fc.get("action", "—"),
                    "color":         _color(fc.get("action", "")),
                }
                for i, fc in enumerate(fwd_classes[:3])
            ]
            logger.info("radar_deduce: pre_plans 由结构模板保底填充，共 %d 条", len(result["pre_plans"]))

        # ② core_defense 保底：用 forward_analysis 的 stop_loss 字段
        if not result.get("core_defense"):
            sl = fwd.get("stop_loss", "")
            result["core_defense"] = sl if sl else "以最近中枢ZD为参考止损线"

        # ③ market_context_verdict 保底：截取市场语境第一行
        if not result.get("market_context_verdict") and ctx_text:
            first_line = ctx_text.split("\n")[0].strip()
            result["market_context_verdict"] = first_line[:100] if first_line else "外部语境获取中"

        # 把原始市场语境也附在返回里（供前端展示）
        result["market_context_raw"] = market_ctx

        # 入库保存推演快照
        def _save_deduction():
            conn = get_connection()
            try:
                summary = result.get("diagnosis", "")  # RadarInferenceResult 使用 diagnosis 字段
                ai_deduction_json = json.dumps(result, ensure_ascii=False)
                conn.execute(
                    """INSERT INTO radar_deductions
                       (user_id, symbol, matrix_state_json, ai_summary, ai_deduction_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (1, symbol, json.dumps(matrix_data, ensure_ascii=False), summary, ai_deduction_json)
                )
                conn.commit()
            finally:
                conn.close()

        try:
            await run_in_threadpool(_save_deduction)
        except Exception as db_e:
            logger.error(f"Failed to save radar deduction to history: {db_e}")

        return {"status": "success", "data": result}

    except Exception as e:
        logger.error(f"Radar deduction failed for {symbol}: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"雷达推演失败: {str(e)}")

@router.get("/radar_history/{symbol}")
async def get_radar_history(symbol: str, limit: int = Query(10, ge=1, le=50)):
    """获取指定股票雷达推演的历史记录"""
    def _fetch_history():
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT id, created_at, ai_summary, matrix_state_json, ai_deduction_json 
                   FROM radar_deductions 
                   WHERE user_id = ? AND symbol = ? 
                   ORDER BY created_at DESC 
                   LIMIT ?""",
                (1, symbol, limit)
            ).fetchall()
            
            history = []
            for r in rows:
                history.append({
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "summary": r["ai_summary"],
                    "matrix_data": json.loads(r["matrix_state_json"]),
                    "deduction_process": json.loads(r["ai_deduction_json"])
                })
            return history
        finally:
            conn.close()

    try:
        history = await run_in_threadpool(_fetch_history)
        return {"status": "success", "data": history}
        
    except Exception as e:
        logger.error(f"Failed to fetch radar history for {symbol}: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取推演历史记录失败: {str(e)}")

@router.post("/portfolio_strategy")
async def portfolio_strategy(request: PortfolioStrategyRequest):
    """
    接收前端所有的持仓扫描结果，并拉取自选观察池中的备选股票。
    综合这所有数据，请求大模型生成一份全局的《仓位调度战略》。
    """
    try:
        # 1. Fetch watchlist candidates
        def _get_watchlist():
            conn = get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT wi.symbol, wi.name
                      FROM watchlist_items wi
                      JOIN watchlist_groups wg ON wg.id = wi.group_id
                     WHERE wg.user_id = 1
                     ORDER BY wg.sort_order, wi.sort_order, wi.id
                    """
                ).fetchall()
                return [{"symbol": r["symbol"], "name": r["name"]} for r in rows]
            finally:
                conn.close()

        watchlist_rows = await run_in_threadpool(_get_watchlist)
        
        watchlist_candidates = []
        for row in watchlist_rows:
            sym = row["symbol"]
            # Fetch context and basic matrix status
            try:
                ctx = await get_market_context(sym)
                matrix = await analyze_matrix_state(sym)
                watchlist_candidates.append({
                    "symbol": sym,
                    "name": row["name"],
                    "market_heat": format_context_for_prompt(ctx),
                    "structure_summary": {
                        "day_state": matrix.get("day", {}).get("state", "UNKNOWN"),
                        "m30_state": matrix.get("m30", {}).get("state", "UNKNOWN"),
                        "patterns": matrix.get("day", {}).get("patterns", [])
                    }
                })
            except Exception as e:
                logger.warning(f"Failed to fetch context for watchlist {sym}: {e}")
                
        # 2. Build the context
        context_json = json.dumps({
            "positions": request.scan_results,
            "watchlist_candidates": watchlist_candidates
        }, ensure_ascii=False)
        
        # 3. Call LLM
        strategy_markdown = await _llm_service.infer_portfolio_strategy(PORTFOLIO_STRATEGY_PROMPT, context_json)
        
        # 4. Save to database
        def _save_portfolio():
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO portfolio_strategies (user_id, context_json, strategy_markdown) VALUES (?, ?, ?)",
                    (1, context_json, strategy_markdown)
                )
                conn.commit()
            finally:
                conn.close()

        try:
            await run_in_threadpool(_save_portfolio)
        except Exception as db_e:
            logger.error(f"Failed to save portfolio strategy to DB: {db_e}")
        
        return {"status": "success", "data": strategy_markdown}
    except Exception as e:
        logger.error(f"Portfolio strategy generation failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"全局战略生成失败: {str(e)}")
