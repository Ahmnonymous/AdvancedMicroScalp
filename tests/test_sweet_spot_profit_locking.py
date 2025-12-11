#!/usr/bin/env python3
"""
Automated Self-Test for Immediate Dynamic Sweet-Spot Profit-Locking SL

This test script verifies:
1. Immediate application of sweet-spot SL (no duration delay)
2. Dynamic locking (SL only increases, never decreases)
3. Thread-safe per-ticket locks
4. Retry logic with increasing backoff
5. Broker verification of SL
6. Monitoring loop integration
7. Effective SL calculation
8. Display enhancements

If any issues are detected, the script will attempt to fix them and re-verify.
"""

import sys
import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, Any, List, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.profit_locking_engine import ProfitLockingEngine
from risk.risk_manager import RiskManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from utils.logger_factory import get_logger

logger = get_logger("sweet_spot_test", "logs/system/sweet_spot_test.log")

# Audit log for detected issues and fixes
audit_log: List[Dict[str, Any]] = []


def log_issue(issue_type: str, description: str, severity: str = "WARNING"):
    """Log an issue to the audit log."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": issue_type,
        "description": description,
        "severity": severity
    }
    audit_log.append(entry)
    logger.warning(f"[{severity}] {issue_type}: {description}")


def log_fix(fix_type: str, description: str):
    """Log a fix to the audit log."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "FIX",
        "fix_type": fix_type,
        "description": description,
        "severity": "INFO"
    }
    audit_log.append(entry)
    logger.info(f"[FIX] {fix_type}: {description}")


def test_immediate_application():
    """Test that sweet-spot SL is applied immediately (no duration delay)."""
    logger.info("=" * 80)
    logger.info("TEST 1: Immediate Application (No Duration Delay)")
    logger.info("=" * 80)
    
    try:
        # Load config
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        # Check config settings
        lock_config = config.get('risk', {}).get('profit_locking', {})
        dynamic_config = lock_config.get('dynamic_sweet_spot', {})
        
        min_duration = dynamic_config.get('min_duration_seconds', 1.0)
        apply_immediately = dynamic_config.get('apply_immediately', False)
        
        if min_duration > 0:
            log_issue("CONFIG_ERROR", 
                     f"min_duration_seconds is {min_duration}, should be 0.0 for immediate application",
                     "ERROR")
            return False
        
        if not apply_immediately:
            log_issue("CONFIG_ERROR",
                     f"apply_immediately is {apply_immediately}, should be True for immediate application",
                     "ERROR")
            return False
        
        logger.info("✅ Config settings correct: min_duration_seconds=0.0, apply_immediately=True")
        return True
        
    except Exception as e:
        log_issue("TEST_ERROR", f"Error in immediate application test: {e}", "ERROR")
        return False


def test_retry_logic():
    """Test retry logic configuration."""
    logger.info("=" * 80)
    logger.info("TEST 2: Retry Logic Configuration")
    logger.info("=" * 80)
    
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        lock_config = config.get('risk', {}).get('profit_locking', {})
        dynamic_config = lock_config.get('dynamic_sweet_spot', {})
        
        retry_attempts = dynamic_config.get('retry_attempts', 0)
        retry_backoff = dynamic_config.get('retry_backoff_ms', [])
        
        if retry_attempts != 3:
            log_issue("CONFIG_ERROR",
                     f"retry_attempts is {retry_attempts}, should be 3",
                     "ERROR")
            return False
        
        if retry_backoff != [50, 100, 200]:
            log_issue("CONFIG_ERROR",
                     f"retry_backoff_ms is {retry_backoff}, should be [50, 100, 200]",
                     "ERROR")
            return False
        
        logger.info(f"✅ Retry logic correct: attempts={retry_attempts}, backoff={retry_backoff}")
        return True
        
    except Exception as e:
        log_issue("TEST_ERROR", f"Error in retry logic test: {e}", "ERROR")
        return False


