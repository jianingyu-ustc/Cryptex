#!/usr/bin/env python3
"""
Polymarket Crypto Predictor - Main Entry Point

A system that uses Polymarket prediction market data to predict
cryptocurrency price movements in short time frames (5min, 15min, 1hour).

Usage:
    python main.py                    # Run with default settings
    python main.py --crypto BTC       # Get predictions for specific crypto
    python main.py --timeframe 5min   # Filter by time frame
    python main.py --opportunities    # Show best trading opportunities
    python main.py --watch            # Continuous monitoring mode
"""

import asyncio
import argparse
import sys
from datetime import datetime
from typing import Optional

from predictor import CryptoPredictor, PredictionAggregator, TimeFrame, PredictionDirection
from display import PredictionDisplay, console, print_welcome
from config import REFRESH_INTERVAL


async def show_all_predictions(predictor: CryptoPredictor, display: PredictionDisplay):
    """Show all available predictions"""
    display.print_loading("Fetching all crypto predictions...")
    
    try:
        # Get market summary
        summary = await predictor.get_market_summary()
        display.print_summary(summary)
        
        # Get all predictions
        all_predictions = await predictor.get_all_short_term_predictions()
        
        if not all_predictions:
            console.print("\n[yellow]No short-term crypto prediction markets found.[/]")
            console.print("This could mean:")
            console.print("  - No active 5min/15min/1hour markets currently exist")
            console.print("  - Markets are using different time frame terminology")
            console.print("\nTry running with --all to see all crypto markets.")
            return
        
        # Display predictions by crypto
        for crypto, predictions in sorted(all_predictions.items()):
            display.print_predictions(predictions, f"📊 {crypto} Predictions")
            
            # Show consensus for this crypto
            consensus = PredictionAggregator.get_consensus(predictions)
            if consensus["sample_size"] > 0:
                direction = consensus["direction"]
                color = "green" if direction == "UP" else "red" if direction == "DOWN" else "yellow"
                console.print(f"  └─ Consensus: [{color}]{direction}[/] "
                            f"(Confidence: {consensus['confidence']*100:.0f}%, "
                            f"Agreement: {consensus['agreement']*100:.0f}%)\n")
        
        display.print_timestamp()
        
    except Exception as e:
        display.print_error(f"Failed to fetch predictions: {str(e)}")
        raise


async def show_crypto_predictions(
    predictor: CryptoPredictor, 
    display: PredictionDisplay,
    crypto: str,
    time_frame: Optional[TimeFrame] = None
):
    """Show predictions for a specific cryptocurrency"""
    display.print_loading(f"Fetching {crypto} predictions...")
    
    try:
        predictions = await predictor.get_predictions_for_crypto(crypto, time_frame)
        
        if not predictions:
            console.print(f"\n[yellow]No prediction markets found for {crypto}[/]")
            if time_frame:
                console.print(f"  (Filtered by time frame: {time_frame.value})")
            console.print("\nAvailable cryptos: BTC, ETH, SOL, DOGE, XRP, BNB, ADA, AVAX, MATIC, DOT")
            return
        
        title = f"📊 {crypto} Predictions"
        if time_frame:
            title += f" ({time_frame.value})"
        
        display.print_predictions(predictions, title)
        
        # Show consensus
        consensus = PredictionAggregator.get_consensus(predictions)
        if consensus["sample_size"] > 0:
            direction = consensus["direction"]
            color = "green" if direction == "UP" else "red" if direction == "DOWN" else "yellow"
            console.print(f"\n📈 Market Consensus: [{color}]{direction}[/]")
            console.print(f"   Confidence: {consensus['confidence']*100:.1f}%")
            console.print(f"   Agreement: {consensus['agreement']*100:.1f}%")
            console.print(f"   Sample Size: {consensus['sample_size']} markets")
        
        display.print_timestamp()
        
    except Exception as e:
        display.print_error(f"Failed to fetch {crypto} predictions: {str(e)}")
        raise


async def show_opportunities(
    predictor: CryptoPredictor, 
    display: PredictionDisplay,
    min_confidence: float = 0.3
):
    """Show best trading opportunities"""
    display.print_loading("Analyzing market opportunities...")
    
    try:
        opportunities = await predictor.get_best_opportunities(
            min_confidence=min_confidence,
            min_probability_deviation=0.1
        )
        
        if not opportunities:
            console.print("\n[yellow]No high-confidence opportunities found.[/]")
            console.print("Try lowering the confidence threshold with --min-confidence")
            return
        
        display.print_opportunities(opportunities, top_n=10)
        
        # Also show as table for detailed view
        display.print_predictions(opportunities[:10], "🎯 Top 10 Opportunities (Detailed)")
        display.print_timestamp()
        
    except Exception as e:
        display.print_error(f"Failed to analyze opportunities: {str(e)}")
        raise


