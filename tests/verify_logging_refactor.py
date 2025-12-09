#!/usr/bin/env python3
"""
Logging Refactor Verification Script
Tests all aspects of the new logging system.
"""

import os
import sys
import json
import time
from datetime import date, datetime
from pathlib import Path

# Test imports
print("=" * 80)
print("VERIFICATION 1: Import Tests")
print("=" * 80)

try:
    from utils.logger_factory import get_logger, get_symbol_logger
    print("[OK] utils.logger_factory imported successfully")
except ImportError as e:
    print(f"[FAIL] Failed to import utils.logger_factory: {e}")
    sys.exit(1)

try:
    from utils.daily_report_generator import generate_daily_summary, save_daily_summary
    print("[OK] utils.daily_report_generator imported successfully")
except ImportError as e:
    print(f"[FAIL] Failed to import utils.daily_report_generator: {e}")
    sys.exit(1)

# Test logger factory
print("\n" + "=" * 80)
print("VERIFICATION 2: Logger Factory Tests")
print("=" * 80)

# Test 1: Create engine logger
try:
    engine_logger = get_logger("test_hft_engine", "logs/engine/test_hft_engine.log")
    engine_logger.info("Test log entry for HFT engine")
    print("[OK] Engine logger created: logs/engine/test_hft_engine.log")
    assert os.path.exists("logs/engine/test_hft_engine.log"), "Log file not created"
    print("[OK] Log file exists")
except Exception as e:
    print(f"❌ Engine logger test failed: {e}")

# Test 2: Create trend detector logger
try:
    trend_logger = get_logger("test_trend_detector", "logs/engine/test_trend_detector.log")
    trend_logger.info("Test log entry for trend detector")
    print("✅ Trend detector logger created: logs/engine/test_trend_detector.log")
except Exception as e:
    print(f"❌ Trend detector logger test failed: {e}")

# Test 3: Create news filter logger
try:
    news_logger = get_logger("test_news_filter", "logs/engine/test_news_filter.log")
    news_logger.info("Test log entry for news filter")
    print("✅ News filter logger created: logs/engine/test_news_filter.log")
except Exception as e:
    print(f"❌ News filter logger test failed: {e}")

# Test 4: Create risk manager logger
try:
    risk_logger = get_logger("test_risk_manager", "logs/engine/test_risk_manager.log")
    risk_logger.info("Test log entry for risk manager")
    print("✅ Risk manager logger created: logs/engine/test_risk_manager.log")
except Exception as e:
    print(f"❌ Risk manager logger test failed: {e}")

# Test 5: Create MT5 connection logger
try:
    mt5_logger = get_logger("test_mt5_connection", "logs/system/test_mt5_connection.log")
    mt5_logger.info("Test log entry for MT5 connection")
    print("✅ MT5 connection logger created: logs/system/test_mt5_connection.log")
except Exception as e:
    print(f"❌ MT5 connection logger test failed: {e}")

# Test 6: Create system loggers
try:
    startup_logger = get_logger("test_system_startup", "logs/system/test_system_startup.log")
    startup_logger.info("Test log entry for system startup")
    print("✅ System startup logger created: logs/system/test_system_startup.log")
    
    scheduler_logger = get_logger("test_scheduler", "logs/system/test_scheduler.log")
    scheduler_logger.info("Test log entry for scheduler")
    print("✅ Scheduler logger created: logs/system/test_scheduler.log")
    
    error_logger = get_logger("test_system_errors", "logs/system/test_system_errors.log")
    error_logger.error("Test error entry")
    print("✅ System errors logger created: logs/system/test_system_errors.log")
except Exception as e:
    print(f"❌ System logger test failed: {e}")

# Test 7: Create symbol logger
try:
    symbol_logger = get_symbol_logger("EURUSD")
    symbol_logger.info("Test trade execution for EURUSD")
    symbol_logger.info("✅ TRADE EXECUTED SUCCESSFULLY")
    symbol_logger.info("   Symbol: EURUSD")
    symbol_logger.info("   Ticket: 12345")
    print("✅ Symbol logger created: logs/trades/EURUSD.log")
    assert os.path.exists("logs/trades/EURUSD.log"), "Symbol log file not created"
    print("✅ Symbol log file exists")
except Exception as e:
    print(f"❌ Symbol logger test failed: {e}")

# Test 8: Test multiple symbols
try:
    for symbol in ["GBPUSD", "XAUUSD", "BTCUSD"]:
        sym_logger = get_symbol_logger(symbol)
        sym_logger.info(f"Test entry for {symbol}")
    print("✅ Multiple symbol loggers created successfully")
except Exception as e:
    print(f"❌ Multiple symbol logger test failed: {e}")

# Test rotation
print("\n" + "=" * 80)
print("VERIFICATION 3: Log Rotation Test")
print("=" * 80)

try:
    # Create a logger and write >6MB to test rotation
    rotation_logger = get_logger("rotation_test", "logs/system/rotation_test.log")
    
    # Write 6.5MB of data (each line ~100 bytes, need ~65,000 lines)
    print("Writing 6.5MB to test rotation...")
    large_message = "X" * 100  # 100 bytes per line
    for i in range(65000):
        rotation_logger.info(f"{large_message} - Line {i}")
    
    # Check for rotated files
    log_dir = Path("logs/system")
    rotation_files = list(log_dir.glob("rotation_test.log*"))
    print(f"✅ Rotation test completed. Found {len(rotation_files)} log files:")
    for f in sorted(rotation_files):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"   - {f.name}: {size_mb:.2f} MB")
    
    if len(rotation_files) > 1:
        print("✅ Log rotation working correctly")
    else:
        print("⚠️  Rotation may not have triggered (file may not have reached 5MB threshold)")
