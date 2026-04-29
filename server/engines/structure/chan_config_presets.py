"""CChan configuration presets shared by structure services.

这里是 CT-OS 调用 CChan/chan.py 的唯一配置表。图层开关控制"画什么"，
这里控制"算法怎么算"。两者必须分开，避免 UI 调参影响结构可信度。
"""

from copy import deepcopy
from typing import Optional


CONFIG_VERSION = "cchan_config.v1"
DEFAULT_PRESET = "live_tolerant"

_COMMON_RUNTIME = {
    "trigger_step": True,
    "kl_data_check": False,
    "print_warning": False,
    "print_err_time": False,
    "auto_skip_illegal_sub_lv": True,
}

_PRESETS = {
    "live_tolerant": {
        "label": "实盘容错",
        "description": "兼容 A 股跳空、涨停和短笔，适合日常看盘。",
        "config": {
            **_COMMON_RUNTIME,
            "bi_strict": False,
            "bi_fx_check": "loss",
            "gap_as_kl": True,
        },
        "effective": {
            "trigger_step": True,
            "bi_strict": False,
            "bi_fx_check": "loss",
            "gap_as_kl": True,
            "seg_algo": "chan",
            "left_seg_method": "peak",
            "zs_combine": True,
            "zs_combine_mode": "zs",
            "zs_algo": "normal",
            "bs_type": ["1", "1p", "2", "2s", "3a", "3b"],
            "macd_algo": "peak",
            "divergence_rate": "inf",
        },
    },
    "textbook_strict": {
        "label": "严格验算",
        "description": "更接近默认严格笔，用于学习、复核和排错。",
        "config": {
            **_COMMON_RUNTIME,
            "bi_strict": True,
            "bi_fx_check": "strict",
            "gap_as_kl": False,
        },
        "effective": {
            "trigger_step": True,
            "bi_strict": True,
            "bi_fx_check": "strict",
            "gap_as_kl": False,
            "seg_algo": "chan",
            "left_seg_method": "peak",
            "zs_combine": True,
            "zs_combine_mode": "zs",
            "zs_algo": "normal",
            "bs_type": ["1", "1p", "2", "2s", "3a", "3b"],
            "macd_algo": "peak",
            "divergence_rate": "inf",
        },
    },
    "sensitive_probe": {
        "label": "敏感观察",
        "description": "保留宽松笔，并提高买卖点观察敏感度，用于推演候选。",
        "config": {
            **_COMMON_RUNTIME,
            "bi_strict": False,
            "bi_fx_check": "loss",
            "gap_as_kl": True,
            "bsp1_only_multibi_zs": False,
            "bsp3_peak": False,
            "strict_bsp3": False,
        },
        "effective": {
            "trigger_step": True,
            "bi_strict": False,
            "bi_fx_check": "loss",
            "gap_as_kl": True,
            "seg_algo": "chan",
            "left_seg_method": "peak",
            "zs_combine": True,
            "zs_combine_mode": "zs",
            "zs_algo": "normal",
            "bs_type": ["1", "1p", "2", "2s", "3a", "3b"],
            "macd_algo": "peak",
            "divergence_rate": "inf",
            "bsp1_only_multibi_zs": False,
            "bsp3_peak": False,
            "strict_bsp3": False,
        },
    },
}


def allowed_preset_names() -> list[str]:
    return list(_PRESETS.keys())


def normalize_preset_name(preset: Optional[str]) -> str:
    name = (preset or DEFAULT_PRESET).strip()
    if name not in _PRESETS:
        allowed = ", ".join(allowed_preset_names())
        raise ValueError(f"unsupported CChan preset: {name}; allowed: {allowed}")
    return name


def get_chan_config_dict(preset: Optional[str] = None) -> dict:
    name = normalize_preset_name(preset)
    return deepcopy(_PRESETS[name]["config"])


def get_chan_config_meta(preset: Optional[str] = None) -> dict:
    name = normalize_preset_name(preset)
    item = _PRESETS[name]
    return {
        "preset": name,
        "label": item["label"],
        "description": item["description"],
        "version": CONFIG_VERSION,
        "allowed_presets": allowed_preset_names(),
        "effective": deepcopy(item["effective"]),
    }
