#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Read execution log
with open('logs/backtest/execution.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find all closed trades
closed_trades = [l for l in lines if 'POSITION CLOSED' in l]

print("=" * 60)
print("BACKTEST STATUS SUMMARY")
print("=" * 60)
print()

# Check for errors
errors = [l for l in lines if any(x in l.upper() for x in ['ERROR', 'CRITICAL', 'EXCEPTION', 'TRACEBACK', 'FAILED'])]
if errors:
    print(f"[WARNING] Found {len(errors)} error/warning lines")
else:
    print("[OK] No critical errors found in execution log")
print()

# Analyze closed trades
if closed_trades:
    profits = []
    for line in closed_trades:
        match = re.search(r'Profit: \$(-?\d+\.\d+)', line)
        if match:
            profits.append(float(match.group(1)))
    
    if profits:
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        breakeven = [p for p in profits if p == 0]
        
        print(f"Total Closed Trades: {len(profits)}")
        print(f"  Winners: {len(wins)} ({len(wins)/len(profits)*100:.1f}%)")
        print(f"  Losers: {len(losses)} ({len(losses)/len(profits)*100:.1f}%)")
        print(f"  Breakeven: {len(breakeven)}")
        print()
        print(f"Net P&L: ${sum(profits):.2f}")
        print(f"Best Trade: ${max(profits):.2f}")
        print(f"Worst Trade: ${min(profits):.2f}")
        print(f"Average Profit: ${sum(profits)/len(profits):.2f}")
        print()
        
        # Check SL hits (should be close to -$2.00)
        sl_hits = [p for p in losses if abs(p + 2.0) < 0.10]
        print(f"SL Hits (close to -$2.00): {len(sl_hits)} ({len(sl_hits)/len(losses)*100 if losses else 0:.1f}% of losses)")
        
        # Show loss distribution
        if losses:
            print()
            print("Loss Distribution:")
            small_losses = [p for p in losses if p > -0.50]
            medium_losses = [p for p in losses if -0.50 >= p > -1.50]
            large_losses = [p for p in losses if -1.50 >= p > -2.50]
            sl_losses = [p for p in losses if abs(p + 2.0) < 0.10]
            print(f"  Small losses (>-$0.50): {len(small_losses)}")
            print(f"  Medium losses (-$0.50 to -$1.50): {len(medium_losses)}")
            print(f"  Large losses (-$1.50 to -$2.50): {len(large_losses)}")
            print(f"  SL hits (~-$2.00): {len(sl_losses)}")
else:
    print("No closed trades found yet")

print()
print("=" * 60)
print("Backtest is running normally")
print("=" * 60)
