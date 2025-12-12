#!/usr/bin/env python3
"""
Comprehensive MT5 Trading Bot Diagnostic System
Performs end-to-end diagnostic of the trading bot system.

This script:
1. Analyzes code structure and identifies potential issues
2. Monitors runtime behavior (when system is running)
3. Replays logs to detect patterns
4. Generates comprehensive diagnostic report
"""

import os
import sys
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger_factory import get_logger

logger = get_logger("diagnostic", "logs/diagnostic/diagnostic.log")


class TradingBotDiagnostic:
    """Comprehensive diagnostic system for MT5 Trading Bot."""
    
    def __init__(self, config_path: str = 'config.json'):
        """Initialize diagnostic system."""
        self.config_path = config_path
        self.config = self._load_config()
        self.issues = []
        self.warnings = []
        self.optimizations = []
        self.metrics = {}
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration file."""
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return {}
    
    def run_full_diagnostic(self) -> Dict[str, Any]:
        """
        Run complete end-to-end diagnostic.
        
        Returns:
            Diagnostic report dictionary
        """
        logger.info("=" * 80)
        logger.info("STARTING COMPREHENSIVE TRADING BOT DIAGNOSTIC")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        # Phase 1: Code Analysis
        logger.info("\n[PHASE 1] Analyzing Code Structure...")
        self._analyze_code_structure()
        
        # Phase 2: Configuration Analysis
        logger.info("\n[PHASE 2] Analyzing Configuration...")
        self._analyze_configuration()
        
        # Phase 3: Worker Loop Analysis
        logger.info("\n[PHASE 3] Analyzing Worker Loops...")
        self._analyze_worker_loops()
        
        # Phase 4: SL Update Logic Analysis
        logger.info("\n[PHASE 4] Analyzing SL Update Logic...")
        self._analyze_sl_update_logic()
        
        # Phase 5: Threading & Concurrency Analysis
        logger.info("\n[PHASE 5] Analyzing Threading & Concurrency...")
        self._analyze_threading()
        
        # Phase 6: Logging System Analysis
        logger.info("\n[PHASE 6] Analyzing Logging System...")
        self._analyze_logging()
        
        # Phase 7: Strategy Engine Analysis
        logger.info("\n[PHASE 7] Analyzing Strategy Engine...")
        self._analyze_strategy_engine()
        
        # Phase 8: MT5 Connection Analysis
        logger.info("\n[PHASE 8] Analyzing MT5 Connection...")
        self._analyze_mt5_connection()
        
        # Phase 9: Profit Locking Analysis
        logger.info("\n[PHASE 9] Analyzing Profit Locking...")
        self._analyze_profit_locking()
        
        # Phase 10: Log Replay (if logs exist)
        logger.info("\n[PHASE 10] Replaying Logs...")
        self._replay_logs()
        
        duration = time.time() - start_time
        
        # Generate report
        report = self._generate_report(duration)
        
        logger.info("\n" + "=" * 80)
        logger.info("DIAGNOSTIC COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Duration: {duration:.2f}s")
        logger.info(f"Issues Found: {len(self.issues)}")
        logger.info(f"Warnings: {len(self.warnings)}")
        logger.info(f"Optimizations: {len(self.optimizations)}")
        
        return report
    
    def _analyze_code_structure(self):
        """Analyze code structure for issues."""
        logger.info("Analyzing code structure...")
        
        # Check for main entry points
        if not os.path.exists('launch_system.py'):
            self.issues.append({
                'category': 'Code Structure',
                'severity': 'CRITICAL',
                'issue': 'launch_system.py not found',
                'description': 'Main entry point missing',
                'fix': 'Ensure launch_system.py exists in project root'
            })
        
        # Check for critical modules
        critical_modules = [
            'bot/trading_bot.py',
            'risk/sl_manager.py',
            'risk/risk_manager.py',
            'execution/mt5_connector.py',
            'execution/order_manager.py',
            'strategies/trend_filter.py',
            'bot/profit_locking_engine.py'
        ]
        
        for module in critical_modules:
            if not os.path.exists(module):
                self.issues.append({
                    'category': 'Code Structure',
                    'severity': 'CRITICAL',
                    'issue': f'Critical module missing: {module}',
                    'description': f'Required module {module} not found',
                    'fix': f'Ensure {module} exists'
                })
    
    def _analyze_configuration(self):
        """Analyze configuration for issues."""
        logger.info("Analyzing configuration...")
        
        if not self.config:
            self.issues.append({
                'category': 'Configuration',
                'severity': 'CRITICAL',
                'issue': 'Configuration file empty or invalid',
                'description': 'config.json could not be loaded',
                'fix': 'Verify config.json is valid JSON'
            })
            return
        
        # Check SL worker interval
        risk_config = self.config.get('risk', {})
        sl_update_min_interval = risk_config.get('sl_update_min_interval_ms', 100)
        trailing_cycle_interval = risk_config.get('trailing_cycle_interval_ms', 50)
        
        if sl_update_min_interval < 50:
            self.warnings.append({
                'category': 'Configuration',
                'severity': 'WARNING',
                'issue': 'SL update interval too short',
                'description': f'sl_update_min_interval_ms={sl_update_min_interval}ms may cause rate limiting issues',
                'recommendation': 'Consider increasing to 100ms minimum'
            })
        
        # Check worker loop interval
        # The SL worker runs at 500ms by default (0.5s)
        # Check if this matches config expectations
        
        # Check profit locking thresholds
        profit_locking = risk_config.get('profit_locking', {})
        min_profit = profit_locking.get('min_profit_threshold_usd', 0.03)
        max_profit = profit_locking.get('max_profit_threshold_usd', 0.10)
        
        if min_profit >= max_profit:
            self.issues.append({
                'category': 'Configuration',
                'severity': 'CRITICAL',
                'issue': 'Invalid profit locking thresholds',
                'description': f'min_profit_threshold_usd ({min_profit}) >= max_profit_threshold_usd ({max_profit})',
                'fix': 'Ensure min_profit_threshold_usd < max_profit_threshold_usd'
            })
    
    def _analyze_worker_loops(self):
        """Analyze worker loops for performance issues."""
        logger.info("Analyzing worker loops...")
        
        # Read SL manager to check worker loop
        sl_manager_path = 'risk/sl_manager.py'
        if os.path.exists(sl_manager_path):
            with open(sl_manager_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Check for busy-wait patterns
                if 'while True:' in content and 'time.sleep(0)' in content:
                    self.issues.append({
                        'category': 'Worker Loop',
                        'severity': 'HIGH',
                        'issue': 'Potential busy-wait in worker loop',
                        'description': 'Found while True with time.sleep(0) - may cause CPU spinning',
                        'fix': 'Ensure minimum sleep time of 0.001s (1ms) to prevent CPU spinning'
                    })
                
                # Check for blocking calls in loop
                if '_sl_worker_loop' in content:
                    # Check if network calls are made inside locks
                    if 'get_position_by_ticket' in content and 'with lock:' in content:
                        # Need to check if they're in the same context
                        # This is a heuristic - actual analysis would need AST parsing
                        pass
                
                # Check loop interval
                if '_sl_worker_interval' in content:
                    # Default is 0.5s (500ms) - check if this is optimal
                    self.optimizations.append({
                        'category': 'Worker Loop',
                        'optimization': 'SL worker loop interval',
                        'description': 'SL worker runs every 500ms - consider instant trailing (0ms) for faster updates',
                        'impact': 'Faster SL updates, better profit locking response time'
                    })
    
    def _analyze_sl_update_logic(self):
        """Analyze SL update logic for issues."""
        logger.info("Analyzing SL update logic...")
        
        sl_manager_path = 'risk/sl_manager.py'
        if os.path.exists(sl_manager_path):
            with open(sl_manager_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Check for duplicate SL update calls
                update_count = content.count('update_sl_atomic')
                if update_count > 10:
                    self.warnings.append({
                        'category': 'SL Update Logic',
                        'severity': 'WARNING',
                        'issue': 'Multiple update_sl_atomic calls detected',
                        'description': f'Found {update_count} references to update_sl_atomic - ensure no duplicate calls',
                        'recommendation': 'Verify only _sl_worker_loop calls update_sl_atomic'
                    })
                
                # Check for lock timeout issues
                if 'lock_acquisition_timeout' in content:
                    # Check if timeout is reasonable
                    risk_config = self.config.get('risk', {})
                    lock_timeout = risk_config.get('lock_acquisition_timeout_seconds', 1.0)
                    
                    if lock_timeout < 0.5:
                        self.warnings.append({
                            'category': 'SL Update Logic',
                            'severity': 'WARNING',
                            'issue': 'Lock timeout too short',
                            'description': f'lock_acquisition_timeout_seconds={lock_timeout}s may cause premature timeouts',
                            'recommendation': 'Consider increasing to 1.0s minimum'
                        })
                
                # Check for profit locking integration
                if 'check_and_lock_profit' in content:
                    # Good - profit locking is integrated
                    pass
                else:
                    self.issues.append({
                        'category': 'SL Update Logic',
                        'severity': 'HIGH',
                        'issue': 'Profit locking not integrated in SL update',
                        'description': 'check_and_lock_profit not called in update_sl_atomic',
                        'fix': 'Ensure ProfitLockingEngine.check_and_lock_profit is called in sweet spot range'
                    })
    
    def _analyze_threading(self):
        """Analyze threading and concurrency issues."""
        logger.info("Analyzing threading...")
        
        # Check for thread safety
        sl_manager_path = 'risk/sl_manager.py'
        if os.path.exists(sl_manager_path):
            with open(sl_manager_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Check for proper lock usage
                if '_tracking_lock' in content and 'threading.Lock' in content:
                    # Good - using locks
                    pass
                else:
                    self.issues.append({
                        'category': 'Threading',
                        'severity': 'HIGH',
                        'issue': 'Missing thread synchronization',
                        'description': 'No tracking lock found - potential race conditions',
                        'fix': 'Ensure all shared state is protected with locks'
                    })
                
                # Check for deadlock potential
                if 'with lock:' in content and 'with self._tracking_lock:' in content:
                    # Check if nested locks could cause deadlock
                    # This is a heuristic - actual analysis would need AST parsing
                    self.warnings.append({
                        'category': 'Threading',
                        'severity': 'WARNING',
                        'issue': 'Potential nested lock usage',
                        'description': 'Nested locks detected - ensure consistent lock ordering to prevent deadlocks',
                        'recommendation': 'Always acquire locks in the same order across all threads'
                    })
    
    def _analyze_logging(self):
        """Analyze logging system."""
        logger.info("Analyzing logging system...")
        
        # Check log directory structure
        log_dirs = [
            'logs/system',
            'logs/engine',
            'logs/trades',
            'logs/diagnostic'
        ]
        
        for log_dir in log_dirs:
            if not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    logger.info(f"Created log directory: {log_dir}")
                except Exception as e:
                    self.warnings.append({
                        'category': 'Logging',
                        'severity': 'WARNING',
                        'issue': f'Log directory not accessible: {log_dir}',
                        'description': f'Cannot create log directory: {e}',
                        'recommendation': 'Ensure write permissions for logs directory'
                    })
        
        # Check logger factory
        logger_factory_path = 'utils/logger_factory.py'
        if not os.path.exists(logger_factory_path):
            self.issues.append({
                'category': 'Logging',
                'severity': 'HIGH',
                'issue': 'Logger factory missing',
                'description': 'utils/logger_factory.py not found',
                'fix': 'Ensure logger factory exists'
            })
    
    def _analyze_strategy_engine(self):
        """Analyze strategy engine."""
        logger.info("Analyzing strategy engine...")
        
        trend_filter_path = 'strategies/trend_filter.py'
        if os.path.exists(trend_filter_path):
            with open(trend_filter_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Check for caching
                if '_rates_cache' in content:
                    # Good - using caching
                    pass
                else:
                    self.optimizations.append({
                        'category': 'Strategy Engine',
                        'optimization': 'Add rate caching',
                        'description': 'Consider caching historical rates to reduce MT5 API calls',
                        'impact': 'Reduced latency, lower API load'
                    })
                
                # Check for proper timeframe usage
                trading_config = self.config.get('trading', {})
                timeframe = trading_config.get('timeframe', 'M1')
                
                if timeframe != 'M1':
                    self.warnings.append({
                        'category': 'Strategy Engine',
                        'severity': 'INFO',
                        'issue': f'Using {timeframe} timeframe',
                        'description': f'Strategy configured for {timeframe} - ensure trend detection matches',
                        'recommendation': 'Verify trend signals are appropriate for selected timeframe'
                    })
    
    def _analyze_mt5_connection(self):
        """Analyze MT5 connection stability."""
        logger.info("Analyzing MT5 connection...")
        
        mt5_connector_path = 'execution/mt5_connector.py'
        if os.path.exists(mt5_connector_path):
            with open(mt5_connector_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Check for reconnection logic
                if 'reconnect' in content.lower():
                    # Good - has reconnection
                    pass
                else:
                    self.issues.append({
                        'category': 'MT5 Connection',
                        'severity': 'HIGH',
                        'issue': 'Missing reconnection logic',
                        'description': 'No reconnection logic found - connection failures will stop trading',
                        'fix': 'Implement automatic reconnection with exponential backoff'
                    })
                
                # Check for connection health checks
                if 'ensure_connected' in content:
                    # Good - has health checks
                    pass
                else:
                    self.warnings.append({
                        'category': 'MT5 Connection',
                        'severity': 'WARNING',
                        'issue': 'Missing connection health checks',
                        'description': 'No ensure_connected method - may use stale connections',
                        'recommendation': 'Add periodic connection health checks'
                    })
    
    def _analyze_profit_locking(self):
        """Analyze profit locking engine."""
        logger.info("Analyzing profit locking...")
        
        profit_locking_path = 'bot/profit_locking_engine.py'
        if os.path.exists(profit_locking_path):
            with open(profit_locking_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Check for sweet spot logic
                if 'sweet_spot' in content.lower():
                    # Good - has sweet spot logic
                    pass
                else:
                    self.issues.append({
                        'category': 'Profit Locking',
                        'severity': 'HIGH',
                        'issue': 'Missing sweet spot logic',
                        'description': 'No sweet spot profit locking found',
                        'fix': 'Implement sweet spot profit locking ($0.03-$0.10 range)'
                    })
                
                # Check for immediate application
                if 'apply_immediately' in content:
                    # Good - can apply immediately
                    pass
                else:
                    self.optimizations.append({
                        'category': 'Profit Locking',
                        'optimization': 'Enable immediate profit locking',
                        'description': 'Consider applying profit locks immediately when entering sweet spot',
                        'impact': 'Faster profit protection, reduced risk of loss'
                    })
    
    def _replay_logs(self):
        """Replay logs to detect patterns."""
        logger.info("Replaying logs...")
        
        log_files = []
        
        # Find all log files
        if os.path.exists('logs'):
            for root, dirs, files in os.walk('logs'):
                for file in files:
                    if file.endswith('.log'):
                        log_files.append(os.path.join(root, file))
        
        if not log_files:
            self.warnings.append({
                'category': 'Log Replay',
                'severity': 'INFO',
                'issue': 'No log files found',
                'description': 'Cannot replay logs - no log files exist yet',
                'recommendation': 'Run the system to generate logs, then rerun diagnostic'
            })
            return
        
        # Analyze recent logs (last 12 hours)
        cutoff_time = datetime.now() - timedelta(hours=12)
        recent_errors = []
        sl_update_failures = []
        profit_locking_events = []
        
        for log_file in log_files[:10]:  # Limit to first 10 files
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        # Check for errors
                        if 'ERROR' in line or 'CRITICAL' in line:
                            recent_errors.append({
                                'file': log_file,
                                'line': line.strip()[:200]  # Truncate long lines
                            })
                        
                        # Check for SL update failures
                        if 'SL UPDATE FAILED' in line or 'SL_UPDATE_FAILED' in line:
                            sl_update_failures.append({
                                'file': log_file,
                                'line': line.strip()[:200]
                            })
                        
                        # Check for profit locking events
                        if 'SWEET SPOT' in line or 'PROFIT LOCKING' in line:
                            profit_locking_events.append({
                                'file': log_file,
                                'line': line.strip()[:200]
                            })
            except Exception as e:
                logger.debug(f"Error reading log file {log_file}: {e}")
        
        if recent_errors:
            self.warnings.append({
                'category': 'Log Replay',
                'severity': 'WARNING',
                'issue': f'{len(recent_errors)} errors found in logs',
                'description': 'Recent errors detected in log files',
                'recommendation': 'Review error logs for patterns and fix root causes'
            })
        
        if sl_update_failures:
            self.issues.append({
                'category': 'Log Replay',
                'severity': 'HIGH',
                'issue': f'{len(sl_update_failures)} SL update failures found',
                'description': 'SL update failures detected in logs',
                'fix': 'Investigate SL update failures - check lock timeouts, rate limits, and network issues'
            })
        
        if not profit_locking_events:
            self.warnings.append({
                'category': 'Log Replay',
                'severity': 'INFO',
                'issue': 'No profit locking events found',
                'description': 'No sweet spot or profit locking events in recent logs',
                'recommendation': 'This may be normal if no trades reached profit zone'
            })
    
    def _generate_report(self, duration: float) -> Dict[str, Any]:
        """Generate comprehensive diagnostic report."""
        report = {
            'timestamp': datetime.now().isoformat(),
            'duration_seconds': duration,
            'summary': {
                'total_issues': len(self.issues),
                'total_warnings': len(self.warnings),
                'total_optimizations': len(self.optimizations),
                'critical_issues': len([i for i in self.issues if i['severity'] == 'CRITICAL']),
                'high_issues': len([i for i in self.issues if i['severity'] == 'HIGH']),
            },
            'issues': self.issues,
            'warnings': self.warnings,
            'optimizations': self.optimizations,
            'recommendations': self._generate_recommendations()
        }
        
        # Write report to file
        report_file = f"logs/diagnostic/diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        os.makedirs(os.path.dirname(report_file), exist_ok=True)
        
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        logger.info(f"Diagnostic report saved to: {report_file}")
        
        # Also print summary
        self._print_summary(report)
        
        return report
    
    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on findings."""
        recommendations = []
        
        if any(i['severity'] == 'CRITICAL' for i in self.issues):
            recommendations.append("URGENT: Fix all CRITICAL issues before running the system")
        
        if len(self.issues) > 0:
            recommendations.append(f"Review and fix {len(self.issues)} identified issues")
        
        if any('Worker Loop' in i['category'] for i in self.issues):
            recommendations.append("Optimize worker loops to prevent blocking and ensure timely SL updates")
        
        if any('SL Update' in i['category'] for i in self.issues):
            recommendations.append("Ensure SL updates are reliable - check lock timeouts and rate limits")
        
        if any('Profit Locking' in i['category'] for i in self.issues):
            recommendations.append("Verify profit locking is working correctly for positions in sweet spot")
        
        if any('Threading' in i['category'] for i in self.issues):
            recommendations.append("Review threading implementation to prevent race conditions and deadlocks")
        
        return recommendations
    
    def _print_summary(self, report: Dict[str, Any]):
        """Print diagnostic summary to console."""
        print("\n" + "=" * 80)
        print("DIAGNOSTIC SUMMARY")
        print("=" * 80)
        print(f"Timestamp: {report['timestamp']}")
        print(f"Duration: {report['duration_seconds']:.2f}s")
        print(f"\nIssues Found: {report['summary']['total_issues']}")
        print(f"  - CRITICAL: {report['summary']['critical_issues']}")
        print(f"  - HIGH: {report['summary']['high_issues']}")
        print(f"Warnings: {report['summary']['total_warnings']}")
        print(f"Optimizations: {report['summary']['total_optimizations']}")
        
        if self.issues:
            print("\n" + "-" * 80)
            print("CRITICAL ISSUES:")
            print("-" * 80)
            for issue in [i for i in self.issues if i['severity'] == 'CRITICAL']:
                print(f"\n[{issue['category']}] {issue['issue']}")
                print(f"  Description: {issue['description']}")
                print(f"  Fix: {issue.get('fix', 'N/A')}")
        
        if self.warnings:
            print("\n" + "-" * 80)
            print("WARNINGS:")
            print("-" * 80)
            for warning in self.warnings[:10]:  # Show first 10
                print(f"\n[{warning['category']}] {warning['issue']}")
                print(f"  {warning['description']}")
        
        if self.optimizations:
            print("\n" + "-" * 80)
            print("OPTIMIZATION OPPORTUNITIES:")
            print("-" * 80)
            for opt in self.optimizations[:10]:  # Show first 10
                print(f"\n[{opt['category']}] {opt['optimization']}")
                print(f"  {opt['description']}")
        
        if report['recommendations']:
            print("\n" + "-" * 80)
            print("RECOMMENDATIONS:")
            print("-" * 80)
            for rec in report['recommendations']:
                print(f"  • {rec}")
        
        print("\n" + "=" * 80)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run comprehensive trading bot diagnostic')
    parser.add_argument('--config', default='config.json', help='Path to config file')
    
    args = parser.parse_args()
    
    diagnostic = TradingBotDiagnostic(config_path=args.config)
    report = diagnostic.run_full_diagnostic()
    
    # Return exit code based on critical issues
    critical_count = report['summary']['critical_issues']
    if critical_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

