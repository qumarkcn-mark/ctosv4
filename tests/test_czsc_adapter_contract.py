from server.engines.structure import czsc_adapter


class FakeCzscApi:
    @staticmethod
    def format_standard_kline(df, freq):
        assert freq == "日线"
        return list(df.to_dict("records"))

    class CZSC:
        def __init__(self, bars):
            self.bars = bars
            self.fx_list = []
            self.bi_list = []
            self.zs_list = []


def test_czsc_adapter_degrades_when_dependency_missing(monkeypatch):
    monkeypatch.setattr(czsc_adapter, "_load_czsc", lambda: None)

    result = czsc_adapter.analyze_czsc_structure_sync("sh600519", levels=["day"], count=50)

    assert result["engine"] == "czsc"
    assert result["error"] == "CZSC_UNAVAILABLE"
    assert result["levels"] == {}


def test_czsc_adapter_runs_with_fake_czsc(monkeypatch):
    rows = [
        {"date": "2026-01-01", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100, "amount": 1000},
        {"date": "2026-01-02", "open": 10.5, "high": 12, "low": 10, "close": 11.5, "volume": 120, "amount": 1200},
    ]
    calls = {}

    def fake_query(symbol, freq, limit, adjustflag, source):
        calls.update({"symbol": symbol, "freq": freq, "limit": limit, "adjustflag": adjustflag, "source": source})
        return rows

    monkeypatch.setattr(czsc_adapter, "_load_czsc", lambda: FakeCzscApi)
    monkeypatch.setattr(czsc_adapter, "query_klines", fake_query)

    result = czsc_adapter.analyze_czsc_structure_sync("sh600519", levels=["day"], count=50)

    assert result["engine"] == "czsc"
    assert result["error"] == ""
    assert calls == {"symbol": "sh.600519", "freq": "day", "limit": 50, "adjustflag": "2", "source": "baostock"}
    assert result["levels"]["day"]["stats"]["kline_count"] == 2
    assert result["levels"]["day"]["state_hint"] == "no_center"


def test_czsc_raw_bi_context_exports_e1_shape(monkeypatch):
    def fake_analyze(symbol, levels, count, compute_profile):
        return {
            "symbol": symbol,
            "engine": "czsc",
            "error": "",
            "levels": {
                "5": {
                    "level": "5",
                    "price": 240.7,
                    "klines": [{"time": "2026-05-12 15:00:00", "close": 240.7}],
                    "bis": [
                        {
                            "x0": "2026-05-12 09:45:00",
                            "x1": "2026-05-12 10:55:00",
                            "start_price": 231.17,
                            "end_price": 245.69,
                            "high": 245.69,
                            "low": 231.17,
                            "bar_count": 14,
                            "is_up": True,
                            "is_sure": True,
                        },
                        {
                            "x0": "2026-05-12 10:55:00",
                            "x1": "2026-05-12 13:05:00",
                            "start_price": 245.69,
                            "end_price": 236.72,
                            "high": 245.69,
                            "low": 236.72,
                            "bar_count": 26,
                            "is_up": False,
                            "is_sure": True,
                        },
                    ],
                    "bi_zhongshus": [
                        {
                            "begin_date": "2026-05-12 09:45:00",
                            "end_date": "2026-05-12 14:40:00",
                            "zg": 243.49,
                            "zd": 236.72,
                            "gg": 245.69,
                            "dd": 231.17,
                            "bi_count": 5,
                        }
                    ],
                }
            },
        }

    monkeypatch.setattr(czsc_adapter, "analyze_czsc_structure_sync", fake_analyze)

    context = czsc_adapter.export_czsc_raw_bi_context_sync("sh688008", levels=["5"], count=1000)

    assert context["symbol"] == "sh.688008"
    assert context["version"] == "czsc_raw_bi_context.v1"
    level = context["levels"]["5"]
    assert level["level"] == "5分钟"
    assert level["last_close"] == 240.7
    assert level["last_bar_time"] == "2026-05-12 15:00:00"
    assert level["bi_sequence"][0]["direction"] == "UP"
    assert level["bi_sequence"][1]["direction"] == "DOWN"
    assert level["algorithm_zhongshus"][0]["zg"] == 243.49
    assert level["algorithm_zhongshus"][0]["source"] == "czsc_algorithm_suggestion"


def test_czsc_raw_bi_context_reuses_precomputed_structure(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("precomputed_result should avoid a second CZSC analyze pass")

    monkeypatch.setattr(czsc_adapter, "analyze_czsc_structure_sync", forbidden)

    context = czsc_adapter.export_czsc_raw_bi_context_sync(
        "sh688008",
        levels=["5"],
        count=1000,
        precomputed_result={
            "symbol": "sh.688008",
            "engine": "czsc",
            "error": "",
            "levels": {
                "5": {
                    "level": "5",
                    "price": 240.7,
                    "klines": [{"time": "2026-05-12 15:00:00", "close": 240.7}],
                    "bis": [],
                    "bi_zhongshus": [],
                }
            },
        },
    )

    assert context["symbol"] == "sh.688008"
    assert context["levels"]["5"]["last_close"] == 240.7
