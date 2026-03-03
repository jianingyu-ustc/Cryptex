"""
Compatibility layer for arbitrage Binance API.

The shared implementation now lives in `common.binance_client`.
"""

from typing import Optional

from common.binance_client import (
    BinanceClient,
    BinanceAPIError,
    BinanceAPIConfig,
    DEFAULT_BINANCE_CONFIG,
    TickerData,
    FundingRateData,
    FuturesContractData,
    AccountBalance,
    PositionData,
    OrderResult,
)
from .config import ArbitrageConfig, DEFAULT_CONFIG


def create_client(config: Optional[ArbitrageConfig] = None) -> BinanceClient:
    """Create shared Binance client using arbitrage config defaults."""
    return BinanceClient(config or DEFAULT_CONFIG)


__all__ = [
    "BinanceClient",
    "BinanceAPIError",
    "BinanceAPIConfig",
    "DEFAULT_BINANCE_CONFIG",
    "TickerData",
    "FundingRateData",
    "FuturesContractData",
    "AccountBalance",
    "PositionData",
    "OrderResult",
    "create_client",
]

