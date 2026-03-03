"""
Common Module - Shared APIs and Utilities

This module contains shared components used by prediction, arbitrage and spot subsystems:
- PriceClient: Multi-source price data client (Binance, OKX, CoinGecko, etc.)
- BinanceClient: Shared Binance trading/data client
- Configuration utilities
"""

from .price_client import (
    PriceClient,
    PriceData,
    PriceMomentum,
    OrderBookData,
    MarketDepthAnalysis,
    get_btc_momentum,
    get_btc_order_book,
    get_btc_market_depth
)
from .binance_client import (
    BinanceAPIConfig,
    DEFAULT_BINANCE_CONFIG,
    BinanceClient,
    BinanceAPIError,
    TickerData,
    FundingRateData,
    FuturesContractData,
    AccountBalance,
    PositionData,
    OrderResult,
    create_client as create_binance_client,
)

__all__ = [
    'PriceClient',
    'PriceData',
    'PriceMomentum',
    'OrderBookData',
    'MarketDepthAnalysis',
    'get_btc_momentum',
    'get_btc_order_book',
    'get_btc_market_depth',
    'BinanceAPIConfig',
    'DEFAULT_BINANCE_CONFIG',
    'BinanceClient',
    'BinanceAPIError',
    'TickerData',
    'FundingRateData',
    'FuturesContractData',
    'AccountBalance',
    'PositionData',
    'OrderResult',
    'create_binance_client',
]
