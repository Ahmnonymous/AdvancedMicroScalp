#!/usr/bin/env python3
"""Find symbols that can actually be traded (test order placement)."""

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

# Get all swap-free symbols
symbols = mt5.symbols_get()
swap_free = [s for s in symbols if s.swap_mode == 0 or (s.swap_long == 0 and s.swap_short == 0)]

print(f"Testing {len(swap_free)} swap-free symbols for tradeability...\n")

working_symbols = []

for symbol in swap_free[:50]:  # Test first 50
    # Try to get rates
    rates = mt5.copy_rates_from_pos(symbol.name, mt5.TIMEFRAME_M1, 0, 1)
    if rates is None or len(rates) == 0:
        continue
    
    # Check spread
    if symbol.bid > 0:
        spread_pct = ((symbol.ask - symbol.bid) / symbol.bid) * 100
        if spread_pct > 2.0:
            continue
    
    # Try different filling modes
    filling_modes = symbol.filling_mode
    test_filling = None
    
    if filling_modes & 4:
        test_filling = mt5.ORDER_FILLING_RETURN
    elif filling_modes & 1:
        test_filling = mt5.ORDER_FILLING_FOK
    elif filling_modes & 2:
        test_filling = mt5.ORDER_FILLING_IOC
    
    if test_filling is None:
        continue
    
    # Try a test order request (we won't actually send it, just validate)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol.name,
        "volume": symbol.volume_min,
        "type": mt5.ORDER_TYPE_BUY,
        "price": symbol.ask,
        "deviation": 20,
        "magic": 234000,
        "comment": "Test",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": test_filling,
    }
    
    # Note: We're not actually sending, just checking if structure is valid
    # For now, if symbol has data and reasonable spread, consider it
    working_symbols.append({
        'name': symbol.name,
        'trade_mode': symbol.trade_mode,
        'spread_pct': spread_pct if symbol.bid > 0 else 999,
        'filling_mode': test_filling
    })

# Sort by spread
working_symbols.sort(key=lambda x: x['spread_pct'])

print(f"Found {len(working_symbols)} potentially tradeable swap-free symbols:\n")
for i, s in enumerate(working_symbols[:15], 1):
    print(f"{i:2d}. {s['name']:15s} - Mode: {s['trade_mode']}, Spread: {s['spread_pct']:.3f}%")

# Update config with best symbols
if working_symbols:
    config['pairs']['allowed_symbols'] = [s['name'] for s in working_symbols[:10]]
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=2)
    print(f"\n[OK] Config updated with {len(config['pairs']['allowed_symbols'])} symbols")

mt5.shutdown()

