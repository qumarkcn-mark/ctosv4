"""Calibration helpers for AI Native Radar batch review."""

from __future__ import annotations

import re
from typing import Iterable

from server.engines.ai_native.schemas import AIReasoningResponse, AllowedPrice


PRICE_LABEL_ALIASES = {
    "confirm": "确认位",
    "maintain": "维持位",
    "observe": "观察位",
    "invalidate": "失效位",
    "support": "支撑位",
    "pressure": "压力位",
    "center": "中枢位",
}


def summarize_reasoning_response(symbol: str, response: AIReasoningResponse) -> dict:
    """把一次 AI Native 推演压缩成可复盘的校准样本。

    校准样本只保留“能指导调参”的字段：门禁、语义过滤、背驰观察、关键价位和教练文本摘要。
    """
    observations = {item.agent_id: item for item in response.agent_observations}
    divergence = observations.get("divergence_agent")
    coach = observations.get("coach_agent")
    key_level = observations.get("key_level_agent")

    return {
        "symbol": symbol,
        "run_id": response.run_id,
        "generated_at": response.generated_at,
        "gate": {
            "status": response.gate_status,
            "score": response.gate_score,
            "fallback_reason": response.fallback_reason or "",
        },
        "semantic_filter_status": response.semantic_filter_status,
        "model_route": response.model_route.model_dump(),
        "divergence": {
            "verdict": divergence.verdict if divergence else "",
            "confidence": divergence.confidence if divergence else 0.0,
            "next_focus": divergence.next_focus if divergence else "",
            "evidence": divergence.evidence[:3] if divergence else [],
        },
        "coach_next_focus": coach.next_focus if coach else "",
        "key_level_next_focus": key_level.next_focus if key_level else "",
        "boundaries": {
            "confirm": _price_lines(response.key_boundaries.confirm),
            "observe": _price_lines(response.key_boundaries.observe or response.key_boundaries.support),
            "invalidate": _price_lines(response.key_boundaries.invalidate),
        },
        "coach_filtered_md": response.coach_filtered_md,
        "raw_reasoning_md": response.raw_reasoning_md,
        "coach_talk": response.coach_filtered_md,
    }


def render_calibration_markdown(items: Iterable[dict]) -> str:
    """渲染给人工复盘看的 Markdown 表格。"""
    rows = [
        "| 股票 | RUN | 门禁 | 语义过滤 | 背驰联动 | 下一步 | 关键价位 |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for item in items:
        gate = item.get("gate") or {}
        divergence = item.get("divergence") or {}
        route = item.get("model_route") or {}
        boundaries = item.get("boundaries") or {}
        price_text = " / ".join(
            part
            for part in (
                _join_prices("确认", boundaries.get("confirm") or []),
                _join_prices("观察", boundaries.get("observe") or []),
                _join_prices("失效", boundaries.get("invalidate") or []),
            )
            if part
        )
        rows.append(
            "| {symbol} | {run_id} | {gate_status} {gate_score} | {filter_status}/{tier} | {divergence} | {focus} | {prices} |".format(
                symbol=item.get("symbol") or "",
                run_id=item.get("run_id") or "",
                gate_status=gate.get("status") or "",
                gate_score=gate.get("score", ""),
                filter_status=item.get("semantic_filter_status") or "",
                tier=route.get("tier") or "auto",
                divergence=divergence.get("verdict") or "",
                focus=(item.get("coach_next_focus") or divergence.get("next_focus") or "")[:80],
                prices=price_text,
            )
        )
    return "\n".join(rows)


def _price_lines(prices: list[AllowedPrice]) -> list[str]:
    lines = []
    for item in prices[:3]:
        if item.value <= 0:
            continue
        label = _friendly_price_label(item.label or item.source)
        value = f"{item.value:.2f}".rstrip("0").rstrip(".")
        label = _strip_duplicate_value(label, value)
        level = f"{item.level} " if item.level else ""
        lines.append(f"{level}{label} {value}")
    return lines


def _join_prices(label: str, values: list[str]) -> str:
    if not values:
        return ""
    return f"{label}: {', '.join(values)}"


def _friendly_price_label(label: str) -> str:
    clean = (label or "").strip()
    return PRICE_LABEL_ALIASES.get(clean, clean or "关键位")


def _strip_duplicate_value(label: str, value: str) -> str:
    return re.sub(rf"\s+{re.escape(value)}(?:\.0+)?$", "", label).strip()
