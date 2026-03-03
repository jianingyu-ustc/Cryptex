"""
Backtesting for Arbitrage Subsystem

Currently supports:
- Funding rate arbitrage backtest (long spot + short perpetual)
- Basis arbitrage backtest (long spot + short delivery futures)
- Stablecoin spread arbitrage backtest
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from .api import BinanceClient, FuturesContractData
from .config import ArbitrageConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class FundingTradeResult:
    """Single trade result in funding backtest."""
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_rate_pct: float
    exit_rate_pct: float
    gross_funding_pct: float
    net_return_pct: float
    duration_hours: int
    exit_reason: str

    @property
    def is_win(self) -> bool:
        return self.net_return_pct > 0


@dataclass
class FundingSymbolStats:
    """Aggregated stats for one symbol."""
    symbol: str
    samples: int
    trades: int
    wins: int
    win_rate_pct: float
    gross_funding_pct: float
    avg_trade_pct: float
    max_drawdown_pct: float
    net_return_pct: float
    final_equity: float
    best_trade_pct: float
    worst_trade_pct: float


@dataclass
class FundingBacktestSummary:
    """Overall backtest summary."""
    strategy: str
    hours: int
    threshold_pct: float
    symbols: List[str]
    initial_capital: float
    final_capital: float
    total_return_pct: float
    total_trades: int
    total_wins: int
    win_rate_pct: float
    max_drawdown_pct: float


class FundingBacktester:
    """Funding rate arbitrage backtester."""

    def __init__(
        self,
        client: BinanceClient,
        config: ArbitrageConfig = None,
        initial_capital: float = 10000.0
    ):
        self.client = client
        self.config = config or DEFAULT_CONFIG
        self.initial_capital = max(100.0, float(initial_capital))

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().upper()

    async def _fetch_histories(
        self,
        symbols: List[str],
        hours: int
    ) -> Dict[str, List[Dict]]:
        # Funding is usually every 8h, with a small buffer for entry/exit windows.
        limit = min(1000, max(12, (hours // 8) + 6))
        tasks = [self.client.get_funding_rate_history(symbol, limit=limit) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        histories: Dict[str, List[Dict]] = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch history for {symbol}: {result}")
                histories[symbol] = []
                continue

            history = result or []
            history.sort(key=lambda item: item["funding_time"])
            histories[symbol] = history

        return histories

    def _simulate_symbol(
        self,
        symbol: str,
        history: List[Dict],
        capital: float
    ) -> Tuple[FundingSymbolStats, List[FundingTradeResult]]:
        trades: List[FundingTradeResult] = []

        if not history:
            empty = FundingSymbolStats(
                symbol=symbol,
                samples=0,
                trades=0,
                wins=0,
                win_rate_pct=0.0,
                gross_funding_pct=0.0,
                avg_trade_pct=0.0,
                max_drawdown_pct=0.0,
                net_return_pct=0.0,
                final_equity=capital,
                best_trade_pct=0.0,
                worst_trade_pct=0.0,
            )
            return empty, trades

        open_cost = self.config.total_cost_per_trade
        close_cost = self.config.total_cost_per_trade
        entry_threshold = self.config.funding_rate_threshold
        exit_threshold = entry_threshold / 2

        position_open = False
        entry_time: Optional[datetime] = None
        entry_rate = 0.0
        trade_gross = 0.0
        trade_net = 0.0

        equity = capital
        peak_equity = capital
        max_drawdown = 0.0

        def close_trade(exit_time: datetime, exit_rate: float, reason: str):
            nonlocal position_open, trade_net, trade_gross, entry_time, entry_rate
            nonlocal equity, peak_equity, max_drawdown

            trade_net -= close_cost
            safe_entry_time = entry_time or exit_time
            duration = int(max(0, (exit_time - safe_entry_time).total_seconds() // 3600))

            trade = FundingTradeResult(
                symbol=symbol,
                entry_time=safe_entry_time,
                exit_time=exit_time,
                entry_rate_pct=entry_rate,
                exit_rate_pct=exit_rate,
                gross_funding_pct=trade_gross,
                net_return_pct=trade_net,
                duration_hours=duration,
                exit_reason=reason,
            )
            trades.append(trade)

            equity *= (1 + trade.net_return_pct / 100)
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                drawdown = (peak_equity - equity) / peak_equity * 100
                max_drawdown = max(max_drawdown, drawdown)

            position_open = False
            entry_time = None
            entry_rate = 0.0
            trade_gross = 0.0
            trade_net = 0.0

        for item in history:
            funding_rate = float(item["funding_rate"])
            funding_time = item["funding_time"]

            if not position_open:
                if funding_rate >= entry_threshold:
                    position_open = True
                    entry_time = funding_time
                    entry_rate = funding_rate
                    trade_gross = 0.0
                    trade_net = -open_cost
                continue

            trade_gross += funding_rate
            trade_net += funding_rate

            should_exit = False
            reason = ""
            if self.config.funding_negative_exit and funding_rate < 0:
                should_exit = True
                reason = "funding_negative"
            elif funding_rate < exit_threshold:
                should_exit = True
                reason = "below_half_threshold"

            if should_exit:
                close_trade(funding_time, funding_rate, reason)

        if position_open:
            last = history[-1]
            close_trade(last["funding_time"], float(last["funding_rate"]), "end_of_backtest")

        trade_returns = [t.net_return_pct for t in trades]
        wins = sum(1 for t in trades if t.is_win)
        gross_funding = sum(t.gross_funding_pct for t in trades)

        stats = FundingSymbolStats(
            symbol=symbol,
            samples=len(history),
            trades=len(trades),
            wins=wins,
            win_rate_pct=(wins / len(trades) * 100) if trades else 0.0,
            gross_funding_pct=gross_funding,
            avg_trade_pct=(sum(trade_returns) / len(trade_returns)) if trade_returns else 0.0,
            max_drawdown_pct=max_drawdown,
            net_return_pct=((equity / capital - 1) * 100) if capital > 0 else 0.0,
            final_equity=equity,
            best_trade_pct=max(trade_returns) if trade_returns else 0.0,
            worst_trade_pct=min(trade_returns) if trade_returns else 0.0,
        )
        return stats, trades

    async def run(
        self,
        symbols: List[str],
        hours: int = 168
    ) -> Tuple[FundingBacktestSummary, List[FundingSymbolStats], List[FundingTradeResult]]:
        clean_symbols = []
        for symbol in symbols:
            s = self._normalize_symbol(symbol)
            if s and s not in clean_symbols:
                clean_symbols.append(s)

        if not clean_symbols:
            clean_symbols = self.config.perpetual_symbols[:5]

        hours = max(8, int(hours))
        histories = await self._fetch_histories(clean_symbols, hours)

        symbol_capital = self.initial_capital / max(1, len(clean_symbols))
        all_stats: List[FundingSymbolStats] = []
        all_trades: List[FundingTradeResult] = []

        for symbol in clean_symbols:
            stats, trades = self._simulate_symbol(symbol, histories.get(symbol, []), symbol_capital)
            all_stats.append(stats)
            all_trades.extend(trades)

        total_final = sum(item.final_equity for item in all_stats)
        total_trades = sum(item.trades for item in all_stats)
        total_wins = sum(item.wins for item in all_stats)
        max_drawdown = max((item.max_drawdown_pct for item in all_stats), default=0.0)

        summary = FundingBacktestSummary(
            strategy="funding",
            hours=hours,
            threshold_pct=self.config.funding_rate_threshold,
            symbols=clean_symbols,
            initial_capital=self.initial_capital,
            final_capital=total_final,
            total_return_pct=((total_final / self.initial_capital - 1) * 100) if self.initial_capital > 0 else 0.0,
            total_trades=total_trades,
            total_wins=total_wins,
            win_rate_pct=(total_wins / total_trades * 100) if total_trades else 0.0,
            max_drawdown_pct=max_drawdown,
        )

        return summary, all_stats, all_trades

    @staticmethod
    def display_results(
        summary: FundingBacktestSummary,
        symbol_stats: List[FundingSymbolStats],
        trades: List[FundingTradeResult]
    ):
        summary_text = f"""
