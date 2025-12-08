#!/usr/bin/env python3
"""
Real-time Bot Monitoring Script
Monitors bot activity, trades, and trailing stop adjustments.
"""

import time
import re
from datetime import datetime
import MetaTrader5 as mt5

def get_open_positions():
    """Get all open positions from MT5."""
    if not mt5.initialize():
        return []
    positions = mt5.positions_get()
    if positions is None:
        mt5.shutdown()
        return []
    result = []
    for pos in positions:
        result.append({
            'ticket': pos.ticket,
            'symbol': pos.symbol,
            'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
            'profit': pos.profit,
            'sl': pos.sl,
            'price_current': pos.price_current,
            'price_open': pos.price_open
        })
    mt5.shutdown()
    return result

def monitor_bot():
    """Monitor bot activity in real-time."""
    print("=" * 80)
    print("🤖 TRADING BOT REAL-TIME MONITOR")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Monitoring interval: Every 5 seconds")
    print("Trailing stop interval: 3.0 seconds (as configured)")
    print("=" * 80)
    print()
    
    last_log_position = 0
    check_count = 0
    
    try:
        while True:
            check_count += 1
            timestamp = datetime.now().strftime('%H:%M:%S')
            
            # Check open positions
            positions = get_open_positions()
            
            # Read new log entries
            try:
                with open('bot_log.txt', 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    new_lines = lines[last_log_position:]
                    last_log_position = len(lines)
                    
                    # Check for important events
                    trailing_stops = []
                    new_trades = []
                    big_jumps = []
                    
                    for line in new_lines:
                        if "TRAILING STOP:" in line:
                            trailing_stops.append(line.strip())
                        if "Trade executed:" in line and "Ticket" in line:
                            new_trades.append(line.strip())
                        if "BIG JUMP:" in line:
                            big_jumps.append(line.strip())
                    
                    # Display status
                    print(f"\n[{timestamp}] Check #{check_count}")
                    print("-" * 80)
                    
                    # Open positions
                    if positions:
                        print(f"📊 Open Positions: {len(positions)}")
                        for pos in positions:
                            pnl_color = "🟢" if pos['profit'] >= 0 else "🔴"
                            print(f"  {pnl_color} Ticket {pos['ticket']}: {pos['symbol']} {pos['type']} | "
                                  f"P/L: ${pos['profit']:.2f} | SL: {pos['sl']:.5f}")
                    else:
                        print("📊 Open Positions: 0")
                    
                    # New trades
                    if new_trades:
                        print(f"\n✅ New Trades ({len(new_trades)}):")
                        for trade in new_trades[-3:]:  # Show last 3
                            match = re.search(r'Ticket (\d+).*?(\w+)\s+(LONG|SHORT)', trade)
                            if match:
                                print(f"  🆕 Ticket {match.group(1)}: {match.group(2)} {match.group(3)}")
                    
                    # Trailing stop adjustments
                    if trailing_stops:
                        print(f"\n📈 Trailing Stop Adjustments ({len(trailing_stops)}):")
                        for adj in trailing_stops[-3:]:  # Show last 3
                            match = re.search(r'Ticket (\d+).*?Profit: \$([\d.]+).*?SL Profit: \$([\d.]+)', adj)
                            if match:
                                print(f"  📊 Ticket {match.group(1)}: Profit ${match.group(2)} → SL ${match.group(3)}")
                    
                    # Big jumps
                    if big_jumps:
                        print(f"\n🚀 Big Jumps Detected ({len(big_jumps)}):")
                        for jump in big_jumps[-2:]:  # Show last 2
                            match = re.search(r'Ticket (\d+)', jump)
                            if match:
                                print(f"  ⚡ Ticket {match.group(1)}: Big profit jump detected!")
                    
                    if not trailing_stops and not new_trades and not big_jumps and not positions:
                        print("  ⏳ Waiting for activity...")
                    
                    print("-" * 80)
            
            except FileNotFoundError:
                print(f"[{timestamp}] ⚠️  Log file not found, waiting...")
            except Exception as e:
                print(f"[{timestamp}] ⚠️  Error: {e}")
            
            time.sleep(5)  # Check every 5 seconds
    
    except KeyboardInterrupt:
        print("\n" + "=" * 80)
        print("Monitoring stopped by user")
        print("=" * 80)

if __name__ == '__main__':
    monitor_bot()

