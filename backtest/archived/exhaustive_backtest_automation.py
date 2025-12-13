#!/usr/bin/env python3
"""
Exhaustive Backtest Automation
Runs all scenarios over extended historical periods, detects errors/anomalies,
automatically applies fixes, and reruns until all scenarios pass cleanly.
"""

import sys
import os
import json
import time
import csv
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_runner import BacktestRunner
from backtest.test_scenarios import TestScenarioManager
from backtest.stress_test_modes import StressTestManager
from utils.logger_factory import get_logger

logger = get_logger("exhaustive_backtest", "logs/backtest/exhaustive.log")


class AutoFixManager:
    """Manages automatic fixes for detected issues."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.fixes_applied = []
    
    def detect_and_fix(self, report: Dict[str, Any], scenario_name: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Detect issues and apply fixes.
        
        Returns:
            (fixes_applied, updated_config)
        """
        fixes_applied = False
        updated_config = self.config.copy()
        
        metrics = report.get('metrics', {})
        threshold_checks = report.get('threshold_checks', {})
        
        # Fix 1: SL update success rate too low
        if not threshold_checks.get('sl_update_success_rate', True):
            logger.warning(f"[AUTO-FIX] SL update success rate too low: {metrics.get('sl_update_success_rate', 0):.2f}%")
            # Reduce lock scope, increase timeout
            if 'risk' not in updated_config:
                updated_config['risk'] = {}
            updated_config['risk']['lock_acquisition_timeout_seconds'] = min(
                updated_config['risk'].get('lock_acquisition_timeout_seconds', 1.0) * 1.5, 3.0
            )
            updated_config['risk']['sl_update_min_interval_ms'] = max(
                updated_config['risk'].get('sl_update_min_interval_ms', 100) * 0.8, 50
            )
            self.fixes_applied.append({
                'scenario': scenario_name,
                'fix': 'increase_lock_timeout_reduce_sl_interval',
                'reason': 'SL update success rate below 95%',
                'timestamp': datetime.now().isoformat()
            })
            fixes_applied = True
        
        # Fix 2: Lock contention too high
        if not threshold_checks.get('lock_contention_rate', True):
            logger.warning(f"[AUTO-FIX] Lock contention too high: {metrics.get('lock_contention_rate', 0):.2f}%")
            # Increase lock timeout, reduce concurrent operations
            if 'risk' not in updated_config:
                updated_config['risk'] = {}
            updated_config['risk']['lock_acquisition_timeout_seconds'] = min(
                updated_config['risk'].get('lock_acquisition_timeout_seconds', 1.0) * 1.5, 3.0
            )
            updated_config['risk']['trailing_cycle_interval_ms'] = max(
                updated_config['risk'].get('trailing_cycle_interval_ms', 50) * 1.2, 50
            )
            self.fixes_applied.append({
                'scenario': scenario_name,
                'fix': 'increase_lock_timeout_increase_trailing_interval',
                'reason': f"Lock contention {metrics.get('lock_contention_rate', 0):.2f}% > 5%",
                'timestamp': datetime.now().isoformat()
            })
            fixes_applied = True
        
        # Fix 3: Worker loop timing violations
        if not threshold_checks.get('worker_loop_timing', True):
            logger.warning(f"[AUTO-FIX] Worker loop timing violation: max={metrics.get('worker_loop_max_duration_ms', 0):.2f}ms")
            # Increase intervals, reduce processing per cycle
            if 'risk' not in updated_config:
                updated_config['risk'] = {}
            updated_config['risk']['trailing_cycle_interval_ms'] = max(
                updated_config['risk'].get('trailing_cycle_interval_ms', 50) * 1.3, 50
            )
            updated_config['risk']['sl_update_min_interval_ms'] = max(
                updated_config['risk'].get('sl_update_min_interval_ms', 100) * 1.2, 100
            )
            self.fixes_applied.append({
                'scenario': scenario_name,
                'fix': 'increase_trailing_interval_increase_sl_interval',
                'reason': f"Worker loop duration {metrics.get('worker_loop_max_duration_ms', 0):.2f}ms > 50ms",
                'timestamp': datetime.now().isoformat()
            })
            fixes_applied = True
        
        # Fix 4: Profit lock activation rate too low
        if not threshold_checks.get('profit_lock_activation_rate', True):
            logger.warning(f"[AUTO-FIX] Profit lock activation rate too low: {metrics.get('profit_lock_activation_rate', 0):.2f}%")
            # Increase retries, reduce lock timeout
            if 'risk' not in updated_config:
                updated_config['risk'] = {}
            if 'profit_locking' not in updated_config['risk']:
                updated_config['risk']['profit_locking'] = {}
            updated_config['risk']['profit_locking']['max_lock_retries'] = min(
                updated_config['risk']['profit_locking'].get('max_lock_retries', 3) + 2, 10
            )
            updated_config['risk']['profit_locking']['lock_retry_delay_ms'] = max(
                updated_config['risk']['profit_locking'].get('lock_retry_delay_ms', 100) * 0.8, 50
            )
            self.fixes_applied.append({
                'scenario': scenario_name,
                'fix': 'increase_profit_lock_retries_reduce_delay',
                'reason': f"Profit lock activation rate {metrics.get('profit_lock_activation_rate', 0):.2f}% < 95%",
                'timestamp': datetime.now().isoformat()
            })
            fixes_applied = True
        
        # Fix 5: Duplicate SL updates
        if metrics.get('sl_update_duplicate_updates', 0) > 0:
            logger.warning(f"[AUTO-FIX] Duplicate SL updates detected: {metrics.get('sl_update_duplicate_updates', 0)}")
            # Increase minimum interval between updates
            if 'risk' not in updated_config:
                updated_config['risk'] = {}
            updated_config['risk']['sl_update_min_interval_ms'] = max(
                updated_config['risk'].get('sl_update_min_interval_ms', 100) * 1.5, 150
            )
            self.fixes_applied.append({
                'scenario': scenario_name,
                'fix': 'increase_sl_update_min_interval',
                'reason': f"Duplicate SL updates: {metrics.get('sl_update_duplicate_updates', 0)}",
                'timestamp': datetime.now().isoformat()
            })
            fixes_applied = True
        
        # Fix 6: Runtime exceptions
        if not threshold_checks.get('no_critical_exceptions', True):
            logger.warning(f"[AUTO-FIX] Critical exceptions detected: {metrics.get('exceptions', 0)}")
            # Increase timeouts, add retry logic
            if 'execution' not in updated_config:
                updated_config['execution'] = {}
            updated_config['execution']['order_timeout_seconds'] = min(
                updated_config['execution'].get('order_timeout_seconds', 2.0) * 1.5, 5.0
            )
            updated_config['execution']['order_max_retries'] = min(
                updated_config['execution'].get('order_max_retries', 3) + 1, 5
            )
            self.fixes_applied.append({
                'scenario': scenario_name,
                'fix': 'increase_order_timeout_retries',
                'reason': f"Critical exceptions: {metrics.get('exceptions', 0)}",
                'timestamp': datetime.now().isoformat()
            })
            fixes_applied = True
        
        return fixes_applied, updated_config


