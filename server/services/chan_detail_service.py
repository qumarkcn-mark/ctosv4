"""CT-OS V4.0 — 缠论全量几何解析服务

从本地数据湖读取 K 线，调用原版 chan.py 引擎解析完整结构（笔、线段、中枢），
并计算 MACD 指标，序列化为前端 KlineChart 可以直接消费的 JSON 格式。

输出结构概览：
{
    "symbol": "sh.600519",
    "freq":   "day",
    "klines": [...],            # 原始 OHLCV（lightweight-charts 格式）
    "bis":    [...],            # 笔：[{x0, y0, x1, y1, is_up, is_sure}, ...]
    "segs":   [...],            # 线段
    "zhongshus": [...],         # 中枢：[{begin_date, end_date, zg, zd, gg, dd}, ...]
    "macd":   {                 # MACD 数据（与 klines 等长）
        "dif": [...],
        "dea": [...],
        "hist": [...]
    }
}
"""

import sys
import os
import logging
from typing import Optional
from fastapi.concurrency import run_in_threadpool

# 引入官方开源版的 chan.py
_VENDOR_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vendor", "chan_py"))
if _VENDOR_ROOT not in sys.path:
    sys.path.insert(0, _VENDOR_ROOT)

try:
    from Chan import CChan
    from ChanConfig import CChanConfig
    from Common.CEnum import AUTYPE, DATA_SRC, DATA_FIELD, KL_TYPE
    from KLine.KLine_Unit import CKLine_Unit
    from Common.CTime import CTime
except ImportError as e:
    logging.error(f"无法导入 chan_py: {e}")

from server.db.kline_lake import query_klines
from server.engines.structure.chan_config_presets import (
    get_chan_config_dict,
    get_chan_config_meta,
)
from server.services.baostock_service import fetch_klines_quick

logger = logging.getLogger(__name__)

# 低于此数量，触发 BaoStock 自动补数据
_MIN_KLINES = 120

# ---------------------------------------------------------------------------
# MACD 计算（纯 Python，不依赖 ta-lib）
# ---------------------------------------------------------------------------

def _ema(prices: list[float], period: int) -> list[float]:
    """指数移动平均线"""
    if not prices:
        return []
    k = 2.0 / (period + 1)
    result = [prices[0]]
    for p in prices[1:]:
        result.append(p * k + result[-1] * (1 - k))
    return result

def _compute_macd(closes: list[float], fast=12, slow=26, signal=9) -> dict:
    """
    计算 MACD 指标。
    返回与 closes 等长的三个序列：dif, dea, hist（红绿柱 = dif-dea）
    """
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]  # ×2 是国内券商标准
    return {
        "dif":  [round(v, 4) for v in dif],
        "dea":  [round(v, 4) for v in dea],
        "hist": [round(v, 4) for v in hist],
    }

# ---------------------------------------------------------------------------
# 动能提取工具
# ---------------------------------------------------------------------------

def _measure_momentum(start_date: str, end_date: str, is_up: bool, macd_data: dict, date_to_idx: dict) -> dict:
    start_idx = date_to_idx.get(start_date)
    end_idx = date_to_idx.get(end_date)
    
    if start_idx is None or end_idx is None or start_idx >= end_idx:
        return {"area": 0.0, "dif_extreme": 0.0}
        
    hist = macd_data["hist"][start_idx:end_idx+1]
    dif = macd_data["dif"][start_idx:end_idx+1]
    
    if is_up:
        area = sum(max(h, 0) for h in hist)
        dif_extreme = max(dif) if dif else 0.0
    else:
        area = sum(abs(min(h, 0)) for h in hist)
        dif_extreme = abs(min(dif)) if dif else 0.0
        
    return {"area": round(area, 4), "dif_extreme": round(dif_extreme, 4)}

# ---------------------------------------------------------------------------
# 序列化工具
# ---------------------------------------------------------------------------

def _format_time(ctime: CTime, ctime_to_date_str: dict) -> str:
    """完美映射 CTime 回原始的 date_str，保障前端不出现查不到 index 的错误"""
    key = f"{ctime.year}-{ctime.month}-{ctime.day}-{ctime.hour}-{ctime.minute}"
    return ctime_to_date_str.get(key, "")

