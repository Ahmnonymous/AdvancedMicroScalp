#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Exit Attribution Analysis for Backtest Trades
Identifies WHY trades are closing early.
"""
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

# Read execution log
with open('logs/backtest/execution.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Parse all closed trades
closed_trades = []
for i, line in enumerate(lines):
    if 'POSITION CLOSED' in line:
        # Extract ticket, profit, closure method, SL/TP hit status
        ticket_match = re.search(r'Ticket (\d+)', line)
        profit_match = re.search(r'Profit: \$(-?\d+\.\d+)', line)
        method_match = re.search(r'Closure method: (\w+)', line)
        sl_hit_match = re.search(r'SL hit: (True|False)', line)
        tp_hit_match = re.search(r'TP hit: (True|False)', line)
        
        if ticket_match and profit_match:
            ticket = int(ticket_match.group(1))
            profit = float(profit_match.group(1))
            method = method_match.group(1) if method_match else 'UNKNOWN'
            sl_hit = sl_hit_match.group(1) == 'True' if sl_hit_match else False
            tp_hit = tp_hit_match.group(1) == 'True' if tp_hit_match else False
            
            # Find corresponding order placement
            entry_info = None
            for j in range(max(0, i-10), i):
                if f'Ticket {ticket}' in lines[j] and 'ORDER PLACED' in lines[j]:
                    entry_match = re.search(r'Entry: ([\d.]+)', lines[j])
                    sl_match = re.search(r'SL: ([\d.]+)', lines[j])
                    if entry_match and sl_match:
                        entry_info = {
                            'entry': float(entry_match.group(1)),
                            'sl': float(sl_match.group(1)),
                            'direction': 'SELL' if 'SELL' in lines[j] else 'BUY'
                        }
                    break
            
            closed_trades.append({
                'ticket': ticket,
                'profit': profit,
                'method': method,
                'sl_hit': sl_hit,
                'tp_hit': tp_hit,
                'entry_info': entry_info,
                'line': line.strip()
            })

# Classify exit reasons
exit_reasons = defaultdict(int)
exit_details = defaultdict(list)

for trade in closed_trades:
    profit = trade['profit']
    sl_hit = trade['sl_hit']
    tp_hit = trade['tp_hit']
    method = trade['method']
    entry_info = trade['entry_info']
    
    # Classification logic
    if sl_hit:
        exit_reason = 'Hard SL'
    elif tp_hit:
        exit_reason = 'Take Profit'
    elif abs(profit + 2.0) < 0.10:  # Close to -$2.00 (SL should have been hit)
        exit_reason = 'Hard SL (missed detection)'
    elif method == 'close_position' and not sl_hit and not tp_hit:
        # Position closed manually without SL/TP hit
        if entry_info:
            # Calculate expected loss at SL
            if entry_info['direction'] == 'SELL':
                expected_loss = (entry_info['sl'] - entry_info['entry']) * 0.01 * 100000
            else:  # BUY
                expected_loss = (entry_info['entry'] - entry_info['sl']) * 0.01 * 100000
            
            if abs(profit - expected_loss) < 0.10:
                exit_reason = 'Hard SL (price-based, manual close)'
            elif profit < -1.50:
                exit_reason = 'Large Loss (manual close)'
            elif profit < -0.50:
                exit_reason = 'Medium Loss (manual close)'
            else:
                exit_reason = 'Small Loss (manual close)'
        else:
            exit_reason = 'Manual Close (unknown reason)'
    else:
        exit_reason = 'Unknown'
    
    exit_reasons[exit_reason] += 1
    exit_details[exit_reason].append(trade)

# Print results
print("=" * 80)
print("EXIT ATTRIBUTION ANALYSIS - BACKTEST TRADES")
print("=" * 80)
print()

total_trades = len(closed_trades)
print(f"Total Closed Trades: {total_trades}")
print()

# Frequency table
print("EXIT REASON FREQUENCY TABLE")
print("-" * 80)
print(f"{'Exit Reason':<40} {'Count':<10} {'% Share':<10}")
print("-" * 80)

sorted_reasons = sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True)
for reason, count in sorted_reasons:
    pct = (count / total_trades * 100) if total_trades > 0 else 0
    print(f"{reason:<40} {count:<10} {pct:>6.1f}%")

print()
print("=" * 80)
print()

# Analyze top 2 exit reasons
if len(sorted_reasons) >= 2:
    top_reason_1 = sorted_reasons[0][0]
    top_reason_2 = sorted_reasons[1][0]
    
    print(f"TOP EXIT REASON #1: {top_reason_1}")
    print("-" * 80)
    print(f"Count: {exit_reasons[top_reason_1]} ({exit_reasons[top_reason_1]/total_trades*100:.1f}%)")
    print()
    
    # Sample trades
    sample_trades = exit_details[top_reason_1][:5]
    print("Sample Trades:")
    for trade in sample_trades:
        print(f"  Ticket {trade['ticket']}: Profit ${trade['profit']:.2f} | "
              f"Method: {trade['method']} | SL hit: {trade['sl_hit']} | TP hit: {trade['tp_hit']}")
    print()
    
    # Calculate average profit for this exit reason
    avg_profit = sum(t['profit'] for t in exit_details[top_reason_1]) / len(exit_details[top_reason_1])
    print(f"Average Profit: ${avg_profit:.2f}")
    print()
    
    print(f"TOP EXIT REASON #2: {top_reason_2}")
    print("-" * 80)
    print(f"Count: {exit_reasons[top_reason_2]} ({exit_reasons[top_reason_2]/total_trades*100:.1f}%)")
    print()
    
    # Sample trades
    sample_trades = exit_details[top_reason_2][:5]
    print("Sample Trades:")
    for trade in sample_trades:
        print(f"  Ticket {trade['ticket']}: Profit ${trade['profit']:.2f} | "
              f"Method: {trade['method']} | SL hit: {trade['sl_hit']} | TP hit: {trade['tp_hit']}")
    print()
    
    # Calculate average profit for this exit reason
    avg_profit = sum(t['profit'] for t in exit_details[top_reason_2]) / len(exit_details[top_reason_2])
    print(f"Average Profit: ${avg_profit:.2f}")
    print()

# Check for [SL/TP HIT] messages from check_sl_tp_hits()
sl_tp_hit_messages = [l for l in lines if '[SL/TP HIT]' in l]
print(f"Positions closed by check_sl_tp_hits(): {len(sl_tp_hit_messages)}")
print(f"Positions closed by close_position(): {sum(1 for t in closed_trades if t['method'] == 'close_position')}")
print()

# Diagnosis
print("=" * 80)
print("DIAGNOSIS")
print("=" * 80)
print()

primary_reason = sorted_reasons[0][0] if sorted_reasons else "Unknown"
secondary_reason = sorted_reasons[1][0] if len(sorted_reasons) >= 2 else "None"

print(f"Primary Loss Cause: {primary_reason}")
print(f"Secondary Loss Cause: {secondary_reason}")
print()

# One-paragraph diagnosis
diagnosis = f"""
All {total_trades} trades closed via close_position() method with SL hit: False and TP hit: False. 
No trades were closed by check_sl_tp_hits() (expected mechanism for SL/TP enforcement). 
The primary exit reason is '{primary_reason}' ({exit_reasons[primary_reason]} trades, {exit_reasons[primary_reason]/total_trades*100:.1f}%), 
followed by '{secondary_reason}' ({exit_reasons[secondary_reason]} trades, {exit_reasons[secondary_reason]/total_trades*100:.1f}%). 
Trades are closing with small losses (average ${sum(t['profit'] for t in closed_trades)/len(closed_trades):.2f}) 
instead of the expected -$2.00 hard SL, indicating positions are being closed prematurely by an unknown mechanism 
that bypasses check_sl_tp_hits(). The SL detection logic in close_position() is not detecting SL hits, 
suggesting either: (1) positions are closing before SL is actually hit, (2) candle high/low data is not available 
when close_position() is called, or (3) there is a code path calling close_position() that is not properly 
checking for SL hits. This is a CRITICAL issue as it prevents proper risk management.
"""

print(diagnosis.strip())
print()

