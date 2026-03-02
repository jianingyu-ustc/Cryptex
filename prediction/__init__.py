"""
Prediction Subsystem - Polymarket-based Crypto Price Predictor

This module provides cryptocurrency price predictions based on Polymarket 
prediction market data and technical analysis.

Main Components:
- CryptoPredictor: Main prediction engine with multi-factor strategy
- PolymarketClient: Polymarket API client (Gamma, CLOB)
- Backtester: Historical accuracy validation
- PredictionDisplay: Rich terminal output

Usage:
    # As a module
    python -m prediction.main --help
    
    # Or import directly
    from prediction import CryptoPredictor, PolymarketClient
"""

from .predictor import (
    CryptoPredictor, 
    PredictionAggregator, 
    CryptoPrediction,
    PredictionDirection, 
    TimeFrame
)
from .api_client import PolymarketClient, MarketAnalyzer
from .backtest import Backtester, BacktestResult, PredictionResult, BacktestStats
from .display import PredictionDisplay, print_welcome
from .demo_data import DemoDataGenerator, get_demo_crypto_markets, get_demo_trades

__all__ = [
    # Core predictor
    'CryptoPredictor',
    'PredictionAggregator', 
    'CryptoPrediction',
    'PredictionDirection',
    'TimeFrame',
    # API client
    'PolymarketClient',
    'MarketAnalyzer',
    # Backtesting
    'Backtester',
    'BacktestResult',
    'PredictionResult',
    'BacktestStats',
    # Display
    'PredictionDisplay',
    'print_welcome',
    # Demo data
    'DemoDataGenerator',
    'get_demo_crypto_markets',
    'get_demo_trades',
]