"""
Stage 1 推演测试 — 极简缠主 prompt + 轻纺城多级别结构数据
直接调用 DeepSeek V4 Pro 测试输出质量

用法:
    cd ct-os-v4
    python -m server.scripts.test_stage1_prompt

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
# STAGE 1 SYSTEM PROMPT — 极简缠主
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是缠中说禅，对当下走势做完全分��推演。

输入是��级别 CZSC 结构快照，包含各级别的笔、中枢、离开段等结构事实。

���完数据，说清��当下是什么、接下来怎么走。

仅供参考���不构成投资建议。"""


# ═══════════════════════════════════════════════════════════════
# 轻纺城 sh.600790 多级别结构数据（模拟真实 snapshot）
#
# 场景：日线正在构建类三买
# - 周线：底部区域刚脱离，第一笔向上
# - 日线：前期形成中枢(3.85-4.15)，当前向上离开中枢，正在回落确认
# - 30分：本轮上涨内部形成小中枢(4.13-4.37)，向上出中枢后第一笔回落
# - 5分：冲高4.50后连续三笔回落，当前在4.38附近
# ═���═════════════════════════════════════════════════════════════

STRUCTURE_DATA = {
    "symbol": "sh.600790",
    "name": "轻纺城",
    "current_price": 4.38,
    "data_as_of": "2025-05-16 15:00",

    "levels": {
        "week": {
            "level": "周线",
            "bi_count": 7,
            "current_bi_direction": "up",
            "current_bi_start": 3.52,
            "current_bi_high": 4.50,
            "active_zhongshu": {
                "zg": 3.85,
                "zd": 3.52,
                "bi_count_in_center": 3,
                "note": "底部中枢，长期横盘区域"
            },
            "price_vs_center": "已向上离开中枢，当前价4.38远高于上沿3.85",
            "bi_sequence_recent": [
                {"dir": "down", "start": 4.10, "end": 3.52, "note": "探底"},
                {"dir": "up", "start": 3.52, "end": 3.85, "note": "反弹进中��"},
                {"dir": "down", "start": 3.85, "end": 3.58, "note": "中枢震荡低点"},
                {"dir": "up", "start": 3.58, "end": 4.50, "note": "当前笔，向上离开中枢"}
            ]
        },

        "day": {
            "level": "���线",
            "bi_count": 11,
            "current_bi_direction": "up",
            "current_bi_start": 3.85,
            "current_bi_high": 4.50,
            "active_zhongshu": {
                "zg": 4.15,
                "zd": 3.85,
                "bi_count_in_center": 5,
                "note": "本轮上涨前的日线中枢"
            },
            "price_vs_center": "已向上离开中枢，当前价4.38高于上沿4.15，但从高点4.50回落中",
            "divergence": "当前向上笔与前一向上笔相比，MACD柱面积略小，存在潜在顶背驰可能，但笔尚未结束",
            "bi_sequence_recent": [
                {"dir": "up", "start": 3.58, "end": 4.15, "note": "进入中枢"},
                {"dir": "down", "start": 4.15, "end": 3.85, "note": "回落到中枢下沿"},
                {"dir": "up", "start": 3.85, "end": 4.10, "note": "中枢内反弹"},
                {"dir": "down", "start": 4.10, "end": 3.88, "note": "中枢内回落"},
                {"dir": "up", "start": 3.88, "end": 4.50, "note": "当前笔，向上离开中枢，创新高"}
            ],
            "key_note": "当前正在做的事：日线向上离开中枢后回落，如果回落不进中枢(不破4.15)，形成日线三买"
        },

        "30": {
            "level": "30分钟",
            "bi_count": 15,
            "current_bi_direction": "down",
            "current_bi_start": 4.50,
            "current_bi_low_so_far": 4.38,
            "active_zhongshu": {
                "zg": 4.37,
                "zd": 4.13,
                "bi_count_in_center": 5,
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
            ],
            "key_note": "30分向上出中枢后回落，当前价接近中枢上沿4.37。如果不回到中枢内部(不破4.13)，对应日线笔内部的正常回调节奏"
        },

        "5": {
            "level": "5分钟",
            "bi_count": 23,
            "current_bi_direction": "down",
            "current_bi_start": 4.42,
            "current_bi_low_so_far": 4.38,
            "active_zhongshu": {
                "zg": 4.44,
                "zd": 4.38,
                "bi_count_in_center": 3,
                "note": "回落过程中形成的5分钟小中枢"
            },
            "price_vs_center": "在中枢下沿附近",
            "volume_note": "冲高后量能持续萎缩，近3笔成交量为基准的69%",
            "bi_sequence_recent": [
                {"dir": "up", "start": 4.25, "end": 4.50, "note": "冲高"},
                {"dir": "down", "start": 4.50, "end": 4.42, "note": "第一笔回落"},
                {"dir": "up", "start": 4.42, "end": 4.47, "note": "反抽，力度弱"},
                {"dir": "down", "start": 4.47, "end": 4.38, "note": "继续回落"},
                {"dir": "up", "start": 4.38, "end": 4.44, "note": "弱反弹"},
                {"dir": "down", "start": 4.44, "end": "ongoing_4.38", "note": "当前笔，再次下探"}
            ],
            "support_cluster": {
                "zone": [4.305, 4.375],
                "hit_count": 5,
                "note": "近期五次在此区间获得承接"
            }
        }
    },

    "multi_level_summary": {
        "周线": "向上离开底部中枢，趋势向上",
        "日线": "向上离开中枢后回落中，三买构建窗口",
        "30分": "向上出中枢后回落，接近中枢上沿",
        "5分": "连续回落，缩量，接近短线支撑簇"
    }
}


# ════════���══════════════════════════════════════════════════════
# 执行测试
# ═��════���════════════════════════════════════���═══════════════════

def run_test():
    user_message = (
        "以下是轻纺城(sh.600790)的多级别结构快照，请推演：\n\n"
        + json.dumps(STRUCTURE_DATA, ensure_ascii=False, indent=2)
    )

    print(f"🚀 模型: {MODEL}")
    print(f"   Base URL: {BASE_URL}")
    print(f"   Symbol: sh.600790 轻纺城")
    print(f"   当前价: 4.38")
    print(f"   场景: 日线三买构建中")
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
    print("📊 STAGE 1 推演输出:")
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
    print("  □ 是否识别出日线三买是当前最重要的结构事件?")
    print("  □ 是否做了完全分类（列出所有可能路径）?")
    print("  □ 是否用低级别解释当前正在走哪条路径?")
    print("  □ 是否指出了分歧点（什么价格/形态决定分类）?")
    print("  □ 是否有明确的当下判断而非全是条件句?")
    print("  □ 级别之间的关系是否说清楚了?")


if __name__ == "__main__":
    run_test()
