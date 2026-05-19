from server.engines.structure.czsc_serializer import serialize_czsc_level


class Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeBI:
    def __init__(self, fx_a, fx_b, direction="Up", high=12.0, low=10.0):
        self.fx_a = fx_a
        self.fx_b = fx_b
        self.direction = direction
        self.high = high
        self.low = low

    def get_high(self):
        return self.high

    def get_low(self):
        return self.low


class FakeZS:
    def __init__(self):
        self.sdt = "2026-01-02"
        self.edt = "2026-01-05"
        self.zg = 11.5
        self.zd = 10.5
        self.zz = 11.0
        self.gg = 12.2
        self.dd = 10.1
        self.sdir = "Up"
        self.edir = "Down"
        self.bis = [1, 2, 3]

    def is_valid(self):
        return True


def test_serialize_czsc_level_exposes_stable_contract():
    fx_a = Obj(dt="2026-01-01", mark="D", high=11, low=10, fx=10)
    fx_b = Obj(dt="2026-01-02", mark="G", high=12, low=11, fx=12)
    czsc_obj = Obj(
        fx_list=[fx_a, fx_b],
        bi_list=[FakeBI(fx_a, fx_b)],
        zs_list=[FakeZS()],
    )

    result = serialize_czsc_level(
        czsc_obj,
        rows=[{"date": "2026-01-06", "open": 11, "high": 12, "low": 10, "close": 11.8, "volume": 100}],
        level="day",
    )

    assert result["level"] == "day"
    assert result["stats"] == {
        "kline_count": 1,
        "fx_count": 2,
        "bi_count": 1,
        "bi_zs_count": 1,
        "seg_count": 0,
        "seg_zs_count": 0,
        "bsp_count": 0,
    }
    assert result["active_zhongshu"]["zg"] == 11.5
    assert result["state_hint"] == "above_zg"
    assert result["bis"][0]["direction"] == "up"
    assert result["segs"] == []
    assert "segs" in result["metadata"]["unsupported_fields"]
    assert result["metadata"]["segment_source"] == "unavailable_in_czsc_object"


def test_serialize_czsc_level_serializes_segments_when_czsc_exposes_them():
    fx_a = Obj(dt="2026-01-01", mark="D", high=11, low=10, fx=10)
    fx_b = Obj(dt="2026-01-05", mark="G", high=13, low=12, fx=13)
    czsc_obj = Obj(
        fx_list=[fx_a, fx_b],
        bi_list=[],
        seg_list=[FakeBI(fx_a, fx_b, high=13.5, low=9.8)],
        zs_list=[],
    )

    result = serialize_czsc_level(
        czsc_obj,
        rows=[{"date": "2026-01-06", "open": 11, "high": 13, "low": 10, "close": 12.8, "volume": 100}],
        level="day",
    )

    assert result["segs"][0]["start_price"] == 10
    assert result["segments"][0]["end_price"] == 13
    assert result["stats"]["seg_count"] == 1
    assert "segs" not in result["metadata"]["unsupported_fields"]
    assert result["metadata"]["segment_source"] == "czsc_object"


def test_serialize_czsc_level_does_not_derive_segments_from_bis_when_native_missing():
    def fx(dt, price):
        return Obj(dt=dt, mark="", high=price, low=price, fx=price)

    points = [
        ("2026-01-01", 10.0),
        ("2026-01-02", 12.0),
        ("2026-01-03", 11.0),
        ("2026-01-04", 13.0),
        ("2026-01-05", 10.0),
        ("2026-01-06", 11.0),
        ("2026-01-07", 9.0),
        ("2026-01-08", 10.0),
    ]
    bis = []
    for index, ((start_dt, start_price), (end_dt, end_price)) in enumerate(zip(points, points[1:])):
        direction = "Up" if end_price >= start_price else "Down"
        bis.append(
            FakeBI(
                fx(start_dt, start_price),
                fx(end_dt, end_price),
                direction=direction,
                high=max(start_price, end_price),
                low=min(start_price, end_price),
            )
        )
    czsc_obj = Obj(fx_list=[], bi_list=bis, zs_list=[])

    result = serialize_czsc_level(
        czsc_obj,
        rows=[{"date": "2026-01-08", "open": 9.5, "high": 10.2, "low": 9.0, "close": 10.0, "volume": 100}],
        level="day",
    )

    assert result["metadata"]["segment_source"] == "unavailable_in_czsc_object"
    assert result["stats"]["seg_count"] == 0
    assert result["segs"] == []
    assert "segs" in result["metadata"]["unsupported_fields"]


def test_serialize_czsc_level_appends_unfinished_bi():
    fx_a = Obj(dt="2026-01-01", mark="D", high=11, low=10, fx=10)
    raw_bars = [
        Obj(dt="2026-01-02"),
        Obj(dt="2026-01-03"),
    ]
    czsc_obj = Obj(
        fx_list=[fx_a],
        bi_list=[],
        zs_list=[],
        ubi={
            "direction": "Up",
            "fx_a": fx_a,
            "raw_bars": raw_bars,
            "high": 12.8,
            "low": 10.2,
        },
    )

    result = serialize_czsc_level(
        czsc_obj,
        rows=[{"date": "2026-01-03", "open": 10, "high": 12.8, "low": 10, "close": 12.5, "volume": 100}],
        level="day",
    )

    assert result["stats"]["bi_count"] == 1
    assert result["bis"][0]["is_sure"] is False
    assert result["bis"][0]["source"] == "czsc_ubi"
    assert result["bis"][0]["status"] == "ongoing"
    assert result["bis"][0]["x1"] == "2026-01-03"
    assert result["bis"][0]["end_price"] == 12.8
