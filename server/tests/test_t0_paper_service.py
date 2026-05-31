"""测试 T0 纸盘撮合服务。"""
import pytest
from unittest.mock import patch, MagicMock
from server.engines.t0.t0_state_machine import T0TickResult, T0State


def _make_tick_result(signal="BUY_LONG", entry_price=100.0, daily_trades=1):
    return T0TickResult(
        state=T0State.POSITION_LONG.value,
        signal=signal,
        signal_price=100.5,
        pivot_zd=100.0,
        pivot_zg=108.0,
        entry_price=entry_price,
        target_price=108.0,
        stop_structural=98.0,
        stop_catastrophic=97.0,
        is_grid_viable=True,
        friction_per_share=0.36,
        daily_pnl=0.0,
        daily_trades=daily_trades,
        daily_stop_count=0,
        reason="测试信号",
    )


class TestRecordT0Signal:
    @pytest.fixture(autouse=True)
    def _patch_db(self, tmp_path):
        """使用临时 SQLite 数据库运行测试。"""
        import os
        os.environ["CT_OS_DB_PATH"] = str(tmp_path / "test.db")
        from server.db.database import init_db
        init_db()
        from server.db.database import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (1, 'test_user_1', '测试用户')"
        )
        conn.commit()
        conn.close()
        yield
        # 清理 env
        del os.environ["CT_OS_DB_PATH"]

    def test_record_creates_fill(self):
        """record_t0_signal() 写入 paper_fills。"""
        from server.engines.t0.t0_paper_service import record_t0_signal, get_or_create_t0_account
        from server.db.database import get_connection

        get_or_create_t0_account(user_id=1)
        result = record_t0_signal(
            user_id=1,
            symbol="sz.300394",
            signal="BUY_LONG",
            signal_price=100.5,
            t0_qty=100,
            tick_result=_make_tick_result("BUY_LONG"),
        )
        assert result["skipped"] is False
        assert result["fill_id"] is not None

        conn = get_connection()
        row = conn.execute(
            "SELECT fill_id FROM paper_fills WHERE fill_id = ?",
            (result["fill_id"],),
        ).fetchone()
        assert row is not None

    def test_idempotency_skip_duplicate(self):
        """相同 idempotency_key 不重复写入。"""
        from server.engines.t0.t0_paper_service import record_t0_signal, get_or_create_t0_account

        get_or_create_t0_account(user_id=1)
        tick = _make_tick_result("BUY_LONG", daily_trades=2)

        r1 = record_t0_signal(1, "sz.300394", "BUY_LONG", 100.5, 100, tick)
        r2 = record_t0_signal(1, "sz.300394", "BUY_LONG", 100.5, 100, tick)

        assert r1["skipped"] is False
        assert r2["skipped"] is True

    def test_fees_correctly_set(self):
        """费用字段正确填入（commission, stamp_tax, transfer_fee, slippage）。"""
        from server.engines.t0.t0_paper_service import record_t0_signal, get_or_create_t0_account
        from server.db.database import get_connection

        get_or_create_t0_account(user_id=1)
        result = record_t0_signal(
            user_id=1,
            symbol="sz.300394",
            signal="SELL_LONG",
            signal_price=108.0,
            t0_qty=100,
            tick_result=_make_tick_result("SELL_LONG", entry_price=100.0, daily_trades=3),
        )
        conn = get_connection()
        row = conn.execute(
            "SELECT commission, stamp_tax, transfer_fee, slippage FROM paper_fills WHERE fill_id = ?",
            (result["fill_id"],),
        ).fetchone()
        assert row is not None
        commission, stamp_tax, transfer_fee, slippage = row
        # SELL 方向有印花税
        assert stamp_tax > 0.0
        # 佣金最低 5 元
        assert commission >= 5.0
        # 过户费存在
        assert transfer_fee > 0.0

    def test_buy_side_no_stamp_tax(self):
        """BUY 方向无印花税。"""
        from server.engines.t0.t0_paper_service import record_t0_signal, get_or_create_t0_account
        from server.db.database import get_connection

        get_or_create_t0_account(user_id=1)
        result = record_t0_signal(
            user_id=1,
            symbol="sz.300394",
            signal="BUY_LONG",
            signal_price=100.0,
            t0_qty=100,
            tick_result=_make_tick_result("BUY_LONG", daily_trades=5),
        )
        conn = get_connection()
        row = conn.execute(
            "SELECT stamp_tax FROM paper_fills WHERE fill_id = ?",
            (result["fill_id"],),
        ).fetchone()
        assert row[0] == 0.0


class TestDailyT0Summary:
    @pytest.fixture(autouse=True)
    def _patch_db(self, tmp_path):
        import os
        os.environ["CT_OS_DB_PATH"] = str(tmp_path / "test.db")
        from server.db.database import init_db
        init_db()
        yield
        del os.environ["CT_OS_DB_PATH"]

    def test_summary_empty_when_no_trades(self):
        """无交易时汇总返回零。"""
        from server.engines.t0.t0_paper_service import get_daily_t0_summary
        summary = get_daily_t0_summary(user_id=1)
        assert summary["total_trades"] == 0
        assert summary["net_pnl"] == 0.0
