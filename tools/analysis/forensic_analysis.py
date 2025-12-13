#!/usr/bin/env python3
"""
Forensic Analysis Tool for MT5 Trading Bot
Compares LIVE vs BACKTEST performance over 12-hour window
"""

import os
import json
import re
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import glob

class ForensicAnalyzer:
    """Comprehensive forensic analysis of trading bot logs."""
    
    def __init__(self, logs_dir: str = "logs"):
        self.logs_dir = Path(logs_dir)
        self.live_dir = self.logs_dir / "live"
        self.backtest_dir = self.logs_dir / "backtest"
        self.runtime_dir = self.logs_dir / "runtime"
        
        # Analysis results
        self.live_data = {
            'trades': [],
            'sl_updates': [],
            'profit_locks': [],
            'errors': [],
            'system_events': [],
            'threading_issues': [],
            'metrics': {}
        }
        
        self.backtest_data = {
            'trades': [],
            'sl_updates': [],
            'profit_locks': [],
            'errors': [],
            'bars_processed': 0,
            'signals_generated': 0,
            'missed_opportunities': [],
            'metrics': {}
        }
        
        self.divergences = []
        
    def parse_datetime(self, timestamp_str: str) -> Optional[datetime]:
        """Parse various timestamp formats."""
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(timestamp_str[:19], fmt)
            except:
                continue
        return None
    
    def filter_time_window(self, events: List[Dict], hours: int = 12) -> List[Dict]:
        """Filter events to last N hours."""
        if not events:
            return []
        
        # Find most recent timestamp
        latest = max((e.get('timestamp') for e in events if e.get('timestamp')), default=None)
        if not latest:
            return events
        
        if isinstance(latest, str):
            latest = self.parse_datetime(latest)
        
        if not latest:
            return events
        
        cutoff = latest - timedelta(hours=hours)
        return [e for e in events if e.get('timestamp') and 
                (isinstance(e['timestamp'], datetime) and e['timestamp'] >= cutoff or
                 isinstance(e['timestamp'], str) and self.parse_datetime(e['timestamp']) and 
                 self.parse_datetime(e['timestamp']) >= cutoff)]
    
    def parse_live_trade_logs(self) -> List[Dict]:
        """Parse live trade logs from logs/live/trades/"""
        trades = []
        trade_log_dir = self.live_dir / "trades"
        
        if not trade_log_dir.exists():
            return trades
        
        for log_file in trade_log_dir.glob("*.log"):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                symbol = log_file.stem
                current_trade = None
                
                for line in lines:
                    # Parse trade execution
                    if "[OK] TRADE EXECUTED SUCCESSFULLY" in line or "TRADE EXECUTED" in line:
                        # Look for JSON entry
                        for i, l in enumerate(lines[lines.index(line):lines.index(line)+30]):
                            if l.strip().startswith('{'):
                                try:
                                    trade_data = json.loads(l.strip())
                                    trade = {
                                        'timestamp': trade_data.get('timestamp'),
                                        'symbol': trade_data.get('symbol', symbol),
                                        'trade_type': trade_data.get('trade_type'),
                                        'ticket': trade_data.get('order_id'),
                                        'entry_price': trade_data.get('entry_price'),
                                        'lot_size': trade_data.get('additional_info', {}).get('lot_size'),
                                        'stop_loss_pips': trade_data.get('additional_info', {}).get('stop_loss_pips'),
                                        'risk_usd': trade_data.get('additional_info', {}).get('risk_usd'),
                                        'slippage': trade_data.get('additional_info', {}).get('slippage', 0),
                                        'spread_cost': trade_data.get('additional_info', {}).get('spread_fees_cost', 0),
                                        'status': trade_data.get('status'),
                                        'source': 'live'
                                    }
                                    trades.append(trade)
                                    current_trade = trade
                                    break
                                except json.JSONDecodeError:
                                    continue
                    
                    # Parse trade close
                    elif "TRADE CLOSED" in line or "Position closed" in line:
                        # Extract close info
                        profit_match = re.search(r'Profit: \$?([\d.-]+)', line)
                        reason_match = re.search(r'Reason: (.+?)(?:\s*$|\s*\|)', line)
                        
                        if current_trade and profit_match:
                            current_trade['exit_price'] = None  # Extract if available
                            current_trade['final_profit'] = float(profit_match.group(1))
                            current_trade['close_reason'] = reason_match.group(1) if reason_match else 'Unknown'
                            current_trade['status'] = 'CLOSED'
                            
            except Exception as e:
                print(f"Error parsing {log_file}: {e}")
        
        return trades
    
    def parse_live_sl_updates(self) -> List[Dict]:
        """Parse SL updates from JSONL files."""
        sl_updates = []
        
        # Check runtime directory for SL update JSONL files
        if self.runtime_dir.exists():
            for jsonl_file in sorted(self.runtime_dir.glob("sl_updates_*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    with open(jsonl_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                try:
                                    data = json.loads(line.strip())
                                    sl_update = {
                                        'timestamp': data.get('timestamp'),
                                        'ticket': data.get('ticket'),
                                        'symbol': data.get('symbol'),
                                        'type': data.get('type'),
                                        'profit': data.get('profit'),
                                        'sl_target': data.get('sl_target'),
                                        'sl_applied': data.get('sl_applied'),
                                        'effective_sl': data.get('effective_sl'),
                                        'sl_status': data.get('sl_status'),
                                        'last_update_result': data.get('last_update_result'),
                                        'consecutive_failures': data.get('consecutive_failures', 0),
                                        'sweet_spot': data.get('sweet_spot', False),
                                        'trailing': data.get('trailing', False),
                                        'last_sl_reason': data.get('last_sl_reason'),
                                        'source': 'live'
                                    }
                                    sl_updates.append(sl_update)
                                except json.JSONDecodeError:
                                    continue
                except Exception as e:
                    print(f"Error parsing {jsonl_file}: {e}")
        
        # Also parse from SL manager log
        sl_log = self.live_dir / "engine" / "sl_manager.log"
        if sl_log.exists():
            try:
                with open(sl_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Look for SL update patterns
                        if "SL UPDATE" in line or "TRAILING STOP" in line:
                            # Extract ticket, symbol, profit, SL price
                            ticket_match = re.search(r'Ticket[:\s]+(\d+)', line)
                            symbol_match = re.search(r'\((\w+)\s+(BUY|SELL)\)', line)
                            profit_match = re.search(r'Profit:?\s*\$?([\d.-]+)', line)
                            sl_match = re.search(r'SL[:\s]+([\d.]+)', line)
                            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            
                            if ticket_match:
                                sl_update = {
                                    'timestamp': time_match.group(1) if time_match else None,
                                    'ticket': int(ticket_match.group(1)),
                                    'symbol': symbol_match.group(1) if symbol_match else None,
                                    'type': symbol_match.group(2) if symbol_match else None,
                                    'profit': float(profit_match.group(1)) if profit_match else None,
                                    'sl_applied': float(sl_match.group(1)) if sl_match else None,
                                    'source': 'live',
                                    'from_log': True
                                }
                                sl_updates.append(sl_update)
            except Exception as e:
                print(f"Error parsing SL manager log: {e}")
        
        return sl_updates
    
    def parse_live_profit_locks(self) -> List[Dict]:
        """Parse profit locking events."""
        profit_locks = []
        profit_log = self.live_dir / "engine" / "profit_locking.log"
        
        if profit_log.exists():
            try:
                with open(profit_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if "PROFIT LOCK" in line or "Sweet spot lock" in line:
                            ticket_match = re.search(r'Ticket[:\s]+(\d+)', line)
                            symbol_match = re.search(r'(\w+)\s+(BUY|SELL)', line)
                            profit_match = re.search(r'Profit:?\s*\$?([\d.-]+)', line)
                            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            
                            if ticket_match:
                                profit_locks.append({
                                    'timestamp': time_match.group(1) if time_match else None,
                                    'ticket': int(ticket_match.group(1)),
                                    'symbol': symbol_match.group(1) if symbol_match else None,
                                    'profit': float(profit_match.group(1)) if profit_match else None,
                                    'source': 'live'
                                })
            except Exception as e:
                print(f"Error parsing profit locking log: {e}")
        
        return profit_locks
    
    def parse_live_errors(self) -> List[Dict]:
        """Parse system errors."""
        errors = []
        error_log = self.live_dir / "system" / "system_errors.log"
        
        if error_log.exists():
            try:
                with open(error_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if "ERROR" in line or "Exception" in line or "Traceback" in line:
                            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            errors.append({
                                'timestamp': time_match.group(1) if time_match else None,
                                'message': line.strip(),
                                'source': 'live'
                            })
            except Exception as e:
                print(f"Error parsing error log: {e}")
        
        return errors
    
    def parse_backtest_logs(self) -> Dict:
        """Parse backtest logs."""
        backtest_data = {
            'trades': [],
            'sl_updates': [],
            'errors': []
        }
        
        # Parse execution log
        exec_log = self.backtest_dir / "execution.log"
        if exec_log.exists():
            try:
                with open(exec_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Look for trade executions
                        if "TRADE EXECUTED" in line or "Order placed" in line:
                            ticket_match = re.search(r'Ticket[:\s]+(\d+)', line)
                            symbol_match = re.search(r'Symbol:?\s*(\w+)', line)
                            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            
                            if ticket_match:
                                backtest_data['trades'].append({
                                    'timestamp': time_match.group(1) if time_match else None,
                                    'ticket': int(ticket_match.group(1)),
                                    'symbol': symbol_match.group(1) if symbol_match else None,
                                    'source': 'backtest'
                                })
            except Exception as e:
                print(f"Error parsing backtest execution log: {e}")
        
        # Parse errors
        error_log = self.backtest_dir / "system_errors.log"
        if error_log.exists():
            try:
                with open(error_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if "ERROR" in line or "Exception" in line:
                            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            backtest_data['errors'].append({
                                'timestamp': time_match.group(1) if time_match else None,
                                'message': line.strip(),
                                'source': 'backtest'
                            })
            except Exception as e:
                print(f"Error parsing backtest error log: {e}")
        
        return backtest_data
    
    def analyze_live_performance(self) -> Dict:
        """Analyze live trading performance."""
        trades = self.parse_live_trade_logs()
        sl_updates = self.parse_live_sl_updates()
        profit_locks = self.parse_live_profit_locks()
        errors = self.parse_live_errors()
        
        # Filter to last 12 hours
        trades = self.filter_time_window(trades, 12)
        sl_updates = self.filter_time_window(sl_updates, 12)
        profit_locks = self.filter_time_window(profit_locks, 12)
        errors = self.filter_time_window(errors, 12)
        
        # Calculate metrics
        total_trades = len(trades)
        closed_trades = [t for t in trades if t.get('status') == 'CLOSED']
        open_trades = [t for t in trades if t.get('status') == 'OPEN']
        
        total_pnl = sum(t.get('final_profit', 0) for t in closed_trades)
        winning_trades = [t for t in closed_trades if t.get('final_profit', 0) > 0]
        losing_trades = [t for t in closed_trades if t.get('final_profit', 0) < 0]
        
        win_rate = len(winning_trades) / len(closed_trades) * 100 if closed_trades else 0
        
        # SL update success rate
        successful_sl_updates = [s for s in sl_updates if s.get('sl_status') == '[OK]' or 
                                s.get('last_update_result') == '[OK]']
        failed_sl_updates = [s for s in sl_updates if s.get('sl_status') == '[W]' or 
                             s.get('last_update_result') == '[W]']
        sl_success_rate = len(successful_sl_updates) / len(sl_updates) * 100 if sl_updates else 0
        
        # Profit locking activation
        profit_locks_activated = len(profit_locks)
        
        # Threading issues (lock timeouts, etc.)
        threading_issues = [s for s in sl_updates if 'timeout' in str(s.get('last_sl_reason', '')).lower() or
                           s.get('consecutive_failures', 0) > 10]
        
        # Parse watchdog warnings
        watchdog_log = self.live_dir / "monitor" / "watchdog.log"
        watchdog_warnings = []
        if watchdog_log.exists():
            try:
                with open(watchdog_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if "SL update rate too low" in line:
                            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            rate_match = re.search(r'(\d+\.?\d*)\s+updates/sec', line)
                            watchdog_warnings.append({
                                'timestamp': time_match.group(1) if time_match else None,
                                'rate': float(rate_match.group(1)) if rate_match else None,
                                'message': 'SL update rate too low'
                            })
            except Exception as e:
                print(f"Error parsing watchdog log: {e}")
        
        # Analyze specific failure patterns
        lock_timeout_failures = [s for s in sl_updates if 'timeout' in str(s.get('last_sl_reason', '')).lower()]
        violation_failures = [s for s in sl_updates if '[W] VIOLATION' in str(s.get('sl_status', ''))]
        
        # Group failures by ticket
        failures_by_ticket = defaultdict(list)
        for s in failed_sl_updates:
            if s.get('ticket'):
                failures_by_ticket[s['ticket']].append(s)
        
        worst_ticket = max(failures_by_ticket.items(), key=lambda x: len(x[1]), default=(None, []))
        
        return {
            'trades': trades,
            'sl_updates': sl_updates,
            'profit_locks': profit_locks,
            'errors': errors,
            'threading_issues': threading_issues,
            'watchdog_warnings': watchdog_warnings,
            'lock_timeout_failures': lock_timeout_failures,
            'violation_failures': violation_failures,
            'worst_failing_ticket': worst_ticket[0] if worst_ticket[0] else None,
            'worst_ticket_failures': len(worst_ticket[1]) if worst_ticket[1] else 0,
            'metrics': {
                'total_trades': total_trades,
                'closed_trades': len(closed_trades),
                'open_trades': len(open_trades),
                'total_pnl': total_pnl,
                'win_rate': win_rate,
                'winning_trades': len(winning_trades),
                'losing_trades': len(losing_trades),
                'sl_updates_total': len(sl_updates),
                'sl_success_rate': sl_success_rate,
                'sl_failures': len(failed_sl_updates),
                'lock_timeout_failures': len(lock_timeout_failures),
                'violation_failures': len(violation_failures),
                'profit_locks_activated': profit_locks_activated,
                'threading_issues': len(threading_issues),
                'watchdog_warnings': len(watchdog_warnings),
                'errors': len(errors)
            }
        }
    
    def analyze_backtest_performance(self) -> Dict:
        """Analyze backtest performance."""
        backtest_data = self.parse_backtest_logs()
        
        # Filter to same time window
        trades = self.filter_time_window(backtest_data['trades'], 12)
        errors = self.filter_time_window(backtest_data['errors'], 12)
        
        return {
            'trades': trades,
            'errors': errors,
            'metrics': {
                'total_trades': len(trades),
                'errors': len(errors)
            }
        }
    
    def compare_live_vs_backtest(self, live_data: Dict, backtest_data: Dict) -> List[Dict]:
        """Compare live vs backtest and identify divergences."""
        divergences = []
        
        # Compare trade counts
        live_trades = len(live_data['trades'])
        backtest_trades = len(backtest_data['trades'])
        
        if abs(live_trades - backtest_trades) > 0:
            divergences.append({
                'type': 'trade_count',
                'live_value': live_trades,
                'backtest_value': backtest_trades,
                'difference': live_trades - backtest_trades,
                'classification': 'B' if backtest_trades < live_trades else 'C',
                'explanation': f'Backtest executed {abs(live_trades - backtest_trades)} {"fewer" if backtest_trades < live_trades else "more"} trades than live'
            })
        
        # Compare SL update behavior
        live_sl_updates = len(live_data['sl_updates'])
        # Backtest may not have SL update logs - flag as missing data
        if live_sl_updates > 0 and len(backtest_data.get('sl_updates', [])) == 0:
            divergences.append({
                'type': 'sl_updates_missing',
                'live_value': live_sl_updates,
                'backtest_value': 0,
                'classification': 'B',
                'explanation': 'Backtest logs missing SL update data - cannot verify SL behavior'
            })
        
        return divergences
    
    def generate_report(self) -> str:
        """Generate comprehensive forensic report."""
        print("=" * 80)
        print("FORENSIC ANALYSIS: LIVE vs BACKTEST")
        print("=" * 80)
        print()
        
        print("Analyzing LIVE logs...")
        live_data = self.analyze_live_performance()
        self.live_data = live_data
        
        print("Analyzing BACKTEST logs...")
        backtest_data = self.analyze_backtest_performance()
        self.backtest_data = backtest_data
        
        print("Comparing LIVE vs BACKTEST...")
        divergences = self.compare_live_vs_backtest(live_data, backtest_data)
        self.divergences = divergences
        
        # Generate report
        report = []
        report.append("=" * 80)
        report.append("FORENSIC ANALYSIS REPORT")
        report.append("=" * 80)
        report.append(f"Analysis Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"Time Window: Last 12 hours")
        report.append("")
        
        # Executive Summary
        report.append("1. EXECUTIVE SUMMARY")
        report.append("-" * 80)
        report.append(f"LIVE Performance:")
        report.append(f"  - Total Trades: {live_data['metrics']['total_trades']}")
        report.append(f"  - Closed Trades: {live_data['metrics']['closed_trades']}")
        report.append(f"  - Win Rate: {live_data['metrics']['win_rate']:.2f}%")
        report.append(f"  - Total P&L: ${live_data['metrics']['total_pnl']:.2f}")
        report.append(f"  - SL Updates: {live_data['metrics']['sl_updates_total']}")
        report.append(f"  - SL Success Rate: {live_data['metrics']['sl_success_rate']:.2f}%")
        report.append(f"  - Profit Locks: {live_data['metrics']['profit_locks_activated']}")
        report.append(f"  - Threading Issues: {live_data['metrics']['threading_issues']}")
        report.append(f"  - Errors: {live_data['metrics']['errors']}")
        report.append("")
        report.append(f"BACKTEST Performance:")
        report.append(f"  - Total Trades: {backtest_data['metrics']['total_trades']}")
        report.append(f"  - Errors: {backtest_data['metrics']['errors']}")
        report.append("")
        
        # LIVE Performance Report
        report.append("2. LIVE PERFORMANCE REPORT")
        report.append("-" * 80)
        report.append(f"Trades Executed: {live_data['metrics']['total_trades']}")
        report.append(f"  - Closed: {live_data['metrics']['closed_trades']}")
        report.append(f"  - Open: {live_data['metrics']['open_trades']}")
        report.append(f"  - Winners: {live_data['metrics']['winning_trades']}")
        report.append(f"  - Losers: {live_data['metrics']['losing_trades']}")
        report.append("")
        report.append(f"SL Management:")
        report.append(f"  - Total Updates: {live_data['metrics']['sl_updates_total']}")
        report.append(f"  - Success Rate: {live_data['metrics']['sl_success_rate']:.2f}%")
        report.append(f"  - Failures: {live_data['metrics']['sl_failures']}")
        report.append("")
        report.append(f"Profit Locking:")
        report.append(f"  - Activated: {live_data['metrics']['profit_locks_activated']}")
        report.append("")
        report.append(f"System Health:")
        report.append(f"  - Threading Issues: {live_data['metrics']['threading_issues']}")
        report.append(f"  - Lock Timeout Failures: {live_data['metrics']['lock_timeout_failures']}")
        report.append(f"  - SL Violation Failures: {live_data['metrics']['violation_failures']}")
        report.append(f"  - Watchdog Warnings: {live_data['metrics']['watchdog_warnings']}")
        report.append(f"  - Errors: {live_data['metrics']['errors']}")
        report.append("")
        
        if live_data.get('worst_failing_ticket'):
            report.append(f"Worst Failing Position:")
            report.append(f"  - Ticket: {live_data['worst_failing_ticket']}")
            report.append(f"  - Total Failures: {live_data['worst_ticket_failures']}")
            report.append("")
        
        # BACKTEST Performance Report
        report.append("3. BACKTEST PERFORMANCE REPORT")
        report.append("-" * 80)
        report.append(f"Trades Executed: {backtest_data['metrics']['total_trades']}")
        report.append(f"Errors: {backtest_data['metrics']['errors']}")
        report.append("")
        
        # Divergences
        report.append("4. DIVERGENCE ANALYSIS")
        report.append("-" * 80)
        if divergences:
            for i, div in enumerate(divergences, 1):
                report.append(f"{i}. {div['type'].upper()}")
                report.append(f"   Live: {div['live_value']}")
                report.append(f"   Backtest: {div['backtest_value']}")
                report.append(f"   Classification: {div['classification']} ({'Market Reality' if div['classification'] == 'A' else 'Backtest Gap' if div['classification'] == 'B' else 'Logic Bug'})")
                report.append(f"   Explanation: {div['explanation']}")
                report.append("")
        else:
            report.append("No significant divergences found.")
        report.append("")
        
        # Root Cause Analysis
        report.append("5. ROOT CAUSE ANALYSIS")
        report.append("-" * 80)
        
        issues = []
        if live_data['metrics']['sl_success_rate'] < 95:
            issues.append({
                'priority': 'CRITICAL',
                'issue': f"SL update success rate ({live_data['metrics']['sl_success_rate']:.2f}%) below target (95%)",
                'impact': 'Positions may not be protected properly',
                'fix': 'Investigate SL update failures and lock contention'
            })
        
        if live_data['metrics']['lock_timeout_failures'] > 0:
            issues.append({
                'priority': 'CRITICAL',
                'issue': f"{live_data['metrics']['lock_timeout_failures']} lock acquisition timeout failures",
                'impact': 'SL updates cannot be applied due to lock contention - positions unprotected',
                'fix': 'Increase lock timeout, reduce lock contention, or implement lock-free updates for critical SL changes'
            })
        
        if live_data['metrics']['watchdog_warnings'] > 10:
            issues.append({
                'priority': 'HIGH',
                'issue': f"{live_data['metrics']['watchdog_warnings']} watchdog warnings - SL update rate too low (0.2/sec vs 5/sec target)",
                'impact': 'SL updates are happening too slowly - positions may not be protected in time',
                'fix': 'Investigate why SL worker loop is running slowly - check for blocking operations or excessive lock contention'
            })
        
        if live_data.get('worst_ticket_failures', 0) > 50:
            issues.append({
                'priority': 'CRITICAL',
                'issue': f"Ticket {live_data.get('worst_failing_ticket')} has {live_data.get('worst_ticket_failures')} consecutive SL update failures",
                'impact': 'This position is completely unprotected - risk of unlimited loss',
                'fix': 'Immediate investigation required - check if position lock is stuck or if there is a deadlock'
            })
        
        if live_data['metrics']['threading_issues'] > 0:
            issues.append({
                'priority': 'HIGH',
                'issue': f"{live_data['metrics']['threading_issues']} threading issues detected",
                'impact': 'SL updates may be delayed or skipped',
                'fix': 'Review lock acquisition timeouts and per-position locks'
            })
        
        if live_data['metrics']['errors'] > 10:
            issues.append({
                'priority': 'HIGH',
                'issue': f"{live_data['metrics']['errors']} system errors in 12 hours",
                'impact': 'System stability concerns',
                'fix': 'Review error logs and implement better error handling'
            })
        
        for issue in issues:
            report.append(f"[{issue['priority']}] {issue['issue']}")
            report.append(f"  Impact: {issue['impact']}")
            report.append(f"  Fix: {issue['fix']}")
            report.append("")
        
        # Fix List
        report.append("6. FIX LIST")
        report.append("-" * 80)
        
        critical_fixes = [i for i in issues if i['priority'] == 'CRITICAL']
        high_fixes = [i for i in issues if i['priority'] == 'HIGH']
        medium_fixes = [i for i in issues if i['priority'] == 'MEDIUM']
        
        if critical_fixes:
            report.append("CRITICAL (Must Fix):")
            for fix in critical_fixes:
                report.append(f"  - {fix['issue']}")
            report.append("")
        
        if high_fixes:
            report.append("HIGH PRIORITY:")
            for fix in high_fixes:
                report.append(f"  - {fix['issue']}")
            report.append("")
        
        if medium_fixes:
            report.append("MEDIUM PRIORITY:")
            for fix in medium_fixes:
                report.append(f"  - {fix['issue']}")
            report.append("")
        
        # Final Verdict
        report.append("7. FINAL VERDICT")
        report.append("-" * 80)
        
        if critical_fixes:
            verdict = "NO - Critical issues must be fixed before scaling"
        elif high_fixes:
            verdict = "CONDITIONAL - High priority issues should be addressed"
        else:
            verdict = "YES - System appears safe to scale"
        
        report.append(f"Safe to Scale: {verdict}")
        report.append("")
        report.append("=" * 80)
        
        return "\n".join(report)
    
    def run(self):
        """Run complete forensic analysis."""
        report = self.generate_report()
        print(report)
        
        # Save report
        report_file = f"forensic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"\nReport saved to: {report_file}")
        return report

if __name__ == "__main__":
    analyzer = ForensicAnalyzer()
    analyzer.run()

