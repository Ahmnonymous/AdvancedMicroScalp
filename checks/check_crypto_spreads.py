#!/usr/bin/env python3
"""Check spreads for crypto symbols."""

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

symbols = config['pairs']['allowed_symbols']
max_spread = config['pairs']['max_spread_points']

print(f"\nChecking spreads (max allowed: {max_spread} points):")
print("=" * 60)

for symbol_name in symbols:
    symbol_info = mt5.symbol_info(symbol_name)
    if symbol_info:
        spread = symbol_info.spread
        point = symbol_info.point
        spread_points = spread / point if point > 0 else 0
        
        # For crypto, spread might be in price units, not points
        # Check if it's reasonable
        bid_ask_spread = symbol_info.ask - symbol_info.bid
        spread_percent = (bid_ask_spread / symbol_info.bid) * 100 if symbol_info.bid > 0 else 0
        
        status = "[OK] PASS" if spread_points <= max_spread else "✗ FAIL"
        print(f"{symbol_name:12s} - Spread: {spread_points:15.1f} points | "
              f"Bid-Ask: ${bid_ask_spread:.5f} ({spread_percent:.3f}%) - {status}")

mt5.shutdown()

