"""Adapter boundary for Vespa314/chan.py.

本模块是 CT-OS 调用 chan.py vendor 的唯一目标入口。第一版先复用
`chan_detail_service` 的序列化函数，避免一次性搬动前端依赖的大字段。
后续迁移时，应把旧服务里的 CChan 构造逐步移到这里。
"""

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server.db.kline_lake import query_klines
from server.domain.symbols import normalize_symbol
from server.engines.structure.chan_config_presets import (
    get_chan_config_dict,
    get_chan_config_meta,
)
from server.engines.structure.derived_facts import check_interval_nesting, enrich_level
from server.engines.structure.structure_key import resolve_compute_bars
from server.services.baostock_service import fetch_klines_quick, fetch_klines_sync
from server.services.chan_detail_service import (
    PERIOD_MAP,
    _LEVEL_ORDER,
    _extract_level_relations,
    _serialize_one_level,
    resolve_chan_compute_bars,
)

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
    logging.getLogger(__name__).error("无法导入 chan.py vendor: %s", exc)
    _CHAN_AVAILABLE = False


logger = logging.getLogger(__name__)

ADAPTER_VERSION = "chan_adapter.v1"
STRUCTURE_SOURCE = "baostock"
STRUCTURE_ADJUSTFLAG = "2"
STRUCTURE_ENGINE = "chan.py"
_MIN_KLINES = 120
_MAX_LEVEL_LAG_DAYS = 0
_WEEK_LEVEL_MAX_LAG_DAYS = 7

_FREQ_ALIASES = {
    "week": "week",
    "w": "week",
    "day": "day",
    "d": "day",
    "60": "60",
    "m60": "60",
    "60m": "60",
    "30": "30",
    "m30": "30",
    "30m": "30",
    "15": "15",
    "m15": "15",
    "15m": "15",
    "5": "5",
    "m5": "5",
    "5m": "5",
    "1": "1",
    "m1": "1",
    "1m": "1",
}


@dataclass(frozen=True)
class LevelInput:
    level: str
    raw_freq: str
    compute_bars: int
    kl_type: object
    rows: list
    units: list
    ctime_to_date_str: dict


def analyze_structure_sync(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 800,
    cchan_preset: str = "live_tolerant",
    compute_profile: Optional[str] = None,
) -> dict:
    """Analyze formal Chan structure through chan.py in sync context."""
    canonical_symbol = normalize_symbol(symbol)
    requested_levels = levels or ["day", "30", "5"]

    if not _CHAN_AVAILABLE:
        return _error_result(
            canonical_symbol,
            requested_levels,
            "ENGINE_ERROR",
            "chan.py vendor is unavailable",
        )

    level_inputs = []
    for level in requested_levels:
        raw_freq = normalize_level(level)
        level_input = _load_level_input(canonical_symbol, raw_freq, count, compute_profile=compute_profile)
        if level_input is not None:
            level_inputs.append(level_input)

    level_inputs = _refresh_lagging_level_inputs(
        canonical_symbol,
        level_inputs,
        count,
        compute_profile=compute_profile,
    )

    if not level_inputs:
        return _error_result(
            canonical_symbol,
            requested_levels,
            "NO_DATA",
            "no usable kline data",
        )

    try:
        kl_data_by_type = _run_chan_py(canonical_symbol, level_inputs, cchan_preset)
    except Exception as exc:
        logger.exception("chan.py adapter failed for %s", canonical_symbol)
        return _error_result(
            canonical_symbol,
            requested_levels,
            "ENGINE_ERROR",
            str(exc),
        )

    serialized_levels = {}
    for item in level_inputs:
        if item.kl_type not in kl_data_by_type:
            serialized_levels[item.raw_freq] = {
                "level": public_level_name(item.raw_freq),
                "error": "LEVEL_INCOMPLETE",
            }
            continue

        kl_data = kl_data_by_type[item.kl_type]
        try:
            level_data = _serialize_one_level(
                kl_data,
                item.ctime_to_date_str,
                item.rows,
                item.raw_freq,
                count,
            )
            level_data["symbol"] = canonical_symbol
            level_data["level"] = public_level_name(item.raw_freq)
            level_data["source"] = _source_meta()
            level_data = enrich_level(level_data)
            serialized_levels[item.raw_freq] = level_data
        except Exception as exc:
            logger.warning(
                "chan.py adapter serialize failed %s/%s: %s",
                canonical_symbol,
                item.raw_freq,
                exc,
            )
            serialized_levels[item.raw_freq] = {
                "level": public_level_name(item.raw_freq),
                "error": "ENGINE_ERROR",
                "message": str(exc),
            }

    stale_reason = _stale_reason(serialized_levels, requested_levels, level_inputs)
    interval_nesting = _interval_nesting(serialized_levels)
    return {
        "adapter_version": ADAPTER_VERSION,
        "symbol": canonical_symbol,
        "data_source": {
            "structure": _source_meta(),
        },
        "structure_config": get_chan_config_meta(cchan_preset),
        "compute_profile": compute_profile or "legacy_default",
        "compute_bars_by_level": {item.raw_freq: item.compute_bars for item in level_inputs},
        "freshness": {
            "source": STRUCTURE_SOURCE,
            "adjustflag": STRUCTURE_ADJUSTFLAG,
            "last_bar_at": _last_bar_at(level_inputs),
            "is_stale": bool(stale_reason),
            "stale_reason": stale_reason,
            "levels": _level_freshness(level_inputs, serialized_levels),
        },
        "levels": serialized_levels,
        "level_relations": _extract_level_relations(serialized_levels),
        "interval_nesting": interval_nesting,
    }


