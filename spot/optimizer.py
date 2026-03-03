"""
Genetic-Algorithm optimizer for spot strategy parameters.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from common.binance_client import BinanceClient
from .config import (
    ExecutionParams,
    RiskParams,
    SpotTradingConfig,
    StrategyParams,
)
from .execution import SpotExecutionEngine
from .models import SpotSignal
from .strategy import SpotStrategyEngine


def _interval_to_seconds(interval: str) -> int:
    if not interval or len(interval) < 2:
        return 900
    unit = interval[-1].lower()
    try:
        value = int(interval[:-1])
    except ValueError:
        return 900

    unit_map = {
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 7 * 86400,
    }
    if interval[-1] == "M":
        return value * 30 * 86400
    if unit not in unit_map:
        return 900
    return max(60, value * unit_map[unit])


def build_walkforward_windows(
    start_time: datetime,
    end_time: datetime,
    train_days: int = 730,
    test_days: int = 90,
    step_days: Optional[int] = None,
) -> List[Tuple[datetime, datetime, datetime, datetime]]:
    """Build walk-forward windows: (train_start, train_end, test_start, test_end)."""
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)
    start_time = start_time.astimezone(timezone.utc)
    end_time = end_time.astimezone(timezone.utc)

    train_days = max(30, int(train_days))
    test_days = max(7, int(test_days))
    step_days = max(7, int(step_days if step_days is not None else test_days))

    windows: List[Tuple[datetime, datetime, datetime, datetime]] = []
    cursor = start_time
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        if test_end > end_time:
            break
        windows.append((train_start, train_end, test_start, test_end))
        cursor = cursor + timedelta(days=step_days)
    return windows


@dataclass
class FitnessWeights:
    ann_return: float = 1.0
    sharpe: float = 0.8
    sortino: float = 0.4
    max_drawdown: float = 1.0
    win_rate: float = 0.2
    profit_factor: float = 0.2

    trade_count: float = 0.15
    holding: float = 0.15
    cost_ratio: float = 0.6
    stability: float = 0.7
    worst_window: float = 0.8
    dsr_proxy: float = 0.3

    @classmethod
    def from_string(cls, text: str) -> "FitnessWeights":
        """
        Parse weights from:
        ann_return=1,sharpe=0.8,max_drawdown=1.2
        """
        if not text:
            return cls()
        data = asdict(cls())
        parts = [p.strip() for p in text.split(",") if p.strip()]
        for part in parts:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            key = k.strip()
            if key in data:
                try:
                    data[key] = float(v.strip())
                except ValueError:
                    pass
        return cls(**data)


@dataclass
class GASettings:
    population_size: int = 20
    generations: int = 10
    mutation_rate: float = 0.15
    crossover_rate: float = 0.75
    elitism_k: int = 2
    top_k_log: int = 5
    seed: int = 42


class ParameterSpace:
    """Search space + constraint repair."""

    def __init__(
        self,
        base_config: SpotTradingConfig,
        search_timeframe: bool = False,
        search_risk: bool = False,
        search_cost: bool = False,
        max_search_dims: int = 12,
    ):
        self.base_config = base_config
        self.search_timeframe = search_timeframe
        self.search_risk = search_risk
        self.search_cost = search_cost
        self.max_search_dims = max(3, int(max_search_dims))

        self._strategy_defaults = asdict(base_config.to_strategy_params())
        self._risk_defaults = asdict(base_config.to_risk_params())
        self._execution_defaults = asdict(base_config.to_execution_params())

        dims: Dict[str, Dict[str, Any]] = {
            "fast_ma_len": {"type": "choice", "values": [5, 7, 9, 12, 15]},
            "slow_ma_len": {"type": "choice", "values": [18, 21, 30, 40, 50, 60]},
            "rsi_len": {"type": "choice", "values": [10, 14, 21]},
            "atr_len": {"type": "choice", "values": [10, 14, 21]},
            # C2 hardening: default adx_len fixed.
            "adx_len": {"type": "choice", "values": [14]},
            "pullback_tol": {"type": "float", "min": 0.001, "max": 0.015},
            "confirm_breakout": {"type": "float", "min": 0.0001, "max": 0.004},
            "rsi_buy_min": {"type": "choice", "values": [35, 40, 45, 50, 55]},
            "rsi_buy_max": {"type": "choice", "values": [60, 65, 70, 75]},
            "adx_min": {"type": "choice", "values": [10, 14, 18, 22, 26]},
            "trend_strength_min": {"type": "float", "min": 0.001, "max": 0.012},
            "atr_k": {"type": "float", "min": 1.2, "max": 3.8},
            "trail_atr_k": {"type": "float", "min": 1.5, "max": 5.0},
            "rsi_sell_min": {"type": "choice", "values": [35, 40, 45, 50, 55]},
            "min_24h_quote_volume": {"type": "choice", "values": [5_000_000, 10_000_000, 20_000_000, 50_000_000]},
        }
        if search_timeframe:
            dims["bar_interval"] = {"type": "choice", "values": ["15m", "30m", "1h", "4h", "1d"]}
        if search_risk:
            dims.update({
                "risk_per_trade_pct": {"type": "float", "min": 0.2, "max": 1.5},
                "usdt_per_trade": {"type": "choice", "values": [50, 80, 100, 150, 200, 300]},
                "max_total_exposure_pct": {"type": "choice", "values": [40, 60, 80, 90, 100]},
                "daily_loss_limit_pct": {"type": "choice", "values": [1.5, 2.0, 3.0, 4.0, 5.0]},
                "cooldown_bars": {"type": "choice", "values": [0, 1, 2, 3, 4, 6]},
            })
        if search_cost:
            dims.update({
                "fee_bps": {"type": "choice", "values": [5, 8, 10, 12, 15]},
                "slippage_bps": {"type": "choice", "values": [5, 8, 10, 12, 15, 20]},
            })

        # C3 hardening: cap search dimensionality.
        ordered_keys = list(dims.keys())[: self.max_search_dims]
        self.dimensions: Dict[str, Dict[str, Any]] = {k: dims[k] for k in ordered_keys}

    def sample(self, rng: random.Random) -> Dict[str, Any]:
        candidate: Dict[str, Any] = {}
        for key, spec in self.dimensions.items():
            if spec["type"] == "choice":
                candidate[key] = rng.choice(spec["values"])
            else:
                candidate[key] = rng.uniform(spec["min"], spec["max"])
        return self.repair(candidate)

    def mutate(self, candidate: Dict[str, Any], rng: random.Random, mutation_rate: float) -> Dict[str, Any]:
        out = dict(candidate)
        for key, spec in self.dimensions.items():
            if rng.random() > mutation_rate:
                continue
            if spec["type"] == "choice":
                out[key] = rng.choice(spec["values"])
            else:
                # local mutation around current value
                span = spec["max"] - spec["min"]
                cur = float(out.get(key, (spec["max"] + spec["min"]) / 2))
                delta = rng.uniform(-0.25 * span, 0.25 * span)
                out[key] = min(spec["max"], max(spec["min"], cur + delta))
        return self.repair(out)

    def crossover(self, a: Dict[str, Any], b: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
        child: Dict[str, Any] = {}
        for key in self.dimensions.keys():
            child[key] = a.get(key) if rng.random() < 0.5 else b.get(key)
        return self.repair(child)

    def repair(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        merged.update(self._strategy_defaults)
        merged.update(self._risk_defaults)
        merged.update(self._execution_defaults)
        merged.update(candidate)

        strategy = StrategyParams(**{
            k: merged.get(k) for k in StrategyParams.__dataclass_fields__.keys()
        }).repair()
        risk = RiskParams(**{
            k: merged.get(k) for k in RiskParams.__dataclass_fields__.keys()
        }).repair()
        execution = ExecutionParams(**{
            k: merged.get(k) for k in ExecutionParams.__dataclass_fields__.keys()
        }).repair()

        out = asdict(strategy)
        out.update(asdict(risk))
        out.update(asdict(execution))
        return out

    def candidate_to_params(self, candidate: Dict[str, Any]) -> Tuple[StrategyParams, RiskParams, ExecutionParams]:
        repaired = self.repair(candidate)
        strategy = StrategyParams(**{
            k: repaired[k] for k in StrategyParams.__dataclass_fields__.keys()
        }).repair()
        risk = RiskParams(**{
            k: repaired[k] for k in RiskParams.__dataclass_fields__.keys()
        }).repair()
        execution = ExecutionParams(**{
            k: repaired[k] for k in ExecutionParams.__dataclass_fields__.keys()
        }).repair()
        return strategy, risk, execution


class _HistoryBacktestClient:
    """Windowed in-memory data client for backtest simulation."""

    def __init__(self, symbol_rows: Dict[str, List[Dict]], interval_seconds: int):
        self.symbol_rows = {
            s: sorted(rows, key=lambda x: x["open_time"])
            for s, rows in symbol_rows.items()
        }
        self.interval_seconds = max(60, int(interval_seconds))
        self.current_index = 0
        self._bars_24h = max(1, int(86400 / self.interval_seconds))

    def set_index(self, idx: int):
        self.current_index = max(0, int(idx))

    def _rows(self, symbol: str) -> List[Dict]:
        rows = self.symbol_rows.get(symbol, [])
        if not rows:
            return []
        return rows[: min(len(rows), self.current_index + 1)]

    async def get_spot_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self._rows(symbol)
        if start_time:
            rows = [r for r in rows if r["open_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["open_time"] <= end_time]
        return rows[-limit:] if limit > 0 else rows

    async def get_spot_ticker(self, symbol: str):
        rows = self._rows(symbol)
        if not rows:
            return None
        recent = rows[-self._bars_24h:]
        quote_volume_24h = sum(float(r["volume"]) * float(r["close"]) for r in recent)
        px = float(rows[-1]["close"])
        return type("Ticker", (), {
            "symbol": symbol,
            "price": px,
            "bid_price": px,
            "ask_price": px,
            "volume_24h": quote_volume_24h,
        })()

    async def get_spot_price(self, symbol: str) -> Optional[float]:
        rows = self._rows(symbol)
        if not rows:
            return None
        return float(rows[-1]["close"])


@dataclass
class WindowMetrics:
    annual_return_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    trade_count: int
    avg_holding_bars: float
    cost_to_gross_ratio: float
    total_return_pct: float
    total_fees: float
    total_slippage: float


@dataclass
class CandidateEvaluation:
    candidate: Dict[str, Any]
    fitness: float
    metrics: Dict[str, float]
    per_window: List[Dict[str, float]] = field(default_factory=list)


class SpotGAOptimizer:
    """Run GA optimization with walk-forward OOS fitness."""

    def __init__(
        self,
        client: Optional[BinanceClient],
        base_config: SpotTradingConfig,
        output_dir: str,
        parameter_space: ParameterSpace,
        settings: GASettings,
        weights: FitnessWeights,
        evaluator_override: Optional[Callable[[Dict[str, Any]], Dict[str, float]]] = None,
    ):
        self.client = client
        self.base_config = base_config
        self.parameter_space = parameter_space
        self.settings = settings
        self.weights = weights
        self.rng = random.Random(settings.seed)
        self.evaluator_override = evaluator_override

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(output_dir) / f"spot_ga_{ts}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.gen_csv_path = self.run_dir / "generation_topk.csv"
        self.best_params_path = self.run_dir / "best_params.json"
        self.run_meta_path = self.run_dir / "run_meta.json"

    async def _fetch_symbol_history(self, symbol: str, start: datetime, end: datetime, interval: str) -> List[Dict]:
        if not self.client:
            return []
        interval_seconds = _interval_to_seconds(interval)
        cursor = start
        rows: List[Dict] = []
        while cursor < end:
            batch = await self.client.get_spot_klines(
                symbol=symbol,
                interval=interval,
                limit=1000,
                start_time=cursor,
                end_time=end,
            )
            if not batch:
                break
            for item in batch:
                if not rows or item["open_time"] > rows[-1]["open_time"]:
                    rows.append(item)
            nxt = batch[-1]["open_time"] + timedelta(seconds=interval_seconds)
            if nxt <= cursor:
                break
            cursor = nxt
            if len(batch) < 1000:
                break
            await asyncio.sleep(0.02)
        return rows

    @staticmethod
    def _max_drawdown_pct(equity_curve: Sequence[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        mdd = 0.0
        for eq in equity_curve:
            peak = max(peak, eq)
            if peak <= 0:
                continue
            dd = (peak - eq) / peak * 100
            mdd = max(mdd, dd)
        return mdd

    @staticmethod
    def _sharpe_sortino(
        returns: Sequence[float],
        bars_per_year: float,
    ) -> Tuple[float, float]:
        if not returns:
            return 0.0, 0.0
        avg_r = mean(returns)
        std_r = pstdev(returns) if len(returns) > 1 else 0.0
        downside = [r for r in returns if r < 0]
        downside_std = pstdev(downside) if len(downside) > 1 else 0.0
        sharpe = (avg_r / std_r * math.sqrt(bars_per_year)) if std_r > 0 else 0.0
        sortino = (avg_r / downside_std * math.sqrt(bars_per_year)) if downside_std > 0 else 0.0
        return sharpe, sortino

    async def _run_window_backtest(
        self,
        history_by_symbol: Dict[str, List[Dict]],
        symbols: List[str],
        test_start: datetime,
        test_end: datetime,
        strategy_params: StrategyParams,
        risk_params: RiskParams,
        execution_params: ExecutionParams,
    ) -> Optional[WindowMetrics]:
        cfg = copy.deepcopy(self.base_config)
        cfg.apply_strategy_params(strategy_params)
        cfg.apply_risk_params(risk_params)
        cfg.apply_execution_params(execution_params)
        cfg.dry_run = True
        cfg.symbols = symbols

        window_rows: Dict[str, List[Dict]] = {}
        warmup = cfg.min_klines_required + 5
        for symbol in symbols:
            rows = history_by_symbol.get(symbol, [])
            if not rows:
                continue
            end_rows = [r for r in rows if r["open_time"] <= test_end]
            if not end_rows:
                continue
            first_test_idx = next((i for i, r in enumerate(end_rows) if r["close_time"] >= test_start), None)
            if first_test_idx is None:
                continue
            begin = max(0, first_test_idx - warmup)
            sliced = end_rows[begin:]
            if len(sliced) >= cfg.min_klines_required + 2:
                window_rows[symbol] = sliced

        if not window_rows:
            return None
        symbols_active = [s for s in symbols if s in window_rows]
        common_len = min(len(window_rows[s]) for s in symbols_active)
        if common_len < cfg.min_klines_required + 2:
            return None
        for symbol in symbols_active:
            window_rows[symbol] = window_rows[symbol][-common_len:]

        interval_seconds = _interval_to_seconds(cfg.kline_interval)
        bars_per_year = (365 * 24 * 3600) / max(60, interval_seconds)
        client = _HistoryBacktestClient(window_rows, interval_seconds)
        strategy = SpotStrategyEngine(client, cfg)
        execution = SpotExecutionEngine(client, cfg)

        equity_curve: List[float] = []
        test_start_idx = None
        start_idx = cfg.min_klines_required - 1
        for idx in range(start_idx, common_len):
            bar_time = max(window_rows[s][idx]["close_time"] for s in symbols_active)
            if test_start_idx is None and bar_time >= test_start:
                test_start_idx = idx
            execution.set_simulation_time(bar_time)
            client.set_index(idx)
            await execution.mark_positions()
            signals = await strategy.analyze_symbols(
                symbols_active,
                execution.positions,
                portfolio_state=execution.get_portfolio_state(),
            )
            if bar_time >= test_start:
                for signal in [s for s in signals if s.is_actionable()]:
                    await execution.execute_signal(signal)
                equity_curve.append(execution.get_stats()["account_value_usdt"])

        # force close in-window for stable stats
        if symbols_active:
            end_ts = max(window_rows[s][-1]["close_time"] for s in symbols_active)
            execution.set_simulation_time(end_ts)
            client.set_index(common_len - 1)
            for symbol in list(execution.positions.keys()):
                px = await client.get_spot_price(symbol)
                if not px:
                    continue
                sig = SpotSignal(
                    symbol=symbol,
                    action="SELL",
                    price=px,
                    confidence=1.0,
                    reason="window_end",
                    reasons=["window_end"],
                )
                await execution.execute_signal(sig)
            execution.set_simulation_time(None)

        stats = execution.get_stats()
        if not equity_curve:
            equity_curve = [stats["initial_capital_usdt"], stats["account_value_usdt"]]
        bar_returns: List[float] = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]
            if prev > 0:
                bar_returns.append((equity_curve[i] - prev) / prev)
        sharpe, sortino = self._sharpe_sortino(bar_returns, bars_per_year)
        mdd = self._max_drawdown_pct(equity_curve)

        sells = [t for t in execution.trades if t.side == "SELL"]
        wins = [t for t in sells if t.realized_pnl_usdt > 0]
        gross_profit = sum(max(0.0, t.realized_pnl_usdt) for t in sells)
        gross_loss = sum(max(0.0, -t.realized_pnl_usdt) for t in sells)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (3.0 if gross_profit > 0 else 0.0)

        # holding bars by symbol BUY->SELL pairing
        last_buy_time: Dict[str, datetime] = {}
        hold_bars: List[float] = []
        for trade in execution.trades:
            if trade.side == "BUY":
                last_buy_time[trade.symbol] = trade.timestamp
            elif trade.side == "SELL" and trade.symbol in last_buy_time:
                dt = (trade.timestamp - last_buy_time[trade.symbol]).total_seconds()
                hold_bars.append(dt / max(60, interval_seconds))
                last_buy_time.pop(trade.symbol, None)
        avg_holding_bars = mean(hold_bars) if hold_bars else 0.0

        total_fees = stats["fees_paid_usdt"]
        total_slippage = stats["slippage_cost_usdt"]
        cost_to_gross = (total_fees + total_slippage) / gross_profit if gross_profit > 0 else 1.0

        test_days = max(1e-9, (test_end - test_start).total_seconds() / 86400)
        total_ret = stats["total_return_pct"]
        annual_ret = ((1 + total_ret / 100) ** (365 / test_days) - 1) * 100 if total_ret > -100 else -100

        return WindowMetrics(
            annual_return_pct=annual_ret,
            sharpe=sharpe,
            sortino=sortino,
            max_drawdown_pct=mdd,
            win_rate_pct=(len(wins) / len(sells) * 100) if sells else 0.0,
            profit_factor=profit_factor,
            trade_count=len(sells),
            avg_holding_bars=avg_holding_bars,
            cost_to_gross_ratio=cost_to_gross,
            total_return_pct=total_ret,
            total_fees=total_fees,
            total_slippage=total_slippage,
        )

    def _fitness_from_windows(self, windows: List[WindowMetrics]) -> Tuple[float, Dict[str, float]]:
        if not windows:
            return -1e9, {"error": 1.0}

        ann = [w.annual_return_pct for w in windows]
        sharpe = [w.sharpe for w in windows]
        sortino = [w.sortino for w in windows]
        mdd = [w.max_drawdown_pct for w in windows]
        win = [w.win_rate_pct for w in windows]
        pf = [w.profit_factor for w in windows]
        trades = [w.trade_count for w in windows]
        hold = [w.avg_holding_bars for w in windows]
        cost = [w.cost_to_gross_ratio for w in windows]
        oos_ret = [w.total_return_pct for w in windows]

        avg_ann = mean(ann)
        avg_sharpe = mean(sharpe)
        avg_sortino = mean(sortino)
        avg_mdd = mean(mdd)
        avg_win = mean(win)
        avg_pf = mean(pf)
        avg_trades = mean(trades)
        avg_hold = mean(hold)
        avg_cost = mean(cost)

        stability_std = pstdev(oos_ret) if len(oos_ret) > 1 else 0.0
        worst_window = min(oos_ret)
        sharpe_std = pstdev(sharpe) if len(sharpe) > 1 else 0.0
        # Simplified DSR-like proxy (higher is better).
        dsr_proxy = avg_sharpe - 0.5 * sharpe_std

        target_trades = 40.0
        min_hold_bars = 2.0
        trade_penalty = max(0.0, (avg_trades - target_trades) / target_trades)
        hold_penalty = max(0.0, (min_hold_bars - avg_hold) / min_hold_bars)
        cost_penalty = max(0.0, avg_cost - 0.2)
        stability_penalty = max(0.0, stability_std / 10.0)
        worst_penalty = abs(min(0.0, worst_window))
        dsr_penalty = max(0.0, -dsr_proxy)

        w = self.weights
        positive = (
            w.ann_return * avg_ann
            + w.sharpe * avg_sharpe * 10
            + w.sortino * avg_sortino * 10
            + w.win_rate * avg_win
            + w.profit_factor * min(avg_pf, 5.0) * 20
        )
        negative = (
            w.max_drawdown * avg_mdd
            + w.trade_count * trade_penalty * 100
            + w.holding * hold_penalty * 100
            + w.cost_ratio * cost_penalty * 100
            + w.stability * stability_penalty * 100
            + w.worst_window * worst_penalty
            + w.dsr_proxy * dsr_penalty * 100
        )
        fitness = positive - negative
        metrics = {
            "avg_annual_return_pct": avg_ann,
            "avg_sharpe": avg_sharpe,
            "avg_sortino": avg_sortino,
            "avg_max_drawdown_pct": avg_mdd,
            "avg_win_rate_pct": avg_win,
            "avg_profit_factor": avg_pf,
            "avg_trade_count": avg_trades,
            "avg_holding_bars": avg_hold,
            "avg_cost_to_gross_ratio": avg_cost,
            "oos_return_std": stability_std,
            "worst_window_return_pct": worst_window,
            "dsr_proxy": dsr_proxy,
            "fitness": fitness,
        }
        return fitness, metrics

    async def _evaluate_candidate(
        self,
        candidate: Dict[str, Any],
        windows: List[Tuple[datetime, datetime, datetime, datetime]],
        history_by_symbol: Dict[str, List[Dict]],
        symbols: List[str],
    ) -> CandidateEvaluation:
        repaired = self.parameter_space.repair(candidate)
        if self.evaluator_override is not None:
            res = self.evaluator_override(repaired)
            fitness = float(res.get("fitness", -1e9))
            return CandidateEvaluation(
                candidate=repaired,
                fitness=fitness,
                metrics=res,
                per_window=[],
            )

        strategy_params, risk_params, execution_params = self.parameter_space.candidate_to_params(repaired)
        window_results: List[WindowMetrics] = []
        per_window_logs: List[Dict[str, float]] = []
        for _, _, test_start, test_end in windows:
            wm = await self._run_window_backtest(
                history_by_symbol=history_by_symbol,
                symbols=symbols,
                test_start=test_start,
                test_end=test_end,
                strategy_params=strategy_params,
                risk_params=risk_params,
                execution_params=execution_params,
            )
            if wm is None:
                continue
            window_results.append(wm)
            per_window_logs.append({
                "test_start": test_start.timestamp(),
                "test_end": test_end.timestamp(),
                "total_return_pct": wm.total_return_pct,
                "max_drawdown_pct": wm.max_drawdown_pct,
                "sharpe": wm.sharpe,
            })

        fitness, metrics = self._fitness_from_windows(window_results)
        return CandidateEvaluation(
            candidate=repaired,
            fitness=fitness,
            metrics=metrics,
            per_window=per_window_logs,
        )

    def _tournament_select(self, population: List[CandidateEvaluation], k: int = 3) -> CandidateEvaluation:
        sample = [population[self.rng.randrange(len(population))] for _ in range(max(1, k))]
        return max(sample, key=lambda x: x.fitness)

    def _save_generation_topk(self, generation: int, evaluated: List[CandidateEvaluation]):
        topk = sorted(evaluated, key=lambda x: x.fitness, reverse=True)[: self.settings.top_k_log]
        first_write = not self.gen_csv_path.exists()
        with open(self.gen_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if first_write:
                writer.writerow([
                    "generation",
                    "rank",
                    "fitness",
                    "avg_annual_return_pct",
                    "avg_sharpe",
                    "avg_max_drawdown_pct",
                    "worst_window_return_pct",
                    "dsr_proxy",
                    "params_json",
                ])
            for idx, item in enumerate(topk, start=1):
                writer.writerow([
                    generation,
                    idx,
                    item.fitness,
                    item.metrics.get("avg_annual_return_pct", 0.0),
                    item.metrics.get("avg_sharpe", 0.0),
                    item.metrics.get("avg_max_drawdown_pct", 0.0),
                    item.metrics.get("worst_window_return_pct", 0.0),
                    item.metrics.get("dsr_proxy", 0.0),
                    json.dumps(item.candidate, ensure_ascii=False, sort_keys=True),
                ])

    async def run(
        self,
        symbols: List[str],
        backtest_start: datetime,
        backtest_end: datetime,
        walkforward_train_days: int = 730,
        walkforward_test_days: int = 90,
        walkforward_step_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols:
            symbols = self.base_config.symbols[:]

        windows = build_walkforward_windows(
            start_time=backtest_start,
            end_time=backtest_end,
            train_days=walkforward_train_days,
            test_days=walkforward_test_days,
            step_days=walkforward_step_days,
        )
        if not windows:
            raise ValueError("No walk-forward windows were generated for this date range.")

        if self.evaluator_override is None:
            if not self.client:
                raise ValueError("Binance client is required for real GA evaluation.")
            history_start = min(w[0] for w in windows)
            history_end = max(w[3] for w in windows)
            strategy_interval = self.base_config.to_strategy_params().bar_interval
            tasks = [self._fetch_symbol_history(s, history_start, history_end, strategy_interval) for s in symbols]
            fetched = await asyncio.gather(*tasks, return_exceptions=True)
            history_by_symbol: Dict[str, List[Dict]] = {}
            for s, rows in zip(symbols, fetched):
                if isinstance(rows, Exception):
                    continue
                if rows:
                    history_by_symbol[s] = rows
            symbols = [s for s in symbols if s in history_by_symbol]
            if not symbols:
                raise ValueError("No valid symbol history available for GA.")
        else:
            history_by_symbol = {}

        pop_size = max(4, int(self.settings.population_size))
        generations = max(1, int(self.settings.generations))
        elitism_k = min(max(1, int(self.settings.elitism_k)), pop_size - 1)

        population = [self.parameter_space.sample(self.rng) for _ in range(pop_size)]
        best_eval: Optional[CandidateEvaluation] = None

        for gen in range(generations):
            evaluated: List[CandidateEvaluation] = []
            for cand in population:
                ev = await self._evaluate_candidate(cand, windows, history_by_symbol, symbols)
                evaluated.append(ev)
            evaluated.sort(key=lambda x: x.fitness, reverse=True)
            self._save_generation_topk(gen, evaluated)

            if best_eval is None or evaluated[0].fitness > best_eval.fitness:
                best_eval = evaluated[0]

            elites = [e.candidate for e in evaluated[:elitism_k]]
            next_population = elites[:]
            while len(next_population) < pop_size:
                parent_a = self._tournament_select(evaluated)
                parent_b = self._tournament_select(evaluated)
                if self.rng.random() < self.settings.crossover_rate:
                    child = self.parameter_space.crossover(parent_a.candidate, parent_b.candidate, self.rng)
                else:
                    child = dict(parent_a.candidate)
                child = self.parameter_space.mutate(child, self.rng, self.settings.mutation_rate)
                next_population.append(child)
            population = next_population

        if best_eval is None:
            raise RuntimeError("GA finished without candidate evaluation.")

        strategy_params, risk_params, execution_params = self.parameter_space.candidate_to_params(best_eval.candidate)
        best_payload = {
            "strategy_params": asdict(strategy_params),
            "risk_params": asdict(risk_params),
            "execution_params": asdict(execution_params),
            "fitness": best_eval.fitness,
            "metrics": best_eval.metrics,
            "oos_windows": best_eval.per_window,
        }
        with open(self.best_params_path, "w", encoding="utf-8") as f:
            json.dump(best_payload, f, ensure_ascii=False, indent=2)

        meta = {
            "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "bar_interval": strategy_params.bar_interval,
            "backtest_start": backtest_start.isoformat(),
            "backtest_end": backtest_end.isoformat(),
            "walkforward_train_days": walkforward_train_days,
            "walkforward_test_days": walkforward_test_days,
            "walkforward_step_days": walkforward_step_days if walkforward_step_days is not None else walkforward_test_days,
            "seed": self.settings.seed,
            "ga_settings": asdict(self.settings),
            "fitness_weights": asdict(self.weights),
            "base_strategy_params": asdict(self.base_config.to_strategy_params()),
            "base_risk_params": asdict(self.base_config.to_risk_params()),
            "base_execution_params": asdict(self.base_config.to_execution_params()),
            "search_dimensions": list(self.parameter_space.dimensions.keys()),
            "best_params_file": str(self.best_params_path),
            "generation_csv": str(self.gen_csv_path),
        }
        with open(self.run_meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return {
            "best_params_path": str(self.best_params_path),
            "run_meta_path": str(self.run_meta_path),
            "generation_csv_path": str(self.gen_csv_path),
            "best_fitness": best_eval.fitness,
            "best_metrics": best_eval.metrics,
            "best_candidate": best_eval.candidate,
        }
