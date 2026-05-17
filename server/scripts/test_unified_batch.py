"""
统一推演批量测试 — 自动从数据库选取多只股票，逐个测试
自动选股逻辑：从 structure_snapshots 中找到有完整四级别数据的股票，
优先选取不同结构状态的（above_zg / below_zd / inside 等）

用法:
    cd ct-os-v4
    python -m server.scripts.test_unified_batch

需要环境变量 LLM_API_KEY 已设置
"""

import os
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

import httpx
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
    print(f"❌ 数据库不存在: {DB_PATH}")
    exit(1)


# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是缠中说禅，用户的盯盘搭档。

输入包含：多级别结构快照、历史压力支撑位、用户持仓。

看完数据，说清楚当下是什么、接下来怎么走、用户该怎么做。

仅供参考，不构成投资建议。"""


# ═══════════════════════════════════════════════════════════════
# 选股逻辑：找到有完整四级别的股票，挑不同状态的
# ═══════════════════════════════════════════════════════════════

def select_test_symbols(max_count: int = 5) -> list[dict]:
    """从数据库中选取测试标的，尽量覆盖不同结构状态"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 找到有完整四级别 snapshot 的股票
    rows = conn.execute("""
        SELECT symbol, COUNT(DISTINCT level) as level_count
        FROM structure_snapshots
        WHERE engine = 'czsc'
          AND compute_profile = 'chart_standard_v1'
          AND status = 'fresh'
        GROUP BY symbol
        HAVING level_count >= 4
        ORDER BY symbol
    """).fetchall()

    if not rows:
        conn.close()
        return []

    candidates = []
    for row in rows:
        symbol = row["symbol"]
        # 取日线 snapshot 的 state_hint 作为分类依据
        day_row = conn.execute("""
            SELECT snapshot_json
            FROM structure_snapshots
            WHERE symbol = ? AND level = 'day' AND engine = 'czsc'
              AND compute_profile = 'chart_standard_v1' AND status = 'fresh'
            ORDER BY updated_at DESC LIMIT 1
        """, (symbol,)).fetchone()

        if day_row:
            snap = json.loads(day_row["snapshot_json"])
            state_hint = snap.get("state_hint", "unknown")
            price = snap.get("price", 0)
            candidates.append({
                "symbol": symbol,
                "state_hint": state_hint,
                "price": price,
            })

    conn.close()

    # 按 state_hint 分组，每种状态选一只，尽量覆盖多种场景
    by_state = {}
    for c in candidates:
        state = c["state_hint"]
        if state not in by_state:
            by_state[state] = []
        by_state[state].append(c)

    selected = []
    # 优先选不同状态
    for state, symbols in by_state.items():
        if len(selected) >= max_count:
            break
        selected.append(symbols[0])

    # 如果不够，从剩余中补
    if len(selected) < max_count:
        for c in candidates:
            if c not in selected and len(selected) < max_count:
                selected.append(c)

    return selected


# ═══════════════════════════════════════════════════════════════
# 数据提取（复用 test_unified_real_data 的逻辑）
# ═══════════════════════════════════════════════════════════════