async def analyze_structure(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 800,
    cchan_preset: str = "live_tolerant",
    compute_profile: Optional[str] = None,
) -> dict:
    """Async wrapper for FastAPI and workers."""
    return await run_in_threadpool(
        analyze_structure_sync,
        symbol,
        levels,
        count,
        cchan_preset,
        compute_profile,
    )


def normalize_level(level: str) -> str:
    raw = str(level).strip().lower()
    if raw not in _FREQ_ALIASES:
        raise ValueError(f"unsupported chan level: {level}")
    return _FREQ_ALIASES[raw]


def public_level_name(raw_freq: str) -> str:
    if raw_freq in ("day", "week"):
        return raw_freq
    return raw_freq


def _interval_nesting(levels: dict) -> dict:
    """从 adapter-derived 背驰字段计算两套 Radar 级别区间套。"""
    return {
        "short_term": check_interval_nesting(
            [levels.get("day"), levels.get("30"), levels.get("5")],
            level_names=["day", "30", "5"],
        ),
        "swing": check_interval_nesting(
            [levels.get("day"), levels.get("60"), levels.get("15")],
            level_names=["day", "60", "15"],
        ),
    }


def _load_level_input(
    symbol: str,
    raw_freq: str,
    count: int,
    *,
    compute_profile: Optional[str] = None,
) -> Optional[LevelInput]:
    if compute_profile:
        compute_bars = resolve_compute_bars(compute_profile, raw_freq, fallback=count)
    else:
        compute_bars = resolve_chan_compute_bars(raw_freq, count)
    rows = query_klines(symbol, raw_freq, limit=compute_bars)
    if len(rows) < _MIN_KLINES:
        try:
            fetch_klines_quick(symbol, raw_freq)
            rows = query_klines(symbol, raw_freq, limit=compute_bars)
        except Exception as exc:
            logger.warning("BaoStock 拉取失败 %s/%s: %s", symbol, raw_freq, exc)

    units, ctime_to_date_str = _rows_to_units(rows)
    if not units:
        return None

    return LevelInput(
        level=public_level_name(raw_freq),
        raw_freq=raw_freq,
        compute_bars=compute_bars,
        kl_type=PERIOD_MAP.get(raw_freq, KL_TYPE.K_DAY),
        rows=rows,
        units=units,
        ctime_to_date_str=ctime_to_date_str,
    )


def _refresh_lagging_level_inputs(
    symbol: str,
    level_inputs: list[LevelInput],
    count: int,
    *,
    compute_profile: Optional[str] = None,
) -> list[LevelInput]:
    if len(level_inputs) < 2:
        return level_inputs

    latest_day = max(
        (_date_part(_last_row_date(item)) for item in level_inputs if _last_row_date(item)),
        default="",
    )
    if not latest_day:
        return level_inputs

    refreshed = []
    for item in level_inputs:
        last_day = _date_part(_last_row_date(item))
        if _level_lag_days(last_day, latest_day) <= _max_lag_days_for_level(item.raw_freq):
            refreshed.append(item)
            continue

        try:
            logger.warning(
                "检测到结构级别数据滞后，尝试补齐: %s/%s last=%s latest=%s",
                symbol,
                item.raw_freq,
                last_day,
                latest_day,
            )
            fetch_klines_sync(symbol, item.raw_freq, start_date=last_day)
            reloaded = _load_level_input(symbol, item.raw_freq, count, compute_profile=compute_profile)
            refreshed.append(reloaded or item)
        except Exception as exc:
            logger.warning(
                "滞后级别补齐失败 %s/%s last=%s latest=%s: %s",
                symbol,
                item.raw_freq,
                last_day,
                latest_day,
                exc,
            )
            refreshed.append(item)

    return refreshed


