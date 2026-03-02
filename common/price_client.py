"""
External Price Data Client - Shared by Prediction and Arbitrage Systems

Fetches real-time and historical crypto prices from multiple public APIs:
- Binance (Global & US)
- OKX (fewer restrictions)
- CoinGecko (free, no API key)
- Kraken
- CryptoCompare
- CoinPaprika

Supports authenticated Binance API for enhanced rate limits.
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
import os
from pathlib import Path

# Load environment variables
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _value = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _value.strip())

# API Configuration
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_BASE = os.environ.get("BINANCE_API_BASE", "https://api.binance.com/api/v3")
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_API_BASE = "https://www.okx.com/api/v5"


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


@dataclass
class OrderBookData:
    """Order book / market depth data"""
    symbol: str
    timestamp: datetime
    
    # Best bid/ask
    best_bid: float  # Highest buy order price
    best_ask: float  # Lowest sell order price
    spread: float    # Ask - Bid (USD)
    spread_pct: float  # Spread as % of mid price
    
    # Aggregated depth (top N levels)
    total_bid_volume: float  # Total volume of buy orders
    total_ask_volume: float  # Total volume of sell orders
    bid_ask_ratio: float     # bid_volume / ask_volume (>1 = bullish)
    
    # Imbalance indicator
    imbalance: float  # (bid - ask) / (bid + ask), range [-1, 1]
    pressure: str     # "BUY", "SELL", "NEUTRAL"
    
    # Large orders detection
    large_bid_walls: List[tuple] = None  # [(price, volume), ...]
    large_ask_walls: List[tuple] = None


@dataclass 
class MarketDepthAnalysis:
    """Combined analysis of order book and recent trades"""
    symbol: str
    timestamp: datetime
    
    # Order book signals
    bid_ask_ratio: float
    imbalance: float
    spread_pct: float
    
    # Recent trades analysis
    buy_volume_pct: float  # % of recent volume that was buys
    avg_trade_size: float
    large_trade_detected: bool
    
    # Combined signal
    signal: str  # "STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"
    confidence: float  # 0-1


class PriceClient:
    """Client for fetching crypto prices from multiple sources"""
    
    # CoinGecko API (free, no API key needed)
    COINGECKO_API = "https://api.coingecko.com/api/v3"
    
    # Binance API endpoints - Global
    BINANCE_APIS_GLOBAL = [
        "https://api.binance.com/api/v3",      # Primary (Global)
        "https://api1.binance.com/api/v3",     # Cluster 1
        "https://api2.binance.com/api/v3",     # Cluster 2
        "https://api3.binance.com/api/v3",     # Cluster 3
        "https://api4.binance.com/api/v3",     # Cluster 4
        "https://data-api.binance.vision/api/v3",  # Historical data
    ]
    
    # Binance API endpoints - US
    BINANCE_APIS_US = [
        "https://api.binance.us/api/v3",       # Binance.US primary
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
        "BTC": {"coingecko": "bitcoin", "binance": "BTCUSDT", "okx": "BTC-USDT"},
        "ETH": {"coingecko": "ethereum", "binance": "ETHUSDT", "okx": "ETH-USDT"},
        "SOL": {"coingecko": "solana", "binance": "SOLUSDT", "okx": "SOL-USDT"},
        "DOGE": {"coingecko": "dogecoin", "binance": "DOGEUSDT", "okx": "DOGE-USDT"},
        "XRP": {"coingecko": "ripple", "binance": "XRPUSDT", "okx": "XRP-USDT"},
        "BNB": {"coingecko": "binancecoin", "binance": "BNBUSDT", "okx": "BNB-USDT"},
        "ADA": {"coingecko": "cardano", "binance": "ADAUSDT", "okx": "ADA-USDT"},
        "AVAX": {"coingecko": "avalanche-2", "binance": "AVAXUSDT", "okx": "AVAX-USDT"},
        "DOT": {"coingecko": "polkadot", "binance": "DOTUSDT", "okx": "DOT-USDT"},
        "MATIC": {"coingecko": "matic-network", "binance": "MATICUSDT", "okx": "MATIC-USDT"},
    }
    
    # OKX API endpoints
    OKX_API = "https://www.okx.com/api/v5"
    
    def __init__(self, auto_detect_region: bool = True):
        self._price_cache: Dict[str, Tuple[PriceData, float]] = {}
        self._price_history: Dict[str, List[Tuple[float, float]]] = {}  # symbol -> [(timestamp, price), ...]
        self._cache_duration = 10  # seconds
        self._is_us_region: bool = False
        self._region_detected: bool = False
        
        # Auto-detect region on initialization
        if auto_detect_region:
            self._detect_region()
        
        # Set BINANCE_APIS based on detected region
        self._update_binance_endpoints()
    
    def _detect_region(self) -> bool:
        """
        Detect if current IP is in US region.
        Uses multiple geolocation services for reliability.
        """
        if self._region_detected:
            return self._is_us_region
        
        geo_services = [
            ("ip-api.com", "http://ip-api.com/json/?fields=status,countryCode"),
            ("ipinfo.io", "https://ipinfo.io/json"),
        ]
        
        for service_name, url in geo_services:
            try:
                result = subprocess.run(
                    ["curl", "-s", "-m", "3", url],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout:
                    data = json.loads(result.stdout)
                    
                    if service_name == "ip-api.com":
                        if data.get("status") == "success":
                            country = data.get("countryCode", "").upper()
                            self._is_us_region = (country == "US")
                            self._region_detected = True
                            return self._is_us_region
                    
                    elif service_name == "ipinfo.io":
                        country = data.get("country", "").upper()
                        self._is_us_region = (country == "US")
                        self._region_detected = True
                        return self._is_us_region
                        
            except Exception:
                continue
        
        # Default to non-US if detection fails
        self._is_us_region = False
        self._region_detected = True
        return False
    
    def _update_binance_endpoints(self):
        """Update Binance API endpoints based on detected region"""
        if self._is_us_region:
            # US region: prioritize Binance.US, then try global as fallback
            self.BINANCE_APIS = self.BINANCE_APIS_US + self.BINANCE_APIS_GLOBAL
            self.BINANCE_API = "https://api.binance.us/api/v3"
        else:
            # Non-US region: use global endpoints, with US as last fallback
            self.BINANCE_APIS = self.BINANCE_APIS_GLOBAL + self.BINANCE_APIS_US
            self.BINANCE_API = "https://api.binance.com/api/v3"
    
    def is_us_region(self) -> bool:
        """Return whether current IP is detected as US region"""
        return self._is_us_region
    
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
    
    async def get_avg_price_binance(self, symbol: str) -> Optional[float]:
        """Get current average price from Binance (lightweight endpoint)."""
        binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
        if not binance_symbol:
            return None
        
        headers = self._get_binance_headers()
        
        for api_base in self.BINANCE_APIS:
            url = f"{api_base}/avgPrice?symbol={binance_symbol}"
            data = self._curl_get(url, timeout=5, headers=headers if headers else None)
            
            if data and "price" in data:
                return float(data["price"])
        
        return None
    
    async def get_book_ticker_binance(self, symbol: str) -> Optional[Dict]:
        """Get best bid/ask price and quantity from Binance."""
        binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
        if not binance_symbol:
            return None
        
        headers = self._get_binance_headers()
        
        for api_base in self.BINANCE_APIS:
            url = f"{api_base}/ticker/bookTicker?symbol={binance_symbol}"
            data = self._curl_get(url, timeout=5, headers=headers if headers else None)
            
            if data and "bidPrice" in data:
                return {
                    "symbol": symbol.upper(),
                    "bid_price": float(data["bidPrice"]),
                    "bid_qty": float(data["bidQty"]),
                    "ask_price": float(data["askPrice"]),
                    "ask_qty": float(data["askQty"]),
                    "spread": float(data["askPrice"]) - float(data["bidPrice"]),
                    "spread_pct": (float(data["askPrice"]) - float(data["bidPrice"])) / float(data["bidPrice"]) * 100
                }
        
        return None
    
    async def get_agg_trades_binance(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get compressed/aggregate trades from Binance."""
        binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
        if not binance_symbol:
            return []
        
        headers = self._get_binance_headers()
        
        for api_base in self.BINANCE_APIS:
            url = f"{api_base}/aggTrades?symbol={binance_symbol}&limit={limit}"
            data = self._curl_get(url, timeout=10, headers=headers if headers else None)
            
            if data and isinstance(data, list) and len(data) > 0:
                trades = []
                for t in data:
                    price = float(t["p"])
                    qty = float(t["q"])
                    trades.append({
                        "agg_trade_id": t["a"],
                        "price": price,
                        "qty": qty,
                        "value": price * qty,
                        "first_trade_id": t["f"],
                        "last_trade_id": t["l"],
                        "timestamp": t["T"],
                        "is_buyer_maker": t["m"],
                        "side": "SELL" if t["m"] else "BUY"
                    })
                return trades
        
        return []
    
    async def get_ticker_price_binance(self, symbol: str = None) -> Optional[Dict]:
        """Get symbol price ticker from Binance (very lightweight)."""
        headers = self._get_binance_headers()
        
        for api_base in self.BINANCE_APIS:
            if symbol:
                binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
                if not binance_symbol:
                    return None
                url = f"{api_base}/ticker/price?symbol={binance_symbol}"
            else:
                url = f"{api_base}/ticker/price"
            
            data = self._curl_get(url, timeout=10, headers=headers if headers else None)
            
            if data:
                if isinstance(data, list):
                    return {item["symbol"]: float(item["price"]) for item in data}
                elif "price" in data:
                    return {
                        "symbol": symbol.upper() if symbol else data.get("symbol"),
                        "price": float(data["price"])
                    }
        
        return None
    
    async def get_exchange_info_binance(self, symbol: str = None) -> Optional[Dict]:
        """Get exchange trading rules and symbol information from Binance."""
        headers = self._get_binance_headers()
        
        for api_base in self.BINANCE_APIS:
            if symbol:
                binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
                if not binance_symbol:
                    return None
                url = f"{api_base}/exchangeInfo?symbol={binance_symbol}"
            else:
                url = f"{api_base}/exchangeInfo"
            
            data = self._curl_get(url, timeout=15, headers=headers if headers else None)
            
            if data and "symbols" in data:
                if symbol:
                    for s in data["symbols"]:
                        if s["symbol"] == self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance"):
                            return {
                                "symbol": symbol.upper(),
                                "status": s["status"],
                                "base_asset": s["baseAsset"],
                                "quote_asset": s["quoteAsset"],
                                "tick_size": next((f["tickSize"] for f in s["filters"] if f["filterType"] == "PRICE_FILTER"), None),
                                "min_qty": next((f["minQty"] for f in s["filters"] if f["filterType"] == "LOT_SIZE"), None),
                                "max_qty": next((f["maxQty"] for f in s["filters"] if f["filterType"] == "LOT_SIZE"), None),
                            }
                else:
                    return data
        
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
                price=float(ticker["c"][0]),
                timestamp=datetime.now(timezone.utc),
                volume_24h=float(ticker["v"][1]) * float(ticker["c"][0])
            )
        return None
    
    async def get_price_coinpaprika(self, symbol: str) -> Optional[PriceData]:
        """Get current price from CoinPaprika (alternative source)"""
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
    
    def _get_okx_headers(self) -> Dict:
        """Get OKX API headers with authentication if API key is configured"""
        headers = {}
        if OKX_API_KEY:
            headers["OK-ACCESS-KEY"] = OKX_API_KEY
        return headers
    
    async def get_price_okx(self, symbol: str) -> Optional[PriceData]:
        """Get current price from OKX (global exchange, fewer restrictions)"""
        okx_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("okx")
        if not okx_symbol:
            return None
        
        headers = self._get_okx_headers()
        url = f"{self.OKX_API}/market/ticker?instId={okx_symbol}"
        data = self._curl_get(url, timeout=10, headers=headers if headers else None)
        
        if data and data.get("code") == "0" and data.get("data"):
            ticker = data["data"][0]
            last_price = float(ticker.get("last", 0))
            open_24h = float(ticker.get("open24h", last_price))
            vol_24h = float(ticker.get("vol24h", 0)) * last_price
            
            price_change_24h = ((last_price - open_24h) / open_24h * 100) if open_24h else 0
            
            return PriceData(
                symbol=symbol.upper(),
                price=last_price,
                timestamp=datetime.now(timezone.utc),
                volume_24h=vol_24h,
                price_change_24h=price_change_24h
            )
        return None
    
    async def get_order_book_okx(self, symbol: str, limit: int = 20) -> Optional[OrderBookData]:
        """Get order book from OKX"""
        okx_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("okx")
        if not okx_symbol:
            return None
        
        headers = self._get_okx_headers()
        url = f"{self.OKX_API}/market/books?instId={okx_symbol}&sz={limit}"
        data = self._curl_get(url, timeout=10, headers=headers if headers else None)
        
        if not data or data.get("code") != "0" or not data.get("data"):
            return None
        
        book = data["data"][0]
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        
        if not bids or not asks:
            return None
        
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_pct = (spread / mid_price) * 100
        
        total_bid_volume = sum(float(b[0]) * float(b[1]) for b in bids)
        total_ask_volume = sum(float(a[0]) * float(a[1]) for a in asks)
        
        bid_ask_ratio = total_bid_volume / total_ask_volume if total_ask_volume > 0 else 1
        total_volume = total_bid_volume + total_ask_volume
        imbalance = (total_bid_volume - total_ask_volume) / total_volume if total_volume > 0 else 0
        
        if imbalance > 0.2:
            pressure = "BUY"
        elif imbalance < -0.2:
            pressure = "SELL"
        else:
            pressure = "NEUTRAL"
        
        return OrderBookData(
            symbol=symbol.upper(),
            timestamp=datetime.now(timezone.utc),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            spread_pct=spread_pct,
            total_bid_volume=total_bid_volume,
            total_ask_volume=total_ask_volume,
            bid_ask_ratio=bid_ask_ratio,
            imbalance=imbalance,
            pressure=pressure,
            large_bid_walls=None,
            large_ask_walls=None
        )
    
    async def get_recent_trades_okx(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get recent trades from OKX"""
        okx_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("okx")
        if not okx_symbol:
            return []
        
        headers = self._get_okx_headers()
        url = f"{self.OKX_API}/market/trades?instId={okx_symbol}&limit={limit}"
        data = self._curl_get(url, timeout=10, headers=headers if headers else None)
        
        if not data or data.get("code") != "0" or not data.get("data"):
            return []
        
        trades = []
        for t in data["data"]:
            price = float(t.get("px", 0))
            qty = float(t.get("sz", 0))
            side = t.get("side", "").upper()
            
            trades.append({
                "price": price,
                "qty": qty,
                "value": price * qty,
                "time": int(t.get("ts", 0)),
                "side": "BUY" if side == "BUY" else "SELL"
            })
        
        return trades
    
    async def get_klines_okx(self, symbol: str, interval: str = "5m", limit: int = 100) -> List[Dict]:
        """Get historical kline/candlestick data from OKX"""
        okx_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("okx")
        if not okx_symbol:
            return []
        
        interval_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1H", "4h": "4H", "1d": "1D"
        }
        okx_interval = interval_map.get(interval.lower(), "5m")
        
        headers = self._get_okx_headers()
        url = f"{self.OKX_API}/market/candles?instId={okx_symbol}&bar={okx_interval}&limit={limit}"
        data = self._curl_get(url, timeout=10, headers=headers if headers else None)
        
        if not data or data.get("code") != "0" or not data.get("data"):
            return []
        
        klines = []
        for k in data["data"]:
            klines.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[0]),
            })
        
        return list(reversed(klines))
    
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
        
        # 1. Try OKX first (global, fewer restrictions than Binance)
        price_data = await self.get_price_okx(symbol)
        
        # 2. Try Binance (fastest when available)
        if not price_data:
            price_data = await self.get_price_binance(symbol)
        
        # 3. Fallback to CoinGecko
        if not price_data:
            price_data = await self.get_price_coingecko(symbol)
        
        # 4. Fallback to Kraken
        if not price_data:
            price_data = await self.get_price_kraken(symbol)
        
        # 5. Fallback to CryptoCompare
        if not price_data:
            price_data = await self.get_price_cryptocompare(symbol)
        
        # 6. Fallback to CoinPaprika
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
            prices = [p for _, p in history[-60:]]
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
            trend_strength = min(1.0, abs(momentum_5m) / 1.0)
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

    async def get_order_book(self, symbol: str, limit: int = 20) -> Optional[OrderBookData]:
        """Get order book (market depth) data."""
        binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
        if not binance_symbol:
            return None
        
        headers = self._get_binance_headers()
        
        for api_base in self.BINANCE_APIS:
            url = f"{api_base}/depth?symbol={binance_symbol}&limit={limit}"
            data = self._curl_get(url, timeout=5, headers=headers if headers else None)
            
            if data and "bids" in data and "asks" in data:
                bids = data["bids"]
                asks = data["asks"]
                
                if not bids or not asks:
                    continue
                
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                mid_price = (best_bid + best_ask) / 2
                spread = best_ask - best_bid
                spread_pct = (spread / mid_price) * 100
                
                total_bid_volume = sum(float(b[0]) * float(b[1]) for b in bids)
                total_ask_volume = sum(float(a[0]) * float(a[1]) for a in asks)
                
                bid_ask_ratio = total_bid_volume / total_ask_volume if total_ask_volume > 0 else 1
                
                total_volume = total_bid_volume + total_ask_volume
                imbalance = (total_bid_volume - total_ask_volume) / total_volume if total_volume > 0 else 0
                
                if imbalance > 0.2:
                    pressure = "BUY"
                elif imbalance < -0.2:
                    pressure = "SELL"
                else:
                    pressure = "NEUTRAL"
                
                avg_bid_size = total_bid_volume / len(bids) if bids else 0
                avg_ask_size = total_ask_volume / len(asks) if asks else 0
                
                large_bid_walls = [
                    (float(b[0]), float(b[0]) * float(b[1]))
                    for b in bids
                    if float(b[0]) * float(b[1]) > avg_bid_size * 2
                ]
                
                large_ask_walls = [
                    (float(a[0]), float(a[0]) * float(a[1]))
                    for a in asks
                    if float(a[0]) * float(a[1]) > avg_ask_size * 2
                ]
                
                return OrderBookData(
                    symbol=symbol.upper(),
                    timestamp=datetime.now(timezone.utc),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                    spread_pct=spread_pct,
                    total_bid_volume=total_bid_volume,
                    total_ask_volume=total_ask_volume,
                    bid_ask_ratio=bid_ask_ratio,
                    imbalance=imbalance,
                    pressure=pressure,
                    large_bid_walls=large_bid_walls[:3],
                    large_ask_walls=large_ask_walls[:3]
                )
        
        # Fallback to Kraken order book
        return await self._get_order_book_kraken(symbol, limit)
    
    async def _get_order_book_kraken(self, symbol: str, limit: int = 20) -> Optional[OrderBookData]:
        """Get order book from Kraken as fallback"""
        kraken_map = {
            "BTC": "XXBTZUSD",
            "ETH": "XETHZUSD",
            "SOL": "SOLUSD",
        }
        kraken_symbol = kraken_map.get(symbol.upper())
        if not kraken_symbol:
            return None
        
        url = f"https://api.kraken.com/0/public/Depth?pair={kraken_symbol}&count={limit}"
        data = self._curl_get(url, timeout=10)
        
        if not data or "result" not in data:
            return None
        
        result = data["result"]
        book = result.get(kraken_symbol, {})
        
        if not book or "bids" not in book or "asks" not in book:
            return None
        
        bids = book["bids"]
        asks = book["asks"]
        
        if not bids or not asks:
            return None
        
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_pct = (spread / mid_price) * 100
        
        total_bid_volume = sum(float(b[0]) * float(b[1]) for b in bids)
        total_ask_volume = sum(float(a[0]) * float(a[1]) for a in asks)
        
        bid_ask_ratio = total_bid_volume / total_ask_volume if total_ask_volume > 0 else 1
        total_volume = total_bid_volume + total_ask_volume
        imbalance = (total_bid_volume - total_ask_volume) / total_volume if total_volume > 0 else 0
        
        if imbalance > 0.2:
            pressure = "BUY"
        elif imbalance < -0.2:
            pressure = "SELL"
        else:
            pressure = "NEUTRAL"
        
        return OrderBookData(
            symbol=symbol.upper(),
            timestamp=datetime.now(timezone.utc),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            spread_pct=spread_pct,
            total_bid_volume=total_bid_volume,
            total_ask_volume=total_ask_volume,
            bid_ask_ratio=bid_ask_ratio,
            imbalance=imbalance,
            pressure=pressure,
            large_bid_walls=None,
            large_ask_walls=None
        )
    
    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get recent trades to analyze buy/sell pressure."""
        binance_symbol = self.SYMBOL_MAP.get(symbol.upper(), {}).get("binance")
        if not binance_symbol:
            return []
        
        headers = self._get_binance_headers()
        
        for api_base in self.BINANCE_APIS:
            url = f"{api_base}/trades?symbol={binance_symbol}&limit={limit}"
            data = self._curl_get(url, timeout=5, headers=headers if headers else None)
            
            if data and isinstance(data, list) and len(data) > 0:
                trades = []
                for t in data:
                    trades.append({
                        "price": float(t["price"]),
                        "qty": float(t["qty"]),
                        "value": float(t["price"]) * float(t["qty"]),
                        "time": t["time"],
                        "is_buyer_maker": t["isBuyerMaker"],
                        "side": "SELL" if t["isBuyerMaker"] else "BUY"
                    })
                return trades
        
        return []
    
    async def get_market_depth_analysis(self, symbol: str) -> Optional[MarketDepthAnalysis]:
        """Comprehensive market depth analysis combining order book and recent trades."""
        symbol = symbol.upper()
        
        order_book = await self.get_order_book(symbol, limit=50)
        trades = await self.get_recent_trades(symbol, limit=100)
        
        if not order_book and not trades:
            return None
        
        bid_ask_ratio = 1.0
        imbalance = 0.0
        spread_pct = 0.0
        buy_volume_pct = 0.5
        avg_trade_size = 0.0
        large_trade_detected = False
        
        if order_book:
            bid_ask_ratio = order_book.bid_ask_ratio
            imbalance = order_book.imbalance
            spread_pct = order_book.spread_pct
        
        if trades:
            buy_volume = sum(t["value"] for t in trades if t["side"] == "BUY")
            sell_volume = sum(t["value"] for t in trades if t["side"] == "SELL")
            total_volume = buy_volume + sell_volume
            
            buy_volume_pct = buy_volume / total_volume if total_volume > 0 else 0.5
            avg_trade_size = total_volume / len(trades) if trades else 0
            
            large_trades = [t for t in trades if t["value"] > avg_trade_size * 3]
            large_trade_detected = len(large_trades) > 0
        
        signal_score = 0
        
        if order_book:
            if bid_ask_ratio > 1.5:
                signal_score += 2
            elif bid_ask_ratio > 1.1:
                signal_score += 1
            elif bid_ask_ratio < 0.67:
                signal_score -= 2
            elif bid_ask_ratio < 0.9:
                signal_score -= 1
        
        if trades:
            if buy_volume_pct > 0.65:
                signal_score += 2
            elif buy_volume_pct > 0.55:
                signal_score += 1
            elif buy_volume_pct < 0.35:
                signal_score -= 2
            elif buy_volume_pct < 0.45:
                signal_score -= 1
        
        if large_trade_detected and trades:
            large_buy = sum(1 for t in trades if t["value"] > avg_trade_size * 3 and t["side"] == "BUY")
            large_sell = sum(1 for t in trades if t["value"] > avg_trade_size * 3 and t["side"] == "SELL")
            if large_buy > large_sell:
                signal_score += 1
            elif large_sell > large_buy:
                signal_score -= 1
        
        if signal_score >= 3:
            signal = "STRONG_BUY"
            confidence = 0.8
        elif signal_score >= 1:
            signal = "BUY"
            confidence = 0.6
        elif signal_score <= -3:
            signal = "STRONG_SELL"
            confidence = 0.8
        elif signal_score <= -1:
            signal = "SELL"
            confidence = 0.6
        else:
            signal = "NEUTRAL"
            confidence = 0.4
        
        return MarketDepthAnalysis(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bid_ask_ratio=bid_ask_ratio,
            imbalance=imbalance,
            spread_pct=spread_pct,
            buy_volume_pct=buy_volume_pct,
            avg_trade_size=avg_trade_size,
            large_trade_detected=large_trade_detected,
            signal=signal,
            confidence=confidence
        )


# Convenience functions
async def get_btc_momentum() -> Optional[PriceMomentum]:
    """Quick helper to get BTC momentum"""
    client = PriceClient()
    return await client.get_price_momentum("BTC")


async def get_btc_order_book() -> Optional[OrderBookData]:
    """Quick helper to get BTC order book"""
    client = PriceClient()
    return await client.get_order_book("BTC")


async def get_btc_market_depth() -> Optional[MarketDepthAnalysis]:
    """Quick helper to get BTC market depth analysis"""
    client = PriceClient()
    return await client.get_market_depth_analysis("BTC")