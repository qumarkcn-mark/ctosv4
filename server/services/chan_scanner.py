"""
chan_scanner.py — 缠论选股扫描器

Adapter 模式：tdx_lake.db (adjustflag='3') → CKLine_Unit → CChan
复用现有缠论引擎（Chan.py），不重写缠论逻辑，不改动 Chan.py 本身。

使用方式:
    from server.services.chan_scanner import scan_symbol, ScanResult
    result = scan_symbol("sz002176", klines)  # klines 来自 kline_lake
"""

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 引入缠论引擎（与 chan_detail_service.py 保持一致）──────────────────────
_CHAN_PY_DIR = Path(__file__).resolve().parent.parent / "vendor" / "chan_py"
if str(_CHAN_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_CHAN_PY_DIR))

try:
    from Chan import CChan
    from ChanConfig import CChanConfig
    from Common.CEnum import AUTYPE, DATA_SRC, DATA_FIELD, KL_TYPE
    from Common.CTime import CTime
    from KLine.KLine_Unit import CKLine_Unit
    _CHAN_AVAILABLE = True
except ImportError as e:
    logger.error("Chan.py 引入失败: %s", e)
    _CHAN_AVAILABLE = False

# ── 扫描参数 ────────────────────────────────────────────────────────────────
WAR1_MIN_BARS        = 60    # 战法一：最少需要的K线根数
WAR2_MIN_BARS        = 40    # 战法二：最少需要的K线根数
WAR1_PULLBACK_DAYS   = 10    # 战法一：回踩窗口（近N日内）
WAR1_ZG_TOLERANCE   = 0.02  # 战法一：回踩触碰 ZG ± 2% 算有效
WAR1_VOLUME_RATIO   = 0.85  # 战法一：回踩期均量 < 突破日 × 85% 算缩量
WAR2_PULLBACK_MIN   = 0.03  # 战法二：最小回调幅度 3%
WAR2_PULLBACK_MAX   = 0.12  # 战法二：最大回调幅度 12%
ATR_PERIOD          = 14
ATR_MULTIPLIER      = 3.0   # 止损 = 收盘价 - ATR × 3
MAX_ATR_PCT         = 0.08  # 战法二：ATR止损幅度上限 8%
MIN_SWING_POINTS    = 3     # 战法二：有效摆动点最少数量

# ── CChan 全局配置（与 K 线图保持一致）──────────────────────────────────────
_CHAN_CONFIG = {
    "trigger_step":             True,
    "kl_data_check":            False,
    "bi_strict":                True,
    "bi_fx_check":              "loss",
    "print_warning":            False,
    "print_err_time":           False,
    "auto_skip_illegal_sub_lv": True,
}


# ── 数据结构 ─────────────────────────────────────────────────────────────────
@dataclass
class ScanResult:
    symbol:       str
    strategy:     str               # 'war1' | 'war2'
    score:        float             # 0~100
    close:        float
    stop_loss:    float
    target:       float             # 目标价（前高）
    rr_ratio:     float             # 赔率
    atr_pct:      float             # ATR止损幅度
    volume_ratio: float             # 量比（战法一：缩量比；战法二：近5日均量/20日均量）
    chan_desc:    str               # 缠论信号描述
    zg:           float = 0.0      # 中枢高点（战法一）
    zd:           float = 0.0      # 中枢低点（战法一）


