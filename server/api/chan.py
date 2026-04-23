import time
import logging
from fastapi import APIRouter, Query
from server.services.chan_service import analyze_matrix_state
from server.services.chan_detail_service import get_chan_detail

router = APIRouter()
logger = logging.getLogger(__name__)

# ─── V2 结果级缓存（15 秒 TTL，防止前端轮询压垮计算资源）───
_v2_cache: dict = {}   # key: "{symbol}_{cost}_{qty}"  value: {"ts": float, "data": dict}
_V2_CACHE_TTL = 15.0   # 秒


def _v2_cache_get(key: str):
    entry = _v2_cache.get(key)
    if entry and (time.monotonic() - entry["ts"]) < _V2_CACHE_TTL:
        return entry["data"], True
    return None, False


def _v2_cache_set(key: str, data: dict):
    _v2_cache[key] = {"ts": time.monotonic(), "data": data}


# ─── V2 字段计算辅助函数 ───

def _compute_entry_checklist(day: dict, m30: dict, m5: dict) -> dict:
    """根据各级别分析结果计算入场五条件（空仓视角）。"""
    day_patterns = " ".join(day.get("patterns", []))
    m30_patterns = " ".join(m30.get("patterns", []))
    m5_patterns  = " ".join(m5.get("patterns", []))
    m30_zoushi   = m30.get("zoushi_type", {}).get("type", "构建中")

    day_buy_node        = any(kw in day_patterns for kw in ("二买", "三买", "类二买", "类三买"))
    day_not_top_diverge = not any(kw in day_patterns for kw in ("顶背驰", "1卖", "二卖", "三卖"))
    thirty_min_structure= m30_zoushi != "构建中"
    thirty_min_buy_node = any(kw in m30_patterns for kw in ("二买", "三买", "类二买", "底背驰"))
    five_min_entry_bar  = any(kw in m5_patterns  for kw in ("底背驰", "二买", "三买", "类二买"))
    all_passed = all([
        day_buy_node, day_not_top_diverge, thirty_min_structure,
        thirty_min_buy_node, five_min_entry_bar,
    ])
    return {
        "day_buy_node":         day_buy_node,
        "day_not_top_diverge":  day_not_top_diverge,
        "thirty_min_structure": thirty_min_structure,
        "thirty_min_buy_node":  thirty_min_buy_node,
        "five_min_entry_bar":   five_min_entry_bar,
        "all_passed":           all_passed,
    }


def _find_structural_high_s1(day_bis: list, current_price: float, day_zg: float) -> float:
    """战法一：从日线笔列表找2买对应的结构前高。

    2买结构 = 上涨笔 → 向下回调笔（当前在回调末端入场）。
    前高 = 最后一根确认向下笔的起点（即上涨笔的顶点）。
    """
    if not day_bis:
        return 0.0
    # 遍历倒序，找最后一根确认的向下笔
    for bi in reversed(day_bis):
        is_up   = bi.get("is_up") or bi.get("isUp")
        is_sure = bi.get("is_sure") or bi.get("isSure")
        if not is_up and is_sure:
            # 向下笔的起点就是前高
            peak = bi.get("start_price") or bi.get("high") or bi.get("fx_high") or 0.0
            peak = float(peak)
            # 合理性校验：前高必须高于当前价，否则结构异常，退回到 day_zg
            if peak > current_price > 0:
                return round(peak, 2)
    return 0.0


def _check_reward_ratio(
    entry_price: float, stop_price: float,
    target_price: float, min_ratio: float,
    is_open_target: bool = False,
) -> dict:
    """赔率门控：入场赔率检查（Task #23）。

    战法一 min_ratio=2.0（1:2），战法二 min_ratio=3.0（1:3+）。
    战法二目标开放时，用估算赔率（仅供参考，不作硬性门控）。
    """
    stop_dist = entry_price - stop_price if entry_price > stop_price > 0 else 0.0
    if stop_dist <= 0:
        return {"ratio": 0.0, "ok": False, "verdict": "止损价格异常，无法计算赔率", "is_open": is_open_target}

    if is_open_target or target_price <= 0:
        # 战法二目标开放：赔率参考值无法精确计算
        return {
            "ratio": None,
            "ok": True,       # 开放目标不做硬性校验
            "verdict": f"战法二目标开放（1:3+ 预期），赔率以结构为准",
            "is_open": True,
        }

    reward = target_price - entry_price
    ratio  = round(reward / stop_dist, 2) if stop_dist > 0 else 0.0
    ok     = ratio >= min_ratio
    if ok:
        verdict = f"赔率 1:{ratio:.1f} ≥ {min_ratio:.0f}:1 ✓"
    else:
        verdict = f"赔率 1:{ratio:.1f} 不足（目标要求 ≥ {min_ratio:.0f}:1），建议重新评估入场"
    return {"ratio": ratio, "ok": ok, "verdict": verdict, "is_open": False}


