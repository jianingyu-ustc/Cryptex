"""
Common Module - Shared APIs and Utilities

This module contains shared components used by both prediction and arbitrage subsystems:
- PriceClient: Multi-source price data client (Binance, OKX, CoinGecko, etc.)
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

__all__ = [
    'PriceClient',
    'PriceData',
    'PriceMomentum',
    'OrderBookData',
    'MarketDepthAnalysis',
    'get_btc_momentum',
    'get_btc_order_book',
    'get_btc_market_depth',
]