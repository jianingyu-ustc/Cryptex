"""
Configuration for spot auto-trading subsystem.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

# Load .env if present.
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())


@dataclass
class SpotTradingConfig:
    """Spot auto-trading runtime configuration."""

    binance_api_key: str = field(default_factory=lambda: os.environ.get("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.environ.get("BINANCE_API_SECRET", ""))

    binance_spot_base: str = "https://api.binance.com"
    binance_futures_base: str = "https://fapi.binance.com"
    binance_delivery_base: str = "https://dapi.binance.com"
    binance_spot_ws: str = "wss://stream.binance.com:9443/ws"
    binance_futures_ws: str = "wss://fstream.binance.com/ws"
    ws_reconnect_delay: int = 5
    max_reconnect_attempts: int = 10

    symbols: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"
    ])

    # Strategy parameters
    kline_interval: str = "15m"
    fast_ma_period: int = 9
    slow_ma_period: int = 21
    rsi_period: int = 14
    rsi_buy_max: float = 68.0
    rsi_sell_min: float = 45.0
    min_24h_quote_volume: float = 20_000_000.0

    # Risk and execution controls
    initial_capital: float = 10_000.0
    usdt_per_trade: float = 100.0
    max_open_positions: int = 3
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    max_daily_trades: int = 50

    # Runtime controls
    check_interval: int = 30
    dry_run: bool = field(default_factory=lambda: os.environ.get("SPOT_DRY_RUN", "true").lower() == "true")

    @property
    def min_klines_required(self) -> int:
        return max(self.slow_ma_period + 5, self.rsi_period + 5, 30)

    def validate(self) -> bool:
        if self.initial_capital <= 0:
            print("❌ Spot initial capital must be > 0")
            return False
        if not self.dry_run and (not self.binance_api_key or not self.binance_api_secret):
            print("❌ Spot live mode requires BINANCE_API_KEY and BINANCE_API_SECRET")
            return False
        return True


DEFAULT_SPOT_CONFIG = SpotTradingConfig()
