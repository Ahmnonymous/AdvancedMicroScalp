#!/usr/bin/env python3
"""
Parallel System Runner
Runs launch system (live trading) and comprehensive backtest in parallel safely.
"""

import os
import sys
import subprocess
import time
import signal
import json
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def check_mt5_connection():
    """Check if MT5 is available and connected."""
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            info = mt5.terminal_info()
            if info:
                print(f"MT5 Terminal: {info.name} | Connected: {mt5.terminal_info() is not None}")
                mt5.shutdown()
                return True
        return False
    except Exception as e:
        print(f"Error checking MT5: {e}")
        return False

def run_parallel():
    """Run launch system and backtest in parallel."""
    print("=" * 80)
    print("PARALLEL SYSTEM RUNNER")
    print("=" * 80)
    print("\nThis will run:")
    print("  1. Launch System (Live Trading Bot)")
    print("  2. Comprehensive Backtest (All Symbols, All Scenarios)")
    print("\nIMPORTANT NOTES:")
    print("  - Backtest loads historical data from MT5, then shuts down MT5 connection")
    print("  - Launch system uses MT5 for live trading (stays connected)")
    print("  - They use separate log folders:")
    print("    * Live bot: logs/live/ (system/, engine/, trades/, monitor/)")
    print("    * Backtest: logs/backtest/")
    print("  - Both read config.json (read-only, no conflicts)")
    print("  - MT5 can handle multiple connections from same process")
    print("\n" + "=" * 80)
    
    # Check MT5 connection
    print("\n[CHECK] Verifying MT5 connection...")
    if not check_mt5_connection():
        print("[ERROR] MT5 terminal is not running or not connected.")
        print("Please start MT5 terminal and login before running this script.")
        return False
    
    print("[OK] MT5 connection verified")
    
    # Load config to check mode
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    if config.get('mode') != 'live':
        print(f"[WARNING] Config mode is '{config.get('mode')}', but launch system expects 'live' mode.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            return False
    
    print("\n[STRATEGY] Starting systems with MT5 connection sequencing:")
    print("  1. Launch system will initialize MT5 first (for live trading)")
    print("  2. Backtest will initialize MT5 after launch system (for data download)")
    print("  3. Backtest will shutdown MT5 after loading data")
    print("  4. Both systems then run independently")
    
    # Start launch system first (it needs MT5 for live trading)
    print("\n[START] Starting Launch System (Live Trading)...")
    launch_process = subprocess.Popen(
        [sys.executable, "launch_system.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    print(f"[OK] Launch system started (PID: {launch_process.pid})")
    
    # Wait for launch system to initialize MT5 connection
    print("[WAIT] Waiting 10 seconds for launch system to initialize MT5...")
    time.sleep(10)
    
    # Start backtest (it will initialize its own MT5 connection for data download)
    print("\n[START] Starting Comprehensive Backtest...")
    print("[NOTE] Backtest will download data from MT5, then disconnect from MT5")
    backtest_process = subprocess.Popen(
        [sys.executable, "backtest/run_comprehensive_test_single_symbol.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    print(f"[OK] Backtest started (PID: {backtest_process.pid})")
    
    print("\n" + "=" * 80)
    print("BOTH SYSTEMS RUNNING IN PARALLEL")
    print("=" * 80)
    print(f"Launch System PID: {launch_process.pid}")
    print(f"Backtest PID: {backtest_process.pid}")
    print("\nPress Ctrl+C to stop both systems")
    print("=" * 80)
    
    # Monitor both processes
    try:
        print("\n[MONITORING] Monitoring both processes...")
        print("  - Launch system: Live trading (runs continuously)")
        print("  - Backtest: Will complete when all scenarios finish")
        print("  - Press Ctrl+C to stop both systems\n")
        
        while True:
            # Check if processes are still running
            launch_status = launch_process.poll()
            backtest_status = backtest_process.poll()
            
            if launch_status is not None:
                print(f"\n[STOPPED] Launch system exited with code {launch_status}")
                if launch_status != 0:
                    try:
                        stdout, stderr = launch_process.communicate(timeout=1)
                        if stdout:
                            print(f"STDOUT (last 500 chars):\n{stdout[-500:]}")
                        if stderr:
                            print(f"STDERR (last 500 chars):\n{stderr[-500:]}")
                    except subprocess.TimeoutExpired:
                        pass
            
            if backtest_status is not None:
                print(f"\n[STOPPED] Backtest exited with code {backtest_status}")
                if backtest_status != 0:
                    try:
                        stdout, stderr = backtest_process.communicate(timeout=1)
                        if stdout:
                            print(f"STDOUT (last 500 chars):\n{stdout[-500:]}")
                        if stderr:
                            print(f"STDERR (last 500 chars):\n{stderr[-500:]}")
                    except subprocess.TimeoutExpired:
                        pass
                else:
                    print("[SUCCESS] Backtest completed successfully!")
            
            if launch_status is not None and backtest_status is not None:
                print("\n[COMPLETE] Both processes have finished")
                break
            
            # Print status every 30 seconds
            if int(time.time()) % 30 == 0:
                launch_running = "RUNNING" if launch_status is None else "STOPPED"
                backtest_running = "RUNNING" if backtest_status is None else "STOPPED"
                print(f"[STATUS] Launch: {launch_running} | Backtest: {backtest_running}")
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n[INTERRUPT] Stopping both systems...")
        
        # Stop launch system
        if launch_process.poll() is None:
            print("[STOP] Stopping launch system...")
            launch_process.terminate()
            try:
                launch_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                launch_process.kill()
        
        # Stop backtest
        if backtest_process.poll() is None:
            print("[STOP] Stopping backtest...")
            backtest_process.terminate()
            try:
                backtest_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backtest_process.kill()
        
        print("[DONE] Both systems stopped")
    
    return True

if __name__ == "__main__":
    try:
        success = run_parallel()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

