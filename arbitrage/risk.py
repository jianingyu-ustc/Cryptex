"""
Risk Control Layer

Implements:
- Maximum position limits (≤50% of account balance)
- Single strategy exposure limits
- Liquidation risk detection
- Stop-loss protection (default 3%)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
from enum import Enum

from .config import ArbitrageConfig, DEFAULT_CONFIG
from .api import BinanceClient, PositionData, AccountBalance
from .execution import Position

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Risk level classification"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskAlertType(Enum):
    """Types of risk alerts"""
    POSITION_LIMIT_EXCEEDED = "POSITION_LIMIT_EXCEEDED"
    STRATEGY_LIMIT_EXCEEDED = "STRATEGY_LIMIT_EXCEEDED"
    STOP_LOSS_TRIGGERED = "STOP_LOSS_TRIGGERED"
    LIQUIDATION_RISK = "LIQUIDATION_RISK"
    MARGIN_CALL = "MARGIN_CALL"
    UNHEDGED_POSITION = "UNHEDGED_POSITION"
    HIGH_DRAWDOWN = "HIGH_DRAWDOWN"


@dataclass
class RiskAlert:
    """Risk alert data"""
    alert_type: RiskAlertType
    level: RiskLevel
    message: str
    symbol: str = ""
    current_value: float = 0
    threshold: float = 0
    action_required: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict:
        return {
            "type": self.alert_type.value,
            "level": self.level.value,
            "message": self.message,
            "symbol": self.symbol,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "action": self.action_required,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class RiskMetrics:
    """Current risk metrics"""
    total_equity: float = 0
    total_position_value: float = 0
    position_ratio: float = 0              # position_value / equity
    
    unrealized_pnl: float = 0
    realized_pnl: float = 0
    
    max_drawdown: float = 0
    current_drawdown: float = 0
    
    margin_ratio: float = 0                # For futures
    liquidation_price: float = 0
    
    num_positions: int = 0
    hedge_ratio: float = 0                 # Spot vs Futures balance
    
    risk_level: RiskLevel = RiskLevel.LOW
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskManager:
    """
    Risk Manager - Monitors and controls trading risk
    
    Features:
    - Real-time position monitoring
    - Automatic stop-loss enforcement
    - Liquidation risk detection
    - Position limit enforcement
    """
    
    def __init__(
        self,
        client: BinanceClient,
        config: ArbitrageConfig = None
    ):
        self.client = client
        self.config = config or DEFAULT_CONFIG
        
        # Alert tracking
        self._alerts: List[RiskAlert] = []
        self._alert_callbacks: List[Callable[[RiskAlert], None]] = []
        
        # Metrics tracking
        self._current_metrics = RiskMetrics()
        self._peak_equity: float = 0
        
        # Monitoring state
        self._running = False
        self._positions: Dict[str, Position] = {}
    
    async def check_all_risks(self, positions: Dict[str, Position] = None) -> List[RiskAlert]:
        """
        Perform comprehensive risk check
        
        Args:
            positions: Current positions to check
            
        Returns:
            List of RiskAlerts
        """
        alerts = []
        self._positions = positions or {}
        
        # Calculate current metrics
        await self._update_metrics()
        
        # Check position limits
        position_alert = await self._check_position_limits()
        if position_alert:
            alerts.append(position_alert)
        
        # Check stop loss
        stop_loss_alerts = await self._check_stop_loss()
        alerts.extend(stop_loss_alerts)
        
        # Check liquidation risk
        liquidation_alert = await self._check_liquidation_risk()
        if liquidation_alert:
            alerts.append(liquidation_alert)
        
        # Check hedge balance
        hedge_alerts = await self._check_hedge_balance()
        alerts.extend(hedge_alerts)
        
        # Check drawdown
        drawdown_alert = await self._check_drawdown()
        if drawdown_alert:
            alerts.append(drawdown_alert)
        
        # Store alerts
        self._alerts.extend(alerts)
        
        # Notify callbacks
        for alert in alerts:
            for callback in self._alert_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(alert)
                    else:
                        callback(alert)
                except Exception as e:
                    logger.error(f"Alert callback error: {e}")
        
        return alerts
    
    async def _update_metrics(self):
        """Update current risk metrics"""
        try:
            # Get account balances
            spot_balances = await self.client.get_spot_balance()
            futures_balances = await self.client.get_perpetual_balance()
            
            # Calculate total equity
            total_spot = sum(b.total for b in spot_balances if b.asset == "USDT")
            total_futures = sum(b.total for b in futures_balances if b.asset == "USDT")
            total_equity = total_spot + total_futures
            
            # Update peak equity for drawdown calculation
            if total_equity > self._peak_equity:
                self._peak_equity = total_equity
            
            # Calculate position value
            total_position_value = 0
            unrealized_pnl = 0
            
            for symbol, position in self._positions.items():
                total_position_value += position.spot_qty * position.spot_current_price
                unrealized_pnl += position.unrealized_pnl
            
            # Get futures positions for margin info
            futures_positions = await self.client.get_perpetual_positions()
            
            min_margin_ratio = float('inf')
            for fp in futures_positions:
                if fp.leverage > 0:
                    # Calculate margin ratio
                    position_value = fp.size * fp.mark_price
                    margin_used = position_value / fp.leverage
                    if margin_used > 0:
                        margin_ratio = (total_futures / margin_used) * 100
                        min_margin_ratio = min(min_margin_ratio, margin_ratio)
            
            if min_margin_ratio == float('inf'):
                min_margin_ratio = 100  # No positions
            
            # Calculate drawdown
            current_drawdown = 0
            if self._peak_equity > 0:
                current_drawdown = (self._peak_equity - total_equity) / self._peak_equity * 100
            
            # Calculate hedge ratio
            total_spot_qty = sum(p.spot_qty for p in self._positions.values())
            total_futures_qty = sum(abs(p.futures_qty) for p in self._positions.values())
            hedge_ratio = total_spot_qty / total_futures_qty if total_futures_qty > 0 else 1.0
            
            # Determine risk level
            risk_level = self._calculate_risk_level(
                position_ratio=total_position_value / total_equity if total_equity > 0 else 0,
                margin_ratio=min_margin_ratio,
                drawdown=current_drawdown,
                hedge_ratio=hedge_ratio
            )
            
            # Update metrics
            self._current_metrics = RiskMetrics(
                total_equity=total_equity,
                total_position_value=total_position_value,
                position_ratio=total_position_value / total_equity * 100 if total_equity > 0 else 0,
                unrealized_pnl=unrealized_pnl,
                margin_ratio=min_margin_ratio,
                max_drawdown=max(self._current_metrics.max_drawdown, current_drawdown),
                current_drawdown=current_drawdown,
                num_positions=len(self._positions),
                hedge_ratio=hedge_ratio,
                risk_level=risk_level,
                timestamp=datetime.now(timezone.utc)
            )
            
        except Exception as e:
            logger.error(f"Failed to update metrics: {e}")
    
    def _calculate_risk_level(
        self,
        position_ratio: float,
        margin_ratio: float,
        drawdown: float,
        hedge_ratio: float
    ) -> RiskLevel:
        """Calculate overall risk level"""
        
        # Critical conditions
        if margin_ratio < 5 or drawdown > 20 or position_ratio > 0.8:
            return RiskLevel.CRITICAL
        
        # High risk conditions
        if margin_ratio < 10 or drawdown > 10 or position_ratio > 0.6:
            return RiskLevel.HIGH
        
        # Medium risk conditions
        if margin_ratio < 20 or drawdown > 5 or position_ratio > 0.4 or abs(hedge_ratio - 1.0) > 0.1:
            return RiskLevel.MEDIUM
        
        return RiskLevel.LOW
    
    async def _check_position_limits(self) -> Optional[RiskAlert]:
        """Check if position limits are exceeded"""
        position_ratio = self._current_metrics.position_ratio
        
        if position_ratio > self.config.max_position_pct:
            return RiskAlert(
                alert_type=RiskAlertType.POSITION_LIMIT_EXCEEDED,
                level=RiskLevel.HIGH,
                message=f"Position ratio {position_ratio:.1f}% exceeds limit {self.config.max_position_pct}%",
                current_value=position_ratio,
                threshold=self.config.max_position_pct,
                action_required="Reduce position size or close some positions"
            )
        
        return None
    
    async def _check_stop_loss(self) -> List[RiskAlert]:
        """Check if any positions hit stop loss"""
        alerts = []
        
        for symbol, position in self._positions.items():
            if position.spot_entry_price <= 0:
                continue
            
            # Calculate P&L percentage
            pnl_pct = position.unrealized_pnl / (position.spot_qty * position.spot_entry_price) * 100
            
            if pnl_pct < -self.config.stop_loss_pct:
                alerts.append(RiskAlert(
                    alert_type=RiskAlertType.STOP_LOSS_TRIGGERED,
                    level=RiskLevel.CRITICAL,
                    message=f"Stop loss triggered for {symbol}: P&L {pnl_pct:.2f}%",
                    symbol=symbol,
                    current_value=pnl_pct,
                    threshold=-self.config.stop_loss_pct,
                    action_required=f"Close position for {symbol} immediately"
                ))
        
        return alerts
    
    async def _check_liquidation_risk(self) -> Optional[RiskAlert]:
        """Check for liquidation risk on futures positions"""
        margin_ratio = self._current_metrics.margin_ratio
        
        if margin_ratio < self.config.min_margin_ratio:
            level = RiskLevel.CRITICAL if margin_ratio < 3 else RiskLevel.HIGH
            
            return RiskAlert(
                alert_type=RiskAlertType.LIQUIDATION_RISK,
                level=level,
                message=f"Margin ratio {margin_ratio:.1f}% below minimum {self.config.min_margin_ratio}%",
                current_value=margin_ratio,
                threshold=self.config.min_margin_ratio,
                action_required="Add margin or reduce futures position"
            )
        
        return None
    
    async def _check_hedge_balance(self) -> List[RiskAlert]:
        """Check if positions are properly hedged"""
        alerts = []
        
        for symbol, position in self._positions.items():
            if not position.is_hedged():
                imbalance = abs(position.spot_qty - abs(position.futures_qty))
                imbalance_pct = imbalance / position.spot_qty * 100 if position.spot_qty > 0 else 100
                
                if imbalance_pct > 5:  # More than 5% imbalance
                    alerts.append(RiskAlert(
                        alert_type=RiskAlertType.UNHEDGED_POSITION,
                        level=RiskLevel.MEDIUM,
                        message=f"Hedge imbalance for {symbol}: {imbalance_pct:.1f}% unhedged",
                        symbol=symbol,
                        current_value=imbalance_pct,
                        threshold=5.0,
                        action_required=f"Rebalance hedge for {symbol}"
                    ))
        
        return alerts
    
    async def _check_drawdown(self) -> Optional[RiskAlert]:
        """Check current drawdown"""
        drawdown = self._current_metrics.current_drawdown
        
        if drawdown > 15:
            return RiskAlert(
                alert_type=RiskAlertType.HIGH_DRAWDOWN,
                level=RiskLevel.HIGH,
                message=f"High drawdown detected: {drawdown:.1f}%",
                current_value=drawdown,
                threshold=15.0,
                action_required="Review and potentially reduce positions"
            )
        
        return None
    
    async def can_open_position(
        self, 
        symbol: str, 
        size_usd: float,
        strategy: str = None
    ) -> tuple:
        """
        Check if a new position can be opened
        
        Args:
            symbol: Trading symbol
            size_usd: Position size in USD
            strategy: Strategy name (for per-strategy limits)
            
        Returns:
            tuple: (can_open: bool, reason: str)
        """
        # Get current equity
        if self._current_metrics.total_equity <= 0:
            await self._update_metrics()
        
        total_equity = self._current_metrics.total_equity
        current_position_value = self._current_metrics.total_position_value
        
        # Check overall position limit
        new_position_ratio = (current_position_value + size_usd) / total_equity * 100
        
        if new_position_ratio > self.config.max_position_pct:
            return False, f"Would exceed position limit: {new_position_ratio:.1f}% > {self.config.max_position_pct}%"
        
        # Check per-strategy limit if applicable
        if strategy:
            strategy_position_value = sum(
                p.spot_qty * p.spot_current_price 
                for p in self._positions.values() 
                if p.strategy == strategy
            )
            new_strategy_ratio = (strategy_position_value + size_usd) / total_equity * 100
            
            if new_strategy_ratio > self.config.max_single_strategy_pct:
                return False, f"Would exceed strategy limit: {new_strategy_ratio:.1f}% > {self.config.max_single_strategy_pct}%"
        
        # Check if in high risk state
        if self._current_metrics.risk_level == RiskLevel.CRITICAL:
            return False, "Risk level is CRITICAL, no new positions allowed"
        
        return True, "OK"
    
    def register_alert_callback(self, callback: Callable[[RiskAlert], None]):
        """Register callback for risk alerts"""
        self._alert_callbacks.append(callback)
    
    def get_current_metrics(self) -> RiskMetrics:
        """Get current risk metrics"""
        return self._current_metrics
    
    def get_alerts(self, limit: int = 100) -> List[RiskAlert]:
        """Get recent alerts"""
        return self._alerts[-limit:]
    
    def clear_alerts(self):
        """Clear all alerts"""
        self._alerts.clear()
    
    async def start_monitoring(
        self, 
        positions_provider: Callable[[], Dict[str, Position]],
        interval: int = None
    ):
        """
        Start continuous risk monitoring
        
        Args:
            positions_provider: Callable that returns current positions
            interval: Check interval in seconds
        """
        self._running = True
        interval = interval or self.config.risk_check_interval
        
        logger.info(f"Starting risk monitoring (interval: {interval}s)")
        
        while self._running:
            try:
                positions = positions_provider()
                alerts = await self.check_all_risks(positions)
                
                if alerts:
                    logger.warning(f"Risk alerts generated: {len(alerts)}")
                    for alert in alerts:
                        logger.warning(f"  {alert.alert_type.value}: {alert.message}")
                
                await asyncio.sleep(interval)
                
            except Exception as e:
                logger.error(f"Risk monitoring error: {e}")
                await asyncio.sleep(5)
    
    def stop_monitoring(self):
        """Stop risk monitoring"""
        self._running = False
        logger.info("Risk monitoring stopped")
    
    def get_status(self) -> Dict:
        """Get risk manager status"""
        return {
            "running": self._running,
            "risk_level": self._current_metrics.risk_level.value,
            "total_equity": self._current_metrics.total_equity,
            "position_ratio": self._current_metrics.position_ratio,
            "margin_ratio": self._current_metrics.margin_ratio,
            "current_drawdown": self._current_metrics.current_drawdown,
            "num_positions": self._current_metrics.num_positions,
            "alert_count": len(self._alerts),
            "last_check": self._current_metrics.timestamp.isoformat()
        }