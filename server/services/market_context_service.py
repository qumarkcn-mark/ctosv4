"""
市场语境服务（Market Context Service）
为 AI 深度看盘提供外部因素数据：
  1. 个股主力资金流向（近3日 + 今日）
  2. 所属行业板块的资金排名
  3. 大盘指数背景（从本地baostock数据）
"""

import logging
import asyncio
from functools import lru_cache
from typing import Optional
import datetime

logger = logging.getLogger("MarketContextService")


def _parse_symbol(symbol: str) -> tuple[str, str]:
    """解析 'sh.603986' → ('603986', 'sh')"""
    parts = symbol.split(".")
    if len(parts) == 2:
        market = parts[0].lower()
        code = parts[1]
    else:
        code = symbol
        market = "sh" if symbol.startswith("6") else "sz"
    return code, market


def _fmt_amount(val) -> str:
    """将原始元数字格式化为「X.XX亿」或「X.XX万」"""
    try:
        v = float(val)
        if abs(v) >= 1e8:
            return f"{v/1e8:+.2f}亿"
        elif abs(v) >= 1e4:
            return f"{v/1e4:+.2f}万"
        else:
            return f"{v:+.0f}元"
    except Exception:
        return str(val)


async def get_fund_flow(code: str, market: str) -> dict:
    """获取个股近3日主力资金流向"""
    import akshare as ak

    try:
        df = await asyncio.to_thread(
            ak.stock_individual_fund_flow,
            stock=code,
            market=market
        )
        if df is None or df.empty:
            return {}

        # 取最近3日
        recent = df.tail(3).copy()
        days = []
        for _, row in recent.iterrows():
            days.append({
                "date": str(row.get("日期", "")),
                "close": float(row.get("收盘价", 0)),
                "pct": float(row.get("涨跌幅", 0)),
                "main_net": float(row.get("主力净流入-净额", 0)),
                "main_net_pct": float(row.get("主力净流入-净占比", 0)),
                "super_net": float(row.get("超大单净流入-净额", 0)),
                "small_net": float(row.get("小单净流入-净额", 0)),
            })

        latest = days[-1] if days else {}
        # 近3日累计
        total_main = sum(d["main_net"] for d in days)
        consecutive_in = all(d["main_net"] > 0 for d in days)
        consecutive_out = all(d["main_net"] < 0 for d in days)

        return {
            "today": {
                "main_net_fmt": _fmt_amount(latest.get("main_net", 0)),
                "main_net_pct": f"{latest.get('main_net_pct', 0):+.1f}%",
                "super_net_fmt": _fmt_amount(latest.get("super_net", 0)),
                "small_net_fmt": _fmt_amount(latest.get("small_net", 0)),
                "direction": "主力净流入" if latest.get("main_net", 0) > 0 else "主力净流出",
            },
            "3day": {
                "total_main_fmt": _fmt_amount(total_main),
                "trend": "连续3日流入" if consecutive_in else ("连续3日流出" if consecutive_out else "混合"),
            },
            "raw_days": days,
        }

    except Exception as e:
        logger.warning(f"获取资金流向失败 {code}: {e}")
        return {}


async def get_sector_context(symbol_code: str) -> dict:
    """获取行业板块资金排名，尝试找到个股所属行业"""
    import akshare as ak

    try:
        df = await asyncio.to_thread(ak.stock_fund_flow_industry)
        if df is None or df.empty:
            return {}

        # 取今日前5名行业
        top5 = []
        for _, row in df.head(5).iterrows():
            top5.append({
                "name": str(row.get("行业", "")),
                "pct": f"{float(row.get('行业-涨跌幅', 0)):+.2f}%",
                "net_fmt": _fmt_amount(float(row.get("净额", 0)) * 1e8 if row.get("净额") else 0),
                "leader": str(row.get("领涨股", "")),
                "leader_pct": f"{float(row.get('领涨股-涨跌幅', 0)):+.2f}%",
            })

        return {
            "top5_sectors": top5,
            "note": "行业资金流入排名（今日前5）",
        }

    except Exception as e:
        logger.warning(f"获取行业板块数据失败: {e}")
        return {}


