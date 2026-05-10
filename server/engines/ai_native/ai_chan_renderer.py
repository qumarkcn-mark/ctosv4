"""Render AIChanInference (三段式 JSON) into user-facing Markdown.

V4.5: AI Native Radar 和 AI Fusion 共用同一个推演引擎 (build_ai_chan_inference)。
本模块将结构化推演结果转换为前端展示的 Markdown 格式，保持"当前定位 + 完全分类"
的板块结构。渲染追求教练口吻：紧凑、果断、重点突出。
"""

from __future__ import annotations

from server.engines.ai_native.fusion_schemas import AIChanInference, AIClassificationOutput, TacticalGuide
from server.engines.ai_native.schemas import DISCLAIMER


def render_ai_chan_markdown(inference: AIChanInference) -> str:
    """Convert AIChanInference into the two-section Markdown the frontend expects."""
    sections: list[str] = []

    # ── 第一段：当前定位 ──
    sections.append("**1. 【当前定位】**")
    position_text = _render_position_summary(inference)
    if position_text:
        sections.append(position_text)
    elif inference.main_deduction:
        sections.append(inference.main_deduction)
    else:
        sections.append(inference.current_position)

    # 实战指引紧跟定位，一段话说完
    if inference.tactical_guide:
        guide_text = _render_tactical_guide(inference.tactical_guide)
        if guide_text:
            sections.append("")
            sections.append(guide_text)

    # ── 第二段：实时完全分类 ──
    sections.append("")
    if inference.classification and inference.classification.paths:
        sections.extend(_render_realtime_classification(inference.classification))
    elif inference.paths:
        path_count = len(inference.paths)
        sections.append(f"**2. 【AI 完全分类 · {path_count} 条实时路径】**")
        for i, path in enumerate(inference.paths[:5], start=1):
            sections.append(f"\n**路径 {i}：{path.name}**")
            if path.description:
                sections.append(path.description)
            sections.append(f"- 当前状态：{path.description}")
            sections.append(f"- 下一边界：{path.entry_condition}")
            sections.append(f"- 触发条件：{path.entry_condition}")
            sections.append("- 目标价：—")
            sections.append(f"- 失效条件：{path.invalidation}")
            sections.append("- 操作指令：等待确认。")
    else:
        sections.append("**2. 【AI 完全分类 · 0 条实时路径】**")
        sections.append("当前结构不清晰，等待新笔、新中枢或近端买卖点补齐后再判断。")

    # 防守更新规则
    if getattr(inference, 'defense_update_rule', None):
        sections.append(f"\n**防守更新：**{inference.defense_update_rule}")

    # 纪律（含免责声明）
    if inference.discipline:
        sections.append(f"\n**纪律：**{inference.discipline}")

    # 修正和不确定性（只展示短结论，避免把推演草稿放到用户界面）
    extras: list[str] = []
    corrections = _compact_notes(inference.corrections)
    uncertainty = _compact_notes(inference.uncertainty)
    if corrections:
        extras.append(f"AI 修正：{'；'.join(corrections)}")
    if uncertainty:
        extras.append(f"不确定因素：{'；'.join(uncertainty)}")
    if extras:
        sections.append("\n" + "\n".join(extras))

    # 兜底免责
    body = "\n".join(sections)
    if "仅供参考" not in body:
        sections.append(f"\n{inference.disclaimer or DISCLAIMER}")

    return "\n".join(sections)


def _render_realtime_classification(classification: AIClassificationOutput) -> list[str]:
    lines: list[str] = []
    count = len(classification.paths)
    lines.append(f"**2. 【AI 完全分类 · {count} 条实时路径】**")
    if classification.current_signal:
        lines.append(f"当前信号：`{classification.current_signal}`")
    if classification.structure_basis:
        lines.append(f"结构依据：{classification.structure_basis}")

    for path in classification.paths[:6]:
        lines.append(f"\n**路径 {path.path_id}：{path.description}**")
        lines.append(f"- 当前状态：{path.current_state}")
        lines.append(f"- 下一边界：{path.next_boundary}")
        lines.append(f"- 触发条件：{path.trigger_condition}")
        target = f"{path.target_price:.2f}" if path.target_price is not None else "—"
        lines.append(f"- 目标价：{target}")
        invalidate = f"{path.invalidate_price:.2f}" if path.invalidate_price is not None else "—"
        lines.append(f"- 失效条件：{invalidate}")
        confirmation = "需要确认" if path.requires_confirmation else "无需额外确认"
        lines.append(f"- 操作指令：{path.action}（{confirmation}）")
        if path.evidence:
            lines.append(f"- 证据：{', '.join(path.evidence[:4])}")
    return lines


