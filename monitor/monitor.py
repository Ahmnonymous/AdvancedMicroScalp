#!/usr/bin/env python3
"""
Real-Time Trading Bot Monitor
Foreground monitoring interface displaying live trade status, SL adjustments, and P/L.
"""

import os
import sys
import time
import json
import re
from datetime import datetime, timedelta
from collections import defaultdict
import MetaTrader5 as mt5

# ANSI color codes for terminal
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def clear_screen():
    """Clear terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')

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
            'volume': pos.volume,
            'price_open': pos.price_open,
            'price_current': pos.price_current,
            'sl': pos.sl,
            'tp': pos.tp,
            'profit': pos.profit,
            'swap': pos.swap,
            'time': datetime.fromtimestamp(pos.time)
        })
    mt5.shutdown()
    return result

def get_account_info():
    """Get account information."""
    if not mt5.initialize():
        return None
    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        return None
    info = {
        'balance': account.balance,
        'equity': account.equity,
        'margin': account.margin,
        'free_margin': account.margin_free,
        'profit': account.profit,
        'currency': account.currency
    }
    mt5.shutdown()
    return info

def parse_log_events(log_file='bot_log.txt'):
    """Parse recent log events."""
    events = {
        'trailing_stops': [],
        'big_jumps': [],
        'trades_executed': [],
        'trades_closed': [],
        'symbols_analyzed': []
    }
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Get last 100 lines
            recent_lines = lines[-100:] if len(lines) > 100 else lines
            
            for line in recent_lines:
                # Trailing stop adjustments
                if "TRAILING STOP:" in line:
                    match = re.search(r'Ticket (\d+).*?\((\w+)\s+(BUY|SELL)\).*?Profit: \$([\d.]+).*?SL Profit: \$([\d.]+).*?SL Price: ([\d.]+).*?Reason: (.+?)(?:\s*$|\s*\||\s*$)', line)
                    if match:
                        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        events['trailing_stops'].append({
                            'ticket': int(match.group(1)),
                            'symbol': match.group(2),
                            'type': match.group(3),
                            'profit': float(match.group(4)),
                            'sl_profit': float(match.group(5)),
                            'sl_price': float(match.group(6)),
                            'reason': match.group(7),
                            'time': datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S') if time_match else datetime.now()
                        })
                
                # Big jumps
                if "BIG JUMP:" in line:
                    match = re.search(r'Ticket (\d+)', line)
                    if match:
                        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        events['big_jumps'].append({
                            'ticket': int(match.group(1)),
                            'time': datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S') if time_match else datetime.now()
                        })
                
                # Trade executions (including test mode)
                if ("Trade executed:" in line or "[TEST] TEST MODE: [OK] Trade executed:" in line) and "Ticket" in line:
                    match = re.search(r'Ticket (\d+).*?(\w+)\s+(LONG|SHORT)', line)
                    if match:
                        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        # Try to extract quality score
                        quality_match = re.search(r'Quality[:\s]+([\d.]+)', line)
                        quality_score = float(quality_match.group(1)) if quality_match else None
                        
                        # Try to extract spread
                        spread_match = re.search(r'Spread[:\s]+([\d.]+)', line)
                        spread = float(spread_match.group(1)) if spread_match else None
                        
                        events['trades_executed'].append({
                            'ticket': int(match.group(1)),
                            'symbol': match.group(2),
                            'direction': match.group(3),
                            'quality_score': quality_score,
                            'spread': spread,
                            'time': datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S') if time_match else datetime.now()
                        })
                
                # Symbol analysis (test mode - symbols being considered)
                if "[TEST] TEST MODE:" in line and "[OK]" in line and "Signal=" in line:
                    # This shows symbols that passed all filters
                    match = re.search(r'[OK]\s+(\w+):\s+Signal=(\w+).*?RSI=([\d.]+).*?Spread=([\d.]+).*?Quality.*?Score:\s+([\d.]+)', line)
                    if match:
                        if 'symbols_analyzed' not in events:
                            events['symbols_analyzed'] = []
                        events['symbols_analyzed'].append({
                            'symbol': match.group(1),
                            'signal': match.group(2),
                            'rsi': float(match.group(3)),
                            'spread': float(match.group(4)),
                            'quality_score': float(match.group(5)),
                            'time': datetime.now()
                        })
    
    except FileNotFoundError:
        # Log file doesn't exist yet
        return events
    except Exception as e:
        # Silently handle parsing errors
        pass
    
    return events

def display_header(account_info):
    """Display header with account info."""
    # Check if test mode is enabled
    try:
        import json
        with open('config.json', 'r') as f:
            config = json.load(f)
        test_mode = config.get('pairs', {}).get('test_mode', False)
        test_mode_indicator = f" {Colors.YELLOW}[TEST] TEST MODE{Colors.END}" if test_mode else ""
    except:
        test_mode = False
        test_mode_indicator = ""
    
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}[BOT] TRADING BOT - REAL-TIME MONITOR{test_mode_indicator}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
    print(f"{Colors.BOLD}Time: {Colors.END}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if account_info:
        balance_color = Colors.GREEN if account_info['equity'] >= account_info['balance'] else Colors.RED
        print(f"{Colors.BOLD}Account: {Colors.END}Balance: ${account_info['balance']:.2f} | "
              f"{balance_color}Equity: ${account_info['equity']:.2f}{Colors.END} | "
              f"Free Margin: ${account_info['free_margin']:.2f} | "
              f"Profit: ${account_info['profit']:.2f}")
    print(f"{Colors.BOLD}Monitoring Interval: {Colors.END}3.0 seconds | "
          f"{Colors.BOLD}Trailing Stop Interval: {Colors.END}3.0 seconds")
    
    if test_mode:
        print(f"{Colors.YELLOW}[WARNING]  TEST MODE: Testing ALL available symbols (spread/exotic/halal restrictions ignored){Colors.END}")
        print(f"{Colors.YELLOW}   Scalping filters (trend, RSI, choppiness, ADX) still active for quality{Colors.END}")
    
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}\n")

def display_positions(positions):
    """Display open positions."""
    if not positions:
        print(f"{Colors.YELLOW}📊 Open Positions: 0{Colors.END}\n")
        return
    
    print(f"{Colors.BOLD}{Colors.BLUE}📊 OPEN POSITIONS ({len(positions)}){Colors.END}")
    print(f"{Colors.BLUE}{'-' * 100}{Colors.END}")
    
    total_profit = 0.0
    for pos in positions:
        duration = datetime.now() - pos['time']
        profit_color = Colors.GREEN if pos['profit'] >= 0 else Colors.RED
        profit_symbol = "[+]" if pos['profit'] >= 0 else "[-]"
        
        total_profit += pos['profit']
        
        print(f"{profit_symbol} {Colors.BOLD}Ticket {pos['ticket']}{Colors.END} | "
              f"{pos['symbol']} {pos['type']} | "
              f"Lot: {pos['volume']:.2f} | "
              f"{profit_color}P/L: ${pos['profit']:.2f}{Colors.END} | "
              f"SL: {pos['sl']:.5f} | "
              f"Duration: {str(duration).split('.')[0]}")
        price_change_pct = ((pos['price_current'] - pos['price_open']) / pos['price_open'] * 100) if pos['price_open'] > 0 else 0
        print(f"   Entry: {pos['price_open']:.5f} → Current: {pos['price_current']:.5f} | "
              f"Price Change: {price_change_pct:.2f}%")
        
        # Calculate unrealized P/L percentage
        if pos['price_open'] > 0:
            pnl_pct = (pos['profit'] / (pos['price_open'] * pos['volume'] * 100000)) * 100 if pos['volume'] > 0 else 0
            print(f"   Unrealized P/L: {profit_color}${pos['profit']:.2f} ({pnl_pct:+.2f}%){Colors.END}")
    
    total_color = Colors.GREEN if total_profit >= 0 else Colors.RED
    print(f"{Colors.BLUE}{'-' * 100}{Colors.END}")
    print(f"{Colors.BOLD}Total P/L: {total_color}${total_profit:.2f}{Colors.END}\n")

def display_recent_events(events):
    """Display recent events."""
    # Recent trailing stops
    if events['trailing_stops']:
        recent_sl = events['trailing_stops'][-5:]  # Last 5
        print(f"{Colors.BOLD}{Colors.YELLOW}[STATS] RECENT TRAILING STOP ADJUSTMENTS ({len(events['trailing_stops'])} total){Colors.END}")
        print(f"{Colors.YELLOW}{'-' * 100}{Colors.END}")
        for adj in recent_sl:
            time_str = adj['time'].strftime('%H:%M:%S')
            print(f"  {time_str} | Ticket {adj['ticket']} ({adj['symbol']} {adj['type']}) | "
                  f"Profit: ${adj['profit']:.2f} → SL: ${adj['sl_profit']:.2f} | "
                  f"SL Price: {adj['sl_price']:.5f} | {adj['reason']}")
        print()
    
    # Big jumps
    if events['big_jumps']:
        recent_jumps = events['big_jumps'][-3:]  # Last 3
        print(f"{Colors.BOLD}{Colors.RED}[JUMP] BIG JUMPS DETECTED ({len(events['big_jumps'])} total){Colors.END}")
        print(f"{Colors.RED}{'-' * 100}{Colors.END}")
        for jump in recent_jumps:
            time_str = jump['time'].strftime('%H:%M:%S')
            print(f"  {time_str} | Ticket {jump['ticket']}: Large profit increase detected!")
        print()
    
    # Recent trades
    if events['trades_executed']:
        recent_trades = events['trades_executed'][-5:]  # Last 5
        print(f"{Colors.BOLD}{Colors.GREEN}[OK] RECENT TRADES ({len(events['trades_executed'])} total){Colors.END}")
        print(f"{Colors.GREEN}{'-' * 100}{Colors.END}")
        for trade in recent_trades:
            time_str = trade['time'].strftime('%H:%M:%S')
            quality_info = f" | Quality: {trade['quality_score']}/100" if trade.get('quality_score') else ""
            spread_info = f" | Spread: {trade['spread']:.1f}pts" if trade.get('spread') else ""
            print(f"  {time_str} | Ticket {trade['ticket']}: {trade['symbol']} {trade['direction']}{quality_info}{spread_info}")
        print()
    
    # Symbols analyzed (test mode)
    if events.get('symbols_analyzed'):
        recent_symbols = events['symbols_analyzed'][-10:]  # Last 10
        print(f"{Colors.BOLD}{Colors.CYAN}🔍 SYMBOLS ANALYZED (Test Mode) - Last 10{Colors.END}")
        print(f"{Colors.CYAN}{'-' * 100}{Colors.END}")
        for sym in recent_symbols:
            print(f"  {sym['symbol']}: {sym['signal']} | RSI: {sym['rsi']:.1f} | "
                  f"Spread: {sym['spread']:.1f}pts | Quality: {sym['quality_score']}/100")
        print()

def display_status_summary(positions, events):
    """Display status summary."""
    print(f"{Colors.BOLD}{Colors.CYAN}📊 STATUS SUMMARY{Colors.END}")
    print(f"{Colors.CYAN}{'-' * 100}{Colors.END}")
    print(f"Open Positions: {len(positions)}")
    print(f"Total Trailing Stop Adjustments: {len(events['trailing_stops'])}")
    print(f"Big Jumps Detected: {len(events['big_jumps'])}")
    print(f"Trades Executed (session): {len(events['trades_executed'])}")
    print(f"{Colors.CYAN}{'-' * 100}{Colors.END}\n")

def monitor():
    """Main monitoring loop."""
    print(f"{Colors.BOLD}Starting real-time monitor...{Colors.END}\n")
    print(f"{Colors.CYAN}Reading live data from MT5 and bot_log.txt...{Colors.END}\n")
    time.sleep(1)  # Brief pause before starting
    
    check_count = 0
    last_positions = {}
    
    try:
        while True:
            check_count += 1
            clear_screen()
            
            # Get current data
            account_info = get_account_info()
            positions = get_open_positions()
            events = parse_log_events()
            
            # Display header
            display_header(account_info)
            
            # Display positions
            display_positions(positions)
            
            # Display recent events
            display_recent_events(events)
            
            # Display status summary
            display_status_summary(positions, events)
            
            # Check for position changes
            current_tickets = {p['ticket'] for p in positions}
            if current_tickets != set(last_positions.keys()):
                if len(current_tickets) > len(last_positions):
                    new_tickets = current_tickets - set(last_positions.keys())
                    for ticket in new_tickets:
                        print(f"{Colors.GREEN}🆕 New position opened: Ticket {ticket}{Colors.END}")
                elif len(current_tickets) < len(last_positions):
                    closed_tickets = set(last_positions.keys()) - current_tickets
                    for ticket in closed_tickets:
                        print(f"{Colors.RED}[-] Position closed: Ticket {ticket}{Colors.END}")
            
            last_positions = {p['ticket']: p for p in positions}
            
            # Footer
            print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
            print(f"{Colors.BOLD}Update #{check_count} | Next update in 3 seconds... | Press Ctrl+C to stop{Colors.END}")
            print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
            
            time.sleep(3)  # Update every 3 seconds
    
    except KeyboardInterrupt:
        clear_screen()
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
        print(f"{Colors.BOLD}Monitoring stopped by user{Colors.END}")
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
        if mt5.initialize():
            mt5.shutdown()
        sys.exit(0)
    except Exception as e:
        print(f"{Colors.RED}Error in monitoring: {e}{Colors.END}")
        if mt5.initialize():
            mt5.shutdown()
        sys.exit(1)

if __name__ == '__main__':
    monitor()

