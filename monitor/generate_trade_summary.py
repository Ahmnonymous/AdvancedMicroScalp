#!/usr/bin/env python3
"""
Generate Trade Summary Report
Analyzes bot_log.txt and generates a comprehensive trade summary.
"""

import re
from datetime import datetime
from collections import defaultdict

def parse_trade_logs():
    """Parse bot_log.txt for trade information."""
    trades = defaultdict(lambda: {
        'symbol': None,
        'direction': None,
        'entry_time': None,
        'entry_price': None,
        'lot_size': None,
        'initial_sl_pips': None,
        'sl_adjustments': [],
        'close_time': None,
        'close_reason': None,
        'final_profit': None
    })
    
    try:
        with open('bot_log.txt', 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            # Parse trade execution
            if "Trade executed:" in line or "Order placed successfully:" in line:
                # Try different patterns
                match = re.search(r'Ticket (\d+).*?(\w+)\s+(LONG|SHORT).*?Lot: ([\d.]+).*?SL: ([\d.]+)', line)
                if not match:
                    match = re.search(r'Ticket (\d+).*?Symbol (\w+).*?Type (BUY|SELL)', line)
                    if match:
                        ticket = int(match.group(1))
                        trades[ticket]['symbol'] = match.group(2)
                        trades[ticket]['direction'] = 'LONG' if match.group(3) == 'BUY' else 'SHORT'
                        # Try to get lot size
                        lot_match = re.search(r'Volume ([\d.]+)', line)
                        if lot_match:
                            trades[ticket]['lot_size'] = float(lot_match.group(1))
                        # Try to get SL
                        sl_match = re.search(r'SL ([\d.]+)', line)
                        if sl_match:
                            trades[ticket]['initial_sl_price'] = float(sl_match.group(1))
                else:
                    ticket = int(match.group(1))
                    trades[ticket]['symbol'] = match.group(2)
                    trades[ticket]['direction'] = match.group(3)
                    trades[ticket]['lot_size'] = float(match.group(4))
                    trades[ticket]['initial_sl_pips'] = float(match.group(5))
                
                # Extract timestamp
                time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                if time_match and ticket:
                    trades[ticket]['entry_time'] = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
            
            # Parse trailing stop adjustments
            if "TRAILING STOP:" in line:
                match = re.search(r'Ticket (\d+)', line)
                if match:
                    ticket = int(match.group(1))
                    # Also try to get symbol from line
                    symbol_match = re.search(r'\((\w+)\s+(BUY|SELL)\)', line)
                    if symbol_match and not trades[ticket]['symbol']:
                        trades[ticket]['symbol'] = symbol_match.group(1)
                        trades[ticket]['direction'] = 'LONG' if symbol_match.group(2) == 'BUY' else 'SHORT'
                    
                    profit_match = re.search(r'Profit: \$([\d.]+)', line)
                    sl_profit_match = re.search(r'SL Profit: \$([\d.]+)', line)
                    sl_price_match = re.search(r'SL Price: ([\d.]+)', line)
                    reason_match = re.search(r'Reason: (.+?)(?:\s*$|\s*\||\s*$)', line)
                    time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    
                    if profit_match and sl_profit_match:
                        adj = {
                            'time': datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S') if time_match else None,
                            'profit': float(profit_match.group(1)),
                            'sl_profit': float(sl_profit_match.group(1)),
                            'sl_price': float(sl_price_match.group(1)) if sl_price_match else None,
                            'reason': reason_match.group(1) if reason_match else 'Unknown'
                        }
                        trades[ticket]['sl_adjustments'].append(adj)
            
            # Parse big jump
            if "BIG JUMP:" in line:
                match = re.search(r'Ticket (\d+)', line)
                if match:
                    ticket = int(match.group(1))
                    trades[ticket]['big_jumps'] = trades[ticket].get('big_jumps', 0) + 1
        
        return trades
    
    except Exception as e:
        print(f"Error parsing logs: {e}")
        return {}

def generate_summary():
    """Generate trade summary report."""
    trades = parse_trade_logs()
    
    if not trades:
        print("No trades found in logs.")
        return
    
    print("=" * 80)
    print("TRADE SUMMARY REPORT")
    print("=" * 80)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    total_profit = 0.0
    total_trades = len(trades)
    total_sl_adjustments = sum(len(t['sl_adjustments']) for t in trades.values())
    
    for ticket, trade in sorted(trades.items()):
        print("=" * 80)
        print(f"TRADE: Ticket {ticket}")
        print("=" * 80)
        print(f"Symbol: {trade['symbol']} {trade['direction']}")
        if trade['entry_time']:
            print(f"Entry Time: {trade['entry_time'].strftime('%Y-%m-%d %H:%M:%S')}")
        if trade['lot_size']:
            print(f"Lot Size: {trade['lot_size']}")
        if trade['initial_sl_pips']:
            print(f"Initial Stop Loss: {trade['initial_sl_pips']:.1f} pips")
        
        if trade['sl_adjustments']:
            print(f"\nSL Adjustments: {len(trade['sl_adjustments'])}")
            print("-" * 80)
            for i, adj in enumerate(trade['sl_adjustments'], 1):
                time_str = adj['time'].strftime('%H:%M:%S') if adj['time'] else 'N/A'
                sl_price_str = f"{adj['sl_price']:.5f}" if adj['sl_price'] else 'N/A'
                print(f"  {i}. Time: {time_str} | "
                      f"Profit: ${adj['profit']:.2f} | "
                      f"SL Profit: ${adj['sl_profit']:.2f} | "
                      f"SL Price: {sl_price_str} | "
                      f"Reason: {adj['reason']}")
        
        if trade.get('big_jumps', 0) > 0:
            print(f"\nBig Jumps Detected: {trade['big_jumps']}")
        
        print()
    
    print("=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    print(f"Total Trades: {total_trades}")
    print(f"Total SL Adjustments: {total_sl_adjustments}")
    print(f"Average SL Adjustments per Trade: {total_sl_adjustments / total_trades:.1f}")
    print("=" * 80)

if __name__ == '__main__':
    generate_summary()