async def get_index_background() -> dict:
    """从本地kline_lake.db获取大盘近期背景（沪深两市）"""
    try:
        import sqlite3
        import os
        db_path = os.path.join(os.path.dirname(__file__), "../../data/kline_lake.db")
        db_path = os.path.abspath(db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        def _fetch(symbol):
            rows = conn.execute(
                """SELECT date, close FROM klines
                   WHERE symbol=? AND freq='day'
                   ORDER BY date DESC LIMIT 5""",
                (symbol,)
            ).fetchall()
            return [r["close"] for r in rows]

        sh_closes = _fetch("sh.000001")
        sz_closes = _fetch("sz.399001")
        conn.close()

        result = {}
        for name, closes, key in [("沪指", sh_closes, "sh"), ("深指", sz_closes, "sz")]:
            if not closes:
                continue
            latest = closes[0]
            prev = closes[1] if len(closes) > 1 else latest
            pct5 = (latest - closes[-1]) / closes[-1] * 100 if len(closes) >= 5 else 0
            direction = "收涨" if latest > prev else ("收跌" if latest < prev else "平")
            result[key] = {
                "name": name,
                "close": round(latest, 2),
                "pct_1d": f"{(latest - prev)/prev*100:+.2f}%",
                "pct_5d": f"{pct5:+.2f}%",
                "direction": direction,
            }

        return result

    except Exception as e:
        logger.warning(f"获取大盘背景失败: {e}")
        return {}


async def get_market_context(symbol: str) -> dict:
    """
    主入口：并行获取所有外部市场数据，返回结构化语境字典。
    即使部分接口失败，也返回已获取的部分数据。
    """
    code, market = _parse_symbol(symbol)

    # 并行请求，超时8秒
    try:
        fund_flow_task = get_fund_flow(code, market)
        sector_task = get_sector_context(code)
        index_task = get_index_background()

        results = await asyncio.wait_for(
            asyncio.gather(fund_flow_task, sector_task, index_task, return_exceptions=True),
            timeout=10.0
        )

        fund_flow, sector, index_bg = results
        if isinstance(fund_flow, Exception):
            fund_flow = {}
        if isinstance(sector, Exception):
            sector = {}
        if isinstance(index_bg, Exception):
            index_bg = {}

    except asyncio.TimeoutError:
        logger.warning(f"市场语境获取超时 {symbol}")
        fund_flow, sector, index_bg = {}, {}, {}

    context = {
        "symbol": symbol,
        "fund_flow": fund_flow,
        "sector": sector,
        "index": index_bg,
    }

    logger.info(f"市场语境获取完成 {symbol}: fund_flow={bool(fund_flow)}, sector={bool(sector)}, index={bool(index_bg)}")
    return context


def format_context_for_prompt(ctx: dict) -> str:
    """
    把市场语境字典格式化为简洁的文字，注入到AI Prompt里。
    保持简短，避免占用过多token。
    """
    lines = []

    # 大盘背景
    idx = ctx.get("index", {})
    if idx:
        parts = []
        for key in ["sh", "sz"]:
            d = idx.get(key)
            if d:
                parts.append(f"{d['name']} {d['close']} {d['pct_1d']}（今日）/ {d['pct_5d']}（近5日）")
        if parts:
            lines.append("【大盘】" + " | ".join(parts))

    # 个股资金
    ff = ctx.get("fund_flow", {})
    if ff:
        today = ff.get("today", {})
        d3 = ff.get("3day", {})
        lines.append(
            f"【资金】今日主力 {today.get('main_net_fmt', 'N/A')}（{today.get('main_net_pct', '')}）/ "
            f"超大单 {today.get('super_net_fmt', 'N/A')} / 散户 {today.get('small_net_fmt', 'N/A')}"
        )
        lines.append(
            f"【资金趋势】近3日{d3.get('trend', '')}，合计 {d3.get('total_main_fmt', 'N/A')}"
        )

    # 板块前三
    sec = ctx.get("sector", {})
    if sec and sec.get("top5_sectors"):
        top3 = sec["top5_sectors"][:3]
        sector_str = " | ".join([f"{s['name']}{s['pct']}" for s in top3])
        lines.append(f"【板块】今日资金流入前3: {sector_str}")

    if not lines:
        lines.append("【外部数据】暂无（接口超时或未获取）")

    return "\n".join(lines)
