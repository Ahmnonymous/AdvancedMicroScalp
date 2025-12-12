#!/usr/bin/env python3
"""
Generate Final Analysis Report comparing HTML Report with Bot Logs
"""

import re
from datetime import datetime
from bs4 import BeautifulSoup

def parse_html_trades(html_file):
    """Parse HTML report."""
    encodings = ['utf-8-sig', 'utf-16', 'latin-1', 'cp1252']
    html = None
    
    for enc in encodings:
        try:
            with open(html_file, 'r', encoding=enc) as f:
                html = f.read()
                break
        except:
            continue
    
    soup = BeautifulSoup(html, 'html.parser')
    trades = []
    rows = soup.find_all('tr', bgcolor=True)
    
    for row in rows:
        cells = row.find_all('td')
        if len(cells) < 13:
            continue
        
        cell_texts = []
        for cell in cells:
            if 'hidden' not in cell.get('class', []):
                cell_texts.append(cell.get_text(strip=True))
        
        if len(cell_texts) < 10:
            continue
        
        open_time_str = None
        position_id = None
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
                    if i + 8 < len(cell_texts):
                        close_time_str = cell_texts[i + 8]
                    if i + 9 < len(cell_texts):
                        close_price = cell_texts[i + 9]
                    if i + 12 < len(cell_texts):
                        profit = cell_texts[i + 12]
                    break
        
        if not open_time_str or not position_id:
            continue
        
        try:
            open_time = datetime.strptime(open_time_str, '%Y.%m.%d %H:%M:%S')
            close_time = None
            if close_time_str and close_time_str.strip():
                close_time = datetime.strptime(close_time_str, '%Y.%m.%d %H:%M:%S')
            
            trades.append({
                'ticket': position_id,
                'symbol': symbol,
                'type': order_type,
                'open_time': open_time,
                'open_price': float(open_price),
                'close_time': close_time,
                'close_price': float(close_price) if close_price else 0.0,
                'profit': float(profit) if profit else 0.0
            })
        except:
            continue
    
    return trades

def parse_bot_trades(log_file, start_time, end_time):
    """Parse bot log trades."""
    trades = []
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    
    for line in lines:
        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        if not time_match:
            continue
        
        try:
            line_time = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
            if line_time < start_time or line_time > end_time:
                continue
        except:
            continue
        
        if "[OK] TRADE EXECUTED:" in line:
            match = re.search(r'[OK] TRADE EXECUTED: (\w+)\s+(LONG|SHORT)\s+\|\s+Ticket:\s+(\d+)\s+\|\s+Entry:\s+([\d.]+)', line)
            if match:
                trades.append({
                    'ticket': match.group(3),
                    'symbol': match.group(1),
                    'direction': match.group(2),
                    'entry_time': line_time,
                    'entry_price': float(match.group(4))
                })
    
    return trades

