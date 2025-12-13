#!/usr/bin/env python3
"""
Test script to verify data download for all backtest symbols.
Tests indices, crypto, and currencies to ensure data is available.
"""

import sys
import os
import json
import MetaTrader5 as mt5
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger_factory import get_logger

logger = get_logger("backtest_data_test", "logs/backtest/data_test.log")

def test_symbol_data_download():
    """Test data download for all backtest symbols."""
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    backtest_config = config.get('backtest', {})
    symbols = backtest_config.get('symbols', [])
    
    if not symbols:
        logger.error("No symbols configured for backtest")
        return False
    
    logger.info("=" * 80)
    logger.info("TESTING SYMBOL DATA DOWNLOAD")
    logger.info("=" * 80)
    logger.info(f"Symbols to test: {symbols}")
    
    # Initialize MT5
    mt5_config = config.get('mt5', {})
    if not mt5.initialize(path=mt5_config.get('path', '')):
        error = mt5.last_error()
        logger.error(f"Failed to initialize MT5: {error}")
        return False
    
    # Login
    if mt5_config.get('account') and mt5_config.get('password'):
        account = int(mt5_config['account'])
        password = mt5_config['password']
        server = mt5_config.get('server', '')
        
        if not mt5.login(account, password=password, server=server):
            error = mt5.last_error()
            logger.error(f"MT5 login failed: {error}")
            mt5.shutdown()
            return False
        else:
            logger.info(f"MT5 logged in successfully to account {account}")
    
    # Test each symbol
    results = {
        'success': [],
        'failed': [],
        'no_data': []
    }
    
    # Use recent date range (last 30 days)
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=30)
    
    logger.info(f"Testing data availability from {start_date} to {end_date}")
    
    for symbol in symbols:
        logger.info(f"\nTesting {symbol}...")
        
        # Check if symbol exists
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.warning(f"  [ERROR] Symbol {symbol} not found in MT5")
            results['failed'].append({'symbol': symbol, 'reason': 'Symbol not found'})
            continue
        
        # Check if symbol is visible
        if not symbol_info.visible:
            logger.warning(f"  [WARNING]  Symbol {symbol} is not visible, attempting to enable...")
            if not mt5.symbol_select(symbol, True):
                logger.warning(f"  [ERROR] Failed to enable symbol {symbol}")
                results['failed'].append({'symbol': symbol, 'reason': 'Cannot enable symbol'})
                continue
        
        # Try to get historical data
        try:
            # Try M1 timeframe
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start_date, end_date)
            
            if rates is None or len(rates) == 0:
                # Try alternative method
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1000)
                
                if rates is None or len(rates) == 0:
                    logger.warning(f"  [ERROR] No data available for {symbol}")
                    results['no_data'].append({'symbol': symbol, 'reason': 'No historical data'})
                    continue
            
            # Check data quality
            if len(rates) > 0:
                # Convert to check dates
                import pandas as pd
                df = pd.DataFrame(rates)
                if 'time' in df.columns:
                    df['time'] = pd.to_datetime(df['time'], unit='s')
                    data_start = df['time'].min()
                    data_end = df['time'].max()
                    logger.info(f"  [OK] {symbol}: {len(rates)} bars available")
                    logger.info(f"     Data range: {data_start} to {data_end}")
                    logger.info(f"     Spread: {symbol_info.spread} points")
                    logger.info(f"     Trade mode: {symbol_info.trade_mode}")
                    logger.info(f"     Swap mode: {symbol_info.swap_mode}")
                    results['success'].append({
                        'symbol': symbol,
                        'bars': len(rates),
                        'data_start': str(data_start),
                        'data_end': str(data_end),
                        'spread': symbol_info.spread,
                        'trade_mode': symbol_info.trade_mode
                    })
                else:
                    logger.warning(f"  [WARNING]  {symbol}: Data available but no time column")
                    results['success'].append({'symbol': symbol, 'bars': len(rates), 'warning': 'No time column'})
            else:
                logger.warning(f"  [ERROR] {symbol}: Empty data")
                results['no_data'].append({'symbol': symbol, 'reason': 'Empty data'})
        
        except Exception as e:
            logger.error(f"  [ERROR] Error testing {symbol}: {e}")
            results['failed'].append({'symbol': symbol, 'reason': str(e)})
    
    mt5.shutdown()
    
    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("DATA DOWNLOAD TEST SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total symbols tested: {len(symbols)}")
    logger.info(f"[OK] Success: {len(results['success'])}")
    logger.info(f"[ERROR] Failed: {len(results['failed'])}")
    logger.info(f"[WARNING]  No data: {len(results['no_data'])}")
    
    if results['success']:
        logger.info("\n[OK] Successful symbols:")
        for item in results['success']:
            logger.info(f"   - {item['symbol']}: {item.get('bars', 0)} bars")
    
    if results['failed']:
        logger.info("\n[ERROR] Failed symbols:")
        for item in results['failed']:
            logger.info(f"   - {item['symbol']}: {item['reason']}")
    
    if results['no_data']:
        logger.info("\n[WARNING]  No data symbols:")
        for item in results['no_data']:
            logger.info(f"   - {item['symbol']}: {item['reason']}")
    
    logger.info("=" * 80)
    
    # Return success if at least some symbols have data
    return len(results['success']) > 0


if __name__ == "__main__":
    success = test_symbol_data_download()
    sys.exit(0 if success else 1)

