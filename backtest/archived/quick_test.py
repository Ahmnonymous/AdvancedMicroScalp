#!/usr/bin/env python3
"""
Quick Backtest - Fast Results
Runs a 30-day backtest with high speed for quick validation and more trading opportunities.
"""

import sys
import os
import json
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_runner import BacktestRunner
from utils.logger_factory import get_logger, close_all_loggers

logger = get_logger("quick_backtest", "logs/backtest/quick.log")

def run_quick_test():
    """Run a quick backtest for fast results."""
    
    print("=" * 80)
    print("QUICK BACKTEST - FAST RESULTS")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Ensure mode is backtest
    if config.get('mode') != 'backtest':
        config['mode'] = 'backtest'
    
    # Get all symbols for comprehensive test
    backtest_config = config.get('backtest', {})
    all_symbols = backtest_config.get('symbols', ['EURUSDm'])
    
    print(f"\n[SYMBOLS] Testing {len(all_symbols)} symbols (all symbols)")
    print(f"   {', '.join(all_symbols)}")
    
    # Use 30 days for more trading opportunities
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=30)
    
    print(f"\n[DATE RANGE] {start_date.date()} to {end_date.date()}")
    print(f"   (30 days - more opportunities for trades)")
    
    # Minimal stress tests for speed
    stress_tests = ['high_volatility', 'extreme_spread']
    print(f"\n[STRESS TESTS] {len(stress_tests)} modes (reduced for speed)")
    print(f"   {', '.join(stress_tests)}")
    
    # Update config
    if 'backtest' not in config:
        config['backtest'] = {}
    
    config['backtest'].update({
        'symbols': all_symbols,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'timeframe': 'M1',
        'use_ticks': False,
        'stress_tests': stress_tests,
        'initial_balance': 10000.0
    })
    
    print("\n" + "=" * 80)
    print("STARTING QUICK BACKTEST")
    print("=" * 80)
    
    try:
        # Create runner
        print(f"\n[INIT] Initializing backtest runner...")
        runner = BacktestRunner(config=config)
        
        # Setup environment
        print(f"[SETUP] Setting up backtest environment...")
        runner.setup_backtest_environment()
        
        # Initialize bot
        print(f"[BOT] Initializing trading bot...")
        runner.initialize_trading_bot()
        
        # Run backtest at maximum speed
        print(f"[RUN] Running backtest (speed: 50000x - maximum speed)...")
        runner.run_backtest(speed=50000.0)
        
        # Generate report
        output_path = f"logs/backtest/quick_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        print(f"[REPORT] Generating report...")
        report = runner.generate_report(output_path=output_path)
        
        # Cleanup
        runner.cleanup()
        close_all_loggers()
        
        print("\n" + "=" * 80)
        print("QUICK BACKTEST COMPLETE")
        print("=" * 80)
        print(f"\n[SUCCESS] Report saved: {output_path}")
        print(f"\nSummary:")
        summary = report.get('summary', {})
        print(f"  Total Trades: {summary.get('total_trades', 0)}")
        print(f"  Win Rate: {summary.get('win_rate', 0):.2f}%")
        print(f"  Net Profit: ${summary.get('net_profit', 0):.2f}")
        print(f"  Max Drawdown: ${summary.get('max_drawdown', 0):.2f}")
        print("=" * 80)
        
        return True
        
    except Exception as e:
        print(f"\n[ERROR] Quick backtest failed: {e}")
        import traceback
        traceback.print_exc()
        close_all_loggers()
        return False

if __name__ == "__main__":
    try:
        success = run_quick_test()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Test interrupted by user")
        close_all_loggers()
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        close_all_loggers()
        sys.exit(1)

