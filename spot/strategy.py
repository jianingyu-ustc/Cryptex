"""
Signal generation for spot auto-trading subsystem.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

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


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if period <= 0 or len(closes) < period + 1 or len(highs) != len(lows) or len(lows) != len(closes):
        return 0.0
    tr_values: List[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_values.append(tr)
    if len(tr_values) < period:
        return 0.0
    return sum(tr_values[-period:]) / period


def _adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if period <= 0 or len(closes) < (period * 2 + 1):
        return 0.0
    if len(highs) != len(lows) or len(lows) != len(closes):
        return 0.0

    plus_dm = [0.0]
    minus_dm = [0.0]
    tr_values = [0.0]

    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        tr_values.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    dx_values: List[float] = []
    for i in range(period, len(closes)):
        tr_sum = sum(tr_values[i - period + 1:i + 1])
        if tr_sum <= 0:
            dx_values.append(0.0)
            continue
        plus_sum = sum(plus_dm[i - period + 1:i + 1])
        minus_sum = sum(minus_dm[i - period + 1:i + 1])
        plus_di = 100.0 * plus_sum / tr_sum
        minus_di = 100.0 * minus_sum / tr_sum
        denom = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0
        dx_values.append(dx)

    if len(dx_values) < period:
        return 0.0
    return sum(dx_values[-period:]) / period


def _market_state_ok(adx: float, trend_strength: float, config: SpotTradingConfig) -> Tuple[bool, str]:
    if adx > 0:
        if adx >= config.adx_min:
            return True, f"adx_ok:{adx:.1f}"
        return False, f"adx_low:{adx:.1f}<{config.adx_min:.1f}"

    if trend_strength >= config.trend_strength_min:
        return True, f"trend_strength_ok:{trend_strength:.4f}"
    return False, f"trend_strength_low:{trend_strength:.4f}<{config.trend_strength_min:.4f}"


class SpotStrategyEngine:
    """Trend + pullback entry + ATR risk controls strategy."""

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
                reasons=["insufficient_klines"],
            )

        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        closes = [k["close"] for k in klines]
        current_price = closes[-1]
        prev_close = closes[-2]
        fast_ma = _sma(closes, self.config.fast_ma_period)
        slow_ma = _sma(closes, self.config.slow_ma_period)
        rsi = _rsi(closes, self.config.rsi_period)
        atr = _atr(highs, lows, closes, self.config.atr_period)
        adx = _adx(highs, lows, closes, self.config.adx_period)
        trend_strength = abs(fast_ma - slow_ma) / current_price if current_price > 0 else 0.0

        momentum_base = closes[-3] if len(closes) >= 3 else closes[-2]
        momentum_pct = 0.0
        if momentum_base > 0:
            momentum_pct = (current_price - momentum_base) / momentum_base * 100

        ticker = await self.client.get_spot_ticker(symbol)
        volume_24h = ticker.volume_24h if ticker else 0.0
        confidence = min(
            0.97,
            max(0.1, 0.35 + trend_strength * 25 + max(momentum_pct, 0.0) * 0.2 + (adx / 100.0) * 0.3),
        )
        market_ok, market_reason = _market_state_ok(adx, trend_strength, self.config)

        if position is None:
            reasons: List[str] = []
            if volume_24h < self.config.min_24h_quote_volume:
                reasons.append(f"volume_too_low:{volume_24h:,.0f}")
                return SpotSignal(
                    symbol=symbol,
                    action="HOLD",
                    price=current_price,
                    confidence=0.2,
                    reason=reasons[0],
                    reasons=reasons,
                    fast_ma=fast_ma,
                    slow_ma=slow_ma,
                    rsi=rsi,
                    atr=atr,
                    adx=adx,
                    trend_strength=trend_strength,
                    momentum_pct=momentum_pct,
                )

            if fast_ma <= slow_ma:
                reasons.append("trend_filter_failed:fast_ma<=slow_ma")
            if not market_ok:
                reasons.append(market_reason)

            pullback_tol = max(0.0001, self.config.pullback_tol)
            recent_low = min(lows[-3:])
            pullback_hit = (
                recent_low <= fast_ma * (1 + pullback_tol)
                and recent_low >= fast_ma * (1 - pullback_tol * 2)
            )
            if not pullback_hit:
                reasons.append("no_pullback_to_fast_ma")

            reclaim_fast_ma = prev_close <= fast_ma < current_price
            breakout_buffer = max(0.0005, pullback_tol / 2)
            small_breakout = current_price >= prev_close * (1 + breakout_buffer)
            confirm_entry = current_price > fast_ma and (reclaim_fast_ma or small_breakout)
            if not confirm_entry:
                reasons.append("no_reclaim_or_breakout_confirmation")

            if not (self.config.rsi_buy_min <= rsi <= self.config.rsi_buy_max):
                reasons.append(
                    f"rsi_out_of_range:{rsi:.1f} not in [{self.config.rsi_buy_min:.1f},{self.config.rsi_buy_max:.1f}]"
                )

            if not reasons:
                stop_price = current_price - self.config.atr_k * atr if atr > 0 else 0.0
                if stop_price <= 0:
                    stop_price = current_price * (1 - self.config.stop_loss_pct / 100)
                buy_reasons = ["trend_ok", market_reason, "pullback_hit", "entry_confirmed", "rsi_in_range"]
                return SpotSignal(
                    symbol=symbol,
                    action="BUY",
                    price=current_price,
                    confidence=confidence,
                    reason=buy_reasons[0],
                    reasons=buy_reasons,
                    fast_ma=fast_ma,
                    slow_ma=slow_ma,
                    rsi=rsi,
                    atr=atr,
                    adx=adx,
                    trend_strength=trend_strength,
                    stop_price=stop_price,
                    momentum_pct=momentum_pct,
                )

            return SpotSignal(
                symbol=symbol,
                action="HOLD",
                price=current_price,
                confidence=0.3,
                reason=reasons[0] if reasons else "no_entry_setup",
                reasons=reasons if reasons else ["no_entry_setup"],
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                atr=atr,
                adx=adx,
                trend_strength=trend_strength,
                momentum_pct=momentum_pct,
            )

        pnl_pct = ((current_price - position.entry_price) / position.entry_price * 100) if position.entry_price > 0 else 0.0
        dynamic_stop = position.stop_price
        if dynamic_stop <= 0:
            dynamic_stop = position.entry_price - self.config.atr_k * atr if atr > 0 else position.entry_price * (
                1 - self.config.stop_loss_pct / 100
            )

        if current_price <= dynamic_stop:
            reasons = [f"atr_stop_hit:{dynamic_stop:.4f}", f"pnl:{pnl_pct:+.2f}%"]
            return SpotSignal(
                symbol=symbol,
                action="SELL",
                price=current_price,
                confidence=0.95,
                reason=reasons[0],
                reasons=reasons,
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                atr=atr,
                adx=adx,
                trend_strength=trend_strength,
                stop_price=dynamic_stop,
                momentum_pct=momentum_pct,
            )

        max_price = max(position.max_price, position.last_price, current_price)
        trail_stop = max_price - self.config.trail_atr_k * atr if atr > 0 else 0.0
        if atr > 0 and current_price <= trail_stop and max_price > position.entry_price:
            reasons = [f"trail_stop_hit:{trail_stop:.4f}", f"max_price:{max_price:.4f}", f"pnl:{pnl_pct:+.2f}%"]
            return SpotSignal(
                symbol=symbol,
                action="SELL",
                price=current_price,
                confidence=0.9,
                reason=reasons[0],
                reasons=reasons,
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                atr=atr,
                adx=adx,
                trend_strength=trend_strength,
                stop_price=dynamic_stop,
                momentum_pct=momentum_pct,
            )

        if fast_ma < slow_ma and rsi <= self.config.rsi_sell_min:
            reasons = [f"trend_breakdown:{fast_ma:.4f}<{slow_ma:.4f}", f"rsi_weak:{rsi:.1f}<={self.config.rsi_sell_min:.1f}"]
            return SpotSignal(
                symbol=symbol,
                action="SELL",
                price=current_price,
                confidence=0.75,
                reason=reasons[0],
                reasons=reasons,
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                atr=atr,
                adx=adx,
                trend_strength=trend_strength,
                stop_price=dynamic_stop,
                momentum_pct=momentum_pct,
            )

        hold_reasons = [f"hold_position:{pnl_pct:+.2f}%", f"active_stop:{dynamic_stop:.4f}"]
        if atr > 0:
            hold_reasons.append(f"trail_stop:{trail_stop:.4f}")
        return SpotSignal(
            symbol=symbol,
            action="HOLD",
            price=current_price,
            confidence=0.4,
            reason=hold_reasons[0],
            reasons=hold_reasons,
            fast_ma=fast_ma,
            slow_ma=slow_ma,
            rsi=rsi,
            atr=atr,
            adx=adx,
            trend_strength=trend_strength,
            stop_price=dynamic_stop,
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
                    SpotSignal(
                        symbol=symbol,
                        action="HOLD",
                        price=0,
                        confidence=0.0,
                        reason="analysis_error",
                        reasons=["analysis_error"],
                    )
                )
            else:
                signals.append(result)
        return signals
