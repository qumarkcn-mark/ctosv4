import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from server.api import chan as chan_api
from server.services import chan_detail_service


def _sample_rows(count: int = 140) -> list[dict]:
    start = date(2025, 1, 1)
    rows = []
    for idx in range(count):
        close = 10 + idx * 0.03
        rows.append({
            "date": (start + timedelta(days=idx)).isoformat(),
            "open": close - 0.05,
            "high": close + 0.12,
            "low": close - 0.12,
            "close": close,
            "volume": 10000 + idx,
            "amount": 0,
        })
    return rows


def _fresh_sample_rows(count: int = 80) -> list[dict]:
    start = date.today() - timedelta(days=count - 1)
    rows = []
    for idx in range(count):
        close = 10 + idx * 0.03
        rows.append({
            "date": (start + timedelta(days=idx)).isoformat(),
            "open": close - 0.05,
            "high": close + 0.12,
            "low": close - 0.12,
            "close": close,
            "volume": 10000 + idx,
            "amount": 0,
        })
    return rows


def _fake_time(day: int):
    return SimpleNamespace(year=2026, month=5, day=day, hour=9, minute=30)


def _link_klus(items):
    for idx, item in enumerate(items):
        item.idx = idx
        item.next = items[idx + 1] if idx + 1 < len(items) else None
    return items


def _fake_line(klus, direction="up"):
    return SimpleNamespace(
        get_begin_klu=lambda: klus[0],
        get_end_klu=lambda: klus[-1],
        is_up=lambda: direction == "up",
        is_down=lambda: direction == "down",
    )


def _time_map(days):
    return {f"2026-5-{day}-9-30": f"2026-05-{day:02d}" for day in days}


def test_serialize_zhongshu_adds_display_dates_from_entry_and_first_full_exit():
    entry_klus = _link_klus([
        SimpleNamespace(time=_fake_time(1), high=9.7, low=9.2),
        SimpleNamespace(time=_fake_time(2), high=10.2, low=9.4),
    ])
    inside_klus = _link_klus([
        SimpleNamespace(time=_fake_time(3), high=10.8, low=9.8),
        SimpleNamespace(time=_fake_time(4), high=10.6, low=9.7),
    ])
    out_klus = _link_klus([
        SimpleNamespace(time=_fake_time(5), high=10.9, low=9.9),
        SimpleNamespace(time=_fake_time(6), high=11.2, low=10.6),
    ])
    zs = SimpleNamespace(
        begin=inside_klus[0],
        end=inside_klus[-1],
        begin_bi=_fake_line(inside_klus),
        bi_in=_fake_line(entry_klus),
        bi_out=_fake_line(out_klus, direction="up"),
        high=10.5,
        low=9.8,
        peak_high=10.9,
        peak_low=9.4,
    )

    result = chan_detail_service._serialize_zhongshus([zs], _time_map(range(1, 7)))

    assert result[0]["begin_date"] == "2026-05-03"
    assert result[0]["end_date"] == "2026-05-06"
    assert result[0]["display_begin_date"] == "2026-05-02"
    assert result[0]["display_end_date"] == "2026-05-06"


def test_serialize_zhongshu_display_end_falls_back_when_not_fully_outside():
    inside_klus = _link_klus([
        SimpleNamespace(time=_fake_time(3), high=10.8, low=9.8),
        SimpleNamespace(time=_fake_time(4), high=10.6, low=9.7),
    ])
    out_klus = _link_klus([
        SimpleNamespace(time=_fake_time(5), high=10.4, low=9.5),
        SimpleNamespace(time=_fake_time(6), high=10.5, low=9.6),
    ])
    zs = SimpleNamespace(
        begin=inside_klus[0],
        end=inside_klus[-1],
        begin_bi=_fake_line(inside_klus),
        bi_in=None,
        bi_out=_fake_line(out_klus, direction="down"),
        high=10.5,
        low=9.8,
        peak_high=10.9,
        peak_low=9.4,
    )

    result = chan_detail_service._serialize_zhongshus([zs], _time_map(range(3, 7)))

    assert result[0]["display_begin_date"] == "2026-05-03"
    assert result[0]["display_end_date"] == "2026-05-04"


def test_serialize_zhongshu_display_end_uses_first_full_downward_exit():
    inside_klus = _link_klus([
        SimpleNamespace(time=_fake_time(3), high=10.8, low=9.8),
        SimpleNamespace(time=_fake_time(4), high=10.6, low=9.7),
    ])
    out_klus = _link_klus([
        SimpleNamespace(time=_fake_time(5), high=10.1, low=9.4),
        SimpleNamespace(time=_fake_time(6), high=9.7, low=9.2),
    ])
    zs = SimpleNamespace(
        begin=inside_klus[0],
        end=inside_klus[-1],
        begin_bi=_fake_line(inside_klus),
        bi_in=None,
        bi_out=_fake_line(out_klus, direction="down"),
        high=10.5,
        low=9.8,
        peak_high=10.9,
        peak_low=9.2,
    )

    result = chan_detail_service._serialize_zhongshus([zs], _time_map(range(3, 7)))

    assert result[0]["display_end_date"] == "2026-05-06"


def _sample_minute_rows(count: int = 720) -> list[dict]:
    rows = []
    day = "2026-04-30"
    hour = 9
    minute = 31
    for idx in range(count):
        close = 20 + idx * 0.01
        rows.append({
            "date": f"{day} {hour:02d}:{minute:02d}:00",
            "open": close - 0.02,
            "high": close + 0.04,
            "low": close - 0.04,
            "close": close,
            "volume": 1000 + idx,
            "amount": 10000 + idx,
        })
        minute += 1
        if minute >= 60:
            hour += 1
            minute = 0
    return rows


