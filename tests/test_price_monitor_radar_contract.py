"""Price monitor should read trailing-stop candidates from Radar contract."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.workers.price_monitor import _m30_trailing_stop_from_radar


def test_m30_trailing_stop_prefers_operative_zg():
    radar_data = {
        "structure": {
            "levels": {
                "30": {
                    "zg": 10.5,
                    "zs_operative_zg": 10.8,
                    "active_zhongshu": {"zg": 10.2},
                }
            }
        }
    }

    assert _m30_trailing_stop_from_radar(radar_data) == 10.8


def test_m30_trailing_stop_falls_back_to_active_zhongshu():
    radar_data = {
        "structure": {
            "levels": {
                "30": {
                    "active_zhongshu": {"zg": 10.2},
                }
            }
        }
    }

    assert _m30_trailing_stop_from_radar(radar_data) == 10.2


def test_m30_trailing_stop_handles_missing_contract():
    assert _m30_trailing_stop_from_radar({}) == 0
