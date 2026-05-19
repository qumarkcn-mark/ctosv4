"""
持仓股票统一推演摘要导出工具

从 ctos.db 查询所有持仓，关联最新的 unified 推演结果，打印结构化摘要。

用法:
    cd /Users/markqu/Desktop/ct-os-v4
    ./venv/bin/python -m server.scripts.dump_position_summaries

注意：symbols 在 positions 表中格式为 sh600118，在 ai_structure_reasoning_runs
中格式为 sh.600118。本脚本自动做双向归一化匹配。
"""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

_BASE_DIR = Path(__file__).parent.parent.parent
DEFAULT_DB_CANDIDATES = [
    _BASE_DIR / "data" / "ctos.db",
    _BASE_DIR / "ctos.db",
]


def resolve_db_path(explicit_path: str | None = None, candidates: Iterable[Path] = DEFAULT_DB_CANDIDATES) -> Path:
    """解析数据库路径，避免模块导入时产生退出副作用。"""
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if path.exists():
            return path
        raise FileNotFoundError(f"ctos.db not found: {path}")

    for path in candidates:
        if path.exists():
            return path
    joined = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"ctos.db not found. tried: {joined}")


# ─── Symbol 归一化 ────────────────────────────────────────────

def _normalize(symbol: str) -> str:
    """将 sh600118 / sh.600118 统一为 sh.600118 格式，便于跨表匹配。"""
    if "." in symbol:
        return symbol.lower()
    # sh600118 → sh.600118, sz002138 → sz.002138
    for prefix in ("sh", "sz", "bj"):
        if symbol.lower().startswith(prefix) and not symbol[len(prefix):len(prefix) + 1] == ".":
            return f"{prefix}.{symbol[len(prefix):]}"
    return symbol.lower()


# ─── 查询 ────────────────────────────────────────────────────

