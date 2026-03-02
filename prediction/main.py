#!/usr/bin/env python3
"""
Polymarket Crypto Predictor - Main Entry Point

Usage:
    python -m prediction.main                    # Show all predictions
    python -m prediction.main --crypto BTC       # Show BTC predictions
    python -m prediction.main --opportunities    # Show best opportunities
    python -m prediction.main --watch            # Continuous monitoring
    python -m prediction.main --backtest         # Run backtesting
"""

import asyncio
import argparse
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.live import Live

from .predictor import CryptoPredictor, PredictionAggregator, TimeFrame
from .display import PredictionDisplay, print_welcome
from .backtest import Backtester


console = Console()


async def show_predictions(crypto: str = None, time_frame: str = None):
    """Display predictions for crypto(s)"""
    display = PredictionDisplay()
    predictor = CryptoPredictor()
    
    display.print_loading("Fetching market data...")
    
    if crypto:
        # Single crypto
        tf = None
        if time_frame:
            tf_map = {"5min": TimeFrame.FIVE_MIN, "15min": TimeFrame.FIFTEEN_MIN, "1hour": TimeFrame.ONE_HOUR}
            tf = tf_map.get(time_frame)
        
        predictions = await predictor.get_predictions_for_crypto(crypto.upper(), tf)
        display.print_predictions(predictions, f"{crypto.upper()} Predictions")
    else:
        # All cryptos
        all_predictions = await predictor.get_all_short_term_predictions()
        
        for crypto_sym, predictions in all_predictions.items():
            display.print_predictions(predictions, f"{crypto_sym} Predictions")
        
        # Show summary
        summary = await predictor.get_market_summary()
        display.print_summary(summary)
    
    display.print_timestamp()


async def show_opportunities(min_confidence: float = 0.5):
    """Display best trading opportunities"""
    display = PredictionDisplay()
    predictor = CryptoPredictor()
    
    display.print_loading("Scanning for opportunities...")
    
    opportunities = await predictor.get_best_opportunities(
        min_confidence=min_confidence,
        min_probability_deviation=0.10
    )
    
    display.print_opportunities(opportunities, top_n=10)
    display.print_timestamp()


async def show_consensus():
    """Display market consensus"""
    display = PredictionDisplay()
    predictor = CryptoPredictor()
    
    display.print_loading("Calculating consensus...")
    
    all_predictions = await predictor.get_all_short_term_predictions()
    
    consensus_data = {}
    for crypto, predictions in all_predictions.items():
        consensus = PredictionAggregator.get_consensus(predictions)
        consensus_data[crypto] = consensus
    
    panel = display.create_consensus_panel(consensus_data)
    console.print(panel)
    display.print_timestamp()


async def watch_mode(interval: int = 30, crypto: str = None):
    """Continuous monitoring mode"""
    display = PredictionDisplay()
    predictor = CryptoPredictor()
    
    console.print(f"\n[bold cyan]👀 Watch Mode[/bold cyan] - Refreshing every {interval} seconds")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")
    
    try:
        while True:
            console.clear()
            display.print_header()
            
            if crypto:
                predictions = await predictor.get_predictions_for_crypto(crypto.upper())
                display.print_predictions(predictions, f"[LIVE] {crypto.upper()} Predictions")
            else:
                opportunities = await predictor.get_best_opportunities(min_confidence=0.4)
                display.print_opportunities(opportunities, top_n=8)
                
                summary = await predictor.get_market_summary()
                display.print_summary(summary)
            
            display.print_timestamp()
            console.print(f"\n[dim]Next refresh in {interval} seconds...[/dim]")
            
            await asyncio.sleep(interval)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch mode stopped.[/yellow]")


async def run_backtest(hours: int = 6, cryptos: str = "btc,eth,sol", demo: bool = False):
    """Run backtesting"""
    crypto_list = [c.strip() for c in cryptos.split(",")]
    
    backtester = Backtester()
    
    if demo:
        await backtester.run_demo_backtest(num_predictions=hours * 12)
    else:
        await backtester.run_backtest(cryptos=crypto_list, hours_back=hours)
        
        if not backtester.results:
            console.print("\n[yellow]No historical data found. Running demo backtest instead...[/yellow]\n")
            await backtester.run_demo_backtest(num_predictions=hours * 12)
    
    backtester.display_results()


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Polymarket Crypto Predictor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m prediction.main                     # Show all predictions
  python -m prediction.main --crypto BTC        # Show BTC predictions only
  python -m prediction.main --crypto ETH --timeframe 5min
  python -m prediction.main --opportunities     # Show best opportunities
  python -m prediction.main --consensus         # Show market consensus
  python -m prediction.main --watch             # Continuous monitoring
  python -m prediction.main --watch --interval 60
  python -m prediction.main --backtest          # Run backtesting
  python -m prediction.main --backtest --hours 12
        """
    )
    
    parser.add_argument("--crypto", "-c", type=str, help="Cryptocurrency symbol (BTC, ETH, SOL, etc.)")
    parser.add_argument("--timeframe", "-t", type=str, choices=["5min", "15min", "1hour"], help="Time frame filter")
    parser.add_argument("--opportunities", "-o", action="store_true", help="Show best trading opportunities")
    parser.add_argument("--consensus", action="store_true", help="Show market consensus")
    parser.add_argument("--watch", "-w", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--interval", "-i", type=int, default=30, help="Refresh interval for watch mode (seconds)")
    parser.add_argument("--backtest", "-b", action="store_true", help="Run backtesting")
    parser.add_argument("--hours", type=int, default=6, help="Hours of history for backtest")
    parser.add_argument("--demo", action="store_true", help="Use demo data for backtest")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="Minimum confidence for opportunities")
    
    args = parser.parse_args()
    
    # Print welcome
    print_welcome()
    
    try:
        if args.backtest:
            await run_backtest(
                hours=args.hours,
                cryptos=args.crypto or "btc,eth,sol",
                demo=args.demo
            )
        elif args.watch:
            await watch_mode(interval=args.interval, crypto=args.crypto)
        elif args.opportunities:
            await show_opportunities(min_confidence=args.min_confidence)
        elif args.consensus:
            await show_consensus()
        else:
            await show_predictions(crypto=args.crypto, time_frame=args.timeframe)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled.[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]Error: {e}[/bold red]")
        raise


if __name__ == "__main__":
    asyncio.run(main())