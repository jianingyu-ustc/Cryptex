"""
Polymarket CLOB API Client with L2 Authentication

Based on: https://docs.polymarket.com/cn/api-reference/authentication

L2 Authentication requires HMAC-SHA256 signature in headers:
- POLY_API_KEY: Your API key
- POLY_TIMESTAMP: Unix timestamp (seconds)
- POLY_SIGNATURE: HMAC-SHA256(timestamp + method + path + body)
- POLY_PASSPHRASE: Your API passphrase
"""

import time
import hmac
import hashlib
import base64
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import httpx

from config import (
    CLOB_API_BASE,
    POLY_API_KEY,
    POLY_API_SECRET,
    POLY_API_PASSPHRASE,
    POLY_PROXY_WALLET
)


@dataclass
class ApiCreds:
    """Polymarket API Credentials"""
    api_key: str
    api_secret: str
    api_passphrase: str


@dataclass
class Balance:
    """Account balance info"""
    balance: float
    allowance: float
    currency: str = "USDC"


@dataclass
class Order:
    """Order info"""
    id: str
    market: str
    asset_id: str
    side: str  # BUY or SELL
    price: float
    size: float
    size_matched: float
    status: str
    created_at: str


class PolymarketClobClient:
    """
    Polymarket CLOB API Client with L2 Authentication
    
    Usage:
        # Using environment variables
        client = PolymarketClobClient()
        
        # Or with explicit credentials
        creds = ApiCreds(api_key='...', api_secret='...', api_passphrase='...')
        client = PolymarketClobClient(creds=creds)
        
        # Get balance
        balance = await client.get_balance()
        print(f"Balance: ${balance.balance}")
    """
    
    def __init__(
        self, 
        creds: Optional[ApiCreds] = None,
        host: str = CLOB_API_BASE
    ):
        """
        Initialize CLOB client
        
        Args:
            creds: API credentials (uses env vars if not provided)
            host: CLOB API base URL
        """
        self.host = host.rstrip('/')
        
        # Use provided creds or load from environment
        if creds:
            self.api_key = creds.api_key
            self.api_secret = creds.api_secret
            self.api_passphrase = creds.api_passphrase
        else:
            self.api_key = POLY_API_KEY
            self.api_secret = POLY_API_SECRET
            self.api_passphrase = POLY_API_PASSPHRASE
        
        self.proxy_wallet = POLY_PROXY_WALLET
        
        # Validate credentials
        self._has_creds = bool(self.api_key and self.api_secret and self.api_passphrase)
    
    def _create_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """
        Create L2 HMAC-SHA256 signature
        
        Signature = Base64(HMAC-SHA256(secret, timestamp + method + path + body))
        
        Note: The secret can be either:
        - A base64-encoded string (needs decoding)
        - A raw string (use as-is)
        """
        message = timestamp + method.upper() + path + body
        
        # Try different secret formats
        # Format 1: Secret is already base64, decode it first
        try:
            # Add padding if needed
            padded_secret = self.api_secret + "=" * (4 - len(self.api_secret) % 4)
            secret_bytes = base64.urlsafe_b64decode(padded_secret)
        except Exception:
            # Format 2: Secret is raw string, use as UTF-8 bytes
            secret_bytes = self.api_secret.encode('utf-8')
        
        signature = hmac.new(
            secret_bytes,
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        return base64.urlsafe_b64encode(signature).decode('utf-8').rstrip('=')
    
    def _get_auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """
        Generate authentication headers for L2 request
        
        Headers:
        - POLY_API_KEY
        - POLY_TIMESTAMP
        - POLY_SIGNATURE
        - POLY_PASSPHRASE
        """
        timestamp = str(int(time.time()))
        signature = self._create_signature(timestamp, method, path, body)
        
        return {
            "POLY_API_KEY": self.api_key,
            "POLY_TIMESTAMP": timestamp,
            "POLY_SIGNATURE": signature,
            "POLY_PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
        }
    
    async def _request(
        self, 
        method: str, 
        path: str, 
        body: Optional[Dict] = None,
        authenticated: bool = True
    ) -> Optional[Dict]:
        """
        Make HTTP request to CLOB API
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            path: API path (e.g., /balance)
            body: Request body (for POST)
            authenticated: Whether to include auth headers
        
        Returns:
            Response JSON or None on error
        """
        url = f"{self.host}{path}"
        body_json = json.dumps(body) if body else ""
        
        headers = {"Content-Type": "application/json"}
        if authenticated and self._has_creds:
            headers = self._get_auth_headers(method, path, body_json)
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method.upper() == "GET":
                    response = await client.get(url, headers=headers)
                elif method.upper() == "POST":
                    response = await client.post(url, headers=headers, content=body_json)
                elif method.upper() == "DELETE":
                    response = await client.delete(url, headers=headers)
                else:
                    return None
                
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            print(f"HTTP Error {e.response.status_code} on {path}: {e.response.text[:200]}")
            return None
        except Exception as e:
            print(f"Request error on {path}: {e}")
            return None
    
    # ==================== Public Endpoints ====================
    
    async def get_server_time(self) -> Optional[int]:
        """Get server time (no auth required)"""
        result = await self._request("GET", "/time", authenticated=False)
        # API may return int directly or {"time": int}
        if result is None:
            return None
        if isinstance(result, int):
            return result
        if isinstance(result, dict):
            return result.get("time")
        return None
    
    async def get_markets(self, next_cursor: str = "") -> Optional[Dict]:
        """
        Get all markets (no auth required)
        
        Returns:
            {
                "markets": [...],
                "next_cursor": "..."
            }
        """
        path = "/markets"
        if next_cursor:
            path = f"/markets?next_cursor={next_cursor}"
        return await self._request("GET", path, authenticated=False)
    
    async def get_market(self, condition_id: str) -> Optional[Dict]:
        """Get single market by condition ID (no auth required)"""
        return await self._request("GET", f"/markets/{condition_id}", authenticated=False)
    
    async def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        Get orderbook for a token (no auth required)
        
        Returns:
            {
                "market": "...",
                "asset_id": "...",
                "bids": [{"price": "0.50", "size": "100"}, ...],
                "asks": [{"price": "0.55", "size": "50"}, ...],
                "hash": "..."
            }
        """
        return await self._request("GET", f"/book?token_id={token_id}", authenticated=False)
    
    async def get_price(self, token_id: str, side: str = "BUY") -> Optional[Dict]:
        """
        Get current price for a token
        
        Args:
            token_id: Token/asset ID
            side: BUY or SELL
        """
        return await self._request(
            "GET", 
            f"/price?token_id={token_id}&side={side}", 
            authenticated=False
        )
    
    # ==================== Authenticated Endpoints ====================
    
    async def get_balance(self) -> Optional[Balance]:
        """
        Get account USDC balance (requires auth)
        
        Note: Uses /data/balance endpoint per CLOB API spec
        
        Returns:
            Balance object with balance and allowance
        """
        if not self._has_creds:
            print("Error: API credentials not configured")
            return None
        
        # Try multiple possible endpoints
        for endpoint in ["/data/balance", "/balance"]:
            result = await self._request("GET", endpoint)
            if result and isinstance(result, dict):
                if "balance" in result:
                    return Balance(
                        balance=float(result.get("balance", 0)),
                        allowance=float(result.get("allowance", 0))
                    )
        return None
    
    async def get_open_orders(self, market: str = "") -> Optional[List[Order]]:
        """
        Get open orders (requires auth)
        
        Args:
            market: Optional market/condition ID filter
        """
        if not self._has_creds:
            print("Error: API credentials not configured")
            return None
        
        path = "/orders"
        if market:
            path = f"/orders?market={market}"
        
        result = await self._request("GET", path)
        if result and isinstance(result, list):
            return [
                Order(
                    id=o.get("id", ""),
                    market=o.get("market", ""),
                    asset_id=o.get("asset_id", ""),
                    side=o.get("side", ""),
                    price=float(o.get("price", 0)),
                    size=float(o.get("original_size", 0)),
                    size_matched=float(o.get("size_matched", 0)),
                    status=o.get("status", ""),
                    created_at=o.get("created_at", "")
                )
                for o in result
            ]
        return []
    
    async def get_trades(self, market: str = "") -> Optional[List[Dict]]:
        """
        Get trade history (requires auth)
        
        Args:
            market: Optional market/condition ID filter
        """
        if not self._has_creds:
            print("Error: API credentials not configured")
            return None
        
        path = "/trades"
        if market:
            path = f"/trades?market={market}"
        
        return await self._request("GET", path)
    
    async def get_positions(self) -> Optional[List[Dict]]:
        """Get current positions (requires auth)"""
        if not self._has_creds:
            print("Error: API credentials not configured")
            return None
        
        return await self._request("GET", "/positions")
    
    # ==================== Trading Endpoints ====================
    
    async def create_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC"
    ) -> Optional[Dict]:
        """
        Create a new order (requires auth)
        
        Args:
            token_id: Asset/token ID to trade
            side: BUY or SELL
            price: Limit price (0-1 for prediction markets)
            size: Amount in USDC
            order_type: GTC (Good Till Cancelled) or FOK (Fill or Kill)
        
        Returns:
            Order creation result
        """
        if not self._has_creds:
            print("Error: API credentials not configured")
            return None
        
        body = {
            "tokenID": token_id,
            "side": side.upper(),
            "price": str(price),
            "size": str(size),
            "type": order_type
        }
        
        return await self._request("POST", "/order", body=body)
    
    async def cancel_order(self, order_id: str) -> Optional[Dict]:
        """Cancel an order by ID (requires auth)"""
        if not self._has_creds:
            print("Error: API credentials not configured")
            return None
        
        return await self._request("DELETE", f"/order/{order_id}")
    
    async def cancel_all_orders(self, market: str = "") -> Optional[Dict]:
        """
        Cancel all open orders (requires auth)
        
        Args:
            market: Optional market filter
        """
        if not self._has_creds:
            print("Error: API credentials not configured")
            return None
        
        body = {}
        if market:
            body["market"] = market
        
        return await self._request("DELETE", "/orders", body=body if body else None)
    
    # ==================== Utility Methods ====================
    
    def is_authenticated(self) -> bool:
        """Check if client has valid credentials configured"""
        return self._has_creds
    
    async def test_connection(self) -> Dict[str, Any]:
        """
        Test API connection and authentication
        
        Returns:
            {
                "server_time": int or None,
                "authenticated": bool,
                "balance": float or None,
                "error": str or None
            }
        """
        result = {
            "server_time": None,
            "authenticated": False,
            "balance": None,
            "error": None
        }
        
        # Test public endpoint
        try:
            server_time = await self.get_server_time()
            result["server_time"] = server_time
            if server_time is None:
                result["error"] = "Could not get server time - API may be unavailable"
        except Exception as e:
            result["error"] = f"Server connection failed: {e}"
            return result
        
        # Test authenticated endpoint
        if self._has_creds:
            try:
                balance = await self.get_balance()
                if balance:
                    result["authenticated"] = True
                    result["balance"] = balance.balance
                else:
                    result["error"] = "Authentication failed - check API credentials"
            except Exception as e:
                result["error"] = f"Authentication error: {e}"
        else:
            result["error"] = "No API credentials configured"
        
        return result


# ==================== Convenience Functions ====================

async def get_polymarket_balance(
    api_key: str = "",
    api_secret: str = "",
    api_passphrase: str = ""
) -> Optional[float]:
    """
    Quick function to get Polymarket balance
    
    Args:
        api_key: API key (uses env var if not provided)
        api_secret: API secret (uses env var if not provided)
        api_passphrase: API passphrase (uses env var if not provided)
    
    Returns:
        Balance in USDC or None on error
    """
    if api_key and api_secret and api_passphrase:
        creds = ApiCreds(api_key, api_secret, api_passphrase)
        client = PolymarketClobClient(creds=creds)
    else:
        client = PolymarketClobClient()
    
    balance = await client.get_balance()
    return balance.balance if balance else None


# ==================== CLI Test ====================

async def main():
    """Test CLOB client"""
    print("=" * 60)
    print("Polymarket CLOB API Test")
    print("=" * 60)
    
    client = PolymarketClobClient()
    
    # Show loaded credentials (masked)
    print("\n[0] Credentials loaded:")
    print(f"  API Key: {client.api_key[:8]}...{client.api_key[-4:] if len(client.api_key) > 12 else ''}")
    print(f"  API Secret: {client.api_secret[:8]}...{client.api_secret[-4:] if len(client.api_secret) > 12 else ''}")
    print(f"  Passphrase: {client.api_passphrase[:8]}...{client.api_passphrase[-4:] if len(client.api_passphrase) > 12 else ''}")
    print(f"  Proxy Wallet: {client.proxy_wallet}")
    print(f"  Has credentials: {client._has_creds}")
    
    # Test connection
    print("\n[1] Testing connection...")
    result = await client.test_connection()
    
    print(f"  Server Time: {result['server_time']}")
    print(f"  Authenticated: {result['authenticated']}")
    
    if result['balance'] is not None:
        print(f"  💰 Balance: ${result['balance']:.2f} USDC")
    
    if result['error']:
        print(f"  ❌ Error: {result['error']}")
    
    # Test public endpoints
    print("\n[2] Testing public endpoints...")
    
    # Get markets
    markets_data = await client.get_markets()
    if markets_data and "markets" in markets_data:
        print(f"  Markets found: {len(markets_data['markets'])}")
    else:
        # Try /markets endpoint without next_cursor
        print("  Note: Markets endpoint may have different structure")
    
    # Test using convenience function
    print("\n[3] Testing get_polymarket_balance()...")
    balance = await get_polymarket_balance()
    if balance is not None:
        print(f"  💰 Balance via convenience function: ${balance:.2f} USDC")
    else:
        print("  ❌ Could not get balance via custom client")
    
    # Test official SDK
    print("\n[4] Testing official py-clob-client SDK...")
    try:
        from py_clob_client.client import ClobClient
        import os
        
        private_key = os.environ.get("POLY_PRIVATE_KEY", "")
        proxy_wallet = client.proxy_wallet
        
        if private_key:
            sdk_client = ClobClient(
                host="https://clob.polymarket.com",
                key=private_key,
                chain_id=137,
                signature_type=2,  # 2=Gnosis Safe
                funder=proxy_wallet
            )
            api_creds = sdk_client.create_or_derive_api_creds()
            sdk_client.set_api_creds(api_creds)
            
            result = sdk_client.get_balance_allowance()
            if result:
                print(f"  💰 Balance (SDK): ${float(result.get('balance', 0)):.2f} USDC")
            else:
                print("  ⚠️  SDK returned empty response")
        else:
            print("  ⚠️  POLY_PRIVATE_KEY not configured in .env")
            print("     Add your EOA private key to use official SDK:")
            print("     POLY_PRIVATE_KEY=0x...")
            
    except ImportError:
        print("  ⚠️  py-clob-client not installed")
        print("     Install: pip install py-clob-client")
    except Exception as e:
        print(f"  ❌ SDK error: {e}")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("\n📝 Note:")
    print("   Polymarket CLOB API requires credentials derived from private key.")
    print("   Install SDK: pip install py-clob-client")
    print("   Then add POLY_PRIVATE_KEY=0x... to .env")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())