"""微信小程序 OAuth 与认证 API"""

import os
import logging
import httpx
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
import jwt
from fastapi.concurrency import run_in_threadpool

from server import config
from server.db.database import get_connection
# from server.config import WECHAT_APP_ID, WECHAT_APP_SECRET, JWT_SECRET

logger = logging.getLogger(__name__)
router = APIRouter()

# TODO: 挪到 config.py
WECHAT_APP_ID = os.getenv("WECHAT_APP_ID", "")
WECHAT_APP_SECRET = os.getenv("WECHAT_APP_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key-for-ct-os-v4")
ALGORITHM = "HS256"

class LoginRequest(BaseModel):
    code: str

class LoginResponse(BaseModel):
    token: str
    user_id: int
    nickname: Optional[str]

@router.post("/wechat-login", response_model=LoginResponse)
async def wechat_login(req: LoginRequest):
    """
    接收小程序 wx.login 返回的 js_code，换取 OpenID 并登录注冊
    """
    if req.code == "mock_code" or not WECHAT_APP_ID:
        # 开发测试模式：直接映射到一个测试 OpenID
        openid = "mock_openid_test_12345"
        logger.info("Using mock OpenID %s", openid)
    else:
        # 真实微信 API 调用
        url = "https://api.weixin.qq.com/sns/jscode2session"
        params = {
            "appid": WECHAT_APP_ID,
            "secret": WECHAT_APP_SECRET,
            "js_code": req.code,
            "grant_type": "authorization_code"
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                if "errcode" in data and data["errcode"] != 0:
                    logger.error("Wechat login failed: %s", data)
                    raise HTTPException(400, f"微信登录失败: {data.get('errmsg')}")
                    
                openid = data.get("openid")
        except httpx.HTTPError as e:
            logger.error("Wechat API connection failed: %s", e)
            raise HTTPException(502, "无法连接微信授权服务器，请稍后重试")
            
    if not openid:
        raise HTTPException(400, "无法获取 OpenID")

    # DB 注册/登录逻辑隔离到线程池执行
    def _db_register_login(oid: str) -> tuple[int, str]:
        conn = get_connection()
        try:
            row = conn.execute("SELECT id, nickname FROM users WHERE openid = ?", (oid,)).fetchone()
            if not row:
                # 新用户注册
                cursor = conn.execute(
                    "INSERT INTO users (openid, nickname) VALUES (?, ?)", 
                    (oid, "投资人_" + oid[-4:])
                )
                user_id = cursor.lastrowid
                nickname = "投资人_" + oid[-4:]
                conn.commit()
            else:
                user_id = row["id"]
                nickname = row["nickname"]
            return user_id, nickname
        finally:
            conn.close()

    user_id, nickname = await run_in_threadpool(_db_register_login, openid)

    # 签发 JWT Token
    expires_delta = timedelta(days=30)
    expire = datetime.utcnow() + expires_delta
    to_encode = {"sub": str(user_id), "exp": expire}
    token = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)

    return {"token": token, "user_id": user_id, "nickname": nickname}

# ── 设置持久化 ──

def _redact_secret_settings(settings: dict) -> dict:
    safe_settings = dict(settings or {})
    for key in ("deepseek_api_key", "gemini_api_key", "qwen_api_key"):
        if safe_settings.get(key):
            safe_settings[f"{key}_configured"] = True
            safe_settings.pop(key, None)
    return safe_settings


def _clean_settings_update(settings: dict) -> dict:
    """移除只属于 settings 响应的派生字段，避免写回持久配置。"""
    cleaned = dict(settings or {})
    for key in ("deepseek_api_key_configured", "gemini_api_key_configured", "qwen_api_key_configured"):
        cleaned.pop(key, None)
    return cleaned


def _settings_current_user_id(authorization: Optional[str] = Header(None)) -> Optional[int]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Invalid authorization header")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return int(payload.get("sub"))
    except Exception as exc:
        raise HTTPException(401, "Invalid token") from exc


def _authorize_settings_user(user_id: int, current_user_id: Optional[int]) -> None:
    if current_user_id is None:
        if config.DEBUG and user_id == 1:
            return
        raise HTTPException(401, "Settings update requires authentication")
    if current_user_id != user_id:
        raise HTTPException(403, "Cannot access another user's settings")


@router.get("/user/{user_id}/settings")
def get_user_settings(user_id: int, current_user_id: Optional[int] = Depends(_settings_current_user_id)):
    """获取用户全局配置，例如 DeepSeek API Key"""
    import json
    _authorize_settings_user(user_id, current_user_id)
    conn = get_connection()
    try:
        row = conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        settings = json.loads(row["settings_json"] or "{}")
        return {"settings": _redact_secret_settings(settings)}
    finally:
        conn.close()

class SettingsUpdate(BaseModel):
    settings: dict

@router.post("/user/{user_id}/settings")
def update_user_settings(
    user_id: int,
    req: SettingsUpdate,
    current_user_id: Optional[int] = Depends(_settings_current_user_id),
):
    """全量/增量保存用户全局配置"""
    import json
    _authorize_settings_user(user_id, current_user_id)
    conn = get_connection()
    try:
        row = conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        
        current_settings = json.loads(row["settings_json"] or "{}")
        current_settings.update(_clean_settings_update(req.settings))
        new_json_str = json.dumps(current_settings, ensure_ascii=False)
        
        conn.execute("UPDATE users SET settings_json = ? WHERE id = ?", (new_json_str, user_id))
        conn.commit()
        return {"status": "ok", "settings": _redact_secret_settings(current_settings)}
    finally:
        conn.close()

# JWT 鉴权依赖注入 (给后续需要拦截鉴权的接口用)
# def get_current_user(token: str = Depends(oauth2_scheme)) -> int: ...
