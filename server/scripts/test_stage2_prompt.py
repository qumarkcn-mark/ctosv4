"""
Stage 2 推演测试 — 盯盘搭档 prompt + 轻纺城样例数据
直接调用 DeepSeek V4 Pro 测试输出质量

用法:
    cd ct-os-v4
    python -m server.scripts.test_stage2_prompt

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
# STAGE 2 SYSTEM PROMPT — 盯盘搭档
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是用户的盯盘搭档，十五年实盘经验，缠论多级别结构判断。
用户懂结构，盯不过来多只票，需要你直接告诉他该怎么做。

输入包含：第一阶段结构推演、当前价格和位置关系、量能、用户持仓。

你的任务：
看完数据，告诉用户——这只票接下来要干什么，怎么干。

仅供参考，不构成投资建议。"""


# ═══════════════════════════════════════════════════════════════
# STAGE 2 INPUT DATA — 轻纺城 sh.600790 测试样例
# ═══════════════════════════════════════════════════════════════

TEST_INPUT = {
    # 第一阶段推演结果摘要
    "stage1_result": {
        "summary": (
            "30分钟级别向上离开中枢后第一笔回落。"
            "中枢区间大约4.13-4.37，当前价格仍在中枢上沿附近。"
            "5分钟级别冲高后连续回落，短线兑现明显。"
            "日线级别仍处于底部第一段上涨结构中，周线刚脱离底部区域。"
            "多级别共振方向向上，但短期有回调压力。"
        )
    },

    # 当前价格和市场状态
    "price_context": {
        "symbol": "sh.600790",
        "name": "轻纺城",
        "current_price": 4.38,
        "volume_state": "冲高后量能回落至基准的69%，缩量但未见恐慌抛售",
        "recent_behavior": "冲高后回落，短线兑现明显，尾盘未反抽"
    },

    # 关键位置（从多级别结构中筛选出的少量核心位置）
    "key_locations": [
        {
            "zone": [4.305, 4.375],
            "distance_pct": -0.9,
            "meaning": "5分钟级别近期五次承接位，短线资金反复在这里接",
            "urgency": "眼前就在这个位置附近"
        },
        {
            "zone": [4.133, 4.20],
            "distance_pct": -4.9,
            "meaning": "30分钟中枢下沿，如果跌回这里说明出中枢失败",
            "urgency": "眼前承接失败才需要看这里"
        },
        {
            "zone": [4.427, 4.499],
            "distance_pct": 1.9,
            "meaning": "日线+30分钟共振压力区，前期套牢和获利盘集中",
            "urgency": "确认回踩成功后的下一个关卡"
        }
    ],

    # 用户当前持仓
    "my_position": {
        "holding": True,
        "shares": 2000,
        "cost": 4.25,
        "position_pct": 15,
        "note": "底仓，上周在中枢震荡时接的"
    }
}


# ═══════════════════════════════════════════════════════════════
# 执行测试
# ═══════════════════════════════════════════════════════════════

def run_test():
    user_message = (
        "以下是轻纺城(sh.600790)的当前状态，请给出你的判断：\n\n"
        + json.dumps(TEST_INPUT, ensure_ascii=False, indent=2)
    )

    print(f"🚀 模型: {MODEL}")
    print(f"   Base URL: {BASE_URL}")
    print(f"   Symbol: {TEST_INPUT['price_context']['symbol']} {TEST_INPUT['price_context']['name']}")
    print(f"   当前价: {TEST_INPUT['price_context']['current_price']}")
    print(f"   持仓: {TEST_INPUT['my_position']['shares']}股 @ {TEST_INPUT['my_position']['cost']}")
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
            "max_tokens": 2048,
            "temperature": 0.7,
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    result = resp.json()

    content = result["choices"][0]["message"]["content"]

    print()
    print("📊 STAGE 2 输出:")
    print("─" * 60)
    print(content)
    print("─" * 60)

    # Token 用量
    usage = result.get("usage", {})
    if usage:
        print(f"\n📈 Token: prompt={usage.get('prompt_tokens', '?')}, "
              f"completion={usage.get('completion_tokens', '?')}, "
              f"total={usage.get('total_tokens', '?')}")

    # 输出质量自检提示
    print()
    print("🔍 输出质量检查点:")
    print("  □ 是否有明确的当下判断（而非全是条件句）?")
    print("  □ 是否直接说了该买/卖/等/加?")
    print("  □ 是否结合了持仓状态给建议?")
    print("  □ 是否区分了眼前和结构性位置?")
    print("  □ 是否有止损/失效条件?")
    print("  □ 语气像不像同行聊天?")


if __name__ == "__main__":
    run_test()