def _serialize_bis(bi_list, ctime_to_date_str, macd_data, date_to_idx) -> list[dict]:
    """将 CBi 列表序列化为前端可用格式，并打上 MACD 动能分"""
    result = []
    for bi in bi_list:
        is_up = str(bi.dir).endswith("UP")
        x0 = _format_time(bi.get_begin_klu().time, ctime_to_date_str)
        x1 = _format_time(bi.get_end_klu().time, ctime_to_date_str)
        y0 = bi.get_begin_val()
        y1 = bi.get_end_val()
        
        momentum = _measure_momentum(x0, x1, is_up, macd_data, date_to_idx)
        result.append({
            "x0": x0,
            "y0": round(y0, 4),
            "x1": x1,
            "y1": round(y1, 4),
            "is_up": is_up,
            "is_sure": bi.is_sure if hasattr(bi, 'is_sure') else True,
            "momentum": momentum,
        })
    return result


def _serialize_segs(seg_list, ctime_to_date_str, macd_data, date_to_idx) -> list[dict]:
    """将 CSeg 序列化为前端可用格式，并打上 MACD 动能分"""
    result = []
    for seg in seg_list:
        is_up = str(seg.dir).endswith("UP")
        x0 = _format_time(seg.get_begin_klu().time, ctime_to_date_str)
        x1 = _format_time(seg.get_end_klu().time, ctime_to_date_str)
        y0 = seg.get_begin_val()
        y1 = seg.get_end_val()
        
        momentum = _measure_momentum(x0, x1, is_up, macd_data, date_to_idx)
        result.append({
            "x0": x0,
            "y0": round(y0, 4),
            "x1": x1,
            "y1": round(y1, 4),
            "is_up": is_up,
            "is_sure": seg.is_sure if hasattr(seg, 'is_sure') else True,
            "momentum": momentum,
        })
    return result

def _serialize_bsps(bsp_list, ctime_to_date_str: dict) -> list[dict]:
    """
    序列化买卖点（Buy/Sell Points）列表为前端可用格式。

    bsp.klu 是该买卖点所在笔的末端 K 线单元：
      - 买点（is_buy=True）：笔向下结束，取 klu.low 作为标注价
      - 卖点（is_buy=False）：笔向上结束，取 klu.high 作为标注价

    返回格式：
      [{ "time": "2024-01-05", "price": 123.4, "type": "1", "is_buy": true }, ...]

    type 值对应 BSP_TYPE.value：
      "1"  → 一买/一卖
      "1p" → 类一买/卖
      "2"  → 二买/卖
      "2s" → 类二买/卖
      "3a" → 三买/卖（中枢在1类后）
      "3b" → 三买/卖（中枢在1类前）
    """
    result = []
    for bsp in bsp_list:
        try:
            klu = bsp.klu
            time_str = _format_time(klu.time, ctime_to_date_str)
            if not time_str:
                continue
            # 买点标注在低点，卖点标注在高点
            price = float(klu.low) if bsp.is_buy else float(klu.high)
            # 取首要类型（type2str 可能返回 "1,2s" 这样的多重类型，取首位）
            primary_type = bsp.type[0].value if bsp.type else "1"
            result.append({
                "time":    time_str,
                "price":   round(price, 4),
                "type":    primary_type,
                "is_buy":  bsp.is_buy,
            })
        except Exception:
            pass
    return result


