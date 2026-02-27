"""
Polymarket API Client for Crypto Markets
"""

import asyncio
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime
from config import GAMMA_API_BASE, DATA_API_BASE, REQUEST_DELAY, MAX_RETRIES, DEMO_MODE, HTTP_PROXY, HTTPS_PROXY


class PolymarketClient:
    """Client for interacting with Polymarket APIs"""
    
    def __init__(self, demo_mode: bool = DEMO_MODE):
        self.gamma_base = GAMMA_API_BASE
        self.data_base = DATA_API_BASE
        self._last_request_time = 0
        self._demo_mode = demo_mode
        self._api_available = None  # Cache API availability
        self._demo_markets = None
        
        # Set up proxy if configured
        self._proxies = {}
        if HTTP_PROXY:
            self._proxies["http://"] = HTTP_PROXY
        if HTTPS_PROXY:
            self._proxies["https://"] = HTTPS_PROXY
        
    async def _rate_limit(self):
        """Apply rate limiting between requests"""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < REQUEST_DELAY:
            await asyncio.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()
    
    async def _make_request(self, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make HTTP request with retry logic"""
        await self._rate_limit()
        
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(
                    timeout=30.0,
                    follow_redirects=True,
                    proxy=self._proxies.get("https://") if self._proxies else None,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                        "Accept": "application/json",
                    }
                ) as client:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                print(f"HTTP error {e.response.status_code} for {url}: {e}")
                if attempt == MAX_RETRIES - 1:
                    return None
            except httpx.ConnectError as e:
                print(f"Connection error for {url}: {e}")
                if attempt == MAX_RETRIES - 1:
                    return None
            except httpx.RequestError as e:
                print(f"Request error for {url}: {type(e).__name__} - {e}")
                if attempt == MAX_RETRIES - 1:
                    return None
            except Exception as e:
                print(f"Unexpected error for {url}: {type(e).__name__} - {e}")
                if attempt == MAX_RETRIES - 1:
                    return None
            await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
        return None
    
    async def search_markets(
        self, 
        tag: Optional[str] = None,
        keyword: Optional[str] = None,
        active: bool = True,
        limit: int = 100,
        order: str = "volume24hr",
        ascending: bool = False
    ) -> List[Dict]:
        """
        Search Polymarket markets with filters
        
        Args:
            tag: Filter by tag (e.g., "crypto", "bitcoin")
            keyword: Search keyword
            active: Only return active markets
            limit: Maximum results to return
            order: Field to order by
            ascending: Sort direction
        
        Returns:
            List of market dictionaries
        """
        params = {
            "limit": limit,
            "order": order,
            "ascending": str(ascending).lower(),
            "active": str(active).lower()
        }
        
        if tag:
            params["tag"] = tag
        if keyword:
            params["_q"] = keyword
            
        url = f"{self.gamma_base}/markets"
        result = await self._make_request(url, params)
        return result if result else []
    
    async def get_market_by_slug(self, slug: str) -> Optional[Dict]:
        """Get detailed market info by slug"""
        url = f"{self.gamma_base}/markets/{slug}"
        return await self._make_request(url)
    
    async def get_market_by_id(self, condition_id: str) -> Optional[Dict]:
        """Get market by condition ID"""
        params = {"id": condition_id}
        url = f"{self.gamma_base}/markets"
        result = await self._make_request(url, params)
        if result and len(result) > 0:
            return result[0]
        return None
    
    async def search_events(
        self,
        tag: Optional[str] = None,
        active: bool = True,
        limit: int = 50
    ) -> List[Dict]:
        """Search Polymarket events (grouped markets)"""
        params = {
            "limit": limit,
            "active": str(active).lower()
        }
        if tag:
            params["tag"] = tag
            
        url = f"{self.gamma_base}/events"
        result = await self._make_request(url, params)
        return result if result else []
    
    async def get_event_by_slug(self, slug: str) -> Optional[Dict]:
        """Get event details by slug"""
        url = f"{self.gamma_base}/events/{slug}"
        return await self._make_request(url)
    
    async def get_trades(
        self,
        market: Optional[str] = None,
        limit: int = 100,
        side: Optional[str] = None
    ) -> List[Dict]:
        """
        Get recent trades
        
        Args:
            market: Filter by market condition ID
            limit: Maximum trades to return
            side: Filter by trade side ("BUY" or "SELL")
        
        Returns:
            List of trade dictionaries
        """
        params = {"limit": limit}
        if market:
            params["market"] = market
        if side:
            params["side"] = side
            
        url = f"{self.data_base}/trades"
        result = await self._make_request(url, params)
        return result if result else []
    
    async def list_tags(self, limit: int = 100) -> List[Dict]:
        """List all available market tags/categories"""
        params = {"limit": limit}
        url = f"{self.gamma_base}/tags"
        result = await self._make_request(url, params)
        return result if result else []
    
    async def check_api_availability(self) -> bool:
        """Check if Polymarket API is accessible"""
        if self._api_available is not None:
            return self._api_available
        
        try:
            result = await self._make_request(f"{self.gamma_base}/markets", {"limit": 1})
            self._api_available = result is not None and len(result) > 0
        except:
            self._api_available = False
        
        return self._api_available
    
    async def get_crypto_markets(self) -> List[Dict]:
        """Get all crypto-related markets"""
        # Check if we should use demo mode
        if self._demo_mode:
            api_available = await self.check_api_availability()
            if not api_available:
                print("📡 API unavailable, using demo data...")
                from demo_data import get_demo_crypto_markets
                if self._demo_markets is None:
                    self._demo_markets = get_demo_crypto_markets()
                return self._demo_markets
        
        crypto_keywords = ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "doge"]
        all_markets = []
        seen_ids = set()
        
        # Search by crypto tag first
        tag_markets = await self.search_markets(tag="crypto", limit=100)
        for market in tag_markets:
            market_id = market.get("id") or market.get("conditionId")
            if market_id and market_id not in seen_ids:
                seen_ids.add(market_id)
                all_markets.append(market)
        
        # Then search by keywords
        for keyword in crypto_keywords:
            keyword_markets = await self.search_markets(keyword=keyword, limit=50)
            for market in keyword_markets:
                market_id = market.get("id") or market.get("conditionId")
                if market_id and market_id not in seen_ids:
                    seen_ids.add(market_id)
                    all_markets.append(market)
        
        # If no markets found but demo mode is enabled, use demo data
        if not all_markets and self._demo_mode:
            print("📡 No markets found, using demo data...")
            from demo_data import get_demo_crypto_markets
            if self._demo_markets is None:
                self._demo_markets = get_demo_crypto_markets()
            return self._demo_markets
        
        return all_markets
    
    async def get_short_term_crypto_markets(self) -> List[Dict]:
        """Get short-term (5min/15min/1hour) crypto markets"""
        all_crypto = await self.get_crypto_markets()
        
        short_term_keywords = ["5 min", "15 min", "1 hour", "hourly", "minute"]
        short_term_markets = []
        
        for market in all_crypto:
            question = market.get("question", "").lower()
            description = market.get("description", "").lower()
            
            for keyword in short_term_keywords:
                if keyword in question or keyword in description:
                    short_term_markets.append(market)
                    break
        
        return short_term_markets


class MarketAnalyzer:
    """Analyze market data for predictions"""
    
    def __init__(self, client: PolymarketClient):
        self.client = client
    
    @staticmethod
    def parse_probability(market: Dict) -> Dict[str, float]:
        """
        Parse outcome probabilities from market data
        
        Returns dict with 'yes' and 'no' probabilities
        """
        outcomes = {}
        
        # Try different data formats
        if "outcomePrices" in market:
            prices = market["outcomePrices"]
            if isinstance(prices, str):
                # Parse JSON string if needed
                import json
                try:
                    prices = json.loads(prices)
                except:
                    pass
            
            if isinstance(prices, list) and len(prices) >= 2:
                outcomes["yes"] = float(prices[0])
                outcomes["no"] = float(prices[1])
        
        elif "tokens" in market:
            for token in market.get("tokens", []):
                outcome = token.get("outcome", "").lower()
                price = float(token.get("price", 0))
                outcomes[outcome] = price
        
        # Default probabilities if not found
        if not outcomes:
            outcomes = {"yes": 0.5, "no": 0.5}
            
        return outcomes
    
    @staticmethod
    def get_market_health(market: Dict) -> Dict[str, Any]:
        """
        Calculate market health indicators
        
        Returns dict with liquidity, volume, and health score
        """
        volume_24h = float(market.get("volume24hr", 0) or 0)
        liquidity = float(market.get("liquidity", 0) or 0)
        volume_total = float(market.get("volume", 0) or 0)
        
        # Calculate health score (0-100)
        health_score = 0
        
        # Volume component (max 40 points)
        if volume_24h > 100000:
            health_score += 40
        elif volume_24h > 10000:
            health_score += 30
        elif volume_24h > 1000:
            health_score += 20
        elif volume_24h > 100:
            health_score += 10
        
        # Liquidity component (max 40 points)
        if liquidity > 50000:
            health_score += 40
        elif liquidity > 10000:
            health_score += 30
        elif liquidity > 1000:
            health_score += 20
        elif liquidity > 100:
            health_score += 10
        
        # Activity component (max 20 points)
        if volume_total > 1000000:
            health_score += 20
        elif volume_total > 100000:
            health_score += 15
        elif volume_total > 10000:
            health_score += 10
        elif volume_total > 1000:
            health_score += 5
        
        return {
            "volume_24h": volume_24h,
            "liquidity": liquidity,
            "volume_total": volume_total,
            "health_score": health_score,
            "health_rating": "Excellent" if health_score >= 80 else
                           "Good" if health_score >= 60 else
                           "Fair" if health_score >= 40 else
                           "Poor"
        }
    
    async def analyze_market(self, market: Dict) -> Dict[str, Any]:
        """Comprehensive market analysis"""
        probabilities = self.parse_probability(market)
        health = self.get_market_health(market)
        
        # Get recent trades for sentiment
        condition_id = market.get("conditionId") or market.get("id")
        trades = []
        
        # Check if this is demo data
        if market.get("_demo_crypto"):
            from demo_data import get_demo_trades
            trades = get_demo_trades(condition_id, 50)
        elif condition_id:
            trades = await self.client.get_trades(market=condition_id, limit=50)
        
        # Calculate sentiment from trades
        buy_count = sum(1 for t in trades if t.get("side") == "BUY")
        sell_count = sum(1 for t in trades if t.get("side") == "SELL")
        total_trades = buy_count + sell_count
        
        sentiment = "Neutral"
        if total_trades > 0:
            buy_ratio = buy_count / total_trades
            if buy_ratio > 0.6:
                sentiment = "Bullish"
            elif buy_ratio < 0.4:
                sentiment = "Bearish"
        
        return {
            "market_id": condition_id,
            "question": market.get("question", "Unknown"),
            "probabilities": probabilities,
            "health": health,
            "sentiment": sentiment,
            "recent_trades": len(trades),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "end_date": market.get("endDate"),
            "created_at": market.get("createdAt")
        }