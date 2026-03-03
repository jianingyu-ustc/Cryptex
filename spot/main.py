#!/usr/bin/env python3
"""
Spot Auto-Trading Subsystem - Main Entry Point.
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from common.binance_client import BinanceClient
from .config import SpotTradingConfig, DEFAULT_SPOT_CONFIG
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


class SpotDisplay:
    """Rich display helpers."""

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
        table.add_column("MA(FAST/SLOW)", width=20, justify="right")
        table.add_column("Reason", width=30, overflow="fold")

        for s in signals:
            if s.action == "BUY":
                action = "[green]BUY[/]"
            elif s.action == "SELL":
                action = "[red]SELL[/]"
            else:
                action = "[yellow]HOLD[/]"
            table.add_row(
                s.symbol,
                action,
                f"{s.price:,.4f}" if s.price > 0 else "-",
                f"{s.confidence:.0%}",
                f"{s.rsi:.1f}",
                f"{s.fast_ma:.3f}/{s.slow_ma:.3f}",
                s.reason,
            )
        return table

    @staticmethod
    def positions_table(positions: List[SpotPosition]) -> Table:
        table = Table(title="Open Spot Positions", box=box.ROUNDED, header_style="bold yellow")
        table.add_column("Symbol", width=10)
        table.add_column("Qty", width=12, justify="right")
        table.add_column("Entry", width=12, justify="right")
        table.add_column("Last", width=12, justify="right")
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
                pnl_str if t.side == "SELL" else "-",
                cum_pnl,
                ret,
                "SIM" if t.dry_run else "LIVE",
            )
        return table


class SpotTradingSystem:
    """Main spot auto-trading system."""

    def __init__(self, config: SpotTradingConfig = None):
        self.config = config or DEFAULT_SPOT_CONFIG
        self.client: Optional[BinanceClient] = None
        self.strategy: Optional[SpotStrategyEngine] = None
        self.execution: Optional[SpotExecutionEngine] = None
        self._running = False

    async def initialize(self) -> bool:
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
        self._running = False
        if self.client:
            await self.client.close()
        console.print("✅ Spot system stopped", style="green")

    async def _scan(self) -> List[SpotSignal]:
        if not self.execution or not self.strategy:
            return []
        await self.execution.mark_positions()
        signals = await self.strategy.analyze_symbols(self.config.symbols, self.execution.positions)
        return signals

    async def run_once(self, auto_execute: bool = False):
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
                    console.print(
                        f"[{side_color}]Executed {trade.side} {trade.symbol} "
                        f"qty={trade.quantity:.6f} @ {trade.price:.4f} "
                        f"({('SIM' if trade.dry_run else 'LIVE')}) | "
                        f"Equity=${trade.account_value_after:,.2f} | "
                        f"Return={trade.cumulative_return_pct:+.2f}% "
                        f"(${trade.cumulative_pnl_usdt:+.2f})[/]"
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
        ])
        console.print(Panel(summary, title="Spot Stats", border_style="cyan"))

    async def monitor(self, auto_execute: bool = False):
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
    parser = argparse.ArgumentParser(description="Crypto Spot Auto Trading System")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
    parser.add_argument("--scan", action="store_true", help="Single scan mode")
    parser.add_argument("--monitor", "-m", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--auto-execute", action="store_true", help="Execute BUY/SELL signals")
    parser.add_argument("--live", action="store_true", help="Enable live trading (default is dry-run)")
    parser.add_argument("--interval", type=int, default=30, help="Monitor interval in seconds")
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="Initial capital in USDT")
    parser.add_argument("--usdt-per-trade", type=float, default=100.0, help="USDT allocation per trade")
    parser.add_argument("--max-positions", type=int, default=3, help="Maximum open positions")
    parser.add_argument("--kline-interval", type=str, default="15m", help="Kline interval for signals")
    parser.add_argument("--stop-loss", type=float, default=2.0, help="Stop loss percent")
    parser.add_argument("--take-profit", type=float, default=4.0, help="Take profit percent")

    args = parser.parse_args()

    config = SpotTradingConfig()
    config.dry_run = not args.live
    config.check_interval = max(5, args.interval)
    config.initial_capital = max(100.0, args.initial_capital)
    config.usdt_per_trade = max(10.0, args.usdt_per_trade)
    config.max_open_positions = max(1, args.max_positions)
    config.kline_interval = args.kline_interval
    config.stop_loss_pct = max(0.2, args.stop_loss)
    config.take_profit_pct = max(0.2, args.take_profit)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if symbols:
            config.symbols = symbols

    SpotDisplay.print_header()

    if not config.validate():
        sys.exit(1)

    system = SpotTradingSystem(config)
    try:
        if not await system.initialize():
            sys.exit(1)

        if args.monitor:
            await system.monitor(auto_execute=args.auto_execute)
        else:
            await system.run_once(auto_execute=args.auto_execute)
    except KeyboardInterrupt:
        console.print("\n[yellow]Spot trading interrupted[/yellow]")
    finally:
        await system.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