def _serialize_zhongshus(zs_list, ctime_to_date_str) -> list[dict]:
    """
    将 CZS 列表序列化为前端矩形框数据。

    中枢框右端的确定规则（符合缠论原文）：
      - bi_out 存在（中枢已被突破）→ 框右端 = bi_out 结束时刻
        （出中枢那一笔结束即代表"已离开中枢"，三买/三卖是其后的确认信号，
         不属于中枢本体范围，框不应延伸到三买/三卖那根笔）
      - bi_out 不存在（中枢仍在延伸）→ 框右端 = 最后一根在中枢内的笔结束时刻
        （价格尚未正式离开，用最新边界）
    """
    result = []
    for zs in zs_list:
        try:
            begin_date = _format_time(zs.begin.time, ctime_to_date_str)

            # 优先使用 bi_out 的结束时刻：出中枢那一笔完成，中枢才算"结案"
            if zs.bi_out is not None:
                end_klu  = zs.bi_out.get_end_klu()
                end_date = _format_time(end_klu.time, ctime_to_date_str)
                # bi_out 时刻可能超出当前数据切片映射范围（末端实时笔），
                # 此时回退到 zs.end（最后内部笔）
                if not end_date:
                    end_date = _format_time(zs.end.time, ctime_to_date_str)
            else:
                end_date = _format_time(zs.end.time, ctime_to_date_str)

            if not begin_date or not end_date:
                continue

            result.append({
                "begin_date": begin_date,
                "end_date":   end_date,
                "zg":  round(zs.high, 4),
                "zd":  round(zs.low, 4),
                "gg":  round(zs.peak_high, 4),
                "dd":  round(zs.peak_low, 4),
            })
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# 核心解析流程（同步，在线程池中运行）
# ---------------------------------------------------------------------------

PERIOD_MAP = {
    # 纯数字格式（kline_lake / 前端 / chan_service 使用）
    "1":   KL_TYPE.K_1M,
    "5":   KL_TYPE.K_5M,
    "15":  KL_TYPE.K_15M,
    "30":  KL_TYPE.K_30M,
    "60":  KL_TYPE.K_60M,
    "day": KL_TYPE.K_DAY,
    "week": KL_TYPE.K_WEEK,
    # XM 格式（兼容）
    "1M":  KL_TYPE.K_1M,
    "5M":  KL_TYPE.K_5M,
    "15M": KL_TYPE.K_15M,
    "30M": KL_TYPE.K_30M,
    "60M": KL_TYPE.K_60M,
    "W":   KL_TYPE.K_WEEK,
}

