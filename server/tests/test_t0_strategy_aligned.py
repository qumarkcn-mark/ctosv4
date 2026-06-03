"""PPE/T0 策略对齐测试。"""
import pytest

from server.engines.t0.ppe_t0_policy import LONG_ONLY, OBSERVE_ONLY, SHORT_ONLY, derive_t0_policy_from_ppe
from server.engines.t0.t0_state_machine import T0State, T0StateMachine


def _bar(high, low, close, open_=None):
    return {"open": open_ or close, "high": high, "low": low, "close": close, "volume": 1000}


def _bottom():
    return [
        _bar(11, 9.5, 10.5),
        _bar(10, 9.0, 9.5),
        _bar(9.5, 7.0, 7.5),
        _bar(10.5, 7.5, 10.2),
    ]


def _top():
    return [
        _bar(11, 9.5, 10.5),
        _bar(11, 9.9, 10.5),
        _bar(13, 10.4, 12.5),
        _bar(12.5, 9.5, 10.0),
    ]


def _policy_kwargs(direction=LONG_ONLY, multiplier=1.0, pivot_id="p1", stage=2):
    return {
        "pivot_id": pivot_id,
        "allowed_t0_direction": direction,
        "size_multiplier": multiplier,
        "ppe_stage": stage,
        "policy_reason": "测试 PPE 许可",
        "policy_source_run_id": "run-test",
    }


def test_policy_projection_defaults_to_observe_when_missing():
    policy = derive_t0_policy_from_ppe(summary={}, position_path={}, source_run_id="")

    assert policy.allowed_t0_direction == OBSERVE_ONLY
    assert policy.size_multiplier == 0.0
    assert "缺少" in policy.policy_reason


def test_policy_projection_maps_buy_and_sell_contexts():
    long_policy = derive_t0_policy_from_ppe(
        summary={"watch_state_machine": {"current_state": {"name": "5分钟三买确认，趋势确立"}}},
        source_run_id="r1",
    )
    short_policy = derive_t0_policy_from_ppe(
        summary={"watch_state_machine": {"current_state": {"name": "压力测试，背驰高抛"}}},
        source_run_id="r2",
    )

    assert long_policy.allowed_t0_direction == LONG_ONLY
    assert long_policy.size_multiplier == 1.0
    assert short_policy.allowed_t0_direction == SHORT_ONLY
    assert short_policy.size_multiplier == 0.5


def test_policy_projection_ignores_failure_branch_text():
    policy = derive_t0_policy_from_ppe(
        summary={
            "watch_state_machine": {
                "current_state": {"name": "5分钟三买确认，趋势确立"},
                "transitions": [
                    {
                        "next_state": "继续观察回踩",
                        "success": "站稳后确认增强",
                        "failure": "失败则转防守",
                    }
                ],
            }
        },
        source_run_id="r3",
    )

    assert policy.allowed_t0_direction == LONG_ONLY
    assert policy.size_multiplier == 1.0


def test_policy_projection_does_not_lock_on_normal_support_defense():
    policy = derive_t0_policy_from_ppe(
        summary={"watch_state_machine": {"current_state": {"name": "5分钟三买尝试，中枢防守"}}},
        source_run_id="r4",
    )

    assert policy.allowed_t0_direction == LONG_ONLY
    assert policy.size_multiplier == 0.1


def test_policy_projection_reduce_lock_is_observe_not_new_short():
    policy = derive_t0_policy_from_ppe(
        summary={"watch_state_machine": {"current_state": {"name": "尾盘未回补，减仓锁利"}}},
        source_run_id="r5",
    )

    assert policy.allowed_t0_direction == OBSERVE_ONLY
    assert policy.size_multiplier == 0.0


