"""
Arbitrage Strategy Layer

Implements three arbitrage strategies:
1. FundingRateStrategy - Perpetual Funding Arbitrage
2. BasisArbitrageStrategy - Cash & Carry Arbitrage
3. StablecoinSpreadStrategy - Stablecoin Spread Arbitrage
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum

from .config import ArbitrageConfig, DEFAULT_CONFIG
from .api import BinanceClient, FundingRateData, FuturesContractData, TickerData

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Trading signal types"""
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    HEDGE = "HEDGE"
    NO_ACTION = "NO_ACTION"


@dataclass
class ArbitrageSignal:
    """Arbitrage trading signal"""
    strategy_name: str
    signal_type: SignalType
    symbol: str
    side: str                      # "BUY" or "SELL"
    quantity: float
    price: float
    reason: str
    expected_profit_pct: float
    net_profit_pct: float          # After fees
    confidence: float              # 0-1
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Additional details
    spot_price: float = 0
    futures_price: float = 0
    funding_rate: float = 0
    basis: float = 0
    spread: float = 0
    
    def is_profitable(self) -> bool:
        """Check if signal is profitable after fees"""
        return self.net_profit_pct > 0
    
    def to_dict(self) -> Dict:
        return {
            "strategy": self.strategy_name,
            "signal": self.signal_type.value,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "reason": self.reason,
            "expected_profit_pct": round(self.expected_profit_pct, 4),
            "net_profit_pct": round(self.net_profit_pct, 4),
            "confidence": round(self.confidence, 2),
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class StrategyState:
    """Strategy state tracking"""
    name: str
    is_active: bool = False
    current_position: float = 0
    entry_price: float = 0
    entry_time: Optional[datetime] = None
    total_pnl: float = 0
    trade_count: int = 0
    last_signal: Optional[ArbitrageSignal] = None
    
    # Position details
    spot_position: float = 0
    futures_position: float = 0
    hedge_ratio: float = 1.0


class BaseStrategy(ABC):
    """
    Abstract base class for arbitrage strategies
    
    All strategies must implement:
    - analyze(): Analyze market data and generate signals
    - get_entry_signal(): Generate entry signal
    - get_exit_signal(): Generate exit signal
    """
    
    def __init__(
        self, 
        client: BinanceClient,
        config: ArbitrageConfig = None,
        name: str = "BaseStrategy"
    ):
        self.client = client
        self.config = config or DEFAULT_CONFIG
        self.name = name
        self.state = StrategyState(name=name)
        self._last_analysis_time: Optional[datetime] = None
        self._market_data_cache: Dict[str, Any] = {}
    
    @abstractmethod
    async def analyze(self) -> List[ArbitrageSignal]:
        """
        Analyze market data and generate trading signals
        
        Returns:
            List of ArbitrageSignal objects
        """
        pass
    
    @abstractmethod
    async def get_entry_signal(self, symbol: str) -> Optional[ArbitrageSignal]:
        """
        Generate entry signal for a symbol
        
        Args:
            symbol: Trading symbol
            
        Returns:
            ArbitrageSignal if entry conditions are met, None otherwise
        """
        pass
    
    @abstractmethod
    async def get_exit_signal(self, symbol: str) -> Optional[ArbitrageSignal]:
        """
        Generate exit signal for a symbol
        
        Args:
            symbol: Trading symbol
            
        Returns:
            ArbitrageSignal if exit conditions are met, None otherwise
        """
        pass
    
    def calculate_net_profit(self, gross_profit_pct: float, num_trades: int = 2) -> float:
        """Calculate net profit after fees"""
        return self.config.get_fee_adjusted_profit(gross_profit_pct, num_trades)
    
    def update_state(self, signal: ArbitrageSignal):
        """Update strategy state after signal"""
        self.state.last_signal = signal
        self.state.trade_count += 1
    
    def get_status(self) -> Dict:
        """Get strategy status"""
        return {
            "name": self.name,
            "is_active": self.state.is_active,
            "position": self.state.current_position,
            "total_pnl": self.state.total_pnl,
            "trade_count": self.state.trade_count
        }


class FundingRateStrategy(BaseStrategy):
    """
    资金费率套利策略 (Perpetual Funding Arbitrage)
    
    策略逻辑:
    - 当资金费率 > 阈值 (默认0.03%): 
      - 买入现货 + 做空永续合约
      - 收取资金费率
    - 当资金费率转负时平仓
    
    收益公式:
    净收益 = 资金费率 × 持仓价值 - 交易成本
           = FR × Position - 2 × (taker_fee + slippage) × Position
           = Position × [FR - 0.10%]
    """
    
    def __init__(
        self, 
        client: BinanceClient,
        config: ArbitrageConfig = None
    ):
        super().__init__(client, config, "FundingRateStrategy")
        self.min_funding_rate = config.funding_rate_threshold if config else 0.03
        self._funding_rates: Dict[str, FundingRateData] = {}
    
    async def analyze(self) -> List[ArbitrageSignal]:
        """Analyze all perpetual contracts for funding rate opportunities"""
        signals = []
        
        try:
            # Get all funding rates
            funding_rates = await self.client.get_all_funding_rates()
            
            for rate_data in funding_rates:
                # Skip if not in our supported symbols
                if rate_data.symbol not in self.config.perpetual_symbols:
                    continue
                
                self._funding_rates[rate_data.symbol] = rate_data
                
                # Check for entry opportunity
                if not self.state.is_active:
                    signal = await self._check_entry(rate_data)
                    if signal:
                        signals.append(signal)
                else:
                    # Check for exit
                    signal = await self._check_exit(rate_data)
                    if signal:
                        signals.append(signal)
            
            self._last_analysis_time = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"FundingRateStrategy analysis failed: {e}")
        
        return signals
    
    async def _check_entry(self, rate_data: FundingRateData) -> Optional[ArbitrageSignal]:
        """Check if funding rate is high enough for entry"""
        funding_rate = rate_data.funding_rate
        
        # Only enter when funding rate is positive and above threshold
        if funding_rate < self.min_funding_rate:
            return None
        
        # Calculate expected profit
        gross_profit = funding_rate
        net_profit = self.calculate_net_profit(gross_profit)
        
        if net_profit <= 0:
            return None
        
        # Get spot price for hedging
        spot_price = await self.client.get_spot_price(rate_data.symbol)
        if not spot_price:
            return None
        
        return ArbitrageSignal(
            strategy_name=self.name,
            signal_type=SignalType.HEDGE,
            symbol=rate_data.symbol,
            side="HEDGE",  # Long spot, Short perpetual
            quantity=0,    # To be calculated by execution engine
            price=rate_data.mark_price,
            reason=f"Funding rate {funding_rate:.4f}% > threshold {self.min_funding_rate}%",
            expected_profit_pct=gross_profit,
            net_profit_pct=net_profit,
            confidence=min(1.0, funding_rate / 0.1),  # Higher funding = higher confidence
            spot_price=spot_price,
            futures_price=rate_data.mark_price,
            funding_rate=funding_rate
        )
    
    async def _check_exit(self, rate_data: FundingRateData) -> Optional[ArbitrageSignal]:
        """Check if should exit position"""
        funding_rate = rate_data.funding_rate
        
        # Exit when funding rate turns negative
        if self.config.funding_negative_exit and funding_rate < 0:
            return ArbitrageSignal(
                strategy_name=self.name,
                signal_type=SignalType.CLOSE_SHORT,
                symbol=rate_data.symbol,
                side="CLOSE",
                quantity=self.state.current_position,
                price=rate_data.mark_price,
                reason=f"Funding rate turned negative: {funding_rate:.4f}%",
                expected_profit_pct=0,
                net_profit_pct=-self.config.round_trip_cost,
                confidence=0.9,
                funding_rate=funding_rate
            )
        
        # Exit when funding rate drops below threshold
        if funding_rate < self.min_funding_rate / 2:
            return ArbitrageSignal(
                strategy_name=self.name,
                signal_type=SignalType.CLOSE_SHORT,
                symbol=rate_data.symbol,
                side="CLOSE",
                quantity=self.state.current_position,
                price=rate_data.mark_price,
                reason=f"Funding rate below half threshold: {funding_rate:.4f}%",
                expected_profit_pct=0,
                net_profit_pct=-self.config.round_trip_cost,
                confidence=0.7,
                funding_rate=funding_rate
            )
        
        return None
    
    async def get_entry_signal(self, symbol: str) -> Optional[ArbitrageSignal]:
        """Get entry signal for specific symbol"""
        rate_data = await self.client.get_funding_rate(symbol)
        if rate_data:
            return await self._check_entry(rate_data)
        return None
    
    async def get_exit_signal(self, symbol: str) -> Optional[ArbitrageSignal]:
        """Get exit signal for specific symbol"""
        rate_data = await self.client.get_funding_rate(symbol)
        if rate_data:
            return await self._check_exit(rate_data)
        return None
    
    def get_current_funding_rates(self) -> Dict[str, float]:
        """Get cached funding rates"""
        return {s: r.funding_rate for s, r in self._funding_rates.items()}


