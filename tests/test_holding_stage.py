"""
状态机快照测试 — holding_stage & entry_checklist
==================================================
测试策略说明（来自设计文档 v2.1 §8，更新至六阶段方案）：
    不测算法本身，只测"给定这组字段，输出应该是什么"。
    覆盖 holding_stage 状态机的六阶段全部关键转换，确保
    _compute_entry_checklist 和 _compute_holding_status
    在已知输入下输出稳定的快照结果。
"""
import sys
import os

# 加入项目根目录，使 server 包可寻址
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from server.api.chan import _compute_entry_checklist, _compute_holding_status


# ─── 工厂函数：构造测试用 level 数据 ──────────────────────────

def make_day(patterns=None, zg=20.0, zd=18.0, price=21.0):
    return {
        "patterns": patterns or [],
        "zoushi_type": {"type": "盘整", "zs_count": 1},
        "zg": zg, "zd": zd, "price": price,
    }

def make_m30(patterns=None, zoushi_type="盘整", zg=20.0, zd=18.0, bis=None):
    return {
        "patterns": patterns or [],
        "zoushi_type": {"type": zoushi_type, "zs_count": 1},
        "zg": zg, "zd": zd, "price": 19.5,
        "detail_bis": bis or [],
    }

def make_m5(patterns=None):
    return {
        "patterns": patterns or [],
        "zoushi_type": {"type": "盘整", "zs_count": 1},
        "zg": 19.8, "zd": 19.2, "price": 19.5,
    }

def make_bi(is_up: bool, is_sure: bool = True):
    """构造一根笔的最小结构（用于 Stage 0 验证）。"""
    return {"is_up": is_up, "isUp": is_up, "is_sure": is_sure, "isSure": is_sure}


# ═══════════════════════════════════════════════════════════════
# 转换 1：无持仓 + 入场五条件全满足 → stage = "building"?
#         entry_checklist.all_passed = True
# ═══════════════════════════════════════════════════════════════
class TestEntryChecklist:
    def test_all_passed_when_five_conditions_met(self):
        day = make_day(patterns=["🟢 二买确认(底部抬高,不创新低)"])
        m30 = make_m30(
            patterns=["🟢 二买确认(底部抬高,不创新低)"],
            zoushi_type="盘整",
        )
        m5  = make_m5(patterns=["🟢 二买确认(底部抬高,不创新低)"])

        result = _compute_entry_checklist(day, m30, m5)

        assert result["day_buy_node"]         is True,  "日线买点节点应满足"
        assert result["day_not_top_diverge"]  is True,  "日线无顶背驰应满足"
        assert result["thirty_min_structure"] is True,  "30分中枢已形成应满足"
        assert result["thirty_min_buy_node"]  is True,  "30分买点节点应满足"
        assert result["five_min_entry_bar"]   is True,  "5分入场K线应满足"
        assert result["all_passed"]           is True,  "五条件全满足"

    def test_all_passed_false_when_top_diverge_present(self):
        """日线有顶背驰时，day_not_top_diverge = False，all_passed = False"""
        day = make_day(patterns=["🟢 二买确认(底部抬高,不创新低)", "🔴 趋势顶背驰 → 1卖风险"])
        m30 = make_m30(patterns=["🟢 二买确认(底部抬高,不创新低)"])
        m5  = make_m5(patterns=["🟢 二买确认(底部抬高,不创新低)"])

        result = _compute_entry_checklist(day, m30, m5)

        assert result["day_not_top_diverge"] is False
        assert result["all_passed"]          is False

    def test_all_false_when_no_patterns(self):
        """所有级别均无信号时，所有条件为 False"""
        result = _compute_entry_checklist(make_day(), make_m30(zoushi_type="构建中"), make_m5())

        assert result["day_buy_node"]         is False
        assert result["thirty_min_structure"] is False
        assert result["all_passed"]           is False

    def test_thirty_min_structure_false_when_building(self):
        """30分走势类型为'构建中'时，thirty_min_structure 应为 False"""
        m30 = make_m30(zoushi_type="构建中")
        result = _compute_entry_checklist(make_day(), m30, make_m5())
        assert result["thirty_min_structure"] is False


