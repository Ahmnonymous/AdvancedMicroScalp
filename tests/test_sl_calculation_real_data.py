#!/usr/bin/env python3
"""
Test SL Calculation with Real Symbol Data
Tests the actual SL calculation logic with real MT5 symbol information.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from risk.sl_manager import SLManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager


def test_us30m_sl_calculation():
    """Test SL calculation for US30m with real data."""
    print("=" * 80)
    print("Testing US30m SL Calculation")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Initialize components
    mt5_connector = MT5Connector(config)
    if not mt5_connector.connect():
        print("[ERROR] Failed to connect to MT5")
        return False
    
    order_manager = OrderManager(mt5_connector)
    sl_manager = SLManager(config, mt5_connector, order_manager)
    
    # Get US30m symbol info
    symbol = 'US30m'
    symbol_info = mt5_connector.get_symbol_info(symbol)
    if not symbol_info:
        print(f"[ERROR] Could not get symbol info for {symbol}")
        return False
    
    print(f"\n{symbol} Symbol Info:")
    print(f"  Contract Size: {symbol_info.get('contract_size', 'N/A')}")
    print(f"  Point: {symbol_info.get('point', 'N/A')}")
    print(f"  Digits: {symbol_info.get('digits', 'N/A')}")
    print(f"  Trade Tick Value: {symbol_info.get('trade_tick_value', 'N/A')}")
    
    # Get current tick
    tick = mt5_connector.get_symbol_info_tick(symbol)
    if not tick:
        print(f"[ERROR] Could not get tick data for {symbol}")
        return False
    
    print(f"\nCurrent Market Prices:")
    print(f"  Bid: {tick.bid}")
    print(f"  Ask: {tick.ask}")
    print(f"  Spread: {tick.ask - tick.bid}")
    
    # Simulate a BUY position
    entry_price = tick.ask  # BUY uses ASK
    lot_size = 0.01
    order_type = 'BUY'
    target_loss = -2.0  # -$2.00
    
    print(f"\nSimulated Position:")
    print(f"  Entry Price (ASK): {entry_price}")
    print(f"  Lot Size: {lot_size}")
    print(f"  Order Type: {order_type}")
    print(f"  Target Loss: ${target_loss}")
    
    # Calculate target SL
    try:
        target_sl = sl_manager._calculate_target_sl_price(
            entry_price, target_loss, order_type, lot_size, symbol_info
        )
        
        print(f"\n[OK] SL Calculation Result:")
        print(f"  Target SL: {target_sl}")
        print(f"  SL Distance: {entry_price - target_sl} points")
        print(f"  SL Distance %: {((entry_price - target_sl) / entry_price) * 100:.2f}%")
        
        # Verify SL is below entry for BUY
        if target_sl >= entry_price:
            print(f"  [ERROR] ERROR: SL ({target_sl}) is not below entry ({entry_price}) for BUY")
            return False
        else:
            print(f"  [OK] SL is correctly below entry")
        
        # Calculate effective SL profit
        test_position = {
            'symbol': symbol,
            'type': order_type,
            'volume': lot_size,
            'price_open': entry_price,
            'price_current': entry_price,
            'sl': target_sl,
            'profit': 0.0
        }
        
        effective_sl_profit = sl_manager.get_effective_sl_profit(test_position)
        print(f"\nEffective SL Profit: ${effective_sl_profit:.2f}")
        print(f"Target Loss: ${target_loss:.2f}")
        
        # Check if effective SL is close to target
        if abs(effective_sl_profit - target_loss) < 0.50:  # Within $0.50
            print(f"  [OK] Effective SL is close to target (within $0.50)")
            return True
        else:
            print(f"  [WARNING]  Effective SL differs from target by ${abs(effective_sl_profit - target_loss):.2f}")
            print(f"     This might be due to spread or calculation differences")
            return False
            
    except Exception as e:
        print(f"[ERROR] Error calculating SL: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_forex_sl_calculation():
    """Test SL calculation for EURUSD (forex) for comparison."""
    print("\n" + "=" * 80)
    print("Testing EURUSD SL Calculation (Forex)")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Initialize components
    mt5_connector = MT5Connector(config)
    if not mt5_connector.connect():
        print("[ERROR] Failed to connect to MT5")
        return False
    
    order_manager = OrderManager(mt5_connector)
    sl_manager = SLManager(config, mt5_connector, order_manager)
    
    # Get EURUSD symbol info
    symbol = 'EURUSD'
    symbol_info = mt5_connector.get_symbol_info(symbol)
    if not symbol_info:
        print(f"[ERROR] Could not get symbol info for {symbol}")
        return False
    
    print(f"\n{symbol} Symbol Info:")
    print(f"  Contract Size: {symbol_info.get('contract_size', 'N/A')}")
    print(f"  Point: {symbol_info.get('point', 'N/A')}")
    print(f"  Digits: {symbol_info.get('digits', 'N/A')}")
    
    # Get current tick
    tick = mt5_connector.get_symbol_info_tick(symbol)
    if not tick:
        print(f"[ERROR] Could not get tick data for {symbol}")
        return False
    
    print(f"\nCurrent Market Prices:")
    print(f"  Bid: {tick.bid}")
    print(f"  Ask: {tick.ask}")
    
    # Simulate a BUY position
    entry_price = tick.ask  # BUY uses ASK
    lot_size = 0.01
    order_type = 'BUY'
    target_loss = -2.0  # -$2.00
    
    print(f"\nSimulated Position:")
    print(f"  Entry Price (ASK): {entry_price}")
    print(f"  Lot Size: {lot_size}")
    print(f"  Target Loss: ${target_loss}")
    
    # Calculate target SL
    try:
        target_sl = sl_manager._calculate_target_sl_price(
            entry_price, target_loss, order_type, lot_size, symbol_info
        )
        
        print(f"\n[OK] SL Calculation Result:")
        print(f"  Target SL: {target_sl}")
        print(f"  SL Distance: {entry_price - target_sl} pips")
        
        # Calculate effective SL profit
        test_position = {
            'symbol': symbol,
            'type': order_type,
            'volume': lot_size,
            'price_open': entry_price,
            'price_current': entry_price,
            'sl': target_sl,
            'profit': 0.0
        }
        
        effective_sl_profit = sl_manager.get_effective_sl_profit(test_position)
        print(f"\nEffective SL Profit: ${effective_sl_profit:.2f}")
        print(f"Target Loss: ${target_loss:.2f}")
        
        if abs(effective_sl_profit - target_loss) < 0.50:
            print(f"  [OK] Effective SL is close to target")
            return True
        else:
            print(f"  [WARNING]  Effective SL differs from target")
            return False
            
    except Exception as e:
        print(f"[ERROR] Error calculating SL: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rate_limiting():
    """Test that rate limiting works."""
    print("\n" + "=" * 80)
    print("Testing Rate Limiting")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Initialize components
    mt5_connector = MT5Connector(config)
    if not mt5_connector.connect():
        print("[ERROR] Failed to connect to MT5")
        return False
    
    order_manager = OrderManager(mt5_connector)
    sl_manager = SLManager(config, mt5_connector, order_manager)
    
    # Check rate limit interval
    print(f"\nRate Limit Configuration:")
    print(f"  Minimum Interval: {sl_manager._sl_update_min_interval} seconds")
    
    # Check disabled symbols
    print(f"\nDisabled Symbols (Safety):")
    if sl_manager._disabled_symbols:
        print(f"  {', '.join(sl_manager._disabled_symbols)}")
        print(f"  [OK] Safety measures active")
    else:
        print(f"  [WARNING]  No symbols disabled")
    
    return True


if __name__ == '__main__':
    print("\n" + "=" * 80)
    print("CRITICAL FIXES TEST SUITE")
    print("=" * 80)
    
    results = []
    
    # Test 1: US30m SL calculation
    results.append(("US30m SL Calculation", test_us30m_sl_calculation()))
    
    # Test 2: EURUSD SL calculation (for comparison)
    results.append(("EURUSD SL Calculation", test_forex_sl_calculation()))
    
    # Test 3: Rate limiting
    results.append(("Rate Limiting", test_rate_limiting()))
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    for test_name, result in results:
        status = "[OK] PASS" if result else "[ERROR] FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(result for _, result in results)
    print("\n" + "=" * 80)
    if all_passed:
        print("[OK] ALL TESTS PASSED")
    else:
        print("[ERROR] SOME TESTS FAILED")
    print("=" * 80)

