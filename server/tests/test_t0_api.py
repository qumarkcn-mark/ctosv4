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
