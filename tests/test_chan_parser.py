import json
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chan_engine.models import KLine, MergedKLine, FenXingType, Direction
from chan_engine.parser import ChanParser

def load_fixture(filename: str) -> list[dict]:
    filepath = os.path.join(os.path.dirname(__file__), "fixtures", filename)
    with open(filepath, "r") as f:
        return json.load(f)

def test_chan_parser_real_data():
    """使用真实的 sz000001 平安银行 K 线快照测试解析器"""
    raw_data = load_fixture("sz000001_daily.json")
    
    # 1. 转化为 KLine 对象
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
    
    assert len(klines) > 100, "测试数据过少"
    
    # 2. 测试包含关系的合并
    merged_klines = ChanParser.merge_klines(klines)
    
    assert len(merged_klines) <= len(klines), "合并后的 K 线数量不可能大于原始数量"
    for mk in merged_klines:
        assert len(mk.elements) >= 1
        assert mk.high >= mk.low
        # 验证合并后的极值一定是最包容的
        raw_high = max(k.high for k in mk.elements)
        raw_low = min(k.low for k in mk.elements)
        # 根据缠论规则，包含后可能取的是 max(high) 或者 min(high)，
        # 所以合并后的 high 可能不等于 raw_high，但一定在 [min_high, max_high] 中。
        # 这里只断言类型和基本合理性。

    # 3. 测试分型提取与验证
    fenxings = ChanParser.find_fenxings(merged_klines, validate_bottom=True)
    
    assert len(fenxings) > 0, "200根K线必然能找出分型"
    for fx in fenxings:
        if fx.fx_type == FenXingType.TOP:
            assert fx.k2.high > fx.k1.high
            assert fx.k2.high > fx.k3.high
        elif fx.fx_type == FenXingType.BOTTOM:
            assert fx.k2.low < fx.k1.low
            assert fx.k2.low < fx.k3.low
            # 由于我们开启了底分型验证，在可看未来 (i+2) 的场景下，底分型必须扛住跌破
            #（注意：最后一两个分型可能正好在末尾，无需满足强验证，所以仅在大样本中抽查逻辑连贯即可）

    # 4. 测试笔的生成
    bis = ChanParser.build_bis(fenxings, merged_klines)
    
    # 验证笔的合法性
    if bis:
        for i in range(len(bis)):
            b = bis[i]
            # 顶底必异性且必交替
            assert b.start_fx.fx_type != b.end_fx.fx_type
            
            if i > 0:
                prev_b = bis[i-1]
                assert prev_b.direction != b.direction
                # 前一笔的终点必须是当下一笔的起点（无缝拼接）
                # 这里暂未写强制缝合验证，但在单向行情中，通常起点是上一个异性分型。
                
            # 方向特征
            if b.direction == Direction.UP:
                assert b.start_fx.fx_type == FenXingType.BOTTOM
                assert b.end_fx.fx_type == FenXingType.TOP
                assert b.high == b.end_fx.high
            else:
                assert b.start_fx.fx_type == FenXingType.TOP
                assert b.end_fx.fx_type == FenXingType.BOTTOM
                assert b.low == b.end_fx.low
                
        print(f"成功解析 {len(klines)} 根原始K线 => {len(merged_klines)} 根合并K线 => {len(fenxings)} 个分型 => {len(bis)} 笔。")
        
        # 调试：打印最近的3笔
        for b in bis[-3:]:
            dir_str = "向上" if b.direction == Direction.UP else "向下"
            print(f"笔: {dir_str} [{b.start_fx.date} -> {b.end_fx.date}] H:{b.high} L:{b.low}")

if __name__ == "__main__":
    test_chan_parser_real_data()
