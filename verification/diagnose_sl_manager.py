#!/usr/bin/env python3
"""
SL Manager Diagnostic Script
Collects current state of SL Manager for analysis.
"""

import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.sl_manager import SLManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
import json as json_module

def load_config():
    """Load configuration."""
    config_path = Path(__file__).parent.parent / 'config.json'
    with open(config_path, 'r') as f:
        return json_module.load(f)

def diagnose_sl_manager():
    """Run comprehensive diagnostics on SL Manager."""
    print("=" * 80)
    print("SL MANAGER DIAGNOSTICS")
    print("=" * 80)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    
    # Load config
    config = load_config()
    
    # Initialize components
    mt5_connector = MT5Connector(config)
    order_manager = OrderManager(mt5_connector)
    sl_manager = SLManager(config, mt5_connector, order_manager)
    
    diagnostics = {
        'timestamp': datetime.now().isoformat(),
        'configuration': {
            'max_risk_usd': sl_manager.max_risk_usd,
            'trailing_increment_usd': sl_manager.trailing_increment_usd,
            'sweet_spot_min': sl_manager.sweet_spot_min,
            'sweet_spot_max': sl_manager.sweet_spot_max,
            'break_even_enabled': sl_manager.break_even_enabled,
            'break_even_duration_seconds': sl_manager.break_even_duration_seconds,
            'sl_worker_interval': sl_manager._sl_worker_interval,
            'sl_update_min_interval': sl_manager._sl_update_min_interval,
            'global_rpc_max_per_second': sl_manager._global_rpc_max_per_second,
            'sl_update_max_retries': sl_manager.sl_update_max_retries,
            'sl_update_retry_backoff_base': sl_manager.sl_update_retry_backoff_base,
            'sl_update_verification_delay': sl_manager.sl_update_verification_delay,
        },
        'worker_status': sl_manager.get_worker_status(),
        'disabled_symbols': list(sl_manager._disabled_symbols),
        'manual_review_tickets': list(sl_manager._manual_review_tickets),
    }
    
    # Get open positions
    positions = order_manager.get_open_positions()
    diagnostics['open_positions'] = len(positions)
    
    # Per-ticket diagnostics
    ticket_diagnostics = {}
    for position in positions:
        ticket = position.get('ticket', 0)
        if ticket == 0:
            continue
        
        symbol = position.get('symbol', 'N/A')
        profit = position.get('profit', 0.0)
        sl_price = position.get('sl', 0.0)
        
        # Get last update info
        with sl_manager._tracking_lock:
            last_update = sl_manager._last_sl_update.get(ticket)
            last_sl_price = sl_manager._last_sl_price.get(ticket, 0.0)
            last_sl_reason = sl_manager._last_sl_reason.get(ticket, 'N/A')
        
        # Get rate limit info
        last_rate_limit_time = sl_manager._sl_update_rate_limit.get(ticket)
        time_since_last = None
        if last_rate_limit_time:
            time_since_last = time.time() - last_rate_limit_time
        
        # Get effective SL profit
        try:
            effective_sl_profit = sl_manager.get_effective_sl_profit(position)
        except Exception as e:
            effective_sl_profit = None
            effective_sl_error = str(e)
        
        ticket_diagnostics[ticket] = {
            'symbol': symbol,
            'profit': profit,
            'sl_price': sl_price,
            'last_update_time': last_update.isoformat() if last_update else None,
            'time_since_last_update_seconds': time_since_last,
            'last_sl_price': last_sl_price,
            'last_sl_reason': last_sl_reason,
            'effective_sl_profit': effective_sl_profit if 'effective_sl_error' not in locals() else None,
            'effective_sl_error': effective_sl_error if 'effective_sl_error' in locals() else None,
            'is_disabled': symbol in sl_manager._disabled_symbols,
            'in_manual_review': ticket in sl_manager._manual_review_tickets,
        }
    
    diagnostics['ticket_diagnostics'] = ticket_diagnostics
    
    # Global RPC rate limit status
    with sl_manager._global_rpc_lock:
        current_time = time.time()
        recent_timestamps = [ts for ts in sl_manager._global_rpc_timestamps if current_time - ts < 1.0]
        diagnostics['global_rpc_rate_limit'] = {
            'recent_updates_last_second': len(recent_timestamps),
            'max_allowed': sl_manager._global_rpc_max_per_second,
            'is_at_limit': len(recent_timestamps) >= sl_manager._global_rpc_max_per_second,
        }
    
    # Lock contention stats
    lock_stats = {}
    with sl_manager._locks_lock:
        for ticket, lock in sl_manager._ticket_locks.items():
            lock_stats[ticket] = {
                'is_locked': lock.locked(),
            }
    
    diagnostics['lock_stats'] = lock_stats
    
    # Timing stats
    timing_stats = sl_manager.get_timing_stats()
    diagnostics['timing_stats'] = timing_stats
    
    # Contract size cache
    with sl_manager._contract_size_lock:
        diagnostics['contract_size_cache'] = dict(sl_manager._contract_size_cache)
    
    # Error occurrence metrics
    diagnostics['error_occurrence_metrics'] = dict(sl_manager._error_occurrence_metrics)
    
    # Save diagnostics
    output_dir = Path(__file__).parent / 'data'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / 'sl_diagnostics.json'
    
    with open(output_file, 'w') as f:
        json.dump(diagnostics, f, indent=2, default=str)
    
    print(f"[OK] Diagnostics saved to: {output_file}")
    print()
    print("SUMMARY:")
    print(f"  Open Positions: {diagnostics['open_positions']}")
    print(f"  Disabled Symbols: {len(diagnostics['disabled_symbols'])}")
    print(f"  Manual Review Tickets: {len(diagnostics['manual_review_tickets'])}")
    print(f"  Global RPC Rate: {diagnostics['global_rpc_rate_limit']['recent_updates_last_second']}/{diagnostics['global_rpc_rate_limit']['max_allowed']}")
    print(f"  Worker Running: {diagnostics['worker_status']['running']}")
    print()
    
    # Print per-ticket summary
    if ticket_diagnostics:
        print("PER-TICKET STATUS:")
        for ticket, info in ticket_diagnostics.items():
            time_since = info['time_since_last_update_seconds']
            time_str = f"{time_since:.1f}s ago" if time_since else "Never"
            print(f"  Ticket {ticket} ({info['symbol']}):")
            print(f"    Profit: ${info['profit']:.2f}")
            print(f"    SL Price: {info['sl_price']:.5f}")
            print(f"    Effective SL Profit: ${info['effective_sl_profit']:.2f}" if info['effective_sl_profit'] else "    Effective SL Profit: N/A")
            print(f"    Last Update: {time_str}")
            print(f"    Last Reason: {info['last_sl_reason']}")
            if info['is_disabled']:
                print(f"    [WARNING]  SYMBOL DISABLED")
            if info['in_manual_review']:
                print(f"    [WARNING]  MANUAL REVIEW REQUIRED")
            print()
    
    return diagnostics

if __name__ == '__main__':
    try:
        diagnostics = diagnose_sl_manager()
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] Error running diagnostics: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

