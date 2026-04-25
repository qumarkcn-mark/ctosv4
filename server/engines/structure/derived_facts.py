"""Derived structure facts built from chan_adapter serialized output.

基础结构必须来自 chan.py；本模块只聚合 CT-OS 产品层派生事实。
"""

from server.engines.structure.divergence import (
    classify_divergence_type,
    detect_recent_divergence,
)
from server.engines.structure.lifecycle import classify_lifecycle
from server.engines.structure.nesting import check_interval_nesting
from server.engines.structure.strategy_detector import derive_patterns
from server.engines.structure.zhongshu import (
    classify_zoushi,
    deduce_state_from_structures,
)


def enrich_level(level_data: dict) -> dict:
    enriched = dict(level_data)
    bis = enriched.get("bis", [])
    zhongshus = enriched.get("bi_zhongshus") or enriched.get("zhongshus") or []
    bsps = enriched.get("bsps", [])
    price = level_price(enriched)

    state, last_zs, recent_ex = deduce_state_from_structures(bis, zhongshus)
    zoushi_type = classify_zoushi(zhongshus)
    div_info = detect_recent_divergence(bis)
    patterns = derive_patterns(bsps, state, div_info)
    classifications = classify_lifecycle(zoushi_type, last_zs, bis)
    div_type = classify_divergence_type(div_info, bis, price)

    enriched["price"] = price
    enriched["state"] = state
    enriched["zg"] = last_zs.get("zg", 0)
    enriched["zd"] = last_zs.get("zd", 0)
    enriched["zs_operative_zg"] = last_zs.get("zg", 0)
    enriched["zs_operative_zd"] = last_zs.get("zd", 0)
    enriched["last_bi_dir"] = _last_bi_dir(bis)
    enriched["zoushi_type"] = zoushi_type
    enriched["patterns"] = patterns
    enriched["classifications"] = classifications
    enriched["recent_ex"] = recent_ex
    enriched["active_zhongshu"] = last_zs
    enriched["div_info"] = div_info
    enriched["latest_top_beichi_type"] = div_type if div_info and div_info.get("type") == "顶背驰" else ""
    enriched["latest_bottom_beichi_type"] = div_type if div_info and div_info.get("type") == "底背驰" else ""
    return enriched


def level_price(level_data: dict) -> float:
    klines = level_data.get("klines") or []
    if klines:
        try:
            return float(klines[-1].get("close", 0))
        except Exception:
            return 0
    bis = level_data.get("bis") or []
    if bis:
        try:
            return float(bis[-1].get("y1", 0))
        except Exception:
            return 0
    return 0


def _last_bi_dir(bis: list) -> str:
    if not bis:
        return "unknown"
    return "up" if bis[-1].get("is_up") else "down"
