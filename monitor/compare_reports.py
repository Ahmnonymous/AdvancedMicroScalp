#!/usr/bin/env python3
"""
Compare MT5 HTML Report with Bot Logs
Extracts trades from HTML report and compares with bot log entries.
"""

import re
from datetime import datetime
from collections import defaultdict
from bs4 import BeautifulSoup

def parse_html_report(html_file):
    """Parse MT5 HTML report and extract trades."""
    # Try different encodings
    encodings = ['utf-8-sig', 'utf-16', 'latin-1', 'cp1252']
    html = None
    
    for enc in encodings:
        try:
            with open(html_file, 'r', encoding=enc) as f:
                html = f.read()
                break
        except:
            continue
    
    if html is None:
        raise ValueError(f"Could not read {html_file} with any encoding")
    
    soup = BeautifulSoup(html, 'html.parser')
    trades = []
    
    # Find all position rows (they have bgcolor="#FFFFFF" or bgcolor="#F7F7F7")
    rows = soup.find_all('tr', bgcolor=True)
    
    for row in rows:
        cells = row.find_all('td')
        # Need at least 13 cells for a position row
        if len(cells) < 13:
            continue
        
        try:
            # Extract position data - handle hidden cells
            cell_texts = []
            for cell in cells:
                # Skip hidden cells
                if 'hidden' in cell.get('class', []):
                    continue
                cell_texts.append(cell.get_text(strip=True))
            
            if len(cell_texts) < 10:
                continue
            
            # Find the first cell with a date (open time)
            open_time_str = None
            position_id = None
            symbol = None
            order_type = None
            volume = None
            open_price = None
            sl = None
            tp = None
            close_time_str = None
            close_price = None
            commission = None
            swap = None
            profit = None
            
            # Look for date pattern in first cell
            for i, text in enumerate(cell_texts):
                if re.match(r'\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}', text):
                    if open_time_str is None:
                        open_time_str = text
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
                        if i + 6 < len(cell_texts):
                            sl = cell_texts[i + 6]
                        if i + 7 < len(cell_texts):
                            tp = cell_texts[i + 7]
                        # Close time is usually 8 cells after open time
                        if i + 8 < len(cell_texts):
                            close_time_str = cell_texts[i + 8]
                        if i + 9 < len(cell_texts):
                            close_price = cell_texts[i + 9]
                        if i + 10 < len(cell_texts):
                            commission = cell_texts[i + 10]
                        if i + 11 < len(cell_texts):
                            swap = cell_texts[i + 11]
                        if i + 12 < len(cell_texts):
                            profit = cell_texts[i + 12]
                        break
            
            if not open_time_str or not position_id:
                continue
            
            # Parse dates
            try:
                open_time = datetime.strptime(open_time_str, '%Y.%m.%d %H:%M:%S')
            except:
                continue
            
            close_time = None
            if close_time_str and close_time_str.strip():
                try:
                    close_time = datetime.strptime(close_time_str, '%Y.%m.%d %H:%M:%S')
                except:
                    pass
            
            # Convert values
            try:
                volume_float = float(volume.split('/')[0] if '/' in volume else volume)
                open_price_float = float(open_price) if open_price else 0.0
                close_price_float = float(close_price) if close_price else 0.0
                profit_float = float(profit) if profit else 0.0
                sl_float = float(sl) if sl else 0.0
            except:
                continue
            
            trades.append({
                'position_id': position_id,
                'symbol': symbol,
                'type': order_type,
                'volume': volume_float,
                'open_time': open_time,
                'open_price': open_price_float,
                'close_time': close_time,
                'close_price': close_price_float,
                'sl': sl_float,
                'profit': profit_float,
                'commission': commission,
                'swap': swap
            })
        except Exception as e:
            continue
    
    return trades