[bold]Strategy:[/bold] Funding Rate Arbitrage
[bold]Window:[/bold] {summary.hours}h
[bold]Symbols:[/bold] {", ".join(summary.symbols)}
[bold]Entry Threshold:[/bold] {summary.threshold_pct:.4f}%

[bold]Initial Capital:[/bold] ${summary.initial_capital:,.2f}
[bold]Final Capital:[/bold] ${summary.final_capital:,.2f}
[bold]Total Return:[/bold] {"[green]" if summary.total_return_pct >= 0 else "[red]"}{summary.total_return_pct:+.2f}%[/]
[bold]Total Trades:[/bold] {summary.total_trades}
[bold]Win Rate:[/bold] {summary.win_rate_pct:.1f}%
[bold]Max Drawdown:[/bold] {summary.max_drawdown_pct:.2f}%
"""
        console.print(Panel(summary_text.strip(), title="📈 Arbitrage Backtest Summary", border_style="cyan"))

        by_symbol = Table(
            title="Per-Symbol Backtest Stats",
            box=box.ROUNDED,
            header_style="bold cyan"
        )
        by_symbol.add_column("Symbol", width=10)
        by_symbol.add_column("Samples", width=8, justify="right")
        by_symbol.add_column("Trades", width=8, justify="right")
        by_symbol.add_column("Win %", width=8, justify="right")
        by_symbol.add_column("Gross %", width=10, justify="right")
        by_symbol.add_column("Avg Trade %", width=12, justify="right")
        by_symbol.add_column("Net %", width=10, justify="right")
        by_symbol.add_column("MDD %", width=9, justify="right")

        for item in sorted(symbol_stats, key=lambda x: x.net_return_pct, reverse=True):
            net_color = "green" if item.net_return_pct >= 0 else "red"
            by_symbol.add_row(
                item.symbol,
                str(item.samples),
                str(item.trades),
                f"{item.win_rate_pct:.1f}",
                f"{item.gross_funding_pct:+.3f}",
                f"{item.avg_trade_pct:+.3f}",
                f"[{net_color}]{item.net_return_pct:+.3f}[/]",
                f"{item.max_drawdown_pct:.2f}",
            )
        console.print(by_symbol)

        if trades:
            recent_table = Table(
                title="Recent Trades (latest 15)",
                box=box.MINIMAL_DOUBLE_HEAD,
                header_style="bold magenta"
            )
            recent_table.add_column("Symbol", width=10)
            recent_table.add_column("Entry (UTC)", width=19)
            recent_table.add_column("Exit (UTC)", width=19)
            recent_table.add_column("Duration(h)", width=10, justify="right")
            recent_table.add_column("Net %", width=10, justify="right")
            recent_table.add_column("Reason", width=20)

            recent = sorted(trades, key=lambda t: t.exit_time, reverse=True)[:15]
            for trade in recent:
                net_color = "green" if trade.net_return_pct >= 0 else "red"
                recent_table.add_row(
                    trade.symbol,
                    trade.entry_time.strftime("%Y-%m-%d %H:%M"),
                    trade.exit_time.strftime("%Y-%m-%d %H:%M"),
                    str(trade.duration_hours),
                    f"[{net_color}]{trade.net_return_pct:+.3f}[/]",
                    trade.exit_reason,
                )
            console.print(recent_table)
        else:
            console.print("[yellow]No trades were generated in this backtest window.[/yellow]")


@dataclass
class BasisTradeResult:
    """Single trade result in basis backtest."""
    contract_symbol: str
    spot_symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_spot_price: float
    entry_futures_price: float
    exit_spot_price: float
    exit_futures_price: float
    entry_annualized_pct: float
    exit_annualized_pct: float
    gross_return_pct: float
    net_return_pct: float
    duration_hours: int
    exit_reason: str

    @property
    def is_win(self) -> bool:
        return self.net_return_pct > 0


@dataclass
class BasisContractStats:
    """Aggregated stats for one basis contract."""
    contract_symbol: str
    spot_symbol: str
    samples: int
    trades: int
    wins: int
    win_rate_pct: float
    avg_annualized_pct: float
    avg_trade_pct: float
    max_drawdown_pct: float
    net_return_pct: float
    final_equity: float
    best_trade_pct: float
    worst_trade_pct: float


@dataclass
class BasisBacktestSummary:
    """Overall basis backtest summary."""
    strategy: str
    hours: int
    threshold_pct: float
    contracts: List[str]
    initial_capital: float
    final_capital: float
    total_return_pct: float
    total_trades: int
    total_wins: int
    win_rate_pct: float
    max_drawdown_pct: float


class BasisBacktester:
    """Cash-and-carry basis backtester."""

    def __init__(
        self,
        client: BinanceClient,
        config: ArbitrageConfig = None,
        initial_capital: float = 10000.0
    ):
        self.client = client
        self.config = config or DEFAULT_CONFIG
        self.initial_capital = max(100.0, float(initial_capital))

    @staticmethod
    def _normalize_symbols(symbols: List[str]) -> List[str]:
        result: List[str] = []
        for symbol in symbols:
            s = symbol.strip().upper()
            if s and s not in result:
                result.append(s)
        return result

    async def _select_contracts(self, symbols: List[str]) -> List[FuturesContractData]:
        contracts = await self.client.get_delivery_contracts()
        if not contracts:
            return []

        selected = self._normalize_symbols(symbols)
        if selected:
            selected_set = set(selected)
            filtered = [c for c in contracts if c.symbol in selected_set]
            if filtered:
                contracts = filtered
            else:
                logger.warning(
                    "No delivery contracts matched provided symbols; falling back to default contract selection"
                )
                selected = []

        if not selected:
            spot_set = set(self.config.spot_symbols)
            contracts = [c for c in contracts if c.pair.replace("USD", "USDT") in spot_set]
            contracts.sort(key=lambda c: (c.delivery_date, c.symbol))
            contracts = contracts[:6]

        dedup: Dict[str, FuturesContractData] = {}
        for c in contracts:
            dedup[c.symbol] = c
        return list(dedup.values())

    async def _build_contract_series(
        self,
        contract: FuturesContractData,
        hours: int
    ) -> Tuple[str, List[Dict]]:
        spot_symbol = contract.pair.replace("USD", "USDT")
        limit = min(1000, max(120, hours + 48))

        spot_task = self.client.get_spot_klines(spot_symbol, interval="1h", limit=limit)
        futures_task = self.client.get_delivery_klines(contract.symbol, interval="1h", limit=limit)
        spot_klines, futures_klines = await asyncio.gather(spot_task, futures_task)

        if not spot_klines or not futures_klines:
            return spot_symbol, []

        spot_map = {k["open_time"]: k["close"] for k in spot_klines}
        futures_map = {k["open_time"]: k["close"] for k in futures_klines}

        common_times = sorted(set(spot_map.keys()).intersection(futures_map.keys()))
        if not common_times:
            return spot_symbol, []

        start_time = common_times[-1] - timedelta(hours=max(8, int(hours)))
        rows: List[Dict] = []
        for ts in common_times:
            if ts < start_time:
                continue
            spot_price = spot_map[ts]
            futures_price = futures_map[ts]
            if spot_price <= 0 or futures_price <= 0:
                continue

            days_to_expiry = (contract.delivery_date - ts).total_seconds() / 86400
            if days_to_expiry <= 0:
                continue

            annualized_basis = ((futures_price - spot_price) / spot_price) * (365 / days_to_expiry) * 100
            rows.append(
                {
                    "time": ts,
                    "spot_price": spot_price,
                    "futures_price": futures_price,
                    "days_to_expiry": days_to_expiry,
                    "annualized_basis_pct": annualized_basis,
                }
            )

        return spot_symbol, rows

    def _simulate_contract(
        self,
        contract_symbol: str,
        spot_symbol: str,
        rows: List[Dict],
        capital: float
    ) -> Tuple[BasisContractStats, List[BasisTradeResult]]:
        trades: List[BasisTradeResult] = []

        if not rows:
            empty = BasisContractStats(
                contract_symbol=contract_symbol,
                spot_symbol=spot_symbol,
                samples=0,
                trades=0,
                wins=0,
                win_rate_pct=0.0,
                avg_annualized_pct=0.0,
                avg_trade_pct=0.0,
                max_drawdown_pct=0.0,
                net_return_pct=0.0,
                final_equity=capital,
                best_trade_pct=0.0,
                worst_trade_pct=0.0,
            )
            return empty, trades

        threshold = self.config.basis_annualized_threshold
        min_days_to_open = 7

        position_open = False
        entry_time: Optional[datetime] = None
        entry_spot = 0.0
        entry_futures = 0.0
        entry_annualized = 0.0

        equity = capital
        peak_equity = capital
        max_drawdown = 0.0

        def close_trade(current: Dict, reason: str):
            nonlocal position_open, entry_time, entry_spot, entry_futures, entry_annualized
            nonlocal equity, peak_equity, max_drawdown

            exit_spot = current["spot_price"]
            exit_futures = current["futures_price"]

            spot_return = ((exit_spot - entry_spot) / entry_spot * 100) if entry_spot > 0 else 0.0
            futures_return = ((entry_futures - exit_futures) / entry_futures * 100) if entry_futures > 0 else 0.0
            gross_return = spot_return + futures_return
            net_return = gross_return - self.config.round_trip_cost

            safe_entry_time = entry_time or current["time"]
            duration = int(max(0, (current["time"] - safe_entry_time).total_seconds() // 3600))
            trade = BasisTradeResult(
                contract_symbol=contract_symbol,
                spot_symbol=spot_symbol,
                entry_time=safe_entry_time,
                exit_time=current["time"],
                entry_spot_price=entry_spot,
                entry_futures_price=entry_futures,
                exit_spot_price=exit_spot,
                exit_futures_price=exit_futures,
                entry_annualized_pct=entry_annualized,
                exit_annualized_pct=current["annualized_basis_pct"],
                gross_return_pct=gross_return,
                net_return_pct=net_return,
                duration_hours=duration,
                exit_reason=reason,
            )
            trades.append(trade)

            equity *= (1 + net_return / 100)
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                drawdown = (peak_equity - equity) / peak_equity * 100
                max_drawdown = max(max_drawdown, drawdown)

            position_open = False
            entry_time = None
            entry_spot = 0.0
            entry_futures = 0.0
            entry_annualized = 0.0

        for row in rows:
            annualized = row["annualized_basis_pct"]
            days_to_expiry = row["days_to_expiry"]

            if not position_open:
                if days_to_expiry < min_days_to_open:
                    continue

                annualized_cost = self.config.round_trip_cost * (365 / max(days_to_expiry, 1e-9))
                net_annualized = annualized - annualized_cost
                if net_annualized >= threshold:
                    position_open = True
                    entry_time = row["time"]
                    entry_spot = row["spot_price"]
                    entry_futures = row["futures_price"]
                    entry_annualized = annualized
                continue

            should_exit = False
            reason = ""
            if days_to_expiry <= self.config.basis_days_before_expiry_exit:
                should_exit = True
                reason = "pre_expiry_exit"
            elif annualized < threshold / 2:
                should_exit = True
                reason = "basis_compressed"

            if should_exit:
                close_trade(row, reason)

        if position_open:
            close_trade(rows[-1], "end_of_backtest")

        trade_returns = [t.net_return_pct for t in trades]
        wins = sum(1 for t in trades if t.is_win)
        avg_annualized = sum(r["annualized_basis_pct"] for r in rows) / len(rows) if rows else 0.0

        stats = BasisContractStats(
            contract_symbol=contract_symbol,
            spot_symbol=spot_symbol,
            samples=len(rows),
            trades=len(trades),
            wins=wins,
            win_rate_pct=(wins / len(trades) * 100) if trades else 0.0,
            avg_annualized_pct=avg_annualized,
            avg_trade_pct=(sum(trade_returns) / len(trade_returns)) if trade_returns else 0.0,
            max_drawdown_pct=max_drawdown,
            net_return_pct=((equity / capital - 1) * 100) if capital > 0 else 0.0,
            final_equity=equity,
            best_trade_pct=max(trade_returns) if trade_returns else 0.0,
            worst_trade_pct=min(trade_returns) if trade_returns else 0.0,
        )
        return stats, trades

    async def run(
        self,
        symbols: List[str],
        hours: int = 168
    ) -> Tuple[BasisBacktestSummary, List[BasisContractStats], List[BasisTradeResult]]:
        hours = max(8, int(hours))
        contracts = await self._select_contracts(symbols)
        if not contracts:
            summary = BasisBacktestSummary(
                strategy="basis",
                hours=hours,
                threshold_pct=self.config.basis_annualized_threshold,
                contracts=[],
                initial_capital=self.initial_capital,
                final_capital=self.initial_capital,
                total_return_pct=0.0,
                total_trades=0,
                total_wins=0,
                win_rate_pct=0.0,
                max_drawdown_pct=0.0,
            )
            return summary, [], []

        tasks = [self._build_contract_series(contract, hours) for contract in contracts]
        series_results = await asyncio.gather(*tasks, return_exceptions=True)

        capital_per_contract = self.initial_capital / max(1, len(contracts))
        all_stats: List[BasisContractStats] = []
        all_trades: List[BasisTradeResult] = []

        for contract, series_result in zip(contracts, series_results):
            if isinstance(series_result, Exception):
                logger.error(f"Failed to build basis series for {contract.symbol}: {series_result}")
                spot_symbol, rows = contract.pair.replace("USD", "USDT"), []
            else:
                spot_symbol, rows = series_result

            stats, trades = self._simulate_contract(
                contract.symbol,
                spot_symbol,
                rows,
                capital_per_contract
            )
            all_stats.append(stats)
            all_trades.extend(trades)

        total_final = sum(s.final_equity for s in all_stats)
        total_trades = sum(s.trades for s in all_stats)
        total_wins = sum(s.wins for s in all_stats)
        max_drawdown = max((s.max_drawdown_pct for s in all_stats), default=0.0)

        summary = BasisBacktestSummary(
            strategy="basis",
            hours=hours,
            threshold_pct=self.config.basis_annualized_threshold,
            contracts=[c.symbol for c in contracts],
            initial_capital=self.initial_capital,
            final_capital=total_final,
            total_return_pct=((total_final / self.initial_capital - 1) * 100) if self.initial_capital > 0 else 0.0,
            total_trades=total_trades,
            total_wins=total_wins,
            win_rate_pct=(total_wins / total_trades * 100) if total_trades else 0.0,
            max_drawdown_pct=max_drawdown,
        )
        return summary, all_stats, all_trades

    @staticmethod
    def display_results(
        summary: BasisBacktestSummary,
        stats: List[BasisContractStats],
        trades: List[BasisTradeResult]
    ):
        summary_text = f"""
