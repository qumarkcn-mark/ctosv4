from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt

from server.api import behavior
from server.api import auth
from server.api.auth import ALGORITHM
from server.db import database


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1'), (2, 'u2', 'U2')")
        conn.commit()
    finally:
        conn.close()


def make_client():
    app = FastAPI()
    app.include_router(behavior.router, prefix="/api/behavior")
    return TestClient(app)


def auth_headers(user_id: int) -> dict:
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(user_id)}, auth.JWT_SECRET, algorithm=ALGORITHM)}"}


def test_behavior_report_ignores_spoofed_user_id(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    client = make_client()

    response = client.get("/api/behavior/report?user_id=2")

    assert response.status_code == 200
    conn = database.get_connection()
    try:
        rows = conn.execute("SELECT user_id FROM behavior_stats ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [row["user_id"] for row in rows] == [1]


def test_behavior_report_requires_auth_when_not_debug(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr("server.api.auth.config.DEBUG", False)
    monkeypatch.setattr("server.api.auth.config.DEV_AUTH_FALLBACK", False)
    monkeypatch.setattr(auth, "JWT_SECRET", "test-production-secret-minimum-32-bytes")
    client = make_client()

    unauthenticated = client.get("/api/behavior/report")
    authenticated = client.get("/api/behavior/report", headers=auth_headers(2))

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    conn = database.get_connection()
    try:
        rows = conn.execute("SELECT user_id FROM behavior_stats ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [row["user_id"] for row in rows] == [2]


def test_behavior_history_is_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO behavior_stats (user_id, period, total_trades, win_rate, profit_loss_ratio, avg_hold_days)
            VALUES (1, 'ALL_TIME', 1, 60, 1.5, 5), (2, 'ALL_TIME', 2, 30, 0.5, 1)
            """
        )
        conn.commit()
    finally:
        conn.close()

    client = make_client()
    own = client.get("/api/behavior/history?user_id=2")
    other = client.get("/api/behavior/history", headers=auth_headers(2))

    assert own.status_code == 200
    assert [row["user_id"] for row in own.json()["data"]] == [1]
    assert other.status_code == 200
    assert [row["user_id"] for row in other.json()["data"]] == [2]
