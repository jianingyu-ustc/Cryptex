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
        self.bar_index = 0
        self._last_sell_bar: Dict[str, int] = {}
        self.total_fees_paid = 0.0
        self.total_slippage_cost = 0.0
        self._day_anchor = datetime.now(timezone.utc).date()
        self._day_start_equity = self.initial_capital
        self._sim_time: Optional[datetime] = None

    def _now(self) -> datetime:
        if self._sim_time is not None:
            return self._sim_time
        return datetime.now(timezone.utc)

    def set_simulation_time(self, current_time: Optional[datetime]):
        """Set logical clock for backtest. None means real-time clock."""
        if current_time is None:
            self._sim_time = None
            return
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        self._sim_time = current_time.astimezone(timezone.utc)

    def _next_sim_order_id(self) -> str:
        self._sim_order_counter += 1
        return f"SIM_{int(self._now().timestamp())}_{self._sim_order_counter}"

    def _today_trade_count(self) -> int:
        today = self._now().date()
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
        return sum(p.unrealized_pnl for p in self.positions.values())

    def _account_value(self) -> float:
        return self.cash_balance + self._positions_market_value()

    def _refresh_day_anchor(self):
        today = self._now().date()
        if today != self._day_anchor:
            self._day_anchor = today
            self._day_start_equity = self._account_value()

    def _daily_return_pct(self) -> float:
        self._refresh_day_anchor()
        if self._day_start_equity <= 0:
            return 0.0
        return (self._account_value() - self._day_start_equity) / self._day_start_equity * 100

    def _daily_loss_limited(self) -> bool:
        if self.config.daily_loss_limit_pct <= 0:
            return False
        return self._daily_return_pct() <= -abs(self.config.daily_loss_limit_pct)

    def _in_cooldown(self, symbol: str) -> bool:
        if self.config.cooldown_bars <= 0:
            return False
        last_sell_bar = self._last_sell_bar.get(symbol)
        if last_sell_bar is None:
            return False
        return (self.bar_index - last_sell_bar) <= self.config.cooldown_bars

    def _exposure_pct(self) -> float:
        equity = self._account_value()
        if equity <= 0:
            return 0.0
        return self._positions_market_value() / equity * 100

    def _slippage_rate(self) -> float:
        return max(0.0, self.config.slippage_bps) / 10_000

    def _sync_trade_account_metrics(self, trade: SpotTrade):
        self._refresh_day_anchor()
        account_value = self._account_value()
        cumulative_pnl = account_value - self.initial_capital
        cumulative_return_pct = (cumulative_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0.0

        trade.cash_balance_after = self.cash_balance
        trade.account_value_after = account_value
        trade.cumulative_pnl_usdt = cumulative_pnl
        trade.cumulative_return_pct = cumulative_return_pct
        self.total_fees_paid += trade.fee_paid
        self.total_slippage_cost += trade.slippage_cost_usdt

    def advance_bar(self, steps: int = 1):
        self.bar_index += max(1, steps)

    async def mark_positions(self):
        """Update latest mark price for tracked positions."""
        self.advance_bar()
        self._refresh_day_anchor()
        if not self.client:
            return
        for symbol, pos in list(self.positions.items()):
            price = await self.client.get_spot_price(symbol)
            if not price:
                continue
            pos.last_price = price
            pos.max_price = max(pos.max_price, price)
            pos.peak_price = pos.max_price
            pos.unrealized_pnl = (pos.last_price - pos.entry_price) * pos.quantity - pos.fees_paid

    def _risk_based_qty_and_stop(self, signal: SpotSignal) -> tuple[float, float]:
        price = signal.price
        if price <= 0:
            return 0.0, 0.0

        stop_price = signal.stop_price
        if stop_price <= 0:
            stop_price = price * (1 - self.config.stop_loss_pct / 100)
        risk_per_unit = price - stop_price
        if risk_per_unit <= 0:
            return 0.0, stop_price

        equity = self._account_value()
        risk_amount = equity * max(0.0, self.config.risk_per_trade_pct) / 100
        if risk_amount <= 0:
            return 0.0, stop_price

        qty_by_risk = risk_amount / risk_per_unit
        notional_by_risk = qty_by_risk * price

        max_notional = min(self.cash_balance, self.config.usdt_per_trade)
        if max_notional <= 0:
            return 0.0, stop_price

        if self.config.max_total_exposure_pct > 0:
            max_exposure = equity * self.config.max_total_exposure_pct / 100
            remaining_exposure = max_exposure - self._positions_market_value()
            if remaining_exposure <= 0:
                return 0.0, stop_price
            max_notional = min(max_notional, remaining_exposure)

        target_notional = min(notional_by_risk, max_notional)
        qty = self._quantize_qty(price, target_notional)
        if qty * price > max_notional:
            qty = max(0.0, max_notional / price)
            qty = round(qty, 7)

        return qty, stop_price

    async def execute_signal(self, signal: SpotSignal) -> Optional[SpotTrade]:
        """Execute BUY/SELL signal in dry-run or live mode."""
        if signal.action not in {"BUY", "SELL"}:
            return None
        trade_time = self._now()

        if signal.action == "BUY":
            if self._today_trade_count() >= self.config.max_daily_trades:
                logger.info("Skip BUY %s: max_daily_trades reached", signal.symbol)
                return None
            if signal.symbol in self.positions:
                return None
            if len(self.positions) >= self.config.max_open_positions:
                logger.info("Skip BUY %s: max_open_positions reached", signal.symbol)
                return None
            if self._in_cooldown(signal.symbol):
                logger.info("Skip BUY %s: cooldown active", signal.symbol)
                return None
            if self._daily_loss_limited():
                logger.info("Skip BUY %s: daily_loss_limit reached", signal.symbol)
                return None
            if self.cash_balance <= 0:
                logger.info("Skip BUY %s: no available capital", signal.symbol)
                return None

            qty, stop_price = self._risk_based_qty_and_stop(signal)
            if qty <= 0:
                logger.warning("Skip BUY %s: invalid quantity", signal.symbol)
                return None

            fill_price = signal.price
            expected_price = signal.price
            slippage_cost = 0.0
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
            else:
                fill_price = signal.price * (1 + self._slippage_rate())

            notional = qty * fill_price
            if notional <= 0:
                logger.warning("Skip BUY %s: invalid notional", signal.symbol)
                return None
            fee = notional * max(0.0, self.config.fee_bps) / 10_000
            total_cash_need = notional + fee
            slippage_cost = max(0.0, qty * (fill_price - expected_price))
            if not self.config.dry_run and total_cash_need > self.cash_balance:
                logger.warning("BUY %s exceeds local cash tracking: %.4f > %.4f", signal.symbol, notional, self.cash_balance)
            if self.config.dry_run and total_cash_need > self.cash_balance:
                logger.info("Skip BUY %s: insufficient capital", signal.symbol)
                return None

            self.cash_balance = max(0.0, self.cash_balance - total_cash_need)
            self.positions[signal.symbol] = SpotPosition(
                symbol=signal.symbol,
                quantity=qty,
                entry_price=fill_price,
                entry_time=trade_time,
                stop_price=stop_price,
                max_price=fill_price,
                peak_price=fill_price,
                last_price=fill_price,
                fees_paid=fee,
                unrealized_pnl=-fee,
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
                reasons=signal.reasons,
                fee_paid=fee,
                slippage_bps=(abs(fill_price - expected_price) / expected_price * 10_000) if expected_price > 0 else 0.0,
                slippage_cost_usdt=slippage_cost,
                expected_price=expected_price,
                timestamp=trade_time,
            )
            self._sync_trade_account_metrics(trade)
            self.trades.append(trade)
            return trade

        # SELL
        position = self.positions.get(signal.symbol)
        if not position:
            return None

        qty = position.quantity
        if qty <= 0:
            self.positions.pop(signal.symbol, None)
            return None

        fill_price = signal.price
        expected_price = signal.price
        slippage_cost = 0.0
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
        else:
            fill_price = signal.price * (1 - self._slippage_rate())

        notional = qty * fill_price
        fee = notional * max(0.0, self.config.fee_bps) / 10_000
        self.cash_balance += max(0.0, notional - fee)
        slippage_cost = max(0.0, qty * (expected_price - fill_price))
        realized_pnl = (notional - fee) - (qty * position.entry_price + position.fees_paid)
        position.realized_pnl = realized_pnl
        trade = SpotTrade(
            symbol=signal.symbol,
            side="SELL",
            quantity=qty,
            price=fill_price,
            notional=notional,
            dry_run=self.config.dry_run,
            order_id=order_id,
            reason=signal.reason,
            reasons=signal.reasons,
            fee_paid=fee,
            slippage_bps=(abs(fill_price - expected_price) / expected_price * 10_000) if expected_price > 0 else 0.0,
            slippage_cost_usdt=slippage_cost,
            expected_price=expected_price,
            realized_pnl_usdt=realized_pnl,
            timestamp=trade_time,
        )
        self.positions.pop(signal.symbol, None)
        self._last_sell_bar[signal.symbol] = self.bar_index
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
            "fees_paid_usdt": self.total_fees_paid,
            "slippage_cost_usdt": self.total_slippage_cost,
            "exposure_pct": self._exposure_pct(),
            "daily_return_pct": self._daily_return_pct(),
            "daily_loss_limited": self._daily_loss_limited(),
        }
