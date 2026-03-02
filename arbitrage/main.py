#!/usr/bin/env python3
"""
Unified Arbitrage Trading System - Main Entry Point

Supports three arbitrage strategies:
1. Funding Rate Arbitrage (Perpetual Funding)
2. Cash & Carry Arbitrage (Basis)
3. Stablecoin Spread Arbitrage

Usage:
    python -m arbitrage.main                    # Run all strategies
    python -m arbitrage.main --strategy funding # Run specific strategy
    python -m arbitrage.main --scan             # Scan for opportunities
    python -m arbitrage.main --monitor          # Continuous monitoring
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime
from typing import List, Dict

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich import box

from .config import ArbitrageConfig, DEFAULT_CONFIG
from .api import BinanceClient, create_client
from .strategy import (
    StrategyManager, 
    FundingRateStrategy,
    BasisArbitrageStrategy,
    StablecoinSpreadStrategy,
    ArbitrageSignal
)
from .execution import ExecutionEngine
from .risk import RiskManager, RiskLevel

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('arbitrage.log')
    ]
)
logger = logging.getLogger(__name__)

console = Console()


class ArbitrageDisplay:
    """Rich display for arbitrage system"""
    
    @staticmethod
    def print_header():
        """Print application header"""
        header = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                                                               ║
║    💰 UNIFIED ARBITRAGE TRADING SYSTEM 💰                                     ║
║    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                        ║
║    Funding Rate | Basis Arbitrage | Stablecoin Spread                         ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""
        console.print(header, style="bold cyan")
    
    @staticmethod
    def print_formulas():
        """Print arbitrage profit formulas"""
        formulas = """
[bold cyan]📊 套利收益公式[/bold cyan]

[bold green]1️⃣ 资金费率套利 (Funding Rate Arbitrage)[/bold green]
   净收益 = Position × [FR - 0.10%]
   条件: 资金费率 > 0.03% (推荐 > 0.20%)

[bold yellow]2️⃣ 期现套利 (Cash & Carry Arbitrage)[/bold yellow]
   年化收益 = [(期货价 - 现货价) / 现货价] × (365 / 到期天数) × 100%
   净年化 = 年化收益 - 年化交易成本
   条件: 净年化 > 15%

[bold magenta]3️⃣ 稳定币套利 (Stablecoin Spread Arbitrage)[/bold magenta]
   净收益 = 价差 - 0.10%
   条件: 价差 > 0.50%

[dim]交易成本: Taker 0.04% + Slippage 0.01% = 0.05%/单向, 0.10%/双向[/dim]
"""
        console.print(Panel(formulas, title="Profit Formulas", border_style="blue"))
    
    @staticmethod
    def create_signals_table(signals: List[ArbitrageSignal], title: str = "Arbitrage Opportunities") -> Table:
        """Create table for arbitrage signals"""
        table = Table(
            title=title,
            box=box.ROUNDED,
            header_style="bold cyan",
            show_lines=True
        )
        
        table.add_column("Strategy", width=20)
        table.add_column("Symbol", width=12)
        table.add_column("Signal", width=12)
        table.add_column("Gross %", width=10)
        table.add_column("Net %", width=10)
        table.add_column("Confidence", width=12)
        table.add_column("Reason", width=40, overflow="fold")
        
        for signal in signals:
            # Color based on profitability
            net_color = "green" if signal.net_profit_pct > 0 else "red"
            conf_color = "green" if signal.confidence > 0.7 else "yellow" if signal.confidence > 0.5 else "red"
            
            table.add_row(
                signal.strategy_name,
                signal.symbol,
                signal.signal_type.value,
                f"{signal.expected_profit_pct:.4f}%",
                f"[{net_color}]{signal.net_profit_pct:.4f}%[/]",
                f"[{conf_color}]{signal.confidence:.0%}[/]",
                signal.reason
            )
        
        return table
    
    @staticmethod
    def create_funding_rates_table(rates: Dict[str, float]) -> Table:
        """Create table for funding rates"""
        table = Table(
            title="📈 Current Funding Rates",
            box=box.ROUNDED,
            header_style="bold green"
        )
        
        table.add_column("Symbol", width=12)
        table.add_column("Rate", width=12)
        table.add_column("Status", width=15)
        
        for symbol, rate in sorted(rates.items(), key=lambda x: x[1], reverse=True):
            rate_color = "green" if rate > 0.03 else "yellow" if rate > 0 else "red"
            status = "✅ Profitable" if rate > 0.1 else "⚠️ Marginal" if rate > 0 else "❌ Negative"
            
            table.add_row(
                symbol,
                f"[{rate_color}]{rate:.4f}%[/]",
                status
            )
        
        return table
    
    @staticmethod
    def create_risk_status_panel(risk_status: Dict) -> Panel:
        """Create panel for risk status"""
        risk_level = risk_status.get("risk_level", "UNKNOWN")
        
        level_colors = {
            "LOW": "green",
            "MEDIUM": "yellow", 
            "HIGH": "red",
            "CRITICAL": "bold red"
        }
        level_color = level_colors.get(risk_level, "white")
        
        content = f"""
