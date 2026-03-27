import asyncio
from datetime import datetime, timedelta, timezone

from common.binance_client import BinanceAPIConfig, BinanceClient
from spot.main import SpotBacktestDataClient, SpotTradingSystem, _interval_to_seconds
from spot.config import SpotTradingConfig
from spot.execution import SpotExecutionEngine
from spot.models import SpotSignal


def _run(coro):
    # 在普通 pytest 用例中调用异步接口。
    return asyncio.run(coro)


def _rows(n: int = 6, quote_asset_volumes=None):
    # 为回测客户端测试构造简短的小时级 K 线序列。
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        row = {
            "open_time": start + timedelta(hours=i),
            "close_time": start + timedelta(hours=i + 1),
            "open": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
            "close": 100 + i,
            "volume": 10.0,
            "number_of_trades": 100 + i,
            "taker_buy_base_volume": 4.0 + i,
            "taker_buy_quote_volume": 400.0 + i,
            "ignore": "0",
        }
        if quote_asset_volumes is not None:
            row["quote_asset_volume"] = float(quote_asset_volumes[i])
        rows.append(row)
    return rows


# 工具函数测试：周期解析与兜底逻辑。
def test_interval_to_seconds():
    assert _interval_to_seconds("15m") == 900
    assert _interval_to_seconds("1h") == 3600
    assert _interval_to_seconds("1d") == 86400
    assert _interval_to_seconds("1w") == 604800
    assert _interval_to_seconds("bad") == 900


# 数据客户端测试：滚动切片、价格快照、24h 成交额优先使用 quote_asset_volume。
def test_backtest_data_client_rolling_slice_and_ticker_prefers_quote_asset_volume():
    client = SpotBacktestDataClient(
        {"BTCUSDT": _rows(6, quote_asset_volumes=[900, 901, 902, 903, 904, 905])},
        interval_seconds=3600,
    )
    client.set_index(3)

    klines = _run(client.get_spot_klines("BTCUSDT", limit=2))
    assert len(klines) == 2
    assert klines[0]["close"] == 102
    assert klines[1]["close"] == 103

    price = _run(client.get_spot_price("BTCUSDT"))
    assert price == 103

    ticker = _run(client.get_spot_ticker("BTCUSDT"))
    # index=3 时，优先累加 quote_asset_volume=900/901/902/903。
    assert round(ticker.volume_24h, 6) == 3606.0


# 兼容旧历史文件：若缺少 quote_asset_volume，则回退为 volume*close。
def test_backtest_data_client_ticker_falls_back_without_quote_asset_volume():
    client = SpotBacktestDataClient({"BTCUSDT": _rows(6)}, interval_seconds=3600)
    client.set_index(3)

    ticker = _run(client.get_spot_ticker("BTCUSDT"))

    assert round(ticker.volume_24h, 6) == 4060.0


# 历史 bundle 序列化/反序列化后，spot/mark/premium kline 的扩展字段不能丢。
def test_history_bundle_roundtrip_preserves_extended_kline_fields(tmp_path):
    bundle = {
        "metadata": {
            "symbols": ["BTCUSDT"],
            "kline_interval": "1h",
            "start_time": "2024-01-01T00:00:00+00:00",
            "end_time": "2024-01-01T06:00:00+00:00",
        },
        "spot": {"BTCUSDT": _rows(2, quote_asset_volumes=[1000, 1001])},
        "mark": {"BTCUSDT": _rows(2, quote_asset_volumes=[2000, 2001])},
        "premium": {"BTCUSDT": _rows(2, quote_asset_volumes=[3000, 3001])},
        "funding": {},
        "dvol": {},
    }
    path = tmp_path / "bundle.json"

    SpotTradingSystem.save_history_bundle(str(path), bundle)
    loaded = SpotTradingSystem.load_history_bundle(str(path))

    row = loaded["spot"]["BTCUSDT"][0]
    assert row["quote_asset_volume"] == 1000.0
    assert row["number_of_trades"] == 100
    assert row["taker_buy_base_volume"] == 4.0
    assert row["taker_buy_quote_volume"] == 400.0
    assert row["ignore"] == "0"
    assert loaded["mark"]["BTCUSDT"][0]["quote_asset_volume"] == 2000.0
    assert loaded["premium"]["BTCUSDT"][0]["quote_asset_volume"] == 3000.0


