"""Compact CZSC signal digest for second-stage reasoning."""

from __future__ import annotations

from typing import Any


CHAN_DIGEST_VERSION = "chan_signal_digest.v1"

SIGNAL_CATEGORIES = {
    "third_buy_sell": {
        "key_markers": ("三买辅助", "BS3辅助"),
        "value_markers": ("三买", "三卖"),
    },
    "first_buy_sell": {
        "key_markers": ("BUY1", "SELL1", "第二买卖点"),
        "value_markers": ("一买", "一卖", "二买", "二卖", "第二买卖点"),
    },
    "zhongshu_resonance": {
        "key_markers": ("中枢共振", "共振"),
        "value_markers": ("中枢共振", "共振"),
    },
    "trend_pullback_rebound": {
        "key_markers": ("BS辅助", "BE辅助", "三笔", "五笔", "七笔", "九笔", "十一笔"),
        "value_markers": ("回调", "反弹", "转折", "中枢完成", "类一买", "类一卖", "向上", "向下"),
    },
}

IGNORE_VALUES = {"", "任意", "无", "None", "nan"}


def build_chan_signal_digest(
    snapshots: dict[str, dict[str, Any]],
    *,
    level_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """筛选最适合实盘推演的 CZSC 原生信号。"""
    names = level_names or {}
    by_level: dict[str, dict[str, list[dict[str, str]]]] = {}
    for level, row in snapshots.items():
        snap = row.get("snapshot") or {}
        raw = snap.get("chan_signals") or snap.get("signals") or {}
        if not isinstance(raw, dict):
            continue
        grouped: dict[str, list[dict[str, str]]] = {category: [] for category in SIGNAL_CATEGORIES}
        for key, value in raw.items():
            key_text = str(key or "")
            value_text = str(value or "")
            if _ignore_value(value_text):
                continue
            for category, spec in SIGNAL_CATEGORIES.items():
                if not _matches_category(key_text, value_text, spec):
                    continue
                grouped[category].append(
                    {
                        "key": key_text[:100],
                        "value": value_text[:100],
                        "source": "czsc.signals",
                        "polarity": _signal_polarity(value_text),
                    }
                )
                break
        compact = {category: items[:4] for category, items in grouped.items() if items}
        if compact:
            by_level[names.get(level, level)] = compact
    return {
        "version": CHAN_DIGEST_VERSION,
        "by_level": by_level,
        "summary": _summarize_digest(by_level),
    }


def _matches_category(key: str, value: str, spec: dict[str, tuple[str, ...]]) -> bool:
    text = f"{key}_{value}"
    has_key = any(marker in key for marker in spec["key_markers"])
    has_value = any(marker in value for marker in spec["value_markers"])
    if "其他" in value and not has_value:
        return False
    return has_key or any(marker in text for marker in spec["value_markers"])


def _ignore_value(value: str) -> bool:
    parts = [item for item in value.split("_") if item]
    if not parts:
        return True
    if all(part in IGNORE_VALUES or part == "其他" for part in parts):
        return True
    return value in IGNORE_VALUES or value.startswith("其他_其他")


def _signal_polarity(value: str) -> str:
    bullish_markers = ("一买", "二买", "三买", "类一买", "底", "向上", "反弹")
    bearish_markers = ("一卖", "二卖", "三卖", "类一卖", "顶", "向下", "回调")
    if any(marker in value for marker in bullish_markers):
        return "bullish"
    if any(marker in value for marker in bearish_markers):
        return "bearish"
    return "neutral"


def _summarize_digest(by_level: dict[str, dict[str, list[dict[str, str]]]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for level, categories in by_level.items():
        for category, items in categories.items():
            for item in items:
                result.append(
                    {
                        "level": level,
                        "category": category,
                        "value": item["value"],
                        "polarity": item["polarity"],
                    }
                )
                if len(result) >= 12:
                    return result
    return result