[bold]Risk Level:[/bold] [{level_color}]{risk_level}[/]
[bold]Total Equity:[/bold] ${risk_status.get('total_equity', 0):,.2f}
[bold]Position Ratio:[/bold] {risk_status.get('position_ratio', 0):.1f}%
[bold]Margin Ratio:[/bold] {risk_status.get('margin_ratio', 0):.1f}%
[bold]Drawdown:[/bold] {risk_status.get('current_drawdown', 0):.2f}%
[bold]Positions:[/bold] {risk_status.get('num_positions', 0)}
"""
        return Panel(content, title="⚠️ Risk Status", border_style=level_color)


class ArbitrageSystem:
    """Main arbitrage system controller"""
    
    def __init__(self, config: ArbitrageConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.client: Optional[BinanceClient] = None
        self.strategy_manager: Optional[StrategyManager] = None
        self.execution_engine: Optional[ExecutionEngine] = None
        self.risk_manager: Optional[RiskManager] = None
        self.display = ArbitrageDisplay()
        self._running = False
    
    async def initialize(self):
        """Initialize all components"""
        console.print("🔄 Initializing arbitrage system...", style="dim")
        
        # Create API client
        self.client = create_client(self.config)
        
        # Test connectivity
        if not await self.client.test_connectivity():
            console.print("❌ Failed to connect to Binance API", style="bold red")
            return False
        
        console.print("✅ Connected to Binance API", style="green")
        
        # Initialize components
        self.strategy_manager = StrategyManager(self.client, self.config)
        self.execution_engine = ExecutionEngine(self.client, self.config)
        self.risk_manager = RiskManager(self.client, self.config)
        
        console.print("✅ System initialized", style="green")
        return True
    
    async def shutdown(self):
        """Shutdown all components"""
        console.print("🔄 Shutting down...", style="dim")
        self._running = False
        
        if self.risk_manager:
            self.risk_manager.stop_monitoring()
        
        if self.client:
            await self.client.close()
        
        console.print("✅ Shutdown complete", style="green")
    
    async def scan_opportunities(self, min_profit: float = 0) -> List[ArbitrageSignal]:
        """Scan for arbitrage opportunities"""
        console.print("🔍 Scanning for opportunities...", style="dim")
        
        signals = await self.strategy_manager.get_best_opportunities(min_profit)
        
        if signals:
            table = self.display.create_signals_table(signals)
            console.print(table)
        else:
            console.print("No profitable opportunities found", style="yellow")
        
        return signals
    
    async def show_funding_rates(self):
        """Display current funding rates"""
        funding_strategy = self.strategy_manager.strategies.get("funding_rate")
        
        if funding_strategy:
            await funding_strategy.analyze()
            rates = funding_strategy.get_current_funding_rates()
            
            if rates:
                table = self.display.create_funding_rates_table(rates)
                console.print(table)
            else:
                console.print("No funding rate data available", style="yellow")
    
    async def show_stablecoin_spreads(self):
        """Display current stablecoin spreads"""
        stablecoin_strategy = self.strategy_manager.strategies.get("stablecoin")
        
        if stablecoin_strategy:
            await stablecoin_strategy.analyze()
            spreads = stablecoin_strategy.get_current_spreads()
            
            if spreads:
                table = Table(
                    title="💵 Stablecoin Spreads",
                    box=box.ROUNDED,
                    header_style="bold magenta"
                )
                
                table.add_column("High", width=10)
                table.add_column("Low", width=10)
                table.add_column("Spread", width=12)
                table.add_column("Net Profit", width=12)
                table.add_column("Status", width=15)
                
                for s in spreads[:10]:
                    net_profit = s["spread_pct"] - 0.10  # Minus trading costs
                    status = "✅ Profitable" if net_profit > 0 else "❌ Unprofitable"
                    color = "green" if net_profit > 0 else "red"
                    
                    table.add_row(
                        s["coin_high"],
                        s["coin_low"],
                        f"{s['spread_pct']:.4f}%",
                        f"[{color}]{net_profit:.4f}%[/]",
                        status
                    )
                
                console.print(table)
            else:
                console.print("No stablecoin spread data available", style="yellow")
    
    async def execute_opportunity(self, signal: ArbitrageSignal) -> bool:
        """Execute an arbitrage opportunity"""
        # Check risk first
        can_open, reason = await self.risk_manager.can_open_position(
            signal.symbol,
            signal.quantity * signal.price if signal.quantity > 0 else 1000,
            signal.strategy_name
        )
        
        if not can_open:
            console.print(f"❌ Risk check failed: {reason}", style="red")
            return False
        
        console.print(f"🚀 Executing: {signal.strategy_name} - {signal.symbol}", style="bold")
        
        # Execute through execution engine
        result = await self.execution_engine.execute_signal(signal)
        
        if result.status.value == "FILLED":
            console.print(f"✅ Execution successful", style="green")
            return True
        else:
            console.print(f"❌ Execution failed: {result.error_message}", style="red")
            return False
    
    async def monitor_loop(self, auto_execute: bool = False):
        """Continuous monitoring loop"""
        self._running = True
        
        console.print("\n👁️ Starting monitoring mode...", style="bold cyan")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
        
        # Start risk monitoring in background
        asyncio.create_task(
            self.risk_manager.start_monitoring(
                lambda: self.execution_engine._positions
            )
        )
        
        while self._running:
            try:
                console.clear()
                self.display.print_header()
                
                # Show risk status
                risk_status = self.risk_manager.get_status()
                console.print(self.display.create_risk_status_panel(risk_status))
                
                # Scan for opportunities
                signals = await self.strategy_manager.get_best_opportunities(min_profit=0)
                
                if signals:
                    table = self.display.create_signals_table(signals[:10], "🎯 Top Opportunities")
                    console.print(table)
                    
                    # Auto-execute if enabled and profitable
                    if auto_execute:
                        profitable_signals = [s for s in signals if s.is_profitable() and s.confidence > 0.7]
                        
                        for signal in profitable_signals[:1]:  # Execute top signal only
                            console.print(f"\n🤖 Auto-executing: {signal.symbol}...", style="yellow")
                            await self.execute_opportunity(signal)
                else:
                    console.print("\n[yellow]No opportunities found[/yellow]")
                
                # Show timestamp
                console.print(f"\n[dim]Last update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                console.print(f"[dim]Next refresh in {self.config.strategy_check_interval}s...[/dim]")
                
                await asyncio.sleep(self.config.strategy_check_interval)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(5)
        
        console.print("\n[yellow]Monitoring stopped[/yellow]")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Unified Arbitrage Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--strategy", "-s",
        choices=["funding", "basis", "stablecoin", "all"],
        default="all",
        help="Strategy to run"
    )
    
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan for opportunities"
    )
    
    parser.add_argument(
        "--monitor", "-m",
        action="store_true",
        help="Continuous monitoring mode"
    )
    
    parser.add_argument(
        "--auto-execute",
        action="store_true",
        help="Auto-execute profitable opportunities"
    )
    
    parser.add_argument(
        "--funding-rates",
        action="store_true",
        help="Show current funding rates"
    )
    
    parser.add_argument(
        "--stablecoin-spreads",
        action="store_true",
        help="Show stablecoin spreads"
    )
    
    parser.add_argument(
        "--formulas",
        action="store_true",
        help="Show profit formulas"
    )
    
    parser.add_argument(
        "--min-profit",
        type=float,
        default=0,
        help="Minimum net profit threshold"
    )
    
    args = parser.parse_args()
    
    # Create system
    system = ArbitrageSystem()
    display = ArbitrageDisplay()
    
    # Print header
    display.print_header()
    
    # Show formulas if requested
    if args.formulas:
        display.print_formulas()
        return
    
    try:
        # Initialize
        if not await system.initialize():
            console.print("❌ Initialization failed", style="bold red")
            sys.exit(1)
        
        # Execute based on arguments
        if args.funding_rates:
            await system.show_funding_rates()
        
        elif args.stablecoin_spreads:
            await system.show_stablecoin_spreads()
        
        elif args.scan:
            await system.scan_opportunities(args.min_profit)
        
        elif args.monitor:
            await system.monitor_loop(auto_execute=args.auto_execute)
        
        else:
            # Default: show formulas and scan
            display.print_formulas()
            await system.scan_opportunities()
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled[/yellow]")
    
    finally:
        await system.shutdown()


if __name__ == "__main__":
    asyncio.run(main())