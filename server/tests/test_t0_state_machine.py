"""测试 T0 状态机完整生命周期。"""
import pytest
from server.engines.t0.t0_state_machine import T0StateMachine, T0State


def _bar(high, low, close, open_=None):
    return {"open": open_ or close, "high": high, "low": low, "close": close, "volume": 1000}


def _make_klines_with_bottom_fractal(base_price=100.0):
    """构造触发底分型确认的 1M K线序列（右侧确认）。"""
    # klines[-3]=left, [-2]=mid, [-1]=right
    # mid.low < left.low AND mid.low < right.low
    # mid.high < max(left.high, right.high)
    # right.close > mid.high
    return [
        _bar(11, 9.5, 10.5),   # unused context
        _bar(10, 9.0, 9.5),    # left (high=10, low=9.0)
        _bar(9.5, 7.0, 7.5),   # mid (high=9.5, low=7.0)
        _bar(10.5, 7.5, 10.2), # right (close=10.2 > mid.high=9.5 → 确认)
    ]


def _make_klines_with_top_fractal(base_price=100.0):
    """构造触发顶分型确认的 1M K线序列（右侧确认）。"""
    # mid.high > left.high AND mid.high > right.high
    # mid.low > min(left.low, right.low)
    # right.close < mid.low
    return [
        _bar(11, 9.5, 10.5),     # unused context
        _bar(11, 9.9, 10.5),     # left (high=11, low=9.9)
        _bar(13, 10.4, 12.5),    # mid (high=13, low=10.4)
        _bar(12.5, 9.5, 10.0),   # right (close=10.0 < mid.low=10.4 → 确认)
    ]


class TestLongT0Lifecycle:
    """正T完整生命周期。"""

    def _make_machine(self):
        return T0StateMachine(symbol="sz.300394", t0_qty=100)

    def test_idle_to_position_long(self):
        """IDLE → POSITION_LONG（低吸触发 + 1M 底分型确认）。"""
        machine = self._make_machine()
        machine.reset_daily("2025-05-26")

        klines = _make_klines_with_bottom_fractal()
        # 价格在 ZD 触发区：current_price <= ZD * 1.005
        result = machine.tick(
            current_price=100.3,  # ZD=100, 触发区 <= 100.5 (避免浮点边界)
            timestamp="2025-05-26 10:30:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=klines,
            atr_5m=0.5,
        )
        assert result.state == T0State.POSITION_LONG.value
        assert result.signal == "BUY_LONG"
        assert result.entry_price == pytest.approx(100.3)
        assert result.target_price == pytest.approx(108.0)

    def test_bi_strength_veto_rejects_accelerating_down_bi(self):
        """向下笔仍在放大时，拒绝低吸开仓。"""
        machine = self._make_machine()
        machine.reset_daily("2025-05-26")

        result = machine.tick(
            current_price=100.3,
            timestamp="2025-05-26 10:30:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=_make_klines_with_bottom_fractal(),
            atr_5m=0.5,
            bi_strength_ratio=0.92,
        )

        assert result.signal is None
        assert result.state == T0State.IDLE.value
        assert "笔动能未衰减" in result.reason

    def test_first_zd_touch_uses_half_signal_qty_and_pnl(self):
        """首次触碰 ZD 时使用减半试探数量，并用实际数量计算后续 PnL。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=400)
        machine.reset_daily("2025-05-26")

        opened = machine.tick(
            current_price=100.3,
            timestamp="2025-05-26 10:30:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=_make_klines_with_bottom_fractal(),
            atr_5m=0.5,
            bi_strength_ratio=0.42,
        )

        assert opened.signal == "BUY_LONG"
        assert opened.signal_qty == 200
        assert machine.serialize()["current_open_qty"] == 200

        closed = machine.tick(
            current_price=108.5,
            timestamp="2025-05-26 11:00:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=[],
        )

        assert closed.signal == "SELL_LONG"
        assert closed.signal_qty == 200
        assert closed.entry_price == pytest.approx(100.3)
        assert closed.daily_pnl < (108.5 - 100.3) * 400
        assert closed.daily_pnl > (108.5 - 100.3) * 150

    def test_position_long_to_idle_profit(self):
        """POSITION_LONG → IDLE（止盈：价格触达 ZG）。"""
        machine = self._make_machine()
        machine.reset_daily("2025-05-26")
        # 手动设置持仓状态
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 100.0
        machine._target_price = 108.0
        machine._stop_structural = 98.0
        machine._stop_catastrophic = 97.0

        result = machine.tick(
            current_price=108.5,  # >= target
            timestamp="2025-05-26 11:00:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=[],
        )
        assert result.state == T0State.IDLE.value
        assert result.signal == "SELL_LONG"
        assert result.daily_pnl > 0


class TestStopLossLockdown:
    def test_position_long_catastrophic_stop(self):
        """POSITION_LONG → LOCKDOWN（灾难止损）。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 100.0
        machine._target_price = 108.0
        machine._stop_structural = 98.0
        machine._stop_catastrophic = 97.0

        result = machine.tick(
            current_price=96.5,  # <= 97.0 灾难止损
            timestamp="2025-05-26 10:45:00",
            pivot_zd=98.0,
            pivot_zg=108.0,
            klines_1m=[],
        )
        assert result.state == T0State.LOCKDOWN.value
        assert result.signal == "STOP_LONG"
        assert result.daily_stop_count == 1

    def test_position_long_structural_stop(self):
        """POSITION_LONG → LOCKDOWN（结构止损）。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 102.0
        machine._target_price = 108.0
        machine._stop_structural = 98.0
        machine._stop_catastrophic = 97.0  # 灾难止损更低

        result = machine.tick(
            current_price=97.5,  # <= 98.0 结构止损
            timestamp="2025-05-26 10:45:00",
            pivot_zd=99.0,
            pivot_zg=108.0,
            klines_1m=[],
        )
        assert result.state == T0State.LOCKDOWN.value
        assert result.signal == "STOP_LONG"

    def test_lockdown_rejects_new_signal(self):
        """LOCKDOWN 后拒绝新开仓信号。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.LOCKDOWN

        klines = _make_klines_with_bottom_fractal()
        result = machine.tick(
            current_price=100.5,
            timestamp="2025-05-26 13:00:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=klines,
        )
        assert result.state == T0State.LOCKDOWN.value
        assert result.signal is None
        assert "锁死" in result.reason


