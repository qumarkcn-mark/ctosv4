import json
from io import StringIO

from server.scripts import ctos


def sample_rebalance_response():
    return {
        "status": "success",
        "data": {
            "contract_version": "ai_native.rebalance.v1",
            "run_id": "rebalance_test",
            "user_id": 1,
            "generated_at": "2026-05-05T15:00:00+08:00",
            "valid_until": "2026-05-06T09:30:00+08:00",
            "refresh_trigger": "NEXT_30M_CLOSE",
            "portfolio_state": {
                "position_count": 2,
                "max_position_weight_pct": 24.88,
                "risk_posture": "DEFENSIVE",
                "summary": "优先处理弱结构。仅供参考，不构成投资建议",
            },
            "summary": {
                "immediate_count": 1,
                "next_session_count": 0,
                "conditional_wait_count": 0,
                "watch_only_count": 1,
                "capital_policy": "释放资金先等待确认。仅供参考，不构成投资建议",
                "coach_message": "先处理高风险仓位。仅供参考，不构成投资建议",
            },
            "intents": [
                {
                    "urgency": "IMMEDIATE",
                    "source": {"symbol": "sz002138", "name": "顺络电子"},
                    "recommended_action": {
                        "action": "REDUCE",
                        "action_label": "降低风险暴露",
                        "reason": "结构修复失败。仅供参考，不构成投资建议",
                    },
                    "conditions": {
                        "execute_if": ["不能重新站回防线"],
                        "delay_if": ["重新修复结构"],
                        "invalidate_if": ["站回 35.47"],
                        "recheck_at": "NEXT_30M_CLOSE",
                    },
                    "risk": {"defense_line": 34.94},
                    "evidence": {
                        "fusion_status": {
                            "state": "FALLBACK",
                            "fallback_reason": "AI Fusion 推演超时 45s",
                        }
                    },
                },
                {
                    "urgency": "WATCH_ONLY",
                    "source": {"symbol": "sz000988", "name": "华工科技"},
                    "recommended_action": {
                        "action": "OBSERVE",
                        "action_label": "观察等待确认",
                        "reason": "等待确认。仅供参考，不构成投资建议",
                    },
                    "conditions": {
                        "execute_if": [],
                        "delay_if": ["站回 120.82"],
                        "invalidate_if": ["跌破结构线"],
                        "recheck_at": "NEXT_30M_CLOSE",
                    },
                    "risk": {},
                },
            ],
        },
    }


def sample_fusion_response():
    return {
        "status": "success",
        "data": {
            "fusion": {
                "version": "ai_fusion_inference.v45",
                "symbol": "sz002138",
                "generated_at": "2026-05-05T15:00:00+08:00",
                "current_judgement": "结构偏弱，等待修复。仅供参考，不构成投资建议",
                "primary_path_id": "C",
                "path_inferences": [
                    {
                        "id": "C",
                        "name": "趋势结束",
                        "probability": 0.62,
                    }
                ],
                "action_playbook": {
                    "action": "REDUCE",
                    "action_label": "降低风险暴露",
                    "primary_reason": "风险结构未修复。仅供参考，不构成投资建议",
                    "reduce_conditions": ["不能重新站回防线"],
                    "exit_conditions": ["跌破观察区间"],
                    "max_position_weight_pct": 4.5,
                    "recheck_trigger": "NEXT_5M_CLOSE",
                },
                "diagnostics": {
                    "total_ms": 8400,
                    "llm_ms": 5100,
                    "kronos_ms": 900,
                    "radar_ms": 1200,
                    "transcript_ms": 30,
                    "prompt_chars": 18200,
                },
                "wait_for": ["重新站回防线"],
                "invalidation": ["跌破观察区间"],
            },
            "ai_chan_inference": {
                "version": "ai_chan_inference.v45",
                "symbol": "sz002138",
                "current_position": "30分钟中枢震荡等待确认",
                "structure_confidence": 0.72,
                "primary_path_id": "B",
                "corrections": ["规则路径降级为等待确认"],
                "uncertainty": ["5分钟尚未闭合确认"],
            },
            "chan_analysis": {
                "symbol": "sz002138",
                "primary_level": "30",
                "structure_state": "STRUCTURE_GAP",
            },
            "kronos_forecast": {
                "levels": ["30"],
            },
        },
    }


def sample_fusion_fallback_response():
    payload = sample_fusion_response()
    fusion = payload["data"]["fusion"]
    fusion["primary_path_id"] = "fallback-B"
    fusion["fallback_reason"] = "AI Fusion 推演超时 45s"
    fusion["diagnostics"]["fallback_reason"] = "AI Fusion 推演超时 45s"
    fusion["path_inferences"][0]["id"] = "fallback-B"
    return payload


def sample_import_response():
    return {
        "status": "success",
        "data": {
            "imported_count": 2,
            "item_ids": [10, 11],
            "fusion_status_summary": {"AI_READY": 1, "FALLBACK": 1},
            "playbook": {
                "items": [
                    {"id": 10, "symbol": "sz002138"},
                    {"id": 11, "symbol": "sz000988"},
                ],
            },
        },
    }


