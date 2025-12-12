#!/usr/bin/env python3
"""
Run Bot with Real-Time Monitoring
Launches the trading bot with comprehensive real-time monitoring and reconciliation.

Features:
- Bot runs normally with all filters and Micro-HFT enabled
- Real-time trade monitoring
- Broker data reconciliation from MT5
- Performance tracking and reporting
- Automated optimization suggestions
"""

import os
import sys
import json
import threading
import signal
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.trading_bot import TradingBot
from monitor.realtime_bot_monitor import RealtimeBotMonitor
from utils.logger_factory import get_logger

# Setup logging
logger = get_logger("system_startup", "logs/live/system/system_startup.log")


class BotWithMonitoring:
    """Runs bot with integrated real-time monitoring."""
    
    def __init__(self, config_path: str = 'config.json'):
        """Initialize bot with monitoring."""
        self.config_path = config_path
        
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Initialize bot
        logger.info("Initializing trading bot...")
        self.bot = TradingBot(config_path)
        
        # Initialize monitoring
        logger.info("Initializing real-time monitoring...")
        reconciliation_interval = self.config.get('monitoring', {}).get('reconciliation_interval_minutes', 30)
        self.monitor = RealtimeBotMonitor(self.config, reconciliation_interval)
        
        # Control flags
        self.running = False
        self.stop_event = threading.Event()
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()
    
    def start(self):
        """Start bot with monitoring."""
        try:
            logger.info("=" * 80)
            logger.info("STARTING TRADING BOT WITH REAL-TIME MONITORING")
            logger.info("=" * 80)
            logger.info(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("")
            
            # Connect to MT5
            if not self.bot.connect():
                logger.error("Failed to connect to MT5. Exiting.")
                return False
            
            logger.info("[OK] MT5 connection established")
            logger.info("")
            
            # Start monitoring
            logger.info("Starting real-time monitoring...")
            self.monitor.start_monitoring()
            logger.info("[OK] Monitoring started")
            logger.info("")
            
            # Start bot
            logger.info("Starting trading bot...")
            self.running = True
            self.bot.running = True
            
            # Start trailing stops (includes position monitoring)
            self.bot.start_continuous_trailing_stop()
            
            # Run main bot loop using bot's native run method
            logger.info("Bot running with monitoring active")
            logger.info("Press Ctrl+C to stop")
            logger.info("")
            
            # Use bot's native run method in a thread to allow monitoring
            cycle_interval = self.config.get('trading', {}).get('cycle_interval_seconds', 20)
            bot_thread = threading.Thread(
                target=self.bot.run,
                args=(cycle_interval,),
                name="TradingBot",
                daemon=True
            )
            bot_thread.start()
            
            # Keep main thread alive for monitoring
            try:
                bot_thread.join()
            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            
            return True
        
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            return False
        except Exception as e:
            logger.error(f"Error starting bot: {e}", exc_info=True)
            return False
        finally:
            self.stop()
    
    def _run_bot_loop(self):
        """Main bot execution loop."""
        cycle_interval = self.config.get('trading', {}).get('cycle_interval_seconds', 20)
        
        while self.running and not self.stop_event.is_set():
            try:
                # Check kill switch
                if self.bot.check_kill_switch():
                    logger.warning("Kill switch active - pausing trading")
                    time.sleep(60)
                    continue
                
                # Check cooldown
                if self.bot.is_in_cooldown():
                    logger.info("Bot in cooldown period - waiting...")
                    time.sleep(60)
                    continue
                
                # Scan for opportunities
                opportunities = self.bot.scan_for_opportunities()
                
                if opportunities:
                    logger.info(f"Found {len(opportunities)} trading opportunity(ies)")
                    
                    # Sort by quality score (if available)
                    opportunities.sort(key=lambda x: x.get('quality_score', 0), reverse=True)
                    
                    # Execute trades (respecting max_open_trades)
                    for opportunity in opportunities:
                        if self.stop_event.is_set():
                            break
                        
                        # Check max open trades
                        max_trades = self.config.get('risk', {}).get('max_open_trades', 6)
                        current_positions = self.bot.order_manager.get_open_positions()
                        
                        if len(current_positions) >= max_trades:
                            logger.info(f"Max open trades ({max_trades}) reached - skipping opportunities")
                            break
                        
                        # Execute trade
                        self.bot.execute_trade(opportunity)
                
                # Update daily P&L
                self.bot.update_daily_pnl()
                
                # Sleep for cycle interval
                import time
                self.stop_event.wait(cycle_interval)
            
            except Exception as e:
                logger.error(f"Error in bot loop: {e}", exc_info=True)
                self.bot.handle_error(e, "Main bot loop")
                import time
                time.sleep(cycle_interval)
    
    def stop(self):
        """Stop bot and monitoring gracefully."""
        if not self.running:
            return
        
        logger.info("Stopping bot and monitoring...")
        self.running = False
        self.stop_event.set()
        
        # Stop bot
        if self.bot:
            self.bot.running = False
            self.bot.stop_continuous_trailing_stop()
            self.bot.mt5_connector.shutdown()
        
        # Stop monitoring
        if self.monitor:
            self.monitor.stop_monitoring()
        
        # Generate final summary
        if self.monitor:
            summary = self.monitor.get_monitoring_summary()
            logger.info("")
            logger.info("=" * 80)
            logger.info("FINAL MONITORING SUMMARY")
            logger.info("=" * 80)
            logger.info(f"HFT Trades: {summary['hft_trades']}")
            logger.info(f"HFT Sweet Spot Rate: {summary['hft_sweet_spot_rate']:.1f}%")
            logger.info(f"Skipped Symbols: {summary['skipped_symbols_count']}")
            logger.info(f"Missed Opportunities: {summary['missed_opportunities']}")
            logger.info("=" * 80)
        
        logger.info("Bot and monitoring stopped")
    
    def run(self):
        """Run bot with monitoring."""
        try:
            return self.start()
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            return False


def main():
    """Main entry point."""
    import argparse
    
    # Get project root directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_config = os.path.join(project_root, 'config.json')
    
    parser = argparse.ArgumentParser(description='Run trading bot with real-time monitoring')
    parser.add_argument('--config', default=default_config, help='Path to config file')
    parser.add_argument('--reconciliation-interval', type=int, default=30,
                       help='Reconciliation interval in minutes (default: 30)')
    
    args = parser.parse_args()
    
    # Ensure config path is absolute
    if not os.path.isabs(args.config):
        args.config = os.path.join(project_root, args.config)
    
    # Override reconciliation interval if specified
    if args.reconciliation_interval:
        with open(args.config, 'r') as f:
            config = json.load(f)
        if 'monitoring' not in config:
            config['monitoring'] = {}
        config['monitoring']['reconciliation_interval_minutes'] = args.reconciliation_interval
        with open(args.config, 'w') as f:
            json.dump(config, f, indent=2)
    
    # Run bot with monitoring
    bot_monitor = BotWithMonitoring(args.config)
    success = bot_monitor.run()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    import time

    main()

