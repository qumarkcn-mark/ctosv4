from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from server.api import auth
from server.db import database


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    monkeypatch.setattr(auth, "get_connection", database.get_connection)
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    return TestClient(app)


def test_mock_wechat_login_is_debug_only(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(auth.config, "DEBUG", False)
    monkeypatch.setattr(auth, "WECHAT_APP_ID", "wx-real-app")

    response = make_client().post("/api/auth/wechat-login", json={"code": "mock_code"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Mock login is disabled"


def test_wechat_login_requires_config_when_not_debug(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(auth.config, "DEBUG", False)
    monkeypatch.setattr(auth, "WECHAT_APP_ID", "")

    response = make_client().post("/api/auth/wechat-login", json={"code": "real_code"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Wechat login is not configured"


def test_mock_wechat_login_works_in_debug(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(auth.config, "DEBUG", True)
    monkeypatch.setattr(auth, "WECHAT_APP_ID", "")

    response = make_client().post("/api/auth/wechat-login", json={"code": "mock_code"})

    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["user_id"] == 1


def test_dev_auth_fallback_is_explicit(monkeypatch):
    monkeypatch.setattr(auth.config, "DEBUG", True)
    monkeypatch.setattr(auth.config, "DEV_AUTH_FALLBACK", False)

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user_id(None)

    assert exc.value.status_code == 401

    monkeypatch.setattr(auth.config, "DEV_AUTH_FALLBACK", True)
    assert auth.get_current_user_id(None) == 1


def test_jwt_secret_required_outside_dev_modes(monkeypatch):
    monkeypatch.setattr(auth.config, "DEBUG", False)
    monkeypatch.setattr(auth.config, "DEV_AUTH_FALLBACK", False)
    monkeypatch.setattr(auth, "JWT_SECRET", auth.DEV_JWT_SECRET)

    with pytest.raises(HTTPException) as exc:
        auth._decode_authorization_user_id("Bearer invalid")

    assert exc.value.status_code == 503
    assert exc.value.detail == "JWT secret is not configured"
