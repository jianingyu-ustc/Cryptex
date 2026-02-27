"""
Polymarket Crypto Predictor Configuration
"""
import os

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

# Display settings
TABLE_WIDTH = 120
REFRESH_INTERVAL = 30  # seconds