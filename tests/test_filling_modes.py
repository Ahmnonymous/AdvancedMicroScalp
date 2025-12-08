#!/usr/bin/env python3
"""Test which filling modes work for LIm"""

import os
import sys
import MetaTrader5 as mt5
import json

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

symbol_name = "LIm"
symbol_info = mt5.symbol_info(symbol_name)

print(f"Symbol: {symbol_name}")
print(f"Filling mode (bitmask): {symbol_info.filling_mode}")
print(f"  Bit 0 (FOK): {bool(symbol_info.filling_mode & 1)}")
print(f"  Bit 1 (IOC): {bool(symbol_info.filling_mode & 2)}")
print(f"  Bit 2 (RETURN): {bool(symbol_info.filling_mode & 4)}")
print()

# Try each filling mode
for mode_name, mode_value in [
    ("FOK", mt5.ORDER_FILLING_FOK),
    ("IOC", mt5.ORDER_FILLING_IOC),
    ("RETURN", mt5.ORDER_FILLING_RETURN)
]:
    if symbol_info.filling_mode & (1 if mode_value == mt5.ORDER_FILLING_FOK else 2 if mode_value == mt5.ORDER_FILLING_IOC else 4):
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol_name,
            "volume": 0.01,
            "type": mt5.ORDER_TYPE_SELL,
            "price": symbol_info.bid,
            "deviation": 20,
            "magic": 234000,
            "comment": f"Test {mode_name}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mode_value,
        }
        
        result = mt5.order_send(request)
        if result:
            print(f"{mode_name:6s} - Code: {result.retcode} - {result.comment}")
        else:
            error = mt5.last_error()
            print(f"{mode_name:6s} - Error: {error}")
    else:
        print(f"{mode_name:6s} - Not supported by symbol")

mt5.shutdown()

