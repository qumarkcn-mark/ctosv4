from server.db import database
from server.engines.ai_native.universe_resolver import (
    has_active_position_for_symbol,
    list_ai_native_user_ids,
    list_interested_user_ids_for_symbol,
    resolve_ai_native_universe,
)


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def test_universe_merges_positions_and_watchlist_by_user(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1'), (2, 'u2', 'U2')"
        )
        conn.execute(
            """
            INSERT INTO positions (user_id, symbol, name, quantity, avg_cost)
            VALUES (1, 'sh600519', '贵州茅台', 100, 100.0),
                   (2, 'sz000001', '平安银行', 100, 10.0)
            """
        )
        conn.execute(
            "INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (10, 1, '观察', 0), (20, 2, '观察', 0)"
        )
        conn.execute(
            """
            INSERT INTO watchlist_items (group_id, symbol, name, sort_order)
            VALUES (10, 'sh.600519', '贵州茅台', 0),
                   (10, 'sz000988', '华工科技', 1),
                   (20, 'sh688008', '澜起科技', 0)
            """
        )
        conn.commit()
    finally:
        conn.close()

    items = resolve_ai_native_universe(1, ["positions", "watchlist"])

    assert [item["symbol"] for item in items] == ["sh.600519", "sz.000988"]
    assert items[0]["sources"] == ["positions", "watchlist"]
    assert items[0]["has_position"] is True
    assert items[0]["priority"] == 110
    assert items[1]["sources"] == ["watchlist"]
    assert items[1]["has_position"] is False
    assert items[1]["priority"] == 60


def test_universe_lists_interested_users_by_normalized_symbol(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1'), (2, 'u2', 'U2'), (3, 'u3', 'U3')"
        )
        conn.execute(
            """
            INSERT INTO positions (user_id, symbol, name, quantity, avg_cost)
            VALUES (1, 'sh600519', '贵州茅台', 100, 100.0),
                   (3, 'sz000001', '平安银行', 100, 10.0)
            """
        )
        conn.execute(
            "INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (10, 2, '观察', 0), (30, 3, '观察', 0)"
        )
        conn.execute(
            """
            INSERT INTO watchlist_items (group_id, symbol, name, sort_order)
            VALUES (10, 'sh.600519', '贵州茅台', 0),
                   (30, 'sz000001', '平安银行', 0)
            """
        )
        conn.commit()
    finally:
        conn.close()

    assert list_ai_native_user_ids() == [1, 2, 3]
    assert list_interested_user_ids_for_symbol("sh.600519") == [1, 2]
    assert has_active_position_for_symbol("sh600519") is True