class TestShortT0Lifecycle:
    def test_idle_to_position_short(self):
        """IDLE → POSITION_SHORT（高抛触发 + 1M 顶分型确认）。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")

        klines = _make_klines_with_top_fractal()
        # 价格在 ZG 触发区：current_price >= ZG * 0.995
        result = machine.tick(
            current_price=107.7,  # ZG=108, 触发区 >= 107.46 (避免浮点边界)
            timestamp="2025-05-26 10:30:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=klines,
            atr_5m=0.5,
        )
        assert result.state == T0State.POSITION_SHORT.value
        assert result.signal == "SELL_SHORT"

    def test_position_short_to_idle_profit(self):
        """POSITION_SHORT → IDLE（买回：价格跌回 ZD）。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_SHORT
        machine._entry_price = 107.0
        machine._target_price = 100.0
        machine._stop_structural = 110.0
        machine._stop_catastrophic = 110.21

        result = machine.tick(
            current_price=99.5,  # <= target=100.0
            timestamp="2025-05-26 13:00:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=[],
        )
        assert result.state == T0State.IDLE.value
        assert result.signal == "BUY_SHORT"
        assert result.daily_pnl > 0

    def test_position_short_structural_stop(self):
        """POSITION_SHORT → LOCKDOWN（涨破结构止损）。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_SHORT
        machine._entry_price = 107.0
        machine._target_price = 100.0
        machine._stop_structural = 110.0
        machine._stop_catastrophic = 110.21

        result = machine.tick(
            current_price=110.5,  # >= 110.0 结构止损
            timestamp="2025-05-26 11:00:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=[],
        )
        assert result.state == T0State.LOCKDOWN.value
        assert result.signal == "STOP_SHORT"


class TestFrictionGating:
    def test_rejects_when_grid_not_viable(self):
        """摩擦不可行时（ZG - ZD 太小）拒绝开仓。"""
        machine = T0StateMachine(symbol="sz.000001", t0_qty=100)
        machine.reset_daily("2025-05-26")

        klines = _make_klines_with_bottom_fractal(base_price=10.0)
        # ZD=10.0, ZG=10.05 → spread=0.05，远小于 3x 摩擦成本
        result = machine.tick(
            current_price=10.03,
            timestamp="2025-05-26 10:30:00",
            pivot_zd=10.0,
            pivot_zg=10.05,  # 仅 0.5% 价差
            klines_1m=klines,
        )
        # is_grid_viable=False，不应开仓
        assert result.is_grid_viable is False
        assert result.signal is None


class TestForceSweep:
    def test_sweep_from_position_long(self):
        """14:55 强制平仓 POSITION_LONG → IDLE。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 100.0

        result = machine.force_sweep(103.0)
        assert result.signal == "SWEEP_LONG"
        assert result.state == T0State.IDLE.value

    def test_sweep_from_position_short(self):
        """14:55 强制平仓 POSITION_SHORT → IDLE。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_SHORT
        machine._entry_price = 107.0

        result = machine.force_sweep(105.0)
        assert result.signal == "SWEEP_SHORT"
        assert result.state == T0State.IDLE.value

    def test_sweep_from_idle_no_signal(self):
        """IDLE 状态 force_sweep 无信号。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        result = machine.force_sweep(100.0)
        assert result.signal is None


class TestSerializationRoundTrip:
    def test_serialize_deserialize(self):
        """serialize() / from_dict() 往返一致。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=200)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 105.5
        machine._target_price = 112.0
        machine._stop_structural = 102.0
        machine._stop_catastrophic = 102.385
        machine._daily_pnl = 150.0
        machine._daily_trades = 3
        machine._daily_stop_count = 1

        data = machine.serialize()
        restored = T0StateMachine.from_dict(data)

        assert restored.symbol == machine.symbol
        assert restored.t0_qty == machine.t0_qty
        assert restored._state == machine._state
        assert restored._entry_price == machine._entry_price
        assert restored._target_price == machine._target_price
        assert restored._stop_structural == machine._stop_structural
        assert restored._stop_catastrophic == machine._stop_catastrophic
        assert restored._daily_pnl == machine._daily_pnl
        assert restored._daily_trades == machine._daily_trades
        assert restored._daily_stop_count == machine._daily_stop_count

    def test_reset_daily_clears_state(self):
        """reset_daily() 清零日内状态。"""
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 100.0
        machine._daily_pnl = 500.0
        machine._daily_trades = 5
        machine._daily_stop_count = 2

        machine.reset_daily("2025-05-27")
        assert machine._state == T0State.IDLE
        assert machine._entry_price is None
        assert machine._daily_pnl == 0.0
        assert machine._daily_trades == 0
        assert machine._daily_stop_count == 0
        assert machine._trade_date == "2025-05-27"
