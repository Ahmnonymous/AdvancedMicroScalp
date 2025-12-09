#!/usr/bin/env python3
"""
Trading System Launcher
Single entry point that runs trading bot, monitoring, and reconciliation in parallel.
"""

import os
import sys
import json
import threading
import signal
import time
import atexit
from datetime import datetime, timedelta
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.trading_bot import TradingBot
from monitor.realtime_bot_monitor import RealtimeBotMonitor
from monitor.realtime_reconciliation import RealtimeReconciliation
from utils.logger_factory import get_logger

# Setup logging
logger = get_logger("system_startup", "logs/system/system_startup.log")
error_logger = get_logger("system_errors", "logs/system/system_errors.log")


class TradingSystemLauncher:
    """Launches and manages all trading system components."""
    
    def __init__(self, config_path: str = 'config.json', reconciliation_interval: int = 30):
        """
        Initialize the trading system launcher.
        
        Args:
            config_path: Path to configuration file
            reconciliation_interval: Reconciliation interval in minutes
        """
        self.config_path = config_path
        self.reconciliation_interval = reconciliation_interval
        
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Update config with reconciliation interval
        if 'monitoring' not in self.config:
            self.config['monitoring'] = {}
        self.config['monitoring']['reconciliation_interval_minutes'] = reconciliation_interval
        
        # Components
        self.bot: Optional[TradingBot] = None
        self.monitor: Optional[RealtimeBotMonitor] = None
        self.reconciliation: Optional[RealtimeReconciliation] = None
        
        # Threads
        self.bot_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None
        self.reconciliation_thread: Optional[threading.Thread] = None
        self.summary_thread: Optional[threading.Thread] = None
        
        # Control flags
        self.running = False
        self.shutdown_event = threading.Event()
        self.reconciliation_stop_event = threading.Event()
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Register cleanup on exit
        atexit.register(self._cleanup)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print("\n" + "=" * 80)
        print("SHUTDOWN SIGNAL RECEIVED")
        print("=" * 80)
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.stop()
    
    def _cleanup(self):
        """Ensure cleanup on exit."""
        if self.running:
            self.stop()
    
    def start(self):
        """Start all system components in parallel."""
        try:
            print("=" * 80)
            print("TRADING SYSTEM LAUNCHER")
            print("=" * 80)
            print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Reconciliation Interval: {self.reconciliation_interval} minutes")
            print("=" * 80)
            print()
            
            logger.info("=" * 80)
            logger.info("TRADING SYSTEM STARTING")
            logger.info("=" * 80)
            logger.info(f"Reconciliation Interval: {self.reconciliation_interval} minutes")
            logger.info("")
            
            # Initialize Trading Bot
            print("🔧 Initializing Trading Bot...")
            logger.info("Initializing Trading Bot...")
            self.bot = TradingBot(self.config_path)
            
            # Connect to MT5
            if not self.bot.connect():
                error_msg = "Failed to connect to MT5. Exiting."
                logger.error(error_msg)
                print(f"❌ {error_msg}")
                return False
            
            print("✅ Trading Bot initialized and connected to MT5")
            logger.info("✅ Trading Bot initialized and connected")
            print()
            
            # Initialize Real-Time Monitor
            print("🔧 Initializing Real-Time Monitor...")
            logger.info("Initializing Real-Time Monitor...")
            self.monitor = RealtimeBotMonitor(self.config, self.reconciliation_interval)
            print("✅ Real-Time Monitor initialized")
            logger.info("✅ Real-Time Monitor initialized")
            print()
            
            # Initialize Broker Reconciliation
            print("🔧 Initializing Broker Reconciliation...")
            logger.info("Initializing Broker Reconciliation...")
            self.reconciliation = RealtimeReconciliation(self.config, self.reconciliation_interval)
            print("✅ Broker Reconciliation initialized")
            logger.info("✅ Broker Reconciliation initialized")
            print()
            
            # Set running flag
            self.running = True
            
            # Start Trading Bot in separate thread
            print("🚀 Starting Trading Bot...")
            logger.info("Starting Trading Bot thread...")
            cycle_interval = self.config.get('trading', {}).get('cycle_interval_seconds', 20)
            self.bot.running = True
            self.bot.start_continuous_trailing_stop()
            
            self.bot_thread = threading.Thread(
                target=self._run_bot,
                args=(cycle_interval,),
                name="TradingBot",
                daemon=False
            )
            self.bot_thread.start()
            print("✅ Trading Bot started")
            logger.info("✅ Trading Bot thread started")
            time.sleep(0.5)  # Brief delay for bot initialization
            print()
            
            # Start Real-Time Monitor in separate thread
            print("🚀 Starting Real-Time Monitor...")
            logger.info("Starting Real-Time Monitor thread...")
            self.monitor.start_monitoring()
            
            # Monitor uses internal threads, but we track it
            self.monitor_thread = threading.Thread(
                target=self._monitor_thread_wrapper,
                name="MonitorWrapper",
                daemon=False
            )
            self.monitor_thread.start()
            print("✅ Real-Time Monitor started")
            logger.info("✅ Real-Time Monitor started")
            time.sleep(0.5)
            print()
            
            # Start Broker Reconciliation in separate thread
            print("🚀 Starting Broker Reconciliation...")
            logger.info("Starting Broker Reconciliation thread...")
            self.reconciliation_thread = threading.Thread(
                target=self._run_reconciliation,
                name="Reconciliation",
                daemon=False
            )
            self.reconciliation_thread.start()
            print("✅ Broker Reconciliation started")
            logger.info("✅ Broker Reconciliation started")
            print()
            
            print("=" * 80)
            print("ALL SYSTEMS OPERATIONAL")
            print("=" * 80)
            print("Press Ctrl+C to stop all components gracefully")
            print()
            logger.info("=" * 80)
            logger.info("ALL SYSTEMS OPERATIONAL")
            logger.info("=" * 80)
            
            # Start trade summary display thread (DISPLAY ONLY - does not affect trading speed)
            print("📊 Starting Trade Summary Display (updates every 5s - does NOT affect trading speed)...")
            logger.info("Starting Trade Summary Display (display-only, updates every 5 seconds)")
            print("   Note: Trading executes in milliseconds independently of display updates")
            self.summary_thread = threading.Thread(
                target=self._display_trade_summary_loop,
                name="TradeSummaryDisplay",
                daemon=False
            )
            self.summary_thread.start()
            print("✅ Trade Summary Display started")
            print()
            logger.info("Trade Summary Display started")
            
            # Keep main thread alive until shutdown
            try:
                while self.running and not self.shutdown_event.is_set():
                    time.sleep(1)
                    
                    # Check if threads are still alive
                    if self.bot_thread and not self.bot_thread.is_alive():
                        logger.warning("Trading Bot thread died unexpectedly")
                        break
                    if self.reconciliation_thread and not self.reconciliation_thread.is_alive():
                        logger.warning("Reconciliation thread died unexpectedly")
                        break
            
            except KeyboardInterrupt:
                print("\nKeyboard interrupt received")
                logger.info("Keyboard interrupt received")
                self.stop()
            
            return True
        
        except Exception as e:
            error_msg = f"Error starting system: {e}"
            logger.error(error_msg, exc_info=True)
            error_logger.critical(error_msg, exc_info=True)
            print(f"❌ {error_msg}")
            self.stop()
            return False
    
    def _run_bot(self, cycle_interval: int):
        """Run trading bot in thread."""
        try:
            logger.info(f"Trading Bot thread started (cycle interval: {cycle_interval}s)")
            self.bot.run(cycle_interval_seconds=cycle_interval)
        except Exception as e:
            error_msg = f"Error in Trading Bot thread: {e}"
            logger.error(error_msg, exc_info=True)
            error_logger.error(error_msg, exc_info=True)
            self.shutdown_event.set()
    
    def _monitor_thread_wrapper(self):
        """Wrapper to keep monitor thread alive."""
        try:
            logger.info("Monitor wrapper thread started")
            while self.running and not self.shutdown_event.is_set():
                if self.monitor and not self.monitor.monitoring_active:
                    break
                time.sleep(5)
        except Exception as e:
            error_msg = f"Error in Monitor wrapper thread: {e}"
            logger.error(error_msg, exc_info=True)
    
    def _run_reconciliation(self):
        """Run broker reconciliation in thread."""
        try:
            logger.info("Reconciliation thread started")
            self.reconciliation.run_continuous(stop_event=self.reconciliation_stop_event)
        except Exception as e:
            error_msg = f"Error in Reconciliation thread: {e}"
            logger.error(error_msg, exc_info=True)
            error_logger.error(error_msg, exc_info=True)
    
    def _display_trade_summary_loop(self):
        """
        Display real-time trade summary in a loop.
        
        IMPORTANT: This is DISPLAY-ONLY and does NOT affect trading speed.
        Trading executes independently at millisecond speeds:
        - Micro-HFT checks: Every 50ms
        - Fast trailing stop: Every 300ms (for profitable positions)
        - Normal trailing stop: Every 3 seconds
        - Trade execution: Instant (milliseconds)
        
        This summary only refreshes the display every 15 seconds for readability.
        """
        import os
        
        # ANSI color codes
        class Colors:
            HEADER = '\033[95m'
            BLUE = '\033[94m'
            CYAN = '\033[96m'
            GREEN = '\033[92m'
            YELLOW = '\033[93m'
            RED = '\033[91m'
            END = '\033[0m'
            BOLD = '\033[1m'
        
        def clear_screen():
            """Clear terminal screen."""
            os.system('cls' if os.name == 'nt' else 'clear')
        
        # Display update interval (5 seconds) - this is ONLY for display, NOT trading
        # Trading happens at millisecond speeds independently
        summary_interval = 5.0  # Update DISPLAY every 5 seconds
        
        try:
            while self.running and not self.shutdown_event.is_set():
                try:
                    # Get account info
                    account_info = None
                    if self.bot and self.bot.mt5_connector:
                        account_info = self.bot.mt5_connector.get_account_info()
                    
                    # Get open positions (excluding Dec 8)
                    positions = []
                    if self.bot and self.bot.order_manager:
                        positions = self.bot.order_manager.get_open_positions(exclude_dec8=True)
                    
                    # Get trade statistics
                    trade_stats = {}
                    daily_pnl = 0.0
                    if self.bot:
                        trade_stats = getattr(self.bot, 'trade_stats', {})
                        daily_pnl = getattr(self.bot, 'daily_pnl', 0.0)
                    
                    # Get monitoring summary
                    monitor_summary = {}
                    if self.monitor:
                        monitor_summary = self.monitor.get_monitoring_summary()
                    
                    # Clear screen and display summary
                    clear_screen()
                    
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.HEADER}📊 TRADING BOT - REAL-TIME SUMMARY{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
                    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    print()
                    
                    # Account Info
                    if account_info:
                        balance = account_info.get('balance', 0)
                        equity = account_info.get('equity', 0)
                        profit = account_info.get('profit', 0)
                        currency = account_info.get('currency', 'USD')
                        
                        profit_color = Colors.GREEN if profit >= 0 else Colors.RED
                        equity_color = Colors.GREEN if equity >= balance else Colors.YELLOW
                        
                        print(f"{Colors.BOLD}Account Information:{Colors.END}")
                        print(f"  Balance: ${balance:.2f} {currency}")
                        print(f"  Equity: {equity_color}${equity:.2f}{Colors.END} {currency}")
                        print(f"  Floating P/L: {profit_color}${profit:.2f}{Colors.END} {currency}")
                        print()
                    
                    # Open Positions
                    print(f"{Colors.BOLD}{Colors.BLUE}📊 OPEN POSITIONS ({len(positions)}){Colors.END}")
                    print(f"{Colors.BLUE}{'-' * 100}{Colors.END}")
                    
                    if positions:
                        total_profit = 0.0
                        for pos in positions:
                            ticket = pos.get('ticket', 0)
                            symbol = pos.get('symbol', 'N/A')
                            pos_type = pos.get('type', 'N/A')
                            volume = pos.get('volume', 0.0)
                            profit = pos.get('profit', 0.0)
                            sl = pos.get('sl', 0.0)
                            entry = pos.get('price_open', 0.0)
                            current = pos.get('price_current', 0.0)
                            time_open = pos.get('time_open', datetime.now())
                            
                            duration = datetime.now() - time_open if isinstance(time_open, datetime) else timedelta(0)
                            
                            profit_color = Colors.GREEN if profit >= 0 else Colors.RED
                            profit_symbol = "🟢" if profit >= 0 else "🔴"
                            
                            total_profit += profit
                            
                            # Determine if Micro-HFT applicable
                            hft_status = ""
                            if 0.03 <= profit <= 0.10:
                                hft_status = f"{Colors.YELLOW}[HFT Sweet Spot]"
                            elif profit > 0.10:
                                hft_status = f"{Colors.CYAN}[HFT Target]"
                            
                            print(f"{profit_symbol} {Colors.BOLD}Ticket {ticket}{Colors.END} | "
                                  f"{symbol} {pos_type} | "
                                  f"Lot: {volume:.4f} | "
                                  f"{profit_color}P/L: ${profit:.2f}{Colors.END} {hft_status}{Colors.END}")
                            print(f"   Entry: {entry:.5f} → Current: {current:.5f} | "
                                  f"SL: {sl:.5f} | "
                                  f"Duration: {str(duration).split('.')[0]}")
                        print(f"{Colors.BLUE}{'-' * 100}{Colors.END}")
                        total_color = Colors.GREEN if total_profit >= 0 else Colors.RED
                        print(f"{Colors.BOLD}Total Floating P/L: {total_color}${total_profit:.2f}{Colors.END}")
                    else:
                        print(f"{Colors.YELLOW}No open positions{Colors.END}")
                    
                    print()
                    
                    # Trade Statistics
                    print(f"{Colors.BOLD}{Colors.CYAN}📈 TRADE STATISTICS{Colors.END}")
                    print(f"{Colors.CYAN}{'-' * 100}{Colors.END}")
                    total_trades = trade_stats.get('total_trades', 0)
                    successful = trade_stats.get('successful_trades', 0)
                    failed = trade_stats.get('failed_trades', 0)
                    success_rate = (successful / total_trades * 100) if total_trades > 0 else 0
                    
                    print(f"Total Trades: {total_trades}")
                    print(f"{Colors.GREEN}Successful: {successful}{Colors.END}")
                    print(f"{Colors.RED}Failed: {failed}{Colors.END}")
                    print(f"Success Rate: {success_rate:.1f}%")
                    print(f"Daily P/L: {Colors.GREEN if daily_pnl >= 0 else Colors.RED}${daily_pnl:.2f}{Colors.END}")
                    print()
                    
                    # Micro-HFT Performance
                    if monitor_summary:
                        print(f"{Colors.BOLD}{Colors.CYAN}⚡ MICRO-HFT PERFORMANCE{Colors.END}")
                        print(f"{Colors.CYAN}{'-' * 100}{Colors.END}")
                        hft_trades = monitor_summary.get('hft_trades', 0)
                        sweet_spot_rate = monitor_summary.get('hft_sweet_spot_rate', 0)
                        print(f"HFT Trades: {hft_trades}")
                        print(f"Sweet Spot Rate: {Colors.GREEN if sweet_spot_rate >= 70 else Colors.YELLOW}{sweet_spot_rate:.1f}%{Colors.END}")
                        print()
                    
                    # Monitoring Status
                    print(f"{Colors.BOLD}{Colors.CYAN}🔍 MONITORING STATUS{Colors.END}")
                    print(f"{Colors.CYAN}{'-' * 100}{Colors.END}")
                    print(f"Bot Status: {Colors.GREEN if self.bot and self.bot.running else Colors.RED}{'Running' if self.bot and self.bot.running else 'Stopped'}{Colors.END}")
                    print(f"Monitoring: {Colors.GREEN if self.monitor and self.monitor.monitoring_active else Colors.RED}{'Active' if self.monitor and self.monitor.monitoring_active else 'Inactive'}{Colors.END}")
                    print(f"Reconciliation: {Colors.GREEN}Active (every {self.reconciliation_interval} min){Colors.END}")
                    print()
                    
                    # Footer
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
                    print(f"{Colors.YELLOW}Display updates every {summary_interval:.0f}s | Trading runs at millisecond speeds | Press Ctrl+C to stop{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.END}")
                    
                except Exception as e:
                    logger.error(f"Error displaying trade summary: {e}", exc_info=True)
                
                # Wait for next update
                self.shutdown_event.wait(summary_interval)
        
        except Exception as e:
            logger.error(f"Error in trade summary display loop: {e}", exc_info=True)
    
    def _display_final_summary(self):
        """Display final summary before shutdown."""
        try:
            # Get final data
            positions = []
            if self.bot and self.bot.order_manager:
                positions = self.bot.order_manager.get_open_positions(exclude_dec8=True)
            
            trade_stats = {}
            if self.bot:
                trade_stats = getattr(self.bot, 'trade_stats', {})
            
            monitor_summary = {}
            if self.monitor:
                monitor_summary = self.monitor.get_monitoring_summary()
            
            print("=" * 80)
            print("FINAL TRADE SUMMARY")
            print("=" * 80)
            print(f"Open Positions: {len(positions)}")
            print(f"Total Trades: {trade_stats.get('total_trades', 0)}")
            print(f"Successful: {trade_stats.get('successful_trades', 0)}")
            print(f"Failed: {trade_stats.get('failed_trades', 0)}")
            
            if monitor_summary:
                print(f"HFT Trades: {monitor_summary.get('hft_trades', 0)}")
                print(f"HFT Sweet Spot Rate: {monitor_summary.get('hft_sweet_spot_rate', 0):.1f}%")
            
            print("=" * 80)
            
        except Exception as e:
            logger.error(f"Error displaying final summary: {e}", exc_info=True)
    
    def stop(self):
        """Stop all components gracefully."""
        if not self.running:
            return
        
        print()
        print("=" * 80)
        print("INITIATING GRACEFUL SHUTDOWN")
        print("=" * 80)
        logger.info("=" * 80)
        logger.info("INITIATING GRACEFUL SHUTDOWN")
        logger.info("=" * 80)
        
        # Set shutdown flag
        self.running = False
        self.shutdown_event.set()
        self.reconciliation_stop_event.set()
        
        # Stop Trading Bot
        print("⏹️  Stopping Trading Bot...")
        logger.info("Stopping Trading Bot...")
        if self.bot:
            self.bot.running = False
            self.bot.stop_continuous_trailing_stop()
            time.sleep(1)  # Give time for threads to stop
        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=10.0)
            if self.bot_thread.is_alive():
                logger.warning("Trading Bot thread did not stop within timeout")
            else:
                print("✅ Trading Bot stopped")
                logger.info("✅ Trading Bot stopped")
        print()
        
        # Stop Real-Time Monitor
        print("⏹️  Stopping Real-Time Monitor...")
        logger.info("Stopping Real-Time Monitor...")
        if self.monitor:
            self.monitor.stop_monitoring()
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5.0)
            if self.monitor_thread.is_alive():
                logger.warning("Monitor thread did not stop within timeout")
            else:
                print("✅ Real-Time Monitor stopped")
                logger.info("✅ Real-Time Monitor stopped")
        print()
        
        # Stop Trade Summary Display
        print("⏹️  Stopping Trade Summary Display...")
        logger.info("Stopping Trade Summary Display...")
        if self.summary_thread and self.summary_thread.is_alive():
            self.summary_thread.join(timeout=3.0)
            if self.summary_thread.is_alive():
                logger.warning("Summary thread did not stop within timeout")
            else:
                print("✅ Trade Summary Display stopped")
                logger.info("✅ Trade Summary Display stopped")
        print()
        
        # Display final summary before stopping
        self._display_final_summary()
        
        # Stop Broker Reconciliation
        print("⏹️  Stopping Broker Reconciliation...")
        logger.info("Stopping Broker Reconciliation...")
        if self.reconciliation_thread and self.reconciliation_thread.is_alive():
            self.reconciliation_thread.join(timeout=5.0)
            if self.reconciliation_thread.is_alive():
                logger.warning("Reconciliation thread did not stop within timeout")
            else:
                print("✅ Broker Reconciliation stopped")
                logger.info("✅ Broker Reconciliation stopped")
        print()
        
        # Generate final reconciliation report
        print("📊 Generating final reconciliation report...")
        logger.info("Generating final reconciliation report...")
        try:
            if self.reconciliation:
                final_results = self.reconciliation.reconcile()
                if final_results:
                    report_file = self.reconciliation.generate_realtime_report(final_results)
                    print(f"✅ Final reconciliation report: {report_file}")
                    logger.info(f"Final reconciliation report: {report_file}")
        except Exception as e:
            logger.error(f"Error generating final reconciliation report: {e}", exc_info=True)
        print()
        
        # Generate monitoring summary
        print("📊 Generating monitoring summary...")
        logger.info("Generating monitoring summary...")
        try:
            if self.monitor:
                summary = self.monitor.get_monitoring_summary()
                print("=" * 80)
                print("MONITORING SUMMARY")
                print("=" * 80)
                print(f"HFT Trades: {summary.get('hft_trades', 0)}")
                print(f"HFT Sweet Spot Rate: {summary.get('hft_sweet_spot_rate', 0):.1f}%")
                print(f"Early Closures: {summary.get('early_closures', 0)}")
                print(f"Skipped Symbols: {summary.get('skipped_symbols_count', 0)}")
                print(f"Missed Opportunities: {summary.get('missed_opportunities', 0)}")
                print("=" * 80)
                logger.info("Monitoring Summary:")
                logger.info(f"  HFT Trades: {summary.get('hft_trades', 0)}")
                logger.info(f"  HFT Sweet Spot Rate: {summary.get('hft_sweet_spot_rate', 0):.1f}%")
                logger.info(f"  Skipped Symbols: {summary.get('skipped_symbols_count', 0)}")
        except Exception as e:
            logger.error(f"Error generating monitoring summary: {e}", exc_info=True)
        print()
        
        # Flush all logs
        print("💾 Flushing logs...")
        logger.info("Flushing all logs...")
        try:
            import logging
            for handler in logging.root.handlers[:]:
                handler.flush()
            print("✅ Logs flushed")
        except Exception as e:
            logger.error(f"Error flushing logs: {e}", exc_info=True)
        print()
        
        # Shutdown MT5 connection
        if self.bot:
            try:
                self.bot.mt5_connector.shutdown()
            except:
                pass
        
        print("=" * 80)
        print("SHUTDOWN COMPLETE")
        print("=" * 80)
        logger.info("=" * 80)
        logger.info("SHUTDOWN COMPLETE")
        logger.info("=" * 80)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Launch trading bot system with monitoring and reconciliation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Use default 30-minute reconciliation
  %(prog)s --reconciliation-interval 15  # Reconcile every 15 minutes
  %(prog)s --reconciliation-interval 60  # Reconcile every 60 minutes
        """
    )
    parser.add_argument(
        '--reconciliation-interval',
        type=int,
        default=30,
        metavar='MINUTES',
        help='Reconciliation interval in minutes (default: 30)'
    )
    
    args = parser.parse_args()
    
    # Validate reconciliation interval
    if args.reconciliation_interval < 1:
        print("❌ Error: Reconciliation interval must be at least 1 minute")
        sys.exit(1)
    
    # Create and start launcher
    launcher = TradingSystemLauncher(
        config_path='config.json',
        reconciliation_interval=args.reconciliation_interval
    )
    
    try:
        success = launcher.start()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        launcher.stop()
        sys.exit(0)
    except Exception as e:
        error_logger.critical(f"Fatal error: {e}", exc_info=True)
        print(f"❌ Fatal error: {e}")
        launcher.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
