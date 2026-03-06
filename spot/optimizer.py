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
    """将 K 线周期字符串（如 15m/1h）转换为秒数。"""
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
    """构建 walk-forward 窗口：返回 (train_start, train_end, test_start, test_end)。"""
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
    """fitness 各指标权重配置。"""
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
        """从 `k=v` 逗号字符串解析权重。"""
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
    """遗传算法超参数配置。"""
    population_size: int = 20
    generations: int = 10
    mutation_rate: float = 0.15
    crossover_rate: float = 0.75
    elitism_k: int = 2
    top_k_log: int = 5
    seed: int = 42


@dataclass
class FitnessConstraints:
    """GA 研究纪律硬约束与软惩罚目标。"""

    max_trades_per_day: float = 6.0
    min_avg_hold_bars: float = 6.0
    max_cost_ratio: float = 1.1
    target_trades_per_year: float = 220.0
    target_avg_hold_bars: float = 18.0


_COST_SENSITIVITY_MULTIPLIERS = (0.5, 1.0, 2.0)


class ParameterSpace:
    """参数搜索空间：负责采样、交叉、变异与约束修复。"""

    def __init__(
        self,
        base_config: SpotTradingConfig,
        search_timeframe: bool = False,
        search_risk: bool = False,
        search_cost: bool = False,
        max_search_dims: int = 14,
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
            "pullback_tol": {"type": "float", "min": 0.001, "max": 0.015},
            "ma_breakout_band": {"type": "float", "min": 0.0001, "max": 0.006},
            "band_atr_k": {"type": "float", "min": 0.0, "max": 1.5},
            "min_edge_over_cost": {"type": "float", "min": 0.0, "max": 0.01},
            "cost_buffer_k": {"type": "float", "min": 0.7, "max": 2.5},
            "min_atr_pct": {"type": "float", "min": 0.0, "max": 0.03},
            "max_mark_spot_diverge": {"type": "float", "min": 0.001, "max": 0.03},
            "premium_abs_max": {"type": "float", "min": 0.001, "max": 0.03},
            "funding_long_max": {"type": "float", "min": 0.0, "max": 0.003},
            "funding_cost_buffer_k": {"type": "float", "min": 0.0, "max": 6.0},
            # Keep legacy key searchable for backward compatibility.
            "confirm_breakout": {"type": "float", "min": 0.0001, "max": 0.006},
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

        # C3 hardening: cap search dimensionality, but force-include core structure/cost-gate dimensions.
        ordered_keys = list(dims.keys())
        selected_keys = ordered_keys[: self.max_search_dims]
        mandatory_dims = [
            "ma_breakout_band",
            "band_atr_k",
            "max_mark_spot_diverge",
            "premium_abs_max",
            "funding_long_max",
            "funding_cost_buffer_k",
        ]
        mandatory_set = set(mandatory_dims)
        for key in mandatory_dims:
            if key not in dims or key in selected_keys:
                continue
            drop_idx = next(
                (i for i in range(len(selected_keys) - 1, -1, -1) if selected_keys[i] not in mandatory_set),
                None,
            )
            if drop_idx is None:
                break
            selected_keys.pop(drop_idx)
            selected_keys.append(key)
        selected_set = set(selected_keys)
        selected_keys = [k for k in ordered_keys if k in selected_set]
        self.dimensions: Dict[str, Dict[str, Any]] = {k: dims[k] for k in selected_keys}

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
    """窗口化内存数据客户端：为 GA 评估提供回测行情接口。"""

    def __init__(
        self,
        symbol_rows: Dict[str, List[Dict]],
        interval_seconds: int,
        symbol_mark_rows: Optional[Dict[str, List[Dict]]] = None,
        symbol_premium_rows: Optional[Dict[str, List[Dict]]] = None,
        symbol_funding_rows: Optional[Dict[str, List[Dict]]] = None,
    ):
        self.symbol_rows = {
            s: sorted(rows, key=lambda x: x["open_time"])
            for s, rows in symbol_rows.items()
        }
        self.symbol_mark_rows = {
            s: sorted(rows, key=lambda x: x["open_time"])
            for s, rows in (symbol_mark_rows or {}).items()
        }
        self.symbol_premium_rows = {
            s: sorted(rows, key=lambda x: x["open_time"])
            for s, rows in (symbol_premium_rows or {}).items()
        }
        self.symbol_funding_rows = {
            s: sorted(rows, key=lambda x: x["funding_time"])
            for s, rows in (symbol_funding_rows or {}).items()
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

    def _current_symbol_time(self, symbol: str) -> Optional[datetime]:
        rows = self.symbol_rows.get(symbol, [])
        if not rows:
            return None
        idx = min(len(rows) - 1, self.current_index)
        return rows[idx].get("close_time") or rows[idx].get("open_time")

    def _aux_rows(self, source: Dict[str, List[Dict]], symbol: str) -> List[Dict]:
        rows = source.get(symbol, [])
        if not rows:
            return []
        current_time = self._current_symbol_time(symbol)
        if not current_time:
            return []
        return [r for r in rows if (r.get("close_time") or r.get("open_time")) <= current_time]

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

    async def get_mark_price_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self._aux_rows(self.symbol_mark_rows, symbol)
        if start_time:
            rows = [r for r in rows if r["open_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["open_time"] <= end_time]
        return rows[-limit:] if limit > 0 else rows

    async def get_premium_index_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self._aux_rows(self.symbol_premium_rows, symbol)
        if start_time:
            rows = [r for r in rows if r["open_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["open_time"] <= end_time]
        return rows[-limit:] if limit > 0 else rows

    async def get_funding_rate_history(
        self,
        symbol: str,
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict]:
        rows = self.symbol_funding_rows.get(symbol, [])
        if not rows:
            return []
        current_time = self._current_symbol_time(symbol)
        if current_time:
            rows = [r for r in rows if r["funding_time"] <= current_time]
        if start_time:
            rows = [r for r in rows if r["funding_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["funding_time"] <= end_time]
        return rows[-limit:] if limit > 0 else rows


@dataclass
class WindowMetrics:
    """单个 OOS 窗口的绩效指标快照。"""
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
    gross_pnl_usdt: float
    trades_per_year: float
    trades_per_day: float


@dataclass
class CandidateEvaluation:
    """候选参数评估结果：fitness、聚合指标与窗口明细。"""
    candidate: Dict[str, Any]
    fitness: float
    metrics: Dict[str, Any]
    per_window: List[Dict[str, Any]] = field(default_factory=list)


class SpotGAOptimizer:
    """GA 优化器：以 walk-forward OOS 结果作为核心适应度。"""

    def __init__(
        self,
        client: Optional[BinanceClient],
        base_config: SpotTradingConfig,
        output_dir: str,
        parameter_space: ParameterSpace,
        settings: GASettings,
        weights: FitnessWeights,
        constraints: Optional[FitnessConstraints] = None,
        evaluator_override: Optional[Callable[[Dict[str, Any]], Dict[str, float]]] = None,
    ):
        self.client = client
        self.base_config = base_config
        self.parameter_space = parameter_space
        self.settings = settings
        self.weights = weights
        self.constraints = constraints or FitnessConstraints()
        self.rng = random.Random(settings.seed)
        self.evaluator_override = evaluator_override

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(output_dir) / f"spot_ga_{ts}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.gen_csv_path = self.run_dir / "generation_topk.csv"
        self.best_params_path = self.run_dir / "best_params.json"
        self.run_meta_path = self.run_dir / "run_meta.json"
        self.cost_sensitivity_path = self.run_dir / "cost_sensitivity_curve.csv"
        self.worst_window_report_path = self.run_dir / "worst_window_report.json"
        self.final_validation_report_path = self.run_dir / "final_validation_report.json"

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

    async def _fetch_symbol_aux_klines(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
        method_name: str,
    ) -> List[Dict]:
        if not self.client or not hasattr(self.client, method_name):
            return []
        interval_seconds = _interval_to_seconds(interval)
        cursor = start
        rows: List[Dict] = []
        getter = getattr(self.client, method_name)
        while cursor < end:
            batch = await getter(
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

    async def _fetch_symbol_funding_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> List[Dict]:
        if not self.client or not hasattr(self.client, "get_funding_rate_history"):
            return []
        cursor = start
        rows: List[Dict] = []
        while cursor < end:
            batch = await self.client.get_funding_rate_history(
                symbol=symbol,
                limit=1000,
                start_time=cursor,
                end_time=end,
            )
            if not batch:
                break
            for item in batch:
                if not rows or item["funding_time"] > rows[-1]["funding_time"]:
                    rows.append(item)
            nxt = batch[-1]["funding_time"] + timedelta(seconds=1)
            if nxt <= cursor:
                break
            cursor = nxt
            if len(batch) < 1000:
                break
            await asyncio.sleep(0.02)
        return rows

    @staticmethod
    def _max_drawdown_pct(equity_curve: Sequence[float]) -> float:
        """计算权益曲线最大回撤（百分比）。"""
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
        """根据 bar 收益率计算 Sharpe 与 Sortino。"""
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
        mark_history_by_symbol: Dict[str, List[Dict]],
        premium_history_by_symbol: Dict[str, List[Dict]],
        funding_history_by_symbol: Dict[str, List[Dict]],
        symbols: List[str],
        test_start: datetime,
        test_end: datetime,
        strategy_params: StrategyParams,
        risk_params: RiskParams,
        execution_params: ExecutionParams,
    ) -> Optional[WindowMetrics]:
        """在单个 OOS 窗口执行回测并产出窗口级指标。"""
        cfg = copy.deepcopy(self.base_config)
        cfg.apply_strategy_params(strategy_params)
        cfg.apply_risk_params(risk_params)
        cfg.apply_execution_params(execution_params)
        cfg.dry_run = True
        cfg.symbols = symbols

        window_rows: Dict[str, List[Dict]] = {}
        window_mark_rows: Dict[str, List[Dict]] = {}
        window_premium_rows: Dict[str, List[Dict]] = {}
        window_funding_rows: Dict[str, List[Dict]] = {}
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
                window_mark_rows[symbol] = [
                    r for r in (mark_history_by_symbol.get(symbol, []) or [])
                    if r["open_time"] <= test_end
                ]
                window_premium_rows[symbol] = [
                    r for r in (premium_history_by_symbol.get(symbol, []) or [])
                    if r["open_time"] <= test_end
                ]
                window_funding_rows[symbol] = [
                    r for r in (funding_history_by_symbol.get(symbol, []) or [])
                    if r["funding_time"] <= test_end
                ]

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
        client = _HistoryBacktestClient(
            window_rows,
            interval_seconds,
            symbol_mark_rows=window_mark_rows,
            symbol_premium_rows=window_premium_rows,
            symbol_funding_rows=window_funding_rows,
        )
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
        gross_pnl = stats["realized_pnl_usdt"] + total_fees + total_slippage
        cost_to_gross = (total_fees + total_slippage) / max(abs(gross_pnl), 1e-9)

        test_days = max(1e-9, (test_end - test_start).total_seconds() / 86400)
        total_ret = stats["total_return_pct"]
        annual_ret = ((1 + total_ret / 100) ** (365 / test_days) - 1) * 100 if total_ret > -100 else -100
        trades_per_year = len(sells) / test_days * 365.0
        trades_per_day = len(sells) / test_days

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
            gross_pnl_usdt=gross_pnl,
            trades_per_year=trades_per_year,
            trades_per_day=trades_per_day,
        )

    def _fitness_from_windows(self, windows: List[WindowMetrics]) -> Tuple[float, Dict[str, Any]]:
        """把多个窗口指标聚合成一个 fitness 与解释性统计。"""
        if not windows:
            return -1e9, {"error": 1.0}

        ann = [w.annual_return_pct for w in windows]
        sharpe = [w.sharpe for w in windows]
        sortino = [w.sortino for w in windows]
        mdd = [w.max_drawdown_pct for w in windows]
        win = [w.win_rate_pct for w in windows]
        pf = [w.profit_factor for w in windows]
        trades = [w.trade_count for w in windows]
        trades_per_year = [w.trades_per_year for w in windows]
        trades_per_day = [w.trades_per_day for w in windows]
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
        avg_trades_per_year = mean(trades_per_year)
        avg_trades_per_day = mean(trades_per_day)
        avg_hold = mean(hold)
        avg_cost = mean(cost)

        stability_std = pstdev(oos_ret) if len(oos_ret) > 1 else 0.0
        worst_window = min(oos_ret)
        sharpe_std = pstdev(sharpe) if len(sharpe) > 1 else 0.0
        # Simplified DSR-like proxy (higher is better).
        dsr_proxy = avg_sharpe - 0.5 * sharpe_std

        c = self.constraints
        hard_fails: List[str] = []
        if max(trades_per_day) > c.max_trades_per_day:
            hard_fails.append(
                f"trades_per_day_exceeded:max={max(trades_per_day):.2f}>limit={c.max_trades_per_day:.2f}"
            )
        if min(hold) < c.min_avg_hold_bars:
            hard_fails.append(
                f"avg_hold_bars_too_low:min={min(hold):.2f}<limit={c.min_avg_hold_bars:.2f}"
            )
        if max(cost) > c.max_cost_ratio:
            hard_fails.append(
                f"cost_ratio_too_high:max={max(cost):.2f}>limit={c.max_cost_ratio:.2f}"
            )

        trade_year_penalty = max(
            0.0,
            (avg_trades_per_year - c.target_trades_per_year) / max(1.0, c.target_trades_per_year),
        )
        hold_penalty = max(
            0.0,
            (c.target_avg_hold_bars - avg_hold) / max(1.0, c.target_avg_hold_bars),
        )
        cost_penalty = max(0.0, avg_cost - 0.35)
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
            + w.trade_count * trade_year_penalty * 120
            + w.holding * hold_penalty * 120
            + w.cost_ratio * cost_penalty * 120
            + w.stability * stability_penalty * 100
            + w.worst_window * worst_penalty
            + w.dsr_proxy * dsr_penalty * 100
        )
        fitness = positive - negative
        constraints_pass = 1.0 if not hard_fails else 0.0
        if hard_fails:
            # Hard constraint violation: directly mark as extreme poor fitness.
            fitness = -1e8 - len(hard_fails) * 1e5

        metrics = {
            "avg_annual_return_pct": avg_ann,
            "avg_sharpe": avg_sharpe,
            "avg_sortino": avg_sortino,
            "avg_max_drawdown_pct": avg_mdd,
            "avg_win_rate_pct": avg_win,
            "avg_profit_factor": avg_pf,
            "avg_trade_count": avg_trades,
            "avg_trades_per_year": avg_trades_per_year,
            "avg_trades_per_day": avg_trades_per_day,
            "avg_holding_bars": avg_hold,
            "avg_cost_to_gross_ratio": avg_cost,
            "max_trades_per_day": max(trades_per_day),
            "min_avg_holding_bars": min(hold),
            "max_cost_to_gross_ratio": max(cost),
            "constraints_pass": constraints_pass,
            "hard_constraint_failures": " | ".join(hard_fails),
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
        mark_history_by_symbol: Dict[str, List[Dict]],
        premium_history_by_symbol: Dict[str, List[Dict]],
        funding_history_by_symbol: Dict[str, List[Dict]],
        symbols: List[str],
    ) -> CandidateEvaluation:
        """评估单个候选参数：跨窗口回测后计算 fitness。"""
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
        per_window_logs: List[Dict[str, Any]] = []
        for _, _, test_start, test_end in windows:
            wm = await self._run_window_backtest(
                history_by_symbol=history_by_symbol,
                mark_history_by_symbol=mark_history_by_symbol,
                premium_history_by_symbol=premium_history_by_symbol,
                funding_history_by_symbol=funding_history_by_symbol,
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
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "total_return_pct": wm.total_return_pct,
                "max_drawdown_pct": wm.max_drawdown_pct,
                "sharpe": wm.sharpe,
                "trades_per_year": wm.trades_per_year,
                "avg_holding_bars": wm.avg_holding_bars,
                "cost_to_gross_ratio": wm.cost_to_gross_ratio,
            })

        fitness, metrics = self._fitness_from_windows(window_results)
        return CandidateEvaluation(
            candidate=repaired,
            fitness=fitness,
            metrics=metrics,
            per_window=per_window_logs,
        )

    def _tournament_select(self, population: List[CandidateEvaluation], k: int = 3) -> CandidateEvaluation:
        """锦标赛选择：从随机样本中选出 fitness 最优个体。"""
        sample = [population[self.rng.randrange(len(population))] for _ in range(max(1, k))]
        return max(sample, key=lambda x: x.fitness)

    def _save_generation_topk(self, generation: int, evaluated: List[CandidateEvaluation]):
        """把每一代 top-k 候选写入 CSV，便于回溯分析。"""
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

    @staticmethod
    def _window_metrics_to_dict(wm: WindowMetrics) -> Dict[str, float]:
        return {
            "annual_return_pct": wm.annual_return_pct,
            "sharpe": wm.sharpe,
            "sortino": wm.sortino,
            "max_drawdown_pct": wm.max_drawdown_pct,
            "win_rate_pct": wm.win_rate_pct,
            "profit_factor": wm.profit_factor,
            "trade_count": wm.trade_count,
            "avg_holding_bars": wm.avg_holding_bars,
            "cost_to_gross_ratio": wm.cost_to_gross_ratio,
            "total_return_pct": wm.total_return_pct,
            "total_fees": wm.total_fees,
            "total_slippage": wm.total_slippage,
            "gross_pnl_usdt": wm.gross_pnl_usdt,
            "trades_per_year": wm.trades_per_year,
            "trades_per_day": wm.trades_per_day,
        }

    def _write_worst_window_report(
        self,
        best_eval: CandidateEvaluation,
        train_start: datetime,
        train_end: datetime,
    ) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "train_start": train_start.isoformat(),
            "train_end": train_end.isoformat(),
            "status": "OK",
            "window_count": len(best_eval.per_window),
            "worst_window": None,
            "hard_constraint_failures": best_eval.metrics.get("hard_constraint_failures", ""),
        }
        if not best_eval.per_window:
            report["status"] = "NO_WINDOW_LOGS"
        else:
            worst = min(best_eval.per_window, key=lambda x: x.get("total_return_pct", 0.0))
            report["worst_window"] = worst
        with open(self.worst_window_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report

    async def _write_cost_sensitivity_curve(
        self,
        history_by_symbol: Dict[str, List[Dict]],
        mark_history_by_symbol: Dict[str, List[Dict]],
        premium_history_by_symbol: Dict[str, List[Dict]],
        funding_history_by_symbol: Dict[str, List[Dict]],
        symbols: List[str],
        final_start: datetime,
        final_end: datetime,
        strategy_params: StrategyParams,
        risk_params: RiskParams,
        execution_params: ExecutionParams,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        with open(self.cost_sensitivity_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "multiplier",
                "fee_bps",
                "slippage_bps",
                "total_return_pct",
                "annual_return_pct",
                "sharpe",
                "max_drawdown_pct",
                "trades_per_year",
                "avg_holding_bars",
                "cost_to_gross_ratio",
            ])
            for mult in _COST_SENSITIVITY_MULTIPLIERS:
                scaled_exec = copy.deepcopy(execution_params)
                scaled_exec.fee_bps = max(0.0, scaled_exec.fee_bps * mult)
                scaled_exec.slippage_bps = max(0.0, scaled_exec.slippage_bps * mult)
                wm = await self._run_window_backtest(
                    history_by_symbol=history_by_symbol,
                    mark_history_by_symbol=mark_history_by_symbol,
                    premium_history_by_symbol=premium_history_by_symbol,
                    funding_history_by_symbol=funding_history_by_symbol,
                    symbols=symbols,
                    test_start=final_start,
                    test_end=final_end,
                    strategy_params=strategy_params,
                    risk_params=risk_params,
                    execution_params=scaled_exec,
                )
                if wm is None:
                    row = {
                        "multiplier": mult,
                        "fee_bps": scaled_exec.fee_bps,
                        "slippage_bps": scaled_exec.slippage_bps,
                        "error": "insufficient_data",
                    }
                    rows.append(row)
                    writer.writerow([mult, scaled_exec.fee_bps, scaled_exec.slippage_bps, "", "", "", "", "", "", ""])
                    continue
                row = {
                    "multiplier": mult,
                    "fee_bps": scaled_exec.fee_bps,
                    "slippage_bps": scaled_exec.slippage_bps,
                    **self._window_metrics_to_dict(wm),
                }
                rows.append(row)
                writer.writerow([
                    mult,
                    scaled_exec.fee_bps,
                    scaled_exec.slippage_bps,
                    wm.total_return_pct,
                    wm.annual_return_pct,
                    wm.sharpe,
                    wm.max_drawdown_pct,
                    wm.trades_per_year,
                    wm.avg_holding_bars,
                    wm.cost_to_gross_ratio,
                ])
        return rows

    def _write_final_validation_report(
        self,
        final_start: datetime,
        final_end: datetime,
        final_metrics: Optional[WindowMetrics],
        cost_curve: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        if final_metrics is None:
            checks.append({"name": "final_window_available", "pass": False, "detail": "insufficient_data"})
            status = "SKIPPED"
            report = {
                "status": status,
                "final_start": final_start.isoformat(),
                "final_end": final_end.isoformat(),
                "constraints": asdict(self.constraints),
                "checks": checks,
                "final_metrics": None,
                "cost_sensitivity": cost_curve,
            }
            with open(self.final_validation_report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            return report

        c = self.constraints
        checks.extend([
            {
                "name": "final_total_return_non_negative",
                "pass": final_metrics.total_return_pct >= 0.0,
                "detail": f"{final_metrics.total_return_pct:+.2f}%",
            },
            {
                "name": "trades_per_day_limit",
                "pass": final_metrics.trades_per_day <= c.max_trades_per_day,
                "detail": f"{final_metrics.trades_per_day:.2f} <= {c.max_trades_per_day:.2f}",
            },
            {
                "name": "min_avg_hold_bars",
                "pass": final_metrics.avg_holding_bars >= c.min_avg_hold_bars,
                "detail": f"{final_metrics.avg_holding_bars:.2f} >= {c.min_avg_hold_bars:.2f}",
            },
            {
                "name": "max_cost_ratio",
                "pass": final_metrics.cost_to_gross_ratio <= c.max_cost_ratio,
                "detail": f"{final_metrics.cost_to_gross_ratio:.2f} <= {c.max_cost_ratio:.2f}",
            },
        ])

        base_row = next((r for r in cost_curve if r.get("multiplier") == 1.0 and "total_return_pct" in r), None)
        high_row = next((r for r in cost_curve if r.get("multiplier") == 2.0 and "total_return_pct" in r), None)
        if base_row and high_row:
            checks.append({
                "name": "cost_sensitivity_drop_controlled",
                "pass": (high_row["total_return_pct"] - base_row["total_return_pct"]) >= -8.0,
                "detail": f"delta={high_row['total_return_pct'] - base_row['total_return_pct']:+.2f}%",
            })

        status = "PASS" if all(bool(c.get("pass")) for c in checks) else "FAIL"
        report = {
            "status": status,
            "final_start": final_start.isoformat(),
            "final_end": final_end.isoformat(),
            "constraints": asdict(self.constraints),
            "checks": checks,
            "final_metrics": self._window_metrics_to_dict(final_metrics),
            "cost_sensitivity": cost_curve,
            "fail_reasons": [x["name"] for x in checks if not x.get("pass")],
        }
        with open(self.final_validation_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report

    async def run(
        self,
        symbols: List[str],
        backtest_start: datetime,
        backtest_end: datetime,
        walkforward_train_days: int = 730,
        walkforward_test_days: int = 90,
        walkforward_step_days: Optional[int] = None,
        final_validation_days: int = 120,
    ) -> Dict[str, Any]:
        """运行完整 GA 主循环并导出 best_params/run_meta/代际日志。"""
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols:
            symbols = self.base_config.symbols[:]

        final_validation_days = max(30, int(final_validation_days))
        final_start = backtest_end - timedelta(days=final_validation_days)
        if final_start <= backtest_start:
            raise ValueError("Final validation window leaves no room for training period.")
        min_train_span = timedelta(days=max(60, walkforward_train_days + walkforward_test_days))
        if final_start - backtest_start < min_train_span:
            raise ValueError("Training span is too short after reserving final validation window.")

        windows = build_walkforward_windows(
            start_time=backtest_start,
            end_time=final_start,
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
            history_end = backtest_end
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

            mark_tasks = [
                self._fetch_symbol_aux_klines(s, history_start, history_end, strategy_interval, "get_mark_price_klines")
                for s in symbols
            ]
            premium_tasks = [
                self._fetch_symbol_aux_klines(s, history_start, history_end, strategy_interval, "get_premium_index_klines")
                for s in symbols
            ]
            funding_tasks = [self._fetch_symbol_funding_history(s, history_start, history_end) for s in symbols]
            fetched_mark, fetched_premium, fetched_funding = await asyncio.gather(
                asyncio.gather(*mark_tasks, return_exceptions=True),
                asyncio.gather(*premium_tasks, return_exceptions=True),
                asyncio.gather(*funding_tasks, return_exceptions=True),
            )
            mark_history_by_symbol: Dict[str, List[Dict]] = {}
            premium_history_by_symbol: Dict[str, List[Dict]] = {}
            funding_history_by_symbol: Dict[str, List[Dict]] = {}
            for s, rows in zip(symbols, fetched_mark):
                if isinstance(rows, list):
                    mark_history_by_symbol[s] = rows
            for s, rows in zip(symbols, fetched_premium):
                if isinstance(rows, list):
                    premium_history_by_symbol[s] = rows
            for s, rows in zip(symbols, fetched_funding):
                if isinstance(rows, list):
                    funding_history_by_symbol[s] = rows
        else:
            history_by_symbol = {}
            mark_history_by_symbol = {}
            premium_history_by_symbol = {}
            funding_history_by_symbol = {}

        pop_size = max(4, int(self.settings.population_size))
        generations = max(1, int(self.settings.generations))
        elitism_k = min(max(1, int(self.settings.elitism_k)), pop_size - 1)

        population = [self.parameter_space.sample(self.rng) for _ in range(pop_size)]
        best_eval: Optional[CandidateEvaluation] = None

        for gen in range(generations):
            evaluated: List[CandidateEvaluation] = []
            for cand in population:
                ev = await self._evaluate_candidate(
                    cand,
                    windows,
                    history_by_symbol,
                    mark_history_by_symbol,
                    premium_history_by_symbol,
                    funding_history_by_symbol,
                    symbols,
                )
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
        worst_window_report = self._write_worst_window_report(best_eval, backtest_start, final_start)

        final_metrics: Optional[WindowMetrics] = None
        cost_curve: List[Dict[str, Any]] = []
        if self.evaluator_override is None:
            final_metrics = await self._run_window_backtest(
                history_by_symbol=history_by_symbol,
                mark_history_by_symbol=mark_history_by_symbol,
                premium_history_by_symbol=premium_history_by_symbol,
                funding_history_by_symbol=funding_history_by_symbol,
                symbols=symbols,
                test_start=final_start,
                test_end=backtest_end,
                strategy_params=strategy_params,
                risk_params=risk_params,
                execution_params=execution_params,
            )
            cost_curve = await self._write_cost_sensitivity_curve(
                history_by_symbol=history_by_symbol,
                mark_history_by_symbol=mark_history_by_symbol,
                premium_history_by_symbol=premium_history_by_symbol,
                funding_history_by_symbol=funding_history_by_symbol,
                symbols=symbols,
                final_start=final_start,
                final_end=backtest_end,
                strategy_params=strategy_params,
                risk_params=risk_params,
                execution_params=execution_params,
            )
        else:
            with open(self.cost_sensitivity_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["multiplier", "status"])
                for mult in _COST_SENSITIVITY_MULTIPLIERS:
                    writer.writerow([mult, "SKIPPED_EVALUATOR_OVERRIDE"])
                    cost_curve.append({"multiplier": mult, "status": "SKIPPED_EVALUATOR_OVERRIDE"})

        final_validation_report = self._write_final_validation_report(
            final_start=final_start,
            final_end=backtest_end,
            final_metrics=final_metrics,
            cost_curve=cost_curve,
        )

        best_payload = {
            "strategy_params": asdict(strategy_params),
            "risk_params": asdict(risk_params),
            "execution_params": asdict(execution_params),
            "fitness": best_eval.fitness,
            "metrics": best_eval.metrics,
            "oos_windows": best_eval.per_window,
            "research_discipline": {
                "train_start": backtest_start.isoformat(),
                "train_end": final_start.isoformat(),
                "final_validation_start": final_start.isoformat(),
                "final_validation_end": backtest_end.isoformat(),
                "constraints": asdict(self.constraints),
                "worst_window_report": str(self.worst_window_report_path),
                "cost_sensitivity_curve": str(self.cost_sensitivity_path),
                "final_validation_report": str(self.final_validation_report_path),
                "final_validation_status": final_validation_report.get("status"),
            },
        }
        with open(self.best_params_path, "w", encoding="utf-8") as f:
            json.dump(best_payload, f, ensure_ascii=False, indent=2)

        meta = {
            "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "bar_interval": strategy_params.bar_interval,
            "backtest_start": backtest_start.isoformat(),
            "backtest_end": backtest_end.isoformat(),
            "train_start": backtest_start.isoformat(),
            "train_end": final_start.isoformat(),
            "final_validation_start": final_start.isoformat(),
            "final_validation_end": backtest_end.isoformat(),
            "final_validation_days": final_validation_days,
            "walkforward_train_days": walkforward_train_days,
            "walkforward_test_days": walkforward_test_days,
            "walkforward_step_days": walkforward_step_days if walkforward_step_days is not None else walkforward_test_days,
            "seed": self.settings.seed,
            "ga_settings": asdict(self.settings),
            "fitness_weights": asdict(self.weights),
            "fitness_constraints": asdict(self.constraints),
            "base_strategy_params": asdict(self.base_config.to_strategy_params()),
            "base_risk_params": asdict(self.base_config.to_risk_params()),
            "base_execution_params": asdict(self.base_config.to_execution_params()),
            "search_dimensions": list(self.parameter_space.dimensions.keys()),
            "best_params_file": str(self.best_params_path),
            "generation_csv": str(self.gen_csv_path),
            "cost_sensitivity_curve": str(self.cost_sensitivity_path),
            "worst_window_report": str(self.worst_window_report_path),
            "final_validation_report": str(self.final_validation_report_path),
            "final_validation_status": final_validation_report.get("status"),
            "worst_window_summary": worst_window_report.get("worst_window"),
        }
        with open(self.run_meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return {
            "best_params_path": str(self.best_params_path),
            "run_meta_path": str(self.run_meta_path),
            "generation_csv_path": str(self.gen_csv_path),
            "cost_sensitivity_curve_path": str(self.cost_sensitivity_path),
            "worst_window_report_path": str(self.worst_window_report_path),
            "final_validation_report_path": str(self.final_validation_report_path),
            "best_fitness": best_eval.fitness,
            "best_metrics": best_eval.metrics,
            "best_candidate": best_eval.candidate,
            "final_validation_status": final_validation_report.get("status"),
        }
