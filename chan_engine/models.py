from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

class Direction(Enum):
    UP = 1
    DOWN = -1
    UNKNOWN = 0

class FenXingType(Enum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"

@dataclass
class KLine:
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float

@dataclass
class MergedKLine:
    """包含了合并过特征的 K 线"""
    start_date: str
    end_date: str
    high: float
    low: float
    elements: List[KLine] = field(default_factory=list) # 组成这根合并K线的原始K线

@dataclass
class FenXing:
    """分型 (顶分型 / 底分型)"""
    fx_type: FenXingType
    k1: MergedKLine
    k2: MergedKLine
    k3: MergedKLine
    
    @property
    def high(self) -> float:
        return self.k2.high
        
    @property
    def low(self) -> float:
        return self.k2.low

    @property
    def date(self) -> str:
        return self.k2.end_date

@dataclass
class Bi:
    """笔"""
    direction: Direction
    start_fx: FenXing
    end_fx: FenXing
    
    @property
    def high(self) -> float:
        return max(self.start_fx.high, self.end_fx.high)
        
    @property
    def low(self) -> float:
        return min(self.start_fx.low, self.end_fx.low)

@dataclass
class Segment:
    """线段 (由多笔构成)"""
    direction: Direction
    bis: List[Bi]
    
    @property
    def high(self) -> float:
        return max(b.high for b in self.bis) if self.bis else 0
        
    @property
    def low(self) -> float:
        return min(b.low for b in self.bis) if self.bis else 0
        
    @property
    def start_date(self) -> str:
        return self.bis[0].start_fx.date if self.bis else ""

    @property
    def end_date(self) -> str:
        return self.bis[-1].end_fx.date if self.bis else ""

@dataclass
class ZhongShu:
    """中枢 (在我们的单级别体系中，由连续三笔重叠构成)"""
    bis: List[Bi]
    
    @property
    def ZG(self) -> float:
        """中枢高点 (ZhongShu Gao): 构成中枢的前三笔中，向下的高点 / 向上的低点... 
           标准算法: min(向上笔的高点)"""
        # 严格算法将在 parser.py 中实现并注入，这里做基础占位
        up_bis = [b for b in self.bis[:3] if b.direction == Direction.UP]
        return min(b.high for b in up_bis) if up_bis else 0
        
    @property
    def ZD(self) -> float:
        """中枢低点 (ZhongShu Di)"""
        down_bis = [b for b in self.bis[:3] if b.direction == Direction.DOWN]
        return max(b.low for b in down_bis) if down_bis else 0

    @property
    def GG(self) -> float:
        return max(b.high for b in self.bis)
        
    @property
    def DD(self) -> float:
        return min(b.low for b in self.bis)
