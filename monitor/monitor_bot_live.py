#!/usr/bin/env python3
"""
Live bot monitoring - watches for trades and reports performance
"""

import os
import sys
import time
import re
from datetime import datetime
from collections import defaultdict

# Add parent directory to path to access root config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def monitor_bot_live(log_file='bot_log.txt', check_interval=10):
    """Monitor bot activity in real-time."""
    
    # Get root directory path
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_path = os.path.join(root_dir, log_file)
    
    print("=" * 80)
    print("🤖 LIVE BOT MONITORING")
    print("=" * 80)
    print(f"Monitoring: {log_path}")
    print(f"Check interval: {check_interval} seconds")
    print("Press Ctrl+C to stop")
    print("=" * 80)
    print()
    
    # Track state
    last_position = 0
    trades_opened = []
    trades_closed = []
    opportunities_count = 0
    attempts_count = 0
    skip_reasons = defaultdict(int)
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            # Go to end of file
            f.seek(0, 2)
            
            cycle_count = 0
            
            while True:
                line = f.readline()
                
                if line:
                    # Track opportunities
                    if "OPPORTUNITY FOUND" in line:
                        opportunities_count += 1
                        cycle_count += 1
                        symbol_match = re.search(r'✅ (\w+): OPPORTUNITY', line)
                        if symbol_match:
                            symbol = symbol_match.group(1)
                            print(f"🟢 [{datetime.now().strftime('%H:%M:%S')}] Opportunity: {symbol}")
                    
                    # Track trade attempts
                    if "RANDOMNESS PASS" in line:
                        attempts_count += 1
                        symbol_match = re.search(r'(\w+): RANDOMNESS PASS', line)
                        if symbol_match:
                            symbol = symbol_match.group(1)
                            print(f"🎯 [{datetime.now().strftime('%H:%M:%S')}] Attempting trade: {symbol}")
                    
                    if "RANDOMNESS SKIP" in line:
                        skip_reasons["Randomness"] += 1
                        symbol_match = re.search(r'(\w+): RANDOMNESS SKIP', line)
                        if symbol_match:
                            symbol = symbol_match.group(1)
                            print(f"⏭️  [{datetime.now().strftime('%H:%M:%S')}] Skipped: {symbol} (randomness)")
                    
                    # Track successful trades
                    if "TRADE EXECUTED SUCCESSFULLY" in line:
                        # Extract trade info from next lines
                        ticket_match = re.search(r'Ticket: (\d+)', line)
                        symbol_match = re.search(r'Symbol: (\w+)', line)
                        direction_match = re.search(r'Direction: (\w+)', line)
                        
                        if ticket_match:
                            ticket = ticket_match.group(1)
                            symbol = symbol_match.group(1) if symbol_match else "Unknown"
                            direction = direction_match.group(1) if direction_match else "Unknown"
                            
                            trades_opened.append({
                                'ticket': ticket,
                                'symbol': symbol,
                                'direction': direction,
                                'time': datetime.now()
                            })
                            
                            print(f"\n{'='*80}")
                            print(f"✅ TRADE OPENED!")
                            print(f"   Ticket: {ticket}")
                            print(f"   Symbol: {symbol}")
                            print(f"   Direction: {direction}")
                            print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                            print(f"{'='*80}\n")
                    
                    # Track failed trades
                    if "TRADE EXECUTION FAILED" in line:
                        print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Trade execution failed")
                    
                    # Track market closed
                    if "Market closed" in line:
                        skip_reasons["Market Closed"] += 1
                        print(f"⏰ [{datetime.now().strftime('%H:%M:%S')}] Market is CLOSED - waiting...")
                    
                    # Track other skip reasons
                    if "NEWS BLOCKING" in line:
                        skip_reasons["News"] += 1
                    if "SPREAD TOO HIGH" in line:
                        skip_reasons["Spread"] += 1
                    if "CANNOT OPEN TRADE" in line:
                        skip_reasons["Max Positions"] += 1
                    
                    # Track trailing stop updates
                    if "TRAILING STOP" in line:
                        print(f"📈 [{datetime.now().strftime('%H:%M:%S')}] Trailing stop updated")
                
                else:
                    # No new lines, wait a bit
                    time.sleep(check_interval)
                    
                    # Print periodic summary
                    if cycle_count > 0 and cycle_count % 5 == 0:
                        print(f"\n📊 Summary (Cycle {cycle_count}):")
                        print(f"   Opportunities: {opportunities_count}")
                        print(f"   Attempts: {attempts_count}")
                        print(f"   Trades Opened: {len(trades_opened)}")
                        print(f"   Trades Closed: {len(trades_closed)}")
                        if skip_reasons:
                            print(f"   Skips: {dict(skip_reasons)}")
                        print()
                
    except KeyboardInterrupt:
        print("\n\n" + "="*80)
        print("📊 FINAL SUMMARY")
        print("="*80)
        print(f"Opportunities Found: {opportunities_count}")
        print(f"Trade Attempts: {attempts_count}")
        print(f"Trades Opened: {len(trades_opened)}")
        print(f"Trades Closed: {len(trades_closed)}")
        if skip_reasons:
            print(f"\nSkip Reasons: {dict(skip_reasons)}")
        print("="*80)

if __name__ == '__main__':
    monitor_bot_live()

