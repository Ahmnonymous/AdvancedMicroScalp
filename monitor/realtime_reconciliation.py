#!/usr/bin/env python3
"""
Real-Time Reconciliation System
Compares bot trades with MT5 broker data in real-time during bot execution.
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
from collections import defaultdict

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.realtime_broker_fetcher import RealtimeBrokerFetcher
from utils.logger_factory import get_logger

logger = get_logger("realtime_recon", "logs/live/system/realtime_reconciliation.log")


class RealtimeReconciliation:
    """Real-time reconciliation between bot logs and MT5 broker data."""
    
    def __init__(self, config: Dict[str, Any], interval_minutes: Optional[int] = None, session_start_time: Optional[datetime] = None):
        """
        Initialize real-time reconciliation.
        
        Args:
            config: Configuration dictionary
            interval_minutes: Interval between reconciliation checks (deprecated - now uses session end only)
            session_start_time: Bot session start time - only reconcile trades from this session
        """
        self.config = config
        reconciliation_config = config.get('reconciliation', {})
        self.run_at_session_end = reconciliation_config.get('run_at_session_end', True)
        self.interval_minutes = interval_minutes  # Kept for backward compatibility but not used if run_at_session_end=True
        self.broker_fetcher = RealtimeBrokerFetcher(config)
        self.discrepancies = []
        self.last_check_time = None
        self.session_start_time = session_start_time  # Only reconcile trades from this session
        
        # Setup discrepancy logging
        # Log directory is created by logger_factory
        self.discrepancy_logger = get_logger("discrepancies", "logs/live/system/discrepancies.log")
    
    def set_session_start_time(self, session_start_time: datetime):
        """
        Set the bot session start time for filtering trades.
        
        Args:
            session_start_time: Bot session start datetime
        """
        self.session_start_time = session_start_time
        logger.info(f"Reconciliation session start time set to: {session_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    def parse_bot_logs_realtime(self) -> Dict[str, Dict[str, Any]]:
        """
        Parse bot trade logs in real-time, filtering by session start time.
        
        Returns:
            Dictionary mapping order_id to trade data (only trades from current session)
        """
        trades_dir = Path('logs/trades')
        bot_trades = {}
        
        if not trades_dir.exists():
            return bot_trades
        
        for log_file in trades_dir.glob('*.log'):
            if 'backup' in log_file.name:
                continue
            
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and line.startswith('{'):
                            try:
                                trade = json.loads(line)
                                
                                # Filter by session start time if set
                                if self.session_start_time:
                                    trade_timestamp_str = trade.get('timestamp', '')
                                    if trade_timestamp_str:
                                        try:
                                            # Parse timestamp (format: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS.microseconds')
                                            if '.' in trade_timestamp_str:
                                                trade_time = datetime.strptime(trade_timestamp_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
                                            else:
                                                trade_time = datetime.strptime(trade_timestamp_str, '%Y-%m-%d %H:%M:%S')
                                            
                                            # Only include trades from current session
                                            if trade_time < self.session_start_time:
                                                continue  # Skip trades before session start
                                        except (ValueError, AttributeError) as e:
                                            logger.debug(f"Could not parse trade timestamp '{trade_timestamp_str}': {e}")
                                            # If we can't parse, include it (fail-safe)
                                
                                order_id = str(trade.get('order_id', ''))
                                if order_id:
                                    # If multiple entries for same order_id, keep the latest
                                    if order_id not in bot_trades or trade.get('timestamp', '') > bot_trades[order_id].get('timestamp', ''):
                                        bot_trades[order_id] = trade
                            except:
                                pass
            except:
                pass
        
        if self.session_start_time:
            logger.info(f"Parsed {len(bot_trades)} bot trades from current session (since {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')})")
        else:
            logger.info(f"Parsed {len(bot_trades)} bot trades (no session filter)")
        
        return bot_trades
    
    def reconcile(self) -> Dict[str, Any]:
        """
        Perform real-time reconciliation.
        
        Returns:
            Reconciliation results dictionary
        """
        if not self.broker_fetcher.ensure_connected():
            logger.warning("Cannot reconcile - MT5 not connected")
            return {}
        
        logger.info("Starting real-time reconciliation...")
        
        # Get broker positions from deals, filtered by session start time
        if self.session_start_time:
            logger.info(f"Filtering broker trades to session start time: {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            # Calculate hours back from session start (with some buffer)
            hours_back = max(24, int((datetime.now() - self.session_start_time).total_seconds() / 3600) + 1)
            broker_positions = self.broker_fetcher.get_positions_from_deals(
                hours_back=hours_back,
                session_start_time=self.session_start_time
            )
        else:
            # No session filter - use default 24 hours
            broker_positions = self.broker_fetcher.get_positions_from_deals(hours_back=24)
        
        # Get bot trades
        bot_trades = self.parse_bot_logs_realtime()
        
        # Compare
        results = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'matched': [],
            'only_in_broker': [],
            'only_in_bot': [],
            'status_mismatches': [],
            'profit_discrepancies': []
        }
        
        broker_ids = set(broker_positions.keys())
        bot_ids = set(bot_trades.keys())
        
        # Matched trades
        matched_ids = broker_ids & bot_ids
        for order_id in matched_ids:
            broker_pos = broker_positions[order_id]
            bot_trade = bot_trades[order_id]
            
            match_result = {
                'order_id': order_id,
                'symbol': broker_pos['symbol'],
                'broker_status': broker_pos['status'],
                'bot_status': bot_trade.get('status', 'UNKNOWN'),
                'broker_profit': broker_pos['total_profit'],
                'bot_profit': bot_trade.get('profit_usd'),
                'broker_entry': broker_pos['entry_price'],
                'bot_entry': bot_trade.get('entry_price'),
                'broker_exit': broker_pos.get('exit_price'),
                'bot_exit': bot_trade.get('exit_price')
            }
            
            results['matched'].append(match_result)
            
            # Check status mismatch
            if broker_pos['status'] != bot_trade.get('status', 'UNKNOWN'):
                results['status_mismatches'].append(match_result)
                self.log_discrepancy(order_id, 'STATUS_MISMATCH', 
                                   f"Broker={broker_pos['status']}, Bot={bot_trade.get('status', 'UNKNOWN')}")
            
            # Check profit discrepancy
            broker_profit = broker_pos['total_profit']
            bot_profit = bot_trade.get('profit_usd')
            if broker_profit is not None and bot_profit is not None:
                profit_diff = abs(broker_profit - bot_profit)
                if profit_diff > 0.01:
                    results['profit_discrepancies'].append(match_result)
                    self.log_discrepancy(order_id, 'PROFIT_MISMATCH',
                                       f"Difference: ${profit_diff:.2f} (Broker: ${broker_profit:.2f}, Bot: ${bot_profit:.2f})")
        
        # Only in broker
        for order_id in broker_ids - bot_ids:
            broker_pos = broker_positions[order_id]
            results['only_in_broker'].append({
                'order_id': order_id,
                'symbol': broker_pos['symbol'],
                'status': broker_pos['status'],
                'profit': broker_pos['total_profit']
            })
            self.log_discrepancy(order_id, 'NOT_IN_BOT', 
                               f"Trade in broker but not in bot logs")
        
        # Only in bot
        for order_id in bot_ids - broker_ids:
            bot_trade = bot_trades[order_id]
            results['only_in_bot'].append({
                'order_id': order_id,
                'symbol': bot_trade.get('symbol', 'UNKNOWN'),
                'status': bot_trade.get('status', 'UNKNOWN'),
                'profit': bot_trade.get('profit_usd')
            })
            self.log_discrepancy(order_id, 'NOT_IN_BROKER',
                               f"Trade in bot logs but not in broker")
        
        self.last_check_time = datetime.now()
        
        # Log session filter info
        session_info = ""
        if self.session_start_time:
            session_info = f" (session since {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')})"
        
        logger.info(f"Reconciliation complete{session_info}: {len(results['matched'])} matched, "
                   f"{len(results['status_mismatches'])} status mismatches, "
                   f"{len(results['profit_discrepancies'])} profit discrepancies, "
                   f"{len(results['only_in_broker'])} only in broker, "
                   f"{len(results['only_in_bot'])} only in bot")
        
        return results
    
    def log_discrepancy(self, order_id: str, discrepancy_type: str, details: str):
        """Log a discrepancy."""
        message = f"Order {order_id} | Type: {discrepancy_type} | {details}"
        self.discrepancy_logger.warning(message)
        self.discrepancies.append({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'order_id': order_id,
            'type': discrepancy_type,
            'details': details
        })
    
    def generate_realtime_report(self, results: Dict[str, Any]) -> str:
        """Generate real-time reconciliation report."""
        os.makedirs('logs/reports', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_file = f'logs/reports/realtime_reconciliation_{timestamp}.txt'
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("REAL-TIME RECONCILIATION REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {results['timestamp']}\n")
            f.write("\n")
            
            f.write("SUMMARY\n")
            f.write("-" * 80 + "\n")
            f.write(f"Matched Trades: {len(results['matched'])}\n")
            f.write(f"Only in Broker: {len(results['only_in_broker'])}\n")
            f.write(f"Only in Bot: {len(results['only_in_bot'])}\n")
            f.write(f"Status Mismatches: {len(results['status_mismatches'])}\n")
            f.write(f"Profit Discrepancies: {len(results['profit_discrepancies'])}\n")
            f.write("\n")
            
            if results['status_mismatches']:
                f.write("STATUS MISMATCHES\n")
                f.write("-" * 80 + "\n")
                for mismatch in results['status_mismatches']:
                    f.write(f"Order {mismatch['order_id']} ({mismatch['symbol']}): "
                           f"Broker={mismatch['broker_status']}, Bot={mismatch['bot_status']}\n")
                f.write("\n")
            
            if results['profit_discrepancies']:
                f.write("PROFIT DISCREPANCIES\n")
                f.write("-" * 80 + "\n")
                for disc in results['profit_discrepancies']:
                    diff = abs(disc['broker_profit'] - (disc['bot_profit'] or 0))
                    f.write(f"Order {disc['order_id']} ({disc['symbol']}): "
                           f"Difference: ${diff:.2f}\n")
                f.write("\n")
        
        return report_file
    
    def is_session_ending(self) -> bool:
        """
        Check if trading session is ending soon.
        Uses market closing filter logic to detect session end.
        """
        try:
            from filters.market_closing_filter import MarketClosingFilter
            from execution.mt5_connector import MT5Connector
            
            # Create a temporary connector for checking (or use existing one if available)
            mt5_connector = MT5Connector(self.config)
            if not mt5_connector.ensure_connected():
                return False
            
            market_filter = MarketClosingFilter(self.config, mt5_connector)
            
            # Check if any major symbols are closing (indicates session end)
            major_symbols = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD']
            for symbol in major_symbols:
                is_closing, reason = market_filter.is_market_closing_soon(symbol)
                if is_closing:
                    logger.info(f"Session ending detected via {symbol}: {reason}")
                    return True
            
            # Also check if it's Friday near market close (end of week)
            from datetime import datetime, timezone
            gmt_now = datetime.now(timezone.utc)
            if gmt_now.weekday() == 4:  # Friday
                # Check if near Friday close (22:00 GMT)
                friday_close = gmt_now.replace(hour=22, minute=0, second=0, microsecond=0)
                if gmt_now < friday_close:
                    minutes_until_close = (friday_close - gmt_now).total_seconds() / 60.0
                    if minutes_until_close <= 30:  # Within 30 minutes of close
                        logger.info(f"Session ending: Friday market close in {minutes_until_close:.1f} minutes")
                        return True
            
            return False
        except Exception as e:
            logger.debug(f"Error checking session end: {e}")
            return False
    
    def run_continuous(self, stop_event=None):
        """
        Run continuous reconciliation.
        If run_at_session_end is True, only runs at end of session.
        Otherwise, runs on interval (backward compatibility).
        
        Args:
            stop_event: Threading event to stop the loop
        """
        if self.run_at_session_end:
            logger.info("Starting reconciliation - will run only at end of trading session")
        else:
            logger.info(f"Starting continuous reconciliation (interval: {self.interval_minutes} minutes)")
        
        try:
            import time
            while stop_event is None or not stop_event.is_set():
                if self.run_at_session_end:
                    # Check if session is ending
                    if self.is_session_ending():
                        logger.info("Session ending detected - running reconciliation...")
                        results = self.reconcile()
                        if results:
                            report_file = self.generate_realtime_report(results)
                            logger.info(f"End-of-session reconciliation report: {report_file}")
                        # Wait a bit before checking again (avoid rapid re-runs)
                        if stop_event:
                            stop_event.wait(60)  # Check every minute
                        else:
                            time.sleep(60)
                    else:
                        # Session not ending - wait and check again
                        if stop_event:
                            stop_event.wait(60)  # Check every minute
                        else:
                            time.sleep(60)
                else:
                    # Interval-based mode (backward compatibility)
                    results = self.reconcile()
                    if results:
                        report_file = self.generate_realtime_report(results)
                        logger.info(f"Real-time reconciliation report: {report_file}")
                    
                    # Wait for next interval
                    if stop_event:
                        stop_event.wait(self.interval_minutes * 60 if self.interval_minutes else 1800)
                    else:
                        time.sleep(self.interval_minutes * 60 if self.interval_minutes else 1800)
        
        except KeyboardInterrupt:
            logger.info("Reconciliation stopped by user")
        except Exception as e:
            logger.error(f"Error in continuous reconciliation: {e}", exc_info=True)