def _rows_to_units(rows: list) -> tuple[list, dict]:
    units = []
    ctime_to_date_str = {}
    for row in rows:
        dt_str = str(_row_get(row, "date", ""))
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
                        DATA_FIELD.FIELD_OPEN: float(_row_get(row, "open", 0)),
                        DATA_FIELD.FIELD_HIGH: float(_row_get(row, "high", 0)),
                        DATA_FIELD.FIELD_LOW: float(_row_get(row, "low", 0)),
                        DATA_FIELD.FIELD_CLOSE: float(_row_get(row, "close", 0)),
                        DATA_FIELD.FIELD_VOLUME: float(_row_get(row, "volume", 0)),
                    }
                )
            )
        except Exception:
            continue
    return units, ctime_to_date_str


def _run_chan_py(symbol: str, level_inputs: list[LevelInput], cchan_preset: str) -> dict:
    kl_types = sorted(
        {item.kl_type for item in level_inputs},
        key=lambda item: _LEVEL_ORDER.get(item, 99),
    )
    units_per_level = {item.kl_type: item.units for item in level_inputs}

    config = CChanConfig(get_chan_config_dict(cchan_preset))
    chan = CChan(
        code=symbol,
        data_src=DATA_SRC.CUSTOM,
        lv_list=kl_types,
        config=config,
        autype=AUTYPE.QFQ,
    )
    chan.trigger_load(units_per_level)

    result = {}
    for kl_type in kl_types:
        kl_data = chan.kl_datas[kl_type]
        kl_data.cal_seg_and_zs()
        result[kl_type] = kl_data
    return result


def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        return getattr(row, key, default)


def _source_meta() -> dict:
    return {
        "provider": STRUCTURE_SOURCE,
        "adjustflag": STRUCTURE_ADJUSTFLAG,
        "engine": STRUCTURE_ENGINE,
        "adapter": "server.engines.structure.chan_adapter",
    }


def _last_bar_at(level_inputs: list[LevelInput]) -> str:
    last_values = []
    for item in level_inputs:
        if item.rows:
            last_values.append(str(_row_get(item.rows[-1], "date", "")))
    return max(last_values) if last_values else ""


def _level_freshness(level_inputs: list[LevelInput], serialized_levels: dict) -> dict:
    by_freq = {item.raw_freq: item for item in level_inputs}
    latest_day = max(
        (_date_part(_last_row_date(item)) for item in level_inputs if _last_row_date(item)),
        default="",
    )
    result = {}
    for raw_freq, level_data in serialized_levels.items():
        item = by_freq.get(raw_freq)
        last_bar_at = str(_row_get(item.rows[-1], "date", "")) if item and item.rows else ""
        is_lagging = _level_lag_days(_date_part(last_bar_at), latest_day) > _max_lag_days_for_level(raw_freq)
        is_stale = bool(level_data.get("error")) or is_lagging
        result[raw_freq] = {
            "last_bar_at": last_bar_at,
            "is_stale": is_stale,
            "stale_reason": level_data.get("error", "") or ("LEVEL_STALE" if is_lagging else ""),
        }
    return result


def _stale_reason(serialized_levels: dict, requested_levels: list[str], level_inputs: list[LevelInput]) -> str:
    if not serialized_levels:
        return "NO_DATA"

    normalized_requested = []
    for level in requested_levels:
        try:
            normalized_requested.append(normalize_level(level))
        except ValueError:
            return "LEVEL_INCOMPLETE"

    missing = [level for level in normalized_requested if level not in serialized_levels]
    if missing:
        return "LEVEL_INCOMPLETE"

    for level_data in serialized_levels.values():
        if level_data.get("error"):
            return level_data["error"]
    latest_day = max(
        (_date_part(_last_row_date(item)) for item in level_inputs if _last_row_date(item)),
        default="",
    )
    for item in level_inputs:
        if _level_lag_days(_date_part(_last_row_date(item)), latest_day) > _max_lag_days_for_level(item.raw_freq):
            return "LEVEL_STALE"
    return ""


