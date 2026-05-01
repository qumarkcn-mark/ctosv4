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


def _symbol_variants(symbol: str) -> list[str]:
    raw = (symbol or "").strip()
    variants = {raw}
    if len(raw) == 8 and raw[:2].lower() in {"sh", "sz"}:
        variants.add(f"{raw[:2].lower()}.{raw[2:]}")
    if "." in raw:
        variants.add(raw.replace(".", ""))
    return [item for item in variants if item]


def _safe_json_loads(value: object) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _load_latest_ai_native_radar_run(*, user_id: int, symbol: str, mode: Optional[str] = None) -> Optional[dict]:
    variants = _symbol_variants(symbol)
    if not variants:
        return None

    placeholders = ",".join("?" for _ in variants)
    params: list[object] = [user_id, *variants]
    mode_clause = ""
    if mode:
        mode_clause = " AND mode = ?"
        params.append(mode)

    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT id, symbol, mode, created_at, model_name,
                   transcript_json, ai_output_json, gate_result_json, model_route_json
              FROM ai_reasoning_runs
             WHERE user_id = ?
               AND symbol IN ({placeholders})
               {mode_clause}
             ORDER BY id DESC
             LIMIT 1
            """,
            params,
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None

    output = _safe_json_loads(row["ai_output_json"])
    transcript_payload = _safe_json_loads(row["transcript_json"])
    gate = _safe_json_loads(row["gate_result_json"])
    model_route = _safe_json_loads(row["model_route_json"])
    reconstructed = _reconstruct_ai_native_report(
        output=output,
        transcript_payload=transcript_payload,
        gate_payload=gate,
        model_route_payload=model_route,
    )
    if reconstructed:
        reconstructed.update(
            {
                "run_id": row["id"],
                "symbol": row["symbol"],
                "mode": row["mode"],
                "generated_at": reconstructed.get("generated_at") or row["created_at"],
            }
        )
        return reconstructed

    latest = {
        **output,
        "run_id": row["id"],
        "symbol": row["symbol"],
        "mode": row["mode"],
        "generated_at": output.get("generated_at") or row["created_at"],
        "gate_status": gate.get("status") or "UNKNOWN",
        "gate_score": gate.get("score") or 0,
        "model_route": model_route or output.get("model_route"),
        "fallback_reason": output.get("fallback_reason"),
    }
    violations = gate.get("violations") or []
    if not latest.get("fallback_reason") and violations:
        latest["fallback_reason"] = "门禁提示：" + "；".join(
            str(item.get("message") or item.get("code") or item) for item in violations
        )
    return latest


def _reconstruct_ai_native_report(
    *,
    output: dict,
    transcript_payload: dict,
    gate_payload: dict,
    model_route_payload: dict,
) -> Optional[dict]:
    if not output or not transcript_payload or not gate_payload:
        return None
    try:
        from server.engines.ai_native.schemas import (
            AIReasoningOutput,
            AIReasoningResponse,
            GateViolation,
            ModelRoute,
            StructureTranscript,
        )
        from server.engines.ai_native.schemas import GateResult
        from server.engines.ai_native.reasoning_orchestrator import _fallback_response

        transcript = StructureTranscript.model_validate(transcript_payload)
        route = ModelRoute.model_validate(model_route_payload or {})
        reasoning = AIReasoningOutput.model_validate(output)

        if config.AI_NATIVE_RADAR_GATE_ENABLED:
            from server.engines.ai_native.verifier import verify_ai_reasoning

            _, gate = verify_ai_reasoning(output, transcript)
        else:
            gate = GateResult(status="PASS", score=100, violations=[])
        if gate.status == "FALLBACK":
            return _fallback_response({}, transcript, gate, route).model_dump()
        violations = gate_payload.get("violations") or []
        fallback_reason = None
        if gate.status != "PASS" and violations:
            fallback_reason = "门禁提示：" + "；".join(
                str(item.get("message") or item.get("code") or item) for item in violations
            )
        response = AIReasoningResponse(
            gate_status=gate.status,
            gate_score=gate.score,
            generated_at=transcript.generated_at,
            raw_reasoning_md=reasoning.raw_reasoning_md,
            coach_filtered_md=reasoning.coach_filtered_md,
            semantic_filter_status=reasoning.semantic_filter_status,
            semantic_filter_violations=[
                item if hasattr(item, "model_dump") else GateViolation(**item)
                for item in reasoning.semantic_filter_violations
            ],
            agent_observations=transcript.agent_observations,
            key_boundaries=transcript.reasoning_boundaries,
            position_context=transcript.position_context,
            model_route=route,
            coach_talk=reasoning.coach_filtered_md,
            disclaimer=reasoning.disclaimer,
            fallback_reason=fallback_reason,
        )
        return response.model_dump()
    except Exception as exc:
        logger.warning("AI Native latest reconstruction failed: %s", exc)
        return None

class InferenceRequest(BaseModel):
    symbol: str
    mode: Optional[str] = None

class AINativeRadarRequest(BaseModel):
    symbol: str
    mode: Optional[str] = None
    user_id: int = 1

class AINativeRadarReviewRequest(BaseModel):
    user_id: int = 1
    actual_hypothesis: str
    quality_score: int
    notes: str = ""
    outcome_path: Optional[str] = None
    reviewer: str = "human"

class AINativeRadarAutoSettleRequest(BaseModel):
    user_id: int = 1
    limit: int = 20
    force: bool = False

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

        result.update(_coach_narrative_defaults(radar_data, result))

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


@router.get("/ai-native-radar/latest")
async def latest_ai_native_radar_run(
    user_id: int = Query(1),
    symbol: str = Query(...),
    mode: Optional[str] = Query(None),
):
    """读取某只股票最近一次完整 AI Native 推演，供前端切票/刷新后回填。"""
    try:
        latest = await run_in_threadpool(
            _load_latest_ai_native_radar_run,
            user_id=user_id,
            symbol=symbol,
            mode=mode,
        )
        return {"status": "success", "data": latest}
    except Exception as e:
        logger.error(f"AI Native Radar latest query failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI Native Radar 最近推演查询失败: {str(e)}")


@router.get("/ai-native-radar/runs")
async def list_ai_native_radar_runs(
    user_id: int = Query(1),
    limit: int = Query(50, ge=1, le=200),
    symbol: Optional[str] = Query(None),
    replay_status: Optional[str] = Query(None),
):
    """列出 AI Native Radar 影子样本，供人工复盘。"""
    try:
        from server.engines.ai_native.observation import list_reasoning_runs

        runs = await run_in_threadpool(
            list_reasoning_runs,
            user_id=user_id,
            limit=limit,
            symbol=symbol,
            replay_status=replay_status,
        )
        return {"status": "success", "data": [item.model_dump() for item in runs]}
    except Exception as e:
        logger.error(f"AI Native Radar runs query failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI Native Radar 样本查询失败: {str(e)}")


@router.post("/ai-native-radar/runs/{run_id}/review")
async def review_ai_native_radar_run(run_id: int, request: AINativeRadarReviewRequest):
    """人工复盘一条 AI Native Radar 样本，形成评分闭环。"""
    try:
        from server.engines.ai_native.observation import review_reasoning_run

        reviewed = await run_in_threadpool(
            review_reasoning_run,
            run_id=run_id,
            user_id=request.user_id,
            actual_hypothesis=request.actual_hypothesis,
            quality_score=request.quality_score,
            notes=request.notes,
            outcome_path=request.outcome_path,
            reviewer=request.reviewer,
        )
        return {"status": "success", "data": reviewed.model_dump()}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"AI Native Radar review failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI Native Radar 复盘失败: {str(e)}")


@router.get("/ai-native-radar/observation-summary")
async def ai_native_radar_observation_summary(user_id: int = Query(1)):
    """汇总 AI Native Radar 影子观测质量，判断是否可进入 UI Beta。"""
    try:
        from server.engines.ai_native.observation import summarize_observation

        summary = await run_in_threadpool(summarize_observation, user_id=user_id)
        return {"status": "success", "data": summary.model_dump()}
    except Exception as e:
        logger.error(f"AI Native Radar observation summary failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI Native Radar 观测汇总失败: {str(e)}")


@router.post("/ai-native-radar/auto-settle")
async def auto_settle_ai_native_radar_runs(request: AINativeRadarAutoSettleRequest):
    """自动结算已过观察期的 AI Native Radar 样本。"""
    try:
        from datetime import date

        from server.engines.ai_native.observation import (
            pending_runs_for_auto_settlement,
            settle_reasoning_run_with_radar,
        )

        today = date.today().isoformat()
        pending = await run_in_threadpool(
            pending_runs_for_auto_settlement,
            user_id=request.user_id,
            limit=request.limit,
            today=today,
            force=request.force,
        )
        settled = []
        failed = []
        for run in pending:
            try:
                radar_result = await radar_api.get_radar(run["symbol"], user_id=request.user_id, include_structure=True)
                radar_data = radar_result.get("data") or {}
                reviewed = await run_in_threadpool(
                    settle_reasoning_run_with_radar,
                    run_row=run,
                    current_radar_data=radar_data,
                    reviewer="auto",
                )
                settled.append(reviewed.model_dump())
            except Exception as exc:
                failed.append({"id": run.get("id"), "symbol": run.get("symbol"), "error": str(exc)[:160]})
        return {
            "status": "success",
            "data": {
                "checked": len(pending),
                "settled": len(settled),
                "failed": failed,
                "runs": settled,
            },
        }
    except Exception as e:
        logger.error(f"AI Native Radar auto-settle failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI Native Radar 自动结算失败: {str(e)}")


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


def _coach_narrative_defaults(radar_data: dict, result: dict) -> dict:
    """Ensure AI radar behaves as coach layer even when LLM omits new fields."""
    algorithm = radar_data.get("algorithm_v2") or {}
    deduction = radar_data.get("deduction") or {}
    position_context = radar_data.get("position_context") or {}
    coach_action = radar_data.get("coach_action") or {}
    current_id = (
        _current_scenario_from_confirmation(algorithm)
        or algorithm.get("current_scenario_id")
        or _current_scenario_from_deduction(deduction)
    )
    summary = algorithm.get("summary") or _diagnosis_from_radar(radar_data)
    focus = _first_trigger_text(algorithm) or _core_defense_from_radar(radar_data)
    position_label = position_context.get("label") or ("持仓" if position_context.get("is_holding") else "空仓")
    fallback = {
        "chan_talk": _chan_talk_from_radar(radar_data, current_id, summary, focus),
        "plain_reading": _plain_reading_from_radar(current_id, summary),
        "operator_mistake": _mistake_text(current_id, algorithm, deduction),
        "empty_position_view": _empty_position_text(current_id),
        "holding_position_view": coach_action.get("summary") or f"持仓视角先按{position_label}管理，重点看 C 路径失效线是否触发。",
        "next_focus": f"接下来只盯：{focus}",
    }
    merged = {key: result.get(key) or value for key, value in fallback.items()}
    if summary:
        merged["diagnosis"] = summary
    for key in ("chan_talk", "plain_reading", "operator_mistake", "empty_position_view", "holding_position_view"):
        if _narrative_contradicts_algorithm(merged.get(key), current_id, algorithm):
            merged[key] = fallback[key]
    return merged


def _chan_talk_from_radar(radar_data: dict, current_id: str, summary: str, focus: str) -> str:
    algorithm = radar_data.get("algorithm_v2") or {}
    up_price = _first_boundary_price(algorithm, ["confirm", "pressure"])
    reclaim_price = _first_boundary_price(algorithm, ["maintain", "support"])
    weak_price = _first_boundary_price(algorithm, ["invalidate"])
    mid_price = _first_boundary_price(algorithm, ["support", "maintain", "invalidate"])
    low_price = weak_price or reclaim_price
    high_price = up_price
    current_desc = _plain_trend_desc(algorithm.get("path"), summary)
    path = algorithm.get("path")
    phase = algorithm.get("phase")

    if path == "UPWARD_MAJOR_WAVE" and phase == "BREAKOUT_EXTENSION" and weak_price:
        mid_line = mid_price or reclaim_price
        current_line = "已经突破旧结构前高" if current_id == "A" else "正在旧结构前高附近确认"
        return (
            f"本轮走势先看大级别：价格{current_line} {weak_price}，属于上升离开后的延伸确认。"
            f"接下来如果回踩不跌回 {weak_price} 下方，说明突破没有被拉回，走势继续按上升段管理；"
            f"如果跌破 {weak_price} 后反抽也站不回去，就说明旧高突破失败，短线转弱。"
            f"中期防线看 {mid_line or weak_price}，没跌破前只能说高位震荡或短线走弱，不能说大级别完全破坏。"
        )

    if up_price and reclaim_price and weak_price:
        return (
            f"本轮走势先按{current_desc}看，已经离开 {reclaim_price} 一带，目前要看它是在上方继续震荡，"
            f"还是重新被拉回去。后面如果突破 {up_price}，回踩又不跌回 {reclaim_price} 下方，"
            f"说明向上离开没有被拉回，走势继续按上升段推演。反过来，如果跌破 {weak_price} 后拉不回 {weak_price} 上方，"
            f"说明这次离开失败，短线转弱。中期防线看 {mid_price or weak_price}，没有跌破前，只能说短线走弱或震荡，"
            f"不能说大级别完全破坏；如果跌破并拉不回来，本轮上升推演就要作废。在 {low_price} - {high_price} 之间，"
            f"先按震荡观察，不提前下结论。"
        )
    if up_price and weak_price:
        return (
            f"本轮走势先按{current_desc}看，现在方向还要等价格自己选择。向上只有突破 {up_price} 后，"
            f"并且回踩不被拉回原区间，才算上升段继续；向下如果跌破 {weak_price} 后拉不回，走势就转弱。"
            f"在这两个价位之间，先按震荡观察，不提前下结论。"
        )
    return (
        f"本轮走势先按{current_desc}看。接下来重点看 {focus}。按缠论处理，先看大级别有没有离开关键中枢，"
        f"再看小级别回踩是否被拉回；没有离开前按震荡观察，离开后不被拉回才按延续推演，"
        f"跌破关键边界且拉不回则推演转弱。"
    )


def _plain_reading_from_radar(current_id: str, summary: str) -> str:
    if current_id == "A":
        return f"规则雷达已把走势归入 A 路径：{summary} 重点不是追涨，而是复核回踩和防线。"
    if current_id == "C":
        return f"规则雷达已把走势归入 C 路径：{summary} 原推演先停止，等新结构重建。"
    return f"规则雷达当前把走势归入 B 路径：{summary} 现在不是结论，继续等事件归类。"


def _empty_position_text(current_id: str) -> str:
    if current_id == "A":
        return "空仓视角不要追在情绪最热处，等回踩不破或执行级别确认后再复核。"
    if current_id == "C":
        return "空仓视角先不急着接，等失效后的新中枢或新买点重新出现。"
    return "空仓视角先等 A 路径确认，不用在 B 路径里提前替市场下结论。"


def _first_boundary_price(algorithm: dict, groups: list[str]) -> str:
    boundaries = algorithm.get("boundaries") or {}
    for group in groups:
        for item in boundaries.get(group) or []:
            value = item.get("value")
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if num > 0:
                return f"{num:.2f}"
    return ""


def _plain_trend_desc(path: str, summary: str) -> str:
    if path == "HIGH_VOLATILITY_OSCILLATION":
        return "高位震荡选择"
    if path == "UPWARD_MAJOR_WAVE":
        return "上涨延续中的强弱确认"
    if path == "PULLBACK_IN_UPTREND":
        return "上涨后的回落验证"
    if path == "BOTTOM_REPAIR":
        return "底部修复后的方向选择"
    if path == "DOWNWARD_DEFENSE":
        return "下跌后的防守观察"
    return summary or "震荡选择"


def _current_scenario_from_confirmation(algorithm: dict) -> str:
    state = (algorithm.get("a_state") or (algorithm.get("confirmation") or {}).get("state") or "")
    if state.startswith("A_"):
        return "A"
    if state.startswith("C_"):
        return "C"
    if state.startswith("B_"):
        return "B"
    return ""


def _current_scenario_from_deduction(deduction: dict) -> str:
    for scenario in deduction.get("complete_classification") or []:
        if scenario.get("state") == "CURRENT":
            return scenario.get("code") or scenario.get("id") or ""
    status = deduction.get("status") or ""
    if status == "FAILED":
        return "C"
    return "B"


def _first_trigger_text(algorithm: dict) -> str:
    for item in algorithm.get("trigger_playbook") or []:
        condition = item.get("condition")
        then = item.get("then") or item.get("title")
        if condition and then:
            return f"{condition}，{then}"
        if condition:
            return condition
    for item in algorithm.get("next_watch") or []:
        if item:
            return str(item)
    return ""


def _mistake_text(current_id: str, algorithm: dict, deduction: dict) -> str:
    if current_id == "A":
        return "最容易犯的错是把 A 触发当成无条件追涨，忽略执行前复核和防线。"
    if current_id == "C" or (deduction.get("path_thesis") or {}).get("phase") == "推演失效":
        return "最容易犯的错是用反弹愿望对抗失效边界，继续沿用已经作废的推演。"
    if current_id == "B":
        return "最容易犯的错是在 B 路径里提前动手，把等待确认误读成已经确认。"
    action = algorithm.get("action_bias") or ""
    if "WAIT" in action:
        return "最容易犯的错是在 B 路径里提前动手，把等待确认误读成已经确认。"
    return "最容易犯的错是只看一句结论，不看 A/B/C 的触发边界。"


def _narrative_contradicts_algorithm(text: object, current_id: str, algorithm: dict) -> bool:
    """AI 只负责翻译规则雷达；如果话术和规则状态冲突，服务端兜底纠正。"""
    body = str(text or "")
    if not body:
        return False
    state = algorithm.get("a_state") or (algorithm.get("confirmation") or {}).get("state") or ""
    if current_id == "A" or state.startswith("A_"):
        forbidden = (
            "B路径",
            "B 路径里",
            "B 路径中",
            "B 路径延长",
            "B路径延长",
            "当前处于B",
            "当前处于 B",
            "没有确认也没有破坏",
            "等待A确认",
            "等待 A 确认",
            "等待5分钟买点确认",
            "等待5分买点确认",
            "等待5分钟是否形成新的买点",
            "等待5分是否形成新的买点",
        )
        return any(token in body for token in forbidden)
    if current_id == "C" or state.startswith("C_"):
        forbidden = (
            "A 路径已确认",
            "A路径已确认",
            "继续按上升段推演",
            "无条件追涨",
        )
        return any(token in body for token in forbidden)
    return False


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
