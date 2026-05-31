"""Hydrate lightweight market / sector relative-strength facts for reasoning tests.

第一版只做事实摘要：指数背景、手工主板块标签、个股相对指数强弱。
不解析全市场板块、不触发 AI、不参与正式 CZSC 结构计算。
"""

from __future__ import annotations

import statistics
from typing import Any

from server.db.kline_lake import query_klines
from server.domain.symbols import normalize_symbol
from server.services.tdx_daily_sync_service import read_tdx_day_klines
from server.services.tdx_minute_service import read_tdx_derived_minute_klines
from server.services.tdx_sector_service import get_tdx_sector_context


INDEX_NAMES = {
    "sh.000001": "上证指数",
    "sz.399001": "深证成指",
    "sz.399006": "创业板指",
    "sh.000688": "科创50",
    "sh.000300": "沪深300",
}


SYMBOL_SECTOR_MAP = {
    "sz.300394": {"sector": "CPO / 光通信 / 算力", "benchmark": "sz.399006", "reason": "创业板成长风格代理"},
    "sh.688008": {"sector": "半导体 / 存储", "benchmark": "sh.000688", "reason": "科创50成长与半导体风格代理"},
    "sh.603986": {"sector": "半导体 / 存储", "benchmark": "sh.000688", "reason": "科创50成长与半导体风格代理"},
    "sh.603893": {"sector": "半导体 / AI 端侧", "benchmark": "sh.000688", "reason": "科创50成长与半导体风格代理"},
    "sh.600259": {"sector": "稀土 / 有色", "benchmark": "sh.000001", "reason": "暂用上证作为周期资源股代理"},
    "sh.600790": {"sector": "轻工 / 纺织", "benchmark": "sh.000001", "reason": "暂用上证作为传统行业代理"},
    "sz.002138": {"sector": "电子元件", "benchmark": "sz.399001", "reason": "暂用深成指作为中小盘电子代理"},
}


def hydrate_index_sector_context(symbol: str) -> dict[str, Any]:
    """Build compact market-relative facts for the second-stage reasoning payload."""
    canonical = normalize_symbol(symbol)
    exact_sector = get_tdx_sector_context(canonical)
    mapping = SYMBOL_SECTOR_MAP.get(canonical)
    mapping_source = "manual_v0" if mapping else "default_proxy_v0"
    mapping = mapping or _default_mapping(canonical)
    benchmark = mapping["benchmark"]
    levels = {
        "day": _level_context(canonical, benchmark, "day"),
        "30": _level_context(canonical, benchmark, "30"),
        "5": _level_context(canonical, benchmark, "5"),
    }
    day = levels.get("day") or {}
    m30 = levels.get("30") or {}
    return {
        "version": "index_sector_context.v0_test",
        "usage": "background_evidence_only",
        "symbol": canonical,
        "market_context": {
            "benchmark": benchmark,
            "benchmark_name": INDEX_NAMES.get(benchmark, benchmark),
            "phase": _phase_label((day.get("benchmark") or {}).get("stats") or {}),
            "short_phase": _phase_label((m30.get("benchmark") or {}).get("stats") or {}),
        },
        "sector_context": {
            "sector": ((exact_sector.get("primary_sector") or {}).get("name") or mapping["sector"]),
            "benchmark": benchmark,
            "benchmark_name": INDEX_NAMES.get(benchmark, benchmark),
            "sector_index": (exact_sector.get("primary_sector") or {}).get("index_code") or "",
            "sector_path": (exact_sector.get("primary_sector") or {}).get("path") or [],
            "tdx_industry_path": (exact_sector.get("tdx_industry") or {}).get("path") or [],
            "mapping_source": "tdx_hq_cache" if exact_sector else mapping_source,
            "mapping_reason": mapping.get("reason", ""),
            "note": (
                "所属板块来自 TDX 本地行业映射；日线相对强弱优先使用所属板块指数摘要，"
                "30分钟/5分钟暂用宽基 benchmark 代理。"
                if exact_sector
                else "第一版尚未接入 TDX 板块指数，sector 仅作人工主板块标签；强弱先用 benchmark 代理。"
            ),
        },
        "concept_context": {
            "themes": _concept_theme_names(exact_sector),
            "items": (exact_sector.get("concept_themes") or [])[:6],
            "source": "tdx_infoharbor_block" if (exact_sector.get("concept_themes") or []) else "",
            "note": (
                "概念主题来自 TDX 本地 infoharbor_block，仅保留少量 GN_ 概念；"
                "用于判断市场活跃方向和题材共振，不作为结构结论。"
                if (exact_sector.get("concept_themes") or [])
                else ""
            ),
        },
        "relative_strength": {
            "vs_benchmark": _relative_label(day),
            "vs_benchmark_30m": _relative_label(m30),
            "vs_sector_daily": _sector_relative_label(day, exact_sector),
            "evidence": (
                _sector_relative_evidence(day, exact_sector)
                + _concept_evidence(exact_sector)
                + _relative_evidence(levels)
            ),
        },
        "levels": levels,
        "tdx_sector_context": exact_sector,
    }


