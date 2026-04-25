"""Radar decision planner.

第一版仍消费 legacy matrix 字段，但输出 Radar Contract 的
strategy / entry_plan / holding_plan / plans。后续会把 `_compute_*`
legacy helper 逐步替换为纯 decision engine 规则。
"""

import logging
from typing import Optional

from server.engines.decision.entry_planner import compute_entry_checklist
from server.engines.decision.holding_manager import compute_holding_status
from server.engines.decision.risk_sizing import calculate_position_size, check_stop_atr
from server.engines.decision.target_planner import calculate_targets, check_reward_ratio

logger = logging.getLogger(__name__)


def build_radar_decision(
    matrix_data: dict,
    levels: dict,
    holding: Optional[dict],
    disclaimer: str,
    account_value: float = 0.0,
    risk_pct: float = 0.01,
    atr: float = 0.0,
) -> tuple[dict, Optional[dict], Optional[dict], list]:
    if holding:
        strategy, holding_plan, plans = _build_holding_plan(
            matrix_data,
            levels,
            holding,
            disclaimer,
        )
        return strategy, None, holding_plan, plans

    strategy, entry_plan, plans = _build_empty_plan(
        matrix_data,
        levels,
        disclaimer,
        account_value=account_value,
        risk_pct=risk_pct,
        atr=atr,
    )
    return strategy, entry_plan, None, plans


def _condition_list_from_entry_checklist(entry_checklist: dict) -> list[dict]:
    conditions = []
    for key, passed in entry_checklist.items():
        if key == "all_passed":
            continue
        conditions.append(
            {
                "condition_id": key,
                "label": key,
                "status": "PASS" if passed else "FAIL",
                "severity": "INFO",
                "evidence": {},
            }
        )
    return conditions


def _build_empty_plan(
    matrix_data: dict,
    levels: dict,
    disclaimer: str,
    account_value: float = 0.0,
    risk_pct: float = 0.01,
    atr: float = 0.0,
) -> tuple[dict, dict, list]:
    day = levels.get("day", {})
    m30 = levels.get("m30", {})
    m5 = levels.get("m5", {})
    strategy = matrix_data.get("strategy_classification") or {}
    entry_checklist = compute_entry_checklist(day, m30, m5)
    conditions = _condition_list_from_entry_checklist(entry_checklist)
    entry_price = m5.get("price", 0) or day.get("price", 0)
    stop_price = m5.get("zg", 0) or m5.get("zs_operative_zg", 0) or day.get("zd", 0)
    targets = calculate_targets(
        entry_price,
        day.get("bis", []) or day.get("detail_bis", []),
        day.get("bi_zhongshus", []) or day.get("zhongshus", []),
        stop_price,
    )
    target_price = targets[0]["price"] if targets else 0.0
    reward_ratio = check_reward_ratio(
        entry_price,
        stop_price,
        target_price,
        min_ratio=2.0,
        is_open_target=False,
    ) if entry_price > 0 and stop_price > 0 else None
    stop_check = check_stop_atr(entry_price, stop_price, atr) if atr and atr > 0 else None
    position_sizing = (
        calculate_position_size(account_value, entry_price, stop_price, risk_pct)
        if account_value > 0 and entry_price > 0 and stop_price > 0
        else None
    )
    plan = {
        "plan_id": "radar_empty_entry_plan",
        "plan_type": "ENTRY",
        "status": "TRIGGERED" if entry_checklist.get("all_passed") else "WATCHING",
        "title": "空仓入场观察",
        "conditions": conditions,
        "risk": {
            "invalid_if": "结构跌回关键中枢或次级别买点失败",
            "stop_reference": {
                "source": "structure",
                "level": "5" if m5 else "day",
                "field": "zg" if m5 else "zd",
                "value": stop_price,
            },
            "stop_check": stop_check,
        },
        "targets": targets,
        "position_sizing": position_sizing,
        "reward_ratio": reward_ratio,
        "disclaimer": disclaimer,
    }
    strategy_contract = {
        "strategy_id": strategy.get("strategy_id", "legacy_matrix_strategy"),
        "strategy_version": strategy.get("strategy_version", "legacy"),
        "strategy_type": strategy.get("strategy_type", "观察中"),
        "name": strategy.get("summary", "旧 matrix 战法分类"),
        "status": plan["status"],
        "conditions": conditions,
        "legacy": strategy,
    }
    return strategy_contract, plan, [plan]


def _build_holding_plan(
    matrix_data: dict,
    levels: dict,
    holding: dict,
    disclaimer: str,
) -> tuple[dict, dict, list]:
    day = levels.get("day", {})
    m30 = levels.get("m30", {})
    m5 = levels.get("m5", {})
    forward_a = matrix_data.get("forward_analysis_a", {})
    holding_status = compute_holding_status(
        day,
        m30,
        holding,
        forward_a,
        m30_bis=m30.get("detail_bis", []),
    )

    stage = holding_status.get("stage", "UNKNOWN")
    trailing_stop = holding_status.get("stair_stop_price", 0)
    plan = {
        "plan_id": "holding_stage_manager",
        "plan_type": "HOLDING",
        "status": "WATCHING",
        "stage": stage,
        "conditions": [],
        "risk": {
            "trailing_stop": trailing_stop,
            "invalid_if": "跌破台阶止损或结构破坏",
        },
        "reduce_plan": None,
        "exit_plan": None,
        "legacy_status": holding_status,
        "legacy_stage_v2": None,
        "disclaimer": disclaimer,
    }
    strategy_contract = {
        "strategy_id": "holding_stage_manager",
        "strategy_version": "legacy",
        "strategy_type": holding.get("strategy_type", "持仓管理"),
        "name": "持仓阶段管理",
        "status": plan["status"],
        "conditions": [],
    }
    return strategy_contract, plan, [plan]