def _last_row_date(item: LevelInput) -> str:
    if not item or not item.rows:
        return ""
    return str(_row_get(item.rows[-1], "date", ""))


def _date_part(value: str) -> str:
    return str(value or "").split(" ", 1)[0]


def _level_lag_days(value: str, latest_value: str) -> int:
    if not value or not latest_value:
        return 0
    try:
        current = datetime.strptime(value[:10], "%Y-%m-%d")
        latest = datetime.strptime(latest_value[:10], "%Y-%m-%d")
    except ValueError:
        return 0
    return max(0, (latest - current).days)


def _max_lag_days_for_level(raw_freq: str) -> int:
    # 周线天然以最近一周收盘日为时间戳；其他正式结构级别必须跟上最新交易日。
    return _WEEK_LEVEL_MAX_LAG_DAYS if raw_freq == "week" else _MAX_LEVEL_LAG_DAYS


def export_raw_bi_context_sync(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 1200,
    cchan_preset: str = "live_tolerant",
    *,
    recent_bi_count: int = 20,
    compute_profile: str = "radar_tactical_v1",
) -> dict:
    """P4: 导出多级别原始笔序列——去标签化，只保留几何事实。

    输出是给 AI Chan Reasoner 的"原始素材"，不含任何硬编码结论。
    AI 自行从笔序列识别中枢、判断位置、确定防守位。

    每笔包含：direction, begin_price, end_price, begin_time, end_time,
    bar_count, is_sure, high, low（笔内最高/最低价）。
    """
    canonical_symbol = normalize_symbol(symbol)
    requested_levels = levels or ["day", "30", "5"]

    if not _CHAN_AVAILABLE:
        return {
            "symbol": canonical_symbol,
            "version": "raw_bi_context.v1",
            "levels": {},
            "error": "chan_engine_unavailable",
        }

    level_inputs = []
    for level in requested_levels:
        raw_freq = normalize_level(level)
        level_input = _load_level_input(canonical_symbol, raw_freq, count, compute_profile=compute_profile)
        if level_input is not None:
            level_inputs.append(level_input)

    level_inputs = _refresh_lagging_level_inputs(
        canonical_symbol,
        level_inputs,
        count,
        compute_profile=compute_profile,
    )

    if not level_inputs:
        return {
            "symbol": canonical_symbol,
            "version": "raw_bi_context.v1",
            "levels": {},
            "error": "no_data",
        }

    try:
        kl_data_by_type = _run_chan_py(canonical_symbol, level_inputs, cchan_preset)
    except Exception as exc:
        logger.exception("export_raw_bi_context failed for %s", canonical_symbol)
        return {
            "symbol": canonical_symbol,
            "version": "raw_bi_context.v1",
            "levels": {},
            "error": str(exc)[:200],
        }

    result_levels = {}
    for item in level_inputs:
        if item.kl_type not in kl_data_by_type:
            continue
        kl_data = kl_data_by_type[item.kl_type]
        bi_list = kl_data.bi_list
        # 只取最近 N 笔，避免 context 过长
        recent_bis = bi_list[-recent_bi_count:] if len(bi_list) > recent_bi_count else bi_list

        serialized_bis = []
        for bi in recent_bis:
            is_up = str(bi.dir).endswith("UP")
            begin_klu = bi.get_begin_klu()
            end_klu = bi.get_end_klu()
            begin_time = _format_time_for_bi(begin_klu.time, item.ctime_to_date_str)
            end_time = _format_time_for_bi(end_klu.time, item.ctime_to_date_str)
            begin_price = bi.get_begin_val()
            end_price = bi.get_end_val()

            # 笔内最高/最低（用于精确计算重叠区间）
            bi_high = max(begin_price, end_price)
            bi_low = min(begin_price, end_price)
            # 尝试从笔内 K 线获取更精确的 high/low
            try:
                if hasattr(bi, 'klc_list'):
                    for klc in bi.klc_list:
                        if hasattr(klc, 'klu_list'):
                            for klu in klc.klu_list:
                                bi_high = max(bi_high, klu.high)
                                bi_low = min(bi_low, klu.low)
                        elif hasattr(klc, 'high') and hasattr(klc, 'low'):
                            bi_high = max(bi_high, klc.high)
                            bi_low = min(bi_low, klc.low)
            except Exception:
                pass

            # 计算笔内 K 线数（估算力度）
            bar_count = 0
            try:
                if hasattr(bi, 'klc_list'):
                    bar_count = len(bi.klc_list)
                elif begin_klu.idx >= 0 and end_klu.idx >= 0:
                    bar_count = abs(end_klu.idx - begin_klu.idx) + 1
            except Exception:
                pass

            serialized_bis.append({
                "direction": "UP" if is_up else "DOWN",
                "begin_price": round(begin_price, 4),
                "end_price": round(end_price, 4),
                "high": round(bi_high, 4),
                "low": round(bi_low, 4),
                "begin_time": begin_time,
                "end_time": end_time,
                "bar_count": bar_count,
                "is_sure": bi.is_sure if hasattr(bi, "is_sure") else True,
            })

        # 同时导出算法建议的中枢（降级为参考）
        algo_zhongshus = []
        for zs in kl_data.zs_list[-5:]:
            try:
                algo_zhongshus.append({
                    "zg": round(float(zs.zg), 4),
                    "zd": round(float(zs.zd), 4),
                    "gg": round(float(zs.gg), 4),
                    "dd": round(float(zs.dd), 4),
                    "begin_time": _format_time_for_bi(zs.begin.time, item.ctime_to_date_str),
                    "end_time": _format_time_for_bi(zs.end.time, item.ctime_to_date_str),
                    "bi_count": len(zs.bi_list) if hasattr(zs, "bi_list") else 0,
                    "source": "algorithm_suggestion",
                })
            except Exception:
                continue

        # 最新收盘价
        last_close = None
        if item.rows:
            try:
                last_close = float(_row_get(item.rows[-1], "close", 0))
            except (TypeError, ValueError):
                pass

        result_levels[item.raw_freq] = {
            "level": public_level_name(item.raw_freq),
            "bi_count_total": len(bi_list),
            "bi_sequence": serialized_bis,
            "last_close": last_close,
            "last_bar_time": str(_row_get(item.rows[-1], "date", "")) if item.rows else "",
            "algorithm_zhongshus": algo_zhongshus,
            "note": "bi_sequence 是原始几何事实；algorithm_zhongshus 仅供参考，AI 应自行从笔序列验证中枢。",
        }

    return {
        "symbol": canonical_symbol,
        "version": "raw_bi_context.v1",
        "levels": result_levels,
        "requested_levels": requested_levels,
        "compute_profile": compute_profile,
        "compute_bars_by_level": {item.raw_freq: item.compute_bars for item in level_inputs},
        "source": _source_meta(),
        "instruction": "你收到的是多级别原始笔序列。请自行识别中枢（连续≥3笔的重叠区间）、判断当前位置（中枢内/离开/回拉）、确定防守位。algorithm_zhongshus 是硬编码算法的建议，可能有错，你需要验证。",
    }