def test_direction_gating_by_ppe():
    machine = T0StateMachine(symbol="sz.300394", t0_qty=200, available_t0_qty=200)
    machine.reset_daily("2026-06-03")

    short_blocked = machine.tick(
        current_price=107.8,
        timestamp="2026-06-03 10:30:00",
        pivot_zd=100.0,
        pivot_zg=108.0,
        klines_1m=_top(),
        **_policy_kwargs(direction=LONG_ONLY),
    )
    long_blocked = machine.tick(
        current_price=100.2,
        timestamp="2026-06-03 10:31:00",
        pivot_zd=100.0,
        pivot_zg=108.0,
        klines_1m=_bottom(),
        **_policy_kwargs(direction=SHORT_ONLY),
    )
    observe_blocked = machine.tick(
        current_price=100.2,
        timestamp="2026-06-03 10:32:00",
        pivot_zd=100.0,
        pivot_zg=108.0,
        klines_1m=_bottom(),
        **_policy_kwargs(direction=OBSERVE_ONLY, multiplier=0.0),
    )

    assert short_blocked.signal is None
    assert "拒绝倒T" in short_blocked.reason
    assert long_blocked.signal is None
    assert "拒绝正T" in long_blocked.reason
    assert observe_blocked.signal is None
    assert observe_blocked.allowed_t0_direction == OBSERVE_ONLY


def test_size_multiplier_and_lot_rounding():
    machine = T0StateMachine(symbol="sz.300394", t0_qty=1000)
    machine.reset_daily("2026-06-03")

    result = machine.tick(
        current_price=100.2,
        timestamp="2026-06-03 10:30:00",
        pivot_zd=100.0,
        pivot_zg=108.0,
        klines_1m=_bottom(),
        bi_strength_ratio=0.4,
        **_policy_kwargs(direction=LONG_ONLY, multiplier=0.1, stage=0),
    )

    assert result.signal == "BUY_LONG"
    assert result.signal_qty == 100
    assert result.size_multiplier == 0.1


def test_one_trade_per_window_limit():
    machine = T0StateMachine(symbol="sz.300394", t0_qty=200, available_t0_qty=200)
    machine.reset_daily("2026-06-03")

    opened = machine.tick(
        current_price=107.8,
        timestamp="2026-06-03 10:30:00",
        pivot_zd=100.0,
        pivot_zg=108.0,
        klines_1m=_top(),
        **_policy_kwargs(direction=SHORT_ONLY, pivot_id="pivot-a"),
    )
    closed = machine.tick(
        current_price=99.9,
        timestamp="2026-06-03 11:00:00",
        pivot_zd=100.0,
        pivot_zg=108.0,
        klines_1m=_bottom(),
        **_policy_kwargs(direction=SHORT_ONLY, pivot_id="pivot-a"),
    )
    blocked = machine.tick(
        current_price=107.8,
        timestamp="2026-06-03 13:00:00",
        pivot_zd=100.0,
        pivot_zg=108.0,
        klines_1m=_top(),
        **_policy_kwargs(direction=SHORT_ONLY, pivot_id="pivot-a"),
    )

    assert opened.signal == "SELL_SHORT"
    assert closed.signal == "BUY_SHORT"
    assert blocked.signal is None
    assert "一窗一做" in blocked.reason
    assert blocked.traded_pivot_count == 1


def test_friction_edge_filter_blocks_tiny_remaining_space():
    machine = T0StateMachine(symbol="sz.300394", t0_qty=1000)

    assert machine._has_enough_edge(0.019, 0.01) is False
    assert machine._has_enough_edge(0.021, 0.01) is True


def test_reduce_lock_no_paper_fill_and_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("CT_OS_DB_PATH", str(tmp_path / "test.db"))
    from server.db.database import init_db
    from server.engines.t0.t0_paper_service import get_or_create_t0_account, record_t0_signal

    init_db()
    from server.db.database import get_connection
    conn = get_connection()
    conn.execute("INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (1, 'u1', '用户1')")
    conn.commit()
    conn.close()

    machine = T0StateMachine(symbol="sz.300394", t0_qty=100, available_t0_qty=100)
    machine.reset_daily("2026-06-03")
    machine._state = T0State.POSITION_SHORT
    machine._entry_price = 108.0
    machine._target_price = 100.0
    machine._current_open_qty = 100
    machine._entry_pivot_id = "pivot-a"
    machine._entry_direction = "SHORT"

    result = machine.force_sweep(109.0)
    get_or_create_t0_account(user_id=1)
    fill = record_t0_signal(1, "sz.300394", "REDUCE_LOCK", 109.0, 100, result)

    assert result.signal == "REDUCE_LOCK"
    assert result.reduce_lock_warning == "做T导致底仓流失，转为观察"
    assert fill["skipped"] is True
    assert fill["event_only"] is True
