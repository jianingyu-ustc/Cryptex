"""
Order execution and position tracking for spot auto-trading subsystem.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from common.binance_client import BinanceClient
from .config import SpotTradingConfig, DEFAULT_SPOT_CONFIG
from .models import SpotSignal, SpotPosition, SpotTrade

logger = logging.getLogger(__name__)


class SpotExecutionEngine:
    """Execute buy/sell signals and maintain local position state."""

    def __init__(self, client: BinanceClient, config: SpotTradingConfig = None):
        self.client = client
        self.config = config or DEFAULT_SPOT_CONFIG
        self.positions: Dict[str, SpotPosition] = {}
        self.trades: List[SpotTrade] = []
        self.initial_capital = max(100.0, float(self.config.initial_capital))
        self.cash_balance = self.initial_capital
        self._sim_order_counter = 0

    def _next_sim_order_id(self) -> str:
        self._sim_order_counter += 1
        return f"SIM_{int(datetime.now(timezone.utc).timestamp())}_{self._sim_order_counter}"

    def _today_trade_count(self) -> int:
        today = datetime.now(timezone.utc).date()
        return sum(1 for t in self.trades if t.timestamp.date() == today)

    @staticmethod
    def _quantize_qty(price: float, usdt: float) -> float:
        if price <= 0:
            return 0.0
        qty = usdt / price
        if qty >= 1:
            return round(qty, 4)
        if qty >= 0.1:
            return round(qty, 5)
        if qty >= 0.01:
            return round(qty, 6)
        return round(qty, 7)

    def _positions_market_value(self) -> float:
        return sum(p.market_value() for p in self.positions.values())

    def _unrealized_pnl(self) -> float:
        return sum((p.last_price - p.entry_price) * p.quantity for p in self.positions.values())

    def _account_value(self) -> float:
        return self.cash_balance + self._positions_market_value()

    def _sync_trade_account_metrics(self, trade: SpotTrade):
        account_value = self._account_value()
        cumulative_pnl = account_value - self.initial_capital
        cumulative_return_pct = (cumulative_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0.0

        trade.cash_balance_after = self.cash_balance
        trade.account_value_after = account_value
        trade.cumulative_pnl_usdt = cumulative_pnl
        trade.cumulative_return_pct = cumulative_return_pct

    async def mark_positions(self):
        """Update latest mark price for tracked positions."""
        for symbol, pos in list(self.positions.items()):
            price = await self.client.get_spot_price(symbol)
            if not price:
                continue
            pos.last_price = price
            pos.peak_price = max(pos.peak_price, price)

    async def execute_signal(self, signal: SpotSignal) -> Optional[SpotTrade]:
        """Execute BUY/SELL signal in dry-run or live mode."""
        if signal.action not in {"BUY", "SELL"}:
            return None

        if signal.action == "BUY":
            if self._today_trade_count() >= self.config.max_daily_trades:
                logger.info("Skip BUY %s: max_daily_trades reached", signal.symbol)
                return None
            if signal.symbol in self.positions:
                return None
            if len(self.positions) >= self.config.max_open_positions:
                logger.info("Skip BUY %s: max_open_positions reached", signal.symbol)
                return None

            if self.cash_balance <= 0:
                logger.info("Skip BUY %s: no available capital", signal.symbol)
                return None

            allocation = min(self.config.usdt_per_trade, self.cash_balance)
            qty = self._quantize_qty(signal.price, allocation)
            if qty <= 0:
                logger.warning("Skip BUY %s: invalid quantity", signal.symbol)
                return None

            fill_price = signal.price
            order_id = self._next_sim_order_id()
            if not self.config.dry_run:
                order = await self.client.spot_market_order(signal.symbol, "BUY", qty)
                if not order:
                    logger.error("BUY failed for %s", signal.symbol)
                    return None
                if order.filled_qty > 0:
                    qty = order.filled_qty
                if order.avg_price > 0:
                    fill_price = order.avg_price
                order_id = order.order_id

            notional = qty * fill_price
            if notional <= 0:
                logger.warning("Skip BUY %s: invalid notional", signal.symbol)
                return None
            if not self.config.dry_run and notional > self.cash_balance:
                logger.warning("BUY %s exceeds local cash tracking: %.4f > %.4f", signal.symbol, notional, self.cash_balance)
            if self.config.dry_run and notional > self.cash_balance:
                logger.info("Skip BUY %s: insufficient capital", signal.symbol)
                return None

            self.cash_balance = max(0.0, self.cash_balance - notional)
            self.positions[signal.symbol] = SpotPosition(
                symbol=signal.symbol,
                quantity=qty,
                entry_price=fill_price,
                entry_time=datetime.now(timezone.utc),
                peak_price=fill_price,
                last_price=fill_price,
            )

            trade = SpotTrade(
                symbol=signal.symbol,
                side="BUY",
                quantity=qty,
                price=fill_price,
                notional=notional,
                dry_run=self.config.dry_run,
                order_id=order_id,
                reason=signal.reason,
            )
            self._sync_trade_account_metrics(trade)
            self.trades.append(trade)
            return trade

        # SELL
        position = self.positions.get(signal.symbol)
        if not position:
            return None
        if self._today_trade_count() >= self.config.max_daily_trades:
            logger.info("Skip SELL %s: max_daily_trades reached", signal.symbol)
            return None

        qty = position.quantity
        if qty <= 0:
            self.positions.pop(signal.symbol, None)
            return None

        fill_price = signal.price
        order_id = self._next_sim_order_id()
        if not self.config.dry_run:
            order = await self.client.spot_market_order(signal.symbol, "SELL", qty)
            if not order:
                logger.error("SELL failed for %s", signal.symbol)
                return None
            if order.filled_qty > 0:
                qty = order.filled_qty
            if order.avg_price > 0:
                fill_price = order.avg_price
            order_id = order.order_id

        notional = qty * fill_price
        self.cash_balance += notional
        realized_pnl = qty * (fill_price - position.entry_price)
        trade = SpotTrade(
            symbol=signal.symbol,
            side="SELL",
            quantity=qty,
            price=fill_price,
            notional=notional,
            dry_run=self.config.dry_run,
            order_id=order_id,
            reason=signal.reason,
            realized_pnl_usdt=realized_pnl,
        )
        self.positions.pop(signal.symbol, None)
        self._sync_trade_account_metrics(trade)
        self.trades.append(trade)
        return trade

    def get_stats(self) -> Dict:
        """Basic execution statistics."""
        sells = [t for t in self.trades if t.side == "SELL"]
        wins = [t for t in sells if t.realized_pnl_usdt > 0]
        realized = sum(t.realized_pnl_usdt for t in sells)
        market_value = self._positions_market_value()
        unrealized = self._unrealized_pnl()
        account_value = self.cash_balance + market_value
        total_pnl = account_value - self.initial_capital
        return {
            "total_trades": len(self.trades),
            "closed_trades": len(sells),
            "winning_trades": len(wins),
            "win_rate": (len(wins) / len(sells) * 100) if sells else 0.0,
            "realized_pnl_usdt": realized,
            "unrealized_pnl_usdt": unrealized,
            "cash_balance_usdt": self.cash_balance,
            "market_value_usdt": market_value,
            "account_value_usdt": account_value,
            "initial_capital_usdt": self.initial_capital,
            "total_pnl_usdt": total_pnl,
            "total_return_pct": (total_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0.0,
            "open_positions": len(self.positions),
        }