def _parse_chan_detail_sync(
    symbol: str,
    freq: str,
    count: int,
    end_date: Optional[str] = None,
    cchan_preset: str = "live_tolerant",
    kline_source: Optional[str] = None,
    adjustflag: str = "2",
    max_compute_bars: Optional[int] = None,
) -> dict:
    """
    同步版本的缠论结构解析。调用方应通过 run_in_threadpool 包装。
    """
    # V6 升级：后端强制提取 5000 根历史，保证线段和均线预热
    COMPUTATION_COUNT = max_compute_bars or 5000

    # 1. 读取 K 线（优先本地缓存）
    rows = query_klines(
        symbol,
        freq,
        end_date=end_date,
        limit=max(count, COMPUTATION_COUNT),
        source=kline_source,
        adjustflag=adjustflag,
    )

    if len(rows) < _MIN_KLINES:
        logger.info("本地数据不足 %s/%s，触发 BaoStock 快速拉取...", symbol, freq)
        try:
            fetch_klines_quick(symbol, freq)
            rows = query_klines(
                symbol,
                freq,
                end_date=end_date,
                limit=max(count, COMPUTATION_COUNT),
                source=kline_source,
                adjustflag=adjustflag,
            )
        except Exception as e:
            logger.warning("BaoStock 拉取失败: %s", e)

    if not rows:
        return {"error": f"无可用 K 线数据: {symbol}/{freq}"}
    
    # ─── 周线实时补全：用日线合成本周未完成的周K ───
    if freq == "week" and rows:
        last_week_date = str(rows[-1]["date"]).split(" ")[0]
        try:
            daily_rows = query_klines(symbol, "day", limit=10)
            if daily_rows:
                # 找出比最后一根完整周K更新的日线
                current_week_days = [
                    r for r in daily_rows
                    if str(r["date"]).split(" ")[0] > last_week_date
                ]
                if current_week_days:
                    synth = {
                        "date": str(current_week_days[-1]["date"]),
                        "open": float(current_week_days[0]["open"]),
                        "high": max(float(r["high"]) for r in current_week_days),
                        "low": min(float(r["low"]) for r in current_week_days),
                        "close": float(current_week_days[-1]["close"]),
                        "volume": sum(float(r["volume"]) for r in current_week_days),
                    }
                    rows.append(synth)
                    logger.debug("周线补全: 用 %d 根日线合成本周K线 %s", len(current_week_days), synth["date"])
        except Exception as e:
            logger.warning("周线补全失败: %s", e)
        
    kl_type = PERIOD_MAP.get(freq, KL_TYPE.K_DAY)
    
    units = []
    ctime_to_date_str = {}  # 原样保留 CTime 指向具体的前端 Date 字符串映射

    for r in rows:
        dt_str = str(r["date"])
        if " " in dt_str:
            date_part, time_part = dt_str.split(" ", 1)
        else:
            date_part, time_part = dt_str, "09:30:00"

        ymd = date_part.split("-")
        hms = time_part.split(":")
        
        try:
            year = int(ymd[0])
            month = int(ymd[1])
            day = int(ymd[2])
            hour = int(hms[0])
            minute = int(hms[1])
        except Exception:
            continue

        ctime = CTime(year, month, day, hour, minute)
        ctime_key = f"{year}-{month}-{day}-{hour}-{minute}"
        ctime_to_date_str[ctime_key] = dt_str
        
        item_dict = {
            DATA_FIELD.FIELD_TIME: ctime,
            DATA_FIELD.FIELD_OPEN: float(r["open"]),
            DATA_FIELD.FIELD_HIGH: float(r["high"]),
            DATA_FIELD.FIELD_LOW: float(r["low"]),
            DATA_FIELD.FIELD_CLOSE: float(r["close"]),
            DATA_FIELD.FIELD_VOLUME: float(r["volume"]),
        }
        units.append(CKLine_Unit(item_dict))

    # 3. 载入官方原生 CChan 引擎计算
    # bi_fx_check 说明：
    #   "strict" / "totally" - 验证分型三根K线 + 前后相邻K线，要求最高
    #   "half"               - 验证分型K线 + 单侧一根相邻K线（原默认）
    #   "loss"               - 只验证分型K线本身，最宽松，最接近缠论原文
    #
    # 选用 "loss" 的原因：
    #   涨停板封板时多根30分一字K线（O=H=L=C）合并后，相邻 CKLine 的
    #   high/low 数据异常（涨停合并K线 high=low=limit_price），导致 "half"
    #   模式扩展到邻近K线后条件无法满足，合法的顶/底分型被错误拒绝。
    #   "loss" 模式只检验分型K线本身的高低关系，完全对应缠论原文定义，
    #   消除涨停板导致的超大笔（如 8.70→11.90 应为三笔却被识别为一笔）。
    config_meta = get_chan_config_meta(cchan_preset)
    config = CChanConfig(get_chan_config_dict(cchan_preset))

    chan = CChan(
        code=symbol,
        data_src=DATA_SRC.CUSTOM,
        lv_list=[kl_type],
        config=config,
        autype=AUTYPE.QFQ,
    )
    
    # 将K线喂入并计算分段、中枢
    chan.trigger_load({kl_type: units})
    chan.kl_datas[kl_type].cal_seg_and_zs()

    kl_data = chan.kl_datas[kl_type]

    bi_list = kl_data.bi_list
    seg_list = getattr(kl_data, "seg_list", [])
    zs_list = kl_data.zs_list
    segzs_list = getattr(kl_data, "segzs_list", [])

    # 提取买卖点（cal_seg_and_zs 已在上方调用，bs_point_lst 已填充）
    try:
        bi_bsps  = kl_data.bs_point_lst.getSortedBspList()
        seg_bsps = kl_data.seg_bs_point_lst.getSortedBspList()
        # 按 klu.idx 去重合并（同一根K线上笔和段买卖点取一个）
        seen_idx: set = set()
        all_bsps = []
        for bsp in sorted(list(bi_bsps) + list(seg_bsps), key=lambda b: b.klu.idx):
            if bsp.klu.idx not in seen_idx:
                seen_idx.add(bsp.klu.idx)
                all_bsps.append(bsp)
    except Exception as e:
        logger.warning("BSP 提取失败: %s", e)
        all_bsps = []

    # 4. MACD 计算和索引 (供 KinematicsAnalyzer 面积使用)
    closes = [r["close"] for r in rows]
    macd   = _compute_macd(closes)
    
    klines_out = []
    for r in rows:
        klines_out.append({
            "time":   r["date"],
            "open":   r["open"],
            "high":   r["high"],
            "low":    r["low"],
            "close":  r["close"],
            "volume": r["volume"],
        })

    dates = [r["date"] for r in rows]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    # 5. 进行兼容性结构序列化
    serialized_bis  = _serialize_bis(bi_list, ctime_to_date_str, macd, date_to_idx)
    serialized_segs = _serialize_segs(seg_list, ctime_to_date_str, macd, date_to_idx)
    serialized_bi_zs  = _serialize_zhongshus(zs_list, ctime_to_date_str)
    serialized_seg_zs = _serialize_zhongshus(segzs_list, ctime_to_date_str)
    serialized_bsps   = _serialize_bsps(all_bsps, ctime_to_date_str)

    # 6. V6 升级：根据前端请求的 count 进行数据截断，确保响应速度
    slice_count = count if count > 0 else len(rows)
    klines_out_sliced = klines_out[-slice_count:]
    macd_sliced = {
        "dif":   macd["dif"][-slice_count:],
        "dea":   macd["dea"][-slice_count:],
        "hist":  macd["hist"][-slice_count:],
        "dates": dates[-slice_count:],
    }

    if klines_out_sliced:
        cutoff_date = klines_out_sliced[0]["time"]
        serialized_bis    = [b for b in serialized_bis    if b["x1"]   >= cutoff_date]
        serialized_segs   = [s for s in serialized_segs   if s["x1"]   >= cutoff_date]
        serialized_bi_zs  = [z for z in serialized_bi_zs  if z["end_date"] >= cutoff_date]
        serialized_seg_zs = [z for z in serialized_seg_zs if z["end_date"] >= cutoff_date]
        serialized_bsps   = [p for p in serialized_bsps   if p["time"] >= cutoff_date]

    return {
        "symbol":     symbol,
        "freq":       freq,
        "klines":     klines_out_sliced,
        "bis":        serialized_bis,
        "segs":       serialized_segs,
        "bi_zhongshus":        serialized_bi_zs,
        "bi_zhongshus_decomp": serialized_bi_zs,  # 原版内部直接生成最好质量的中枢
        "seg_zhongshus":       serialized_seg_zs,
        "zhongshus":  serialized_bi_zs,  # 后向兼容
        "bsps":       serialized_bsps,
        "macd":       macd_sliced,
        "config":     config_meta,
        "stats": {
            "kline_count":        len(klines_out_sliced),
            "bi_count":           len(serialized_bis),
            "seg_count":          len(serialized_segs),
            "bi_zs_count":        len(serialized_bi_zs),
            "bi_zs_decomp_count": len(serialized_bi_zs),
            "seg_zs_count":       len(serialized_seg_zs),
            "bsp_count":          len(serialized_bsps),
            "computation_klines": len(rows),
        }
    }


