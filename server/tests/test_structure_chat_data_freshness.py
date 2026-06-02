from server.engines.ai_native import structure_chat_service as svc


def test_chat_data_freshness_prefers_intraday_observation_price():
    freshness = svc._chat_data_freshness(
        context={"context_id": "ctx1", "updated_at": "2026-06-02 10:00:00"},
        data_status={
            "status": "fresh",
            "missing_levels": [],
            "reasoning_status": {"ready": True},
        },
        runtime_context={"chat_current_price": 10.8, "chat_current_price_source": "intraday_quote"},
        intraday_observation={
            "source": "tdx_quote_aggregation",
            "usage": "intraday_preview",
            "as_of": "2026-06-02 10:01:00",
            "quote": {"price": 10.8},
            "coverage": {"quality": "partial"},
        },
        intraday_snapshot={
            "available": True,
            "source": "tdx_lake",
            "usage": "postmarket_1m_reference",
            "date": "2026-06-02",
            "coverage": {"quality": "full"},
        },
    )

    assert freshness["structure_basis"] == "fresh_snapshot_read_only"
    assert freshness["current_price"] == 10.8
    assert freshness["current_price_source"] == "intraday_quote"
    assert freshness["intraday_basis"]["source"] == "tdx_quote_aggregation"
    assert freshness["intraday_basis"]["coverage"]["quality"] == "partial"
    assert freshness["postmarket_1m_basis"]["source"] == "tdx_lake"


def test_chat_context_pack_contains_data_freshness():
    data_freshness = {
        "version": "ai_structure_chat_data_freshness.v1",
        "current_price_source": "watchboard_quote",
    }

    pack = svc._build_chat_context_pack(
        question="现在能买吗",
        intent_type="buy_window",
        intraday_observation={},
        intraday_snapshot={},
        data_freshness=data_freshness,
        reasoning_continuity_context={},
        conversation_context={},
        runtime_context={},
    )

    assert pack["data_freshness"] == data_freshness


def test_chat_without_context_does_not_build_llm_answer(monkeypatch):
    calls = {"llm": 0}

    monkeypatch.setattr(svc, "get_latest_ai_structure_context", lambda **_kwargs: None)
    monkeypatch.setattr(
        svc,
        "upsert_chat_session",
        lambda **_kwargs: {"session_id": "session_1"},
    )
    monkeypatch.setattr(svc, "get_recent_conversation_context", lambda **_kwargs: {})
    monkeypatch.setattr(
        svc,
        "_context_data_status",
        lambda **_kwargs: {"status": "no_snapshot", "missing_levels": ["day"]},
    )
    monkeypatch.setattr(svc, "get_memory_context_for_chat", lambda **_kwargs: {})
    monkeypatch.setattr(
        svc,
        "save_chat_message",
        lambda **_kwargs: {"message_id": "message_1"},
    )

    def fail_llm_path(**_kwargs):
        calls["llm"] += 1
        raise AssertionError("chat without context must not build LLM answer")

    monkeypatch.setattr(svc, "_build_ai_answer_from_full_reasoning", fail_llm_path)

    payload = svc.answer_structure_question(
        user_id=1,
        symbol="sh.600118",
        question="现在能买吗？",
    )

    assert calls["llm"] == 0
    assert payload["context_id"] == ""
    assert payload["data_status"]["status"] == "no_snapshot"
    assert "结构快照" in payload["coach_answer"]
