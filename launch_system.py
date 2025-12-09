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
from monitor.comprehensive_bot_monitor import ComprehensiveBotMonitor
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
        self.comprehensive_monitor: Optional[ComprehensiveBotMonitor] = None
        
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
            
            # Initialize Comprehensive Bot Monitor
            print("🔧 Initializing Comprehensive Bot Monitor...")
            logger.info("Initializing Comprehensive Bot Monitor...")
            self.comprehensive_monitor = ComprehensiveBotMonitor(self.config_path)
            print("✅ Comprehensive Bot Monitor initialized")
            logger.info("✅ Comprehensive Bot Monitor initialized")
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
            time.sleep(0.5)
            print()
            
            # Start Comprehensive Bot Monitor
            print("🚀 Starting Comprehensive Bot Monitor...")
            logger.info("Starting Comprehensive Bot Monitor...")
            self.comprehensive_monitor.start()
            print("✅ Comprehensive Bot Monitor started")
            logger.info("✅ Comprehensive Bot Monitor started")
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
                    
                    # Header
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.HEADER}📊 TRADING BOT - REAL-TIME SUMMARY{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    print(f"{Colors.BOLD}Time: {Colors.END}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    print()
                    
                    # ==================== ACCOUNT INFORMATION TABLE ====================
                    if account_info:
                        balance = account_info.get('balance', 0)
                        equity = account_info.get('equity', 0)
                        profit = account_info.get('profit', 0)
                        currency = account_info.get('currency', 'USD')
                        free_margin = account_info.get('free_margin', 0)
                        margin = account_info.get('margin', 0)
                        
                        profit_color = Colors.GREEN if profit >= 0 else Colors.RED
                        equity_color = Colors.GREEN if equity >= balance else Colors.YELLOW
                        
                        print(f"{Colors.BOLD}{Colors.BLUE}💰 ACCOUNT INFORMATION{Colors.END}")
                        print(f"{Colors.BLUE}{'-' * 120}{Colors.END}")
                        print(f"{Colors.BOLD}{'Metric':<25} | {'Value':<30} | {'Status':<30}{Colors.END}")
                        print(f"{Colors.BLUE}{'-' * 120}{Colors.END}")
                        print(f"{'Balance':<25} | ${balance:>12,.2f} {currency:<15} | {'Base Capital'}")
                        print(f"{'Equity':<25} | {equity_color}${equity:>12,.2f}{Colors.END} {currency:<15} | {equity_color}{'Above Balance' if equity >= balance else 'Below Balance'}{Colors.END}")
                        print(f"{'Floating P/L':<25} | {profit_color}${profit:>12,.2f}{Colors.END} {currency:<15} | {profit_color}{'Profit' if profit >= 0 else 'Loss'}{Colors.END}")
                        print(f"{'Free Margin':<25} | ${free_margin:>12,.2f} {currency:<15} | {'Available'}")
                        print(f"{'Used Margin':<25} | ${margin:>12,.2f} {currency:<15} | {'In Positions'}")
                        print(f"{Colors.BLUE}{'-' * 120}{Colors.END}")
                        print()
                    
                    # ==================== OPEN POSITIONS TABLE ====================
                    print(f"{Colors.BOLD}{Colors.BLUE}📊 OPEN POSITIONS ({len(positions)}){Colors.END}")
                    print(f"{Colors.BLUE}{'-' * 120}{Colors.END}")
                    
                    if positions:
                        # Table header
                        print(f"{Colors.BOLD}{'Ticket':<10} | {'Symbol':<12} | {'Type':<6} | {'Lot':<8} | {'Entry':<12} | {'Current':<12} | {'SL':<12} | {'P/L':<12} | {'Duration':<12} | {'Status'}{Colors.END}")
                        print(f"{Colors.BLUE}{'-' * 120}{Colors.END}")
                        
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
                            duration_str = str(duration).split('.')[0] if duration.total_seconds() > 0 else "0:00:00"
                            
                            profit_color = Colors.GREEN if profit >= 0 else Colors.RED
                            profit_symbol = "🟢" if profit >= 0 else "🔴"
                            
                            total_profit += profit
                            
                            # Determine HFT status
                            hft_status = ""
                            hft_color = ""
                            if 0.03 <= profit <= 0.10:
                                hft_status = "HFT Sweet Spot"
                                hft_color = Colors.YELLOW
                            elif profit > 0.10:
                                hft_status = "HFT Target"
                                hft_color = Colors.CYAN
                            else:
                                hft_status = "Active"
                                hft_color = Colors.BLUE
                            
                            # Format entry, current, and SL prices based on symbol precision
                            entry_str = f"{entry:.5f}" if entry < 1000 else f"{entry:.2f}"
                            current_str = f"{current:.5f}" if current < 1000 else f"{current:.2f}"
                            sl_str = f"{sl:.5f}" if sl < 1000 and sl > 0 else f"{sl:.2f}" if sl > 0 else "N/A"
                            
                            print(f"{profit_symbol} {ticket:<9} | {symbol:<12} | {pos_type:<6} | {volume:<8.4f} | {entry_str:<12} | {current_str:<12} | {sl_str:<12} | "
                                  f"{profit_color}${profit:>10,.2f}{Colors.END} | {duration_str:<12} | {hft_color}{hft_status}{Colors.END}")
                        
                        print(f"{Colors.BLUE}{'-' * 120}{Colors.END}")
                        total_color = Colors.GREEN if total_profit >= 0 else Colors.RED
                        print(f"{Colors.BOLD}{'TOTAL FLOATING P/L':<25} | {total_color}${total_profit:>12,.2f}{Colors.END}")
                    else:
                        print(f"{Colors.YELLOW}{'No open positions':<120}{Colors.END}")
                    
                    print()
                    
                    # ==================== TRADE STATISTICS TABLE ====================
                    total_trades = trade_stats.get('total_trades', 0)
                    successful = trade_stats.get('successful_trades', 0)
                    failed = trade_stats.get('failed_trades', 0)
                    filtered = trade_stats.get('filtered_opportunities', 0)
                    success_rate = (successful / total_trades * 100) if total_trades > 0 else 0
                    
                    # Session P/L
                    session_pnl = getattr(self.bot, 'session_pnl', daily_pnl) if self.bot else daily_pnl
                    session_pnl_color = Colors.GREEN if session_pnl >= 0 else Colors.RED
                    
                    # Daily P/L (realized + floating)
                    realized_pnl_today = getattr(self.bot, 'realized_pnl_today', 0.0) if self.bot else 0.0
                    floating_pnl = sum([p.get('profit', 0) for p in positions])
                    
                    print(f"{Colors.BOLD}{Colors.CYAN}📈 TRADE STATISTICS{Colors.END}")
                    print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                    print(f"{Colors.BOLD}{'Metric':<40} | {'Value':<20} | {'Details':<55}{Colors.END}")
                    print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                    
                    success_rate_color = Colors.GREEN if success_rate >= 70 else Colors.YELLOW if success_rate >= 50 else Colors.RED
                    print(f"{'Total Trades Executed':<40} | {total_trades:<20} | {'Trades attempted and executed'}")
                    print(f"{'Successful Trades':<40} | {Colors.GREEN}{successful:<20}{Colors.END} | {'Trades opened successfully'}")
                    print(f"{'Failed (Execution Errors)':<40} | {Colors.RED}{failed:<20}{Colors.END} | {'Actual order placement failures'}")
                    print(f"{'Filtered Opportunities':<40} | {Colors.YELLOW}{filtered:<20}{Colors.END} | {'Filtered by rules (not failures)'}")
                    print(f"{'Success Rate':<40} | {success_rate_color}{success_rate:>6.1f}%{Colors.END}{' ' * 13} | {'Successful / Total Trades'}")
                    print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                    
                    # P/L Statistics
                    print(f"{Colors.BOLD}{'Session P/L':<40} | {session_pnl_color}${session_pnl:>12,.2f}{Colors.END}{' ' * 7} | {'From session start'}")
                    print(f"{'Daily P/L (Total)':<40} | {Colors.GREEN if daily_pnl >= 0 else Colors.RED}${daily_pnl:>12,.2f}{Colors.END}{' ' * 7} | {'Realized + Floating'}")
                    print(f"{'  ├─ Realized P/L':<40} | ${realized_pnl_today:>12,.2f}{' ' * 7} | {'From closed trades today'}")
                    print(f"{'  └─ Floating P/L':<40} | ${floating_pnl:>12,.2f}{' ' * 7} | {'From open positions'}")
                    print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                    print()
                    
                    # ==================== MICRO-HFT PERFORMANCE TABLE ====================
                    if monitor_summary:
                        hft_trades = monitor_summary.get('hft_trades', 0)
                        sweet_spot_rate = monitor_summary.get('hft_sweet_spot_rate', 0)
                        sweet_spot_color = Colors.GREEN if sweet_spot_rate >= 70 else Colors.YELLOW if sweet_spot_rate >= 50 else Colors.RED
                        
                        print(f"{Colors.BOLD}{Colors.CYAN}⚡ MICRO-HFT PERFORMANCE{Colors.END}")
                        print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                        print(f"{Colors.BOLD}{'Metric':<50} | {'Value':<20} | {'Target':<45}{Colors.END}")
                        print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                        print(f"{'HFT Trades (Total)':<50} | {hft_trades:<20} | {'Micro-HFT trades executed'}")
                        print(f"{'Sweet Spot Capture Rate':<50} | {sweet_spot_color}{sweet_spot_rate:>6.1f}%{Colors.END}{' ' * 13} | {'Target: ≥80% ($0.03-$0.10 profit)'}")
                        print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                        print()
                    
                    # ==================== MONITORING STATUS TABLE ====================
                    bot_status_color = Colors.GREEN if (self.bot and self.bot.running) else Colors.RED
                    bot_status_text = 'Running' if (self.bot and self.bot.running) else 'Stopped'
                    
                    monitor_status_color = Colors.GREEN if (self.monitor and self.monitor.monitoring_active) else Colors.RED
                    monitor_status_text = 'Active' if (self.monitor and self.monitor.monitoring_active) else 'Inactive'
                    
                    print(f"{Colors.BOLD}{Colors.CYAN}🔍 MONITORING STATUS{Colors.END}")
                    print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                    print(f"{Colors.BOLD}{'Component':<50} | {'Status':<20} | {'Details':<45}{Colors.END}")
                    print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                    print(f"{'Bot Status':<50} | {bot_status_color}{bot_status_text:<20}{Colors.END} | {'Trading bot main loop'}")
                    print(f"{'Real-Time Monitoring':<50} | {monitor_status_color}{monitor_status_text:<20}{Colors.END} | {'Trade monitoring system'}")
                    print(f"{'Broker Reconciliation':<50} | {Colors.GREEN}{'Active':<20}{Colors.END} | {f'Every {self.reconciliation_interval} minutes'}")
                    print(f"{'Comprehensive Monitor':<50} | {Colors.GREEN}{'Active':<20}{Colors.END} | {'Performance analysis'}")
                    print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                    print()
                    
                    # Footer
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    print(f"{Colors.YELLOW}Display updates every {summary_interval:.0f}s | Trading runs at millisecond speeds | Press Ctrl+C to stop{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    
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
        
        # Stop Comprehensive Bot Monitor
        print("⏹️  Stopping Comprehensive Bot Monitor...")
        logger.info("Stopping Comprehensive Bot Monitor...")
        if self.comprehensive_monitor:
            self.comprehensive_monitor.stop()
            print("✅ Comprehensive Bot Monitor stopped")
            logger.info("✅ Comprehensive Bot Monitor stopped")
        print()
        
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
