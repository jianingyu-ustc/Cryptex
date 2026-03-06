#!/usr/bin/env python3
"""
Spot Auto-Trading Subsystem - Main Entry Point.
"""

import asyncio
import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace
from typing import Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from common.binance_client import BinanceClient
from .config import SpotTradingConfig, DEFAULT_SPOT_CONFIG
from .optimizer import FitnessWeights, GASettings, ParameterSpace, SpotGAOptimizer
from .strategy import SpotStrategyEngine
from .execution import SpotExecutionEngine
from .models import SpotSignal, SpotTrade, SpotPosition

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("spot.log"),
    ],
)
logger = logging.getLogger(__name__)
console = Console()


class _EventTimeFilter(logging.Filter):
    """日志时间过滤器：若记录中有 `event_time`，则用它覆盖默认输出时间。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """改写 logging 的 created/msecs，让日志前缀显示事件时间。"""
        event_time = getattr(record, "event_time", None)
        if isinstance(event_time, datetime):
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            event_time = event_time.astimezone(timezone.utc)
            created = event_time.timestamp()
            record.created = created
            record.msecs = (created - int(created)) * 1000
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(_EventTimeFilter())


def _interval_to_seconds(interval: str) -> int:
    """将 Binance K 线周期字符串转换为秒数。"""
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


def _parse_utc_datetime(value: str) -> Optional[datetime]:
    """解析 ISO 时间并标准化为 UTC。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SpotBacktestDataClient:
    """回测数据客户端：基于内存 K 线切片模拟行情接口。"""

    def __init__(
        self,
        symbol_klines: Dict[str, List[Dict]],
        interval_seconds: int,
        symbol_mark_klines: Optional[Dict[str, List[Dict]]] = None,
        symbol_premium_klines: Optional[Dict[str, List[Dict]]] = None,
        symbol_funding_rates: Optional[Dict[str, List[Dict]]] = None,
    ):
        self.symbol_klines = {
            symbol: sorted(klines, key=lambda x: x["open_time"])
            for symbol, klines in symbol_klines.items()
        }
        self.symbol_mark_klines = {
            symbol: sorted(klines, key=lambda x: x["open_time"])
            for symbol, klines in (symbol_mark_klines or {}).items()
        }
        self.symbol_premium_klines = {
            symbol: sorted(klines, key=lambda x: x["open_time"])
            for symbol, klines in (symbol_premium_klines or {}).items()
        }
        self.symbol_funding_rates = {
            symbol: sorted(rows, key=lambda x: x["funding_time"])
            for symbol, rows in (symbol_funding_rates or {}).items()
        }
        self.interval_seconds = max(60, interval_seconds)
        self.current_index = 0
        self._bars_24h = max(1, int(86400 / self.interval_seconds))

    def set_index(self, index: int):
        self.current_index = max(0, index)

    def _slice_rows(self, symbol: str) -> List[Dict]:
        rows = self.symbol_klines.get(symbol, [])
        if not rows:
            return []
        end = min(len(rows), self.current_index + 1)
        return rows[:end]

    def _current_symbol_time(self, symbol: str) -> Optional[datetime]:
        rows = self.symbol_klines.get(symbol, [])
        if not rows:
            return None
        idx = min(len(rows) - 1, self.current_index)
        return rows[idx].get("close_time") or rows[idx].get("open_time")

    def _slice_aux_klines(self, source: Dict[str, List[Dict]], symbol: str) -> List[Dict]:
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
        rows = self._slice_rows(symbol)
        if start_time:
            rows = [r for r in rows if r["open_time"] >= start_time]
        if end_time:
            rows = [r for r in rows if r["open_time"] <= end_time]
        if limit > 0:
            return rows[-limit:]
        return rows

    async def get_spot_ticker(self, symbol: str):
        rows = self._slice_rows(symbol)
        if not rows:
            return None
        recent = rows[-self._bars_24h:]
        quote_volume_24h = sum(float(k["volume"]) * float(k["close"]) for k in recent)
        last_price = float(rows[-1]["close"])
        return SimpleNamespace(
            symbol=symbol,
            price=last_price,
            bid_price=last_price,
            ask_price=last_price,
            volume_24h=quote_volume_24h,
        )

    async def get_spot_price(self, symbol: str) -> Optional[float]:
        rows = self._slice_rows(symbol)
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
        rows = self._slice_aux_klines(self.symbol_mark_klines, symbol)
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
        rows = self._slice_aux_klines(self.symbol_premium_klines, symbol)
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
        rows = self.symbol_funding_rates.get(symbol, [])
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


class SpotDisplay:
    """终端展示组件：封装信号、持仓、成交和统计表格渲染。"""

    @staticmethod
    def print_header():
        header = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║    📈 CRYPTO SPOT AUTO TRADING SYSTEM 📈                                     ║
