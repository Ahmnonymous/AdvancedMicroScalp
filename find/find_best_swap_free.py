#!/usr/bin/env python3
"""Find best swap-free symbols for trading."""

import MetaTrader5 as mt5
import json
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

with open('config.json', 'r') as f:
    config = json.load(f)

mt5.initialize()
mt5.login(
    login=int(config['mt5']['account']),
    password=config['mt5']['password'],
    server=config['mt5']['server']
)

symbols = mt5.symbols_get()
swap_free = []

# Forex currency codes
forex_codes = ['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'NZD', 'CHF', 'HUF', 'PLN', 'CZK', 'SEK', 'NOK', 'DKK']

for s in symbols:
    if s.swap_mode == 0 or (s.swap_long == 0 and s.swap_short == 0):
        # Check if it's a forex-like pair (contains currency codes)
        symbol_upper = s.name.upper()
        has_forex = any(code in symbol_upper for code in forex_codes)
        
        # Get spread info
        spread_points = s.spread / s.point if s.point > 0 else 999999
        
        swap_free.append({
            'name': s.name,
            'spread': spread_points,
            'has_forex': has_forex,
            'swap_mode': s.swap_mode
        })

# Sort: forex pairs first, then by spread
swap_free.sort(key=lambda x: (not x['has_forex'], x['spread']))

print("\n" + "=" * 60)
print("BEST SWAP-FREE SYMBOLS FOR TRADING")
print("=" * 60)
print(f"\nTotal swap-free symbols: {len(swap_free)}")
print(f"\nTop 30 forex-like swap-free symbols:")

forex_like = [s for s in swap_free if s['has_forex']][:30]
for i, s in enumerate(forex_like, 1):
    print(f"  {i:2d}. {s['name']:15s} - Spread: {s['spread']:6.1f} points")

print(f"\nTop 20 other swap-free symbols (crypto/stocks):")
other = [s for s in swap_free if not s['has_forex']][:20]
for i, s in enumerate(other, 1):
    print(f"  {i:2d}. {s['name']:15s} - Spread: {s['spread']:6.1f} points")

# Get best symbols for config (forex-like with reasonable spreads)
best_symbols = [s['name'] for s in forex_like if s['spread'] < 500][:15]
if len(best_symbols) < 10:
    # Add some crypto if not enough forex
    best_symbols.extend([s['name'] for s in other if s['spread'] < 1000][:10-len(best_symbols)])

print(f"\n" + "=" * 60)
print(f"RECOMMENDED SYMBOLS FOR CONFIG: {len(best_symbols)} symbols")
print("=" * 60)
for sym in best_symbols:
    print(f"  - {sym}")

mt5.shutdown()

# Update config
config['pairs']['allowed_symbols'] = best_symbols
with open('config.json', 'w') as f:
    json.dump(config, f, indent=2)

print(f"\n✓ Config updated with {len(best_symbols)} swap-free symbols")

