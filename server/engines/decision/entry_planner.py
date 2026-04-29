"""Empty-position entry planning rules."""


def compute_entry_checklist(day: dict, m30: dict, m5: dict) -> dict:
    """Compute the five empty-position entry checks from structured facts.

    这是空仓视角的入场观察条件，只输出规则状态，不执行交易。
    """
    day_patterns = " ".join(day.get("patterns", []))
    m30_patterns = " ".join(m30.get("patterns", []))
    m5_patterns = " ".join(m5.get("patterns", []))
    m30_zoushi = m30.get("zoushi_type", {}).get("type", "构建中")

    day_buy_node = any(kw in day_patterns for kw in ("二买", "三买", "类二买", "类三买"))
    day_not_top_diverge = not any(kw in day_patterns for kw in ("顶背驰", "1卖", "二卖", "三卖"))
    thirty_min_structure = m30_zoushi != "构建中"
    thirty_min_buy_node = any(kw in m30_patterns for kw in ("二买", "三买", "类二买", "底背驰"))
    five_min_entry_bar = any(kw in m5_patterns for kw in ("底背驰", "二买", "三买", "类二买"))
    all_passed = all(
        [
            day_buy_node,
            day_not_top_diverge,
            thirty_min_structure,
            thirty_min_buy_node,
            five_min_entry_bar,
        ]
    )
    return {
        "day_buy_node": day_buy_node,
        "day_not_top_diverge": day_not_top_diverge,
        "thirty_min_structure": thirty_min_structure,
        "thirty_min_buy_node": thirty_min_buy_node,
        "five_min_entry_bar": five_min_entry_bar,
        "all_passed": all_passed,
    }