def parse_bot_log_trades(log_file, start_time, end_time):
    """Parse bot log for trades in the specified time range."""
    trades = []
    
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    
    for line in lines:
        # Check if line is in time range
        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        if not time_match:
            continue
        
        try:
            line_time = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
            if line_time < start_time or line_time > end_time:
                continue
        except:
            continue
        
        # Parse trade execution
        if "✅ TRADE EXECUTED:" in line:
            match = re.search(r'✅ TRADE EXECUTED: (\w+)\s+(LONG|SHORT)\s+\|\s+Ticket:\s+(\d+)\s+\|\s+Entry:\s+([\d.]+)\s+\|\s+Lot:\s+([\d.]+).*?SL:\s+([\d.]+)pips', line)
            if match:
                trades.append({
                    'ticket': int(match.group(3)),
                    'symbol': match.group(1),
                    'direction': match.group(2),
                    'entry_time': line_time,
                    'entry_price': float(match.group(4)),
                    'lot_size': float(match.group(5)),
                    'sl_pips': float(match.group(6)),
                    'source': 'bot_log'
                })
    
    return trades

def compare_trades(html_trades, bot_trades, start_time, end_time):
    """Compare trades from HTML report and bot logs."""
    print("=" * 80)
    print("TRADE COMPARISON REPORT")
    print("=" * 80)
    print(f"Time Range: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Filter HTML trades to time range
    html_trades_filtered = [t for t in html_trades if start_time <= t['open_time'] <= end_time]
    
    print(f"HTML Report Trades (in time range): {len(html_trades_filtered)}")
    print(f"Bot Log Trades (in time range): {len(bot_trades)}")
    print()
    
    # Create lookup dictionaries - convert to strings for comparison
    html_by_ticket = {str(t['position_id']): t for t in html_trades_filtered}
    bot_by_ticket = {str(t['ticket']): t for t in bot_trades}
    
    # Find matches
    html_tickets = set(html_by_ticket.keys())
    bot_tickets = set(bot_by_ticket.keys())
    
    matched_tickets = html_tickets & bot_tickets
    only_in_html = html_tickets - bot_tickets
    only_in_bot = bot_tickets - html_tickets
    
    print("=" * 80)
    print("COMPARISON RESULTS")
    print("=" * 80)
    print(f"Matched Trades (in both): {len(matched_tickets)}")
    print(f"Only in HTML Report: {len(only_in_html)}")
    print(f"Only in Bot Logs: {len(only_in_bot)}")
    print()
    
    # Analyze matched trades
    if matched_tickets:
        print("MATCHED TRADES ANALYSIS:")
        print("-" * 80)
        discrepancies = []
        
        for ticket in sorted(matched_tickets)[:20]:  # Show first 20
            html_trade = html_by_ticket[ticket]
            bot_trade = bot_by_ticket[ticket]
            
            # Compare key fields
            symbol_match = html_trade['symbol'] == bot_trade['symbol']
            price_diff = abs(html_trade['open_price'] - bot_trade['entry_price'])
            volume_match = abs(html_trade['volume'] - bot_trade['lot_size']) < 0.0001
            
            # Check for discrepancies
            issues = []
            if not symbol_match:
                issues.append(f"Symbol: HTML={html_trade['symbol']}, Bot={bot_trade['symbol']}")
            if price_diff > 0.0001:
                issues.append(f"Entry Price diff: {price_diff:.5f} (HTML={html_trade['open_price']:.5f}, Bot={bot_trade['entry_price']:.5f})")
            if not volume_match:
                issues.append(f"Volume: HTML={html_trade['volume']}, Bot={bot_trade['lot_size']}")
            
            if issues:
                discrepancies.append({
                    'ticket': ticket,
                    'html': html_trade,
                    'bot': bot_trade,
                    'issues': issues
                })
            
            # Show closure info
            close_info = ""
            if html_trade['close_time']:
                close_info = f" | Closed: {html_trade['close_time'].strftime('%H:%M:%S')} @ {html_trade['close_price']:.5f} | Profit: ${html_trade['profit']:.2f}"
            else:
                close_info = " | OPEN"
            
            print(f"Ticket {ticket}: {html_trade['symbol']} | "
                  f"HTML Open: {html_trade['open_time'].strftime('%H:%M:%S')} @ {html_trade['open_price']:.5f} | "
                  f"Bot Entry: {bot_trade['entry_time'].strftime('%H:%M:%S')} @ {bot_trade['entry_price']:.5f}"
                  f"{close_info}")
        
        if discrepancies:
            print()
            print(f"WARNING: DISCREPANCIES FOUND: {len(discrepancies)}")
            for disc in discrepancies[:10]:
                print(f"  Ticket {disc['ticket']}: {', '.join(disc['issues'])}")
        print()
    
    # Show trades only in HTML
    if only_in_html:
        print("TRADES ONLY IN HTML REPORT (not in bot logs):")
        print("-" * 80)
        for ticket in sorted(only_in_html)[:20]:
            trade = html_by_ticket[ticket]
            print(f"  Ticket {ticket}: {trade['symbol']} {trade['type']} | "
                  f"Open: {trade['open_time'].strftime('%Y-%m-%d %H:%M:%S')} @ {trade['open_price']:.5f} | "
                  f"Close: {trade['close_time'].strftime('%Y-%m-%d %H:%M:%S') if trade['close_time'] else 'OPEN'} @ {trade['close_price']:.5f} | "
                  f"Profit: ${trade['profit']:.2f}")
        print()
    
    # Show trades only in bot logs
    if only_in_bot:
        print("TRADES ONLY IN BOT LOGS (not in HTML report):")
        print("-" * 80)
        for ticket in sorted(only_in_bot)[:20]:
            trade = bot_by_ticket[ticket]
            print(f"  Ticket {ticket}: {trade['symbol']} {trade['direction']} | "
                  f"Entry: {trade['entry_time'].strftime('%Y-%m-%d %H:%M:%S')} @ {trade['entry_price']:.5f} | "
                  f"Lot: {trade['lot_size']:.4f}")
        print()
    
    # Summary statistics
    print("=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    
    if html_trades_filtered:
        html_profit = sum(t['profit'] for t in html_trades_filtered)
        html_closed = [t for t in html_trades_filtered if t['close_time']]
        html_wins = [t for t in html_closed if t['profit'] > 0]
        html_losses = [t for t in html_closed if t['profit'] < 0]
        
        print(f"HTML Report:")
        print(f"  Total Trades: {len(html_trades_filtered)}")
        print(f"  Closed Trades: {len(html_closed)}")
        print(f"  Wins: {len(html_wins)}")
        print(f"  Losses: {len(html_losses)}")
        print(f"  Net Profit: ${html_profit:.2f}")
        if html_wins:
            print(f"  Avg Win: ${sum(t['profit'] for t in html_wins) / len(html_wins):.2f}")
        if html_losses:
            print(f"  Avg Loss: ${sum(t['profit'] for t in html_losses) / len(html_losses):.2f}")
        print()
    
    print(f"Bot Logs:")
    print(f"  Total Trades: {len(bot_trades)}")
    print()

def main():
    # Define time range from bot session
    start_time = datetime(2025, 12, 8, 13, 7, 13)  # Bot start
    end_time = datetime(2025, 12, 8, 16, 39, 21)   # Last trade
    
    print("Parsing HTML report...")
    html_trades = parse_html_report('ReportHistory-259747051.html')
    print(f"Found {len(html_trades)} total trades in HTML report")
    
    print("Parsing bot logs...")
    bot_trades = parse_bot_log_trades('bot_log.txt', start_time, end_time)
    print(f"Found {len(bot_trades)} trades in bot logs")
    
    print()
    compare_trades(html_trades, bot_trades, start_time, end_time)

if __name__ == "__main__":
    main()

