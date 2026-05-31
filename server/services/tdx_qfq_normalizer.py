"""Build TDX front-adjusted bars from local raw bars and existing TDX factors.

TDX local vipdoc .day/.lc1 files are raw price facts. This module only writes
`source=tdx, adjustflag=2` when a same-day front-adjusted daily row already
exists, so we never pretend raw local prices are formal CZSC structure data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from server.config import TDX_ROOT, TDX_VIPDOC
from server.db.kline_lake import query_klines, upsert_klines
from server.domain.symbols import normalize_symbol
from server.services.qfq_normalizer import aggregate_week_rows, normalize_minute_rows

logger = logging.getLogger(__name__)

TDX_QFQ_FREQS = ("week", "1", "5", "15", "30", "60")
GBBQ_COLUMNS = (
    "market",
    "code",
    "datetime",
    "category",
    "hongli_panqianliutong",
    "peigujia_qianzongguben",
    "songgu_qianzongguben",
    "peigu_houzongguben",
)


@dataclass(frozen=True)
class TdxQfqBuildResult:
    symbol: str
    day_factor_count: int
    written: dict[str, int] = field(default_factory=dict)
    missing_factor_dates: dict[str, int] = field(default_factory=dict)
    status: str = "ok"
    reason: str = ""

    @property
    def total_written(self) -> int:
        return sum(self.written.values())


def rebuild_tdx_qfq_from_existing_factors(
    symbol: str,
    *,
    target_freqs: Iterable[str] | None = None,
    limit: int = 20000,
    tdx_root: str | None = None,
) -> TdxQfqBuildResult:
    """Rebuild TDX qfq cache from local raw bars and qfq daily anchors.

    Required inputs in the TDX lake:
    - `day/3`: raw local TDX daily bars.
    - `day/2`: front-adjusted daily bars. If absent, build it from local
      TDX `T0002/hq_cache/gbbq`.

    Outputs:
    - `day/2` when rebuilt from gbbq.
    - `week/2` from `day/2`.
    - minute `1/5/15/30/60` by applying same-day daily factors to raw TDX bars.
    """
    canonical = normalize_symbol(symbol)
    requested_freqs = TDX_QFQ_FREQS if target_freqs is None else target_freqs
    requested = tuple(dict.fromkeys(str(freq) for freq in requested_freqs))
    raw_day_rows = query_klines(canonical, "day", limit=limit, adjustflag="3", source="tdx")
    qfq_day_rows = query_klines(canonical, "day", limit=limit, adjustflag="2", source="tdx")
    written: dict[str, int] = {}
    if raw_day_rows and _missing_qfq_days(raw_day_rows, qfq_day_rows):
        generated = build_tdx_qfq_day_rows_from_gbbq(canonical, raw_day_rows, tdx_root=tdx_root)
        if generated:
            written["day"] = upsert_klines(canonical, "day", generated, adjustflag="2", source="tdx")
            qfq_day_rows = generated
    if raw_day_rows and not qfq_day_rows:
        return TdxQfqBuildResult(
            symbol=canonical,
            day_factor_count=0,
            written=written,
            status="skipped",
            reason="NO_TDX_DAY_QFQ_FACTOR_OR_GBBQ",
        )

    missing_day_factors = _missing_qfq_days(raw_day_rows, qfq_day_rows)
    if missing_day_factors:
        return TdxQfqBuildResult(
            symbol=canonical,
            day_factor_count=len(qfq_day_rows),
            written=written,
            missing_factor_dates={"day": len(missing_day_factors)},
            status="skipped",
            reason="STALE_TDX_DAY_QFQ_FACTOR",
        )

    factor_rows = _day_rows_with_factor(raw_day_rows, qfq_day_rows)
    if not factor_rows:
        return TdxQfqBuildResult(
            symbol=canonical,
            day_factor_count=0,
            status="skipped",
            reason="NO_TDX_DAY_QFQ_FACTOR_OR_GBBQ",
        )

    missing_factor_dates: dict[str, int] = {}

    if "week" in requested:
        week_rows = aggregate_week_rows(factor_rows)
        written["week"] = upsert_klines(canonical, "week", week_rows, adjustflag="2", source="tdx") if week_rows else 0

    for freq in requested:
        if freq == "week":
            continue
        raw_rows = query_klines(canonical, freq, limit=limit, adjustflag="3", source="tdx")
        qfq_rows = normalize_minute_rows(raw_rows, factor_rows)
        written[freq] = upsert_klines(canonical, freq, qfq_rows, adjustflag="2", source="tdx") if qfq_rows else 0
        raw_days = {str(row.get("date") or "")[:10] for row in raw_rows}
        factor_days = {str(row.get("date") or "")[:10] for row in factor_rows}
        missing_factor_dates[freq] = len(raw_days - factor_days)

    logger.info(
        "TDX qfq rebuild %s: factors=%d written=%s missing_factor_dates=%s",
        canonical,
        len(factor_rows),
        written,
        missing_factor_dates,
    )
    return TdxQfqBuildResult(
        symbol=canonical,
        day_factor_count=len(factor_rows),
        written=written,
        missing_factor_dates=missing_factor_dates,
    )


def build_tdx_qfq_day_rows_from_gbbq(
    symbol: str,
    raw_day_rows: list[dict],
    *,
    tdx_root: str | None = None,
) -> list[dict]:
    """Build TDX day/2 rows from local raw day bars and gbbq events."""
    rows = _valid_day_rows(raw_day_rows)
    if not rows:
        return []
    try:
        gbbq_df = _read_gbbq_df(tdx_root=tdx_root)
    except Exception as exc:
        logger.warning("TDX gbbq unavailable for %s: %s", symbol, exc)
        return []
    return build_qfq_day_rows_from_gbbq_events(symbol, rows, gbbq_df)


def build_qfq_day_rows_from_gbbq_events(symbol: str, raw_day_rows: list[dict], gbbq_events: pd.DataFrame) -> list[dict]:
    """Calculate front-adjusted daily bars from TDX gbbq ex-right events."""
    code = normalize_symbol(symbol).split(".", 1)[1]
    rows = _valid_day_rows(raw_day_rows)
    if not rows:
        return []
    df_code = pd.DataFrame(rows).copy()
    df_code["date"] = pd.to_datetime(df_code["date"].astype(str).str[:10], format="%Y-%m-%d")
    df_code.set_index("date", drop=True, inplace=True)
    df_code.sort_index(inplace=True)
    df_code["if_trade"] = True

    events = _normalize_gbbq_events(gbbq_events)
    events = events[(events["code"] == code) & (events["category"] == 1)].copy()
    if events.empty:
        events = pd.DataFrame(columns=[*GBBQ_COLUMNS, "date"]).set_index(pd.DatetimeIndex([], name="date"))
    else:
        last_day = df_code.index[-1]
        events = events[events["date"] <= last_day]
        events.set_index("date", drop=True, inplace=True)
        events.sort_index(inplace=True)
        events = events[~events.index.duplicated(keep="last")]

    data = pd.concat([df_code, events[["category"]][df_code.index[0] :]], axis=1)
    data["if_trade"] = data["if_trade"].fillna(False)
    data.ffill(inplace=True)
    data = pd.concat(
        [
            data,
            events[
                [
                    "hongli_panqianliutong",
                    "peigujia_qianzongguben",
                    "songgu_qianzongguben",
                    "peigu_houzongguben",
                ]
            ][df_code.index[0] :],
        ],
        axis=1,
    )
    data.fillna(0, inplace=True)
    data["preclose"] = (
        data["close"].shift(1) * 10
        - data["hongli_panqianliutong"]
        + data["peigu_houzongguben"] * data["peigujia_qianzongguben"]
    ) / (10 + data["peigu_houzongguben"] + data["songgu_qianzongguben"])
    data["adj"] = (data["preclose"].shift(-1) / data["close"]).fillna(1)[::-1].cumprod()

    result: list[dict] = []
    for index, row in data[data["if_trade"]].iterrows():
        factor = _num(row.get("adj")) or 1.0
        item = {
            "date": index.strftime("%Y-%m-%d"),
            "open": round(float(row["open"]) * factor, 4),
            "high": round(float(row["high"]) * factor, 4),
            "low": round(float(row["low"]) * factor, 4),
            "close": round(float(row["close"]) * factor, 4),
            "volume": float(row.get("volume", 0) or 0),
            "amount": float(row.get("amount", 0) or 0),
            "qfq_factor": factor,
        }
        if item["open"] > 0 and item["close"] > 0:
            result.append(item)
    return result


def _day_rows_with_factor(raw_day_rows: list[dict], qfq_day_rows: list[dict]) -> list[dict]:
    raw_by_day = {str(row.get("date") or "")[:10]: row for row in raw_day_rows}
    result: list[dict] = []
    for qfq in qfq_day_rows:
        day = str(qfq.get("date") or "")[:10]
        raw = raw_by_day.get(day)
        raw_close = _num((raw or {}).get("close"))
        qfq_close = _num(qfq.get("close"))
        if not raw or raw_close <= 0 or qfq_close <= 0:
            continue
        item = dict(qfq)
        item["qfq_factor"] = qfq_close / raw_close
        result.append(item)
    return result


def _missing_qfq_days(raw_day_rows: list[dict], qfq_day_rows: list[dict]) -> set[str]:
    raw_days = {str(row.get("date") or "")[:10] for row in raw_day_rows if row.get("date")}
    qfq_days = {str(row.get("date") or "")[:10] for row in qfq_day_rows if row.get("date")}
    return {day for day in raw_days - qfq_days if day}


def _read_gbbq_df(*, tdx_root: str | None = None) -> pd.DataFrame:
    from pytdx.reader.gbbq_reader import GbbqReader

    return GbbqReader().get_df(str(_resolve_gbbq_path(tdx_root)))


def _resolve_gbbq_path(tdx_root: str | None = None) -> Path:
    candidates = []
    if tdx_root:
        candidates.append(Path(tdx_root))
    if TDX_ROOT:
        candidates.append(Path(TDX_ROOT))
    if TDX_VIPDOC:
        vipdoc = Path(TDX_VIPDOC)
        candidates.extend([vipdoc.parent, vipdoc.parent.parent])
    candidates.extend(
        [
            Path("/Users/markqu/Desktop/new_tdx64_mount"),
            Path("/Volumes/new_tdx64"),
        ]
    )
    for root in candidates:
        path = root / "T0002" / "hq_cache" / "gbbq"
        if path.is_file():
            return path
    raise FileNotFoundError("TDX gbbq not found under known TDX roots")


def _normalize_gbbq_events(events: pd.DataFrame) -> pd.DataFrame:
    df = events.copy()
    if not set(GBBQ_COLUMNS).issubset(df.columns):
        df.rename(columns={old: new for old, new in zip(df.columns, GBBQ_COLUMNS)}, inplace=True)
    missing = set(GBBQ_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"gbbq missing columns: {sorted(missing)}")
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["datetime"].astype(int).astype(str), format="%Y%m%d", errors="coerce")
    return df.dropna(subset=["date"])


def _valid_day_rows(rows: list[dict]) -> list[dict]:
    result = []
    for row in sorted(rows, key=lambda item: str(item.get("date") or "")):
        if _num(row.get("open")) <= 0 or _num(row.get("close")) <= 0:
            continue
        try:
            datetime.strptime(str(row.get("date") or "")[:10], "%Y-%m-%d")
        except ValueError:
            continue
        result.append(dict(row))
    return result


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
