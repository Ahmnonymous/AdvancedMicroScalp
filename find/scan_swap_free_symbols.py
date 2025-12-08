#!/usr/bin/env python3
"""
Scan all MT5 symbols and identify swap-free (halal) symbols.
Update config.json with only swap-free symbols.
"""

import MetaTrader5 as mt5
import json
import logging
from typing import List, Dict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def scan_swap_free_symbols() -> List[str]:
    """Scan all symbols and return list of swap-free symbols."""
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Initialize and login
    if not mt5.initialize():
        logger.error(f"MT5 initialization failed: {mt5.last_error()}")
        return []
    
    authorized = mt5.login(
        login=int(config['mt5']['account']),
        password=config['mt5']['password'],
        server=config['mt5']['server']
    )
    
    if not authorized:
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return []
    
    # Get all symbols
    symbols = mt5.symbols_get()
    if symbols is None:
        logger.error("Failed to get symbols")
        mt5.shutdown()
        return []
    
    swap_free_symbols = []
    swap_enabled_symbols = []
    
    logger.info(f"Scanning {len(symbols)} symbols for swap-free status...")
    
    for symbol in symbols:
        # Check swap mode: 0 = disabled (swap-free)
        # Also check swap_long and swap_short should be 0
        is_swap_free = (
            symbol.swap_mode == 0 or  # Swap disabled
            (symbol.swap_long == 0 and symbol.swap_short == 0)  # Both swaps are 0
        )
        
        if is_swap_free:
            swap_free_symbols.append(symbol.name)
        else:
            swap_enabled_symbols.append(symbol.name)
    
    logger.info(f"Found {len(swap_free_symbols)} swap-free symbols")
    logger.info(f"Found {len(swap_enabled_symbols)} symbols with swaps")
    
    mt5.shutdown()
    return swap_free_symbols

def update_config_with_swap_free_symbols(swap_free_symbols: List[str]):
    """Update config.json with only swap-free symbols."""
    # Load current config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    old_symbols = config['pairs'].get('allowed_symbols', [])
    
    # Filter to only include swap-free symbols that are in our list
    # Also prioritize common trading pairs
    common_pairs = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'NZDUSD', 
                    'EURGBP', 'EURJPY', 'GBPJPY', 'USDCHF', 'XAUUSD', 'GOLD']
    
    # Find symbols that match common pairs (with or without suffix)
    prioritized_symbols = []
    other_symbols = []
    
    for symbol in swap_free_symbols:
        symbol_base = symbol.replace('m', '').replace('r', '').replace('.', '')
        if any(pair in symbol_base for pair in common_pairs):
            prioritized_symbols.append(symbol)
        else:
            other_symbols.append(symbol)
    
    # Sort prioritized symbols to put common pairs first
    prioritized_symbols.sort()
    other_symbols.sort()
    
    # Combine: prioritized first, then others (limit to reasonable number)
    new_symbols = prioritized_symbols[:20]  # Top 20 common pairs
    new_symbols.extend(other_symbols[:10])  # Top 10 others
    
    # Update config
    config['pairs']['allowed_symbols'] = new_symbols
    
    # Save config
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=2)
    
    # Log changes
    removed = [s for s in old_symbols if s not in new_symbols]
    added = [s for s in new_symbols if s not in old_symbols]
    
    logger.info("=" * 60)
    logger.info("CONFIG UPDATE SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total swap-free symbols found: {len(swap_free_symbols)}")
    logger.info(f"Symbols added to config: {len(added)}")
    if added:
        logger.info(f"  Added: {', '.join(added[:10])}{'...' if len(added) > 10 else ''}")
    logger.info(f"Symbols removed from config: {len(removed)}")
    if removed:
        logger.info(f"  Removed: {', '.join(removed)}")
    logger.info(f"Total symbols in config now: {len(new_symbols)}")
    logger.info(f"  Symbols: {', '.join(new_symbols[:15])}{'...' if len(new_symbols) > 15 else ''}")
    logger.info("=" * 60)
    
    return new_symbols, removed, added

def main():
    """Main function."""
    logger.info("Starting swap-free symbol scan...")
    
    # Scan for swap-free symbols
    swap_free_symbols = scan_swap_free_symbols()
    
    if not swap_free_symbols:
        logger.error("=" * 60)
        logger.error("CRITICAL: No swap-free symbols found!")
        logger.error("Please check your account settings or contact broker.")
        logger.error("=" * 60)
        return False
    
    # Update config
    new_symbols, removed, added = update_config_with_swap_free_symbols(swap_free_symbols)
    
    logger.info("Config updated successfully!")
    logger.info("Bot will use these swap-free symbols on next restart.")
    
    return True

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)

