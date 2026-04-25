"""Interval nesting facts across multiple Chan levels."""

from typing import Optional


def check_interval_nesting(levels_data, level_names=None) -> Optional[dict]:
    """检测多级别同方向背驰链条，用于区间套信号增强。"""
    if isinstance(levels_data, dict):
        ordered = [levels_data.get(key) for key in ("day", "m30", "m5")]
        names = level_names or ["day", "m30", "m5"]
    else:
        ordered = levels_data
        names = level_names or [f"l{idx + 1}" for idx in range(len(ordered))]

    nesting = []
    nesting_direction = None
    for idx, data in enumerate(ordered):
        if not data:
            break
        level_key = names[idx] if idx < len(names) else f"l{idx + 1}"
        patterns = " ".join(data.get("patterns", []))
        div_type = (data.get("div_info") or {}).get("type", "")
        has_top_div = "顶背驰" in patterns or div_type == "顶背驰"
        has_bottom_div = "底背驰" in patterns or div_type == "底背驰"

        if not nesting:
            if has_top_div:
                nesting_direction = "top"
                nesting.append({"level": level_key, "type": "顶背驰"})
            elif has_bottom_div:
                nesting_direction = "bottom"
                nesting.append({"level": level_key, "type": "底背驰"})
            else:
                break
            continue

        if nesting_direction == "top" and has_top_div:
            nesting.append({"level": level_key, "type": "顶背驰"})
        elif nesting_direction == "bottom" and has_bottom_div:
            nesting.append({"level": level_key, "type": "底背驰"})
        else:
            break

    if len(nesting) >= 3:
        return {
            "depth": 3,
            "label": "三级区间套确认",
            "direction": nesting_direction,
            "levels": nesting,
            "confidence_gate": "HIGH",
        }
    if len(nesting) >= 2:
        return {
            "depth": 2,
            "label": "两级区间套",
            "direction": nesting_direction,
            "levels": nesting,
            "confidence_gate": "MEDIUM",
        }
    if len(nesting) >= 1:
        return {
            "depth": 1,
            "label": "单级别背驰",
            "direction": nesting_direction,
            "levels": nesting,
            "confidence_gate": "LOW",
        }
    return None