# ── Adapter：kline_lake 行 → CKLine_Unit ────────────────────────────────────
def _rows_to_chan_units(rows: list) -> list:
    """
    把 kline_lake 查询结果转换为 CKLine_Unit 列表。
    rows 每行需要有: date(str 'YYYY-MM-DD'), open, high, low, close, volume
    """
    if not _CHAN_AVAILABLE:
        return []

    units = []
    for r in rows:
        # 兼容 sqlite3.Row 和 dict
        def _get(key):
            try:
                return r[key]
            except (TypeError, IndexError):
                return getattr(r, key, None)

        date_str = _get("date")   # 'YYYY-MM-DD'
        try:
            y, m, d = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
        except Exception:
            continue

        ctime = CTime(y, m, d, 0, 0)
        item_dict = {
            DATA_FIELD.FIELD_TIME:   ctime,
            DATA_FIELD.FIELD_OPEN:   float(_get("open")  or 0),
            DATA_FIELD.FIELD_HIGH:   float(_get("high")  or 0),
            DATA_FIELD.FIELD_LOW:    float(_get("low")   or 0),
            DATA_FIELD.FIELD_CLOSE:  float(_get("close") or 0),
            DATA_FIELD.FIELD_VOLUME: float(_get("volume") or 0),
        }
        if item_dict[DATA_FIELD.FIELD_CLOSE] <= 0:
            continue
        try:
            units.append(CKLine_Unit(item_dict))
        except Exception:
            continue
    return units


def _run_chan(units: list) -> Optional[object]:
    """
    把 CKLine_Unit 列表喂给 CChan，返回 kl_data 对象。
    失败时返回 None。
    """
    if not _CHAN_AVAILABLE or not units:
        return None
    try:
        config = CChanConfig(_CHAN_CONFIG)
        chan = CChan(
            code="scan",
            data_src=DATA_SRC.CUSTOM,
            lv_list=[KL_TYPE.K_DAY],
            config=config,
            autype=AUTYPE.QFQ,
        )
        chan.trigger_load({KL_TYPE.K_DAY: units})
        chan.kl_datas[KL_TYPE.K_DAY].cal_seg_and_zs()
        return chan.kl_datas[KL_TYPE.K_DAY]
    except Exception as e:
        logger.debug("CChan 运行失败: %s", e)
        return None


# ── ATR 计算（同步版，不依赖 atr_service.py 的异步版）──────────────────────
def _calc_atr(rows: list, period: int = ATR_PERIOD) -> float:
    """Wilder 移动平均 ATR，与 atr_service.calculate_atr_from_klines 逻辑一致"""
    highs, lows, closes = [], [], []
    for r in rows:
        def _get(key):
            try: return float(r[key])
            except Exception: return 0.0
        highs.append(_get("high"))
        lows.append(_get("low"))
        closes.append(_get("close"))

    if len(closes) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return 0.0

    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


# ── 评分算法 ──────────────────────────────────────────────────────────────────
def _calc_score(rr_ratio: float, atr_pct: float, volume_ratio: float,
                pullback_precision: float) -> float:
    """
    启发式评分 0~100，四个维度加权：
      赔率        40%：rr_ratio ≥ 3 满分，< 1 零分，线性插值
      止损幅度    25%：atr_pct ≤ 0.04 满分，≥ 0.08 零分
      量比健康度  20%：war1缩量越明显越高 / war2越接近1越高
      回踩精度    15%：价格越接近 ZG 或支撑位满分
    """
    # 赔率得分
    rr_score = min(max((rr_ratio - 1.0) / 2.0, 0.0), 1.0) * 40

    # 止损幅度得分（越小越好）
    if atr_pct <= 0.04:
        atr_score = 25.0
    elif atr_pct >= 0.08:
        atr_score = 0.0
    else:
        atr_score = (0.08 - atr_pct) / 0.04 * 25

    # 量比健康度得分
    # war1: volume_ratio = 回踩均量/突破量，越小越好（< 0.5 满分，> 0.85 零分）
    if volume_ratio <= 0.5:
        vol_score = 20.0
    elif volume_ratio >= 0.85:
        vol_score = 0.0
    else:
        vol_score = (0.85 - volume_ratio) / 0.35 * 20

    # 回踩精度得分（pullback_precision: 0=偏差最大，1=完美回踩）
    prec_score = min(max(pullback_precision, 0.0), 1.0) * 15

    return round(rr_score + atr_score + vol_score + prec_score, 1)


