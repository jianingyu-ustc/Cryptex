#!/usr/bin/env python3
"""
test_binance_arb.py - Binance 套利系统 API 测试脚本
===================================================

功能: 测试 Binance 现货和合约 API，验证套利系统所需的各项功能
用途: 在启动套利系统前验证 API 连接和权限

测试内容:
  1. API 连接测试
  2. 现货账户余额
  3. 合约账户余额 (USDT-M)
  4. 资金费率查询
  5. 期货合约信息
  6. 订单簿深度
  7. 挂单查询
  8. 持仓查询

使用方法:
  python tests/test_binance_arb.py              # 运行所有测试
  python tests/test_binance_arb.py --quick      # 快速测试 (仅公开API)
  python tests/test_binance_arb.py --balance    # 仅测试余额

依赖:
  pip install httpx python-dotenv

环境变量:
  BINANCE_API_KEY - Binance API Key
  BINANCE_API_SECRET - Binance API Secret
"""

import os
import sys
import time
import hmac
import hashlib
import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from urllib.parse import urlencode

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, _, value = line.partition('=')
            os.environ[key.strip()] = value.strip()

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_header(text: str):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}{text.center(60)}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")


def print_success(text: str):
    print(f"{GREEN}✅ {text}{RESET}")


def print_error(text: str):
    print(f"{RED}❌ {text}{RESET}")


def print_warning(text: str):
    print(f"{YELLOW}⚠️  {text}{RESET}")


def print_info(text: str):
    print(f"{CYAN}ℹ️  {text}{RESET}")


