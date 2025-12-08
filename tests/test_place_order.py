#!/usr/bin/env python3
"""Test placing a small order to see what error we get."""

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

# Test with mode 4 swap-free symbols first
symbols_to_test = ['JP225m', 'ATVIm', 'XRPUSDm']

for symbol_name in symbols_to_test:
    symbol_info = mt5.symbol_info(symbol_name)
    if not symbol_info:
        print(f"{symbol_name}: Not found")
        continue
    
    print(f"\n{symbol_name}:")
    print(f"  Trade Mode: {symbol_info.trade_mode}")
    print(f"  Swap Mode: {symbol_info.swap_mode}")
    print(f"  Volume Min: {symbol_info.volume_min}")
    print(f"  Volume Step: {symbol_info.volume_step}")
    print(f"  Bid: {symbol_info.bid}, Ask: {symbol_info.ask}")
    
    # Try a minimal order request
    if symbol_info.trade_mode == 4 or symbol_info.trade_mode == 0:
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol_name,
            "volume": symbol_info.volume_min,  # Use minimum volume
            "type": mt5.ORDER_TYPE_BUY,
            "price": symbol_info.ask,
            "deviation": 20,
            "magic": 234000,
            "comment": "Test order",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        
        result = mt5.order_send(request)
        if result:
            print(f"  Order result: {result.retcode} - {result.comment}")
        else:
            error = mt5.last_error()
            print(f"  Order failed: {error}")

mt5.shutdown()

