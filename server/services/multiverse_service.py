"""CT-OS V4.5 多元宇宙日志服务

每日自动记录各级别的完全分类，次日对比实际走势结算，
累积为分支树，供 AI 复盘和系统优化。

核心流程：
  20:30 自动触发 → 拉取分钟线 → 拍快照 → 结算昨天 → AI 复盘
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from server.db.database import get_connection
from server.services.chan_service import analyze_matrix_state

logger = logging.getLogger(__name__)

# ═══ 结算阈值 ═══
SETTLEMENT_THRESHOLDS = {
    'day': {'breakout_pct': 0.02},
    'm30': {'breakout_pct': 0.01},
    'm5':  {'breakout_pct': 0.005},
}

LEVEL_KEYS = {
    'A': ['day', 'm30', 'm5'],
    'B': ['day', 'm60', 'm15'],
}


# ─── 1. 拍快照 ───

async def take_daily_snapshot(symbol: str, user_id: int = 1, mode: str = 'A') -> dict:
    """拍摄当日多级别完全分类快照。

    Args:
        symbol: 股票代码 (如 "sh688008")
        user_id: 用户ID
        mode: 'A' (日+30m+5m) 或 'B' (日+60m+15m)

    Returns:
        快照字典，包含 id 和各级别分类
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # 检查今天是否已经拍过
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM multiverse_snapshots WHERE user_id=? AND symbol=? AND snapshot_date=?",
            (user_id, symbol, today)
        ).fetchone()
        if existing:
            logger.info("今日快照已存在: %s %s", symbol, today)
            return {"id": existing["id"], "status": "exists"}
    finally:
        conn.close()

    # 获取最新矩阵状态
    matrix_data = await analyze_matrix_state(symbol)
    matrix_key = f"matrix_{'a' if mode == 'A' else 'b'}"
    levels = matrix_data.get(matrix_key, [])

    if not levels:
        logger.warning("矩阵数据为空: %s", symbol)
        return {"status": "no_data"}

    level_names = LEVEL_KEYS[mode]

    # 提取各级别的结构和分类
    structure = {}
    classifications = {}
    highlighted = {}

    for i, lv in enumerate(levels):
        level_key = level_names[i] if i < len(level_names) else f"l{i}"
        zoushi = lv.get("zoushi_type", {})
        cls_list = lv.get("classifications", [])

        structure[level_key] = {
            "zoushi_type": zoushi.get("type", "未知"),
            "zs_count": zoushi.get("zs_count", 0),
            "completion": zoushi.get("completion", ""),
            "zg": lv.get("zg"),
            "zd": lv.get("zd"),
            "price": lv.get("price"),
            "state": lv.get("state"),
            "patterns": lv.get("patterns", []),
        }
        classifications[level_key] = cls_list

        # 判断"当下"在哪个分类
        price = lv.get("price", 0)
        zg = lv.get("zg", 0)
        zd = lv.get("zd", 0)
        zoushi_type = zoushi.get("type", "")

        if zoushi_type == "盘整" and zg and zd:
            if price > zg:
                highlighted[level_key] = "A"
            elif price < zd:
                highlighted[level_key] = "C"
            else:
                highlighted[level_key] = "B"
        elif "趋势" in zoushi_type:
            patterns_str = " ".join(lv.get("patterns", []))
            if "背驰" in patterns_str:
                highlighted[level_key] = "B"
            else:
                highlighted[level_key] = "A"
        else:
            highlighted[level_key] = "A"

    # 找到昨天的快照作为 parent
    conn = get_connection()
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        parent_row = conn.execute(
            "SELECT id FROM multiverse_snapshots WHERE user_id=? AND symbol=? AND snapshot_date=?",
            (user_id, symbol, yesterday)
        ).fetchone()
        parent_id = parent_row["id"] if parent_row else None

        # 入库
        cursor = conn.execute(
            """INSERT INTO multiverse_snapshots 
               (user_id, symbol, snapshot_date, structure_json, classifications_json, 
                highlighted_json, parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, symbol, today,
             json.dumps(structure, ensure_ascii=False),
             json.dumps(classifications, ensure_ascii=False),
             json.dumps(highlighted, ensure_ascii=False),
             parent_id)
        )
        conn.commit()
        snapshot_id = cursor.lastrowid
        logger.info("快照已保存: %s %s (id=%d)", symbol, today, snapshot_id)

        return {
            "id": snapshot_id,
            "status": "created",
            "date": today,
            "structure": structure,
            "classifications": classifications,
            "highlighted": highlighted,
        }
    finally:
        conn.close()


# ─── 2. 结算昨天的分类 ───

async def settle_previous(symbol: str, user_id: int = 1) -> dict:
    """对比今天的结构与昨天的分类，判定走了哪条路。"""

    # 先获取今天的最新结构
    matrix_data = await analyze_matrix_state(symbol)
    today_levels = matrix_data.get("matrix_a", [])

    conn = get_connection()
    try:
        # 找到最近一个 PENDING 快照
        pending = conn.execute(
            """SELECT id, snapshot_date, structure_json, classifications_json, highlighted_json
               FROM multiverse_snapshots
               WHERE user_id=? AND symbol=? AND settlement_status='PENDING'
               ORDER BY snapshot_date DESC LIMIT 1""",
            (user_id, symbol)
        ).fetchone()

        if not pending:
            return {"status": "no_pending"}

        old_structure = json.loads(pending["structure_json"])
        old_classifications = json.loads(pending["classifications_json"])
        old_highlighted = json.loads(pending["highlighted_json"]) if pending["highlighted_json"] else {}

        outcomes = {}
        scores = {}

        level_map = {'day': 0, 'm30': 1, 'm5': 2}

        for level_key, idx in level_map.items():
            if level_key not in old_structure:
                continue
            if idx >= len(today_levels):
                continue

            old = old_structure[level_key]
            new = today_levels[idx]
            old_cls = old_classifications.get(level_key, [])
            threshold = SETTLEMENT_THRESHOLDS.get(level_key, {}).get('breakout_pct', 0.01)

            outcome_id = _determine_outcome(old, new, old_cls, threshold)
            outcomes[level_key] = outcome_id

            # 评分：当时的highlighted是否与实际outcome一致
            predicted = old_highlighted.get(level_key)
            if outcome_id and predicted:
                scores[level_key] = 1 if predicted == outcome_id else 0

        # 获取当前价格
        current_price = today_levels[0].get("price", 0) if today_levels else 0

        # 更新数据库
        conn.execute(
            """UPDATE multiverse_snapshots 
               SET outcome_json=?, outcome_price=?, settlement_status='SETTLED',
                   settled_at=CURRENT_TIMESTAMP,
                   day_correct=?, m30_correct=?, m5_correct=?
               WHERE id=?""",
            (json.dumps(outcomes, ensure_ascii=False),
             current_price,
             scores.get('day'), scores.get('m30'), scores.get('m5'),
             pending["id"])
        )
        conn.commit()

        logger.info("结算完成: %s %s → %s", symbol, pending["snapshot_date"], outcomes)
        return {
            "status": "settled",
            "snapshot_date": pending["snapshot_date"],
            "outcomes": outcomes,
            "scores": scores,
        }
    finally:
        conn.close()


def _determine_outcome(old_structure: dict, new_level: dict, 
                       old_cls: list, threshold: float) -> Optional[str]:
    """根据新旧结构对比，判定走了哪条路。"""
    zoushi_type = old_structure.get("zoushi_type", "")
    old_zg = old_structure.get("zg") or 0
    old_zd = old_structure.get("zd") or 0
    new_price = new_level.get("price", 0)
    new_patterns = " ".join(new_level.get("patterns", []))

    if zoushi_type == "盘整" and old_zg > 0 and old_zd > 0:
        if new_price > old_zg * (1 + threshold):
            return "A"  # 向上突破
        elif new_price < old_zd * (1 - threshold):
            return "C"  # 向下突破
        else:
            return "B"  # 继续盘整

    elif zoushi_type == "上涨趋势":
        if "顶背驰" in new_patterns:
            return "B"  # 趋势完成
        else:
            return "A"  # 趋势延伸

    elif zoushi_type == "下跌趋势":
        if "底背驰" in new_patterns:
            return "B"  # 趋势完成(1买)
        else:
            return "A"  # 趋势延伸

    elif zoushi_type == "构建中":
        new_zoushi = new_level.get("zoushi_type", {}).get("type", "")
        if new_zoushi == "盘整":
            return "A"
        elif new_zoushi in ("上涨趋势", "下跌趋势"):
            return "B"
        else:
            return "A"

    return None


# ─── 3. 查询接口 ───

def get_timeline(symbol: str, user_id: int = 1, days: int = 30) -> list:
    """获取时间线（最近N天的快照和结算）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, snapshot_date, structure_json, classifications_json,
                      highlighted_json, outcome_json, outcome_reason,
                      settlement_status, day_correct, m30_correct, m5_correct,
                      ai_review, parent_id
               FROM multiverse_snapshots
               WHERE user_id=? AND symbol=?
               ORDER BY snapshot_date DESC LIMIT ?""",
            (user_id, symbol, days)
        ).fetchall()

        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "date": r["snapshot_date"],
                "structure": json.loads(r["structure_json"]),
                "classifications": json.loads(r["classifications_json"]),
                "highlighted": json.loads(r["highlighted_json"]) if r["highlighted_json"] else {},
                "outcome": json.loads(r["outcome_json"]) if r["outcome_json"] else None,
                "outcome_reason": r["outcome_reason"],
                "status": r["settlement_status"],
                "scores": {
                    "day": r["day_correct"],
                    "m30": r["m30_correct"],
                    "m5": r["m5_correct"],
                },
                "ai_review": r["ai_review"],
                "parent_id": r["parent_id"],
            })
        return result
    finally:
        conn.close()


def get_scorecard(symbol: str, user_id: int = 1, days: int = 30) -> dict:
    """获取记分卡统计。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT day_correct, m30_correct, m5_correct, snapshot_date
               FROM multiverse_snapshots
               WHERE user_id=? AND symbol=? AND settlement_status='SETTLED'
               ORDER BY snapshot_date DESC LIMIT ?""",
            (user_id, symbol, days)
        ).fetchall()

        if not rows:
            return {"total": 0}

        total = len(rows)
        day_correct = sum(1 for r in rows if r["day_correct"] == 1)
        m30_correct = sum(1 for r in rows if r["m30_correct"] == 1)
        m5_correct = sum(1 for r in rows if r["m5_correct"] == 1)
        day_total = sum(1 for r in rows if r["day_correct"] is not None)
        m30_total = sum(1 for r in rows if r["m30_correct"] is not None)
        m5_total = sum(1 for r in rows if r["m5_correct"] is not None)

        # 连续正确天数
        streak = 0
        for r in rows:
            m30_ok = r["m30_correct"]
            if m30_ok == 1:
                streak += 1
            else:
                break

        return {
            "total": total,
            "day_accuracy": round(day_correct / day_total * 100, 1) if day_total else 0,
            "m30_accuracy": round(m30_correct / m30_total * 100, 1) if m30_total else 0,
            "m5_accuracy": round(m5_correct / m5_total * 100, 1) if m5_total else 0,
            "streak": streak,
            "day_total": day_total,
            "m30_total": m30_total,
            "m5_total": m5_total,
        }
    finally:
        conn.close()


# ─── 4. AI 复盘 ───

async def ai_daily_review(symbol: str, user_id: int = 1) -> dict:
    """调用 AI 对比昨天分类与今天走势，生成复盘解读。"""
    from server.services.llm_service import LLMService
    from server.prompts.multiverse_review_prompt import REVIEW_PROMPT

    timeline = get_timeline(symbol, user_id, days=2)
    if len(timeline) < 2:
        return {"status": "need_more_data"}

    today_snap = timeline[0]
    yesterday_snap = timeline[1]

    context = json.dumps({
        "symbol": symbol,
        "yesterday": {
            "date": yesterday_snap["date"],
            "structure": yesterday_snap["structure"],
            "classifications": yesterday_snap["classifications"],
            "highlighted": yesterday_snap["highlighted"],
            "outcome": yesterday_snap.get("outcome"),
        },
        "today": {
            "date": today_snap["date"],
            "structure": today_snap["structure"],
            "classifications": today_snap["classifications"],
        }
    }, ensure_ascii=False)

    llm = LLMService()
    try:
        result = await llm.infer_radar_deduction(REVIEW_PROMPT, context)

        # 保存 AI 复盘到昨天的快照
        review_text = result.get("review_text", result.get("position", ""))
        outcome_reason = json.dumps(result.get("outcomes", {}), ensure_ascii=False)

        conn = get_connection()
        try:
            conn.execute(
                """UPDATE multiverse_snapshots SET ai_review=?, outcome_reason=?
                   WHERE id=?""",
                (review_text, outcome_reason, yesterday_snap["id"])
            )
            conn.commit()
        finally:
            conn.close()

        return {"status": "success", "review": result}
    except Exception as e:
        logger.error("AI 复盘失败: %s", e)
        return {"status": "error", "message": str(e)}


# ─── 5. 批量快照（自动化调用） ───

async def auto_daily_run(user_id: int = 1):
    """20:30 自动运行：对所有自选股拍快照 + 结算昨天。"""
    conn = get_connection()
    try:
        # 获取自选股列表 (从 positions 和 watchlist localStorage 无法访问，用 positions)
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM positions WHERE user_id=? AND quantity > 0",
            (user_id,)
        ).fetchall()
        symbols = [r["symbol"] for r in rows] if rows else []
    finally:
        conn.close()

    if not symbols:
        logger.info("没有持仓股，跳过自动快照")
        return

    for symbol in symbols:
        try:
            # 先结算昨天
            settle_result = await settle_previous(symbol, user_id)
            logger.info("自动结算: %s → %s", symbol, settle_result.get("status"))

            # 再拍今天的快照
            snap_result = await take_daily_snapshot(symbol, user_id)
            logger.info("自动快照: %s → %s", symbol, snap_result.get("status"))
        except Exception as e:
            logger.error("自动处理 %s 失败: %s", symbol, e)
