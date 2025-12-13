"""
Performance Reporter for Backtesting
Tracks metrics, SL updates, worker loop timing, and anomalies.
"""

import json
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import pandas as pd

from utils.logger_factory import get_logger

logger = get_logger("performance_reporter", "logs/backtest/performance.log")


class PerformanceReporter:
    """Tracks and reports backtest performance metrics."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize performance reporter."""
        self.config = config
        
        # Trade tracking
        self.trades: List[Dict[str, Any]] = []
        self.closed_trades: List[Dict[str, Any]] = []
        
        # SL update tracking
        self.sl_updates: List[Dict[str, Any]] = []
        self.duplicate_sl_updates: int = 0
        
        # Worker loop timing
        self.worker_loop_timings: List[Dict[str, Any]] = []
        
        # Account snapshots (for equity curve)
        self.account_snapshots: List[Dict[str, Any]] = []
        
        # Profit locking tracking
        self.profit_locks: List[Dict[str, Any]] = []
        
        # Anomalies
        self.anomalies: List[Dict[str, Any]] = []
        
        # Lock contention tracking
        self.lock_contention_events: List[Dict[str, Any]] = []
        
        # Runtime exceptions
        self.exceptions: List[Dict[str, Any]] = []
        
        # Metrics thresholds (from rules)
        self.thresholds = {
            'sl_update_success_rate_min': 95.0,
            'lock_contention_rate_max': 5.0,
            'worker_loop_duration_max_ms': 50.0,
            'profit_lock_activation_rate_min': 95.0
        }
    
    def record_trade_opened(self, ticket: int, symbol: str, direction: str,
                           entry_price: float, lot_size: float, sl_price: float,
                           time: datetime):
        """Record a trade opening."""
        trade = {
            'ticket': ticket,
            'symbol': symbol,
            'direction': direction,
            'entry_price': entry_price,
            'lot_size': lot_size,
            'sl_price': sl_price,
            'entry_time': time,
            'close_time': None,
            'close_price': None,
            'close_reason': None,
            'profit': None,
            'sl_updates': []
        }
        self.trades.append(trade)
        logger.debug(f"Trade opened: {ticket} {symbol} {direction}")
    
    def record_trade_closed(self, ticket: int, close_price: float,
                           close_reason: str, profit: float, time: datetime):
        """Record a trade closure."""
        # Find trade
        trade = None
        for t in self.trades:
            if t['ticket'] == ticket:
                trade = t
                break
        
        if trade:
            trade['close_time'] = time
            trade['close_price'] = close_price
            trade['close_reason'] = close_reason
            trade['profit'] = profit
            self.closed_trades.append(trade.copy())
            logger.debug(f"Trade closed: {ticket} profit=${profit:.2f}")
        else:
            logger.warning(f"Trade {ticket} closed but not found in tracking")
    
    def record_sl_update(self, ticket: int, symbol: str, old_sl: float,
                        new_sl: float, reason: str, success: bool,
                        duration_ms: float, time: datetime):
        """Record an SL update."""
        update = {
            'ticket': ticket,
            'symbol': symbol,
            'old_sl': old_sl,
            'new_sl': new_sl,
            'reason': reason,
            'success': success,
            'duration_ms': duration_ms,
            'time': time
        }
        self.sl_updates.append(update)
        
        # Check for duplicates (same ticket, same new_sl within short time)
        if len(self.sl_updates) > 1:
            prev_update = self.sl_updates[-2]
            if (prev_update['ticket'] == ticket and
                prev_update['new_sl'] == new_sl and
                (time - prev_update['time']).total_seconds() < 1.0):
                self.duplicate_sl_updates += 1
                logger.warning(f"Duplicate SL update detected: ticket={ticket}, sl={new_sl}")
        
        # Add to trade's SL updates
        for trade in self.trades:
            if trade['ticket'] == ticket:
                trade['sl_updates'].append(update)
                break
        
        if not success:
            self.record_anomaly('sl_update_failed', {
                'ticket': ticket,
                'symbol': symbol,
                'reason': reason,
                'time': time
            })
    
    def record_worker_loop_timing(self, duration_ms: float, position_count: int, time: datetime):
        """Record worker loop execution timing."""
        timing = {
            'duration_ms': duration_ms,
            'position_count': position_count,
            'time': time
        }
        self.worker_loop_timings.append(timing)
        
        # Check for timing violations
        if duration_ms > self.thresholds['worker_loop_duration_max_ms']:
            self.record_anomaly('worker_loop_timing_violation', {
                'duration_ms': duration_ms,
                'position_count': position_count,
                'time': time
            })
    
    def record_account_snapshot(self, balance: float, equity: float,
                               profit: float, time: datetime):
        """Record account snapshot for equity curve."""
        snapshot = {
            'balance': balance,
            'equity': equity,
            'profit': profit,
            'time': time
        }
        self.account_snapshots.append(snapshot)
    
    def record_profit_lock(self, ticket: int, symbol: str, profit_usd: float,
                          sl_price: float, time: datetime, success: bool):
        """Record profit locking event."""
        lock = {
            'ticket': ticket,
            'symbol': symbol,
            'profit_usd': profit_usd,
            'sl_price': sl_price,
            'time': time,
            'success': success
        }
        self.profit_locks.append(lock)
        
        if not success:
            self.record_anomaly('profit_lock_failed', {
                'ticket': ticket,
                'symbol': symbol,
                'profit_usd': profit_usd,
                'time': time
            })
    
    def record_lock_contention(self, ticket: int, symbol: str, timeout: bool,
                              duration_ms: float, time: datetime):
        """Record lock contention event."""
        event = {
            'ticket': ticket,
            'symbol': symbol,
            'timeout': timeout,
            'duration_ms': duration_ms,
            'time': time
        }
        self.lock_contention_events.append(event)
        
        if timeout:
            self.record_anomaly('lock_timeout', {
                'ticket': ticket,
                'symbol': symbol,
                'duration_ms': duration_ms,
                'time': time
            })
    
    def record_exception(self, exception_type: str, message: str,
                        traceback: str, time: datetime):
        """Record runtime exception."""
        exc = {
            'type': exception_type,
            'message': message,
            'traceback': traceback,
            'time': time
        }
        self.exceptions.append(exc)
        logger.error(f"Exception recorded: {exception_type}: {message}")
    
    def record_anomaly(self, anomaly_type: str, details: Dict[str, Any]):
        """Record an anomaly."""
        anomaly = {
            'type': anomaly_type,
            'details': details,
            'time': details.get('time', datetime.now())
        }
        self.anomalies.append(anomaly)
        logger.warning(f"Anomaly detected: {anomaly_type}")
    
    def calculate_metrics(self) -> Dict[str, Any]:
        """Calculate all performance metrics."""
        metrics = {}
        
        # SL update metrics
        if self.sl_updates:
            total_updates = len(self.sl_updates)
            successful_updates = sum(1 for u in self.sl_updates if u['success'])
            metrics['sl_update_success_rate'] = (successful_updates / total_updates) * 100.0
            metrics['sl_update_total'] = total_updates
            metrics['sl_update_successful'] = successful_updates
            metrics['sl_update_failed'] = total_updates - successful_updates
            
            durations = [u['duration_ms'] for u in self.sl_updates if u['success']]
            if durations:
                metrics['sl_update_avg_delay_ms'] = sum(durations) / len(durations)
                metrics['sl_update_max_delay_ms'] = max(durations)
            else:
                metrics['sl_update_avg_delay_ms'] = 0.0
                metrics['sl_update_max_delay_ms'] = 0.0
        else:
            metrics['sl_update_success_rate'] = 0.0
            metrics['sl_update_total'] = 0
            metrics['sl_update_successful'] = 0
            metrics['sl_update_failed'] = 0
            metrics['sl_update_avg_delay_ms'] = 0.0
            metrics['sl_update_max_delay_ms'] = 0.0
        
        metrics['sl_update_duplicate_updates'] = self.duplicate_sl_updates
        
        # Worker loop timing metrics
        if self.worker_loop_timings:
            durations = [t['duration_ms'] for t in self.worker_loop_timings]
            metrics['worker_loop_avg_duration_ms'] = sum(durations) / len(durations)
            metrics['worker_loop_max_duration_ms'] = max(durations)
            metrics['worker_loop_min_duration_ms'] = min(durations)
            metrics['worker_loop_timing_violations'] = sum(
                1 for d in durations if d > self.thresholds['worker_loop_duration_max_ms']
            )
        else:
            metrics['worker_loop_avg_duration_ms'] = 0.0
            metrics['worker_loop_max_duration_ms'] = 0.0
            metrics['worker_loop_min_duration_ms'] = 0.0
            metrics['worker_loop_timing_violations'] = 0
        
        # Lock contention metrics
        if self.lock_contention_events:
            total_locks = len(self.lock_contention_events)
            timeout_locks = sum(1 for e in self.lock_contention_events if e['timeout'])
            metrics['lock_contention_rate'] = (timeout_locks / total_locks) * 100.0
            metrics['lock_contention_total'] = total_locks
            metrics['lock_contention_timeouts'] = timeout_locks
        else:
            metrics['lock_contention_rate'] = 0.0
            metrics['lock_contention_total'] = 0
            metrics['lock_contention_timeouts'] = 0
        
        # Profit locking metrics
        if self.profit_locks:
            total_locks = len(self.profit_locks)
            successful_locks = sum(1 for l in self.profit_locks if l['success'])
            metrics['profit_lock_activation_rate'] = (successful_locks / total_locks) * 100.0
            metrics['profit_lock_total'] = total_locks
            metrics['profit_lock_successful'] = successful_locks
            metrics['profit_lock_failed'] = total_locks - successful_locks
        else:
            metrics['profit_lock_activation_rate'] = 0.0
            metrics['profit_lock_total'] = 0
            metrics['profit_lock_successful'] = 0
            metrics['profit_lock_failed'] = 0
        
        # Trade metrics
        if self.closed_trades:
            total_trades = len(self.closed_trades)
            winning_trades = sum(1 for t in self.closed_trades if t['profit'] > 0)
            losing_trades = total_trades - winning_trades
            total_profit = sum(t['profit'] for t in self.closed_trades)
            profits = [t['profit'] for t in self.closed_trades if t['profit'] > 0]
            losses = [abs(t['profit']) for t in self.closed_trades if t['profit'] < 0]
            
            metrics['total_trades'] = total_trades
            metrics['winning_trades'] = winning_trades
            metrics['losing_trades'] = losing_trades
            metrics['win_rate'] = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0
            metrics['net_profit'] = total_profit
            
            if profits and losses:
                avg_profit = sum(profits) / len(profits)
                avg_loss = sum(losses) / len(losses)
                metrics['profit_factor'] = avg_profit / avg_loss if avg_loss > 0 else 0.0
            else:
                metrics['profit_factor'] = 0.0
            
            # Drawdown calculation
            equity_curve = []
            running_equity = 10000.0  # Initial balance
            for trade in sorted(self.closed_trades, key=lambda t: t['close_time']):
                running_equity += trade['profit']
                equity_curve.append(running_equity)
            
            if equity_curve:
                peak = equity_curve[0]
                max_dd = 0.0
                for equity in equity_curve:
                    if equity > peak:
                        peak = equity
                    dd = peak - equity
                    if dd > max_dd:
                        max_dd = dd
                
                metrics['max_drawdown'] = max_dd
                metrics['max_drawdown_pct'] = (max_dd / peak) * 100.0 if peak > 0 else 0.0
            else:
                metrics['max_drawdown'] = 0.0
                metrics['max_drawdown_pct'] = 0.0
        else:
            metrics['total_trades'] = 0
            metrics['winning_trades'] = 0
            metrics['losing_trades'] = 0
            metrics['win_rate'] = 0.0
            metrics['net_profit'] = 0.0
            metrics['profit_factor'] = 0.0
            metrics['max_drawdown'] = 0.0
            metrics['max_drawdown_pct'] = 0.0
        
        # Anomaly counts
        anomaly_counts = defaultdict(int)
        for anomaly in self.anomalies:
            anomaly_counts[anomaly['type']] += 1
        
        metrics['anomalies'] = dict(anomaly_counts)
        metrics['total_anomalies'] = len(self.anomalies)
        metrics['exceptions'] = len(self.exceptions)
        
        return metrics
    
    def check_thresholds(self) -> Dict[str, bool]:
        """Check if metrics meet thresholds."""
        metrics = self.calculate_metrics()
        checks = {}
        
        checks['sl_update_success_rate'] = (
            metrics.get('sl_update_success_rate', 0.0) >=
            self.thresholds['sl_update_success_rate_min']
        )
        
        checks['lock_contention_rate'] = (
            metrics.get('lock_contention_rate', 0.0) <=
            self.thresholds['lock_contention_rate_max']
        )
        
        checks['worker_loop_timing'] = (
            metrics.get('worker_loop_max_duration_ms', 0.0) <=
            self.thresholds['worker_loop_duration_max_ms']
        )
        
        checks['profit_lock_activation_rate'] = (
            metrics.get('profit_lock_activation_rate', 0.0) >=
            self.thresholds['profit_lock_activation_rate_min']
        )
        
        checks['no_critical_exceptions'] = (metrics.get('exceptions', 0) == 0)
        
        checks['all_passed'] = all(checks.values())
        
        return checks
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report."""
        metrics = self.calculate_metrics()
        threshold_checks = self.check_thresholds()
        
        # Group anomalies
        anomaly_summary = defaultdict(int)
        for anomaly in self.anomalies:
            anomaly_summary[anomaly['type']] += 1
        
        report = {
            'summary': {
                'total_trades': metrics.get('total_trades', 0),
                'winning_trades': metrics.get('winning_trades', 0),
                'losing_trades': metrics.get('losing_trades', 0),
                'win_rate': metrics.get('win_rate', 0.0),
                'net_profit': metrics.get('net_profit', 0.0),
                'max_drawdown': metrics.get('max_drawdown', 0.0),
                'max_drawdown_pct': metrics.get('max_drawdown_pct', 0.0),
                'profit_factor': metrics.get('profit_factor', 0.0),
                'avg_rr': 0.0  # TODO: Calculate R:R ratio
            },
            'sl_performance': {
                'success_rate': metrics.get('sl_update_success_rate', 0.0),
                'avg_delay_ms': metrics.get('sl_update_avg_delay_ms', 0.0),
                'max_delay_ms': metrics.get('sl_update_max_delay_ms', 0.0),
                'duplicate_updates': metrics.get('sl_update_duplicate_updates', 0),
                'total_updates': metrics.get('sl_update_total', 0),
                'failed_updates': metrics.get('sl_update_failed', 0)
            },
            'worker_loop_performance': {
                'avg_duration_ms': metrics.get('worker_loop_avg_duration_ms', 0.0),
                'max_duration_ms': metrics.get('worker_loop_max_duration_ms', 0.0),
                'min_duration_ms': metrics.get('worker_loop_min_duration_ms', 0.0),
                'timing_violations': metrics.get('worker_loop_timing_violations', 0)
            },
            'lock_contention': {
                'rate': metrics.get('lock_contention_rate', 0.0),
                'total_events': metrics.get('lock_contention_total', 0),
                'timeouts': metrics.get('lock_contention_timeouts', 0)
            },
            'profit_locking': {
                'activation_rate': metrics.get('profit_lock_activation_rate', 0.0),
                'total_attempts': metrics.get('profit_lock_total', 0),
                'successful': metrics.get('profit_lock_successful', 0),
                'failed': metrics.get('profit_lock_failed', 0)
            },
            'anomalies': {
                'total': metrics.get('total_anomalies', 0),
                'by_type': dict(anomaly_summary),
                'early_exits': anomaly_summary.get('early_exit', 0),
                'late_exits': anomaly_summary.get('late_exit', 0),
                'missed_sl_updates': anomaly_summary.get('sl_update_failed', 0),
                'duplicate_updates': metrics.get('sl_update_duplicate_updates', 0)
            },
            'exceptions': {
                'total': metrics.get('exceptions', 0),
                'list': self.exceptions[-10:]  # Last 10 exceptions
            },
            'threshold_checks': threshold_checks,
            'metrics': metrics,
            'timestamp': datetime.now().isoformat()
        }
        
        return report
    
    def save_report(self, output_path: str):
        """Save report to JSON file."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        report = self.generate_report()
        
        # Convert datetime objects to ISO strings
        def serialize_datetime(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {k: serialize_datetime(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [serialize_datetime(item) for item in obj]
            return obj
        
        report_serialized = serialize_datetime(report)
        
        with open(output_path, 'w') as f:
            json.dump(report_serialized, f, indent=2)
        
        logger.info(f"Report saved to {output_path}")

