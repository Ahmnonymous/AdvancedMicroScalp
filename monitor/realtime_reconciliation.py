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

logger = get_logger("realtime_recon", "logs/system/realtime_reconciliation.log")


class RealtimeReconciliation:
    """Real-time reconciliation between bot logs and MT5 broker data."""
    
    def __init__(self, config: Dict[str, Any], interval_minutes: int = 30):
        """
        Initialize real-time reconciliation.
        
        Args:
            config: Configuration dictionary
            interval_minutes: Interval between reconciliation checks
        """
        self.config = config
        self.interval_minutes = interval_minutes
        self.broker_fetcher = RealtimeBrokerFetcher(config)
        self.discrepancies = []
        self.last_check_time = None
        
        # Setup discrepancy logging
        os.makedirs('logs/system', exist_ok=True)
        self.discrepancy_logger = get_logger("discrepancies", "logs/system/discrepancies.log")
    
    def parse_bot_logs_realtime(self) -> Dict[str, Dict[str, Any]]:
        """
        Parse bot trade logs in real-time.
        
        Returns:
            Dictionary mapping order_id to trade data
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
                                order_id = str(trade.get('order_id', ''))
                                if order_id:
                                    # If multiple entries for same order_id, keep the latest
                                    if order_id not in bot_trades or trade.get('timestamp', '') > bot_trades[order_id].get('timestamp', ''):
                                        bot_trades[order_id] = trade
                            except:
                                pass
            except:
                pass
        
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
        
        # Get broker positions from deals
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
        logger.info(f"Reconciliation complete: {len(results['matched'])} matched, "
                   f"{len(results['status_mismatches'])} status mismatches, "
                   f"{len(results['profit_discrepancies'])} profit discrepancies")
        
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
    
    def run_continuous(self, stop_event=None):
        """
        Run continuous reconciliation.
        
        Args:
            stop_event: Threading event to stop the loop
        """
        logger.info(f"Starting continuous reconciliation (interval: {self.interval_minutes} minutes)")
        
        try:
            while stop_event is None or not stop_event.is_set():
                results = self.reconcile()
                if results:
                    report_file = self.generate_realtime_report(results)
                    logger.info(f"Real-time reconciliation report: {report_file}")
                
                # Wait for next interval
                import time
                if stop_event:
                    stop_event.wait(self.interval_minutes * 60)
                else:
                    time.sleep(self.interval_minutes * 60)
        
        except KeyboardInterrupt:
            logger.info("Reconciliation stopped by user")
        except Exception as e:
            logger.error(f"Error in continuous reconciliation: {e}", exc_info=True)

