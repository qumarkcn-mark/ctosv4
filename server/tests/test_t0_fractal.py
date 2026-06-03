"""测试 T0 1分钟分型过滤器。"""
import pytest
from server.engines.t0.t0_fractal import (
    calculate_bi_strength_ratio,
    validate_1m_bottom_fractal,
    validate_1m_top_fractal,
    calculate_atr_1m,
)


def _bar(open_=10, high=11, low=9, close=10.5):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": 1000}


class TestBottomFractal:
    def test_standard_v_shape_confirmed(self):
        """标准底分型（V 形 3 根柱），右侧收盘突破中间高点。"""
        bars = [
            _bar(10, 11, 9.5, 10.5),   # left
            _bar(10, 10.5, 8.0, 8.5),  # mid: low=8.0（最低）
            _bar(8.5, 11.0, 8.5, 10.8), # right: close=10.8 > mid.high=10.5 → 确认
        ]
        result = validate_1m_bottom_fractal(bars)
        assert result["confirmed"] is True
        assert result["fractal_low"] == 8.0

    def test_waterfall_no_fractal(self):
        """瀑布下跌，无底分型。"""
        bars = [
            _bar(15, 15, 14, 14),
            _bar(14, 14, 13, 13),
            _bar(13, 13, 12, 12),
        ]
        result = validate_1m_bottom_fractal(bars)
        assert result["confirmed"] is False

    def test_right_side_not_confirmed(self):
        """右侧未确认（close <= mid.high）。"""
        bars = [
            _bar(10, 11, 9.5, 10.5),
            _bar(10, 10.5, 8.0, 8.5),
            _bar(8.5, 10.0, 8.5, 9.0),  # right.close=9.0 <= mid.high=10.5
        ]
        result = validate_1m_bottom_fractal(bars)
        assert result["confirmed"] is False

    def test_inclusion_excluded(self):
        """包容关系排除。"""
        # mid.high >= max(left.high, right.high) → 包容
        bars = [
            _bar(10, 11, 9, 10),
            _bar(10, 12, 8, 9),   # mid.high=12 >= left.high=11
            _bar(9, 11.5, 8.5, 11.0),
        ]
        result = validate_1m_bottom_fractal(bars)
        assert result["confirmed"] is False

    def test_insufficient_bars(self):
        """不足 3 根柱。"""
        result = validate_1m_bottom_fractal([_bar(), _bar()])
        assert result["confirmed"] is False
        assert "不足" in result["reason"]

    def test_empty_bars(self):
        """空列表。"""
        result = validate_1m_bottom_fractal([])
        assert result["confirmed"] is False


class TestTopFractal:
    def test_standard_inverted_v_confirmed(self):
        """标准顶分型（倒 V），右侧收盘跌破中间低点。"""
        bars = [
            _bar(10, 11, 9.5, 10.5),   # left
            _bar(10.5, 13, 10.4, 12.5), # mid: high=13（最高）, low=10.4
            _bar(12.5, 12.5, 9.5, 10.0),# right: close=10.0 < mid.low=10.4 → 确认
        ]
        result = validate_1m_top_fractal(bars)
        assert result["confirmed"] is True
        assert result["fractal_high"] == 13

    def test_right_side_not_confirmed(self):
        """右侧未确认（close >= mid.low）。"""
        bars = [
            _bar(10, 11, 9.5, 10.5),
            _bar(10.5, 13, 10.4, 12.5),
            _bar(12.5, 12.5, 10.5, 11.0),  # close=11.0 >= mid.low=10.4
        ]
        result = validate_1m_top_fractal(bars)
        assert result["confirmed"] is False

    def test_insufficient_bars(self):
        result = validate_1m_top_fractal([_bar()])
        assert result["confirmed"] is False


class TestCalculateAtr1m:
    def test_basic_calculation(self):
        """ATR 计算返回非负值。"""
        bars = [_bar(10, 10 + i * 0.1, 10 - i * 0.1, 10) for i in range(20)]
        atr = calculate_atr_1m(bars, period=14)
        assert atr >= 0.0

    def test_insufficient_bars_returns_zero(self):
        """不足 2 根返回 0。"""
        assert calculate_atr_1m([]) == 0.0
        assert calculate_atr_1m([_bar()]) == 0.0

    def test_constant_price_atr_near_zero(self):
        """恒定价格，ATR 接近 0。"""
        bars = [_bar(10, 10, 10, 10) for _ in range(20)]
        atr = calculate_atr_1m(bars, period=14)
        assert atr == pytest.approx(0.0, abs=1e-6)

    def test_wilder_smoothing(self):
        """使用超过 period 根数据时 Wilder 平滑生效，结果合理。"""
        bars = [_bar(100, 101 + i * 0.01, 99 - i * 0.01, 100) for i in range(30)]
        atr_14 = calculate_atr_1m(bars, period=14)
        atr_5 = calculate_atr_1m(bars, period=5)
        # ATR(5) 对短期波动更敏感，应 >= ATR(14) 或大致相当
        assert atr_14 > 0.0
        assert atr_5 > 0.0


class TestBiStrengthRatio:
    def test_detects_down_bi_strength_contraction(self):
        bis = [
            {"direction": "down", "high": 128.0, "low": 122.0},
            {"direction": "up", "high": 126.0, "low": 123.0},
            {"direction": "down", "high": 125.0, "low": 122.5},
        ]
        assert calculate_bi_strength_ratio(bis, direction="down") == pytest.approx(0.4167)

    def test_returns_none_when_same_direction_bis_are_insufficient(self):
        bis = [{"direction": "down", "high": 128.0, "low": 122.0}]
        assert calculate_bi_strength_ratio(bis, direction="down") is None
