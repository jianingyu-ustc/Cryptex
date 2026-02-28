"""
Polymarket Crypto Predictor Configuration
"""
import os
from pathlib import Path

# Load .env file if exists (simple dotenv loading without external dependency)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _value = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _value.strip())

# API Endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# Proxy settings (set environment variable if needed)
HTTP_PROXY = os.environ.get("HTTP_PROXY", "")
HTTPS_PROXY = os.environ.get("HTTPS_PROXY", "")

# Demo mode - use simulated data when API is unavailable
DEMO_MODE = os.environ.get("POLYMARKET_DEMO_MODE", "true").lower() == "true"

# Crypto market settings
CRYPTO_TAGS = ["crypto", "bitcoin", "ethereum", "cryptocurrency"]
SUPPORTED_CRYPTOS = ["BTC", "ETH", "SOL", "DOGE", "XRP"]

# Time windows for prediction
TIME_WINDOWS = {
    "5min": 5,
    "15min": 15,
    "1hour": 60,
    "4hour": 240,
    "daily": 1440
}

# API rate limiting
REQUEST_DELAY = 0.5  # seconds between requests
MAX_RETRIES = 3

# External Price API Configuration
# Binance API (set via environment variable for security)
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BINANCE_API_BASE = "https://api.binance.com/api/v3"

# CoinGecko API
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"

# OKX API (global exchange, fewer restrictions than Binance)
OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET", "")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")
OKX_API_BASE = "https://www.okx.com/api/v5"

# Polymarket CLOB API (L2 Authentication)
# Generate keys at: https://polymarket.com -> Settings -> API Keys
POLY_API_KEY = os.environ.get("POLY_API_KEY", "")
POLY_API_SECRET = os.environ.get("POLY_API_SECRET", "")
POLY_API_PASSPHRASE = os.environ.get("POLY_API_PASSPHRASE", "")
POLY_PROXY_WALLET = os.environ.get("POLY_PROXY_WALLET", "")  # Your Gnosis Safe proxy wallet
CLOB_API_BASE = "https://clob.polymarket.com"

# Display settings
TABLE_WIDTH = 120
REFRESH_INTERVAL = 30  # seconds