║    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                     ║
║    Trend Following | Risk Managed | Dry-Run by Default                       ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""
        console.print(header, style="bold green")

    @staticmethod
    def signals_table(signals: List[SpotSignal]) -> Table:
        table = Table(title="Spot Strategy Signals", box=box.ROUNDED, header_style="bold cyan")
        table.add_column("Symbol", width=10)
        table.add_column("Action", width=8)
        table.add_column("Price", width=12, justify="right")
        table.add_column("Confidence", width=10, justify="right")
        table.add_column("RSI", width=8, justify="right")
        table.add_column("ATR/ADX", width=14, justify="right")
        table.add_column("MA(FAST/SLOW)", width=20, justify="right")
        table.add_column("Reasons", width=46, overflow="fold")

        for s in signals:
            if s.action == "BUY":
                action = "[green]BUY[/]"
            elif s.action == "SELL":
                action = "[red]SELL[/]"
            else:
                action = "[yellow]HOLD[/]"
            reasons = s.reasons if s.reasons else ([s.reason] if s.reason else [])
            reasons_text = " | ".join(reasons[:3])
            if len(reasons) > 3:
                reasons_text += " | ..."
            table.add_row(
                s.symbol,
                action,
                f"{s.price:,.4f}" if s.price > 0 else "-",
                f"{s.confidence:.0%}",
                f"{s.rsi:.1f}",
                f"{s.atr:.4f}/{s.adx:.1f}",
                f"{s.fast_ma:.3f}/{s.slow_ma:.3f}",
                reasons_text or "-",
            )
        return table

    @staticmethod
    def positions_table(positions: List[SpotPosition]) -> Table:
        table = Table(title="Open Spot Positions", box=box.ROUNDED, header_style="bold yellow")
        table.add_column("Symbol", width=10)
        table.add_column("Qty", width=12, justify="right")
        table.add_column("Entry", width=12, justify="right")
        table.add_column("Last", width=12, justify="right")
        table.add_column("Stop", width=12, justify="right")
        table.add_column("Max", width=12, justify="right")
        table.add_column("PnL %", width=10, justify="right")
        table.add_column("Value", width=12, justify="right")

        for p in positions:
            pnl = p.unrealized_pnl_pct()
            pnl_str = f"[green]{pnl:+.2f}%[/]" if pnl >= 0 else f"[red]{pnl:+.2f}%[/]"
            table.add_row(
                p.symbol,
                f"{p.quantity:.6f}",
                f"{p.entry_price:,.4f}",
                f"{p.last_price:,.4f}",
                f"{p.stop_price:,.4f}",
                f"{p.max_price:,.4f}",
                pnl_str,
                f"${p.market_value():,.2f}",
            )
        return table

    @staticmethod
    def trade_table(trades: List[SpotTrade], title: str = "Recent Trades") -> Table:
        table = Table(title=title, box=box.MINIMAL_DOUBLE_HEAD, header_style="bold magenta")
        table.add_column("Time", width=16)
        table.add_column("Symbol", width=10)
        table.add_column("Side", width=8)
        table.add_column("Qty", width=12, justify="right")
        table.add_column("Price", width=12, justify="right")
        table.add_column("Fee", width=10, justify="right")
        table.add_column("PnL", width=10, justify="right")
        table.add_column("CumPnL", width=12, justify="right")
        table.add_column("Return", width=9, justify="right")
        table.add_column("Mode", width=7)

        for t in trades:
            pnl = t.realized_pnl_usdt
            pnl_str = f"${pnl:+.2f}"
            if t.side == "SELL":
                pnl_str = f"[green]{pnl_str}[/]" if pnl >= 0 else f"[red]{pnl_str}[/]"
            cum_pnl = f"${t.cumulative_pnl_usdt:+.2f}"
            ret = f"{t.cumulative_return_pct:+.2f}%"
            cum_pnl = f"[green]{cum_pnl}[/]" if t.cumulative_pnl_usdt >= 0 else f"[red]{cum_pnl}[/]"
            ret = f"[green]{ret}[/]" if t.cumulative_return_pct >= 0 else f"[red]{ret}[/]"
            table.add_row(
                t.timestamp.strftime("%m-%d %H:%M:%S"),
                t.symbol,
                t.side,
                f"{t.quantity:.6f}",
                f"{t.price:,.4f}",
                f"${t.fee_paid:.2f}",
                pnl_str if t.side == "SELL" else "-",
                cum_pnl,
                ret,
                "SIM" if t.dry_run else "LIVE",
            )
        return table