# ---------------------------------------------------------------------------
# 异步公共接口
# ---------------------------------------------------------------------------

async def get_chan_detail(
    symbol: str,
    freq: str = "day",
    count: int = 500,
    end_date: Optional[str] = None,
    cchan_preset: str = "live_tolerant",
    kline_source: Optional[str] = None,
    adjustflag: str = "2",
    max_compute_bars: Optional[int] = None,
) -> dict:
    """
    异步版本，供 FastAPI 路由调用。
    通过 run_in_threadpool 将 CPU 密集型计算偏移到线程池，
    确保不阻塞 asyncio event loop。
    """
    # ★ 统一 symbol 格式：sh600519 → sh.600519（BaoStock/kline_lake 要求）
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    return await run_in_threadpool(
        _parse_chan_detail_sync,
        symbol_bs,
        freq,
        count,
        end_date,
        cchan_preset,
        kline_source,
        adjustflag,
        max_compute_bars,
    )


# ---------------------------------------------------------------------------
# 多级别联动解析（第一阶段：真实级别关系）
# ---------------------------------------------------------------------------

# 级别优先级（从高到低，CChan 要求顺序）
_LEVEL_ORDER = {
    KL_TYPE.K_WEEK:  0,
    KL_TYPE.K_DAY:   1,
    KL_TYPE.K_60M:   2,
    KL_TYPE.K_30M:   3,
    KL_TYPE.K_15M:   4,
    KL_TYPE.K_5M:    5,
    KL_TYPE.K_1M:    6,
}


