#!/usr/bin/env python3
"""
Single-Window Trading System Launcher
Runs bot in background thread and monitor in foreground - all in one console.
"""

import sys
import os
import json
import threading
import time
import logging
import argparse
from bot.logger_setup import setup_logging
from bot.trading_bot import TradingBot
from monitor.monitor import monitor

# Global bot instance
bot_instance = None
bot_running = False

def run_bot_in_thread(test_mode=False):
    """Run the trading bot in a background thread."""
    global bot_instance, bot_running
    
    try:
        # Load config
        if not os.path.exists('config.json'):
            print("ERROR: config.json not found!")
            return
        
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        # Enable test mode if requested
        if test_mode:
            config.setdefault('pairs', {})['test_mode'] = True
            print("🧪 TEST MODE ENABLED: All symbols will be tested (restrictions ignored)")
        
        # Setup logging to file only (not console, to avoid interfering with monitor)
        logger = setup_logging(config, console_output=False)
        
        # Create bot instance
        bot_instance = TradingBot('config.json')
        bot_running = True
        
        # Connect to MT5
        if not bot_instance.connect():
            logger.error("Failed to connect to MT5")
            bot_running = False
            return
        
        # Reset kill switch and errors for fresh start
        bot_instance.reset_kill_switch()
        logger.info("System initialized - kill switch reset, ready for trading")
        
        # Run bot (this blocks until stopped)
        cycle_interval = config.get('trading', {}).get('cycle_interval_seconds', 30)
        trailing_interval = config.get('risk', {}).get('trailing_cycle_interval_seconds', 3.0)
        fast_interval = config.get('risk', {}).get('fast_trailing_interval_ms', 300) / 1000.0
        logger.info(f"Bot running in background thread. Main cycle: {cycle_interval}s, Trailing: {trailing_interval}s, Fast: {fast_interval:.3f}s")
        bot_instance.run(cycle_interval_seconds=cycle_interval)
    
    except KeyboardInterrupt:
        bot_running = False
        if bot_instance:
            bot_instance.shutdown()
    except Exception as e:
        bot_running = False
        logger = logging.getLogger(__name__)
        logger.critical(f"Fatal error in bot thread: {e}", exc_info=True)

def main():
    """Main launcher - single console window."""
    global bot_instance, bot_running
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Trading Bot Launcher')
    parser.add_argument('--test-mode', action='store_true', help='Enable test mode (ignores restrictions)')
    args = parser.parse_args()
    
    print("=" * 100)
    print("🤖 SCALPING TRADING SYSTEM - SINGLE WINDOW MODE")
    if args.test_mode:
        print("🧪 TEST MODE ENABLED")
    print("=" * 100)
    print()
    print("Starting system...")
    print("  - Trading bot: Background thread")
    print("  - Monitor: Foreground (this window)")
    print("  - Trailing stops: Normal 3s, Fast 300ms (when profit >= threshold)")
    print("  - Updates: Every 3 seconds")
    print()
    
    # Start bot in background thread
    print("🚀 Initializing trading bot...")
    bot_thread = threading.Thread(
        target=run_bot_in_thread,
        args=(args.test_mode,),
        name="TradingBot",
        daemon=True
    )
    bot_thread.start()
    
    # Wait for bot to initialize
    print("⏳ Waiting for bot initialization...")
    max_wait = 10
    waited = 0
    while bot_instance is None and waited < max_wait:
        time.sleep(1)
        waited += 1
        if waited % 2 == 0:
            print(f"   Still initializing... ({waited}s)")
    
    # Check if bot initialized successfully
    if bot_instance is None or not bot_running:
        print("❌ Error: Bot failed to initialize")
        print("Check bot_log.txt for details")
        sys.exit(1)
    
    print("✅ Bot initialized successfully")
    print("   - MT5 connection: Active")
    print("   - Trailing stop monitor: Running (normal: 3s, fast: 300ms)")
    print("   - Main trading cycle: 30s interval (medium-frequency)")
    print()
    print("=" * 100)
    print("📊 LAUNCHING REAL-TIME MONITOR")
    print("=" * 100)
    print("Monitor will update every 3 seconds showing:")
    print("  - Open positions with live P/L")
    print("  - Trailing stop adjustments")
    print("  - Big jump detections")
    print("  - Trade executions")
    print("  - Account status")
    print()
    time.sleep(2)
    
    # Run monitor in foreground (this blocks)
    try:
        monitor()
    except KeyboardInterrupt:
        print("\n\n" + "=" * 100)
        print("Shutting down system...")
        print("=" * 100)
        if bot_instance:
            bot_instance.shutdown()
        print("System stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error in monitor: {e}")
        if bot_instance:
            bot_instance.shutdown()
        sys.exit(1)

if __name__ == '__main__':
    main()
