import asyncio
from datetime import datetime, timedelta, timezone

from spot.main import SpotBacktestDataClient, _interval_to_seconds
from spot.config import SpotTradingConfig
from spot.execution import SpotExecutionEngine
from spot.models import SpotSignal


def _run(coro):
    return asyncio.run(coro)


def _rows(n: int = 6):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": start + timedelta(hours=i),
                "close_time": start + timedelta(hours=i + 1),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100 + i,
                "volume": 10.0,
            }
        )
    return rows


def test_interval_to_seconds():
    assert _interval_to_seconds("15m") == 900
    assert _interval_to_seconds("1h") == 3600
    assert _interval_to_seconds("1d") == 86400
    assert _interval_to_seconds("1w") == 604800
    assert _interval_to_seconds("bad") == 900


def test_backtest_data_client_rolling_slice_and_ticker():
    client = SpotBacktestDataClient({"BTCUSDT": _rows(6)}, interval_seconds=3600)
    client.set_index(3)

    klines = _run(client.get_spot_klines("BTCUSDT", limit=2))
    assert len(klines) == 2
    assert klines[0]["close"] == 102
    assert klines[1]["close"] == 103

    price = _run(client.get_spot_price("BTCUSDT"))
    assert price == 103

    ticker = _run(client.get_spot_ticker("BTCUSDT"))
    # index=3 -> closes 100,101,102,103 with vol 10 each
    assert round(ticker.volume_24h, 6) == 4060.0


def test_execution_daily_trade_count_uses_simulation_time():
    config = SpotTradingConfig(
        initial_capital=1000.0,
        usdt_per_trade=100.0,
        risk_per_trade_pct=1.0,
        max_daily_trades=2,
        cooldown_bars=0,
        fee_bps=0.0,
        slippage_bps=0.0,
        daily_loss_limit_pct=99.0,
    )
    engine = SpotExecutionEngine(client=None, config=config)

    buy = SpotSignal(
        symbol="BTCUSDT",
        action="BUY",
        price=100.0,
        confidence=1.0,
        reason="buy",
        reasons=["buy"],
        stop_price=90.0,
    )
    sell = SpotSignal(
        symbol="BTCUSDT",
        action="SELL",
        price=100.0,
        confidence=1.0,
        reason="sell",
        reasons=["sell"],
    )

    engine.set_simulation_time(datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc))
    first_buy = _run(engine.execute_signal(buy))
    first_sell = _run(engine.execute_signal(sell))

    engine.set_simulation_time(datetime(2025, 1, 1, 1, 0, tzinfo=timezone.utc))
    blocked_buy_same_day = _run(engine.execute_signal(buy))

    engine.set_simulation_time(datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc))
    allowed_buy_next_day = _run(engine.execute_signal(buy))

    assert first_buy is not None
    assert first_sell is not None
    assert blocked_buy_same_day is None
    assert allowed_buy_next_day is not None
