"""CChan config preset contract tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.structure.chan_config_presets import (
    DEFAULT_PRESET,
    CONFIG_VERSION,
    allowed_preset_names,
    get_chan_config_dict,
    get_chan_config_meta,
    normalize_preset_name,
)


def test_default_preset_matches_current_live_tolerant_runtime_config():
    config = get_chan_config_dict()
    meta = get_chan_config_meta()

    assert normalize_preset_name(None) == DEFAULT_PRESET
    assert meta["preset"] == "live_tolerant"
    assert meta["version"] == CONFIG_VERSION
    assert config["trigger_step"] is True
    assert config["kl_data_check"] is False
    assert config["bi_strict"] is False
    assert config["bi_fx_check"] == "loss"
    assert config["gap_as_kl"] is True
    assert config["auto_skip_illegal_sub_lv"] is True


def test_textbook_strict_preset_exposes_readable_metadata():
    config = get_chan_config_dict("textbook_strict")
    meta = get_chan_config_meta("textbook_strict")

    assert config["bi_strict"] is True
    assert config["bi_fx_check"] == "strict"
    assert config["gap_as_kl"] is False
    assert meta["label"] == "严格验算"
    assert meta["effective"]["bi_strict"] is True
    assert meta["effective"]["bs_type"] == ["1", "1p", "2", "2s", "3a", "3b"]


def test_invalid_preset_lists_allowed_names():
    try:
        get_chan_config_dict("unknown")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("invalid preset should raise ValueError")

    assert "unsupported CChan preset" in message
    for name in allowed_preset_names():
        assert name in message
