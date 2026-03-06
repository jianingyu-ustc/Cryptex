"""
Configuration for spot auto-trading subsystem.
"""

import json
import os
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Load .env if present.
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())


SUPPORTED_BAR_INTERVALS = ("15m", "30m", "1h", "4h", "1d")
SUPPORTED_DECISION_TIMING = ("on_close", "intrabar")


@dataclass
class StrategyParams:
    """策略参数集合：控制指标窗口、入场过滤、出场阈值与结构约束。"""

    bar_interval: str = "15m"
    decision_timing: str = "on_close"

    fast_ma_len: int = 9
    slow_ma_len: int = 21
    rsi_len: int = 14
    atr_len: int = 14
    adx_len: int = 14

    pullback_tol: float = 0.003
    # Legacy alias for breakout percentage band (kept for backward compatibility).
    confirm_breakout: float = 0.0005
    ma_breakout_band: float = 0.0005
    band_atr_k: float = 0.2
    min_edge_over_cost: float = 0.0
    cost_buffer_k: float = 1.0
    min_atr_pct: float = 0.0
    max_mark_spot_gap_pct: float = 0.012
    premium_abs_entry_max: float = 0.006
    premium_z_entry_min: float = -2.2
    premium_z_entry_max: float = 2.2
    max_mark_spot_gap_exit: float = 0.02
    enable_overheat_derisk_exit: bool = True
    overheat_exit_min_pnl_pct: float = 0.4
    overheat_exit_funding_min: float = 0.0002
    overheat_exit_premium_abs_min: float = 0.004
    max_mark_spot_diverge: float = 0.012
    premium_abs_max: float = 0.008
    funding_long_max: float = 0.0005
    funding_cost_buffer_k: float = 1.0
    rsi_buy_min: float = 45.0
    rsi_buy_max: float = 65.0
    adx_min: float = 18.0
    trend_strength_min: float = 0.003
    min_24h_quote_volume: float = 20_000_000.0

    atr_k: float = 2.0
    trail_atr_k: float = 2.5
    rsi_sell_min: float = 45.0

    def repair(self) -> "StrategyParams":
        """修复非法或越界参数，保证策略计算与约束始终成立。"""
        if self.bar_interval not in SUPPORTED_BAR_INTERVALS:
            self.bar_interval = "15m"
        if self.decision_timing not in SUPPORTED_DECISION_TIMING:
            self.decision_timing = "on_close"

        self.fast_ma_len = max(2, int(self.fast_ma_len))
        self.slow_ma_len = max(int(self.slow_ma_len), self.fast_ma_len * 2)
        self.rsi_len = max(2, int(self.rsi_len))
        self.atr_len = max(2, int(self.atr_len))
        self.adx_len = max(2, int(self.adx_len))

        self.pullback_tol = max(0.0001, float(self.pullback_tol))
        self.confirm_breakout = max(0.0, float(self.confirm_breakout))
        self.ma_breakout_band = max(0.0, float(self.ma_breakout_band))
        if self.ma_breakout_band <= 0 and self.confirm_breakout > 0:
            self.ma_breakout_band = self.confirm_breakout
        self.confirm_breakout = self.ma_breakout_band
        self.band_atr_k = max(0.0, float(self.band_atr_k))
        if self.band_atr_k <= 0 and self.ma_breakout_band <= 0:
            self.ma_breakout_band = 0.0001
            self.confirm_breakout = self.ma_breakout_band
        self.min_edge_over_cost = max(0.0, float(self.min_edge_over_cost))
        self.cost_buffer_k = max(0.1, float(self.cost_buffer_k))
        self.min_atr_pct = max(0.0, float(self.min_atr_pct))
        self.max_mark_spot_gap_pct = max(0.0, float(self.max_mark_spot_gap_pct))
        self.max_mark_spot_diverge = max(0.0, float(self.max_mark_spot_diverge))
        if self.max_mark_spot_gap_pct <= 0 and self.max_mark_spot_diverge > 0:
            self.max_mark_spot_gap_pct = self.max_mark_spot_diverge
        if self.max_mark_spot_diverge <= 0 and self.max_mark_spot_gap_pct > 0:
            self.max_mark_spot_diverge = self.max_mark_spot_gap_pct
        if self.max_mark_spot_gap_pct <= 0 and self.max_mark_spot_diverge <= 0:
            self.max_mark_spot_gap_pct = 0.012
            self.max_mark_spot_diverge = 0.012

        self.premium_abs_entry_max = max(0.0, float(self.premium_abs_entry_max))
        self.premium_abs_max = max(0.0, float(self.premium_abs_max))
        if self.premium_abs_entry_max <= 0 and self.premium_abs_max > 0:
            self.premium_abs_entry_max = self.premium_abs_max
        if self.premium_abs_max <= 0 and self.premium_abs_entry_max > 0:
            self.premium_abs_max = self.premium_abs_entry_max
        if self.premium_abs_entry_max <= 0 and self.premium_abs_max <= 0:
            self.premium_abs_entry_max = 0.006
            self.premium_abs_max = 0.008

        self.premium_z_entry_min = max(-12.0, min(12.0, float(self.premium_z_entry_min)))
        self.premium_z_entry_max = max(-12.0, min(12.0, float(self.premium_z_entry_max)))
        if self.premium_z_entry_min >= self.premium_z_entry_max:
            self.premium_z_entry_max = self.premium_z_entry_min + 0.5

        self.max_mark_spot_gap_exit = max(0.0, float(self.max_mark_spot_gap_exit))
        if self.max_mark_spot_gap_exit < self.max_mark_spot_gap_pct:
            self.max_mark_spot_gap_exit = self.max_mark_spot_gap_pct

        self.enable_overheat_derisk_exit = bool(self.enable_overheat_derisk_exit)
        self.overheat_exit_min_pnl_pct = max(0.0, float(self.overheat_exit_min_pnl_pct))
        self.overheat_exit_funding_min = float(self.overheat_exit_funding_min)
        self.overheat_exit_premium_abs_min = max(0.0, float(self.overheat_exit_premium_abs_min))

        self.funding_long_max = float(self.funding_long_max)
        self.funding_cost_buffer_k = max(0.0, float(self.funding_cost_buffer_k))
        self.rsi_buy_min = min(99.0, max(0.0, float(self.rsi_buy_min)))
        self.rsi_buy_max = min(100.0, max(1.0, float(self.rsi_buy_max)))
        if self.rsi_buy_min >= self.rsi_buy_max:
            self.rsi_buy_max = min(100.0, self.rsi_buy_min + 5.0)

        self.adx_min = max(0.0, float(self.adx_min))
        self.trend_strength_min = max(0.0, float(self.trend_strength_min))
        self.min_24h_quote_volume = max(0.0, float(self.min_24h_quote_volume))

        self.atr_k = max(0.1, float(self.atr_k))
        self.trail_atr_k = max(float(self.trail_atr_k), self.atr_k)
        self.rsi_sell_min = min(100.0, max(0.0, float(self.rsi_sell_min)))
        return self

    @property
    def min_klines_required(self) -> int:
        """根据当前指标窗口估算最小预热 K 线数量。"""
        adx_need = self.adx_len * 2 + 5
        atr_need = self.atr_len + 5
        return max(self.slow_ma_len + 5, self.rsi_len + 5, adx_need, atr_need, 40)


