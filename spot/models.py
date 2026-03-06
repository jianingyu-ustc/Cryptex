"""
Shared data models for spot auto-trading subsystem.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass
class DecisionContext:
    """统一决策上下文：把行情、仓位、账户和模式信息一次性传给策略层。"""

    symbol: str
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    bar_volume: float
    recent_klines: List[Dict[str, Any]]
    quote_volume_24h: float

    has_position: bool = False
    entry_price: float = 0.0
    stop_price: float = 0.0
    max_price: float = 0.0
    position_qty: float = 0.0
    fees_paid: float = 0.0

    cash_balance: float = 0.0
    equity: float = 0.0
    day_start_equity: float = 0.0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0

    funding_rate: float = 0.0
    funding_rate_series: List[Dict[str, Any]] = field(default_factory=list)
    premium_kline_series: List[Dict[str, Any]] = field(default_factory=list)
    mark_kline_series: List[Dict[str, Any]] = field(default_factory=list)
    mark_price_close: float = 0.0
    premium_close: float = 0.0

    decision_timing: str = "on_close"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def daily_drawdown_pct(self) -> float:
        """按“日初净值”计算当前日内回撤百分比。"""
        if self.day_start_equity <= 0:
            return 0.0
        return (self.equity - self.day_start_equity) / self.day_start_equity * 100


@dataclass
class SpotSignal:
    """策略输出信号：包含动作、置信度、指标快照和可解释 reasons。"""
    symbol: str
    action: str  # BUY / SELL / HOLD
    price: float
    confidence: float
    reason: str
    reasons: List[str] = field(default_factory=list)
    fast_ma: float = 0.0
    slow_ma: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0
    adx: float = 0.0
    trend_strength: float = 0.0
    stop_price: float = 0.0
    momentum_pct: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        """保证 `reason` 与 `reasons` 至少有一个可用于展示/日志。"""
        if not self.reasons:
            self.reasons = [self.reason] if self.reason else []
        if not self.reason and self.reasons:
            self.reason = self.reasons[0]

    def is_actionable(self) -> bool:
        """仅 BUY/SELL 视为可执行动作，HOLD 不下单。"""
        return self.action in {"BUY", "SELL"}


@dataclass
class SpotPosition:
    """持仓对象：记录开仓成本、动态止损与浮动/已实现盈亏。"""
    symbol: str
    quantity: float
    entry_price: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_price: float = 0.0
    max_price: float = 0.0
    peak_price: float = 0.0  # backwards-compatible alias
    last_price: float = 0.0
    fees_paid: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    def __post_init__(self):
        """初始化关键价格字段，确保后续风控计算可用。"""
        if self.max_price <= 0:
            self.max_price = self.entry_price
        if self.peak_price <= 0:
            self.peak_price = self.max_price
        if self.last_price <= 0:
            self.last_price = self.entry_price

    def unrealized_pnl_pct(self) -> float:
        """计算当前持仓浮动收益率（百分比）。"""
        if self.entry_price <= 0:
            return 0.0
        return (self.last_price - self.entry_price) / self.entry_price * 100

    def market_value(self) -> float:
        """按最新价格计算持仓市值。"""
        return self.quantity * self.last_price


@dataclass
class SpotTrade:
    """成交记录：用于对账、统计、回测报告和终端展示。"""
    symbol: str
    side: str  # BUY / SELL
    quantity: float
    price: float
    notional: float
    dry_run: bool
    order_id: str
    reason: str
    reasons: List[str] = field(default_factory=list)
    fee_paid: float = 0.0
    slippage_bps: float = 0.0
    slippage_cost_usdt: float = 0.0
    expected_price: float = 0.0
    realized_pnl_usdt: float = 0.0
    cash_balance_after: float = 0.0
    account_value_after: float = 0.0
    cumulative_pnl_usdt: float = 0.0
    cumulative_return_pct: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