class SpotTradingSystem:
    """现货子系统主控制器：串联行情、策略、执行与模式流程。"""

    def __init__(self, config: SpotTradingConfig = None):
        self.config = config or DEFAULT_SPOT_CONFIG
        self.client: Optional[BinanceClient] = None
        self.strategy: Optional[SpotStrategyEngine] = None
        self.execution: Optional[SpotExecutionEngine] = None
        self._running = False

    async def initialize(self) -> bool:
        """初始化 API 客户端、策略引擎和执行引擎。"""
        console.print("🔄 Initializing spot trading system...", style="dim")
        self.client = BinanceClient(self.config)
        if not await self.client.test_connectivity():
            console.print("❌ Failed to connect Binance Spot API", style="bold red")
            return False

        self.strategy = SpotStrategyEngine(self.client, self.config)
        self.execution = SpotExecutionEngine(self.client, self.config)
        mode = "DRY-RUN" if self.config.dry_run else "LIVE"
        console.print(
            f"✅ Spot system ready [{mode}] | Initial Capital: ${self.config.initial_capital:,.2f}",
            style="green",
        )
        return True

    async def shutdown(self):
        """关闭系统并释放客户端资源。"""
        self._running = False
        if self.client:
            await self.client.close()
        console.print("✅ Spot system stopped", style="green")

    async def _scan(self) -> List[SpotSignal]:
        """执行一轮扫描：先更新持仓估值，再并行生成信号。"""
        if not self.execution or not self.strategy:
            return []
        await self.execution.mark_positions()
        signals = await self.strategy.analyze_symbols(
            self.config.symbols,
            self.execution.positions,
            portfolio_state=self.execution.get_portfolio_state(),
        )
        return signals

    async def run_once(self, auto_execute: bool = False):
        """单次运行：扫描、展示、可选执行，并输出账户统计。"""
        if not self.execution:
            console.print("❌ Spot execution engine not initialized", style="red")
            return
        signals = await self._scan()
        console.print(SpotDisplay.signals_table(signals))

        actionable = [s for s in signals if s.is_actionable()]
        if auto_execute and actionable:
            for signal in actionable:
                trade = await self.execution.execute_signal(signal)
                if trade:
                    side_color = "green" if trade.side == "BUY" else "red"
                    reasons_text = " | ".join((trade.reasons or [trade.reason])[:3])
                    console.print(
                        f"[{side_color}]Executed {trade.side} {trade.symbol} "
                        f"qty={trade.quantity:.6f} @ {trade.price:.4f} "
                        f"({('SIM' if trade.dry_run else 'LIVE')}) | "
                        f"Fee=${trade.fee_paid:.2f} | "
                        f"Equity=${trade.account_value_after:,.2f} | "
                        f"Return={trade.cumulative_return_pct:+.2f}% "
                        f"(${trade.cumulative_pnl_usdt:+.2f}) | "
                        f"Reason: {reasons_text}[/]"
                    )

        positions = list(self.execution.positions.values())
        if positions:
            console.print(SpotDisplay.positions_table(positions))
        else:
            console.print("[yellow]No open spot positions[/yellow]")

        if self.execution.trades:
            console.print(SpotDisplay.trade_table(self.execution.trades[-10:], "Latest 10 Trades"))

        stats = self.execution.get_stats()
        summary = "\n".join([
            (
                f"Initial: ${stats['initial_capital_usdt']:,.2f} | Cash: ${stats['cash_balance_usdt']:,.2f} | "
                f"Position Value: ${stats['market_value_usdt']:,.2f} | Equity: ${stats['account_value_usdt']:,.2f}"
            ),
            (
                f"Total Return: {stats['total_return_pct']:+.2f}% (${stats['total_pnl_usdt']:+.2f}) | "
                f"Realized: ${stats['realized_pnl_usdt']:+.2f} | Unrealized: ${stats['unrealized_pnl_usdt']:+.2f}"
            ),
            (
                f"Trades: {stats['total_trades']} | Closed: {stats['closed_trades']} | "
                f"Win Rate: {stats['win_rate']:.1f}% | Open Positions: {stats['open_positions']}"
            ),
            (
                f"Fees: ${stats['fees_paid_usdt']:.2f} | Slippage: ${stats['slippage_cost_usdt']:.2f} | "
                f"Exposure: {stats['exposure_pct']:.1f}% | Daily Return: {stats['daily_return_pct']:+.2f}% "
                f"| DailyLimitHit: {stats['daily_loss_limited']}"
            ),
        ])
        console.print(Panel(summary, title="Spot Stats", border_style="cyan"))

    async def _fetch_symbol_history(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Dict]:
        """分页拉取单个交易对历史 K 线。"""
        if not self.client:
            return []
        interval_seconds = _interval_to_seconds(self.config.kline_interval)
        cursor = start_time
        all_klines: List[Dict] = []

        while cursor < end_time:
            batch = await self.client.get_spot_klines(
                symbol=symbol,
                interval=self.config.kline_interval,
                limit=1000,
                start_time=cursor,
                end_time=end_time,
            )
            if not batch:
                break

            for row in batch:
                if not all_klines or row["open_time"] > all_klines[-1]["open_time"]:
                    all_klines.append(row)

            next_cursor = batch[-1]["open_time"] + timedelta(seconds=interval_seconds)
            if next_cursor <= cursor:
                break
            cursor = next_cursor

            # Keep request rate stable during long-history download.
            await asyncio.sleep(0.02)

            if len(batch) < 1000:
                break

        return all_klines

    async def _fetch_symbol_aux_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        method_name: str,
    ) -> List[Dict]:
        """分页拉取单个交易对的衍生 K 线（mark/premium）。"""
        if not self.client or not hasattr(self.client, method_name):
            return []
        interval_seconds = _interval_to_seconds(self.config.kline_interval)
        cursor = start_time
        all_klines: List[Dict] = []
        getter = getattr(self.client, method_name)

        while cursor < end_time:
            batch = await getter(
                symbol=symbol,
                interval=self.config.kline_interval,
                limit=1000,
                start_time=cursor,
                end_time=end_time,
            )
            if not batch:
                break
            for row in batch:
                if not all_klines or row["open_time"] > all_klines[-1]["open_time"]:
                    all_klines.append(row)

            next_cursor = batch[-1]["open_time"] + timedelta(seconds=interval_seconds)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            await asyncio.sleep(0.02)
            if len(batch) < 1000:
                break

        return all_klines

    async def _fetch_symbol_funding_history(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Dict]:
        """分页拉取单个交易对 funding 序列。"""
        if not self.client or not hasattr(self.client, "get_funding_rate_history"):
            return []
        cursor = start_time
        all_rows: List[Dict] = []

        while cursor < end_time:
            batch = await self.client.get_funding_rate_history(
                symbol=symbol,
                limit=1000,
                start_time=cursor,
                end_time=end_time,
            )
            if not batch:
                break
            for row in batch:
                if not all_rows or row["funding_time"] > all_rows[-1]["funding_time"]:
                    all_rows.append(row)

            next_cursor = batch[-1]["funding_time"] + timedelta(seconds=1)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            await asyncio.sleep(0.02)
            if len(batch) < 1000:
                break

        return all_rows

    async def run_backtest(
        self,
        years: int = 3,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        sleep_seconds: float = 0.0,
    ) -> Optional[Dict]:
        """运行历史回测：复用同一策略与执行逻辑并输出结果统计。"""
        if not self.client:
            console.print("❌ Spot client not initialized", style="red")
            return None

        years = max(3, int(years))
        end_time = end_time or datetime.now(timezone.utc)
        start_time = start_time or (end_time - timedelta(days=365 * years))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        start_time = start_time.astimezone(timezone.utc)
        end_time = end_time.astimezone(timezone.utc)

        min_window = timedelta(days=365 * 3)
        if end_time - start_time < min_window:
            start_time = end_time - min_window
            console.print(
                "[yellow]Backtest window adjusted to minimum 3 years.[/yellow]",
            )

        if start_time >= end_time:
            console.print("❌ Backtest time window invalid", style="red")
            return None

        console.print(
            f"⏪ Running spot backtest | Window: {start_time.date()} -> {end_time.date()} | "
            f"Interval: {self.config.kline_interval}",
            style="bold cyan",
        )

        symbols = list(self.config.symbols)
        tasks = [self._fetch_symbol_history(symbol, start_time, end_time) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        history_by_symbol: Dict[str, List[Dict]] = {}
        skipped: List[str] = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.error("Backtest history fetch failed for %s: %s", symbol, result)
                skipped.append(symbol)
                continue
            rows = result or []
            if len(rows) < self.config.min_klines_required + 10:
                skipped.append(symbol)
                continue
            history_by_symbol[symbol] = rows

        active_symbols = list(history_by_symbol.keys())
        if not active_symbols:
            console.print("❌ No symbols have enough history for backtest.", style="red")
            return None

        mark_tasks = [
            self._fetch_symbol_aux_klines(symbol, start_time, end_time, "get_mark_price_klines")
            for symbol in active_symbols
        ]
        premium_tasks = [
            self._fetch_symbol_aux_klines(symbol, start_time, end_time, "get_premium_index_klines")
            for symbol in active_symbols
        ]
        funding_tasks = [
            self._fetch_symbol_funding_history(symbol, start_time, end_time)
            for symbol in active_symbols
        ]
        mark_results, premium_results, funding_results = await asyncio.gather(
            asyncio.gather(*mark_tasks, return_exceptions=True),
            asyncio.gather(*premium_tasks, return_exceptions=True),
            asyncio.gather(*funding_tasks, return_exceptions=True),
        )

        mark_history_by_symbol: Dict[str, List[Dict]] = {}
        premium_history_by_symbol: Dict[str, List[Dict]] = {}
        funding_history_by_symbol: Dict[str, List[Dict]] = {}
        for symbol, rows in zip(active_symbols, mark_results):
            if isinstance(rows, list):
                mark_history_by_symbol[symbol] = rows
        for symbol, rows in zip(active_symbols, premium_results):
            if isinstance(rows, list):
                premium_history_by_symbol[symbol] = rows
        for symbol, rows in zip(active_symbols, funding_results):
            if isinstance(rows, list):
                funding_history_by_symbol[symbol] = rows

        common_len = min(len(history_by_symbol[s]) for s in active_symbols)
        if common_len < self.config.min_klines_required + 2:
            console.print("❌ Backtest bars are insufficient after alignment.", style="red")
            return None

        # Align bars by using the same trailing window length across symbols.
        for symbol in active_symbols:
            history_by_symbol[symbol] = history_by_symbol[symbol][-common_len:]

        interval_seconds = _interval_to_seconds(self.config.kline_interval)
        bt_client = SpotBacktestDataClient(
            history_by_symbol,
            interval_seconds,
            symbol_mark_klines=mark_history_by_symbol,
            symbol_premium_klines=premium_history_by_symbol,
            symbol_funding_rates=funding_history_by_symbol,
        )
        bt_config = replace(self.config, dry_run=True)
        strategy = SpotStrategyEngine(bt_client, bt_config)
        execution = SpotExecutionEngine(bt_client, bt_config)

        start_idx = bt_config.min_klines_required - 1
        total_steps = common_len - start_idx
        if total_steps <= 0:
            console.print("❌ Backtest bars are insufficient for indicator warm-up.", style="red")
            return None

        progress_step = max(1, total_steps // 20)
        for idx in range(start_idx, common_len):
            bar_time = max(history_by_symbol[s][idx]["close_time"] for s in active_symbols)
            execution.set_simulation_time(bar_time)
            bt_client.set_index(idx)
            await execution.mark_positions()
            signals = await strategy.analyze_symbols(
                active_symbols,
                execution.positions,
                portfolio_state=execution.get_portfolio_state(),
            )
            actionable = [s for s in signals if s.is_actionable()]
            for signal in actionable:
                await execution.execute_signal(signal)

            done = idx - start_idx + 1
            if done % progress_step == 0 or idx == common_len - 1:
                eq = execution.get_stats()["account_value_usdt"]
                console.print(
                    f"[dim]Backtest progress: {done}/{total_steps} bars | Equity=${eq:,.2f}[/dim]"
                )
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

        # Force close at end of backtest to lock realized stats.
        bt_client.set_index(common_len - 1)
        execution.set_simulation_time(max(history_by_symbol[s][-1]["close_time"] for s in active_symbols))
        for symbol, pos in list(execution.positions.items()):
            last_price = await bt_client.get_spot_price(symbol)
            if not last_price:
                continue
            exit_signal = SpotSignal(
                symbol=symbol,
                action="SELL",
                price=last_price,
                confidence=1.0,
                reason="end_of_backtest",
                reasons=["end_of_backtest"],
            )
            await execution.execute_signal(exit_signal)

        execution.set_simulation_time(None)
        self.strategy = strategy
        self.execution = execution

        period_start = max(
            history_by_symbol[s][start_idx]["open_time"] for s in active_symbols
        )
        period_end = min(
            history_by_symbol[s][-1]["close_time"] for s in active_symbols
        )
        meta_lines = [
            f"Symbols: {', '.join(active_symbols)}",
            f"Bars Used: {total_steps} ({self.config.kline_interval})",
            f"Aligned Window: {period_start.date()} -> {period_end.date()}",
        ]
        if skipped:
            meta_lines.append(f"Skipped (insufficient history): {', '.join(skipped)}")
        console.print(Panel("\n".join(meta_lines), title="Spot Backtest Meta", border_style="blue"))

        if execution.trades:
            console.print(SpotDisplay.trade_table(execution.trades[-20:], "Backtest Last 20 Trades"))
        else:
            console.print("[yellow]No trades generated in this backtest window.[/yellow]")

        stats = execution.get_stats()
        summary = "\n".join([
            (
                f"Initial: ${stats['initial_capital_usdt']:,.2f} | Cash: ${stats['cash_balance_usdt']:,.2f} | "
                f"Position Value: ${stats['market_value_usdt']:,.2f} | Equity: ${stats['account_value_usdt']:,.2f}"
            ),
            (
                f"Total Return: {stats['total_return_pct']:+.2f}% (${stats['total_pnl_usdt']:+.2f}) | "
                f"Realized: ${stats['realized_pnl_usdt']:+.2f} | Unrealized: ${stats['unrealized_pnl_usdt']:+.2f}"
            ),
            (
                f"Trades: {stats['total_trades']} | Closed: {stats['closed_trades']} | "
                f"Win Rate: {stats['win_rate']:.1f}% | Open Positions: {stats['open_positions']}"
            ),
            (
                f"Fees: ${stats['fees_paid_usdt']:.2f} | Slippage: ${stats['slippage_cost_usdt']:.2f} | "
                f"Exposure: {stats['exposure_pct']:.1f}% | Daily Return: {stats['daily_return_pct']:+.2f}% "
                f"| DailyLimitHit: {stats['daily_loss_limited']}"
            ),
        ])
        console.print(Panel(summary, title="Spot Backtest Stats", border_style="cyan"))
        return stats

    async def run_optimize_ga(
        self,
        backtest_start: datetime,
        backtest_end: datetime,
        ga_settings: GASettings,
        fitness_weights: FitnessWeights,
        output_dir: str,
        walkforward_train_days: int = 730,
        walkforward_test_days: int = 90,
        walkforward_step_days: Optional[int] = None,
        search_timeframe: bool = False,
        search_risk: bool = False,
        search_cost: bool = False,
        max_search_dims: int = 14,
        final_validation_days: int = 120,
        export_best_params_path: Optional[str] = None,
    ) -> Optional[Dict]:
        """运行 GA 参数优化并导出最优参数及元信息。"""
        if not self.client:
            console.print("❌ Spot client not initialized", style="red")
            return None
        if backtest_start >= backtest_end:
            console.print("❌ GA optimization time window invalid", style="red")
            return None

        parameter_space = ParameterSpace(
            base_config=self.config,
            search_timeframe=search_timeframe,
            search_risk=search_risk,
            search_cost=search_cost,
            max_search_dims=max_search_dims,
        )
        optimizer = SpotGAOptimizer(
            client=self.client,
            base_config=self.config,
            output_dir=output_dir,
            parameter_space=parameter_space,
            settings=ga_settings,
            weights=fitness_weights,
        )

        run_meta = "\n".join([
            f"Symbols: {', '.join(self.config.symbols)}",
            f"Window: {backtest_start.date()} -> {backtest_end.date()}",
            f"Population: {ga_settings.population_size} | Generations: {ga_settings.generations}",
            f"Mutation: {ga_settings.mutation_rate:.2f} | Crossover: {ga_settings.crossover_rate:.2f} | Elitism: {ga_settings.elitism_k}",
            f"Walk-forward: train={walkforward_train_days}d test={walkforward_test_days}d step={walkforward_step_days or walkforward_test_days}d",
            f"Final Validation (sealed): {max(30, int(final_validation_days))}d",
            f"Search Dims ({len(parameter_space.dimensions)}): {', '.join(parameter_space.dimensions.keys())}",
        ])
        console.print(Panel(run_meta, title="Spot GA Optimization", border_style="magenta"))

        result = await optimizer.run(
            symbols=self.config.symbols,
            backtest_start=backtest_start,
            backtest_end=backtest_end,
            walkforward_train_days=walkforward_train_days,
            walkforward_test_days=walkforward_test_days,
            walkforward_step_days=walkforward_step_days,
            final_validation_days=max(30, int(final_validation_days)),
        )

        metrics = result.get("best_metrics", {})
        summary = "\n".join([
            f"Best Fitness: {result.get('best_fitness', 0.0):.4f}",
            f"Avg Annual Return: {metrics.get('avg_annual_return_pct', 0.0):+.2f}%",
            f"Avg Sharpe: {metrics.get('avg_sharpe', 0.0):.3f}",
            f"Avg Max Drawdown: {metrics.get('avg_max_drawdown_pct', 0.0):.2f}%",
            f"Worst OOS Window: {metrics.get('worst_window_return_pct', 0.0):+.2f}%",
            f"DSR Proxy: {metrics.get('dsr_proxy', 0.0):.3f}",
            f"Final Validation: {result.get('final_validation_status', 'UNKNOWN')}",
        ])
        files_text = "\n".join([
            f"best_params.json: {result.get('best_params_path')}",
            f"run_meta.json: {result.get('run_meta_path')}",
            f"generation_topk.csv: {result.get('generation_csv_path')}",
            f"cost_sensitivity_curve.csv: {result.get('cost_sensitivity_curve_path')}",
            f"worst_window_report.json: {result.get('worst_window_report_path')}",
            f"final_validation_report.json: {result.get('final_validation_report_path')}",
        ])
        console.print(Panel(summary, title="GA Best Candidate", border_style="green"))
        console.print(Panel(files_text, title="GA Output Files", border_style="blue"))

        if export_best_params_path:
            export_target = Path(export_best_params_path)
            export_target.parent.mkdir(parents=True, exist_ok=True)
            copyfile(result.get("best_params_path"), str(export_target))
            console.print(f"✅ Exported best params to: {export_target}", style="green")

        return result

    async def monitor(self, auto_execute: bool = False):
        """持续监控模式：按固定间隔循环扫描并可选执行。"""
        self._running = True
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
        while self._running:
            try:
                console.clear()
                SpotDisplay.print_header()
                await self.run_once(auto_execute=auto_execute)
                console.print(f"\n[dim]Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                console.print(f"[dim]Next refresh in {self.config.check_interval}s...[/dim]")
                await asyncio.sleep(self.config.check_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Spot monitor error: %s", e)
                await asyncio.sleep(5)


async def main():
    defaults = SpotTradingConfig()
    parser = argparse.ArgumentParser(description="现货自动交易系统")
    parser.add_argument("--symbols", type=str, default="", help="交易对列表，逗号分隔（如 BTCUSDT,ETHUSDT）")
    parser.add_argument("--scan", action="store_true", help="单次扫描模式")
    parser.add_argument("--monitor", "-m", action="store_true", help="持续监控模式")
    parser.add_argument("--backtest", "-b", action="store_true", help="历史回测模式")
    parser.add_argument("--optimize-ga", action="store_true", help="遗传算法参数优化模式")
    parser.add_argument("--backtest-years", type=int, default=3, help="回测年数（最少 3 年）")
    parser.add_argument("--backtest-start", type=str, default="", help="回测开始 UTC 时间（ISO 格式）")
    parser.add_argument("--backtest-end", type=str, default="", help="回测结束 UTC 时间（ISO 格式）")
    parser.add_argument("--backtest-sleep", type=float, default=0.0, help="回测每根 bar 暂停秒数（0 表示尽快运行）")
    parser.add_argument("--auto-execute", action="store_true", help="自动执行 BUY/SELL 信号")
    parser.add_argument("--live", action="store_true", help="开启实盘交易（默认 dry-run）")
    parser.add_argument("--interval", type=int, default=defaults.check_interval, help="监控刷新间隔（秒）")
    parser.add_argument("--initial-capital", type=float, default=defaults.initial_capital, help="初始资金（USDT）")
    parser.add_argument("--usdt-per-trade", type=float, default=defaults.usdt_per_trade, help="单笔交易名义金额上限（USDT）")
    parser.add_argument("--max-positions", type=int, default=defaults.max_open_positions, help="最大同时持仓数量")
    parser.add_argument("--kline-interval", type=str, default=defaults.kline_interval, help="信号计算使用的 K 线周期")
    parser.add_argument("--decision-timing", type=str, choices=["on_close", "intrabar"], default=defaults.decision_timing, help="决策时点（收盘/盘中）")
    parser.add_argument("--fast-ma-len", type=int, default=defaults.fast_ma_period, help="快均线窗口长度")
    parser.add_argument("--slow-ma-len", type=int, default=defaults.slow_ma_period, help="慢均线窗口长度")
    parser.add_argument("--rsi-len", type=int, default=defaults.rsi_period, help="RSI 窗口长度")
    parser.add_argument("--atr-len", type=int, default=defaults.atr_period, help="ATR 窗口长度")
    parser.add_argument("--adx-len", type=int, default=defaults.adx_period, help="ADX 窗口长度")
    parser.add_argument("--pullback-tol", type=float, default=defaults.pullback_tol, help="回踩快均线容忍阈值")
    parser.add_argument("--confirm-breakout", type=float, default=defaults.confirm_breakout, help="兼容参数：百分比突破带宽（等价 ma_breakout_band）")
    parser.add_argument("--ma-breakout-band", type=float, default=defaults.ma_breakout_band, help="入场百分比带宽：close >= fast_ma*(1+ma_breakout_band)")
    parser.add_argument("--band-atr-k", type=float, default=defaults.band_atr_k, help="入场 ATR 带宽：close >= fast_ma + band_atr_k*ATR")
    parser.add_argument("--min-edge-over-cost", type=float, default=defaults.min_edge_over_cost, help="成本门槛额外边际（小数，如 0.001=0.1%）")
    parser.add_argument("--cost-buffer-k", type=float, default=defaults.cost_buffer_k, help="双边成本缓冲倍数")
    parser.add_argument("--min-atr-pct", type=float, default=defaults.min_atr_pct, help="入场最小 ATR 波动率门槛（ATR/close）")
    parser.add_argument("--max-mark-spot-gap-pct", type=float, default=defaults.max_mark_spot_gap_pct, help="买入时允许的最大 mark/spot 偏离")
    parser.add_argument("--premium-abs-entry-max", type=float, default=defaults.premium_abs_entry_max, help="买入 premium 绝对值门槛（方案a）")
    parser.add_argument("--premium-z-entry-min", type=float, default=defaults.premium_z_entry_min, help="买入 premium zscore 下限（方案b）")
    parser.add_argument("--premium-z-entry-max", type=float, default=defaults.premium_z_entry_max, help="买入 premium zscore 上限（方案b）")
    parser.add_argument("--max-mark-spot-gap-exit", type=float, default=defaults.max_mark_spot_gap_exit, help="紧急离场 mark/spot 偏离阈值")
    parser.add_argument("--disable-overheat-derisk-exit", action="store_true", help="关闭盈利状态下 funding+premium 过热减仓离场")
    parser.add_argument("--overheat-exit-min-pnl-pct", type=float, default=defaults.overheat_exit_min_pnl_pct, help="触发过热减仓离场的最小盈利阈值")
    parser.add_argument("--overheat-exit-funding-min", type=float, default=defaults.overheat_exit_funding_min, help="触发过热减仓离场的 funding 下限")
    parser.add_argument("--overheat-exit-premium-abs-min", type=float, default=defaults.overheat_exit_premium_abs_min, help="触发过热减仓离场的 premium 绝对值下限")
    parser.add_argument("--max-mark-spot-diverge", type=float, default=defaults.max_mark_spot_diverge, help="GA/策略约束：mark 与 spot 最大偏离")
    parser.add_argument("--premium-abs-max", type=float, default=defaults.premium_abs_max, help="GA/策略约束：premium 绝对值上限")
    parser.add_argument("--funding-long-max", type=float, default=defaults.funding_long_max, help="GA/策略约束：多头 funding 上限")
    parser.add_argument("--funding-cost-buffer-k", type=float, default=defaults.funding_cost_buffer_k, help="funding 成本缓冲系数")
    parser.add_argument("--rsi-sell-min", type=float, default=defaults.rsi_sell_min, help="趋势转弱卖出 RSI 阈值")
    parser.add_argument("--min-24h-quote-volume", type=float, default=defaults.min_24h_quote_volume, help="允许开仓的最小 24h 成交额")
    parser.add_argument("--stop-loss", type=float, default=defaults.stop_loss_pct, help="兼容参数：兜底止损百分比")
    parser.add_argument("--take-profit", type=float, default=defaults.take_profit_pct, help="兼容参数：旧版止盈百分比")
    parser.add_argument("--rsi-buy-min", type=float, default=defaults.rsi_buy_min, help="BUY 的 RSI 下限")
    parser.add_argument("--rsi-buy-max", type=float, default=defaults.rsi_buy_max, help="BUY 的 RSI 上限")
    parser.add_argument("--atr-k", type=float, default=defaults.atr_k, help="初始 ATR 止损倍数")
    parser.add_argument("--trail-atr-k", type=float, default=defaults.trail_atr_k, help="ATR 追踪止盈倍数")
    parser.add_argument("--adx-min", type=float, default=defaults.adx_min, help="允许开仓的最小 ADX")
    parser.add_argument("--trend-strength-min", type=float, default=defaults.trend_strength_min, help="趋势强度代理阈值")
    parser.add_argument("--risk-per-trade-pct", type=float, default=defaults.risk_per_trade_pct, help="单笔风险占净值比例")
    parser.add_argument("--max-daily-trades", type=int, default=defaults.max_daily_trades, help="单日最大成交笔数")
    parser.add_argument("--fee-bps", type=float, default=defaults.fee_bps, help="单边手续费（bps）")
    parser.add_argument("--slippage-bps", type=float, default=defaults.slippage_bps, help="模拟滑点（bps）")
    parser.add_argument("--max-total-exposure-pct", type=float, default=defaults.max_total_exposure_pct, help="组合总暴露上限（占净值百分比）")
    parser.add_argument("--daily-loss-limit-pct", type=float, default=defaults.daily_loss_limit_pct, help="当日回撤超阈值后停止开新仓")
    parser.add_argument("--cooldown-bars", type=int, default=defaults.cooldown_bars, help="卖出后冷却 bar 数")
    parser.add_argument("--best-params-file", type=str, default="", help="加载参数文件（best_params.json）")
    parser.add_argument("--export-best-params", type=str, default="", help="导出当前参数或 GA 最优参数到指定路径")

    parser.add_argument("--ga-output-dir", type=str, default="spot/ga_runs", help="GA 输出目录（保存 CSV/JSON）")
    parser.add_argument("--ga-pop-size", type=int, default=20, help="GA 每代种群数量")
    parser.add_argument("--ga-generations", type=int, default=10, help="GA 进化代数")
    parser.add_argument("--ga-mutation-rate", type=float, default=0.15, help="GA 变异概率")
    parser.add_argument("--ga-crossover-rate", type=float, default=0.75, help="GA 交叉概率")
    parser.add_argument("--ga-elitism-k", type=int, default=2, help="每代保留的精英个体数")
    parser.add_argument("--ga-top-k-log", type=int, default=5, help="每代写入日志的 Top-K 数量")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（用于结果复现）")
    parser.add_argument("--fitness-weights", type=str, default="", help="fitness 权重，示例 ann_return=1,sharpe=0.8")
    parser.add_argument("--walkforward-train", type=int, default=730, help="walk-forward 训练窗口（天）")
    parser.add_argument("--walkforward-test", type=int, default=90, help="walk-forward 测试窗口（天）")
    parser.add_argument("--walkforward-step", type=int, default=0, help="walk-forward 滚动步长（天，0 表示等于 test）")
    parser.add_argument("--ga-final-test-days", type=int, default=120, help="GA 封存终检窗口（天，终检期间不参与调参）")
    parser.add_argument("--ga-search-timeframe", action="store_true", help="允许 GA 搜索 K 线周期")
    parser.add_argument("--ga-search-risk", action="store_true", help="允许 GA 搜索风险参数")
    parser.add_argument("--ga-search-cost", action="store_true", help="允许 GA 搜索手续费/滑点参数")
    parser.add_argument("--ga-max-search-dims", type=int, default=14, help="GA 搜索维度上限（用于抑制过拟合）")

    args = parser.parse_args()

    config = SpotTradingConfig()
    config.dry_run = not args.live
    config.check_interval = max(5, args.interval)
    config.initial_capital = max(100.0, args.initial_capital)
    config.usdt_per_trade = max(10.0, args.usdt_per_trade)
    config.max_open_positions = max(1, args.max_positions)
    config.kline_interval = args.kline_interval
    config.decision_timing = args.decision_timing
    config.fast_ma_period = max(2, args.fast_ma_len)
    config.slow_ma_period = max(2, args.slow_ma_len)
    config.rsi_period = max(2, args.rsi_len)
    config.atr_period = max(2, args.atr_len)
    config.adx_period = max(2, args.adx_len)
    config.pullback_tol = max(0.0001, args.pullback_tol)
    config.ma_breakout_band = max(0.0, args.ma_breakout_band)
    config.confirm_breakout = max(config.ma_breakout_band, max(0.0, args.confirm_breakout))
    config.band_atr_k = max(0.0, args.band_atr_k)
    config.min_edge_over_cost = max(0.0, args.min_edge_over_cost)
    config.cost_buffer_k = max(0.1, args.cost_buffer_k)
    config.min_atr_pct = max(0.0, args.min_atr_pct)
    config.max_mark_spot_gap_pct = max(0.0, args.max_mark_spot_gap_pct)
    config.premium_abs_entry_max = max(0.0, args.premium_abs_entry_max)
    config.premium_z_entry_min = float(args.premium_z_entry_min)
    config.premium_z_entry_max = float(args.premium_z_entry_max)
    config.max_mark_spot_gap_exit = max(0.0, args.max_mark_spot_gap_exit)
    config.enable_overheat_derisk_exit = not args.disable_overheat_derisk_exit
    config.overheat_exit_min_pnl_pct = max(0.0, args.overheat_exit_min_pnl_pct)
    config.overheat_exit_funding_min = float(args.overheat_exit_funding_min)
    config.overheat_exit_premium_abs_min = max(0.0, args.overheat_exit_premium_abs_min)
    config.max_mark_spot_diverge = max(0.0, args.max_mark_spot_diverge)
    config.premium_abs_max = max(0.0, args.premium_abs_max)
    config.funding_long_max = float(args.funding_long_max)
    config.funding_cost_buffer_k = max(0.0, args.funding_cost_buffer_k)
    config.rsi_sell_min = max(0.0, min(100.0, args.rsi_sell_min))
    config.min_24h_quote_volume = max(0.0, args.min_24h_quote_volume)
    config.stop_loss_pct = max(0.2, args.stop_loss)
    config.take_profit_pct = max(0.2, args.take_profit)
    config.rsi_buy_min = args.rsi_buy_min
    config.rsi_buy_max = args.rsi_buy_max
    config.atr_k = max(0.1, args.atr_k)
    config.trail_atr_k = max(0.1, args.trail_atr_k)
    config.adx_min = max(0.0, args.adx_min)
    config.trend_strength_min = max(0.0, args.trend_strength_min)
    config.risk_per_trade_pct = max(0.01, args.risk_per_trade_pct)
    config.max_daily_trades = max(1, args.max_daily_trades)
    config.fee_bps = max(0.0, args.fee_bps)
    config.slippage_bps = max(0.0, args.slippage_bps)
    config.max_total_exposure_pct = max(1.0, args.max_total_exposure_pct)
    config.daily_loss_limit_pct = max(0.0, args.daily_loss_limit_pct)
    config.cooldown_bars = max(0, args.cooldown_bars)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if symbols:
            config.symbols = symbols

    if args.best_params_file:
        if args.optimize_ga:
            console.print(
                "[yellow]--optimize-ga is enabled: --best-params-file is ignored (GA starts from random population).[/yellow]"
            )
        else:
            loaded = config.load_best_params(args.best_params_file)
            if not loaded:
                console.print(f"❌ Failed to load best params file: {args.best_params_file}", style="red")
                sys.exit(1)
            console.print(f"✅ Loaded best params from: {args.best_params_file}", style="green")

    SpotDisplay.print_header()

    if not config.validate():
        sys.exit(1)

    if args.export_best_params and not args.optimize_ga:
        config.save_best_params(
            args.export_best_params,
            extra={
                "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                "mode": "runtime_config",
            },
        )
        console.print(f"✅ Exported active params to: {args.export_best_params}", style="green")

    system = SpotTradingSystem(config)
    try:
        if not await system.initialize():
            sys.exit(1)

        start_time = _parse_utc_datetime(args.backtest_start) if args.backtest_start else None
        end_time = _parse_utc_datetime(args.backtest_end) if args.backtest_end else None
        if args.backtest_start and not start_time:
            console.print("❌ Invalid --backtest-start datetime format", style="red")
            sys.exit(1)
        if args.backtest_end and not end_time:
            console.print("❌ Invalid --backtest-end datetime format", style="red")
            sys.exit(1)

        now_utc = datetime.now(timezone.utc)
        end_time = end_time or now_utc
        start_time = start_time or (end_time - timedelta(days=365 * max(3, args.backtest_years)))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        start_time = start_time.astimezone(timezone.utc)
        end_time = end_time.astimezone(timezone.utc)

        if args.optimize_ga:
            if args.backtest or args.monitor or args.scan:
                console.print(
                    "[yellow]--optimize-ga takes priority over --backtest/--monitor/--scan.[/yellow]"
                )
            ga_settings = GASettings(
                population_size=max(4, args.ga_pop_size),
                generations=max(1, args.ga_generations),
                mutation_rate=min(1.0, max(0.0, args.ga_mutation_rate)),
                crossover_rate=min(1.0, max(0.0, args.ga_crossover_rate)),
                elitism_k=max(1, args.ga_elitism_k),
                top_k_log=max(1, args.ga_top_k_log),
                seed=args.seed,
            )
            fitness_weights = FitnessWeights.from_string(args.fitness_weights)
            await system.run_optimize_ga(
                backtest_start=start_time,
                backtest_end=end_time,
                ga_settings=ga_settings,
                fitness_weights=fitness_weights,
                output_dir=args.ga_output_dir,
                walkforward_train_days=max(30, args.walkforward_train),
                walkforward_test_days=max(7, args.walkforward_test),
                walkforward_step_days=max(1, args.walkforward_step) if args.walkforward_step > 0 else None,
                search_timeframe=args.ga_search_timeframe,
                search_risk=args.ga_search_risk,
                search_cost=args.ga_search_cost,
                max_search_dims=max(3, args.ga_max_search_dims),
                final_validation_days=max(30, args.ga_final_test_days),
                export_best_params_path=args.export_best_params or None,
            )
        elif args.backtest:
            await system.run_backtest(
                years=max(3, args.backtest_years),
                start_time=start_time,
                end_time=end_time,
                sleep_seconds=max(0.0, args.backtest_sleep),
            )
        elif args.monitor:
            await system.monitor(auto_execute=args.auto_execute)
        else:
            await system.run_once(auto_execute=args.auto_execute)
    except KeyboardInterrupt:
        console.print("\n[yellow]Spot trading interrupted[/yellow]")
    finally:
        await system.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