class ExhaustiveBacktestAutomation:
    """Main automation class for exhaustive backtesting."""
    
    def __init__(self, config_path: str = 'config.json', 
                 historical_months: int = 12,
                 max_retries_per_scenario: int = 3):
        """
        Initialize exhaustive backtest automation.
        
        Args:
            config_path: Path to config.json
            historical_months: Number of months of historical data to test
            max_retries_per_scenario: Maximum retries per scenario before giving up
        """
        self.config_path = config_path
        self.historical_months = historical_months
        self.max_retries_per_scenario = max_retries_per_scenario
        
        # Load base config
        with open(config_path, 'r') as f:
            self.base_config = json.load(f)
        
        # Ensure mode is backtest
        self.base_config['mode'] = 'backtest'
        
        # Initialize managers
        self.scenario_manager = TestScenarioManager()
        self.stress_test_manager = StressTestManager()
        self.auto_fix_manager = AutoFixManager(self.base_config)
        
        # Results tracking
        self.all_results: List[Dict[str, Any]] = []
        self.fixes_log: List[Dict[str, Any]] = []
        
        # Define all scenarios
        self.scenarios = [
            'trending_up',
            'trending_down',
            'sideways_chop',
            'flash_crash',
            'spike_candle',
            'spread_widening',
            'news_volatility',
            'market_dead'
        ]
        
        # Define stress tests
        self.stress_tests = [
            'high_volatility',
            'extreme_spread',
            'slippage_spikes',
            'tick_gaps'
        ]
    
    def extend_scenario_period(self, scenario_config: Dict[str, Any], 
                               months: int) -> Dict[str, Any]:
        """Extend scenario to cover extended historical period."""
        # Calculate end date (1 week ago for reliable data, rounded to day)
        # Ensure we don't go into the future
        now = datetime.now()
        new_end = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Ensure dates are reasonable (not too far in past, not in future)
        # MT5 typically has data going back 1-2 years, so cap at 24 months
        max_months = min(months, 24)
        if max_months < months:
            logger.warning(f"Requested {months} months, but capping at {max_months} months for MT5 compatibility")
        
        # Calculate start date (months ago, rounded to day)
        new_start = (new_end - timedelta(days=max_months * 30)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Double-check: ensure end date is not in the future
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if new_end > today:
            logger.warning(f"End date {new_end} is in the future, adjusting to {today}")
            new_end = today
            # Recalculate start if needed
            new_start = (new_end - timedelta(days=max_months * 30)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        scenario_config['start_date'] = new_start.isoformat()
        scenario_config['end_date'] = new_end.isoformat()
        
        logger.info(f"Extended period: {new_start.date()} to {new_end.date()} ({max_months} months)")
        
        return scenario_config
    
    def run_scenario_with_retries(self, scenario_name: str, 
                                  stress_tests: List[str] = None,
                                  use_extended_period: bool = True) -> Dict[str, Any]:
        """
        Run a scenario with automatic retries and fixes.
        
        Returns:
            Result dictionary with pass/fail status and report
        """
        stress_tests = stress_tests or []
        retry_count = 0
        last_error = None
        
        while retry_count <= self.max_retries_per_scenario:
            try:
                logger.info(f"Running scenario '{scenario_name}' (attempt {retry_count + 1}/{self.max_retries_per_scenario + 1})")
                
                # Get scenario config
                scenario_data = self.scenario_manager.run_scenario(
                    scenario_name, 
                    self.base_config,
                    months=self.historical_months
                )
                config = scenario_data['config']
                
                # Extend period if requested
                if use_extended_period:
                    config['backtest'] = self.extend_scenario_period(
                        config['backtest'], 
                        self.historical_months
                    )
                
                # Apply stress tests
                if stress_tests:
                    if 'backtest' not in config:
                        config['backtest'] = {}
                    config['backtest']['stress_tests'] = stress_tests
                
                # Apply fixes from previous attempts
                if retry_count > 0:
                    logger.info(f"Applying fixes from previous attempt...")
                    config = self.auto_fix_manager.config.copy()
                    config['backtest'] = scenario_data['config']['backtest']
                    if stress_tests:
                        config['backtest']['stress_tests'] = stress_tests
                
                # Create runner
                runner = BacktestRunner(config=config)
                
                # Setup and run
                runner.setup_backtest_environment()
                runner.initialize_trading_bot()
                
                # Run backtest - use very high speed for exhaustive testing (100x = no sleep, max speed)
                runner.run_backtest(speed=100.0)  # 100x speed = process as fast as possible
                
                # Generate report
                report = runner.generate_report()
                
                # Check if passed
                threshold_checks = report.get('threshold_checks', {})
                passed = threshold_checks.get('all_passed', False)
                
                if passed:
                    logger.info(f"[OK] Scenario '{scenario_name}' PASSED")
                    return {
                        'scenario': scenario_name,
                        'stress_tests': stress_tests,
                        'status': 'passed',
                        'report': report,
                        'retry_count': retry_count,
                        'timestamp': datetime.now().isoformat()
                    }
                else:
                    # Detect and apply fixes
                    logger.warning(f"[WARNING] Scenario '{scenario_name}' FAILED threshold checks")
                    # CRITICAL: AUTO-FIX DISABLED for deterministic backtesting
                    # DO NOT ENABLE - auto-fix modifies config at runtime, breaking determinism
                    # fixes_applied, updated_config = self.auto_fix_manager.detect_and_fix(
                    fixes_applied = False  # DISABLED
                    updated_config = config  # Use original config, no modifications
                    # Original code (DISABLED):
                    # fixes_applied, updated_config = self.auto_fix_manager.detect_and_fix(
                    #     report, scenario_name
                    # )
                    
                    if fixes_applied:
                        self.auto_fix_manager.config = updated_config
                        self.fixes_log.extend(self.auto_fix_manager.fixes_applied)
                        logger.info(f"Applied fixes, retrying scenario '{scenario_name}'...")
                        retry_count += 1
                        continue
                    else:
                        # No fixes available, mark as failed
                        logger.error(f"[ERROR] Scenario '{scenario_name}' FAILED - no fixes available")
                        return {
                            'scenario': scenario_name,
                            'stress_tests': stress_tests,
                            'status': 'failed',
                            'report': report,
                            'retry_count': retry_count,
                            'reason': 'threshold_checks_failed',
                            'timestamp': datetime.now().isoformat()
                        }
            
            except Exception as e:
                last_error = str(e)
                logger.error(f"Error running scenario '{scenario_name}': {e}", exc_info=True)
                
                # Try to fix based on error type
                error_str = str(e).lower()
                if "mt5" in error_str or "connection" in error_str or "initialize" in error_str:
                    logger.warning("MT5 connection/initialization error - will retry with backoff")
                    time.sleep(5)
                    retry_count += 1
                    if retry_count > self.max_retries_per_scenario:
                        return {
                            'scenario': scenario_name,
                            'stress_tests': stress_tests,
                            'status': 'error',
                            'error': f"MT5 connection failed after {retry_count} retries: {str(e)}",
                            'retry_count': retry_count,
                            'timestamp': datetime.now().isoformat()
                        }
                    continue
                elif "data" in error_str or "historical" in error_str or "failed to load" in error_str:
                    logger.error(f"Historical data error: {str(e)}")
                    # Try reducing date range as a fix
                    if retry_count == 0:
                        logger.info("Attempting to reduce date range as fix...")
                        # Reduce to 12 months instead of 24
                        if 'backtest' in config:
                            original_start = datetime.fromisoformat(config['backtest'].get('start_date', ''))
                            original_end = datetime.fromisoformat(config['backtest'].get('end_date', ''))
                            # Try 12 months instead
                            new_start = original_end - timedelta(days=365)
                            config['backtest']['start_date'] = new_start.isoformat()
                            logger.info(f"Reduced date range to {new_start} to {original_end}")
                            retry_count += 1
                            continue
                    
                    return {
                        'scenario': scenario_name,
                        'stress_tests': stress_tests,
                        'status': 'error',
                        'error': f"Historical data error: {str(e)}",
                        'retry_count': retry_count,
                        'timestamp': datetime.now().isoformat()
                    }
                else:
                    retry_count += 1
                    if retry_count > self.max_retries_per_scenario:
                        return {
                            'scenario': scenario_name,
                            'stress_tests': stress_tests,
                            'status': 'error',
                            'error': str(e),
                            'retry_count': retry_count,
                            'timestamp': datetime.now().isoformat()
                        }
                    time.sleep(2)
                    continue
        
        # Max retries exceeded
        logger.error(f"[ERROR] Scenario '{scenario_name}' FAILED after {self.max_retries_per_scenario} retries")
        return {
            'scenario': scenario_name,
            'stress_tests': stress_tests,
            'status': 'failed',
            'error': last_error or 'max_retries_exceeded',
            'retry_count': retry_count,
            'timestamp': datetime.now().isoformat()
        }
    
    def run_all_scenarios(self):
        """Run all scenarios with all stress test combinations."""
        logger.info("=" * 80)
        logger.info("STARTING EXHAUSTIVE BACKTEST AUTOMATION")
        logger.info("=" * 80)
        logger.info(f"Scenarios: {len(self.scenarios)}")
        logger.info(f"Stress tests: {len(self.stress_tests)}")
        logger.info(f"Historical period: {self.historical_months} months")
        logger.info("=" * 80)
        
        total_scenarios = len(self.scenarios) * (len(self.stress_tests) + 1)  # +1 for no stress tests
        scenario_count = 0
        
        # Run each scenario
        for scenario_name in self.scenarios:
            # Run without stress tests
            scenario_count += 1
            logger.info(f"\n[{scenario_count}/{total_scenarios}] Running scenario: {scenario_name} (no stress)")
            result = self.run_scenario_with_retries(scenario_name, stress_tests=[])
            self.all_results.append(result)
            
            # Run with each stress test
            for stress_test in self.stress_tests:
                scenario_count += 1
                logger.info(f"\n[{scenario_count}/{total_scenarios}] Running scenario: {scenario_name} + {stress_test}")
                result = self.run_scenario_with_retries(scenario_name, stress_tests=[stress_test])
                self.all_results.append(result)
        
        logger.info("\n" + "=" * 80)
        logger.info("ALL SCENARIOS COMPLETE")
        logger.info("=" * 80)
    
    def generate_aggregated_report(self, output_dir: str = 'logs/backtest/exhaustive'):
        """Generate aggregated reports (CSV, equity curves, summary)."""
        os.makedirs(output_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Summary CSV
        csv_path = os.path.join(output_dir, f'summary_{timestamp}.csv')
        self._generate_summary_csv(csv_path)
        
        # Fixes log
        fixes_path = os.path.join(output_dir, f'fixes_applied_{timestamp}.json')
        with open(fixes_path, 'w') as f:
            json.dump(self.fixes_log, f, indent=2)
        
        # Detailed results JSON
        results_path = os.path.join(output_dir, f'results_{timestamp}.json')
        with open(results_path, 'w') as f:
            json.dump(self.all_results, f, indent=2, default=str)
        
        # Print summary
        self._print_summary()
        
        logger.info(f"\nReports saved to: {output_dir}")
        logger.info(f"  - Summary CSV: {csv_path}")
        logger.info(f"  - Fixes log: {fixes_path}")
        logger.info(f"  - Detailed results: {results_path}")
    
    def _generate_summary_csv(self, output_path: str):
        """Generate summary CSV report."""
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Scenario', 'Stress Tests', 'Status', 'Retry Count',
                'Total Trades', 'Win Rate %', 'Net Profit', 'Max Drawdown %',
                'SL Success Rate %', 'Lock Contention %', 'Worker Loop Max (ms)',
                'Profit Lock Rate %', 'Anomalies', 'Exceptions'
            ])
            
            for result in self.all_results:
                if result.get('status') == 'error':
                    writer.writerow([
                        result.get('scenario', ''),
                        ','.join(result.get('stress_tests', [])),
                        result.get('status', ''),
                        result.get('retry_count', 0),
                        '', '', '', '', '', '', '', '', '',
                        result.get('error', '')
                    ])
                else:
                    report = result.get('report', {})
                    summary = report.get('summary', {})
                    sl_perf = report.get('sl_performance', {})
                    lock_cont = report.get('lock_contention', {})
                    worker_loop = report.get('worker_loop_performance', {})
                    profit_lock = report.get('profit_locking', {})
                    anomalies = report.get('anomalies', {})
                    
                    writer.writerow([
                        result.get('scenario', ''),
                        ','.join(result.get('stress_tests', [])),
                        result.get('status', ''),
                        result.get('retry_count', 0),
                        summary.get('total_trades', 0),
                        summary.get('win_rate', 0.0),
                        summary.get('net_profit', 0.0),
                        summary.get('max_drawdown_pct', 0.0),
                        sl_perf.get('success_rate', 0.0),
                        lock_cont.get('rate', 0.0),
                        worker_loop.get('max_duration_ms', 0.0),
                        profit_lock.get('activation_rate', 0.0),
                        anomalies.get('total', 0),
                        report.get('exceptions', {}).get('total', 0)
                    ])
    
    def _print_summary(self):
        """Print summary statistics."""
        total = len(self.all_results)
        passed = sum(1 for r in self.all_results if r.get('status') == 'passed')
        failed = sum(1 for r in self.all_results if r.get('status') == 'failed')
        errors = sum(1 for r in self.all_results if r.get('status') == 'error')
        total_fixes = len(self.fixes_log)
        
        print("\n" + "=" * 80)
        print("EXHAUSTIVE BACKTEST SUMMARY")
        print("=" * 80)
        print(f"Total Scenarios Run: {total}")
        print(f"  [OK] Passed: {passed} ({passed/total*100:.1f}%)")
        print(f"  [ERROR] Failed: {failed} ({failed/total*100:.1f}%)")
        print(f"  [WARNING]  Errors: {errors} ({errors/total*100:.1f}%)")
        print(f"Total Fixes Applied: {total_fixes}")
        print("=" * 80)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Run exhaustive backtests on all scenarios',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--config', default='config.json', help='Config file path')
    parser.add_argument('--months', type=int, default=12, help='Historical period in months')
    parser.add_argument('--max-retries', type=int, default=3, help='Max retries per scenario')
    parser.add_argument('--output-dir', default='logs/backtest/exhaustive', help='Output directory')
    
    args = parser.parse_args()
    
    # Create automation
    automation = ExhaustiveBacktestAutomation(
        config_path=args.config,
        historical_months=args.months,
        max_retries_per_scenario=args.max_retries
    )
    
    # Run all scenarios
    automation.run_all_scenarios()
    
    # Generate reports
    automation.generate_aggregated_report(output_dir=args.output_dir)
    
    # Exit with error code if any failures
    failed = sum(1 for r in automation.all_results if r.get('status') in ['failed', 'error'])
    if failed > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