def _build_kline_units(symbol: str, freq: str,
                       count: int = 5000) -> tuple[list, dict, list]:
    """从数据湖读取 K 线，返回 (units, ctime_to_date_str, rows)。"""
    rows = query_klines(symbol, freq, limit=max(count, 5000))
    if len(rows) < _MIN_KLINES:
        try:
            fetch_klines_quick(symbol, freq)
            rows = query_klines(symbol, freq, limit=max(count, 5000))
        except Exception as e:
            logger.warning("BaoStock 拉取失败 %s/%s: %s", symbol, freq, e)

    units = []
    ctime_to_date_str = {}
    for r in rows:
        dt_str = str(r["date"])
        date_part, time_part = (dt_str.split(" ", 1) if " " in dt_str
                                else (dt_str, "09:30:00"))
        ymd = date_part.split("-")
        hms = time_part.split(":")
        try:
            y, m, d = int(ymd[0]), int(ymd[1]), int(ymd[2])
            h, mi = int(hms[0]), int(hms[1])
            ctime = CTime(y, m, d, h, mi)
            key = f"{y}-{m}-{d}-{h}-{mi}"
            ctime_to_date_str[key] = dt_str
            units.append(CKLine_Unit({
                DATA_FIELD.FIELD_TIME:   ctime,
                DATA_FIELD.FIELD_OPEN:   float(r["open"]),
                DATA_FIELD.FIELD_HIGH:   float(r["high"]),
                DATA_FIELD.FIELD_LOW:    float(r["low"]),
                DATA_FIELD.FIELD_CLOSE:  float(r["close"]),
                DATA_FIELD.FIELD_VOLUME: float(r["volume"]),
            }))
        except Exception:
            continue
    return units, ctime_to_date_str, rows


def _serialize_one_level(kl_data, ctime_to_date_str: dict,
                         rows: list, freq: str, count: int) -> dict:
    """序列化单个级别数据（与 _parse_chan_detail_sync 后半段等价）。"""
    bi_list    = kl_data.bi_list
    seg_list   = getattr(kl_data, "seg_list", [])
    zs_list    = kl_data.zs_list
    segzs_list = getattr(kl_data, "segzs_list", [])

    closes    = [r["close"] for r in rows]
    macd      = _compute_macd(closes)
    dates     = [r["date"] for r in rows]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    klines_out = [{"time": r["date"], "open": r["open"], "high": r["high"],
                   "low": r["low"], "close": r["close"], "volume": r["volume"]}
                  for r in rows]

    s_bis    = _serialize_bis(bi_list, ctime_to_date_str, macd, date_to_idx)
    s_segs   = _serialize_segs(seg_list, ctime_to_date_str, macd, date_to_idx)
    s_bi_zs  = _serialize_zhongshus(zs_list, ctime_to_date_str)
    s_seg_zs = _serialize_zhongshus(segzs_list, ctime_to_date_str)

    # 提取买卖点
    try:
        bi_bsps  = kl_data.bs_point_lst.getSortedBspList()
        seg_bsps = kl_data.seg_bs_point_lst.getSortedBspList()
        seen_idx: set = set()
        all_bsps = []
        for bsp in sorted(list(bi_bsps) + list(seg_bsps), key=lambda b: b.klu.idx):
            if bsp.klu.idx not in seen_idx:
                seen_idx.add(bsp.klu.idx)
                all_bsps.append(bsp)
    except Exception as e:
        logger.warning("BSP 提取失败 (multi-level) %s: %s", freq, e)
        all_bsps = []
    s_bsps = _serialize_bsps(all_bsps, ctime_to_date_str)

    slice_count = count if count > 0 else len(rows)
    klines_sliced = klines_out[-slice_count:]
    macd_sliced = {
        "dif":   macd["dif"][-slice_count:],
        "dea":   macd["dea"][-slice_count:],
        "hist":  macd["hist"][-slice_count:],
        "dates": dates[-slice_count:],
    }

    if klines_sliced:
        cutoff = klines_sliced[0]["time"]
        s_bis   = [b for b in s_bis   if b["x1"]       >= cutoff]
        s_segs  = [s for s in s_segs  if s["x1"]       >= cutoff]
        s_bi_zs = [z for z in s_bi_zs if z["end_date"] >= cutoff]
        s_seg_zs = [z for z in s_seg_zs if z["end_date"] >= cutoff]
        s_bsps  = [p for p in s_bsps  if p["time"]     >= cutoff]

    return {
        "freq":                freq,
        "klines":              klines_sliced,
        "bis":                 s_bis,
        "segs":                s_segs,
        "bi_zhongshus":        s_bi_zs,
        "bi_zhongshus_decomp": s_bi_zs,
        "seg_zhongshus":       s_seg_zs,
        "zhongshus":           s_bi_zs,
        "bsps":                s_bsps,
        "macd":                macd_sliced,
        "stats": {
            "kline_count":        len(klines_sliced),
            "bi_count":           len(s_bis),
            "seg_count":          len(s_segs),
            "bi_zs_count":        len(s_bi_zs),
            "seg_zs_count":       len(s_seg_zs),
            "bsp_count":          len(s_bsps),
            "computation_klines": len(rows),
        },
    }


