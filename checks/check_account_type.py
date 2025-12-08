#!/usr/bin/env python3
"""Check if account is Islamic/swap-free and can trade mode 4 symbols."""

import MetaTrader5 as mt5
import json

with open('config.json', 'r') as f:
    config = json.load(f)

mt5.initialize()
mt5.login(
    login=int(config['mt5']['account']),
    password=config['mt5']['password'],
    server=config['mt5']['server']
)

account = mt5.account_info()
if account:
    print("\nAccount Information:")
    print("=" * 60)
    print(f"Account: {account.login}")
    print(f"Server: {account.server}")
    print(f"Balance: {account.balance}")
    print(f"Currency: {account.currency}")
    print(f"Trade Allowed: {account.trade_allowed}")
    print(f"Trade Expert: {account.trade_expert}")
    print(f"Margin Mode: {account.margin_mode}")
    print(f"Leverage: {account.leverage}")

# Check mode 4 symbols that might work
symbols = mt5.symbols_get()
mode4_symbols = [s for s in symbols if s.trade_mode == 4]

print(f"\nMode 4 (Full Trading) Symbols: {len(mode4_symbols)}")
print("Sample:", [s.name for s in mode4_symbols[:10]])

# Test a mode 4 symbol
test_symbol = 'BTCUSDm' if 'BTCUSDm' in [s.name for s in mode4_symbols] else mode4_symbols[0].name if mode4_symbols else None

if test_symbol:
    symbol_info = mt5.symbol_info(test_symbol)
    if symbol_info:
        print(f"\nTesting {test_symbol}:")
        print(f"  Trade Mode: {symbol_info.trade_mode}")
        print(f"  Swap Mode: {symbol_info.swap_mode}")
        print(f"  Swap Long: {symbol_info.swap_long}")
        print(f"  Swap Short: {symbol_info.swap_short}")
        
        # Check filling mode
        filling_modes = symbol_info.filling_mode
        print(f"  Filling Modes: {filling_modes} (bitmask)")
        if filling_modes & 4:
            print("    - Supports RETURN")
        if filling_modes & 1:
            print("    - Supports FOK")
        if filling_modes & 2:
            print("    - Supports IOC")
        
        # Try order with RETURN
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": test_symbol,
            "volume": symbol_info.volume_min,
            "type": mt5.ORDER_TYPE_BUY,
            "price": symbol_info.ask,
            "deviation": 20,
            "magic": 234000,
            "comment": "Test",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        
        result = mt5.order_send(request)
        if result:
            print(f"  Order Test: {result.retcode} - {result.comment}")
        else:
            error = mt5.last_error()
            print(f"  Order Test Failed: {error}")

mt5.shutdown()

