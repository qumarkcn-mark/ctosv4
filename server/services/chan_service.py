import asyncio
from typing import Tuple, Optional
from chan_engine.models import KLine, ZhongShu
from chan_engine.parser import ChanParser
from chan_engine.fsm import ChanFSM, ChanState
from server.services.price_service import get_daily_klines, get_minute_klines

async def analyze_stock_chan_state(symbol: str) -> Tuple[ChanState, Optional[ZhongShu]]:
    """
    抓取个股近200日历史 K 线，推演当前的缠论日线走势状态与最后一个中枢。
    这是给后台定时器监控专用的单级别版本。
    """
    raw_data = await get_daily_klines(symbol, count=200)
    if not raw_data:
        return ChanState.UNKNOWN, None

    klines = []
    for item in raw_data:
        klines.append(KLine(
            date=item["date"],
            open=item["open"],
            close=item["close"],
            high=item["high"],
            low=item["low"],
            volume=item["volume"]
        ))
    
    merged = ChanParser.merge_klines(klines)
    fenxings = ChanParser.find_fenxings(merged, validate_bottom=True)
    bis = ChanParser.build_bis(fenxings, merged)
    zhongshus, free_bis = ChanFSM.identify_zhongshu(bis)
    state, latest_zs = ChanFSM.deduce_state(zhongshus, free_bis)
    
    return state, latest_zs

async def _analyze_single_level(symbol: str, level: str) -> dict:
    if level == "day":
        raw_data = await get_daily_klines(symbol, count=250)
    else:
        raw_data = await get_minute_klines(symbol, interval=level, count=320)
        
    if not raw_data:
        return {"level": level, "state": ChanState.UNKNOWN.name, "zd": 0, "zg": 0}
        
    klines = []
    for item in raw_data:
        klines.append(KLine(
            date=item["date"],
            open=item["open"],
            close=item["close"],
            high=item["high"],
            low=item["low"],
            volume=item["volume"]
        ))
        
    merged = ChanParser.merge_klines(klines)
    fenxings = ChanParser.find_fenxings(merged, validate_bottom=True)
    bis = ChanParser.build_bis(fenxings, merged)
    zhongshus, free_bis = ChanFSM.identify_zhongshu(bis)
    state, latest_zs = ChanFSM.deduce_state(zhongshus, free_bis)
    
    return {
        "level": level,
        "state": state.name,
        "zd": latest_zs.ZD if latest_zs else 0,
        "zg": latest_zs.ZG if latest_zs else 0
    }

async def analyze_matrix_state(symbol: str) -> dict:
    """
    双轴跨级别融合计算
    体系 A: 日线 + 30分钟 + 5分钟
    体系 B: 日线 + 60分钟 + 15分钟
    """
    # 一次性并发抓取 5 个不同跨度的数据，极大节省网络时间
    levels = ["day", "m60", "m30", "m15", "m5"]
    tasks = [_analyze_single_level(symbol, lvl) for lvl in levels]
    results = await asyncio.gather(*tasks)
    
    data_map = {r["level"]: r for r in results}
    
    matrix_a = [data_map["day"], data_map["m30"], data_map["m5"]]
    matrix_b = [data_map["day"], data_map["m60"], data_map["m15"]]
    
    return {
        "symbol": symbol,
        "matrix_a": matrix_a,
        "matrix_b": matrix_b
    }
