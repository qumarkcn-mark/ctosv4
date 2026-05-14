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
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
DEV_AUTH_FALLBACK = os.getenv("DEV_AUTH_FALLBACK", "false").lower() == "true"

# 数据库
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "ctos.db"))

# 微信小程序
WX_APP_ID = os.getenv("WX_APP_ID", "")
WX_APP_SECRET = os.getenv("WX_APP_SECRET", "")

# LLM
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_ALLOWED_BASE_URLS = tuple(
    url.strip().rstrip("/")
    for url in os.getenv(
        "QWEN_ALLOWED_BASE_URLS",
        "https://dashscope.aliyuncs.com/compatible-mode/v1,"
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1,"
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    ).split(",")
    if url.strip()
)
QWEN_DEFAULT_MODEL = os.getenv("QWEN_DEFAULT_MODEL", "qwen-plus")
QWEN_TRADE_PARSE_MODEL = os.getenv("QWEN_TRADE_PARSE_MODEL", "qwen-flash")
QWEN_SCREENSHOT_OCR_MODEL = os.getenv("QWEN_SCREENSHOT_OCR_MODEL", "qwen-vl-ocr-latest")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

AI_NATIVE_MODEL = os.getenv("AI_NATIVE_MODEL", "deepseek-v4-pro")
AI_NATIVE_THINKING_ENABLED = os.getenv("AI_NATIVE_THINKING_ENABLED", "true").lower() == "true"
AI_NATIVE_REASONING_EFFORT = os.getenv("AI_NATIVE_REASONING_EFFORT", "high")
AI_NATIVE_MAX_TOKENS = int(os.getenv("AI_NATIVE_MAX_TOKENS", "4096"))
AI_NATIVE_LLM_TIMEOUT = float(os.getenv("AI_NATIVE_LLM_TIMEOUT", "150"))
AI_NATIVE_TRADING_CALENDAR_PATH = os.getenv("AI_NATIVE_TRADING_CALENDAR_PATH", str(DATA_DIR / "trading_calendar.json"))

# CZSC structure snapshot job queue. Snapshot-first is now the only V5 structure path.
STRUCTURE_SNAPSHOT_FIRST_ENABLED = os.getenv("STRUCTURE_SNAPSHOT_FIRST_ENABLED", "true").lower() == "true"
STRUCTURE_WORKER_ENABLED = os.getenv("STRUCTURE_WORKER_ENABLED", "true").lower() == "true"
STRUCTURE_SYNC_IF_MISSING = os.getenv("STRUCTURE_SYNC_IF_MISSING", "false").lower() == "true"
STRUCTURE_WORKER_INTERVAL = float(os.getenv("STRUCTURE_WORKER_INTERVAL", "2"))
STRUCTURE_JOB_TIMEOUT_SECONDS = int(os.getenv("STRUCTURE_JOB_TIMEOUT_SECONDS", "600"))
AI_STRUCTURE_OUTCOME_WORKER_INTERVAL = float(os.getenv("AI_STRUCTURE_OUTCOME_WORKER_INTERVAL", "60"))

# 行情 API
PRICE_API_TIMEOUT = 5  # 秒
PRICE_MONITOR_INTERVAL = 30  # 秒，持仓价格检查间隔

# QMT 只读行情桥。默认指向 Windows 侧本机端口；未启动时后端必须可降级。
QMT_BRIDGE_URL = os.getenv("QMT_BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/")
QMT_BRIDGE_TIMEOUT = float(os.getenv("QMT_BRIDGE_TIMEOUT", "2"))

# QMT 日志行情旁路，只用于盘中 preview 价，不作为正式结构或执行依据。
QMT_LOG_BRIDGE_URL = os.getenv("QMT_LOG_BRIDGE_URL", "http://127.0.0.1:8766").rstrip("/")

# TDX 本地数据目录。当前只读 1 分钟展示/回放，不作为实时确认源。
TDX_VIPDOC = os.getenv("TDX_VIPDOC", "/Volumes/tdx_vipdoc")

# 推送
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
