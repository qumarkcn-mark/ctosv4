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
from server.api import radar as radar_api
from server.services.chan_service import analyze_matrix_state
from server.services.market_context_service import get_market_context, format_context_for_prompt
from server import config

logger = logging.getLogger("AgentAPI")
router = APIRouter()

_llm_service = LLMService()

class InferenceRequest(BaseModel):
    symbol: str
    mode: Optional[str] = None

class AINativeRadarRequest(BaseModel):
    symbol: str
    mode: Optional[str] = None
    user_id: int = 1

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
    1. Radar v1 正式结构合同（含规则推演）
    2. 外部市场语境（资金流向、板块排名、大盘背景）
    然后合并输入大模型，生成含仓位建议的深度推演。
    """
    symbol = request.symbol
    try:
        # 并行获取正式 Radar contract + 市场语境。
        # AI 只做叙述，不再直接消费 legacy matrix 作为结构事实来源。
        import asyncio
        radar_response, market_ctx = await asyncio.gather(
            radar_api.get_radar(symbol, user_id=1),
            get_market_context(symbol),
        )
        radar_data = radar_response.get("data") or {}
        matrix_data = _radar_contract_to_display_snapshot(radar_data)
        user_position = _user_position_from_radar(radar_data)

        # 格式化外部语境文字
        ctx_text = format_context_for_prompt(market_ctx)

        # 组装 Prompt Context：结构数据 + 外部语境 + 用户持仓
        context_json = json.dumps({
            "symbol": symbol,
            "radar_contract": radar_data,
            "matrix_data": matrix_data,
            "market_context": ctx_text,
            "user_position": user_position,
        }, ensure_ascii=False)

        result = await _llm_service.infer_radar_deduction(RADAR_SYSTEM_PROMPT, context_json)

        # ── 保底填充：LLM 有时因 context 超长或 forward_classes 为空，
        #    仅返回 diagnosis 而不填充其他字段（Pydantic 不报错，但前端空白）
        #    此处直接从结构数据模板生成，确保 AISection 始终有内容可渲染。
        fwd = matrix_data.get("forward_analysis_a") or {}
        fwd_classes = fwd.get("forward_classes") or []
        deterministic_plans = _pre_plans_from_radar_deduction(radar_data.get("deduction") or {})

        # ① pre_plans 保底：从 forward_classes 1:1 映射
        if (_is_llm_fallback_result(result) or not result.get("pre_plans")) and deterministic_plans:
            result["pre_plans"] = deterministic_plans
            if _is_llm_fallback_result(result):
                result["diagnosis"] = _diagnosis_from_radar(radar_data)
            logger.info("radar_deduce: pre_plans 由 Radar 规则推演保底填充，共 %d 条", len(result["pre_plans"]))

        elif not result.get("pre_plans") and fwd_classes:
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
            result["core_defense"] = _core_defense_from_radar(radar_data)

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


@router.post("/ai-native-radar")
async def ai_native_radar(request: AINativeRadarRequest):
    """AI Native Radar 影子接口。默认关闭，不影响老 Radar。"""
    if not config.AI_NATIVE_RADAR_ENABLED:
        return {"status": "disabled", "message": "AI Native Radar is disabled"}
    try:
        from server.engines.ai_native.reasoning_orchestrator import build_ai_native_reasoning

        result = await build_ai_native_reasoning(
            symbol=request.symbol,
            user_id=request.user_id,
            mode=request.mode,
            llm_service=_llm_service,
        )
        return {"status": "success", "data": result.model_dump()}
    except Exception as e:
        logger.error(f"AI Native Radar failed for {request.symbol}: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI Native Radar 失败: {str(e)}")


def _radar_contract_to_display_snapshot(radar_data: dict) -> dict:
    """把 Radar v1 contract 映射成 TRadar 历史快照仍能读取的显示形状。"""
    structure = radar_data.get("structure") or {}
    levels = structure.get("levels") or {}
    deduction = radar_data.get("deduction") or {}
    current_position = (
        (deduction.get("path_thesis") or {}).get("title")
        or deduction.get("summary")
        or (radar_data.get("entry_plan") or {}).get("title")
        or (radar_data.get("holding_plan") or {}).get("plan_id")
        or ""
    )
    forward_classes = _forward_classes_from_radar_deduction(deduction)

    return {
        "api_version": radar_data.get("api_version", "radar.v1"),
        "symbol": radar_data.get("symbol"),
        "mode": radar_data.get("mode"),
        "matrix_a": [
            _display_level(levels.get("day"), "day"),
            _display_level(levels.get("30"), "m30"),
            _display_level(levels.get("5"), "m5"),
        ],
        "matrix_b": [
            _display_level(levels.get("day"), "day"),
            _display_level(levels.get("60"), "m60"),
            _display_level(levels.get("15"), "m15"),
        ],
        "week": _display_level(levels.get("week"), "week"),
        "interval_nesting_a": (structure.get("systems") or {}).get("short_term", {}).get("interval_nesting"),
        "interval_nesting_b": (structure.get("systems") or {}).get("swing", {}).get("interval_nesting"),
        "forward_analysis_a": {
            "current_position": current_position,
            "forward_classes": forward_classes,
            "stop_loss": _core_defense_from_radar(radar_data),
        },
        "forward_analysis_b": {
            "current_position": current_position,
            "forward_classes": None,
        },
        "entry_checklist": _entry_checklist_from_plan(radar_data.get("entry_plan")),
        "holding_status": _holding_status_from_plan(radar_data.get("holding_plan")),
        "holding_stage_v2": _holding_status_from_plan(radar_data.get("holding_plan")),
        "strategy_classification": {
            **(radar_data.get("strategy") or {}),
            "strategy_type": (radar_data.get("strategy") or {}).get("strategy_type", "观察中"),
            "summary": (radar_data.get("strategy") or {}).get("name", "Radar Contract"),
        },
        "targets": (radar_data.get("entry_plan") or {}).get("targets"),
        "reward_ratio": (radar_data.get("entry_plan") or {}).get("reward_ratio"),
        "data_freshness": radar_data.get("freshness"),
        "deduction": deduction,
        "structure_config": radar_data.get("structure_config"),
        "radar_contract": radar_data,
    }


def _display_level(level: Optional[dict], fallback_level: str) -> dict:
    if not level:
        return {"level": fallback_level, "state": "UNKNOWN", "price": 0, "zg": 0, "zd": 0}
    active = level.get("active_zhongshu") or {}
    return {
        **level,
        "level": fallback_level,
        "price": level.get("price", 0),
        "zg": level.get("zg") or active.get("zg", 0),
        "zd": level.get("zd") or active.get("zd", 0),
        "zs_operative_zg": level.get("zs_operative_zg") or level.get("zg") or active.get("zg", 0),
        "zs_operative_zd": level.get("zs_operative_zd") or level.get("zd") or active.get("zd", 0),
        "detail_bis": level.get("detail_bis") or level.get("recent_bis") or level.get("bis") or [],
        "patterns": level.get("patterns") or [],
        "zoushi_type": level.get("zoushi_type") or {"type": "数据不足", "zs_count": 0},
        "classifications": level.get("classifications") or [],
    }


def _entry_checklist_from_plan(entry_plan: Optional[dict]) -> Optional[dict]:
    if not entry_plan:
        return None
    checklist = {}
    for condition in entry_plan.get("conditions") or []:
        condition_id = condition.get("condition_id")
        if condition_id:
            checklist[condition_id] = condition.get("status") == "PASS"
    if checklist:
        checklist["all_passed"] = all(checklist.values())
    return checklist or None


def _holding_status_from_plan(holding_plan: Optional[dict]) -> dict:
    if not holding_plan:
        return {"stage": "empty", "label": "空仓"}
    legacy = holding_plan.get("legacy_status")
    if legacy:
        return legacy
    risk = holding_plan.get("risk") or {}
    return {
        "stage": holding_plan.get("stage", "WATCHING"),
        "label": holding_plan.get("stage") or "持仓管理",
        "stair_stop_price": risk.get("trailing_stop", 0),
        "locked_profit_pct": 0,
        "action": risk.get("invalid_if", ""),
    }


def _forward_classes_from_radar_deduction(deduction: dict) -> list:
    classes = []
    for scenario in deduction.get("complete_classification") or []:
        trigger_if = scenario.get("trigger_if") or []
        classes.append({
            "id": scenario.get("code") or scenario.get("id"),
            "name": scenario.get("title") or scenario.get("label"),
            "condition": "；".join(trigger_if) if trigger_if else "等待结构事件",
            "deduction": scenario.get("summary", ""),
            "meaning": scenario.get("summary", ""),
            "action": _scenario_action_text(scenario),
            "stop_loss": None,
        })
    return classes


def _scenario_action_text(scenario: dict) -> str:
    code = scenario.get("code") or ""
    state = scenario.get("state") or ""
    title = scenario.get("title") or ""
    if code == "C" or "失效" in title:
        return "结构失效时离场或保持空仓，仅供参考"
    if code == "A":
        return "确认事件触发后进入执行前复核，仅供参考"
    if state == "CURRENT":
        return "当前路径延长，等待下一根结构确认，仅供参考"
    return "继续观察，不执行交易，仅供参考"


def _pre_plans_from_radar_deduction(deduction: dict) -> list:
    plans = []
    labels = {"A": "A", "B": "B", "C": "C"}
    for scenario in deduction.get("complete_classification") or []:
        code = scenario.get("code") or scenario.get("id", "")
        trigger_if = scenario.get("trigger_if") or []
        plans.append({
            "plan_name": f"预案{labels.get(code, code)}：{scenario.get('title') or scenario.get('label') or code}",
            "trigger": "；".join(trigger_if) if trigger_if else "等待结构事件",
            "deduction": scenario.get("summary") or "结构推演进行中",
            "machine_action": _scenario_action_text(scenario),
            "color": _scenario_color(code),
        })
    return plans


def _scenario_color(code: str) -> str:
    if code == "A":
        return "🟢"
    if code == "C":
        return "🔴"
    return "🟡"


def _core_defense_from_radar(radar_data: dict) -> str:
    deduction = radar_data.get("deduction") or {}
    thesis = deduction.get("path_thesis") or {}
    for boundary in thesis.get("boundaries") or []:
        price = boundary.get("price")
        if price:
            return f"{boundary.get('label', '结构边界')} {float(price):.2f}，{boundary.get('meaning', '跌破则推演失效')}"
    invalid_if = (deduction.get("main_path") or {}).get("invalid_if") or deduction.get("invalid_if") or []
    if invalid_if:
        return str(invalid_if[0])
    return "以 Radar 规则推演给出的最近结构边界为准"


def _diagnosis_from_radar(radar_data: dict) -> str:
    deduction = radar_data.get("deduction") or {}
    return (
        (deduction.get("path_thesis") or {}).get("title")
        or deduction.get("summary")
        or "Radar 规则推演已生成"
    )


def _user_position_from_radar(radar_data: dict) -> Optional[dict]:
    holding_plan = radar_data.get("holding_plan") or {}
    entry_thesis = holding_plan.get("entry_thesis") or {}
    if radar_data.get("mode") != "HOLDING" and not entry_thesis:
        return None
    return {
        "shares": entry_thesis.get("qty"),
        "avg_cost": entry_thesis.get("cost") or entry_thesis.get("avg_cost"),
    }


def _is_llm_fallback_result(result: dict) -> bool:
    if str(result.get("diagnosis", "")).startswith("推演引擎异常"):
        return True
    plans = result.get("pre_plans") or []
    return bool(plans and plans[0].get("plan_name") == "系统故障")

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
            # Fetch context and basic Radar status. Portfolio narrative should not
            # consume legacy matrix as its structure source.
            try:
                ctx = await get_market_context(sym)
                radar_response = await radar_api.get_radar(sym, user_id=1)
                radar_data = radar_response.get("data") or {}
                watchlist_candidates.append({
                    "symbol": sym,
                    "name": row["name"],
                    "market_heat": format_context_for_prompt(ctx),
                    "structure_summary": _portfolio_structure_summary_from_radar(radar_data),
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


def _portfolio_structure_summary_from_radar(radar_data: dict) -> dict:
    levels = ((radar_data.get("structure") or {}).get("levels") or {})
    day = levels.get("day") or {}
    m30 = levels.get("30") or {}
    deduction = radar_data.get("deduction") or {}
    return {
        "source": "radar.v1",
        "mode": radar_data.get("mode"),
        "status": deduction.get("status"),
        "summary": deduction.get("summary"),
        "day_state": day.get("state", "UNKNOWN"),
        "m30_state": m30.get("state", "UNKNOWN"),
        "patterns": day.get("patterns", []),
        "freshness": radar_data.get("freshness"),
    }
