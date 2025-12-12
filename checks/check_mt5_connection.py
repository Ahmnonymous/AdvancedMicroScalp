#!/usr/bin/env python3
"""Quick script to check MT5 connection and symbol availability."""

import MetaTrader5 as mt5

print("=" * 80)
print("MT5 Connection Diagnostic")
print("=" * 80)

# Initialize MT5
print("\n1. Initializing MT5...")
if not mt5.initialize():
    error = mt5.last_error()
    print(f"   [FAILED] Cannot initialize MT5. Error: {error}")
    exit(1)
else:
    print("   [OK] MT5 initialized successfully")

# Check account info
print("\n2. Checking account connection...")
account_info = mt5.account_info()
if account_info:
    print(f"   [OK] Connected to account: {account_info.login}")
    print(f"   [OK] Server: {account_info.server}")
    print(f"   [OK] Balance: {account_info.balance}")
else:
    print("   [WARNING] MT5 initialized but not logged in")

# Check symbols
print("\n3. Checking symbol availability...")
symbols = mt5.symbols_get()
if symbols:
    print(f"   [OK] Total symbols available: {len(symbols)}")
    
    # Check for EURUSD
    eurusd = mt5.symbol_info("EURUSD")
    if eurusd:
        print(f"   [OK] EURUSD found: {eurusd.name}")
        print(f"   [OK] EURUSD description: {eurusd.description}")
        print(f"   [OK] EURUSD available from: {eurusd.time}")
    else:
        print("   [FAILED] EURUSD not found")
        # Try to find similar symbols
        print("\n   Searching for EUR/USD variants...")
        found = False
        for s in symbols:
            if 'EUR' in s.name and 'USD' in s.name:
                print(f"   Found: {s.name} - {s.description}")
                found = True
        if not found:
            print("   No EUR/USD variants found")
else:
    print("   [FAILED] Cannot get symbols list")

# Test historical data
print("\n4. Testing historical data access...")
if account_info:
    from datetime import datetime, timedelta
    test_end = datetime.now() - timedelta(days=7)
    test_start = test_end - timedelta(hours=1)
    
    if eurusd:
        rates = mt5.copy_rates_range("EURUSD", mt5.TIMEFRAME_M1, test_start, test_end)
        if rates is not None and len(rates) > 0:
            print(f"   [OK] Historical data available: {len(rates)} bars")
            print(f"   [OK] Date range tested: {test_start} to {test_end}")
        else:
            error = mt5.last_error()
            print(f"   [WARNING] No historical data. Error: {error}")
            print(f"   [INFO] Date range tested: {test_start} to {test_end}")
    else:
        print("   [SKIPPED] EURUSD not available")

print("\n" + "=" * 80)
print("Diagnostic Complete")
print("=" * 80)

mt5.shutdown()

