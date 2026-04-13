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
class FeatureElement:
    """特征序列元素 (提取出的包含特定极值特征的区间)"""
    bi: Bi
    high: float
    low: float
    # 与前一个元素的包含处理后的原素集合，类似 K线的包含处理
    elements: List[Bi] = field(default_factory=list)

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
    """中枢 (支持基于笔，或基于线段的多段重叠)"""
    bis: List[Bi] = field(default_factory=list)
    segs: List[Segment] = field(default_factory=list)
    
    @property
    def _components(self):
        """返回构成中枢的基础组件（线段或笔）"""
        return self.segs if self.segs else self.bis
    
    @property
    def ZG(self) -> float:
        """中枢高点: min(前三组件的 high)"""
        comps = self._components[:3]
        return min(c.high for c in comps) if comps else 0
        
    @property
    def ZD(self) -> float:
        """中枢低点: max(前三组件的 low)"""
        comps = self._components[:3]
        return max(c.low for c in comps) if comps else 0

    @property
    def GG(self) -> float:
        comps = self._components
        return max(c.high for c in comps) if comps else 0
        
    @property
    def DD(self) -> float:
        comps = self._components
        return min(c.low for c in comps) if comps else 0
