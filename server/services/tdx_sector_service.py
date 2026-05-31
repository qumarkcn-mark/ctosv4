"""Read TDX local industry / sector mappings.

通达信本地 `T0002/hq_cache` 里有两类对推演很有用的数据：
- `tdxhy.cfg`: 股票 -> 行业代码
- `tdxzs3.cfg`: 行业代码 -> 板块指数代码 / 名称
- `tdxzsbase.cfg`: 板块指数的当日、5日、20日表现摘要
- `infoharbor_block.dat`: 股票 -> 概念 / 风格 / 指数成分标签

这里只做只读解析，不写数据库，也不参与正式 CZSC 结构计算。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from server.config import TDX_ROOT, TDX_VIPDOC
from server.domain.symbols import normalize_symbol


CONCEPT_KEYWORD_PRIORITY = (
    "CPO",
    "光通信",
    "算力",
    "数据中心",
    "英伟达",
    "先进封装",
    "存储芯片",
    "芯片",
    "半导体",
    "人工智能",
    "AI",
    "机器人",
    "人形机器",
    "机器视觉",
    "工业互联",
    "智能电网",
    "储能",
    "新能源车",
    "氢能源",
    "光伏",
    "热泵",
    "一带一路",
)

CONCEPT_NAME_BLOCKLIST = {
    "通达信88",
}


def get_tdx_sector_context(symbol: str) -> dict[str, Any]:
    """Return exact TDX sector facts for a stock when local files are available."""
    canonical = normalize_symbol(symbol)
    code = canonical.split(".")[-1]
    root = _resolve_tdx_root()
    if not root:
        return {}
    hq_cache = root / "T0002" / "hq_cache"
    stock_map = _read_tdxhy(hq_cache / "tdxhy.cfg")
    row = stock_map.get(code)
    if not row:
        return {}
    sector_defs = _read_tdxzs(hq_cache / "tdxzs3.cfg")
    base_stats = _read_tdxzsbase(hq_cache / "tdxzsbase.cfg")
    industry = _sector_path(row.get("tdx_industry_code", ""), sector_defs)
    concept = _sector_path(row.get("industry_code", ""), sector_defs)
    primary = concept[-1] if concept else industry[-1] if industry else {}
    if not primary:
        return {}
    primary_code = str(primary.get("index") or "")
    concept_themes = _select_concept_themes(
        _read_infoharbor_blocks(hq_cache / "infoharbor_block.dat").get(code, [])
    )
    return {
        "source": "tdx_hq_cache",
        "symbol": canonical,
        "primary_sector": {
            "name": primary.get("name", ""),
            "index_code": primary_code,
            "taxonomy_code": primary.get("taxonomy_code", ""),
            "path": [item.get("name", "") for item in (concept or industry) if item.get("name")],
        },
        "tdx_industry": {
            "code": row.get("tdx_industry_code", ""),
            "path": [item.get("name", "") for item in industry if item.get("name")],
        },
        "industry": {
            "code": row.get("industry_code", ""),
            "path": [item.get("name", "") for item in concept if item.get("name")],
        },
        "concept_themes": concept_themes,
        "daily_stats": base_stats.get(primary_code) or {},
    }


@lru_cache(maxsize=1)
def _resolve_tdx_root() -> Path | None:
    candidates: list[Path] = []
    if TDX_ROOT:
        candidates.append(Path(TDX_ROOT))
    if TDX_VIPDOC:
        vipdoc = Path(TDX_VIPDOC)
        candidates.extend([vipdoc, vipdoc.parent])
    candidates.extend(
        [
            Path("/Users/markqu/Desktop/new_tdx64_mount"),
            Path("/Volumes/new_tdx64"),
        ]
    )
    for candidate in candidates:
        if (candidate / "T0002" / "hq_cache" / "tdxhy.cfg").exists():
            return candidate
    return None


@lru_cache(maxsize=2)
def _read_tdxhy(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="gbk", errors="ignore").splitlines():
        parts = line.split("|")
        if len(parts) < 6:
            continue
        market, code, tdx_industry_code, _, _, industry_code = parts[:6]
        if code:
            result[code] = {
                "market": market,
                "code": code,
                "tdx_industry_code": tdx_industry_code,
                "industry_code": industry_code,
            }
    return result


@lru_cache(maxsize=2)
def _read_tdxzs(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="gbk", errors="ignore").splitlines():
        parts = line.split("|")
        if len(parts) < 6:
            continue
        name, index, category, _, leaf_flag, taxonomy_code = parts[:6]
        if taxonomy_code:
            result[taxonomy_code] = {
                "name": name,
                "index": index,
                "category": category,
                "is_leaf": leaf_flag,
                "taxonomy_code": taxonomy_code,
            }
    return result


@lru_cache(maxsize=2)
def _read_tdxzsbase(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="gbk", errors="ignore").splitlines():
        parts = line.split("|")
        if len(parts) < 24:
            continue
        code = parts[1]
        result[code] = {
            "date": parts[7],
            "ret_1": _num(parts[9]),
            "ret_5": _num(parts[21]),
            "ret_20": _num(parts[14]),
            "ret_60": _num(parts[15]),
            "ret_ytd": _num(parts[17]),
        }
    return result


@lru_cache(maxsize=2)
def _read_infoharbor_blocks(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Parse TDX concept memberships from `infoharbor_block.dat`.

    Header example:
    `#GN_CPO概念,42,880xxx,...`
    Member example:
    `0#300394,1#600xxx`
    """
    if not path.exists():
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    current: dict[str, Any] | None = None
    for raw in path.read_text(encoding="gbk", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            current = _parse_block_header(line)
            continue
        if not current:
            continue
        for token in line.split(","):
            code = token.split("#")[-1].strip()
            if len(code) != 6 or not code.isdigit():
                continue
            result.setdefault(code, []).append(dict(current))
    return result


def _parse_block_header(line: str) -> dict[str, Any]:
    header = line.lstrip("#")
    parts = header.split(",")
    raw_name = parts[0].strip()
    category, _, name = raw_name.partition("_")
    return {
        "category": category,
        "name": name or raw_name,
        "raw_name": raw_name,
        "index_code": parts[2].strip() if len(parts) > 2 else "",
        "member_count": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
    }


def _select_concept_themes(items: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    concepts = [
        item
        for item in items
        if item.get("category") == "GN"
        and item.get("name")
        and str(item.get("name") or "") not in CONCEPT_NAME_BLOCKLIST
    ]
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in concepts:
        name = str(item.get("name") or "")
        if name in seen:
            continue
        seen.add(name)
        unique.append(item)
    unique.sort(key=_concept_rank)
    return [
        {
            "name": item.get("name", ""),
            "index_code": item.get("index_code", ""),
            "priority": _concept_priority(str(item.get("name") or "")),
            "source": "tdx_infoharbor_block",
        }
        for item in unique[:limit]
    ]


def _concept_rank(item: dict[str, Any]) -> tuple[int, int, str]:
    name = str(item.get("name") or "")
    priority = _concept_priority(name)
    member_count = int(item.get("member_count") or 0)
    return (priority, member_count, name)


def _concept_priority(name: str) -> int:
    for index, keyword in enumerate(CONCEPT_KEYWORD_PRIORITY):
        if keyword and keyword in name:
            return index
    return len(CONCEPT_KEYWORD_PRIORITY)


def _sector_path(code: str, sector_defs: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    if not code:
        return []
    matches = [
        item
        for taxonomy_code, item in sector_defs.items()
        if code.startswith(taxonomy_code) and taxonomy_code[0] == code[0]
    ]
    return sorted(matches, key=lambda item: len(item.get("taxonomy_code", "")))


def _num(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0
