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

        # 缠论标准：由前两根K线确立初始方向
        direction = Direction.UP if (
            raw_klines[1].high > raw_klines[0].high and
            raw_klines[1].low > raw_klines[0].low
        ) else Direction.DOWN

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
    def find_fenxings(merged_klines: List[MergedKLine], validate_bottom: bool = False) -> List[FenXing]:
        """
        识别顶底分型。
        :param validate_bottom: 是否要求底分型"停顿验证"（默认关闭，减少误过滤）
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
                    if i + 2 < n:
                        k4 = merged_klines[i+2]
                        if k4.low < k2.low:
                            continue
                        if k4.high <= k3.high:
                            continue

                fenxings.append(fx)
                
        return fenxings

    @staticmethod
    def build_bis(fenxings: List[FenXing], merged_klines: List[MergedKLine]) -> List[Bi]:
        """
        利用分型连成笔。
        核心规则：
        1. 顶底必须交替。
        2. 顶到底、底到顶之间，必须包含至少一根不共享的K线（合并K线索引差 >= 4）
        3. 如果出现连续两个顶，则取最高的顶。如果出现连续两个底，取最低的底。
        4. 笔必须首尾相连：bi[n].end_fx == bi[n+1].start_fx
        """
        if not fenxings:
            return []

        # 获取合并K线列表的快速索引映射
        mk_idx_map = {id(mk): idx for idx, mk in enumerate(merged_klines)}

        valid_bis = []
        candidate_fx = fenxings[0]

        for i in range(1, len(fenxings)):
            current_fx = fenxings[i]

            # 同性分型：取极值（顶取高，底取低），即笔的延伸
            if current_fx.fx_type == candidate_fx.fx_type:
                updated = False
                if current_fx.fx_type == FenXingType.TOP:
                    if current_fx.high > candidate_fx.high:
                        candidate_fx = current_fx
                        updated = True
                else:
                    if current_fx.low < candidate_fx.low:
                        candidate_fx = current_fx
                        updated = True
                        
                # 如果当前分型更新了极值，并且之前的最新一笔恰好也是以该类型分型结束，则直接延伸那一笔！
                if updated and len(valid_bis) > 0 and valid_bis[-1].end_fx.fx_type == candidate_fx.fx_type:
                    valid_bis[-1].end_fx = candidate_fx
                continue

            # 异性分型：检查是否满足一笔的时间条件
            idx_start = mk_idx_map[id(candidate_fx.k2)]
            idx_end = mk_idx_map[id(current_fx.k2)]

            if (idx_end - idx_start) < 4:
                # 跨度不够，跳过这个不合格的反向分型
                continue

            # P1-FIX: 价格合理性检查（标准缠论定义）
            direction = Direction.UP if current_fx.fx_type == FenXingType.TOP else Direction.DOWN
            price_ok = False
            if direction == Direction.UP:
                # 向上笔：顶分型高点 > 底分型高点
                price_ok = current_fx.high > candidate_fx.high
            else:
                # 向下笔：底分型低点 < 顶分型低点
                price_ok = current_fx.low < candidate_fx.low

            if price_ok:
                new_bi = Bi(
                    direction=direction,
                    start_fx=candidate_fx,
                    end_fx=current_fx
                )
                valid_bis.append(new_bi)
                # 关键：下一笔的起点 = 当前笔的终点，确保首尾相连
                candidate_fx = current_fx

        return valid_bis

    # ═══════════════════════════════════════════════════════════════
    #  线段算法 — 标准特征序列分型法（缠论62-67课）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def build_segments(bis: List[Bi]) -> List[Segment]:
        """
        标准特征序列算法构建线段。
        
        核心步骤：
        1. 确定线段方向（由第一笔决定）
        2. 提取反向笔作为特征序列（向上线段取向下笔）
        3. 对特征序列做包含处理（同K线包含规则）
        4. 在标准特征序列中找分型 → 确认线段终点
        5. 分两类情况处理（无缺口直接确认 / 有缺口需二次确认）
        """
        if len(bis) < 3:
            return []

        segments = []
        seg_start_idx = 0
        current_dir = bis[0].direction

        while seg_start_idx < len(bis) - 2:
            result = ChanParser._find_segment_end(bis, seg_start_idx, current_dir)
            
            if result is None:
                break

            end_idx = result
            seg_bis = bis[seg_start_idx : end_idx + 1]
            segments.append(Segment(direction=current_dir, bis=seg_bis))

            seg_start_idx = end_idx
            current_dir = Direction.DOWN if current_dir == Direction.UP else Direction.UP

        # 处理最后未封闭的线段（至少3笔）
        remaining = bis[seg_start_idx:]
        if len(remaining) >= 3:
            segments.append(Segment(direction=current_dir, bis=remaining))

        return segments

    @staticmethod
    def _find_segment_end(bis: List[Bi], start_idx: int, seg_dir: Direction):
        """
        从 start_idx 开始寻找线段结束位置。
        
        返回结束笔的索引，找不到返回 None。
        """
        feat_dir = Direction.DOWN if seg_dir == Direction.UP else Direction.UP
        
        # 提取特征序列元素
        feat_elements = []
        for i in range(start_idx, len(bis)):
            if bis[i].direction == feat_dir:
                feat_elements.append({
                    'high': bis[i].high,
                    'low': bis[i].low,
                    'bi_idx': i,
                })
        
        if len(feat_elements) < 3:
            return None

        # 对特征序列做包含处理
        std_feat = ChanParser._merge_feature_elements(feat_elements, seg_dir)
        
        if len(std_feat) < 3:
            return None

        # 在标准特征序列中寻找分型
        for i in range(1, len(std_feat) - 1):
            f1, f2, f3 = std_feat[i-1], std_feat[i], std_feat[i+1]
            
            is_fx = False
            if seg_dir == Direction.UP:
                # 向上线段 → 特征序列（向下笔）中找顶分型
                is_fx = (f2['high'] > f1['high'] and f2['high'] > f3['high'] and
                         f2['low'] > f1['low'] and f2['low'] > f3['low'])
            else:
                # 向下线段 → 特征序列（向上笔）中找底分型
                is_fx = (f2['low'] < f1['low'] and f2['low'] < f3['low'] and
                         f2['high'] < f1['high'] and f2['high'] < f3['high'])

            if is_fx:
                has_gap = ChanParser._check_feature_gap(f1, f2, seg_dir)
                
                if not has_gap:
                    # 第一类（无缺口）：线段结束在顶分型前的那笔（f1对应的笔）
                    f1_bi_idx = f1['bi_idx']
                    end_bi_idx = f1_bi_idx - 1 if f1_bi_idx > start_idx else f1_bi_idx
                    if end_bi_idx - start_idx >= 2:
                        return end_bi_idx
                else:
                    # 第二类（有缺口）：需二次确认
                    # 向上线段有缺口后，需找到比第一笔(f1)更低的向下笔才确认结束
                    # 向下线段有缺口后，需找到比第一笔(f1)更高的向上笔才确认结束
                    feat_bi_idx = f2['bi_idx']
                    for j in range(feat_bi_idx + 1, len(bis)):
                        if seg_dir == Direction.UP:
                            if bis[j].direction == Direction.DOWN and bis[j].low < f1['low']:
                                end_bi_idx = feat_bi_idx - 1 if feat_bi_idx > start_idx else feat_bi_idx
                                if end_bi_idx - start_idx >= 2:
                                    return end_bi_idx
                                break
                        else:
                            if bis[j].direction == Direction.UP and bis[j].high > f1['high']:
                                end_bi_idx = feat_bi_idx - 1 if feat_bi_idx > start_idx else feat_bi_idx
                                if end_bi_idx - start_idx >= 2:
                                    return end_bi_idx
                                break
        
        return None

    @staticmethod
    def _merge_feature_elements(elements: list, seg_dir: Direction) -> list:
        """
        对特征序列做包含处理。
        向上线段的特征序列（向下笔）用向下包含处理（取低值）。
        向下线段的特征序列（向上笔）用向上包含处理（取高值）。
        """
        if not elements:
            return []
        
        merged = [elements[0].copy()]
        
        for i in range(1, len(elements)):
            e = elements[i]
            prev = merged[-1]
            
            is_include = ((e['high'] <= prev['high'] and e['low'] >= prev['low']) or
                          (e['high'] >= prev['high'] and e['low'] <= prev['low']))
            
            if is_include:
                if seg_dir == Direction.UP:
                    # 向上线段 → 特征序列向下包含
                    prev['high'] = min(prev['high'], e['high'])
                    prev['low'] = min(prev['low'], e['low'])
                else:
                    # 向下线段 → 特征序列向上包含
                    prev['high'] = max(prev['high'], e['high'])
                    prev['low'] = max(prev['low'], e['low'])
                prev['bi_idx'] = e['bi_idx']
            else:
                merged.append(e.copy())
        
        return merged

    @staticmethod
    def _check_feature_gap(f1: dict, f2: dict, seg_dir: Direction) -> bool:
        """
        检查特征序列中 f1 和 f2 之间是否存在缺口。
        """
        if seg_dir == Direction.UP:
            return f2['high'] < f1['low']
        else:
            return f2['low'] > f1['high']
