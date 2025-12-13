#!/usr/bin/env python3
"""
Backtest Runner Entry Point
Simple entry point for running backtests with the consolidated backtest engine.
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_runner import BacktestRunner
from backtest.utils import validate_backtest_config, calculate_date_range, parse_timeframe
from utils.logger_factory import get_logger

logger = get_logger("backtest_main", "logs/backtest/main.log")


def load_config(config_path: str = 'config.json') -> dict:
    """Load configuration, merging backtest-specific config if available."""
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Try to load backtest-specific config
    backtest_config_path = os.path.join(os.path.dirname(__file__), 'config_backtest.json')
    if os.path.exists(backtest_config_path):
        with open(backtest_config_path, 'r') as f:
            backtest_config = json.load(f)
            # Merge backtest config into main config
            if 'backtest' in backtest_config:
                config['backtest'] = backtest_config['backtest']
            if 'mode' in backtest_config:
                config['mode'] = backtest_config['mode']
    
    return config


def main():
    """Main entry point for backtesting."""
    parser = argparse.ArgumentParser(
        description='Run historical backtest on MT5 Trading Bot',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default config
  python backtest/run_backtest.py
  
  # Run with custom date range
  python backtest/run_backtest.py --start 2024-01-01 --end 2024-12-31
  
  # Run with specific symbols
  python backtest/run_backtest.py --symbols EURUSDm GBPUSDm
  
  # Run with real-time speed (slow, matches broker timing)
  python backtest/run_backtest.py --real-speed
  
  # Run accelerated replay (default, fast)
  python backtest/run_backtest.py --speed 10
        """
    )
    
    parser.add_argument('--config', default='config.json', help='Main config file path')
    parser.add_argument('--backtest-config', help='Backtest-specific config file (optional)')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)')
    parser.add_argument('--symbols', nargs='+', help='List of symbols to test')
    parser.add_argument('--timeframe', default='M1', help='Timeframe (M1, M5, H1, etc.)')
    parser.add_argument('--months', type=int, help='Number of months to go back from end date')
    parser.add_argument('--real-speed', action='store_true', help='Use real-time replay speed (matches broker timing)')
    parser.add_argument('--speed', type=float, help='Replay speed multiplier (default: accelerated, 1.0 = real-time)')
    parser.add_argument('--stress-tests', nargs='+', help='Stress test modes to apply')
    parser.add_argument('--output', help='Output report path')
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {args.config}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in config file: {e}")
        sys.exit(1)
    
    # Load backtest-specific config if provided
    if args.backtest_config:
        try:
            with open(args.backtest_config, 'r') as f:
                backtest_config = json.load(f)
                if 'backtest' in backtest_config:
                    config['backtest'] = backtest_config['backtest']
        except FileNotFoundError:
            print(f"WARNING: Backtest config file not found: {args.backtest_config}")
    
    # Ensure mode is backtest
    config['mode'] = 'backtest'
    
    # Get or create backtest config
    if 'backtest' not in config:
        config['backtest'] = {}
    
    backtest_config = config['backtest']
    
    # Override with command-line arguments
    if args.start:
        backtest_config['start_date'] = args.start
    elif 'start_date' not in backtest_config or not backtest_config.get('start_date'):
        # Default to 30 days ago (more reasonable than full year)
        end_date = datetime.now() - timedelta(days=1)
        start_date = end_date - timedelta(days=30)
        backtest_config['start_date'] = start_date.isoformat()
    
    if args.end:
        backtest_config['end_date'] = args.end
    elif 'end_date' not in backtest_config or not backtest_config.get('end_date'):
        # Default to yesterday
        end_date = datetime.now() - timedelta(days=1)
        backtest_config['end_date'] = end_date.isoformat()
    
    if args.months:
        end_date = datetime.fromisoformat(backtest_config.get('end_date', (datetime.now() - timedelta(days=1)).isoformat()))
        start_date = end_date - timedelta(days=args.months * 30)
        backtest_config['start_date'] = start_date.isoformat()
    
    if args.symbols:
        backtest_config['symbols'] = args.symbols
    
    if args.timeframe:
        backtest_config['timeframe'] = args.timeframe
    
    if args.real_speed:
        backtest_config['real_speed'] = True
    elif args.speed is not None:
        backtest_config['real_speed'] = False
        backtest_config['replay_speed'] = args.speed
    elif 'real_speed' not in backtest_config:
        backtest_config['real_speed'] = False  # Default to accelerated
    
    if args.stress_tests:
        backtest_config['stress_tests'] = args.stress_tests
    
    # Validate configuration
    is_valid, errors, warnings = validate_backtest_config(config)
    if not is_valid:
        print("ERROR: Configuration validation failed:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    
    if warnings:
        print("WARNING: Configuration warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    
    # Print configuration
    print("=" * 80)
    print("BACKTEST CONFIGURATION")
    print("=" * 80)
    print(f"Symbols: {', '.join(backtest_config.get('symbols', []))}")
    print(f"Date Range: {backtest_config.get('start_date')} to {backtest_config.get('end_date')}")
    print(f"Timeframe: {backtest_config.get('timeframe', 'M1')}")
    print(f"Speed: {'Real-time' if backtest_config.get('real_speed', False) else 'Accelerated'}")
    if backtest_config.get('stress_tests'):
        print(f"Stress Tests: {', '.join(backtest_config['stress_tests'])}")
    print("=" * 80)
    print()
    
    # Run backtest
    try:
        runner = BacktestRunner(config=config)
        runner.setup_backtest_environment()
        runner.initialize_trading_bot()
        
        # Determine replay speed
        if backtest_config.get('real_speed', False):
            replay_speed = 1.0  # Real-time (matches broker timing)
        elif 'replay_speed' in backtest_config:
            replay_speed = backtest_config['replay_speed']
        else:
            replay_speed = 100.0  # Default accelerated speed
        
        runner.run_backtest(speed=replay_speed)
        
        # Generate report if output specified
        if args.output:
            runner.performance_reporter.generate_report(output_path=args.output)
        else:
            runner.performance_reporter.generate_report()
        
        print("\n" + "=" * 80)
        print("BACKTEST COMPLETE")
        print("=" * 80)
        
    except KeyboardInterrupt:
        print("\n\nBacktest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        print(f"\nERROR: Backtest failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