# ── 战法一：日线三买检测 ─────────────────────────────────────────────────────
def _scan_war1(rows: list, kl_data) -> Optional[ScanResult]:
    """
    战法一（三买结构）五条件：
      ① 识别出最近一个日线中枢（ZG/ZD 确定）
      ② 当前收盘价 > ZG（已突破中枢高点）
      ③ 近10日内曾回踩至 ZG ± 2% 区间
      ④ 当前价 > 回踩低点（未破 ZG）
      ⑤ 回踩期间日均量 < 突破当日量 × 85%（缩量）
    """
    if len(rows) < WAR1_MIN_BARS:
        return None

    try:
        zs_list = list(kl_data.zs_list)
    except Exception:
        return None

    if not zs_list:
        return None

    # 取最近一个确认的中枢（is_sure 或取末尾）
    last_zs = None
    for zs in reversed(zs_list):
        if zs.is_sure:
            last_zs = zs
            break
    if last_zs is None:
        last_zs = zs_list[-1]

    zg = last_zs.high   # 中枢高点 ZG
    zd = last_zs.low    # 中枢低点 ZD

    # 拿最近的行情数据（adjustflag='3' 为不复权，价格直接用）
    close_prices = [float(r["close"] if hasattr(r, "keys") else r[4]) for r in rows]
    low_prices   = [float(r["low"]   if hasattr(r, "keys") else r[3]) for r in rows]
    vol_list     = [float(r["volume"] if hasattr(r, "keys") else r[5]) for r in rows]

    # 为了简化，统一用 dict-like 访问
    def _f(r, key, idx):
        try:
            return float(r[key])
        except Exception:
            return float(r[idx])

    close_prices = [_f(r, "close",  4) for r in rows]
    low_prices   = [_f(r, "low",    3) for r in rows]
    vol_list     = [_f(r, "volume", 5) for r in rows]
    high_prices  = [_f(r, "high",   2) for r in rows]
    open_prices  = [_f(r, "open",   1) for r in rows]

    current_close = close_prices[-1]

    # ② 当前价 > ZG
    if current_close <= zg:
        return None

    # 找突破那根K线（第一根收盘 > ZG 的）
    breakout_idx = None
    for i in range(len(close_prices) - WAR1_PULLBACK_DAYS - 1, len(close_prices)):
        if close_prices[i] > zg:
            breakout_idx = i
            break
    if breakout_idx is None:
        return None

    breakout_vol = vol_list[breakout_idx] if vol_list[breakout_idx] > 0 else 1.0

    # ③ 近10日内曾回踩至 ZG ± 2% 区间
    pullback_window = rows[-WAR1_PULLBACK_DAYS:]
    pullback_lows  = [_f(r, "low", 3) for r in pullback_window]
    pullback_vols  = [_f(r, "volume", 5) for r in pullback_window]

    touched_zg = False
    pullback_min_price = float("inf")
    pullback_min_idx   = -1
    for i, lo in enumerate(pullback_lows):
        if zg * (1 - WAR1_ZG_TOLERANCE) <= lo <= zg * (1 + WAR1_ZG_TOLERANCE):
            touched_zg = True
        if lo < pullback_min_price:
            pullback_min_price = lo
            pullback_min_idx   = i

    if not touched_zg:
        return None

    # ④ 当前价 > 回踩低点（未跌破 ZG × (1 - tolerance)）
    if pullback_min_price < zg * (1 - WAR1_ZG_TOLERANCE):
        return None

    # ⑤ 回踩期均量 < 突破量 × WAR1_VOLUME_RATIO
    avg_pullback_vol = sum(pullback_vols) / len(pullback_vols) if pullback_vols else 0
    volume_ratio     = avg_pullback_vol / breakout_vol if breakout_vol > 0 else 1.0
    if volume_ratio >= WAR1_VOLUME_RATIO:
        return None

    # ── 计算止损/目标/赔率 ──────────────────────────────────────────────────
    atr = _calc_atr(rows)
    if atr <= 0:
        return None

    stop_loss = round(zd, 2)                       # 止损放在中枢低点 ZD
    atr_pct   = atr / current_close
    target    = round(max(high_prices[-60:]) if len(high_prices) >= 60 else max(high_prices), 2)

    if target <= current_close:
        return None

    rr_ratio = round((target - current_close) / (current_close - stop_loss), 2) \
        if current_close > stop_loss else 0.0

    if rr_ratio < 1.0:
        return None

    # 回踩精度：越接近 ZG 越好
    pullback_dev = abs(pullback_min_price - zg) / zg if zg > 0 else 1.0
    pullback_precision = max(0.0, 1.0 - pullback_dev / WAR1_ZG_TOLERANCE)

    score = _calc_score(rr_ratio, atr_pct, volume_ratio, pullback_precision)

    chan_desc = (
        f"日线三买，ZG={zg:.2f}，回踩低={pullback_min_price:.2f}未破，"
        f"缩量{volume_ratio:.0%}，赔率1:{rr_ratio:.1f}"
    )

    return ScanResult(
        symbol       = "",          # 由调用方填入
        strategy     = "war1",
        score        = score,
        close        = current_close,
        stop_loss    = stop_loss,
        target       = target,
        rr_ratio     = rr_ratio,
        atr_pct      = round(atr_pct, 4),
        volume_ratio = round(volume_ratio, 3),
        chan_desc     = chan_desc,
        zg           = round(zg, 2),
        zd           = round(zd, 2),
    )


