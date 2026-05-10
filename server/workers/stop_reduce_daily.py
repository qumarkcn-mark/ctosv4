"""Daily orchestration for AI stop/reduce shadow training V1."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from server.db.database import get_connection
from server.workers.stop_reduce_monitor import (
    StopReduceMonitorConfig,
    StopReduceMonitorReport,
    render_stop_reduce_monitor_report,
    run_stop_reduce_monitor,
)
from server.workers.stop_reduce_settlement import (
    StopReduceSettlementConfig,
    StopReduceSettlementReport,
    render_stop_reduce_settlement_report,
    run_stop_reduce_settlement,
)


@dataclass(frozen=True)
class StopReduceDailyConfig:
    user_id: int = 1
    symbol: str | None = None
    limit: int = 20
    settlement_limit: int = 5
    daily_window_limit: int = 260
    fundamental_verdict: str = "中性"
    dry_run: bool = False
    skip_monitor: bool = False
    skip_settlement: bool = False
    output_path: str = ""


@dataclass(frozen=True)
class StopReduceDailyReport:
    generated_at: str
    monitor: StopReduceMonitorReport | None = None
    settlement: StopReduceSettlementReport | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def plans(self) -> int:
        return self.monitor.saved_plans if self.monitor else 0

    @property
    def intents(self) -> int:
        return self.monitor.enqueued_intents if self.monitor else 0

    @property
    def settled(self) -> int:
        return self.settlement.settled if self.settlement else 0

    @property
    def lessons(self) -> int:
        return self.settlement.case_memory_writes if self.settlement else 0


def stop_reduce_daily_mode(*, skip_monitor: bool, skip_settlement: bool) -> str:
    if skip_monitor and not skip_settlement:
        return "SETTLEMENT"
    if skip_settlement and not skip_monitor:
        return "MONITOR"
    return "FULL"


async def run_stop_reduce_daily_logged(
    *,
    config: StopReduceDailyConfig | None = None,
    trigger: str = "MANUAL",
    runner=None,
    audit_conn=None,
) -> StopReduceDailyReport:
    """Run daily loop and persist a compact control-plane audit row."""
    config = config or StopReduceDailyConfig()
    mode = stop_reduce_daily_mode(skip_monitor=config.skip_monitor, skip_settlement=config.skip_settlement)
    started_at = datetime.now().isoformat(timespec="seconds")
    run_date = started_at[:10]
    run_id = (
        f"stop_reduce_daily:{config.user_id}:{run_date}:{trigger.lower()}:"
        f"{mode.lower()}:{started_at[11:19].replace(':', '')}:{uuid.uuid4().hex[:8]}"
    )
    owns_audit_conn = audit_conn is None
    audit_conn = audit_conn or get_connection()
    try:
        _insert_daily_run(
            audit_conn,
            run_id=run_id,
            user_id=config.user_id,
            run_date=run_date,
            trigger=trigger,
            mode=mode,
            started_at=started_at,
        )
        try:
            run = runner or run_stop_reduce_daily
            report = await run(config=config)
            _finish_daily_run(audit_conn, run_id=run_id, status="SUCCESS", report=report)
            return report
        except Exception as exc:
            _finish_daily_run(audit_conn, run_id=run_id, status="FAILED", error=str(exc)[:500])
            raise
    finally:
        if owns_audit_conn:
            audit_conn.close()


async def run_stop_reduce_daily(
    *,
    conn=None,
    config: StopReduceDailyConfig | None = None,
    reasoning_builder=None,
    kline_loader=None,
) -> StopReduceDailyReport:
    """Run the V1 daily loop: plan generation, intent enqueue, and settlement."""
    config = config or StopReduceDailyConfig()
    monitor_report = None
    settlement_report = None
    rows: list[dict[str, Any]] = []

    if not config.skip_monitor:
        monitor_report = await run_stop_reduce_monitor(
            conn=conn,
            config=StopReduceMonitorConfig(
                user_id=config.user_id,
                symbol=config.symbol,
                limit=config.limit,
                fundamental_verdict=config.fundamental_verdict,
                dry_run=config.dry_run,
            ),
            reasoning_builder=reasoning_builder,
        )
        rows.extend({"stage": "monitor", **row} for row in monitor_report.rows)

    if not config.skip_settlement:
        settlement_report = run_stop_reduce_settlement(
            conn=conn,
            config=StopReduceSettlementConfig(
                user_id=config.user_id,
                symbol=config.symbol,
                limit=config.limit,
                settlement_limit=config.settlement_limit,
                daily_window_limit=config.daily_window_limit,
                persist=not config.dry_run,
            ),
            kline_loader=kline_loader,
        )
        rows.extend({"stage": "settlement", **row} for row in settlement_report.rows)

    report = StopReduceDailyReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        monitor=monitor_report,
        settlement=settlement_report,
        rows=rows,
    )
    if config.output_path:
        Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.output_path).write_text(render_stop_reduce_daily_report(report), encoding="utf-8")
    return report


def summarize_stop_reduce_daily_report(report: StopReduceDailyReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "plans_saved": report.plans,
        "intents_enqueued": report.intents,
        "intents_settled": report.settled,
        "case_memory_writes": report.lessons,
        "rows": report.rows,
    }


def render_stop_reduce_daily_report(report: StopReduceDailyReport) -> str:
    """Render one operator-facing daily report."""
    lines = [
        "# AI Stop/Reduce Daily Report",
        "",
        "交易相关内容仅供参考，不构成投资建议。",
        "",
        f"- Generated at: {report.generated_at}",
        f"- Plans saved: {report.plans}",
        f"- Intents enqueued: {report.intents}",
        f"- Intents settled: {report.settled}",
        f"- Case memory writes: {report.lessons}",
    ]
    if report.monitor:
        lines.extend(["", "## Monitor", "", render_stop_reduce_monitor_report(report.monitor).strip()])
    if report.settlement:
        lines.extend(["", "## Settlement", "", render_stop_reduce_settlement_report(report.settlement).strip()])
    if report.rows:
        lines.extend(["", "## Machine Rows", "", "```json", json.dumps(report.rows, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines).rstrip() + "\n"


def _insert_daily_run(
    conn,
    *,
    run_id: str,
    user_id: int,
    run_date: str,
    trigger: str,
    mode: str,
    started_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ai_stop_reduce_daily_runs (
            run_id, user_id, run_date, trigger, mode, status, started_at
        )
        VALUES (?, ?, ?, ?, ?, 'RUNNING', ?)
        """,
        (run_id, user_id, run_date, trigger, mode, started_at),
    )
    conn.commit()


