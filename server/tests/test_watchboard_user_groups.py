"""测试 WatchBoard 使用教练 watchlist 分类，而不是交易持仓自动分组。"""
import os

import pytest


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    os.environ["CT_OS_DB_PATH"] = str(tmp_path / "test.db")
    from server.db.database import init_db

    init_db()
    yield
    del os.environ["CT_OS_DB_PATH"]


def _seed_watchboard_groups():
    from server.db.database import get_connection

    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (1, 'u1', '用户1')")
        conn.execute("INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (1, 1, '观察', 0)")
        conn.execute("INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (2, 1, '持仓', 1)")
        conn.execute("INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (1, 'sh.600519', '茅台', 0)")
        conn.execute("INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (2, 'sz.000725', '京东方A', 0)")
        conn.execute(
            """
            INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, current_price, updated_at)
            VALUES (1, 'sz.000725', '京东方A', 1000, 4.10, 4.30, CURRENT_TIMESTAMP)
            """
        )
        conn.execute(
            """
            INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, current_price, updated_at)
            VALUES (1, 'sz.300999', '持仓但不在教练列表', 1000, 20.0, 21.0, CURRENT_TIMESTAMP)
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_watchboard_groups_follow_user_watchlist_and_attach_position_overlay():
    """WatchBoard 展示用户分类；真实持仓只附加到同名 watchlist 股票。"""
    from server.api.ai_structure import _load_watchboard_groups

    _seed_watchboard_groups()

    groups = _load_watchboard_groups(1)

    assert [group["name"] for group in groups] == ["观察", "持仓"]
    assert [item["symbol"] for item in groups[0]["items"]] == ["sh.600519"]
    assert groups[0]["items"][0]["position"] is None

    holding_item = groups[1]["items"][0]
    assert holding_item["symbol"] == "sz.000725"
    assert holding_item["position"]["shares"] == 1000
    assert holding_item["position"]["cost"] == 4.10

    all_symbols = [item["symbol"] for group in groups for item in group["items"]]
    assert "sz.300999" not in all_symbols


def test_watchboard_universe_excludes_positions_not_in_coach_watchlist():
    """后台自动推演 universe 跟随教练 watchlist，不自动扫交易面板持仓。"""
    from server.engines.ai_native.universe_resolver import resolve_watchboard_universe, list_watchboard_user_ids

    _seed_watchboard_groups()

    universe = resolve_watchboard_universe(1)
    symbols = [item["symbol"] for item in universe]

    assert symbols == ["sz.000725", "sh.600519"]
    assert "sz.300999" not in symbols
    assert next(item for item in universe if item["symbol"] == "sz.000725")["has_position"] is True
    assert next(item for item in universe if item["symbol"] == "sh.600519")["has_position"] is False
    assert list_watchboard_user_ids() == [1]
