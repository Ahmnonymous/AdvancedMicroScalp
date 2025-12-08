#!/usr/bin/env python3
"""
Trade Monitoring Script
Monitors all open trades and tracks SL adjustments until trades close.
"""

import time
import re
from datetime import datetime
from collections import defaultdict
import MetaTrader5 as mt5

# Trade tracking
trades = {}  # {ticket: {'symbol': str, 'entry_time': datetime, 'entry_price': float, 'sl_adjustments': [], 'status': str}}
sl_adjustments = []  # List of all SL adjustments

def parse_log_line(line):
    """Parse log line for trade-related information."""
    # Parse trailing stop adjustments
    if "TRAILING STOP:" in line:
        match = re.search(r'Ticket (\d+)', line)
        if match:
            ticket = int(match.group(1))
            # Extract profit, SL profit, reason
            profit_match = re.search(r'Profit: \$([\d.]+)', line)
            sl_profit_match = re.search(r'SL Profit: \$([\d.]+)', line)
            sl_price_match = re.search(r'SL Price: ([\d.]+)', line)
            reason_match = re.search(r'Reason: (.+?)(?:\s*$|\s*\||\s*$)', line)
            
            if profit_match and sl_profit_match:
                return {
                    'type': 'sl_adjustment',
                    'ticket': ticket,
                    'profit': float(profit_match.group(1)),
                    'sl_profit': float(sl_profit_match.group(1)),
                    'sl_price': float(sl_price_match.group(1)) if sl_price_match else None,
                    'reason': reason_match.group(1) if reason_match else 'Unknown',
                    'timestamp': datetime.now()
                }
    
    # Parse big jump
    if "BIG JUMP:" in line:
        match = re.search(r'Ticket (\d+)', line)
        if match:
            ticket = int(match.group(1))
            return {
                'type': 'big_jump',
                'ticket': ticket,
                'timestamp': datetime.now()
            }
    
    # Parse trade execution
    if "Trade executed:" in line:
        match = re.search(r'Ticket (\d+)', line)
        symbol_match = re.search(r'(\w+)\s+(LONG|SHORT)', line)
        if match and symbol_match:
            return {
                'type': 'trade_opened',
                'ticket': int(match.group(1)),
                'symbol': symbol_match.group(1),
                'direction': symbol_match.group(2),
                'timestamp': datetime.now()
            }
    
    # Parse position closed (if logged)
    if "Position" in line and "closed" in line.lower():
        match = re.search(r'(\d+)', line)
        if match:
            return {
                'type': 'trade_closed',
                'ticket': int(match.group(1)),
                'timestamp': datetime.now()
            }
    
    return None

def get_open_positions():
    """Get all open positions from MT5."""
    if not mt5.initialize():
        return []
    
    positions = mt5.positions_get()
    if positions is None:
        return []
    
    result = []
    for pos in positions:
        result.append({
            'ticket': pos.ticket,
            'symbol': pos.symbol,
            'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
            'volume': pos.volume,
            'price_open': pos.price_open,
            'price_current': pos.price_current,
            'sl': pos.sl,
            'tp': pos.tp,
            'profit': pos.profit,
            'time': datetime.fromtimestamp(pos.time)
        })
    
    return result

