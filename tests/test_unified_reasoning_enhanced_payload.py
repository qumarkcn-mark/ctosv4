from server.db import database
from server.engines.ai_native import unified_reasoning_service as service


def make_snapshot(level: str, *, price: float, zg: float, zd: float, bi_count: int) -> dict:
    klines = []
    current = price * 0.8
    for index in range(80):
        current += price * 0.002
        klines.append(
            {
                "time": f"2026-05-{(index % 20) + 1:02d}",
                "close": round(current, 4),
                "high": round(current + price * 0.005, 4),
                "low": round(current - price * 0.005, 4),
                "volume": 1000 + index * 20,
            }
        )
    return {
        "snapshot_id": f"snap-{level}",
        "data_as_of": "2026-05-20",
        "snapshot": {
            "level": level,
            "price": price,
            "last_bi_dir": "up",
            "state_hint": "above_zg" if price > zg else "inside_center",
            "klines": klines,
            "active_zhongshu": {
                "zg": zg,
                "zd": zd,
                "gg": zg * 1.05,
                "dd": zd * 0.95,
                "bi_count": bi_count,
                "begin_date": "2026-05-01",
                "end_date": "2026-05-10",
            },
            "price_vs_center": {"position": "above_zg" if price > zg else "inside_center"},
            "bis": [
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "start_price": zd,
                    "end_price": zg,
                    "high": zg,
                    "low": zd,
                    "bar_count": 8,
                },
                {
                    "direction": "down",
                    "is_up": False,
                    "is_sure": True,
                    "start_price": zg,
                    "end_price": zd,
                    "high": zg,
                    "low": zd,
                    "bar_count": 7,
                },
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": False,
                    "source": "czsc_ubi",
                    "status": "ongoing",
                    "start_price": zd,
                    "end_price": price,
                    "high": price,
                    "low": zd,
                    "bar_count": 5,
                },
            ],
            "bi_zhongshus": [],
        },
    }


def test_unified_position_context_accepts_compact_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (?, ?, ?)",
            (1, "u1", "U1"),
        )
        conn.execute(
            "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, current_price) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "sz002138", "顺络电子", 8000, 35.46, 39.88),
        )
        conn.commit()
    finally:
        conn.close()

    position = service._position_context(user_id=1, symbol="sz.002138", current_price=39.88)

    assert position["holding"] is True
    assert position["shares"] == 8000
    assert position["cost"] == 35.46


def test_build_unified_reasoning_input_includes_enhanced_payload(monkeypatch):
    snapshots = {
        "day": make_snapshot("day", price=10.2, zg=10.0, zd=9.2, bi_count=9),
        "5": make_snapshot("5", price=10.2, zg=10.1, zd=9.8, bi_count=3),
        "week": make_snapshot("week", price=10.2, zg=4.0, zd=3.5, bi_count=7),
    }

    def fake_latest_snapshot(*, symbol, level, compute_profile):
        return snapshots.get(level)

    monkeypatch.setattr(service, "get_latest_snapshot", fake_latest_snapshot)
    monkeypatch.setattr(service, "_position_context", lambda **kwargs: {"holding": False})

    payload = service.build_unified_reasoning_input(user_id=1, symbol="sh600519", levels=["day", "5", "week"])
    data = payload["input"]

    assert data["first_stage_reasoning"] == data["structure"]
    assert data["nearby_pressure_support"] == data["pressure_support"]
    assert data["position_context"] == data["my_position"]
    assert data["structure_geometry"]["日线"]["center"]["maturity"] == "upgrade_watch"
    assert data["structure_geometry"]["5分钟"]["center"]["maturity"] == "forming"
    assert data["structure_geometry"]["周线"]["center"]["relevance"] == "distant_context"
    assert data["structure_geometry"]["日线"]["unfinished_bi"]["status"] == "ongoing"
    assert data["structure"]["日线"]["total_bi_count"] == 2
    assert data["structure"]["日线"]["current_unfinished_bi"]["is_sure"] is False
    assert "macd_state" in data["momentum_dynamics"]["日线"]


def test_pressure_support_semantics_ignore_distant_centers():
    clusters = [
        {"zone": [3.98, 4.02], "type": "support", "distance_pct": -60},
        {"zone": [9.95, 10.02], "type": "support", "distance_pct": -1},
    ]
    geometry = {
        "周线": {"center": {"zg": 4.0, "zd": 3.5, "relevance": "distant_context"}},
        "日线": {"center": {"zg": 10.0, "zd": 9.2, "relevance": "active_boundary"}},
    }

    result = service._add_pressure_support_semantics(clusters, geometry)

    assert "semantic" not in result[0]
    assert result[1]["semantic"] == "日线:接近中枢上沿ZG，属于离开后回拉观察边界"
