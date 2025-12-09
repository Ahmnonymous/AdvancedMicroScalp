#!/usr/bin/env python3
"""
Legacy Log Conversion Script
Converts legacy bot logs to the new modular JSONL logging structure.

Converts:
- bot_log.txt (main bot log)
- logs/symbols/*.log (old symbol-based logs)

To:
- logs/trades/SYMBOL.log (JSONL format)
"""

import os
import re
import json
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple


class LegacyLogConverter:
    """Converts legacy logs to new JSONL trade log format."""
    
    def __init__(self):
        self.trades: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.errors: List[str] = []
        self.stats = {
            'total_trades': 0,
            'trades_by_symbol': defaultdict(int),
            'errors': 0,
            'open_trades': 0,
            'closed_trades': 0
        }
        
    def parse_symbol_log(self, file_path: str) -> List[Dict[str, Any]]:
        """Parse a legacy symbol log file (logs/symbols/SYMBOL_DATE.log)."""
        trades = []
        current_trade = None
        open_trades_by_ticket: Dict[str, Dict[str, Any]] = {}  # ticket -> trade
        current_block_lines = []
        in_execution_block = False
        in_closure_block = False
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e:
            self.errors.append(f"Error reading {file_path}: {e}")
            return []
        
        symbol = None
        # Extract symbol from filename (e.g., ETHUSDm_2025-12-08.log -> ETHUSDm)
        filename = os.path.basename(file_path)
        match = re.match(r'([^_]+)_\d{4}-\d{2}-\d{2}\.log', filename)
        if match:
            symbol = match.group(1)
        
        closure_ticket = None
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Parse timestamp
            timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            timestamp_str = timestamp_match.group(1) if timestamp_match else None
            
            # Start of trade execution block
            if "✅ TRADE EXECUTED SUCCESSFULLY" in line:
                if current_trade:
                    # Save previous incomplete trade
                    trades.append(current_trade)
                current_trade = {
                    'timestamp': timestamp_str,
                    'symbol': symbol,
                    'trade_type': None,
                    'entry_price': None,
                    'exit_price': None,
                    'profit_usd': None,
                    'status': 'OPEN',
                    'order_id': None,
                    'additional_info': {}
                }
                in_execution_block = True
                current_block_lines = [line]
            
            # Continue parsing execution block
            elif in_execution_block and current_trade:
                current_block_lines.append(line)
                
                # Extract fields from execution block
                if "Symbol:" in line:
                    match = re.search(r'Symbol:\s+(\w+)', line)
                    if match:
                        current_trade['symbol'] = match.group(1)
                elif "Direction:" in line:
                    match = re.search(r'Direction:\s+(LONG|SHORT)', line)
                    if match:
                        current_trade['trade_type'] = match.group(1)
                elif "Ticket:" in line:
                    match = re.search(r'Ticket:\s+(\d+)', line)
                    if match:
                        current_trade['order_id'] = match.group(1)
                elif "Entry Price (Actual Fill):" in line:
                    match = re.search(r'Entry Price \(Actual Fill\):\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['entry_price'] = float(match.group(1))
                        except:
                            pass
                elif "Entry Price (Requested):" in line and current_trade['entry_price'] is None:
                    match = re.search(r'Entry Price \(Requested\):\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['entry_price'] = float(match.group(1))
                        except:
                            pass
                elif "Spread:" in line:
                    match = re.search(r'Spread:\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['additional_info']['spread_points'] = float(match.group(1))
                        except:
                            pass
                elif "Lot Size:" in line:
                    match = re.search(r'Lot Size:\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['additional_info']['lot_size'] = float(match.group(1))
                        except:
                            pass
                elif "Stop Loss:" in line:
                    match = re.search(r'Stop Loss:\s+([\d.]+)\s+pips', line)
                    if match:
                        try:
                            current_trade['additional_info']['stop_loss_pips'] = float(match.group(1))
                        except:
                            pass
                elif "Quality Score:" in line:
                    match = re.search(r'Quality Score:\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['additional_info']['quality_score'] = float(match.group(1))
                        except:
                            pass
                elif "Risk:" in line:
                    match = re.search(r'Risk:\s+\$([\d.]+)', line)
                    if match:
                        try:
                            current_trade['additional_info']['risk_usd'] = float(match.group(1))
                        except:
                            pass
                elif "=" * 80 in line or "=" * 50 in line:
                    # End of execution block - save trade if complete
                    in_execution_block = False
                    if current_trade and current_trade.get('order_id'):
                        # Store in open trades dict
                        open_trades_by_ticket[current_trade['order_id']] = current_trade
                        # Don't add to trades yet - wait for closure or end of file
                        current_trade = None
            
            # Start of position closure block
            elif "🔴 POSITION CLOSED" in line:
                in_closure_block = True
                current_block_lines = [line]
                closure_ticket = None  # Will be extracted from closure block
            
            # Continue parsing closure block
            elif in_closure_block:
                current_block_lines.append(line)
                
                if "Ticket:" in line:
                    match = re.search(r'Ticket:\s+(\d+)', line)
                    if match:
                        closure_ticket = match.group(1)
                        # Try to match with existing open trade
                        if closure_ticket in open_trades_by_ticket:
                            current_trade = open_trades_by_ticket[closure_ticket]
                        elif not current_trade:
                            # Create new trade entry for orphaned closure
                            current_trade = {
                                'timestamp': timestamp_str,
                                'symbol': symbol,
                                'trade_type': None,
                                'entry_price': None,
                                'exit_price': None,
                                'profit_usd': None,
                                'status': 'CLOSED',
                                'order_id': closure_ticket,
                                'additional_info': {}
                            }
                elif current_trade and "Entry Price:" in line:
                    match = re.search(r'Entry Price:\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['entry_price'] = float(match.group(1))
                        except:
                            pass
                elif current_trade and "Close Price:" in line:
                    match = re.search(r'Close Price:\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['exit_price'] = float(match.group(1))
                        except:
                            pass
                elif current_trade and "Profit/Loss:" in line:
                    match = re.search(r'Profit/Loss:\s+\$([\d.]+)', line)
                    if match:
                        try:
                            current_trade['profit_usd'] = float(match.group(1))
                            current_trade['status'] = 'CLOSED'
                        except:
                            pass
                elif current_trade and "Duration:" in line:
                    match = re.search(r'Duration:\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['additional_info']['duration_minutes'] = float(match.group(1))
                        except:
                            pass
                elif current_trade and "Close Reason:" in line:
                    match = re.search(r'Close Reason:\s+(.+)', line)
                    if match:
                        current_trade['additional_info']['close_reason'] = match.group(1).strip()
                elif "=" * 80 in line or "=" * 50 in line:
                    # End of closure block - finalize trade
                    in_closure_block = False
                    if closure_ticket and current_trade:
                        current_trade['status'] = 'CLOSED'
                        if closure_ticket in open_trades_by_ticket:
                            # Remove from open trades
                            del open_trades_by_ticket[closure_ticket]
                        trades.append(current_trade)
                        current_trade = None
                    closure_ticket = None
                    current_block_lines = []
            
            # Parse micro-HFT profit close
            elif "⚡ MICRO-HFT PROFIT CLOSE" in line:
                if current_trade:
                    trades.append(current_trade)
                current_trade = {
                    'timestamp': timestamp_str,
                    'symbol': symbol,
                    'trade_type': None,
                    'entry_price': None,
                    'exit_price': None,
                    'profit_usd': None,
                    'status': 'CLOSED',
                    'order_id': None,
                    'additional_info': {'close_type': 'micro_hft'}
                }
                in_execution_block = True
                current_block_lines = [line]
                
            # Continue parsing micro-HFT block
            elif in_execution_block and "MICRO-HFT PROFIT CLOSE" in str(current_block_lines):
                current_block_lines.append(line)
                
                if "Ticket:" in line:
                    match = re.search(r'Ticket:\s+(\d+)', line)
                    if match:
                        current_trade['order_id'] = match.group(1)
                elif "Entry Price (Actual Fill):" in line:
                    match = re.search(r'Entry Price \(Actual Fill\):\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['entry_price'] = float(match.group(1))
                        except:
                            pass
                elif "Close Price (Actual Fill):" in line:
                    match = re.search(r'Close Price \(Actual Fill\):\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['exit_price'] = float(match.group(1))
                        except:
                            pass
                elif "Profit:" in line:
                    match = re.search(r'Profit:\s+\$([\d.]+)', line)
                    if match:
                        try:
                            current_trade['profit_usd'] = float(match.group(1))
                        except:
                            pass
                elif "Spread:" in line:
                    match = re.search(r'Spread:\s+([\d.]+)', line)
                    if match:
                        try:
                            current_trade['additional_info']['spread_points'] = float(match.group(1))
                        except:
                            pass
                elif "Execution Time:" in line:
                    match = re.search(r'Execution Time:\s+([\d.]+)\s+ms', line)
                    if match:
                        try:
                            current_trade['additional_info']['execution_time_ms'] = float(match.group(1))
                        except:
                            pass
                elif "=" * 80 in line or "=" * 50 in line:
                    in_execution_block = False
                    if current_trade and current_trade['order_id']:
                        trades.append(current_trade)
                        current_trade = None
            
            i += 1
        
        # Save any remaining open trades
        for ticket, trade in open_trades_by_ticket.items():
            # Only add if not already added (as CLOSED)
            if trade.get('status') != 'CLOSED':
                trades.append(trade)
        
        # Save last trade if exists and not already saved
        if current_trade and current_trade['order_id']:
            if current_trade['order_id'] not in open_trades_by_ticket:
                trades.append(current_trade)
        
        return trades
    
    def parse_bot_log_trades(self, file_path: str) -> List[Dict[str, Any]]:
        """Parse bot_log.txt for trade executions and closures."""
        trades = []
        open_trades: Dict[str, Dict[str, Any]] = {}  # ticket -> trade
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e:
            self.errors.append(f"Error reading {file_path}: {e}")
            return []
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Parse timestamp
            timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            timestamp_str = timestamp_match.group(1) if timestamp_match else None
            
            # Parse "Order placed successfully" lines (they come before TRADE EXECUTED)
            # Order placed successfully: Ticket 466976692, Symbol EBAYm, Type SELL, Volume 0.01, SL 83.79000, Entry Price: 83.59000 (requested: 83.59000)
            if "Order placed successfully:" in line:
                match = re.search(
                    r'Order placed successfully:\s+Ticket\s+(\d+),\s+Symbol\s+(\w+),\s+Type\s+(BUY|SELL)',
                    line
                )
                if match:
                    ticket = match.group(1)
                    symbol = match.group(2)
                    order_type = match.group(3)
                    trade_type = 'LONG' if order_type == 'BUY' else 'SHORT'
                    
                    # Extract additional info
                    volume_match = re.search(r'Volume\s+([\d.]+)', line)
                    entry_match = re.search(r'Entry Price:\s+([\d.]+)', line)
                    sl_match = re.search(r'SL\s+([\d.]+)', line)
                    
                    # Create or update trade
                    if ticket not in open_trades:
                        trade = {
                            'timestamp': timestamp_str,
                            'symbol': symbol,
                            'trade_type': trade_type,
                            'entry_price': None,
                            'exit_price': None,
                            'profit_usd': None,
                            'status': 'OPEN',
                            'order_id': ticket,
                            'additional_info': {}
                        }
                        open_trades[ticket] = trade
                    
                    trade = open_trades[ticket]
                    if entry_match:
                        trade['entry_price'] = float(entry_match.group(1))
                    if volume_match:
                        trade['additional_info']['lot_size'] = float(volume_match.group(1))
                    if sl_match:
                        trade['additional_info']['stop_loss_price'] = float(sl_match.group(1))
            
            # Parse trade execution (simple format)
            # ✅ TRADE EXECUTED: EBAYm SHORT | Ticket: 466976692 | Entry: 83.59000 (req: 83.59000) | Lot: 0.0100 | SL: 10.0pips | Q:80.0 | Risk: $0.10
            elif "✅ TRADE EXECUTED:" in line:
                match = re.search(
                    r'✅ TRADE EXECUTED:\s+(\w+)\s+(LONG|SHORT)\s+\|\s+Ticket:\s+(\d+)\s+\|\s+Entry:\s+([\d.]+)',
                    line
                )
                if match:
                    symbol = match.group(1)
                    trade_type = match.group(2)
                    ticket = match.group(3)
                    entry_price = float(match.group(4))
                    
                    # Extract additional info
                    lot_match = re.search(r'Lot:\s+([\d.]+)', line)
                    sl_match = re.search(r'SL:\s+([\d.]+)pips', line)
                    q_match = re.search(r'Q:([\d.]+)', line)
                    risk_match = re.search(r'Risk:\s+\$([\d.]+)', line)
                    
                    trade = {
                        'timestamp': timestamp_str,
                        'symbol': symbol,
                        'trade_type': trade_type,
                        'entry_price': entry_price,
                        'exit_price': None,
                        'profit_usd': None,
                        'status': 'OPEN',
                        'order_id': ticket,
                        'additional_info': {}
                    }
                    
                    if lot_match:
                        trade['additional_info']['lot_size'] = float(lot_match.group(1))
                    if sl_match:
                        trade['additional_info']['stop_loss_pips'] = float(sl_match.group(1))
                    if q_match:
                        trade['additional_info']['quality_score'] = float(q_match.group(1))
                    if risk_match:
                        trade['additional_info']['risk_usd'] = float(risk_match.group(1))
                    
                    open_trades[ticket] = trade
            
            # Parse position closure (if any in bot_log.txt)
            # Format: 🔴 POSITION CLOSED: ETHUSDm Ticket 467012733 | Entry: 3113.93000 | Close: 3115.81000 | Profit: +$0.19 | Duration: 1.2min | Reason: Take Profit or Trailing Stop
            elif "🔴 POSITION CLOSED:" in line:
                match = re.search(
                    r'🔴 POSITION CLOSED:\s+(\w+)\s+Ticket\s+(\d+)\s+\|\s+Entry:\s+([\d.]+)\s+\|\s+Close:\s+([\d.]+)\s+\|\s+Profit:\s+([+-]?\$?)([\d.]+)',
                    line
                )
                if match:
                    symbol = match.group(1)
                    ticket = match.group(2)
                    entry_price = float(match.group(3))
                    exit_price = float(match.group(4))
                    profit_sign = match.group(5)
                    profit_value = float(match.group(6))
                    
                    # Determine profit sign
                    if '-' in profit_sign:
                        profit_usd = -profit_value
                    else:
                        profit_usd = profit_value
                    
                    # Extract duration and reason
                    duration_match = re.search(r'Duration:\s+([\d.]+)min', line)
                    reason_match = re.search(r'Reason:\s+(.+?)(?:\s*$|\s*\|)', line)
                    
                    # Check if we already have this trade open
                    if ticket in open_trades:
                        trade = open_trades[ticket]
                        trade['exit_price'] = exit_price
                        trade['profit_usd'] = profit_usd
                        trade['status'] = 'CLOSED'
                        trade['timestamp'] = timestamp_str or trade['timestamp']
                        if duration_match:
                            trade['additional_info']['duration_minutes'] = float(duration_match.group(1))
                        if reason_match:
                            trade['additional_info']['close_reason'] = reason_match.group(1).strip()
                        trades.append(trade)
                        del open_trades[ticket]
                    else:
                        # Orphaned closure - create new trade entry
                        trade = {
                            'timestamp': timestamp_str,
                            'symbol': symbol,
                            'trade_type': None,  # Not available in closure line
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'profit_usd': profit_usd,
                            'status': 'CLOSED',
                            'order_id': ticket,
                            'additional_info': {}
                        }
                        if duration_match:
                            trade['additional_info']['duration_minutes'] = float(duration_match.group(1))
                        if reason_match:
                            trade['additional_info']['close_reason'] = reason_match.group(1).strip()
                        trades.append(trade)
            elif "POSITION CLOSED" in line and "🔴" not in line:
                ticket_match = re.search(r'Ticket[:\s]+(\d+)', line)
                if ticket_match:
                    ticket = ticket_match.group(1)
                    if ticket in open_trades:
                        trade = open_trades[ticket]
                        
                        # Try to extract close info
                        close_price_match = re.search(r'Close[:\s]+([\d.]+)', line)
                        profit_match = re.search(r'Profit[:\s]+([+-]?\$?)([\d.]+)', line)
                        
                        if close_price_match:
                            trade['exit_price'] = float(close_price_match.group(1))
                        if profit_match:
                            profit_sign = profit_match.group(1)
                            profit_value = float(profit_match.group(2))
                            if '-' in profit_sign:
                                trade['profit_usd'] = -profit_value
                            else:
                                trade['profit_usd'] = profit_value
                        
                        trade['status'] = 'CLOSED'
                        trade['timestamp'] = timestamp_str or trade['timestamp']
                        trades.append(trade)
                        del open_trades[ticket]
            
            i += 1
        
        # Add remaining open trades
        for trade in open_trades.values():
            trades.append(trade)
        
        return trades
    
    def normalize_symbol_name(self, symbol: str) -> str:
        """Normalize symbol name (remove 'm' suffix if present for consistency)."""
        # Keep original but ensure consistent naming
        return symbol
    
    def convert_to_jsonl_entry(self, trade: Dict[str, Any]) -> Optional[str]:
        """Convert trade dict to JSONL entry string."""
        try:
            # Ensure required fields
            if not trade.get('order_id') or not trade.get('symbol'):
                return None
            
            # Build JSON entry
            entry = {
                'timestamp': trade.get('timestamp') or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'symbol': trade['symbol'],
                'trade_type': trade.get('trade_type') or 'UNKNOWN',
                'entry_price': trade.get('entry_price'),
                'exit_price': trade.get('exit_price'),
                'profit_usd': trade.get('profit_usd'),
                'status': trade.get('status', 'OPEN'),
                'order_id': trade['order_id'],
                'additional_info': trade.get('additional_info', {})
            }
            
            return json.dumps(entry, ensure_ascii=False)
        except Exception as e:
            self.errors.append(f"Error converting trade to JSONL: {e}, trade: {trade}")
            return None
    
    def convert_all(self):
        """Convert all legacy logs to new format."""
        print("=" * 80)
        print("LEGACY LOG CONVERSION")
        print("=" * 80)
        print()
        
        # Ensure output directory exists
        os.makedirs('logs/trades', exist_ok=True)
        os.makedirs('logs/system', exist_ok=True)
        
        # Step 1: Parse bot_log.txt
        if os.path.exists('bot_log.txt'):
            print("Parsing bot_log.txt...")
            bot_trades = self.parse_bot_log_trades('bot_log.txt')
            print(f"  Found {len(bot_trades)} trades in bot_log.txt")
            
            for trade in bot_trades:
                symbol = self.normalize_symbol_name(trade['symbol'])
                self.trades[symbol].append(trade)
        else:
            print("bot_log.txt not found, skipping...")
        
        # Step 2: Parse all symbol log files
        symbols_dir = Path('logs/symbols')
        if symbols_dir.exists():
            print(f"\nParsing logs/symbols/*.log files...")
            symbol_files = list(symbols_dir.glob('*.log'))
            print(f"  Found {len(symbol_files)} symbol log files")
            
            for file_path in symbol_files:
                symbol_trades = self.parse_symbol_log(str(file_path))
                if symbol_trades:
                    # Extract symbol from first trade or filename
                    if symbol_trades and symbol_trades[0].get('symbol'):
                        symbol = self.normalize_symbol_name(symbol_trades[0]['symbol'])
                    else:
                        filename = file_path.stem
                        match = re.match(r'([^_]+)_\d{4}-\d{2}-\d{2}', filename)
                        symbol = match.group(1) if match else filename
                    
                    self.trades[symbol].extend(symbol_trades)
        
        # Step 3: Sort trades by timestamp for each symbol
        print("\nSorting trades chronologically...")
        for symbol in self.trades:
            self.trades[symbol].sort(key=lambda x: x.get('timestamp') or '0000-00-00 00:00:00')
        
        # Step 4: Write to new format
        print("\nWriting to logs/trades/SYMBOL.log...")
        for symbol, symbol_trades in self.trades.items():
            output_file = f'logs/trades/{symbol}.log'
            
            # Check if file already exists (for backup)
            backup_file = None
            if os.path.exists(output_file):
                backup_file = f'{output_file}.backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                print(f"  Warning: {output_file} exists, will create backup: {backup_file}")
            
            try:
                # Read existing entries if file exists
                existing_entries = []
                if os.path.exists(output_file) and backup_file:
                    with open(output_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    existing_entries.append(json.loads(line))
                                except:
                                    pass
                
                # Merge and deduplicate by order_id
                existing_by_id = {e.get('order_id'): e for e in existing_entries}
                new_by_id = {t.get('order_id'): t for t in symbol_trades}
                
                # Combine, preferring new entries
                combined = {**existing_by_id, **new_by_id}
                all_trades = list(combined.values())
                
                # Sort chronologically
                all_trades.sort(key=lambda x: x.get('timestamp') or '0000-00-00 00:00:00')
                
                # Write to file
                with open(output_file, 'w', encoding='utf-8') as f:
                    for trade in all_trades:
                        jsonl_line = self.convert_to_jsonl_entry(trade)
                        if jsonl_line:
                            f.write(jsonl_line + '\n')
                
                # Create backup if needed
                if backup_file and existing_entries:
                    with open(backup_file, 'w', encoding='utf-8') as f:
                        for entry in existing_entries:
                            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    print(f"  Backup created: {backup_file}")
                
                # Update stats
                closed = sum(1 for t in symbol_trades if t.get('status') == 'CLOSED')
                open_count = len(symbol_trades) - closed
                
                self.stats['total_trades'] += len(symbol_trades)
                self.stats['trades_by_symbol'][symbol] = len(symbol_trades)
                self.stats['open_trades'] += open_count
                self.stats['closed_trades'] += closed
                
                print(f"  {symbol}: {len(symbol_trades)} trades ({closed} closed, {open_count} open)")
                
            except Exception as e:
                self.errors.append(f"Error writing {output_file}: {e}")
        
        # Step 5: Write error log
        if self.errors:
            error_log_path = 'logs/system/conversion_errors.log'
            with open(error_log_path, 'w', encoding='utf-8') as f:
                f.write(f"Legacy Log Conversion Errors\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 80 + "\n\n")
                for error in self.errors:
                    f.write(f"{error}\n")
            
            self.stats['errors'] = len(self.errors)
            print(f"\n⚠️  {len(self.errors)} errors encountered, see {error_log_path}")
        
        # Step 6: Print summary
        print("\n" + "=" * 80)
        print("CONVERSION SUMMARY")
        print("=" * 80)
        print(f"Total trades converted: {self.stats['total_trades']}")
        print(f"  Closed trades: {self.stats['closed_trades']}")
        print(f"  Open trades: {self.stats['open_trades']}")
        print(f"Errors: {self.stats['errors']}")
        print(f"\nTrades by symbol:")
        for symbol, count in sorted(self.stats['trades_by_symbol'].items()):
            print(f"  {symbol}: {count}")
        print("\nConversion complete!")


def main():
    converter = LegacyLogConverter()
    converter.convert_all()


if __name__ == "__main__":
    main()

