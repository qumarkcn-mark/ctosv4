"""测试 T0 回测框架（使用合成数据）。"""
import pytest
from server.engines.t0.t0_backtest import BacktestConfig, BacktestResult, run_backtest, print_backtest_report
from server.engines.t0.t0_state_machine import T0State


class TestBacktestWithSyntheticData:
    """用合成 K 线数据验证回测核心行为。"""

    @pytest.fixture(autouse=True)
    def _patch_db_and_data(self, tmp_path, monkeypatch):
        """mock 数据库和 kline_lake，注入合成数据。"""
        import os
        os.environ["CT_OS_DB_PATH"] = str(tmp_path / "test.db")
        from server.db.database import init_db
        init_db()

        # 合成 5 天 × 每天 6 根 1M K线（测试用，简化）
        import datetime

        def synthetic_klines(symbol, freq, start_date=None, end_date=None, limit=2000, **kwargs):
            base_dates = [
                "2025-05-26", "2025-05-27", "2025-05-28", "2025-05-29", "2025-05-30"
            ]
            bars = []
            if freq == "1":
                for date in base_dates:
                    # 构造: 先跌到 ZD 触发区，再涨到 ZG 触发区
                    for i, hhmm in enumerate(["09:31", "09:32", "09:33", "09:34", "10:00", "14:56"]):
                        price = 100.0 - (i * 0.3) if i < 3 else 108.0 + (i - 3) * 0.1
                        bars.append({
                            "date": f"{date} {hhmm}:00",
                            "open": price,
                            "high": price + 0.5,
                            "low": price - 0.5,
                            "close": price,
                            "volume": 10000,
                        })
            elif freq == "5":
                for date in base_dates:
                    for i in range(4):
                        bars.append({
                            "date": f"{date} {9 + i}:30:00",
                            "open": 100 + i,
                            "high": 101 + i,
                            "low": 99 + i,
                            "close": 100 + i,
                            "volume": 50000,
                        })
            return bars

        monkeypatch.setattr(
            "server.engines.t0.t0_backtest.query_klines",
            synthetic_klines,
        )

        # mock structure_snapshots: ZD=100.0, ZG=108.0
        from unittest.mock import MagicMock, patch

        def mock_get_connection():
            import sqlite3
            conn = sqlite3.connect(str(tmp_path / "test.db"))
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(
            "server.engines.t0.t0_backtest.get_connection",
            mock_get_connection,
        )

        yield
        del os.environ["CT_OS_DB_PATH"]

    def test_backtest_runs_without_error(self):
        """回测能正常完成，返回 BacktestResult。"""
        config = BacktestConfig(
            symbol="sz.300394",
            start_date="2025-05-26",
            end_date="2025-05-30",
            t0_qty=100,
            use_paper_db=False,
        )
        result = run_backtest(config)
        assert isinstance(result, BacktestResult)
        assert result.symbol == "sz.300394"
        assert result.trading_days >= 1

    def test_lockdown_stops_trading(self):
        """LOCKDOWN 触发后当日不再开仓。"""
        from server.engines.t0.t0_state_machine import T0StateMachine
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        # 手动进入 LOCKDOWN
        machine._state = T0State.LOCKDOWN
        machine._daily_stop_count = 1

        # tick 多次，不应产生任何开仓信号
        for _ in range(5):
            result = machine.tick(
                current_price=99.5,
                timestamp="2025-05-26 11:00:00",
                pivot_zd=100.0,
                pivot_zg=108.0,
                klines_1m=[],
            )
            assert result.signal is None
            assert result.state == T0State.LOCKDOWN.value

    def test_force_sweep_clears_position(self):
        """14:55 强平后无残留头寸（state=IDLE）。"""
        from server.engines.t0.t0_state_machine import T0StateMachine
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 100.0
        machine._target_price = 108.0

        result = machine.force_sweep(104.0)
        assert result.state == T0State.IDLE.value
        assert machine._entry_price is None

    def test_net_pnl_includes_fees(self):
        """净 PnL 已扣除交易费用（结果应小于毛利润）。"""
        from server.engines.t0.t0_state_machine import T0StateMachine
        machine = T0StateMachine(symbol="sz.300394", t0_qty=100)
        machine.reset_daily("2025-05-26")
        machine._state = T0State.POSITION_LONG
        machine._entry_price = 100.0
        machine._target_price = 108.0
        machine._stop_structural = 98.0
        machine._stop_catastrophic = 97.0

        result = machine.tick(
            current_price=109.0,  # 触达止盈
            timestamp="2025-05-26 13:00:00",
            pivot_zd=100.0,
            pivot_zg=108.0,
            klines_1m=[],
        )
        # 毛利润 = (109 - 100) * 100 = 900
        # 净 PnL 应小于 900（已扣费）
        assert result.daily_pnl < 900.0
        assert result.daily_pnl > 0.0

    def test_print_report_no_crash(self):
        """print_backtest_report 不崩溃，返回字符串。"""
        from server.engines.t0.t0_backtest import BacktestResult
        result = BacktestResult(
            symbol="sz.300394",
            trading_days=5,
            total_signals=10,
            total_fills=10,
            win_count=3,
            loss_count=2,
            gross_pnl=500.0,
            total_fees=50.0,
            net_pnl=450.0,
            max_drawdown=100.0,
            sharpe_daily=1.5,
            daily_summary=[
                {"date": "2025-05-26", "signals": 2, "fills": 2, "net_pnl": 150.0, "stop_count": 0, "final_state": "IDLE"},
            ],
        )
        report = print_backtest_report(result)
        assert isinstance(report, str)
        assert "sz.300394" in report

    def test_strict_ppe_missing_defaults_to_observe_only(self, monkeypatch):
        """严格 PPE 回测缺少大周期许可时，不允许状态机自行开仓。"""
        calls: list[dict] = []
        self._patch_fake_machine(monkeypatch, calls)

        config = BacktestConfig(
            symbol="sz.300394",
            start_date="2025-05-26",
            end_date="2025-05-30",
            t0_qty=100,
            use_paper_db=False,
            strict_ppe_policy=True,
        )
        result = run_backtest(config)

        assert result.total_signals == 0
        assert calls
        assert all(call.get("allowed_t0_direction") == "OBSERVE_ONLY" for call in calls)
        assert all(day["allowed_t0_direction"] == "OBSERVE_ONLY" for day in result.daily_summary)

    def test_strict_ppe_short_only_blocks_positive_t(self, monkeypatch):
        """PPE 只允许倒T时，回测不得出现正T信号。"""
        calls: list[dict] = []
        self._patch_fake_machine(monkeypatch, calls)

        config = BacktestConfig(
            symbol="sz.300394",
            start_date="2025-05-26",
            end_date="2025-05-30",
            t0_qty=100,
            use_paper_db=False,
            strict_ppe_policy=True,
            position_path={"data_status": "ready", "current_phase": "压力测试"},
            policy_source_run_id="run_short",
        )
        result = run_backtest(config)

        assert result.trades
        assert all(trade["signal"] != "BUY_LONG" for trade in result.trades)
        assert all(trade["allowed_t0_direction"] == "SHORT_ONLY" for trade in result.trades)
        assert all(call.get("policy_source_run_id") == "run_short" for call in calls)

    def test_strict_ppe_long_only_blocks_short_t(self, monkeypatch):
        """PPE 只允许正T时，回测不得出现倒T信号。"""
        calls: list[dict] = []
        self._patch_fake_machine(monkeypatch, calls)

        config = BacktestConfig(
            symbol="sz.300394",
            start_date="2025-05-26",
            end_date="2025-05-30",
            t0_qty=100,
            use_paper_db=False,
            strict_ppe_policy=True,
            position_path={"data_status": "ready", "current_phase": "三买确认"},
        )
        result = run_backtest(config)

        assert result.trades
        assert all(trade["signal"] != "SELL_SHORT" for trade in result.trades)
        assert all(trade["allowed_t0_direction"] == "LONG_ONLY" for trade in result.trades)

    @staticmethod
    def _patch_fake_machine(monkeypatch, calls: list[dict]):
        """替换状态机，专门验证 backtest 是否把 PPE 字段透传给 tick。"""
        class FakeResult:
            def __init__(self, signal=None, state="IDLE", reason=""):
                self.signal = signal
                self.signal_qty = 100 if signal else None
                self.entry_price = None
                self.daily_pnl = 0.0
                self.state = state
                self.reason = reason
                self.current_pivot_id = "fake-pivot"

        class FakeMachine:
            def __init__(self, symbol, t0_qty=100):
                self.symbol = symbol
                self.t0_qty = t0_qty
                self._daily_pnl = 0.0
                self._daily_stop_count = 0
                self._state = T0State.IDLE
                self._traded_pivot_ids = set()
                self._emitted_dates = set()

            def reset_daily(self, date):
                self._date = date
                self._state = T0State.IDLE

            def tick(self, **kwargs):
                calls.append(kwargs)
                direction = kwargs.get("allowed_t0_direction")
                date = str(kwargs.get("timestamp", ""))[:10]
                if date in self._emitted_dates:
                    return FakeResult()
                self._emitted_dates.add(date)
                if direction == "LONG_ONLY":
                    return FakeResult("BUY_LONG", reason="fake long")
                if direction == "SHORT_ONLY":
                    return FakeResult("SELL_SHORT", reason="fake short")
                if direction is None:
                    return FakeResult("BUY_LONG", reason="fake legacy")
                return FakeResult()

            def force_sweep(self, price):
                return FakeResult()

        monkeypatch.setattr(
            "server.engines.t0.t0_state_machine.T0StateMachine",
            FakeMachine,
        )
