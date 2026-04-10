import json
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chan_engine.models import KLine, MergedKLine, FenXingType, Direction
from chan_engine.parser import ChanParser
from chan_engine.fsm import ChanFSM, ChanState

def load_fixture(filename: str) -> list[dict]:
    filepath = os.path.join(os.path.dirname(__file__), "fixtures", filename)
    with open(filepath, "r") as f:
        return json.load(f)

def test_chan_fsm_real_data():
    """使用真实的 sz000001 K线快照，连贯测试 中枢构建 与 FSM 状态推演"""
    raw_data = load_fixture("sz000001_daily.json")
    
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
    
    # 核心几何解析
    merged_klines = ChanParser.merge_klines(klines)
    fenxings = ChanParser.find_fenxings(merged_klines, validate_bottom=True)
    bis = ChanParser.build_bis(fenxings, merged_klines)
    
    # FSM 引擎切入
    # 1. 寻找中枢
    zhongshus, free_bis = ChanFSM.identify_zhongshu(bis)
    
    print(f"\n[FSM 分析报告] 股票: 平安银行 (sz000001)")
    print(f"共生成 {len(bis)} 笔。提取出 {len(zhongshus)} 个完整中枢。游离笔数量: {len(free_bis)}")
    
    for idx, zs in enumerate(zhongshus):
        start_date = zs.bis[0].start_fx.date
        end_date = zs.bis[-1].end_fx.date
        print(f"中枢 #{idx+1}: [{start_date} -> {end_date}] | ZD: {zs.ZD} | ZG: {zs.ZG}")
        
    for idx, fb in enumerate(free_bis):
        dir_str = "↑" if fb.direction == Direction.UP else "↓"
        print(f"走势尾部的游离笔 #{idx+1}: {dir_str} [{fb.start_fx.date} -> {fb.end_fx.date}], High: {fb.high}, Low: {fb.low}")

    # 2. 状态推演
    state, latest_zs = ChanFSM.deduce_state(zhongshus, free_bis)
    
    print(f"\n>>>> 当下最终走势评级: {state.name}")
    assert state != ChanState.UNKNOWN, "状态引擎解析失败，落入 UNKNOWN"

    # 断言基本数据完整性
    if zhongshus:
        assert zhongshus[0].ZD < zhongshus[0].ZG
    
if __name__ == "__main__":
    test_chan_fsm_real_data()