[bold]Strategy:[/bold] Basis Arbitrage (Cash & Carry)
[bold]Window:[/bold] {summary.hours}h
[bold]Contracts:[/bold] {", ".join(summary.contracts) if summary.contracts else "-"}
[bold]Entry Threshold:[/bold] {summary.threshold_pct:.2f}% annualized

[bold]Initial Capital:[/bold] ${summary.initial_capital:,.2f}
[bold]Final Capital:[/bold] ${summary.final_capital:,.2f}
[bold]Total Return:[/bold] {"[green]" if summary.total_return_pct >= 0 else "[red]"}{summary.total_return_pct:+.2f}%[/]
[bold]Total Trades:[/bold] {summary.total_trades}
[bold]Win Rate:[/bold] {summary.win_rate_pct:.1f}%
[bold]Max Drawdown:[/bold] {summary.max_drawdown_pct:.2f}%
"""
        console.print(Panel(summary_text.strip(), title="📈 Basis Backtest Summary", border_style="yellow"))

        table = Table(title="Per-Contract Backtest Stats", box=box.ROUNDED, header_style="bold yellow")
        table.add_column("Contract", width=16)
        table.add_column("Spot", width=10)
        table.add_column("Samples", width=8, justify="right")
        table.add_column("Trades", width=8, justify="right")
        table.add_column("Win %", width=8, justify="right")
        table.add_column("Avg Ann %", width=10, justify="right")
        table.add_column("Avg Trade %", width=12, justify="right")
        table.add_column("Net %", width=10, justify="right")

        for item in sorted(stats, key=lambda x: x.net_return_pct, reverse=True):
            net_color = "green" if item.net_return_pct >= 0 else "red"
            table.add_row(
                item.contract_symbol,
                item.spot_symbol,
                str(item.samples),
                str(item.trades),
                f"{item.win_rate_pct:.1f}",
                f"{item.avg_annualized_pct:+.2f}",
                f"{item.avg_trade_pct:+.3f}",
                f"[{net_color}]{item.net_return_pct:+.3f}[/]",
            )
        console.print(table)

        if trades:
            recent = sorted(trades, key=lambda t: t.exit_time, reverse=True)[:15]
            t = Table(title="Recent Basis Trades (latest 15)", box=box.MINIMAL_DOUBLE_HEAD, header_style="bold yellow")
            t.add_column("Contract", width=16)
            t.add_column("Entry (UTC)", width=19)
            t.add_column("Exit (UTC)", width=19)
            t.add_column("Gross %", width=10, justify="right")
            t.add_column("Net %", width=10, justify="right")
            t.add_column("Reason", width=18)
            for trade in recent:
                color = "green" if trade.net_return_pct >= 0 else "red"
                t.add_row(
                    trade.contract_symbol,
                    trade.entry_time.strftime("%Y-%m-%d %H:%M"),
                    trade.exit_time.strftime("%Y-%m-%d %H:%M"),
                    f"{trade.gross_return_pct:+.3f}",
                    f"[{color}]{trade.net_return_pct:+.3f}[/]",
                    trade.exit_reason,
                )
            console.print(t)
        else:
            console.print("[yellow]No basis trades were generated in this backtest window.[/yellow]")


@dataclass
class StablecoinTradeResult:
    """Single trade result in stablecoin spread backtest."""
    pair: str
    entry_time: datetime
    exit_time: datetime
    entry_spread_pct: float
    exit_spread_pct: float
    gross_capture_pct: float
    net_return_pct: float
    duration_hours: int
    exit_reason: str

    @property
    def is_win(self) -> bool:
        return self.net_return_pct > 0


@dataclass
class StablecoinPairStats:
    """Aggregated stats for one stablecoin pair."""
    pair: str
    samples: int
    trades: int
    wins: int
    win_rate_pct: float
    avg_spread_pct: float
    avg_trade_pct: float
    max_drawdown_pct: float
    net_return_pct: float
    final_equity: float
    best_trade_pct: float
    worst_trade_pct: float


@dataclass
class StablecoinBacktestSummary:
    """Overall stablecoin spread backtest summary."""
    strategy: str
    hours: int
    threshold_pct: float
    pairs: List[str]
    initial_capital: float
    final_capital: float
    total_return_pct: float
    total_trades: int
    total_wins: int
    win_rate_pct: float
    max_drawdown_pct: float


class StablecoinBacktester:
    """Stablecoin spread arbitrage backtester."""

    COIN_TO_SYMBOL = {
        "USDC": "USDCUSDT",
        "BUSD": "BUSDUSDT",
        "DAI": "DAIUSDT",
        "TUSD": "TUSDUSDT",
        "USDP": "USDPUSDT",
        "FDUSD": "FDUSDUSDT",
    }

    def __init__(
        self,
        client: BinanceClient,
        config: ArbitrageConfig = None,
        initial_capital: float = 10000.0
    ):
        self.client = client
        self.config = config or DEFAULT_CONFIG
        self.initial_capital = max(100.0, float(initial_capital))

    def _parse_coins(self, symbols: List[str]) -> List[str]:
        coins: List[str] = []
        if symbols:
            for raw in symbols:
                token = raw.strip().upper()
                if not token:
                    continue
                if "/" in token:
                    left, right = token.split("/", 1)
                    for c in (left.strip(), right.strip()):
                        if c and c not in coins:
                            coins.append(c)
                elif token.endswith("USDT") and len(token) > 4:
                    c = token[:-4]
                    if c and c not in coins:
                        coins.append(c)
                    if "USDT" not in coins:
                        coins.append("USDT")
                else:
                    if token not in coins:
                        coins.append(token)
        else:
            for c in self.config.stablecoins:
                cu = c.strip().upper()
                if cu and cu not in coins:
                    coins.append(cu)

        if "USDT" not in coins:
            coins.append("USDT")

        supported = {"USDT", *self.COIN_TO_SYMBOL.keys()}
        filtered = [c for c in coins if c in supported]

        if len(filtered) < 2:
            # Fallback for cases where symbols are for other strategies
            filtered = []
            for c in self.config.stablecoins:
                cu = c.strip().upper()
                if cu in supported and cu not in filtered:
                    filtered.append(cu)
            if "USDT" not in filtered:
                filtered.append("USDT")

        return filtered

    async def _fetch_price_maps(self, coins: List[str], hours: int) -> Dict[str, Dict[datetime, float]]:
        limit = min(1000, max(120, hours + 48))
        tasks = []
        requested: List[str] = []
        for coin in coins:
            if coin == "USDT":
                continue
            symbol = self.COIN_TO_SYMBOL.get(coin)
            if symbol:
                tasks.append(self.client.get_spot_klines(symbol, interval="1h", limit=limit))
                requested.append(coin)

        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        maps: Dict[str, Dict[datetime, float]] = {}
        for coin, result in zip(requested, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch stablecoin klines for {coin}: {result}")
                maps[coin] = {}
                continue

            rows = result or []
            if not rows:
                maps[coin] = {}
                continue

            end_time = max(row["open_time"] for row in rows)
            start_time = end_time - timedelta(hours=max(8, int(hours)))
            maps[coin] = {
                row["open_time"]: row["close"]
                for row in rows
                if row["open_time"] >= start_time
            }

        maps["USDT"] = {}
        return maps

    def _simulate_pair(
        self,
        coin_a: str,
        coin_b: str,
        price_maps: Dict[str, Dict[datetime, float]],
        capital: float
    ) -> Tuple[StablecoinPairStats, List[StablecoinTradeResult]]:
        pair = f"{coin_a}/{coin_b}"
        trades: List[StablecoinTradeResult] = []

        map_a = price_maps.get(coin_a, {})
        map_b = price_maps.get(coin_b, {})

        if coin_a == "USDT" and coin_b == "USDT":
            times: List[datetime] = []
        elif coin_a == "USDT":
            times = sorted(map_b.keys())
        elif coin_b == "USDT":
            times = sorted(map_a.keys())
        else:
            times = sorted(set(map_a.keys()).intersection(map_b.keys()))

        if not times:
            empty = StablecoinPairStats(
                pair=pair,
                samples=0,
                trades=0,
                wins=0,
                win_rate_pct=0.0,
                avg_spread_pct=0.0,
                avg_trade_pct=0.0,
                max_drawdown_pct=0.0,
                net_return_pct=0.0,
                final_equity=capital,
                best_trade_pct=0.0,
                worst_trade_pct=0.0,
            )
            return empty, trades

        threshold = self.config.stablecoin_spread_threshold
        exit_threshold = threshold / 2

        position_open = False
        entry_time: Optional[datetime] = None
        entry_spread = 0.0

        equity = capital
        peak_equity = capital
        max_drawdown = 0.0
        spread_values: List[float] = []

        def get_price(coin: str, ts: datetime) -> float:
            if coin == "USDT":
                return 1.0
            return price_maps.get(coin, {}).get(ts, 0.0)

        def close_trade(ts: datetime, spread: float, reason: str):
            nonlocal position_open, entry_time, entry_spread
            nonlocal equity, peak_equity, max_drawdown

            gross_capture = entry_spread - spread
            net_return = gross_capture - self.config.round_trip_cost
            safe_entry_time = entry_time or ts
            duration = int(max(0, (ts - safe_entry_time).total_seconds() // 3600))

            trade = StablecoinTradeResult(
                pair=pair,
                entry_time=safe_entry_time,
                exit_time=ts,
                entry_spread_pct=entry_spread,
                exit_spread_pct=spread,
                gross_capture_pct=gross_capture,
                net_return_pct=net_return,
                duration_hours=duration,
                exit_reason=reason,
            )
            trades.append(trade)

            equity *= (1 + net_return / 100)
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                drawdown = (peak_equity - equity) / peak_equity * 100
                max_drawdown = max(max_drawdown, drawdown)

            position_open = False
            entry_time = None
            entry_spread = 0.0

        last_ts: Optional[datetime] = None
        last_spread = 0.0
        for ts in times:
            price_a = get_price(coin_a, ts)
            price_b = get_price(coin_b, ts)
            if price_a <= 0 or price_b <= 0:
                continue

            spread = abs(price_a - price_b) / min(price_a, price_b) * 100
            spread_values.append(spread)
            last_ts = ts
            last_spread = spread

            if not position_open:
                if spread >= threshold:
                    position_open = True
                    entry_time = ts
                    entry_spread = spread
                continue

            if spread <= exit_threshold:
                close_trade(ts, spread, "spread_converged")

        if position_open and last_ts is not None:
            close_trade(last_ts, last_spread, "end_of_backtest")

        trade_returns = [t.net_return_pct for t in trades]
        wins = sum(1 for t in trades if t.is_win)
        avg_spread = (sum(spread_values) / len(spread_values)) if spread_values else 0.0

        stats = StablecoinPairStats(
            pair=pair,
            samples=len(spread_values),
            trades=len(trades),
            wins=wins,
            win_rate_pct=(wins / len(trades) * 100) if trades else 0.0,
            avg_spread_pct=avg_spread,
            avg_trade_pct=(sum(trade_returns) / len(trade_returns)) if trade_returns else 0.0,
            max_drawdown_pct=max_drawdown,
            net_return_pct=((equity / capital - 1) * 100) if capital > 0 else 0.0,
            final_equity=equity,
            best_trade_pct=max(trade_returns) if trade_returns else 0.0,
            worst_trade_pct=min(trade_returns) if trade_returns else 0.0,
        )
        return stats, trades

    async def run(
        self,
        symbols: List[str],
        hours: int = 168
    ) -> Tuple[StablecoinBacktestSummary, List[StablecoinPairStats], List[StablecoinTradeResult]]:
        hours = max(8, int(hours))
        coins = self._parse_coins(symbols)
        if len(coins) < 2:
            summary = StablecoinBacktestSummary(
                strategy="stablecoin",
                hours=hours,
                threshold_pct=self.config.stablecoin_spread_threshold,
                pairs=[],
                initial_capital=self.initial_capital,
                final_capital=self.initial_capital,
                total_return_pct=0.0,
                total_trades=0,
                total_wins=0,
                win_rate_pct=0.0,
                max_drawdown_pct=0.0,
            )
            return summary, [], []

        price_maps = await self._fetch_price_maps(coins, hours)

        pairs: List[Tuple[str, str]] = []
        for i in range(len(coins)):
            for j in range(i + 1, len(coins)):
                pairs.append((coins[i], coins[j]))

        if not pairs:
            summary = StablecoinBacktestSummary(
                strategy="stablecoin",
                hours=hours,
                threshold_pct=self.config.stablecoin_spread_threshold,
                pairs=[],
                initial_capital=self.initial_capital,
                final_capital=self.initial_capital,
                total_return_pct=0.0,
                total_trades=0,
                total_wins=0,
                win_rate_pct=0.0,
                max_drawdown_pct=0.0,
            )
            return summary, [], []

        capital_per_pair = self.initial_capital / len(pairs)
        all_stats: List[StablecoinPairStats] = []
        all_trades: List[StablecoinTradeResult] = []
        for coin_a, coin_b in pairs:
            stats, trades = self._simulate_pair(coin_a, coin_b, price_maps, capital_per_pair)
            all_stats.append(stats)
            all_trades.extend(trades)

        total_final = sum(s.final_equity for s in all_stats)
        total_trades = sum(s.trades for s in all_stats)
        total_wins = sum(s.wins for s in all_stats)
        max_drawdown = max((s.max_drawdown_pct for s in all_stats), default=0.0)

        summary = StablecoinBacktestSummary(
            strategy="stablecoin",
            hours=hours,
            threshold_pct=self.config.stablecoin_spread_threshold,
            pairs=[f"{a}/{b}" for a, b in pairs],
            initial_capital=self.initial_capital,
            final_capital=total_final,
            total_return_pct=((total_final / self.initial_capital - 1) * 100) if self.initial_capital > 0 else 0.0,
            total_trades=total_trades,
            total_wins=total_wins,
            win_rate_pct=(total_wins / total_trades * 100) if total_trades else 0.0,
            max_drawdown_pct=max_drawdown,
        )
        return summary, all_stats, all_trades

    @staticmethod
    def display_results(
        summary: StablecoinBacktestSummary,
        stats: List[StablecoinPairStats],
        trades: List[StablecoinTradeResult]
    ):
        summary_text = f"""
