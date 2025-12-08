#!/usr/bin/env python3
"""Find mode 4 symbols and check if account is swap-free (can trade even if symbol has swaps)."""

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

# Get mode 4 symbols (actually tradeable)
symbols = mt5.symbols_get()
mode4 = [s for s in symbols if s.trade_mode == 4]

print(f"Found {len(mode4)} mode 4 (tradeable) symbols\n")

# Check a few popular ones
test_symbols = ['BTCUSDm', 'ETHUSDm', 'XAUUSDm', 'EURUSDm', 'GBPUSDm', 'USDJPYm']

print("Checking popular symbols:")
print("=" * 60)
for name in test_symbols:
    symbol = mt5.symbol_info(name)
    if symbol and symbol.trade_mode == 4:
        # Check filling mode
        filling_modes = symbol.filling_mode
        filling_supported = []
        if filling_modes & 1:
            filling_supported.append("FOK")
        if filling_modes & 2:
            filling_supported.append("IOC")
        if filling_modes & 4:
            filling_supported.append("RETURN")
        
        print(f"{name:12s} - Swap Mode: {symbol.swap_mode}, Filling: {', '.join(filling_supported)}")
        
        # Try FOK if available
        if filling_modes & 1:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": name,
                "volume": symbol.volume_min,
                "type": mt5.ORDER_TYPE_BUY,
                "price": symbol.ask,
                "deviation": 20,
                "magic": 234000,
                "comment": "Test",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
            result = mt5.order_send(request)
            if result:
                print(f"  Order test: {result.retcode} - {result.comment}")
            else:
                error = mt5.last_error()
                print(f"  Order test: {error}")

mt5.shutdown()

