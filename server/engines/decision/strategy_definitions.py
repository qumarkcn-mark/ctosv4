"""Versioned strategy definitions.

Strategies are deterministic coach rules. They may produce plans and alert
candidates, but they must not execute trades or call push/QMT directly.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    strategy_version: str
    name: str
    strategy_type: str
    scope: List[str]
    mode: List[str]
    description: str
    inputs: Dict[str, object]
    freshness_required: bool
    outputs: List[str]
    disclaimer_required: bool = True

    def to_contract(self, status: str, conditions: list = None) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_type": self.strategy_type,
            "name": self.name,
            "scope": list(self.scope),
            "mode": list(self.mode),
            "description": self.description,
            "inputs": dict(self.inputs),
            "freshness_required": self.freshness_required,
            "outputs": list(self.outputs),
            "disclaimer_required": self.disclaimer_required,
            "status": status,
            "conditions": conditions or [],
        }


STRATEGY_DEFINITIONS = {
    "war1_third_buy": StrategyDefinition(
        strategy_id="war1_third_buy",
        strategy_version="1.0.0",
        name="战法一：日线三买",
        strategy_type="战法一",
        scope=["scanner", "radar", "alerts"],
        mode=["EMPTY"],
        description="日线买点背景下，等待 30 分钟结构和 5 分钟买点共振确认。",
        inputs={
            "structure": ["day", "30", "5"],
            "position": False,
            "market_context": True,
            "user_config": True,
        },
        freshness_required=True,
        outputs=["plans", "alerts"],
    ),
    "war2_trend_step": StrategyDefinition(
        strategy_id="war2_trend_step",
        strategy_version="1.0.0",
        name="战法二：趋势台阶",
        strategy_type="战法二",
        scope=["scanner", "radar", "alerts"],
        mode=["EMPTY", "HOLDING"],
        description="趋势进行中跟随台阶结构，开放目标，以高级别顶背驰或结构破坏管理风险。",
        inputs={
            "structure": ["day", "30", "5"],
            "position": True,
            "market_context": True,
            "user_config": True,
        },
        freshness_required=True,
        outputs=["plans", "alerts"],
    ),
    "holding_stage_manager": StrategyDefinition(
        strategy_id="holding_stage_manager",
        strategy_version="1.0.0",
        name="持仓六阶段管理",
        strategy_type="持仓管理",
        scope=["radar", "alerts"],
        mode=["HOLDING"],
        description="基于成本、结构止损、浮盈倍数和顶背驰风险管理持仓阶段。",
        inputs={
            "structure": ["day", "30"],
            "position": True,
            "market_context": False,
            "user_config": True,
        },
        freshness_required=True,
        outputs=["plans", "alerts"],
    ),
    "rotation_comparison": StrategyDefinition(
        strategy_id="rotation_comparison",
        strategy_version="0.1.0",
        name="调仓罗盘横向比较",
        strategy_type="调仓比较",
        scope=["rotation"],
        mode=["ROTATION"],
        description="横向比较持仓和候选票的结构预案，只排序和解释，不直接给执行指令。",
        inputs={
            "structure": ["day", "60", "30", "15", "5"],
            "position": True,
            "market_context": True,
            "user_config": True,
        },
        freshness_required=True,
        outputs=["plans"],
    ),
    "intraday_t_base_position": StrategyDefinition(
        strategy_id="intraday_t_base_position",
        strategy_version="0.1.0",
        name="有底仓日内 T",
        strategy_type="日内T候选",
        scope=["execution_candidate"],
        mode=["EXECUTION_CANDIDATE"],
        description="私有部署下，对已有底仓生成日内 T 候选意图。候选必须经过 Execution Layer 和 Risk Gate。",
        inputs={
            "structure": ["5", "1"],
            "position": True,
            "market_context": True,
            "user_config": True,
            "qmt_context": True,
        },
        freshness_required=True,
        outputs=["execution_intent_candidates"],
    ),
}

LEGACY_STRATEGY_ALIASES = {
    "war1": "war1_third_buy",
    "war2": "war2_trend_step",
}


def normalize_strategy_id(strategy_id: str) -> str:
    return LEGACY_STRATEGY_ALIASES.get(strategy_id, strategy_id)


def get_strategy_definition(strategy_id: str) -> StrategyDefinition:
    strategy_id = normalize_strategy_id(strategy_id)
    try:
        return STRATEGY_DEFINITIONS[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown strategy_id: {strategy_id}") from exc


def build_strategy_contract(strategy_id: str, status: str, conditions: list = None) -> dict:
    return get_strategy_definition(strategy_id).to_contract(status=status, conditions=conditions)