async def show_all_crypto_markets(predictor: CryptoPredictor, display: PredictionDisplay):
    """Show all crypto markets (not just short-term)"""
    display.print_loading("Fetching all crypto markets from Polymarket...")
    
    try:
        markets = await predictor._get_all_crypto_markets(force_refresh=True)
        
        if not markets:
            console.print("\n[yellow]No crypto markets found on Polymarket.[/]")
            return
        
        from rich.table import Table
        from rich import box
        
        table = Table(
            title=f"📊 All Crypto Markets ({len(markets)} found)",
            box=box.ROUNDED,
            header_style="bold cyan"
        )
        
        table.add_column("#", width=4)
        table.add_column("Question", width=60, overflow="fold")
        table.add_column("Volume 24h", width=12)
        table.add_column("Liquidity", width=12)
        table.add_column("Status", width=10)
        
        for i, market in enumerate(markets[:50], 1):  # Show top 50
            question = market.get("question", "N/A")
            volume = float(market.get("volume24hr", 0) or 0)
            liquidity = float(market.get("liquidity", 0) or 0)
            active = market.get("active", False)
            
            status = "[green]Active[/]" if active else "[red]Closed[/]"
            
            table.add_row(
                str(i),
                question[:100] + "..." if len(question) > 100 else question,
                display.format_volume(volume),
                display.format_volume(liquidity),
                status
            )
        
        console.print(table)
        
        if len(markets) > 50:
            console.print(f"\n[dim]Showing 50 of {len(markets)} markets[/]")
        
        display.print_timestamp()
        
    except Exception as e:
        display.print_error(f"Failed to fetch markets: {str(e)}")
        raise


async def watch_mode(
    predictor: CryptoPredictor, 
    display: PredictionDisplay,
    crypto: Optional[str] = None,
    interval: int = REFRESH_INTERVAL
):
    """Continuous monitoring mode"""
    console.print(f"\n[bold cyan]👁️  Watch Mode Active[/] (Refresh every {interval}s)")
    console.print("[dim]Press Ctrl+C to exit[/]\n")
    
    try:
        while True:
            console.clear()
            display.print_header()
            
            if crypto:
                await show_crypto_predictions(predictor, display, crypto)
            else:
                await show_opportunities(predictor, display, min_confidence=0.2)
            
            console.print(f"\n[dim]Next refresh in {interval} seconds... (Ctrl+C to exit)[/]")
            await asyncio.sleep(interval)
            
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Watch mode stopped.[/]")


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Polymarket Crypto Price Predictor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          Show all short-term predictions
  python main.py --crypto BTC             Show BTC predictions only
  python main.py --crypto ETH --tf 1hour  Show ETH 1-hour predictions
  python main.py --opportunities          Show best trading opportunities
  python main.py --all                    Show all crypto markets
  python main.py --watch                  Continuous monitoring
  python main.py --watch --crypto BTC     Watch BTC predictions
        """
    )
    
    parser.add_argument(
        "--crypto", "-c",
        type=str,
        help="Cryptocurrency symbol (BTC, ETH, SOL, etc.)"
    )
    
    parser.add_argument(
        "--timeframe", "--tf",
        type=str,
        choices=["5min", "15min", "1hour"],
        help="Time frame filter"
    )
    
    parser.add_argument(
        "--opportunities", "-o",
        action="store_true",
        help="Show best trading opportunities"
    )
    
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Show all crypto markets (not just short-term)"
    )
    
    parser.add_argument(
        "--watch", "-w",
        action="store_true",
        help="Enable continuous monitoring mode"
    )
    
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=REFRESH_INTERVAL,
        help=f"Refresh interval in seconds for watch mode (default: {REFRESH_INTERVAL})"
    )
    
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.3,
        help="Minimum confidence threshold for opportunities (0-1, default: 0.3)"
    )
    
    parser.add_argument(
        "--include-settled",
        action="store_true",
        help="Include settled/historical markets (default: only show active future markets)"
    )
    
    return parser.parse_args()


async def main():
    """Main entry point"""
    args = parse_args()
    
    # Create predictor with include_settled option
    predictor = CryptoPredictor(include_settled=args.include_settled)
    display = PredictionDisplay()
    
    # Print welcome header
    print_welcome()
    
    # Parse time frame if provided
    time_frame = None
    if args.timeframe:
        tf_map = {"5min": TimeFrame.FIVE_MIN, "15min": TimeFrame.FIFTEEN_MIN, "1hour": TimeFrame.ONE_HOUR}
        time_frame = tf_map.get(args.timeframe)
    
    try:
        if args.watch:
            # Watch mode
            await watch_mode(predictor, display, args.crypto, args.interval)
        
        elif args.all:
            # Show all crypto markets
            await show_all_crypto_markets(predictor, display)
        
        elif args.opportunities:
            # Show opportunities
            await show_opportunities(predictor, display, args.min_confidence)
        
        elif args.crypto:
            # Show specific crypto
            await show_crypto_predictions(predictor, display, args.crypto.upper(), time_frame)
        
        else:
            # Default: show all predictions
            await show_all_predictions(predictor, display)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled.[/]")
        sys.exit(0)
    except Exception as e:
        display.print_error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())