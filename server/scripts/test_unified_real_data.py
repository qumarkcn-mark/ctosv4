"""
统一推演测试 — 用真实 snapshot 数据
从 ctos.db 拉取轻纺城四级别快照，提取关键结构，调用 DeepSeek V4 Pro

用法:
    cd ct-os-v4
    python -m server.scripts.test_unified_real_data

需要环境变量 LLM_API_KEY 已设置
"""

import os
import json
import sqlite3
import httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── 配置 ───
API_KEY = os.environ.get("LLM_API_KEY", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("AI_NATIVE_MODEL", "deepseek-v4-pro")
DB_PATH = Path(__file__).parent.parent.parent / "data" / "ctos.db"

if not API_KEY:
    print("❌ LLM_API_KEY 未设置")
    exit(1)

if not DB_PATH.exists():
    print(f"�� 数据库不存在: {DB_PATH}")
    exit(1)


# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是缠中说禅，用户的盯盘搭档。

输入包含：多级别结构快照、历史压力支撑位、用户持仓。

看完数据，说清楚当下是什么、接下来怎么走、用户该怎么做。

仅供参考，不构成投资建议。"""


# ═══════════════════════════════════════════════════════════════
# 从数据库提取结构数据
# ═══════════════════════════════════════════════════════════════

def get_snapshot(symbol: str, level: str) -> dict | None:
    """从 DB 拉取最新 snapshot"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT snapshot_id, data_as_of, snapshot_json, raw_bi_context_json
          FROM structure_snapshots
         WHERE symbol = ? AND level = ? AND engine = 'czsc'
           AND compute_profile = 'chart_standard_v1'
           AND status = 'fresh'
         ORDER BY updated_at DESC, id DESC
         LIMIT 1
    """, (symbol, level)).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "snapshot_id": row["snapshot_id"],
        "data_as_of": row["data_as_of"],
        "snapshot": json.loads(row["snapshot_json"]),
        "raw_bi_context": json.loads(row["raw_bi_context_json"]),
    }


def extract_structure_for_llm(snapshot_data: dict, level_name: str) -> dict:
    """从完整 snapshot 提取 LLM 需要的关键结构信息"""
    snap = snapshot_data["snapshot"]

    # 基本信息
    result = {
        "level": level_name,
        "data_as_of": snapshot_data["data_as_of"],
        "current_price": snap.get("price"),
        "last_bi_direction": snap.get("last_bi_dir"),
        "state_hint": snap.get("state_hint"),
    }

    # 活跃中枢
    active_zs = snap.get("active_zhongshu") or {}
    if active_zs:
        result["active_zhongshu"] = {
            "zg": active_zs.get("zg"),
            "zd": active_zs.get("zd"),
            "gg": active_zs.get("gg"),
            "dd": active_zs.get("dd"),
            "bi_count": active_zs.get("bi_count"),
            "begin_date": active_zs.get("begin_date"),
            "end_date": active_zs.get("end_date"),
        }

    # 价格相对中枢
    pvc = snap.get("price_vs_center") or {}
    if pvc:
        result["price_vs_center"] = pvc

    # 最近的笔序列（取最后8笔，避免太长）
    bis = snap.get("bis") or []
    recent_bis = bis[-8:] if len(bis) > 8 else bis
    result["recent_bis"] = [
        {
            "direction": b.get("direction"),
            "start_price": b.get("start_price"),
            "end_price": b.get("end_price"),
            "high": b.get("high"),
            "low": b.get("low"),
            "bar_count": b.get("bar_count"),
            "is_sure": b.get("is_sure"),
        }
        for b in recent_bis
    ]
    result["total_bi_count"] = len(bis)

    # 所有中枢
    zhongshus = snap.get("bi_zhongshus") or snap.get("zhongshus") or []
    if zhongshus:
        # 取最近3个中枢
        recent_zs = zhongshus[-3:] if len(zhongshus) > 3 else zhongshus
        result["recent_zhongshus"] = [
            {
                "zg": z.get("zg"),
                "zd": z.get("zd"),
                "gg": z.get("gg"),
                "dd": z.get("dd"),
                "bi_count": z.get("bi_count"),
                "begin_date": z.get("begin_date"),
                "end_date": z.get("end_date"),
            }
            for z in recent_zs
        ]

    # 最近的分型（取最后5个）
    fxs = snap.get("fxs") or []
    recent_fxs = fxs[-5:] if len(fxs) > 5 else fxs
    if recent_fxs:
        result["recent_fxs"] = [
            {
                "dt": f.get("dt"),
                "fx": f.get("fx"),
                "high": f.get("high"),
                "low": f.get("low"),
                "mark": f.get("mark"),
            }
            for f in recent_fxs
        ]

    return result


def compute_pressure_support(snapshots: dict) -> list:
    """从多级别笔端点聚类计算压力/支撑位"""
    # 收集所有级别的笔高低点
    swing_points = []
    for level, snap_data in snapshots.items():
        if not snap_data:
            continue
        snap = snap_data["snapshot"]
        bis = snap.get("bis") or []
        price = snap.get("price", 0)

        for b in bis[-12:]:  # 每个级别取最近12笔
            high = b.get("high") or b.get("end_price")
            low = b.get("low") or b.get("start_price")
            if high and high > price * 0.9:  # 只看当前价附近的
                swing_points.append({
                    "price": high,
                    "type": "high",
                    "level": level,
                })
            if low and low > price * 0.8:
                swing_points.append({
                    "price": low,
                    "type": "low",
                    "level": level,
                })

    if not swing_points:
        return []

    # 简单聚类：按价格排序，1.5%内的合并
    current_price = snapshots.get("day", {})
    if current_price:
        current_price = current_price["snapshot"].get("price", 4.38)
    else:
        current_price = 4.38

    sorted_points = sorted(swing_points, key=lambda x: x["price"])
    clusters = []
    current_cluster = [sorted_points[0]]

    for i in range(1, len(sorted_points)):
        if sorted_points[i]["price"] / current_cluster[0]["price"] - 1 < 0.015:
            current_cluster.append(sorted_points[i])
        else:
            if len(current_cluster) >= 2:  # 至少2次触及才算有效
                clusters.append(current_cluster)
            current_cluster = [sorted_points[i]]
    if len(current_cluster) >= 2:
        clusters.append(current_cluster)

    # 格式化输出
    result = []
    for cluster in clusters:
        prices = [p["price"] for p in cluster]
        levels = list(set(p["level"] for p in cluster))
        zone_low = min(prices)
        zone_high = max(prices)
        center = (zone_low + zone_high) / 2
        distance_pct = round((center - current_price) / current_price * 100, 1)

        # 判断压力还是支撑
        high_count = sum(1 for p in cluster if p["type"] == "high")
        low_count = sum(1 for p in cluster if p["type"] == "low")
        cluster_type = "pressure" if center > current_price else "support"

        result.append({
            "zone": [round(zone_low, 4), round(zone_high, 4)],
            "type": cluster_type,
            "source_levels": levels,
            "hit_count": len(cluster),
            "distance_pct": distance_pct,
        })

    # 按距离当前价排序，取最近的6个
    result.sort(key=lambda x: abs(x["distance_pct"]))
    return result[:6]


# ═══════════════════════════════════════════════════════════════
# 主测试逻辑
# ═══════════════════════════════════════════════════════════════

def run_test():
    symbol = "sh.600790"
    levels = ["week", "day", "30", "5"]
    level_names = {"week": "周线", "day": "日线", "30": "30分钟", "5": "5分钟"}

    print(f"📂 数据库: {DB_PATH}")
    print(f"🔍 正在拉取 {symbol} 四级别 snapshot...")

    # 拉取 snapshot
    snapshots = {}
    for level in levels:
        snap = get_snapshot(symbol, level)
        if snap:
            snapshots[level] = snap
            print(f"   ✅ {level_names[level]}: snapshot_id={snap['snapshot_id'][:20]}... data_as_of={snap['data_as_of']}")
        else:
            print(f"   ❌ {level_names[level]}: 无数据")

    if not snapshots:
        print("❌ 无任何 snapshot 数���")
        exit(1)

    # 提取结构
    print("\n🔧 提取关键结构信息...")
    structure = {}
    for level in levels:
        if level in snapshots:
            structure[level_names[level]] = extract_structure_for_llm(snapshots[level], level_names[level])

    # 计算压力/支撑
    print("🔧 计算压力/支撑簇...")
    pressure_support = compute_pressure_support(snapshots)
    print(f"   找到 {len(pressure_support)} 个有效簇")

    # 获取当前价
    day_snap = snapshots.get("day", {})
    current_price = day_snap.get("snapshot", {}).get("price", 0) if day_snap else 0

    # 构建完整输入
    full_input = {
        "symbol": symbol,
        "name": "轻纺城",
        "current_price": current_price,
        "data_as_of": snapshots.get("day", {}).get("data_as_of", "unknown"),
        "structure": structure,
        "pressure_support": pressure_support,
        "my_position": {
            "holding": True,
            "shares": 20000,
            "cost": 4.22,
            "current_pnl_pct": round((current_price - 4.22) / 4.22 * 100, 2) if current_price else 0,
            "position_pct": 40,
            "note": "中等仓位，中枢震荡期间分批建仓"
        }
    }

    # 打印输入数据大小
    input_json = json.dumps(full_input, ensure_ascii=False, indent=2)
    print(f"\n📊 输入数据大小: {len(input_json)} 字符 (约 {len(input_json)//4} tokens)")

    # 也保存一份输入数据供检查
    output_dir = Path(__file__).parent.parent.parent / "data"
    input_file = output_dir / "test_unified_input_600790.json"
    input_file.write_text(input_json, encoding="utf-8")
    print(f"   输入数据已保存: {input_file}")

    # 调用 LLM
    print(f"\n🚀 调用 {MODEL}...")
    print(f"   Symbol: {symbol} 轻纺城")
    print(f"   当前价: {current_price}")
    print(f"   持仓: 20000股 @ 4.22, 40%仓位")
    print("=" * 60)

    user_message = (
        f"以下是轻纺城({symbol})的完整数据，请给出你的推演和操作建议：\n\n"
        + input_json
    )

    try:
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
        print("📊 统一推演输出（真实数据）:")
        print("─" * 60)
        print(content)
        print("─" * 60)

        # Token 用量
        usage = result.get("usage", {})
        if usage:
            print(f"\n📈 Token: prompt={usage.get('prompt_tokens', '?')}, "
                  f"completion={usage.get('completion_tokens', '?')}, "
                  f"total={usage.get('total_tokens', '?')}")

        # 保存输出
        output_file = output_dir / "test_unified_output_600790.txt"
        output_file.write_text(content, encoding="utf-8")
        print(f"\n💾 输出已保存: {output_file}")

    except httpx.HTTPStatusError as e:
        print(f"❌ API 错误: {e.response.status_code}")
        print(e.response.text)
    except Exception as e:
        print(f"❌ 请求失败: {e}")


if __name__ == "__main__":
    run_test()