def test_chan_detail_uses_tencent_fallback_when_lake_and_baostock_fail(monkeypatch):
    monkeypatch.setattr(chan_detail_service, "query_klines", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        chan_detail_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("BaoStock down")),
    )
    monkeypatch.setattr(
        chan_detail_service,
        "_fetch_tencent_fallback_klines",
        lambda *args, **kwargs: _sample_rows(),
    )

    result = chan_detail_service._parse_chan_detail_sync(
        "sh.600118",
        "day",
        count=120,
        max_compute_bars=120,
    )

    assert "error" not in result
    assert len(result["klines"]) == 120
    assert result["stats"]["computation_klines"] == 140


def test_chan_detail_short_fresh_cache_does_not_block_on_fallbacks(monkeypatch):
    monkeypatch.setattr(chan_detail_service, "query_klines", lambda *args, **kwargs: _fresh_sample_rows())
    monkeypatch.setattr(
        chan_detail_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("BaoStock should not run for fresh short cache")),
    )
    monkeypatch.setattr(
        chan_detail_service,
        "_fetch_tencent_fallback_klines",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Tencent should not run for fresh short cache")),
    )

    result = chan_detail_service._parse_chan_detail_sync(
        "sh.600118",
        "day",
        count=120,
        max_compute_bars=120,
    )

    assert "error" not in result
    assert len(result["klines"]) == 80
    assert result["data_source"]["provider"] == "baostock"


def test_chan_detail_uses_tdx_day_before_tencent(monkeypatch):
    def fake_query(symbol, freq, **kwargs):
        if kwargs.get("source") == "tdx" and kwargs.get("adjustflag") == "3":
            return _sample_rows()
        return []

    monkeypatch.setattr(chan_detail_service, "query_klines", fake_query)
    monkeypatch.setattr(
        chan_detail_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("BaoStock down")),
    )

    def fail_tencent(*args, **kwargs):
        raise AssertionError("Tencent fallback should not run when TDX day is available")

    monkeypatch.setattr(chan_detail_service, "_fetch_tencent_fallback_klines", fail_tencent)

    result = chan_detail_service._parse_chan_detail_sync(
        "sh.600118",
        "day",
        count=120,
        max_compute_bars=120,
    )

    assert "error" not in result
    assert result["data_source"]["provider"] == "tdx"
    assert result["data_source"]["adjustflag"] == "3"
    assert result["dataBadge"]["label"] == "day · TDX本地"


def test_chan_detail_uses_tdx_minute_before_tencent(monkeypatch):
    monkeypatch.setattr(chan_detail_service, "query_klines", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        chan_detail_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("BaoStock down")),
    )
    monkeypatch.setattr(
        chan_detail_service,
        "read_tdx_1m_klines",
        lambda *args, **kwargs: _sample_minute_rows(),
    )

    def fail_tencent(*args, **kwargs):
        raise AssertionError("Tencent fallback should not run when TDX minute is available")

    monkeypatch.setattr(chan_detail_service, "_fetch_tencent_fallback_klines", fail_tencent)

    result = chan_detail_service._parse_chan_detail_sync(
        "sh.600118",
        "5",
        count=120,
        max_compute_bars=120,
    )

    assert "error" not in result
    assert result["data_source"]["provider"] == "tdx_minute"
    assert result["data_source"]["adjustflag"] == "3"
    assert result["dataBadge"]["label"] == "5 · TDX本地分钟"
    assert len(result["klines"]) == 120


def test_chan_detail_geometry_contract_characterization(monkeypatch):
    """锁住旧 get_chan_detail 几何输出字段，供 snapshot worker 对照。"""
    monkeypatch.setattr(chan_detail_service, "query_klines", lambda *args, **kwargs: _sample_rows())
    monkeypatch.setattr(
        chan_detail_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fresh enough sample should not fetch")),
    )

    result = chan_detail_service._parse_chan_detail_sync(
        "sh.600118",
        "day",
        count=120,
        max_compute_bars=120,
    )

    for key in (
        "klines",
        "bis",
        "segs",
        "bi_zhongshus",
        "bi_zhongshus_decomp",
        "seg_zhongshus",
        "bsps",
        "stats",
        "data_source",
    ):
        assert key in result
    assert len(result["klines"]) == 120
    assert result["stats"]["kline_count"] == 120
    assert result["stats"]["computation_klines"] == 140
    if result["bis"]:
        assert {"x0", "y0", "x1", "y1", "is_up", "is_sure", "momentum"} <= set(result["bis"][0])
    if result["bi_zhongshus"]:
        assert {
            "begin_date",
            "end_date",
            "display_begin_date",
            "display_end_date",
            "zg",
            "zd",
            "gg",
            "dd",
        } <= set(result["bi_zhongshus"][0])


def test_chan_detail_api_always_uses_snapshot_first(monkeypatch):
    async def fake_snapshot_first(**kwargs):
        assert kwargs["symbol"] == "sh.600519"
        assert kwargs["freq"] == "30"
        assert kwargs["display_count"] == 88
        assert kwargs["compute_profile"] == "radar_tactical_v1"
        assert kwargs["snapshot_mode"] == "fresh_only"
        assert kwargs["sync_if_missing"] is False
        return {"snapshot_status": "pending", "structure_key_hash": "hash-a"}

    monkeypatch.setattr(chan_api, "get_structure_snapshot_or_enqueue", fake_snapshot_first)

    response = asyncio.run(
        chan_api.get_chan_detail_api(
            "sh600519",
            freq="30",
            count=500,
            display_count=88,
            cchan_preset="live_tolerant",
            compute_profile="radar_tactical_v1",
            snapshot_mode="fresh_only",
            sync_if_missing=None,
        )
    )

    assert response["status"] == "success"
    assert response["data"]["snapshot_status"] == "pending"