def _extract_level_relations(details: dict[str, dict]) -> dict:
    """
    基于时间区间推断笔层面的跨级别归属关系。

    思路：高级别的每一笔（bi）定义了一个时间区间 [x0, x1]；
    低级别在此区间内的所有笔都"属于"这根高级别笔。
    这是缠论中"高级别笔 = 低级别完整走势"的近似实现。

    返回:
        {
          "m30_in_day": [{"m30_bi_idx": i, "day_bi_idx": j}, ...],
          "m5_in_m30":  [{"m5_bi_idx":  i, "m30_bi_idx": j}, ...],
          "m5_in_day":  [{"m5_bi_idx":  i, "day_bi_idx": j}, ...],
        }
    """
    relations = {}
    freq_pairs = []

    # 自动推断哪些级别要做包含关系
    available = list(details.keys())
    level_priority = {"week": 0, "day": 1, "m60": 2, "m30": 3, "m15": 4, "m5": 5}
    available_sorted = sorted(available, key=lambda x: level_priority.get(x, 99))

    for i in range(len(available_sorted) - 1):
        high_lv = available_sorted[i]
        low_lv  = available_sorted[i + 1]
        freq_pairs.append((high_lv, low_lv))

    for high_lv, low_lv in freq_pairs:
        high_bis = details[high_lv].get("bis", [])
        low_bis  = details[low_lv].get("bis", [])

        if not high_bis or not low_bis:
            continue

        key = f"{low_lv}_in_{high_lv}"
        mapping = []
        for li, lb in enumerate(low_bis):
            lb_start = lb.get("x0", "")
            lb_end   = lb.get("x1", "")
            # 找到包含此低级别笔时间区间的高级别笔
            for hi, hb in enumerate(high_bis):
                hb_start = hb.get("x0", "")
                hb_end   = hb.get("x1", "")
                if hb_start <= lb_start and lb_end <= hb_end:
                    mapping.append({
                        f"{low_lv}_bi_idx":  li,
                        f"{high_lv}_bi_idx": hi,
                        "parent_is_up": hb.get("is_up"),  # 高级别笔方向
                    })
                    break  # 一根低级别笔只属于一根高级别笔
        relations[key] = mapping

    return relations