def get_snapshot(symbol: str, level: str) -> Optional[dict]:
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
    snap = snapshot_data["snapshot"]
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

    # 最近笔序列（取最后6笔）
    bis = snap.get("bis") or []
    recent_bis = bis[-6:] if len(bis) > 6 else bis
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

    # 中枢列表（最近2个）
    zhongshus = snap.get("bi_zhongshus") or snap.get("zhongshus") or []
    if zhongshus:
        recent_zs = zhongshus[-2:] if len(zhongshus) > 2 else zhongshus
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

    # 最近分型（取最后4个）
    fxs = snap.get("fxs") or []
    recent_fxs = fxs[-4:] if len(fxs) > 4 else fxs
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
    """从多级别笔端点聚类计算压力/支撑位，带 status 字段"""
    swing_points = []
    current_price = None

    for level, snap_data in snapshots.items():
        if not snap_data:
            continue
        snap = snap_data["snapshot"]
        bis = snap.get("bis") or []
        price = snap.get("price", 0)
        if current_price is None:
            current_price = price

        for b in bis[-10:]:
            high = b.get("high") or b.get("end_price")
            low = b.get("low") or b.get("start_price")
            if high and abs(high - price) / price < 0.15:
                swing_points.append({"price": high, "type": "high", "level": level})
            if low and abs(low - price) / price < 0.15:
                swing_points.append({"price": low, "type": "low", "level": level})

    if not swing_points or not current_price:
        return []

    # 聚类：1.5%内合并
    sorted_points = sorted(swing_points, key=lambda x: x["price"])
    clusters = []
    current_cluster = [sorted_points[0]]

    for i in range(1, len(sorted_points)):
        if sorted_points[i]["price"] / current_cluster[0]["price"] - 1 < 0.015:
            current_cluster.append(sorted_points[i])
        else:
            if len(current_cluster) >= 2:
                clusters.append(current_cluster)
            current_cluster = [sorted_points[i]]
    if len(current_cluster) >= 2:
        clusters.append(current_cluster)

    # 格式化 + status 判断
    result = []
    for cluster in clusters:
        prices = [p["price"] for p in cluster]
        levels = list(set(p["level"] for p in cluster))
        zone_low = min(prices)
        zone_high = max(prices)
        center = (zone_low + zone_high) / 2
        distance_pct = round((center - current_price) / current_price * 100, 1)

        # 判断 type 和 status
        if center > current_price:
            cluster_type = "pressure"
            # 如果当前价曾经在这个区间上方（看最近笔的高点是否超过），则是 just_broken_below
            if current_price < zone_low and any(
                p["price"] > zone_high for p in swing_points if p["type"] == "high"
            ):
                status = "just_broken_below"
            elif abs(distance_pct) < 1.0:
                status = "testing"
            else:
                status = "holding"
        else:
            cluster_type = "support"
            if current_price > zone_high:
                status = "confirmed"
            elif abs(distance_pct) < 1.0:
                status = "testing"
            else:
                status = "holding"

        result.append({
            "zone": [round(zone_low, 4), round(zone_high, 4)],
            "type": cluster_type,
            "status": status,
            "source_levels": levels,
            "hit_count": len(cluster),
            "distance_pct": distance_pct,
        })

    result.sort(key=lambda x: abs(x["distance_pct"]))
    return result[:6]


def get_position(symbol: str) -> dict:
    """从数据库获取持仓，没有则模拟一个"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT quantity, avg_cost, current_price
        FROM positions
        WHERE symbol = ?
        ORDER BY updated_at DESC LIMIT 1
    """, (symbol,)).fetchone()
    conn.close()

    if row and row["quantity"] and row["quantity"] > 0:
        return {
            "holding": True,
            "shares": row["quantity"],
            "cost": row["avg_cost"],
            "source": "database",
        }
    else:
        # 无持仓，模拟空仓观望场景
        return {
            "holding": False,
            "shares": 0,
            "cost": 0,
            "source": "simulated_empty",
            "note": "当前无持仓，观望中"
        }


# ═══════════════════════════════════════════════════════════════
# 单只股票测试
# ═══════════════════════════════════════════════════════════════