class BinanceArbTester:
    """Binance API tester for arbitrage system"""
    
    # API endpoints
    SPOT_BASE = "https://api.binance.com"
    SPOT_DATA = "https://data-api.binance.vision"  # Backup for price data
    FUTURES_BASE = "https://fapi.binance.com"
    DELIVERY_BASE = "https://dapi.binance.com"
    
    def __init__(self):
        self.api_key = os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = os.environ.get("BINANCE_API_SECRET", "")
        self.results: Dict[str, bool] = {}
        
        # Try alternative endpoints if main ones fail
        self.spot_base = self.SPOT_BASE
        self.use_data_api = False
    
    def _sign(self, params: Dict) -> str:
        """Create HMAC SHA256 signature"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers with API key"""
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }
    
    async def _request(
        self, 
        base: str, 
        endpoint: str, 
        params: Dict = None,
        signed: bool = False,
        method: str = "GET"
    ) -> Optional[Dict]:
        """Make HTTP request to Binance API"""
        if params is None:
            params = {}
        
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)
        
        url = f"{base}{endpoint}"
        if params and method == "GET":
            url = f"{url}?{urlencode(params)}"
        
        headers = self._get_headers() if self.api_key else {}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                elif method == "POST":
                    response = await client.post(url, headers=headers, json=params)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers)
                else:
                    return None
                
                if response.status_code == 200:
                    return response.json()
                else:
                    print_warning(f"HTTP {response.status_code}: {response.text[:100]}")
                    return None
        except Exception as e:
            print_warning(f"Request error: {e}")
            return None
    
    # ==================== Public API Tests ====================
    
    async def test_connectivity(self) -> bool:
        """Test API connectivity"""
        print_header("1. API Connectivity Test")
        
        # Test spot API
        print_info("Testing Spot API...")
        result = await self._request(self.SPOT_BASE, "/api/v3/ping")
        if result is not None:
            print_success("Spot API: Connected")
        else:
            # Try data API
            print_warning("Main API failed, trying data API...")
            result = await self._request(self.SPOT_DATA, "/api/v3/ping")
            if result is not None:
                print_success("Spot Data API: Connected")
                self.spot_base = self.SPOT_DATA
                self.use_data_api = True
            else:
                print_error("Spot API: Failed")
                self.results["spot_connectivity"] = False
                return False
        
        # Test futures API
        print_info("Testing Futures API (USDT-M)...")
        result = await self._request(self.FUTURES_BASE, "/fapi/v1/ping")
        if result is not None:
            print_success("Futures API: Connected")
        else:
            print_warning("Futures API: Failed (may be blocked in your region)")
        
        # Test delivery API
        print_info("Testing Delivery API (Coin-M)...")
        result = await self._request(self.DELIVERY_BASE, "/dapi/v1/ping")
        if result is not None:
            print_success("Delivery API: Connected")
        else:
            print_warning("Delivery API: Failed (may be blocked in your region)")
        
        self.results["connectivity"] = True
        return True
    
    async def test_server_time(self) -> bool:
        """Test server time and sync"""
        print_header("2. Server Time Test")
        
        result = await self._request(self.spot_base, "/api/v3/time")
        if result and "serverTime" in result:
            server_time = result["serverTime"]
            local_time = int(time.time() * 1000)
            diff_ms = abs(server_time - local_time)
            
            print_success(f"Server Time: {datetime.fromtimestamp(server_time/1000, tz=timezone.utc)}")
            print_info(f"Time Difference: {diff_ms}ms")
            
            if diff_ms > 1000:
                print_warning("Time diff > 1s, may cause signature issues")
            else:
                print_success("Time sync OK")
            
            self.results["time_sync"] = True
            return True
        
        print_error("Failed to get server time")
        self.results["time_sync"] = False
        return False
    
    async def test_ticker_prices(self) -> bool:
        """Test ticker price data"""
        print_header("3. Ticker Prices Test")
        
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        success = 0
        
        for symbol in symbols:
            result = await self._request(self.spot_base, f"/api/v3/ticker/price?symbol={symbol}")
            if result and "price" in result:
                price = float(result["price"])
                print_success(f"{symbol}: ${price:,.2f}")
                success += 1
            else:
                print_warning(f"{symbol}: Failed")
        
        self.results["ticker_prices"] = success > 0
        return success > 0
    
    async def test_funding_rates(self) -> bool:
        """Test funding rate data (Futures)"""
        print_header("4. Funding Rates Test (套利核心数据)")
        
        # Get funding rate for major symbols
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        
        for symbol in symbols:
            result = await self._request(self.FUTURES_BASE, f"/fapi/v1/premiumIndex?symbol={symbol}")
            if result:
                funding_rate = float(result.get("lastFundingRate", 0)) * 100
                mark_price = float(result.get("markPrice", 0))
                index_price = float(result.get("indexPrice", 0))
                next_funding = result.get("nextFundingTime", 0)
                
                # Calculate basis
                basis = ((mark_price - index_price) / index_price * 100) if index_price else 0
                
                print_success(f"{symbol}:")
                print_info(f"  Funding Rate: {funding_rate:+.4f}%")
                print_info(f"  Mark Price: ${mark_price:,.2f}")
                print_info(f"  Index Price: ${index_price:,.2f}")
                print_info(f"  Basis: {basis:+.4f}%")
                
                if next_funding:
                    next_time = datetime.fromtimestamp(next_funding/1000, tz=timezone.utc)
                    print_info(f"  Next Funding: {next_time.strftime('%H:%M:%S UTC')}")
            else:
                print_warning(f"{symbol}: Failed to get funding rate")
        
        # Get all funding rates
        print_info("\nTop funding rates (absolute value):")
        result = await self._request(self.FUTURES_BASE, "/fapi/v1/premiumIndex")
        if result and isinstance(result, list):
            # Sort by absolute funding rate
            sorted_rates = sorted(
                result, 
                key=lambda x: abs(float(x.get("lastFundingRate", 0))),
                reverse=True
            )[:10]
            
            for item in sorted_rates:
                symbol = item.get("symbol", "")
                rate = float(item.get("lastFundingRate", 0)) * 100
                print_info(f"  {symbol}: {rate:+.4f}%")
            
            self.results["funding_rates"] = True
            return True
        
        print_warning("Failed to get funding rates (Futures API may be blocked)")
        self.results["funding_rates"] = False
        return False
    
    async def test_delivery_contracts(self) -> bool:
        """Test delivery (quarterly) contracts"""
        print_header("5. Delivery Contracts Test (期现套利)")
        
        result = await self._request(self.DELIVERY_BASE, "/dapi/v1/exchangeInfo")
        if result and "symbols" in result:
            symbols = result["symbols"]
            
            # Filter for quarterly contracts
            quarterly = [s for s in symbols if s.get("contractType") in ["CURRENT_QUARTER", "NEXT_QUARTER"]]
            
            print_success(f"Found {len(quarterly)} quarterly contracts")
            
            for contract in quarterly[:5]:
                symbol = contract.get("symbol", "")
                pair = contract.get("pair", "")
                contract_type = contract.get("contractType", "")
                delivery_date = contract.get("deliveryDate", 0)
                
                if delivery_date:
                    delivery = datetime.fromtimestamp(delivery_date/1000, tz=timezone.utc)
                    days_to_delivery = (delivery - datetime.now(timezone.utc)).days
                else:
                    days_to_delivery = "N/A"
                
                print_info(f"  {symbol} ({pair})")
                print_info(f"    Type: {contract_type}, Days to delivery: {days_to_delivery}")
            
            self.results["delivery_contracts"] = True
            return True
        
        print_warning("Failed to get delivery contracts (API may be blocked)")
        self.results["delivery_contracts"] = False
        return False
    
    async def test_order_book(self) -> bool:
        """Test order book depth"""
        print_header("6. Order Book Test")
        
        symbols = ["BTCUSDT", "ETHUSDT"]
        
        for symbol in symbols:
            result = await self._request(self.spot_base, f"/api/v3/depth?symbol={symbol}&limit=10")
            if result and "bids" in result and "asks" in result:
                bids = result["bids"]
                asks = result["asks"]
                
                if bids and asks:
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    spread = best_ask - best_bid
                    spread_pct = (spread / best_bid) * 100
                    
                    bid_depth = sum(float(b[0]) * float(b[1]) for b in bids[:5])
                    ask_depth = sum(float(a[0]) * float(a[1]) for a in asks[:5])
                    
                    print_success(f"{symbol}:")
                    print_info(f"  Best Bid: ${best_bid:,.2f}")
                    print_info(f"  Best Ask: ${best_ask:,.2f}")
                    print_info(f"  Spread: ${spread:.2f} ({spread_pct:.4f}%)")
                    print_info(f"  Bid Depth (5 levels): ${bid_depth:,.0f}")
                    print_info(f"  Ask Depth (5 levels): ${ask_depth:,.0f}")
            else:
                print_warning(f"{symbol}: Failed to get order book")
        
        self.results["order_book"] = True
        return True
    
    # ==================== Private API Tests ====================
    
    async def test_spot_balance(self) -> bool:
        """Test spot account balance (requires API key)"""
        print_header("7. Spot Account Balance (现货账户)")
        
        if not self.api_key or not self.api_secret:
            print_warning("API Key not configured, skipping balance test")
            print_info("Add BINANCE_API_KEY and BINANCE_API_SECRET to .env")
            self.results["spot_balance"] = False
            return False
        
        result = await self._request(
            self.SPOT_BASE, 
            "/api/v3/account",
            signed=True
        )
        
        if result and "balances" in result:
            balances = result["balances"]
            
            # Separate spot and earn (LD prefix) assets
            spot_assets = []
            earn_assets = []
            
            for b in balances:
                asset = b.get("asset", "")
                free = float(b.get("free", 0))
                locked = float(b.get("locked", 0))
                total = free + locked
                
                if total > 0:
                    if asset.startswith("LD"):
                        earn_assets.append({"asset": asset, "total": total})
                    else:
                        spot_assets.append({"asset": asset, "free": free, "locked": locked, "total": total})
            
            # Display spot assets
            if spot_assets:
                print_success(f"现货可用: {len(spot_assets)} 种资产")
                for b in spot_assets[:8]:
                    asset = b["asset"]
                    if asset in ["USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "SOL"]:
                        print_success(f"  {asset}: {b['total']:.8f} (free: {b['free']:.8f}, locked: {b['locked']:.8f})")
                    else:
                        print_info(f"  {asset}: {b['total']:.8f}")
            else:
                print_warning("现货账户无余额")
            
            # Display earn assets (理财)
            if earn_assets:
                print_info(f"\n理财资产 (LD前缀): {len(earn_assets)} 种")
                total_earn_usdt = 0
                for b in earn_assets:
                    asset = b["asset"]
                    amount = b["total"]
                    real_asset = asset[2:] if asset.startswith("LD") else asset
                    print_info(f"  {real_asset}: {amount:.8f} (在理财中)")
                    if real_asset == "USDT":
                        total_earn_usdt = amount
                
                if total_earn_usdt > 0:
                    print_success(f"\n💰 理财中 USDT: ${total_earn_usdt:,.2f}")
            
            self.results["spot_balance"] = True
            return True
        
        print_error("Failed to get spot balance")
        print_info("Check API Key permissions (need 'Enable Reading')")
        self.results["spot_balance"] = False
        return False
    
    async def test_funding_balance(self) -> bool:
        """Test funding account balance (资金账户)"""
        print_header("7.5 Funding Account Balance (资金账户)")
        
        if not self.api_key or not self.api_secret:
            print_warning("API Key not configured, skipping")
            self.results["funding_balance"] = False
            return False
        
        result = await self._request(
            self.SPOT_BASE,
            "/sapi/v1/asset/get-funding-asset",
            params={},
            signed=True,
            method="POST"
        )
        
        if result and isinstance(result, list):
            if result:
                print_success(f"资金账户: {len(result)} 种资产")
                total_usdt = 0
                
                for asset in result:
                    name = asset.get("asset", "")
                    free = float(asset.get("free", 0))
                    locked = float(asset.get("locked", 0))
                    total = free + locked
                    
                    if name in ["USDT", "USDC", "BTC", "ETH", "BNB"]:
                        print_success(f"  {name}: {total:.8f}")
                        if name == "USDT":
                            total_usdt = total
                    else:
                        print_info(f"  {name}: {total:.8f}")
                
                if total_usdt > 0:
                    print_success(f"\n💰 资金账户 USDT: ${total_usdt:,.2f}")
            else:
                print_info("资金账户无余额")
            
            self.results["funding_balance"] = True
            return True
        
        print_warning("Failed to get funding balance")
        self.results["funding_balance"] = False
        return False
    
    async def test_earn_balance(self) -> bool:
        """Test simple earn (flexible savings) balance"""
        print_header("7.6 Simple Earn Balance (灵活理财)")
        
        if not self.api_key or not self.api_secret:
            print_warning("API Key not configured, skipping")
            self.results["earn_balance"] = False
            return False
        
        # First get real-time prices for accurate valuation
        prices = {}
        price_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT"]
        for symbol in price_symbols:
            price_result = await self._request(self.spot_base, f"/api/v3/ticker/price?symbol={symbol}")
            if price_result and "price" in price_result:
                asset = symbol.replace("USDT", "")
                prices[asset] = float(price_result["price"])
        
        result = await self._request(
            self.SPOT_BASE,
            "/sapi/v1/simple-earn/flexible/position",
            params={"size": 100},
            signed=True
        )
        
        if result and "rows" in result:
            rows = result["rows"]
            if rows:
                print_success(f"灵活理财: {len(rows)} 种资产")
                total_value = 0
                
                for pos in rows:
                    asset = pos.get("asset", "")
                    amount = float(pos.get("totalAmount", 0))
                    apy = float(pos.get("latestAnnualPercentageRate", 0)) * 100
                    
                    # Calculate value using real-time price
                    if asset == "USDT" or asset == "USDC":
                        value = amount
                    elif asset in prices:
                        value = amount * prices[asset]
                    else:
                        value = 0  # Unknown asset
                    
                    total_value += value
                    
                    if value > 0:
                        print_success(f"  {asset}: {amount:.8f} (≈${value:,.2f}, APY: {apy:.2f}%)")
                    else:
                        print_success(f"  {asset}: {amount:.8f} (APY: {apy:.2f}%)")
                
                print_success(f"\n💰 理财总价值 (实时): ${total_value:,.2f}")
            else:
                print_info("无灵活理财持仓")
            
            self.results["earn_balance"] = True
            return True
        
        print_warning("Failed to get earn balance (may need permission)")
        self.results["earn_balance"] = False
        return False
    
    async def test_futures_balance(self) -> bool:
        """Test futures account balance (USDT-M)"""
        print_header("8. Futures Account Balance (USDT-M)")
        
        if not self.api_key or not self.api_secret:
            print_warning("API Key not configured, skipping")
            self.results["futures_balance"] = False
            return False
        
        result = await self._request(
            self.FUTURES_BASE,
            "/fapi/v2/balance",
            signed=True
        )
        
        if result and isinstance(result, list):
            non_zero = [
                b for b in result
                if float(b.get("balance", 0)) > 0
            ]
            
            print_success(f"Found {len(non_zero)} assets with balance")
            
            for balance in non_zero:
                asset = balance.get("asset", "")
                bal = float(balance.get("balance", 0))
                available = float(balance.get("availableBalance", 0))
                
                print_success(f"  {asset}: {bal:.4f} (available: {available:.4f})")
            
            self.results["futures_balance"] = True
            return True
        
        print_warning("Failed to get futures balance (may need Futures permission)")
        self.results["futures_balance"] = False
        return False
    
    async def test_futures_positions(self) -> bool:
        """Test futures positions"""
        print_header("9. Futures Positions")
        
        if not self.api_key or not self.api_secret:
            print_warning("API Key not configured, skipping")
            self.results["futures_positions"] = False
            return False
        
        result = await self._request(
            self.FUTURES_BASE,
            "/fapi/v2/positionRisk",
            signed=True
        )
        
        if result and isinstance(result, list):
            # Filter positions with non-zero amount
            open_positions = [
                p for p in result
                if float(p.get("positionAmt", 0)) != 0
            ]
            
            if open_positions:
                print_success(f"Found {len(open_positions)} open positions")
                
                for pos in open_positions:
                    symbol = pos.get("symbol", "")
                    amount = float(pos.get("positionAmt", 0))
                    entry_price = float(pos.get("entryPrice", 0))
                    mark_price = float(pos.get("markPrice", 0))
                    unrealized_pnl = float(pos.get("unRealizedProfit", 0))
                    leverage = pos.get("leverage", "")
                    
                    side = "LONG" if amount > 0 else "SHORT"
                    
                    print_success(f"  {symbol} ({side}):")
                    print_info(f"    Amount: {abs(amount):.4f}")
                    print_info(f"    Entry: ${entry_price:,.2f}")
                    print_info(f"    Mark: ${mark_price:,.2f}")
                    print_info(f"    PnL: ${unrealized_pnl:,.2f}")
                    print_info(f"    Leverage: {leverage}x")
            else:
                print_info("No open positions")
            
            self.results["futures_positions"] = True
            return True
        
        print_warning("Failed to get positions")
        self.results["futures_positions"] = False
        return False
    
    async def test_open_orders(self) -> bool:
        """Test open orders query"""
        print_header("10. Open Orders")
        
        if not self.api_key or not self.api_secret:
            print_warning("API Key not configured, skipping")
            self.results["open_orders"] = False
            return False
        
        # Spot orders
        print_info("Checking Spot orders...")
        spot_orders = await self._request(
            self.SPOT_BASE,
            "/api/v3/openOrders",
            signed=True
        )
        
        if spot_orders is not None:
            if spot_orders:
                print_success(f"Found {len(spot_orders)} open spot orders")
                for order in spot_orders[:5]:
                    symbol = order.get("symbol", "")
                    side = order.get("side", "")
                    price = float(order.get("price", 0))
                    qty = float(order.get("origQty", 0))
                    print_info(f"  {symbol}: {side} {qty} @ ${price:,.2f}")
            else:
                print_info("No open spot orders")
        else:
            print_warning("Failed to get spot orders")
        
        # Futures orders
        print_info("Checking Futures orders...")
        futures_orders = await self._request(
            self.FUTURES_BASE,
            "/fapi/v1/openOrders",
            signed=True
        )
        
        if futures_orders is not None:
            if futures_orders:
                print_success(f"Found {len(futures_orders)} open futures orders")
                for order in futures_orders[:5]:
                    symbol = order.get("symbol", "")
                    side = order.get("side", "")
                    price = float(order.get("price", 0))
                    qty = float(order.get("origQty", 0))
                    print_info(f"  {symbol}: {side} {qty} @ ${price:,.2f}")
            else:
                print_info("No open futures orders")
        else:
            print_warning("Failed to get futures orders")
        
        self.results["open_orders"] = True
        return True
    
    # ==================== Arbitrage Specific Tests ====================
    
    async def test_stablecoin_spreads(self) -> bool:
        """Test stablecoin price spreads"""
        print_header("11. Stablecoin Spreads (稳定币套利)")
        
        pairs = [
            ("BUSDUSDT", "BUSD/USDT"),
            ("USDCUSDT", "USDC/USDT"),
            ("TUSDUSDT", "TUSD/USDT"),
            ("FDUSDUSDT", "FDUSD/USDT"),
        ]
        
        for symbol, name in pairs:
            result = await self._request(self.spot_base, f"/api/v3/ticker/24hr?symbol={symbol}")
            if result:
                price = float(result.get("lastPrice", 0))
                high = float(result.get("highPrice", 0))
                low = float(result.get("lowPrice", 0))
                volume = float(result.get("volume", 0))
                
                # Calculate deviation from peg (1.0)
                deviation = (price - 1.0) * 100
                range_24h = (high - low) * 100
                
                color = GREEN if abs(deviation) > 0.1 else YELLOW
                print(f"{color}  {name}: ${price:.6f} (deviation: {deviation:+.4f}%){RESET}")
                print_info(f"    24h Range: {range_24h:.4f}%, Volume: ${volume:,.0f}")
            else:
                print_warning(f"  {name}: Not available")
        
        self.results["stablecoin_spreads"] = True
        return True
    
    async def test_arbitrage_opportunities(self) -> bool:
        """Scan for potential arbitrage opportunities"""
        print_header("12. Arbitrage Opportunities Scan")
        
        print_info("Scanning funding rate arbitrage opportunities...")
        
        # Get all funding rates
        result = await self._request(self.FUTURES_BASE, "/fapi/v1/premiumIndex")
        if not result:
            print_warning("Cannot scan - Futures API unavailable")
            self.results["arb_scan"] = False
            return False
        
        opportunities = []
        
        for item in result:
            symbol = item.get("symbol", "")
            funding_rate = float(item.get("lastFundingRate", 0)) * 100
            mark_price = float(item.get("markPrice", 0))
            
            # Funding rate arbitrage: when rate > 0.03%, can earn by shorting perp
            if abs(funding_rate) > 0.03:
                net_profit = abs(funding_rate) - 0.10  # Subtract trading cost
                if net_profit > 0:
                    opportunities.append({
                        "symbol": symbol,
                        "type": "FUNDING",
                        "direction": "SHORT_PERP" if funding_rate > 0 else "LONG_PERP",
                        "rate": funding_rate,
                        "net_profit": net_profit,
                        "price": mark_price
                    })
        
        # Sort by profit potential
        opportunities.sort(key=lambda x: x["net_profit"], reverse=True)
        
        if opportunities:
            print_success(f"Found {len(opportunities)} potential opportunities")
            for opp in opportunities[:10]:
                print(f"{GREEN}  {opp['symbol']}:{RESET}")
                print_info(f"    Type: {opp['type']}")
                print_info(f"    Direction: {opp['direction']}")
                print_info(f"    Funding Rate: {opp['rate']:+.4f}%")
                print_info(f"    Est. Net Profit: {opp['net_profit']:+.4f}%")
        else:
            print_info("No clear arbitrage opportunities found")
        
        self.results["arb_scan"] = True
        return True
    
    def print_summary(self):
        """Print test results summary"""
        print_header("Test Results Summary")
        
        total = len(self.results)
        passed = sum(1 for v in self.results.values() if v)
        
        for test_name, result in self.results.items():
            status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
            print(f"  {test_name.ljust(25)} [{status}]")
        
        print()
        if passed == total:
            print_success(f"All tests passed! ({passed}/{total})")
        elif passed > total // 2:
            print_warning(f"Some tests failed ({passed}/{total} passed)")
        else:
            print_error(f"Many tests failed ({passed}/{total} passed)")
        
        # Print recommendations
        print("\n" + "=" * 60)
        print_info("套利系统建议:")
        
        if self.results.get("funding_rates"):
            print_success("  ✅ 资金费率数据可用 - 可进行资金费率套利")
        else:
            print_warning("  ⚠️ 资金费率数据不可用 - 可能需要VPN或代理")
        
        if self.results.get("delivery_contracts"):
            print_success("  ✅ 季度合约数据可用 - 可进行期现套利")
        else:
            print_warning("  ⚠️ 季度合约数据不可用")
        
        if self.results.get("spot_balance") and self.results.get("futures_balance"):
            print_success("  ✅ 账户权限正常 - 可进行交易")
        else:
            print_warning("  ⚠️ 需要配置 API Key 或检查权限")
    
    async def run_all_tests(self, quick: bool = False, balance_only: bool = False):
        """Run all tests"""
        print(f"\n{BOLD}🔧 Binance Arbitrage System - API Test Suite{RESET}")
        print(f"   Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        if self.api_key:
            print_success(f"API Key loaded: {self.api_key[:8]}...{self.api_key[-4:]}")
        else:
            print_warning("No API Key configured (private endpoints will be skipped)")
        
        if balance_only:
            # Balance only mode - all account types
            await self.test_connectivity()
            await self.test_spot_balance()
            await self.test_funding_balance()
            await self.test_earn_balance()
            await self.test_futures_balance()
            await self.test_futures_positions()
        elif quick:
            # Quick mode - public APIs only
            await self.test_connectivity()
            await self.test_server_time()
            await self.test_ticker_prices()
            await self.test_funding_rates()
        else:
            # Full test suite
            await self.test_connectivity()
            await self.test_server_time()
            await self.test_ticker_prices()
            await self.test_funding_rates()
            await self.test_delivery_contracts()
            await self.test_order_book()
            await self.test_spot_balance()
            await self.test_funding_balance()
            await self.test_earn_balance()
            await self.test_futures_balance()
            await self.test_futures_positions()
            await self.test_open_orders()
            await self.test_stablecoin_spreads()
            await self.test_arbitrage_opportunities()
        
        self.print_summary()


async def main():
    parser = argparse.ArgumentParser(description="Binance Arbitrage API Test")
    parser.add_argument("--quick", "-q", action="store_true", help="Quick test (public APIs only)")
    parser.add_argument("--balance", "-b", action="store_true", help="Balance test only")
    args = parser.parse_args()
    
    tester = BinanceArbTester()
    await tester.run_all_tests(quick=args.quick, balance_only=args.balance)


if __name__ == "__main__":
    asyncio.run(main())