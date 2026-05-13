import pytest

from server import config
from server.api import auth


@pytest.fixture(autouse=True)
def enable_dev_auth_for_tests(monkeypatch):
    """测试默认使用开发认证，生产边界由专门测试显式关闭。"""
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setattr(config, "DEV_AUTH_FALLBACK", True)
    monkeypatch.setattr(auth, "JWT_SECRET", auth.DEV_JWT_SECRET)
