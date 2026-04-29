"""Machine gate for AI Native Radar output."""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import ValidationError

from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    GateResult,
    GateViolation,
    StructureTranscript,
)


PRICE_TOLERANCE = 0.01
REQUIRED_HYPOTHESES = {"A", "B", "C", "D"}
HARD_FORBIDDEN_TERMS = (
    "必涨",
    "必跌",
    "稳赚",
    "抄底",
    "梭哈",
    "满仓",
    "清仓",
    "买入",
    "卖出",
    "建仓",
    "加仓",
    "减仓",
    "止盈",
    "止损执行",
)


def verify_ai_reasoning(raw_output: dict, transcript: StructureTranscript) -> tuple[AIReasoningOutput | None, GateResult]:
    """Validate model output and decide PASS / REWRITE / FALLBACK."""
    violations: list[GateViolation] = []
    try:
        output = AIReasoningOutput(**raw_output)
    except ValidationError as exc:
        return None, GateResult(
            status="REWRITE",
            score=0,
            violations=[
                GateViolation(
                    code="SCHEMA_INVALID",
                    message="AI 输出不符合 AIReasoningOutput schema",
                    severity="REWRITE",
                    evidence=[str(exc)[:500]],
                )
            ],
        )

    body = _flatten_output(output)
    violations.extend(_forbidden_term_violations(body))
    violations.extend(_hypothesis_violations(output))
    violations.extend(_price_violations(body, transcript))
    if transcript.disclaimer not in output.disclaimer and "仅供参考" not in body:
        violations.append(
            GateViolation(
                code="MISSING_DISCLAIMER",
                message="AI 输出缺少风险提示",
                severity="REWRITE",
            )
        )

    score = _score(violations)
    status = _status(violations)
    return output, GateResult(status=status, score=score, violations=violations)


def _hypothesis_violations(output: AIReasoningOutput) -> list[GateViolation]:
    violations = []
    ids = {item.id for item in output.hypotheses}
    missing = sorted(REQUIRED_HYPOTHESES - ids)
    if missing:
        violations.append(
            GateViolation(
                code="MISSING_HYPOTHESES",
                message="AI 输出没有覆盖完整 A/B/C/D 分类",
                severity="REWRITE",
                evidence=missing,
            )
        )
    if ids == {"A"} or (ids and ids.issubset({"A", "B"})):
        violations.append(
            GateViolation(
                code="BULL_ONLY",
                message="AI 输出只有向上或等待路径，缺少失效/停止推演",
                severity="REWRITE",
                evidence=sorted(ids),
            )
        )
    for item in output.hypotheses:
        if not item.invalidation.strip():
            violations.append(
                GateViolation(
                    code="MISSING_INVALIDATION",
                    message=f"{item.id} 缺少失效条件",
                    severity="REWRITE",
                )
            )
    return violations


def _forbidden_term_violations(body: str) -> list[GateViolation]:
    found = [term for term in HARD_FORBIDDEN_TERMS if term in body]
    if not found:
        return []
    return [
        GateViolation(
            code="FORBIDDEN_TRADING_COMMAND",
            message="AI 输出包含直接交易指令或确定性措辞",
            severity="FALLBACK",
            evidence=found,
        )
    ]


def _price_violations(body: str, transcript: StructureTranscript) -> list[GateViolation]:
    allowed = [round(price.value, 2) for price in transcript.allowed_prices]
    if not allowed:
        return []
    unknown = []
    for number in _extract_price_like_numbers(body):
        if not any(abs(number - value) <= PRICE_TOLERANCE for value in allowed):
            unknown.append(f"{number:.2f}")
    if not unknown:
        return []
    return [
        GateViolation(
            code="UNKNOWN_PRICE",
            message="AI 输出引用了 allowed_prices 之外的价格",
            severity="FALLBACK",
            evidence=sorted(set(unknown)),
        )
    ]


def _extract_price_like_numbers(body: str) -> Iterable[float]:
    for match in re.finditer(r"(?<![\d.])(\d{1,4}(?:\.\d{1,3})?)(?![\d.%])", body):
        raw = match.group(1)
        try:
            value = float(raw)
        except ValueError:
            continue
        # 过滤 A/B/C 编号、K线根数、年份、纯百分比等非价格上下文。
        left = body[max(0, match.start() - 4):match.start()]
        right = body[match.end():match.end() + 4]
        if "年" in right or "根" in right or "%" in right or "分" in right:
            continue
        if value <= 0:
            continue
        yield value


def _flatten_output(output: AIReasoningOutput) -> str:
    parts = [
        output.diagnosis,
        output.current_hypothesis,
        output.reasoning_boundary,
        output.operator_mistake,
        output.coach_talk,
        output.disclaimer,
    ]
    for item in output.hypotheses:
        parts.extend(
            [
                item.id,
                item.name,
                item.current_applicability,
                " ".join(item.evidence),
                item.trigger,
                item.invalidation,
                item.next_focus,
                item.empty_position_view,
                item.holding_position_view,
            ]
        )
    return "\n".join(str(part) for part in parts if part)


def _score(violations: list[GateViolation]) -> int:
    score = 100
    for violation in violations:
        score -= 40 if violation.severity == "FALLBACK" else 18
    return max(score, 0)


def _status(violations: list[GateViolation]) -> str:
    if any(item.severity == "FALLBACK" for item in violations):
        return "FALLBACK"
    if violations:
        return "REWRITE"
    return "PASS"

