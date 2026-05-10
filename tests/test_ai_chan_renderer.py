from server.engines.ai_native.ai_chan_renderer import render_ai_chan_markdown
from server.engines.ai_native.fusion_schemas import (
    AIChanInference,
    AIChanPath,
    AIClassificationOutput,
    AIPathScenario,
    LevelPosition,
)


def test_renderer_prefers_level_positions_over_main_deduction():
    inference = AIChanInference(
        symbol="sz002138",
        generated_at="2026-05-08T15:00:00+08:00",
        current_position="当前向上离开中枢。",
        structure_confidence=0.72,
        level_positions=[
            LevelPosition(level="day", position="上涨笔未结束，顶背驰仅是迹象。", key_price=36.29, key_price_label="防守位"),
            LevelPosition(level="30", position="向上离开中枢，等待回踩确认三买。", key_price=36.29, key_price_label="中枢上沿"),
        ],
        synthesis="现价在30分中枢上方，先看36.29是否守住。",
        main_deduction="参与中枢的笔：UP 34.94→36.29，ZG=min(36.29,36.95)=36.29。",
        paths=[
            AIChanPath(
                id="A",
                name="延续/转强",
                description="守住防守位后延续。",
                status="CURRENT",
                entry_condition="回踩不破36.29。",
                invalidation="跌破36.29。",
                chan_basis="30分中枢上方运行。",
                confidence=0.3,
            )
        ],
        defense_line=36.29,
        discipline="守不住36.29就降级处理。仅供参考，不构成投资建议。",
        disclaimer="仅供参考，不构成投资建议",
    )

    markdown = render_ai_chan_markdown(inference)

    assert "现价在30分中枢上方" in markdown
    assert "**日线**" in markdown
    assert "**30分**" in markdown
    assert "参与中枢的笔" not in markdown
    assert "ZG=min" not in markdown


def test_renderer_outputs_realtime_classification_paths():
    inference = AIChanInference(
        symbol="sz300014",
        generated_at="2026-05-09T15:00:00+08:00",
        current_position="现价已跌破5分中枢下沿。",
        structure_confidence=0.68,
        synthesis="现价在5分中枢下方，先看176.87能否收回。",
        classification=AIClassificationOutput(
            current_signal="d1_zs_above_ss1_strong",
            structure_basis="现价低于5分中枢下沿，30分下行笔未确认。",
            paths=[
                AIPathScenario(
                    path_id=1,
                    current_state="现价已跌破5分中枢下沿176.87，等待反抽确认。",
                    description="跌破后反抽不过，确认下行延续",
                    next_boundary="5分中枢下沿 176.87",
                    trigger_condition="反弹高点低于176.87。",
                    target_price=170.0,
                    invalidate_price=176.87,
                    action="等待反抽确认，不追空。仅供参考，不构成投资建议。",
                    evidence=["d1_zs_above_ss1_strong"],
                ),
                AIPathScenario(
                    path_id=2,
                    current_state="现价跌破后尚未确认失效。",
                    description="收回中枢下沿，跌破失败",
                    next_boundary="5分中枢下沿 176.87",
                    trigger_condition="重新站回176.87上方。",
                    target_price=None,
                    invalidate_price=173.0,
                    action="观望，等待新中枢。仅供参考，不构成投资建议。",
                    evidence=["d1_zs_above_ss1_strong"],
                ),
            ],
        ),
        discipline="未确认前不追。仅供参考，不构成投资建议。",
        disclaimer="仅供参考，不构成投资建议",
    )

    markdown = render_ai_chan_markdown(inference)

    assert "AI 完全分类 · 2 条实时路径" in markdown
    assert "路径 1" in markdown
    assert "路径 2" in markdown
    assert "剧本A" not in markdown
    assert "d1_zs_above_ss1_strong" in markdown