# 本地历史文件加载后，回测 client 返回的 kline 切片应保留新增字段。
def test_backtest_client_reads_extended_fields_from_local_bundle(tmp_path):
    bundle = {
        "metadata": {"symbols": ["BTCUSDT"], "kline_interval": "1h"},
        "spot": {"BTCUSDT": _rows(3, quote_asset_volumes=[111, 222, 333])},
        "mark": {"BTCUSDT": _rows(3, quote_asset_volumes=[444, 555, 666])},
        "premium": {"BTCUSDT": _rows(3, quote_asset_volumes=[777, 888, 999])},
        "funding": {},
        "dvol": {},
    }
    path = tmp_path / "bundle.json"
    SpotTradingSystem.save_history_bundle(str(path), bundle)
    loaded = SpotTradingSystem.load_history_bundle(str(path))
    client = SpotBacktestDataClient(
        loaded["spot"],
        interval_seconds=3600,
        symbol_mark_klines=loaded["mark"],
        symbol_premium_klines=loaded["premium"],
    )
    client.set_index(2)

    spot_rows = _run(client.get_spot_klines("BTCUSDT", limit=3))
    mark_rows = _run(client.get_mark_price_klines("BTCUSDT", limit=3))
    premium_rows = _run(client.get_premium_index_klines("BTCUSDT", limit=3))

    assert spot_rows[-1]["quote_asset_volume"] == 333.0
    assert mark_rows[-1]["quote_asset_volume"] == 666.0
    assert premium_rows[-1]["quote_asset_volume"] == 999.0
    assert spot_rows[-1]["number_of_trades"] == 102


# Binance spot kline 解析应保留交易所返回的完整常用字段。
def test_binance_client_spot_klines_include_extended_fields():
    client = BinanceClient(BinanceAPIConfig())

    async def fake_request(method, base_url, endpoint, params=None, signed=False):
        return [[
            1704067200000,
            "100.0",
            "101.0",
            "99.0",
            "100.5",
            "12.3",
            1704070799999,
            "1234.56",
            89,
            "4.5",
            "456.78",
            "0",
        ]]

    client._request = fake_request  # type: ignore[method-assign]

    rows = _run(client.get_spot_klines("BTCUSDT", interval="1h", limit=1))

    assert len(rows) == 1
    row = rows[0]
    assert row["quote_asset_volume"] == 1234.56
    assert row["number_of_trades"] == 89
    assert row["taker_buy_base_volume"] == 4.5
    assert row["taker_buy_quote_volume"] == 456.78
    assert row["ignore"] == "0"


# Binance mark/premium/delivery kline 解析也应保留交易所返回的完整常用字段。
def test_binance_client_other_klines_include_extended_fields():
    client = BinanceClient(BinanceAPIConfig())

    async def fake_request(method, base_url, endpoint, params=None, signed=False):
        return [[
            1704067200000,
            "10.0",
            "11.0",
            "9.0",
            "10.5",
            "1.23",
            1704070799999,
            "234.56",
            17,
            "0.45",
            "45.67",
            "0",
        ]]

    client._request = fake_request  # type: ignore[method-assign]

    mark_rows = _run(client.get_mark_price_klines("BTCUSDT", interval="1h", limit=1))
    premium_rows = _run(client.get_premium_index_klines("BTCUSDT", interval="1h", limit=1))
    delivery_rows = _run(client.get_delivery_klines("BTCUSD_240329", interval="1h", limit=1))

    for rows in (mark_rows, premium_rows, delivery_rows):
        assert len(rows) == 1
        row = rows[0]
        assert row["quote_asset_volume"] == 234.56
        assert row["number_of_trades"] == 17
        assert row["taker_buy_base_volume"] == 0.45
        assert row["taker_buy_quote_volume"] == 45.67
        assert row["ignore"] == "0"


# DVOL 序列测试：应按当前回测索引做时间门控。
def test_backtest_data_client_dvol_slice():
    rows = _rows(6)
    dvol_rows = [
        {"time": row["close_time"], "dvol_value": 50.0 + i}
        for i, row in enumerate(rows)
    ]
    client = SpotBacktestDataClient(
        {"BTCUSDT": rows},
        interval_seconds=3600,
        symbol_dvol_series={"BTCUSDT": dvol_rows},
    )
    client.set_index(2)

    dvol = _run(client.get_dvol_index_history("BTCUSDT", limit=10))

    assert len(dvol) == 3
    assert dvol[-1]["dvol_value"] == 52.0


# 执行引擎测试：max_daily_trades 在 UTC 跨日后应重置。
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
