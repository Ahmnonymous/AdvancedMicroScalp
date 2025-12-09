#!/usr/bin/env python3
"""
Trade Reconciliation System
Reconciles bot trade logs with broker HTML reports to ensure 100% accuracy.

Features:
- Updates bot logs with broker-confirmed status, exit prices, and profits
- Flags open trades until confirmed closed by broker
- Tracks discrepancies
- Generates reconciled reports
"""

import os
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.compare_bot_vs_broker import BotBrokerComparator


class TradeReconciler:
    """Reconciles bot trades with broker data and updates logs."""
    
    def __init__(self, broker_html_file: str = "TradingHistoryFromBroker.html"):
        self.broker_html_file = broker_html_file
        self.comparator = BotBrokerComparator(broker_html_file)
        self.discrepancies_log = []
        self.reconciled_count = 0
        self.updated_count = 0
        
    def log_discrepancy(self, order_id: str, discrepancy_type: str, details: str):
        """Log a discrepancy."""
        self.discrepancies_log.append({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'order_id': order_id,
            'type': discrepancy_type,
            'details': details
        })
        
        # Also write to discrepancy log file
        os.makedirs('logs/system', exist_ok=True)
        with open('logs/system/discrepancies.log', 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Order: {order_id} | Type: {discrepancy_type} | {details}\n")
    
    def reconcile_trades(self, create_backup: bool = True) -> Dict[str, Any]:
        """Reconcile bot trades with broker data."""
        print("=" * 80)
        print("TRADE RECONCILIATION SYSTEM")
        print("=" * 80)
        print()
        
        # Parse broker and bot trades
        print("Step 1: Parsing broker and bot trades...")
        broker_trades = self.comparator.parse_broker_html()
        bot_trades, bot_start_time, bot_end_time = self.comparator.parse_bot_logs()
        
        if not broker_trades or not bot_trades:
            print("❌ Cannot reconcile - missing trade data")
            return {}
        
        print()
        
        # Filter broker trades to bot trading period
        broker_trades_filtered = self.comparator.filter_broker_trades_by_time(
            broker_trades, bot_start_time, bot_end_time
        )
        
        print()
        
        # Create lookup dictionaries
        broker_by_id = {t['order_id']: t for t in broker_trades_filtered}
        bot_trades_by_id = {t['order_id']: t for t in bot_trades}
        bot_trades_by_symbol: Dict[str, List[Dict]] = {}
        
        for trade in bot_trades:
            symbol = trade['symbol']
            if symbol not in bot_trades_by_symbol:
                bot_trades_by_symbol[symbol] = []
            bot_trades_by_symbol[symbol].append(trade)
        
        print("Step 2: Identifying discrepancies and updates needed...")
        updates_by_file: Dict[str, List[Tuple[Dict, Dict]]] = {}  # file -> [(original_trade, updated_trade)]
        trades_to_remove: Dict[str, List[str]] = {}  # file -> [order_ids] (trades not in broker)
        
        # Process each bot trade
        for bot_trade in bot_trades:
            order_id = bot_trade['order_id']
            symbol = bot_trade['symbol']
            log_file = f'logs/trades/{symbol}.log'
            
            if order_id in broker_by_id:
                # Trade exists in broker - reconcile
                broker_trade = broker_by_id[order_id]
                
                # Check for updates needed
                needs_update = False
                updated_trade = bot_trade.copy()
                
                # Update status if broker says CLOSED
                if broker_trade['status'] == 'CLOSED' and bot_trade['status'] == 'OPEN':
                    updated_trade['status'] = 'CLOSED'
                    needs_update = True
                    self.log_discrepancy(order_id, 'STATUS_MISMATCH', 
                                       f'Bot=OPEN, Broker=CLOSED - Updating to CLOSED')
                
                # Update exit_price if missing or different
                if broker_trade.get('exit_price') and (
                    not bot_trade.get('exit_price') or 
                    abs(broker_trade['exit_price'] - (bot_trade.get('exit_price') or 0)) > 0.0001
                ):
                    updated_trade['exit_price'] = broker_trade['exit_price']
                    needs_update = True
                
                # Update profit_usd if missing or different
                if broker_trade.get('profit_usd') is not None and (
                    bot_trade.get('profit_usd') is None or
                    abs(broker_trade['profit_usd'] - (bot_trade.get('profit_usd') or 0)) > 0.01
                ):
                    updated_trade['profit_usd'] = broker_trade['profit_usd']
                    needs_update = True
                    if bot_trade.get('profit_usd') is None:
                        self.log_discrepancy(order_id, 'MISSING_PROFIT', 
                                           f'Added missing profit: ${broker_trade["profit_usd"]:.2f}')
                    else:
                        profit_diff = broker_trade['profit_usd'] - bot_trade['profit_usd']
                        self.log_discrepancy(order_id, 'PROFIT_MISMATCH', 
                                           f'Profit difference: ${profit_diff:.2f} (Bot: ${bot_trade["profit_usd"]:.2f}, Broker: ${broker_trade["profit_usd"]:.2f})')
                
                # Update entry_price if significantly different (slippage)
                if abs(broker_trade.get('entry_price', 0) - bot_trade.get('entry_price', 0)) > 0.0001:
                    entry_diff = abs(broker_trade['entry_price'] - bot_trade['entry_price'])
                    updated_trade['entry_price'] = broker_trade['entry_price']
                    if 'additional_info' not in updated_trade:
                        updated_trade['additional_info'] = {}
                    updated_trade['additional_info']['slippage_corrected'] = entry_diff
                    needs_update = True
                    self.log_discrepancy(order_id, 'ENTRY_PRICE_MISMATCH', 
                                       f'Entry price difference: {entry_diff:.5f} (corrected from broker)')
                
                # Add close_reason if missing
                if broker_trade['status'] == 'CLOSED' and not updated_trade.get('additional_info', {}).get('close_reason'):
                    if 'additional_info' not in updated_trade:
                        updated_trade['additional_info'] = {}
                    updated_trade['additional_info']['close_reason'] = 'Broker Confirmed'
                    updated_trade['additional_info']['reconciled'] = True
                    updated_trade['additional_info']['reconciled_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    needs_update = True
                
                if needs_update:
                    if log_file not in updates_by_file:
                        updates_by_file[log_file] = []
                    updates_by_file[log_file].append((bot_trade, updated_trade))
                    self.updated_count += 1
                
                self.reconciled_count += 1
            else:
                # Trade not in broker - might be unfilled order or duplicate
                # Mark for removal if it's clearly not a valid trade
                if log_file not in trades_to_remove:
                    trades_to_remove[log_file] = []
                trades_to_remove[log_file].append(order_id)
                self.log_discrepancy(order_id, 'NOT_IN_BROKER', 
                                   f'Trade in bot logs but not in broker report (possibly unfilled order)')
        
        print(f"  Found {self.updated_count} trades needing updates")
        print(f"  Found {len(trades_to_remove)} trades to flag for review")
        print()
        
        # Step 3: Update log files
        if updates_by_file or trades_to_remove:
            print("Step 3: Updating trade log files...")
            
            for log_file_path, updates in updates_by_file.items():
                self._update_log_file(log_file_path, updates, create_backup)
            
            # Note: We don't automatically remove trades not in broker - they might be recent
            # Instead, we flag them in the discrepancy log
            
            print(f"  Updated {len(updates_by_file)} log files")
            print()
        
        # Step 4: Generate reconciliation report
        print("Step 4: Generating reconciliation report...")
        self._generate_reconciliation_report(broker_trades_filtered, bot_trades, 
                                            updates_by_file, trades_to_remove,
                                            bot_start_time, bot_end_time)
        
        return {
            'reconciled_trades': self.reconciled_count,
            'updated_trades': self.updated_count,
            'trades_not_in_broker': sum(len(ids) for ids in trades_to_remove.values()),
            'discrepancies': len(self.discrepancies_log)
        }
    
    def _update_log_file(self, log_file_path: str, updates: List[Tuple[Dict, Dict]], create_backup: bool):
        """Update a log file with reconciled trade data."""
        if not os.path.exists(log_file_path):
            return
        
        # Create backup
        if create_backup:
            backup_path = f"{log_file_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(log_file_path, backup_path)
            print(f"  Created backup: {backup_path}")
        
        # Read existing trades
        existing_trades = []
        with open(log_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and line.startswith('{'):
                    try:
                        trade = json.loads(line)
                        existing_trades.append(trade)
                    except:
                        pass
        
        # Create update map
        update_map = {update[0]['order_id']: update[1] for update in updates}
        
        # Update trades
        updated_trades = []
        for trade in existing_trades:
            order_id = str(trade.get('order_id', ''))
            if order_id in update_map:
                updated_trades.append(update_map[order_id])
            else:
                updated_trades.append(trade)
        
        # Sort chronologically
        updated_trades.sort(key=lambda x: x.get('timestamp') or '0000-00-00 00:00:00')
        
        # Write back
        with open(log_file_path, 'w', encoding='utf-8') as f:
            for trade in updated_trades:
                f.write(json.dumps(trade, ensure_ascii=False) + '\n')
        
        print(f"  Updated {log_file_path}: {len(updates)} trades reconciled")
    
    def _generate_reconciliation_report(self, broker_trades: List[Dict], bot_trades: List[Dict],
                                       updates_by_file: Dict, trades_to_remove: Dict,
                                       bot_start_time: Optional[datetime], bot_end_time: Optional[datetime]):
        """Generate reconciliation report."""
        os.makedirs('logs/reports', exist_ok=True)
        date_str = datetime.now().strftime('%Y-%m-%d')
        report_file = f'logs/reports/reconciliation_report_{date_str}.txt'
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("TRADE RECONCILIATION REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if bot_start_time and bot_end_time:
                f.write(f"Bot Trading Period: {bot_start_time.strftime('%Y-%m-%d %H:%M:%S')} to {bot_end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n")
            
            f.write("RECONCILIATION SUMMARY\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total Bot Trades: {len(bot_trades)}\n")
            f.write(f"Total Broker Trades (in period): {len(broker_trades)}\n")
            f.write(f"Trades Reconciled: {self.reconciled_count}\n")
            f.write(f"Trades Updated: {self.updated_count}\n")
            f.write(f"Trades Not in Broker: {sum(len(ids) for ids in trades_to_remove.values())}\n")
            f.write(f"Total Discrepancies: {len(self.discrepancies_log)}\n")
            f.write("\n")
            
            if self.discrepancies_log:
                f.write("DISCREPANCIES LOG\n")
                f.write("-" * 80 + "\n")
                for disc in self.discrepancies_log:
                    f.write(f"[{disc['timestamp']}] {disc['type']}: Order {disc['order_id']} - {disc['details']}\n")
                f.write("\n")
            
            if updates_by_file:
                f.write("UPDATED TRADES\n")
                f.write("-" * 80 + "\n")
                for log_file, updates in updates_by_file.items():
                    f.write(f"\n{log_file}:\n")
                    for original, updated in updates:
                        f.write(f"  Order {original['order_id']} ({original['symbol']}):\n")
                        if original['status'] != updated['status']:
                            f.write(f"    Status: {original['status']} -> {updated['status']}\n")
                        if original.get('profit_usd') != updated.get('profit_usd'):
                            f.write(f"    Profit: {original.get('profit_usd')} -> {updated.get('profit_usd')}\n")
                        if original.get('exit_price') != updated.get('exit_price'):
                            f.write(f"    Exit Price: {original.get('exit_price')} -> {updated.get('exit_price')}\n")
                f.write("\n")
            
            if trades_to_remove:
                f.write("TRADES NOT IN BROKER REPORT\n")
                f.write("-" * 80 + "\n")
                f.write("These trades appear in bot logs but not in broker report.\n")
                f.write("They may be:\n")
                f.write("  - Orders that were placed but never filled\n")
                f.write("  - Duplicate entries\n")
                f.write("  - Trades outside the broker report time range\n")
                f.write("\n")
                for log_file, order_ids in trades_to_remove.items():
                    f.write(f"{log_file}:\n")
                    for order_id in order_ids:
                        f.write(f"  Order ID: {order_id}\n")
                f.write("\n")
        
        print(f"  Report saved: {report_file}")
    
    def run(self, create_backup: bool = True):
        """Run full reconciliation."""
        results = self.reconcile_trades(create_backup)
        
        print()
        print("=" * 80)
        print("RECONCILIATION COMPLETE")
        print("=" * 80)
        print(f"Trades Reconciled: {results.get('reconciled_trades', 0)}")
        print(f"Trades Updated: {results.get('updated_trades', 0)}")
        print(f"Trades Not in Broker: {results.get('trades_not_in_broker', 0)}")
        print(f"Discrepancies Logged: {results.get('discrepancies', 0)}")
        print()


def main():
    """Main entry point."""
    import sys
    
    create_backup = True
    if len(sys.argv) > 1 and sys.argv[1] == '--no-backup':
        create_backup = False
    
    reconciler = TradeReconciler()
    reconciler.run(create_backup=create_backup)


if __name__ == "__main__":
    main()

