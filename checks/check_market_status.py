#!/usr/bin/env python3
"""Check if market is open for trading"""

import os
import sys
import MetaTrader5 as mt5
import json
from datetime import datetime

# Add parent directory to path to access root config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Get root directory path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(root_dir, 'config.json')

with open(config_path, 'r') as f:
    config = json.load(f)

mt5.initialize()
mt5.login(
    login=int(config['mt5']['account']),
    password=config['mt5']['password'],
    server=config['mt5']['server']
)

# Check a few major symbols
symbols_to_check = ['XAUUSD', 'EURUSD', 'DE30m', 'US30m', 'LIm']

print("="*80)
print("MARKET STATUS CHECK")
print("="*80)
print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

for symbol_name in symbols_to_check:
    symbol_info = mt5.symbol_info(symbol_name)
    if symbol_info:
        # Try to get tick to see if market is active
        tick = mt5.symbol_info_tick(symbol_name)
        
        # Check if we can get rates
        rates = mt5.copy_rates_from_pos(symbol_name, mt5.TIMEFRAME_M1, 0, 1)
        
        status = "🟢 OPEN" if tick and rates and len(rates) > 0 else "🔴 CLOSED"
        
        bid = tick.bid if tick else 0
        ask = tick.ask if tick else 0
        
        print(f"{symbol_name:10s} - {status:10s} - Bid: {bid:10.5f} Ask: {ask:10.5f} Spread: {(ask-bid):.5f}")
    else:
        print(f"{symbol_name:10s} - NOT FOUND")

mt5.shutdown()

print()
print("="*80)
print("Note: Forex markets typically open Sunday 22:00 GMT")
print("Indices have specific trading hours depending on region")
print("="*80)