def fetch_data(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # 1. 所有持仓
    positions = conn.execute("""
        SELECT
            symbol,
            name,
            quantity,
            avg_cost,
            current_price,
            unrealized_pnl,
            stop_loss_price,
            trailing_stop_price,
            entry_date,
            strategy_type,
            days_held,
            updated_at
        FROM positions
        ORDER BY symbol
    """).fetchall()

    if not positions:
        print("持仓表为空，无数据可导出。")
        conn.close()
        return []

    # 2. 构建 normalized → raw 映射，方便反查
    pos_map: dict[str, sqlite3.Row] = {_normalize(p["symbol"]): p for p in positions}
    normalized_symbols = list(pos_map.keys())

    # 3. 对每个 normalized symbol，取最新的 unified 推演结果
    placeholders = ",".join("?" * len(normalized_symbols))
    runs = conn.execute(f"""
        SELECT
            r.symbol,
            r.prompt_version,
            r.status,
            r.summary_json,
            r.full_reasoning_text,
            r.think_model,
            r.summary_model,
            r.updated_at AS run_updated_at
        FROM ai_structure_reasoning_runs r
        INNER JOIN (
            SELECT symbol, MAX(updated_at) AS max_updated
            FROM ai_structure_reasoning_runs
            WHERE prompt_version LIKE '%unified%'
              AND symbol IN ({placeholders})
            GROUP BY symbol
        ) latest
            ON r.symbol = latest.symbol
           AND r.updated_at = latest.max_updated
        WHERE r.prompt_version LIKE '%unified%'
    """, normalized_symbols).fetchall()

    conn.close()

    # 4. 按 normalized symbol 索引推演结果
    run_map: dict[str, sqlite3.Row] = {_normalize(r["symbol"]): r for r in runs}

    # 5. 拼接
    results: list[dict] = []
    for norm_sym, pos in pos_map.items():
        run = run_map.get(norm_sym)
        summary: dict = {}
        if run and run["summary_json"]:
            try:
                summary = json.loads(run["summary_json"])
            except json.JSONDecodeError:
                summary = {"_parse_error": run["summary_json"][:200]}

        market_value: float | None = None
        if pos["current_price"] and pos["quantity"]:
            market_value = pos["current_price"] * pos["quantity"]

        results.append(
            {
                "symbol": pos["symbol"],
                "normalized_symbol": norm_sym,
                "name": pos["name"] or "",
                "quantity": pos["quantity"],
                "avg_cost": pos["avg_cost"],
                "current_price": pos["current_price"],
                "market_value": market_value,
                "unrealized_pnl": pos["unrealized_pnl"],
                "stop_loss_price": pos["stop_loss_price"],
                "trailing_stop_price": pos["trailing_stop_price"],
                "entry_date": pos["entry_date"],
                "strategy_type": pos["strategy_type"],
                "days_held": pos["days_held"],
                "position_updated_at": pos["updated_at"],
                # 推演字段
                "has_unified_run": run is not None,
                "run_updated_at": run["run_updated_at"] if run else None,
                "prompt_version": run["prompt_version"] if run else None,
                "think_model": run["think_model"] if run else None,
                "summary_model": run["summary_model"] if run else None,
                "coach_summary": summary.get("coach_summary", ""),
                "monitor_conditions": summary.get("monitor_conditions", {}),
                "full_reasoning_text": run["full_reasoning_text"] if run else "",
            }
        )

    return results


# ─── 打印 ────────────────────────────────────────────────────

def _fmt_price(v: float | None) -> str:
    return f"{v:.3f}" if v is not None else "—"


def _fmt_pnl(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}"


def print_summary(records: list[dict], db_path: Path, include_full_reasoning: bool = False) -> None:
    sep = "═" * 72

    print(f"\n{sep}")
    print(f"  CT-OS 持仓统一推演摘要  |  共 {len(records)} 只股票")
    print(f"  数据库: {db_path}")
    print(sep)

    no_run_symbols: list[str] = []

    for rec in records:
        sym = rec["symbol"]
        name = rec["name"]
        qty = rec["quantity"]
        cost = _fmt_price(rec["avg_cost"])
        cur = _fmt_price(rec["current_price"])
        mv = f"{rec['market_value']:.0f}" if rec["market_value"] else "—"
        pnl = _fmt_pnl(rec["unrealized_pnl"])
        sl = _fmt_price(rec["stop_loss_price"])
        tsl = _fmt_price(rec["trailing_stop_price"])
        entry = rec["entry_date"] or "—"
        stype = rec["strategy_type"] or "未知"
        days = str(rec["days_held"]) if rec["days_held"] is not None else "—"

        print(f"\n{'─' * 72}")
        print(f"  {sym}  {name}  |  {stype}  |  入场: {entry}  持仓 {days} 天")
        print(f"  持仓: {qty} 股  |  均价: {cost}  |  现价: {cur}  |  市值: {mv}")
        print(f"  浮动盈亏: {pnl}  |  止损价: {sl}  |  台阶止损: {tsl}")

        if not rec["has_unified_run"]:
            print(f"\n  [!] 无 unified 推演结果")
            no_run_symbols.append(f"{sym} {name}")
            continue

        print(f"\n  推演版本: {rec['prompt_version']}  |  更新: {rec['run_updated_at']}")
        print(f"  模型: think={rec['think_model'] or '—'}  summary={rec['summary_model'] or '—'}")

        # Coach summary（缩进显示）
        if rec["coach_summary"]:
            print(f"\n  【AI 教练摘要】")
            for line in rec["coach_summary"].splitlines():
                print(f"    {line}")

        # Monitor conditions / triggers
        mc = rec["monitor_conditions"]
        triggers = mc.get("triggers", []) if isinstance(mc, dict) else []
        if triggers:
            print(f"\n  【监控条件 — {len(triggers)} 个触发点】")
            for t in triggers:
                tid = t.get("id", "?")
                ttype = t.get("type", "")
                level = t.get("level", "")
                msg = t.get("message_on_trigger", "")
                action = t.get("action_on_trigger", "")
                direction = "↑ 突破" if "above" in ttype else ("↓ 跌破" if "below" in ttype else ttype)
                print(f"    [{tid}] {direction} {level}  →  {msg}  ({action})")

        # 可选：完整推演原文
        if include_full_reasoning and rec["full_reasoning_text"]:
            print(f"\n  【完整推演原文】")
            for line in rec["full_reasoning_text"].splitlines():
                print(f"    {line}")

    print(f"\n{sep}")
    if no_run_symbols:
        print(f"  以下股票无 unified 推演结果 ({len(no_run_symbols)} 只):")
        for s in no_run_symbols:
            print(f"    • {s}")
    else:
        print(f"  所有持仓股票均有 unified 推演结果。")
    print(f"{sep}\n")


# ─── JSON 导出 ────────────────────────────────────────────────

def dump_json(records: list[dict], output_path: Path) -> None:
    """将结果导出为 JSON 文件（不含 full_reasoning_text，保持紧凑）。"""
    exportable = []
    for rec in records:
        row = {k: v for k, v in rec.items() if k != "full_reasoning_text"}
        exportable.append(row)

    output_path.write_text(
        json.dumps(exportable, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"JSON 已导出: {output_path}")


# ─── 入口 ────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="导出持仓股票的 unified 推演摘要",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m server.scripts.dump_position_summaries
  python -m server.scripts.dump_position_summaries --full-reasoning
  python -m server.scripts.dump_position_summaries --json out.json
        """,
    )
    parser.add_argument(
        "--full-reasoning",
        action="store_true",
        help="同时打印完整推演原文（内容较长）",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="额外导出 JSON 文件到指定路径",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="指定 ctos.db 路径，默认查找 data/ctos.db 和项目根目录 ctos.db",
    )
    args = parser.parse_args()

    try:
        db_path = resolve_db_path(args.db)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    records = fetch_data(db_path)
    if not records:
        return

    print_summary(records, db_path, include_full_reasoning=args.full_reasoning)

    if args.json:
        dump_json(records, Path(args.json))


if __name__ == "__main__":
    main()
