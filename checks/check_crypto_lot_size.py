#!/usr/bin/env python3
"""Check minimum lot sizes for crypto symbols"""

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

symbols = ['ETHUSDm', 'BTCUSDm', 'BTCAUDm', 'BTCXAUm']

print("="*80)
print("CRYPTO SYMBOL LOT SIZE INFO")
print("="*80)

for symbol_name in symbols:
    symbol_info = mt5.symbol_info(symbol_name)
    if symbol_info:
        print(f"\n{symbol_name}:")
        print(f"  Volume Min: {symbol_info.volume_min}")
        print(f"  Volume Max: {symbol_info.volume_max}")
        print(f"  Volume Step: {symbol_info.volume_step}")
        print(f"  Contract Size: {symbol_info.trade_contract_size}")
        
        # Try different lot sizes
        for test_lot in [symbol_info.volume_min, 0.001, 0.01, 0.1, 0.5, 1.0]:
            if test_lot < symbol_info.volume_min:
                continue
            if test_lot > symbol_info.volume_max:
                continue
            # Check if it's a valid step
            if (test_lot - symbol_info.volume_min) % symbol_info.volume_step != 0:
                continue
            
            # Test if this lot size works
            filling_mode = mt5.ORDER_FILLING_IOC if symbol_info.filling_mode & 2 else mt5.ORDER_FILLING_FOK
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol_name,
                "volume": test_lot,
                "type": mt5.ORDER_TYPE_BUY,
                "price": symbol_info.ask,
                "deviation": 20,
                "magic": 234000,
                "comment": "Lot test",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }
            
            result = mt5.order_send(request)
            if result:
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"  ✅ Lot {test_lot} - WORKS! (Code: {result.retcode})")
                    # Close immediately
                    close_request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol_name,
                        "volume": test_lot,
                        "type": mt5.ORDER_TYPE_SELL,
                        "position": result.order,
                        "deviation": 20,
                        "magic": 234000,
                        "comment": "Close test",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": filling_mode,
                    }
                    mt5.order_send(close_request)
                    break
                elif result.retcode == 10014:
                    print(f"  ❌ Lot {test_lot} - Invalid volume (Code: {result.retcode})")
                else:
                    print(f"  ⚠️  Lot {test_lot} - Code: {result.retcode} - {result.comment}")

mt5.shutdown()

