#!/usr/bin/env python3
"""
Trading Bot Entry Point
Run this script to start the automated trading bot.
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
    """Main entry point for the trading bot."""
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
    
    # Log that bot is starting in background mode
    logger.info("=" * 80)
    logger.info("TRADING BOT STARTING IN BACKGROUND MODE")
    logger.info("=" * 80)
    
    try:
        # Create and start bot
        bot = TradingBot(config_path)
        
        # Connect to MT5
        if not bot.connect():
            logger.error("Failed to connect to MT5. Please check your credentials in config.json")
            sys.exit(1)
        
        # Reset kill switch and errors for fresh start
        bot.reset_kill_switch()
        logger.info("System initialized - kill switch reset, ready for trading")
        
        # Run bot (this will run continuously in background)
        cycle_interval = config.get('trading', {}).get('cycle_interval_seconds', 60)
        logger.info(f"Bot running in background. Main cycle: {cycle_interval}s, Trailing stop: 3.0s")
        bot.run(cycle_interval_seconds=cycle_interval)
    
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        error_logger.critical(f"Fatal error: {e}", exc_info=True)
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()

