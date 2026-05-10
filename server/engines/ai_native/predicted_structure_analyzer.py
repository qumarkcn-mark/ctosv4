"""P1: Run chan.py analysis on Kronos predicted K-lines.

将 Kronos 预测序列当作"未来的 K 线"，用缠论引擎识别其中的
笔（bi）、线段（seg）、中枢（zhongshu）、分型（fenxing）候选。

输出是概率性的——预测 K 线本身不确定，但结构形态能直接回答
"未来大概在哪里出现买卖点"，为 AI Fusion 提供结构化预测证据。

设计原则：
- 轻量独立模块，只依赖 chan.py vendor 和 fusion_schemas
- 预测序列通常只有 5-15 根 K 线，远少于正式结构分析的 800 根
- 输出用 Pydantic schema，方便序列化进 FusionInputBundle
- 失败时返回空结构 + warnings，绝不阻塞 Fusion 主流程
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

from server.engines.ai_native.fusion_schemas import KronosForecastPoint, KronosForecastResult

logger = logging.getLogger(__name__)

# ── chan.py vendor import ──────────────────────────────────────────
_VENDOR_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "vendor", "chan_py")
)
if _VENDOR_ROOT not in sys.path:
    sys.path.insert(0, _VENDOR_ROOT)

try:
    from Chan import CChan
    from ChanConfig import CChanConfig
    from Common.CEnum import AUTYPE, DATA_SRC, DATA_FIELD, KL_TYPE
    from Common.CTime import CTime
    from KLine.KLine_Unit import CKLine_Unit

    _CHAN_AVAILABLE = True
except ImportError as exc:
    logger.warning("predicted_structure_analyzer: chan.py unavailable: %s", exc)
    _CHAN_AVAILABLE = False

# ── 最少需要的 K 线数量 ──────────────────────────────────────────
# 缠论至少需要 3 根 K 线才能形成分型，5 根才可能形成一笔
_MIN_BARS_FOR_FENXING = 3
_MIN_BARS_FOR_BI = 5


# ── Output schemas ─────────────────────────────────────────────────

class PredictedFenxing(BaseModel):
    """预测序列中的分型候选。"""
    step: int = Field(description="在预测序列中的位置（1-based）")
    timestamp: str = ""
    type: str = Field(description="TOP / BOTTOM")
    price: float = Field(description="顶分型取 high，底分型取 low")
    confidence_note: str = "预测 K 线衍生，仅供参考"


class PredictedBi(BaseModel):
    """预测序列中的笔候选。"""
    begin_step: int
    end_step: int
    begin_timestamp: str = ""
    end_timestamp: str = ""
    begin_price: float
    end_price: float
    direction: str = Field(description="UP / DOWN")
    is_sure: bool = False
    bar_count: int = 0
    confidence_note: str = "预测 K 线衍生，仅供参考"


class PredictedZhongshu(BaseModel):
    """预测序列中的中枢候选。"""
    begin_step: int
    end_step: int
    zg: float = Field(description="中枢上沿")
    zd: float = Field(description="中枢下沿")
    gg: float = Field(description="中枢最高")
    dd: float = Field(description="中枢最低")
    bi_count: int = 0
    confidence_note: str = "预测 K 线衍生，仅供参考"


class PredictedChanStructure(BaseModel):
    """Kronos 预测序列的缠论结构分析结果。"""
    version: str = "predicted_chan_structure.v1"
    source: str = "kronos_forecast_mean"
    bar_count: int = 0
    fenxings: list[PredictedFenxing] = Field(default_factory=list)
    bis: list[PredictedBi] = Field(default_factory=list)
    zhongshus: list[PredictedZhongshu] = Field(default_factory=list)
    trend_summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    note: str = "基于 Kronos 预测 K 线的缠论结构分析，所有结构均为概率性候选，不是正式结构。仅供参考，不构成投资建议。"


# ── Core analysis ──────────────────────────────────────────────────

def analyze_predicted_structure(
    kronos_forecast: KronosForecastResult,
    *,
    cchan_preset: str = "live_tolerant",
) -> PredictedChanStructure:
    """对 Kronos 预测序列跑缠论分析。

    失败时返回带 warnings 的空结构，绝不抛异常。
    """
    points = kronos_forecast.forecast_mean
    if not points:
        return PredictedChanStructure(
            warnings=["no_forecast_points"],
            trend_summary="预测序列为空，无法分析结构。",
        )

    bar_count = len(points)
    if bar_count < _MIN_BARS_FOR_FENXING:
        return PredictedChanStructure(
            bar_count=bar_count,
            warnings=["insufficient_bars_for_fenxing"],
            trend_summary=f"预测序列仅 {bar_count} 根，不足以形成分型。",
        )

    if not _CHAN_AVAILABLE:
        # chan.py 不可用时，用简单逻辑做分型检测
        return _fallback_simple_analysis(points)

    try:
        return _run_chan_analysis(points, kronos_forecast.symbol, cchan_preset)
    except Exception as exc:
        logger.warning("predicted_structure_analyzer chan.py failed: %s", exc, exc_info=True)
        # 降级到简单分析
        result = _fallback_simple_analysis(points)
        result.warnings.append(f"chan_engine_error: {str(exc)[:100]}")
        return result


def _run_chan_analysis(
    points: list[KronosForecastPoint],
    symbol: str,
    cchan_preset: str,
) -> PredictedChanStructure:
    """用 chan.py 引擎分析预测序列。"""
    from server.engines.structure.chan_config_presets import get_chan_config_dict

    rows = _forecast_points_to_rows(points)
    units, ctime_to_date_str = _rows_to_units(rows)

    if len(units) < _MIN_BARS_FOR_FENXING:
        return PredictedChanStructure(
            bar_count=len(points),
            warnings=["insufficient_valid_units"],
            trend_summary="有效预测 K 线不足，无法形成分型。",
        )

    # 跑 chan.py
    kl_type = KL_TYPE.K_DAY
    config = CChanConfig(get_chan_config_dict(cchan_preset))
    chan = CChan(
        code=symbol or "predicted",
        data_src=DATA_SRC.CUSTOM,
        lv_list=[kl_type],
        config=config,
        autype=AUTYPE.QFQ,
    )
    chan.trigger_load({kl_type: units})
    kl_data = chan.kl_datas[kl_type]
    kl_data.cal_seg_and_zs()

    # 提取结构
    bi_list = kl_data.bi_list
    seg_list = getattr(kl_data, "seg_list", [])
    zs_list = kl_data.zs_list

    # 序列化
    fenxings = _extract_fenxings_from_points(points)
    bis = _serialize_predicted_bis(bi_list, ctime_to_date_str, points)
    zhongshus = _serialize_predicted_zhongshus(zs_list, ctime_to_date_str, points)

    # 如果 chan.py 跑出了 bi 但我们的简单分型检测没发现足够分型，
    # 用 bi 的端点补充分型信息
    if bis and len(fenxings) < 2:
        fenxings = _fenxings_from_bis(bis)

    warnings = []
    if len(points) < _MIN_BARS_FOR_BI and not bis:
        warnings.append("too_few_bars_for_bi")
    if not bis and len(points) >= _MIN_BARS_FOR_BI:
        warnings.append("no_bi_detected_in_predicted_sequence")

    trend_summary = _build_trend_summary(points, fenxings, bis, zhongshus)

    return PredictedChanStructure(
        bar_count=len(points),
        fenxings=fenxings,
        bis=bis,
        zhongshus=zhongshus,
        trend_summary=trend_summary,
        warnings=warnings,
    )


# ── Helpers: convert forecast points to chan.py input ───────────────

def _forecast_points_to_rows(points: list[KronosForecastPoint]) -> list[dict]:
    """把 KronosForecastPoint 列表转换为 chan_adapter 兼容的 row 格式。"""
    rows = []
    for point in points:
        if point.close is None or point.high is None or point.low is None:
            continue
        # 预测序列可能没有精确时间戳，用 step 生成伪日期
        timestamp = point.timestamp or f"2099-01-{point.step:02d}"
        rows.append({
            "date": timestamp,
            "open": point.open if point.open is not None else point.close,
            "high": point.high,
            "low": point.low,
            "close": point.close,
            "volume": point.volume if point.volume is not None else 0.0,
        })
    return rows


def _rows_to_units(rows: list[dict]) -> tuple[list, dict]:
    """复用 chan_adapter 的逻辑，把 row dicts 转成 CKLine_Unit。"""
    units = []
    ctime_to_date_str = {}
    for row in rows:
        dt_str = str(row.get("date", ""))
        if not dt_str:
            continue
        date_part, time_part = (
            dt_str.split(" ", 1) if " " in dt_str else (dt_str, "09:30:00")
        )
        try:
            ymd = date_part.split("-")
            hms = time_part.split(":")
            year, month, day = int(ymd[0]), int(ymd[1]), int(ymd[2])
            hour, minute = int(hms[0]), int(hms[1])
            ctime = CTime(year, month, day, hour, minute)
            ctime_key = f"{year}-{month}-{day}-{hour}-{minute}"
            ctime_to_date_str[ctime_key] = dt_str
            units.append(
                CKLine_Unit(
                    {
                        DATA_FIELD.FIELD_TIME: ctime,
                        DATA_FIELD.FIELD_OPEN: float(row.get("open", 0)),
                        DATA_FIELD.FIELD_HIGH: float(row.get("high", 0)),
                        DATA_FIELD.FIELD_LOW: float(row.get("low", 0)),
                        DATA_FIELD.FIELD_CLOSE: float(row.get("close", 0)),
                        DATA_FIELD.FIELD_VOLUME: float(row.get("volume", 0)),
                    }
                )
            )
        except Exception:
            continue
    return units, ctime_to_date_str


# ── Helpers: extract structures ────────────────────────────────────

def _extract_fenxings_from_points(points: list[KronosForecastPoint]) -> list[PredictedFenxing]:
    """直接从 OHLCV 序列识别顶底分型候选。

    简单规则：三根 K 线，中间那根的 high 最高→顶分型，low 最低→底分型。
    """
    fenxings = []
    closes_and_highs = [
        (p.high or 0, p.low or 0, p.step, p.timestamp or "")
        for p in points
        if p.high is not None and p.low is not None
    ]
    for i in range(1, len(closes_and_highs) - 1):
        prev_h, prev_l, _, _ = closes_and_highs[i - 1]
        curr_h, curr_l, step, ts = closes_and_highs[i]
        next_h, next_l, _, _ = closes_and_highs[i + 1]

        # 顶分型：中间 high 最高且 low 最高
        if curr_h >= prev_h and curr_h >= next_h and curr_l >= prev_l and curr_l >= next_l:
            fenxings.append(PredictedFenxing(
                step=step, timestamp=ts, type="TOP", price=round(curr_h, 4),
            ))
        # 底分型：中间 low 最低且 high 最低
        elif curr_l <= prev_l and curr_l <= next_l and curr_h <= prev_h and curr_h <= next_h:
            fenxings.append(PredictedFenxing(
                step=step, timestamp=ts, type="BOTTOM", price=round(curr_l, 4),
            ))

    return fenxings


def _serialize_predicted_bis(bi_list, ctime_to_date_str: dict, points: list[KronosForecastPoint]) -> list[PredictedBi]:
    """把 chan.py 的 CBi 列表转成 PredictedBi。"""
    step_lookup = {(p.timestamp or ""): p.step for p in points}
    result = []
    for bi in bi_list:
        is_up = str(bi.dir).endswith("UP")
        begin_time = _format_ctime(bi.get_begin_klu().time, ctime_to_date_str)
        end_time = _format_ctime(bi.get_end_klu().time, ctime_to_date_str)
        begin_price = bi.get_begin_val()
        end_price = bi.get_end_val()

        begin_step = step_lookup.get(begin_time, 0) or _guess_step(begin_time, points)
        end_step = step_lookup.get(end_time, 0) or _guess_step(end_time, points)

        result.append(PredictedBi(
            begin_step=begin_step,
            end_step=end_step,
            begin_timestamp=begin_time,
            end_timestamp=end_time,
            begin_price=round(begin_price, 4),
            end_price=round(end_price, 4),
            direction="UP" if is_up else "DOWN",
            is_sure=bi.is_sure if hasattr(bi, "is_sure") else False,
            bar_count=max(0, end_step - begin_step + 1) if begin_step and end_step else 0,
        ))
    return result


def _serialize_predicted_zhongshus(zs_list, ctime_to_date_str: dict, points: list[KronosForecastPoint]) -> list[PredictedZhongshu]:
    """把 chan.py 的 CZS 列表转成 PredictedZhongshu。"""
    step_lookup = {(p.timestamp or ""): p.step for p in points}
    result = []
    for zs in zs_list:
        try:
            begin_time = _format_ctime(zs.begin.time, ctime_to_date_str)
            end_time = _format_ctime(zs.end.time, ctime_to_date_str)
            begin_step = step_lookup.get(begin_time, 0) or _guess_step(begin_time, points)
            end_step = step_lookup.get(end_time, 0) or _guess_step(end_time, points)

            result.append(PredictedZhongshu(
                begin_step=begin_step,
                end_step=end_step,
                zg=round(float(zs.zg), 4),
                zd=round(float(zs.zd), 4),
                gg=round(float(zs.gg), 4),
                dd=round(float(zs.dd), 4),
                bi_count=len(zs.bi_list) if hasattr(zs, "bi_list") else 0,
            ))
        except Exception:
            continue
    return result


def _fenxings_from_bis(bis: list[PredictedBi]) -> list[PredictedFenxing]:
    """从笔的端点反推分型。"""
    fenxings = []
    for bi in bis:
        if bi.direction == "DOWN":
            # 下行笔起点是顶分型，终点是底分型
            fenxings.append(PredictedFenxing(
                step=bi.begin_step, timestamp=bi.begin_timestamp,
                type="TOP", price=bi.begin_price,
            ))
            fenxings.append(PredictedFenxing(
                step=bi.end_step, timestamp=bi.end_timestamp,
                type="BOTTOM", price=bi.end_price,
            ))
        else:
            # 上行笔起点是底分型，终点是顶分型
            fenxings.append(PredictedFenxing(
                step=bi.begin_step, timestamp=bi.begin_timestamp,
                type="BOTTOM", price=bi.begin_price,
            ))
            fenxings.append(PredictedFenxing(
                step=bi.end_step, timestamp=bi.end_timestamp,
                type="TOP", price=bi.end_price,
            ))
    # 去重：同一 step 只保留一个
    seen = set()
    unique = []
    for f in fenxings:
        if f.step not in seen:
            seen.add(f.step)
            unique.append(f)
    return sorted(unique, key=lambda x: x.step)


def _format_ctime(ctime, ctime_to_date_str: dict) -> str:
    """CTime → 原始 date_str 映射。"""
    try:
        key = f"{ctime.year}-{ctime.month}-{ctime.day}-{ctime.hour}-{ctime.minute}"
        return ctime_to_date_str.get(key, "")
    except Exception:
        return ""


def _guess_step(timestamp: str, points: list[KronosForecastPoint]) -> int:
    """如果时间戳精确匹配失败，尝试按日期部分查找 step。"""
    if not timestamp:
        return 0
    date_part = timestamp.split(" ", 1)[0] if " " in timestamp else timestamp
    for p in points:
        p_date = (p.timestamp or "").split(" ", 1)[0] if " " in (p.timestamp or "") else (p.timestamp or "")
        if p_date == date_part:
            return p.step
    return 0


# ── Trend summary ──────────────────────────────────────────────────

def _build_trend_summary(
    points: list[KronosForecastPoint],
    fenxings: list[PredictedFenxing],
    bis: list[PredictedBi],
    zhongshus: list[PredictedZhongshu],
) -> str:
    """生成人类可读的预测结构摘要。"""
    parts = []
    n = len(points)
    parts.append(f"预测序列共 {n} 根 K 线")

    # 整体方向
    closes = [p.close for p in points if p.close is not None]
    if len(closes) >= 2:
        start_price = closes[0]
        end_price = closes[-1]
        change = (end_price - start_price) / start_price * 100 if start_price else 0
        if change > 0.5:
            parts.append(f"整体上行约 {change:.1f}%（{start_price:.2f}→{end_price:.2f}）")
        elif change < -0.5:
            parts.append(f"整体下行约 {abs(change):.1f}%（{start_price:.2f}→{end_price:.2f}）")
        else:
            parts.append(f"整体横盘（{start_price:.2f}→{end_price:.2f}）")

    # 分型
    tops = [f for f in fenxings if f.type == "TOP"]
    bottoms = [f for f in fenxings if f.type == "BOTTOM"]
    if tops or bottoms:
        fx_parts = []
        if tops:
            fx_parts.append(f"{len(tops)}个顶分型候选")
        if bottoms:
            fx_parts.append(f"{len(bottoms)}个底分型候选")
        parts.append("、".join(fx_parts))

    # 笔
    if bis:
        bi_desc = []
        for bi in bis:
            bi_desc.append(f"第{bi.begin_step}-{bi.end_step}根{'上行' if bi.direction == 'UP' else '下行'}笔（{bi.begin_price:.2f}→{bi.end_price:.2f}）")
        parts.append("笔结构：" + "，".join(bi_desc))

    # 中枢
    if zhongshus:
        for zs in zhongshus:
            parts.append(f"中枢候选（第{zs.begin_step}-{zs.end_step}根，区间{zs.zd:.2f}-{zs.zg:.2f}）")

    # 转折点
    if len(closes) >= 3:
        turning_steps = []
        for i in range(1, len(closes) - 1):
            if (closes[i] - closes[i - 1]) * (closes[i + 1] - closes[i]) < 0:
                turning_steps.append(i + 1)  # 1-based
        if turning_steps:
            parts.append(f"转折候选在第 {', '.join(str(s) for s in turning_steps[:3])} 根")

    return "；".join(parts) + "。"


# ── Fallback: simple analysis without chan.py ──────────────────────

def _fallback_simple_analysis(points: list[KronosForecastPoint]) -> PredictedChanStructure:
    """chan.py 不可用时的简单分型检测。"""
    fenxings = _extract_fenxings_from_points(points)
    bis = _simple_bis_from_fenxings(fenxings, points)
    trend_summary = _build_trend_summary(points, fenxings, bis, [])

    return PredictedChanStructure(
        bar_count=len(points),
        fenxings=fenxings,
        bis=bis,
        zhongshus=[],
        trend_summary=trend_summary,
        warnings=["chan_engine_unavailable_using_simple_analysis"],
    )


def _simple_bis_from_fenxings(fenxings: list[PredictedFenxing], points: list[KronosForecastPoint]) -> list[PredictedBi]:
    """从分型序列推导简单笔。相邻的顶底分型对构成一笔。"""
    if len(fenxings) < 2:
        return []

    # 确保顶底交替
    alternating = [fenxings[0]]
    for fx in fenxings[1:]:
        if fx.type != alternating[-1].type:
            alternating.append(fx)
        else:
            # 同类型取极值
            if fx.type == "TOP" and fx.price > alternating[-1].price:
                alternating[-1] = fx
            elif fx.type == "BOTTOM" and fx.price < alternating[-1].price:
                alternating[-1] = fx

    bis = []
    for i in range(len(alternating) - 1):
        f1, f2 = alternating[i], alternating[i + 1]
        direction = "DOWN" if f1.type == "TOP" else "UP"
        bis.append(PredictedBi(
            begin_step=f1.step,
            end_step=f2.step,
            begin_timestamp=f1.timestamp,
            end_timestamp=f2.timestamp,
            begin_price=f1.price,
            end_price=f2.price,
            direction=direction,
            is_sure=False,
            bar_count=max(0, f2.step - f1.step + 1),
            confidence_note="简单分型推导，非 chan.py 引擎，仅供参考",
        ))
    return bis
