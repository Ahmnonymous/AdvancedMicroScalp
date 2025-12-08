#!/usr/bin/env python3
"""
Analyze bot performance from logs
"""

import os
import sys
import re
from datetime import datetime
from collections import defaultdict

# Add parent directory to path to access root config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def analyze_bot_logs(log_file='bot_log.txt'):
    """Analyze bot logs to find issues and performance metrics."""
    
    # Get root directory path
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_path = os.path.join(root_dir, log_file)
    
    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    
    # Track metrics
    opportunities_found = 0
    trades_attempted = 0
    trades_executed = 0
    trades_failed = 0
    skip_reasons = defaultdict(int)
    errors = []
    spread_issues = []
    
    print("=" * 80)
    print("BOT PERFORMANCE ANALYSIS")
    print("=" * 80)
    print()
    
    for i, line in enumerate(lines[-500:], 1):  # Last 500 lines
        # Track opportunities
        if "OPPORTUNITY FOUND" in line:
            opportunities_found += 1
            # Check for high spread
            spread_match = re.search(r'Spread=(\d+\.?\d*)pts', line)
            if spread_match:
                spread = float(spread_match.group(1))
                if spread > 10000:
                    spread_issues.append((line.strip(), spread))
        
        # Track trade attempts
        if "RANDOMNESS PASS" in line:
            trades_attempted += 1
        
        if "RANDOMNESS SKIP" in line:
            skip_reasons["Randomness"] += 1
        
        # Track executions
        if "TRADE EXECUTED SUCCESSFULLY" in line:
            trades_executed += 1
        
        if "TRADE EXECUTION FAILED" in line:
            trades_failed += 1
            # Find reason
            if i < len(lines):
                next_lines = lines[i:min(i+5, len(lines))]
                for next_line in next_lines:
                    if "Market closed" in next_line:
                        skip_reasons["Market Closed"] += 1
                        break
                    elif "Order failed" in next_line:
                        error_match = re.search(r'Order failed: (\d+) - (.+)', next_line)
                        if error_match:
                            skip_reasons[f"Error {error_match.group(1)}"] += 1
                        break
        
        # Track other skip reasons
        if "SKIPPED" in line or "BLOCKING" in line:
            if "NEWS BLOCKING" in line:
                skip_reasons["News"] += 1
            elif "SPREAD TOO HIGH" in line:
                skip_reasons["Spread"] += 1
            elif "CANNOT OPEN TRADE" in line:
                skip_reasons["Max Positions"] += 1
        
        # Track errors
        if "ERROR" in line and "Error in" in line:
            errors.append(line.strip())
    
    print(f"📊 METRICS (from last 500 log lines):")
    print(f"   Opportunities Found: {opportunities_found}")
    print(f"   Trade Attempts: {trades_attempted}")
    print(f"   Trades Executed: {trades_executed}")
    print(f"   Trades Failed: {trades_failed}")
    print()
    
    if skip_reasons:
        print(f"⏭️  SKIP REASONS:")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"   {reason}: {count}")
        print()
    
    if spread_issues:
        print(f"⚠️  SPREAD CALCULATION ISSUES ({len(spread_issues)} found):")
        for issue, spread in spread_issues[:5]:  # Show first 5
            print(f"   Spread: {spread:.0f} points - {issue[:80]}")
        print()
    
    if trades_executed > 0:
        success_rate = (trades_executed / trades_attempted * 100) if trades_attempted > 0 else 0
        print(f"✅ SUCCESS RATE: {success_rate:.1f}% ({trades_executed}/{trades_attempted})")
    else:
        print(f"❌ NO TRADES EXECUTED - Reasons:")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"   - {reason}: {count} times")
    
    print()
    print("=" * 80)

if __name__ == '__main__':
    analyze_bot_logs()

