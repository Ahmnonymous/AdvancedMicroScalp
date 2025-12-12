#!/usr/bin/env python3
"""
Comprehensive Backtest Runner - All Symbols and All Scenarios
Tests all scenarios with ALL symbols for comprehensive coverage.
"""

import sys
import os
import json
import traceback
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_runner import BacktestRunner
from backtest.test_scenarios import TestScenarioManager
from utils.logger_factory import get_logger, close_all_loggers

logger = get_logger("comprehensive_backtest_all", "logs/backtest/comprehensive_all.log")

def run_comprehensive_test_all_symbols():
    """Run comprehensive backtest with all scenarios and ALL symbols."""
    
    print("=" * 80)
    print("COMPREHENSIVE BACKTEST - ALL SCENARIOS, ALL SYMBOLS")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Ensure mode is backtest
    if config.get('mode') != 'backtest':
        config['mode'] = 'backtest'
        print("[WARNING] Setting mode to 'backtest' for this run...")
    
    # Get all symbols from config
    backtest_config = config.get('backtest', {})
    all_symbols = backtest_config.get('symbols', ['EURUSDm'])
    
    print(f"\n[SYMBOLS] Testing all symbols: {len(all_symbols)}")
    print(f"   {', '.join(all_symbols)}")
    
    # Get all scenarios
    scenario_manager = TestScenarioManager()
    all_scenarios = scenario_manager.list_scenarios()
    
    print(f"\n[SCENARIOS] Scenarios to test: {len(all_scenarios)}")
    print(f"   {', '.join(all_scenarios)}")
    
    # Use recent date range (30 days)
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=30)
    
    print(f"\n[DATE RANGE] {start_date.date()} to {end_date.date()}")
    print(f"   (30 days of historical data)")
    
    # Stress tests to apply (all available stress tests)
    stress_tests = [
        'high_volatility',      # 2x volatility amplification
        'extreme_spread',       # 5x spread expansion
        'fast_reversals',       # Rapid trend reversals
        'tick_gaps',            # Missing ticks
        'slippage_spikes',      # Execution slippage
        'candle_anomalies',     # Random wicks/spikes
        'circuit_breaker'       # High failure rate
    ]
    print(f"\n[STRESS TESTS] {len(stress_tests)} modes")
    print(f"   {', '.join(stress_tests)}")
    
    print("\n" + "=" * 80)
    print("STARTING COMPREHENSIVE TEST")
    print("=" * 80)
    
    all_results = []
    total_scenarios = len(all_scenarios)
    total_combinations = total_scenarios * len(all_symbols)
    current_combination = 0
    
    # Loop through all scenarios
    for scenario_name in all_scenarios:
        print(f"\n{'=' * 80}")
        print(f"SCENARIO: {scenario_name.upper()}")
        print(f"{'=' * 80}")
        
        try:
            # Get scenario data
            scenario_data = scenario_manager.run_scenario(scenario_name, config, months=1)
            scenario_config = scenario_data['config']
            expected_results = scenario_data['expected_results']
            
            print(f"\n[DESCRIPTION] {scenario_data['scenario_description']}")
            print(f"[EXPECTED] {expected_results}")
            
            # Override symbols to use ALL symbols
            scenario_config['backtest']['symbols'] = all_symbols
            scenario_config['backtest']['start_date'] = start_date.isoformat()
            scenario_config['backtest']['end_date'] = end_date.isoformat()
            scenario_config['backtest']['stress_tests'] = stress_tests
            
            print(f"\n[CONFIG] Configuration:")
            print(f"   Symbols: {', '.join(all_symbols)}")
            print(f"   Date range: {start_date.date()} to {end_date.date()}")
            print(f"   Timeframe: {scenario_config['backtest'].get('timeframe', 'M1')}")
            print(f"   Stress tests: {stress_tests}")
            
            # Create runner
            print(f"\n[INIT] Initializing backtest runner...")
            runner = BacktestRunner(config=scenario_config)
            
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
            output_path = f"logs/backtest/report_{scenario_name}_all_symbols_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            print(f"[REPORT] Generating report...")
            report = runner.generate_report(output_path=output_path)
            
            # Cleanup runner resources
            print(f"[CLEANUP] Cleaning up runner resources...")
            runner.cleanup()
            runner = None  # Release reference
            
            all_results.append({
                'scenario': scenario_name,
                'symbols': all_symbols,
                'status': 'success',
                'report': report,
                'output_path': output_path
            })
            
            print(f"\n[SUCCESS] Scenario '{scenario_name}' completed successfully for all symbols")
            print(f"   Report: {output_path}")
            
        except Exception as e:
            error_msg = str(e)
            error_trace = traceback.format_exc()
            
            print(f"\n[FAILED] Scenario '{scenario_name}' FAILED")
            print(f"   Error: {error_msg}")
            print(f"\n   Full traceback:")
            print(f"   {error_trace}")
            
            logger.error(f"Scenario '{scenario_name}' failed: {e}", exc_info=True)
            
            all_results.append({
                'scenario': scenario_name,
                'symbols': all_symbols,
                'status': 'failed',
                'error': error_msg,
                'traceback': error_trace
            })
        finally:
            # CRITICAL: Close all loggers after each scenario to prevent file locking
            try:
                print(f"[CLEANUP] Closing log file handles for scenario '{scenario_name}'...")
                close_all_loggers()
                import time
                time.sleep(0.1)
            except Exception as cleanup_error:
                print(f"[WARNING] Error during cleanup: {cleanup_error}")
    
    # Print final summary
    print("\n" + "=" * 80)
    print("COMPREHENSIVE TEST SUMMARY")
    print("=" * 80)
    
    successful = [r for r in all_results if r['status'] == 'success']
    failed = [r for r in all_results if r['status'] == 'failed']
    
    print(f"\n[SUCCESS] Successful: {len(successful)}/{total_scenarios}")
    print(f"[FAILED] Failed: {len(failed)}/{total_scenarios}")
    print(f"[SYMBOLS] Symbols tested: {len(all_symbols)}")
    print(f"[TOTAL] Total combinations: {total_scenarios} scenarios x {len(all_symbols)} symbols")
    
    if successful:
        print(f"\n[SUCCESS] Successful scenarios:")
        for result in successful:
            print(f"   - {result['scenario']}: {result['output_path']}")
    
    if failed:
        print(f"\n[FAILED] Failed scenarios:")
        for result in failed:
            print(f"   - {result['scenario']}: {result['error']}")
    
    # Save comprehensive report
    comprehensive_report_path = f"logs/backtest/comprehensive_report_all_symbols_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(comprehensive_report_path, 'w') as f:
        json.dump({
            'test_date': datetime.now().isoformat(),
            'symbols_tested': all_symbols,
            'scenarios_tested': all_scenarios,
            'date_range': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            },
            'stress_tests': stress_tests,
            'results': all_results,
            'summary': {
                'total_scenarios': total_scenarios,
                'total_symbols': len(all_symbols),
                'successful': len(successful),
                'failed': len(failed),
                'success_rate': f"{(len(successful)/total_scenarios*100):.1f}%"
            }
        }, f, indent=2)
    
    print(f"\n[REPORT] Comprehensive report saved: {comprehensive_report_path}")
    print("=" * 80)
    
    # Final cleanup
    try:
        print(f"\n[CLEANUP] Performing final cleanup - closing all log file handles...")
        close_all_loggers()
        import time
        time.sleep(0.2)
        print(f"[CLEANUP] All log files closed. Logs folder can now be safely deleted.")
    except Exception as cleanup_error:
        print(f"[WARNING] Error during final cleanup: {cleanup_error}")
    
    return len(failed) == 0


if __name__ == "__main__":
    try:
        success = run_comprehensive_test_all_symbols()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Test interrupted by user")
        close_all_loggers()
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FATAL ERROR] Fatal error: {e}")
        traceback.print_exc()
        close_all_loggers()
        sys.exit(1)

