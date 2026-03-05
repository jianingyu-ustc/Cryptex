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
    build_walkforward_windows,
)


def _run(coro):
    return asyncio.run(coro)


def _deterministic_evaluator(candidate):
    # Deterministic objective used for reproducibility tests.
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
