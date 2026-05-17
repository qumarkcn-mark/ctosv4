"""
统一推演测试 — 一步到位：结构 + 压力支撑 + 持仓 → 判断
直接调用 DeepSeek V4 Pro

用法:
    cd ct-os-v4
    python -m server.scripts.test_unified_prompt

需要环境变量 LLM_API_KEY 已设置（DeepSeek API Key）
"""

import os
import json
import httpx
from dotenv import load_dotenv

load_dotenv()

# ─── 配置 ───
API_KEY = os.environ.get("LLM_API_KEY", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("AI_NATIVE_MODEL", "deepseek-v4-pro")

if not API_KEY:
    print("❌ LLM_API_KEY 未设置，请检查环境变量或 .env 文件")
    exit(1)


# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPT — 极简，不限制
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是缠中说禅，用户的盯盘搭档。

输入包含：多级别结构快照、历史压力支撑位、用户持仓。

看完数据，说清楚当下是什么、接下来怎么走、用户该怎么做。

仅供参考，不构成投资建议。"""


# ═══════════════════════════════════════════════════════════════
# 轻纺城 sh.600790 完整输入数据
# ═══════════════════════════════════════════════════════════════

FULL_INPUT = {
    "symbol": "sh.600790",
    "name": "轻纺城",
    "current_price": 4.38,
    "data_as_of": "2025-05-16 15:00",

    # ─── 多级别结构快照 ───
    "structure": {
        "week": {
            "level": "周线",
            "current_bi_direction": "up",
            "current_bi_start": 3.52,
            "current_bi_high": 4.50,
            "active_zhongshu": {
                "zg": 3.85,
                "zd": 3.52,
                "note": "底部中枢，长期横盘区域"
            },
            "price_vs_center": "已向上离开中枢，当前价远高于上沿3.85",
            "bi_sequence_recent": [
                {"dir": "down", "start": 4.10, "end": 3.52, "note": "探底"},
                {"dir": "up", "start": 3.52, "end": 3.85, "note": "反弹进中枢"},
                {"dir": "down", "start": 3.85, "end": 3.58, "note": "中枢震荡低点"},
                {"dir": "up", "start": 3.58, "end": 4.50, "note": "当前笔，向上离开中枢"}
            ]
        },

        "day": {
            "level": "日线",
            "current_bi_direction": "up",
            "current_bi_start": 3.85,
            "current_bi_high": 4.50,
            "active_zhongshu": {
                "zg": 4.15,
                "zd": 3.85,
                "note": "本轮上涨前的日线中枢"
            },
            "price_vs_center": "已向上离开中枢，当前价4.38高于上沿4.15，但从高点4.50回落中",
            "divergence": "当前向上笔与前一向上笔相比，MACD柱面积略小，存在潜在顶背驰可能，但笔尚未结束",
            "bi_sequence_recent": [
                {"dir": "up", "start": 3.58, "end": 4.15, "note": "进入中枢"},
                {"dir": "down", "start": 4.15, "end": 3.85, "note": "回落到中枢下沿"},
                {"dir": "up", "start": 3.85, "end": 4.10, "note": "中枢内反弹"},
                {"dir": "down", "start": 4.10, "end": 3.88, "note": "中枢内回落"},
                {"dir": "up", "start": 3.88, "end": 4.50, "note": "当前笔，向上离开中枢"}
            ]
        },

        "30min": {
            "level": "30分钟",
            "current_bi_direction": "down",
            "current_bi_start": 4.50,
            "current_bi_low_so_far": 4.38,
            "active_zhongshu": {
                "zg": 4.37,
                "zd": 4.13,
                "note": "本轮上涨内部30分钟中枢"
            },
            "price_vs_center": "从中枢上方回落，当前价4.38刚到中枢上沿4.37附近",
            "divergence": "向上离开段与前一向上段相比，力度偏弱，存在小级别背驰",
            "bi_sequence_recent": [
                {"dir": "up", "start": 4.13, "end": 4.37, "note": "进入中枢"},
                {"dir": "down", "start": 4.37, "end": 4.20, "note": "中枢内回落"},
                {"dir": "up", "start": 4.20, "end": 4.42, "note": "中枢内反弹"},
                {"dir": "down", "start": 4.42, "end": 4.25, "note": "中枢内回落"},
                {"dir": "up", "start": 4.25, "end": 4.50, "note": "向上离开中枢"},
                {"dir": "down", "start": 4.50, "end": "ongoing_4.38", "note": "当前笔，回落中"}
            ]
        },

        "5min": {
            "level": "5分钟",
            "current_bi_direction": "down",
            "current_bi_start": 4.44,
            "current_bi_low_so_far": 4.38,
            "active_zhongshu": {
                "zg": 4.44,
                "zd": 4.38,
                "note": "回落过程中形成的5分钟小中枢"
            },
            "price_vs_center": "在中枢下沿附近",
            "volume": "冲高后量能持续萎缩，近3笔成交量为基准的69%",
            "bi_sequence_recent": [
                {"dir": "up", "start": 4.25, "end": 4.50, "note": "冲高"},
                {"dir": "down", "start": 4.50, "end": 4.42, "note": "第一笔回落"},
                {"dir": "up", "start": 4.42, "end": 4.47, "note": "反抽，力度弱"},
                {"dir": "down", "start": 4.47, "end": 4.38, "note": "继续回落"},
                {"dir": "up", "start": 4.38, "end": 4.44, "note": "弱反弹"},
                {"dir": "down", "start": 4.44, "end": "ongoing_4.38", "note": "当前笔，再次下探"}
            ]
        }
    },

    # ─── 历史压力/支撑簇（evaluate_next_gates 计算结果）───
    "pressure_support": [
        {
            "zone": [4.305, 4.375],
            "type": "support",
            "source_levels": ["5min"],
            "hit_count": 5,
            "note": "5分钟级别近期五次承接位，短线资金反复在此接"
        },
        {
            "zone": [4.13, 4.20],
            "type": "support",
            "source_levels": ["30min", "day"],
            "hit_count": 3,
            "note": "30分钟中枢下沿+日线中枢上沿重合区域，多级别共振支撑"
        },
        {
            "zone": [4.427, 4.499],
            "type": "pressure",
            "source_levels": ["day", "30min"],
            "hit_count": 4,
            "note": "前期冲高回落密集区+日线前高附近，获利盘和套牢盘共振压力"
        },
        {
            "zone": [4.55, 4.62],
            "type": "pressure",
            "source_levels": ["week"],
            "hit_count": 2,
            "note": "周线级别前期平台，更高位压力参考"
        }
    ],

    # ─── 用户持仓 ───
    "my_position": {
        "holding": True,
        "shares": 20000,
        "cost": 4.22,
        "current_pnl_pct": 3.79,
        "position_pct": 40,
        "note": "中等仓位，中枢震荡期间分批建仓"
    }
}


# ═══════════════════════════════════════════════════════════════
# 执行测试
# ═══════════════════════════════════════════════════════════════

def run_test():
    user_message = (
        "以下是轻纺城(sh.600790)的完整数据，请给出你的推演和操作建议：\n\n"
        + json.dumps(FULL_INPUT, ensure_ascii=False, indent=2)
    )

    print(f"🚀 模型: {MODEL}")
    print(f"   Base URL: {BASE_URL}")
    print(f"   Symbol: sh.600790 轻纺城")
    print(f"   当前价: 4.38 | 成本: 4.22 | 持仓: 20000股 (40%仓位)")
    print(f"   浮盈: +3.79%")
    print("=" * 60)
    print()
    print("📋 System Prompt:")
    print(SYSTEM_PROMPT)
    print()
    print("=" * 60)

    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 4096,
            "temperature": 0.7,
        },
        timeout=150.0,
    )
    resp.raise_for_status()
    result = resp.json()

    content = result["choices"][0]["message"]["content"]

    print()
    print("📊 统一推演输出:")
    print("─" * 60)
    print(content)
    print("─" * 60)

    # Token 用量
    usage = result.get("usage", {})
    if usage:
        print(f"\n📈 Token: prompt={usage.get('prompt_tokens', '?')}, "
              f"completion={usage.get('completion_tokens', '?')}, "
              f"total={usage.get('total_tokens', '?')}")

    # 输出质量检查
    print()
    print("🔍 输出质量检查点:")
    print("  □ 是否识别出日线三买是核心结构事件?")
    print("  □ 是否利用了压力/支撑数据做具体判断?")
    print("  □ 是否结合持仓(20000股@4.22, 40%仓位)给了具体操作建议?")
    print("  □ 操作建议是否包含：加仓/减仓/持有的明确判断?")
    print("  □ 是否有止损位和加仓位?")
    print("  □ 是否区分了不同情景下的应对?")


if __name__ == "__main__":
    run_test()