def _default_mapping(symbol: str) -> dict[str, str]:
    if symbol.startswith("sh.688"):
        return {"sector": "科创成长", "benchmark": "sh.000688", "reason": "科创股票默认用科创50代理"}
    if symbol.startswith("sz.300"):
        return {"sector": "创业成长", "benchmark": "sz.399006", "reason": "创业板股票默认用创业板指代理"}
    if symbol.startswith("sz."):
        return {"sector": "深市综合", "benchmark": "sz.399001", "reason": "深市股票默认用深成指代理"}
    return {"sector": "沪市综合", "benchmark": "sh.000001", "reason": "沪市股票默认用上证指数代理"}


def _level_context(symbol: str, benchmark: str, freq: str) -> dict[str, Any]:
    stock_rows = _read_rows(symbol, freq, is_index=False)
    benchmark_rows = _read_rows(benchmark, freq, is_index=True)
    stock_stats = _series_stats(stock_rows, freq=freq)
    benchmark_stats = _series_stats(benchmark_rows, freq=freq)
    return {
        "freq": freq,
        "stock": {
            "rows": len(stock_rows),
            "last_time": stock_rows[-1]["date"] if stock_rows else "",
            "stats": stock_stats,
        },
        "benchmark": {
            "symbol": benchmark,
            "name": INDEX_NAMES.get(benchmark, benchmark),
            "rows": len(benchmark_rows),
            "last_time": benchmark_rows[-1]["date"] if benchmark_rows else "",
            "stats": benchmark_stats,
        },
        "spread": _spread_stats(stock_stats, benchmark_stats),
    }


def _read_rows(symbol: str, freq: str, *, is_index: bool) -> list[dict[str, Any]]:
    if not is_index:
        rows = query_klines(symbol, freq, limit=240, adjustflag="2", source="tdx")
        if rows:
            return rows
        rows = query_klines(symbol, freq, limit=240, adjustflag="3", source="tdx")
        if rows:
            return rows
    if freq == "day":
        return read_tdx_day_klines(symbol, limit=240)
    if freq in {"5", "15", "30", "60"}:
        return read_tdx_derived_minute_klines(symbol, freq, limit=240)
    return []