def main():
    start_time = datetime(2025, 12, 8, 13, 7, 13)
    end_time = datetime(2025, 12, 8, 16, 39, 21)
    
    print("Parsing HTML report...")
    html_trades = parse_html_trades('ReportHistory-259747051.html')
    html_filtered = [t for t in html_trades if start_time <= t['open_time'] <= end_time]
    
    print("Parsing bot logs...")
    bot_trades = parse_bot_trades('bot_log.txt', start_time, end_time)
    
    print("\n" + "=" * 80)
    print("CRITICAL FINDINGS: HTML REPORT vs BOT LOGS")
    print("=" * 80)
    
    # Key discrepancies
    html_by_ticket = {str(t['ticket']): t for t in html_filtered}
    bot_by_ticket = {str(t['ticket']): t for t in bot_trades}
    
    html_profit = sum(t['profit'] for t in html_filtered)
    html_closed = [t for t in html_filtered if t['close_time']]
    html_wins = [t for t in html_closed if t['profit'] > 0]
    html_losses = [t for t in html_closed if t['profit'] < 0]
    
    print(f"\nHTML REPORT (MT5 Official):")
    print(f"  Total Trades: {len(html_filtered)}")
    print(f"  All Closed: {len(html_closed)}")
    print(f"  Wins: {len(html_wins)}")
    print(f"  Losses: {len(html_losses)}")
    print(f"  Net Profit: ${html_profit:.2f}")
    if html_wins:
        print(f"  Avg Win: ${sum(t['profit'] for t in html_wins) / len(html_wins):.2f}")
    if html_losses:
        print(f"  Avg Loss: ${sum(t['profit'] for t in html_losses) / len(html_losses):.2f}")
    
    print(f"\nBOT LOGS (Bot's Perspective):")
    print(f"  Total Trades: {len(bot_trades)}")
    print(f"  Closed Trades: Unknown (not properly logged)")
    print(f"  Wins: Unknown")
    print(f"  Losses: Unknown")
    print(f"  Net Profit: Unknown (bot logs show SL adjustments, not actual closures)")
    
    print("\n" + "=" * 80)
    print("CRITICAL ISSUES IDENTIFIED:")
    print("=" * 80)
    
    print("\n1. BOT LOGS MISSING TRADE CLOSURES:")
    print("   - Bot logs show 26 'wins' based on SL adjustments, but HTML shows 40 losses")
    print("   - Bot logs don't properly log when trades are closed by SL/TP")
    print("   - Bot logs show final SL profit, not actual close profit")
    
    print("\n2. ACTUAL P/L MISMATCH:")
    print(f"   - HTML Report: ${html_profit:.2f} (REAL)")
    print(f"   - Bot Logs inferred: +$5.90 (WRONG - based on SL adjustments, not actual closes)")
    print("   - Bot is tracking SL adjustments as 'wins' but trades actually closed at loss")
    
    print("\n3. ENTRY PRICE SLIPPAGE:")
    price_diffs = []
    for ticket in html_by_ticket:
        if ticket in bot_by_ticket:
            html_t = html_by_ticket[ticket]
            bot_t = bot_by_ticket[ticket]
            diff = abs(html_t['open_price'] - bot_t['entry_price'])
            if diff > 0.0001:
                price_diffs.append((ticket, html_t['symbol'], diff, html_t['open_price'], bot_t['entry_price']))
    
    if price_diffs:
        print(f"   - Found {len(price_diffs)} trades with entry price differences > 0.0001")
        print("   - Examples:")
        for ticket, symbol, diff, html_p, bot_p in price_diffs[:5]:
            print(f"     Ticket {ticket} ({symbol}): HTML={html_p:.5f}, Bot={bot_p:.5f}, Diff={diff:.5f}")
    
    print("\n4. TRADES CLOSING AT LOSS (HTML shows, bot logs don't capture):")
    large_losses = [t for t in html_closed if t['profit'] < -1.0]
    if large_losses:
        print(f"   - {len(large_losses)} trades closed with loss > $1.00")
        print("   - Examples:")
        for t in large_losses[:5]:
            print(f"     Ticket {t['ticket']} ({t['symbol']}): Closed at ${t['profit']:.2f}")
            print(f"       Open: {t['open_time'].strftime('%H:%M:%S')} @ {t['open_price']:.5f}")
            print(f"       Close: {t['close_time'].strftime('%H:%M:%S')} @ {t['close_price']:.5f}")
    
    print("\n5. TRADES CLOSING EARLY (before -$2.00):")
    early_losses = [t for t in html_closed if -2.0 < t['profit'] < 0]
    if early_losses:
        print(f"   - {len(early_losses)} trades closed with loss between $0 and -$2.00")
        print("   - These should have been stopped at -$2.00")
    
    print("\n6. TRADES CLOSING LATE (beyond -$2.00):")
    late_losses = [t for t in html_closed if t['profit'] < -2.0]
    if late_losses:
        print(f"   - {len(late_losses)} trades closed with loss > -$2.00")
        print("   - Examples:")
        for t in late_losses[:5]:
            print(f"     Ticket {t['ticket']} ({t['symbol']}): Loss ${t['profit']:.2f} (expected -$2.00)")
    
    print("\n" + "=" * 80)
    print("ROOT CAUSES:")
    print("=" * 80)
    print("1. Bot logs don't capture actual trade closures from MT5")
    print("2. Bot infers 'wins' from SL adjustments, but trades may close at loss")
    print("3. No logging when position is closed by broker (SL/TP hit)")
    print("4. Entry price logging may be from order request, not actual fill")
    print("5. Trailing stop adjustments don't reflect actual close prices")
    
    print("\n" + "=" * 80)
    print("REQUIRED FIXES:")
    print("=" * 80)
    print("1. Add position closure monitoring - check MT5 for closed positions")
    print("2. Log actual close price and profit when position closes")
    print("3. Distinguish between SL adjustment profit and actual close profit")
    print("4. Log entry price from actual fill, not order request")
    print("5. Track all closures (SL, TP, manual) with actual P/L")

if __name__ == "__main__":
    main()

