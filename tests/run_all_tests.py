#!/usr/bin/env python3
"""
Test Runner for Trading Bot
Runs all local simulation tests (no MT5 connection required).
"""

import sys
import os
import traceback
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_test_module(module_name):
    """Run a test module and return results."""
    print(f"\n{'=' * 80}")
    print(f"Running: {module_name}")
    print('=' * 80)
    
    try:
        if module_name == 'test_trailing_behavior':
            from tests.test_trailing_behavior import test_elastic_trailing_sequence
            test_elastic_trailing_sequence()
            return True, None
        elif module_name == 'test_staged_open':
            from tests.test_staged_open import test_staged_open_basic, test_staged_open_window_expiry
            test_staged_open_basic()
            test_staged_open_window_expiry()
            return True, None
        else:
            return False, f"Unknown test module: {module_name}"
    except AssertionError as e:
        return False, f"Assertion failed: {e}"
    except Exception as e:
        return False, f"Exception: {e}\n{traceback.format_exc()}"


def main():
    """Run all tests."""
    print("=" * 80)
    print("TRADING BOT TEST SUITE")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    test_modules = [
        'test_trailing_behavior',
        'test_staged_open'
    ]
    
    results = []
    passed = 0
    failed = 0
    
    for module in test_modules:
        success, error = run_test_module(module)
        results.append({
            'module': module,
            'success': success,
            'error': error
        })
        
        if success:
            passed += 1
            print(f"\n✅ {module}: PASSED")
        else:
            failed += 1
            print(f"\n❌ {module}: FAILED")
            if error:
                print(f"   Error: {error}")
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Total tests: {len(test_modules)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    if failed > 0:
        print("\n❌ SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("\n✅ ALL TESTS PASSED")
        sys.exit(0)


if __name__ == '__main__':
    main()

