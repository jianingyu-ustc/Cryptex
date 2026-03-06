"""
Signal generation for spot auto-trading subsystem.
"""

import asyncio
import logging
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from common.binance_client import BinanceClient
from .config import DEFAULT_SPOT_CONFIG, SpotTradingConfig, StrategyParams
from .models import DecisionContext, SpotPosition, SpotSignal

logger = logging.getLogger(__name__)


def _sma(values: List[float], period: int) -> float:
    """简单移动平均（SMA）。"""
    if len(values) < period or period <= 0:
        return 0.0
    return sum(values[-period:]) / period


def _rsi(values: List[float], period: int = 14) -> float:
    """相对强弱指数（RSI），用于动量与超买超卖过滤。"""
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
    """平均真实波幅（ATR），用于波动率风控。"""
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
    """平均趋向指数（ADX），用于判断趋势强弱。"""
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


class SpotDecisionEngine:
    """统一决策引擎：同一套规则同时服务回测与实时模式。"""

    @staticmethod
    def _series_has_source(series: List[Dict[str, Any]]) -> bool:
        """判断对齐后的辅助序列在当前 bar 是否已有真实来源数据。"""
        if not series:
            return False
        last = series[-1]
        return isinstance(last, dict) and last.get("source_time") is not None

    @staticmethod
    def _market_state_ok(adx: float, trend_strength: float, params: StrategyParams) -> Tuple[bool, str]:
        """市场状态过滤：优先 ADX，不可用时退化为趋势强度代理。"""
        if adx > 0:
            if adx >= params.adx_min:
                return True, f"adx_ok:{adx:.1f}"
            return False, f"adx_low:{adx:.1f}<{params.adx_min:.1f}"
        if trend_strength >= params.trend_strength_min:
            return True, f"trend_strength_ok:{trend_strength:.4f}"
        return False, f"trend_strength_low:{trend_strength:.4f}<{params.trend_strength_min:.4f}"

    @staticmethod
    def _edge_over_cost_ok(
        current_price: float,
        fast_ma: float,
        slow_ma: float,
        atr: float,
        fee_bps: float,
        slippage_bps: float,
        funding_rate: float,
        params: StrategyParams,
    ) -> Tuple[bool, str]:
        """检查入场预期 edge 是否足以覆盖双边成本与缓冲。"""
        if current_price <= 0:
            return False, "edge_over_cost_fail:invalid_price"
        atr_pct = atr / current_price if atr > 0 else 0.0
        trend_space_pct = max(0.0, (fast_ma - slow_ma) / current_price)
        expected_edge_pct = max(atr_pct, trend_space_pct)
        round_trip_cost_pct = 2.0 * max(0.0, fee_bps + slippage_bps) / 10_000
        funding_edge_adj = funding_rate * params.funding_cost_buffer_k
        required_edge_pct = round_trip_cost_pct * params.cost_buffer_k + funding_edge_adj + params.min_edge_over_cost

        if atr_pct < params.min_atr_pct:
            return (
                False,
                f"min_atr_pct_fail:atr={atr_pct:.4%}<min={params.min_atr_pct:.4%}",
            )
        if expected_edge_pct < required_edge_pct:
            return (
                False,
                (
                    "edge_over_cost_fail:"
                    f"expected={expected_edge_pct:.4%},required={required_edge_pct:.4%},"
                    f"cost={round_trip_cost_pct:.4%},funding={funding_rate:.4%},buffer={params.cost_buffer_k:.2f}"
                ),
            )
        return (
            True,
            (
                "edge_over_cost_ok:"
                f"expected={expected_edge_pct:.4%},required={required_edge_pct:.4%},"
                f"cost={round_trip_cost_pct:.4%},funding={funding_rate:.4%}"
            ),
        )

    @staticmethod
    def _premium_zscore(premium_series: List[Dict[str, Any]], premium_close: float) -> float:
        closes = [
            float(k.get("close", 0.0))
            for k in premium_series
            if isinstance(k, dict) and k.get("close") is not None
        ]
        if len(closes) < 2:
            return 0.0
        mu = mean(closes)
        sigma = pstdev(closes)
        if sigma <= 1e-12:
            return 0.0
        return (premium_close - mu) / sigma

    @classmethod
    def _derivatives_state_gate(
        cls,
        current_price: float,
        context: DecisionContext,
        params: StrategyParams,
    ) -> Tuple[bool, List[str], str, float, float]:
        reasons: List[str] = []
        mark_close = float(context.mark_price_close)
        premium_close = float(context.premium_close)
        funding_rate = float(context.funding_rate)

        mark_available = cls._series_has_source(context.mark_kline_series) and mark_close > 0
        premium_available = cls._series_has_source(context.premium_kline_series)
        funding_available = cls._series_has_source(context.funding_rate_series)
        missing_sources: List[str] = []
        if not mark_available:
            missing_sources.append("mark_data_unavailable")
        if not premium_available:
            missing_sources.append("premium_data_unavailable")
        if not funding_available:
            missing_sources.append("funding_data_unavailable")

        mark_spot_gap = abs(mark_close - current_price) / current_price if mark_available and current_price > 0 else 0.0
        premium_z = cls._premium_zscore(context.premium_kline_series, premium_close) if premium_available else 0.0

        if mark_available and mark_spot_gap > params.max_mark_spot_gap_pct:
            reasons.append(
                f"mark_spot_gap_fail:gap={mark_spot_gap:.4%}>{params.max_mark_spot_gap_pct:.4%}"
            )
        if mark_available and mark_spot_gap > params.max_mark_spot_diverge:
            reasons.append(
                f"mark_spot_diverge_fail:gap={mark_spot_gap:.4%}>{params.max_mark_spot_diverge:.4%}"
            )

        if premium_available and abs(premium_close) > params.premium_abs_max:
            reasons.append(
                f"premium_extreme_fail:abs={abs(premium_close):.4%}>{params.premium_abs_max:.4%}"
            )

        premium_gate_ok = True
        if premium_available:
            premium_gate_ok = (
                abs(premium_close) <= params.premium_abs_entry_max
                or (params.premium_z_entry_min <= premium_z <= params.premium_z_entry_max)
            )
        if not premium_gate_ok:
            reasons.append(
                "premium_overheat_fail:"
                f"abs={abs(premium_close):.4%},z={premium_z:.3f},"
                f"abs_max={params.premium_abs_entry_max:.4%},"
                f"z_range=[{params.premium_z_entry_min:.2f},{params.premium_z_entry_max:.2f}]"
            )

        if funding_available and funding_rate > params.funding_long_max:
            reasons.append(
                f"funding_too_high_fail:{funding_rate:.4%}>{params.funding_long_max:.4%}"
            )

        ok = len(reasons) == 0
        missing_text = ",".join(missing_sources) if missing_sources else "none"
        ok_reason = (
            "derivatives_state_ok:"
            f"mark_gap={mark_spot_gap:.4%},premium={premium_close:.4%},"
            f"premium_z={premium_z:.3f},funding={funding_rate:.4%},missing={missing_text}"
        )
        return ok, reasons, ok_reason, mark_spot_gap, premium_z

    def decide(self, context: DecisionContext, params: StrategyParams) -> SpotSignal:
        """核心决策函数：根据上下文输出 BUY/SELL/HOLD 与 reasons。"""
        params = params.repair()
        klines = context.recent_klines
        if len(klines) < params.min_klines_required:
            return SpotSignal(
                symbol=context.symbol,
                action="HOLD",
                price=context.bar_close,
                confidence=0.0,
                reason="insufficient_klines",
                reasons=["insufficient_klines"],
            )

        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        closes = [float(k["close"]) for k in klines]
        current_price = float(context.bar_close)

        fast_ma = _sma(closes, params.fast_ma_len)
        slow_ma = _sma(closes, params.slow_ma_len)
        rsi = _rsi(closes, params.rsi_len)
        atr = _atr(highs, lows, closes, params.atr_len)
        atr_prev = _atr(highs[:-1], lows[:-1], closes[:-1], params.atr_len) if len(closes) > params.atr_len + 1 else atr
        adx = _adx(highs, lows, closes, params.adx_len)
        trend_strength = abs(fast_ma - slow_ma) / current_price if current_price > 0 else 0.0

        momentum_base = closes[-3] if len(closes) >= 3 else closes[-2]
        momentum_pct = (current_price - momentum_base) / momentum_base * 100 if momentum_base > 0 else 0.0
        confidence = min(
            0.97,
            max(0.1, 0.35 + trend_strength * 25 + max(momentum_pct, 0.0) * 0.2 + (adx / 100.0) * 0.3),
        )
        market_ok, market_reason = self._market_state_ok(adx, trend_strength, params)
        mark_price_close = float(context.mark_price_close)
        premium_close = float(context.premium_close)
        funding_rate = float(context.funding_rate)
        mark_spot_gap = abs(mark_price_close - current_price) / current_price if mark_price_close > 0 and current_price > 0 else 0.0
        premium_z = self._premium_zscore(context.premium_kline_series, premium_close) if context.premium_kline_series else 0.0

        if not context.has_position:
            reasons: List[str] = [f"decision_timing:{context.decision_timing}"]
            if context.quote_volume_24h < params.min_24h_quote_volume:
                reasons.append(f"volume_too_low:{context.quote_volume_24h:,.0f}")
                return SpotSignal(
                    symbol=context.symbol,
                    action="HOLD",
                    price=current_price,
                    confidence=0.2,
                    reason=reasons[-1],
                    reasons=reasons,
                    fast_ma=fast_ma,
                    slow_ma=slow_ma,
                    rsi=rsi,
                    atr=atr,
                    adx=adx,
                    trend_strength=trend_strength,
                    momentum_pct=momentum_pct,
                )

            trend_ok = fast_ma > slow_ma
            if not trend_ok:
                reasons.append("trend_filter_failed:fast_ma<=slow_ma")

            if not market_ok:
                reasons.append(market_reason)

            pullback_tol = max(0.0001, params.pullback_tol)
            recent_low = min(lows[-3:])
            pullback_hit = (
                recent_low <= fast_ma * (1 + pullback_tol)
                and recent_low >= fast_ma * (1 - pullback_tol * 2)
            )
            if not pullback_hit:
                reasons.append("no_pullback_to_fast_ma")

            atr_band_price = fast_ma + params.band_atr_k * atr if atr > 0 else float("inf")
            pct_band_price = fast_ma * (1 + params.ma_breakout_band)
            atr_band_hit = atr > 0 and current_price >= atr_band_price
            pct_band_hit = current_price >= pct_band_price
            confirm_entry = atr_band_hit or pct_band_hit
            confirm_reason = (
                "entry_confirmed:atr_band"
                if atr_band_hit and not pct_band_hit
                else "entry_confirmed:pct_band"
                if pct_band_hit and not atr_band_hit
                else "entry_confirmed:atr_or_pct_band"
            )
            if not confirm_entry:
                atr_text = f"{atr_band_price:.4f}" if atr > 0 else "na"
                reasons.append(
                    f"entry_band_fail:close={current_price:.4f},atr_band={atr_text},pct_band={pct_band_price:.4f}"
                )

            rsi_ok = params.rsi_buy_min <= rsi <= params.rsi_buy_max
            if not rsi_ok:
                reasons.append(
                    f"rsi_out_of_range:{rsi:.1f} not in [{params.rsi_buy_min:.1f},{params.rsi_buy_max:.1f}]"
                )

            edge_ok, edge_reason = self._edge_over_cost_ok(
                current_price=current_price,
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                atr=atr,
                fee_bps=context.fee_bps,
                slippage_bps=context.slippage_bps,
                funding_rate=funding_rate,
                params=params,
            )
            if not edge_ok:
                reasons.append(edge_reason)

            derivatives_ok, derivatives_fail_reasons, derivatives_ok_reason, _, _ = self._derivatives_state_gate(
                current_price=current_price,
                context=context,
                params=params,
            )
            if not derivatives_ok:
                reasons.extend(derivatives_fail_reasons)

            if trend_ok and market_ok and pullback_hit and confirm_entry and rsi_ok and edge_ok and derivatives_ok:
                stop_price = current_price - params.atr_k * atr if atr > 0 else 0.0
                if stop_price <= 0:
                    stop_price = current_price * (1 - 0.02)
                buy_reasons = [
                    f"decision_timing:{context.decision_timing}",
                    "trend_ok",
                    market_reason,
                    "pullback_hit",
                    confirm_reason,
                    "rsi_in_range",
                    edge_reason,
                    derivatives_ok_reason,
                ]
                return SpotSignal(
                    symbol=context.symbol,
                    action="BUY",
                    price=current_price,
                    confidence=confidence,
                    reason=buy_reasons[1],
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
                symbol=context.symbol,
                action="HOLD",
                price=current_price,
                confidence=0.3,
                reason=reasons[-1] if reasons else "no_entry_setup",
                reasons=reasons if reasons else ["no_entry_setup"],
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                rsi=rsi,
                atr=atr,
                adx=adx,
                trend_strength=trend_strength,
                momentum_pct=momentum_pct,
            )

        entry_price = max(0.0, context.entry_price)
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
        stop_atr = atr_prev if atr_prev > 0 else atr
        dynamic_stop = context.stop_price
        if dynamic_stop <= 0 and entry_price > 0:
            dynamic_stop = entry_price - params.atr_k * stop_atr if stop_atr > 0 else entry_price * (1 - 0.02)

        if params.max_mark_spot_gap_exit > 0 and mark_price_close > 0 and mark_spot_gap >= params.max_mark_spot_gap_exit:
            reasons = [
                f"decision_timing:{context.decision_timing}",
                f"emergency_mark_gap_exit:{mark_spot_gap:.4%}>={params.max_mark_spot_gap_exit:.4%}",
                f"mark={mark_price_close:.4f},spot={current_price:.4f}",
            ]
            return SpotSignal(
                symbol=context.symbol,
                action="SELL",
                price=current_price,
                confidence=0.99,
                reason=reasons[1],
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

        overheat_hit = (
            params.enable_overheat_derisk_exit
            and pnl_pct >= params.overheat_exit_min_pnl_pct
            and funding_rate >= params.overheat_exit_funding_min
            and abs(premium_close) >= params.overheat_exit_premium_abs_min
        )
        if overheat_hit:
            reasons = [
                f"decision_timing:{context.decision_timing}",
                (
                    "overheat_derisk_exit:"
                    f"pnl={pnl_pct:+.2f}%,funding={funding_rate:.4%},"
                    f"premium={premium_close:.4%},premium_z={premium_z:.3f}"
                ),
                f"mark_spot_gap:{mark_spot_gap:.4%}",
            ]
            return SpotSignal(
                symbol=context.symbol,
                action="SELL",
                price=current_price,
                confidence=0.85,
                reason=reasons[1],
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

        if current_price <= dynamic_stop:
            reasons = [
                f"decision_timing:{context.decision_timing}",
                f"atr_stop_hit:{dynamic_stop:.4f}",
                f"pnl:{pnl_pct:+.2f}%",
            ]
            return SpotSignal(
                symbol=context.symbol,
                action="SELL",
                price=current_price,
                confidence=0.95,
                reason=reasons[1],
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

        max_price = max(context.max_price, current_price)
        trail_stop = max_price - params.trail_atr_k * stop_atr if stop_atr > 0 else 0.0
        if stop_atr > 0 and current_price <= trail_stop and max_price > entry_price:
            reasons = [
                f"decision_timing:{context.decision_timing}",
                f"trail_stop_hit:{trail_stop:.4f}",
                f"max_price:{max_price:.4f}",
                f"pnl:{pnl_pct:+.2f}%",
            ]
            return SpotSignal(
                symbol=context.symbol,
                action="SELL",
                price=current_price,
                confidence=0.9,
                reason=reasons[1],
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

        trend_breakdown = fast_ma < slow_ma and rsi <= params.rsi_sell_min
        if trend_breakdown:
            reasons = [
                f"decision_timing:{context.decision_timing}",
                f"trend_breakdown:{fast_ma:.4f}<{slow_ma:.4f}",
                f"rsi_weak:{rsi:.1f}<={params.rsi_sell_min:.1f}",
            ]
            return SpotSignal(
                symbol=context.symbol,
                action="SELL",
                price=current_price,
                confidence=0.75,
                reason=reasons[1],
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

        hold_reasons = [
            f"decision_timing:{context.decision_timing}",
            f"hold_position:{pnl_pct:+.2f}%",
            f"active_stop:{dynamic_stop:.4f}",
        ]
        if atr > 0:
            hold_reasons.append(f"trail_stop:{trail_stop:.4f}")
        return SpotSignal(
            symbol=context.symbol,
            action="HOLD",
            price=current_price,
            confidence=0.4,
            reason=hold_reasons[1],
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


class SpotStrategyEngine:
    """策略适配器：负责拉取数据、组装上下文并调用统一决策引擎。"""

    def __init__(self, client: BinanceClient, config: SpotTradingConfig = None):
        self.client = client
        self.config = config or DEFAULT_SPOT_CONFIG
        self.decision_engine = SpotDecisionEngine()

    @staticmethod
    def _align_kline_series_to_spot(spot_klines: List[Dict], aux_klines: List[Dict]) -> List[Dict]:
        """以 spot bar 为主，对齐辅助 K 线（最近时间点匹配 + forward-fill）。"""
        if not spot_klines:
            return []
        aux_sorted = sorted(aux_klines or [], key=lambda x: x.get("close_time") or x.get("open_time"))
        aligned: List[Dict] = []
        cursor = 0
        last_row: Optional[Dict] = None
        for spot in spot_klines:
            spot_close = spot.get("close_time") or spot.get("open_time")
            while cursor < len(aux_sorted):
                row_time = aux_sorted[cursor].get("close_time") or aux_sorted[cursor].get("open_time")
                if row_time and row_time <= spot_close:
                    last_row = aux_sorted[cursor]
                    cursor += 1
                    continue
                break
            if last_row is None:
                aligned.append({
                    "open_time": spot.get("open_time"),
                    "close_time": spot_close,
                    "open": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "close": 0.0,
                    "volume": 0.0,
                    "source_time": None,
                })
            else:
                aligned.append({
                    "open_time": spot.get("open_time"),
                    "close_time": spot_close,
                    "open": float(last_row.get("open", 0.0)),
                    "high": float(last_row.get("high", 0.0)),
                    "low": float(last_row.get("low", 0.0)),
                    "close": float(last_row.get("close", 0.0)),
                    "volume": float(last_row.get("volume", 0.0)),
                    "source_time": last_row.get("close_time") or last_row.get("open_time"),
                })
        return aligned

    @staticmethod
    def _align_funding_to_spot(spot_klines: List[Dict], funding_series: List[Dict]) -> List[Dict]:
        """以 spot bar 为主，对齐 funding 点序列（最近时间点匹配 + forward-fill）。"""
        if not spot_klines:
            return []
        funding_sorted = sorted(funding_series or [], key=lambda x: x.get("funding_time") or x.get("time"))
        aligned: List[Dict] = []
        cursor = 0
        last_row: Optional[Dict] = None
        for spot in spot_klines:
            spot_close = spot.get("close_time") or spot.get("open_time")
            while cursor < len(funding_sorted):
                row_time = funding_sorted[cursor].get("funding_time") or funding_sorted[cursor].get("time")
                if row_time and row_time <= spot_close:
                    last_row = funding_sorted[cursor]
                    cursor += 1
                    continue
                break
            if last_row is None:
                aligned.append({
                    "close_time": spot_close,
                    "funding_rate": 0.0,
                    "source_time": None,
                })
            else:
                raw_rate = float(last_row.get("funding_rate", 0.0))
                # Accept both percent-like values (e.g. 0.01) and decimal ratios (e.g. 0.0001).
                normalized_rate = raw_rate / 100.0 if abs(raw_rate) >= 0.005 else raw_rate
                aligned.append({
                    "close_time": spot_close,
                    "funding_rate": normalized_rate,
                    "source_time": last_row.get("funding_time") or last_row.get("time"),
                })
        return aligned

    async def analyze_symbol(
        self,
        symbol: str,
        position: Optional[SpotPosition] = None,
        portfolio_state: Optional[Dict[str, float]] = None,
    ) -> SpotSignal:
        """分析单个交易对并输出信号。"""
        params = self.config.to_strategy_params()
        klines = await self.client.get_spot_klines(
            symbol=symbol,
            interval=params.bar_interval,
            limit=params.min_klines_required
        )
        if len(klines) < params.min_klines_required:
            return SpotSignal(
                symbol=symbol,
                action="HOLD",
                price=0.0,
                confidence=0.0,
                reason="insufficient_klines",
                reasons=["insufficient_klines"],
            )

        mark_task = None
        premium_task = None
        funding_task = None
        if hasattr(self.client, "get_mark_price_klines"):
            mark_task = self.client.get_mark_price_klines(
                symbol=symbol,
                interval=params.bar_interval,
                limit=params.min_klines_required,
            )
        if hasattr(self.client, "get_premium_index_klines"):
            premium_task = self.client.get_premium_index_klines(
                symbol=symbol,
                interval=params.bar_interval,
                limit=params.min_klines_required,
            )
        if hasattr(self.client, "get_funding_rate_history"):
            funding_task = self.client.get_funding_rate_history(
                symbol=symbol,
                limit=max(64, params.min_klines_required * 2),
            )

        aux_tasks = [task for task in [mark_task, premium_task, funding_task] if task is not None]
        aux_results: List[Any] = []
        if aux_tasks:
            aux_results = list(await asyncio.gather(*aux_tasks, return_exceptions=True))

        mark_klines: List[Dict] = []
        premium_klines: List[Dict] = []
        funding_series: List[Dict] = []
        aux_idx = 0
        if mark_task is not None:
            mark_res = aux_results[aux_idx]
            aux_idx += 1
            if isinstance(mark_res, list):
                mark_klines = mark_res
        if premium_task is not None:
            premium_res = aux_results[aux_idx]
            aux_idx += 1
            if isinstance(premium_res, list):
                premium_klines = premium_res
        if funding_task is not None:
            funding_res = aux_results[aux_idx]
            if isinstance(funding_res, list):
                funding_series = funding_res

        aligned_mark_series = self._align_kline_series_to_spot(klines, mark_klines)
        aligned_premium_series = self._align_kline_series_to_spot(klines, premium_klines)
        aligned_funding_series = self._align_funding_to_spot(klines, funding_series)

        ticker = await self.client.get_spot_ticker(symbol)
        volume_24h = ticker.volume_24h if ticker else 0.0
        last_bar = klines[-1]
        latest_mark_close = float(aligned_mark_series[-1]["close"]) if aligned_mark_series else 0.0
        latest_premium_close = float(aligned_premium_series[-1]["close"]) if aligned_premium_series else 0.0
        latest_funding = float(aligned_funding_series[-1]["funding_rate"]) if aligned_funding_series else 0.0

        portfolio_state = portfolio_state or {}
        context = DecisionContext(
            symbol=symbol,
            bar_open=float(last_bar["open"]),
            bar_high=float(last_bar["high"]),
            bar_low=float(last_bar["low"]),
            bar_close=float(last_bar["close"]),
            bar_volume=float(last_bar["volume"]),
            recent_klines=klines,
            quote_volume_24h=float(volume_24h),
            has_position=position is not None,
            entry_price=float(position.entry_price) if position else 0.0,
            stop_price=float(position.stop_price) if position else 0.0,
            max_price=float(position.max_price) if position else 0.0,
            position_qty=float(position.quantity) if position else 0.0,
            fees_paid=float(position.fees_paid) if position else 0.0,
            cash_balance=float(portfolio_state.get("cash_balance", 0.0)),
            equity=float(portfolio_state.get("equity", 0.0)),
            day_start_equity=float(portfolio_state.get("day_start_equity", 0.0)),
            fee_bps=float(self.config.fee_bps),
            slippage_bps=float(self.config.slippage_bps),
            funding_rate=latest_funding,
            funding_rate_series=aligned_funding_series,
            premium_kline_series=aligned_premium_series,
            mark_kline_series=aligned_mark_series,
            mark_price_close=latest_mark_close,
            premium_close=latest_premium_close,
            decision_timing=str(portfolio_state.get("decision_timing", params.decision_timing)),
            timestamp=last_bar.get("close_time") or last_bar.get("open_time"),
        )
        return self.decision_engine.decide(context=context, params=params)

    async def analyze_symbols(
        self,
        symbols: List[str],
        positions: Dict[str, SpotPosition],
        portfolio_state: Optional[Dict[str, float]] = None,
    ) -> List[SpotSignal]:
        """并行分析多个交易对并聚合信号。"""
        tasks = [self.analyze_symbol(s, positions.get(s), portfolio_state=portfolio_state) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals: List[SpotSignal] = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.error(f"Signal analysis failed for {symbol}: {result}")
                signals.append(
                    SpotSignal(
                        symbol=symbol,
                        action="HOLD",
                        price=0.0,
                        confidence=0.0,
                        reason="analysis_error",
                        reasons=["analysis_error"],
                    )
                )
            else:
                signals.append(result)
        return signals
