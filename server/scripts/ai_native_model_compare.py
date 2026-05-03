#!/usr/bin/env python3
"""Compare AI Native Radar output quality across model routes."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server import config
from server.api import radar as radar_api
from server.db.database import init_db
from server.engines.ai_native.case_memory import find_similar_cases
from server.engines.ai_native.hypothesis_reasoner import infer_ai_hypotheses
from server.engines.ai_native.reasoning_orchestrator import _attach_tactical_structure
from server.engines.ai_native.schemas import GateResult, GateViolation, ModelRoute, StructureTranscript
from server.engines.ai_native.transcript_compiler import compile_structure_transcript
from server.engines.ai_native.verifier import verify_ai_reasoning
from server.services.llm_service import LLMService


DEFAULT_SYMBOLS = ["sz002138", "sz002176", "sz300124"]
DEFAULT_ROUTES = ["flash", "pro"]


def route_for(name: str) -> ModelRoute:
    if name == "flash":
        return ModelRoute(
            tier="simple",
            difficulty_score=0,
            model_name="deepseek-v4-flash",
            thinking_enabled=False,
            reasoning_effort="high",
            max_tokens=min(config.AI_NATIVE_RADAR_MAX_TOKENS, 2048),
            timeout_seconds=min(config.AI_NATIVE_RADAR_LLM_TIMEOUT, 45),
            reasons=["model_compare: flash no-thinking baseline"],
        )
    if name == "pro":
        return ModelRoute(
            tier="hard",
            difficulty_score=0,
            model_name=config.AI_NATIVE_RADAR_MODEL or "deepseek-v4-pro",
            thinking_enabled=False,
            reasoning_effort="high",
            max_tokens=config.AI_NATIVE_RADAR_MAX_TOKENS,
            timeout_seconds=config.AI_NATIVE_RADAR_LLM_TIMEOUT,
            reasons=["model_compare: pro no-thinking baseline"],
        )
    if name == "pro-thinking":
        return ModelRoute(
            tier="hard",
            difficulty_score=0,
            model_name=config.AI_NATIVE_RADAR_MODEL or "deepseek-v4-pro",
            thinking_enabled=True,
            reasoning_effort="high",
            max_tokens=config.AI_NATIVE_RADAR_MAX_TOKENS,
            timeout_seconds=config.AI_NATIVE_RADAR_LLM_TIMEOUT,
            reasons=["model_compare: pro thinking high reference"],
        )
    raise ValueError(f"Unknown route: {name}")


async def build_transcript(symbol: str, *, user_id: int, mode: str | None) -> StructureTranscript:
    radar_response = await radar_api.get_radar(symbol, user_id=user_id, include_structure=True)
    radar_contract = radar_response.get("data") or {}
    await _attach_tactical_structure(radar_contract)
    transcript = compile_structure_transcript(radar_contract)
    if mode in {"EMPTY", "HOLDING"} and transcript.mode != mode:
        transcript.mode = mode  # type: ignore[misc]
    return transcript


async def run_route(
    *,
    symbol: str,
    transcript: StructureTranscript,
    route_name: str,
    route: ModelRoute,
    user_id: int,
    llm_service: LLMService,
) -> dict[str, Any]:
    memory = find_similar_cases(transcript)
    started = time.perf_counter()
    try:
        raw_output = await infer_ai_hypotheses(
            user_id=user_id,
            transcript=transcript,
            similar_cases=memory,
            llm_service=llm_service,
            model_route=route,
        )
        output, gate = verify_ai_reasoning(raw_output, transcript)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "symbol": symbol,
            "route": route_name,
            "elapsed_ms": elapsed_ms,
            "model_route": route.model_dump(),
            "gate": gate.model_dump(),
            "raw_reasoning_md": raw_output.get("raw_reasoning_md", ""),
            "coach_filtered_md": raw_output.get("coach_filtered_md", ""),
            "semantic_filter_status": output.semantic_filter_status if output else "SCHEMA_INVALID",
            "semantic_filter_violations": [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in (output.semantic_filter_violations if output else gate.violations)
            ],
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        gate = GateResult(
            status="FALLBACK",
            score=0,
            violations=[
                GateViolation(
                    code="MODEL_COMPARE_ERROR",
                    message=f"{type(exc).__name__}: {str(exc)[:300]}",
                    severity="FALLBACK",
                )
            ],
        )
        return {
            "symbol": symbol,
            "route": route_name,
            "elapsed_ms": elapsed_ms,
            "model_route": route.model_dump(),
            "gate": gate.model_dump(),
            "raw_reasoning_md": "",
            "coach_filtered_md": "",
            "semantic_filter_status": "FALLBACK",
            "semantic_filter_violations": [item.model_dump() for item in gate.violations],
        }


async def compare_symbols(symbols: list[str], routes: list[str], user_id: int, mode: str | None) -> dict[str, Any]:
    init_db()
    llm_service = LLMService()
    rows = []
    for symbol in symbols:
        transcript = await build_transcript(symbol, user_id=user_id, mode=mode)
        route_rows = []
        for route_name in routes:
            route_rows.append(
                await run_route(
                    symbol=symbol,
                    transcript=transcript,
                    route_name=route_name,
                    route=route_for(route_name),
                    user_id=user_id,
                    llm_service=llm_service,
                )
            )
        rows.append(
            {
                "symbol": symbol,
                "generated_at": transcript.generated_at,
                "mode": transcript.mode,
                "structure_fingerprint": transcript.structure_fingerprint,
                "primary_context": (transcript.reasoning_evidence_pack.get("commander_context") or {}).get("primary_context"),
                "must_use_levels": (transcript.reasoning_evidence_pack.get("commander_context") or {}).get("must_use_levels"),
                "routes": route_rows,
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id,
        "mode": mode,
        "symbols": symbols,
        "routes": routes,
        "items": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AI Native Model Compare",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- routes: `{', '.join(report['routes'])}`",
        f"- symbols: `{', '.join(report['symbols'])}`",
        "",
        "## Review Rubric",
        "",
        "- 结构事实是否准",
        "- 关键价位是否准",
        "- 剧本是否有操作意义",
        "- 是否不乱做空",
        "- 文风是否像交易教练",
        "",
    ]
    for item in report["items"]:
        lines.extend(
            [
                f"## {item['symbol']}",
                "",
                f"- fingerprint: `{item['structure_fingerprint']}`",
                f"- primary_context: `{json.dumps(item.get('primary_context'), ensure_ascii=False)}`",
                f"- must_use_levels: `{json.dumps(item.get('must_use_levels'), ensure_ascii=False)}`",
                "",
            ]
        )
        for route in item["routes"]:
            gate = route["gate"]
            lines.extend(
                [
                    f"### {route['route']} ({route['model_route']['model_name']})",
                    "",
                    f"- elapsed: `{route['elapsed_ms']}ms`",
                    f"- gate: `{gate['status']}` score `{gate['score']}`",
                    f"- violations: `{json.dumps(gate.get('violations') or [], ensure_ascii=False)}`",
                    "",
                    route["coach_filtered_md"] or "_No output_",
                    "",
                ]
            )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare AI Native Radar model output quality")
    parser.add_argument("symbols", nargs="*", help="股票代码，例如 sz002138 sz002176 sz300124")
    parser.add_argument("--routes", nargs="+", choices=["flash", "pro", "pro-thinking"], default=DEFAULT_ROUTES)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--mode", choices=["EMPTY", "HOLDING"], default=None)
    parser.add_argument("--out-dir", default=str(Path(config.AI_NATIVE_RADAR_DATA_DIR) / "model_compare"))
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = args.symbols or DEFAULT_SYMBOLS
    report = await compare_symbols(symbols=symbols, routes=args.routes, user_id=args.user_id, mode=args.mode)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"model_compare_{stamp}.json"
    md_path = out_dir / f"model_compare_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    if args.format == "json":
        print(json.dumps({"json": str(json_path), "markdown": str(md_path), "report": report}, ensure_ascii=False, indent=2))
    else:
        print(f"Wrote JSON: {json_path}")
        print(f"Wrote Markdown: {md_path}")
        print(render_markdown(report))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
