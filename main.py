#!/usr/bin/env python3
"""
Cryptex Trading System - Main Entry Point

Unified command-line interface for prediction, arbitrage and spot-trading subsystems.

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
    python main.py arb --backtest                # Run arbitrage backtest

    # Spot Trading System
    python main.py spot --scan                   # Scan spot signals
    python main.py spot --monitor --auto-execute # Continuous auto trading
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
    if hasattr(args, 'backtest') and args.backtest:
        arb_args.append('--backtest')
    if hasattr(args, 'hours') and args.hours:
        arb_args.extend(['--hours', str(args.hours)])
    if hasattr(args, 'symbols') and args.symbols:
        arb_args.extend(['--symbols', args.symbols])
    if hasattr(args, 'initial_capital') and args.initial_capital:
        arb_args.extend(['--initial-capital', str(args.initial_capital)])
    
    # Update sys.argv and run arbitrage main
    sys.argv = ['arbitrage.main'] + arb_args
    
    from arbitrage import main as arb_main
    asyncio.run(arb_main.main())


def run_spot_module(args):
    """Run spot auto-trading subsystem"""
    spot_args = []

    if hasattr(args, 'symbols') and args.symbols:
        spot_args.extend(['--symbols', args.symbols])
    if hasattr(args, 'scan') and args.scan:
        spot_args.append('--scan')
    if hasattr(args, 'monitor') and args.monitor:
        spot_args.append('--monitor')
    if hasattr(args, 'backtest') and args.backtest:
        spot_args.append('--backtest')
    if hasattr(args, 'backtest_years') and args.backtest_years is not None:
        spot_args.extend(['--backtest-years', str(args.backtest_years)])
    if hasattr(args, 'backtest_start') and args.backtest_start:
        spot_args.extend(['--backtest-start', str(args.backtest_start)])
    if hasattr(args, 'backtest_end') and args.backtest_end:
        spot_args.extend(['--backtest-end', str(args.backtest_end)])
    if hasattr(args, 'backtest_sleep') and args.backtest_sleep is not None:
        spot_args.extend(['--backtest-sleep', str(args.backtest_sleep)])
    if hasattr(args, 'auto_execute') and args.auto_execute:
        spot_args.append('--auto-execute')
    if hasattr(args, 'live') and args.live:
        spot_args.append('--live')
    if hasattr(args, 'interval') and args.interval is not None:
        spot_args.extend(['--interval', str(args.interval)])
    if hasattr(args, 'initial_capital') and args.initial_capital is not None:
        spot_args.extend(['--initial-capital', str(args.initial_capital)])
    if hasattr(args, 'usdt_per_trade') and args.usdt_per_trade is not None:
        spot_args.extend(['--usdt-per-trade', str(args.usdt_per_trade)])
    if hasattr(args, 'max_positions') and args.max_positions is not None:
        spot_args.extend(['--max-positions', str(args.max_positions)])
    if hasattr(args, 'kline_interval') and args.kline_interval is not None:
        spot_args.extend(['--kline-interval', args.kline_interval])
    if hasattr(args, 'stop_loss') and args.stop_loss is not None:
        spot_args.extend(['--stop-loss', str(args.stop_loss)])
    if hasattr(args, 'take_profit') and args.take_profit is not None:
        spot_args.extend(['--take-profit', str(args.take_profit)])
    if hasattr(args, 'rsi_buy_min') and args.rsi_buy_min is not None:
        spot_args.extend(['--rsi-buy-min', str(args.rsi_buy_min)])
    if hasattr(args, 'rsi_buy_max') and args.rsi_buy_max is not None:
        spot_args.extend(['--rsi-buy-max', str(args.rsi_buy_max)])
    if hasattr(args, 'atr_k') and args.atr_k is not None:
        spot_args.extend(['--atr-k', str(args.atr_k)])
    if hasattr(args, 'trail_atr_k') and args.trail_atr_k is not None:
        spot_args.extend(['--trail-atr-k', str(args.trail_atr_k)])
    if hasattr(args, 'adx_min') and args.adx_min is not None:
        spot_args.extend(['--adx-min', str(args.adx_min)])
    if hasattr(args, 'trend_strength_min') and args.trend_strength_min is not None:
        spot_args.extend(['--trend-strength-min', str(args.trend_strength_min)])
    if hasattr(args, 'risk_per_trade_pct') and args.risk_per_trade_pct is not None:
        spot_args.extend(['--risk-per-trade-pct', str(args.risk_per_trade_pct)])
    if hasattr(args, 'fee_bps') and args.fee_bps is not None:
        spot_args.extend(['--fee-bps', str(args.fee_bps)])
    if hasattr(args, 'slippage_bps') and args.slippage_bps is not None:
        spot_args.extend(['--slippage-bps', str(args.slippage_bps)])
    if hasattr(args, 'max_total_exposure_pct') and args.max_total_exposure_pct is not None:
        spot_args.extend(['--max-total-exposure-pct', str(args.max_total_exposure_pct)])
    if hasattr(args, 'daily_loss_limit_pct') and args.daily_loss_limit_pct is not None:
        spot_args.extend(['--daily-loss-limit-pct', str(args.daily_loss_limit_pct)])
    if hasattr(args, 'cooldown_bars') and args.cooldown_bars is not None:
        spot_args.extend(['--cooldown-bars', str(args.cooldown_bars)])

    sys.argv = ['spot.main'] + spot_args

    from spot import main as spot_main
    asyncio.run(spot_main.main())


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Cryptex Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Subsystems:
  predict    Polymarket-based crypto price predictions
  arb        Binance arbitrage trading (funding rate, basis, stablecoin)
  spot       Binance spot auto trading

Examples:
  python main.py predict                      # Show all predictions
  python main.py predict --opportunities      # Show best opportunities
  python main.py predict --watch              # Continuous monitoring
  python main.py arb --formulas               # Show profit formulas
  python main.py arb --scan                   # Scan arbitrage opportunities
  python main.py arb --funding-rates          # Show current funding rates
  python main.py arb --backtest --hours 168   # Run funding-rate backtest
  python main.py spot --monitor --auto-execute # Run spot auto trading
  python main.py spot --backtest --backtest-years 3 # Run spot backtest
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
    arb_parser.add_argument('--backtest', '-b', action='store_true', help='Run arbitrage backtest')
    arb_parser.add_argument('--hours', type=int, default=168, help='Hours of history for backtest')
    arb_parser.add_argument('--symbols', type=str, help='Backtest symbols, comma-separated')
    arb_parser.add_argument('--initial-capital', type=float, default=10000.0, help='Initial capital for backtest')

    # Spot subcommand
    spot_parser = subparsers.add_parser('spot', help='Spot auto trading system')
    spot_parser.add_argument('--symbols', type=str, help='Trading symbols, comma-separated')
    spot_parser.add_argument('--scan', action='store_true', help='Single scan mode')
    spot_parser.add_argument('--monitor', action='store_true', help='Continuous monitoring')
    spot_parser.add_argument('--backtest', '-b', action='store_true', help='Run historical backtest mode')
    spot_parser.add_argument('--backtest-years', type=int, default=3, help='Backtest years (minimum 3)')
    spot_parser.add_argument('--backtest-start', type=str, help='Backtest start UTC date/time (ISO)')
    spot_parser.add_argument('--backtest-end', type=str, help='Backtest end UTC date/time (ISO)')
    spot_parser.add_argument('--backtest-sleep', type=float, default=0.0, help='Sleep seconds per backtest bar')
    spot_parser.add_argument('--auto-execute', action='store_true', help='Auto execute spot signals')
    spot_parser.add_argument('--live', action='store_true', help='Live trading mode (default dry-run)')
    spot_parser.add_argument('--interval', type=int, default=30, help='Refresh interval (seconds)')
    spot_parser.add_argument('--initial-capital', type=float, default=10000.0, help='Initial capital (USDT)')
    spot_parser.add_argument('--usdt-per-trade', type=float, default=100.0, help='USDT per trade')
    spot_parser.add_argument('--max-positions', type=int, default=3, help='Maximum open positions')
    spot_parser.add_argument('--kline-interval', type=str, default='15m', help='Signal kline interval')
    spot_parser.add_argument('--stop-loss', type=float, default=2.0, help='Stop loss (%%)')
    spot_parser.add_argument('--take-profit', type=float, default=4.0, help='Take profit (%%)')
    spot_parser.add_argument('--rsi-buy-min', type=float, default=45.0, help='RSI buy lower bound')
    spot_parser.add_argument('--rsi-buy-max', type=float, default=68.0, help='RSI buy upper bound')
    spot_parser.add_argument('--atr-k', type=float, default=2.0, help='ATR stop multiplier')
    spot_parser.add_argument('--trail-atr-k', type=float, default=2.5, help='ATR trailing multiplier')
    spot_parser.add_argument('--adx-min', type=float, default=18.0, help='ADX minimum threshold')
    spot_parser.add_argument('--trend-strength-min', type=float, default=0.003, help='Trend strength proxy threshold')
    spot_parser.add_argument('--risk-per-trade-pct', type=float, default=0.5, help='Risk per trade (%% of equity)')
    spot_parser.add_argument('--fee-bps', type=float, default=10.0, help='Fee in bps')
    spot_parser.add_argument('--slippage-bps', type=float, default=10.0, help='Slippage in bps')
    spot_parser.add_argument('--max-total-exposure-pct', type=float, default=80.0, help='Max total exposure (%%)')
    spot_parser.add_argument('--daily-loss-limit-pct', type=float, default=3.0, help='Daily loss limit (%%)')
    spot_parser.add_argument('--cooldown-bars', type=int, default=2, help='Bars to wait after SELL')
    
    args = parser.parse_args()
    
    if args.command == 'predict':
        run_prediction_module(args)
    elif args.command == 'arb':
        run_arbitrage_module(args)
    elif args.command == 'spot':
        run_spot_module(args)
    else:
        # Default: show help
        parser.print_help()
        print("\n\n💡 Quick Start:")
        print("  python main.py predict              # 查看价格预测")
        print("  python main.py arb --formulas       # 查看套利公式")
        print("  python main.py arb --scan           # 扫描套利机会")
        print("  python main.py spot --scan          # 现货自动交易扫描")


if __name__ == "__main__":
    main()
