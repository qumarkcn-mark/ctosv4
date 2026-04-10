from typing import List
from .models import KLine, MergedKLine, FenXing, FenXingType, Bi, Direction, Segment, ZhongShu

class ChanParser:
    """缠论几何解析器"""

    @staticmethod
    def merge_klines(raw_klines: List[KLine]) -> List[MergedKLine]:
        """
        K线包含合并。
        处理规则：
        1. 假设刚开始趋势为向上（或由前两根确立）
        2. 向上包含：取高高中、低高中（高点取高，低点取高）
        3. 向下包含：取高低中、低低中（高点取低，低点取低）
        """
        if not raw_klines:
            return []

        merged = []
        # 初始化第一根K线
        curr = MergedKLine(
            start_date=raw_klines[0].date,
            end_date=raw_klines[0].date,
            high=raw_klines[0].high,
            low=raw_klines[0].low,
            elements=[raw_klines[0]]
        )
        merged.append(curr)

        direction = Direction.UP # 默认初始向上处理包含关系

        for i in range(1, len(raw_klines)):
            k = raw_klines[i]
            prev = merged[-1]

            # 包含关系判定
            is_include = (k.high <= prev.high and k.low >= prev.low) or \
                         (k.high >= prev.high and k.low <= prev.low)

            if is_include:
                # 发生包含
                if direction == Direction.UP:
                    new_high = max(prev.high, k.high)
                    new_low = max(prev.low, k.low)
                else:
                    new_high = min(prev.high, k.high)
                    new_low = min(prev.low, k.low)
                
                # 合并到最新的 K线上
                prev.end_date = k.date
                prev.high = new_high
                prev.low = new_low
                prev.elements.append(k)
            else:
                # 不包含，确定真实的方向
                if k.high > prev.high and k.low > prev.low:
                    direction = Direction.UP
                elif k.high < prev.high and k.low < prev.low:
                    direction = Direction.DOWN
                
                # 创建新的合并K线
                curr = MergedKLine(
                    start_date=k.date,
                    end_date=k.date,
                    high=k.high,
                    low=k.low,
                    elements=[k]
                )
                merged.append(curr)

        return merged

    @staticmethod
    def find_fenxings(merged_klines: List[MergedKLine], validate_bottom: bool = True) -> List[FenXing]:
        """
        识别顶底分型。
        :param validate_bottom: 是否要求底分型"停顿验证"（第3根或后续有有效站稳动作，过滤下跌中继的假分型）
        """
        fenxings = []
        n = len(merged_klines)
        
        for i in range(1, n - 1):
            k1, k2, k3 = merged_klines[i-1], merged_klines[i], merged_klines[i+1]
            
            # 顶分型：中间这根高点最高，低点也最高
            if k2.high > k1.high and k2.high > k3.high and k2.low > k1.low and k2.low > k3.low:
                fx = FenXing(fx_type=FenXingType.TOP, k1=k1, k2=k2, k3=k3)
                fenxings.append(fx)
                
            # 底分型：中间这根高点最低，低点也最低
            elif k2.high < k1.high and k2.high < k3.high and k2.low < k1.low and k2.low < k3.low:
                fx = FenXing(fx_type=FenXingType.BOTTOM, k1=k1, k2=k2, k3=k3)
                
                if validate_bottom:
                    # 【停顿验证 / 有效性验证】
                    # 如果后续走势没有体现出强劲的反转向上，则过滤掉这个底分型（它很大可能是下跌中继）
                    # 验证规则一：后面至少要有1根K线（k4 = merged_klines[i+2]）不跌破底分型的绝对低点 (k2.low)，
                    # 验证规则二：且价格能突破 k3 的高点或 k1 的高点。
                    if i + 2 < n:
                        k4 = merged_klines[i+2]
                        # 假底分型：k4 直接杀破了新低，说明底分型构建失败
                        if k4.low < k2.low:
                            continue
                        # 弱底分型：k4 连 k3 的高点都过不去，上攻动能不足，忽略！
                        if k4.high <= k3.high:
                            continue
                    else:
                        # 对于当下的最新K线，底分型还没来得及走 k4，此时有两种处理：
                        # 1. 严格模式：丢弃，不作为有效底分型计算，直到明天收盘！
                        # 2. 宽松模式（这里暂定）：作为不确定的临界状态保留，方便实盘预警
                        pass

                fenxings.append(fx)
                
        return fenxings

    @staticmethod
    def build_bis(fenxings: List[FenXing], merged_klines: List[MergedKLine]) -> List[Bi]:
        """
        利用分型连成笔。
        核心规则：
        1. 顶底必须交替。
        2. 顶到底、底到顶之间，必须包含至少一根不共享的K线（即满足5根独立K线原则：顶的分型3根 + 中间至少1根 + 底的分型3根 = 最少合并K线跨度不能小于 4，即间隔 > 3）
        3. 如果出现连续两个顶，则取最高的顶。如果出现连续两个底，取最低的底。
        """
        if not fenxings:
            return []

        # 获取合并K线列表的快速索引映射，用于计算K线间隔
        mk_idx_map = {id(mk): idx for idx, mk in enumerate(merged_klines)}

        valid_bis = []
        candidate_fx = fenxings[0]

        for i in range(1, len(fenxings)):
            current_fx = fenxings[i]

            # 同性分型：取极值（顶取高，底取低）
            if current_fx.fx_type == candidate_fx.fx_type:
                if current_fx.fx_type == FenXingType.TOP:
                    if current_fx.high > candidate_fx.high:
                        candidate_fx = current_fx
                else:
                    if current_fx.low < candidate_fx.low:
                        candidate_fx = current_fx
                continue

            # 异性分型：检查是否满足一笔的空间与时间（K线根数）条件
            idx_start = mk_idx_map[id(candidate_fx.k2)]
            idx_end = mk_idx_map[id(current_fx.k2)]

            # 一笔要求顶底之间至少有独立的K线。即 candidate_k2 与 current_k2 索引差至少为 3
            # (例如：0是顶K2，1是顶K3，2是底K1，3是底K2，索引差为 3，刚好凑不够 5 根，中间无独立K线。索引差需 >= 4)
            if (idx_end - idx_start) >= 4:
                direction = Direction.UP if current_fx.fx_type == FenXingType.TOP else Direction.DOWN
                new_bi = Bi(
                    direction=direction,
                    start_fx=candidate_fx,
                    end_fx=current_fx
                )
                # 检查价格逻辑是否合理（向上笔不仅要求顶的分型，还要求实际价格升高）
                if (direction == Direction.UP and current_fx.high > candidate_fx.low) or \
                   (direction == Direction.DOWN and current_fx.low < candidate_fx.high):
                    valid_bis.append(new_bi)
                    candidate_fx = current_fx
            else:
                # 跨度不够，不是合法的一笔。需要判断是抛弃当前的，还是怎样处理？
                # 缠论里通常意味着这不能成笔，当前处于原来的分型延伸中。
                # 这是一个简化的妥协：我们跳过这个不合格的反向分型。
                pass

        return valid_bis
