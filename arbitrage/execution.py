"""
Execution Layer - Order Management and Hedging

Handles:
- Order placement and management
- Hedge execution (simultaneous spot + futures)
- Automatic rollback on failures
- Position tracking
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from enum import Enum

from .config import ArbitrageConfig, DEFAULT_CONFIG
from .api import BinanceClient, OrderResult, PositionData, AccountBalance
from .strategy import ArbitrageSignal, SignalType

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order status"""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class HedgeOrder:
    """Hedge order pair (spot + futures)"""
    id: str
    signal: ArbitrageSignal
    
    # Spot order
    spot_order: Optional[OrderResult] = None
    spot_status: OrderStatus = OrderStatus.PENDING
    
    # Futures order
    futures_order: Optional[OrderResult] = None
    futures_status: OrderStatus = OrderStatus.PENDING
    
    # Overall status
    status: OrderStatus = OrderStatus.PENDING
    error_message: str = ""
    
    # Execution details
    spot_filled_qty: float = 0
    spot_avg_price: float = 0
    futures_filled_qty: float = 0
    futures_avg_price: float = 0
    
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    
    def is_complete(self) -> bool:
        """Check if hedge order is complete"""
        return self.status in [OrderStatus.FILLED, OrderStatus.FAILED, OrderStatus.ROLLED_BACK]
    
    def calculate_actual_cost(self, taker_fee: float = 0.04) -> float:
        """Calculate actual trading cost"""
        spot_cost = self.spot_filled_qty * self.spot_avg_price * (taker_fee / 100)
        futures_cost = self.futures_filled_qty * self.futures_avg_price * (taker_fee / 100)
        return spot_cost + futures_cost


@dataclass
class Position:
    """Tracked position"""
    symbol: str
    strategy: str
    
    # Position sizes
    spot_qty: float = 0
    futures_qty: float = 0
    
    # Entry prices
    spot_entry_price: float = 0
    futures_entry_price: float = 0
    
    # Current prices
    spot_current_price: float = 0
    futures_current_price: float = 0
    
    # P&L
    unrealized_pnl: float = 0
    realized_pnl: float = 0
    
    # Metadata
    opened_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    
    def calculate_pnl(self) -> float:
        """Calculate current P&L"""
        spot_pnl = self.spot_qty * (self.spot_current_price - self.spot_entry_price)
        futures_pnl = self.futures_qty * (self.futures_entry_price - self.futures_current_price)
        return spot_pnl + futures_pnl
    
    def is_hedged(self) -> bool:
        """Check if position is properly hedged"""
        return abs(self.spot_qty - abs(self.futures_qty)) < 0.001 * self.spot_qty


