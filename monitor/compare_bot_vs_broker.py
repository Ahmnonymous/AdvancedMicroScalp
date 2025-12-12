#!/usr/bin/env python3
"""
Bot vs Broker Trade Comparison Tool
Compares bot trade logs with broker HTML report for accuracy verification.

Features:
- Parses broker HTML report
- Parses bot JSONL trade logs
- Matches trades and identifies discrepancies
- Calculates performance metrics
- Generates detailed reports
"""

import os
import re
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from bs4 import BeautifulSoup


class BotBrokerComparator:
    """Comprehensive bot vs broker trade comparison system."""
    
    def __init__(self, broker_html_file: str = "TradingHistoryFromBroker.html"):
        self.broker_html_file = broker_html_file
        self.errors: List[str] = []
        self.stats = {
            'broker_trades': 0,
            'bot_trades': 0,
            'matched_trades': 0,
            'only_in_broker': 0,
            'only_in_bot': 0,
            'profit_discrepancies': 0,
            'status_discrepancies': 0
        }
        
    def log_error(self, message: str):
        """Log error message."""
        self.errors.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {message}")
        print(f"[WARNING]  ERROR: {message}")
    
    def parse_broker_html(self) -> List[Dict[str, Any]]:
        """Parse broker HTML report and extract all trades."""
        print("Parsing broker HTML report...")
        trades = []
        
        if not os.path.exists(self.broker_html_file):
            self.log_error(f"Broker HTML file not found: {self.broker_html_file}")
            return trades
        
        # Try different encodings
        encodings = ['utf-8-sig', 'utf-16', 'latin-1', 'cp1252', 'utf-8']
        html = None
        
        for enc in encodings:
            try:
                with open(self.broker_html_file, 'r', encoding=enc) as f:
                    html = f.read()
                    print(f"  Successfully read with encoding: {enc}")
                    break
            except Exception as e:
                continue
        
        if html is None:
            self.log_error(f"Could not read {self.broker_html_file} with any encoding")
            return trades
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find all position rows (they have bgcolor attribute)
            rows = soup.find_all('tr', bgcolor=True)
            print(f"  Found {len(rows)} table rows")
            
            for row_idx, row in enumerate(rows):
                try:
                    cells = row.find_all('td')
                    if len(cells) < 10:
                        continue
                    
                    # Extract position data - handle hidden cells
                    cell_texts = []
                    visible_cells = []
                    for cell in cells:
                        # Skip hidden cells (class="hidden" or display:none)
                        classes = cell.get('class', [])
                        if 'hidden' in classes:
                            continue
                        text = cell.get_text(strip=True)
                        # Skip test text
                        if text.lower() == 'test':
                            continue
                        # Skip empty text from colspan cells (they're just spacing)
                        if text:
                            cell_texts.append(text)
                            visible_cells.append(cell)
                    
                    if len(cell_texts) < 10:
                        continue
                    
                    # Look for date pattern (YYYY.MM.DD HH:MM:SS)
                    open_time_str = None
                    position_id = None
                    symbol = None
                    order_type = None
                    volume = None
                    open_price = None
                    close_time_str = None
                    close_price = None
                    commission = None
                    swap = None
                    profit = None
                    
                    # Find the first cell with a date (open time)
                    # Structure: Time, Position, Symbol, Type, Volume, Price, S/L, T/P, Close Time, Close Price, Commission, Swap, Profit
                    for i, text in enumerate(cell_texts):
                        if re.match(r'\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}', text):
                            open_time_str = text
                            # Extract fields based on position
                            if i + 1 < len(cell_texts):
                                position_id = cell_texts[i + 1]
                            if i + 2 < len(cell_texts):
                                symbol = cell_texts[i + 2]
                            if i + 3 < len(cell_texts):
                                order_type = cell_texts[i + 3]
                            if i + 4 < len(cell_texts):
                                volume = cell_texts[i + 4]
                            if i + 5 < len(cell_texts):
                                open_price = cell_texts[i + 5]
                            
                            # Look for close time (next date pattern)
                            # Structure: Time, Position, Symbol, Type, Volume, Price, S/L, T/P, Close Time, Close Price, Commission, Swap, Profit
                            for j in range(i + 6, len(cell_texts)):
                                if re.match(r'\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}', cell_texts[j]):
                                    close_time_str = cell_texts[j]
                                    # Close price is after close time
                                    if j + 1 < len(cell_texts):
                                        close_price = cell_texts[j + 1]
                                    # Commission, Swap, and Profit follow
                                    if j + 2 < len(cell_texts):
                                        commission = cell_texts[j + 2]
                                    if j + 3 < len(cell_texts):
                                        swap = cell_texts[j + 3]
                                    # Profit is usually the last cell (or second to last if colspan)
                                    # Look at the last visible cells
                                    if len(cell_texts) > j + 4:
                                        # Profit should be one of the last 2-3 cells
                                        for k in range(max(j + 4, len(cell_texts) - 2), len(cell_texts)):
                                            text_k = cell_texts[k]
                                            # Check if it looks like a profit value (numeric, possibly negative)
                                            cleaned = text_k.replace(',', '').replace('$', '').strip()
                                            if re.match(r'^-?\d+\.?\d*$', cleaned):
                                                try:
                                                    val = float(cleaned)
                                                    # Profit is usually between -10 and 10 for these trades
                                                    if -10 <= val <= 10 and profit is None:
                                                        profit = cleaned
                                                        break
                                                except:
                                                    pass
                                    break
                            break
                    
                    if not open_time_str or not position_id:
                        continue
                    
                    # Parse dates
                    try:
                        open_time = datetime.strptime(open_time_str, '%Y.%m.%d %H:%M:%S')
                    except Exception as e:
                        self.log_error(f"Failed to parse open_time '{open_time_str}': {e}")
                        continue
                    
                    close_time = None
                    if close_time_str and close_time_str.strip():
                        try:
                            close_time = datetime.strptime(close_time_str, '%Y.%m.%d %H:%M:%S')
                        except:
                            pass
                    
                    # Skip internal/external transfers (type is "in" or "out")
                    if order_type and order_type.lower() in ['in', 'out']:
                        continue
                    
                    # Convert values
                    try:
                        volume_str = volume.split('/')[0] if '/' in volume else volume
                        # Skip if volume is not numeric
                        if not re.match(r'^[\d.]+$', volume_str):
                            continue
                        volume_float = float(volume_str) if volume_str else 0.0
                        open_price_float = float(open_price) if open_price and re.match(r'^[\d.]+$', open_price) else 0.0
                        close_price_float = float(close_price) if close_price and re.match(r'^[\d.]+$', close_price) else 0.0
                        profit_float = float(profit.replace('$', '').replace(',', '').strip()) if profit and re.match(r'^[-\d.]+$', profit.replace('$', '').replace(',', '').strip()) else 0.0
                        
                        # Determine trade type
                        trade_type = "LONG" if order_type and ("BUY" in order_type.upper() or "LONG" in order_type.upper()) else "SHORT"
                        
                        trades.append({
                            'timestamp': open_time.strftime('%Y-%m-%d %H:%M:%S'),
                            'symbol': symbol,
                            'trade_type': trade_type,
                            'entry_price': open_price_float,
                            'exit_price': close_price_float if close_time else None,
                            'profit_usd': profit_float if close_time else None,
                            'status': 'CLOSED' if close_time else 'OPEN',
                            'order_id': str(position_id),
                            'close_time': close_time.strftime('%Y-%m-%d %H:%M:%S') if close_time else None,
                            'volume': volume_float,
                            'commission': commission,
                            'swap': swap,
                            'source': 'broker'
                        })
                    except Exception as e:
                        self.log_error(f"Failed to parse trade values for position {position_id}: {e}")
                        continue
                        
                except Exception as e:
                    self.log_error(f"Error parsing row {row_idx}: {e}")
                    continue
            
            print(f"  Successfully parsed {len(trades)} trades from broker report")
            self.stats['broker_trades'] = len(trades)
            
        except Exception as e:
            self.log_error(f"Error parsing HTML: {e}")
        
        return trades
    
    def parse_bot_logs(self) -> Tuple[List[Dict[str, Any]], Optional[datetime], Optional[datetime]]:
        """Parse all bot trade logs from logs/live/trades/*.log files.
        
        Returns:
            Tuple of (trades_list, earliest_timestamp, latest_timestamp)
        """
        print("Parsing bot trade logs...")
        all_trades = []
        timestamps = []
        trades_dir = Path('logs/trades')
        
        if not trades_dir.exists():
            self.log_error("logs/trades directory does not exist")
            return all_trades, None, None
        
        # Get all .log files (excluding backups)
        log_files = [f for f in trades_dir.glob('*.log') if 'backup' not in f.name]
        print(f"  Found {len(log_files)} trade log files")
        
        for log_file in log_files:
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        
                        # Check if JSONL format
                        if not line.startswith('{'):
                            continue
                        
                        try:
                            trade = json.loads(line)
                            
                            # Validate and normalize trade
                            if not trade.get('order_id') or not trade.get('symbol'):
                                continue
                            
                            # Parse timestamp
                            timestamp_str = trade.get('timestamp')
                            if timestamp_str:
                                try:
                                    timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                                    timestamps.append(timestamp)
                                except:
                                    pass
                            
                            # Ensure required fields
                            normalized_trade = {
                                'timestamp': timestamp_str,
                                'symbol': trade.get('symbol'),
                                'trade_type': trade.get('trade_type'),
                                'entry_price': trade.get('entry_price'),
                                'exit_price': trade.get('exit_price'),
                                'profit_usd': trade.get('profit_usd'),
                                'status': trade.get('status', 'OPEN'),
                                'order_id': str(trade.get('order_id')),
                                'additional_info': trade.get('additional_info', {}),
                                'source': 'bot'
                            }
                            
                            all_trades.append(normalized_trade)
                            
                        except json.JSONDecodeError as e:
                            self.log_error(f"{log_file.name}: Line {line_num} - JSON decode error: {e}")
                            continue
                        except Exception as e:
                            self.log_error(f"{log_file.name}: Line {line_num} - Error: {e}")
                            continue
            
            except Exception as e:
                self.log_error(f"Error reading {log_file}: {e}")
        
        # Determine time range
        earliest_time = min(timestamps) if timestamps else None
        latest_time = max(timestamps) if timestamps else None
        
        print(f"  Successfully parsed {len(all_trades)} trades from bot logs")
        if earliest_time and latest_time:
            print(f"  Bot trading period: {earliest_time.strftime('%Y-%m-%d %H:%M:%S')} to {latest_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.stats['bot_trades'] = len(all_trades)
        return all_trades, earliest_time, latest_time
    
    def filter_broker_trades_by_time(self, broker_trades: List[Dict], start_time: Optional[datetime], end_time: Optional[datetime]) -> List[Dict]:
        """Filter broker trades to only include those within bot trading period."""
        if not start_time or not end_time:
            print("  [WARNING]  No bot trading time range detected - comparing all broker trades")
            return broker_trades
        
        # Add small buffer (5 minutes before/after) to account for timing differences
        buffer_minutes = 5
        start_time_filtered = start_time - timedelta(minutes=buffer_minutes)
        end_time_filtered = end_time + timedelta(minutes=buffer_minutes)
        
        filtered_trades = []
        for trade in broker_trades:
            try:
                trade_time = datetime.strptime(trade['timestamp'], '%Y-%m-%d %H:%M:%S')
                if start_time_filtered <= trade_time <= end_time_filtered:
                    filtered_trades.append(trade)
            except:
                # If timestamp parsing fails, include the trade to be safe
                filtered_trades.append(trade)
        
        print(f"  Filtered broker trades: {len(broker_trades)} total -> {len(filtered_trades)} in bot trading period")
        print(f"  Time range filter: {start_time_filtered.strftime('%Y-%m-%d %H:%M:%S')} to {end_time_filtered.strftime('%Y-%m-%d %H:%M:%S')}")
        
        return filtered_trades
    
    def match_trades(self, broker_trades: List[Dict], bot_trades: List[Dict]) -> Dict[str, Any]:
        """Match broker and bot trades."""
        print("Matching trades...")
        
        # Create lookup dictionaries
        broker_by_id = {t['order_id']: t for t in broker_trades if t.get('order_id')}
        bot_by_id = {t['order_id']: t for t in bot_trades if t.get('order_id')}
        
        # Also create by timestamp+symbol+trade_type for fuzzy matching
        broker_by_key = {}
        for t in broker_trades:
            key = f"{t['timestamp']}|{t['symbol']}|{t['trade_type']}"
            if key not in broker_by_key:
                broker_by_key[key] = []
            broker_by_key[key].append(t)
        
        bot_by_key = {}
        for t in bot_trades:
            key = f"{t['timestamp']}|{t['symbol']}|{t['trade_type']}"
            if key not in bot_by_key:
                bot_by_key[key] = []
            bot_by_key[key].append(t)
        
        # Match by order_id
        broker_ids = set(broker_by_id.keys())
        bot_ids = set(bot_by_id.keys())
        matched_ids = broker_ids & bot_ids
        
        matched_trades = []
        for order_id in matched_ids:
            broker_trade = broker_by_id[order_id]
            bot_trade = bot_by_id[order_id]
            
            # Check for discrepancies
            discrepancies = []
            profit_diff = None
            
            if broker_trade.get('profit_usd') is not None and bot_trade.get('profit_usd') is not None:
                profit_diff = abs(broker_trade['profit_usd'] - bot_trade['profit_usd'])
                if profit_diff > 0.01:  # More than 1 cent difference
                    discrepancies.append(f"Profit difference: ${profit_diff:.2f} (Broker: ${broker_trade['profit_usd']:.2f}, Bot: ${bot_trade['profit_usd']:.2f})")
                    self.stats['profit_discrepancies'] += 1
            
            if broker_trade['status'] != bot_trade['status']:
                discrepancies.append(f"Status mismatch: Broker={broker_trade['status']}, Bot={bot_trade['status']}")
                self.stats['status_discrepancies'] += 1
            
            if abs(broker_trade.get('entry_price', 0) - bot_trade.get('entry_price', 0)) > 0.0001:
                entry_diff = abs(broker_trade['entry_price'] - bot_trade['entry_price'])
                discrepancies.append(f"Entry price difference: {entry_diff:.5f}")
            
            matched_trades.append({
                'order_id': order_id,
                'broker': broker_trade,
                'bot': bot_trade,
                'discrepancies': discrepancies,
                'profit_diff': profit_diff
            })
        
        # Find unmatched trades
        only_in_broker = [broker_by_id[id] for id in (broker_ids - bot_ids)]
        only_in_bot = [bot_by_id[id] for id in (bot_ids - broker_ids)]
        
        # Try fuzzy matching by timestamp+symbol for unmatched trades
        fuzzy_matched = []
        for broker_trade in only_in_broker[:]:
            key = f"{broker_trade['timestamp']}|{broker_trade['symbol']}|{broker_trade['trade_type']}"
            if key in bot_by_key:
                # Found potential match
                for bot_trade in bot_by_key[key]:
                    if bot_trade['order_id'] not in matched_ids:
                        fuzzy_matched.append({
                            'broker': broker_trade,
                            'bot': bot_trade,
                            'match_type': 'fuzzy_timestamp_symbol',
                            'order_id_mismatch': True
                        })
                        only_in_broker.remove(broker_trade)
                        if bot_trade in only_in_bot:
                            only_in_bot.remove(bot_trade)
                        break
        
        self.stats['matched_trades'] = len(matched_trades)
        self.stats['only_in_broker'] = len(only_in_broker)
        self.stats['only_in_bot'] = len(only_in_bot)
        
        print(f"  Matched: {len(matched_trades)}")
        print(f"  Only in broker: {len(only_in_broker)}")
        print(f"  Only in bot: {len(only_in_bot)}")
        
        return {
            'matched': matched_trades,
            'only_in_broker': only_in_broker,
            'only_in_bot': only_in_bot,
            'fuzzy_matched': fuzzy_matched
        }
    
    def calculate_metrics(self, matches: Dict, broker_trades: List[Dict], bot_trades: List[Dict]) -> Dict[str, Any]:
        """Calculate performance metrics."""
        print("Calculating performance metrics...")
        
        # Broker metrics
        broker_closed = [t for t in broker_trades if t['status'] == 'CLOSED' and t.get('profit_usd') is not None]
        broker_profit = sum(t['profit_usd'] for t in broker_closed)
        broker_wins = [t for t in broker_closed if t['profit_usd'] > 0]
        broker_losses = [t for t in broker_closed if t['profit_usd'] < 0]
        
        # Bot metrics
        bot_closed = [t for t in bot_trades if t['status'] == 'CLOSED' and t.get('profit_usd') is not None]
        bot_profit = sum(t['profit_usd'] for t in bot_closed)
        bot_wins = [t for t in bot_closed if t['profit_usd'] > 0]
        bot_losses = [t for t in bot_closed if t['profit_usd'] < 0]
        
        # Micro-HFT detection (trades closed very quickly with small profits)
        broker_micro_hft = [t for t in broker_closed 
                           if t.get('additional_info', {}).get('duration_minutes', 999) < 5
                           and 0.01 <= abs(t['profit_usd']) <= 0.50]
        bot_micro_hft = [t for t in bot_closed 
                        if t.get('additional_info', {}).get('duration_minutes', 999) < 5
                        and 0.01 <= abs(t['profit_usd']) <= 0.50]
        
        metrics = {
            'broker': {
                'total_trades': len(broker_trades),
                'closed_trades': len(broker_closed),
                'open_trades': len(broker_trades) - len(broker_closed),
                'wins': len(broker_wins),
                'losses': len(broker_losses),
                'win_rate': (len(broker_wins) / len(broker_closed) * 100) if broker_closed else 0,
                'total_profit': broker_profit,
                'avg_profit': broker_profit / len(broker_closed) if broker_closed else 0,
                'avg_win': sum(t['profit_usd'] for t in broker_wins) / len(broker_wins) if broker_wins else 0,
                'avg_loss': sum(t['profit_usd'] for t in broker_losses) / len(broker_losses) if broker_losses else 0,
                'largest_profit': max((t['profit_usd'] for t in broker_closed), default=0),
                'largest_loss': min((t['profit_usd'] for t in broker_closed), default=0),
                'micro_hft_trades': len(broker_micro_hft),
                'micro_hft_profit': sum(t['profit_usd'] for t in broker_micro_hft)
            },
            'bot': {
                'total_trades': len(bot_trades),
                'closed_trades': len(bot_closed),
                'open_trades': len(bot_trades) - len(bot_closed),
                'wins': len(bot_wins),
                'losses': len(bot_losses),
                'win_rate': (len(bot_wins) / len(bot_closed) * 100) if bot_closed else 0,
                'total_profit': bot_profit,
                'avg_profit': bot_profit / len(bot_closed) if bot_closed else 0,
                'avg_win': sum(t['profit_usd'] for t in bot_wins) / len(bot_wins) if bot_wins else 0,
                'avg_loss': sum(t['profit_usd'] for t in bot_losses) / len(bot_losses) if bot_losses else 0,
                'largest_profit': max((t['profit_usd'] for t in bot_closed), default=0),
                'largest_loss': min((t['profit_usd'] for t in bot_closed), default=0),
                'micro_hft_trades': len(bot_micro_hft),
                'micro_hft_profit': sum(t['profit_usd'] for t in bot_micro_hft)
            },
            'matching': {
                'matched_trades': len(matches['matched']),
                'only_in_broker': len(matches['only_in_broker']),
                'only_in_bot': len(matches['only_in_bot']),
                'profit_discrepancies': self.stats['profit_discrepancies'],
                'status_discrepancies': self.stats['status_discrepancies']
            }
        }
        
        return metrics
    
    def generate_reports(self, matches: Dict, metrics: Dict, broker_trades: List[Dict], bot_trades: List[Dict], 
                        bot_start_time: Optional[datetime], bot_end_time: Optional[datetime]):
        """Generate summary and detailed reports."""
        print("Generating reports...")
        
        os.makedirs('logs/reports', exist_ok=True)
        # Log directory is created by logger_factory
        
        date_str = datetime.now().strftime('%Y-%m-%d')
        
        # Generate summary report
        summary_file = f'logs/reports/bot_vs_broker_summary_{date_str}.txt'
        self._write_summary_report(summary_file, matches, metrics, broker_trades, bot_trades, bot_start_time, bot_end_time)
        
        # Generate detailed CSV
        csv_file = f'logs/reports/bot_vs_broker_detailed_{date_str}.csv'
        self._write_csv_report(csv_file, matches, broker_trades, bot_trades)
        
        # Write error log
        if self.errors:
            error_file = 'logs/live/system/broker_comparison_errors.log'
            with open(error_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Comparison Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'='*80}\n")
                for error in self.errors:
                    f.write(f"{error}\n")
            print(f"  Errors logged to: {error_file}")
        
        print(f"  Summary report: {summary_file}")
        print(f"  Detailed CSV: {csv_file}")
    
    def _write_summary_report(self, filename: str, matches: Dict, metrics: Dict, 
                             broker_trades: List[Dict], bot_trades: List[Dict],
                             bot_start_time: Optional[datetime], bot_end_time: Optional[datetime]):
        """Write summary text report."""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("BOT VS BROKER TRADE COMPARISON REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Broker Report: {self.broker_html_file}\n")
            f.write(f"Bot Logs: logs/live/trades/*.log\n")
            if bot_start_time and bot_end_time:
                f.write(f"Bot Trading Period: {bot_start_time.strftime('%Y-%m-%d %H:%M:%S')} to {bot_end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Broker trades filtered to bot trading period only.\n")
            f.write("\n")
            
            # Summary Statistics
            f.write("=" * 80 + "\n")
            f.write("SUMMARY STATISTICS\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Broker Report:\n")
            f.write(f"  Total Trades: {metrics['broker']['total_trades']}\n")
            f.write(f"  Closed Trades: {metrics['broker']['closed_trades']}\n")
            f.write(f"  Open Trades: {metrics['broker']['open_trades']}\n")
            f.write(f"  Wins: {metrics['broker']['wins']}\n")
            f.write(f"  Losses: {metrics['broker']['losses']}\n")
            f.write(f"  Win Rate: {metrics['broker']['win_rate']:.2f}%\n")
            f.write(f"  Total Profit: ${metrics['broker']['total_profit']:.2f}\n")
            f.write(f"  Average Profit: ${metrics['broker']['avg_profit']:.2f}\n")
            f.write(f"  Average Win: ${metrics['broker']['avg_win']:.2f}\n")
            f.write(f"  Average Loss: ${metrics['broker']['avg_loss']:.2f}\n")
            f.write(f"  Largest Profit: ${metrics['broker']['largest_profit']:.2f}\n")
            f.write(f"  Largest Loss: ${metrics['broker']['largest_loss']:.2f}\n")
            f.write(f"  Micro-HFT Trades: {metrics['broker']['micro_hft_trades']}\n")
            f.write(f"  Micro-HFT Profit: ${metrics['broker']['micro_hft_profit']:.2f}\n")
            f.write("\n")
            
            f.write(f"Bot Logs:\n")
            f.write(f"  Total Trades: {metrics['bot']['total_trades']}\n")
            f.write(f"  Closed Trades: {metrics['bot']['closed_trades']}\n")
            f.write(f"  Open Trades: {metrics['bot']['open_trades']}\n")
            f.write(f"  Wins: {metrics['bot']['wins']}\n")
            f.write(f"  Losses: {metrics['bot']['losses']}\n")
            f.write(f"  Win Rate: {metrics['bot']['win_rate']:.2f}%\n")
            f.write(f"  Total Profit: ${metrics['bot']['total_profit']:.2f}\n")
            f.write(f"  Average Profit: ${metrics['bot']['avg_profit']:.2f}\n")
            f.write(f"  Average Win: ${metrics['bot']['avg_win']:.2f}\n")
            f.write(f"  Average Loss: ${metrics['bot']['avg_loss']:.2f}\n")
            f.write(f"  Largest Profit: ${metrics['bot']['largest_profit']:.2f}\n")
            f.write(f"  Largest Loss: ${metrics['bot']['largest_loss']:.2f}\n")
            f.write(f"  Micro-HFT Trades: {metrics['bot']['micro_hft_trades']}\n")
            f.write(f"  Micro-HFT Profit: ${metrics['bot']['micro_hft_profit']:.2f}\n")
            f.write("\n")
            
            # Matching Results
            f.write("=" * 80 + "\n")
            f.write("TRADE MATCHING RESULTS\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Matched Trades (by order_id): {metrics['matching']['matched_trades']}\n")
            f.write(f"Trades Only in Broker Report: {metrics['matching']['only_in_broker']}\n")
            f.write(f"Trades Only in Bot Logs: {metrics['matching']['only_in_bot']}\n")
            f.write(f"Profit Discrepancies (>$0.01): {metrics['matching']['profit_discrepancies']}\n")
            f.write(f"Status Discrepancies: {metrics['matching']['status_discrepancies']}\n")
            f.write("\n")
            
            # Discrepancies
            if metrics['matching']['profit_discrepancies'] > 0 or metrics['matching']['status_discrepancies'] > 0:
                f.write("=" * 80 + "\n")
                f.write("DISCREPANCIES\n")
                f.write("=" * 80 + "\n\n")
                
                discrepancy_count = 0
                for match in matches['matched']:
                    if match['discrepancies']:
                        discrepancy_count += 1
                        f.write(f"Order ID: {match['order_id']}\n")
                        f.write(f"  Symbol: {match['broker']['symbol']} | Type: {match['broker']['trade_type']}\n")
                        for disc in match['discrepancies']:
                            f.write(f"  [WARNING]  {disc}\n")
                        f.write("\n")
                        
                        if discrepancy_count >= 50:  # Limit output
                            f.write(f"... and {metrics['matching']['profit_discrepancies'] + metrics['matching']['status_discrepancies'] - discrepancy_count} more discrepancies\n")
                            break
                f.write("\n")
            
            # Unmatched Trades
            if matches['only_in_broker']:
                f.write("=" * 80 + "\n")
                f.write("TRADES ONLY IN BROKER REPORT\n")
                f.write("=" * 80 + "\n\n")
                for trade in matches['only_in_broker'][:50]:  # Limit to first 50
                    f.write(f"Order ID: {trade['order_id']}\n")
                    f.write(f"  Timestamp: {trade['timestamp']}\n")
                    f.write(f"  Symbol: {trade['symbol']} | Type: {trade['trade_type']}\n")
                    f.write(f"  Entry: {trade['entry_price']:.5f}\n")
                    if trade['exit_price']:
                        f.write(f"  Exit: {trade['exit_price']:.5f}\n")
                    if trade['profit_usd'] is not None:
                        f.write(f"  Profit: ${trade['profit_usd']:.2f}\n")
                    f.write(f"  Status: {trade['status']}\n")
                    f.write("\n")
                if len(matches['only_in_broker']) > 50:
                    f.write(f"... and {len(matches['only_in_broker']) - 50} more trades\n")
                f.write("\n")
            
            if matches['only_in_bot']:
                f.write("=" * 80 + "\n")
                f.write("TRADES ONLY IN BOT LOGS\n")
                f.write("=" * 80 + "\n\n")
                for trade in matches['only_in_bot'][:50]:  # Limit to first 50
                    f.write(f"Order ID: {trade['order_id']}\n")
                    f.write(f"  Timestamp: {trade['timestamp']}\n")
                    f.write(f"  Symbol: {trade['symbol']} | Type: {trade['trade_type']}\n")
                    f.write(f"  Entry: {trade['entry_price']:.5f}\n")
                    if trade['exit_price']:
                        f.write(f"  Exit: {trade['exit_price']:.5f}\n")
                    if trade['profit_usd'] is not None:
                        f.write(f"  Profit: ${trade['profit_usd']:.2f}\n")
                    f.write(f"  Status: {trade['status']}\n")
                    f.write("\n")
                if len(matches['only_in_bot']) > 50:
                    f.write(f"... and {len(matches['only_in_bot']) - 50} more trades\n")
                f.write("\n")
    
    def _write_csv_report(self, filename: str, matches: Dict, broker_trades: List[Dict], bot_trades: List[Dict]):
        """Write detailed CSV report."""
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Header
            writer.writerow([
                'Order ID', 'Source', 'Timestamp', 'Symbol', 'Type',
                'Entry Price', 'Exit Price', 'Profit USD', 'Status',
                'Broker Entry', 'Broker Exit', 'Broker Profit', 'Broker Status',
                'Bot Entry', 'Bot Exit', 'Bot Profit', 'Bot Status',
                'Profit Diff', 'Status Match', 'Discrepancies'
            ])
            
            # Matched trades
            for match in matches['matched']:
                broker = match['broker']
                bot = match['bot']
                writer.writerow([
                    match['order_id'], 'MATCHED', broker['timestamp'], broker['symbol'], broker['trade_type'],
                    broker.get('entry_price'), broker.get('exit_price'), broker.get('profit_usd'), broker['status'],
                    broker.get('entry_price'), broker.get('exit_price'), broker.get('profit_usd'), broker['status'],
                    bot.get('entry_price'), bot.get('exit_price'), bot.get('profit_usd'), bot['status'],
                    match.get('profit_diff', ''),
                    'YES' if broker['status'] == bot['status'] else 'NO',
                    '; '.join(match['discrepancies']) if match['discrepancies'] else ''
                ])
            
            # Only in broker
            for trade in matches['only_in_broker']:
                writer.writerow([
                    trade['order_id'], 'BROKER ONLY', trade['timestamp'], trade['symbol'], trade['trade_type'],
                    trade.get('entry_price'), trade.get('exit_price'), trade.get('profit_usd'), trade['status'],
                    trade.get('entry_price'), trade.get('exit_price'), trade.get('profit_usd'), trade['status'],
                    '', '', '', '',
                    '', 'N/A', 'Not in bot logs'
                ])
            
            # Only in bot
            for trade in matches['only_in_bot']:
                writer.writerow([
                    trade['order_id'], 'BOT ONLY', trade['timestamp'], trade['symbol'], trade['trade_type'],
                    trade.get('entry_price'), trade.get('exit_price'), trade.get('profit_usd'), trade['status'],
                    '', '', '', '',
                    trade.get('entry_price'), trade.get('exit_price'), trade.get('profit_usd'), trade['status'],
                    '', 'N/A', 'Not in broker report'
                ])
    
    def run(self):
        """Run full comparison process."""
        print("=" * 80)
        print("BOT VS BROKER TRADE COMPARISON")
        print("=" * 80)
        print()
        
        # Parse bot logs first to determine trading time range
        bot_trades, bot_start_time, bot_end_time = self.parse_bot_logs()
        if not bot_trades:
            print("[WARNING]  No bot trades found. Cannot proceed with comparison.")
            return
        
        print()
        
        # Parse broker report
        all_broker_trades = self.parse_broker_html()
        if not all_broker_trades:
            print("[WARNING]  No broker trades found. Cannot proceed with comparison.")
            return
        
        print()
        
        # Filter broker trades to bot trading period
        broker_trades = self.filter_broker_trades_by_time(all_broker_trades, bot_start_time, bot_end_time)
        
        print()
        
        # Match trades
        matches = self.match_trades(broker_trades, bot_trades)
        
        print()
        
        # Calculate metrics
        metrics = self.calculate_metrics(matches, broker_trades, bot_trades)
        
        print()
        
        # Generate reports
        self.generate_reports(matches, metrics, broker_trades, bot_trades, bot_start_time, bot_end_time)
        
        print()
        print("=" * 80)
        print("COMPARISON COMPLETE")
        print("=" * 80)
        print(f"Matched: {metrics['matching']['matched_trades']}")
        print(f"Only in Broker: {metrics['matching']['only_in_broker']}")
        print(f"Only in Bot: {metrics['matching']['only_in_bot']}")
        print(f"Profit Discrepancies: {metrics['matching']['profit_discrepancies']}")
        print(f"Status Discrepancies: {metrics['matching']['status_discrepancies']}")
        print()


def main():
    """Main entry point."""
    comparator = BotBrokerComparator()
    comparator.run()


if __name__ == "__main__":
    main()

