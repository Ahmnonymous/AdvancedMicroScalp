#!/usr/bin/env python3
"""
Comprehensive Index SL Calculation Test
Tests SL calculation for all problematic indices: US30m, US500m, UK100m, STOXX50m
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from risk.sl_manager import SLManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager


def test_index_sl_calculation(symbol, sl_manager, mt5_connector):
    """Test SL calculation for a specific index symbol."""
    print(f"\n{'='*80}")
    print(f"Testing {symbol} SL Calculation")
    print(f"{'='*80}")
    
    # Get symbol info
    symbol_info = mt5_connector.get_symbol_info(symbol)
    if not symbol_info:
        print(f"[ERROR] Could not get symbol info for {symbol}")
        return False, None
    
    print(f"\n{symbol} Symbol Info:")
    print(f"  Contract Size: {symbol_info.get('contract_size', 'N/A')}")
    print(f"  Point: {symbol_info.get('point', 'N/A')}")
    print(f"  Digits: {symbol_info.get('digits', 'N/A')}")
    print(f"  Trade Tick Value: {symbol_info.get('trade_tick_value', 'N/A')}")
    
    # Get current tick
    tick = mt5_connector.get_symbol_info_tick(symbol)
    if not tick:
        print(f"[ERROR] Could not get tick data for {symbol}")
        return False, None
    
    print(f"\nCurrent Market Prices:")
    print(f"  Bid: {tick.bid}")
    print(f"  Ask: {tick.ask}")
    print(f"  Spread: {tick.ask - tick.bid}")
    
    results = {}
    
    # Test BUY position
    print(f"\n--- BUY Position Test ---")
    entry_price_buy = tick.ask  # BUY uses ASK
    lot_size = 0.01
    order_type = 'BUY'
    target_loss = -2.0
    
    print(f"Entry Price (ASK): {entry_price_buy}")
    print(f"Lot Size: {lot_size}")
    print(f"Target Loss: ${target_loss}")
    
    try:
        target_sl_buy = sl_manager._calculate_target_sl_price(
            entry_price_buy, target_loss, order_type, lot_size, symbol_info
        )
        
        print(f"\n[OK] BUY SL Calculation:")
        print(f"  Target SL: {target_sl_buy}")
        print(f"  SL Distance: {entry_price_buy - target_sl_buy} points")
        sl_diff_pct = ((entry_price_buy - target_sl_buy) / entry_price_buy) * 100
        print(f"  SL Distance %: {sl_diff_pct:.2f}%")
        
        # Verify SL is below entry for BUY
        if target_sl_buy >= entry_price_buy:
            print(f"  [ERROR] ERROR: SL ({target_sl_buy}) is not below entry ({entry_price_buy}) for BUY")
            results['buy'] = False
        else:
            print(f"  [OK] SL is correctly below entry")
            
            # Calculate effective SL profit
            test_position = {
                'symbol': symbol,
                'type': order_type,
                'volume': lot_size,
                'price_open': entry_price_buy,
                'price_current': entry_price_buy,
                'sl': target_sl_buy,
                'profit': 0.0
            }
            
            effective_sl_profit = sl_manager.get_effective_sl_profit(test_position)
            print(f"  Effective SL Profit: ${effective_sl_profit:.2f}")
            print(f"  Target Loss: ${target_loss:.2f}")
            
            if abs(effective_sl_profit - target_loss) < 0.50:
                print(f"  [OK] Effective SL matches target (within $0.50)")
                results['buy'] = True
                results['buy_effective_sl'] = effective_sl_profit
            else:
                print(f"  [WARNING]  Effective SL differs by ${abs(effective_sl_profit - target_loss):.2f}")
                results['buy'] = False
                results['buy_effective_sl'] = effective_sl_profit
    except Exception as e:
        print(f"  [ERROR] Error: {e}")
        results['buy'] = False
    
    # Test SELL position
    print(f"\n--- SELL Position Test ---")
    entry_price_sell = tick.bid  # SELL uses BID
    order_type = 'SELL'
    
    print(f"Entry Price (BID): {entry_price_sell}")
    print(f"Lot Size: {lot_size}")
    print(f"Target Loss: ${target_loss}")
    
    try:
        target_sl_sell = sl_manager._calculate_target_sl_price(
            entry_price_sell, target_loss, order_type, lot_size, symbol_info
        )
        
        print(f"\n[OK] SELL SL Calculation:")
        print(f"  Target SL: {target_sl_sell}")
        print(f"  SL Distance: {target_sl_sell - entry_price_sell} points")
        sl_diff_pct = ((target_sl_sell - entry_price_sell) / entry_price_sell) * 100
        print(f"  SL Distance %: {sl_diff_pct:.2f}%")
        
        # Verify SL is above entry for SELL
        if target_sl_sell <= entry_price_sell:
            print(f"  [ERROR] ERROR: SL ({target_sl_sell}) is not above entry ({entry_price_sell}) for SELL")
            results['sell'] = False
        else:
            print(f"  [OK] SL is correctly above entry")
            
            # Calculate effective SL profit
            test_position = {
                'symbol': symbol,
                'type': order_type,
                'volume': lot_size,
                'price_open': entry_price_sell,
                'price_current': entry_price_sell,
                'sl': target_sl_sell,
                'profit': 0.0
            }
            
            effective_sl_profit = sl_manager.get_effective_sl_profit(test_position)
            print(f"  Effective SL Profit: ${effective_sl_profit:.2f}")
            print(f"  Target Loss: ${target_loss:.2f}")
            
            if abs(effective_sl_profit - target_loss) < 0.50:
                print(f"  [OK] Effective SL matches target (within $0.50)")
                results['sell'] = True
                results['sell_effective_sl'] = effective_sl_profit
            else:
                print(f"  [WARNING]  Effective SL differs by ${abs(effective_sl_profit - target_loss):.2f}")
                results['sell'] = False
                results['sell_effective_sl'] = effective_sl_profit
    except Exception as e:
        print(f"  [ERROR] Error: {e}")
        results['sell'] = False
    
    return results.get('buy', False) and results.get('sell', False), results


def main():
    """Run comprehensive index SL calculation tests."""
    print("=" * 80)
    print("COMPREHENSIVE INDEX SL CALCULATION TEST")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Initialize components
    mt5_connector = MT5Connector(config)
    if not mt5_connector.connect():
        print("[ERROR] Failed to connect to MT5")
        return
    
    order_manager = OrderManager(mt5_connector)
    sl_manager = SLManager(config, mt5_connector, order_manager)
    
    # Test all problematic indices
    indices_to_test = ['US30m', 'US500m', 'UK100m', 'STOXX50m']
    
    results = {}
    for symbol in indices_to_test:
        success, details = test_index_sl_calculation(symbol, sl_manager, mt5_connector)
        results[symbol] = {
            'success': success,
            'details': details
        }
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    all_passed = True
    for symbol, result in results.items():
        status = "[OK] PASS" if result['success'] else "[ERROR] FAIL"
        print(f"{status}: {symbol}")
        if not result['success']:
            all_passed = False
    
    print("\n" + "=" * 80)
    if all_passed:
        print("[OK] ALL INDEX TESTS PASSED")
    else:
        print("[ERROR] SOME INDEX TESTS FAILED")
    print("=" * 80)
    
    return results


if __name__ == '__main__':
    main()