class BasisArbitrageStrategy(BaseStrategy):
    """
    期现套利策略 (Cash & Carry / Basis Arbitrage)
    
    策略逻辑:
    - 计算年化基差: annualized_basis = (futures - spot) / spot × 365 / days_to_expiry
    - 当年化收益率 > 15%: 买入现货 + 做空季度合约
    - 到期前3天自动平仓
    
    收益公式:
    基差 = (期货价格 - 现货价格) / 现货价格
    年化收益 = 基差 × (365 / 到期天数) × 100%
    净年化收益 = 年化收益 - 年化交易成本
    """
    
    def __init__(
        self, 
        client: BinanceClient,
        config: ArbitrageConfig = None
    ):
        super().__init__(client, config, "BasisArbitrageStrategy")
        self.min_annualized_return = config.basis_annualized_threshold if config else 15.0
        self.exit_days_before_expiry = config.basis_days_before_expiry_exit if config else 3
        self._contracts: Dict[str, FuturesContractData] = {}
    
    def _calculate_annualized_basis(
        self, 
        spot_price: float, 
        futures_price: float, 
        days_to_expiry: int
    ) -> float:
        """
        Calculate annualized basis return
        
        Formula: (futures - spot) / spot × 365 / days_to_expiry × 100%
        """
        if spot_price <= 0 or days_to_expiry <= 0:
            return 0
        
        basis = (futures_price - spot_price) / spot_price
        annualized = basis * (365 / days_to_expiry) * 100
        return annualized
    
    async def analyze(self) -> List[ArbitrageSignal]:
        """Analyze quarterly futures contracts for basis opportunities"""
        signals = []
        
        try:
            # Get all delivery contracts
            contracts = await self.client.get_delivery_contracts()
            
            for contract in contracts:
                self._contracts[contract.symbol] = contract
                
                # Calculate days to expiry
                now = datetime.now(timezone.utc)
                days_to_expiry = (contract.delivery_date - now).days
                
                if days_to_expiry <= 0:
                    continue
                
                # Get spot price
                spot_symbol = contract.pair.replace("USD", "USDT")  # e.g., BTCUSD -> BTCUSDT
                spot_price = await self.client.get_spot_price(spot_symbol)
                
                if not spot_price:
                    continue
                
                # Calculate annualized basis
                annualized_basis = self._calculate_annualized_basis(
                    spot_price, 
                    contract.mark_price, 
                    days_to_expiry
                )
                
                # Check for entry/exit
                if not self.state.is_active:
                    signal = self._check_entry(contract, spot_price, annualized_basis, days_to_expiry)
                    if signal:
                        signals.append(signal)
                else:
                    signal = self._check_exit(contract, days_to_expiry)
                    if signal:
                        signals.append(signal)
            
            self._last_analysis_time = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"BasisArbitrageStrategy analysis failed: {e}")
        
        return signals
    
    def _check_entry(
        self, 
        contract: FuturesContractData,
        spot_price: float,
        annualized_basis: float,
        days_to_expiry: int
    ) -> Optional[ArbitrageSignal]:
        """Check if basis is high enough for entry"""
        
        # Need enough days until expiry
        if days_to_expiry < 7:
            return None
        
        # Calculate net return after fees
        # Annualize the trading cost for proper comparison
        annualized_cost = self.config.round_trip_cost * (365 / days_to_expiry)
        net_annualized = annualized_basis - annualized_cost
        
        if net_annualized < self.min_annualized_return:
            return None
        
        return ArbitrageSignal(
            strategy_name=self.name,
            signal_type=SignalType.HEDGE,
            symbol=contract.symbol,
            side="HEDGE",  # Long spot, Short futures
            quantity=0,
            price=contract.mark_price,
            reason=f"Annualized basis {annualized_basis:.2f}% > threshold {self.min_annualized_return}%",
            expected_profit_pct=annualized_basis,
            net_profit_pct=net_annualized,
            confidence=min(1.0, annualized_basis / 30),
            spot_price=spot_price,
            futures_price=contract.mark_price,
            basis=contract.basis_rate
        )
    
    def _check_exit(
        self, 
        contract: FuturesContractData,
        days_to_expiry: int
    ) -> Optional[ArbitrageSignal]:
        """Check if should exit before expiry"""
        
        # Exit before expiry to avoid settlement issues
        if days_to_expiry <= self.exit_days_before_expiry:
            return ArbitrageSignal(
                strategy_name=self.name,
                signal_type=SignalType.CLOSE_SHORT,
                symbol=contract.symbol,
                side="CLOSE",
                quantity=self.state.current_position,
                price=contract.mark_price,
                reason=f"Approaching expiry: {days_to_expiry} days remaining",
                expected_profit_pct=0,
                net_profit_pct=-self.config.round_trip_cost,
                confidence=1.0,
                basis=contract.basis_rate
            )
        
        return None
    
    async def get_entry_signal(self, symbol: str) -> Optional[ArbitrageSignal]:
        """Get entry signal for specific contract"""
        if symbol in self._contracts:
            contract = self._contracts[symbol]
            now = datetime.now(timezone.utc)
            days_to_expiry = (contract.delivery_date - now).days
            
            spot_symbol = contract.pair.replace("USD", "USDT")
            spot_price = await self.client.get_spot_price(spot_symbol)
            
            if spot_price:
                annualized = self._calculate_annualized_basis(
                    spot_price, contract.mark_price, days_to_expiry
                )
                return self._check_entry(contract, spot_price, annualized, days_to_expiry)
        return None
    
    async def get_exit_signal(self, symbol: str) -> Optional[ArbitrageSignal]:
        """Get exit signal for specific contract"""
        if symbol in self._contracts:
            contract = self._contracts[symbol]
            now = datetime.now(timezone.utc)
            days_to_expiry = (contract.delivery_date - now).days
            return self._check_exit(contract, days_to_expiry)
        return None


