"""测试 T0 API 的用户边界与基础校验。"""
import os

import pytest


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    os.environ["CT_OS_DB_PATH"] = str(tmp_path / "test.db")
    from server.db.database import init_db

    init_db()
    yield
    del os.environ["CT_OS_DB_PATH"]


def _seed_two_users_same_symbol():
    from server.db.database import get_connection

    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (1, 'u1', '用户1')")
        conn.execute("INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (2, 'u2', '用户2')")
        conn.execute("INSERT INTO watchlist_groups (id, user_id, name) VALUES (1, 1, '持仓')")
        conn.execute("INSERT INTO watchlist_groups (id, user_id, name) VALUES (2, 2, '持仓')")
        conn.execute("INSERT INTO watchlist_items (group_id, symbol, name) VALUES (1, 'sz.300394', '测试')")
        conn.execute("INSERT INTO watchlist_items (group_id, symbol, name) VALUES (2, 'sz.300394', '测试')")
        conn.execute(
            """
            INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, updated_at)
            VALUES (1, 'sz.300394', '测试', 500, 10.0, CURRENT_TIMESTAMP)
            """
        )
        conn.execute(
            """
            INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, updated_at)
            VALUES (2, 'sz.300394', '测试', 300, 10.0, CURRENT_TIMESTAMP)
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_enable_t0_only_updates_current_users_watchlist_item():
    """同一 symbol 出现在多个用户自选时，只更新当前用户的那条。"""
    from server.api.t0 import EnableT0Request, enable_t0
    from server.db.database import get_connection

    _seed_two_users_same_symbol()

    result = enable_t0("sz.300394", EnableT0Request(t0_qty=200), user_id=1)
    assert result["status"] == "ok"

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT wg.user_id, wi.t0_enabled, wi.t0_qty
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             WHERE wi.symbol = 'sz.300394'
             ORDER BY wg.user_id
            """
        ).fetchall()
    finally:
        conn.close()

    assert [(r[0], r[1], r[2]) for r in rows] == [(1, 1, 200), (2, 0, 0)]


def test_enable_t0_rejects_qty_above_users_position():
    """做T数量不能超过当前用户底仓。"""
    from fastapi import HTTPException
    from server.api.t0 import EnableT0Request, enable_t0

    _seed_two_users_same_symbol()

    with pytest.raises(HTTPException) as exc:
        enable_t0("sz.300394", EnableT0Request(t0_qty=400), user_id=2)

    assert exc.value.status_code == 400


def test_enable_t0_rejects_symbol_without_position():
    """没有底仓的 watchlist 股票不能启用做T。"""
    from fastapi import HTTPException
    from server.api.t0 import EnableT0Request, enable_t0
    from server.db.database import get_connection

    _seed_two_users_same_symbol()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO watchlist_items (group_id, symbol, name) VALUES (1, 'sh.600519', '无底仓')")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HTTPException) as exc:
        enable_t0("sh.600519", EnableT0Request(t0_qty=100), user_id=1)

    assert exc.value.status_code == 400


def test_enable_t0_rejects_non_lot_qty():
    """做T数量必须是一手整数倍。"""
    from fastapi import HTTPException
    from server.api.t0 import EnableT0Request, enable_t0

    _seed_two_users_same_symbol()

    with pytest.raises(HTTPException) as exc:
        enable_t0("sz.300394", EnableT0Request(t0_qty=150), user_id=1)

    assert exc.value.status_code == 400


def test_disable_t0_turns_off_current_users_item():
    """关闭做T只影响当前用户的 watchlist item。"""
    from server.api.t0 import EnableT0Request, disable_t0, enable_t0
    from server.db.database import get_connection

    _seed_two_users_same_symbol()
    enable_t0("sz.300394", EnableT0Request(t0_qty=200), user_id=1)

    result = disable_t0("sz.300394", user_id=1)
    assert result["status"] == "ok"

    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT wi.t0_enabled, wi.t0_qty
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             WHERE wg.user_id = 1 AND wi.symbol = 'sz.300394'
            """
        ).fetchone()
    finally:
        conn.close()

    assert tuple(row) == (0, 0)


def test_get_all_t0_states_returns_action_fields():
    """T0 状态接口返回卡片/Drawer 需要的新字段，并保留旧字段。"""
    from server.api.t0 import get_all_t0_states
    from server.db.database import get_connection

    _seed_two_users_same_symbol()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO t0_state_cache (
                user_id, symbol, state, pivot_zd, pivot_zg,
                t0_qty, friction_per_share, is_grid_viable,
                signal, signal_price, reason, state_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "sz.300394",
                "IDLE",
                9.8,
                10.6,
                200,
                0.02,
                1,
                None,
                None,
                "进入ZD触发区但1M底分型未确认",
                '{"current_open_qty": 100, "risk_budget_left": 88.5, "lock_reason": "", "position_constraints": {"available_t0_qty": 200, "configured_t0_qty": 200, "lot_size": 100}, "allowed_t0_direction": "LONG_ONLY", "size_multiplier": 0.5, "ppe_stage": 3, "policy_reason": "PPE测试", "policy_source_run_id": "run-1", "current_pivot_id": "pivot-1", "traded_pivot_count": 2, "reduce_lock_warning": ""}',
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = get_all_t0_states(user_id=1)
    state = result["states"]["sz.300394"]

    assert result["engine_enabled"] is False
    assert result["mode"] == "paper"
    assert state["pivot_zd"] == 9.8
    assert state["data_quality"] == "ready"
    assert state["action_window"] == "near_zd"
    assert state["next_step"] == "进入ZD触发区但1M底分型未确认"
    assert state["signal_qty"] == 100
    assert state["available_t0_qty"] == 200
    assert state["risk_budget_left"] == 88.5
    assert state["lock_reason"] == ""
    assert state["position_constraints"]["lot_size"] == 100
    assert state["allowed_t0_direction"] == "LONG_ONLY"
    assert state["size_multiplier"] == 0.5
    assert state["ppe_stage"] == 3
    assert state["policy_reason"] == "PPE测试"
    assert state["policy_source_run_id"] == "run-1"
    assert state["current_pivot_id"] == "pivot-1"
    assert state["traded_pivot_count"] == 2
    assert state["reduce_lock_warning"] == ""
    assert state["engine_enabled"] is False
    assert state["mode"] == "paper"
