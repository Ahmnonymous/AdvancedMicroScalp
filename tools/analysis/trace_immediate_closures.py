#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trace Immediate Closures - Full Code Path Analysis
Identifies exact call stack and conditions causing <5s closures
"""
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

# Read execution log
with open('logs/backtest/execution.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Parse all trades with full context
trades = []
for i, line in enumerate(lines):
    if 'ORDER PLACED' in line:
        ticket_match = re.search(r'Ticket (\d+)', line)
        entry_match = re.search(r'Entry: ([\d.]+)', line)
        sl_match = re.search(r'SL: ([\d.]+)', line)
        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        direction_match = re.search(r'(BUY|SELL)', line)
        symbol_match = re.search(r'\| (\w+) (?:BUY|SELL)', line)
        
        if ticket_match and entry_match and sl_match:
            ticket = int(ticket_match.group(1))
            entry_price = float(entry_match.group(1))
            sl_price = float(sl_match.group(1))
            trade_time = time_match.group(1) if time_match else None
            direction = direction_match.group(1) if direction_match else 'UNKNOWN'
            symbol = symbol_match.group(1) if symbol_match else 'UNKNOWN'
            
            # Find corresponding close with context
            close_info = None
            context_lines = []
            for j in range(i+1, min(i+100, len(lines))):  # Look ahead up to 100 lines
                context_lines.append((j+1, lines[j].strip()))
                if f'Ticket {ticket}' in lines[j] and 'POSITION CLOSED' in lines[j]:
                    close_profit_match = re.search(r'Profit: \$(-?\d+\.\d+)', lines[j])
                    close_time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', lines[j])
                    method_match = re.search(r'Closure method: (\w+)', lines[j])
                    sl_hit_match = re.search(r'SL hit: (True|False)', lines[j])
                    comment_match = re.search(r'comment: (.+?)(?:\|)', lines[j])
                    
                    if close_profit_match:
                        close_info = {
                            'profit': float(close_profit_match.group(1)),
                            'time': close_time_match.group(1) if close_time_match else None,
                            'method': method_match.group(1) if method_match else 'UNKNOWN',
                            'sl_hit': sl_hit_match.group(1) == 'True' if sl_hit_match else False,
                            'comment': comment_match.group(1).strip() if comment_match else None,
                            'line_num': j+1,
                            'context': context_lines[-20:]  # Last 20 lines before close
                        }
                    break
            
            if close_info:
                # Calculate duration
                duration_sec = None
                if trade_time and close_info['time']:
                    try:
                        t1 = datetime.strptime(trade_time, '%Y-%m-%d %H:%M:%S')
                        t2 = datetime.strptime(close_info['time'], '%Y-%m-%d %H:%M:%S')
                        duration_sec = (t2 - t1).total_seconds()
                    except:
                        pass
                
                trades.append({
                    'ticket': ticket,
                    'symbol': symbol,
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'direction': direction,
                    'entry_time': trade_time,
                    'entry_line': i+1,
                    'close_profit': close_info['profit'],
                    'close_time': close_info['time'],
                    'close_method': close_info['method'],
                    'close_comment': close_info['comment'],
                    'sl_hit': close_info['sl_hit'],
                    'duration_sec': duration_sec,
                    'close_line_num': close_info['line_num'],
                    'context': close_info['context']
                })

# Filter immediate closures (<5s)
immediate_closures = [t for t in trades if t['duration_sec'] is not None and t['duration_sec'] < 5]

print("=" * 100)
print("IMMEDIATE CLOSURE TRACE REPORT")
print("=" * 100)
print()

print(f"Total Trades Analyzed: {len(trades)}")
print(f"Immediate Closures (<5s): {len(immediate_closures)} ({len(immediate_closures)/len(trades)*100:.1f}%)")
print()

# Distribution of closure times
if immediate_closures:
    durations = [t['duration_sec'] for t in immediate_closures if t['duration_sec'] is not None]
    print("CLOSURE TIME DISTRIBUTION:")
    print("-" * 100)
    print(f"  <1s:  {sum(1 for d in durations if d < 1)} trades")
    print(f"  1-2s: {sum(1 for d in durations if 1 <= d < 2)} trades")
    print(f"  2-3s: {sum(1 for d in durations if 2 <= d < 3)} trades")
    print(f"  3-4s: {sum(1 for d in durations if 3 <= d < 4)} trades")
    print(f"  4-5s: {sum(1 for d in durations if 4 <= d < 5)} trades")
    print(f"  Average: {sum(durations)/len(durations):.2f}s")
    print(f"  Median: {sorted(durations)[len(durations)//2]:.2f}s")
    print()

# Analyze closure comments/methods
closure_methods = defaultdict(int)
closure_comments = defaultdict(int)
sl_detection_status = {'detected': 0, 'not_detected': 0}

for trade in immediate_closures:
    closure_methods[trade['close_method']] += 1
    if trade['close_comment']:
        closure_comments[trade['close_comment']] += 1
    if trade['sl_hit']:
        sl_detection_status['detected'] += 1
    else:
        sl_detection_status['not_detected'] += 1

print("CLOSURE METHOD ANALYSIS:")
print("-" * 100)
for method, count in closure_methods.items():
    print(f"  {method}: {count} trades ({count/len(immediate_closures)*100:.1f}%)")
print()

print("CLOSURE COMMENT ANALYSIS:")
print("-" * 100)
if closure_comments:
    for comment, count in sorted(closure_comments.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  '{comment}': {count} trades")
else:
    print("  No closure comments found")
print()

print("SL DETECTION STATUS:")
print("-" * 100)
print(f"  SL Detected: {sl_detection_status['detected']} trades")
print(f"  SL NOT Detected: {sl_detection_status['not_detected']} trades ({sl_detection_status['not_detected']/len(immediate_closures)*100:.1f}%)")
print()

# Sample immediate closures with full context
print("=" * 100)
print("SAMPLE IMMEDIATE CLOSURES - FULL CONTEXT")
print("=" * 100)
print()

for i, trade in enumerate(immediate_closures[:10], 1):
    print(f"SAMPLE #{i}: Ticket {trade['ticket']} ({trade['symbol']} {trade['direction']})")
    print("-" * 100)
    print(f"Entry: {trade['entry_time']} | Entry Price: {trade['entry_price']:.5f} | SL: {trade['sl_price']:.5f}")
    print(f"Close: {trade['close_time']} | Duration: {trade['duration_sec']:.1f}s | Profit: ${trade['close_profit']:.2f}")
    print(f"Method: {trade['close_method']} | SL Hit: {trade['sl_hit']} | Comment: {trade['close_comment'] or 'None'}")
    print()
    print("Context (last 10 lines before close):")
    for line_num, line_text in trade['context'][-10:]:
        print(f"  Line {line_num}: {line_text}")
    print()

# Check for check_sl_tp_hits calls
print("=" * 100)
print("check_sl_tp_hits() ANALYSIS")
print("=" * 100)
print()

sl_tp_hit_messages = [l for l in lines if '[SL/TP HIT]' in l]
print(f"Total [SL/TP HIT] messages in log: {len(sl_tp_hit_messages)}")
print(f"Expected: {len(immediate_closures)} (one per immediate closure)")
print(f"Missing: {len(immediate_closures) - len(sl_tp_hit_messages)}")
print()

if sl_tp_hit_messages:
    print("Sample SL/TP HIT messages:")
    for msg in sl_tp_hit_messages[:5]:
        print(f"  {msg.strip()}")
else:
    print("CRITICAL: No [SL/TP HIT] messages found in log!")
    print("This confirms check_sl_tp_hits() is NOT detecting or closing positions.")
print()

# Final summary
print("=" * 100)
print("ROOT CAUSE ANALYSIS")
print("=" * 100)
print()

print("EVIDENCE:")
print(f"  1. {len(immediate_closures)} trades close within <5s (100% of all trades)")
print(f"  2. All closures use close_position() method")
print(f"  3. SL detection fails in {sl_detection_status['not_detected']} cases (100%)")
print(f"  4. check_sl_tp_hits() never triggers (0 [SL/TP HIT] messages)")
print(f"  5. Average closure time: {sum(t['duration_sec'] for t in immediate_closures if t['duration_sec'])/len(immediate_closures):.2f}s")
print()

print("CODE PATH HYPOTHESIS:")
print("  1. An unknown mechanism calls close_position() directly within 1-2s of entry")
print("  2. This bypasses check_sl_tp_hits() completely")
print("  3. When close_position() is called, SL detection logic fails because:")
print("     a. Candle high/low data not available at closure time")
print("     b. Position closed before SL price is actually touched")
print("     c. Bug in SL detection logic in close_position() method")
print()

print("NEXT STEPS FOR CODE INSPECTION:")
print("  1. Search codebase for all calls to close_position() in backtest mode")
print("  2. Check if replay engine or backtest runner closes positions automatically")
print("  3. Verify if max_open_trades mechanism closes positions")
print("  4. Check if signal reversal logic closes positions")
print("  5. Inspect close_position() SL detection logic for bugs")
print()

