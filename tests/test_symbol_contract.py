import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.domain.symbols import (
    normalize_symbol,
    parse_symbol,
    to_tdx_filename,
    to_tencent_symbol,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("sh600519", "sh.600519"),
        ("sh.600519", "sh.600519"),
        ("sh-600519", "sh.600519"),
        ("SH600519", "sh.600519"),
        (" sz000001 ", "sz.000001"),
        ("600519", "sh.600519"),
        ("000001", "sz.000001"),
    ],
)
def test_normalize_symbol_accepts_contract_formats(raw, expected):
    assert normalize_symbol(raw) == expected


def test_parse_symbol_exposes_external_formats():
    symbol = parse_symbol("sh.600519")

    assert symbol.market == "sh"
    assert symbol.code == "600519"
    assert symbol.canonical == "sh.600519"
    assert symbol.tencent == "sh600519"
    assert symbol.tdx_filename == "sh600519.day"


def test_format_helpers_convert_from_any_supported_input():
    assert to_tencent_symbol("sz-000001") == "sz000001"
    assert to_tdx_filename("600519") == "sh600519.day"


@pytest.mark.parametrize("raw", ["", "hk00700", "sh.60051", "foo", None])
def test_normalize_symbol_rejects_unknown_formats(raw):
    with pytest.raises(ValueError):
        normalize_symbol(raw)
