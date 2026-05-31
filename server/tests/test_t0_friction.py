"""测试 T0 摩擦成本模型。"""
import pytest
from server.engines.t0.t0_friction import (
    calculate_friction,
    calculate_round_trip_friction,
    is_grid_viable,
    min_viable_spread,
)


class TestCalculateFriction:
    def test_min_commission_small_order(self):
        """小额订单佣金最低 5 元规则。"""
        # price=5, qty=100 → amount=500 → commission=500*0.00015=0.075 → 应取 min=5.0
        result = calculate_friction(5.0, 100, "BUY")
        assert result["commission"] == 5.0, "小额订单佣金应触底 5 元"

    def test_stamp_duty_only_on_sell(self):
        """印花税仅 SELL 方向。"""
        buy = calculate_friction(100.0, 100, "BUY")
        sell = calculate_friction(100.0, 100, "SELL")
        assert buy["stamp_duty"] == 0.0, "BUY 无印花税"
        assert sell["stamp_duty"] > 0.0, "SELL 有印花税"
        assert sell["stamp_duty"] == pytest.approx(100.0 * 100 * 0.0005, rel=1e-4)

    def test_normal_commission(self):
        """正常佣金计算（大额订单，超过最低门槛）。"""
        # price=100, qty=1000 → amount=100000 → commission=100000*0.00015=15
        result = calculate_friction(100.0, 1000, "BUY")
        assert result["commission"] == pytest.approx(15.0, rel=1e-4)

    def test_transfer_fee(self):
        """过户费买卖双向收取。"""
        buy = calculate_friction(100.0, 100, "BUY")
        sell = calculate_friction(100.0, 100, "SELL")
        assert buy["transfer_fee"] > 0.0
        assert sell["transfer_fee"] > 0.0
        assert buy["transfer_fee"] == pytest.approx(100.0 * 100 * 0.00001, rel=1e-4)

    def test_slippage(self):
        """滑点计算正确。"""
        result = calculate_friction(100.0, 100, "BUY", slippage_ticks=2, tick_size=0.01)
        assert result["slippage"] == pytest.approx(0.02 * 100, rel=1e-4)  # 2 * 0.01 * 100

    def test_etf_tick_size(self):
        """ETF tick_size=0.001 场景。"""
        result = calculate_friction(1.5, 10000, "BUY", slippage_ticks=1, tick_size=0.001)
        assert result["slippage"] == pytest.approx(0.001 * 10000, rel=1e-4)


class TestRoundTripFriction:
    def test_total_includes_both_sides(self):
        """往返成本 = 买入费用 + 卖出费用。"""
        rt = calculate_round_trip_friction(100.0, 100)
        assert rt["total_cost"] == pytest.approx(rt["buy_cost"] + rt["sell_cost"], rel=1e-6)

    def test_cost_per_share(self):
        """cost_per_share = total_cost / qty。"""
        rt = calculate_round_trip_friction(100.0, 200)
        assert rt["cost_per_share"] == pytest.approx(rt["total_cost"] / 200, rel=1e-6)


class TestIsGridViable:
    def test_narrow_spread_not_viable(self):
        """窄幅震荡不可做T。"""
        assert is_grid_viable(0.05, 0.03) is False  # 0.05 < 3 * 0.03 = 0.09

    def test_wide_spread_viable(self):
        """宽幅可做T。"""
        assert is_grid_viable(0.15, 0.03) is True  # 0.15 >= 3 * 0.03 = 0.09

    def test_exactly_3x_viable(self):
        """恰好 3x 可行。"""
        assert is_grid_viable(0.09, 0.03) is True

    def test_custom_ratio(self):
        """自定义 min_ratio。"""
        assert is_grid_viable(0.12, 0.03, min_ratio=4.0) is True
        assert is_grid_viable(0.12, 0.03, min_ratio=3.0) is True

    def test_zero_friction_returns_false(self):
        """零摩擦（不合法输入）返回 False。"""
        assert is_grid_viable(1.0, 0.0) is False


class TestMinViableSpread:
    def test_returns_positive(self):
        """最小盈利价差应为正。"""
        spread = min_viable_spread(100.0, 100)
        assert spread > 0.0

    def test_proportional_to_price(self):
        """更高价格对应更大最小价差。"""
        s_low = min_viable_spread(10.0, 100)
        s_high = min_viable_spread(100.0, 100)
        assert s_high > s_low
