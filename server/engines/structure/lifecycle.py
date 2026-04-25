"""Lifecycle classifications derived from zoushi and zhongshu facts."""

from typing import Optional


def classify_lifecycle(zoushi: dict, last_zs: dict, bis: list) -> list[dict]:
    zoushi_type = zoushi.get("type", "构建中")
    if zoushi_type == "构建中":
        return [
            {
                "id": "A",
                "name": "形成中枢",
                "condition": "三笔重叠形成中枢 → 可判定走势类型",
                "action": "等待中枢形成后再分析",
                "stopLoss": None,
            }
        ]
    if not last_zs:
        return []

    zg = last_zs.get("zg", 0)
    zd = last_zs.get("zd", 0)
    if zoushi_type == "盘整":
        return [
            {
                "id": "A",
                "name": "向上突破",
                "condition": f"次级别走势向上离开中枢，回踩不破ZG({zg:.2f})",
                "action": "三买确认后观察入场",
                "stopLoss": round(stop_for_3buy(bis, zg), 2) if zg else None,
            },
            {
                "id": "B",
                "name": "继续盘整",
                "condition": f"价格在ZD({zd:.2f})-ZG({zg:.2f})之间运行",
                "action": "观望，等方向选择",
                "stopLoss": round(zd, 2) if zd else None,
            },
            {
                "id": "C",
                "name": "向下突破",
                "condition": f"次级别走势向下离开中枢，反弹不破ZD({zd:.2f})",
                "action": "三卖确认后离场。仅供参考",
                "stopLoss": None,
            },
        ]
    if zoushi_type == "上涨趋势":
        return [
            {
                "id": "A",
                "name": "趋势延伸",
                "condition": "向上离开中枢且无顶背驰迹象",
                "action": "持有",
                "stopLoss": round(zd, 2) if zd else None,
            },
            {
                "id": "B",
                "name": "趋势完成",
                "condition": "最后一段向上离开中枢出现顶背驰",
                "action": "减仓/离场。仅供参考",
                "stopLoss": None,
            },
        ]
    if zoushi_type == "下跌趋势":
        return [
            {
                "id": "甲",
                "name": "趋势延伸",
                "condition": "向下离开中枢且无底背驰迹象",
                "action": "空仓等待",
                "stopLoss": None,
            },
            {
                "id": "乙",
                "name": "趋势完成(一买)",
                "condition": "最后一段向下离开中枢出现底背驰",
                "action": "关注一买，轻仓试探。仅供参考",
                "stopLoss": round(stop_for_1buy(bis), 2) if stop_for_1buy(bis) else None,
            },
        ]
    return []


def stop_for_1buy(bis: list) -> Optional[float]:
    for bi in reversed(bis):
        if not bi.get("is_up"):
            return min(bi.get("y0", 0), bi.get("y1", 0))
    return None


def stop_for_3buy(bis: list, zg: float) -> float:
    for bi in reversed(bis[-4:]):
        if not bi.get("is_up") and bi.get("y1", 0) > zg:
            return min(bi.get("y0", 0), bi.get("y1", 0))
    return zg