def _series_stats(rows: list[dict[str, Any]], *, freq: str) -> dict[str, Any]:
    closes = [_num(row.get("close")) for row in rows if _num(row.get("close")) > 0]
    if len(closes) < 2:
        return {}
    latest = closes[-1]
    unit = _freq_unit(freq)
    ret_1 = _ret(closes, 1)
    ret_5 = _ret(closes, 5)
    ret_20 = _ret(closes, 20)
    stats = {
        "latest": round(latest, 4),
        # 旧字段保留给历史消费方；给 LLM 优先看 returns / return_labels。
        "ret_1": ret_1,
        "ret_5": ret_5,
        "ret_20": ret_20,
        "returns": {
            "freq": freq,
            "bar_unit": unit,
            "last_1_bar_pct": ret_1,
            "last_5_bars_pct": ret_5,
            "last_20_bars_pct": ret_20,
        },
        "return_labels": {
            "last_1_bar_pct": f"最近1根{unit}涨跌幅",
            "last_5_bars_pct": f"最近5根{unit}累计涨跌幅",
            "last_20_bars_pct": f"最近20根{unit}累计涨跌幅",
        },
        "ma20_gap": _ma_gap(closes, 20),
        "volatility_20": _volatility(closes, 20),
    }
    stats["phase"] = _phase_label(stats)
    return stats


