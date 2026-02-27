"""
External Price Data Client
Fetches real-time and historical crypto prices from public APIs
Supports authenticated Binance API for enhanced rate limits
"""

import asyncio
import subprocess
import json
import time
import hashlib
import hmac
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from config import BINANCE_API_KEY, BINANCE_API_BASE, COINGECKO_API_BASE


@dataclass
class PriceData:
    """Price data point"""
    symbol: str
    price: float
    timestamp: datetime
    volume_24h: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0


@dataclass
class PriceMomentum:
    """Price momentum indicators"""
    symbol: str
    current_price: float
    price_1m_ago: float
    price_5m_ago: float
    momentum_1m: float  # % change in last 1 minute
    momentum_5m: float  # % change in last 5 minutes
    volatility_5m: float  # Standard deviation of 5-min returns
    trend_direction: str  # "UP", "DOWN", "NEUTRAL"
    trend_strength: float  # 0-1 scale


class PriceClient:
    """Client for fetching crypto prices from multiple sources"""
    
    # CoinGecko API (free, no API key needed)
    COINGECKO_API = "https://api.coingecko.com/api/v3"
    
    # Binance API endpoints (multiple for fallback)
    BINANCE_APIS = [
        "https://api.binance.com/api/v3",      # Primary (Global)
        "https://api1.binance.com/api/v3",     # Fallback 1
        "https://api2.binance.com/api/v3",     # Fallback 2
        "https://api3.binance.com/api/v3",     # Fallback 3
        "https://api4.binance.com/api/v3",     # Fallback 4
        "https://api.binance.us/api/v3",       # Binance.US (for US servers)
    ]
    BINANCE_API = "https://api.binance.com/api/v3"  # Default
    
    # Alternative crypto price APIs
    ALTERNATIVE_APIS = [
        ("cryptocompare", "https://min-api.cryptocompare.com/data/price"),
        ("coinpaprika", "https://api.coinpaprika.com/v1"),
        ("kraken", "https://api.kraken.com/0/public"),
    ]
    
    # Symbol mapping
    SYMBOL_MAP = {
        "BTC": {"coingecko": "bitcoin", "binance": "BTCUSDT"},
        "ETH": {"coingecko": "ethereum", "binance": "ETHUSDT"},
        "SOL": {"coingecko": "solana", "binance": "SOLUSDT"},
        "DOGE": {"coingecko": "dogecoin", "binance": "DOGEUSDT"},
        "XRP": {"coingecko": "ripple", "binance": "XRPUSDT"},
    }
    
    def __init__(self):
        self._price_cache: Dict[str, Tuple[PriceData, float]] = {}
        self._price_history: Dict[str, List[Tuple[float, float]]] = {}  # symbol -> [(timestamp, price), ...]
        self._cache_duration = 10  # seconds
    
    def _curl_get(self, url: str, timeout: int = 10, headers: Dict = None) -> Optional[Dict]:
        """Make HTTP request using curl with optional headers"""
        try:
            cmd = ["curl", "-s", "-m", str(timeout)]
            if headers:
                for key, value in headers.items():
                    cmd.extend(["-H", f"{key}: {value}"])
            cmd.append(url)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 5
            )
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)
        except Exception:
            pass
        return None
    
    def _get_binance_headers(self) -> Dict:
        """Get Binance API headers with authentication if API key is configured"""
        headers = {}
        if BINANCE_API_KEY:
            headers["X-MBX-APIKEY"] = BINANCE_API_KEY
        return headers
    
    async def get_price_binance(self, symbol: str) -> Optional[PriceData]:
        """Get current price from Binance (with API key for better rate limits)"""
        binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
        if not binance_symbol:
            return None
        
        headers = self._get_binance_headers()
        
        # Try multiple Binance API endpoints
        for api_base in self.BINANCE_APIS:
            url = f"{api_base}/ticker/24hr?symbol={binance_symbol}"
            data = self._curl_get(url, timeout=5, headers=headers if headers else None)
            
            if data and "lastPrice" in data:
                return PriceData(
                    symbol=symbol.upper(),
                    price=float(data["lastPrice"]),
                    timestamp=datetime.now(timezone.utc),
                    volume_24h=float(data.get("volume", 0)) * float(data.get("lastPrice", 0)),
                    price_change_1h=0,  # Binance doesn't provide 1h change directly
                    price_change_24h=float(data.get("priceChangePercent", 0))
                )
        
        return None
    
    async def get_price_coingecko(self, symbol: str) -> Optional[PriceData]:
        """Get current price from CoinGecko"""
        cg_id = self.SYMBOL_MAP.get(symbol.upper(), {}).get("coingecko")
        if not cg_id:
            return None
        
        url = f"{self.COINGECKO_API}/simple/price?ids={cg_id}&vs_currencies=usd&include_24hr_vol=true&include_24hr_change=true"
        data = self._curl_get(url)
        
        if data and cg_id in data:
            coin_data = data[cg_id]
            return PriceData(
                symbol=symbol.upper(),
                price=float(coin_data.get("usd", 0)),
                timestamp=datetime.now(timezone.utc),
                volume_24h=float(coin_data.get("usd_24h_vol", 0)),
                price_change_24h=float(coin_data.get("usd_24h_change", 0))
            )
        return None
    
    async def get_price_cryptocompare(self, symbol: str) -> Optional[PriceData]:
        """Get current price from CryptoCompare (alternative source)"""
        symbol = symbol.upper()
        url = f"https://min-api.cryptocompare.com/data/price?fsym={symbol}&tsyms=USD"
        data = self._curl_get(url, timeout=10)
        
        if data and "USD" in data:
            return PriceData(
                symbol=symbol,
                price=float(data["USD"]),
                timestamp=datetime.now(timezone.utc)
            )
        return None
    
    async def get_price_kraken(self, symbol: str) -> Optional[PriceData]:
        """Get current price from Kraken (alternative source)"""
        # Kraken uses different symbol format
        kraken_map = {
            "BTC": "XXBTZUSD",
            "ETH": "XETHZUSD",
            "SOL": "SOLUSD",
            "DOGE": "XDGUSD",
            "XRP": "XXRPZUSD",
        }
        kraken_symbol = kraken_map.get(symbol.upper())
        if not kraken_symbol:
            return None
        
        url = f"https://api.kraken.com/0/public/Ticker?pair={kraken_symbol}"
        data = self._curl_get(url, timeout=10)
        
        if data and "result" in data and kraken_symbol in data["result"]:
            ticker = data["result"][kraken_symbol]
            return PriceData(
                symbol=symbol.upper(),
                price=float(ticker["c"][0]),  # Last trade price
                timestamp=datetime.now(timezone.utc),
                volume_24h=float(ticker["v"][1]) * float(ticker["c"][0])  # 24h volume in USD
            )
        return None
    
    async def get_price_coinpaprika(self, symbol: str) -> Optional[PriceData]:
        """Get current price from CoinPaprika (alternative source)"""
        # CoinPaprika uses different IDs
        paprika_map = {
            "BTC": "btc-bitcoin",
            "ETH": "eth-ethereum",
            "SOL": "sol-solana",
            "DOGE": "doge-dogecoin",
            "XRP": "xrp-xrp",
        }
        paprika_id = paprika_map.get(symbol.upper())
        if not paprika_id:
            return None
        
        url = f"https://api.coinpaprika.com/v1/tickers/{paprika_id}"
        data = self._curl_get(url, timeout=10)
        
        if data and "quotes" in data and "USD" in data["quotes"]:
            usd_data = data["quotes"]["USD"]
            return PriceData(
                symbol=symbol.upper(),
                price=float(usd_data.get("price", 0)),
                timestamp=datetime.now(timezone.utc),
                volume_24h=float(usd_data.get("volume_24h", 0)),
                price_change_24h=float(usd_data.get("percent_change_24h", 0))
            )
        return None
    
    async def get_current_price(self, symbol: str) -> Optional[PriceData]:
        """Get current price with caching, try multiple sources in order"""
        symbol = symbol.upper()
        now = time.time()
        
        # Check cache
        if symbol in self._price_cache:
            cached_data, cached_time = self._price_cache[symbol]
            if now - cached_time < self._cache_duration:
                return cached_data
        
        # Try sources in order of preference
        price_data = None
        
        # 1. Try Binance first (fastest, most reliable for crypto)
        price_data = await self.get_price_binance(symbol)
        
        # 2. Fallback to CoinGecko
        if not price_data:
            price_data = await self.get_price_coingecko(symbol)
        
        # 3. Fallback to Kraken
        if not price_data:
            price_data = await self.get_price_kraken(symbol)
        
        # 4. Fallback to CryptoCompare
        if not price_data:
            price_data = await self.get_price_cryptocompare(symbol)
        
        # 5. Fallback to CoinPaprika
        if not price_data:
            price_data = await self.get_price_coinpaprika(symbol)
        
        if price_data:
            self._price_cache[symbol] = (price_data, now)
            
            # Store in history for momentum calculation
            if symbol not in self._price_history:
                self._price_history[symbol] = []
            self._price_history[symbol].append((now, price_data.price))
            
            # Keep only last 10 minutes of history
            cutoff = now - 600
            self._price_history[symbol] = [
                (t, p) for t, p in self._price_history[symbol] if t > cutoff
            ]
        
        return price_data
    
    async def get_price_momentum(self, symbol: str) -> Optional[PriceMomentum]:
        """Calculate price momentum indicators"""
        symbol = symbol.upper()
        
        # Get current price first
        current_data = await self.get_current_price(symbol)
        if not current_data:
            return None
        
        current_price = current_data.price
        now = time.time()
        
        history = self._price_history.get(symbol, [])
        
        # Find price 1 minute ago
        price_1m_ago = current_price
        for t, p in reversed(history):
            if now - t >= 60:
                price_1m_ago = p
                break
        
        # Find price 5 minutes ago
        price_5m_ago = current_price
        for t, p in reversed(history):
            if now - t >= 300:
                price_5m_ago = p
                break
        
        # Calculate momentum (% change)
        momentum_1m = ((current_price - price_1m_ago) / price_1m_ago * 100) if price_1m_ago else 0
        momentum_5m = ((current_price - price_5m_ago) / price_5m_ago * 100) if price_5m_ago else 0
        
        # Calculate volatility (simplified - using range)
        if len(history) >= 2:
            prices = [p for _, p in history[-60:]]  # Last ~5 min of data points
            if prices:
                price_range = max(prices) - min(prices)
                avg_price = sum(prices) / len(prices)
                volatility_5m = (price_range / avg_price * 100) if avg_price else 0
            else:
                volatility_5m = 0
        else:
            volatility_5m = 0
        
        # Determine trend
        if momentum_5m > 0.1:
            trend_direction = "UP"
            trend_strength = min(1.0, abs(momentum_5m) / 1.0)  # 1% = full strength
        elif momentum_5m < -0.1:
            trend_direction = "DOWN"
            trend_strength = min(1.0, abs(momentum_5m) / 1.0)
        else:
            trend_direction = "NEUTRAL"
            trend_strength = 0
        
        return PriceMomentum(
            symbol=symbol,
            current_price=current_price,
            price_1m_ago=price_1m_ago,
            price_5m_ago=price_5m_ago,
            momentum_1m=momentum_1m,
            momentum_5m=momentum_5m,
            volatility_5m=volatility_5m,
            trend_direction=trend_direction,
            trend_strength=trend_strength
        )
    
    async def get_historical_klines(
        self, 
        symbol: str, 
        interval: str = "5m",
        limit: int = 100
    ) -> List[Dict]:
        """Get historical kline/candlestick data from Binance"""
        binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
        if not binance_symbol:
            return []
        
        headers = self._get_binance_headers()
        
        # Try multiple Binance API endpoints
        data = None
        for api_base in self.BINANCE_APIS:
            url = f"{api_base}/klines?symbol={binance_symbol}&interval={interval}&limit={limit}"
            data = self._curl_get(url, timeout=10, headers=headers if headers else None)
            if data and isinstance(data, list):
                break
        
        if not data or not isinstance(data, list):
            return []
        
        klines = []
        for k in data:
            klines.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
            })
        
        return klines
    
    def calculate_technical_indicators(self, klines: List[Dict]) -> Dict:
        """Calculate technical indicators from kline data"""
        if len(klines) < 14:
            return {}
        
        closes = [k["close"] for k in klines]
        
        # Simple Moving Averages
        sma_5 = sum(closes[-5:]) / 5
        sma_14 = sum(closes[-14:]) / 14
        
        # Price position relative to SMAs
        current_price = closes[-1]
        above_sma_5 = current_price > sma_5
        above_sma_14 = current_price > sma_14
        
        # RSI (simplified 14-period)
        gains = []
        losses = []
        for i in range(1, min(15, len(closes))):
            diff = closes[-i] - closes[-i-1]
            if diff > 0:
                gains.append(diff)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(diff))
        
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0.0001
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        # Trend determination
        if above_sma_5 and above_sma_14 and rsi > 50:
            trend = "BULLISH"
        elif not above_sma_5 and not above_sma_14 and rsi < 50:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
        
        return {
            "current_price": current_price,
            "sma_5": sma_5,
            "sma_14": sma_14,
            "above_sma_5": above_sma_5,
            "above_sma_14": above_sma_14,
            "rsi": rsi,
            "trend": trend,
            "overbought": rsi > 70,
            "oversold": rsi < 30
        }


# Convenience function
async def get_btc_momentum() -> Optional[PriceMomentum]:
    """Quick helper to get BTC momentum"""
    client = PriceClient()
    return await client.get_price_momentum("BTC")