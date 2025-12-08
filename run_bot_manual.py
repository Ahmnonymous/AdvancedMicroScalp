#!/usr/bin/env python3
"""
Trading Bot Entry Point - Manual Approval Mode
Run this script to start the trading bot with manual batch trade approval.
The bot will ask you how many trades to take, show top setups, and require approval before executing.
"""

import sys
import os
import json
from bot.logger_setup import setup_logging
from bot.trading_bot import TradingBot

def main():
    """Main entry point for the trading bot in manual approval mode."""
    # Check if config exists
    if not os.path.exists('config.json'):
        print("ERROR: config.json not found!")
        print("Please create config.json with your MT5 credentials and settings.")
        sys.exit(1)
    
    # Load config to setup logging
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Setup logging
    logger = setup_logging(config)
    
    # Log that bot is starting in manual approval mode
    logger.info("=" * 80)
    logger.info("TRADING BOT STARTING IN MANUAL APPROVAL MODE")
    logger.info("=" * 80)
    
    print("\n" + "=" * 100)
    print("🤖 TRADING BOT - MANUAL APPROVAL MODE")
    print("=" * 100)
    print("\nThis mode allows you to:")
    print("  1. Specify how many trades to take (1-6)")
    print("  2. Review top trading opportunities sorted by quality score")
    print("  3. Approve or reject each trade before execution")
    print("  4. Approve all remaining trades at once (type 'ALL')")
    print("  5. Sequential execution: Each trade completes before the next starts")
    print("\nAll existing safety features are active:")
    print("  ✓ Quality score filtering")
    print("  ✓ Risk management ($2 per trade)")
    print("  ✓ Spread and fees checks")
    print("  ✓ Portfolio risk limits")
    print("  ✓ Halal compliance (test/live mode)")
    print("  ✓ Stop-loss and trailing stops")
    print("  ✓ Position verification after each trade")
    print("=" * 100)
    
    try:
        # Create and start bot
        bot = TradingBot('config.json')
        
        # Connect to MT5
        if not bot.connect():
            logger.error("Failed to connect to MT5. Please check your credentials in config.json")
            print("\n❌ Failed to connect to MT5. Please check your credentials in config.json")
            sys.exit(1)
        
        # Reset kill switch and errors for fresh start
        bot.reset_kill_switch()
        logger.info("System initialized - kill switch reset, ready for trading")
        print("\n✅ Connected to MT5 successfully")
        print("✅ System initialized and ready for trading\n")
        
        # Run bot in manual approval mode
        cycle_interval = config.get('trading', {}).get('cycle_interval_seconds', 60)
        print(f"Bot running in MANUAL APPROVAL MODE")
        print(f"Cycle interval: {cycle_interval}s\n")
        bot.run(cycle_interval_seconds=cycle_interval, manual_approval=True)
    
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("\n\n✅ Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        print(f"\n❌ Fatal error: {e}")
        print("Check bot_log.txt for details")
        sys.exit(1)

if __name__ == '__main__':
    main()

