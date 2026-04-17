from enum import Enum
from typing import List, Optional, Tuple
from .models import Bi, ZhongShu, Direction

class ChanState(Enum):
    UNKNOWN = "UNKNOWN"
    IN_CENTER_OSC = "IN_CENTER_OSC"               # 维持中枢震荡
    UPWARD_LEAVING = "UPWARD_LEAVING"             # 向上离开中枢阶段
    DOWNWARD_LEAVING = "DOWNWARD_LEAVING"         # 向下脱离跌破了中枢(ZD)
    WAITING_FOR_PULLBACK = "WAITING_FOR_PULLBACK" # 向上离开后，等待回踩结果
    THIRD_BUY_CONFIRMED = "THIRD_BUY_CONFIRMED"   # 三买确立
    THIRD_SELL_CONFIRMED = "THIRD_SELL_CONFIRMED" # 三卖确立（向下离开后反弹不过ZD）
    MAJOR_WAVE_UP = "MAJOR_WAVE_UP"               # 主升浪延续（free_bis>3 持续向上）
    MAJOR_WAVE_DOWN = "MAJOR_WAVE_DOWN"           # 主跌浪延续（free_bis>3 持续向下）

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
                 b_leave  = free_bis[0]
                 b_pullback = free_bis[1]
                 b_turn   = free_bis[2]
                 
                 # 三买定律：向下回调的一笔其最低点，死活不跌破 ZG，并且随后构成了向上的一笔
                 if b_pullback.direction == Direction.DOWN and b_pullback.low > zg:
                      return ChanState.THIRD_BUY_CONFIRMED, latest_zs
                 else:
                      # 如果跌破了ZG，中枢级别扩张或者重新陷入震荡
                      return ChanState.IN_CENTER_OSC, latest_zs
            else:
                 # free_bis > 3：主升浪延续阶段
                 # 规则：看最后一笔的方向决定当前状态
                 last_free = free_bis[-1]
                 prev_free = free_bis[-2]
                 if last_free.direction == Direction.UP:
                     # 最后一笔向上：持续拉升中（主升浪）
                     return ChanState.MAJOR_WAVE_UP, latest_zs
                 else:
                     # 最后一笔向下：回调中
                     # 判断回调低点是否在 ZG 上方（健康的主升浪回调）
                     if last_free.low > zg:
                         return ChanState.WAITING_FOR_PULLBACK, latest_zs
                     else:
                         # 回调跌回中枢，主升浪结构破坏
                         return ChanState.IN_CENTER_OSC, latest_zs
                 
        elif b_leave.high < latest_zs.ZD:
            # 向下脱离跌破了中枢 (ZD)
            zd = latest_zs.ZD
            if len(free_bis) == 1:
                return ChanState.DOWNWARD_LEAVING, latest_zs
            elif len(free_bis) == 2:
                # 有两笔（向下离开 + 向上反弹），等待反弹结果
                return ChanState.DOWNWARD_LEAVING, latest_zs
            elif len(free_bis) == 3:
                # 三卖判断：向上反弹的高点不突破 ZD，形成三卖点
                b_pullback = free_bis[1]  # 反弹笔（向上）
                b_cont     = free_bis[2]  # 继续下行笔（向下）
                if b_pullback.direction == Direction.UP and b_pullback.high < zd:
                    # 反弹高点未过ZD → 三卖确认
                    return ChanState.THIRD_SELL_CONFIRMED, latest_zs
                else:
                    # 反弹过了ZD → 中枢延伸或结构改变
                    return ChanState.IN_CENTER_OSC, latest_zs
            else:
                # free_bis > 3：主跌浪持续延续
                last_free = free_bis[-1]
                if last_free.direction == Direction.DOWN:
                    return ChanState.MAJOR_WAVE_DOWN, latest_zs
                else:
                    # 空头回调中
                    return ChanState.DOWNWARD_LEAVING, latest_zs

        else:
            # 脱离的这几笔依然和 ZG/ZD 有纠缠，处于中心震荡衍生阶段
            return ChanState.IN_CENTER_OSC, latest_zs