except Exception as e:
    print(f"❌ Rotation test failed: {e}")
    import traceback
    traceback.print_exc()

# Test daily report generator
print("\n" + "=" * 80)
print("VERIFICATION 4: Daily Report Generator Test")
print("=" * 80)

try:
    # Create some test trade logs
    test_symbols = ["EURUSD", "GBPUSD"]
    for idx, symbol in enumerate(test_symbols):
        sym_log = get_symbol_logger(symbol)
        today = date.today().strftime('%Y-%m-%d')
        
        # Simulate trade execution
        sym_log.info(f"{today} 10:00:00 - ✅ TRADE EXECUTED SUCCESSFULLY")
        sym_log.info(f"{today} 10:00:00 -    Symbol: {symbol}")
        sym_log.info(f"{today} 10:00:00 -    Ticket: 1000{idx}")
        
        # Simulate position closure
        profit = 0.05 if symbol == "EURUSD" else -0.02
        sym_log.info(f"{today} 10:05:00 - 🔴 POSITION CLOSED")
        sym_log.info(f"{today} 10:05:00 -    Profit/Loss: ${profit:.2f} USD")
        close_reason = "Take Profit" if profit > 0 else "Stop Loss"
        sym_log.info(f"{today} 10:05:00 -    Close Reason: {close_reason}")
    
    # Generate report
    summary = generate_daily_summary()
    print("✅ Daily summary generated")
    print(f"   Date: {summary.get('date')}")
    print(f"   Total Trades: {summary.get('total_trades')}")
    print(f"   Wins: {summary.get('wins')}")
    print(f"   Losses: {summary.get('losses')}")
    print(f"   Win Rate: {summary.get('win_rate_pct')}%")
    
    # Check required fields
    required_fields = [
        'total_trades', 'wins', 'losses', 'win_rate_pct', 
        'total_profit_usd', 'avg_win_usd', 'avg_loss_usd',
        'largest_loss_usd', 'micro_hft', 'errors', 'warnings', 'symbol_stats'
    ]
    missing_fields = [f for f in required_fields if f not in summary]
    if missing_fields:
        print(f"❌ Missing fields in summary: {missing_fields}")
    else:
        print("✅ All required fields present in summary")
    
    # Save report
    if save_daily_summary():
        reports_dir = Path("logs/reports")
        today_str = date.today().strftime('%Y-%m-%d')
        json_file = reports_dir / f"summary_{today_str}.json"
        txt_file = reports_dir / f"summary_{today_str}.txt"
        
        if json_file.exists():
            print(f"✅ JSON report saved: {json_file}")
        else:
            print(f"❌ JSON report not found: {json_file}")
        
        if txt_file.exists():
            print(f"✅ TXT report saved: {txt_file}")
        else:
            print(f"❌ TXT report not found: {txt_file}")
    else:
        print("❌ Failed to save daily summary")
        
except Exception as e:
    print(f"❌ Daily report test failed: {e}")
    import traceback
    traceback.print_exc()

# Directory tree
print("\n" + "=" * 80)
print("VERIFICATION 5: Directory Structure")
print("=" * 80)

def print_tree(directory, prefix="", max_depth=3, current_depth=0):
    """Print directory tree structure."""
    if current_depth >= max_depth:
        return
    
    try:
        items = sorted(Path(directory).iterdir())
        dirs = [item for item in items if item.is_dir()]
        files = [item for item in items if item.is_file()]
        
        for item in dirs:
            print(f"{prefix}├── {item.name}/")
            print_tree(item, prefix + "│   ", max_depth, current_depth + 1)
        
        for item in files[:20]:  # Limit to first 20 files
            size = item.stat().st_size
            size_str = f"({size:,} bytes)" if size < 1024 else f"({size/1024:.1f} KB)" if size < 1024*1024 else f"({size/(1024*1024):.1f} MB)"
            print(f"{prefix}├── {item.name} {size_str}")
        
        if len(files) > 20:
            print(f"{prefix}└── ... ({len(files) - 20} more files)")
    except Exception as e:
        print(f"{prefix}❌ Error reading {directory}: {e}")

if os.path.exists("logs"):
    print("logs/")
    print_tree("logs", "", max_depth=3)
else:
    print("❌ logs/ directory does not exist")

# Check for old log files
print("\n" + "=" * 80)
print("VERIFICATION 6: Old Log File Check")
print("=" * 80)

old_files = []
if os.path.exists("bot_log.txt"):
    old_files.append("bot_log.txt")
if os.path.exists("main.log"):
    old_files.append("main.log")

if old_files:
    print(f"⚠️  Found old log files (may be legacy): {old_files}")
else:
    print("✅ No old log files (bot_log.txt, main.log) found in root")

# Summary
print("\n" + "=" * 80)
print("VERIFICATION SUMMARY")
print("=" * 80)
print("All verification tests completed.")
print("Check output above for any ❌ errors.")