async def export_raw_bi_context(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 1200,
    cchan_preset: str = "live_tolerant",
    *,
    recent_bi_count: int = 20,
    compute_profile: str = "radar_tactical_v1",
) -> dict:
    """Async wrapper for export_raw_bi_context_sync."""
    return await run_in_threadpool(
        export_raw_bi_context_sync,
        symbol,
        levels,
        count,
        cchan_preset,
        recent_bi_count=recent_bi_count,
        compute_profile=compute_profile,
    )


def _format_time_for_bi(ctime, ctime_to_date_str: dict) -> str:
    """CTime → date_str for bi export."""
    try:
        key = f"{ctime.year}-{ctime.month}-{ctime.day}-{ctime.hour}-{ctime.minute}"
        return ctime_to_date_str.get(key, f"{ctime.year}-{ctime.month:02d}-{ctime.day:02d}")
    except Exception:
        return ""


def _error_result(symbol: str, requested_levels: list[str], code: str, message: str) -> dict:
    return {
        "adapter_version": ADAPTER_VERSION,
        "symbol": symbol,
        "data_source": {
            "structure": _source_meta(),
        },
        "freshness": {
            "source": STRUCTURE_SOURCE,
            "adjustflag": STRUCTURE_ADJUSTFLAG,
            "last_bar_at": "",
            "is_stale": True,
            "stale_reason": code,
            "levels": {},
        },
        "levels": {},
        "level_relations": {},
        "error": {
            "code": code,
            "message": message,
            "requested_levels": requested_levels,
        },
    }