def _parse_chan_multi_level_sync(
    symbol: str,
    system_freqs: list[str],   # 例如 ["day", "30", "5"]（从高到低）
    count: int = 800,
) -> dict:
    """
    多级别联动解析：一次 CChan 调用同时处理所有级别，
    获得真实的级别包含关系（parent K-line ↔ child K-line）。

    相较于分别调用 _parse_chan_detail_sync 5 次，此函数：
    1. CChan 内部自动建立 K 线的 parent-child 链接
    2. 级别包含关系通过 _extract_level_relations 在笔层面计算
    3. 各级别的分析逻辑（笔/中枢/背驰）保持不变

    Returns:
        {
          "day": {serialized level data},
          "30":  {serialized level data},
          "5":   {serialized level data},
          "_level_relations": {crossing-level bi containment},
        }
    """
    # ── 1. 读取各级别 K 线数据 ──
    freq_map = {
        "day": "day", "week": "week",
        "m60": "60", "m30": "30", "m15": "15", "m5": "5",
        # 兼容数字格式
        "60": "60", "30": "30", "15": "15", "5": "5",
    }
    kl_type_list = []
    units_per_level   = {}   # KL_TYPE → units
    ctime_maps        = {}   # freq_str → ctime_to_date_str
    rows_per_level    = {}   # freq_str → rows

    for freq in system_freqs:
        raw_freq = freq_map.get(freq, freq)          # "m30" → "30", "day" → "day"
        kl_type  = PERIOD_MAP.get(raw_freq, KL_TYPE.K_DAY)
        if kl_type in units_per_level:
            continue  # 避免重复

        units, ctime_map, rows = _build_kline_units(symbol, raw_freq, count=count)
        if not units:
            logger.warning("多级别解析：%s/%s 无数据", symbol, raw_freq)
            continue

        kl_type_list.append(kl_type)
        units_per_level[kl_type]  = units
        ctime_maps[freq]          = ctime_map
        rows_per_level[freq]      = rows

    if not kl_type_list:
        return {"error": f"无可用K线数据: {symbol}"}

    # 按级别从高到低排序（CChan 要求）
    kl_type_list.sort(key=lambda x: _LEVEL_ORDER.get(x, 99))

    # ── 2. 单次 CChan 调用（多级别联动）──
    config = CChanConfig(get_chan_config_dict("live_tolerant"))

    chan = CChan(
        code=symbol,
        data_src=DATA_SRC.CUSTOM,
        lv_list=kl_type_list,       # ← 多个级别同时传入，建立真实层级关系
        config=config,
        autype=AUTYPE.QFQ,
    )

    chan.trigger_load(units_per_level)

    # ── 3. 各级别独立后处理（cal_seg_and_zs）+ 序列化 ──
    result = {}
    freq_to_kl_type = {freq_map.get(f, f): PERIOD_MAP.get(freq_map.get(f, f), KL_TYPE.K_DAY)
                       for f in system_freqs}

    for freq in system_freqs:
        raw_freq = freq_map.get(freq, freq)
        kl_type  = PERIOD_MAP.get(raw_freq, KL_TYPE.K_DAY)
        if kl_type not in chan.kl_datas:
            result[freq] = {"error": f"CChan 未生成 {freq} 级别数据"}
            continue

        kl_data = chan.kl_datas[kl_type]
        try:
            kl_data.cal_seg_and_zs()
        except Exception as e:
            logger.warning("cal_seg_and_zs 失败 %s/%s: %s", symbol, freq, e)

        rows = rows_per_level.get(freq, [])
        ctime_map = ctime_maps.get(freq, {})

        if not rows:
            result[freq] = {"error": f"无行数据 {freq}"}
            continue

        try:
            level_data = _serialize_one_level(kl_data, ctime_map, rows, raw_freq, count)
            level_data["symbol"] = symbol
            result[freq] = level_data
        except Exception as e:
            logger.error("序列化失败 %s/%s: %s", symbol, freq, e)
            result[freq] = {"error": str(e)}

    # ── 4. 跨级别 bi 包含关系 ──
    result["_level_relations"] = _extract_level_relations(result)

    return result


async def get_chan_multi_level(
    symbol: str,
    system_freqs: list[str],
    count: int = 800,
) -> dict:
    """
    异步多级别联动接口。

    Args:
        symbol:       股票代码（sh600519 或 sh.600519）
        system_freqs: 级别列表，从高到低，如 ["day", "m30", "m5"]
        count:        每级别返回的 K 线根数（计算始终用 5000 根）

    Returns:
        {freq: {klines, bis, segs, ...}, "_level_relations": {...}}
    """
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    return await run_in_threadpool(
        _parse_chan_multi_level_sync, symbol_bs, system_freqs, count
    )
