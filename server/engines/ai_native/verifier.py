"""Deterministic gate for AI Native Radar commander Markdown output.

2026-05-01 重构：
- Semantic Coach Filter（第二次 LLM 调用）已移除。
- 教练用语（买入/卖出/止损/清仓/接飞刀等）允许出现在推演中，
  因为它们都绑定了结构条件，不是裸喊单。
- 本 verifier 只做确定性检查：
  1. 三段式结构完整性
  2. 真正危险的确定性/极端交易词
  3. A 股口径做空词拦截
  4. 价格越界检测
  5. 免责声明
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    GateResult,
    GateViolation,
    StructureTranscript,
)


PRICE_TOLERANCE = 0.01

# 真正危险的词：确定性承诺、极端交易行为
HARD_FORBIDDEN_TERMS = (
    "必涨",
    "必跌",
    "稳赚",
    "梭哈",
    "满仓",
    "保证盈利",
    "包赚",
)

# A 股口径：普通股票不能做空，拦截做空执行词
A_SHARE_SHORT_TERMS = (
    "做空",
    "开空",
    "加空",
    "空头持仓",
    "空头目标",
    "融券卖出",
)
REQUIRED_SECTIONS = (
    "【全局语境定性】",
    "【防守看门狗】",
    "【推演与应对沙盘】",
)
REQUIRED_SECTION_ALIASES = (
    ("【全局语境定性】", "全局语境定性"),
    ("【防守看门狗】", "防守看门狗"),
    ("【推演与应对沙盘】", "推演与应对沙盘"),
)


def verify_ai_reasoning(raw_output: dict, transcript: StructureTranscript) -> tuple[AIReasoningOutput | None, GateResult]:
    """Deterministic gate：结构完整性 + 危险词 + 价格校验 + 免责声明。

    教练用语（买入/卖出/止损/清仓/接飞刀）允许出现，
    只要价格来自 Evidence Pack 且不含确定性承诺。
    """
    try:
        output = AIReasoningOutput(**raw_output)
    except ValidationError as exc:
        return None, GateResult(
            status="REWRITE",
            score=0,
            violations=[
                GateViolation(
                    code="SCHEMA_INVALID",
                    message="AI 输出不符合 Markdown 推演 schema",
                    severity="REWRITE",
                    evidence=[str(exc)[:500]],
                )
            ],
        )

    body = output.coach_filtered_md or ""
    violations: list[GateViolation] = []
    violations.extend(_required_section_violations(body))
    violations.extend(_forbidden_term_violations(body))
    violations.extend(_price_violations(body, transcript))
    if "仅供参考" not in body and "仅供参考" not in output.disclaimer:
        violations.append(
            GateViolation(
                code="MISSING_DISCLAIMER",
                message="用户可见推演缺少风险提示",
                severity="REWRITE",
            )
        )

    status = _status(violations)
    score = _score(violations)
    output.semantic_filter_status = status
    output.semantic_filter_violations = [item.model_dump() for item in violations]
    return output, GateResult(status=status, score=score, violations=violations)


def _required_section_violations(body: str) -> list[GateViolation]:
    missing = [aliases[0] for aliases in REQUIRED_SECTION_ALIASES if not any(alias in body for alias in aliases)]
    if not missing:
        return []
    return [
        GateViolation(
            code="MISSING_MARKDOWN_SECTIONS",
            message="用户可见推演缺少固定三段式结构",
            severity="REWRITE",
            evidence=missing,
        )
    ]


def _forbidden_term_violations(body: str) -> list[GateViolation]:
    violations = []
    hard_hits = [term for term in HARD_FORBIDDEN_TERMS if term in body]
    if hard_hits:
        violations.append(
            GateViolation(
                code="HARD_FORBIDDEN_TERMS",
                message="推演包含确定性承诺或极端交易词",
                severity="FALLBACK",
                evidence=hard_hits,
            )
        )
    short_hits = [term for term in A_SHARE_SHORT_TERMS if term in body]
    if short_hits:
        violations.append(
            GateViolation(
                code="A_SHARE_SHORT_SELLING",
                message="A 股普通股票场景不允许做空执行建议",
                severity="REWRITE",
                evidence=short_hits,
            )
        )
    return violations


def _price_violations(body: str, transcript: StructureTranscript) -> list[GateViolation]:
    allowed = [float(item.value) for item in transcript.allowed_prices if item.value]
    evidence_pack = transcript.reasoning_evidence_pack or {}
    current = evidence_pack.get("current_price")
    if current:
        allowed.append(float(current))
    allowed.extend(_evidence_pack_prices(evidence_pack))
    if not allowed:
        return []

    unknown = []
    for token in re.findall(r"(?<![\w.])\d{1,4}\.\d{1,3}(?![\w.%％])", body):
        price = float(token)
        if not any(abs(price - item) <= PRICE_TOLERANCE for item in allowed):
            unknown.append(token)
    if not unknown:
        return []
    return [
        GateViolation(
            code="UNKNOWN_PRICE_REFERENCE",
            message="用户可见推演引用了 Evidence Pack 之外的价格",
            severity="REWRITE",
            evidence=sorted(set(unknown))[:8],
        )
    ]


def _evidence_pack_prices(value: object) -> list[float]:
    prices: list[float] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"price", "value", "current_price", "intraday_high", "intraday_low", "prev_close"}:
                try:
                    price = float(item)
                except (TypeError, ValueError):
                    price = 0.0
                if price > 0:
                    prices.append(price)
            elif isinstance(item, (dict, list, tuple)):
                prices.extend(_evidence_pack_prices(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            prices.extend(_evidence_pack_prices(item))
    return prices


def _score(violations: list[GateViolation]) -> int:
    score = 100
    for item in violations:
        score -= 45 if item.severity == "FALLBACK" else 18
    return max(score, 0)


def _status(violations: list[GateViolation]) -> str:
    if any(item.severity == "FALLBACK" for item in violations):
        return "FALLBACK"
    if violations:
        return "REWRITE"
    return "PASS"
