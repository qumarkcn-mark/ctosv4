"""Persistence helpers for AI daily holding plans."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from typing import Any

from server.engines.ai_native.holding_plan import AIHoldingPlan


def save_holding_plan(conn: sqlite3.Connection, plan: AIHoldingPlan) -> None:
    conn.execute(
        """
        INSERT INTO ai_holding_plans (
            plan_id, user_id, symbol, trade_date, as_of, radar_run_id, plan_status,
            current_script, target_weight_pct, max_position_pct, defense_line,
            repair_line, trigger_conditions_json, cancel_conditions_json,
            observation_focus_json, evidence_refs_json, raw_plan_json, disclaimer
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, symbol, trade_date) DO UPDATE SET
            as_of=excluded.as_of,
            radar_run_id=excluded.radar_run_id,
            plan_status=excluded.plan_status,
            current_script=excluded.current_script,
            target_weight_pct=excluded.target_weight_pct,
            max_position_pct=excluded.max_position_pct,
            defense_line=excluded.defense_line,
            repair_line=excluded.repair_line,
            trigger_conditions_json=excluded.trigger_conditions_json,
            cancel_conditions_json=excluded.cancel_conditions_json,
            observation_focus_json=excluded.observation_focus_json,
            evidence_refs_json=excluded.evidence_refs_json,
            raw_plan_json=excluded.raw_plan_json,
            disclaimer=excluded.disclaimer
        """,
        (
            plan.plan_id,
            plan.user_id,
            plan.symbol,
            plan.trade_date,
            plan.as_of,
            plan.radar_run_id,
            plan.plan_status,
            plan.current_script,
            plan.target_weight_pct,
            plan.max_position_pct,
            plan.defense_line,
            plan.repair_line,
            _json(plan.trigger_conditions),
            _json(plan.cancel_conditions),
            _json(plan.observation_focus),
            _json(plan.evidence_refs),
            _json(plan.raw_plan),
            plan.disclaimer,
        ),
    )


def load_latest_holding_plan(conn: sqlite3.Connection, *, user_id: int, symbol: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
          FROM ai_holding_plans
         WHERE user_id = ? AND symbol = ?
         ORDER BY trade_date DESC, created_at DESC
         LIMIT 1
        """,
        (user_id, symbol),
    ).fetchone()
    return dict(row) if row else {}


def _json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True)


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value
