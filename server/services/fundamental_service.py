"""选股扫描器基本面调研服务。

MVP 设计：
1. 先把技术扫描结果整理成 research context。
2. 如果配置了 DeepSeek API Key，用 LLM 输出严格 JSON。
3. 如果未配置或调用失败，降级为技术面摘要，保证 scan_results 不堵在 pending。

后续 GitHub skill / 东方财富抓取都接在 build_research_context 这一层，不影响 worker/API。
"""

import asyncio
import json
import logging
import os
from typing import Iterable

from server.db.database import get_connection

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位专注A股的基本面调研助理，服务于短线技术交易者。
你的任务是根据输入的技术扫描候选和调研上下文，判断是否存在基本面红旗。

严格要求：
1. 只输出 JSON，不输出 Markdown。
2. 不给买入、卖出、仓位、目标价、止损建议。
3. verdict 只能是 "支持"、"中性"、"回避"。
4. red_flags 只放重大风险，例如减持、立案调查、连续亏损、退市风险、流动性过低。
5. 如果上下文不足，verdict 用 "中性"，不要编造信息。

输出格式：
{
  "verdict": "支持 | 中性 | 回避",
  "summary": "一句话结论（不超过30字）",
  "pros": ["利多点"],
  "cons": ["风险点"],
  "red_flags": ["重大风险"]
}
"""


def _get_api_key() -> str:
    """读取 DeepSeek API Key。优先用户设置，其次环境变量。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT settings_json FROM users WHERE id=1").fetchone()
        if row and row["settings_json"]:
            settings = json.loads(row["settings_json"] or "{}")
            if settings.get("deepseek_api_key"):
                return settings["deepseek_api_key"]
    except Exception:
        logger.debug("读取用户 DeepSeek API Key 失败", exc_info=True)
    finally:
        conn.close()
    return os.environ.get("LLM_API_KEY", "")


def _fetch_scan_row(conn, result_id: int):
    return conn.execute(
        """
        SELECT id, symbol, strategy, score, close, stop_loss, target,
               rr_ratio, atr_pct, volume_ratio, chan_desc
          FROM scan_results
         WHERE id = ?
        """,
        (result_id,),
    ).fetchone()


def build_research_context(row) -> dict:
    """构造调研上下文。

    当前 MVP 只有技术扫描上下文。后续可在这里追加东方财富/公告/新闻/skill 输出。
    """
    return {
        "symbol": row["symbol"],
        "strategy": row["strategy"],
        "technical": {
            "score": row["score"],
            "close": row["close"],
            "stop_loss": row["stop_loss"],
            "target": row["target"],
            "rr_ratio": row["rr_ratio"],
            "atr_pct": row["atr_pct"],
            "volume_ratio": row["volume_ratio"],
            "chan_desc": row["chan_desc"],
        },
        "research_backend": "technical_only_mvp",
        "sources": [],
    }


def fallback_analysis(context: dict, reason: str = "research_unavailable") -> dict:
    """无 LLM 或调研失败时的确定性降级结果。"""
    tech = context.get("technical", {})
    pros = []
    cons = []

    rr = tech.get("rr_ratio") or 0
    atr_pct = tech.get("atr_pct") or 0
    if rr >= 2:
        pros.append(f"技术赔率约1:{rr:.1f}")
    if atr_pct and atr_pct >= 0.08:
        cons.append(f"ATR止损幅度偏大（{atr_pct:.1%}）")

    return {
        "verdict": "中性",
        "summary": "仅技术面通过，待基本面确认",
        "pros": pros,
        "cons": cons or ["基本面调研未接入"],
        "red_flags": [],
        "meta": {"fallback": True, "reason": reason},
    }


def _clean_string_list(value, *, max_items: int = 5, max_chars: int = 80) -> list[str]:
    """清洗 LLM 返回的数组字段，只保留短字符串，避免结构漂移写入 DB。"""
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value[:max_items]:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            cleaned.append(text[:max_chars])
    return cleaned


async def _llm_analyze(context: dict) -> dict:
    api_key = _get_api_key()
    if not api_key or api_key == "dummy_key_replace_in_prod":
        return fallback_analysis(context, "missing_api_key")

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        response = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
    except Exception as exc:
        logger.warning("基本面 LLM 调研失败，降级为技术面摘要: %s", exc)
        return fallback_analysis(context, str(exc)[:120])

    verdict = data.get("verdict")
    if verdict not in ("支持", "中性", "回避"):
        verdict = "中性"
    return {
        "verdict": verdict,
        "summary": str(data.get("summary") or "基本面信息不足")[:60],
        "pros": _clean_string_list(data.get("pros")),
        "cons": _clean_string_list(data.get("cons")),
        "red_flags": _clean_string_list(data.get("red_flags")),
        "meta": {"fallback": False, "reason": ""},
    }


def _write_analysis(result_id: int, context: dict, analysis: dict, status: str = "ready"):
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE scan_results
               SET status = ?,
                   fundamental = ?,
                   llm_verdict = ?,
                   llm_summary = ?,
                   llm_pros = ?,
                   llm_cons = ?,
                   llm_red_flags = ?,
                   fundamental_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (
                status,
                json.dumps(context, ensure_ascii=False),
                analysis["verdict"],
                analysis["summary"],
                json.dumps(analysis["pros"], ensure_ascii=False),
                json.dumps(analysis["cons"], ensure_ascii=False),
                json.dumps(analysis["red_flags"], ensure_ascii=False),
                result_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_status(result_id: int, status: str, retry_increment: bool = False):
    conn = get_connection()
    try:
        if retry_increment:
            conn.execute(
                "UPDATE scan_results SET status=?, retry_count=retry_count+1 WHERE id=?",
                (status, result_id),
            )
        else:
            conn.execute("UPDATE scan_results SET status=? WHERE id=?", (status, result_id))
        conn.commit()
    finally:
        conn.close()


async def analyze_one(result_id: int, symbol: str, strategy: str) -> dict:
    """分析单条候选股并写回 scan_results。"""
    _mark_status(result_id, "analyzing")
    conn = get_connection()
    try:
        row = _fetch_scan_row(conn, result_id)
    finally:
        conn.close()

    if row is None:
        return {"id": result_id, "status": "missing"}

    try:
        context = build_research_context(row)
        analysis = await _llm_analyze(context)
        _write_analysis(result_id, context, analysis, "ready")
        return {
            "id": result_id,
            "symbol": symbol,
            "strategy": strategy,
            "status": "ready",
            "verdict": analysis["verdict"],
        }
    except Exception as exc:
        logger.error("候选股基本面分析失败 id=%s symbol=%s: %s", result_id, symbol, exc)
        _mark_status(result_id, "failed", retry_increment=True)
        return {"id": result_id, "symbol": symbol, "status": "failed", "error": str(exc)}


async def analyze_batch(
    pending: Iterable[tuple[int, str, str]],
    concurrency: int = 5,
) -> list[dict]:
    """批量分析候选股。

    Args:
        pending: (id, symbol, strategy) 列表。
        concurrency: 并发数，默认 5。
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _run(item):
        result_id, symbol, strategy = item
        async with sem:
            return await analyze_one(result_id, symbol, strategy)

    return await asyncio.gather(*[_run(item) for item in pending])