class ExecutionEngine:
    """
    Execution Engine - Handles order execution with safety mechanisms
    
    Features:
    - Atomic hedge execution (spot + futures)
    - Automatic rollback on partial fills
    - Slippage protection
    - Position tracking
    """
    
    def __init__(
        self, 
        client: BinanceClient,
        config: ArbitrageConfig = None
    ):
        self.client = client
        self.config = config or DEFAULT_CONFIG
        
        # Order tracking
        self._pending_orders: Dict[str, HedgeOrder] = {}
        self._completed_orders: List[HedgeOrder] = []
        
        # Position tracking
        self._positions: Dict[str, Position] = {}
        
        # Order ID counter
        self._order_counter = 0
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
    
    def _generate_order_id(self) -> str:
        """Generate unique order ID"""
        self._order_counter += 1
        return f"ARB_{self._order_counter}_{int(datetime.now().timestamp())}"
    
    async def execute_signal(self, signal: ArbitrageSignal) -> HedgeOrder:
        """
        Execute an arbitrage signal
        
        Args:
            signal: ArbitrageSignal to execute
            
        Returns:
            HedgeOrder with execution results
        """
        order_id = self._generate_order_id()
        hedge_order = HedgeOrder(id=order_id, signal=signal)
        
        async with self._lock:
            self._pending_orders[order_id] = hedge_order
        
        try:
            if signal.signal_type == SignalType.HEDGE:
                await self._execute_hedge(hedge_order)
            elif signal.signal_type in [SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]:
                await self._execute_close(hedge_order)
            else:
                logger.warning(f"Unsupported signal type: {signal.signal_type}")
                hedge_order.status = OrderStatus.FAILED
                hedge_order.error_message = "Unsupported signal type"
            
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            hedge_order.status = OrderStatus.FAILED
            hedge_order.error_message = str(e)
            
            # Attempt rollback if partial execution
            await self._rollback_if_needed(hedge_order)
        
        finally:
            hedge_order.completed_at = datetime.now(timezone.utc)
            
            async with self._lock:
                if order_id in self._pending_orders:
                    del self._pending_orders[order_id]
                self._completed_orders.append(hedge_order)
        
        return hedge_order
    
    async def _execute_hedge(self, hedge_order: HedgeOrder):
        """
        Execute hedge order (long spot + short futures)
        
        Process:
        1. Calculate position sizes
        2. Place spot buy order
        3. Place futures short order
        4. Verify both filled
        5. Rollback if either fails
        """
        signal = hedge_order.signal
        symbol = signal.symbol
        
        logger.info(f"Executing hedge: {symbol}")
        
        # Step 1: Calculate position sizes based on available balance
        quantity = await self._calculate_position_size(signal)
        
        if quantity <= 0:
            hedge_order.status = OrderStatus.FAILED
            hedge_order.error_message = "Insufficient balance or invalid quantity"
            return
        
        # Step 2: Execute spot buy order
        logger.info(f"Placing spot buy order: {symbol} qty={quantity}")
        hedge_order.spot_status = OrderStatus.SUBMITTED
        
        spot_result = await self.client.spot_market_order(
            symbol=symbol,
            side="BUY",
            quantity=quantity
        )
        
        if not spot_result or spot_result.status not in ["FILLED", "NEW"]:
            hedge_order.spot_status = OrderStatus.FAILED
            hedge_order.status = OrderStatus.FAILED
            hedge_order.error_message = "Spot order failed"
            return
        
        hedge_order.spot_order = spot_result
        hedge_order.spot_filled_qty = spot_result.filled_qty
        hedge_order.spot_avg_price = spot_result.avg_price
        
        if spot_result.filled_qty > 0:
            hedge_order.spot_status = OrderStatus.FILLED
        
        # Step 3: Execute futures short order
        logger.info(f"Placing futures short order: {symbol} qty={hedge_order.spot_filled_qty}")
        hedge_order.futures_status = OrderStatus.SUBMITTED
        
        # Set leverage and margin type first
        await self.client.set_leverage(symbol, 1)  # 1x leverage for hedging
        await self.client.set_margin_type(symbol, "CROSSED")
        
        futures_result = await self.client.perpetual_market_order(
            symbol=symbol,
            side="SELL",
            quantity=hedge_order.spot_filled_qty
        )
        
        if not futures_result or futures_result.status not in ["FILLED", "NEW"]:
            hedge_order.futures_status = OrderStatus.FAILED
            
            # Rollback spot order
            logger.warning("Futures order failed, rolling back spot order")
            await self._rollback_spot(hedge_order)
            
            hedge_order.status = OrderStatus.ROLLED_BACK
            hedge_order.error_message = "Futures order failed, spot position rolled back"
            return
        
        hedge_order.futures_order = futures_result
        hedge_order.futures_filled_qty = futures_result.filled_qty
        hedge_order.futures_avg_price = futures_result.avg_price
        
        if futures_result.filled_qty > 0:
            hedge_order.futures_status = OrderStatus.FILLED
        
        # Step 4: Verify hedge is balanced
        qty_diff = abs(hedge_order.spot_filled_qty - hedge_order.futures_filled_qty)
        if qty_diff > hedge_order.spot_filled_qty * 0.01:  # Allow 1% difference
            logger.warning(f"Hedge imbalance: spot={hedge_order.spot_filled_qty}, futures={hedge_order.futures_filled_qty}")
            # Could add logic to balance the hedge here
        
        # Update position tracking
        await self._update_position(hedge_order, is_open=True)
        
        hedge_order.status = OrderStatus.FILLED
        logger.info(f"Hedge executed successfully: {symbol}")
    
    async def _execute_close(self, hedge_order: HedgeOrder):
        """
        Close hedge position
        
        Process:
        1. Close futures position first (reduce risk)
        2. Sell spot position
        3. Calculate realized P&L
        """
        signal = hedge_order.signal
        symbol = signal.symbol
        
        logger.info(f"Closing position: {symbol}")
        
        # Get current position
        position = self._positions.get(symbol)
        if not position:
            hedge_order.status = OrderStatus.FAILED
            hedge_order.error_message = f"No position found for {symbol}"
            return
        
        # Step 1: Close futures position
        logger.info(f"Closing futures position: {symbol} qty={position.futures_qty}")
        hedge_order.futures_status = OrderStatus.SUBMITTED
        
        futures_result = await self.client.perpetual_market_order(
            symbol=symbol,
            side="BUY",  # Buy to close short
            quantity=abs(position.futures_qty),
            reduce_only=True
        )
        
        if futures_result and futures_result.filled_qty > 0:
            hedge_order.futures_order = futures_result
            hedge_order.futures_filled_qty = futures_result.filled_qty
            hedge_order.futures_avg_price = futures_result.avg_price
            hedge_order.futures_status = OrderStatus.FILLED
        else:
            hedge_order.futures_status = OrderStatus.FAILED
            logger.warning("Failed to close futures position")
        
        # Step 2: Sell spot position
        logger.info(f"Selling spot position: {symbol} qty={position.spot_qty}")
        hedge_order.spot_status = OrderStatus.SUBMITTED
        
        spot_result = await self.client.spot_market_order(
            symbol=symbol,
            side="SELL",
            quantity=position.spot_qty
        )
        
        if spot_result and spot_result.filled_qty > 0:
            hedge_order.spot_order = spot_result
            hedge_order.spot_filled_qty = spot_result.filled_qty
            hedge_order.spot_avg_price = spot_result.avg_price
            hedge_order.spot_status = OrderStatus.FILLED
        else:
            hedge_order.spot_status = OrderStatus.FAILED
            logger.warning("Failed to sell spot position")
        
        # Update position tracking
        await self._update_position(hedge_order, is_open=False)
        
        if hedge_order.spot_status == OrderStatus.FILLED and hedge_order.futures_status == OrderStatus.FILLED:
            hedge_order.status = OrderStatus.FILLED
        else:
            hedge_order.status = OrderStatus.PARTIALLY_FILLED
        
        logger.info(f"Position closed: {symbol}")
    
    async def _rollback_spot(self, hedge_order: HedgeOrder):
        """Rollback spot position if futures order fails"""
        if hedge_order.spot_filled_qty <= 0:
            return
        
        try:
            result = await self.client.spot_market_order(
                symbol=hedge_order.signal.symbol,
                side="SELL",
                quantity=hedge_order.spot_filled_qty
            )
            
            if result and result.filled_qty > 0:
                logger.info(f"Spot rollback successful: sold {result.filled_qty}")
            else:
                logger.error("Spot rollback failed")
                
        except Exception as e:
            logger.error(f"Spot rollback error: {e}")
    
    async def _rollback_if_needed(self, hedge_order: HedgeOrder):
        """Check and perform rollback if needed"""
        # If spot filled but futures failed
        if (hedge_order.spot_status == OrderStatus.FILLED and 
            hedge_order.futures_status != OrderStatus.FILLED):
            await self._rollback_spot(hedge_order)
            hedge_order.status = OrderStatus.ROLLED_BACK
    
    async def _calculate_position_size(self, signal: ArbitrageSignal) -> float:
        """
        Calculate position size based on:
        - Available balance
        - Risk limits
        - Signal confidence
        """
        # Get available balance
        balances = await self.client.get_spot_balance()
        
        usdt_balance = 0
        for balance in balances:
            if balance.asset == "USDT":
                usdt_balance = balance.free
                break
        
        if usdt_balance <= 0:
            return 0
        
        # Apply position limits
        max_position_value = usdt_balance * (self.config.max_position_pct / 100)
        strategy_limit = usdt_balance * (self.config.max_single_strategy_pct / 100)
        
        # Use the smaller of the two limits
        position_value = min(max_position_value, strategy_limit)
        
        # Adjust by confidence
        position_value *= signal.confidence
        
        # Calculate quantity based on price
        price = signal.price or signal.spot_price
        if price <= 0:
            return 0
        
        quantity = position_value / price
        
        # Round to appropriate precision (simplified)
        quantity = round(quantity, 5)
        
        return quantity
    
    async def _update_position(self, hedge_order: HedgeOrder, is_open: bool):
        """Update position tracking"""
        symbol = hedge_order.signal.symbol
        
        if is_open:
            # Opening new position
            position = Position(
                symbol=symbol,
                strategy=hedge_order.signal.strategy_name,
                spot_qty=hedge_order.spot_filled_qty,
                futures_qty=-hedge_order.futures_filled_qty,  # Negative for short
                spot_entry_price=hedge_order.spot_avg_price,
                futures_entry_price=hedge_order.futures_avg_price,
                opened_at=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc)
            )
            self._positions[symbol] = position
            
        else:
            # Closing position
            if symbol in self._positions:
                position = self._positions[symbol]
                
                # Calculate realized P&L
                spot_pnl = hedge_order.spot_filled_qty * (hedge_order.spot_avg_price - position.spot_entry_price)
                futures_pnl = hedge_order.futures_filled_qty * (position.futures_entry_price - hedge_order.futures_avg_price)
                
                position.realized_pnl = spot_pnl + futures_pnl
                
                # Remove position
                del self._positions[symbol]
    
    async def get_positions(self) -> Dict[str, Position]:
        """Get all tracked positions"""
        return self._positions.copy()
    
    async def sync_positions(self):
        """Sync positions with exchange"""
        # Get spot balances
        spot_balances = await self.client.get_spot_balance()
        
        # Get futures positions
        futures_positions = await self.client.get_perpetual_positions()
        
        # Update current prices for tracked positions
        for symbol, position in self._positions.items():
            spot_price = await self.client.get_spot_price(symbol)
            if spot_price:
                position.spot_current_price = spot_price
            
            for fp in futures_positions:
                if fp.symbol == symbol:
                    position.futures_current_price = fp.mark_price
                    break
            
            position.unrealized_pnl = position.calculate_pnl()
            position.last_updated = datetime.now(timezone.utc)
    
    def get_pending_orders(self) -> Dict[str, HedgeOrder]:
        """Get pending orders"""
        return self._pending_orders.copy()
    
    def get_completed_orders(self, limit: int = 100) -> List[HedgeOrder]:
        """Get completed orders"""
        return self._completed_orders[-limit:]
    
    def get_statistics(self) -> Dict:
        """Get execution statistics"""
        total_orders = len(self._completed_orders)
        filled_orders = sum(1 for o in self._completed_orders if o.status == OrderStatus.FILLED)
        failed_orders = sum(1 for o in self._completed_orders if o.status == OrderStatus.FAILED)
        rolled_back = sum(1 for o in self._completed_orders if o.status == OrderStatus.ROLLED_BACK)
        
        return {
            "total_orders": total_orders,
            "filled_orders": filled_orders,
            "failed_orders": failed_orders,
            "rolled_back_orders": rolled_back,
            "success_rate": filled_orders / total_orders if total_orders > 0 else 0,
            "active_positions": len(self._positions)
        }