def _compute_holding_status(
    day: dict, m30: dict, holding, forward_a: dict,
    m30_bis: list = None,
    strategy_type: str = "战法一",
) -> dict:
    """六阶段持仓状态机 v2 — 战法感知版（雷达重设计方案 §2.5 + 补充方案）。

    参数说明：
        day           — 日线级别分析结果
        m30           — 30分级别分析结果
        holding       — 持仓信息 {"cost": float, "qty": int, "entry_date": str|None,
                                   "trailing_stop_price": float|None}
        forward_a     — forward_analysis_a 结果（用于提取止损位）
        m30_bis       — 30分笔列表（Stage 0 验证用）
        strategy_type — 入场战法："战法一"（趋势延续）| "战法二"（趋势启动）| "未知"

    战法一 阶段判定优先级（趋势延续，持仓周期短，对次级别信号敏感）：
        Stage 5：日线顶背驰 OR 三卖 OR 台阶止损触穿 → 清仓
        Stage 4：30分顶背驰（任何类型）OR 二卖 → 减仓50%
        Stage 3：浮盈≥2×止损距 → 利润保护期
        Stage 2：浮盈≥1×止损距 → 保本期
        Stage 1：30分向上确认笔 → 验证期
        Stage 0：持仓中，尚未确认

    战法二 阶段判定优先级（趋势启动，持仓周期长，容忍次级别震荡）：
        Stage 5：日线顶背驰 OR 三卖 OR 台阶止损触穿 → 清仓
        Stage 4：30分顶背驰（转折型）→ 减仓50%；中继型只记录不减仓
        Stage 3：浮盈≥3×止损距 → 利润保护期（战法二赔率目标更高）
        Stage 2：浮盈≥1×止损距 → 保本期
        Stage 1：30分向上确认笔 → 验证期
        Stage 0：持仓中，尚未确认
    """
    import datetime as _dt

    if m30_bis is None:
        m30_bis = m30.get("detail_bis", [])

    day_patterns  = " ".join(day.get("patterns", []))
    m30_patterns  = " ".join(m30.get("patterns", []))
    current_price = day.get("price", 0.0)
    day_zg        = day.get("zg", 0.0)
    m30_zg        = m30.get("zg", 0.0)

    # ── 无持仓 → 直接返回 empty ──
    if not holding or not (holding.get("cost", 0) > 0 and holding.get("qty", 0) > 0):
        return {
            "stage":             "empty",
            "label":             "空仓",
            "strategy_type":     strategy_type,
            "stair_stop_price":  0.0,
            "locked_profit_pct": 0.0,
            "top_diverge_30min": False,
            "top_diverge_30min_type": "",
            "top_diverge_day":   False,
            "m30_relay_note":    "",
            "action":            "",
            "target_price_1":    0.0,
            "target_price_2":    0.0,
            "target_is_placeholder": True,
            "target_open":       False,
            "target_label":      "",
            "target_1_reached":  False,
            "target_2_reached":  False,
            "validation": {
                "m30_bi_direction": "未形成",
                "m30_bi_complete":  False,
                "bars_since_entry": 0,
                "bars_remaining":   10,
                "status":           "空仓",
            },
        }

    cost           = holding["cost"]
    persisted_stop = holding.get("trailing_stop_price") or 0.0

    # ── 台阶止损（只上移不下移）──
    computed_stop = 0.0
    if forward_a:
        for fc in (forward_a.get("forward_classes") or []):
            sl = fc.get("stop_loss") or fc.get("stopLoss")
            if sl and float(sl) > 0:
                computed_stop = float(sl); break
    if computed_stop == 0.0:
        computed_stop = round(m30_zg, 2) if m30_zg > 0 else (round(day_zg, 2) if day_zg > 0 else 0.0)
    stair_stop = round(max(computed_stop, persisted_stop), 2)

    # ── 信号检测 ──
    top_diverge_30min = any(kw in m30_patterns for kw in ("顶背驰", "1卖"))
    top_diverge_day   = any(kw in day_patterns for kw in ("顶背驰", "1卖"))
    second_sell_day   = "二卖" in day_patterns
    third_sell_day    = "三卖" in day_patterns
    broken_stop       = (current_price > 0 and stair_stop > 0 and current_price <= stair_stop)

    # 30分顶背驰类型（中继/转折）— 用于战法二分叉
    m30_beichi_type = m30.get("latest_top_beichi_type", "")  # "中继" | "转折" | ""
    # 若字段缺失，用 patterns 推断（"中继" 字样优先）
    if not m30_beichi_type and top_diverge_30min:
        m30_beichi_type = "中继" if "中继" in m30_patterns else "转折"

    # ── 止损距离 & 浮盈倍数 ──
    stop_distance    = (cost - stair_stop) if stair_stop > 0 and stair_stop < cost else (cost * 0.03)
    profit_amount    = (current_price - cost) if current_price > 0 else 0.0
    profit_multiple  = (profit_amount / stop_distance) if stop_distance > 0 else 0.0
    locked_profit_pct = round(profit_amount / cost * 100, 2) if cost > 0 else 0.0

    # ── Stage 0 验证 ──
    m30_bi_direction = "未形成"; m30_bi_complete = False
    if m30_bis:
        last_bi = m30_bis[-1]
        if last_bi.get("is_sure") or last_bi.get("isSure"):
            m30_bi_direction = "向上" if (last_bi.get("is_up") or last_bi.get("isUp")) else "向下"
            m30_bi_complete  = True
        else:
            m30_bi_direction = "向上（未确认）" if (last_bi.get("is_up") or last_bi.get("isUp")) else "向下"

    bars_since_entry = 0
    if entry_date_str := holding.get("entry_date"):
        try:
            bars_since_entry = max(0, (_dt.date.today() - _dt.date.fromisoformat(entry_date_str)).days) * 8
        except ValueError:
            pass
    VALIDATION_BARS = 10
    bars_remaining  = max(0, VALIDATION_BARS - bars_since_entry)

    if m30_bi_complete and m30_bi_direction == "向上":   val_status = "验证通过"
    elif m30_bi_complete and m30_bi_direction == "向下": val_status = "预案失效"
    elif bars_since_entry >= VALIDATION_BARS:            val_status = "时间失效"
    else:                                                val_status = "验证中"

    validation = {
        "m30_bi_direction": m30_bi_direction,
        "m30_bi_complete":  m30_bi_complete,
        "bars_since_entry": bars_since_entry,
        "bars_remaining":   bars_remaining,
        "status":           val_status,
    }

    # ── 阶段判定：战法一 vs 战法二 分叉 ──────────────────────
    is_s2 = (strategy_type == "战法二")

    # Stage 5（两套战法一致）：日线顶背驰/三卖/台阶止损触穿
    if third_sell_day or broken_stop or top_diverge_day:
        stage = 5

    # Stage 4 分叉：
    elif is_s2:
        # 战法二：只有30分转折型顶背驰才减仓；中继型仅记录，不升阶
        if top_diverge_30min and m30_beichi_type == "转折":
            stage = 4
        elif second_sell_day:
            stage = 4
        # 战法二利润目标更高，Stage 3 门槛 ≥ 3× 止损距
        elif profit_multiple >= 3.0:
            stage = 3
        elif profit_multiple >= 1.0:
            stage = 2
        elif m30_bi_complete and m30_bi_direction == "向上":
            stage = 1
        else:
            stage = 0
    else:
        # 战法一：30分任何顶背驰都触发 Stage 4
        if top_diverge_30min or second_sell_day:
            stage = 4
        elif profit_multiple >= 2.0:
            stage = 3
        elif profit_multiple >= 1.0:
            stage = 2
        elif m30_bi_complete and m30_bi_direction == "向上":
            stage = 1
        else:
            stage = 0

    # ── 阶段标签 & 操作建议（战法差异化）──
    if is_s2:
        STAGE_META = {
            0: ("走势验证期",   "持续观察30分走势，确认突破有效"),
            1: ("验证期",       "30分上涨笔已确认，趋势启动中，持仓"),
            2: ("保本期",       "浮盈≥1倍止损距，止损上移至成本附近"),
            3: ("利润保护期",   "浮盈≥3倍止损距，台阶止损跟踪，等待日线信号"),
            4: ("次级减速",     "⚠️ 30分转折型背驰，建议减仓50%，剩余等待日线顶背驰"),
            5: ("趋势终结",     "🔴 日线顶背驰/三卖确认，建议清仓"),
        }
    else:
        STAGE_META = {
            0: ("走势验证期",   "持续观察30分走势，尚未确认上涨笔"),
            1: ("验证期",       "30分上涨笔已确认，持仓运行"),
            2: ("保本期",       "浮盈≥1倍止损距，止损上移至成本附近"),
            3: ("利润保护期",   "浮盈≥2倍止损距，台阶止损跟踪中枢ZG"),
            4: ("减速预警",     "⚠️ 30分顶背驰/卖点，建议减仓50%观察"),
            5: ("趋势终结",     "🔴 日线顶背驰/三卖确认或止损触穿，建议清仓"),
        }
    label, action = STAGE_META.get(stage, (str(stage), ""))

    # 战法二 Stage 0-3 时，若30分有中继型背驰，追加说明而不升阶
    m30_relay_note = ""
    if is_s2 and top_diverge_30min and m30_beichi_type == "中继" and stage < 4:
        m30_relay_note = "30分出现中继背驰，次级别震荡，结构仍有效，继续持有"

    # ── 结构化目标价（Task #22）──
    target_open = is_s2   # 战法二目标开放
    target_label = ""
    target_1 = 0.0; target_2 = 0.0

    if is_s2:
        # 战法二：目标开放，以日线顶背驰为出场信号
        target_label = "趋势进行中，无固定目标价——以日线顶背驰为出场信号"
    else:
        # 战法一：目标 = 日线二买对应的结构前高
        day_bis = day.get("bi_list", []) or day.get("bis", [])
        target_1 = _find_structural_high_s1(day_bis, current_price, day_zg)
        if target_1 > 0:
            target_2 = round(target_1 * 1.05, 2)   # 前高突破5%为第二目标
            target_label = f"结构前高 {target_1:.2f}"
        else:
            # fallback：日线ZG × 1.10（占位）
            target_1 = round(day_zg * 1.10, 2) if day_zg > 0 else 0.0
            target_2 = round(day_zg * 1.20, 2) if day_zg > 0 else 0.0
            target_label = "前高未检测到，使用估算"

    return {
        "stage":              stage,
        "label":              label,
        "strategy_type":      strategy_type,
        "stair_stop_price":   stair_stop,
        "locked_profit_pct":  locked_profit_pct,
        "top_diverge_30min":  top_diverge_30min,
        "top_diverge_30min_type": m30_beichi_type,
        "top_diverge_day":    top_diverge_day,
        "m30_relay_note":     m30_relay_note,     # 战法二：中继背驰说明
        "action":             action,
        "target_price_1":     target_1,
        "target_price_2":     target_2,
        "target_is_placeholder": not bool(target_1 > 0 and not is_s2),
        "target_open":        target_open,
        "target_label":       target_label,
        "target_1_reached":   bool(not is_s2 and current_price > 0 and target_1 > 0 and current_price >= target_1),
        "target_2_reached":   bool(not is_s2 and current_price > 0 and target_2 > 0 and current_price >= target_2),
        "validation":         validation,
    }


