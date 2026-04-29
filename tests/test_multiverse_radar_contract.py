"""Multiverse snapshots should read Radar contract levels, not legacy matrix."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services import multiverse_service as multiverse


def make_radar_contract() -> dict:
    def level(public_level: str, price: float) -> dict:
        return {
            "level": public_level,
            "price": price,
            "zg": None,
            "zd": None,
            "state": "UPWARD_LEAVING",
            "patterns": [f"{public_level}底背驰"],
            "zoushi_type": {
                "type": "盘整",
                "zs_count": 1,
                "completion": "中枢离开段",
            },
            "classifications": [
                {"id": "A", "title": "向上突破"},
                {"id": "B", "title": "继续盘整"},
            ],
            "active_zhongshu": {"zg": price + 1, "zd": price - 1},
        }

    return {
        "api_version": "radar.v1",
        "structure": {
            "levels": {
                "day": level("day", 10.0),
                "60": level("60", 10.6),
                "30": level("30", 10.3),
                "15": level("15", 10.15),
                "5": level("5", 10.05),
            }
        },
    }


def test_levels_from_radar_maps_mode_a_to_multiverse_legacy_names():
    levels = multiverse._levels_from_radar(make_radar_contract(), "A")

    assert [level["level"] for level in levels] == ["day", "m30", "m5"]
    assert levels[1]["price"] == 10.3
    assert levels[1]["zg"] == 11.3
    assert levels[1]["zd"] == 9.3
    assert levels[1]["patterns"] == ["30底背驰"]
    assert levels[1]["zoushi_type"]["type"] == "盘整"
    assert levels[1]["classifications"][0]["id"] == "A"


def test_levels_from_radar_maps_mode_b_to_multiverse_legacy_names():
    levels = multiverse._levels_from_radar(make_radar_contract(), "B")

    assert [level["level"] for level in levels] == ["day", "m60", "m15"]
    assert levels[1]["price"] == 10.6
    assert levels[2]["patterns"] == ["15底背驰"]


def test_levels_from_radar_returns_empty_for_missing_contract():
    assert multiverse._levels_from_radar({}, "A") == []