def test_format_fusion_response_shows_action_and_paths():
    text = ctos.format_fusion_response(sample_fusion_response()["data"])

    assert "CT-OS AI Native Fusion" in text
    assert "标的: sz002138" in text
    assert "AI Chan 推演" in text
    assert "定位: 30分钟中枢震荡等待确认" in text
    assert "置信度: 72.00%" in text
    assert "修正: 规则路径降级为等待确认" in text
    assert "趋势结束: 62%" in text
    assert "动作: REDUCE 降低风险暴露" in text
    assert "复核: NEXT_5M_CLOSE" in text
    assert "仓位上限: 4.5%" in text
    assert "减仓: 不能重新站回防线" in text
    assert "清仓: 跌破观察区间" in text
    assert "Preview: YES" in text
    assert "Preview: NEXT_5M_CLOSE · BOUNDARY_TOUCHED · DIVERGENT" in text
    assert "状态: AI_READY" in text
    assert "性能诊断" in text
    assert "Total: 8400ms" in text
    assert "Prompt: 18200 chars" in text
    assert "仅供参考" in text


def test_format_fusion_response_shows_fallback_state():
    text = ctos.format_fusion_response(sample_fusion_fallback_response()["data"])

    assert "状态: FALLBACK" in text
    assert "兜底原因: AI Fusion 推演超时 45s" in text
    assert "Fallback: AI Fusion 推演超时 45s" in text
    assert "结构兜底" in text


def test_format_rebalance_contract_groups_actions():
    text = ctos.format_rebalance_contract(sample_rebalance_response()["data"])

    assert "CT-OS AI Native Rebalance" in text
    assert "立即处理: 1" in text
    assert "sz002138 顺络电子 [REDUCE]" in text
    assert "Fusion: FALLBACK · AI Fusion 推演超时 45s" in text
    assert "Fusion 状态: AI_READY 1 / FALLBACK 1" in text
    assert "触发: 不能重新站回防线" in text
    assert "sz000988 华工科技 [OBSERVE]" in text
    assert "仅供参考" in text


def test_rebalance_cli_posts_expected_payload(monkeypatch, capsys):
    calls = {}

    def fake_post_json(url, payload, timeout=120.0):
        calls["url"] = url
        calls["payload"] = payload
        return sample_rebalance_response()

    monkeypatch.setattr(ctos, "post_json", fake_post_json)

    code = ctos.main([
        "--server-url",
        "http://testserver",
        "rebalance",
        "--symbol",
        "sz002138",
        "--symbol",
        "sz000988",
        "--max-items",
        "2",
    ])

    captured = capsys.readouterr()
    assert code == 0
    assert calls["url"] == "http://testserver/api/agent/ai-native-rebalance"
    assert calls["payload"]["symbols"] == ["sz002138", "sz000988"]
    assert calls["payload"]["max_items"] == 2
    assert "今日动作队列" in captured.out


def test_rebalance_cli_json_mode(monkeypatch, capsys):
    monkeypatch.setattr(ctos, "post_json", lambda *args, **kwargs: sample_rebalance_response())

    code = ctos.main(["rebalance", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "success"
    assert payload["data"]["contract_version"] == "ai_native.rebalance.v1"


def test_fusion_cli_posts_expected_payload(monkeypatch, capsys):
    calls = {}

    def fake_post_json(url, payload, timeout=120.0):
        calls["url"] = url
        calls["payload"] = payload
        return sample_fusion_response()

    monkeypatch.setattr(ctos, "post_json", fake_post_json)

    code = ctos.main([
        "--server-url",
        "http://testserver",
        "fusion",
        "--symbol",
        "sz002138",
        "--mode",
        "HOLDING",
    ])

    captured = capsys.readouterr()
    assert code == 0
    assert calls["url"] == "http://testserver/api/agent/ai-native-fusion"
    assert calls["payload"] == {"user_id": 1, "symbol": "sz002138", "mode": "HOLDING"}
    assert "CT-OS AI Native Fusion" in captured.out


def test_playbook_import_rebalance_accepts_wrapped_contract(monkeypatch, capsys):
    calls = {}

    def fake_post_json(url, payload, timeout=120.0):
        calls["url"] = url
        calls["payload"] = payload
        return sample_import_response()

    monkeypatch.setattr(ctos, "post_json", fake_post_json)
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(sample_rebalance_response(), ensure_ascii=False)))

    code = ctos.main([
        "--server-url",
        "http://testserver",
        "playbook",
        "import-rebalance",
        "--trade-date",
        "2026-05-05",
    ])

    captured = capsys.readouterr()
    assert code == 0
    assert calls["url"] == "http://testserver/api/playbook/today/import-rebalance"
    assert calls["payload"]["trade_date"] == "2026-05-05"
    assert calls["payload"]["contract"]["contract_version"] == "ai_native.rebalance.v1"
    assert "已导入/更新: 2" in captured.out
    assert "Fusion 状态: AI_READY 1 / FALLBACK 1" in captured.out