def test_single_symbol(symbol: str, symbol_info: dict) -> dict:
    """对单只股票执行完整测试，返回结果"""
    levels = ["week", "day", "30", "5"]
    level_names = {"week": "周线", "day": "日线", "30": "30分钟", "5": "5分钟"}

    # 拉取 snapshot
    snapshots = {}
    for level in levels:
        snap = get_snapshot(symbol, level)
        if snap:
            snapshots[level] = snap

    if len(snapshots) < 4:
        return {"symbol": symbol, "error": f"只有 {len(snapshots)} 个级别的数据"}

    # 提取结构
    structure = {}
    for level in levels:
        if level in snapshots:
            structure[level_names[level]] = extract_structure_for_llm(snapshots[level], level_names[level])

    # 压力支撑
    pressure_support = compute_pressure_support(snapshots)

    # 持仓
    position = get_position(symbol)

    # 当前价
    current_price = snapshots["day"]["snapshot"].get("price", 0)

    # 如果有持仓，计算浮盈
    if position["holding"] and position["cost"] > 0 and current_price > 0:
        position["current_pnl_pct"] = round((current_price - position["cost"]) / position["cost"] * 100, 2)

    # 构建输入
    full_input = {
        "symbol": symbol,
        "current_price": current_price,
        "data_as_of": snapshots["day"]["data_as_of"],
        "structure": structure,
        "pressure_support": pressure_support,
        "my_position": position,
    }

    input_json = json.dumps(full_input, ensure_ascii=False, indent=2)

    # 调用 LLM
    user_message = f"以下是 {symbol} 的完整数据，请给出你的推演和操作建议：\n\n{input_json}"

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
    usage = result.get("usage", {})

    return {
        "symbol": symbol,
        "state_hint": symbol_info.get("state_hint", "unknown"),
        "current_price": current_price,
        "position": position,
        "pressure_support_count": len(pressure_support),
        "input_chars": len(input_json),
        "output": content,
        "tokens": usage,
    }


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def run_batch():
    print("=" * 60)
    print("🔍 统一推演批量测试")
    print(f"   模型: {MODEL}")
    print(f"   数据库: {DB_PATH}")
    print("=" * 60)

    # 选股
    print("\n📋 选取测试标的...")
    symbols = select_test_symbols(max_count=5)

    if not symbols:
        print("❌ 数据库中没有找到有完整四级别数据的股票")
        exit(1)

    print(f"   找到 {len(symbols)} 只符合条件的标的:")
    for s in symbols:
        print(f"   • {s['symbol']} | 日线状态: {s['state_hint']} | 价格: {s['price']}")

    # 逐只测试
    results = []
    for i, sym_info in enumerate(symbols):
        symbol = sym_info["symbol"]
        print(f"\n{'─' * 60}")
        print(f"[{i+1}/{len(symbols)}] 测试 {symbol} (日线: {sym_info['state_hint']})")
        print(f"{'─' * 60}")

        try:
            result = test_single_symbol(symbol, sym_info)

            if "error" in result:
                print(f"   ⚠️ 跳过: {result['error']}")
                continue

            print(f"   ✅ 当前价: {result['current_price']}")
            print(f"   持仓: {'有' if result['position']['holding'] else '无'}")
            print(f"   压力支撑簇: {result['pressure_support_count']} 个")
            print(f"   输入: {result['input_chars']} 字符")
            print(f"   Token: prompt={result['tokens'].get('prompt_tokens', '?')}, "
                  f"completion={result['tokens'].get('completion_tokens', '?')}, "
                  f"total={result['tokens'].get('total_tokens', '?')}")
            print()
            print("   📊 输出:")
            print("   " + "─" * 56)
            # 缩进打印输出
            for line in result["output"].split("\n"):
                print(f"   {line}")
            print("   " + "─" * 56)

            results.append(result)

            # 避免 API 限速
            if i < len(symbols) - 1:
                print("\n   ⏳ 等待 3 秒...")
                time.sleep(3)

        except Exception as e:
            print(f"   ❌ 失败: {e}")
            results.append({"symbol": symbol, "error": str(e)})

    # 汇总
    print(f"\n{'=' * 60}")
    print("📊 批量测试汇总")
    print(f"{'=' * 60}")
    print(f"测试标的数: {len(symbols)}")
    print(f"成功: {len([r for r in results if 'error' not in r])}")
    print(f"失败: {len([r for r in results if 'error' in r])}")

    total_tokens = sum(r.get("tokens", {}).get("total_tokens", 0) for r in results if "error" not in r)
    print(f"总 Token 消耗: {total_tokens}")

    print("\n各标的状态覆盖:")
    for r in results:
        if "error" not in r:
            pos_str = f"持仓 浮盈{r['position'].get('current_pnl_pct', '?')}%" if r["position"]["holding"] else "空仓"
            print(f"  • {r['symbol']} | {r['state_hint']} | {pos_str}")

    # 保存完整结果
    output_dir = Path(__file__).parent.parent.parent / "data"
    output_file = output_dir / "test_unified_batch_results.json"
    # 保存精简版（不含完整输出文本，太长）
    summary = []
    for r in results:
        if "error" not in r:
            summary.append({
                "symbol": r["symbol"],
                "state_hint": r["state_hint"],
                "current_price": r["current_price"],
                "position": r["position"],
                "tokens": r["tokens"],
                "output_preview": r["output"][:500] + "..." if len(r["output"]) > 500 else r["output"],
            })
    output_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 结果已保存: {output_file}")


if __name__ == "__main__":
    run_batch()
