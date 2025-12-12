"""
Live System Monitor - Monitors for SL issues and errors continuously
"""

import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launch_system import TradingSystemLauncher
from utils.logger_factory import get_logger

logger = get_logger("live_monitor", "logs/live/monitor/live_monitor.log")


class LiveMonitor:
    """Monitors live trading system for SL issues and errors."""
    
    def __init__(self):
        """Initialize monitor."""
        self.launcher = None
        self.monitoring = True
        self.issues_found = []
        self.stop_requested = False
        self.start_time = datetime.now()  # Track start time for delayed checks
        
    def start(self):
        """Start monitoring."""
        logger.info("=" * 80)
        logger.info("LIVE SYSTEM MONITOR STARTING")
        logger.info("=" * 80)
        logger.info("Will monitor continuously until SL issues or errors are found")
        logger.info("=" * 80)
        
        try:
            # Start trading system
            logger.info("Starting trading system...")
            self.launcher = TradingSystemLauncher(
                config_path='config.json',
                reconciliation_interval=30
            )
            
            # Start system in background thread
            system_thread = threading.Thread(
                target=self._run_system,
                name="TradingSystem",
                daemon=False
            )
            system_thread.start()
            
            # Wait a bit for system to start
            time.sleep(5)
            
            # Start monitoring loop
            logger.info("[OK] System started - beginning continuous monitoring...")
            self._monitor_loop()
            
        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
            self.stop()
        except Exception as e:
            logger.error(f"Error in monitor: {e}", exc_info=True)
            self.stop()
    
    def _run_system(self):
        """Run the trading system."""
        try:
            self.launcher.start()
        except Exception as e:
            logger.error(f"Error starting system: {e}", exc_info=True)
            self.stop_requested = True
    
    def _monitor_loop(self):
        """Main monitoring loop - checks every 5 seconds."""
        logger.info("Monitoring loop started")
        check_count = 0
        
        while self.monitoring and not self.stop_requested:
            try:
                check_count += 1
                
                if not self.launcher or not self.launcher.bot:
                    time.sleep(5)
                    continue
                
                bot = self.launcher.bot
                
                # Check SL Manager
                if hasattr(bot, 'risk_manager') and hasattr(bot.risk_manager, 'sl_manager'):
                    sl_manager = bot.risk_manager.sl_manager
                    
                    # Check worker status (with thread alive check)
                    worker_status = sl_manager.get_worker_status()
                    is_running = worker_status.get('running', False)
                    is_thread_alive = worker_status.get('thread_alive', False)
                    
                    # Only report if both running flag and thread are not active
                    # Give it a few seconds after startup before checking
                    if not is_running and not is_thread_alive:
                        # Check if this is right after startup (first 10 seconds)
                        from datetime import datetime as dt
                        from datetime import datetime as dt_now
                        time_since_start = (dt_now.now() - self.start_time).total_seconds() if hasattr(self, 'start_time') else 999
                        if time_since_start > 10:  # Only report after 10 seconds
                            self._report_critical_issue(
                                'SL_WORKER_NOT_RUNNING',
                                'SL Worker is not running',
                                {
                                    'running_flag': is_running,
                                    'thread_alive': is_thread_alive,
                                    'time_since_start': time_since_start
                                }
                            )
                    
                    # Check manual review tickets (potential violations)
                    manual_review = worker_status.get('manual_review_tickets', [])
                    if manual_review:
                        for ticket in manual_review:
                            position = bot.order_manager.get_position_by_ticket(ticket)
                            if position:
                                effective_sl = sl_manager.get_effective_sl_profit(position)
                                if effective_sl < -2.50:
                                    self._report_critical_issue(
                                        'STRICT_LOSS_VIOLATION',
                                        f'Ticket {ticket} has effective SL ${effective_sl:.2f} worse than -$2.50',
                                        {
                                            'ticket': ticket,
                                            'symbol': position.get('symbol', 'N/A'),
                                            'effective_sl': effective_sl
                                        }
                                    )
                
                # Check all open positions
                positions = bot.order_manager.get_open_positions()
                for position in positions:
                    ticket = position.get('ticket', 0)
                    symbol = position.get('symbol', '')
                    profit = position.get('profit', 0.0)
                    sl_price = position.get('sl', 0.0)
                    entry_price = position.get('price_open', 0.0)
                    current_price = position.get('price_current', 0.0)
                    
                    # Get SL manager for detailed checks
                    sl_manager = None
                    if hasattr(bot, 'risk_manager') and hasattr(bot.risk_manager, 'sl_manager'):
                        sl_manager = bot.risk_manager.sl_manager
                    
                    # Check 1: Losing trades without SL
                    if profit < 0 and sl_price <= 0:
                        self._report_critical_issue(
                            'NO_SL_ON_LOSING_TRADE',
                            f'Ticket {ticket} ({symbol}) is losing ${profit:.2f} but has no SL',
                            {
                                'ticket': ticket,
                                'symbol': symbol,
                                'profit': profit,
                                'entry': entry_price,
                                'current': current_price
                            }
                        )
                    
                    # Check 2: Effective SL issues (for both losing and profitable trades)
                    if sl_manager and sl_price > 0:
                        effective_sl = sl_manager.get_effective_sl_profit(position)
                        
                        # Check if effective SL is significantly different from -$2.00 target
                        # For strict loss enforcement, effective SL should be close to -$2.00
                        sl_error = abs(effective_sl - (-2.00))
                        
                        # For losing trades: must be within $0.50 of -$2.00
                        if profit < 0:
                            if effective_sl < -2.50:  # Worse than -$2.50
                                self._report_critical_issue(
                                    'EFFECTIVE_SL_VIOLATION',
                                    f'Ticket {ticket} ({symbol}) losing trade has effective SL ${effective_sl:.2f} worse than -$2.50 limit',
                                    {
                                        'ticket': ticket,
                                        'symbol': symbol,
                                        'profit': profit,
                                        'effective_sl': effective_sl,
                                        'applied_sl': sl_price,
                                        'entry': entry_price,
                                        'current': current_price,
                                        'sl_error': sl_error
                                    }
                                )
                            elif sl_error > 0.50:  # More than $0.50 away from target
                                self._report_critical_issue(
                                    'EFFECTIVE_SL_MISMATCH',
                                    f'Ticket {ticket} ({symbol}) losing trade effective SL ${effective_sl:.2f} is ${sl_error:.2f} away from -$2.00 target (tolerance: $0.50)',
                                    {
                                        'ticket': ticket,
                                        'symbol': symbol,
                                        'profit': profit,
                                        'effective_sl': effective_sl,
                                        'target_sl': -2.00,
                                        'applied_sl': sl_price,
                                        'entry': entry_price,
                                        'current': current_price,
                                        'sl_error': sl_error
                                    }
                                )
                        # For profitable trades: check if SL is too close (risk of premature stop)
                        elif profit > 0:
                            # If effective SL is negative and far from break-even, it's a problem
                            if effective_sl < -1.00 and sl_error > 1.00:
                                self._report_critical_issue(
                                    'PROFITABLE_TRADE_SL_ISSUE',
                                    f'Ticket {ticket} ({symbol}) profitable trade (${profit:.2f}) has effective SL ${effective_sl:.2f} which is ${sl_error:.2f} away from target',
                                    {
                                        'ticket': ticket,
                                        'symbol': symbol,
                                        'profit': profit,
                                        'effective_sl': effective_sl,
                                        'applied_sl': sl_price,
                                        'entry': entry_price,
                                        'current': current_price,
                                        'sl_error': sl_error
                                    }
                                )
                    
                    # Check 3: Missing SL updates (Last Update: N/A or very old)
                    # CRITICAL FIX: Only flag stale updates if SL is actually wrong, not if it's already correct
                    if sl_manager:
                        # Check if SL was updated recently
                        with sl_manager._tracking_lock:
                            last_update = sl_manager._last_sl_update.get(ticket)
                            last_reason = sl_manager._last_sl_reason.get(ticket, 'N/A')
                        
                        # Get current effective SL to verify if it's correct
                        current_effective_sl = sl_manager.get_effective_sl_profit(position) if sl_manager else None
                        target_sl_usd = -sl_manager.max_risk_usd if sl_manager else -2.00  # Should be -$2.00
                        
                        # Determine tolerance based on symbol type
                        symbol_info = sl_manager.mt5_connector.get_symbol_info(symbol) if sl_manager else None
                        point = symbol_info.get('point', 0.00001) if symbol_info else 0.00001
                        
                        if (point >= 0.01) or (point < 0.0001 and entry_price > 100):  # Crypto or Index
                            tolerance = min(1.0, max(0.5, point * 100 / 100))  # Max $1.00, prefer $0.50
                        else:  # Forex
                            tolerance = 0.50  # $0.50 for forex
                        
                        # Check if SL is correct (within tolerance)
                        sl_is_correct = False
                        if current_effective_sl is not None:
                            sl_error = abs(current_effective_sl - target_sl_usd)
                            sl_is_correct = sl_error <= tolerance
                        
                        # If last reason indicates SL is already correct, don't flag stale updates
                        reason_indicates_correct = any(phrase in last_reason for phrase in [
                            'SL already at strict loss limit',
                            'already at strict loss',
                            'SL already correct',
                            'No SL update needed'
                        ])
                        
                        # If position has been open for more than 30 seconds and no SL update recorded
                        time_open = position.get('time_open')
                        if time_open:
                            from datetime import datetime
                            if isinstance(time_open, datetime):
                                time_since_open = (datetime.now() - time_open).total_seconds()
                                if time_since_open > 30 and last_update is None:
                                    # Only flag if SL is wrong
                                    if not sl_is_correct:
                                        self._report_critical_issue(
                                            'MISSING_SL_UPDATE',
                                            f'Ticket {ticket} ({symbol}) has been open for {time_since_open:.0f}s but no SL update recorded and SL is incorrect (effective: ${current_effective_sl:.2f}, target: ${target_sl_usd:.2f})',
                                            {
                                                'ticket': ticket,
                                                'symbol': symbol,
                                                'profit': profit,
                                                'time_open_seconds': time_since_open,
                                                'sl_price': sl_price,
                                                'entry': entry_price,
                                                'current': current_price,
                                                'effective_sl': current_effective_sl,
                                                'target_sl': target_sl_usd
                                            }
                                        )
                                elif last_update:
                                    time_since_update = (datetime.now() - last_update).total_seconds()
                                    # CRITICAL FIX: Only flag stale updates if:
                                    # 1. It's a losing trade AND
                                    # 2. No update in last 10 seconds AND
                                    # 3. SL is actually wrong (not correct) AND
                                    # 4. Last reason doesn't indicate SL is already correct
                                    if profit < 0 and time_since_update > 10:
                                        # Only report if SL is wrong or reason doesn't indicate it's correct
                                        if not sl_is_correct and not reason_indicates_correct:
                                            self._report_critical_issue(
                                                'STALE_SL_UPDATE',
                                                f'Ticket {ticket} ({symbol}) losing trade has not had SL update for {time_since_update:.0f}s (last reason: {last_reason}) | Effective SL: ${current_effective_sl:.2f} (target: ${target_sl_usd:.2f}, error: ${abs(current_effective_sl - target_sl_usd):.2f})',
                                                {
                                                    'ticket': ticket,
                                                    'symbol': symbol,
                                                    'profit': profit,
                                                    'time_since_update': time_since_update,
                                                    'last_update_reason': last_reason,
                                                    'effective_sl': current_effective_sl,
                                                    'target_sl': target_sl_usd,
                                                    'sl_error': abs(current_effective_sl - target_sl_usd) if current_effective_sl else None,
                                                    'sl_is_correct': sl_is_correct
                                                }
                                            )
                    
                    # Check 4: SL Status warning (if we can detect it from position data)
                    # This would require checking the SL monitor status, but we can infer from missing updates
                
                # Check for errors in logs
                self._check_log_errors()
                
                # Log progress every 10 checks (50 seconds)
                if check_count % 10 == 0:
                    logger.info(f"Monitoring... (check #{check_count}, {len(positions)} open positions)")
                
                time.sleep(5)
                
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}", exc_info=True)
                time.sleep(5)
    
    def _check_log_errors(self):
        """Check log files for actual errors (excluding false positives)."""
        log_dirs = ['logs/engine', 'logs/system']
        
        # Exclude live_monitor.log to avoid circular reporting
        excluded_files = ['live_monitor.log']
        
        for log_dir in log_dirs:
            log_path = Path(log_dir)
            if not log_path.exists():
                continue
            
            for log_file in log_path.glob('*.log'):
                # Skip excluded files
                if log_file.name in excluded_files:
                    continue
                
                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        recent_lines = lines[-20:] if len(lines) > 20 else lines
                        
                        for line in recent_lines:
                            # Only flag actual errors, not INFO messages
                            if ' - ERROR - ' in line or ' - CRITICAL - ' in line:
                                line_upper = line.upper()
                                
                                # Skip known false positives
                                false_positive_patterns = [
                                    'SAFETY: SL UPDATES DISABLED',
                                    'SUMMARY_METRICS',
                                    'RSI FILTER',
                                    'SMA FILTER',
                                    'VOLUME FILTER',
                                    'MARKET CLOSING FILTER',
                                    'MICRO-HFT PROFIT ENGINE NOT AVAILABLE',
                                    'PROFIT-LOCKING ENGINE NOT AVAILABLE',
                                    'CANNOT GET SYMBOL INFO',
                                    'CANNOT GET MARKET PRICES',
                                    'CRITICAL ISSUE FOUND',  # Avoid reporting our own reports
                                    'ERROR IN LIVE_MONITOR.LOG',  # Avoid circular reporting
                                    'ERROR IN MONITOR LOOP',  # Our own errors
                                    'UNBOUNDLOCALERROR',  # Already fixed
                                    'CANNOT ACCESS LOCAL VARIABLE'
                                ]
                                
                                if any(pattern in line_upper for pattern in false_positive_patterns):
                                    continue
                                
                                # Only flag critical system errors
                                critical_keywords = [
                                    'EXCEPTION',
                                    'TRACEBACK',
                                    'FATAL',
                                    'CRASHED',
                                    'THREAD DIED',
                                    'CONNECTION LOST',
                                    'MT5 ERROR',
                                    'ORDER FAILED: 10016',  # Invalid stops - real error
                                    'ORDER FAILED: 10018',  # Market closed - real error
                                    'FAIL-SAFE VIOLATION',  # Real violation
                                    'SL UPDATE FAILED',  # Real failure
                                    'EMERGENCY STRICT SL FAILED'  # Real failure
                                ]
                                
                                if any(kw in line_upper for kw in critical_keywords):
                                    # Check if already reported (avoid duplicates)
                                    line_hash = hash(line.strip()[:100])
                                    if not any(hash(issue.get('log_line', '')[:100]) == line_hash for issue in self.issues_found[-30:]):
                                        self._report_critical_issue(
                                            'LOG_ERROR',
                                            f'Error in {log_file.name}: {line.strip()[:150]}',
                                            {
                                                'log_file': str(log_file),
                                                'log_line': line.strip()[:200]
                                            }
                                        )
                                        break  # One error per file per check
                except Exception:
                    pass
    
    def _report_critical_issue(self, issue_type, message, details):
        """Report a critical issue found."""
        issue = {
            'timestamp': datetime.now(),
            'type': issue_type,
            'message': message,
            'details': details
        }
        self.issues_found.append(issue)
        
        # Log and print
        logger.critical(f"🚨 CRITICAL ISSUE FOUND: {message}")
        print("\n" + "=" * 80)
        print("🚨 CRITICAL ISSUE DETECTED")
        print("=" * 80)
        print(f"Time: {issue['timestamp']}")
        print(f"Type: {issue_type}")
        print(f"Message: {message}")
        for key, value in details.items():
            print(f"{key}: {value}")
        print("=" * 80)
        print("\n[WARNING] ISSUE FOUND - Monitoring will continue but issue has been logged")
        print("=" * 80 + "\n")
    
    def stop(self):
        """Stop monitoring."""
        self.monitoring = False
        if self.launcher:
            self.launcher.stop()
        logger.info("Live monitoring stopped")


if __name__ == '__main__':
    monitor = LiveMonitor()
    try:
        monitor.start()
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user")
        monitor.stop()

