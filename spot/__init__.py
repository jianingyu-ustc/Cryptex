"""
Spot auto-trading subsystem.
"""

from .config import SpotTradingConfig, DEFAULT_SPOT_CONFIG
from .models import SpotSignal, SpotPosition, SpotTrade
from .strategy import SpotStrategyEngine
from .execution import SpotExecutionEngine

__all__ = [
    "SpotTradingConfig",
    "DEFAULT_SPOT_CONFIG",
    "SpotSignal",
    "SpotPosition",
    "SpotTrade",
    "SpotStrategyEngine",
    "SpotExecutionEngine",
]

