from enum import Enum
from typing import List, Optional, Tuple
from .models import Bi, ZhongShu, Direction

class ChanState(Enum):
    UNKNOWN = "UNKNOWN"
    IN_CENTER_OSC = "IN_CENTER_OSC"               # 维持中枢震荡
    UPWARD_LEAVING = "UPWARD_LEAVING"             # 向上离开中枢阶段 (某笔的低点已经大于中枢ZG，甚至只是当下一笔直接干出去了)
    DOWNWARD_LEAVING = "DOWNWARD_LEAVING"         # 向下脱离跌破了中枢 (ZD)
    WAITING_FOR_PULLBACK = "WAITING_FOR_PULLBACK" # 向上离开后，正在形成向下的一笔，准备试探支撑
    THIRD_BUY_CONFIRMED = "THIRD_BUY_CONFIRMED"   # 三买确立

class ChanFSM:
    """
    缠论状态推演引擎 (单级别)
    负责从分笔 (Bi) 数据中寻找中枢 (ZhongShu)，并判定出当前的价格处于什么阶段。
    """
    
    @staticmethod
    def identify_zhongshu(bis: List[Bi]) -> Tuple[List[ZhongShu], List[Bi]]:
        """
        寻找图谱上的历史中枢。
        简化版核心逻辑：连续寻找3根有严格重叠的笔定义为中枢的核 (Kernel)。
        遇到脱离重叠区的笔，则认为中枢被破坏，开始寻找下一个中枢。
        
        返回: 
        1. 历史所有的中枢列表
        2. 最后一个中枢建立之后，剩下的(游离在外)的未成枢笔
        """
        zhongshus = []
        i = 0
        n = len(bis)
        
        last_zs_end_idx = -1
        
        while i <= n - 3:
            b1, b2, b3 = bis[i], bis[i+1], bis[i+2]
            
            # 判断前三笔是否有完全重合的交集 (也就是寻找中枢的 [ZD, ZG])
            # 重合交集要求: 三笔里的最大低点(ZD) < 三笔里的最小高点(ZG)
            zd_candidate = max(b1.low, b2.low, b3.low)
            zg_candidate = min(b1.high, b2.high, b3.high)
            
            if zd_candidate < zg_candidate:
                # 形成了中枢基础组件！
                # 收录构成中枢的前三笔
                current_zs_bis = [b1, b2, b3]
                
                # 开始判断延伸：后续的笔是否一直在 [ZD, ZG] 中枢震荡？
                j = i + 3
                while j < n:
                    b_next = bis[j]
                    # 规则：如果接下来的这笔，高点甚至都碰不到 ZD，或者低点竟然高于 ZG，
                    # 那么这笔已经明确脱离了当前中枢的震荡范围 (即破坏了本级别中枢延伸)
                    if b_next.low > zg_candidate or b_next.high < zd_candidate:
                        break # 中枢由于单边脱离而结束
                    else:
                        current_zs_bis.append(b_next)
                        j += 1
                
                new_zs = ZhongShu(bis=current_zs_bis)
                zhongshus.append(new_zs)
                
                # 下一次寻找从此中枢脱离后的一笔开始
                i = j
                last_zs_end_idx = j - 1
            else:
                # 这三笔没有交集（处于强烈单边趋势中），往后挪一步继续找
                i += 1
                
        # 截取最后脱离枢纽的“自由笔”序列
        if last_zs_end_idx == -1:
             free_bis = bis # 从头到尾没形成任何中枢（极少见的超级单边或数据太少）
        else:
             free_bis = bis[last_zs_end_idx + 1:]
             
        return zhongshus, free_bis

    @staticmethod
    def deduce_state(zhongshus: List[ZhongShu], free_bis: List[Bi]) -> Tuple[ChanState, Optional[ZhongShu]]:
        """
        根据历史最后一个已知中枢，以及脱离该中枢后的笔状态，
        推演出当下的绝对状态！（猎杀三买）。
        """
        if not zhongshus:
            return ChanState.UNKNOWN, None
            
        latest_zs = zhongshus[-1]
        zg = latest_zs.ZG
        
        if not free_bis:
            # 没有自由笔，代表行情目前的最后一笔，依然紧紧贴在中枢内部震荡
            return ChanState.IN_CENTER_OSC, latest_zs
            
        # 根据最后离开的一段笔序列开始分析
        # 重点：我们只关注"向上脱离"寻找三买的场景。
        
        # 首个离开中枢的笔，是否已经高于 ZG （离开段）
        b_leave = free_bis[0]
        
        if b_leave.low > zg:
            # 这个脱离笔明确已经踩在中枢头上飞了
            if len(free_bis) == 1:
                if b_leave.direction == Direction.UP:
                    return ChanState.UPWARD_LEAVING, latest_zs
                else: 
                     # 本身方向向下却高于ZG？只出现在极其异常或跨度极小的缝隙中，统归为等待回调
                    return ChanState.WAITING_FOR_PULLBACK, latest_zs
                    
            elif len(free_bis) == 2:
                 # 有两笔：离开一笔（向上），加上新的一笔（必定向下回调）
                 b_pullback = free_bis[1]
                 return ChanState.WAITING_FOR_PULLBACK, latest_zs
                 
            elif len(free_bis) == 3:
                 # 有三笔：离开一笔(上)，回调一笔(下)，反转一笔(上)！
                 # 这正是三买判断最核心的回头看！
                 b_leave = free_bis[0]
                 b_pullback = free_bis[1]
                 b_turn = free_bis[2]
                 
                 # 三买定律：向下回调的一笔其最低点，死活不跌破 ZG，并且随后构成了向上的一笔
                 if b_pullback.direction == Direction.DOWN and b_pullback.low > zg:
                      return ChanState.THIRD_BUY_CONFIRMED, latest_zs
                 else:
                      # 如果跌破了ZG，中枢级别扩张或者重新陷入震荡，那就不符合严苛的日线三买了
                      return ChanState.IN_CENTER_OSC, latest_zs
            else:
                 # 脱离单边走势已经走成线段了...此时如果一直不破ZG，已经是漫天天际的上涨了
                 return ChanState.UNKNOWN, latest_zs
                 
        elif b_leave.high < latest_zs.ZD:
            # 向下脱离跌破了中枢 (ZD)
            if len(free_bis) == 1:
                return ChanState.DOWNWARD_LEAVING, latest_zs
            else:
                # 哪怕有反弹笔，只要最高点依然不碰 ZD，这就是三卖确认
                # 但由于我们目前系统主要抓"第三类买点(多头)"，对空头形态统一定义为向下脱离期
                return ChanState.DOWNWARD_LEAVING, latest_zs
        else:
            # 脱离的这几笔依然和 ZG/ZD 有纠缠，处于中心震荡衍生阶段
            return ChanState.IN_CENTER_OSC, latest_zs
