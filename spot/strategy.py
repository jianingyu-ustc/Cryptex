"""
Signal generation for spot auto-trading subsystem.
"""

import asyncio
import logging
from typing import Dict, List, Optional

from common.binance_client import BinanceClient
from .config import SpotTradingConfig, DEFAULT_SPOT_CONFIG
from .models import SpotSignal, SpotPosition

logger = logging.getLogger(__name__)


def _sma(values: List[float], period: int) -> float:
    if len(values) < period or period <= 0:
        return 0.0
    return sum(values[-period:]) / period


def _rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1 or period <= 0:
        return 50.0

    gains = 0.0
    losses = 0.0
    window = values[-(period + 1):]
    for i in range(1, len(window)):
        diff = window[i] - window[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class SpotStrategyEngine:
    """Simple trend-following strategy with RSI and stop conditions."""

    def __init__(self, client: BinanceClient, config: SpotTradingConfig = None):
        self.client = client
        self.config = config or DEFAULT_SPOT_CONFIG

    async def analyze_symbol(
        self,
        symbol: str,
        position: Optional[SpotPosition] = None
    ) -> SpotSignal:
        klines = await self.client.get_spot_klines(
            symbol=symbol,
            interval=self.config.kline_interval,
            limit=self.config.min_klines_required
        )
        if len(klines) < self.config.min_klines_required:
            return SpotSignal(
                symbol=symbol,
                action="HOLD",
                price=0.0,
                confidence=0.0,
                reason="insufficient_klines",
            )

        closes = [k["close"] for k in klines]
        current_price = closes[-1]
        fast_ma = _sma(closes, self.config.fast_ma_period)
        slow_ma = _sma(closes, self.config.slow_ma_period)
        rsi = _rsi(closes, self.config.rsi_period)

        momentum_base = closes[-3] if len(closes) >= 3 else closes[-2]
        momentum_pct = 0.0
        if momentum_base > 0:
            momentum_pct = (current_price - momentum_base) / momentum_base * 100

        ticker = await self.client.get_spot_ticker(symbol)
        volume_24h = ticker.volume_24h if ticker else 0.0
        trend_strength = ((fast_ma - slow_ma) / current_price * 100) if current_price > 0 else 0.0
        confidence = min(0.95, max(0.1, 0.5 + trend_strength * 3 + momentum_pct * 0.6))

        if position is None:
            if volume_24h < self.config.min_24h_quote_volume:
                return SpotSignal(
                    symbol=symbol,
                    action="HOLD",
                    price=current_price,
                    confidence=0.2,
                    reason=f"volume_too_low:{volume_24h:,.0f}",
                    fast_ma=fast_ma,
                    slow_ma=slow_ma,
                    rsi=rsi,
                    momentum_pct=momentum_pct,
                )

            if fast_ma > slow_ma and momentum_pct > 0 and 50 <= rsi <= self.config.rsi_buy_max:
                return SpotSignal(
                    symbol=symbol,
                    action="BUY",
                    price=current_price,
                    confidence=confidence,
                    reason="uptrend_entry",
                    fast_ma=fast_ma,
                    slow_ma=slow_ma,
                    rsi=rsi,
                    momentum_pct=momentum_pct,
                )

            return SpotSignal(
                symbol=symbol,
                action="HOLD",
                price=current_price,
                confidence=0.3,
                reason="no_entry_setup",
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                momentum_pct=momentum_pct,
            )

        pnl_pct = ((current_price - position.entry_price) / position.entry_price * 100) if position.entry_price > 0 else 0.0
        if pnl_pct <= -self.config.stop_loss_pct:
            return SpotSignal(
                symbol=symbol,
                action="SELL",
                price=current_price,
                confidence=0.95,
                reason=f"stop_loss:{pnl_pct:.2f}%",
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                momentum_pct=momentum_pct,
            )

        if pnl_pct >= self.config.take_profit_pct:
            return SpotSignal(
                symbol=symbol,
                action="SELL",
                price=current_price,
                confidence=0.9,
                reason=f"take_profit:{pnl_pct:.2f}%",
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                momentum_pct=momentum_pct,
            )

        if fast_ma < slow_ma and rsi <= self.config.rsi_sell_min:
            return SpotSignal(
                symbol=symbol,
                action="SELL",
                price=current_price,
                confidence=0.75,
                reason="trend_breakdown",
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                momentum_pct=momentum_pct,
            )

        return SpotSignal(
            symbol=symbol,
            action="HOLD",
            price=current_price,
            confidence=0.4,
            reason=f"hold_position:{pnl_pct:+.2f}%",
            fast_ma=fast_ma,
            slow_ma=slow_ma,
            rsi=rsi,
            momentum_pct=momentum_pct,
        )

    async def analyze_symbols(self, symbols: List[str], positions: Dict[str, SpotPosition]) -> List[SpotSignal]:
        tasks = [self.analyze_symbol(s, positions.get(s)) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals: List[SpotSignal] = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.error(f"Signal analysis failed for {symbol}: {result}")
                signals.append(
                    SpotSignal(symbol=symbol, action="HOLD", price=0, confidence=0.0, reason="analysis_error")
                )
            else:
                signals.append(result)
        return signals

