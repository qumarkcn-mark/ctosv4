import pytest

from server.scripts.test_intraday_chat_context import _format_summary, _parse_mock_prices


def test_parse_mock_prices_normalizes_symbols() -> None:
    assert _parse_mock_prices(["sh600790=4.33", "300394.SZ=372.88"]) == {
        "sh.600790": 4.33,
        "sz.300394": 372.88,
    }


def test_parse_mock_prices_rejects_invalid_value() -> None:
    with pytest.raises(SystemExit):
        _parse_mock_prices(["sh600790"])


def test_format_summary_includes_crossed_trigger() -> None:
    payload = {
        "version": "dry",
        "generated_at": "2026-05-24T12:00:00",
        "items": [
            {
                "symbol": "sh.600790",
                "prompt_version": "unified_reasoning.v2.full_text",
                "intraday_summary": {"quote": {"price": 4.33}, "coverage": {"quality": "partial"}},
                "questions": [
                    {
                        "question": "现在算突破吗？",
                        "intent_type": "buy_window",
                        "focus_level": "5",
                        "chat_context": {
                            "live_tape": {"price": 4.33},
                            "trigger_state": {
                                "crossed": [
                                    {
                                        "type": "price_above",
                                        "level": 4.3,
                                        "distance_pct": 0.7,
                                        "message_on_trigger": "突破4.30，转强信号",
                                    }
                                ]
                            },
                        },
                    }
                ],
            }
        ],
    }

    summary = _format_summary(payload)

    assert "sh.600790" in summary
    assert "tape_price=4.33" in summary
    assert "crossed: price_above 4.3" in summary
