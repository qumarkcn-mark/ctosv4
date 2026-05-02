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
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

# AI Native Radar 核心推演体验。默认开启；失败时必须降级到确定性结构雷达。
AI_NATIVE_RADAR_ENABLED = os.getenv("AI_NATIVE_RADAR_ENABLED", "true").lower() == "true"
AI_NATIVE_RADAR_DATA_DIR = os.getenv("AI_NATIVE_RADAR_DATA_DIR", str(BASE_DIR / "data" / "ai_native_radar"))
AI_NATIVE_RADAR_WRITE_SNAPSHOTS = os.getenv("AI_NATIVE_RADAR_WRITE_SNAPSHOTS", "false").lower() == "true"
AI_NATIVE_RADAR_GATE_ENABLED = os.getenv("AI_NATIVE_RADAR_GATE_ENABLED", "true").lower() == "true"
AI_NATIVE_RADAR_MAX_REWRITE = int(os.getenv("AI_NATIVE_RADAR_MAX_REWRITE", "1"))
AI_NATIVE_RADAR_MODEL = os.getenv("AI_NATIVE_RADAR_MODEL", "deepseek-v4-pro")
AI_NATIVE_RADAR_THINKING_ENABLED = os.getenv("AI_NATIVE_RADAR_THINKING_ENABLED", "true").lower() == "true"
AI_NATIVE_RADAR_REASONING_EFFORT = os.getenv("AI_NATIVE_RADAR_REASONING_EFFORT", "high")
AI_NATIVE_RADAR_MAX_TOKENS = int(os.getenv("AI_NATIVE_RADAR_MAX_TOKENS", "4096"))
AI_NATIVE_RADAR_LLM_TIMEOUT = float(os.getenv("AI_NATIVE_RADAR_LLM_TIMEOUT", "90"))
AI_NATIVE_RADAR_PROMPT_VERSION = os.getenv("AI_NATIVE_RADAR_PROMPT_VERSION", "ai_native_radar.v1")
AI_NATIVE_RADAR_FINGERPRINT_VERSION = os.getenv("AI_NATIVE_RADAR_FINGERPRINT_VERSION", "fingerprint.v2")

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

# Scanner 管理接口保护。为空时保持本地开发兼容；生产应设置并通过 X-Scanner-Admin-Token 传入。
SCANNER_ADMIN_TOKEN = os.getenv("SCANNER_ADMIN_TOKEN", "")
