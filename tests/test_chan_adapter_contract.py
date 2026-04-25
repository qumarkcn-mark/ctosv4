import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.structure import chan_adapter


def make_rows(count=130):
    rows = []
    for idx in range(count):
        day = idx + 1
        rows.append(
            {
                "date": f"2026-01-{(day - 1) % 28 + 1:02d}",
                "open": 10.0 + idx * 0.01,
                "high": 10.5 + idx * 0.01,
                "low": 9.8 + idx * 0.01,
                "close": 10.2 + idx * 0.01,
                "volume": 1000 + idx,
            }
        )
    return rows


def test_normalize_level_accepts_public_aliases():
    assert chan_adapter.normalize_level("day") == "day"
    assert chan_adapter.normalize_level("m30") == "30"
    assert chan_adapter.normalize_level("30m") == "30"
    assert chan_adapter.normalize_level("5") == "5"


def test_analyze_structure_sync_returns_adapter_contract(monkeypatch):
    rows = make_rows()

    def fake_query(symbol, freq, limit):
        assert symbol == "sh.600519"
        assert freq in ("day", "30")
        assert limit >= 5000
        return rows

    def fake_run(symbol, level_inputs):
        return {item.kl_type: object() for item in level_inputs}

    def fake_serialize(kl_data, ctime_to_date_str, input_rows, raw_freq, count):
        return {
            "freq": raw_freq,
            "klines": [{"time": input_rows[-1]["date"], "close": input_rows[-1]["close"]}],
            "bis": [],
            "segs": [],
            "bi_zhongshus": [],
            "seg_zhongshus": [],
            "zhongshus": [],
            "bsps": [],
            "stats": {"kline_count": count},
        }

    monkeypatch.setattr(chan_adapter, "query_klines", fake_query)
    monkeypatch.setattr(chan_adapter, "fetch_klines_quick", lambda symbol, freq: None)
    monkeypatch.setattr(chan_adapter, "_run_chan_py", fake_run)
    monkeypatch.setattr(chan_adapter, "_serialize_one_level", fake_serialize)
    monkeypatch.setattr(chan_adapter, "_extract_level_relations", lambda levels: {"ok": True})

    result = chan_adapter.analyze_structure_sync("sh600519", levels=["day", "m30"], count=50)

    assert result["adapter_version"] == "chan_adapter.v1"
    assert result["symbol"] == "sh.600519"
    assert result["data_source"]["structure"]["provider"] == "baostock"
    assert result["data_source"]["structure"]["adjustflag"] == "2"
    assert result["data_source"]["structure"]["engine"] == "chan.py"
    assert result["freshness"]["is_stale"] is False
    assert result["freshness"]["stale_reason"] == ""
    assert set(result["levels"]) == {"day", "30"}
    assert result["levels"]["day"]["source"]["adapter"] == "server.engines.structure.chan_adapter"
    assert result["level_relations"] == {"ok": True}


def test_analyze_structure_sync_fetches_when_lake_has_too_few_rows(monkeypatch):
    calls = {"query": 0, "fetch": []}
    short_rows = make_rows(20)
    full_rows = make_rows(130)

    def fake_query(symbol, freq, limit):
        calls["query"] += 1
        return short_rows if calls["query"] == 1 else full_rows

    monkeypatch.setattr(chan_adapter, "query_klines", fake_query)
    monkeypatch.setattr(
        chan_adapter,
        "fetch_klines_quick",
        lambda symbol, freq: calls["fetch"].append((symbol, freq)),
    )
    monkeypatch.setattr(
        chan_adapter,
        "_run_chan_py",
        lambda symbol, level_inputs: {item.kl_type: object() for item in level_inputs},
    )
    monkeypatch.setattr(
        chan_adapter,
        "_serialize_one_level",
        lambda kl_data, ctime, rows, raw_freq, count: {
            "freq": raw_freq,
            "klines": [],
            "bis": [],
            "segs": [],
            "bi_zhongshus": [],
            "seg_zhongshus": [],
            "zhongshus": [],
            "bsps": [],
            "stats": {},
        },
    )
    monkeypatch.setattr(chan_adapter, "_extract_level_relations", lambda levels: {})

    result = chan_adapter.analyze_structure_sync("600519", levels=["day"], count=50)

    assert calls["fetch"] == [("sh.600519", "day")]
    assert result["freshness"]["is_stale"] is False


def test_analyze_structure_sync_reports_no_data(monkeypatch):
    monkeypatch.setattr(chan_adapter, "query_klines", lambda symbol, freq, limit: [])
    monkeypatch.setattr(chan_adapter, "fetch_klines_quick", lambda symbol, freq: None)

    result = chan_adapter.analyze_structure_sync("sz000001", levels=["day"], count=50)

    assert result["symbol"] == "sz.000001"
    assert result["freshness"]["is_stale"] is True
    assert result["freshness"]["stale_reason"] == "NO_DATA"
    assert result["error"]["code"] == "NO_DATA"
