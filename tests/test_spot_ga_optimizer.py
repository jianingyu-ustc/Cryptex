import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from spot.config import SpotTradingConfig
from spot.optimizer import (
    FitnessWeights,
    GASettings,
    ParameterSpace,
    SpotGAOptimizer,
    _HistoryBacktestClient,
    build_walkforward_windows,
)


def _run(coro):
    # 在同步测试入口中执行异步优化流程。
    return asyncio.run(coro)


def _deterministic_evaluator(candidate):
    # 无噪声、可复现的目标函数，用于稳定性测试。
    fitness = (
        120.0
        - abs(candidate["fast_ma_len"] - 9) * 2.0
        - abs(candidate["slow_ma_len"] - 21) * 0.5
        - abs(candidate["atr_k"] - 2.2) * 12.0
        - abs(candidate["trail_atr_k"] - 2.8) * 8.0
        - abs(candidate["rsi_buy_min"] - 45.0) * 1.5
        - abs(candidate["rsi_buy_max"] - 65.0) * 1.2
    )
    return {
        "fitness": fitness,
        "avg_annual_return_pct": fitness / 3.0,
        "avg_sharpe": fitness / 40.0,
    }


def _spot_rows(n: int = 6, quote_asset_volumes=None):
    # 构造 GA 本地历史 client 用的现货 K 线。
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
        }
        if quote_asset_volumes is not None:
            row["quote_asset_volume"] = float(quote_asset_volumes[i])
        rows.append(row)
    return rows


# 参数空间修复应始终满足结构性约束。
def test_parameter_space_repair_constraints():
    cfg = SpotTradingConfig()
    space = ParameterSpace(
        base_config=cfg,
        search_timeframe=True,
        search_risk=True,
        search_cost=True,
        max_search_dims=30,
    )
    repaired = space.repair({
        "fast_ma_len": 18,
        "slow_ma_len": 20,
        "atr_k": 3.2,
        "trail_atr_k": 2.1,
        "rsi_buy_min": 72.0,
        "rsi_buy_max": 60.0,
    })
    assert repaired["slow_ma_len"] >= repaired["fast_ma_len"] * 2
    assert repaired["trail_atr_k"] >= repaired["atr_k"]
    assert repaired["rsi_buy_min"] < repaired["rsi_buy_max"]


# GA 本地历史 client 应与 backtest 本地 client 使用同一 24h 成交额口径。
def test_ga_history_client_ticker_prefers_quote_asset_volume():
    client = _HistoryBacktestClient(
        {"BTCUSDT": _spot_rows(6, quote_asset_volumes=[700, 701, 702, 703, 704, 705])},
        interval_seconds=3600,
    )
    client.set_index(3)

    ticker = _run(client.get_spot_ticker("BTCUSDT"))

    assert round(ticker.volume_24h, 6) == 2806.0


# GA 本地历史 client 返回的 kline 切片也应保留新增字段。
def test_ga_history_client_reads_extended_fields():
    client = _HistoryBacktestClient(
        {"BTCUSDT": _spot_rows(3, quote_asset_volumes=[11, 22, 33])},
        interval_seconds=3600,
        symbol_mark_rows={"BTCUSDT": _spot_rows(3, quote_asset_volumes=[44, 55, 66])},
        symbol_premium_rows={"BTCUSDT": _spot_rows(3, quote_asset_volumes=[77, 88, 99])},
    )
    client.set_index(2)

    spot_rows = _run(client.get_spot_klines("BTCUSDT", limit=3))
    mark_rows = _run(client.get_mark_price_klines("BTCUSDT", limit=3))
    premium_rows = _run(client.get_premium_index_klines("BTCUSDT", limit=3))

    assert spot_rows[-1]["quote_asset_volume"] == 33.0
    assert mark_rows[-1]["quote_asset_volume"] == 66.0
    assert premium_rows[-1]["quote_asset_volume"] == 99.0


# Walk-forward 切窗应生成正确的训练/测试边界。
def test_walkforward_split_is_correct():
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = datetime(2022, 1, 1, tzinfo=timezone.utc)
    windows = build_walkforward_windows(
        start_time=start,
        end_time=end,
        train_days=180,
        test_days=60,
        step_days=30,
    )

    assert windows
    for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
        assert train_end - train_start == timedelta(days=180)
        assert test_start == train_end
        assert test_end - test_start == timedelta(days=60)
        if i > 0:
            assert train_start - windows[i - 1][0] == timedelta(days=30)
    assert windows[-1][3] <= end