@dataclass
class RiskParams:
    """风险参数集合：控制定仓、暴露、日内止损与交易频率上限。"""

    risk_per_trade_pct: float = 0.5
    usdt_per_trade: float = 100.0
    max_total_exposure_pct: float = 80.0
    daily_loss_limit_pct: float = 3.0
    cooldown_bars: int = 2
    max_positions: int = 3
    max_daily_trades: int = 50

    def repair(self) -> "RiskParams":
        """修复风险参数边界，避免出现不可执行或不合理约束。"""
        self.risk_per_trade_pct = max(0.01, float(self.risk_per_trade_pct))
        self.usdt_per_trade = max(10.0, float(self.usdt_per_trade))
        self.max_total_exposure_pct = max(1.0, float(self.max_total_exposure_pct))
        self.daily_loss_limit_pct = max(0.0, float(self.daily_loss_limit_pct))
        self.cooldown_bars = max(0, int(self.cooldown_bars))
        self.max_positions = max(1, int(self.max_positions))
        self.max_daily_trades = max(1, int(self.max_daily_trades))
        return self


@dataclass
class ExecutionParams:
    """执行成本参数：控制手续费和滑点模型。"""

    fee_bps: float = 10.0
    slippage_bps: float = 10.0

    def repair(self) -> "ExecutionParams":
        """修复执行成本参数，确保不出现负费率/负滑点。"""
        self.fee_bps = max(0.0, float(self.fee_bps))
        self.slippage_bps = max(0.0, float(self.slippage_bps))
        return self


