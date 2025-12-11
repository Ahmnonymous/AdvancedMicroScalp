#!/usr/bin/env python3
"""
Reset Kill Switch Script
Resets the kill switch and error count to allow trading to resume.
"""

import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.trading_bot import TradingBot
from utils.logger_factory import get_logger

logger = get_logger("kill_switch_reset", "logs/system/kill_switch_reset.log")

def reset_kill_switch():
    """Reset kill switch and error count."""
    try:
        # Load config
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        # Create bot instance
        bot = TradingBot('config.json')
        
        # Connect to MT5
        if not bot.connect():
            logger.error("Failed to connect to MT5")
            print("❌ Failed to connect to MT5")
            return False
        
        # Reset kill switch
        bot.reset_kill_switch()
        
        logger.info("Kill switch and error count reset successfully")
        print("✅ Kill switch and error count reset successfully")
        print(f"   Kill switch active: {bot.kill_switch_active}")
        print(f"   Consecutive errors: {bot.consecutive_errors}")
        
        # Shutdown
        bot.mt5_connector.shutdown()
        
        return True
        
    except Exception as e:
        logger.error(f"Error resetting kill switch: {e}", exc_info=True)
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("RESET KILL SWITCH")
    print("=" * 60)
    print()
    
    success = reset_kill_switch()
    
    if success:
        print()
        print("✅ System is ready for trading")
        print("   Restart the bot to begin trading")
    else:
        print()
        print("❌ Failed to reset kill switch")
        sys.exit(1)