# 相同 seed + 相同确定性评估器 => 最优结果一致。
def test_ga_seed_reproducible(tmp_path):
    cfg = SpotTradingConfig(symbols=["BTCUSDT"])
    settings = GASettings(
        population_size=10,
        generations=4,
        mutation_rate=0.2,
        crossover_rate=0.7,
        elitism_k=2,
        top_k_log=3,
        seed=123,
    )
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)

    space1 = ParameterSpace(base_config=cfg, max_search_dims=10)
    opt1 = SpotGAOptimizer(
        client=None,
        base_config=cfg,
        output_dir=str(tmp_path / "run1"),
        parameter_space=space1,
        settings=settings,
        weights=FitnessWeights(),
        evaluator_override=_deterministic_evaluator,
    )
    result1 = _run(opt1.run(
        symbols=cfg.symbols,
        backtest_start=start,
        backtest_end=end,
        walkforward_train_days=365,
        walkforward_test_days=90,
        walkforward_step_days=90,
    ))

    space2 = ParameterSpace(base_config=cfg, max_search_dims=10)
    opt2 = SpotGAOptimizer(
        client=None,
        base_config=cfg,
        output_dir=str(tmp_path / "run2"),
        parameter_space=space2,
        settings=settings,
        weights=FitnessWeights(),
        evaluator_override=_deterministic_evaluator,
    )
    result2 = _run(opt2.run(
        symbols=cfg.symbols,
        backtest_start=start,
        backtest_end=end,
        walkforward_train_days=365,
        walkforward_test_days=90,
        walkforward_step_days=90,
    ))

    assert result1["best_candidate"] == result2["best_candidate"]
    assert result1["best_fitness"] == result2["best_fitness"]


# 优化器运行后必须产出预期的报告与产物文件。
def test_ga_exports_are_created_with_required_fields(tmp_path):
    cfg = SpotTradingConfig(symbols=["BTCUSDT", "ETHUSDT"])
    settings = GASettings(
        population_size=8,
        generations=3,
        mutation_rate=0.2,
        crossover_rate=0.8,
        elitism_k=2,
        top_k_log=2,
        seed=7,
    )
    optimizer = SpotGAOptimizer(
        client=None,
        base_config=cfg,
        output_dir=str(tmp_path / "exports"),
        parameter_space=ParameterSpace(base_config=cfg, max_search_dims=8),
        settings=settings,
        weights=FitnessWeights(),
        evaluator_override=_deterministic_evaluator,
    )
    result = _run(optimizer.run(
        symbols=cfg.symbols,
        backtest_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
        backtest_end=datetime(2024, 1, 1, tzinfo=timezone.utc),
        walkforward_train_days=365,
        walkforward_test_days=90,
        walkforward_step_days=90,
    ))

    best_path = Path(result["best_params_path"])
    meta_path = Path(result["run_meta_path"])
    csv_path = Path(result["generation_csv_path"])
    cost_curve_path = Path(result["cost_sensitivity_curve_path"])
    worst_report_path = Path(result["worst_window_report_path"])
    final_report_path = Path(result["final_validation_report_path"])
    assert best_path.exists()
    assert meta_path.exists()
    assert csv_path.exists()
    assert cost_curve_path.exists()
    assert worst_report_path.exists()
    assert final_report_path.exists()

    best_payload = json.loads(best_path.read_text(encoding="utf-8"))
    assert "strategy_params" in best_payload
    assert "risk_params" in best_payload
    assert "execution_params" in best_payload
    assert "fitness" in best_payload
    assert "metrics" in best_payload
    assert "oos_windows" in best_payload

    run_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert run_meta["symbols"] == cfg.symbols
    assert run_meta["seed"] == settings.seed
    assert "ga_settings" in run_meta
    assert "fitness_weights" in run_meta
    assert "best_params_file" in run_meta
    assert "generation_csv" in run_meta
    assert "cost_sensitivity_curve" in run_meta
    assert "worst_window_report" in run_meta
    assert "final_validation_report" in run_meta

    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert "generation" in header
    assert "fitness" in header
    assert "params_json" in header
