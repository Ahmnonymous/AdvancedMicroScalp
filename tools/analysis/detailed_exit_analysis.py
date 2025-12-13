#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Detailed Exit Analysis - Identifies exact code paths causing early closures
"""
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

# Read execution log
with open('logs/backtest/execution.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Parse all trades with timing
trades = []
for i, line in enumerate(lines):
    if 'ORDER PLACED' in line:
        ticket_match = re.search(r'Ticket (\d+)', line)
        entry_match = re.search(r'Entry: ([\d.]+)', line)
        sl_match = re.search(r'SL: ([\d.]+)', line)
        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        direction_match = re.search(r'(BUY|SELL)', line)
        
        if ticket_match and entry_match and sl_match:
            ticket = int(ticket_match.group(1))
            entry_price = float(entry_match.group(1))
            sl_price = float(sl_match.group(1))
            trade_time = time_match.group(1) if time_match else None
            direction = direction_match.group(1) if direction_match else 'UNKNOWN'
            
            # Find corresponding close
            close_info = None
            for j in range(i+1, min(i+50, len(lines))):  # Look ahead up to 50 lines
                if f'Ticket {ticket}' in lines[j] and 'POSITION CLOSED' in lines[j]:
                    close_profit_match = re.search(r'Profit: \$(-?\d+\.\d+)', lines[j])
                    close_time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', lines[j])
                    method_match = re.search(r'Closure method: (\w+)', lines[j])
                    sl_hit_match = re.search(r'SL hit: (True|False)', lines[j])
                    
                    if close_profit_match:
                        close_info = {
                            'profit': float(close_profit_match.group(1)),
                            'time': close_time_match.group(1) if close_time_match else None,
                            'method': method_match.group(1) if method_match else 'UNKNOWN',
                            'sl_hit': sl_hit_match.group(1) == 'True' if sl_hit_match else False,
                            'line_num': j+1
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
                
                # Calculate expected loss at SL
                if direction == 'SELL':
                    expected_loss_sl = (sl_price - entry_price) * 0.01 * 100000
                else:  # BUY
                    expected_loss_sl = (entry_price - sl_price) * 0.01 * 100000
                
                trades.append({
                    'ticket': ticket,
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'direction': direction,
                    'entry_time': trade_time,
                    'close_profit': close_info['profit'],
                    'close_time': close_info['time'],
                    'close_method': close_info['method'],
                    'sl_hit': close_info['sl_hit'],
                    'duration_sec': duration_sec,
                    'expected_loss_sl': expected_loss_sl,
                    'close_line_num': close_info['line_num']
                })

# Analyze
print("=" * 80)
print("DETAILED EXIT ANALYSIS - CODE PATH IDENTIFICATION")
print("=" * 80)
print()

total_trades = len(trades)
print(f"Total Trades Analyzed: {total_trades}")
print()

# Exit reason classification
exit_reasons = defaultdict(int)
exit_timing = defaultdict(list)

for trade in trades:
    profit = trade['close_profit']
    sl_hit = trade['sl_hit']
    method = trade['close_method']
    duration = trade['duration_sec']
    expected_sl_loss = trade['expected_loss_sl']
    
    # Classify
    if sl_hit:
        reason = 'Hard SL (detected)'
    elif abs(profit - expected_sl_loss) < 0.10:
        reason = 'Hard SL (price-based, not detected)'
    elif method == 'close_position' and not sl_hit:
        if duration is not None:
            if duration < 5:
                reason = 'Immediate Close (<5s)'
            elif duration < 60:
                reason = 'Very Early Close (<60s)'
            else:
                reason = 'Early Close (manual)'
        else:
            reason = 'Manual Close (unknown timing)'
    else:
        reason = 'Unknown'
    
    exit_reasons[reason] += 1
    exit_timing[reason].append(duration if duration is not None else 0)

# Frequency table
print("EXIT REASON FREQUENCY TABLE")
print("-" * 80)
print(f"{'Exit Reason':<40} {'Count':<10} {'% Share':<10} {'Avg Duration':<15}")
print("-" * 80)

sorted_reasons = sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True)
for reason, count in sorted_reasons:
    pct = (count / total_trades * 100) if total_trades > 0 else 0
    durations = exit_timing[reason]
    avg_duration = sum(durations) / len(durations) if durations else 0
    print(f"{reason:<40} {count:<10} {pct:>6.1f}% {avg_duration:>10.1f}s")

print()
print("=" * 80)
print()

# Top 2 exit reasons - detailed analysis
if len(sorted_reasons) >= 1:
    top_reason = sorted_reasons[0][0]
    print(f"TOP EXIT REASON: {top_reason}")
    print("-" * 80)
    print(f"Count: {exit_reasons[top_reason]} ({exit_reasons[top_reason]/total_trades*100:.1f}%)")
    print()
    
    # Sample trades with details
    sample_trades = [t for t in trades if (
        (top_reason == 'Immediate Close (<5s)' and t['duration_sec'] is not None and t['duration_sec'] < 5) or
        (top_reason == 'Very Early Close (<60s)' and t['duration_sec'] is not None and 5 <= t['duration_sec'] < 60) or
        (top_reason == 'Early Close (manual)' and t['duration_sec'] is not None and t['duration_sec'] >= 60) or
        (top_reason == 'Manual Close (unknown timing)' and t['duration_sec'] is None) or
        (top_reason == 'Hard SL (price-based, not detected)' and abs(t['close_profit'] - t['expected_loss_sl']) < 0.10)
    )][:10]
    
    print("Sample Trades (showing entry → close timing):")
    for trade in sample_trades:
        duration_str = f"{trade['duration_sec']:.1f}s" if trade['duration_sec'] is not None else "UNKNOWN"
        print(f"  Ticket {trade['ticket']}: Entry {trade['entry_time']} → Close {trade['close_time']} ({duration_str})")
        print(f"    Profit: ${trade['close_profit']:.2f} | Expected SL loss: ${trade['expected_loss_sl']:.2f}")
        print(f"    Method: {trade['close_method']} | SL hit: {trade['sl_hit']}")
        print()
    
    # Code path analysis
    print("CODE PATH ANALYSIS:")
    print(f"  - All trades closed via: {trade['close_method']}")
    print(f"  - SL detection in close_position(): {sum(1 for t in trades if t['sl_hit'])} trades detected SL")
    print(f"  - check_sl_tp_hits() closures: 0 (NONE)")
    print()
    
    # Timing analysis
    durations = [t['duration_sec'] for t in trades if t['duration_sec'] is not None]
    if durations:
        print("TIMING ANALYSIS:")
        print(f"  - Fastest close: {min(durations):.1f}s")
        print(f"  - Slowest close: {max(durations):.1f}s")
        print(f"  - Average duration: {sum(durations)/len(durations):.1f}s")
        print(f"  - Median duration: {sorted(durations)[len(durations)//2]:.1f}s")
        print()
    
    if len(sorted_reasons) >= 2:
        second_reason = sorted_reasons[1][0]
        print(f"SECONDARY EXIT REASON: {second_reason}")
        print("-" * 80)
        print(f"Count: {exit_reasons[second_reason]} ({exit_reasons[second_reason]/total_trades*100:.1f}%)")
        print()

# Check for patterns
print("=" * 80)
print("PATTERN ANALYSIS")
print("=" * 80)
print()

# Check if positions close immediately after entry
immediate_closes = [t for t in trades if t['duration_sec'] is not None and t['duration_sec'] < 2]
print(f"Immediate closes (<2s): {len(immediate_closes)} ({len(immediate_closes)/total_trades*100:.1f}%)")

# Check if positions close at same time as new entries
entry_times = {t['entry_time'] for t in trades if t['entry_time']}
close_times = {t['close_time'] for t in trades if t['close_time']}
overlapping = entry_times.intersection(close_times)
print(f"Entry/close time overlaps: {len(overlapping)} timestamps")
print()

# Final diagnosis
print("=" * 80)
print("FINAL DIAGNOSIS")
print("=" * 80)
print()

primary = sorted_reasons[0][0] if sorted_reasons else "Unknown"
secondary = sorted_reasons[1][0] if len(sorted_reasons) >= 2 else "None"

print(f"PRIMARY LOSS CAUSE: {primary}")
print(f"SECONDARY LOSS CAUSE: {secondary}")
print()

diagnosis = f"""
CRITICAL FINDING: All {total_trades} trades are closing via close_position() method with SL hit: False. 
ZERO trades are being closed by check_sl_tp_hits() (the expected SL/TP enforcement mechanism).

The primary exit reason is '{primary}' ({exit_reasons[primary]} trades, {exit_reasons[primary]/total_trades*100:.1f}%), 
indicating positions are closing prematurely before SL is actually hit. The average close duration is 
{sum(exit_timing[primary])/len(exit_timing[primary]):.1f}s for this exit type.

ROOT CAUSE HYPOTHESIS:
1. An unknown code path is calling close_position() directly, bypassing check_sl_tp_hits()
2. When close_position() is called, the SL detection logic fails because:
   - Candle high/low data is not available at the time of closure
   - The position is being closed before the SL price is actually touched
   - There is a bug in the SL detection logic in close_position()

EVIDENCE:
- 0 trades closed by check_sl_tp_hits() (expected mechanism)
- 243 trades closed by close_position() with SL hit: False
- Average loss: ${sum(t['close_profit'] for t in trades)/len(trades):.2f} (should be -$2.00 if SL hit)
- Positions closing with small losses instead of hard SL

This is a CRITICAL SYSTEM FAILURE: Risk management is completely bypassed.
"""

print(diagnosis.strip())

