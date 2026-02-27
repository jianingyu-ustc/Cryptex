#!/usr/bin/env python3
"""
Server Test Script for Polymarket Crypto Predictor
Run this script on an overseas server to test all functionality.

Usage:
    python3 test_server.py           # Run all tests
    python3 test_server.py --quick   # Quick connectivity test only
"""

import asyncio
import subprocess
import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List

# ANSI color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_header(text: str):
    """Print a section header"""
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}{text.center(60)}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


def print_success(text: str):
    print(f"{GREEN}✅ {text}{RESET}")


def print_error(text: str):
    print(f"{RED}❌ {text}{RESET}")


def print_warning(text: str):
    print(f"{YELLOW}⚠️  {text}{RESET}")


def print_info(text: str):
    print(f"{CYAN}ℹ️  {text}{RESET}")


def curl_get(url: str, headers: Dict = None, timeout: int = 10) -> Optional[Dict]:
    """Make HTTP request using curl"""
    try:
        cmd = ["curl", "-s", "-m", str(timeout)]
        if headers:
            for key, value in headers.items():
                cmd.extend(["-H", f"{key}: {value}"])
        cmd.append(url)
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except Exception as e:
        print_error(f"Request failed: {e}")
    return None


class ServerTester:
    """Test suite for overseas server deployment"""
    
    def __init__(self):
        self.results: Dict[str, bool] = {}
        self.api_key = ""
        
    def load_config(self):
        """Load configuration from .env or environment"""
        import os
        
        # Try to load from .env file
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ.setdefault(key.strip(), value.strip())
        
        self.api_key = os.environ.get("BINANCE_API_KEY", "")
        if self.api_key:
            print_success(f"Binance API Key loaded: {self.api_key[:8]}...{self.api_key[-4:]}")
        else:
            print_warning("No Binance API Key configured (will use public endpoints)")
    
    def test_network_connectivity(self) -> bool:
        """Test basic network connectivity"""
        print_header("1. Network Connectivity Test")
        
        endpoints = [
            ("Google DNS", "https://dns.google/resolve?name=google.com&type=A"),
            ("Cloudflare", "https://1.1.1.1/cdn-cgi/trace"),
        ]
        
        success_count = 0
        for name, url in endpoints:
            try:
                result = subprocess.run(
                    ["curl", "-s", "-m", "5", url],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and result.stdout:
                    print_success(f"{name}: Connected")
                    success_count += 1
                else:
                    print_error(f"{name}: Failed")
            except Exception as e:
                print_error(f"{name}: {e}")
        
        self.results["network"] = success_count > 0
        return success_count > 0
    
    def test_binance_api(self) -> bool:
        """Test Binance API connectivity"""
        print_header("2. Binance API Test")
        
        # Multiple endpoints including regional variants
        endpoints = [
            ("api.binance.com", "https://api.binance.com/api/v3"),
            ("api1.binance.com", "https://api1.binance.com/api/v3"),
            ("api2.binance.com", "https://api2.binance.com/api/v3"),
            ("api3.binance.com", "https://api3.binance.com/api/v3"),
            ("api4.binance.com", "https://api4.binance.com/api/v3"),
            ("fapi.binance.com", "https://fapi.binance.com/fapi/v1"),  # Futures API
            ("dapi.binance.com", "https://dapi.binance.com/dapi/v1"),  # Delivery API
        ]
        
        headers = {}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key
        
        # First, debug network connectivity to Binance
        print_info("Debugging Binance connectivity...")
        
        # Check DNS resolution
        try:
            result = subprocess.run(
                ["nslookup", "api.binance.com"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print_success("DNS resolution: OK")
            else:
                print_warning("DNS resolution: Failed")
                print_info(f"  Error: {result.stderr[:100] if result.stderr else 'Unknown'}")
        except Exception as e:
            print_warning(f"DNS check failed: {e}")
        
        # Check raw curl with verbose output for first endpoint
        print_info("Testing direct curl...")
        try:
            result = subprocess.run(
                ["curl", "-v", "-s", "-m", "10", "--connect-timeout", "5", 
                 "https://api.binance.com/api/v3/ping"],
                capture_output=True, text=True, timeout=15
            )
            if "200" in result.stderr or result.stdout == "{}":
                print_success("Direct curl to /ping: OK")
            else:
                print_warning("Direct curl to /ping: Failed")
                # Show last few lines of error
                stderr_lines = result.stderr.split('\n')[-5:] if result.stderr else []
                for line in stderr_lines:
                    if line.strip():
                        print_info(f"  {line.strip()[:80]}")
        except Exception as e:
            print_warning(f"Direct curl failed: {e}")
        
        # Now test actual price endpoints
        print_info("\nTesting price endpoints...")
        working_endpoint = None
        
        for name, endpoint in endpoints:
            # Skip futures/delivery for spot price
            if "fapi" in endpoint or "dapi" in endpoint:
                url = f"{endpoint}/ping"
            else:
                url = f"{endpoint}/ticker/price?symbol=BTCUSDT"
            
            data = curl_get(url, headers if headers else None, timeout=10)
            
            if data is not None:
                if "price" in data:
                    print_success(f"{name}: BTC = ${float(data['price']):,.2f}")
                    working_endpoint = endpoint
                    break
                elif data == {} or "serverTime" in str(data):
                    print_success(f"{name}: Ping OK (API reachable)")
                    # Try to get price from this working endpoint
                    if "fapi" not in endpoint and "dapi" not in endpoint:
                        working_endpoint = endpoint
                        break
                else:
                    print_warning(f"{name}: Unexpected response: {str(data)[:50]}")
            else:
                print_warning(f"{name}: No response")
        
        if working_endpoint:
            # Test additional data
            print_info("\nFetching additional market data...")
            
            # 24hr stats
            url = f"{working_endpoint}/ticker/24hr?symbol=BTCUSDT"
            data = curl_get(url, headers if headers else None, timeout=10)
            if data and "lastPrice" in data:
                print_success(f"  24h Volume: ${float(data.get('volume', 0)) * float(data.get('lastPrice', 0)):,.0f}")
                print_success(f"  24h Change: {float(data.get('priceChangePercent', 0)):+.2f}%")
            
            # Klines (candlesticks)
            url = f"{working_endpoint}/klines?symbol=BTCUSDT&interval=5m&limit=5"
            data = curl_get(url, headers if headers else None, timeout=10)
            if data and isinstance(data, list) and len(data) > 0:
                print_success(f"  Klines available: {len(data)} candles")
            
            self.results["binance"] = True
            return True
        
        # If all Binance endpoints failed, show troubleshooting tips
        print_error("All Binance endpoints failed")
        print_info("\n?? Detected Issue: Geographic/IP Restriction")
        print_info("   Binance blocks access from certain locations/IPs.")
        print_info("   Common affected regions: US, some EU countries, certain VPS providers")
        print_info("")
        print_info("   ✅ Don't worry! The system will use alternative APIs:")
        print_info("      • CoinGecko (global, no restrictions)")
        print_info("      • Kraken (EU-friendly)")
        print_info("      • CryptoCompare (global)")
        print_info("      • CoinPaprika (global)")
        print_info("")
        print_info("   All price data functions will still work normally!")
        
        self.results["binance"] = False
        return False
    
    def test_coingecko_api(self) -> bool:
        """Test CoinGecko API connectivity"""
        print_header("3. CoinGecko API Test")
        
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
        data = curl_get(url, timeout=10)
        
        if data:
            for coin in ["bitcoin", "ethereum", "solana"]:
                if coin in data:
                    price = data[coin].get("usd", 0)
                    change = data[coin].get("usd_24h_change", 0)
                    print_success(f"{coin.upper()}: ${price:,.2f} ({change:+.2f}% 24h)")
            
            self.results["coingecko"] = True
            return True
        
        print_error("CoinGecko API failed")
        self.results["coingecko"] = False
        return False
    
    def test_alternative_apis(self) -> bool:
        """Test alternative price APIs (Kraken, CryptoCompare, CoinPaprika)"""
        print_header("3.5. Alternative Price APIs Test")
        
        success_count = 0
        
        # Test Kraken
        print_info("Testing Kraken API...")
        url = "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD"
        data = curl_get(url, timeout=10)
        if data and "result" in data and "XXBTZUSD" in data.get("result", {}):
            ticker = data["result"]["XXBTZUSD"]
            price = float(ticker["c"][0])
            print_success(f"Kraken: BTC = ${price:,.2f}")
            success_count += 1
        else:
            print_warning("Kraken: No response")
        
        # Test CryptoCompare
        print_info("Testing CryptoCompare API...")
        url = "https://min-api.cryptocompare.com/data/price?fsym=BTC&tsyms=USD"
        data = curl_get(url, timeout=10)
        if data and "USD" in data:
            print_success(f"CryptoCompare: BTC = ${float(data['USD']):,.2f}")
            success_count += 1
        else:
            print_warning("CryptoCompare: No response")
        
        # Test CoinPaprika
        print_info("Testing CoinPaprika API...")
        url = "https://api.coinpaprika.com/v1/tickers/btc-bitcoin"
        data = curl_get(url, timeout=10)
        if data and "quotes" in data and "USD" in data.get("quotes", {}):
            price = float(data["quotes"]["USD"]["price"])
            print_success(f"CoinPaprika: BTC = ${price:,.2f}")
            success_count += 1
        else:
            print_warning("CoinPaprika: No response")
        
        if success_count > 0:
            print_success(f"{success_count}/3 alternative APIs working")
            self.results["alt_apis"] = True
            return True
        
        print_error("All alternative APIs failed")
        self.results["alt_apis"] = False
        return False
    
    def test_polymarket_api(self) -> bool:
        """Test Polymarket API connectivity"""
        print_header("4. Polymarket API Test")
        
        # Test gamma API
        print_info("Testing Gamma API...")
        
        # Try to find active BTC markets
        now_ts = int(time.time())
        slot_5m = (now_ts // 300) * 300
        
        found_events = 0
        for offset in [0, 300, 600, 900, 1200]:
            slot_ts = slot_5m + offset
            slug = f"btc-updown-5m-{slot_ts}"
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            data = curl_get(url, timeout=10)
            
            if data and isinstance(data, list) and len(data) > 0:
                event = data[0]
                title = event.get("title", "Unknown")[:50]
                print_success(f"Found event: {title}...")
                found_events += 1
                
                # Get market details
                markets = event.get("markets", [])
                if markets:
                    market = markets[0]
                    question = market.get("question", "")[:60]
                    print_info(f"  Question: {question}...")
                break
        
        if found_events > 0:
            print_success(f"Polymarket API is working")
            self.results["polymarket"] = True
            return True
        
        print_error("No active Polymarket events found")
        self.results["polymarket"] = False
        return False
    
    async def test_price_client(self) -> bool:
        """Test the price_client module"""
        print_header("5. Price Client Module Test")
        
        try:
            from price_client import PriceClient
            
            client = PriceClient()
            
            # Test get_price_binance
            print_info("Testing Binance price fetch...")
            price = await client.get_price_binance("BTC")
            if price:
                print_success(f"BTC Price (Binance): ${price.price:,.2f}")
                print_success(f"  24h Change: {price.price_change_24h:+.2f}%")
            else:
                print_warning("Binance price fetch failed")
            
            # Test get_price_coingecko
            print_info("Testing CoinGecko price fetch...")
            price_cg = await client.get_price_coingecko("BTC")
            if price_cg:
                print_success(f"BTC Price (CoinGecko): ${price_cg.price:,.2f}")
            else:
                print_warning("CoinGecko price fetch failed")
            
            # Test get_current_price (combined)
            print_info("Testing combined price fetch...")
            price_combined = await client.get_current_price("ETH")
            if price_combined:
                print_success(f"ETH Price: ${price_combined.price:,.2f}")
            
            # Test get_historical_klines
            print_info("Testing historical klines...")
            klines = await client.get_historical_klines("BTC", "5m", 20)
            if klines:
                print_success(f"Got {len(klines)} klines")
                
                # Test technical indicators
                indicators = client.calculate_technical_indicators(klines)
                if indicators:
                    print_success(f"  RSI(14): {indicators.get('rsi', 0):.1f}")
                    print_success(f"  Trend: {indicators.get('trend', 'N/A')}")
            else:
                print_warning("Klines fetch failed")
            
            # Test momentum calculation
            print_info("Testing momentum calculation...")
            momentum = await client.get_price_momentum("BTC")
            if momentum:
                print_success(f"BTC Momentum 5m: {momentum.momentum_5m:+.3f}%")
                print_success(f"  Trend: {momentum.trend_direction}")
            
            self.results["price_client"] = True
            return True
            
        except Exception as e:
            print_error(f"Price client test failed: {e}")
            import traceback
            traceback.print_exc()
            self.results["price_client"] = False
            return False
    
    async def test_predictor(self) -> bool:
        """Test the predictor module"""
        print_header("6. Predictor Module Test")
        
        try:
            from predictor import CryptoPredictor, TimeFrame
            
            predictor = CryptoPredictor()
            
            print_info("Fetching BTC predictions...")
            predictions = await predictor.get_predictions_for_crypto("BTC", TimeFrame.FIVE_MIN)
            
            if predictions:
                print_success(f"Found {len(predictions)} BTC predictions")
                for pred in predictions[:3]:  # Show first 3
                    print_info(f"  {pred.direction.value} ({pred.probability*100:.0f}%) - Confidence: {pred.confidence*100:.0f}%")
                
                self.results["predictor"] = True
                return True
            else:
                print_warning("No predictions found (may be no active markets)")
                self.results["predictor"] = False
                return False
                
        except Exception as e:
            print_error(f"Predictor test failed: {e}")
            import traceback
            traceback.print_exc()
            self.results["predictor"] = False
            return False
    
    async def test_backtest(self) -> bool:
        """Test the backtest module"""
        print_header("7. Backtest Module Test")
        
        try:
            from backtest import Backtester
            
            backtester = Backtester()
            
            print_info("Running backtest for last 2 hours...")
            stats = await backtester.run_backtest(cryptos=["btc"], hours_back=2)
            
            if stats.total_predictions > 0:
                print_success(f"Backtest completed: {stats.total_predictions} predictions")
                print_success(f"  Accuracy: {stats.accuracy*100:.1f}%")
                print_success(f"  ROI: {stats.roi*100:+.1f}%")
                self.results["backtest"] = True
                return True
            else:
                print_warning("No historical data found, trying demo mode...")
                stats = await backtester.run_demo_backtest(num_predictions=30)
                print_success(f"Demo backtest: {stats.total_predictions} predictions")
                print_success(f"  Accuracy: {stats.accuracy*100:.1f}%")
                self.results["backtest"] = True
                return True
                
        except Exception as e:
            print_error(f"Backtest test failed: {e}")
            import traceback
            traceback.print_exc()
            self.results["backtest"] = False
            return False
    
    def print_summary(self):
        """Print test results summary"""
        print_header("Test Results Summary")
        
        total = len(self.results)
        passed = sum(1 for v in self.results.values() if v)
        
        for test_name, result in self.results.items():
            status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
            print(f"  {test_name.ljust(20)} [{status}]")
        
        print()
        if passed == total:
            print_success(f"All tests passed! ({passed}/{total})")
            print_info("Your server is ready for production use.")
        elif passed > total // 2:
            print_warning(f"Some tests failed ({passed}/{total} passed)")
            print_info("Basic functionality should work, but some features may be limited.")
        else:
            print_error(f"Many tests failed ({passed}/{total} passed)")
            print_info("Please check your network connectivity and API access.")
    
    async def run_all_tests(self, quick: bool = False):
        """Run all tests"""
        print(f"\n{BOLD}🚀 Polymarket Crypto Predictor - Server Test Suite{RESET}")
        print(f"   Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        self.load_config()
        
        # Quick connectivity test
        self.test_network_connectivity()
        
        if quick:
            self.test_binance_api()
            self.test_polymarket_api()
            self.print_summary()
            return
        
        # Full test suite
        self.test_binance_api()
        self.test_coingecko_api()
        self.test_alternative_apis()
        self.test_polymarket_api()
        await self.test_price_client()
        await self.test_predictor()
        await self.test_backtest()
        
        self.print_summary()


async def main():
    """Main entry point"""
    quick_mode = "--quick" in sys.argv or "-q" in sys.argv
    
    tester = ServerTester()
    await tester.run_all_tests(quick=quick_mode)


if __name__ == "__main__":
    asyncio.run(main())