@dataclass
class SpotTradingConfig:
    """现货子系统统一运行配置（策略/风控/执行/连接/模式）。"""

    binance_api_key: str = field(default_factory=lambda: os.environ.get("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.environ.get("BINANCE_API_SECRET", ""))

    binance_spot_base: str = "https://api.binance.com"
    binance_futures_base: str = "https://fapi.binance.com"
    binance_delivery_base: str = "https://dapi.binance.com"
    binance_spot_ws: str = "wss://stream.binance.com:9443/ws"
    binance_futures_ws: str = "wss://fstream.binance.com/ws"
    ws_reconnect_delay: int = 5
    max_reconnect_attempts: int = 10

    symbols: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"
    ])

    # Strategy parameters (backward-compatible flat fields)
    kline_interval: str = "15m"
    decision_timing: str = "on_close"
    fast_ma_period: int = 9
    slow_ma_period: int = 21
    rsi_period: int = 14
    rsi_buy_min: float = 45.0
    rsi_buy_max: float = 65.0
    rsi_sell_min: float = 45.0
    pullback_tol: float = 0.003
    confirm_breakout: float = 0.0005
    ma_breakout_band: float = 0.0005
    band_atr_k: float = 0.2
    min_edge_over_cost: float = 0.0
    cost_buffer_k: float = 1.0
    min_atr_pct: float = 0.0
    max_mark_spot_gap_pct: float = 0.012
    premium_abs_entry_max: float = 0.006
    premium_z_entry_min: float = -2.2
    premium_z_entry_max: float = 2.2
    max_mark_spot_gap_exit: float = 0.02
    enable_overheat_derisk_exit: bool = True
    overheat_exit_min_pnl_pct: float = 0.4
    overheat_exit_funding_min: float = 0.0002
    overheat_exit_premium_abs_min: float = 0.004
    max_mark_spot_diverge: float = 0.012
    premium_abs_max: float = 0.008
    funding_long_max: float = 0.0005
    funding_cost_buffer_k: float = 1.0
    atr_period: int = 14
    atr_k: float = 2.0
    trail_atr_k: float = 2.5
    adx_period: int = 14
    adx_min: float = 18.0
    trend_strength_min: float = 0.003
    min_24h_quote_volume: float = 20_000_000.0

    # Risk and execution controls (backward-compatible flat fields)
    initial_capital: float = 10_000.0
    usdt_per_trade: float = 100.0
    risk_per_trade_pct: float = 0.5
    fee_bps: float = 10.0
    slippage_bps: float = 10.0
    max_total_exposure_pct: float = 80.0
    daily_loss_limit_pct: float = 3.0
    cooldown_bars: int = 2
    max_open_positions: int = 3
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    max_daily_trades: int = 50

    # Runtime controls
    check_interval: int = 30
    dry_run: bool = field(default_factory=lambda: os.environ.get("SPOT_DRY_RUN", "true").lower() == "true")

    @property
    def min_klines_required(self) -> int:
        """向外暴露策略预热所需最小 K 线数量。"""
        return self.to_strategy_params().min_klines_required

    def to_strategy_params(self) -> StrategyParams:
        """把兼容字段映射为 `StrategyParams`，并执行修复。"""
        return StrategyParams(
            bar_interval=self.kline_interval,
            decision_timing=self.decision_timing,
            fast_ma_len=self.fast_ma_period,
            slow_ma_len=self.slow_ma_period,
            rsi_len=self.rsi_period,
            atr_len=self.atr_period,
            adx_len=self.adx_period,
            pullback_tol=self.pullback_tol,
            confirm_breakout=self.confirm_breakout,
            ma_breakout_band=self.ma_breakout_band,
            band_atr_k=self.band_atr_k,
            min_edge_over_cost=self.min_edge_over_cost,
            cost_buffer_k=self.cost_buffer_k,
            min_atr_pct=self.min_atr_pct,
            max_mark_spot_gap_pct=self.max_mark_spot_gap_pct,
            premium_abs_entry_max=self.premium_abs_entry_max,
            premium_z_entry_min=self.premium_z_entry_min,
            premium_z_entry_max=self.premium_z_entry_max,
            max_mark_spot_gap_exit=self.max_mark_spot_gap_exit,
            enable_overheat_derisk_exit=self.enable_overheat_derisk_exit,
            overheat_exit_min_pnl_pct=self.overheat_exit_min_pnl_pct,
            overheat_exit_funding_min=self.overheat_exit_funding_min,
            overheat_exit_premium_abs_min=self.overheat_exit_premium_abs_min,
            max_mark_spot_diverge=self.max_mark_spot_diverge,
            premium_abs_max=self.premium_abs_max,
            funding_long_max=self.funding_long_max,
            funding_cost_buffer_k=self.funding_cost_buffer_k,
            rsi_buy_min=self.rsi_buy_min,
            rsi_buy_max=self.rsi_buy_max,
            adx_min=self.adx_min,
            trend_strength_min=self.trend_strength_min,
            min_24h_quote_volume=self.min_24h_quote_volume,
            atr_k=self.atr_k,
            trail_atr_k=self.trail_atr_k,
            rsi_sell_min=self.rsi_sell_min,
        ).repair()

    def to_risk_params(self) -> RiskParams:
        """把兼容字段映射为 `RiskParams`，并执行修复。"""
        return RiskParams(
            risk_per_trade_pct=self.risk_per_trade_pct,
            usdt_per_trade=self.usdt_per_trade,
            max_total_exposure_pct=self.max_total_exposure_pct,
            daily_loss_limit_pct=self.daily_loss_limit_pct,
            cooldown_bars=self.cooldown_bars,
            max_positions=self.max_open_positions,
            max_daily_trades=self.max_daily_trades,
        ).repair()

    def to_execution_params(self) -> ExecutionParams:
        """把兼容字段映射为 `ExecutionParams`，并执行修复。"""
        return ExecutionParams(
            fee_bps=self.fee_bps,
            slippage_bps=self.slippage_bps,
        ).repair()

    def apply_strategy_params(self, params: StrategyParams):
        """将策略参数回写到兼容字段（便于 CLI 与旧代码共存）。"""
        p = params.repair()
        self.kline_interval = p.bar_interval
        self.decision_timing = p.decision_timing
        self.fast_ma_period = p.fast_ma_len
        self.slow_ma_period = p.slow_ma_len
        self.rsi_period = p.rsi_len
        self.atr_period = p.atr_len
        self.adx_period = p.adx_len
        self.pullback_tol = p.pullback_tol
        self.confirm_breakout = p.confirm_breakout
        self.ma_breakout_band = p.ma_breakout_band
        self.band_atr_k = p.band_atr_k
        self.min_edge_over_cost = p.min_edge_over_cost
        self.cost_buffer_k = p.cost_buffer_k
        self.min_atr_pct = p.min_atr_pct
        self.max_mark_spot_gap_pct = p.max_mark_spot_gap_pct
        self.premium_abs_entry_max = p.premium_abs_entry_max
        self.premium_z_entry_min = p.premium_z_entry_min
        self.premium_z_entry_max = p.premium_z_entry_max
        self.max_mark_spot_gap_exit = p.max_mark_spot_gap_exit
        self.enable_overheat_derisk_exit = p.enable_overheat_derisk_exit
        self.overheat_exit_min_pnl_pct = p.overheat_exit_min_pnl_pct
        self.overheat_exit_funding_min = p.overheat_exit_funding_min
        self.overheat_exit_premium_abs_min = p.overheat_exit_premium_abs_min
        self.max_mark_spot_diverge = p.max_mark_spot_diverge
        self.premium_abs_max = p.premium_abs_max
        self.funding_long_max = p.funding_long_max
        self.funding_cost_buffer_k = p.funding_cost_buffer_k
        self.rsi_buy_min = p.rsi_buy_min
        self.rsi_buy_max = p.rsi_buy_max
        self.adx_min = p.adx_min
        self.trend_strength_min = p.trend_strength_min
        self.min_24h_quote_volume = p.min_24h_quote_volume
        self.atr_k = p.atr_k
        self.trail_atr_k = p.trail_atr_k
        self.rsi_sell_min = p.rsi_sell_min

    def apply_risk_params(self, params: RiskParams):
        """将风险参数回写到兼容字段。"""
        p = params.repair()
        self.risk_per_trade_pct = p.risk_per_trade_pct
        self.usdt_per_trade = p.usdt_per_trade
        self.max_total_exposure_pct = p.max_total_exposure_pct
        self.daily_loss_limit_pct = p.daily_loss_limit_pct
        self.cooldown_bars = p.cooldown_bars
        self.max_open_positions = p.max_positions
        self.max_daily_trades = p.max_daily_trades

    def apply_execution_params(self, params: ExecutionParams):
        """将执行参数回写到兼容字段。"""
        p = params.repair()
        self.fee_bps = p.fee_bps
        self.slippage_bps = p.slippage_bps

    def to_best_params_dict(self) -> Dict[str, Any]:
        """导出可复用参数结构（strategy/risk/execution）。"""
        return {
            "strategy_params": asdict(self.to_strategy_params()),
            "risk_params": asdict(self.to_risk_params()),
            "execution_params": asdict(self.to_execution_params()),
        }

    def apply_best_params_dict(self, data: Dict[str, Any]):
        """从字典结构加载参数并回写到当前配置。"""
        if not isinstance(data, dict):
            return
        strategy_raw = data.get("strategy_params", {})
        risk_raw = data.get("risk_params", {})
        execution_raw = data.get("execution_params", {})

        if isinstance(strategy_raw, dict):
            strategy = StrategyParams(**{
                k: v for k, v in strategy_raw.items() if k in StrategyParams.__dataclass_fields__
            }).repair()
            self.apply_strategy_params(strategy)
        if isinstance(risk_raw, dict):
            risk = RiskParams(**{
                k: v for k, v in risk_raw.items() if k in RiskParams.__dataclass_fields__
            }).repair()
            self.apply_risk_params(risk)
        if isinstance(execution_raw, dict):
            execution = ExecutionParams(**{
                k: v for k, v in execution_raw.items() if k in ExecutionParams.__dataclass_fields__
            }).repair()
            self.apply_execution_params(execution)

    def load_best_params(self, path: str) -> bool:
        """从 JSON 文件加载参数；失败返回 False。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.apply_best_params_dict(payload)
            return True
        except Exception:
            return False

    def save_best_params(self, path: str, extra: Optional[Dict[str, Any]] = None):
        """保存参数到 JSON，可附加额外元信息。"""
        payload = self.to_best_params_dict()
        if extra:
            payload.update(extra)
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def validate(self) -> bool:
        """运行前校验：修复参数并检查关键约束。"""
        self.apply_strategy_params(self.to_strategy_params())
        self.apply_risk_params(self.to_risk_params())
        self.apply_execution_params(self.to_execution_params())

        if self.initial_capital <= 0:
            print("❌ Spot initial capital must be > 0")
            return False
        if self.kline_interval not in SUPPORTED_BAR_INTERVALS:
            print(f"❌ Spot kline interval must be one of {SUPPORTED_BAR_INTERVALS}")
            return False
        if self.decision_timing not in SUPPORTED_DECISION_TIMING:
            print(f"❌ Spot decision_timing must be one of {SUPPORTED_DECISION_TIMING}")
            return False
        if self.rsi_buy_min >= self.rsi_buy_max:
            print("❌ Spot RSI buy range invalid: rsi_buy_min must be < rsi_buy_max")
            return False
        if self.risk_per_trade_pct <= 0:
            print("❌ Spot risk_per_trade_pct must be > 0")
            return False
        if self.max_total_exposure_pct <= 0:
            print("❌ Spot max_total_exposure_pct must be > 0")
            return False
        if self.slow_ma_period < self.fast_ma_period * 2:
            print("❌ Spot constraint invalid: slow_ma_len must be >= 2 * fast_ma_len")
            return False
        if self.trail_atr_k < self.atr_k:
            print("❌ Spot constraint invalid: trail_atr_k must be >= atr_k")
            return False
        if self.cost_buffer_k <= 0:
            print("❌ Spot constraint invalid: cost_buffer_k must be > 0")
            return False
        if self.ma_breakout_band < 0 or self.band_atr_k < 0:
            print("❌ Spot breakout band params must be >= 0")
            return False
        if self.premium_z_entry_min >= self.premium_z_entry_max:
            print("❌ Spot premium_z entry range invalid: premium_z_entry_min must be < premium_z_entry_max")
            return False
        if self.max_mark_spot_gap_exit < self.max_mark_spot_gap_pct:
            print("❌ Spot constraint invalid: max_mark_spot_gap_exit must be >= max_mark_spot_gap_pct")
            return False
        if not self.dry_run and (not self.binance_api_key or not self.binance_api_secret):
            print("❌ Spot live mode requires BINANCE_API_KEY and BINANCE_API_SECRET")
            return False
        return True


DEFAULT_SPOT_CONFIG = SpotTradingConfig()
