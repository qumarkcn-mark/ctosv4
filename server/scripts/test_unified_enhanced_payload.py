"""Offline A/B test for enhanced unified reasoning payloads.

Usage:
    cd /Users/markqu/Desktop/ct-os-v4
    ./venv/bin/python -m server.scripts.test_unified_enhanced_payload

If LLM_API_KEY is set, the script calls the configured DeepSeek-compatible API
for both the current flat payload and the enhanced multidimensional payload.
Without LLM_API_KEY, it still writes the input comparison to data/ for review.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from server.engines.ai_native.dynamics_hydrator import hydrate_dynamics


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "ctos.db"
OUTPUT_PATH = ROOT / "data" / "test_unified_enhanced_payload_results.json"

LEVELS = ["week", "day", "30", "5"]
LEVEL_NAMES = {"week": "周线", "day": "日线", "30": "30分钟", "5": "5分钟"}
DEFAULT_SYMBOLS = ["sh.600790", "sz.300394", "sh.688008"]

CURRENT_SYSTEM_PROMPT = """你是用户的缠论盯盘搭档。

输入包含：多级别结构快照、历史压力支撑位、用户持仓。

看完数据，说清楚当下是什么、接下来怎么走、需要盯住什么变化。

不要给买卖、加仓、减仓、止损指令。仅供参考，不构成投资建议。"""

ENHANCED_SYSTEM_PROMPT = """你是用户的缠论盯盘搭档。

输入包含多级别结构几何、动力状态、附近压力支撑、持仓背景和原始笔序列。

基于这些客观数据，自主推演当前走势最可能的演化路径、关键触发价格和需要盯住的变化。

不要给买卖、加仓、减仓、止损指令。仅供参考，不构成投资建议。"""


def main() -> None:
    load_dotenv(ROOT / ".env")
    if not DB_PATH.exists():
        raise SystemExit(f"数据库不存在: {DB_PATH}")

    api_key = os.environ.get("LLM_API_KEY", "") or _user_deepseek_api_key(user_id=1)
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("AI_NATIVE_MODEL", "deepseek-v4-pro")
    symbols = _symbols_from_env() or DEFAULT_SYMBOLS

    print(f"DB: {DB_PATH}")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"LLM: {'enabled' if api_key else 'disabled (LLM_API_KEY 未设置)'}")

    results = []
    for symbol in symbols:
        snapshots = _load_snapshots(symbol)
        if not snapshots:
            print(f"\n--- {symbol} ---\n无可用四级别 snapshot，跳过")
            continue

        current_payload = _build_current_payload(symbol, snapshots)
        enhanced_payload = _build_enhanced_payload(symbol, snapshots)
        record: dict[str, Any] = {
            "symbol": symbol,
            "current_payload": current_payload,
            "enhanced_payload": enhanced_payload,
            "llm_enabled": bool(api_key),
        }

        print(f"\n--- {symbol} ---")
        _print_payload_summary(enhanced_payload)

        if api_key:
            record["current_output"] = _call_llm(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=CURRENT_SYSTEM_PROMPT,
                payload=current_payload,
            )
            time.sleep(3)
            record["enhanced_output"] = _call_llm(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=ENHANCED_SYSTEM_PROMPT,
                payload=enhanced_payload,
            )
            print("\n[当前输入输出]\n" + record["current_output"][:3000])
            print("\n[增强输入输出]\n" + record["enhanced_output"][:3000])
            time.sleep(3)

        results.append(record)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUTPUT_PATH}")


def _symbols_from_env() -> list[str]:
    raw = os.environ.get("TEST_SYMBOLS", "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _load_snapshots(symbol: str) -> dict[str, dict[str, Any]]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        snapshots: dict[str, dict[str, Any]] = {}
        for level in LEVELS:
            row = conn.execute(
                """
                SELECT snapshot_id, data_as_of, snapshot_json, raw_bi_context_json
                  FROM structure_snapshots
                 WHERE symbol = ? AND level = ? AND engine = 'czsc'
                   AND compute_profile = 'chart_standard_v1'
                   AND status = 'fresh'
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (symbol, level),
            ).fetchone()
            if not row:
                continue
            snapshots[level] = {
                "snapshot_id": row["snapshot_id"],
                "data_as_of": row["data_as_of"],
                "snapshot": json.loads(row["snapshot_json"] or "{}"),
                "raw_bi_context": json.loads(row["raw_bi_context_json"] or "{}"),
            }
        return snapshots
    finally:
        conn.close()