class StablecoinSpreadStrategy(BaseStrategy):
    """
    稳定币价差套利策略 (Stablecoin Spread Arbitrage)
    
    策略逻辑:
    - 监控 USDT/USDC/BUSD/DAI 等稳定币之间的价差
    - 当价差 > 0.5%: 卖出高价稳定币，买入低价稳定币
    - 价差收敛时平仓获利
    
    收益公式:
    价差收益 = (高价 - 低价) / 低价 × 100%
    净收益 = 价差 - 2 × (taker_fee + slippage)
           = Spread - 0.10%
    """
    
    def __init__(
        self, 
        client: BinanceClient,
        config: ArbitrageConfig = None
    ):
        super().__init__(client, config, "StablecoinSpreadStrategy")
        self.min_spread = config.stablecoin_spread_threshold if config else 0.5
        self._stablecoin_prices: Dict[str, float] = {}
        self._current_spreads: List[Dict] = []
    
    async def analyze(self) -> List[ArbitrageSignal]:
        """Analyze stablecoin spreads for arbitrage opportunities"""
        signals = []
        
        try:
            # Get stablecoin prices
            self._stablecoin_prices = await self.client.get_stablecoin_prices()
            
            # Calculate spreads
            self._current_spreads = await self.client.get_stablecoin_spreads()
            
            for spread_data in self._current_spreads:
                spread_pct = spread_data["spread_pct"]
                
                # Check if spread exceeds threshold
                if spread_pct >= self.min_spread:
                    signal = self._check_entry(spread_data)
                    if signal:
                        signals.append(signal)
            
            self._last_analysis_time = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"StablecoinSpreadStrategy analysis failed: {e}")
        
        return signals
    
    def _check_entry(self, spread_data: Dict) -> Optional[ArbitrageSignal]:
        """Check if spread is profitable after fees"""
        spread_pct = spread_data["spread_pct"]
        
        # Calculate net profit
        net_profit = self.calculate_net_profit(spread_pct)
        
        if net_profit <= 0:
            return None
        
        coin_high = spread_data["coin_high"]
        coin_low = spread_data["coin_low"]
        
        # Construct trading pair (sell high, buy low)
        # Need to determine the correct trading pair
        trade_pair = f"{coin_high}{coin_low}"
        
        return ArbitrageSignal(
            strategy_name=self.name,
            signal_type=SignalType.HEDGE,
            symbol=trade_pair,
            side="ARBITRAGE",  # Sell high, Buy low
            quantity=0,
            price=(spread_data["price_high"] + spread_data["price_low"]) / 2,
            reason=f"Spread {spread_pct:.4f}% > threshold {self.min_spread}% ({coin_high}/{coin_low})",
            expected_profit_pct=spread_pct,
            net_profit_pct=net_profit,
            confidence=min(1.0, spread_pct / 1.0),  # 1% spread = full confidence
            spread=spread_pct
        )
    
    async def get_entry_signal(self, symbol: str = None) -> Optional[ArbitrageSignal]:
        """Get entry signal - analyzes all stablecoin pairs"""
        spreads = await self.client.get_stablecoin_spreads()
        
        for spread_data in spreads:
            if spread_data["spread_pct"] >= self.min_spread:
                signal = self._check_entry(spread_data)
                if signal and signal.is_profitable():
                    return signal
        
        return None
    
    async def get_exit_signal(self, symbol: str = None) -> Optional[ArbitrageSignal]:
        """Get exit signal - exit when spread narrows"""
        if not self.state.is_active:
            return None
        
        # Get current spreads
        spreads = await self.client.get_stablecoin_spreads()
        
        # Find the spread we're trading
        for spread_data in spreads:
            # Exit if spread has narrowed significantly
            if spread_data["spread_pct"] < self.min_spread / 2:
                return ArbitrageSignal(
                    strategy_name=self.name,
                    signal_type=SignalType.CLOSE_LONG,
                    symbol=f"{spread_data['coin_high']}{spread_data['coin_low']}",
                    side="CLOSE",
                    quantity=self.state.current_position,
                    price=spread_data["price_low"],
                    reason=f"Spread narrowed to {spread_data['spread_pct']:.4f}%",
                    expected_profit_pct=0,
                    net_profit_pct=-self.config.round_trip_cost / 2,
                    confidence=0.8,
                    spread=spread_data["spread_pct"]
                )
        
        return None
    
    def get_current_spreads(self) -> List[Dict]:
        """Get current stablecoin spreads"""
        return self._current_spreads


