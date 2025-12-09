#!/usr/bin/env python3
"""
Verification Script for Legacy Log Conversion
Verifies that all converted trades are in valid JSONL format and compatible with the new system.
"""

import os
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any


def verify_conversion():
    """Verify converted log files."""
    print("=" * 80)
    print("CONVERSION VERIFICATION")
    print("=" * 80)
    print()
    
    trades_dir = Path('logs/trades')
    if not trades_dir.exists():
        print("❌ ERROR: logs/trades/ directory does not exist!")
        return False
    
    stats = {
        'total_files': 0,
        'total_trades': 0,
        'valid_trades': 0,
        'invalid_trades': 0,
        'closed_trades': 0,
        'open_trades': 0,
        'trades_by_symbol': defaultdict(int),
        'errors': []
    }
    
    # Check all .log files in trades directory
    for log_file in sorted(trades_dir.glob('*.log')):
        # Skip backup files
        if 'backup' in log_file.name:
            continue
        
        symbol = log_file.stem
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                f.seek(0)
                lines = f.readlines()
            
            # Check if file is JSONL format (starts with '{')
            if not first_line or not first_line.startswith('{'):
                # Skip old format log files (standard logging format)
                print(f"  ⚠️  Skipping {log_file.name} (old format, not JSONL)")
                continue
            
            stats['total_files'] += 1
            
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if not line:
                    continue
                
                stats['total_trades'] += 1
                
                try:
                    trade = json.loads(line)
                    
                    # Validate required fields
                    required_fields = ['timestamp', 'symbol', 'trade_type', 'entry_price', 
                                     'status', 'order_id']
                    missing_fields = [f for f in required_fields if f not in trade or trade[f] is None]
                    
                    if missing_fields:
                        stats['errors'].append(
                            f"{symbol}.log: Line {line_num} missing fields: {missing_fields}"
                        )
                        stats['invalid_trades'] += 1
                        continue
                    
                    # Validate field types
                    if not isinstance(trade['timestamp'], str):
                        stats['errors'].append(
                            f"{symbol}.log: Line {line_num} timestamp must be string"
                        )
                        stats['invalid_trades'] += 1
                        continue
                    
                    if trade['status'] not in ['OPEN', 'CLOSED']:
                        stats['errors'].append(
                            f"{symbol}.log: Line {line_num} invalid status: {trade['status']}"
                        )
                        stats['invalid_trades'] += 1
                        continue
                    
                    # Count status
                    if trade['status'] == 'CLOSED':
                        stats['closed_trades'] += 1
                    else:
                        stats['open_trades'] += 1
                    
                    stats['valid_trades'] += 1
                    stats['trades_by_symbol'][symbol] += 1
                    
                except json.JSONDecodeError as e:
                    stats['errors'].append(
                        f"{symbol}.log: Line {line_num} JSON decode error: {e}"
                    )
                    stats['invalid_trades'] += 1
        
        except Exception as e:
            stats['errors'].append(f"{symbol}.log: File read error: {e}")
    
    # Print results
    print(f"Files processed: {stats['total_files']}")
    print(f"Total trades: {stats['total_trades']}")
    print(f"  Valid trades: {stats['valid_trades']}")
    print(f"  Invalid trades: {stats['invalid_trades']}")
    print(f"  Closed trades: {stats['closed_trades']}")
    print(f"  Open trades: {stats['open_trades']}")
    print()
    
    if stats['errors']:
        print(f"⚠️  Found {len(stats['errors'])} errors:")
        for error in stats['errors'][:20]:  # Show first 20 errors
            print(f"  - {error}")
        if len(stats['errors']) > 20:
            print(f"  ... and {len(stats['errors']) - 20} more errors")
        print()
    
    print("Trades by symbol:")
    for symbol, count in sorted(stats['trades_by_symbol'].items()):
        print(f"  {symbol}: {count}")
    print()
    
    # Check format compatibility
    print("Format Compatibility Check:")
    print("  ✓ JSONL format (one JSON object per line)")
    print("  ✓ Required fields: timestamp, symbol, trade_type, entry_price, status, order_id")
    print("  ✓ Optional fields: exit_price, profit_usd, additional_info")
    print()
    
    if stats['invalid_trades'] == 0:
        print("✅ All trades are valid!")
        return True
    else:
        print(f"⚠️  {stats['invalid_trades']} invalid trades found")
        return False


if __name__ == "__main__":
    success = verify_conversion()
    exit(0 if success else 1)

