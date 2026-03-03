"""
Shared Binance API client.

Supports:
- Spot Trading
- USDT-M Perpetual Futures
- Coin-M Delivery Futures
- WebSocket real-time data
"""

import asyncio
import hashlib
import hmac
import json
import time
import logging
import os
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode
from pathlib import Path

import aiohttp
import websockets

logger = logging.getLogger(__name__)

# Load .env from repository root if present.
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())


@dataclass
class BinanceAPIConfig:
    """Minimal configuration required by shared Binance client."""

    binance_api_key: str = field(default_factory=lambda: os.environ.get("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.environ.get("BINANCE_API_SECRET", ""))

    binance_spot_base: str = "https://api.binance.com"
    binance_futures_base: str = "https://fapi.binance.com"
    binance_delivery_base: str = "https://dapi.binance.com"

    binance_spot_ws: str = "wss://stream.binance.com:9443/ws"
    binance_futures_ws: str = "wss://fstream.binance.com/ws"

    ws_reconnect_delay: int = 5
    max_reconnect_attempts: int = 10


DEFAULT_BINANCE_CONFIG = BinanceAPIConfig()


@dataclass
class TickerData:
    """Real-time ticker data"""
    symbol: str
    price: float
    bid_price: float
    ask_price: float
    volume_24h: float
    timestamp: datetime


@dataclass 
class FundingRateData:
    """Funding rate data for perpetual contracts"""
    symbol: str
    funding_rate: float           # Current funding rate
    funding_time: datetime        # Next funding time
    mark_price: float
    index_price: float
    estimated_settle_price: float


@dataclass
class FuturesContractData:
    """Delivery futures contract data"""
    symbol: str
    pair: str                     # e.g., "BTCUSD"
    contract_type: str            # "CURRENT_QUARTER", "NEXT_QUARTER"
    delivery_date: datetime
    mark_price: float
    index_price: float
    basis: float                  # futures - spot
    basis_rate: float             # basis / spot


@dataclass
class AccountBalance:
    """Account balance data"""
    asset: str
    free: float
    locked: float
    total: float


@dataclass
class PositionData:
    """Position data"""
    symbol: str
    side: str                     # "LONG" or "SHORT"
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    margin_type: str              # "CROSS" or "ISOLATED"
    liquidation_price: float


@dataclass
class OrderResult:
    """Order execution result"""
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float
    status: str
    filled_qty: float
    avg_price: float
    timestamp: datetime


class BinanceAPIError(Exception):
    """Binance API Error"""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Binance API Error [{code}]: {message}")


class BinanceClient:
    """
    Unified Binance API Client
    
    Supports Spot, USDT-M Futures, and Coin-M Delivery Futures
    """
    
    def __init__(self, config: BinanceAPIConfig = None):
        self.config = config or DEFAULT_BINANCE_CONFIG
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._ws_callbacks: Dict[str, List[Callable]] = {}
        self._running = False
        
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        """Close all connections"""
        self._running = False
        
        # Close WebSocket connections
        for ws in self._ws_connections.values():
            await ws.close()
        self._ws_connections.clear()
        
        # Close HTTP session
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _generate_signature(self, params: Dict) -> str:
        """Generate HMAC SHA256 signature"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.config.binance_api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _get_headers(self) -> Dict[str, str]:
        """Get API headers"""
        return {
            "X-MBX-APIKEY": self.config.binance_api_key,
            "Content-Type": "application/json"
        }
    
    async def _request(
        self, 
        method: str, 
        base_url: str, 
        endpoint: str, 
        params: Dict = None,
        signed: bool = False
    ) -> Dict:
        """Make HTTP request to Binance API"""
        session = await self._get_session()
        params = params or {}
        
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._generate_signature(params)
        
        url = f"{base_url}{endpoint}"
        
        try:
            if method == "GET":
                async with session.get(url, params=params, headers=self._get_headers()) as resp:
                    data = await resp.json()
            elif method == "POST":
                async with session.post(url, params=params, headers=self._get_headers()) as resp:
                    data = await resp.json()
            elif method == "DELETE":
                async with session.delete(url, params=params, headers=self._get_headers()) as resp:
                    data = await resp.json()
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            # Check for API errors
            if isinstance(data, dict) and "code" in data and data["code"] != 200:
                raise BinanceAPIError(data["code"], data.get("msg", "Unknown error"))
            
            return data
            
        except aiohttp.ClientError as e:
            logger.error(f"HTTP request failed: {e}")
            raise
    
    # =========================================
    # Spot API
    # =========================================
    
    async def get_spot_ticker(self, symbol: str) -> Optional[TickerData]:
        """Get spot ticker price"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_spot_base,
                "/api/v3/ticker/24hr",
                {"symbol": symbol}
            )
            return TickerData(
                symbol=symbol,
                price=float(data["lastPrice"]),
                bid_price=float(data["bidPrice"]),
                ask_price=float(data["askPrice"]),
                volume_24h=float(data["quoteVolume"]),
                timestamp=datetime.now(timezone.utc)
            )
        except Exception as e:
            logger.error(f"Failed to get spot ticker for {symbol}: {e}")
            return None
    
    async def get_spot_price(self, symbol: str) -> Optional[float]:
        """Get current spot price"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_spot_base,
                "/api/v3/ticker/price",
                {"symbol": symbol}
            )
            return float(data["price"])
        except Exception as e:
            logger.error(f"Failed to get spot price for {symbol}: {e}")
            return None
    
    async def get_spot_orderbook(self, symbol: str, limit: int = 20) -> Optional[Dict]:
        """Get spot order book"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_spot_base,
                "/api/v3/depth",
                {"symbol": symbol, "limit": limit}
            )
            return {
                "bids": [(float(p), float(q)) for p, q in data["bids"]],
                "asks": [(float(p), float(q)) for p, q in data["asks"]]
            }
        except Exception as e:
            logger.error(f"Failed to get spot orderbook for {symbol}: {e}")
            return None

    async def get_spot_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500
    ) -> List[Dict]:
        """Get spot candlestick data."""
        try:
            data = await self._request(
                "GET",
                self.config.binance_spot_base,
                "/api/v3/klines",
                {"symbol": symbol, "interval": interval, "limit": limit}
            )
            return [
                {
                    "open_time": datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc),
                    "close_time": datetime.fromtimestamp(item[6] / 1000, tz=timezone.utc),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
                for item in data
            ]
        except Exception as e:
            logger.error(f"Failed to get spot klines for {symbol}: {e}")
            return []
    
    async def get_spot_balance(self) -> List[AccountBalance]:
        """Get spot account balances"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_spot_base,
                "/api/v3/account",
                signed=True
            )
            balances = []
            for b in data.get("balances", []):
                free = float(b["free"])
                locked = float(b["locked"])
                if free > 0 or locked > 0:
                    balances.append(AccountBalance(
                        asset=b["asset"],
                        free=free,
                        locked=locked,
                        total=free + locked
                    ))
            return balances
        except Exception as e:
            logger.error(f"Failed to get spot balance: {e}")
            return []
    
    async def spot_market_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float
    ) -> Optional[OrderResult]:
        """Place spot market order"""
        try:
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "MARKET",
                "quantity": quantity
            }
            data = await self._request(
                "POST",
                self.config.binance_spot_base,
                "/api/v3/order",
                params,
                signed=True
            )
            return OrderResult(
                order_id=str(data["orderId"]),
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=quantity,
                price=0,
                status=data["status"],
                filled_qty=float(data.get("executedQty", 0)),
                avg_price=float(data.get("fills", [{}])[0].get("price", 0)) if data.get("fills") else 0,
                timestamp=datetime.now(timezone.utc)
            )
        except Exception as e:
            logger.error(f"Spot market order failed: {e}")
            return None
    
    async def spot_limit_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float,
        price: float
    ) -> Optional[OrderResult]:
        """Place spot limit order"""
        try:
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": quantity,
                "price": price
            }
            data = await self._request(
                "POST",
                self.config.binance_spot_base,
                "/api/v3/order",
                params,
                signed=True
            )
            return OrderResult(
                order_id=str(data["orderId"]),
                symbol=symbol,
                side=side,
                order_type="LIMIT",
                quantity=quantity,
                price=price,
                status=data["status"],
                filled_qty=float(data.get("executedQty", 0)),
                avg_price=price,
                timestamp=datetime.now(timezone.utc)
            )
        except Exception as e:
            logger.error(f"Spot limit order failed: {e}")
            return None
    
    # =========================================
    # USDT-M Perpetual Futures API
    # =========================================
    
    async def get_perpetual_ticker(self, symbol: str) -> Optional[TickerData]:
        """Get perpetual futures ticker"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v1/ticker/24hr",
                {"symbol": symbol}
            )
            return TickerData(
                symbol=symbol,
                price=float(data["lastPrice"]),
                bid_price=float(data.get("bidPrice", data["lastPrice"])),
                ask_price=float(data.get("askPrice", data["lastPrice"])),
                volume_24h=float(data["quoteVolume"]),
                timestamp=datetime.now(timezone.utc)
            )
        except Exception as e:
            logger.error(f"Failed to get perpetual ticker for {symbol}: {e}")
            return None
    
    async def get_funding_rate(self, symbol: str) -> Optional[FundingRateData]:
        """Get current funding rate for perpetual contract"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v1/premiumIndex",
                {"symbol": symbol}
            )
            return FundingRateData(
                symbol=symbol,
                funding_rate=float(data["lastFundingRate"]) * 100,  # Convert to percentage
                funding_time=datetime.fromtimestamp(
                    data["nextFundingTime"] / 1000, 
                    tz=timezone.utc
                ),
                mark_price=float(data["markPrice"]),
                index_price=float(data["indexPrice"]),
                estimated_settle_price=float(data.get("estimatedSettlePrice", 0))
            )
        except Exception as e:
            logger.error(f"Failed to get funding rate for {symbol}: {e}")
            return None
    
    async def get_all_funding_rates(self) -> List[FundingRateData]:
        """Get funding rates for all perpetual contracts"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v1/premiumIndex"
            )
            rates = []
            for item in data:
                rates.append(FundingRateData(
                    symbol=item["symbol"],
                    funding_rate=float(item["lastFundingRate"]) * 100,
                    funding_time=datetime.fromtimestamp(
                        item["nextFundingTime"] / 1000,
                        tz=timezone.utc
                    ),
                    mark_price=float(item["markPrice"]),
                    index_price=float(item["indexPrice"]),
                    estimated_settle_price=float(item.get("estimatedSettlePrice", 0))
                ))
            return rates
        except Exception as e:
            logger.error(f"Failed to get all funding rates: {e}")
            return []
    
    async def get_funding_rate_history(
        self, 
        symbol: str, 
        limit: int = 100
    ) -> List[Dict]:
        """Get historical funding rates"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v1/fundingRate",
                {"symbol": symbol, "limit": limit}
            )
            return [
                {
                    "symbol": item["symbol"],
                    "funding_rate": float(item["fundingRate"]) * 100,
                    "funding_time": datetime.fromtimestamp(
                        item["fundingTime"] / 1000,
                        tz=timezone.utc
                    )
                }
                for item in data
            ]
        except Exception as e:
            logger.error(f"Failed to get funding rate history: {e}")
            return []
    
    async def get_perpetual_balance(self) -> List[AccountBalance]:
        """Get USDT-M futures account balance"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v2/balance",
                signed=True
            )
            balances = []
            for b in data:
                balance = float(b["balance"])
                if balance > 0:
                    balances.append(AccountBalance(
                        asset=b["asset"],
                        free=float(b["availableBalance"]),
                        locked=balance - float(b["availableBalance"]),
                        total=balance
                    ))
            return balances
        except Exception as e:
            logger.error(f"Failed to get perpetual balance: {e}")
            return []
    
    async def get_perpetual_positions(self) -> List[PositionData]:
        """Get all perpetual futures positions"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v2/positionRisk",
                signed=True
            )
            positions = []
            for p in data:
                size = float(p["positionAmt"])
                if abs(size) > 0:
                    positions.append(PositionData(
                        symbol=p["symbol"],
                        side="LONG" if size > 0 else "SHORT",
                        size=abs(size),
                        entry_price=float(p["entryPrice"]),
                        mark_price=float(p["markPrice"]),
                        unrealized_pnl=float(p["unRealizedProfit"]),
                        leverage=int(p["leverage"]),
                        margin_type=p["marginType"],
                        liquidation_price=float(p["liquidationPrice"])
                    ))
            return positions
        except Exception as e:
            logger.error(f"Failed to get perpetual positions: {e}")
            return []
    
    async def perpetual_market_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float,
        reduce_only: bool = False
    ) -> Optional[OrderResult]:
        """Place perpetual futures market order"""
        try:
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "MARKET",
                "quantity": quantity
            }
            if reduce_only:
                params["reduceOnly"] = "true"
            
            data = await self._request(
                "POST",
                self.config.binance_futures_base,
                "/fapi/v1/order",
                params,
                signed=True
            )
            return OrderResult(
                order_id=str(data["orderId"]),
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=quantity,
                price=0,
                status=data["status"],
                filled_qty=float(data.get("executedQty", 0)),
                avg_price=float(data.get("avgPrice", 0)),
                timestamp=datetime.now(timezone.utc)
            )
        except Exception as e:
            logger.error(f"Perpetual market order failed: {e}")
            return None
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol"""
        try:
            await self._request(
                "POST",
                self.config.binance_futures_base,
                "/fapi/v1/leverage",
                {"symbol": symbol, "leverage": leverage},
                signed=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set leverage for {symbol}: {e}")
            return False
    
    async def set_margin_type(self, symbol: str, margin_type: str) -> bool:
        """Set margin type (ISOLATED or CROSSED)"""
        try:
            await self._request(
                "POST",
                self.config.binance_futures_base,
                "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": margin_type.upper()},
                signed=True
            )
            return True
        except Exception as e:
            # Error code -4046 means margin type is already set
            if isinstance(e, BinanceAPIError) and e.code == -4046:
                return True
            logger.error(f"Failed to set margin type for {symbol}: {e}")
            return False
    
    # =========================================
    # Coin-M Delivery Futures API
    # =========================================
    
    async def get_delivery_contracts(self) -> List[FuturesContractData]:
        """Get all delivery futures contracts"""
        try:
            # Get contract info
            exchange_info = await self._request(
                "GET",
                self.config.binance_delivery_base,
                "/dapi/v1/exchangeInfo"
            )
            
            # Get current prices
            prices = await self._request(
                "GET",
                self.config.binance_delivery_base,
                "/dapi/v1/premiumIndex"
            )
            price_map = {p["symbol"]: p for p in prices}
            
            contracts = []
            for s in exchange_info.get("symbols", []):
                if s["contractType"] in ["CURRENT_QUARTER", "NEXT_QUARTER"]:
                    symbol = s["symbol"]
                    price_data = price_map.get(symbol, {})
                    
                    mark_price = float(price_data.get("markPrice", 0))
                    index_price = float(price_data.get("indexPrice", 0))
                    
                    contracts.append(FuturesContractData(
                        symbol=symbol,
                        pair=s["pair"],
                        contract_type=s["contractType"],
                        delivery_date=datetime.fromtimestamp(
                            s["deliveryDate"] / 1000,
                            tz=timezone.utc
                        ),
                        mark_price=mark_price,
                        index_price=index_price,
                        basis=mark_price - index_price,
                        basis_rate=(mark_price - index_price) / index_price * 100 if index_price else 0
                    ))
            
            return contracts
        except Exception as e:
            logger.error(f"Failed to get delivery contracts: {e}")
            return []
    
    async def get_quarterly_futures_price(self, symbol: str) -> Optional[float]:
        """Get quarterly futures contract price"""
        try:
            data = await self._request(
                "GET",
                self.config.binance_delivery_base,
                "/dapi/v1/ticker/price",
                {"symbol": symbol}
            )
            if isinstance(data, list):
                for item in data:
                    if item["symbol"] == symbol:
                        return float(item["price"])
            return float(data["price"])
        except Exception as e:
            logger.error(f"Failed to get quarterly futures price for {symbol}: {e}")
            return None

    async def get_delivery_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500
    ) -> List[Dict]:
        """Get delivery futures candlestick data."""
        try:
            data = await self._request(
                "GET",
                self.config.binance_delivery_base,
                "/dapi/v1/klines",
                {"symbol": symbol, "interval": interval, "limit": limit}
            )
            return [
                {
                    "open_time": datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc),
                    "close_time": datetime.fromtimestamp(item[6] / 1000, tz=timezone.utc),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
                for item in data
            ]
        except Exception as e:
            logger.error(f"Failed to get delivery klines for {symbol}: {e}")
            return []
    
    # =========================================
    # Stablecoin API
    # =========================================
    
    async def get_stablecoin_prices(self) -> Dict[str, float]:
        """Get prices of all stablecoins against USDT"""
        stablecoin_pairs = {
            "USDC": "USDCUSDT",
            "BUSD": "BUSDUSDT",
            "DAI": "DAIUSDT",
            "TUSD": "TUSDUSDT",
            "USDP": "USDPUSDT"
        }
        
        prices = {"USDT": 1.0}  # USDT is the base
        
        for coin, pair in stablecoin_pairs.items():
            try:
                price = await self.get_spot_price(pair)
                if price:
                    prices[coin] = price
            except Exception:
                pass
        
        return prices
    
    async def get_stablecoin_spreads(self) -> List[Dict]:
        """Calculate spreads between stablecoins"""
        prices = await self.get_stablecoin_prices()
        
        if len(prices) < 2:
            return []
        
        spreads = []
        coins = list(prices.keys())
        
        for i in range(len(coins)):
            for j in range(i + 1, len(coins)):
                coin_a = coins[i]
                coin_b = coins[j]
                price_a = prices[coin_a]
                price_b = prices[coin_b]
                
                spread = abs(price_a - price_b) / min(price_a, price_b) * 100
                
                spreads.append({
                    "coin_high": coin_a if price_a > price_b else coin_b,
                    "coin_low": coin_b if price_a > price_b else coin_a,
                    "price_high": max(price_a, price_b),
                    "price_low": min(price_a, price_b),
                    "spread_pct": spread
                })
        
        # Sort by spread descending
        spreads.sort(key=lambda x: x["spread_pct"], reverse=True)
        return spreads
    
    # =========================================
    # WebSocket API
    # =========================================
    
    async def subscribe_ticker(
        self, 
        symbol: str, 
        callback: Callable[[TickerData], None],
        market_type: str = "spot"
    ):
        """Subscribe to real-time ticker updates"""
        if market_type == "spot":
            ws_url = f"{self.config.binance_spot_ws}/{symbol.lower()}@ticker"
        else:
            ws_url = f"{self.config.binance_futures_ws}/{symbol.lower()}@ticker"
        
        stream_key = f"ticker_{market_type}_{symbol}"
        
        if stream_key not in self._ws_callbacks:
            self._ws_callbacks[stream_key] = []
        self._ws_callbacks[stream_key].append(callback)
        
        if stream_key not in self._ws_connections:
            asyncio.create_task(self._ws_listen(ws_url, stream_key, self._parse_ticker))
    
    async def subscribe_funding_rate(
        self, 
        symbol: str, 
        callback: Callable[[FundingRateData], None]
    ):
        """Subscribe to funding rate updates"""
        ws_url = f"{self.config.binance_futures_ws}/{symbol.lower()}@markPrice"
        stream_key = f"funding_{symbol}"
        
        if stream_key not in self._ws_callbacks:
            self._ws_callbacks[stream_key] = []
        self._ws_callbacks[stream_key].append(callback)
        
        if stream_key not in self._ws_connections:
            asyncio.create_task(self._ws_listen(ws_url, stream_key, self._parse_funding))
    
    async def _ws_listen(
        self, 
        url: str, 
        stream_key: str,
        parser: Callable
    ):
        """WebSocket listener with auto-reconnect"""
        self._running = True
        reconnect_delay = self.config.ws_reconnect_delay
        attempts = 0
        
        while self._running and attempts < self.config.max_reconnect_attempts:
            try:
                async with websockets.connect(url) as ws:
                    self._ws_connections[stream_key] = ws
                    attempts = 0  # Reset on successful connection
                    logger.info(f"WebSocket connected: {stream_key}")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        try:
                            data = json.loads(message)
                            parsed = parser(data)
                            
                            # Call all registered callbacks
                            for callback in self._ws_callbacks.get(stream_key, []):
                                try:
                                    if asyncio.iscoroutinefunction(callback):
                                        await callback(parsed)
                                    else:
                                        callback(parsed)
                                except Exception as e:
                                    logger.error(f"Callback error: {e}")
                        except Exception as e:
                            logger.error(f"Message parse error: {e}")
                            
            except websockets.exceptions.ConnectionClosed:
                logger.warning(f"WebSocket closed: {stream_key}, reconnecting...")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            
            attempts += 1
            if self._running and attempts < self.config.max_reconnect_attempts:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)  # Exponential backoff
        
        if stream_key in self._ws_connections:
            del self._ws_connections[stream_key]
        logger.info(f"WebSocket stopped: {stream_key}")
    
    def _parse_ticker(self, data: Dict) -> TickerData:
        """Parse ticker WebSocket message"""
        return TickerData(
            symbol=data["s"],
            price=float(data["c"]),
            bid_price=float(data.get("b", data["c"])),
            ask_price=float(data.get("a", data["c"])),
            volume_24h=float(data.get("q", 0)),
            timestamp=datetime.now(timezone.utc)
        )
    
    def _parse_funding(self, data: Dict) -> FundingRateData:
        """Parse funding rate WebSocket message"""
        return FundingRateData(
            symbol=data["s"],
            funding_rate=float(data.get("r", 0)) * 100,
            funding_time=datetime.fromtimestamp(
                data.get("T", time.time() * 1000) / 1000,
                tz=timezone.utc
            ),
            mark_price=float(data.get("p", 0)),
            index_price=float(data.get("i", 0)),
            estimated_settle_price=float(data.get("P", 0))
        )
    
    # =========================================
    # Utility Methods
    # =========================================
    
    async def get_server_time(self) -> datetime:
        """Get Binance server time"""
        data = await self._request(
            "GET",
            self.config.binance_spot_base,
            "/api/v3/time"
        )
        return datetime.fromtimestamp(data["serverTime"] / 1000, tz=timezone.utc)
    
    async def test_connectivity(self) -> bool:
        """Test API connectivity"""
        try:
            await self._request(
                "GET",
                self.config.binance_spot_base,
                "/api/v3/ping"
            )
            return True
        except Exception:
            return False
    
    async def get_exchange_info(self, symbol: str = None) -> Dict:
        """Get exchange trading rules"""
        params = {"symbol": symbol} if symbol else {}
        return await self._request(
            "GET",
            self.config.binance_spot_base,
            "/api/v3/exchangeInfo",
            params
        )


# Convenience function to create a client
def create_client(config: BinanceAPIConfig = None) -> BinanceClient:
    """Create a Binance client instance"""
    return BinanceClient(config or DEFAULT_BINANCE_CONFIG)