# ═══════════════════════════════════════════════════════════════
# 转换组：六阶段状态机完整覆盖
# ═══════════════════════════════════════════════════════════════
class TestHoldingStatus:
    # ── 基础：无持仓 ──────────────────────────────────────────
    def test_stage_empty_when_no_holding(self):
        """无持仓时 stage 应为 'empty'（字符串，向后兼容）"""
        result = _compute_holding_status(make_day(), make_m30(), holding=None, forward_a={})
        assert result["stage"] == "empty"
        assert result["label"] == "空仓"

    # ── Stage 0：走势验证期 ───────────────────────────────────
    def test_stage_0_when_just_entered(self):
        """刚入场，30分无确认笔，浮盈 < 1×止损距 → stage = 0
        cost=20, m30_zg=17（止损距=3），price=21（浮盈1/3=0.33× < 1）"""
        holding = {"cost": 20.0, "qty": 1000}
        result = _compute_holding_status(
            make_day(price=21.0), make_m30(zg=17.0),
            holding=holding, forward_a={},
        )
        assert result["stage"] == 0
        assert result["validation"]["status"] == "验证中"

    def test_stage_0_validation_fail_when_down_bi(self):
        """Stage 0：30分走出向下确认笔 → validation.status = '预案失效'
        使用 m30_zg=17（止损距=3）使浮盈倍数 < 1，确保不升入 Stage 2"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(bis=[make_bi(is_up=False, is_sure=True)], zg=17.0)
        result = _compute_holding_status(make_day(price=21.0), m30, holding=holding, forward_a={})
        assert result["stage"] == 0, f"got stage={result['stage']}"
        assert result["validation"]["status"] == "预案失效"
        assert result["validation"]["m30_bi_direction"] == "向下"

    # ── Stage 0 → Stage 1：30分上涨笔形成 ───────────────────
    def test_stage_1_when_upward_bi_confirmed(self):
        """30分完整向上确认笔已形成，浮盈 < 1×止损距 → stage = 1（验证期）
        m30_zg=17（止损距=3），price=21（倍数0.33 < 1）"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(bis=[make_bi(is_up=True, is_sure=True)], zg=17.0)
        result = _compute_holding_status(make_day(price=21.0), m30, holding=holding, forward_a={})
        assert result["stage"] == 1, f"got stage={result['stage']}"
        assert result["label"] == "验证期"
        assert result["validation"]["m30_bi_complete"] is True
        assert result["validation"]["status"] == "验证通过"

    # ── Stage 1 → Stage 2：浮盈 ≥ 1×止损距离 ────────────────
    def test_stage_2_when_profit_gte_1x_stop_distance(self):
        """浮盈 ≥ 1×止损距离 → stage = 2（保本期）。
        cost=20, m30_zg=18（止损距=2），price=22.1 → 倍数=2.1/2=1.05 ≥ 1"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(
            bis=[make_bi(is_up=True, is_sure=True)],
            zg=18.0,   # 台阶止损 = 18 → 止损距 = 20 - 18 = 2
        )
        day = make_day(price=22.1)  # 浮盈 = 2.1 → 倍数 = 2.1/2 = 1.05
        result = _compute_holding_status(day, m30, holding=holding, forward_a={})
        assert result["stage"] == 2
        assert result["label"] == "保本期"

    # ── Stage 2 → Stage 3：浮盈 ≥ 2×止损距离 ────────────────
    def test_stage_3_when_profit_gte_2x_stop_distance(self):
        """浮盈 ≥ 2×止损距离 → stage = 3（利润保护期）。
        cost=20, m30_zg=18（止损距=2），price=24.5 → 倍数=4.5/2=2.25 ≥ 2"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(
            bis=[make_bi(is_up=True, is_sure=True)],
            zg=18.0,   # 止损距 = 2
        )
        day = make_day(price=24.5)  # 浮盈 = 4.5 → 倍数 = 2.25
        result = _compute_holding_status(day, m30, holding=holding, forward_a={})
        assert result["stage"] == 3
        assert result["label"] == "利润保护期"

    # ── Stage 4：30分顶背驰 ───────────────────────────────────
    def test_stage_4_when_30min_top_diverge(self):
        """30分顶背驰出现 → stage = 4（减速预警），top_diverge_30min = True。
        顶背驰优先级高于浮盈倍数判定"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(
            patterns=["🔴 趋势顶背驰 → 1卖风险"],
            bis=[make_bi(is_up=True, is_sure=True)],
            zg=18.0,
        )
        day = make_day(price=24.5)  # 浮盈倍数 > 2，但顶背驰优先级更高
        result = _compute_holding_status(day, m30, holding=holding, forward_a={})
        assert result["stage"] == 4
        assert result["top_diverge_30min"] is True
        assert "减仓" in result["action"] or "减速" in result["label"]

    # ── Stage 5：日线顶背驰 ───────────────────────────────────
    def test_stage_5_when_day_top_diverge(self):
        """日线顶背驰 → stage = 5（趋势终结），top_diverge_day = True"""
        holding = {"cost": 20.0, "qty": 1000}
        day = make_day(patterns=["🔴 趋势顶背驰 → 1卖风险"], price=22.0)
        result = _compute_holding_status(day, make_m30(), holding=holding, forward_a={})
        assert result["stage"] == 5
        assert result["top_diverge_day"] is True
        assert result["label"] == "趋势终结"

    def test_stage_5_when_broken_stair_stop(self):
        """跌破台阶止损 → stage = 5（趋势终结）"""
        holding = {"cost": 20.0, "qty": 1000, "trailing_stop_price": 18.5}
        day = make_day(price=18.0)  # 18.0 <= 18.5 → 跌破台阶止损
        result = _compute_holding_status(day, make_m30(zg=18.5), holding=holding, forward_a={})
        assert result["stage"] == 5

    # ── 台阶止损只上移不下移 ──────────────────────────────────
    def test_trailing_stop_only_moves_up(self):
        """持久化止损(19.0) > 当前计算止损(17.0)时，台阶止损取大值=19.0"""
        holding = {"cost": 20.0, "qty": 1000, "trailing_stop_price": 19.0}
        m30 = make_m30(zg=17.0)   # 当前计算止损 = 17.0（低于持久化值）
        result = _compute_holding_status(make_day(price=21.0), m30, holding=holding, forward_a={})
        assert result["stair_stop_price"] == 19.0, "台阶止损不得下移"

    # ── 旧有断言（向后兼容） ──────────────────────────────────
    def test_locked_profit_computed_correctly(self):
        """locked_profit_pct = (current - cost) / cost * 100"""
        holding = {"cost": 20.0, "qty": 1000}
        result = _compute_holding_status(make_day(price=22.0), make_m30(), holding=holding, forward_a={})
        assert abs(result["locked_profit_pct"] - 10.0) < 0.01

    def test_target_reached_when_price_above_target(self):
        """current_price >= target_price_1 → target_1_reached = True"""
        holding = {"cost": 18.0, "qty": 1000}
        day = make_day(zg=20.0, price=22.5)   # 22.5 >= 20 * 1.10 = 22.0
        result = _compute_holding_status(day, make_m30(), holding=holding, forward_a={})
        assert result["target_1_reached"] is True


# ═══════════════════════════════════════════════════════════════
# 快速冒烟：确保函数可被正常调用，返回所有新字段
# ═══════════════════════════════════════════════════════════════
class TestSmoke:
    def test_entry_checklist_returns_all_keys(self):
        result = _compute_entry_checklist(make_day(), make_m30(), make_m5())
        expected_keys = {
            "day_buy_node", "day_not_top_diverge", "thirty_min_structure",
            "thirty_min_buy_node", "five_min_entry_bar", "all_passed",
        }
        assert set(result.keys()) == expected_keys

    def test_holding_status_returns_all_keys_empty(self):
        """空仓时返回所有新字段（含战法字段、中继说明、目标开放标记）"""
        result = _compute_holding_status(make_day(), make_m30(), holding=None, forward_a={})
        expected_keys = {
            "stage", "label", "strategy_type", "stair_stop_price", "locked_profit_pct",
            "top_diverge_30min", "top_diverge_30min_type", "top_diverge_day",
            "m30_relay_note", "action",
            "target_price_1", "target_price_2", "target_is_placeholder",
            "target_open", "target_label",
            "target_1_reached", "target_2_reached",
            "validation",
        }
        assert set(result.keys()) == expected_keys

    def test_holding_status_returns_all_keys_holding(self):
        """有持仓时返回所有新字段（含战法字段）"""
        holding = {"cost": 20.0, "qty": 1000}
        result = _compute_holding_status(make_day(price=21.0), make_m30(), holding=holding, forward_a={})
        expected_keys = {
            "stage", "label", "strategy_type", "stair_stop_price", "locked_profit_pct",
            "top_diverge_30min", "top_diverge_30min_type", "top_diverge_day",
            "m30_relay_note", "action",
            "target_price_1", "target_price_2", "target_is_placeholder",
            "target_open", "target_label",
            "target_1_reached", "target_2_reached",
            "validation",
        }
        assert set(result.keys()) == expected_keys

    def test_stage_is_int_when_holding(self):
        """有持仓时 stage 应为 int（0-5），不是字符串"""
        holding = {"cost": 20.0, "qty": 1000}
        result = _compute_holding_status(make_day(price=21.0), make_m30(), holding=holding, forward_a={})
        assert isinstance(result["stage"], int), f"stage 应为 int，实际为 {type(result['stage'])}"

    def test_validation_dict_has_required_keys(self):
        """validation 字典包含全部 5 个必须字段"""
        holding = {"cost": 20.0, "qty": 1000}
        result = _compute_holding_status(make_day(price=21.0), make_m30(), holding=holding, forward_a={})
        val_keys = {"m30_bi_direction", "m30_bi_complete", "bars_since_entry", "bars_remaining", "status"}
        assert val_keys.issubset(set(result["validation"].keys()))


# ═══════════════════════════════════════════════════════════════
# Task #26 回归：六阶段状态机战法分叉验证
# ═══════════════════════════════════════════════════════════════
class TestStrategyBranchRegression:
    """
    验证战法一/战法二在关键信号下的 Stage 分叉行为。

    覆盖：
    ① 战法二 + 中继型背驰 → stage 不升至 4，relay_note 已填充
    ② 战法二 + 转折型背驰 → stage = 4
    ③ 战法一 + 任意30分顶背驰 → stage = 4
    ④ 两套战法 + 日线顶背驰 → stage = 5
    ⑤ 战法二 Stage 3 门槛为 3× (战法一为 2×)
    ⑥ _check_reward_ratio：赔率不足时 ok=False，verdict 含警告
    ⑦ 战法二：target_open=True，target_is_placeholder=True
    ⑧ 战法二 stage = 1 时 strategy_type 字段正确回传
    """

    # ── ① 战法二 + 中继型 → 不升 Stage 4 ─────────────────────
    def test_s2_relay_diverge_does_not_trigger_stage4(self):
        """战法二 + 30分中继型顶背驰 → stage 保持在 1-3，m30_relay_note 填充"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(
            patterns=["🔴 趋势顶背驰 → 1卖风险 (中继)"],   # 含"中继"关键词
            bis=[make_bi(is_up=True, is_sure=True)],
            zg=18.0,
        )
        day = make_day(price=24.5)   # 浮盈倍数=2.25，战法一为 Stage 3，战法二仍 Stage 2（<3×）
        result = _compute_holding_status(
            day, m30, holding=holding, forward_a={},
            strategy_type="战法二",
        )
        assert result["stage"] != 4, f"战法二中继背驰不应触发Stage4，实际 stage={result['stage']}"
        assert result["m30_relay_note"] != "", "中继背驰应填充 m30_relay_note"
        assert result["top_diverge_30min"] is True, "top_diverge_30min 应仍为 True（记录信号）"

    # ── ② 战法二 + 转折型 → Stage 4 ──────────────────────────
    def test_s2_reversal_diverge_triggers_stage4(self):
        """战法二 + 30分转折型顶背驰 → stage = 4，动作含减仓指令"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(
            patterns=["🔴 趋势顶背驰 → 1卖风险"],   # 无"中继"字样 → 推断为转折型
            bis=[make_bi(is_up=True, is_sure=True)],
            zg=18.0,
        )
        day = make_day(price=24.5)
        result = _compute_holding_status(
            day, m30, holding=holding, forward_a={},
            strategy_type="战法二",
        )
        assert result["stage"] == 4, f"战法二转折型背驰应触发Stage4，实际 stage={result['stage']}"
        assert "减仓" in result["action"] or "次级" in result["label"], \
            f"Stage4 动作应含减仓指令，实际 action={result['action']}"
        assert result["m30_relay_note"] == "", "转折型不应有 m30_relay_note"

    # ── ③ 战法一 + 任意30分顶背驰 → Stage 4 ──────────────────
    def test_s1_any_30min_diverge_triggers_stage4(self):
        """战法一对30分顶背驰零容忍：任何类型（含中继型）都升至 Stage 4"""
        holding = {"cost": 20.0, "qty": 1000}
        m30_relay = make_m30(
            patterns=["🔴 趋势顶背驰 → 1卖风险 (中继)"],
            bis=[make_bi(is_up=True, is_sure=True)],
            zg=18.0,
        )
        day = make_day(price=24.5)
        result = _compute_holding_status(
            day, m30_relay, holding=holding, forward_a={},
            strategy_type="战法一",
        )
        assert result["stage"] == 4, \
            f"战法一对中继型背驰也应触发Stage4，实际 stage={result['stage']}"

    # ── ④ 两套战法 + 日线顶背驰 → Stage 5 ────────────────────
    def test_day_top_diverge_always_stage5(self):
        """日线顶背驰是最高级别信号，两套战法都应立即触发 Stage 5"""
        holding = {"cost": 20.0, "qty": 1000}
        day = make_day(patterns=["🔴 趋势顶背驰 → 1卖风险"], price=25.0)
        for st in ("战法一", "战法二"):
            result = _compute_holding_status(
                day, make_m30(), holding=holding, forward_a={},
                strategy_type=st,
            )
            assert result["stage"] == 5, \
                f"{st} 日线顶背驰应触发Stage5，实际 stage={result['stage']}"
            assert result["top_diverge_day"] is True

    # ── ⑤ 战法二 Stage 3 门槛 3×，战法一门槛 2× ──────────────
    def test_s2_stage3_threshold_is_3x(self):
        """浮盈 2.5× 止损距：战法一应为 Stage 3，战法二应为 Stage 2"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(bis=[make_bi(is_up=True, is_sure=True)], zg=18.0)
        # 止损距=2，浮盈=5.0 → 2.5×：满足战法一(≥2×)，不满足战法二(≥3×)
        day = make_day(price=25.0)   # 浮盈=5，倍数=2.5

        res_s1 = _compute_holding_status(day, m30, holding=holding, forward_a={}, strategy_type="战法一")
        res_s2 = _compute_holding_status(day, m30, holding=holding, forward_a={}, strategy_type="战法二")

        assert res_s1["stage"] == 3, f"战法一 2.5× 应为 Stage 3，实际 stage={res_s1['stage']}"
        assert res_s2["stage"] == 2, f"战法二 2.5× 应为 Stage 2（门槛3×），实际 stage={res_s2['stage']}"

    def test_s2_stage3_reached_at_3x(self):
        """浮盈达到 3× 止损距时，战法二才升入 Stage 3"""
        holding = {"cost": 20.0, "qty": 1000}
        m30 = make_m30(bis=[make_bi(is_up=True, is_sure=True)], zg=18.0)
        # 止损距=2，浮盈=6.1 → 3.05×：满足战法二 ≥ 3×
        day = make_day(price=26.1)

        result = _compute_holding_status(day, m30, holding=holding, forward_a={}, strategy_type="战法二")
        assert result["stage"] == 3, f"战法二 3.05× 应为 Stage 3，实际 stage={result['stage']}"

    # ── ⑥ _check_reward_ratio：赔率不足警告 ──────────────────
    def test_reward_ratio_ok_when_sufficient(self):
        """赔率满足最低要求：ok=True"""
        from server.api.chan import _check_reward_ratio
        r = _check_reward_ratio(
            entry_price=20.0, stop_price=18.0, target_price=24.5, min_ratio=2.0
        )
        # 止损距=2，获利=4.5，赔率=2.25 ≥ 2.0
        assert r["ok"] is True, f"赔率应通过，实际 ok={r['ok']}, verdict={r['verdict']}"
        assert r["ratio"] >= 2.0

    def test_reward_ratio_fail_when_insufficient(self):
        """赔率不足：ok=False，verdict 含警告信息"""
        from server.api.chan import _check_reward_ratio
        r = _check_reward_ratio(
            entry_price=20.0, stop_price=18.0, target_price=21.5, min_ratio=2.0
        )
        # 止损距=2，获利=1.5，赔率=0.75 < 2.0
        assert r["ok"] is False, f"赔率应失败，实际 ok={r['ok']}"
        assert "不足" in r["verdict"] or "重新评估" in r["verdict"], \
            f"失败 verdict 应含提示，实际: {r['verdict']}"

    def test_reward_ratio_open_target_always_ok(self):
        """战法二目标开放（is_open_target=True）时，赔率检查恒过"""
        from server.api.chan import _check_reward_ratio
        r = _check_reward_ratio(
            entry_price=20.0, stop_price=18.0, target_price=0,
            min_ratio=3.0, is_open_target=True,
        )
        assert r["ok"] is True
        assert r["is_open"] is True

    # ── ⑦ 战法二目标字段 ──────────────────────────────────────
    def test_s2_target_fields(self):
        """战法二：target_open=True，target_is_placeholder=True（无固定目标）"""
        holding = {"cost": 20.0, "qty": 1000}
        result = _compute_holding_status(
            make_day(price=22.0), make_m30(), holding=holding, forward_a={},
            strategy_type="战法二",
        )
        assert result["target_open"] is True, "战法二应 target_open=True"
        assert result["target_is_placeholder"] is True, "战法二应 target_is_placeholder=True"
        assert result["target_label"] != "", "战法二 target_label 应有内容"

    # ── ⑧ strategy_type 字段正确回传 ─────────────────────────
    def test_strategy_type_echoed_in_result(self):
        """传入哪个战法，返回值 strategy_type 字段就应是哪个"""
        holding = {"cost": 20.0, "qty": 1000}
        for st in ("战法一", "战法二", "未知"):
            result = _compute_holding_status(
                make_day(price=21.0), make_m30(), holding=holding, forward_a={},
                strategy_type=st,
            )
            assert result["strategy_type"] == st, \
                f"传入 {st}，返回 strategy_type={result['strategy_type']}"
