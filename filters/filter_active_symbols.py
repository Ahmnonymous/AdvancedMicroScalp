#!/usr/bin/env python3
"""Filter symbols to only include those with active market data."""

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

symbols_to_check = config['pairs']['allowed_symbols']
active_symbols = []

logger.info(f"Checking {len(symbols_to_check)} symbols for market data availability...")

for symbol_name in symbols_to_check:
    symbol_info = mt5.symbol_info(symbol_name)
    if symbol_info is None:
        logger.warning(f"{symbol_name}: Symbol not found")
        continue
    
    # Try to get rates
    rates = mt5.copy_rates_from_pos(symbol_name, mt5.TIMEFRAME_M1, 0, 10)
    if rates is not None and len(rates) > 0:
        active_symbols.append(symbol_name)
        logger.info(f"✓ {symbol_name}: Active (spread: {symbol_info.spread/symbol_info.point:.1f} points)")
    else:
        logger.warning(f"✗ {symbol_name}: No market data available")

mt5.shutdown()

# Update config with only active symbols
config['pairs']['allowed_symbols'] = active_symbols

with open('config.json', 'w') as f:
    json.dump(config, f, indent=2)

logger.info("=" * 60)
logger.info(f"CONFIG UPDATED: {len(active_symbols)} active symbols")
logger.info(f"Active symbols: {', '.join(active_symbols)}")
logger.info("=" * 60)

