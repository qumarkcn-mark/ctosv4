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
AI_NATIVE_RADAR_PROMPT_VERSION = os.getenv("AI_NATIVE_RADAR_PROMPT_VERSION", "ai_native_radar.v71_signal_v2")
AI_NATIVE_RADAR_FINGERPRINT_VERSION = os.getenv("AI_NATIVE_RADAR_FINGERPRINT_VERSION", "fingerprint.v2")
AI_NATIVE_FUSION_MODEL = os.getenv("AI_NATIVE_FUSION_MODEL", AI_NATIVE_RADAR_MODEL)
AI_NATIVE_FUSION_LLM_TIMEOUT = float(os.getenv("AI_NATIVE_FUSION_LLM_TIMEOUT", "45"))
AI_NATIVE_FUSION_MAX_TOKENS = int(os.getenv("AI_NATIVE_FUSION_MAX_TOKENS", str(AI_NATIVE_RADAR_MAX_TOKENS)))
AI_NATIVE_FUSION_THINKING_ENABLED = os.getenv("AI_NATIVE_FUSION_THINKING_ENABLED", "false").lower() == "true"

# AI Native 自动调度。默认关闭，避免开发环境意外触发 LLM/Kronos 调用。
AI_NATIVE_SCHEDULER_ENABLED = os.getenv("AI_NATIVE_SCHEDULER_ENABLED", "false").lower() == "true"
AI_NATIVE_SCHEDULER_INTERVAL = int(os.getenv("AI_NATIVE_SCHEDULER_INTERVAL", "30"))
AI_NATIVE_SCHEDULER_USER_ID = int(os.getenv("AI_NATIVE_SCHEDULER_USER_ID", "1"))
AI_NATIVE_REBALANCE_MAX_ITEMS = int(os.getenv("AI_NATIVE_REBALANCE_MAX_ITEMS", "8"))
AI_NATIVE_TRADING_CALENDAR_PATH = os.getenv("AI_NATIVE_TRADING_CALENDAR_PATH", str(DATA_DIR / "trading_calendar.json"))

# Structure snapshot job queue. Snapshot-first is now the default structure path;
# old get_chan_detail() is retained only as the worker's compatibility calculator.
STRUCTURE_SNAPSHOT_FIRST_ENABLED = os.getenv("STRUCTURE_SNAPSHOT_FIRST_ENABLED", "true").lower() == "true"
STRUCTURE_WORKER_ENABLED = os.getenv("STRUCTURE_WORKER_ENABLED", "false").lower() == "true"
STRUCTURE_SYNC_IF_MISSING = os.getenv("STRUCTURE_SYNC_IF_MISSING", "false").lower() == "true"
STRUCTURE_WORKER_INTERVAL = float(os.getenv("STRUCTURE_WORKER_INTERVAL", "2"))
STRUCTURE_JOB_TIMEOUT_SECONDS = int(os.getenv("STRUCTURE_JOB_TIMEOUT_SECONDS", "600"))

# AI Stop/Reduce shadow training daily loop. Coach-only: creates plans/intents/scores, never sends orders.
AI_STOP_REDUCE_DAILY_ENABLED = os.getenv("AI_STOP_REDUCE_DAILY_ENABLED", "true").lower() == "true"
AI_STOP_REDUCE_DAILY_START = os.getenv("AI_STOP_REDUCE_DAILY_START", "15:35")
AI_STOP_REDUCE_DAILY_END = os.getenv("AI_STOP_REDUCE_DAILY_END", "16:30")

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
