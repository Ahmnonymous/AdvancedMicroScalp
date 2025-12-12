#!/usr/bin/env python3
"""
Test MT5 Connection Script
Use this to verify your MT5 connection before running the main bot.
"""

import json
import sys
from execution.mt5_connector import MT5Connector
from utils.logger_factory import get_logger

def main():
    """Test MT5 connection."""
    print("=" * 60)
    print("MT5 Connection Test")
    print("=" * 60)
    
    # Load config
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: config.json not found!")
        print("Please create config.json first.")
        sys.exit(1)
    
    # Setup logging
    logger = get_logger("system_startup", "logs/live/system/system_startup.log")
    
    # Test connection
    print("\n1. Testing MT5 Connection...")
    connector = MT5Connector(config)
    
    if connector.connect():
        print("   [OK] MT5 Connected Successfully")
        
        # Get account info
        print("\n2. Retrieving Account Information...")
        account_info = connector.get_account_info()
        
        if account_info:
            print(f"   Account: {account_info['login']}")
            print(f"   Server: {account_info['server']}")
            print(f"   Balance: {account_info['balance']} {account_info['currency']}")
            print(f"   Equity: {account_info['equity']} {account_info['currency']}")
            print(f"   Margin: {account_info['margin']} {account_info['currency']}")
            print(f"   Free Margin: {account_info['free_margin']} {account_info['currency']}")
            print(f"   Leverage: 1:{account_info['leverage']}")
            print(f"   Trade Allowed: {account_info['trade_allowed']}")
            print(f"   Trade Expert: {account_info['trade_expert']}")
        else:
            print("   ✗ Failed to get account info")
        
        # Test symbol info
        print("\n3. Testing Symbol Information...")
        test_symbols = ['EURUSD', 'GBPUSD', 'USDJPY']
        
        for symbol in test_symbols:
            symbol_info = connector.get_symbol_info(symbol)
            if symbol_info:
                spread_points = symbol_info['spread'] / symbol_info['point']
                is_swap_free = connector.is_swap_free(symbol)
                print(f"   {symbol}:")
                print(f"     Bid: {symbol_info['bid']:.5f}")
                print(f"     Ask: {symbol_info['ask']:.5f}")
                print(f"     Spread: {spread_points:.1f} points")
                print(f"     Swap-Free: {'Yes' if is_swap_free else 'No'}")
            else:
                print(f"   {symbol}: ✗ Not available")
        
        # Check halal compliance
        print("\n4. Checking Halal Compliance...")
        halal_config = config.get('halal', {})
        if halal_config.get('enabled', True):
            print("   Halal compliance: ENABLED")
            print(f"   Swap-free mode required: {halal_config.get('swap_free_mode', True)}")
            print(f"   Max hold hours: {halal_config.get('max_hold_hours', 24)}")
            print(f"   No overnight holds: {halal_config.get('no_overnight_holds', True)}")
        else:
            print("   Halal compliance: DISABLED")
        
        print("\n" + "=" * 60)
        print("Connection test completed successfully!")
        print("You can now run the bot with: python run_bot.py")
        print("=" * 60)
        
        connector.shutdown()
        return True
    else:
        print("   ✗ Failed to connect to MT5")
        print("\nTroubleshooting:")
        print("  - Ensure MT5 terminal is running")
        print("  - Check credentials in config.json")
        print("  - Verify server name is correct")
        print("  - Try leaving credentials empty to use active session")
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

