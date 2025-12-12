#!/usr/bin/env python3
"""
Backtest Runner Entry Point
Run historical backtests with various scenarios and stress tests.
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_runner import BacktestRunner
from backtest.test_scenarios import TestScenarioManager
from utils.logger_factory import get_logger

logger = get_logger("backtest_main", "logs/backtest/main.log")


def main():
    """Main entry point for backtesting."""
    parser = argparse.ArgumentParser(
        description='Run historical backtest on MT5 Trading Bot',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --scenario trending_up
  %(prog)s --scenario flash_crash --speed 10
  %(prog)s --custom --start 2024-01-01 --end 2024-01-02 --symbols EURUSD GBPUSD
  %(prog)s --scenario all --stress-tests high_volatility,extreme_spread
        """
    )
    
    parser.add_argument('--config', default='config.json', help='Config file path')
    parser.add_argument('--scenario', help='Test scenario name (or "all" for all scenarios)')
    parser.add_argument('--custom', action='store_true', help='Use custom backtest parameters')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)')
    parser.add_argument('--symbols', help='Comma-separated list of symbols')
    parser.add_argument('--timeframe', default='M1', help='Timeframe (M1, M5, H1, etc.)')
    parser.add_argument('--speed', type=float, default=1.0, help='Replay speed multiplier')
    parser.add_argument('--stress-tests', help='Comma-separated stress test modes')
    parser.add_argument('--output', help='Output report path')
    parser.add_argument('--use-ticks', action='store_true', help='Use tick-by-tick replay (slower but more accurate)')
    
    args = parser.parse_args()
    
    # Load config
    with open(args.config, 'r') as f:
        config = json.load(f)
    
    # Ensure mode is backtest
    if config.get('mode') != 'backtest':
        print("WARNING: Config mode is not 'backtest'")
        print("   Setting mode to 'backtest' for this run...")
        config['mode'] = 'backtest'
    
    # Initialize scenario manager
    scenario_manager = TestScenarioManager()
    
    # Determine which scenarios to run
    if args.scenario:
        if args.scenario == 'all':
            scenarios_to_run = scenario_manager.list_scenarios()
        else:
            scenarios_to_run = [args.scenario]
    elif args.custom:
        scenarios_to_run = ['custom']
    else:
        print("ERROR: Must specify --scenario or --custom")
        parser.print_help()
        sys.exit(1)
    
    # Run each scenario
    all_reports = []
    
    for scenario_name in scenarios_to_run:
        print("\n" + "=" * 80)
        print(f"RUNNING SCENARIO: {scenario_name}")
        print("=" * 80)
        
        try:
            if scenario_name == 'custom':
                # Custom backtest
                if not args.start or not args.end:
                    print("ERROR: --start and --end required for custom backtest")
                    sys.exit(1)
                
                start_date = datetime.fromisoformat(args.start.replace(' ', 'T'))
                end_date = datetime.fromisoformat(args.end.replace(' ', 'T'))
                symbols = args.symbols.split(',') if args.symbols else ['EURUSD']
                
                # Update config
                if 'backtest' not in config:
                    config['backtest'] = {}
                
                config['backtest'].update({
                    'symbols': symbols,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'timeframe': args.timeframe,
                    'use_ticks': args.use_ticks,
                    'stress_tests': args.stress_tests.split(',') if args.stress_tests else [],
                    'initial_balance': 10000.0
                })
            else:
                # Use predefined scenario
                scenario_data = scenario_manager.run_scenario(scenario_name, config)
                config = scenario_data['config']
                expected_results = scenario_data['expected_results']
                print(f"Scenario: {scenario_data['scenario_description']}")
                print(f"Expected Results: {expected_results}")
            
            # Create runner with config
            runner = BacktestRunner(config=config)
            
            # Setup and run
            runner.setup_backtest_environment()
            runner.initialize_trading_bot()
            runner.run_backtest(speed=args.speed)
            
            # Generate report
            output_path = args.output or f"logs/backtest/report_{scenario_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            report = runner.generate_report(output_path=output_path)
            all_reports.append({
                'scenario': scenario_name,
                'report': report
            })
            
            print(f"\n[SUCCESS] Scenario '{scenario_name}' completed")
            print(f"   Report saved to: {output_path}")
            
        except Exception as e:
            logger.error(f"Error running scenario '{scenario_name}': {e}", exc_info=True)
            print(f"\n[ERROR] Scenario '{scenario_name}' failed: {e}")
            continue
    
    # Print summary
    print("\n" + "=" * 80)
    print("ALL SCENARIOS COMPLETE")
    print("=" * 80)
    print(f"Scenarios run: {len(all_reports)}")
    print(f"Reports generated: {len(all_reports)}")
    print("=" * 80)


if __name__ == "__main__":
    main()

