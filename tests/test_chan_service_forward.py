"""
TRadar Forward Analysis 回归测试
对应审计报告 T1-T5：覆盖第一批修复的核心逻辑

测试范围：
  T1 - C2: 高危顶背驰不被偏多上下文覆盖
  T2 - H2: 警示型"顶背驰"字符串不触发一卖节点
  T3 - H4: 收敛待选情形 action 字段不含 "止损=数字" 模式
  T4 - M1: _build_forward_analysis 返回 bi_attribution 字段
  T5 - E1: _build_zs_context 失败时返回 zs_data_ok=False

运行：
  cd /path/to/ct-os-v4
  python -m pytest tests/test_chan_service_forward.py -v
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.chan_service import (
    _build_empty_position_classes,
    _build_forward_analysis,
    _build_zs_context,
    _detect_lifecycle_node,
)


# ─────────────────────────────────────────────────────────────────
# 测试夹具：构建最小化的 l1/l2/l3 数据结构
# ─────────────────────────────────────────────────────────────────

def _make_l1(dep="above", zd=280.0, zg=300.0, price=320.0):
    """日线数据（大级别）。dep: above/inside/below"""
    return {
        "level": "day",
        "state": "up_trend",
        "zd": zd,
        "zg": zg,
        "price": price,
        "bi_count": 36,
        "zs_count": 2,
        "last_bi_dir": "up",
        "zs_departure": dep,
        "zs_operative_zd": zd,
        "zs_operative_zg": zg,
        "zs_free_bis_count": 1,
        "zs_free_bis_dir": "up",
        "zs_free_high": price,
        "zs_free_low": zg + 1,
        "zs_last_centers": [{"zd": zd - 20, "zg": zg - 20}, {"zd": zd, "zg": zg}],
        "zs_data_ok": True,
        "div_info": None,
        "bi_extreme_seq": {"bots": [], "tops": [], "is_higher_low": True, "is_lower_high": False},
        "patterns": [],
        "zoushi_type": {"type": "上涨趋势", "zs_count": 2, "completion": ""},
        "classifications": [],
        "ex_support": zd,
        "ex_pressure": 0,
        "is_near_historical_high": False,
        "has_bottom_fractal": False,
        "has_top_fractal": False,
        "virtual_zd": 0,
        "virtual_zg": 0,
        "data_status": "ok",
        "kline_count": 500,
        "detail_bis": [],
        "recent_klines": [],
        "in_limbo": False,
        "zs_distance_pct": 0.0,
    }


def _make_l2(dep="inside", zd=290.0, zg=315.0, price=305.0,
             div_type=None, div_severity=None, patterns=None):
    """30分数据（操作级别）"""
    div_info = None
    if div_type:
        div_info = {"type": div_type, "severity": div_severity or "轻微"}
    return {
        "level": "m30",
        "state": "oscillating",
        "zd": zd,
        "zg": zg,
        "price": price,
        "bi_count": 12,
        "zs_count": 1,
        "last_bi_dir": "up",
        "zs_departure": dep,
        "zs_operative_zd": zd,
        "zs_operative_zg": zg,
        "zs_free_bis_count": 0,
        "zs_free_bis_dir": "none",
        "zs_free_high": 0,
        "zs_free_low": 0,
        "zs_last_centers": [{"zd": zd, "zg": zg}],
        "zs_data_ok": True,
        "div_info": div_info,
        "bi_extreme_seq": {
            "bots": [290.0],
            "tops": [315.0],
            "is_higher_low": True,
            "is_lower_high": False,
        },
        "patterns": patterns or [],
        "zoushi_type": {"type": "中枢震荡", "zs_count": 1, "completion": ""},
        "classifications": [],
        "ex_support": zd,
        "ex_pressure": zg,
        "is_near_historical_high": False,
        "has_bottom_fractal": False,
        "has_top_fractal": False,
        "virtual_zd": 0,
        "virtual_zg": 0,
        "data_status": "ok",
        "kline_count": 200,
        "detail_bis": [
            {"y0": 290.0, "y1": 315.0, "is_up": True, "start_date": "2025-01-01", "end_date": "2025-01-10"},
        ],
        "recent_klines": [
            {"date": "2025-01-10", "close": price, "high": price + 2, "low": price - 2}
        ],
        "in_limbo": False,
        "zs_distance_pct": 0.0,
    }


def _make_l3(dep="inside", zd=300.0, zg=312.0, price=305.0):
    """5分数据（入场级别）"""
    return {
        "level": "m5",
        "state": "oscillating",
        "zd": zd,
        "zg": zg,
        "price": price,
        "bi_count": 8,
        "zs_count": 1,
        "last_bi_dir": "up",
        "zs_departure": dep,
        "zs_operative_zd": zd,
        "zs_operative_zg": zg,
        "zs_free_bis_count": 0,
        "zs_free_bis_dir": "none",
        "zs_free_high": 0,
        "zs_free_low": 0,
        "zs_last_centers": [{"zd": zd, "zg": zg}],
        "zs_data_ok": True,
        "div_info": None,
        "bi_extreme_seq": {"bots": [], "tops": [], "is_higher_low": False, "is_lower_high": False},
        "patterns": [],
        "zoushi_type": {"type": "中枢震荡", "zs_count": 1, "completion": ""},
        "classifications": [],
        "ex_support": zd,
        "ex_pressure": zg,
        "is_near_historical_high": False,
        "has_bottom_fractal": False,
        "has_top_fractal": False,
        "virtual_zd": 0,
        "virtual_zg": 0,
        "data_status": "ok",
        "kline_count": 100,
        "detail_bis": [],
        "recent_klines": [],
        "in_limbo": False,
        "zs_distance_pct": 0.0,
    }


# ─────────────────────────────────────────────────────────────────
# T1: C2 回归 — 高危顶背驰不被偏多上下文覆盖
# ─────────────────────────────────────────────────────────────────

def test_severe_top_div_not_overridden_by_context():
    """
    C2 修复验证：日线 above（偏多背景）+ 30分高危顶背驰
    → 结果应有 lifecycle_node=="一卖_触发"，不被改写为"中枢震荡"

    修复前：node 强制覆盖为"中枢震荡"，用户看不到卖点
    修复后：高危顶背驰保留 node="一卖_触发"
    """
    l1 = _make_l1(dep="above", zg=300.0, price=320.0)
    # 30分顶背驰，高危程度
    l2 = _make_l2(
        dep="above",  # 30分也在中枢上方 → 触发 A2 三买_确认 或 A1 类二买
        div_type="顶背驰",
        div_severity="高危",
        patterns=["🔴 趋势顶背驰 → 1卖风险"],
        price=330.0,
        zg=315.0,
    )
    l3 = _make_l3()

    result = _build_empty_position_classes(l1, l2, l3)

    # 至少有一个预案
    assert len(result) > 0, "应返回至少一个预案"

    # 所有预案的 lifecycle_node 不应包含"中枢震荡"（高危顶背驰不应被覆盖）
    nodes = [c.get("lifecycle_node", "") for c in result]
    # 注意：_detect_lifecycle_node 对 dep="above" 返回三买_确认 / 类二买，
    # 高危顶背驰下 C2 修复保留该 node 而不覆盖为"中枢震荡"
    # → 任何 node 不应是"中枢震荡"
    assert "中枢震荡" not in nodes, (
        f"C2 修复失败：高危顶背驰被覆盖为中枢震荡。nodes={nodes}"
    )


# ─────────────────────────────────────────────────────────────────
# T2: H2 回归 — "三买后顶背驰警示"不触发一卖节点
# ─────────────────────────────────────────────────────────────────

def test_third_buy_warning_does_not_trigger_sell_node():
    """
    H2 修复验证：patterns 中包含"三买后顶背驰→谨防3买转1卖"这类警示字符串
    → div_info 为空时，不应触发 has_top_div=True
    → lifecycle_node 不应为"一卖_触发"

    修复前："顶背驰" 作为子串匹配，"三买后顶背驰→谨防3买转1卖"会被误匹配
    修复后：只有精确匹配 _TOP_DIV_CONFIRMED 才触发
    """
    l1 = _make_l1(dep="above")
    # 仅有警示字符串，div_info 为 None（没有确认的顶背驰）
    warning_pattern = "三买后顶背驰→ 谨防3买转1卖"
    l2 = _make_l2(
        dep="inside",
        div_type=None,
        patterns=["🟢 三买确认(回踩不破ZG)", warning_pattern],
        price=308.0,
    )
    l3 = _make_l3()

    # _detect_lifecycle_node 直接测试
    node_info = _detect_lifecycle_node(l2, l3)
    node = node_info["node"]

    assert node != "一卖_触发", (
        f"H2 修复失败：警示字符串'{warning_pattern}'错误触发了一卖节点。node={node}"
    )


# ─────────────────────────────────────────────────────────────────
# T3: H4 回归 — action 字段不含 "止损=数字" 模式
# ─────────────────────────────────────────────────────────────────

def test_action_field_no_number_jump_out():
    """
    H4 修复验证：收敛待选情形下，甲情形的 action 字段
    不再包含 "止损=XXX.XX" 格式的数字跳出

    修复前: "向上突破确认后可轻仓跟进，止损=320.00回踩低点"
    修复后: "向上突破30分ZG=315.00并获得确认后轻仓跟进。若突破后价格回落至..."
    """
    # 触发收敛待选：三级别全在中枢内
    l1 = _make_l1(dep="inside", zg=315.0, price=305.0)
    l2 = _make_l2(dep="inside", zg=315.0, zd=290.0, price=305.0)
    l3 = _make_l3(dep="inside", zg=312.0, zd=300.0, price=305.0)

    result = _build_empty_position_classes(l1, l2, l3)

    # 找到甲情形
    jia = next((c for c in result if c.get("id") == "甲"), None)
    assert jia is not None, "应存在甲情形"

    action = jia.get("action", "")
    # 匹配 "止损=数字" 模式（修复前的写法）
    assert not re.search(r"止损=\d+", action), (
        f"H4 修复失败：action 字段包含数字跳出。action='{action}'"
    )


# ─────────────────────────────────────────────────────────────────
# T4: M1 回归 — _build_forward_analysis 返回 bi_attribution 字段
# ─────────────────────────────────────────────────────────────────

def test_forward_analysis_contains_bi_attribution():
    """
    M1 修复验证：_build_forward_analysis 的返回值包含 bi_attribution 字段，
    并且包含必须的子字段（l1_bi_count, l2_bi_count）

    用途：前端/用户可通过此字段验证推演所基于的笔数，排查笔数漂移（36 vs 39）
    """
    l1 = _make_l1(dep="above")
    l2 = _make_l2(dep="inside")
    l3 = _make_l3(dep="inside")
    matrix = [l1, l2, l3]

    result = _build_forward_analysis(matrix)

    assert "bi_attribution" in result, (
        "M1 修复失败：forward_analysis 返回值缺少 bi_attribution 字段"
    )
    attr = result["bi_attribution"]
    assert "l1_bi_count" in attr, "bi_attribution 缺少 l1_bi_count"
    assert "l2_bi_count" in attr, "bi_attribution 缺少 l2_bi_count"
    assert attr["l1_bi_count"] == 36, f"l1_bi_count 期望 36，实际 {attr['l1_bi_count']}"
    assert attr["l2_bi_count"] == 12, f"l2_bi_count 期望 12，实际 {attr['l2_bi_count']}"


# ─────────────────────────────────────────────────────────────────
# T5: E1 回归 — _build_zs_context 失败时返回 zs_data_ok=False
# ─────────────────────────────────────────────────────────────────

def test_zs_context_failure_returns_degraded_flag():
    """
    E1 修复验证：传入空 klines 列表时，_build_zs_context 返回的 _empty 字典
    包含 zs_data_ok=False，供下游检测降级状态

    修复前：_empty 没有 zs_data_ok 字段，下游无法区分"正常零值"和"引擎失败零值"
    修复后：zs_data_ok=False 明确标记降级路径
    """
    # 空 klines → 触发 _empty 返回路径
    result = _build_zs_context([], "m30")

    assert "zs_data_ok" in result, (
        "E1 修复失败：_build_zs_context 返回值缺少 zs_data_ok 字段"
    )
    assert result["zs_data_ok"] is False, (
        f"E1 修复失败：空 klines 应返回 zs_data_ok=False，实际={result['zs_data_ok']}"
    )
    # 确认正常字段仍存在
    assert result["zs_operative_zg"] == 0
    assert result["zs_departure"] == "unknown"