def _spread_stats(stock: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    if not stock or not benchmark:
        return {}
    freq = str(((stock.get("returns") or {}).get("freq") or "")).strip()
    unit = str(((stock.get("returns") or {}).get("bar_unit") or _freq_unit(freq))).strip()
    ret_1_spread = round(_num(stock.get("ret_1")) - _num(benchmark.get("ret_1")), 2)
    ret_5_spread = round(_num(stock.get("ret_5")) - _num(benchmark.get("ret_5")), 2)
    ret_20_spread = round(_num(stock.get("ret_20")) - _num(benchmark.get("ret_20")), 2)
    spread = {
        "ret_1_spread": ret_1_spread,
        "ret_5_spread": ret_5_spread,
        "ret_20_spread": ret_20_spread,
        "return_spreads": {
            "freq": freq,
            "bar_unit": unit,
            "last_1_bar_spread_pct": ret_1_spread,
            "last_5_bars_spread_pct": ret_5_spread,
            "last_20_bars_spread_pct": ret_20_spread,
        },
        "return_spread_labels": {
            "last_1_bar_spread_pct": f"个股相对基准最近1根{unit}涨跌差",
            "last_5_bars_spread_pct": f"个股相对基准最近5根{unit}累计涨跌差",
            "last_20_bars_spread_pct": f"个股相对基准最近20根{unit}累计涨跌差",
        },
    }
    spread["label"] = _strength_label(spread.get("ret_5_spread"), spread.get("ret_20_spread"))
    return spread


def _relative_label(level: dict[str, Any]) -> str:
    spread = level.get("spread") or {}
    return str(spread.get("label") or "unknown")


def _relative_evidence(levels: dict[str, dict[str, Any]]) -> list[str]:
    result = []
    for name, label in (("day", "日线"), ("30", "30分钟"), ("5", "5分钟")):
        level = levels.get(name) or {}
        spread = level.get("spread") or {}
        stock = ((level.get("stock") or {}).get("stats") or {})
        bench = ((level.get("benchmark") or {}).get("stats") or {})
        if not spread:
            continue
        unit = _freq_unit(str(level.get("freq") or name))
        result.append(
            f"{label}: 最近5根{unit}累计，个股{_fmt_pct(stock.get('ret_5'))} / "
            f"{(level.get('benchmark') or {}).get('name', '基准')}{_fmt_pct(bench.get('ret_5'))}，"
            f"相对{_fmt_pct(spread.get('ret_5_spread'))}，{spread.get('label')}"
        )
    return result[:3]


def _sector_relative_label(day_level: dict[str, Any], exact_sector: dict[str, Any]) -> str:
    sector_stats = exact_sector.get("daily_stats") or {}
    stock_stats = ((day_level.get("stock") or {}).get("stats") or {})
    if not sector_stats or not stock_stats:
        return "unknown"
    spread5 = _num(stock_stats.get("ret_5")) - _num(sector_stats.get("ret_5"))
    spread20 = _num(stock_stats.get("ret_20")) - _num(sector_stats.get("ret_20"))
    return _strength_label(spread5, spread20)


def _sector_relative_evidence(day_level: dict[str, Any], exact_sector: dict[str, Any]) -> list[str]:
    sector_stats = exact_sector.get("daily_stats") or {}
    stock_stats = ((day_level.get("stock") or {}).get("stats") or {})
    sector_name = ((exact_sector.get("primary_sector") or {}).get("name") or "").strip()
    if not sector_stats or not stock_stats or not sector_name:
        return []
    ret5_spread = round(_num(stock_stats.get("ret_5")) - _num(sector_stats.get("ret_5")), 2)
    ret20_spread = round(_num(stock_stats.get("ret_20")) - _num(sector_stats.get("ret_20")), 2)
    label = _strength_label(ret5_spread, ret20_spread)
    return [
        f"所属板块日线: 最近5根日K累计，个股{_fmt_pct(stock_stats.get('ret_5'))} / "
        f"{sector_name}{_fmt_pct(sector_stats.get('ret_5'))}，"
        f"最近5根日K相对{_fmt_pct(ret5_spread)}，最近20根日K相对{_fmt_pct(ret20_spread)}，{label}"
    ]


def _concept_theme_names(exact_sector: dict[str, Any]) -> list[str]:
    result = []
    for item in (exact_sector.get("concept_themes") or [])[:6]:
        name = str(item.get("name") or "").strip()
        if name:
            result.append(name)
    return result


def _concept_evidence(exact_sector: dict[str, Any]) -> list[str]:
    themes = _concept_theme_names(exact_sector)
    if not themes:
        return []
    return [f"概念主题: {', '.join(themes[:6])}"]


def _phase_label(stats: dict[str, Any]) -> str:
    if not stats:
        return "unknown"
    ret5 = _num(stats.get("ret_5"))
    ma20_gap = _num(stats.get("ma20_gap"))
    if ma20_gap > 1.5 and ret5 > 1:
        return "uptrend_or_breakout"
    if ma20_gap < -1.5 and ret5 < -1:
        return "weak_or_pullback"
    if abs(ma20_gap) <= 1.5:
        return "range_or_balance"
    return "mixed"


def _strength_label(ret5_spread: Any, ret20_spread: Any) -> str:
    ret5 = _num(ret5_spread)
    ret20 = _num(ret20_spread)
    if ret5 >= 3 or (ret5 >= 1.5 and ret20 >= 5):
        return "stronger_than_benchmark"
    if ret5 <= -3 or (ret5 <= -1.5 and ret20 <= -5):
        return "weaker_than_benchmark"
    return "neutral_vs_benchmark"


def _ret(closes: list[float], periods: int) -> float:
    if len(closes) <= periods or closes[-1 - periods] <= 0:
        return 0.0
    return round((closes[-1] / closes[-1 - periods] - 1) * 100, 2)


def _freq_unit(freq: str) -> str:
    normalized = str(freq or "").strip().lower()
    if normalized == "day":
        return "日K"
    if normalized == "week":
        return "周K"
    if normalized in {"1", "1m"}:
        return "1分钟K"
    if normalized in {"5", "5m"}:
        return "5分钟K"
    if normalized in {"15", "15m"}:
        return "15分钟K"
    if normalized in {"30", "30m"}:
        return "30分钟K"
    if normalized in {"60", "60m"}:
        return "60分钟K"
    return f"{normalized or '当前频率'}K"


def _ma_gap(closes: list[float], n: int) -> float:
    if len(closes) < n:
        return 0.0
    ma = statistics.mean(closes[-n:])
    return round((closes[-1] / ma - 1) * 100, 2) if ma else 0.0


def _volatility(closes: list[float], n: int) -> float:
    if len(closes) <= n:
        return 0.0
    rets = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(len(closes) - n + 1, len(closes)) if closes[i - 1] > 0]
    return round(statistics.pstdev(rets), 2) if len(rets) >= 2 else 0.0


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt_pct(value: Any) -> str:
    return f"{_num(value):+.2f}%"
