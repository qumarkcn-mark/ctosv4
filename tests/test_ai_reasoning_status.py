from server.engines.ai_native.structure_context_service import reasoning_availability


def test_reasoning_availability_only_ready_for_successful_llm():
    status = reasoning_availability({
        "reasoning": {
            "reasoning_meta": {
                "provider": "llm",
                "llm_status": "success",
            }
        }
    })

    assert status["ready"] is True
    assert status["status"] == "success"


def test_reasoning_availability_hides_local_fallback():
    status = reasoning_availability({
        "reasoning": {
            "reasoning_meta": {
                "provider": "local_fallback",
                "llm_status": "not_invoked",
            }
        }
    })

    assert status["ready"] is False
    assert status["status"] == "unavailable"
    assert "不展示本地算法边界" in status["message"]


def test_reasoning_availability_hides_failed_llm():
    status = reasoning_availability({
        "reasoning": {
            "reasoning_meta": {
                "provider": "local_fallback",
                "llm_status": "failed",
            }
        }
    })

    assert status["ready"] is False
    assert status["status"] == "failed"
    assert "不展示本地算法边界" in status["message"]
