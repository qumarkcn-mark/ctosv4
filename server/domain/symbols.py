"""Symbol normalization contract.

内部统一使用 BaoStock 风格的 canonical symbol：sh.600519 / sz.000001。
外部接口适配只在边界层做，业务层不要自己拼接 symbol。
"""

import re
from dataclasses import dataclass


_CANONICAL_RE = re.compile(r"^(sh|sz)\.(\d{6})$")
_COMPACT_RE = re.compile(r"^(sh|sz)(\d{6})$")
_DASH_RE = re.compile(r"^(sh|sz)-(\d{6})$")
_PLAIN_CODE_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class Symbol:
    market: str
    code: str

    @property
    def canonical(self) -> str:
        return f"{self.market}.{self.code}"

    @property
    def tencent(self) -> str:
        return f"{self.market}{self.code}"

    @property
    def tdx_filename(self) -> str:
        return f"{self.tencent}.day"


def normalize_symbol(raw_symbol: str) -> str:
    """Normalize supported A-share symbol input to sh.600519 / sz.000001."""
    return parse_symbol(raw_symbol).canonical


def parse_symbol(raw_symbol: str) -> Symbol:
    if raw_symbol is None:
        raise ValueError("symbol is required")

    value = str(raw_symbol).strip().lower()
    if not value:
        raise ValueError("symbol is required")

    value = value.replace(" ", "")

    for pattern in (_CANONICAL_RE, _COMPACT_RE, _DASH_RE):
        match = pattern.match(value)
        if match:
            return Symbol(market=match.group(1), code=match.group(2))

    if _PLAIN_CODE_RE.match(value):
        return Symbol(market=_infer_market(value), code=value)

    raise ValueError(f"unsupported symbol format: {raw_symbol}")


def to_tencent_symbol(raw_symbol: str) -> str:
    """Convert any supported symbol input to Tencent quote API format."""
    return parse_symbol(raw_symbol).tencent


def symbol_aliases(raw_symbol: str) -> tuple[str, str, str]:
    """Return common persisted forms for a symbol.

    旧数据里同时存在 sh600519 / sh.600519 / 600519。查询用户持仓等
    人工录入数据时必须兼容这些形态，结构和行情内部仍使用 canonical。
    """
    parsed = parse_symbol(raw_symbol)
    return (parsed.canonical, parsed.tencent, parsed.code)


def to_tdx_filename(raw_symbol: str) -> str:
    """Convert any supported symbol input to TDX .day filename."""
    return parse_symbol(raw_symbol).tdx_filename


def _infer_market(code: str) -> str:
    # A 股常用规则：6/5/9 开头归上海，其余默认深圳。
    if code.startswith(("6", "5", "9")):
        return "sh"
    return "sz"
