"""微信小程序推送服务 — 订阅消息"""

import os
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

WECHAT_APP_ID = os.getenv("WECHAT_APP_ID", "")
WECHAT_APP_SECRET = os.getenv("WECHAT_APP_SECRET", "")
# 订阅消息模板 ID
TMPL_STOP_LOSS = os.getenv("TMPL_STOP_LOSS", "mock_tmpl_id_stop_loss")

# Token 简单缓存机制 (内存中)，生产环境可以放入 Redis 或 DB
_ACCESS_TOKEN: Optional[str] = None
_ACCESS_TOKEN_EXPIRES: float = 0.0

async def _get_access_token() -> str:
    from time import time
    global _ACCESS_TOKEN, _ACCESS_TOKEN_EXPIRES
    
    if _ACCESS_TOKEN and time() < _ACCESS_TOKEN_EXPIRES:
        return _ACCESS_TOKEN
        
    if not WECHAT_APP_ID:
        return "mock_access_token"

    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": WECHAT_APP_ID,
        "secret": WECHAT_APP_SECRET
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            if "access_token" in data:
                _ACCESS_TOKEN = data["access_token"]
                # 提前 5 分钟过期
                _ACCESS_TOKEN_EXPIRES = time() + data["expires_in"] - 300
                return _ACCESS_TOKEN
            else:
                logger.error("Failed to get wechat token: %s", data)
                return ""
    except Exception as e:
        logger.error("Wechat token request failed: %s", e)
        return ""

async def send_stop_loss_alert(user_id: int, message: str):
    """
    发送止损预警推送给小程序用户。
    实际使用中，需要查出 user_id 对应的 openid。
    """
    from server.db.database import get_connection
    conn = get_connection()
    try:
        row = conn.execute("SELECT openid FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            logger.warning("Push failed: User %s not found", user_id)
            return False
            
        openid = row["openid"]
    finally:
        conn.close()

    if openid.startswith("mock_"):
        logger.info("[Mock Push] to %s: %s", openid, message)
        return True

    token = await _get_access_token()
    if not token:
        return False

    url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}"
    
    # 按照微信模板消息格式构造
    # 此处 data 的 key 取决于申请的模板（例如 thing1: {value:xxx}, amount2: {value:xxx}）
    payload = {
        "touser": openid,
        "template_id": TMPL_STOP_LOSS,
        "page": "pages/index/index",
        "data": {
            # 按微信的要求填写, 这里只写一个占位示意
            "thing1": {"value": "止损预警"},
            "thing2": {"value": message[:20]}
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if data.get("errcode") == 0:
                logger.info("Successfully sent push to %s", openid)
                return True
            else:
                logger.error("Push failed: %s", data)
                return False
    except Exception as e:
        logger.error("Push network error: %s", e)
        return False
