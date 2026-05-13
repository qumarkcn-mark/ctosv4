import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import auth


class ConnWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def commit(self):
        return self.conn.commit()

    def close(self):
        pass


def make_settings_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, settings_json TEXT)")
    conn.execute(
        "INSERT INTO users (id, settings_json) VALUES (?, ?)",
        (
            1,
            json.dumps(
                {
                    "deepseek_api_key": "sk-secret",
                    "gemini_api_key": "AIza-secret",
                    "qwen_api_key": "sk-qwen-secret",
                    "expert_mode": True,
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    return conn


def test_get_user_settings_redacts_api_keys(monkeypatch):
    conn = make_settings_conn()
    monkeypatch.setattr(auth, "get_connection", lambda: ConnWrapper(conn))

    response = auth.get_user_settings(1, current_user_id=1)
    settings = response["settings"]

    assert "deepseek_api_key" not in settings
    assert "gemini_api_key" not in settings
    assert "qwen_api_key" not in settings
    assert settings["deepseek_api_key_configured"] is True
    assert settings["gemini_api_key_configured"] is True
    assert settings["qwen_api_key_configured"] is True
    assert settings["expert_mode"] is True


def test_get_my_settings_uses_current_user(monkeypatch):
    conn = make_settings_conn()
    monkeypatch.setattr(auth, "get_connection", lambda: ConnWrapper(conn))

    response = auth.get_my_settings(current_user_id=1)

    assert response["settings"]["expert_mode"] is True


def test_update_user_settings_preserves_secret_when_omitted_and_redacts_response(monkeypatch):
    conn = make_settings_conn()
    monkeypatch.setattr(auth, "get_connection", lambda: ConnWrapper(conn))

    response = auth.update_user_settings(1, auth.SettingsUpdate(settings={"expert_mode": False}), current_user_id=1)

    settings = response["settings"]
    assert settings["expert_mode"] is False
    assert "deepseek_api_key" not in settings
    assert "qwen_api_key" not in settings
    assert settings["deepseek_api_key_configured"] is True
    assert settings["qwen_api_key_configured"] is True

    row = conn.execute("SELECT settings_json FROM users WHERE id = 1").fetchone()
    stored = json.loads(row["settings_json"])
    assert stored["deepseek_api_key"] == "sk-secret"
    assert stored["gemini_api_key"] == "AIza-secret"
    assert stored["qwen_api_key"] == "sk-qwen-secret"


def test_update_my_settings_uses_current_user(monkeypatch):
    conn = make_settings_conn()
    monkeypatch.setattr(auth, "get_connection", lambda: ConnWrapper(conn))

    response = auth.update_my_settings(auth.SettingsUpdate(settings={"expert_mode": False}), current_user_id=1)

    assert response["settings"]["expert_mode"] is False


def test_update_user_settings_does_not_persist_redaction_markers(monkeypatch):
    conn = make_settings_conn()
    monkeypatch.setattr(auth, "get_connection", lambda: ConnWrapper(conn))

    auth.update_user_settings(
        1,
        auth.SettingsUpdate(
            settings={
                "deepseek_api_key_configured": False,
                "gemini_api_key_configured": False,
                "qwen_api_key_configured": False,
                "expert_mode": False,
            }
        ),
        current_user_id=1,
    )

    row = conn.execute("SELECT settings_json FROM users WHERE id = 1").fetchone()
    stored = json.loads(row["settings_json"])
    assert "deepseek_api_key_configured" not in stored
    assert "gemini_api_key_configured" not in stored
    assert "qwen_api_key_configured" not in stored
    assert stored["deepseek_api_key"] == "sk-secret"
    assert stored["qwen_api_key"] == "sk-qwen-secret"


def test_update_user_settings_rejects_cross_user_write(monkeypatch):
    conn = make_settings_conn()
    monkeypatch.setattr(auth, "get_connection", lambda: ConnWrapper(conn))

    try:
        auth.update_user_settings(1, auth.SettingsUpdate(settings={"qwen_api_key": "stolen"}), current_user_id=2)
    except auth.HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("cross-user settings write should be rejected")

    row = conn.execute("SELECT settings_json FROM users WHERE id = 1").fetchone()
    stored = json.loads(row["settings_json"])
    assert stored["qwen_api_key"] == "sk-qwen-secret"
