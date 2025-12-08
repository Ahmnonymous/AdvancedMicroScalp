#!/usr/bin/env python3
"""Quick script to check available symbols on Exness account."""

import MetaTrader5 as mt5
import json

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

# Initialize and login
mt5.initialize()
mt5.login(
    login=int(config['mt5']['account']),
    password=config['mt5']['password'],
    server=config['mt5']['server']
)

# Get all symbols
symbols = mt5.symbols_get()
if symbols:
    print(f"\nFound {len(symbols)} symbols:")
    print("\nFirst 20 symbols:")
    for i, s in enumerate(symbols[:20]):
        print(f"  {s.name} - Spread: {s.spread} points, Swap: {s.swap_mode}")
    
    # Check for XAUUSD
    xau_symbols = [s for s in symbols if 'XAU' in s.name or 'GOLD' in s.name.upper()]
    if xau_symbols:
        print(f"\nGold/XAU symbols found:")
        for s in xau_symbols:
            print(f"  {s.name} - Spread: {s.spread} points, Swap Mode: {s.swap_mode}")
    else:
        print("\nNo XAU/GOLD symbols found")
    
    # Check major pairs
    majors = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD']
    print(f"\nChecking major pairs:")
    for major in majors:
        found = [s for s in symbols if major in s.name]
        if found:
            print(f"  {major}: {[s.name for s in found]}")
        else:
            print(f"  {major}: Not found")

mt5.shutdown()

