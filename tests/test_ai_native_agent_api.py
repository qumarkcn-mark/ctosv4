import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent
from server.db import database
from server.engines.ai_native.case_memory import save_reasoning_run
from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    AllowedPrice,
    ChartOverlayAlignment,
    GateResult,
    SimilarCaseSummary,
    StructureSnapshot,
    StructureTranscript,
)


COACH_MD = """**1. 【全局语境定性】**
高位震荡，观察 5 分钟边界。

**2. 【防守看门狗】**
只在 302.57 到 331.16 内有效。

**3. 【推演与应对沙盘】**
先看结构边界。仅供参考，不构成投资建议。"""


def test_ai_native_route_disabled_by_default(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", False)

    response = __import__("asyncio").run(
        agent.ai_native_radar(agent.AINativeRadarRequest(symbol="sh600519"))
    )

    assert response["status"] == "disabled"


def test_ai_native_radar_route_forwards_signal_code(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)
    seen = {}

    class FakeResponse:
        def model_dump(self):
            return {"ok": True}

    async def fake_build_ai_native_reasoning(**kwargs):
        seen.update(kwargs)
        return FakeResponse()

    from server.engines.ai_native import reasoning_orchestrator

    monkeypatch.setattr(reasoning_orchestrator, "build_ai_native_reasoning", fake_build_ai_native_reasoning)

    response = __import__("asyncio").run(
        agent.ai_native_radar(
            agent.AINativeRadarRequest(
                symbol="sh600519",
                user_id=1,
                mode="EMPTY",
                signal_code="m30_zs_above_breakout_medium",
                structure_fingerprint="fingerprint-current",
            )
        )
    )

    assert response == {"status": "success", "data": {"ok": True}}
    assert seen["expected_signal_code"] == "m30_zs_above_breakout_medium"
    assert seen["expected_structure_fingerprint"] == "fingerprint-current"


def test_latest_ai_native_radar_run_backfills_dotless_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    run_id = save_reasoning_run(
        user_id=1,
        symbol="sh.603986",
        mode="HOLDING",
            prompt_version=agent.config.AI_NATIVE_RADAR_PROMPT_VERSION,
        model_name="deepseek-v4-pro",
        transcript=StructureTranscript(
            symbol="sh.603986",
            generated_at="2026-04-30T15:00:00+08:00",
            fingerprint_version="fingerprint.v1",
            structure_fingerprint="TEST|603986",
            structure_snapshot=StructureSnapshot(
                chart_alignment=ChartOverlayAlignment(status="ALIGNED"),
            ),
            allowed_prices=[
                AllowedPrice(label="support", value=302.57, source="test"),
                AllowedPrice(label="pressure", value=331.16, source="test"),
            ],
        ),
        memory_context=SimilarCaseSummary(),
        ai_output=AIReasoningOutput(
            raw_reasoning_md=COACH_MD,
            coach_filtered_md=COACH_MD,
        ),
        gate_result=GateResult(status="PASS", score=100, violations=[]),
    )

    response = __import__("asyncio").run(
        agent.latest_ai_native_radar_run(user_id=1, symbol="sh603986", mode="HOLDING")
    )

    assert response["status"] == "success"
    assert response["data"]["run_id"] == run_id
    assert response["data"]["symbol"] == "sh.603986"
    assert response["data"]["gate_status"] == "PASS"
    assert "高位震荡" in response["data"]["coach_filtered_md"]

    plain_response = __import__("asyncio").run(
        agent.latest_ai_native_radar_run(user_id=1, symbol="603986", mode="HOLDING")
    )

    assert plain_response["status"] == "success"
    assert plain_response["data"]["run_id"] == run_id


def test_latest_ai_native_radar_run_rejects_mismatched_signal_code(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    transcript = StructureTranscript(
        symbol="sh.600519",
        generated_at="2026-05-09T15:00:00+08:00",
        fingerprint_version="fingerprint.v2",
        structure_fingerprint="TEST|SIGNAL",
        signal_v2={
            "primary": {"code": "d1_zs_inside_ss2_strong"},
            "state": "success",
        },
    )
    save_reasoning_run(
        user_id=1,
        symbol="sh.600519",
        mode="EMPTY",
        prompt_version=agent.config.AI_NATIVE_RADAR_PROMPT_VERSION,
        model_name="deepseek-v4-pro",
        transcript=transcript,
        memory_context=SimilarCaseSummary(),
        ai_output=AIReasoningOutput(
            raw_reasoning_md=COACH_MD,
            coach_filtered_md=COACH_MD,
        ),
        gate_result=GateResult(status="PASS", score=100, violations=[]),
    )

    response = __import__("asyncio").run(
        agent.latest_ai_native_radar_run(
            user_id=1,
            symbol="sh600519",
            mode="EMPTY",
            signal_code="m30_zs_above_bs3_strong",
        )
    )

    assert response["status"] == "success"
    assert response["data"] is None


def test_latest_ai_native_radar_run_rejects_mismatched_structure_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    transcript = StructureTranscript(
        symbol="sh.600519",
        generated_at="2026-05-09T15:00:00+08:00",
        fingerprint_version="fingerprint.v2",
        structure_fingerprint="STRUCTURE|OLD",
        signal_v2={
            "primary": {"code": "m30_zs_above_bs3_strong"},
            "state": "success",
        },
    )
    save_reasoning_run(
        user_id=1,
        symbol="sh.600519",
        mode="EMPTY",
        prompt_version=agent.config.AI_NATIVE_RADAR_PROMPT_VERSION,
        model_name="deepseek-v4-pro",
        transcript=transcript,
        memory_context=SimilarCaseSummary(),
        ai_output=AIReasoningOutput(
            raw_reasoning_md=COACH_MD,
            coach_filtered_md=COACH_MD,
        ),
        gate_result=GateResult(status="PASS", score=100, violations=[]),
    )

    response = __import__("asyncio").run(
        agent.latest_ai_native_radar_run(
            user_id=1,
            symbol="sh600519",
            mode="EMPTY",
            signal_code="m30_zs_above_bs3_strong",
            structure_fingerprint="STRUCTURE|NEW",
        )
    )

    assert response["status"] == "success"
    assert response["data"] is None


class FakeFusionLLM:
    def __init__(self):
        self.contexts = []

    async def infer_ai_native_radar(self, *args, **kwargs):
        if len(args) >= 2:
            self.contexts.append(args[1])
        return {
            "version": "ai_fusion_inference.v45",
            "symbol": "sh.600519",
            "generated_at": "2026-05-05T15:00:00+08:00",
            "current_judgement": "结构等待确认，Kronos 偏正但不是确定性信号。",
            "primary_path_id": "path-A",
            "path_inferences": [
                {
                    "id": "path-A",
                    "chan_path_id": "A",
                    "rank": 1,
                    "name": "结构确认后延续",
                    "probability": 0.48,
                    "confidence": "MEDIUM",
                    "chan_basis": "缠论要求重新站上确认边界。",
                    "kronos_basis": "Kronos 预测偏正，但概率来自代理摘要。",
                    "action_bias": "OBSERVE",
                    "wait_condition": "等待重新站上 12.80。",
                    "trigger_condition": "站上 12.80 后再评估。",
                    "invalidation_condition": "跌破 11.20 则结构失效。",
                    "position_discipline": "未确认前不追高。",
                    "risk_note": "只按结构边界观察。仅供参考，不构成投资建议。",
                }
            ],
            "coach_message": "等待确认，不追高。仅供参考，不构成投资建议。",
            "defense_line": "跌破 11.20 则结构失效。",
            "wait_for": ["站上 12.80"],
            "invalidation": ["跌破 11.20"],
            "position_sizing_note": "不输出自动交易指令。仅供参考，不构成投资建议。",
            "source_versions": {"chan": "chan_analysis.v45", "kronos": "kronos_forecast.v45"},
            "disclaimer": "仅供参考，不构成投资建议",
        }


def fusion_radar_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "freshness": {"is_stale": False},
        "structure": {
            "levels": {
                "day": {"level": "day", "price": 12.3, "state": "UPWARD", "zg": 11.9, "zd": 10.8},
                "30": {"level": "30", "price": 12.3, "state": "WAITING", "zg": 11.8, "zd": 11.2},
                "5": {"level": "5", "price": 12.3, "state": "REPAIR", "zg": 12.8, "zd": 11.9},
            }
        },
        "quote": {
            "available": True,
            "price": 13.2,
            "open": 12.4,
            "high": 13.25,
            "low": 12.35,
            "prev_close": 12.3,
            "change_pct": 7.32,
            "provider": "tencent",
        },
        "algorithm_v2": {
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "atoms": {
                "L2": {
                    "public_level": "30",
                    "level": "30",
                    "price": 12.3,
                    "position_state": "UP_RETEST",
                    "center": {"zd": 11.2, "zg": 12.8, "dd": 10.8, "gg": 13.2},
                    "event_sequence": [
                        {"code": "B3A", "is_buy": True, "is_current": True, "time": "2026-05-05 14:30:00", "price": 12.3}
                    ],
                    "buy_events": [],
                    "sell_events": [],
                    "divergence": {},
                    "momentum_compare": {"area_ratio": 0.35},
                }
            },
            "boundaries": {
                "confirm": [{"label": "确认", "value": 12.8, "level": "5"}],
                "maintain": [{"label": "观察", "value": 11.9, "level": "5"}],
                "invalidate": [{"label": "失效", "value": 11.2, "level": "30"}],
            },
            "boundary_groups": [
                {
                    "id": "upside_confirm",
                    "label": "上方确认线",
                    "items": [
                        {
                            "level": "5",
                            "field": "ZG",
                            "value": 12.8,
                            "trigger": "break_above",
                            "meaning": "重新站回小级别中枢上沿",
                        }
                    ],
                }
            ],
        },
        "signals_v2": {
            "version": "semantic_signal.v2",
            "state": "success",
            "primary": {
                "code": "m30_zs_above_breakout_medium",
                "label_plain": "30分钟级别突破关键位，信号中等",
                "label_expert": "30分 · 中枢上 · 突破 · 中",
                "action": "建议观察确认，止损 11.20",
            },
            "context": {
                "signal_code": "m30_zs_above_breakout_medium",
                "key_price": 12.3,
                "boundary_state": "observe",
                "stop_loss_price": 11.2,
                "risk_reward_ratio": 2.1,
            },
            "classification": [],
        },
    }


async def fake_get_radar(symbol, user_id=1, include_structure=True):
    return {"status": "success", "data": fusion_radar_contract()}


async def fake_kronos_multi_level(symbol):
    return {
        "symbol": "sh.600519",
        "levels": {
            "30": {
                "symbol": "sh.600519",
                "interval": "30",
                "change_pct": 1.4,
                "force_score": 18.0,
                "verdict": "Positive Force",
                "last_date": "2026-05-05 14:30:00",
                "forecast_data": [
                    {"date": "2026-05-05 15:00:00", "open": 12.3, "high": 12.5, "low": 12.2, "close": 12.45},
                    {"date": "2026-05-06 10:00:00", "open": 12.45, "high": 12.45, "low": 12.1, "close": 12.2},
                    {"date": "2026-05-06 10:30:00", "open": 12.2, "high": 12.8, "low": 12.18, "close": 12.7},
                ],
            }
        },
    }


def test_ai_native_fusion_route_runs_full_contract(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)
    monkeypatch.setattr(agent.radar_api, "get_radar", fake_get_radar)
    latest_queries = []
    monkeypatch.setattr(agent, "_load_latest_ai_native_radar_run", lambda **kwargs: latest_queries.append(kwargs) or None)
    llm = FakeFusionLLM()
    monkeypatch.setattr(agent, "_llm_service", llm)

    from server.services import kronos_service as kronos_module

    events = []

    async def tracked_kronos_multi_level(symbol):
        events.append("kronos")
        return await fake_kronos_multi_level(symbol)

    async def tracked_infer(*args, **kwargs):
        events.append("llm")
        return await FakeFusionLLM.infer_ai_native_radar(llm, *args, **kwargs)

    llm.infer_ai_native_radar = tracked_infer
    monkeypatch.setattr(kronos_module.kronos_service, "get_multi_level_analysis", tracked_kronos_multi_level)

    response = __import__("asyncio").run(
        agent.ai_native_fusion(agent.AINativeFusionRequest(symbol="sh600519", user_id=1))
    )

    assert response["status"] == "success"
    data = response["data"]
    assert "gate" not in data
    assert data["fusion"]["version"] == "ai_fusion_inference.v45"
    assert data["ai_chan_inference"]["version"] == "ai_chan_inference.v70"
    assert data["ai_chan_inference"]["paths"]
    assert data["chan_analysis"]["version"] == "chan_analysis.v45"
    assert data["kronos_forecast"]["version"] == "kronos_forecast.v45"
    assert data["kronos_forecast"]["forecast_mean"]
    assert data["data_alignment"]["primary_data_time"] == "2026-05-05 14:30:00"
    assert "path_probabilities" not in data["kronos_forecast"]
    assert "force_score" not in json.dumps(data["kronos_forecast"], ensure_ascii=False)
    assert "verdict" not in json.dumps(data["kronos_forecast"], ensure_ascii=False)
    assert "resonance_type" not in json.dumps(data["kronos_forecast"], ensure_ascii=False)
    assert data["signals_v2"]["version"] == "semantic_signal.v2"
    assert data["signals_v2"]["state"] == "success"
    assert "kronos_timeline" in data["signals_v2"]["context"]
    assert "kronos_envelope" in data["signals_v2"]["context"]
    diagnostics = data["fusion"]["diagnostics"]
    assert diagnostics["has_ai_chan_inference"] is True
    assert diagnostics["data_alignment"]["primary_data_time"] == "2026-05-05 14:30:00"
    assert diagnostics["radar_ms"] >= 0
    assert diagnostics["transcript_ms"] >= 0
    assert diagnostics["ai_chan_ms"] >= 0
    assert diagnostics["kronos_ms"] >= 0
    assert diagnostics["llm_ms"] >= 0
    assert diagnostics["total_ms"] >= 0
    assert diagnostics["prompt_chars"] > 0
    assert sorted(events[:2]) == ["kronos", "llm"]
    assert events[2] == "llm"
    assert latest_queries[0]["mode"] == "EMPTY"
    assert latest_queries[0]["signal_code"] == "m30_zs_above_breakout_medium"
    ai_chan_context = json.loads(llm.contexts[0])
    assert "forecast_data" not in ai_chan_context
    assert "raw_bi_context" in ai_chan_context
    assert "semantic_signal" in ai_chan_context
    assert ai_chan_context["semantic_signal"]["primary"]["code"] == "m30_zs_above_breakout_medium"
    assert [item["id"] for item in ai_chan_context["semantic_signal"]["deterministic_scenarios"]] == ["A", "B", "C"]
    assert "output_format" in ai_chan_context["rules"]
    assert "ai_chan_inference" in llm.contexts[1]
    assert "kronos_evidence" not in llm.contexts[1]
    assert "forecast_mean" not in llm.contexts[1]
    assert "force_score" not in llm.contexts[1]
    assert "verdict" not in llm.contexts[1]
    assert "resonance_type" not in llm.contexts[1]


def test_ai_native_fusion_rejects_stale_signal_code(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)
    monkeypatch.setattr(agent.radar_api, "get_radar", fake_get_radar)

    try:
        __import__("asyncio").run(
            agent.ai_native_fusion(
                agent.AINativeFusionRequest(
                    symbol="sh600519",
                    user_id=1,
                    signal_code="stale_signal_code",
                )
            )
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "当前信号已变化" in str(getattr(exc, "detail", exc))
    else:
        raise AssertionError("stale signal code should be rejected")


def test_ai_native_fusion_rejects_missing_current_signal_when_expected(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)

    async def fake_get_radar_without_signal(symbol, user_id=1, include_structure=True):
        contract = fusion_radar_contract()
        contract.pop("signals_v2", None)
        return {"status": "success", "data": contract}

    monkeypatch.setattr(agent.radar_api, "get_radar", fake_get_radar_without_signal)

    try:
        __import__("asyncio").run(
            agent.ai_native_fusion(
                agent.AINativeFusionRequest(
                    symbol="sh600519",
                    user_id=1,
                    signal_code="m30_zs_above_breakout_medium",
                )
            )
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "actual=NONE" in str(getattr(exc, "detail", exc))
    else:
        raise AssertionError("missing current signal should be rejected when request carries signal_code")


def test_ai_native_fusion_rejects_stale_structure_fingerprint(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)
    monkeypatch.setattr(agent.radar_api, "get_radar", fake_get_radar)

    try:
        __import__("asyncio").run(
            agent.ai_native_fusion(
                agent.AINativeFusionRequest(
                    symbol="sh600519",
                    user_id=1,
                    signal_code="m30_zs_above_breakout_medium",
                    structure_fingerprint="STRUCTURE|OLD",
                )
            )
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "当前结构已刷新" in str(getattr(exc, "detail", exc))
    else:
        raise AssertionError("stale structure fingerprint should be rejected")


def test_first_stage_reasoning_reuse_rejects_fallback_reason():
    assert agent._usable_first_stage_reasoning(
        {
            "gate_status": "WARN",
            "fallback_reason": "门禁提示：结构事实保护模式",
            "coach_filtered_md": "【当前定位】等待确认\n【三种剧本】A/B/C",
        }
    ) is False


def test_generated_ai_chan_cache_skip_waiting_or_fallback():
    class WaitingInference:
        fallback_reason = "AI Chan 输入数据不足"
        source_versions = {"waiting_triggered": True}

    assert agent._save_generated_ai_chan_reasoning_run(
        user_id=1,
        symbol="sh600519",
        mode="EMPTY",
        transcript=object(),
        ai_chan_inference=WaitingInference(),
    ) is None


async def fake_ai_native_fusion(request):
    action = "REDUCE" if request.mode == "HOLDING" else "OBSERVE"
    symbol = request.symbol
    return {
        "status": "success",
        "data": {
            "fusion": {
                "version": "ai_fusion_inference.v45",
                "symbol": symbol,
                "generated_at": "2026-05-05T15:00:00+08:00",
                "current_judgement": "结构偏弱，按条件降低风险。仅供参考，不构成投资建议",
                "primary_path_id": "C" if request.mode == "HOLDING" else "B",
                "action_playbook": {
                    "action": action,
                    "action_label": "降低风险暴露" if action == "REDUCE" else "观察等待确认",
                    "primary_reason": "Fusion 动作手册输出。仅供参考，不构成投资建议",
                    "reduce_conditions": ["不能重新站回防线"] if action == "REDUCE" else [],
                    "test_conditions": [],
                    "add_conditions": [],
                    "exit_conditions": [],
                    "hold_conditions": [],
                    "max_position_weight_pct": 5 if action == "REDUCE" else None,
                    "recheck_trigger": "NEXT_30M_CLOSE",
                    "risk_note": "只做条件化意图。仅供参考，不构成投资建议",
                },
                "wait_for": ["不能重新站回防线"],
                "invalidation": ["重新修复结构"],
                "disclaimer": "仅供参考，不构成投资建议",
            },
            "chan_analysis": {
                "version": "chan_analysis.v45",
                "primary_level": "30",
                "current_position": "测试结构",
                "structure_state": "TEST",
                "key_levels": [],
            },
            "kronos_forecast": {
                "version": "kronos_forecast.v45",
                "levels": ["30"],
                "regime_shift_score": 0.2,
                "signal_validation": {"force_score": -18},
                "warnings": [],
            },
        },
    }


def test_ai_native_rebalance_route_builds_contract_from_fusion(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)
    monkeypatch.setattr(
        agent,
        "_collect_rebalance_candidates",
        lambda user_id, symbols, sources, max_items: [
            {
                "symbol": "sz002138",
                "name": "顺络电子",
                "is_holding": True,
                "quantity": 3000,
                "weight_pct": 2.62,
                "avg_cost": 35.164,
                "current_price": 33.95,
            },
            {
                "symbol": "sz000988",
                "name": "华工科技",
                "is_holding": False,
            },
        ],
    )
    monkeypatch.setattr(agent, "ai_native_fusion", fake_ai_native_fusion)

    response = __import__("asyncio").run(
        agent.ai_native_rebalance(agent.AINativeRebalanceRequest(user_id=1, max_items=2))
    )

    assert response["status"] == "success"
    data = response["data"]
    assert data["contract_version"] == "ai_native.rebalance.v1"
    assert len(data["intents"]) == 2
    assert data["intents"][0]["recommended_action"]["action"] == "REDUCE"
    assert data["intents"][0]["conditions"]["execute_if"] == ["不能重新站回防线"]
    assert data["intents"][1]["intent_type"] == "WATCH_REPLACEMENT"
    assert "仅供参考" in data["summary"]["coach_message"]


def test_rebalance_memory_reads_prior_playbook_responses():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE daily_playbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL
        );
        CREATE TABLE daily_playbook_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playbook_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            source TEXT,
            status TEXT NOT NULL,
            response_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO daily_playbooks (id, user_id, trade_date)
        VALUES (1, 1, '2026-05-01'), (2, 1, '2026-05-02');
        INSERT INTO daily_playbook_items (
            playbook_id, user_id, symbol, source, status, response_json, created_at, updated_at
        )
        VALUES
            (1, 1, 'sz002138', 'rebalance', 'WATCHING', NULL, '2026-05-01 10:00:00', '2026-05-01 10:00:00'),
            (2, 1, 'sz002138', 'rebalance', 'WATCHING', '{"response":"CONTINUE_WATCHING"}', '2026-05-02 10:00:00', '2026-05-02 14:30:00'),
            (2, 1, 'sz000988', 'watchlist', 'WATCHING', NULL, '2026-05-02 10:00:00', '2026-05-02 10:00:00');
        """
    )

    memory = agent._rebalance_memory_for_symbol(conn, 1, "sz002138")

    assert memory["previous_intent_count"] == 2
    assert memory["first_seen_at"] == "2026-05-01 10:00:00"
    assert memory["last_user_response"] == "CONTINUE_WATCHING"
