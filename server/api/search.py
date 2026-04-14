"""CT-OS V4.0 — 股票搜索 API

支持代码/名称模糊搜索。数据源：腾讯搜索接口 (smartbox)。
"""

import logging
import httpx
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

# 腾讯智能搜索接口
_SEARCH_URL = "https://smartbox.gtimg.cn/s3/?v=2&t=all&c=1&q={query}"


@router.get("/search")
async def search_stocks(q: str = Query(..., min_length=1, description="搜索关键词")):
    """
    搜索股票，支持代码/名称/拼音。
    返回 [{symbol, name, market}, ...]
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_SEARCH_URL.format(query=q))
            resp.raise_for_status()
            text = resp.text
            # 腾讯返回用 unicode 转义编码中文，需要解码
            try:
                text = text.encode('utf-8').decode('unicode_escape')
            except Exception:
                pass

        # 腾讯返回格式: v_hint="sh~600519~贵州茅台~gzmt~GP-A"
        # 多条结果用 ^ 分隔。字段: 市场~代码~名称~拼音~类型
        results = []
        if 'v_hint="' in text:
            data_str = text.split('v_hint="')[1].rstrip('";\n')
            items = data_str.split("^")
            for item in items:
                if not item.strip():
                    continue
                parts = item.split("~")
                if len(parts) < 5:
                    continue
                market = parts[0]       # "sh" / "sz" / "hk" / "us"
                code = parts[1]         # "600519"
                name = parts[2]         # "贵州茅台"
                stock_type = parts[4]   # "GP-A" / "GP" / "ZS" / "JJ" / "QZ"

                # 只保留 A股(sh/sz) 和 港股(hk) 的股票和指数
                if market not in ("sh", "sz", "hk"):
                    continue
                if not stock_type.startswith(("GP", "ZS")):
                    continue

                market_label = {"sh": "沪", "sz": "深", "hk": "港"}[market]
                results.append({
                    "symbol": f"{market}{code}",
                    "name": name,
                    "market": market_label,
                    "type": "GP" if stock_type.startswith("GP") else "ZS",
                })

        return {"results": results[:20]}

    except Exception as e:
        logger.warning("搜索失败: %s", e)
        return {"results": [], "error": str(e)}
