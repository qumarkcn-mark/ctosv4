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
        "seg_zs_count": 0,
        "bsp_count": 0,
    }
    assert result["active_zhongshu"]["zg"] == 11.5
    assert result["state_hint"] == "above_zg"
    assert result["bis"][0]["direction"] == "up"