[bold]Strategy:[/bold] Stablecoin Spread Arbitrage
[bold]Window:[/bold] {summary.hours}h
[bold]Pairs:[/bold] {len(summary.pairs)}
[bold]Entry Threshold:[/bold] {summary.threshold_pct:.4f}%

[bold]Initial Capital:[/bold] ${summary.initial_capital:,.2f}
[bold]Final Capital:[/bold] ${summary.final_capital:,.2f}
[bold]Total Return:[/bold] {"[green]" if summary.total_return_pct >= 0 else "[red]"}{summary.total_return_pct:+.2f}%[/]
[bold]Total Trades:[/bold] {summary.total_trades}
[bold]Win Rate:[/bold] {summary.win_rate_pct:.1f}%
[bold]Max Drawdown:[/bold] {summary.max_drawdown_pct:.2f}%
"""
        console.print(Panel(summary_text.strip(), title="📈 Stablecoin Backtest Summary", border_style="magenta"))

        table = Table(title="Per-Pair Backtest Stats", box=box.ROUNDED, header_style="bold magenta")
        table.add_column("Pair", width=14)
        table.add_column("Samples", width=8, justify="right")
        table.add_column("Trades", width=8, justify="right")
        table.add_column("Win %", width=8, justify="right")
        table.add_column("Avg Spread %", width=12, justify="right")
        table.add_column("Avg Trade %", width=12, justify="right")
        table.add_column("Net %", width=10, justify="right")

        for item in sorted(stats, key=lambda x: x.net_return_pct, reverse=True):
            color = "green" if item.net_return_pct >= 0 else "red"
            table.add_row(
                item.pair,
                str(item.samples),
                str(item.trades),
                f"{item.win_rate_pct:.1f}",
                f"{item.avg_spread_pct:.4f}",
                f"{item.avg_trade_pct:+.3f}",
                f"[{color}]{item.net_return_pct:+.3f}[/]",
            )
        console.print(table)

        if trades:
            recent = sorted(trades, key=lambda t: t.exit_time, reverse=True)[:15]
            t = Table(
                title="Recent Stablecoin Trades (latest 15)",
                box=box.MINIMAL_DOUBLE_HEAD,
                header_style="bold magenta"
            )
            t.add_column("Pair", width=14)
            t.add_column("Entry (UTC)", width=19)
            t.add_column("Exit (UTC)", width=19)
            t.add_column("Gross %", width=10, justify="right")
            t.add_column("Net %", width=10, justify="right")
            t.add_column("Reason", width=18)
            for trade in recent:
                color = "green" if trade.net_return_pct >= 0 else "red"
                t.add_row(
                    trade.pair,
                    trade.entry_time.strftime("%Y-%m-%d %H:%M"),
                    trade.exit_time.strftime("%Y-%m-%d %H:%M"),
                    f"{trade.gross_capture_pct:+.3f}",
                    f"[{color}]{trade.net_return_pct:+.3f}[/]",
                    trade.exit_reason,
                )
            console.print(t)
        else:
            console.print("[yellow]No stablecoin spread trades were generated in this backtest window.[/yellow]")
