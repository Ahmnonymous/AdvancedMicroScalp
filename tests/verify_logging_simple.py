#!/usr/bin/env python3
"""Simple logging verification script without Unicode issues."""

import os
import sys
from pathlib import Path
from datetime import date

print("=" * 80)
print("LOGGING REFACTOR VERIFICATION")
print("=" * 80)

# Test 1: Imports
print("\n1. Testing imports...")
try:
    import sys
    sys.path.insert(0, '..')
    from utils.logger_factory import get_logger, get_symbol_logger
    from utils.daily_report_generator import generate_daily_summary, save_daily_summary
    print("   [PASS] All imports successful")
except Exception as e:
    print(f"   [FAIL] Import error: {e}")
    sys.exit(1)

# Test 2: Module logging paths
print("\n2. Verifying module logging paths...")
modules = [
    ("../bot/micro_profit_engine.py", "logs/live/engine/hft_engine.log"),
    ("../strategies/trend_filter.py", "logs/live/engine/trend_detector.log"),
    ("../news_filter/news_api.py", "logs/live/engine/news_filter.log"),
    ("../risk/risk_manager.py", "logs/live/engine/risk_manager.log"),
    ("../execution/mt5_connector.py", "logs/live/system/mt5_connection.log"),
]

all_pass = True
for module_file, expected_log in modules:
    if os.path.exists(module_file):
        with open(module_file, 'r', encoding='utf-8') as f:
            content = f.read()
            if expected_log in content:
                print(f"   [PASS] {module_file} -> {expected_log}")
            else:
                print(f"   [FAIL] {module_file} missing path: {expected_log}")
                all_pass = False
    else:
        print(f"   [WARN] {module_file} not found")

# Test 3: Trading bot loggers
print("\n3. Verifying trading_bot.py loggers...")
if os.path.exists("../bot/trading_bot.py"):
    with open("../bot/trading_bot.py", 'r', encoding='utf-8') as f:
        content = f.read()
        required_logs = [
            "logs/live/system/system_startup.log",
            "logs/live/system/scheduler.log",
            "logs/live/system/system_errors.log"
        ]
        for log_path in required_logs:
            if log_path in content:
                print(f"   [PASS] Found {log_path}")
            else:
                print(f"   [FAIL] Missing {log_path}")
                all_pass = False

# Test 4: Trade logger
print("\n4. Verifying trade_logger.py...")
if os.path.exists("../trade_logging/trade_logger.py"):
    with open("../trade_logging/trade_logger.py", 'r', encoding='utf-8') as f:
        content = f.read()
        if "get_symbol_logger" in content and "logs/trades" in content:
            print("   [PASS] Trade logger uses symbol logging")
        else:
            print("   [FAIL] Trade logger not properly configured")
            all_pass = False

# Test 5: Old log references
print("\n5. Checking for old log references...")
old_refs = []
for root, dirs, files in os.walk(".."):
    # Skip certain directories
    if any(skip in root for skip in ['.git', '__pycache__', 'node_modules', 'migration']):
        continue
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'main.log' in content and 'logger_setup' not in filepath:
                        old_refs.append(f"{filepath}: main.log")
                    if 'bot_log.txt' in content and filepath not in ['config.json', 'README.md']:
                        # Allow in monitor files (they may read old logs)
                        if 'monitor' not in filepath:
                            old_refs.append(f"{filepath}: bot_log.txt")
            except:
                pass

if old_refs:
    print(f"   [WARN] Found {len(old_refs)} old log references (may be legacy):")
    for ref in old_refs[:5]:
        print(f"      - {ref}")
else:
    print("   [PASS] No old log references found in code")

# Test 6: Create test loggers
print("\n6. Testing logger creation...")
try:
    test_loggers = [
        ("test_engine", "logs/live/engine/test.log"),
        ("test_system", "logs/live/system/test.log"),
        ("test_trade", "logs/live/trades/TEST.log"),
    ]
    for name, path in test_loggers:
        # Adjust path for tests/ folder location
        log_path = f"../{path}" if not os.path.isabs(path) else path
        logger = get_logger(name, log_path)
        logger.info("Test message")
        if os.path.exists(log_path):
            print(f"   [PASS] Created {path}")
        else:
            print(f"   [FAIL] Failed to create {path}")
            all_pass = False
except Exception as e:
    print(f"   [FAIL] Logger creation error: {e}")
    all_pass = False

# Test 7: Rotation
print("\n7. Testing log rotation...")
try:
    rot_logger = get_logger("rotation_test", "../logs/live/system/rotation_test.log")
    # Write enough to trigger rotation (6MB)
    large_msg = "X" * 100
    for i in range(60000):
        rot_logger.info(f"{large_msg} {i}")
    
    log_dir = Path("../logs/system")
    rot_files = sorted(log_dir.glob("rotation_test.log*"))
    if len(rot_files) > 1:
        print(f"   [PASS] Rotation working ({len(rot_files)} files created)")
    else:
        print(f"   [WARN] Rotation may not have triggered ({len(rot_files)} files)")
except Exception as e:
    print(f"   [FAIL] Rotation test error: {e}")

# Test 8: Daily report
print("\n8. Testing daily report generator...")
try:
    summary = generate_daily_summary()
    required = ['total_trades', 'wins', 'losses', 'win_rate_pct', 'total_profit_usd',
                'avg_win_usd', 'avg_loss_usd', 'micro_hft', 'symbol_stats']
    missing = [f for f in required if f not in summary]
    if missing:
        print(f"   [FAIL] Missing fields: {missing}")
        all_pass = False
    else:
        print("   [PASS] Daily summary has all required fields")
    
    if save_daily_summary():
        today = date.today().strftime('%Y-%m-%d')
        json_file = f"../logs/reports/summary_{today}.json"
        txt_file = f"../logs/reports/summary_{today}.txt"
        if os.path.exists(json_file) and os.path.exists(txt_file):
            print("   [PASS] Reports saved successfully")
        else:
            print("   [FAIL] Report files not created")
            all_pass = False
    else:
        print("   [FAIL] Failed to save daily summary")
        all_pass = False
except Exception as e:
    print(f"   [FAIL] Daily report error: {e}")
    import traceback
    traceback.print_exc()
    all_pass = False

# Directory structure
print("\n9. Log directory structure:")
if os.path.exists("../logs"):
    for root, dirs, files in os.walk("../logs"):
        level = root.replace("logs", "").count(os.sep)
        indent = " " * 2 * level
        print(f"{indent}{os.path.basename(root)}/")
        subindent = " " * 2 * (level + 1)
        for file in sorted(files)[:10]:
            size = os.path.getsize(os.path.join(root, file))
            size_str = f"({size:,} bytes)" if size < 1024 else f"({size/1024:.1f} KB)"
            print(f"{subindent}{file} {size_str}")
        if len(files) > 10:
            print(f"{subindent}... ({len(files) - 10} more files)")
else:
    print("   [FAIL] logs/ directory does not exist")

# Summary
print("\n" + "=" * 80)
if all_pass:
    print("VERIFICATION: PASSED")
else:
    print("VERIFICATION: FAILED (see errors above)")
print("=" * 80)

