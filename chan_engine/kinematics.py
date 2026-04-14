from typing import List, Dict
from chan_engine.models import Bi, Segment, Direction

class KinematicsAnalyzer:
    @staticmethod
    def measure_bi_momentum(bi: Bi, macd_data: Dict[str, list], date_to_idx: Dict[str, int]) -> dict:
        """
        测量单一笔内的 MACD 动能。
        返回：
          - area: MACD 柱子面积绝对值之和
          - dif_extreme: 笔内 DIF 的极值（绝对值）
        """
        start_idx = date_to_idx.get(bi.start_fx.date)
        end_idx = date_to_idx.get(bi.end_fx.date)
        
        if start_idx is None or end_idx is None or start_idx >= end_idx:
            return {"area": 0.0, "dif_extreme": 0.0}
            
        hist = macd_data["hist"][start_idx:end_idx+1]
        dif = macd_data["dif"][start_idx:end_idx+1]
        
        if bi.direction == Direction.UP:
            area = sum(max(h, 0) for h in hist)
            dif_extreme = max(dif) if dif else 0.0
        else:
            area = sum(abs(min(h, 0)) for h in hist)
            dif_extreme = abs(min(dif)) if dif else 0.0
            
        return {"area": round(area, 4), "dif_extreme": round(dif_extreme, 4)}

    @staticmethod
    def measure_segment_momentum(segment: Segment, macd_data: Dict[str, list], date_to_idx: Dict[str, int]) -> dict:
        """测量跨越一整个线段的动能"""
        start_idx = date_to_idx.get(segment.start_date)
        end_idx = date_to_idx.get(segment.end_date)
        
        if start_idx is None or end_idx is None or start_idx >= end_idx:
            return {"area": 0.0, "dif_extreme": 0.0}
            
        hist = macd_data["hist"][start_idx:end_idx+1]
        dif = macd_data["dif"][start_idx:end_idx+1]
        
        if segment.direction == Direction.UP:
            area = sum(max(h, 0) for h in hist)
            dif_extreme = max(dif) if dif else 0.0
        else:
            area = sum(abs(min(h, 0)) for h in hist)
            # 向下取最低点再绝对值
            dif_extreme = abs(min(dif)) if dif else 0.0
            
        return {"area": round(area, 4), "dif_extreme": round(dif_extreme, 4)}

    @staticmethod
    def check_divergence(c1_momentum: dict, c2_momentum: dict) -> float:
        """
        量化对比两段同向走势的 MACD 背驰概率。
        返回 0-100 的分数。
        """
        a1 = c1_momentum["area"]
        a2 = c2_momentum["area"]
        d1 = c1_momentum["dif_extreme"]
        d2 = c2_momentum["dif_extreme"]
        
        score = 0.0
        if a1 > 0 and a2 > 0:
            area_ratio = a2 / a1
            if area_ratio < 0.6:
                score += 50
            elif area_ratio < 0.8:
                score += 30
                
        if d1 > 0 and d2 > 0:
            dif_ratio = d2 / d1
            if dif_ratio < 0.7:
                score += 50
            elif dif_ratio < 0.9:
                score += 30
                
        return min(100.0, score)