class StrategyManager:
    """
    Strategy Manager - Manages all arbitrage strategies
    """
    
    def __init__(self, client: BinanceClient, config: ArbitrageConfig = None):
        self.client = client
        self.config = config or DEFAULT_CONFIG
        
        # Initialize strategies
        self.strategies: Dict[str, BaseStrategy] = {
            "funding_rate": FundingRateStrategy(client, config),
            "basis": BasisArbitrageStrategy(client, config),
            "stablecoin": StablecoinSpreadStrategy(client, config)
        }
        
        self._running = False
    
    async def analyze_all(self) -> Dict[str, List[ArbitrageSignal]]:
        """Run analysis on all strategies"""
        results = {}
        
        for name, strategy in self.strategies.items():
            try:
                signals = await strategy.analyze()
                results[name] = signals
                logger.info(f"{name}: {len(signals)} signals generated")
            except Exception as e:
                logger.error(f"Strategy {name} failed: {e}")
                results[name] = []
        
        return results
    
    async def get_best_opportunities(self, min_profit: float = 0) -> List[ArbitrageSignal]:
        """Get best arbitrage opportunities across all strategies"""
        all_signals = []
        
        analysis = await self.analyze_all()
        
        for signals in analysis.values():
            for signal in signals:
                if signal.is_profitable() and signal.net_profit_pct >= min_profit:
                    all_signals.append(signal)
        
        # Sort by net profit
        all_signals.sort(key=lambda s: s.net_profit_pct, reverse=True)
        
        return all_signals
    
    def get_status(self) -> Dict[str, Dict]:
        """Get status of all strategies"""
        return {name: s.get_status() for name, s in self.strategies.items()}
    
    async def start_monitoring(self, callback: callable = None):
        """Start continuous monitoring loop"""
        self._running = True
        
        while self._running:
            try:
                signals = await self.get_best_opportunities()
                
                if callback and signals:
                    await callback(signals)
                
                await asyncio.sleep(self.config.strategy_check_interval)
                
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                await asyncio.sleep(5)
    
    def stop_monitoring(self):
        """Stop monitoring loop"""
        self._running = False