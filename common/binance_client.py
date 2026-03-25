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
from collections import deque
from typing import Dict, List, Optional, Any, Callable, Deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
    deribit_base_url: str = "https://www.deribit.com"
    dvol_default_currency: str = "BTC"

    binance_spot_ws: str = "wss://stream.binance.com:9443/ws"
    binance_futures_ws: str = "wss://fstream.binance.com/ws"

    ws_reconnect_delay: int = 5
    max_reconnect_attempts: int = 10
    # 全局请求节流：0 表示不启用客户端侧限速。
    max_requests_per_minute: int = 900
    # 命中交易所限流（如 -1003）时的重试参数。
    rate_limit_max_retries: int = 6
    rate_limit_retry_backoff_sec: float = 0.6
    rate_limit_retry_max_backoff_sec: float = 10.0


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
        # 用滑动窗口控制“每 60 秒请求数”，避免 GA/回测批量抓数时打爆限额。
        self._request_timestamps: Deque[float] = deque()
        self._request_lock = asyncio.Lock()
        
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

    @staticmethod
    def _is_rate_limit_error(code: int, message: str) -> bool:
        """判断是否为限流类错误（HTTP429/418、-1003、-1015 等）。"""
        msg = (message or "").lower()
        return (
            code in {-1003, -1015, 418, 429}
            or "too many requests" in msg
            or "request weight" in msg
        )

    @staticmethod
    def _is_deribit_rate_limit_error(code: int, message: str) -> bool:
        """判断 Deribit 限流错误。"""
        msg = (message or "").lower()
        return code in {10028, 429} or "too_many_requests" in msg or "rate limit" in msg

    @staticmethod
    def _normalize_utc(ts: datetime) -> datetime:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    @staticmethod
    def _resolution_to_seconds(resolution: str) -> int:
        # Deribit volatility index 的 resolution 参数单位为“秒”（如 60=60s，1D=1天）。
        if resolution == "1D":
            return 86400
        try:
            return max(1, int(resolution))
        except ValueError:
            return 60

    def _dvol_resolution(self, interval: str) -> str:
        """把系统 bar 周期映射到 Deribit DVOL resolution。"""
        mapping = {
            # Deribit volatility index（kind=option）当前稳定支持的盘中分辨率为 60。
            # 因此所有盘中周期统一拉 60 秒，再在策略层按 spot bar 对齐/forward-fill。
            "15m": "60",
            "30m": "60",
            "1h": "60",
            "4h": "60",
            "1d": "1D",
        }
        return mapping.get((interval or "").lower(), "60")

    def _resolve_dvol_currency(self, symbol: str) -> str:
        """按 symbol 解析 DVOL 币种；未命中时回退默认币种。"""
        symbol_upper = (symbol or "").upper()
        if symbol_upper.startswith("ETH"):
            return "ETH"
        if symbol_upper.startswith("BTC"):
            return "BTC"
        default_currency = str(getattr(self.config, "dvol_default_currency", "BTC") or "BTC").upper()
        return default_currency if default_currency in {"BTC", "ETH"} else "BTC"

    async def _acquire_request_slot(self):
        """基于滑动窗口节流请求速率。"""
        limit = max(0, int(getattr(self.config, "max_requests_per_minute", 0) or 0))
        if limit <= 0:
            return

        while True:
            async with self._request_lock:
                now = time.monotonic()
                while self._request_timestamps and (now - self._request_timestamps[0]) >= 60.0:
                    self._request_timestamps.popleft()
                if len(self._request_timestamps) < limit:
                    self._request_timestamps.append(now)
                    return
                wait_seconds = 60.0 - (now - self._request_timestamps[0]) + 0.01
            await asyncio.sleep(max(0.01, wait_seconds))
    
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
        base_params = dict(params or {})
        url = f"{base_url}{endpoint}"

        max_retries = max(0, int(getattr(self.config, "rate_limit_max_retries", 0) or 0))
        backoff_base = max(0.05, float(getattr(self.config, "rate_limit_retry_backoff_sec", 0.6) or 0.6))
        backoff_cap = max(
            backoff_base,
            float(getattr(self.config, "rate_limit_retry_max_backoff_sec", 10.0) or 10.0),
        )

        attempt = 0
        while True:
            req_params = dict(base_params)
            if signed:
                req_params["timestamp"] = int(time.time() * 1000)
                req_params["signature"] = self._generate_signature(req_params)

            await self._acquire_request_slot()
            try:
                if method == "GET":
                    async with session.get(url, params=req_params, headers=self._get_headers()) as resp:
                        data = await resp.json(content_type=None)
                elif method == "POST":
                    async with session.post(url, params=req_params, headers=self._get_headers()) as resp:
                        data = await resp.json(content_type=None)
                elif method == "DELETE":
                    async with session.delete(url, params=req_params, headers=self._get_headers()) as resp:
                        data = await resp.json(content_type=None)
                else:
                    raise ValueError(f"Unsupported method: {method}")
            except aiohttp.ClientError as e:
                logger.error(f"HTTP request failed: {e}")
                raise

            # Binance 错误响应通常是 {"code": -1003, "msg": "..."}。
            if isinstance(data, dict) and "code" in data and data["code"] != 200:
                api_error = BinanceAPIError(int(data["code"]), data.get("msg", "Unknown error"))
                if self._is_rate_limit_error(api_error.code, api_error.message) and attempt < max_retries:
                    delay = min(backoff_cap, backoff_base * (2 ** attempt))
                    attempt += 1
                    logger.warning(
                        "Rate limit hit on %s %s (code=%s), retrying in %.2fs (%d/%d)",
                        method,
                        endpoint,
                        api_error.code,
                        delay,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise api_error

            return data
    
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
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        """Get spot candlestick data."""
        try:
            params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
            if start_time:
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                params["startTime"] = int(start_time.timestamp() * 1000)
            if end_time:
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)
                params["endTime"] = int(end_time.timestamp() * 1000)
            data = await self._request(
                "GET",
                self.config.binance_spot_base,
                "/api/v3/klines",
                params
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

    async def get_mark_price_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        """Get mark-price candlestick data from USDT-M futures."""
        try:
            params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
            if start_time:
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                params["startTime"] = int(start_time.timestamp() * 1000)
            if end_time:
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)
                params["endTime"] = int(end_time.timestamp() * 1000)
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v1/markPriceKlines",
                params,
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
            logger.error(f"Failed to get mark price klines for {symbol}: {e}")
            return []

    async def get_premium_index_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        """Get premium-index candlestick data from USDT-M futures."""
        try:
            params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
            if start_time:
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                params["startTime"] = int(start_time.timestamp() * 1000)
            if end_time:
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)
                params["endTime"] = int(end_time.timestamp() * 1000)
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v1/premiumIndexKlines",
                params,
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
            logger.error(f"Failed to get premium index klines for {symbol}: {e}")
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
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        """Get historical funding rates"""
        try:
            params: Dict[str, Any] = {"symbol": symbol, "limit": limit}
            if start_time:
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                params["startTime"] = int(start_time.timestamp() * 1000)
            if end_time:
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)
                params["endTime"] = int(end_time.timestamp() * 1000)
            data = await self._request(
                "GET",
                self.config.binance_futures_base,
                "/fapi/v1/fundingRate",
                params
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

    async def _deribit_public_get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """请求 Deribit 公共接口（含限流退避）。"""
        session = await self._get_session()
        base_url = str(getattr(self.config, "deribit_base_url", "https://www.deribit.com")).rstrip("/")
        url = f"{base_url}{endpoint}"
        max_retries = max(0, int(getattr(self.config, "rate_limit_max_retries", 0) or 0))
        backoff_base = max(0.05, float(getattr(self.config, "rate_limit_retry_backoff_sec", 0.6) or 0.6))
        backoff_cap = max(
            backoff_base,
            float(getattr(self.config, "rate_limit_retry_max_backoff_sec", 10.0) or 10.0),
        )
        attempt = 0
        while True:
            await self._acquire_request_slot()
            try:
                async with session.get(url, params=params or {}) as resp:
                    status = resp.status
                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        text = await resp.text()
                        payload = {
                            "error": {
                                "code": status,
                                "message": text[:500],
                            }
                        }
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries:
                    delay = min(backoff_cap, backoff_base * (2 ** attempt))
                    attempt += 1
                    logger.warning(
                        "Deribit request failed on GET %s (%s), retrying in %.2fs (%d/%d)",
                        endpoint,
                        type(e).__name__,
                        delay,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Deribit HTTP request failed: %r", e)
                raise

            code = 0
            message = ""
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                err = payload.get("error", {})
                code = int(err.get("code", status))
                message = str(err.get("message", ""))
            elif status >= 400:
                code = status
                message = str(payload)

            if code != 0:
                if self._is_deribit_rate_limit_error(code, message) and attempt < max_retries:
                    delay = min(backoff_cap, backoff_base * (2 ** attempt))
                    attempt += 1
                    logger.warning(
                        "Rate limit hit on GET %s (code=%s), retrying in %.2fs (%d/%d)",
                        endpoint,
                        code,
                        delay,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise BinanceAPIError(code, f"Deribit error: {message}")
            if not isinstance(payload, dict):
                raise BinanceAPIError(-1, f"Deribit unexpected response: {payload!r}")
            return payload

    @staticmethod
    def _parse_deribit_dvol_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """解析 Deribit DVOL 返回结构为标准时间序列。"""
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        raw_rows = result.get("data", []) if isinstance(result, dict) else []
        parsed: List[Dict[str, Any]] = []
        for row in raw_rows:
            if isinstance(row, (list, tuple)) and len(row) >= 5:
                ts_ms = int(row[0])
                value = float(row[4])
            elif isinstance(row, dict):
                ts_raw = row.get("timestamp") or row.get("time")
                if ts_raw is None:
                    continue
                ts_ms = int(ts_raw)
                value = float(
                    row.get("close", row.get("value", row.get("dvol", 0.0)))
                )
            else:
                continue
            parsed.append(
                {
                    "time": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                    "dvol_value": value,
                }
            )
        parsed.sort(key=lambda x: x["time"])
        dedup: List[Dict[str, Any]] = []
        for item in parsed:
            if not dedup or item["time"] > dedup[-1]["time"]:
                dedup.append(item)
        return dedup

    async def get_dvol_index_history(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        """
        获取 Deribit DVOL 历史序列（按 symbol 映射到 BTC/ETH）。

        返回格式：
        - time: datetime(UTC)
        - dvol_value: float
        """
        try:
            resolution = self._dvol_resolution(interval)
            end_ts = self._normalize_utc(end_time) if end_time else datetime.now(timezone.utc)
            if start_time is None:
                span_seconds = self._resolution_to_seconds(resolution) * max(1, int(limit))
                start_ts = end_ts - timedelta(seconds=span_seconds)
            else:
                start_ts = self._normalize_utc(start_time)
            if start_ts >= end_ts:
                return []

            payload = await self._deribit_public_get(
                "/api/v2/public/get_volatility_index_data",
                {
                    "currency": self._resolve_dvol_currency(symbol),
                    "kind": "option",
                    "resolution": resolution,
                    "start_timestamp": int(start_ts.timestamp() * 1000),
                    "end_timestamp": int(end_ts.timestamp() * 1000),
                },
            )
            rows = self._parse_deribit_dvol_rows(payload)
            if start_time is not None:
                rows = [r for r in rows if r["time"] >= start_ts]
            rows = [r for r in rows if r["time"] <= end_ts]
            return rows[-limit:] if limit > 0 else rows
        except Exception as e:
            logger.error("Failed to get Deribit DVOL history for %s: %r", symbol, e)
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
