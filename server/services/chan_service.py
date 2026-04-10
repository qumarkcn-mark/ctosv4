from typing import Tuple, Optional
from chan_engine.models import KLine, ZhongShu
from chan_engine.parser import ChanParser
from chan_engine.fsm import ChanFSM, ChanState
from server.services.price_service import get_daily_klines

async def analyze_stock_chan_state(symbol: str) -> Tuple[ChanState, Optional[ZhongShu]]:
    """
    抓取个股近200日历史 K 线，推演当前的缠论日线走势状态与最后一个中枢。
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
