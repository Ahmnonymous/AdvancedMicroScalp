#!/usr/bin/env python3
"""Find swap-free symbols that are actually tradeable (trade_mode = 4)."""

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
tradeable_swap_free = []

logger.info(f"Scanning {len(symbols)} symbols for tradeable swap-free symbols...")

for symbol in symbols:
    # Check if swap-free
    is_swap_free = (
        symbol.swap_mode == 0 or
        (symbol.swap_long == 0 and symbol.swap_short == 0)
    )
    
    # Check if tradeable (trade_mode = 4 = Full trading)
    is_tradeable = symbol.trade_mode == 4
    
    if is_swap_free and is_tradeable:
        # Try to get rates to verify market data
        rates = mt5.copy_rates_from_pos(symbol.name, mt5.TIMEFRAME_M1, 0, 10)
        if rates is not None and len(rates) > 0:
            # Check spread (for crypto, use percentage)
            bid = symbol.bid
            ask = symbol.ask
            if bid > 0:
                spread_percent = ((ask - bid) / bid) * 100
                if spread_percent <= 2.0:  # Max 2% spread
                    tradeable_swap_free.append({
                        'name': symbol.name,
                        'spread_percent': spread_percent,
                        'spread_points': symbol.spread / symbol.point if symbol.point > 0 else 0
                    })

# Sort by spread
tradeable_swap_free.sort(key=lambda x: x['spread_percent'])

logger.info(f"\n{'='*60}")
logger.info(f"Found {len(tradeable_swap_free)} tradeable swap-free symbols")
logger.info(f"{'='*60}")

for i, s in enumerate(tradeable_swap_free[:20], 1):
    logger.info(f"{i:2d}. {s['name']:15s} - Spread: {s['spread_percent']:.3f}% ({s['spread_points']:.1f} points)")

# Update config
if tradeable_swap_free:
    config['pairs']['allowed_symbols'] = [s['name'] for s in tradeable_swap_free[:15]]
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=2)
    
    logger.info(f"\n✓ Config updated with {len(config['pairs']['allowed_symbols'])} tradeable swap-free symbols")
else:
    logger.error("\n✗ No tradeable swap-free symbols found!")

mt5.shutdown()