def _persist_trailing_stop(symbol: str, new_stop: float):
    """台阶止损只上移不下移，异步写入 positions.trailing_stop_price。

    使用 MAX(COALESCE(...), ?) 保证幂等且永不下移。
    """
    try:
        from server.db.database import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE positions
                   SET trailing_stop_price = MAX(COALESCE(trailing_stop_price, 0), ?)
                   WHERE symbol = ? AND quantity > 0""",
                (new_stop, symbol),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("[台阶止损持久化] 失败 symbol=%s stop=%.2f error=%s", symbol, new_stop, exc)


def _compute_data_freshness(day: dict) -> dict:
    """根据日线最后一根 K 线时间戳判断数据时效性。"""
    import datetime as dt

    last_ts = day.get("last_bar_ts", 0) or 0   # chan_service 若输出该字段则使用
    if last_ts == 0:
        # 降级：用当前时间戳，标记为"无法判断"
        return {"last_updated_ts": 0, "is_stale": False, "stale_reason": ""}

    now = dt.datetime.now()
    last_dt = dt.datetime.fromtimestamp(last_ts)

    # 判断是否交易时间
    t = now.time()
    is_trading = (
        (dt.time(9, 30) <= t <= dt.time(11, 30)) or
        (dt.time(13, 0) <= t <= dt.time(15, 0))
    ) and now.weekday() < 5

    is_stale = False
    stale_reason = ""

    if is_trading:
        lag_minutes = (now - last_dt).total_seconds() / 60
        if lag_minutes > 5:
            is_stale = True
            stale_reason = "TRADING_HOUR_LAG"
    else:
        # 盘后：若缺当天 15:00 收盘数据则视为过期
        today_close = now.replace(hour=15, minute=0, second=0, microsecond=0)
        if now.weekday() < 5 and now >= today_close and last_dt < today_close:
            is_stale = True
            stale_reason = "MISSING_CLOSE"

    return {
        "last_updated_ts": int(last_ts),
        "is_stale":        is_stale,
        "stale_reason":    stale_reason,
    }


@router.get("/matrix/{symbol}")
async def get_chan_matrix(
    symbol: str,
    cost: float = Query(default=0.0, description="持仓成本价，0=空仓"),
    qty:  int   = Query(default=0,   description="持仓数量（股），0=空仓"),
):
    """
    获取指定股票的双轴跨级别缠论状态矩阵。
    包含：
    - matrix_a: 日线 + 30分钟 + 5分钟
    - matrix_b: 日线 + 60分钟 + 15分钟

    持仓参数（可选）：
    - cost: 均摊成本价（avg_cost）
    - qty:  持仓数量（股）
    传入后 forward_analysis 切换为持仓路径，生成止损/减仓预案。
    """
    # 兼容多种股票代码格式：sh600519 / sh.600519 / sh-600519
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    holding = {"cost": cost, "qty": qty} if cost > 0 and qty > 0 else None
    matrix_data = await analyze_matrix_state(symbol_bs, holding=holding)
    return {"status": "success", "data": matrix_data}


@router.get("/matrix/v2/{symbol}")
async def get_chan_matrix_v2(
    symbol: str,
    cost: float = Query(default=0.0, description="持仓成本价，0=空仓"),
    qty:  int   = Query(default=0,   description="持仓数量（股），0=空仓"),
):
    """
    V2 雷达专用路由（不破坏旧 ChanView/SandTable）。

    在 V1 矩阵数据基础上新增：
    - error:           顶层错误捕获（code / message / fallback_used）
    - entry_checklist: 入场五条件逐一检测（空仓视角）
    - holding_status:  持仓阶段 / 台阶止损 / 锁定利润 / 目标价（持仓视角）
    - data_freshness:  数据时效性元信息（is_stale / stale_reason）

    缓存：结果级 TTL=15s，防止前端轮询压垮计算资源。
    日志：每次调用输出 holding_stage、cache_hit、耗时(ms)。
    """
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    holding = {"cost": cost, "qty": qty} if cost > 0 and qty > 0 else None
    cache_key = f"{symbol_bs}_{cost}_{qty}"

    # ── 缓存命中 ──
    cached, hit = _v2_cache_get(cache_key)
    if hit:
        logger.info(
            "[V2] symbol=%s holding_stage=%s cache_hit=True elapsed_ms=0",
            symbol_bs, cached.get("holding_status", {}).get("stage", "?"),
        )
        return {"status": "success", "data": cached}

    # ── 计算 ──
    t_start = time.monotonic()
    error_info = {"code": None, "message": "", "fallback_used": False}
    matrix_data = None

    try:
        matrix_data = await analyze_matrix_state(symbol_bs, holding=holding)
    except Exception as exc:
        logger.error(
            "[ERROR] CHAN_ENGINE_FALLBACK: %s  error=%s", symbol_bs, exc, exc_info=True
        )
        error_info = {"code": "ENGINE_ERROR", "message": str(exc), "fallback_used": True}
        # 降级返回空结构
        return {
            "status": "error",
            "data": {
                "symbol": symbol_bs,
                "error": error_info,
                "entry_checklist": None,
                "holding_status":  None,
                "data_freshness":  {"last_updated_ts": 0, "is_stale": True, "stale_reason": "ENGINE_ERROR"},
            }
        }

    elapsed_ms = round((time.monotonic() - t_start) * 1000)

    # ── 计算扩展字段 ──
    matrix_a   = matrix_data.get("matrix_a", [])
    forward_a  = matrix_data.get("forward_analysis_a", {})
    day  = matrix_a[0] if len(matrix_a) > 0 else {}
    m30  = matrix_a[1] if len(matrix_a) > 1 else {}
    m5   = matrix_a[2] if len(matrix_a) > 2 else {}

    # 提取 30分笔数据（用于 Stage 0 验证：是否走出完整向上笔）
    m30_bis = m30.get("detail_bis", [])

    # 从数据库读取持仓扩展字段（entry_date / trailing_stop_price / strategy_type / m5_entry_zg）
    _db_strategy_type = "未知"
    if holding:
        try:
            from server.db.database import get_connection
            _conn = get_connection()
            try:
                _row = _conn.execute(
                    """SELECT entry_date, trailing_stop_price,
                              strategy_type, m5_entry_zg
                       FROM positions WHERE symbol = ? AND quantity > 0""",
                    (symbol_bs,)
                ).fetchone()
                if _row:
                    holding = {
                        **holding,
                        "entry_date":          _row[0],
                        "trailing_stop_price": _row[1],
                        "strategy_type":       _row[2] or "未知",
                        "m5_entry_zg":         _row[3] or 0,
                    }
                    _db_strategy_type = _row[2] or "未知"
            finally:
                _conn.close()
        except Exception as _e:
            logger.debug("[V2] 读取持仓扩展字段失败: %s", _e)

    # 战法分类（空仓模式从 analyze_matrix_state 结果中提取）—— 必须先赋值再使用
    strategy_classification = matrix_data.get("strategy_classification")

    # 解析持仓时使用的战法（DB 存储的入场时战法；若未知则用当前分析结果推断）
    _effective_strategy_type = _db_strategy_type
    if _effective_strategy_type == "未知" and strategy_classification:
        _effective_strategy_type = strategy_classification.get("strategy_type", "战法一")

    entry_checklist = _compute_entry_checklist(day, m30, m5)
    holding_status  = _compute_holding_status(
        day, m30, holding, forward_a,
        m30_bis=m30_bis,
        strategy_type=_effective_strategy_type,
    )
    data_freshness  = _compute_data_freshness(day)

    # 止盈六阶段状态机 v2（持仓模式）
    holding_stage_v2 = None
    if holding:
        try:
            from server.services.chan_service import _detect_holding_stage
            # 补充 m5_entry_zg 到 holding（从 positions 表读）
            if "m5_entry_zg" not in holding:
                try:
                    from server.db.database import get_connection as _gc
                    _c = _gc()
                    _r = _c.execute(
                        "SELECT m5_entry_zg FROM positions WHERE symbol=? AND quantity>0",
                        (symbol_bs,)
                    ).fetchone()
                    if _r:
                        holding = {**holding, "m5_entry_zg": _r[0] or 0}
                    _c.close()
                except Exception:
                    pass
            holding_stage_v2 = _detect_holding_stage(holding, day, m30, m5)
            # 台阶止损持久化（v2 状态机结果优先）
            ts = holding_stage_v2.get("trailing_stop", 0)
            if ts > 0:
                _persist_trailing_stop(symbol_bs, ts)
        except Exception as _e:
            logger.warning("[V2] _detect_holding_stage 失败: %s", _e)

    # ATR止损校验 + 目标价 + 建议仓位 + 赔率（空仓模式）
    stop_atr_check  = None
    targets         = None
    position_sizing = None
    reward_ratio    = None
    if not holding and strategy_classification and strategy_classification.get("primary"):
        primary    = strategy_classification["primary"]
        stop_price = primary.get("stop_price", 0)
        curr_price = m5.get("price", 0) or day.get("price", 0)
        _stype_entry = strategy_classification.get("strategy_type", "战法一")
        if stop_price and curr_price:
            try:
                from server.services.chan_service import (
                    _check_stop_atr, _calc_target_price, _calc_position_size
                )
                from server.services.atr_service import calculate_atr_from_klines
                day_klines = day.get("klines", []) or []
                atr_val    = calculate_atr_from_klines(day_klines) if day_klines else None
                stop_atr_check = _check_stop_atr(curr_price, stop_price, atr_val)
                targets        = _calc_target_price(
                    curr_price, day.get("detail_bis", []),
                    day.get("zhongshus", []) or [], stop_price
                )
                position_sizing = _calc_position_size(
                    account_value=1_000_000,  # 默认值，后续从用户设置读取
                    current_price=curr_price,
                    stop_price=stop_price,
                    risk_pct=0.01,
                )
                # ── 赔率门控（入场第六条件）──
                # 战法一：目标=结构前高，最低赔率 1:2
                # 战法二：目标开放，最低赔率 1:3（无固定目标价时 ok=True）
                _is_s2 = (_stype_entry == "战法二")
                _target_price = 0.0
                if targets and not _is_s2:
                    _target_price = targets.get("s1_target", 0.0) or 0.0
                reward_ratio = _check_reward_ratio(
                    entry_price   = curr_price,
                    stop_price    = stop_price,
                    target_price  = _target_price,
                    min_ratio     = 3.0 if _is_s2 else 2.0,
                    is_open_target= _is_s2,
                )
            except Exception as _e:
                logger.warning("[V2] ATR/目标价/仓位/赔率计算失败: %s", _e)

    # 旧路径台阶止损兜底（holding_stage_v2 未成功时使用）
    if holding and not holding_stage_v2 and holding_status.get("stair_stop_price", 0) > 0:
        _persist_trailing_stop(symbol_bs, holding_status["stair_stop_price"])

    response_data = {
        **matrix_data,
        "error":                   error_info,
        "entry_checklist":         entry_checklist,          # 旧格式，向后兼容
        "strategy_classification": strategy_classification,  # Task #5 战法分类
        "holding_status":          holding_status,           # 旧格式，向后兼容
        "holding_stage_v2":        holding_stage_v2,         # Task #9 六阶段状态机
        "stop_atr_check":          stop_atr_check,           # Task #6 ATR校验
        "targets":                 targets,                  # Task #7 目标价
        "position_sizing":         position_sizing,          # Task #7 建议仓位
        "reward_ratio":            reward_ratio,             # Task #23 赔率门控
        "data_freshness":          data_freshness,
    }

    _v2_cache_set(cache_key, response_data)

    logger.info(
        "[V2] symbol=%s strategy=%s holding_stage=%s cache_hit=False elapsed_ms=%d",
        symbol_bs,
        strategy_classification.get("strategy_type", "N/A") if strategy_classification else "持仓模式",
        holding_stage_v2.get("stage", holding_status.get("stage", "?")) if holding_stage_v2 else holding_status.get("stage", "?"),
        elapsed_ms,
    )
    return {"status": "success", "data": response_data}


@router.get("/detail/{symbol}")
async def get_chan_detail_api(
    symbol: str,
    freq: str = Query(default="day", description="K线级别: day/60/30/15/5"),
    count: int = Query(default=500, ge=50, le=5000, description="K线条数"),
):
    """
    获取指定股票的完整缠论几何解析数据，供 KlineChart 前端渲染。

    返回：
    - klines:    原始 K 线（OHLCV）
    - bis:       笔（折线几何坐标 x0/y0/x1/y1）
    - segs:      线段（TODO，待 chan_engine 升级后接入）
    - zhongshus: 中枢（矩形框 begin_date/end_date/zg/zd/gg/dd）
    - macd:      MACD 指标（dif/dea/hist/dates）
    - stats:     统计摘要（k线数/笔数/中枢数）
    """
    # 兼容多种股票代码格式：sh600519 / sh.600519 / sh-600519
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    result = await get_chan_detail(symbol_bs, freq=freq, count=count)
    return {"status": "success", "data": result}
