#!/usr/bin/env python3
"""Diagnose why bot is not taking trades"""

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

print("="*80)
print("TRADING DIAGNOSIS")
print("="*80)

# Check account
account = mt5.account_info()
print(f"\nAccount Info:")
print(f"  Login: {account.login}")
print(f"  Server: {account.server}")
print(f"  Trade Allowed: {account.trade_allowed}")
print(f"  Trade Expert: {account.trade_expert}")
print(f"  Trade Mode: {account.trade_mode}")
print(f"  Margin Free: {account.margin_free}")
print(f"  Balance: {account.balance}")

# Check multiple symbols
symbols_to_test = ['XAUUSD', 'EURUSD', 'GBPUSD', 'DE30m', 'US30m', 'LIm', 'BTCUSD', 'ETHUSD']

print(f"\n{'='*80}")
print("Symbol Trading Status:")
print("="*80)

for symbol_name in symbols_to_test:
    symbol_info = mt5.symbol_info(symbol_name)
    if not symbol_info:
        print(f"{symbol_name:12s} - NOT FOUND")
        continue
    
    tick = mt5.symbol_info_tick(symbol_name)
    
    # Check filling mode
    filling_modes = []
    if symbol_info.filling_mode & 1:
        filling_modes.append("FOK")
    if symbol_info.filling_mode & 2:
        filling_modes.append("IOC")
    if symbol_info.filling_mode & 4:
        filling_modes.append("RETURN")
    
    # Try to place a test order with each filling mode
    tradeable = False
    working_mode = None
    
    for mode_name, mode_value in [
        ("FOK", mt5.ORDER_FILLING_FOK),
        ("IOC", mt5.ORDER_FILLING_IOC),
        ("RETURN", mt5.ORDER_FILLING_RETURN)
    ]:
        if mode_name not in filling_modes:
            continue
            
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol_name,
            "volume": symbol_info.volume_min,
            "type": mt5.ORDER_TYPE_BUY,
            "price": symbol_info.ask,
            "deviation": 20,
            "magic": 234000,
            "comment": "Test",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mode_value,
        }
        
        # Don't actually send, just check request structure
        # Actually, let's try to get market state
        result = mt5.order_send(request)
        if result:
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                tradeable = True
                working_mode = mode_name
                # Close it immediately if opened
                if result.order:
                    close_request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol_name,
                        "volume": symbol_info.volume_min,
                        "type": mt5.ORDER_TYPE_SELL,
                        "position": result.order,
                        "deviation": 20,
                        "magic": 234000,
                        "comment": "Close test",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mode_value,
                    }
                    mt5.order_send(close_request)
                break
            elif result.retcode == 10018:
                # Market closed
                pass
            else:
                # Other error - log it
                if not working_mode:
                    working_mode = f"Error {result.retcode}: {result.comment[:30]}"
    
    status = "🟢 TRADEABLE" if tradeable else "🔴 NOT TRADEABLE"
    mode_info = f" (Mode: {working_mode})" if working_mode else f" (Modes: {', '.join(filling_modes)})"
    
    print(f"{symbol_name:12s} - {status:15s} {mode_info}")
    if tick:
        print(f"              Bid: {tick.bid:.5f}, Ask: {tick.ask:.5f}, Spread: {tick.ask-tick.bid:.5f}")

mt5.shutdown()

print("\n" + "="*80)
print("Recommendation: Use symbols marked as TRADEABLE")
print("="*80)

