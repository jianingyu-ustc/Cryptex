#!/usr/bin/env python3
"""
Cryptex Trading System - Main Entry Point

Unified command-line interface for both prediction and arbitrage subsystems.

Usage:
    # Prediction System
    python main.py predict                       # Show predictions
    python main.py predict --crypto BTC          # BTC predictions
    python main.py predict --opportunities       # Best opportunities
    python main.py predict --watch               # Continuous monitoring
    python main.py predict --backtest            # Run backtesting
    
    # Arbitrage System
    python main.py arb --formulas                # Show profit formulas
    python main.py arb --scan                    # Scan opportunities
    python main.py arb --funding-rates           # Show funding rates
    python main.py arb --monitor                 # Continuous monitoring
"""

import asyncio
import sys
import argparse


def run_prediction_module(args):
    """Run prediction subsystem"""
    # Build prediction arguments
    pred_args = []
    
    if hasattr(args, 'crypto') and args.crypto:
        pred_args.extend(['--crypto', args.crypto])
    if hasattr(args, 'timeframe') and args.timeframe:
        pred_args.extend(['--timeframe', args.timeframe])
    if hasattr(args, 'opportunities') and args.opportunities:
        pred_args.append('--opportunities')
    if hasattr(args, 'consensus') and args.consensus:
        pred_args.append('--consensus')
    if hasattr(args, 'watch') and args.watch:
        pred_args.append('--watch')
    if hasattr(args, 'interval') and args.interval:
        pred_args.extend(['--interval', str(args.interval)])
    if hasattr(args, 'backtest') and args.backtest:
        pred_args.append('--backtest')
    if hasattr(args, 'hours') and args.hours:
        pred_args.extend(['--hours', str(args.hours)])
    if hasattr(args, 'demo') and args.demo:
        pred_args.append('--demo')
    
    # Update sys.argv and run prediction main
    sys.argv = ['prediction.main'] + pred_args
    
    from prediction import main as pred_main
    asyncio.run(pred_main.main())


def run_arbitrage_module(args):
    """Run arbitrage subsystem"""
    # Build arbitrage arguments
    arb_args = []
    
    if hasattr(args, 'strategy') and args.strategy:
        arb_args.extend(['--strategy', args.strategy])
    if hasattr(args, 'scan') and args.scan:
        arb_args.append('--scan')
    if hasattr(args, 'monitor') and args.monitor:
        arb_args.append('--monitor')
    if hasattr(args, 'auto_execute') and args.auto_execute:
        arb_args.append('--auto-execute')
    if hasattr(args, 'funding_rates') and args.funding_rates:
        arb_args.append('--funding-rates')
    if hasattr(args, 'stablecoin_spreads') and args.stablecoin_spreads:
        arb_args.append('--stablecoin-spreads')
    if hasattr(args, 'formulas') and args.formulas:
        arb_args.append('--formulas')
    if hasattr(args, 'min_profit') and args.min_profit:
        arb_args.extend(['--min-profit', str(args.min_profit)])
    
    # Update sys.argv and run arbitrage main
    sys.argv = ['arbitrage.main'] + arb_args
    
    from arbitrage import main as arb_main
    asyncio.run(arb_main.main())


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Cryptex Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Subsystems:
  predict    Polymarket-based crypto price predictions
  arb        Binance arbitrage trading (funding rate, basis, stablecoin)

Examples:
  python main.py predict                      # Show all predictions
  python main.py predict --opportunities      # Show best opportunities
  python main.py predict --watch              # Continuous monitoring
  python main.py arb --formulas               # Show profit formulas
  python main.py arb --scan                   # Scan arbitrage opportunities
  python main.py arb --funding-rates          # Show current funding rates
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Subsystem to run')
    
    # Prediction subcommand
    pred_parser = subparsers.add_parser('predict', help='Crypto price prediction system')
    pred_parser.add_argument('--crypto', '-c', type=str, help='Cryptocurrency symbol (BTC, ETH, etc.)')
    pred_parser.add_argument('--timeframe', '-t', choices=['5min', '15min', '1hour'], help='Time frame filter')
    pred_parser.add_argument('--opportunities', '-o', action='store_true', help='Show best opportunities')
    pred_parser.add_argument('--consensus', action='store_true', help='Show market consensus')
    pred_parser.add_argument('--watch', '-w', action='store_true', help='Continuous monitoring')
    pred_parser.add_argument('--interval', '-i', type=int, default=30, help='Refresh interval (seconds)')
    pred_parser.add_argument('--backtest', '-b', action='store_true', help='Run backtesting')
    pred_parser.add_argument('--hours', type=int, default=6, help='Hours for backtest')
    pred_parser.add_argument('--demo', action='store_true', help='Use demo data')
    
    # Arbitrage subcommand
    arb_parser = subparsers.add_parser('arb', help='Arbitrage trading system')
    arb_parser.add_argument('--strategy', '-s', choices=['funding', 'basis', 'stablecoin', 'all'],
                           default='all', help='Strategy to use')
    arb_parser.add_argument('--scan', action='store_true', help='Scan for opportunities')
    arb_parser.add_argument('--monitor', action='store_true', help='Continuous monitoring')
    arb_parser.add_argument('--auto-execute', action='store_true', help='Auto execute trades')
    arb_parser.add_argument('--funding-rates', action='store_true', help='Show funding rates')
    arb_parser.add_argument('--stablecoin-spreads', action='store_true', help='Show stablecoin spreads')
    arb_parser.add_argument('--formulas', action='store_true', help='Show profit formulas')
    arb_parser.add_argument('--min-profit', type=float, help='Minimum profit threshold')
    
    args = parser.parse_args()
    
    if args.command == 'predict':
        run_prediction_module(args)
    elif args.command == 'arb':
        run_arbitrage_module(args)
    else:
        # Default: show help
        parser.print_help()
        print("\n\n💡 Quick Start:")
        print("  python main.py predict              # 查看价格预测")
        print("  python main.py arb --formulas       # 查看套利公式")
        print("  python main.py arb --scan           # 扫描套利机会")


if __name__ == "__main__":
    main()