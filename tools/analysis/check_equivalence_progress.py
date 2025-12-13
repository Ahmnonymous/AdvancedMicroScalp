#!/usr/bin/env python3
"""
Check Equivalence Backtest Progress
Monitors the running backtest and shows current status.
"""

import os
import time
from pathlib import Path
from datetime import datetime

def check_progress():
    """Check backtest progress from logs."""
    log_file = Path("logs/backtest/equivalence_run.log")
    backtest_log_dir = Path("logs/backtest")
    
    print("=" * 80)
    print("EQUIVALENCE BACKTEST PROGRESS CHECK")
    print("=" * 80)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Check if log file exists
    if log_file.exists():
        print(f"[LOG] Reading from: {log_file}")
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            if lines:
                print(f"[LOG] Total lines: {len(lines)}")
                print("\n[LAST 20 LINES]:")
                print("-" * 80)
                for line in lines[-20:]:
                    print(line.rstrip())
            else:
                print("[LOG] Log file is empty")
    else:
        print("[LOG] Log file not found - backtest may not have started yet")
    
    # Check for limit entry logs
    print("\n" + "=" * 80)
    print("DRY-RUN LIMIT ENTRY ANALYSIS (so far)")
    print("=" * 80)
    
    limit_entry_count = 0
    would_fill_count = 0
    would_expire_count = 0
    
    # Check all backtest log files
    for log_file_path in backtest_log_dir.rglob("*.log"):
        try:
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "[DRY_RUN][LIMIT_ENTRY]" in line:
                        limit_entry_count += 1
                        if "would_fill=true" in line:
                            would_fill_count += 1
                        elif "would_fill=false" in line:
                            would_expire_count += 1
        except Exception:
            continue
    
    print(f"Total limit entry analyses: {limit_entry_count}")
    if limit_entry_count > 0:
        print(f"  Would fill: {would_fill_count} ({would_fill_count/limit_entry_count*100:.1f}%)")
        print(f"  Would expire: {would_expire_count} ({would_expire_count/limit_entry_count*100:.1f}%)")
    
    # Check for trades
    print("\n" + "=" * 80)
    print("TRADE ACTIVITY (so far)")
    print("=" * 80)
    
    trades_taken = 0
    trades_skipped = 0
    
    for log_file_path in backtest_log_dir.rglob("*.log"):
        try:
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "[OK]" in line and "Position" in line and "verified" in line:
                        trades_taken += 1
                    elif "[SKIP]" in line:
                        trades_skipped += 1
        except Exception:
            continue
    
    print(f"Trades taken: {trades_taken}")
    print(f"Trades skipped: {trades_skipped}")
    
    print("\n" + "=" * 80)
    print("To check again, run: python check_equivalence_progress.py")
    print("=" * 80)

if __name__ == "__main__":
    check_progress()