def _user_deepseek_api_key(user_id: int) -> str:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT settings_json FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row or not row["settings_json"]:
            return ""
        settings = json.loads(row["settings_json"] or "{}")
        return str(settings.get("deepseek_api_key") or "")
    finally:
        conn.close()


def _build_current_payload(symbol: str, snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    structure = {
        LEVEL_NAMES.get(level, level): _extract_flat_structure(row, LEVEL_NAMES.get(level, level))
        for level, row in snapshots.items()
    }
    current_price = _current_price(snapshots)
    return {
        "symbol": symbol,
        "current_price": current_price,
        "data_as_of": _data_as_of(snapshots),
        "structure": structure,
        "pressure_support": _compute_pressure_support(snapshots),
        "my_position": _mock_position(symbol, current_price),
    }


def _build_enhanced_payload(symbol: str, snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current_price = _current_price(snapshots)
    structure_geometry = {}
    momentum_dynamics = {}
    raw_structure = {}
    for level, row in snapshots.items():
        level_name = LEVEL_NAMES.get(level, level)
        snap = row["snapshot"]
        structure_geometry[level_name] = _hydrate_structure_geometry(row)
        momentum_dynamics[level_name] = _hydrate_dynamics(snap.get("klines") or [])
        raw_structure[level_name] = _extract_flat_structure(row, level_name)

    pressure_support = _add_pressure_support_semantics(
        _compute_pressure_support(snapshots),
        structure_geometry,
    )
    return {
        "symbol": symbol,
        "current_price": current_price,
        "data_as_of": _data_as_of(snapshots),
        "structure_geometry": structure_geometry,
        "momentum_dynamics": momentum_dynamics,
        "nearby_pressure_support": pressure_support,
        "position_context": _mock_position(symbol, current_price),
        "raw_structure": raw_structure,
    }


def _extract_flat_structure(snapshot_data: dict[str, Any], level_name: str) -> dict[str, Any]:
    snap = snapshot_data["snapshot"]
    bis, unfinished_bi = _split_confirmed_and_unfinished_bis(snap)
    result: dict[str, Any] = {
        "level": level_name,
        "data_as_of": snapshot_data["data_as_of"],
        "current_price": snap.get("price"),
        "last_bi_direction": snap.get("last_bi_dir"),
        "state_hint": snap.get("state_hint"),
    }
    active_zs = snap.get("active_zhongshu") or {}
    if active_zs:
        result["active_zhongshu"] = _center_fields(active_zs)
    if snap.get("price_vs_center"):
        result["price_vs_center"] = snap.get("price_vs_center")
    result["recent_bis"] = [_bi_fields(item) for item in bis[-6:]]
    result["total_bi_count"] = len(bis)
    if unfinished_bi:
        result["current_unfinished_bi"] = _bi_fields(unfinished_bi)
    zhongshus = snap.get("bi_zhongshus") or snap.get("zhongshus") or []
    if zhongshus:
        result["recent_zhongshus"] = [_center_fields(item) for item in zhongshus[-2:] if isinstance(item, dict)]
    return result


def _hydrate_structure_geometry(snapshot_data: dict[str, Any]) -> dict[str, Any]:
    snap = snapshot_data["snapshot"]
    price = _num(snap.get("price"))
    active_zs = snap.get("active_zhongshu") or {}
    bis, unfinished_bi = _split_confirmed_and_unfinished_bis(snap)
    center = _center_fields(active_zs) if active_zs else {}
    if center:
        center["maturity"] = _center_maturity(center.get("bi_count"))
        center["maturity_note"] = _center_maturity_note(center["maturity"])
        center["relevance"] = _center_relevance(price, center)
    return {
        "center": center,
        "price_position": _price_position(price, center.get("zg"), center.get("zd")) if center else {"position": "no_center"},
        "unfinished_bi": _bi_fields(unfinished_bi) if unfinished_bi else None,
        "recent_bis": [_bi_fields(item) for item in bis[-6:]],
        "total_confirmed_bi_count": len(bis),
    }


def _hydrate_dynamics(klines: list[dict[str, Any]]) -> dict[str, Any]:
    return hydrate_dynamics(klines)


def _compute_pressure_support(snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    swing_points: list[dict[str, Any]] = []
    current_price = _current_price(snapshots)
    if current_price <= 0:
        return []
    for level, row in snapshots.items():
        snap = row["snapshot"]
        price = _num(snap.get("price")) or current_price
        bis, _unfinished_bi = _split_confirmed_and_unfinished_bis(snap)
        for bi in bis[-10:]:
            high = _num(bi.get("high") or bi.get("end_price"))
            low = _num(bi.get("low") or bi.get("start_price"))
            if high > 0 and abs(high - price) / price < 0.15:
                swing_points.append({"price": high, "type": "high", "level": level})
            if low > 0 and abs(low - price) / price < 0.15:
                swing_points.append({"price": low, "type": "low", "level": level})
    if not swing_points:
        return []

    clusters: list[list[dict[str, Any]]] = []
    sorted_points = sorted(swing_points, key=lambda item: item["price"])
    current = [sorted_points[0]]
    for point in sorted_points[1:]:
        if point["price"] / current[0]["price"] - 1 < 0.015:
            current.append(point)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [point]
    if len(current) >= 2:
        clusters.append(current)

    result = []
    for cluster in clusters:
        prices = [item["price"] for item in cluster]
        zone_low = min(prices)
        zone_high = max(prices)
        center = (zone_low + zone_high) / 2
        distance_pct = round((center - current_price) / current_price * 100, 1)
        result.append(
            {
                "zone": [round(zone_low, 4), round(zone_high, 4)],
                "type": "pressure" if center > current_price else "support",
                "source_levels": sorted({item["level"] for item in cluster}),
                "hit_count": len(cluster),
                "distance_pct": distance_pct,
                "status": "testing" if abs(distance_pct) < 1 else "holding",
            }
        )
    return sorted(result, key=lambda item: abs(item["distance_pct"]))[:6]


def _add_pressure_support_semantics(
    clusters: list[dict[str, Any]],
    structure_geometry: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for cluster in clusters:
        zone = cluster.get("zone") or []
        if len(zone) != 2:
            result.append(cluster)
            continue
        center_price = (_num(zone[0]) + _num(zone[1])) / 2
        semantics = []
        for level_name, geometry in structure_geometry.items():
            center = geometry.get("center") or {}
            relevance = center.get("relevance")
            if relevance == "distant_context":
                continue
            for key, label in (
                ("zg", "接近中枢上沿ZG，属于离开后回拉观察边界"),
                ("zd", "接近中枢下沿ZD，属于跌破后反抽观察边界"),
            ):
                value = _num(center.get(key))
                if value > 0 and abs(center_price - value) / value < 0.01:
                    semantics.append(f"{level_name}:{label}")
        enriched = dict(cluster)
        if semantics:
            enriched["semantic"] = "；".join(semantics[:2])
        result.append(enriched)
    return result


def _split_confirmed_and_unfinished_bis(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    raw_bis = [item for item in (snapshot.get("bis") or []) if isinstance(item, dict)]
    raw_unfinished = snapshot.get("unfinished_bi") if isinstance(snapshot.get("unfinished_bi"), dict) else None
    if raw_unfinished:
        return raw_bis, raw_unfinished
    if raw_bis and _is_unfinished_bi(raw_bis[-1]):
        return raw_bis[:-1], raw_bis[-1]
    return raw_bis, None


def _is_unfinished_bi(item: dict[str, Any]) -> bool:
    return bool(item.get("is_sure") is False or item.get("source") == "czsc_ubi" or item.get("status") == "ongoing")


def _center_fields(center: dict[str, Any]) -> dict[str, Any]:
    return {
        "zg": center.get("zg"),
        "zd": center.get("zd"),
        "gg": center.get("gg"),
        "dd": center.get("dd"),
        "bi_count": center.get("bi_count"),
        "begin_date": center.get("begin_date"),
        "end_date": center.get("end_date"),
    }


def _bi_fields(bi: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bi:
        return None
    return {
        "direction": bi.get("direction"),
        "start_price": bi.get("start_price"),
        "end_price": bi.get("end_price"),
        "high": bi.get("high"),
        "low": bi.get("low"),
        "bar_count": bi.get("bar_count"),
        "is_sure": bi.get("is_sure"),
        "status": bi.get("status"),
    }


def _center_maturity(bi_count: Any) -> str:
    count = int(_num(bi_count))
    if count <= 3:
        return "forming"
    if count <= 5:
        return "normal_extension"
    if count <= 8:
        return "late_extension"
    return "upgrade_watch"


def _center_maturity_note(maturity: str) -> str:
    return {
        "forming": "中枢刚形成，重点看是否继续延伸或快速离开",
        "normal_extension": "中枢正常延伸，方向仍需等待离开与回拉确认",
        "late_extension": "中枢延伸较充分，需关注离开确认或升级扩展",
        "upgrade_watch": "中枢延伸充分，需观察离开确认、三买三卖或升级扩展",
    }.get(maturity, "")


def _center_relevance(price: float, center: dict[str, Any]) -> str:
    zg = _num(center.get("zg"))
    zd = _num(center.get("zd"))
    if price <= 0 or zg <= 0 or zd <= 0:
        return "unknown"
    nearest = min(abs(price - zg) / zg, abs(price - zd) / zd)
    return "distant_context" if nearest > 0.2 else "active_boundary"


def _price_position(price: float, zg: Any, zd: Any) -> dict[str, Any]:
    upper = _num(zg)
    lower = _num(zd)
    if price <= 0 or upper <= 0 or lower <= 0:
        return {"position": "no_center"}
    position = "above_zg" if price > upper else "below_zd" if price < lower else "in_center"
    return {
        "position": position,
        "distance_to_zg_pct": round((price - upper) / upper * 100, 2),
        "distance_to_zd_pct": round((price - lower) / lower * 100, 2),
    }


def _current_price(snapshots: dict[str, dict[str, Any]]) -> float:
    for level in ("day", "30", "5", "week"):
        if level in snapshots:
            price = _num(snapshots[level]["snapshot"].get("price"))
            if price > 0:
                return price
    return 0.0


def _data_as_of(snapshots: dict[str, dict[str, Any]]) -> str:
    for level in ("day", "30", "5", "week"):
        if level in snapshots and snapshots[level].get("data_as_of"):
            return str(snapshots[level]["data_as_of"])
    return ""


def _mock_position(symbol: str, current_price: float) -> dict[str, Any]:
    if symbol == "sh.600790":
        cost = 4.22
        return {"holding": True, "shares": 20000, "cost": cost, "current_pnl_pct": round((current_price - cost) / cost * 100, 2)}
    return {"holding": False, "shares": 0, "cost": 0, "note": "测试样例按空仓背景处理"}


def _num(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed else 0.0


def _call_llm(*, api_key: str, base_url: str, model: str, system_prompt: str, payload: dict[str, Any]) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "以下是真实结构数据，请直接给出你的推演：\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 3500,
    }
    with httpx.Client(timeout=150) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
    return str(data["choices"][0]["message"]["content"]).strip()


def _print_payload_summary(payload: dict[str, Any]) -> None:
    print(f"current_price: {payload.get('current_price')}")
    for level_name, geometry in (payload.get("structure_geometry") or {}).items():
        center = geometry.get("center") or {}
        position = geometry.get("price_position") or {}
        dynamics = (payload.get("momentum_dynamics") or {}).get(level_name) or {}
        print(
            f"{level_name}: maturity={center.get('maturity')} relevance={center.get('relevance')} "
            f"position={position.get('position')} dzg={position.get('distance_to_zg_pct')} "
            f"macd={dynamics.get('macd_state')}/{dynamics.get('macd_momentum')} "
            f"vol={dynamics.get('volume_state')} ma={dynamics.get('ma_posture')}"
        )
    print("nearby_pressure_support:")
    for item in (payload.get("nearby_pressure_support") or [])[:4]:
        print(f"  {item}")


if __name__ == "__main__":
    main()
