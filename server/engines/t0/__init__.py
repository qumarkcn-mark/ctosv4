"""T+0 Bounded Trading Coach Engine.

确定性做T引擎，零 LLM 依赖。纯数学计算中枢边界和做T信号。
"""
from .t0_friction import calculate_round_trip_friction, is_grid_viable
from .t0_fractal import validate_1m_bottom_fractal, validate_1m_top_fractal
from .t0_state_machine import T0StateMachine, T0State, T0TickResult

__all__ = [
    "calculate_round_trip_friction",
    "is_grid_viable",
    "validate_1m_bottom_fractal",
    "validate_1m_top_fractal",
    "T0StateMachine",
    "T0State",
    "T0TickResult",
]
