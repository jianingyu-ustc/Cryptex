"""
Arbitrage System Configuration
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Load .env file
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _value = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _value.strip())


@dataclass
class ArbitrageConfig:
    """Arbitrage system configuration"""
    
    # ===========================================
    # API Configuration
    # ===========================================
    
    # Binance API
    binance_api_key: str = field(default_factory=lambda: os.environ.get("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.environ.get("BINANCE_API_SECRET", ""))
    
    # API Endpoints
    binance_spot_base: str = "https://api.binance.com"
    binance_futures_base: str = "https://fapi.binance.com"  # USDT-M Futures
    binance_delivery_base: str = "https://dapi.binance.com"  # Coin-M Futures
    
    # WebSocket Endpoints
    binance_spot_ws: str = "wss://stream.binance.com:9443/ws"
    binance_futures_ws: str = "wss://fstream.binance.com/ws"
    
    # ===========================================
    # Trading Cost Configuration (%)
    # ===========================================
    
    # Fee rates (in percentage)
    taker_fee: float = 0.04  # 0.04%
    maker_fee: float = 0.02  # 0.02%
    slippage: float = 0.01   # 0.01% estimated slippage
    
    # Total cost per trade (one-way)
    @property
    def total_cost_per_trade(self) -> float:
        """Total cost for a single trade (taker + slippage)"""
        return self.taker_fee + self.slippage  # 0.05%
    
    @property
    def round_trip_cost(self) -> float:
        """Total cost for opening and closing a position"""
        return self.total_cost_per_trade * 2  # 0.10%
    
    # ===========================================
    # Strategy Parameters
    # ===========================================
    
    # Funding Rate Arbitrage
    funding_rate_threshold: float = 0.03  # 0.03% minimum funding rate
    funding_negative_exit: bool = True    # Exit when funding turns negative
    
    # Basis Arbitrage (Cash & Carry)
    basis_annualized_threshold: float = 15.0  # 15% minimum annualized return
    basis_days_before_expiry_exit: int = 3    # Exit 3 days before expiry
    
    # Stablecoin Arbitrage
    stablecoin_spread_threshold: float = 0.5  # 0.5% minimum spread
    stablecoins: List[str] = field(default_factory=lambda: ["USDT", "USDC", "BUSD", "DAI"])
    
    # ===========================================
    # Risk Control Parameters
    # ===========================================
    
    # Position limits
    max_position_pct: float = 50.0        # Max 50% of account balance
    max_single_strategy_pct: float = 25.0 # Max 25% per strategy
    
    # Stop loss
    stop_loss_pct: float = 3.0            # 3% stop loss
    
    # Liquidation protection
    min_margin_ratio: float = 5.0         # Minimum 5% margin ratio
    
    # ===========================================
    # Supported Trading Pairs
    # ===========================================
    
    spot_symbols: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
    ])
    
    perpetual_symbols: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
    ])
    
    # ===========================================
    # System Configuration
    # ===========================================
    
    # Logging
    log_level: str = "INFO"
    log_file: str = "arbitrage.log"
    
    # Refresh intervals (seconds)
    market_data_refresh: int = 1
    strategy_check_interval: int = 5
    risk_check_interval: int = 10
    
    # Reconnection settings
    ws_reconnect_delay: int = 5
    max_reconnect_attempts: int = 10
    
    def validate(self) -> bool:
        """Validate configuration"""
        if not self.binance_api_key or not self.binance_api_secret:
            print("⚠️  Warning: Binance API credentials not configured")
            print("   Set BINANCE_API_KEY and BINANCE_API_SECRET in .env file")
            return False
        return True
    
    def get_fee_adjusted_profit(self, gross_profit_pct: float, num_trades: int = 2) -> float:
        """
        Calculate net profit after fees and slippage
        
        Args:
            gross_profit_pct: Gross profit percentage
            num_trades: Number of trades (default 2 for open + close)
        
        Returns:
            Net profit percentage
        """
        total_fees = self.total_cost_per_trade * num_trades
        return gross_profit_pct - total_fees


# ===========================================
# Arbitrage Profit Formulas
# ===========================================

"""
📊 三种套利策略收益公式:

1️⃣ 资金费率套利 (Funding Rate Arbitrage)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
净收益 = Funding收益 - 交易成本
       = (资金费率 × 持仓价值) - (开仓手续费 + 平仓手续费 + 滑点)
       = FR × Position - 2 × (taker_fee + slippage) × Position
       = Position × [FR - 2 × (0.04% + 0.01%)]
       = Position × [FR - 0.10%]

条件: FR > 0.10% (需覆盖双向手续费)
推荐: FR > 0.20% (留有安全边际)

2️⃣ 期现套利 (Cash & Carry / Basis Arbitrage)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
基差 = (期货价格 - 现货价格) / 现货价格
年化收益率 = 基差 × (365 / 到期天数) × 100%
           = [(F - S) / S] × (365 / D) × 100%

净年化收益 = 年化收益率 - 年化交易成本
           = 年化收益率 - [2 × (0.04% + 0.01%) × (365 / D)]

条件: 净年化收益 > 10% (考虑资金占用)
推荐: 净年化收益 > 15%

3️⃣ 稳定币价差套利 (Stablecoin Spread Arbitrage)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
价差收益 = (高价稳定币价格 - 低价稳定币价格) / 低价稳定币价格 × 100%
         = (P_high - P_low) / P_low × 100%

净收益 = 价差收益 - 交易成本
       = Spread - 2 × (taker_fee + slippage)
       = Spread - 2 × 0.05%
       = Spread - 0.10%

条件: Spread > 0.10%
推荐: Spread > 0.50% (确保明显利润)

📝 交易成本假设:
- Taker Fee: 0.04%
- Maker Fee: 0.02%
- Slippage: 0.01%
- 单向成本: 0.05% (taker + slippage)
- 双向成本: 0.10% (开仓 + 平仓)
"""


# Global configuration instance
DEFAULT_CONFIG = ArbitrageConfig()