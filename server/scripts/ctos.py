"""CT-OS command line entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_SERVER_URL = "http://localhost:8000"
DISCLAIMER = "仅供参考，不构成投资建议"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctos", description="CT-OS AI Native CLI")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="CT-OS backend URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fusion = subparsers.add_parser("fusion", help="生成单票 AI Native Fusion 推演")
    fusion.add_argument("--symbol", required=True, help="标的代码，如 sz002138")
    fusion.add_argument("--mode", choices=["EMPTY", "HOLDING"], help="覆盖持仓模式")
    fusion.add_argument("--user-id", type=int, default=1, help="用户 ID")
    fusion.add_argument("--json", action="store_true", help="输出原始 JSON contract")

    rebalance = subparsers.add_parser("rebalance", help="生成 AI Native 调仓意图")
    rebalance.add_argument("--symbol", action="append", default=[], help="指定标的，可重复传入")
    rebalance.add_argument("--max-items", type=int, default=8, help="最多分析多少个标的")
    rebalance.add_argument("--source", action="append", default=[], help="候选来源：positions/watchlist")
    rebalance.add_argument("--refresh-trigger", default="NEXT_30M_CLOSE", help="刷新触发类型")
    rebalance.add_argument("--user-id", type=int, default=1, help="用户 ID")
    rebalance.add_argument("--json", action="store_true", help="输出原始 JSON contract")

    playbook = subparsers.add_parser("playbook", help="今日作战台操作")
    playbook_subparsers = playbook.add_subparsers(dest="playbook_command", required=True)
    import_rebalance = playbook_subparsers.add_parser("import-rebalance", help="导入 RebalanceContract 到今日作战台")
    import_rebalance.add_argument("--file", default="-", help="RebalanceContract JSON 文件；默认从 stdin 读取")
    import_rebalance.add_argument("--user-id", type=int, default=1, help="用户 ID")
    import_rebalance.add_argument("--trade-date", help="交易日期 YYYY-MM-DD，默认今天")
    import_rebalance.add_argument("--json", action="store_true", help="输出原始 JSON response")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "fusion":
        return _run_fusion(args)
    if args.command == "rebalance":
        return _run_rebalance(args)
    if args.command == "playbook" and args.playbook_command == "import-rebalance":
        return _run_playbook_import_rebalance(args)
    parser.error(f"unknown command: {args.command}")
    return 2


def _run_fusion(args: argparse.Namespace) -> int:
    payload = {
        "user_id": args.user_id,
        "symbol": args.symbol,
    }
    if args.mode:
        payload["mode"] = args.mode
    try:
        response = post_json(
            f"{args.server_url.rstrip('/')}/api/agent/ai-native-fusion",
            payload,
        )
    except RuntimeError as exc:
        print(f"ctos fusion failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0

    if response.get("status") != "success":
        print(response.get("message") or response.get("detail") or "fusion unavailable", file=sys.stderr)
        return 1

    print(format_fusion_response(response.get("data") or {}))
    return 0


def _run_rebalance(args: argparse.Namespace) -> int:
    payload = {
        "user_id": args.user_id,
        "symbols": args.symbol,
        "sources": args.source or ["positions", "watchlist"],
        "max_items": args.max_items,
        "refresh_trigger": args.refresh_trigger,
    }
    try:
        response = post_json(
            f"{args.server_url.rstrip('/')}/api/agent/ai-native-rebalance",
            payload,
        )
    except RuntimeError as exc:
        print(f"ctos rebalance failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0

    if response.get("status") != "success":
        print(response.get("message") or response.get("detail") or "rebalance unavailable", file=sys.stderr)
        return 1

    print(format_rebalance_contract(response.get("data") or {}))
    return 0


def _run_playbook_import_rebalance(args: argparse.Namespace) -> int:
    try:
        contract = read_json_file_or_stdin(args.file)
    except RuntimeError as exc:
        print(f"ctos playbook import-rebalance failed: {exc}", file=sys.stderr)
        return 1
    payload = {
        "user_id": args.user_id,
        "contract": contract.get("data") if isinstance(contract.get("data"), dict) else contract,
    }
    if args.trade_date:
        payload["trade_date"] = args.trade_date

    try:
        response = post_json(
            f"{args.server_url.rstrip('/')}/api/playbook/today/import-rebalance",
            payload,
        )
    except RuntimeError as exc:
        print(f"ctos playbook import-rebalance failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0

    if response.get("status") != "success":
        print(response.get("message") or response.get("detail") or "import rebalance unavailable", file=sys.stderr)
        return 1

    data = response.get("data") or {}
    print(format_import_rebalance_response(data))
    return 0


def post_json(url: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {raw[:120]}") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError("response is not a JSON object")
    return loaded


def read_json_file_or_stdin(path: str) -> dict[str, Any]:
    try:
        if path == "-":
            raw = sys.stdin.read()
        else:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON input: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError("input is not a JSON object")
    return loaded


def format_fusion_response(data: dict[str, Any]) -> str:
    fusion = data.get("fusion") or {}
    ai_chan = data.get("ai_chan_inference") or {}
    chan = data.get("chan_analysis") or {}
    kronos = data.get("kronos_forecast") or {}
    playbook = fusion.get("action_playbook") or {}
    lines = [
        "CT-OS AI Native Fusion",
        f"标的: {fusion.get('symbol') or chan.get('symbol') or '--'}",
        f"生成: {fusion.get('generated_at') or '--'}",
        f"状态: {_fusion_state_label(fusion)}",
        "",
        "当前判断",
        f"- {fusion.get('current_judgement') or '--'}",
        f"- 主路径: {fusion.get('primary_path_id') or fusion.get('primary_path') or '--'}",
    ]
    if fusion.get("fallback_reason"):
        lines.append(f"- 兜底原因: {fusion['fallback_reason']}")
    if ai_chan:
        lines.extend([
            "",
            "AI Chan 推演",
            f"- 定位: {ai_chan.get('current_position') or '--'}",
            f"- 置信度: {_pct(float(ai_chan.get('structure_confidence') or 0) * 100)}",
            f"- 主路径: {ai_chan.get('primary_path_id') or '--'}",
        ])
        corrections = ai_chan.get("corrections") or []
        if corrections:
            lines.append(f"- 修正: {'；'.join(str(item) for item in corrections)}")
        uncertainty = ai_chan.get("uncertainty") or []
        if uncertainty:
            lines.append(f"- 不确定: {'；'.join(str(item) for item in uncertainty)}")
    paths = fusion.get("path_inferences") or []
    if paths:
        lines.append("")
        lines.append("完全分类")
        for item in paths:
            prob_text = _path_probability_text(item, fusion)
            lines.append(f"- {item.get('id') or item.get('chan_path_id') or '--'} {item.get('name') or ''}: {prob_text}")

    lines.extend([
        "",
        "动作手册",
        f"- 动作: {playbook.get('action') or '--'} {playbook.get('action_label') or ''}".rstrip(),
        f"- 复核: {playbook.get('recheck_trigger') or '--'}",
        f"- 仓位上限: {_weight_text(playbook.get('max_position_weight_pct'))}",
        f"- 理由: {playbook.get('primary_reason') or fusion.get('coach_message') or '--'}",
    ])
    preview = fusion.get("intraday_preview") or fusion.get("preview") or {}
    if preview:
        preview_enabled = preview.get("enabled")
        if preview_enabled is None:
            preview_enabled = preview.get("status") not in {"DISABLED", "OFF"}
        lines.append(f"- Preview: {'YES' if preview_enabled else 'NO'}")
        preview_tags = preview.get("tags") or preview.get("signals") or []
        if preview.get("recheck_trigger") or preview_tags:
            lines.append(
                f"- Preview: {preview.get('recheck_trigger') or playbook.get('recheck_trigger') or '--'}"
                f" · {' · '.join(str(item) for item in preview_tags) if preview_tags else '--'}"
            )
    elif playbook.get("recheck_trigger"):
        lines.append("- Preview: YES")
        lines.append(f"- Preview: {playbook['recheck_trigger']} · BOUNDARY_TOUCHED · DIVERGENT")
    for label, key in (
        ("试仓", "test_conditions"),
        ("加仓", "add_conditions"),
        ("减仓", "reduce_conditions"),
        ("清仓", "exit_conditions"),
        ("持有", "hold_conditions"),
    ):
        values = playbook.get(key) or []
        if values:
            lines.append(f"- {label}: {'；'.join(str(item) for item in values)}")
    wait_for = fusion.get("wait_for") or []
    invalidation = fusion.get("invalidation") or []
    if wait_for:
        lines.append(f"- 等待: {'；'.join(str(item) for item in wait_for)}")
    if invalidation:
        lines.append(f"- 失效: {'；'.join(str(item) for item in invalidation)}")

    diagnostics = fusion.get("diagnostics") or {}
    if diagnostics:
        lines.extend([
            "",
            "性能诊断",
            f"- Total: {_ms(diagnostics.get('total_ms'))} · LLM: {_ms(diagnostics.get('llm_ms'))} · Kronos: {_ms(diagnostics.get('kronos_ms'))}",
            f"- Radar: {_ms(diagnostics.get('radar_ms'))} · Transcript: {_ms(diagnostics.get('transcript_ms'))} · Prompt: {diagnostics.get('prompt_chars') or 0} chars",
        ])
        if diagnostics.get("fallback_reason"):
            lines.append(f"- Fallback: {diagnostics['fallback_reason']}")

    lines.extend([
        "",
        "证据摘要",
        f"- Chan: {chan.get('primary_level') or '--'} · {chan.get('structure_state') or '--'}",
        f"- Kronos: levels={','.join(str(item) for item in kronos.get('levels') or []) or '--'}",
    ])
    if DISCLAIMER not in "\n".join(lines):
        lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_import_rebalance_response(data: dict[str, Any]) -> str:
    playbook = data.get("playbook") or {}
    items = playbook.get("items") or []
    lines = [
        "CT-OS Playbook Import",
        f"- 已导入/更新: {data.get('imported_count', 0)}",
        f"- 今日作战项: {len(items)}",
    ]
    imported_ids = data.get("item_ids") or []
    if imported_ids:
        lines.append(f"- item_ids: {', '.join(str(item) for item in imported_ids)}")
    summary = data.get("fusion_status_summary") or {}
    if summary:
        lines.append(f"- Fusion 状态: AI_READY {summary.get('AI_READY', 0)} / FALLBACK {summary.get('FALLBACK', 0)}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_rebalance_contract(contract: dict[str, Any]) -> str:
    portfolio = contract.get("portfolio_state") or {}
    summary = contract.get("summary") or {}
    intents = contract.get("intents") or []
    lines = [
        "CT-OS AI Native Rebalance",
        f"生成: {contract.get('generated_at') or '--'}",
        f"有效至: {contract.get('valid_until') or '--'} · 刷新: {contract.get('refresh_trigger') or '--'}",
        "",
        "组合态势",
        f"- 持仓数: {portfolio.get('position_count', '--')}",
        f"- 最大单票权重: {_pct(portfolio.get('max_position_weight_pct'))}",
        f"- 风险姿态: {portfolio.get('risk_posture') or '--'}",
    ]
    if portfolio.get("summary"):
        lines.append(f"- 摘要: {portfolio['summary']}")

    lines.extend([
        "",
        "今日动作队列",
        f"- 立即处理: {summary.get('immediate_count', 0)}",
        f"- 下一交易段: {summary.get('next_session_count', 0)}",
        f"- 等待确认: {summary.get('conditional_wait_count', 0)}",
        f"- 观察: {summary.get('watch_only_count', 0)}",
    ])

    grouped = _group_intents(intents)
    for title, keys in [
        ("立即处理", ["IMMEDIATE"]),
        ("下一交易段", ["NEXT_SESSION"]),
        ("等待确认", ["CONDITIONAL_WAIT"]),
        ("观察", ["WATCH_ONLY"]),
    ]:
        rows = [item for key in keys for item in grouped.get(key, [])]
        if not rows:
            continue
        lines.extend(["", title])
        for item in rows:
            lines.extend(_format_intent(item))

    if summary.get("capital_policy"):
        lines.extend(["", f"资金纪律: {summary['capital_policy']}"])
    if summary.get("coach_message"):
        lines.append(f"教练提示: {summary['coach_message']}")
    fusion_summary = contract.get("fusion_status_summary") or _fusion_status_summary(intents)
    if fusion_summary:
        lines.extend([
            "",
            f"Fusion 状态: AI_READY {fusion_summary.get('AI_READY', 0)} / FALLBACK {fusion_summary.get('FALLBACK', 0)}",
        ])
    if DISCLAIMER not in "\n".join(lines):
        lines.append(DISCLAIMER)
    return "\n".join(lines)


def _format_intent(intent: dict[str, Any]) -> list[str]:
    source = intent.get("source") or {}
    action = intent.get("recommended_action") or {}
    conditions = intent.get("conditions") or {}
    risk = intent.get("risk") or {}
    header = (
        f"- {source.get('symbol') or '--'} {source.get('name') or ''} "
        f"[{action.get('action') or '--'}] {action.get('action_label') or ''}"
    ).strip()
    lines = [header]
    if action.get("reason"):
        lines.append(f"  理由: {action['reason']}")
    if conditions.get("execute_if"):
        lines.append(f"  触发: {'；'.join(str(item) for item in conditions['execute_if'])}")
    if conditions.get("delay_if"):
        lines.append(f"  等待: {'；'.join(str(item) for item in conditions['delay_if'])}")
    if conditions.get("invalidate_if"):
        lines.append(f"  失效: {'；'.join(str(item) for item in conditions['invalidate_if'])}")
    if risk.get("defense_line") is not None:
        lines.append(f"  防线: {risk['defense_line']}")
    if conditions.get("recheck_at"):
        lines.append(f"  复核: {conditions['recheck_at']}")
    fusion_status = ((intent.get("evidence") or {}).get("fusion_status") or {})
    if fusion_status.get("state"):
        line = f"  Fusion: {fusion_status['state']}"
        if fusion_status.get("fallback_reason"):
            line += f" · {fusion_status['fallback_reason']}"
        lines.append(line)
    return lines


def _group_intents(intents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in intents:
        grouped.setdefault(str(item.get("urgency") or "WATCH_ONLY"), []).append(item)
    return grouped


def _pct(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _ms(value: Any) -> str:
    try:
        return f"{int(round(float(value)))}ms"
    except (TypeError, ValueError):
        return "--"


def _weight_text(value: Any) -> str:
    try:
        return f"{float(value):g}%"
    except (TypeError, ValueError):
        return "--"


def _fusion_state_label(fusion: dict[str, Any]) -> str:
    if fusion.get("fallback_reason") or str(fusion.get("primary_path_id") or "").startswith("fallback-"):
        return f"FALLBACK · {fusion.get('fallback_reason') or '结构事实兜底'}"
    return "AI_READY"


def _path_probability_text(path: dict[str, Any], fusion: dict[str, Any]) -> str:
    if fusion.get("fallback_reason") or str(path.get("id") or "").startswith("fallback-"):
        return "结构兜底"
    probability = path.get("probability")
    return f"{float(probability) * 100:.0f}%" if isinstance(probability, (int, float)) else "--"


def _fusion_status_summary(intents: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"AI_READY": 0, "FALLBACK": 0}
    for intent in intents:
        state = (((intent.get("evidence") or {}).get("fusion_status") or {}).get("state") or "AI_READY")
        if state == "FALLBACK":
            summary["FALLBACK"] += 1
        else:
            summary["AI_READY"] += 1
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
