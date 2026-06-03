"""T+0 有边界做T状态机 — 核心引擎。

确定性，零 LLM 依赖。基于 5M 中枢 ZG/ZD 边界 + 1M 分型确认执行日内做T。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .t0_friction import calculate_round_trip_friction, is_grid_viable
from .t0_fractal import validate_1m_bottom_fractal, validate_1m_top_fractal, calculate_atr_1m
from .ppe_t0_policy import LONG_ONLY, OBSERVE_ONLY, SHORT_ONLY

logger = logging.getLogger(__name__)
LEGACY_BOTH = "BOTH"


class T0State(str, Enum):
    IDLE = "IDLE"                     # 底仓状态，无超额敞口
    POSITION_LONG = "POSITION_LONG"   # 正T：已低吸，持仓 Base+T
    POSITION_SHORT = "POSITION_SHORT"  # 倒T：已高抛，持仓 Base-T
    LOCKDOWN = "LOCKDOWN"             # 当日止损后锁死


@dataclass
class T0TickResult:
    state: str
    signal: Optional[str]              # BUY_LONG/SELL_LONG/SELL_SHORT/BUY_SHORT/STOP_LONG/SWEEP_LONG/REDUCE_LOCK
    signal_price: Optional[float]
    pivot_zd: Optional[float]
    pivot_zg: Optional[float]
    entry_price: Optional[float]
    target_price: Optional[float]
    stop_structural: Optional[float]   # 结构止损 (ZD - 1.2×ATR)
    stop_catastrophic: Optional[float] # 灾难止损 (入场价 × 0.97)
    is_grid_viable: bool
    friction_per_share: float
    daily_pnl: float
    daily_trades: int
    daily_stop_count: int
    reason: str
    signal_qty: Optional[int] = None
    available_t0_qty: Optional[int] = None
    risk_budget_left: Optional[float] = None
    lock_reason: str = ""
    position_constraints: dict = field(default_factory=dict)
    allowed_t0_direction: str = OBSERVE_ONLY
    size_multiplier: float = 0.0
    ppe_stage: Optional[int] = None
    policy_reason: str = ""
    policy_source_run_id: str = ""
    current_pivot_id: str = ""
    traded_pivot_count: int = 0
    reduce_lock_warning: str = ""


class T0StateMachine:
    """有边界做T状态机。

    生命周期：每交易日 reset_daily()，每次价格更新 tick()。
    状态转换：
      IDLE → POSITION_LONG（低吸触发 + 1M 底分型确认）
      IDLE → POSITION_SHORT（高抛触发 + 1M 顶分型确认）
      POSITION_LONG → IDLE（止盈：价格触达 ZG）
      POSITION_LONG → LOCKDOWN（止损：跌破结构或灾难止损）
      POSITION_SHORT → IDLE（买回：价格跌回 ZD，或低于卖出价且 1M 底分型确认）
      POSITION_SHORT → IDLE（REDUCE_LOCK：尾盘未回接，转为减仓锁利事件，不强制买回）
      LOCKDOWN → （当日不再开仓，次日 reset_daily() 解锁）
    """

    # 触发区阈值：距中枢边界在此比例内视为"进入触发区"
    TRIGGER_ZONE_RATIO = 0.005   # 0.5%
    # 结构止损缓冲倍数
    STRUCTURAL_STOP_ATR_MULT = 1.2
    # 灾难止损比率（入场价的 97%）
    CATASTROPHIC_STOP_RATIO = 0.97
    # 笔振幅衰减阈值：当前向下笔振幅 / 上一笔振幅 < 此值才允许买入
    BI_STRENGTH_VETO_THRESHOLD = 0.7
    # 首次触碰 ZD 时的额度比例（未验证支撑，减半试探）
    FIRST_TOUCH_QTY_RATIO = 0.5

    def __init__(
        self,
        symbol: str,
        t0_qty: int,
        *,
        available_t0_qty: Optional[int] = None,
        max_t0_qty: Optional[int] = None,
        daily_loss_limit: Optional[float] = None,
        consecutive_stop_limit: int = 2,
    ):
        """
        Args:
            symbol: 标的代码
            t0_qty: 做T数量（股），必须为 100 的整数倍
            available_t0_qty: 今日可用于倒T高抛的 T+1 底仓数量；None 时按 t0_qty 兼容旧行为
            max_t0_qty: 系统/单票额度上限
            daily_loss_limit: 日内亏损上限；None 表示不启用
            consecutive_stop_limit: 连续止损锁定阈值
        """
        self.symbol = symbol
        self.t0_qty = t0_qty
        self.available_t0_qty = available_t0_qty
        self.max_t0_qty = max_t0_qty
        self.daily_loss_limit = daily_loss_limit
        self.consecutive_stop_limit = consecutive_stop_limit

        # 日内状态（reset_daily 清零）
        self._state = T0State.IDLE
        self._trade_date: Optional[str] = None
        self._entry_price: Optional[float] = None
        self._target_price: Optional[float] = None
        self._stop_structural: Optional[float] = None
        self._stop_catastrophic: Optional[float] = None
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._daily_stop_count: int = 0
        self._current_open_qty: int = t0_qty
        self._lock_reason: str = ""
        self._allowed_t0_direction: str = OBSERVE_ONLY
        self._size_multiplier: float = 0.0
        self._ppe_stage: Optional[int] = None
        self._policy_reason: str = ""
        self._policy_source_run_id: str = ""
        self._current_pivot_id: str = ""
        self._entry_pivot_id: str = ""
        self._entry_direction: str = ""
        self._traded_pivot_ids: set[str] = set()
        self._reduce_lock_warning: str = ""
        # 笔动力学状态
        self._zd_touch_count: int = 0      # 当前中枢 ZD 被触碰次数
        self._last_pivot_zd: Optional[float] = None  # 上一次 tick 的 ZD，用于检测中枢漂移

    # ------------------------------------------------------------------ #
    #  公共接口
    # ------------------------------------------------------------------ #

    def reset_daily(self, date_str: str) -> None:
        """每交易日重置日内状态。"""
        self._trade_date = date_str
        self._state = T0State.IDLE
        self._entry_price = None
        self._target_price = None
        self._current_open_qty = self.t0_qty
        self._zd_touch_count = 0
        self._last_pivot_zd = None
        self._stop_structural = None
        self._stop_catastrophic = None
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._daily_stop_count = 0
        self._lock_reason = ""
        self._current_pivot_id = ""
        self._entry_pivot_id = ""
        self._entry_direction = ""
        self._traded_pivot_ids = set()
        self._reduce_lock_warning = ""
        logger.debug("[T0 %s] 日内重置 date=%s", self.symbol, date_str)

    def tick(
        self,
        current_price: float,
        timestamp: str,
        pivot_zd: Optional[float],
        pivot_zg: Optional[float],
        klines_1m: Optional[list] = None,
        klines_5m: Optional[list] = None,
        atr_5m: Optional[float] = None,
        bi_strength_ratio: Optional[float] = None,  # 笔振幅衰减比，None 表示无数据（跳过过滤）
        pressure_nearby: bool = True,
        base_trend_gear: Optional[str] = None,
        volume_surge_ratio: Optional[float] = None,
        pivot_id: Optional[str] = None,
        allowed_t0_direction: Optional[str] = None,
        size_multiplier: Optional[float] = None,
        ppe_stage: Optional[int] = None,
        policy_reason: str = "",
        policy_source_run_id: str = "",
    ) -> T0TickResult:
        """每次价格更新调用一次。

        Args:
            current_price: 当前价格
            timestamp: 当前时间戳 (ISO 字符串，如 "2025-05-26 10:30:00")
            pivot_zd: 5M 中枢下沿 ZD
            pivot_zg: 5M 中枢上沿 ZG
            klines_1m: 最近 N 根已收盘 1M K线（时间升序）
            klines_5m: 最近 N 根已收盘 5M K线（开盘上冲过滤使用）
            atr_5m: 5M 级 ATR（用于结构止损计算）
            bi_strength_ratio: 当前向下笔振幅 / 上一向下笔振幅。
                < BI_STRENGTH_VETO_THRESHOLD (0.7) = 空头动能衰减，允许开仓。
                >= 0.7 = 空头仍在加速，拒绝买入。
                None = 笔数据不足，跳过此过滤层。
            pressure_nearby: 是否接近有效压力/中枢上沿/前高；False 时不开倒T。
            base_trend_gear: 大级别档位；强主升时不开倒T。
            volume_surge_ratio: 量能放大倍率；持续放量时不开倒T。
            pivot_id: 当前 5M 中枢窗口唯一标识，用于一窗一做。
            allowed_t0_direction: PPE 注入的日内方向许可。
            size_multiplier: PPE 注入的额度乘数。
            ppe_stage: PPE 大周期阶段。
            policy_reason: PPE 方向许可的原因。
            policy_source_run_id: 来源推演版本。

        Returns:
            T0TickResult
        """
        # 自动日内重置
        today = timestamp[:10] if timestamp else ""
        if today and today != self._trade_date:
            self.reset_daily(today)

        legacy_policy_mode = allowed_t0_direction is None and size_multiplier is None and not policy_source_run_id and not policy_reason
        self._apply_ppe_policy(
            pivot_id=pivot_id,
            allowed_t0_direction=LEGACY_BOTH if legacy_policy_mode else allowed_t0_direction,
            size_multiplier=1.0 if legacy_policy_mode else size_multiplier,
            ppe_stage=ppe_stage,
            policy_reason=policy_reason or ("兼容旧状态机直接调用" if legacy_policy_mode else ""),
            policy_source_run_id=policy_source_run_id,
        )

        # 计算摩擦成本
        rt_friction = calculate_round_trip_friction(current_price, self.t0_qty)
        fps = rt_friction["cost_per_share"]

        # 中枢边界检查
        has_pivot = pivot_zd is not None and pivot_zg is not None and pivot_zg > pivot_zd
        grid_spread = (pivot_zg - pivot_zd) if has_pivot else 0.0
        viable = is_grid_viable(grid_spread, fps) if has_pivot else False

        # 1M ATR（用于结构止损）
        atr_1m = calculate_atr_1m(klines_1m) if klines_1m else 0.0
        # 优先用 5M ATR（更稳定），无则用 1M ATR
        atr_ref = atr_5m if atr_5m and atr_5m > 0 else atr_1m

        def make_result(
            signal=None,
            signal_price=None,
            reason="",
            signal_qty: Optional[int] = None,
            entry_price_for_result: Optional[float] = None,
        ):
            return T0TickResult(
                state=self._state.value,
                signal=signal,
                signal_price=signal_price,
                pivot_zd=pivot_zd,
                pivot_zg=pivot_zg,
                entry_price=entry_price_for_result if entry_price_for_result is not None else self._entry_price,
                target_price=self._target_price,
                stop_structural=self._stop_structural,
                stop_catastrophic=self._stop_catastrophic,
                is_grid_viable=viable,
                friction_per_share=fps,
                daily_pnl=self._daily_pnl,
                daily_trades=self._daily_trades,
                daily_stop_count=self._daily_stop_count,
                reason=reason,
                signal_qty=signal_qty,
                available_t0_qty=self._short_available_qty(),
                risk_budget_left=self._risk_budget_left(),
                lock_reason=self._lock_reason,
                position_constraints=self._position_constraints(),
                allowed_t0_direction=self._allowed_t0_direction,
                size_multiplier=self._size_multiplier,
                ppe_stage=self._ppe_stage,
                policy_reason=self._policy_reason,
                policy_source_run_id=self._policy_source_run_id,
                current_pivot_id=self._current_pivot_id,
                traded_pivot_count=len(self._traded_pivot_ids),
                reduce_lock_warning=self._reduce_lock_warning,
            )

        # ---- 各状态处理 ----

        if self._state == T0State.LOCKDOWN:
            return make_result(reason=self._lock_reason or "当日已触发止损，锁死不再开仓")

        if self._state == T0State.POSITION_LONG:
            return self._tick_position_long(current_price, pivot_zd, pivot_zg, atr_ref, fps, make_result)

        if self._state == T0State.POSITION_SHORT:
            return self._tick_position_short(current_price, pivot_zd, pivot_zg, klines_1m, fps, make_result)

        if self._should_lock_for_risk():
            self._state = T0State.LOCKDOWN
            return make_result(reason=self._lock_reason)

        # IDLE 态：检查是否进入触发区
        if not has_pivot or not viable:
            return make_result(reason=f"中枢无效或摩擦不足 viable={viable} spread={grid_spread:.3f} fps={fps:.4f}")

        if self._allowed_t0_direction == OBSERVE_ONLY or self._size_multiplier <= 0:
            return make_result(reason=self._policy_reason or "PPE 未允许 T0 开仓，保持观察")

        # 中枢漂移检测：若 ZD 发生变化，重置触碰计数器
        if pivot_zd != self._last_pivot_zd:
            self._zd_touch_count = 0
            self._last_pivot_zd = pivot_zd

        # 正T触发：价格接近 ZD（支撑区低吸）
        zd_trigger = round(pivot_zd * (1 + self.TRIGGER_ZONE_RATIO), 6)
        if current_price <= zd_trigger + 1e-9:
            if self._allowed_t0_direction not in {LONG_ONLY, LEGACY_BOTH}:
                return make_result(reason=f"PPE 方向为 {self._allowed_t0_direction}，拒绝正T开仓")
            if self._pivot_already_traded("LONG"):
                return make_result(reason=f"当前中枢 {self._current_pivot_id} 已完成过正T，一窗一做拒绝重复开仓")
            if not self._has_enough_edge(pivot_zg - current_price, fps):
                return make_result(reason=f"正T预期空间不足2倍摩擦成本，拒绝开仓 edge={pivot_zg - current_price:.4f} fps={fps:.4f}")
            # 第一层过滤：笔振幅衰减比（有数据时才过滤）
            if bi_strength_ratio is not None and bi_strength_ratio >= self.BI_STRENGTH_VETO_THRESHOLD:
                return make_result(
                    reason=f"笔动能未衰减（空头加速中），拒绝开仓。"
                           f"bi_ratio={bi_strength_ratio:.2f} >= {self.BI_STRENGTH_VETO_THRESHOLD}。"
                           f"等待向下笔力度收缩。"
                )

            # 第二层过滤：1M 底分型右侧确认
            if klines_1m and len(klines_1m) >= 3:
                bottom = validate_1m_bottom_fractal(klines_1m)
                if bottom["confirmed"]:
                    # ZD 触碰计数 +1
                    self._zd_touch_count += 1
                    # 首次触碰减半额度（支撑未验证，轻仓试探）
                    effective_qty = self._scaled_qty(self._long_configured_qty())
                    touch_note = ""
                    if self._zd_touch_count == 1:
                        configured_qty = self._scaled_qty(self._long_configured_qty())
                        half_qty = self._floor_lot(configured_qty * self.FIRST_TOUCH_QTY_RATIO)
                        effective_qty = 100 if configured_qty >= 100 and half_qty < 100 else half_qty
                        touch_note = f" [首次触碰ZD，减半试探 qty={effective_qty}]"
                    reason_detail = f"1M底分型确认@{bottom['fractal_low']}{touch_note}"
                    if bi_strength_ratio is not None:
                        reason_detail += f" bi_ratio={bi_strength_ratio:.2f}"
                    return self._open_long(
                        current_price, pivot_zd, pivot_zg, atr_ref, fps, make_result,
                        reason_detail, effective_qty=effective_qty,
                    )
                else:
                    return make_result(reason=f"进入ZD触发区但1M底分型未确认: {bottom['reason']}")
            else:
                return make_result(reason="进入ZD触发区，等待1M分型数据")

        # 倒T触发：价格接近 ZG（阻力区高抛）
        zg_trigger = round(pivot_zg * (1 - self.TRIGGER_ZONE_RATIO), 6)
        if current_price >= zg_trigger - 1e-9:
            if self._allowed_t0_direction not in {SHORT_ONLY, LEGACY_BOTH}:
                return make_result(reason=f"PPE 方向为 {self._allowed_t0_direction}，拒绝倒T开仓")
            if self._pivot_already_traded("SHORT"):
                return make_result(reason=f"当前中枢 {self._current_pivot_id} 已完成过倒T，一窗一做拒绝重复开仓")
            if not self._has_enough_edge(current_price - pivot_zd, fps):
                return make_result(reason=f"倒T预期空间不足2倍摩擦成本，拒绝开仓 edge={current_price - pivot_zd:.4f} fps={fps:.4f}")
            short_filter = self._short_open_quality_filter(
                timestamp=timestamp,
                klines_5m=klines_5m,
                pressure_nearby=pressure_nearby,
                base_trend_gear=base_trend_gear,
                volume_surge_ratio=volume_surge_ratio,
            )
            if short_filter:
                return make_result(reason=short_filter)

            short_qty = self._scaled_qty(self._short_available_qty())
            if short_qty < 100:
                return make_result(reason="T+1 可卖底仓不足100股，拒绝开倒T")

            # 检查 1M 顶分型确认
            if klines_1m and len(klines_1m) >= 3:
                top = validate_1m_top_fractal(klines_1m)
                if top["confirmed"]:
                    return self._open_short(current_price, pivot_zd, pivot_zg, atr_ref, fps, make_result,
                                            f"1M顶分型确认@{top['fractal_high']}", effective_qty=short_qty)
                else:
                    return make_result(reason=f"进入ZG触发区但1M顶分型未确认: {top['reason']}")
            else:
                return make_result(reason="进入ZG触发区，等待1M分型数据")

        return make_result(reason=f"IDLE 等待触发 ZD={pivot_zd:.2f} ZG={pivot_zg:.2f} 当前={current_price:.2f}")

    def force_sweep(self, current_price: float) -> T0TickResult:
        """14:55 强制平仓扫尾。"""
        signal = None
        reason = "14:55强制平仓"
        qty = None
        entry = None

        if self._state == T0State.POSITION_LONG:
            signal = "SWEEP_LONG"
            qty = self._effective_open_qty()
            entry = self._entry_price
            pnl = (current_price - entry) * qty
            rt = calculate_round_trip_friction(current_price, qty)
            self._daily_pnl += pnl - rt["sell_cost"]
            self._daily_trades += 1
            self._state = T0State.IDLE
            self._entry_price = None
            self._entry_pivot_id = ""
            self._entry_direction = ""
            self._current_open_qty = self.t0_qty
        elif self._state == T0State.POSITION_SHORT:
            # 倒T不是裸空。尾盘不再高位强制买回，未低价回接则确认成减仓锁利事件。
            signal = "REDUCE_LOCK"
            reason = "尾盘未出现低价回接，倒T转为减仓锁利"
            qty = self._effective_open_qty()
            entry = self._entry_price
            self._mark_entry_pivot_traded()
            self._reduce_lock_warning = self._build_reduce_lock_warning(qty)
            self._state = T0State.IDLE
            self._entry_price = None
            self._target_price = None
            self._stop_structural = None
            self._stop_catastrophic = None
            self._entry_pivot_id = ""
            self._entry_direction = ""
            self._current_open_qty = self.t0_qty

        rt_friction = calculate_round_trip_friction(current_price, self.t0_qty)
        fps = rt_friction["cost_per_share"]

        return T0TickResult(
            state=self._state.value,
            signal=signal,
            signal_price=current_price if signal else None,
            pivot_zd=None,
            pivot_zg=None,
            entry_price=entry,
            target_price=None,
            stop_structural=None,
            stop_catastrophic=None,
            is_grid_viable=False,
            friction_per_share=fps,
            daily_pnl=self._daily_pnl,
            daily_trades=self._daily_trades,
            daily_stop_count=self._daily_stop_count,
            reason=reason,
            signal_qty=qty if signal else None,
            available_t0_qty=self._short_available_qty(),
            risk_budget_left=self._risk_budget_left(),
            lock_reason=self._lock_reason,
            position_constraints=self._position_constraints(),
            allowed_t0_direction=self._allowed_t0_direction,
            size_multiplier=self._size_multiplier,
            ppe_stage=self._ppe_stage,
            policy_reason=self._policy_reason,
            policy_source_run_id=self._policy_source_run_id,
            current_pivot_id=self._current_pivot_id,
            traded_pivot_count=len(self._traded_pivot_ids),
            reduce_lock_warning=self._reduce_lock_warning,
        )

    def serialize(self) -> dict:
        """序列化状态机状态为字典（存入 t0_state_cache.state_json）。"""
        return {
            "symbol": self.symbol,
            "t0_qty": self.t0_qty,
            "state": self._state.value,
            "trade_date": self._trade_date,
            "entry_price": self._entry_price,
            "target_price": self._target_price,
            "stop_structural": self._stop_structural,
            "stop_catastrophic": self._stop_catastrophic,
            "daily_pnl": self._daily_pnl,
            "daily_trades": self._daily_trades,
            "daily_stop_count": self._daily_stop_count,
            "current_open_qty": self._current_open_qty,
            "available_t0_qty": self.available_t0_qty,
            "max_t0_qty": self.max_t0_qty,
            "daily_loss_limit": self.daily_loss_limit,
            "consecutive_stop_limit": self.consecutive_stop_limit,
            "risk_budget_left": self._risk_budget_left(),
            "lock_reason": self._lock_reason,
            "position_constraints": self._position_constraints(),
            "allowed_t0_direction": self._allowed_t0_direction,
            "size_multiplier": self._size_multiplier,
            "ppe_stage": self._ppe_stage,
            "policy_reason": self._policy_reason,
            "policy_source_run_id": self._policy_source_run_id,
            "current_pivot_id": self._current_pivot_id,
            "entry_pivot_id": self._entry_pivot_id,
            "entry_direction": self._entry_direction,
            "traded_pivot_ids": sorted(self._traded_pivot_ids),
            "traded_pivot_count": len(self._traded_pivot_ids),
            "reduce_lock_warning": self._reduce_lock_warning,
            # 笔动力学状态（进程重启后恢复计数）
            "zd_touch_count": self._zd_touch_count,
            "last_pivot_zd": self._last_pivot_zd,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "T0StateMachine":
        """从字典恢复状态机（从 t0_state_cache.state_json 读取）。"""
        machine = cls(
            symbol=data["symbol"],
            t0_qty=data["t0_qty"],
            available_t0_qty=data.get("available_t0_qty"),
            max_t0_qty=data.get("max_t0_qty"),
            daily_loss_limit=data.get("daily_loss_limit"),
            consecutive_stop_limit=data.get("consecutive_stop_limit", 2),
        )
        machine._state = T0State(data.get("state", T0State.IDLE.value))
        machine._trade_date = data.get("trade_date")
        machine._entry_price = data.get("entry_price")
        machine._target_price = data.get("target_price")
        machine._stop_structural = data.get("stop_structural")
        machine._stop_catastrophic = data.get("stop_catastrophic")
        machine._daily_pnl = data.get("daily_pnl", 0.0)
        machine._daily_trades = data.get("daily_trades", 0)
        machine._daily_stop_count = data.get("daily_stop_count", 0)
        machine._current_open_qty = data.get("current_open_qty", machine.t0_qty)
        machine._lock_reason = data.get("lock_reason", "")
        machine._allowed_t0_direction = _normalize_direction(data.get("allowed_t0_direction"))
        machine._size_multiplier = _normalize_multiplier(data.get("size_multiplier"))
        machine._ppe_stage = data.get("ppe_stage")
        machine._policy_reason = str(data.get("policy_reason") or "")
        machine._policy_source_run_id = str(data.get("policy_source_run_id") or "")
        machine._current_pivot_id = str(data.get("current_pivot_id") or "")
        machine._entry_pivot_id = str(data.get("entry_pivot_id") or "")
        machine._entry_direction = str(data.get("entry_direction") or "")
        machine._traded_pivot_ids = set(str(x) for x in (data.get("traded_pivot_ids") or []) if x)
        machine._reduce_lock_warning = str(data.get("reduce_lock_warning") or "")
        machine._zd_touch_count = data.get("zd_touch_count", 0)
        machine._last_pivot_zd = data.get("last_pivot_zd")
        return machine

    # ------------------------------------------------------------------ #
    #  内部方法
    # ------------------------------------------------------------------ #

    def _open_long(self, price, pivot_zd, pivot_zg, atr_ref, fps, make_result, reason_detail,
                   effective_qty: Optional[int] = None):
        """开正T仓位（低吸买入）。effective_qty 允许首次触碰时使用减半额度。"""
        effective_qty = self._floor_lot(effective_qty if effective_qty is not None else self._long_configured_qty())
        if effective_qty < 100:
            return make_result(reason="单票做T额度不足100股，拒绝开正T")
        self._state = T0State.POSITION_LONG
        self._entry_price = price
        self._target_price = pivot_zg
        self._entry_pivot_id = self._current_pivot_id
        self._entry_direction = "LONG"
        # 记录实际使用的做T额度（不改变 self.t0_qty，仅影响本次仓位大小）
        self._current_open_qty = effective_qty if effective_qty else self.t0_qty
        # 结构止损 = ZD - 1.2 × ATR
        self._stop_structural = pivot_zd - self.STRUCTURAL_STOP_ATR_MULT * atr_ref if atr_ref > 0 else pivot_zd * 0.995
        # 灾难止损 = 入场价 × 0.97
        self._stop_catastrophic = price * self.CATASTROPHIC_STOP_RATIO
        self._daily_trades += 1
        logger.info("[T0 %s] 开正T BUY_LONG price=%.2f target=%.2f stop_s=%.2f stop_c=%.2f %s",
                    self.symbol, price, self._target_price, self._stop_structural, self._stop_catastrophic, reason_detail)
        return make_result(signal="BUY_LONG", signal_price=price,
                           reason=f"开正T {reason_detail}", signal_qty=self._current_open_qty)

    def _open_short(self, price, pivot_zd, pivot_zg, atr_ref, fps, make_result, reason_detail,
                    effective_qty: Optional[int] = None):
        """开倒T仓位（高抛卖出）。"""
        effective_qty = self._floor_lot(effective_qty if effective_qty is not None else self._short_available_qty())
        if effective_qty < 100:
            return make_result(reason="T+1 可卖底仓不足100股，拒绝开倒T")
        self._state = T0State.POSITION_SHORT
        self._entry_price = price
        self._target_price = pivot_zd
        self._current_open_qty = effective_qty
        self._entry_pivot_id = self._current_pivot_id
        self._entry_direction = "SHORT"
        # 倒T基于已有底仓，不是裸空；不设置高位强制买回止损，避免把“少赚”变成现金亏损。
        self._stop_structural = None
        self._stop_catastrophic = None
        self._daily_trades += 1
        logger.info("[T0 %s] 开倒T SELL_SHORT price=%.2f target=%.2f",
                    self.symbol, price, self._target_price)
        return make_result(signal="SELL_SHORT", signal_price=price,
                           reason=f"开倒T {reason_detail}", signal_qty=self._current_open_qty)

    def _tick_position_long(self, price, pivot_zd, pivot_zg, atr_ref, fps, make_result):
        """正T持仓中的 tick 处理。"""
        # 灾难止损（优先检查）
        if self._stop_catastrophic and price <= self._stop_catastrophic:
            return self._trigger_stop_long(price, make_result,
                                           f"灾难止损触发 price={price:.2f} <= {self._stop_catastrophic:.2f}")
        # 结构止损
        if self._stop_structural and price <= self._stop_structural:
            return self._trigger_stop_long(price, make_result,
                                           f"结构止损触发 price={price:.2f} <= {self._stop_structural:.2f}")
        # 止盈：价格触达 ZG
        if self._target_price and price >= self._target_price:
            qty = self._effective_open_qty()
            entry = self._entry_price
            pnl = (price - entry) * qty
            rt = calculate_round_trip_friction(price, qty)
            net_pnl = pnl - rt["sell_cost"]
            self._daily_pnl += net_pnl
            self._mark_entry_pivot_traded()
            self._state = T0State.IDLE
            self._entry_price = None
            self._target_price = None
            self._entry_pivot_id = ""
            self._entry_direction = ""
            self._current_open_qty = self.t0_qty
            logger.info("[T0 %s] 正T止盈 SELL_LONG price=%.2f entry=%.2f net_pnl=%.2f",
                        self.symbol, price, entry, net_pnl)
            return make_result(signal="SELL_LONG", signal_price=price,
                               reason=f"正T止盈: price={price:.2f} >= ZG={self._target_price or pivot_zg:.2f} net_pnl={net_pnl:.2f}",
                               signal_qty=qty,
                               entry_price_for_result=entry)
        # 持仓等待
        return make_result(reason=f"POSITION_LONG 持仓中 entry={self._entry_price:.2f} target={self._target_price:.2f}")

    def _tick_position_short(self, price, pivot_zd, pivot_zg, klines_1m, fps, make_result):
        """倒T持仓中的 tick 处理。"""
        # 止盈：价格跌回 ZD
        if self._target_price and price <= self._target_price:
            qty = self._effective_open_qty()
            entry = self._entry_price
            pnl = (entry - price) * qty
            rt = calculate_round_trip_friction(price, qty)
            net_pnl = pnl - rt["buy_cost"]
            self._daily_pnl += net_pnl
            self._mark_entry_pivot_traded()
            self._state = T0State.IDLE
            self._entry_price = None
            self._target_price = None
            self._entry_pivot_id = ""
            self._entry_direction = ""
            self._current_open_qty = self.t0_qty
            logger.info("[T0 %s] 倒T止盈 BUY_SHORT price=%.2f entry=%.2f net_pnl=%.2f",
                        self.symbol, price, entry, net_pnl)
            return make_result(signal="BUY_SHORT", signal_price=price,
                               reason=f"倒T止盈: price={price:.2f} <= ZD={self._target_price or pivot_zd:.2f} net_pnl={net_pnl:.2f}",
                               signal_qty=qty,
                               entry_price_for_result=entry)

        # 智能回接：只有价格低于卖出均价，且 1M 底分型右侧确认，才买回锁利。
        if self._entry_price and price < self._entry_price:
            if klines_1m and len(klines_1m) >= 3:
                bottom = validate_1m_bottom_fractal(klines_1m)
                if bottom["confirmed"]:
                    qty = self._effective_open_qty()
                    entry = self._entry_price
                    pnl = (entry - price) * qty
                    rt = calculate_round_trip_friction(price, qty)
                    net_pnl = pnl - rt["buy_cost"]
                    self._daily_pnl += net_pnl
                    self._mark_entry_pivot_traded()
                    self._state = T0State.IDLE
                    self._entry_price = None
                    self._target_price = None
                    self._entry_pivot_id = ""
                    self._entry_direction = ""
                    self._current_open_qty = self.t0_qty
                    logger.info("[T0 %s] 倒T智能回接 BUY_SHORT price=%.2f entry=%.2f net_pnl=%.2f",
                                self.symbol, price, entry, net_pnl)
                    return make_result(signal="BUY_SHORT", signal_price=price,
                                       reason=f"倒T回接: price={price:.2f} < entry={entry:.2f} 且1M底分型确认@{bottom['fractal_low']} net_pnl={net_pnl:.2f}",
                                       signal_qty=qty,
                                       entry_price_for_result=entry)
                return make_result(reason=f"POSITION_SHORT 待回补，价格低于卖出价但1M底分型未确认: {bottom['reason']}")
            return make_result(reason=f"POSITION_SHORT 待回补 entry={self._entry_price:.2f} 当前已低于卖出价，等待1M分型数据")

        return make_result(reason=f"POSITION_SHORT 待回补 entry={self._entry_price:.2f} target={self._target_price:.2f}，不做高位强制买回")

    def _trigger_stop_long(self, price, make_result, reason):
        """触发正T止损，进入 LOCKDOWN。"""
        qty = self._effective_open_qty()
        entry = self._entry_price
        pnl = (price - entry) * qty
        rt = calculate_round_trip_friction(price, qty)
        net_pnl = pnl - rt["sell_cost"]
        self._daily_pnl += net_pnl
        self._daily_stop_count += 1
        self._mark_entry_pivot_traded()
        self._state = T0State.LOCKDOWN
        self._lock_reason = f"正T止损锁定：{reason}"
        self._entry_price = None
        self._entry_pivot_id = ""
        self._entry_direction = ""
        self._current_open_qty = self.t0_qty
        logger.warning("[T0 %s] 正T止损 STOP_LONG %s net_pnl=%.2f", self.symbol, reason, net_pnl)
        return make_result(
            signal="STOP_LONG",
            signal_price=price,
            reason=f"正T止损→LOCKDOWN: {reason}",
            signal_qty=qty,
            entry_price_for_result=entry,
        )

    def _effective_open_qty(self) -> int:
        """返回当前已开 T 仓数量；旧状态无该字段时回退到 t0_qty。"""
        qty = int(self._current_open_qty or self.t0_qty or 0)
        return max(100, qty)

    def _apply_ppe_policy(
        self,
        *,
        pivot_id: Optional[str],
        allowed_t0_direction: Optional[str],
        size_multiplier: Optional[float],
        ppe_stage: Optional[int],
        policy_reason: str,
        policy_source_run_id: str,
    ) -> None:
        """应用 PPE 对 T0 的上位许可。"""
        self._current_pivot_id = str(pivot_id or "").strip()
        self._allowed_t0_direction = _normalize_direction(allowed_t0_direction)
        self._size_multiplier = _normalize_multiplier(size_multiplier)
        self._ppe_stage = ppe_stage
        self._policy_reason = str(policy_reason or "").strip()
        self._policy_source_run_id = str(policy_source_run_id or "").strip()

    def _pivot_key(self, direction: str) -> str:
        pivot = self._current_pivot_id or f"zd:{self._last_pivot_zd or ''}"
        return f"{pivot}:{direction}"

    def _pivot_already_traded(self, direction: str) -> bool:
        return self._pivot_key(direction) in self._traded_pivot_ids

    def _mark_entry_pivot_traded(self) -> None:
        if self._entry_pivot_id and self._entry_direction:
            self._traded_pivot_ids.add(f"{self._entry_pivot_id}:{self._entry_direction}")

    def _scaled_qty(self, base_qty: int) -> int:
        """应用 PPE 阶段额度乘数，并保持 A 股一手约束。"""
        base = self._floor_lot(base_qty)
        if base < 100 or self._size_multiplier <= 0:
            return 0
        qty = self._floor_lot(base * self._size_multiplier)
        if self._size_multiplier > 0 and qty < 100:
            return 100
        return min(base, qty)

    def _has_enough_edge(self, expected_edge: float, fps: float) -> bool:
        """预期空间必须大于 2 倍摩擦成本。"""
        try:
            return float(expected_edge) > 2 * float(fps)
        except Exception:
            return False

    def _build_reduce_lock_warning(self, qty: Optional[int]) -> str:
        """倒T未回补会造成底仓流失，给前端和复盘一个明确警示。"""
        qty = int(qty or 0)
        available = self._short_available_qty()
        if qty > 0 and available - qty < 100:
            return "做T导致底仓流失，转为观察"
        return ""

    def _floor_lot(self, qty: Optional[float]) -> int:
        """A股整手约束：向下取整到100股。"""
        try:
            value = int(float(qty or 0))
        except Exception:
            value = 0
        return max(0, (value // 100) * 100)

    def _long_configured_qty(self) -> int:
        """正T可用额度：用户配置与系统上限取小，并按整手下取整。"""
        qty = self._floor_lot(self.t0_qty)
        if self.max_t0_qty is not None:
            qty = min(qty, self._floor_lot(self.max_t0_qty))
        return qty

    def _short_available_qty(self) -> int:
        """倒T可用底仓：T+1可卖、用户配置、系统上限三者取小。"""
        qty = self._long_configured_qty()
        if self.available_t0_qty is not None:
            qty = min(qty, self._floor_lot(self.available_t0_qty))
        return self._floor_lot(qty)

    def _risk_budget_left(self) -> Optional[float]:
        """日内风险预算剩余额度。None 表示未配置硬预算。"""
        if self.daily_loss_limit is None:
            return None
        return round(float(self.daily_loss_limit) + float(self._daily_pnl), 2)

    def _position_constraints(self) -> dict:
        """输出给 API / 前端的资金约束摘要。"""
        return {
            "configured_t0_qty": self._floor_lot(self.t0_qty),
            "available_t0_qty": self._short_available_qty(),
            "max_t0_qty": self._floor_lot(self.max_t0_qty) if self.max_t0_qty is not None else None,
            "lot_size": 100,
            "daily_loss_limit": self.daily_loss_limit,
            "consecutive_stop_limit": self.consecutive_stop_limit,
            "allowed_t0_direction": self._allowed_t0_direction,
            "size_multiplier": self._size_multiplier,
        }

    def _should_lock_for_risk(self) -> bool:
        """日内亏损或连续止损达到阈值后，进入 LOCKDOWN。"""
        if self.daily_loss_limit is not None and self._daily_pnl <= -abs(float(self.daily_loss_limit)):
            self._lock_reason = f"日内亏损达到上限 {self.daily_loss_limit:.2f}，T0 锁定"
            return True
        if self.consecutive_stop_limit and self._daily_stop_count >= self.consecutive_stop_limit:
            self._lock_reason = f"连续止损 {self._daily_stop_count} 次，T0 锁定"
            return True
        return False

    def _short_open_quality_filter(
        self,
        *,
        timestamp: str,
        klines_5m: Optional[list],
        pressure_nearby: bool,
        base_trend_gear: Optional[str],
        volume_surge_ratio: Optional[float],
    ) -> str:
        """倒T开仓质量过滤：避免开盘强冲和主升段卖飞。"""
        gear = str(base_trend_gear or "").upper()
        if gear in {"UP", "STRONG_UP", "THIRD_BUY", "LOCK_UP"}:
            return f"大级别处于强主升档位 {gear}，不开倒T"
        if not pressure_nearby:
            return "未接近有效压力/中枢上沿/前高，拒绝开倒T"
        if volume_surge_ratio is not None and volume_surge_ratio >= 1.8:
            return f"量能持续放大 ratio={volume_surge_ratio:.2f}，不开倒T"
        if self._is_opening_surge_window(timestamp):
            if not klines_5m or len(klines_5m) < 3:
                return "09:30-10:15 开盘上冲阶段，等待5M顶分型数据"
            top_5m = validate_1m_top_fractal(klines_5m)
            if not top_5m["confirmed"]:
                return f"开盘上冲阶段等待5M顶分型确认: {top_5m['reason']}"
        return ""

    def _is_opening_surge_window(self, timestamp: str) -> bool:
        """A股开盘惯性冲高窗口：09:30-10:15。"""
        try:
            dt = datetime.fromisoformat(str(timestamp).replace("T", " "))
        except Exception:
            return False
        minutes = dt.hour * 60 + dt.minute
        return (9 * 60 + 30) <= minutes <= (10 * 60 + 15)


def _normalize_direction(value: Optional[str]) -> str:
    direction = str(value or "").strip().upper()
    if direction in {LONG_ONLY, SHORT_ONLY, OBSERVE_ONLY, LEGACY_BOTH}:
        return direction
    return OBSERVE_ONLY


def _normalize_multiplier(value: Optional[float]) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    if num <= 0:
        return 0.0
    return min(1.0, num)