def monitor_trades():
    """Monitor trades continuously."""
    print("=" * 80)
    print("TRADE MONITORING STARTED")
    print("=" * 80)
    print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Monitoring bot_log.txt for trade events...")
    print("=" * 80)
    print()
    
    # Track last log position
    last_position = 0
    
    # Initialize MT5 connection
    if not mt5.initialize():
        print("⚠️  Warning: Could not initialize MT5. Will only monitor logs.")
    else:
        print("✅ MT5 connection established for position checking")
    
    print()
    
    try:
        while True:
            # Read new log lines
            try:
                with open('bot_log.txt', 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    new_lines = lines[last_position:]
                    last_position = len(lines)
                    
                    # Parse new lines
                    for line in new_lines:
                        event = parse_log_line(line)
                        if event:
                            if event['type'] == 'trade_opened':
                                ticket = event['ticket']
                                if ticket not in trades:
                                    trades[ticket] = {
                                        'symbol': event['symbol'],
                                        'direction': event['direction'],
                                        'entry_time': event['timestamp'],
                                        'sl_adjustments': [],
                                        'status': 'open',
                                        'big_jumps': 0
                                    }
                                    print(f"📊 NEW TRADE OPENED: Ticket {ticket} | {event['symbol']} {event['direction']} | Time: {event['timestamp'].strftime('%H:%M:%S')}")
                            
                            elif event['type'] == 'sl_adjustment':
                                ticket = event['ticket']
                                if ticket in trades:
                                    trades[ticket]['sl_adjustments'].append({
                                        'time': event['timestamp'],
                                        'profit': event['profit'],
                                        'sl_profit': event['sl_profit'],
                                        'sl_price': event['sl_price'],
                                        'reason': event['reason']
                                    })
                                    print(f"📈 SL ADJUSTMENT: Ticket {ticket} | Profit: ${event['profit']:.2f} | SL Profit: ${event['sl_profit']:.2f} | Reason: {event['reason']}")
                            
                            elif event['type'] == 'big_jump':
                                ticket = event['ticket']
                                if ticket in trades:
                                    trades[ticket]['big_jumps'] += 1
                            
                            elif event['type'] == 'trade_closed':
                                ticket = event['ticket']
                                if ticket in trades:
                                    trades[ticket]['status'] = 'closed'
                                    trades[ticket]['close_time'] = event['timestamp']
                                    print(f"🔴 TRADE CLOSED: Ticket {ticket}")
            
            except FileNotFoundError:
                print("⚠️  Log file not found, waiting...")
                time.sleep(1)
                continue
            except Exception as e:
                print(f"⚠️  Error reading log: {e}")
                time.sleep(1)
                continue
            
            # Check MT5 for current positions
            try:
                positions = get_open_positions()
                current_tickets = {pos['ticket'] for pos in positions}
                
                # Update trade info
                for pos in positions:
                    ticket = pos['ticket']
                    if ticket not in trades:
                        # New trade not yet logged
                        trades[ticket] = {
                            'symbol': pos['symbol'],
                            'direction': pos['type'],
                            'entry_time': pos['time'],
                            'entry_price': pos['price_open'],
                            'sl_adjustments': [],
                            'status': 'open',
                            'big_jumps': 0
                        }
                        print(f"📊 FOUND OPEN TRADE: Ticket {ticket} | {pos['symbol']} {pos['type']} | Entry: {pos['price_open']:.5f} | Current P/L: ${pos['profit']:.2f}")
                    
                    # Update current status
                    trades[ticket]['current_price'] = pos['price_current']
                    trades[ticket]['current_profit'] = pos['profit']
                    trades[ticket]['current_sl'] = pos['sl']
                
                # Check for closed trades
                for ticket in list(trades.keys()):
                    if trades[ticket]['status'] == 'open' and ticket not in current_tickets:
                        trades[ticket]['status'] = 'closed'
                        trades[ticket]['close_time'] = datetime.now()
                        print(f"🔴 TRADE CLOSED: Ticket {ticket} | {trades[ticket]['symbol']}")
                
                # Display current status every 10 seconds
                if int(time.time()) % 10 == 0:
                    print_status_summary(positions)
            
            except Exception as e:
                print(f"⚠️  Error checking MT5 positions: {e}")
            
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n" + "=" * 80)
        print("MONITORING STOPPED BY USER")
        print("=" * 80)
        print_final_summary()

def print_status_summary(positions):
    """Print current status summary."""
    if not positions:
        return
    
    print("\n" + "-" * 80)
    print(f"📊 CURRENT STATUS ({datetime.now().strftime('%H:%M:%S')})")
    print("-" * 80)
    for pos in positions:
        ticket = pos['ticket']
        if ticket in trades:
            trade = trades[ticket]
            duration = datetime.now() - trade['entry_time']
            print(f"Ticket {ticket} | {pos['symbol']} {pos['type']} | "
                  f"P/L: ${pos['profit']:.2f} | "
                  f"SL: {pos['sl']:.5f} | "
                  f"Duration: {duration} | "
                  f"SL Adjustments: {len(trade['sl_adjustments'])}")
    print("-" * 80)

def print_final_summary():
    """Print final trade summary."""
    print("\n" + "=" * 80)
    print("FINAL TRADE SUMMARY")
    print("=" * 80)
    
    if not trades:
        print("No trades tracked.")
        return
    
    total_profit = 0.0
    total_trades = len(trades)
    closed_trades = sum(1 for t in trades.values() if t['status'] == 'closed')
    total_sl_adjustments = sum(len(t['sl_adjustments']) for t in trades.values())
    total_big_jumps = sum(t['big_jumps'] for t in trades.values())
    
    print(f"\nTotal Trades Tracked: {total_trades}")
    print(f"Closed Trades: {closed_trades}")
    print(f"Open Trades: {total_trades - closed_trades}")
    print(f"Total SL Adjustments: {total_sl_adjustments}")
    print(f"Total Big Jumps Detected: {total_big_jumps}")
    print()
    
    for ticket, trade in trades.items():
        print(f"\n{'=' * 80}")
        print(f"TRADE: Ticket {ticket} | {trade['symbol']} {trade['direction']}")
        print(f"{'=' * 80}")
        print(f"Status: {trade['status'].upper()}")
        print(f"Entry Time: {trade['entry_time'].strftime('%Y-%m-%d %H:%M:%S')}")
        if 'entry_price' in trade:
            print(f"Entry Price: {trade['entry_price']:.5f}")
        if 'current_profit' in trade:
            print(f"Current P/L: ${trade['current_profit']:.2f}")
            total_profit += trade['current_profit']
        if trade['status'] == 'closed' and 'close_time' in trade:
            duration = trade['close_time'] - trade['entry_time']
            print(f"Close Time: {trade['close_time'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Duration: {duration}")
        print(f"SL Adjustments: {len(trade['sl_adjustments'])}")
        print(f"Big Jumps: {trade['big_jumps']}")
        
        if trade['sl_adjustments']:
            print("\nSL Adjustment History:")
            for i, adj in enumerate(trade['sl_adjustments'], 1):
                print(f"  {i}. Time: {adj['time'].strftime('%H:%M:%S')} | "
                      f"Profit: ${adj['profit']:.2f} | "
                      f"SL Profit: ${adj['sl_profit']:.2f} | "
                      f"Reason: {adj['reason']}")
    
    print("\n" + "=" * 80)
    print(f"TOTAL PROFIT/LOSS: ${total_profit:.2f}")
    print("=" * 80)
    
    # Shutdown MT5
    mt5.shutdown()

if __name__ == '__main__':
    try:
        monitor_trades()
    except KeyboardInterrupt:
        print_final_summary()

