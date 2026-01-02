"""
Lightweight Real-Time Logger
Prints system status every 1 second without interfering with trading operations.
Also logs to file for later review.

Enhanced version for SL Manager verification:
- Per-second summary table of all open positions
- Structured JSONL and CSV logging
- SL Manager metrics tracking
- Sweet spot/trailing zone detection
- SL update status monitoring
"""

import time
import threading
import json
import csv
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path
import MetaTrader5 as mt5
from utils.logger_factory import get_logger


def start_realtime_logger(mt5_connector, bot_state_getter, shutdown_event: threading.Event, sl_manager=None, console_output=True):
    """
    Start lightweight real-time logger in a separate daemon thread.
    
    Args:
        mt5_connector: MT5 connector instance
        bot_state_getter: Function that returns bot state dictionary (thread-safe access)
        shutdown_event: Event to signal shutdown
        sl_manager: Optional SLManager instance for metrics tracking
        console_output: If False, disable all console output (default: True)
    """
    # File logging disabled to save storage space
    # Runtime logs are no longer written to files
    jsonl_file = None
    csv_file = None
    csv_writer = None
    file_logger = None
    
    def logger_loop():
        """Main logging loop - runs every 1 second."""
        # Access console_output from outer scope
        nonlocal console_output
        # Track previous values for event detection
        prev_profits = {}  # {ticket: profit}
        prev_sl_prices = {}  # {ticket: sl_price}
        prev_sl_update_times = {}  # {ticket: last_update_time}
        execution_errors = []  # List of recent errors (last 60s)
        slow_executions = []  # List of slow executions (last 60s)
        close_failures = []  # List of close failures (last 60s)
        sweet_spot_entries = []  # List of sweet spot entries (last 60s)
        trailing_zone_entries = []  # List of trailing zone entries (last 60s)
        sl_not_updating = []  # List of SL not updating warnings (last 60s)
        
        # Track runtime
        start_time = datetime.now()
        
        try:
            while not shutdown_event.is_set():
                try:
                    current_time = datetime.now()
                    timestamp = current_time.strftime('%H:%M:%S')
                    timestamp_full = current_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    
                    # Get bot state (call getter function for thread-safe read)
                    if callable(bot_state_getter):
                        bot_state = bot_state_getter()
                    else:
                        bot_state = bot_state_getter
                    
                    bot_running = bot_state.get('running', False)
                    current_state = bot_state.get('current_state', 'IDLE')
                    current_symbol = bot_state.get('current_symbol', 'N/A')
                    last_action = bot_state.get('last_action', 'N/A')
                    last_action_time = bot_state.get('last_action_time', None)
                    
                    # Get open positions
                    positions = []
                    if mt5_connector and mt5_connector.ensure_connected():
                        positions_raw = mt5.positions_get()
                        if positions_raw:
                            for pos in positions_raw:
                                positions.append({
                                    'ticket': pos.ticket,
                                    'symbol': pos.symbol,
                                    'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                                    'volume': pos.volume,
                                    'price_open': pos.price_open,
                                    'price_current': pos.price_current,
                                    'profit': pos.profit,
                                    'sl': pos.sl,
                                    'tp': pos.tp,
                                    'time_open': datetime.fromtimestamp(pos.time) if pos.time > 0 else None
                                })
                    
                    # Get SL Manager metrics if available
                    sl_metrics = {}
                    if sl_manager:
                        try:
                            with sl_manager._tracking_lock:
                                sl_metrics = {
                                    'last_sl_success': sl_manager._last_sl_success.copy(),
                                    'last_sl_attempt': sl_manager._last_sl_attempt.copy(),
                                    'consecutive_failures': dict(sl_manager._consecutive_failures),
                                    'ticket_circuit_breaker': sl_manager._ticket_circuit_breaker.copy(),
                                    'last_sl_price': sl_manager._last_sl_price.copy(),
                                    'last_sl_reason': sl_manager._last_sl_reason.copy()
                                }
                        except Exception as e:
                            # If we can't get metrics, continue without them
                            pass
                    
                    # Clean old events (older than 60 seconds)
                    cutoff_time = current_time - timedelta(seconds=60)
                    execution_errors = [e for e in execution_errors if e['time'] > cutoff_time]
                    slow_executions = [e for e in slow_executions if e['time'] > cutoff_time]
                    close_failures = [e for e in close_failures if e['time'] > cutoff_time]
                    sweet_spot_entries = [e for e in sweet_spot_entries if e['time'] > cutoff_time]
                    trailing_zone_entries = [e for e in trailing_zone_entries if e['time'] > cutoff_time]
                    sl_not_updating = [e for e in sl_not_updating if e['time'] > cutoff_time]
                    
                    # Detect events for each position
                    for pos in positions:
                        ticket = pos['ticket']
                        profit = pos['profit']
                        sl_price = pos['sl']
                        
                        # Detect sweet spot entry ($0.03 - $0.10)
                        if ticket in prev_profits:
                            prev_profit = prev_profits[ticket]
                            if prev_profit < 0.03 and 0.03 <= profit <= 0.10:
                                sweet_spot_entries.append({
                                    'time': current_time,
                                    'ticket': ticket,
                                    'symbol': pos['symbol'],
                                    'profit': profit
                                })
                        
                        # Detect trailing zone entry ($0.10+)
                        if ticket in prev_profits:
                            prev_profit = prev_profits[ticket]
                            if prev_profit < 0.10 and profit >= 0.10:
                                trailing_zone_entries.append({
                                    'time': current_time,
                                    'ticket': ticket,
                                    'symbol': pos['symbol'],
                                    'profit': profit
                                })
                        
                        # Detect SL not updating (>5 seconds)
                        if ticket in prev_sl_update_times:
                            time_since_update = (current_time - prev_sl_update_times[ticket]).total_seconds()
                            if time_since_update > 5.0:
                                # Check if price has moved but SL hasn't
                                if ticket in prev_sl_prices and prev_sl_prices[ticket] == sl_price:
                                    # Price moved but SL didn't
                                    if ticket not in [e['ticket'] for e in sl_not_updating if (current_time - e['time']).total_seconds() < 5]:
                                        sl_not_updating.append({
                                            'time': current_time,
                                            'ticket': ticket,
                                            'symbol': pos['symbol'],
                                            'time_since_update': time_since_update
                                        })
                        
                        # Update tracking
                        prev_profits[ticket] = profit
                        prev_sl_prices[ticket] = sl_price
                        if ticket not in prev_sl_update_times:
                            prev_sl_update_times[ticket] = current_time
                    
                    # Update SL update times from SL Manager metrics
                    if sl_manager and 'last_sl_success' in sl_metrics:
                        for ticket, update_time in sl_metrics['last_sl_success'].items():
                            if isinstance(update_time, datetime):
                                if ticket in prev_sl_update_times:
                                    prev_sl_update_times[ticket] = update_time
                    
                    # Remove tracking for closed positions
                    current_tickets = {pos['ticket'] for pos in positions}
                    prev_profits = {t: p for t, p in prev_profits.items() if t in current_tickets}
                    prev_sl_prices = {t: p for t, p in prev_sl_prices.items() if t in current_tickets}
                    prev_sl_update_times = {t: p for t, p in prev_sl_update_times.items() if t in current_tickets}
                    
                    # Calculate runtime
                    runtime = current_time - start_time
                    runtime_str = str(runtime).split('.')[0]  # HH:MM:SS format
                    
                    # Print header (only if console output enabled)
                    if console_output:
                        print(f"\n{'='*100}")
                        print(f"🔄 OPEN POSITIONS - {timestamp} (Runtime: {runtime_str})")
                        print(f"{'='*100}")
                    
                    # Build position table
                    if positions:
                        # Table header (only if console output enabled)
                        if console_output:
                            print(f"+{'------------'}+{'--------'}+{'------'}+{'-------+'}+{'---------'}+{'---------'}+{'---------'}+{'--------'}+")
                            print(f"| {'Ticket':<10} | {'Symbol':<6} | {'Type':<4} | {'Lot':<5} | {'Entry':<7} | {'Current':<7} | {'SL':<7} | {'P/L':<6} |")
                            print(f"+{'------------'}+{'--------'}+{'------'}+{'-------+'}+{'---------'}+{'---------'}+{'---------'}+{'--------'}+")
                        
                        # Position rows
                        for pos in positions:
                            ticket = pos['ticket']
                            symbol = pos['symbol']
                            pos_type = pos['type']
                            volume = pos['volume']
                            entry = pos['price_open']
                            current = pos['price_current']
                            profit = pos['profit']
                            sl = pos['sl']
                            
                            # Get SL Manager metrics for this ticket
                            last_sl_success = sl_metrics.get('last_sl_success', {}).get(ticket)
                            last_sl_attempt = sl_metrics.get('last_sl_attempt', {}).get(ticket)
                            consecutive_failures = sl_metrics.get('consecutive_failures', {}).get(ticket, 0)
                            circuit_breaker = sl_metrics.get('ticket_circuit_breaker', {}).get(ticket)
                            last_sl_reason = sl_metrics.get('last_sl_reason', {}).get(ticket, 'N/A')
                            last_sl_price_target = sl_metrics.get('last_sl_price', {}).get(ticket)
                            
                            # Calculate effective SL profit
                            effective_sl_profit = 0.0
                            if sl_manager:
                                try:
                                    effective_sl_profit = sl_manager.get_effective_sl_profit(pos)
                                except:
                                    pass
                            
                            # Calculate time since last SL update
                            time_since_update = 'N/A'
                            last_update_result = 'N/A'
                            if last_sl_success:
                                if isinstance(last_sl_success, datetime):
                                    time_diff = (current_time - last_sl_success).total_seconds()
                                    if time_diff < 60:
                                        time_since_update = f"{time_diff:.1f}s"
                                    else:
                                        time_since_update = f"{time_diff/60:.1f}m"
                                    last_update_result = '[OK]'
                                else:
                                    time_since_update = 'N/A'
                            elif last_sl_attempt:
                                if isinstance(last_sl_attempt, datetime):
                                    time_diff = (current_time - last_sl_attempt).total_seconds()
                                    if time_diff < 60:
                                        time_since_update = f"{time_diff:.1f}s"
                                    else:
                                        time_since_update = f"{time_diff/60:.1f}m"
                                    last_update_result = '[W]'
                            
                            # Determine SL status
                            sl_status = '[OK]'
                            if consecutive_failures >= 5:
                                sl_status = '[W] VIOLATION'
                            elif circuit_breaker:
                                if isinstance(circuit_breaker, datetime):
                                    if current_time < circuit_breaker:
                                        sl_status = '[W] CB'
                                else:
                                    sl_status = '[W]'
                            elif time_since_update != 'N/A':
                                try:
                                    time_val = float(time_since_update.replace('s', '').replace('m', ''))
                                    if 'm' in time_since_update:
                                        time_val *= 60
                                    # CRITICAL FIX: Only show "[W]" if time > 5s AND last_update_result is "[W]" (failed update)
                                    # If last_update_result is "[OK]" (successful or no update needed), keep "[OK]" even if > 5s
                                    if time_val > 5.0 and last_update_result == '[W]':
                                        sl_status = '[W]'
                                    # If last_update_result is "[OK]", keep "[OK]" regardless of time (SL is correct)
                                except:
                                    pass
                            
                            # Check sweet spot and trailing
                            in_sweet_spot = 0.03 <= profit <= 0.10
                            in_trailing_zone = profit >= 0.10
                            
                            # Format values
                            ticket_str = str(ticket)[-7:]  # Last 7 digits
                            entry_str = f"{entry:.2f}" if entry < 10000 else f"{entry:.0f}"
                            current_str = f"{current:.2f}" if current < 10000 else f"{current:.0f}"
                            sl_str = f"{sl:.2f}" if sl > 0 and sl < 10000 else (f"{sl:.0f}" if sl > 0 else "None")
                            profit_str = f"${profit:.2f}"
                            
                            # Print row (only if console output enabled)
                            if console_output:
                                print(f"| {ticket_str:<10} | {symbol:<6} | {pos_type:<4} | {volume:<5.2f} | {entry_str:<7} | {current_str:<7} | {sl_str:<7} | {profit_str:<6} |")
                                
                                # Print status line
                                status_line = f"  SL Status: {sl_status} | Last Update: {time_since_update} ({last_update_result})"
                                if consecutive_failures > 0:
                                    status_line += f" | Failures: {consecutive_failures}"
                                if circuit_breaker:
                                    status_line += f" | Circuit Breaker: ON"
                                if in_sweet_spot:
                                    status_line += f" | [+] Sweet Spot"
                                if in_trailing_zone:
                                    status_line += f" | 🔵 Trailing"
                                print(status_line)
                            
                            # File logging disabled - no JSONL or CSV writes
                        
                        if console_output:
                            print(f"+{'------------'}+{'--------'}+{'------'}+{'-------+'}+{'---------'}+{'---------'}+{'---------'}+{'--------'}+")
                    else:
                        if console_output:
                            print("No open positions")
                    
                    # Print events summary (only if console output enabled)
                    if console_output:
                        events_summary = []
                        if sweet_spot_entries:
                            for e in sweet_spot_entries[-3:]:
                                events_summary.append(f"[+] Sweet Spot: Ticket {e['ticket']} @ ${e['profit']:.2f}")
                        if trailing_zone_entries:
                            for e in trailing_zone_entries[-3:]:
                                events_summary.append(f"🔵 Trailing: Ticket {e['ticket']} @ ${e['profit']:.2f}")
                        if sl_not_updating:
                            for e in sl_not_updating[-3:]:
                                events_summary.append(f"[WARNING] SL Not Updated: Ticket {e['ticket']} ({e['time_since_update']:.1f}s)")
                        if close_failures:
                            for e in close_failures[-3:]:
                                events_summary.append(f"[ERROR] Close Failed: Ticket {e.get('ticket', 'N/A')}")
                        if execution_errors:
                            for e in execution_errors[-3:]:
                                events_summary.append(f"[ERROR] Execution Error: {e.get('message', 'Unknown')[:50]}")
                        
                        if events_summary:
                            print(f"\n📊 RECENT EVENTS (last 60s):")
                            for event in events_summary:
                                print(f"  {event}")
                    
                    # File logging disabled - no text log writes
                    
                except Exception as e:
                    # Don't let logger errors break the system
                    error_msg = f"[ERROR] Logger error: {e}"
                    if console_output:
                        print(error_msg)
                    # File logging disabled - no file error logging
                
                # Sleep exactly 1 second
                shutdown_event.wait(1.0)
        
        finally:
            # Close files on shutdown (if they were opened)
            if jsonl_file:
                jsonl_file.close()
            if csv_file:
                csv_file.close()
            # File logging disabled - no shutdown message
    
    # Start logger thread
    logger_thread = threading.Thread(
        target=logger_loop,
        name="LightweightRealtimeLogger",
        daemon=True
    )
    logger_thread.start()
    
    return logger_thread
