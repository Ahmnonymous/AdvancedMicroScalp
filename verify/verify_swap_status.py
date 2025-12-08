#!/usr/bin/env python3
"""Verify swap status of specific symbols."""

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

# Check major pairs
test_symbols = ['EURUSDm', 'GBPUSDm', 'USDJPYm', 'XAUUSDm', 'AUDUSDm']

print("\nSwap Status Check:")
print("=" * 60)
for symbol_name in test_symbols:
    symbol = mt5.symbol_info(symbol_name)
    if symbol:
        print(f"{symbol_name}:")
        print(f"  Swap Mode: {symbol.swap_mode} (0=disabled/swap-free)")
        print(f"  Swap Long: {symbol.swap_long}")
        print(f"  Swap Short: {symbol.swap_short}")
        print(f"  Is Swap-Free: {symbol.swap_mode == 0 or (symbol.swap_long == 0 and symbol.swap_short == 0)}")
    else:
        print(f"{symbol_name}: Not found")

# Check account swap-free status
account = mt5.account_info()
if account:
    print(f"\nAccount Info:")
    print(f"  Account: {account.login}")
    print(f"  Balance: {account.balance}")
    print(f"  Server: {account.server}")

mt5.shutdown()

