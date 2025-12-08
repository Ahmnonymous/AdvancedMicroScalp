#!/usr/bin/env python3
"""Find symbols that can actually be traded RIGHT NOW"""

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

print("="*80)
print("FINDING TRADEABLE SYMBOLS (Testing actual order placement)")
print("="*80)

# Get all symbols
all_symbols = mt5.symbols_get()
print(f"Total symbols available: {len(all_symbols)}\n")

tradeable_symbols = []

# Test a sample of symbols (first 100 to save time)
for symbol_info in all_symbols[:200]:
    symbol_name = symbol_info.name
    
    # Skip if not tradeable mode
    if symbol_info.trade_mode != 4:
        continue
    
    # Check if we can get current price
    tick = mt5.symbol_info_tick(symbol_name)
    if not tick or tick.bid <= 0:
        continue
    
    # Determine filling mode
    filling_mode = None
    if symbol_info.filling_mode & 4:
        filling_mode = mt5.ORDER_FILLING_RETURN
    elif symbol_info.filling_mode & 2:
        filling_mode = mt5.ORDER_FILLING_IOC
    elif symbol_info.filling_mode & 1:
        filling_mode = mt5.ORDER_FILLING_FOK
    
    if not filling_mode:
        continue
    
    # Try a minimal test order (market order - will fail if market closed, but we check the error)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol_name,
        "volume": symbol_info.volume_min,
        "type": mt5.ORDER_TYPE_BUY,
        "price": tick.ask,
        "deviation": 20,
        "magic": 234000,
        "comment": "Tradeability test",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }
    
    result = mt5.order_send(request)
    
    if result:
        # Check if order was accepted (code 10018 means market closed, which is OK - it means symbol is valid)
        # Code 10009 means invalid stops, code 10004 means requote - these are also OK (symbol is tradeable)
        if result.retcode in [mt5.TRADE_RETCODE_DONE, 10018, 10004, 10009]:
            # Symbol is tradeable (either worked or market is closed)
            spread = tick.ask - tick.bid
            spread_points = spread / symbol_info.point if symbol_info.point > 0 else 0
            
            tradeable_symbols.append({
                'name': symbol_name,
                'spread_points': spread_points,
                'spread': spread,
                'filling_mode': 'RETURN' if filling_mode == mt5.ORDER_FILLING_RETURN else 'IOC' if filling_mode == mt5.ORDER_FILLING_IOC else 'FOK',
                'tradeable': result.retcode == mt5.TRADE_RETCODE_DONE
            })
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                # Close immediately
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
                    "type_filling": filling_mode,
                }
                mt5.order_send(close_request)
                print(f"✅ {symbol_name:15s} - TRADEABLE NOW (Spread: {spread_points:.1f} pts)")
            else:
                status = "Market Closed" if result.retcode == 10018 else f"Code {result.retcode}"
                print(f"⏰ {symbol_name:15s} - Valid symbol, {status} (Spread: {spread_points:.1f} pts)")

mt5.shutdown()

print(f"\n{'='*80}")
print(f"FOUND {len(tradeable_symbols)} POTENTIALLY TRADEABLE SYMBOLS")
print("="*80)

# Sort by spread
tradeable_symbols.sort(key=lambda x: x['spread_points'])

print("\nTop 20 symbols (by spread):")
for i, sym in enumerate(tradeable_symbols[:20], 1):
    status = "🟢 NOW" if sym['tradeable'] else "⏰ Closed"
    print(f"  {i:2d}. {sym['name']:15s} - {status:8s} - Spread: {sym['spread_points']:8.1f} pts ({sym['filling_mode']})")

if tradeable_symbols:
    # Get symbols that worked RIGHT NOW
    now_tradeable = [s for s in tradeable_symbols if s['tradeable']]
    if now_tradeable:
        print(f"\n🟢 SYMBOLS TRADEABLE RIGHT NOW ({len(now_tradeable)}):")
        for sym in now_tradeable:
            print(f"   - {sym['name']}")