def _finish_daily_run(
    conn,
    *,
    run_id: str,
    status: str,
    report: StopReduceDailyReport | None = None,
    error: str = "",
) -> None:
    summary = summarize_stop_reduce_daily_report(report) if report else {}
    conn.execute(
        """
        UPDATE ai_stop_reduce_daily_runs
           SET status = ?,
               completed_at = ?,
               plans_saved = ?,
               intents_enqueued = ?,
               intents_settled = ?,
               case_memory_writes = ?,
               error = ?,
               summary_json = ?
         WHERE run_id = ?
        """,
        (
            status,
            datetime.now().isoformat(timespec="seconds"),
            summary.get("plans_saved", 0),
            summary.get("intents_enqueued", 0),
            summary.get("intents_settled", 0),
            summary.get("case_memory_writes", 0),
            error,
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            run_id,
        ),
    )
    conn.commit()


def parse_args(argv: list[str] | None = None) -> StopReduceDailyConfig:
    parser = argparse.ArgumentParser(description="Run AI stop/reduce daily shadow-training loop")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--settlement-limit", type=int, default=5)
    parser.add_argument("--daily-window-limit", type=int, default=260)
    parser.add_argument("--fundamental-verdict", choices=["支持", "中性", "回避"], default="中性")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-monitor", action="store_true")
    parser.add_argument("--skip-settlement", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    return StopReduceDailyConfig(
        user_id=args.user_id,
        symbol=args.symbol,
        limit=args.limit,
        settlement_limit=args.settlement_limit,
        daily_window_limit=args.daily_window_limit,
        fundamental_verdict=args.fundamental_verdict,
        dry_run=args.dry_run,
        skip_monitor=args.skip_monitor,
        skip_settlement=args.skip_settlement,
        output_path=args.output,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    report = asyncio.run(run_stop_reduce_daily(config=config))
    print(render_stop_reduce_daily_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
