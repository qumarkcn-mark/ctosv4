"""CT-OS V4.0 配置管理"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

# 路径
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 服务器
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# 数据库
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "ctos.db"))

# 微信小程序
WX_APP_ID = os.getenv("WX_APP_ID", "")
WX_APP_SECRET = os.getenv("WX_APP_SECRET", "")

# LLM
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# 行情 API
PRICE_API_TIMEOUT = 5  # 秒
PRICE_MONITOR_INTERVAL = 30  # 秒，持仓价格检查间隔

# 推送
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")

# Scanner 管理接口保护。为空时保持本地开发兼容；生产应设置并通过 X-Scanner-Admin-Token 传入。
SCANNER_ADMIN_TOKEN = os.getenv("SCANNER_ADMIN_TOKEN", "")
