import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from spot.config import SpotTradingConfig
from spot.execution import SpotExecutionEngine
from spot.models import SpotPosition, SpotSignal
from spot.strategy import SpotStrategyEngine


def _run(coro):
    return asyncio.run(coro)


def _build_klines(closes: list[float], wick: float = 0.6) -> list[dict]:
    now = datetime.now(timezone.utc)
    klines = []
    prev = closes[0]
    for i, close in enumerate(closes):
        open_price = prev
        high = max(open_price, close) + wick
        low = min(open_price, close) - wick
        klines.append(
            {
                "open_time": now + timedelta(minutes=15 * i),
                "close_time": now + timedelta(minutes=15 * (i + 1)),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000.0,
            }
        )
        prev = close
    return klines


class DummyClient:
    def __init__(self, klines_by_symbol=None, ticker_volume=100_000_000.0, prices=None):
        self.klines_by_symbol = klines_by_symbol or {}
        self.ticker_volume = ticker_volume
        self.prices = prices or {}

    async def get_spot_klines(self, symbol: str, interval: str, limit: int):
        return self.klines_by_symbol.get(symbol, [])[-limit:]

    async def get_spot_ticker(self, symbol: str):
        return SimpleNamespace(volume_24h=self.ticker_volume)

    async def get_spot_price(self, symbol: str):
        return self.prices.get(symbol)

    async def spot_market_order(self, symbol: str, side: str, quantity: float):
        return None


def test_trend_ok_but_no_pullback_should_hold():
    closes = [100 + i * 0.9 for i in range(45)]
    client = DummyClient({"BTCUSDT": _build_klines(closes, wick=0.05)})
    config = SpotTradingConfig(
        rsi_buy_min=0.0,
        rsi_buy_max=100.0,
        adx_min=5.0,
        pullback_tol=0.003,
    )
    engine = SpotStrategyEngine(client, config)

    signal = _run(engine.analyze_symbol("BTCUSDT"))

    assert signal.action == "HOLD"
    assert any("no_pullback_to_fast_ma" in r for r in signal.reasons)


def test_trend_pullback_and_confirmation_should_buy():
    closes = [100 + i * 0.22 for i in range(35)] + [107.4, 107.8, 107.1, 106.8, 107.4, 108.0, 108.6, 109.2, 109.8, 111.3]
    client = DummyClient({"ETHUSDT": _build_klines(closes)})
    config = SpotTradingConfig(
        rsi_buy_min=40.0,
        rsi_buy_max=95.0,
        adx_min=5.0,
        pullback_tol=0.005,
    )
    engine = SpotStrategyEngine(client, config)

    signal = _run(engine.analyze_symbol("ETHUSDT"))

    assert signal.action == "BUY"
    assert signal.stop_price > 0
    assert any("pullback_hit" in r for r in signal.reasons)


def test_atr_stop_hit_should_sell():
    closes = ([100.0] * 40) + [99.0, 98.0, 97.0, 96.0, 94.0]
    client = DummyClient({"SOLUSDT": _build_klines(closes)})
    config = SpotTradingConfig()
    engine = SpotStrategyEngine(client, config)
    position = SpotPosition(symbol="SOLUSDT", quantity=1.0, entry_price=100.0, stop_price=95.0, max_price=103.0, last_price=100.0)

    signal = _run(engine.analyze_symbol("SOLUSDT", position=position))

    assert signal.action == "SELL"
    assert any("atr_stop_hit" in r for r in signal.reasons)


def test_trailing_stop_hit_should_sell():
    closes = [100 + i * 0.2 for i in range(35)] + [107.5, 108.5, 109.5, 110.0, 109.0, 108.0, 107.2, 107.1, 107.0, 106.9]
    client = DummyClient({"BNBUSDT": _build_klines(closes)})
    config = SpotTradingConfig(trail_atr_k=1.5)
    engine = SpotStrategyEngine(client, config)
    position = SpotPosition(symbol="BNBUSDT", quantity=1.0, entry_price=100.0, stop_price=95.0, max_price=110.0, last_price=108.0)

    signal = _run(engine.analyze_symbol("BNBUSDT", position=position))

    assert signal.action == "SELL"
    assert any("trail_stop_hit" in r for r in signal.reasons)


def test_fee_and_slippage_reduce_equity():
    client = DummyClient()
    config = SpotTradingConfig(
        initial_capital=1000.0,
        usdt_per_trade=100.0,
        risk_per_trade_pct=1.0,
        fee_bps=10.0,
        slippage_bps=10.0,
        daily_loss_limit_pct=99.0,
    )
    engine = SpotExecutionEngine(client, config)

    buy_signal = SpotSignal(
        symbol="XRPUSDT",
        action="BUY",
        price=100.0,
        confidence=1.0,
        reason="test_buy",
        reasons=["test_buy"],
        stop_price=90.0,
    )
    sell_signal = SpotSignal(
        symbol="XRPUSDT",
        action="SELL",
        price=100.0,
        confidence=1.0,
        reason="test_sell",
        reasons=["test_sell"],
    )

    _run(engine.execute_signal(buy_signal))
    sell_trade = _run(engine.execute_signal(sell_signal))
    stats = engine.get_stats()

    assert sell_trade is not None
    assert stats["account_value_usdt"] < config.initial_capital
    assert stats["fees_paid_usdt"] > 0
    assert stats["slippage_cost_usdt"] > 0


def test_cooldown_blocks_reentry_for_n_bars():
    client = DummyClient()
    config = SpotTradingConfig(
        initial_capital=1000.0,
        usdt_per_trade=100.0,
        risk_per_trade_pct=1.0,
        fee_bps=0.0,
        slippage_bps=0.0,
        cooldown_bars=2,
        daily_loss_limit_pct=99.0,
    )
    engine = SpotExecutionEngine(client, config)

    buy_signal = SpotSignal(
        symbol="BTCUSDT",
        action="BUY",
        price=100.0,
        confidence=1.0,
        reason="buy",
        reasons=["buy"],
        stop_price=90.0,
    )
    sell_signal = SpotSignal(
        symbol="BTCUSDT",
        action="SELL",
        price=101.0,
        confidence=1.0,
        reason="sell",
        reasons=["sell"],
    )

    first_buy = _run(engine.execute_signal(buy_signal))
    first_sell = _run(engine.execute_signal(sell_signal))
    blocked_buy_now = _run(engine.execute_signal(buy_signal))

    engine.advance_bar(1)
    blocked_buy_bar1 = _run(engine.execute_signal(buy_signal))
    engine.advance_bar(2)
    allowed_buy = _run(engine.execute_signal(buy_signal))

    assert first_buy is not None
    assert first_sell is not None
    assert blocked_buy_now is None
    assert blocked_buy_bar1 is None
    assert allowed_buy is not None
