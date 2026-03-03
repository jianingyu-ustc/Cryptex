"""
Arbitrage Subsystem - Unified Crypto Arbitrage Trading System

Supports three arbitrage strategies:
1. Funding Rate Arbitrage (Perpetual Funding)
2. Cash & Carry Arbitrage (Basis Arbitrage)  
3. Stablecoin Spread Arbitrage

Architecture:
- Market Data Layer: Real-time WebSocket + REST API
- Strategy Layer: BaseStrategy with 3 implementations
- Execution Layer: Order management and hedging
- Risk Control Layer: Position limits, stop-loss, liquidation detection
"""

from .config import ArbitrageConfig
from .strategy import (
    BaseStrategy,
    FundingRateStrategy,
    BasisArbitrageStrategy,
    StablecoinSpreadStrategy
)
from .execution import ExecutionEngine
from .risk import RiskManager
from .backtest import FundingBacktester, BasisBacktester, StablecoinBacktester

__all__ = [
    'ArbitrageConfig',
    'BaseStrategy',
    'FundingRateStrategy',
    'BasisArbitrageStrategy',
    'StablecoinSpreadStrategy',
    'ExecutionEngine',
    'RiskManager',
    'FundingBacktester',
    'BasisBacktester',
    'StablecoinBacktester',
]
