"""Shared CT-OS domain enums."""

from enum import Enum


class RadarMode(str, Enum):
    EMPTY = "EMPTY"
    HOLDING = "HOLDING"


class PlanType(str, Enum):
    ENTRY = "ENTRY"
    HOLDING = "HOLDING"
    ALERT = "ALERT"
    REVIEW = "REVIEW"


class PlanStatus(str, Enum):
    WATCHING = "WATCHING"
    TRIGGERED = "TRIGGERED"
    BLOCKED = "BLOCKED"
    INVALIDATED = "INVALIDATED"


class StructureProvider(str, Enum):
    BAOSTOCK = "baostock"
    TDX = "tdx"
    TENCENT = "tencent"
    QMT = "qmt"


class AdjustmentFlag(str, Enum):
    FRONT = "2"
    NONE = "3"


class StaleReason(str, Enum):
    NONE = ""
    NO_DATA = "NO_DATA"
    LEVEL_INCOMPLETE = "LEVEL_INCOMPLETE"
    ENGINE_ERROR = "ENGINE_ERROR"
    DATA_STALE = "DATA_STALE"
