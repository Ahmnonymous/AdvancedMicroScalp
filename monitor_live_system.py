"""
Live System Monitor
Monitors the trading system for SL issues and errors continuously.
"""

import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from launch_system import TradingSystemLauncher
from utils.logger_factory import get_logger

logger = get_logger("live_monitor", "logs/live_monitor.log")


class LiveSystemMonitor:
    """Monitors live trading system for SL issues and errors."""
    
    def __init__(self):
        """Initialize live monitor."""
        self.launcher = None
        self.monitoring = False
        self.issues_found = []
        self.monitor_thread = None
        
    def start_monitoring(self):
        """Start monitoring the system."""
        logger.info("=" * 80)
        logger.info("LIVE SYSTEM MONITOR STARTING")
        logger.info("=" * 80)
        logger.info("Monitoring for SL issues and errors...")
        logger.info("Will continue until issues are found")
        logger.info("=" * 80)
        
        try:
            # Start the trading system
            logger.info("Starting trading system...")
            self.launcher = TradingSystemLauncher(
                config_path='config.json',
                reconciliation_interval=30
            )
            
            # Start system in a separate thread
            self.monitoring = True
            self.monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="LiveMonitor",
                daemon=False
            )
            self.monitor_thread.start()
            
            # Start the trading system
            success = self.launcher.start()
            
            if not success:
                logger.error("Failed to start trading system")
                return False
            
            # Keep monitoring until issues found
            logger.info("✅ Trading system started - monitoring for issues...")
            self.monitor_thread.join()
            
            return True
            
        except KeyboardInterrupt:
            logger.info("Monitor interrupted by user")
            self.stop()
            return False
        except Exception as e:
            logger.error(f"Error in live monitor: {e}", exc_info=True)
            self.stop()
            return False
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("Monitoring loop started")
        
        check_interval = 5.0  # Check every 5 seconds
        
        while self.monitoring:
            try:
                if not self.launcher or not self.launcher.bot:
                    time.sleep(check_interval)
                    continue
                
                bot = self.launcher.bot
                
                # Check for SL manager
                if hasattr(bot, 'risk_manager') and hasattr(bot.risk_manager, 'sl_manager'):
                    sl_manager = bot.risk_manager.sl_manager
                    
                    # Check worker status
                    worker_status = sl_manager.get_worker_status()
                    if not worker_status.get('running', False):
                        issue = {
                            'timestamp': datetime.now(),
                            'type': 'SL_WORKER_NOT_RUNNING',
                            'severity': 'CRITICAL',
                            'message': 'SL Worker is not running'
                        }
                        self._report_issue(issue)
                    
                    # Check for manual review tickets
                    manual_review = worker_status.get('manual_review_tickets', [])
                    if manual_review:
                        for ticket in manual_review:
                            position = bot.order_manager.get_position_by_ticket(ticket)
                            if position:
                                effective_sl = sl_manager.get_effective_sl_profit(position)
                                if effective_sl < -2.50:  # Worse than -$2.50
                                    issue = {
                                        'timestamp': datetime.now(),
                                        'type': 'STRICT_LOSS_VIOLATION',
                                        'severity': 'CRITICAL',
                                        'ticket': ticket,
                                        'symbol': position.get('symbol', 'N/A'),
                                        'effective_sl': effective_sl,
                                        'message': f'Ticket {ticket} has effective SL ${effective_sl:.2f} worse than -$2.50'
                                    }
                                    self._report_issue(issue)
                    
                    # Check timing stats
                    timing_stats = sl_manager.get_timing_stats()
                    if 'ticket_update_latency' in timing_stats:
                        latency = timing_stats['ticket_update_latency']
                        if latency:
                            p95 = latency.get('p95', 0)
                            if p95 > 250:  # Exceeds 250ms
                                issue = {
                                    'timestamp': datetime.now(),
                                    'type': 'LATENCY_EXCEEDED',
                                    'severity': 'WARNING',
                                    'p95_latency_ms': p95,
                                    'message': f'P95 latency {p95:.2f}ms exceeds 250ms threshold'
                                }
                                self._report_issue(issue)
                
                # Check for open positions with SL issues
                positions = bot.order_manager.get_open_positions()
                for position in positions:
                    ticket = position.get('ticket', 0)
                    symbol = position.get('symbol', '')
                    profit = position.get('profit', 0.0)
                    sl_price = position.get('sl', 0.0)
                    
                    # Check if losing trade has no SL or bad SL
                    if profit < 0:
                        if sl_price <= 0:
                            issue = {
                                'timestamp': datetime.now(),
                                'type': 'NO_SL_ON_LOSING_TRADE',
                                'severity': 'CRITICAL',
                                'ticket': ticket,
                                'symbol': symbol,
                                'profit': profit,
                                'message': f'Ticket {ticket} ({symbol}) is losing ${profit:.2f} but has no SL'
                            }
                            self._report_issue(issue)
                        else:
                            # Check effective SL
                            if hasattr(bot, 'risk_manager') and hasattr(bot.risk_manager, 'sl_manager'):
                                sl_manager = bot.risk_manager.sl_manager
                                effective_sl = sl_manager.get_effective_sl_profit(position)
                                if effective_sl < -2.50:
                                    issue = {
                                        'timestamp': datetime.now(),
                                        'type': 'EFFECTIVE_SL_VIOLATION',
                                        'severity': 'CRITICAL',
                                        'ticket': ticket,
                                        'symbol': symbol,
                                        'profit': profit,
                                        'effective_sl': effective_sl,
                                        'message': f'Ticket {ticket} ({symbol}) effective SL ${effective_sl:.2f} exceeds -$2.50 limit'
                                    }
                                    self._report_issue(issue)
                
                # Check log files for errors
                self._check_log_errors()
                
                # Sleep before next check
                time.sleep(check_interval)
                
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}", exc_info=True)
                issue = {
                    'timestamp': datetime.now(),
                    'type': 'MONITOR_ERROR',
                    'severity': 'ERROR',
                    'message': f'Monitor loop error: {e}'
                }
                self._report_issue(issue)
                time.sleep(check_interval)
    
    def _check_log_errors(self):
        """Check log files for recent errors."""
        log_dirs = [
            'logs/engine',
            'logs/system',
            'logs'
        ]
        
        for log_dir in log_dirs:
            log_path = Path(log_dir)
            if log_path.exists():
                for log_file in log_path.glob('*.log'):
                    try:
                        # Check last 50 lines for errors
                        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                            recent_lines = lines[-50:] if len(lines) > 50 else lines
                            
                            for line in recent_lines:
                                # Only flag actual errors, not INFO messages with "ERROR" in them
                                line_upper = line.upper()
                                # Skip INFO/DEBUG messages that contain "ERROR" as part of normal text
                                if ' - ERROR - ' in line or ' - CRITICAL - ' in line:
                                    # Check if it's a real error (not just informational)
                                    if any(keyword in line_upper for keyword in ['EXCEPTION', 'TRACEBACK', 'FAILED', 'VIOLATION', 'MISMATCH', 'CANNOT', 'UNABLE']):
                                        # Check if this is a new error (not already reported)
                                        if not any(issue.get('log_line') == line.strip() for issue in self.issues_found[-10:]):
                                            # Skip known safe messages
                                            if not any(safe in line_upper for safe in ['SAFETY: SL UPDATES DISABLED', 'SUMMARY_METRICS', 'RSI FILTER FAILED']):
                                                issue = {
                                                    'timestamp': datetime.now(),
                                                    'type': 'LOG_ERROR',
                                                    'severity': 'ERROR',
                                                    'log_file': str(log_file),
                                                    'log_line': line.strip()[:200],  # First 200 chars
                                                    'message': f'Error found in {log_file.name}: {line.strip()[:100]}'
                                                }
                                                self._report_issue(issue)
                                                break  # Only report one error per file per check
                    except Exception as e:
                        logger.debug(f"Could not read log file {log_file}: {e}")
    
    def _report_issue(self, issue):
        """Report an issue found during monitoring."""
        self.issues_found.append(issue)
        
        severity = issue.get('severity', 'INFO')
        message = issue.get('message', 'Unknown issue')
        timestamp = issue.get('timestamp', datetime.now())
        
        # Log the issue
        if severity == 'CRITICAL':
            logger.critical(f"🚨 CRITICAL ISSUE FOUND: {message}")
            print(f"\n{'='*80}")
            print(f"🚨 CRITICAL ISSUE DETECTED")
            print(f"{'='*80}")
            print(f"Time: {timestamp}")
            print(f"Type: {issue.get('type', 'UNKNOWN')}")
            print(f"Message: {message}")
            if 'ticket' in issue:
                print(f"Ticket: {issue['ticket']}")
            if 'symbol' in issue:
                print(f"Symbol: {issue['symbol']}")
            if 'effective_sl' in issue:
                print(f"Effective SL: ${issue['effective_sl']:.2f}")
            print(f"{'='*80}\n")
        elif severity == 'ERROR':
            logger.error(f"❌ ERROR FOUND: {message}")
        else:
            logger.warning(f"⚠️ WARNING: {message}")
        
        # If critical issue found, we can optionally stop monitoring
        # But user said "don't stop until you find a trade with issues"
        # So we'll continue monitoring but report all issues
    
    def stop(self):
        """Stop monitoring."""
        self.monitoring = False
        if self.launcher:
            self.launcher.stop()
        logger.info("Live monitoring stopped")


if __name__ == '__main__':
    monitor = LiveSystemMonitor()
    try:
        monitor.start_monitoring()
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user")
        monitor.stop()
    except Exception as e:
        print(f"\nError: {e}")
        monitor.stop()