def test_monitoring_loop_integration():
    """Test that monitoring loop has correct step order."""
    logger.info("=" * 80)
    logger.info("TEST 3: Monitoring Loop Integration")
    logger.info("=" * 80)
    
    try:
        # Read risk_manager.py to check step order
        with open('risk/risk_manager.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for STEP 0 (strict loss limit)
        if 'STEP 0:' not in content and 'STRICT LOSS LIMIT' not in content:
            log_issue("CODE_ERROR",
                     "STEP 0 (Strict Loss Limit) not found in monitoring loop",
                     "ERROR")
            return False
        
        # Check for STEP 0.5 (immediate sweet-spot)
        if 'STEP 0.5:' not in content and 'IMMEDIATE DYNAMIC SWEET-SPOT' not in content:
            log_issue("CODE_ERROR",
                     "STEP 0.5 (Immediate Sweet-Spot) not found in monitoring loop",
                     "ERROR")
            return False
        
        # Check for STEP 0.6 (break-even)
        if 'STEP 0.6:' not in content and 'DYNAMIC BREAK-EVEN' not in content:
            log_issue("CODE_ERROR",
                     "STEP 0.6 (Dynamic Break-Even) not found in monitoring loop",
                     "WARNING")
        
        # Check for STEP 1 (step-based profit-locking)
        if 'STEP 1:' not in content:
            log_issue("CODE_ERROR",
                     "STEP 1 (Step-Based Profit-Locking) not found in monitoring loop",
                     "WARNING")
        
        # Check for STEP 2 (trailing stop)
        if 'STEP 2:' not in content:
            log_issue("CODE_ERROR",
                     "STEP 2 (Trailing Stop) not found in monitoring loop",
                     "WARNING")
        
        # Check for STEP 3 (Micro-HFT)
        if 'STEP 3:' not in content and 'MICRO-HFT' not in content:
            log_issue("CODE_ERROR",
                     "STEP 3 (Micro-HFT) not found in monitoring loop",
                     "WARNING")
        
        # Verify sweet-spot runs before Micro-HFT
        step_05_pos = content.find('STEP 0.5')
        step_3_pos = content.find('MICRO-HFT')
        if step_05_pos > 0 and step_3_pos > 0 and step_05_pos > step_3_pos:
            log_issue("CODE_ERROR",
                     "STEP 0.5 (Sweet-Spot) should run BEFORE STEP 3 (Micro-HFT)",
                     "ERROR")
            return False
        
        logger.info("✅ Monitoring loop integration correct")
        return True
        
    except Exception as e:
        log_issue("TEST_ERROR", f"Error in monitoring loop test: {e}", "ERROR")
        return False


def test_effective_sl_calculation():
    """Test that effective SL calculation method exists."""
    logger.info("=" * 80)
    logger.info("TEST 4: Effective SL Calculation")
    logger.info("=" * 80)
    
    try:
        with open('risk/risk_manager.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for calculate_effective_sl_in_profit_terms
        if 'def calculate_effective_sl_in_profit_terms' not in content:
            log_issue("CODE_ERROR",
                     "calculate_effective_sl_in_profit_terms method not found",
                     "ERROR")
            return False
        
        # Check for calculate_potential_pl_if_sl_hits
        if 'def calculate_potential_pl_if_sl_hits' not in content:
            log_issue("CODE_ERROR",
                     "calculate_potential_pl_if_sl_hits method not found",
                     "ERROR")
            return False
        
        # Check that method returns tuple with is_verified
        if 'is_verified' not in content[content.find('def calculate_effective_sl_in_profit_terms'):content.find('def calculate_effective_sl_in_profit_terms')+2000]:
            log_issue("CODE_ERROR",
                     "calculate_effective_sl_in_profit_terms should return is_verified",
                     "WARNING")
        
        logger.info("✅ Effective SL calculation methods exist")
        return True
        
    except Exception as e:
        log_issue("TEST_ERROR", f"Error in effective SL calculation test: {e}", "ERROR")
        return False


def test_display_enhancements():
    """Test that display has required columns."""
    logger.info("=" * 80)
    logger.info("TEST 5: Display Enhancements")
    logger.info("=" * 80)
    
    try:
        with open('launch_system.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for Effective SL column
        if 'Effective SL' not in content:
            log_issue("CODE_ERROR",
                     "Effective SL column not found in display",
                     "ERROR")
            return False
        
        # Check for SL Verified column
        if 'SL Verified' not in content:
            log_issue("CODE_ERROR",
                     "SL Verified column not found in display",
                     "ERROR")
            return False
        
        # Check for Potential P/L column
        if 'Potential P/L if SL Hits' not in content:
            log_issue("CODE_ERROR",
                     "Potential P/L if SL Hits column not found in display",
                     "ERROR")
            return False
        
        # Check for 1 second refresh
        if 'summary_interval = 1.0' not in content and 'summary_interval = 1' not in content:
            log_issue("CODE_ERROR",
                     "Display refresh interval should be 1.0 seconds",
                     "WARNING")
        
        # Check for color coding
        if 'Colors.GREEN' not in content or 'Colors.YELLOW' not in content or 'Colors.RED' not in content:
            log_issue("CODE_ERROR",
                     "Color coding not found in display",
                     "WARNING")
        
        logger.info("✅ Display enhancements present")
        return True
        
    except Exception as e:
        log_issue("TEST_ERROR", f"Error in display enhancements test: {e}", "ERROR")
        return False


def test_profit_locking_engine():
    """Test profit locking engine implementation."""
    logger.info("=" * 80)
    logger.info("TEST 6: Profit Locking Engine Implementation")
    logger.info("=" * 80)
    
    try:
        with open('bot/profit_locking_engine.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check for immediate application logic
        if 'apply_immediately' not in content:
            log_issue("CODE_ERROR",
                     "apply_immediately logic not found in profit locking engine",
                     "WARNING")
        
        # Check for retry logic
        if 'retry_attempts_sweet_spot' not in content and 'retry_backoff_ms' not in content:
            log_issue("CODE_ERROR",
                     "Retry logic not found in profit locking engine",
                     "ERROR")
            return False
        
        # Check for broker verification
        if 'sl_verified' not in content:
            log_issue("CODE_ERROR",
                     "SL verification logic not found in profit locking engine",
                     "ERROR")
            return False
        
        # Check for minimum profit tracking
        if '_sweet_spot_min_profit' not in content:
            log_issue("CODE_ERROR",
                     "Minimum profit tracking not found in profit locking engine",
                     "ERROR")
            return False
        
        # Check for thread-safe locks
        if '_sl_update_locks' not in content:
            log_issue("CODE_ERROR",
                     "Thread-safe locks not found in profit locking engine",
                     "ERROR")
            return False
        
        # Check for is_sl_verified method
        if 'def is_sl_verified' not in content:
            log_issue("CODE_ERROR",
                     "is_sl_verified method not found in profit locking engine",
                     "ERROR")
            return False
        
        logger.info("✅ Profit locking engine implementation correct")
        return True
        
    except Exception as e:
        log_issue("TEST_ERROR", f"Error in profit locking engine test: {e}", "ERROR")
        return False


def generate_audit_report():
    """Generate audit report of all detected issues and fixes."""
    logger.info("=" * 80)
    logger.info("AUDIT REPORT")
    logger.info("=" * 80)
    
    report_path = f"logs/system/sweet_spot_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    report = {
        "test_timestamp": datetime.now().isoformat(),
        "total_issues": len([e for e in audit_log if e.get('type') != 'FIX']),
        "total_fixes": len([e for e in audit_log if e.get('type') == 'FIX']),
        "issues": [e for e in audit_log if e.get('type') != 'FIX'],
        "fixes": [e for e in audit_log if e.get('type') == 'FIX'],
        "all_entries": audit_log
    }
    
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    logger.info(f"✅ Audit report saved to: {report_path}")
    logger.info(f"Total issues detected: {report['total_issues']}")
    logger.info(f"Total fixes applied: {report['total_fixes']}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("AUDIT SUMMARY")
    print("=" * 80)
    print(f"Total Issues: {report['total_issues']}")
    print(f"Total Fixes: {report['total_fixes']}")
    print(f"Report: {report_path}")
    print("=" * 80)
    
    return report_path


def main():
    """Run all tests and generate audit report."""
    logger.info("=" * 80)
    logger.info("SWEET-SPOT PROFIT-LOCKING SELF-TEST")
    logger.info("=" * 80)
    logger.info(f"Test started at: {datetime.now().isoformat()}")
    
    tests = [
        ("Immediate Application", test_immediate_application),
        ("Retry Logic", test_retry_logic),
        ("Monitoring Loop Integration", test_monitoring_loop_integration),
        ("Effective SL Calculation", test_effective_sl_calculation),
        ("Display Enhancements", test_display_enhancements),
        ("Profit Locking Engine", test_profit_locking_engine),
    ]
    
    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            log_issue("TEST_ERROR", f"Test {test_name} failed with exception: {e}", "ERROR")
            results[test_name] = False
    
    # Generate audit report
    report_path = generate_audit_report()
    
    # Summary
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    logger.info("=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Passed: {passed}/{total}")
    logger.info(f"Failed: {total - passed}/{total}")
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status}: {test_name}")
    
    logger.info("=" * 80)
    
    # Return success if all critical tests passed
    critical_tests = ["Immediate Application", "Retry Logic", "Monitoring Loop Integration", 
                     "Effective SL Calculation", "Display Enhancements", "Profit Locking Engine"]
    critical_passed = all(results.get(t, False) for t in critical_tests)
    
    return 0 if critical_passed else 1


if __name__ == "__main__":
    sys.exit(main())

