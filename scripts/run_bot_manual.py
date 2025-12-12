#!/usr/bin/env python3
"""
Trading Bot Entry Point - Manual Approval Mode
Run this script to start the trading bot with manual batch trade approval.
The bot will ask you how many trades to take, show top setups, and require approval before executing.
"""

import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger_factory import get_logger
from bot.trading_bot import TradingBot

# System startup logger
logger = get_logger("system_startup", "logs/live/system/system_startup.log")
error_logger = get_logger("system_errors", "logs/live/system/system_errors.log")

def main():
    """Main entry point for the trading bot in manual approval mode."""
    # Get project root directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, 'config.json')
    
    # Check if config exists
    if not os.path.exists(config_path):
        print("ERROR: config.json not found!")
        print("Please create config.json with your MT5 credentials and settings.")
        sys.exit(1)
    
    # Load config
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Log that bot is starting in manual approval mode
    logger.info("=" * 80)
    logger.info("TRADING BOT STARTING IN MANUAL APPROVAL MODE")
    logger.info("=" * 80)
    
    print("\n" + "=" * 100)
    print("[BOT] TRADING BOT - MANUAL APPROVAL MODE")
    print("=" * 100)
    print("\nThis mode allows you to:")
    print("  1. Specify how many trades to take (1-6)")
    print("  2. Review top trading opportunities sorted by quality score")
    print("  3. Approve or reject each trade before execution")
    print("  4. Approve all remaining trades at once (type 'ALL')")
    print("  5. Sequential execution: Each trade completes before the next starts")
    print("\nAll existing safety features are active:")
    print("  [OK] Quality score filtering")
    print("  [OK] Risk management ($2 per trade)")
    print("  [OK] Spread and fees checks")
    print("  [OK] Portfolio risk limits")
    print("  [OK] Halal compliance (test/live mode)")
    print("  [OK] Stop-loss and trailing stops")
    print("  [OK] Position verification after each trade")
    print("=" * 100)
    
    try:
        # Create and start bot
        bot = TradingBot(config_path)
        
        # Connect to MT5
        if not bot.connect():
            logger.error("Failed to connect to MT5. Please check your credentials in config.json")
            print("\n[ERROR] Failed to connect to MT5. Please check your credentials in config.json")
            sys.exit(1)
        
        # Reset kill switch and errors for fresh start
        bot.reset_kill_switch()
        logger.info("System initialized - kill switch reset, ready for trading")
        print("\n[OK] Connected to MT5 successfully")
        print("[OK] System initialized and ready for trading\n")
        
        # Run bot in manual approval mode
        cycle_interval = config.get('trading', {}).get('cycle_interval_seconds', 60)
        print(f"Bot running in MANUAL APPROVAL MODE")
        print(f"Cycle interval: {cycle_interval}s\n")
        bot.run(cycle_interval_seconds=cycle_interval, manual_approval=True)
    
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("\n\n[OK] Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        error_logger.critical(f"Fatal error: {e}", exc_info=True)
        logger.critical(f"Fatal error: {e}")
        print(f"\n[ERROR] Fatal error: {e}")
        print("Check logs/live/system/system_errors.log for details")
        sys.exit(1)

if __name__ == '__main__':
    main()