def _render_position_summary(inference: AIChanInference) -> str:
    lines: list[str] = []
    if inference.synthesis:
        lines.append(inference.synthesis)
    elif inference.current_position:
        lines.append(inference.current_position)

    for item in _ordered_level_positions(inference):
        label = _level_label(item.level)
        key_text = ""
        if item.key_price is not None:
            key_label = item.key_price_label or "关键价"
            key_text = f"（{key_label} {item.key_price:.2f}）"
        lines.append(f"- **{label}**：{item.position}{key_text}")
    return "\n".join(line for line in lines if line).strip()


def _ordered_level_positions(inference: AIChanInference):
    order = {"week": 0, "day": 1, "60": 2, "30": 3, "15": 4, "5": 5}
    return sorted(
        inference.level_positions,
        key=lambda item: order.get(str(item.level), 99),
    )


def _level_label(level: str) -> str:
    mapping = {
        "week": "周线",
        "day": "日线",
        "60": "60分",
        "30": "30分",
        "15": "15分",
        "5": "5分",
    }
    return mapping.get(str(level), str(level))


def _compact_notes(items: list[str]) -> list[str]:
    compact: list[str] = []
    for item in items:
        text = " ".join(str(item).strip().split())
        if not text or _looks_like_calculation_draft(text):
            continue
        compact.append(text[:140])
        if len(compact) >= 3:
            break
    return compact


def _looks_like_calculation_draft(text: str) -> bool:
    markers = ("ZG=min", "ZD=max", "参与中枢的笔", "从笔序列看", "取笔", "由笔")
    return any(marker in text for marker in markers)


def _render_tactical_guide(guide: TacticalGuide) -> str:
    """Render tactical guide as a compact coach-style paragraph."""
    parts: list[str] = []

    # 空间距离：一句话说完现价、防守位、距离
    if guide.current_price and guide.defense_price:
        pct = guide.space_to_defense_pct
        if pct is None and guide.defense_price > 0:
            pct = abs(guide.current_price - guide.defense_price) / guide.defense_price * 100
        pct_text = f"（{pct:.1f}%）" if pct is not None else ""
        parts.append(f"现价 {guide.current_price:.2f}，防守位 {guide.defense_price:.2f}{pct_text}")

    # 即时策略
    if guide.immediate_action:
        parts.append(guide.immediate_action)

    # 把空间距离+即时策略合成一句
    headline = "。".join(parts) + "。" if parts else ""

    # 操作区间：试仓/加仓/止损合成一段
    ops: list[str] = []
    if guide.test_zone:
        basis = f"，{guide.test_basis}" if guide.test_basis else ""
        ops.append(f"试仓看 {guide.test_zone[0]:.2f}-{guide.test_zone[1]:.2f}{basis}")
    if guide.add_zone:
        basis = f"，{guide.add_basis}" if guide.add_basis else ""
        ops.append(f"加仓确认 {guide.add_zone[0]:.2f}-{guide.add_zone[1]:.2f}{basis}")
    if guide.stop_anchor:
        basis = f"（{guide.stop_basis}）" if guide.stop_basis else ""
        ops.append(f"止损 {guide.stop_anchor:.2f}{basis}")

    ops_text = "；".join(ops) + "。" if ops else ""

    # 盈亏比单独一句
    rr_text = f"{guide.risk_reward_note}。" if guide.risk_reward_note else ""

    return " ".join(filter(None, [headline, ops_text, rr_text])).strip()
