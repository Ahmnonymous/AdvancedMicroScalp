#!/usr/bin/env python3
"""Test placing an actual trade to see what error we get"""

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

# Test with LIm which showed as OPEN
symbol_name = "LIm"
symbol_info = mt5.symbol_info(symbol_name)

if not symbol_info:
    print(f"Symbol {symbol_name} not found")
    mt5.shutdown()
    exit(1)

print("="*80)
print(f"TESTING TRADE PLACEMENT: {symbol_name}")
print("="*80)
print(f"Symbol Info:")
print(f"  Name: {symbol_info.name}")
print(f"  Trade Mode: {symbol_info.trade_mode} (4=Full trading enabled)")
print(f"  Bid: {symbol_info.bid}")
print(f"  Ask: {symbol_info.ask}")
print(f"  Spread: {symbol_info.spread}")
print()

# Check account info
account_info = mt5.account_info()
if account_info:
    print(f"Account Info:")
    print(f"  Login: {account_info.login}")
    print(f"  Trade Allowed: {account_info.trade_allowed}")
    print(f"  Trade Expert: {account_info.trade_expert}")
    print(f"  Server: {account_info.server}")
    print()

# Try to place a minimal order
print("Attempting to place test order...")
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol_name,
    "volume": 0.01,  # Minimum lot
    "type": mt5.ORDER_TYPE_SELL,
    "price": symbol_info.bid,
    "deviation": 20,
    "magic": 234000,
    "comment": "Test order",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_RETURN,
}

result = mt5.order_send(request)

if result is None:
    error = mt5.last_error()
    print(f"[ERROR] Order send returned None")
    print(f"Error: {error}")
else:
    print(f"Result Code: {result.retcode}")
    print(f"Comment: {result.comment}")
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print("[OK] Order placed successfully!")
    else:
        print(f"[ERROR] Order failed: {result.retcode} - {result.comment}")

mt5.shutdown()

