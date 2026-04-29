"""Rotation compass planner.

The scorer ranks symbols for comparison. This planner turns each scored symbol
into a coach-facing contract with scenario plans. It never emits orders.
"""

from typing import Dict, List


RISK_DISCLAIMER = "仅供参考，不构成交易建议；请结合账户风险和市场环境自行决策。"


def build_rotation_item(row: dict, is_holding: bool) -> dict:
    """Attach mode, structure summary, and 甲乙丙 scenario plans."""
    mode = "HOLDING" if is_holding else "CANDIDATE"
    item = dict(row)
    item["mode"] = mode
    item["structure_summary"] = _build_structure_summary(item)
    item["plans"] = _build_plans(item, mode)
    item["risk_disclaimer"] = RISK_DISCLAIMER
    return item


def _build_structure_summary(row: dict) -> dict:
    return {
        "state_label": row.get("state_label") or "待定位",
        "zoushi_type": row.get("zoushi_type") or "",
        "lifecycle_node": row.get("lifecycle_node") or "",
        "price": row.get("price") or row.get("current_price"),
        "stop_loss": row.get("stop_loss"),
        "distance_pct": row.get("distance_pct"),
        "zd": row.get("zd"),
        "zg": row.get("zg"),
        "sort_score": row.get("sort_score", 0),
        "error": row.get("error"),
    }


def _build_plans(row: dict, mode: str) -> List[Dict[str, str]]:
    state_label = row.get("state_label") or "待定位"
    lifecycle_node = row.get("lifecycle_node") or "结构节点待确认"
    zoushi_type = row.get("zoushi_type") or "走势类型待确认"
    stop_loss = row.get("stop_loss")
    distance_pct = row.get("distance_pct")
    main_action = row.get("main_action") or "等待雷达确认结构条件，不直接执行交易动作。"

    defense = f"{stop_loss:.2f}" if isinstance(stop_loss, (int, float)) else "结构防线"
    distance = (
        f"距防线 {distance_pct:.2f}%"
        if isinstance(distance_pct, (int, float))
        else "防线距离待确认"
    )

    if mode == "HOLDING":
        alpha_action = "维持观察持仓，防线未破前只跟踪结构，不自动加减仓。"
        beta_action = "若结构防线被跌破，回到雷达复核减仓或退出条件。"
        gamma_action = "若候选票结构显著更清晰，仅进入调仓比较，不直接换仓。"
    else:
        alpha_action = "加入观察池，等待回踩或买点触发后再用雷达确认。"
        beta_action = "若跌回结构防线或调研红旗出现，移出候选或降级观察。"
        gamma_action = "若与持仓比较优势不足，保留观察，不替代现有持仓。"

    return [
        {
            "name": "甲",
            "title": "结构延续",
            "condition": f"{state_label} 保持有效，{distance}。",
            "structure_evidence": f"{zoushi_type}；当前节点：{lifecycle_node}。",
            "position_action": alpha_action,
            "radar_check": main_action,
            "disclaimer": RISK_DISCLAIMER,
        },
        {
            "name": "乙",
            "title": "防线失效",
            "condition": f"价格跌破 {defense}，或 30 分钟结构转弱。",
            "structure_evidence": f"以 {lifecycle_node} 和防线距离为复核依据。",
            "position_action": beta_action,
            "radar_check": "先检查 Radar 的持仓/空仓预案，再记录决策原因。",
            "disclaimer": RISK_DISCLAIMER,
        },
        {
            "name": "丙",
            "title": "横向替代",
            "condition": "同组候选出现更清晰的高级别结构，且原标的结构走弱。",
            "structure_evidence": "只做持仓与候选的结构横向比较，分数仅用于排序。",
            "position_action": gamma_action,
            "radar_check": "进入单票 Radar 做最终确认，系统不替用户拍板。",
            "disclaimer": RISK_DISCLAIMER,
        },
    ]
