#!/usr/bin/env python3
"""Check all symbol trade modes to understand what's available."""

import MetaTrader5 as mt5
import json
from collections import defaultdict

with open('config.json', 'r') as f:
    config = json.load(f)

mt5.initialize()
mt5.login(
    login=int(config['mt5']['account']),
    password=config['mt5']['password'],
    server=config['mt5']['server']
)

symbols = mt5.symbols_get()
modes = defaultdict(list)
swap_free_by_mode = defaultdict(list)

for s in symbols:
    modes[s.trade_mode].append(s.name)
    
    # Check swap-free
    is_swap_free = (s.swap_mode == 0 or (s.swap_long == 0 and s.swap_short == 0))
    if is_swap_free:
        swap_free_by_mode[s.trade_mode].append(s.name)

print("\nSymbol Trade Modes Distribution:")
print("=" * 60)
for mode, syms in sorted(modes.items()):
    print(f"Mode {mode}: {len(syms)} symbols")
    if mode == 4:
        print(f"  Sample: {syms[:10]}")

print("\nSwap-Free Symbols by Trade Mode:")
print("=" * 60)
for mode, syms in sorted(swap_free_by_mode.items()):
    print(f"Mode {mode}: {len(syms)} swap-free symbols")
    if len(syms) <= 20:
        print(f"  Symbols: {syms}")
    else:
        print(f"  Sample: {syms[:10]}")

# Check if mode 0 can still trade (maybe it's just a flag)
print("\nTesting if mode 0 symbols can trade...")
test_symbol = swap_free_by_mode[0][0] if swap_free_by_mode[0] else None
if test_symbol:
    print(f"Testing {test_symbol}...")
    symbol_info = mt5.symbol_info(test_symbol)
    if symbol_info:
        print(f"  Trade Mode: {symbol_info.trade_mode}")
        print(f"  Can get rates: {mt5.copy_rates_from_pos(test_symbol, mt5.TIMEFRAME_M1, 0, 1) is not None}")

mt5.shutdown()