# ── 战法二：趋势台阶检测 ─────────────────────────────────────────────────────
def _find_swing_points(prices: list[float], window: int = 5) -> list[tuple[int, float]]:
    """
    简单摆动点识别：局部极值，左右各 window 根都更低/更高。
    返回 [(index, price), ...]
    """
    points = []
    for i in range(window, len(prices) - window):
        lo = prices[i]
        if all(prices[j] >= lo for j in range(i - window, i + window + 1) if j != i):
            points.append((i, lo))
    return points


def _scan_war2(rows: list) -> Optional[ScanResult]:
    """
    战法二（趋势台阶）五条件：
      ① 近40根：低点序列抬高（至少3个有效摆动低点）
      ② 近40根：高点序列抬高（至少3个有效摆动高点）
      ③ 当前价较近期最高点回调 3%～12%
      ④ 当前价 > 前一个台阶摆动低点（结构未破）
      ⑤ ATR止损幅度 < 8%
    """
    if len(rows) < WAR2_MIN_BARS:
        return None

    def _f(r, key, idx):
        try: return float(r[key])
        except Exception: return float(r[idx])

    recent  = rows[-WAR2_MIN_BARS:]
    closes  = [_f(r, "close",  4) for r in recent]
    highs   = [_f(r, "high",   2) for r in recent]
    lows    = [_f(r, "low",    3) for r in recent]
    vols    = [_f(r, "volume", 5) for r in recent]

    current_close = closes[-1]
    recent_high   = max(highs)

    # ① 低点抬高
    swing_lows = _find_swing_points(lows, window=3)
    if len(swing_lows) < MIN_SWING_POINTS:
        return None
    low_prices_seq = [p for _, p in swing_lows]
    if not all(low_prices_seq[i] < low_prices_seq[i+1]
               for i in range(len(low_prices_seq) - 1)):
        return None

    # ② 高点抬高
    swing_highs = _find_swing_points(highs, window=3)
    if len(swing_highs) < MIN_SWING_POINTS:
        return None
    high_prices_seq = [p for _, p in swing_highs]
    if not all(high_prices_seq[i] < high_prices_seq[i+1]
               for i in range(len(high_prices_seq) - 1)):
        return None

    # ③ 当前价回调 3%～12%（相对近期最高点）
    if recent_high <= 0:
        return None
    pullback_pct = (recent_high - current_close) / recent_high
    if not (WAR2_PULLBACK_MIN <= pullback_pct <= WAR2_PULLBACK_MAX):
        return None

    # ④ 当前价 > 前一个台阶摆动低点（结构未破）
    if len(swing_lows) < 2:
        return None
    prev_swing_low = swing_lows[-2][1]   # 倒数第二个摆动低点
    if current_close <= prev_swing_low:
        return None

    # ⑤ ATR止损幅度 < MAX_ATR_PCT
    atr = _calc_atr(rows[-60:] if len(rows) >= 60 else rows)
    if atr <= 0:
        return None
    atr_pct = atr / current_close
    if atr_pct >= MAX_ATR_PCT:
        return None

    # ── 计算止损/目标/赔率 ──────────────────────────────────────────────────
    stop_loss = round(prev_swing_low - atr * 0.5, 2)   # 前台阶低点再下移半个ATR
    target    = round(recent_high * 1.05, 2)            # 前高突破 5% 作为初始目标

    if target <= current_close or current_close <= stop_loss:
        return None

    rr_ratio = round((target - current_close) / (current_close - stop_loss), 2)
    if rr_ratio < 1.0:
        return None

    # 量比：近5日均量 / 近20日均量
    avg_vol_5  = sum(vols[-5:])  / 5  if len(vols) >= 5  else 0
    avg_vol_20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1
    volume_ratio = round(avg_vol_5 / avg_vol_20, 3) if avg_vol_20 > 0 else 1.0

    # 回踩精度：回调越接近中间值（7.5%）越好
    ideal_pullback = (WAR2_PULLBACK_MIN + WAR2_PULLBACK_MAX) / 2
    pullback_precision = 1.0 - abs(pullback_pct - ideal_pullback) / (WAR2_PULLBACK_MAX - WAR2_PULLBACK_MIN)

    score = _calc_score(rr_ratio, atr_pct, min(volume_ratio, 1.0), pullback_precision)

    chan_desc = (
        f"趋势台阶，低点抬高×{len(swing_lows)}，"
        f"近期高点{recent_high:.2f}，当前回调{pullback_pct:.1%}，"
        f"结构支撑{prev_swing_low:.2f}，赔率1:{rr_ratio:.1f}"
    )

    return ScanResult(
        symbol       = "",
        strategy     = "war2",
        score        = score,
        close        = current_close,
        stop_loss    = stop_loss,
        target       = target,
        rr_ratio     = rr_ratio,
        atr_pct      = round(atr_pct, 4),
        volume_ratio = volume_ratio,
        chan_desc     = chan_desc,
    )


# ── 主入口：扫描单只股票 ─────────────────────────────────────────────────────
def scan_symbol(symbol: str, rows: list) -> list[ScanResult]:
    """
    对单只股票的 kline_lake 行进行战法一、战法二扫描。

    Args:
        symbol: 股票代码，如 'sz002176'
        rows:   kline_lake 查询结果（按日期升序），每行需包含
                date/open/high/low/close/volume/amount

    Returns:
        命中的 ScanResult 列表（0~2个，可能同时满足两种战法）
    """
    results = []

    # 战法一需要缠论引擎
    if len(rows) >= WAR1_MIN_BARS and _CHAN_AVAILABLE:
        units   = _rows_to_chan_units(rows)
        kl_data = _run_chan(units)
        if kl_data is not None:
            r = _scan_war1(rows, kl_data)
            if r is not None:
                r.symbol = symbol
                results.append(r)

    # 战法二不需要缠论引擎，直接用 kline_lake 数据
    if len(rows) >= WAR2_MIN_BARS:
        r = _scan_war2(rows)
        if r is not None:
            r.symbol = symbol
            results.append(r)

    return results
