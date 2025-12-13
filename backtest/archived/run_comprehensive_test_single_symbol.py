#!/usr/bin/env python3
"""
Single Symbol Backtest Runner - 12 Months Performance Test
Runs a focused backtest on ONE symbol for 12 months to analyze bot performance.
"""

import sys
import os
import json
import traceback
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_runner import BacktestRunner
from utils.logger_factory import get_logger, close_all_loggers

logger = get_logger("single_symbol_backtest", "logs/backtest/single_symbol.log")

def run_single_symbol_backtest():
    """Run backtest on ONE symbol for 12 months to analyze performance."""
    
    print("=" * 80)
    print("SINGLE SYMBOL BACKTEST - 12 MONTHS PERFORMANCE TEST")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Ensure mode is backtest
    if config.get('mode') != 'backtest':
        config['mode'] = 'backtest'
        print("[INFO] Setting mode to 'backtest' for this run...")
    
    # RELAX FILTERS FOR BACKTESTING TO VERIFY BOT LOGIC
    # This allows trades to execute so we can test SL, trailing, profit locking, etc.
    print("\n[FILTER RELAXATION] Relaxing filters for backtest verification...")
    
    # Relax trend strength requirement (allow weaker trends for testing)
    if 'trading' not in config:
        config['trading'] = {}
    original_trend_strength = config['trading'].get('min_trend_strength_pct', 0.02)
    config['trading']['min_trend_strength_pct'] = 0.001  # Lower from 0.02% to 0.001% (20x more lenient)
    print(f"  - Trend strength: {original_trend_strength}% → 0.001% (relaxed)")
    
    # Relax quality score requirement
    original_quality_score = config['trading'].get('min_quality_score', 50.0)
    config['trading']['min_quality_score'] = 30.0  # Lower from 50 to 30
    print(f"  - Quality score: {original_quality_score} → 30.0 (relaxed)")
    
    # DISABLE RSI FILTER for backtesting (allows all RSI values)
    original_use_rsi = config['trading'].get('use_rsi_filter', True)
    config['trading']['use_rsi_filter'] = False  # Disable RSI filter completely
    print(f"  - RSI filter: {'ENABLED' if original_use_rsi else 'DISABLED'} → DISABLED (relaxed)")
    
    # Expand RSI entry range (in case filter is still checked elsewhere)
    config['trading']['rsi_entry_range_min'] = 0  # Allow any RSI value
    config['trading']['rsi_entry_range_max'] = 100  # Allow any RSI value
    print(f"  - RSI entry range: 0-100 (fully relaxed)")
    
    # Ensure test mode ignores spread (already set, but verify)
    if 'pairs' not in config:
        config['pairs'] = {}
    config['pairs']['test_mode'] = True
    config['pairs']['test_mode_ignore_spread'] = True
    print(f"  - Test mode spread check: IGNORED (enabled)")
    
    # VERIFICATION: Print config values to confirm changes
    print(f"\n[VERIFICATION] Config values after modification:")
    print(f"  - use_rsi_filter: {config['trading'].get('use_rsi_filter')}")
    print(f"  - rsi_entry_range: {config['trading'].get('rsi_entry_range_min')}-{config['trading'].get('rsi_entry_range_max')}")
    print(f"  - min_trend_strength_pct: {config['trading'].get('min_trend_strength_pct')}")
    print(f"  - min_quality_score: {config['trading'].get('min_quality_score')}")
    
    print("[INFO] Filters relaxed - backtest will allow more trades for logic verification")
    
    # Select ONE symbol with best trade opportunities
    # Priority: Low spreads (pass $0.30 test limit) + Good volatility + Clear trends
    # Options:
    #   - GBPUSDm: Volatile, good trends, reasonable spreads (should pass $0.30 limit)
    #   - EURUSDm: Most liquid, lowest spreads, but weak trends (ranging market)
    #   - XAUUSDm: HIGH volatility but spread $1.60 > $0.30 limit (REJECTED)
    # Using GBPUSDm for best balance: good trends + spreads that pass test mode limit
    selected_symbol = 'GBPUSDm'  # Cable - good volatility, strong trends, spreads typically <$0.30
    
    print(f"\n[SYMBOL] Testing: {selected_symbol}")
    print(f"   (Selected for better volatility and trend opportunities)")
    
    # Use 12 months of data (365 days)
    end_date = datetime.now() - timedelta(days=1)  # Yesterday
    start_date = end_date - timedelta(days=365)     # 365 days before (12 months)
    
    print(f"\n[DATE RANGE] {start_date.date()} to {end_date.date()}")
    print(f"   (12 months / 365 days of historical data)")
    
    # NO stress tests - just clean performance analysis
    stress_tests = []
    print(f"\n[STRESS TESTS] None (clean performance test)")
    
    # Update backtest config
    if 'backtest' not in config:
        config['backtest'] = {}
    
    config['backtest']['symbols'] = [selected_symbol]  # ONE symbol only
    config['backtest']['start_date'] = start_date.strftime('%Y-%m-%dT%H:%M:%S')
    config['backtest']['end_date'] = end_date.strftime('%Y-%m-%dT%H:%M:%S')
    config['backtest']['timeframe'] = 'M1'
    config['backtest']['use_ticks'] = False
    config['backtest']['stress_tests'] = stress_tests  # No stress tests
    config['backtest']['initial_balance'] = 10000.0
    
    print("\n" + "=" * 80)
    print("STARTING BACKTEST")
    print("=" * 80)
    print(f"Symbol: {selected_symbol}")
    print(f"Date Range: {start_date.date()} to {end_date.date()}")
    print(f"Timeframe: M1")
    print(f"Mode: BACKTEST")
    print(f"Replay Speed: Realistic (1.0x)")
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
        
        # Run backtest at realistic speed (1.0 = no acceleration)
        print(f"\n[RUN] Running backtest at realistic speed (1.0x)...")
        print(f"      This may take a while for 12 months of M1 data...")
        runner.run_backtest(speed=1.0)
        
        # Generate report
        output_path = f"logs/backtest/single_symbol_{selected_symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        print(f"\n[REPORT] Generating performance report...")
        report = runner.generate_report(output_path=output_path)
        
        # Display summary
        print("\n" + "=" * 80)
        print("BACKTEST PERFORMANCE SUMMARY")
        print("=" * 80)
        
        summary = report.get('summary', {})
        print(f"\n📊 TRADE STATISTICS:")
        print(f"   Total Trades: {summary.get('total_trades', 0)}")
        print(f"   Winning Trades: {summary.get('winning_trades', 0)}")
        print(f"   Losing Trades: {summary.get('losing_trades', 0)}")
        print(f"   Win Rate: {summary.get('win_rate', 0):.2f}%")
        
        print(f"\n💰 PROFIT & LOSS:")
        print(f"   Net Profit: ${summary.get('net_profit', 0):.2f}")
        print(f"   Total Profit: ${summary.get('total_profit', 0):.2f}")
        print(f"   Total Loss: ${summary.get('total_loss', 0):.2f}")
        print(f"   Profit Factor: {summary.get('profit_factor', 0):.2f}")
        print(f"   Max Drawdown: ${summary.get('max_drawdown', 0):.2f} ({summary.get('max_drawdown_pct', 0):.2f}%)")
        
        print(f"\n📈 RISK METRICS:")
        print(f"   Average R:R: {summary.get('avg_rr', 0):.2f}")
        print(f"   Sharpe Ratio: {summary.get('sharpe_ratio', 0):.2f}")
        
        # SL Performance
        sl_perf = report.get('sl_performance', {})
        if sl_perf:
            print(f"\n🛡️ SL SYSTEM PERFORMANCE:")
            print(f"   Success Rate: {sl_perf.get('success_rate', 0):.2f}%")
            print(f"   Avg Delay: {sl_perf.get('avg_delay_ms', 0):.2f}ms")
            print(f"   Max Delay: {sl_perf.get('max_delay_ms', 0):.2f}ms")
        
        print(f"\n📄 Report saved: {output_path}")
        print("=" * 80)
        
        # Cleanup
        print(f"\n[CLEANUP] Cleaning up...")
        runner.cleanup()
        
        return True
        
    except Exception as e:
        error_msg = str(e)
        error_trace = traceback.format_exc()
        
        print(f"\n[ERROR] Backtest FAILED")
        print(f"   Error: {error_msg}")
        print(f"\n   Full traceback:")
        print(f"   {error_trace}")
        
        logger.error(f"Backtest failed: {e}", exc_info=True)
        return False
        
    finally:
        # Cleanup loggers
        try:
            close_all_loggers()
        except Exception as cleanup_error:
            print(f"[WARNING] Error during cleanup: {cleanup_error}")


if __name__ == "__main__":
    try:
        success = run_single_symbol_backtest()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Backtest interrupted by user")
        close_all_loggers()
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FATAL ERROR] Fatal error: {e}")
        traceback.print_exc()
        close_all_loggers()
        sys.exit(1)

