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
from typing import Optional, List, Dict, Any

try:
    from tabulate import tabulate
    TABULATE_AVAILABLE = True
except ImportError:
    TABULATE_AVAILABLE = False
    print("[WARNING] 'tabulate' library not found. Install with: pip install tabulate")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.trading_bot import TradingBot
from monitor.realtime_bot_monitor import RealtimeBotMonitor
from monitor.realtime_reconciliation import RealtimeReconciliation
from monitor.comprehensive_bot_monitor import ComprehensiveBotMonitor
from monitor.sl_realtime_monitor import SLRealtimeMonitor
from monitor.lightweight_realtime_logger import start_realtime_logger
from utils.logger_factory import get_logger
# Execution tracer disabled to save storage space
# from utils.execution_tracer import get_tracer, trace_function

# Import shared Colors utility
from utils.colors import Colors

# Setup logging
logger = get_logger("system_startup", "logs/live/system/system_startup.log")
error_logger = get_logger("system_errors", "logs/live/system/system_errors.log")


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
        self.sl_monitor: Optional[SLRealtimeMonitor] = None
        
        # Threads
        self.bot_thread: Optional[threading.Thread] = None
        self.monitor_thread: Optional[threading.Thread] = None
        self.reconciliation_thread: Optional[threading.Thread] = None
        self.summary_thread: Optional[threading.Thread] = None
        self.sl_monitor_thread: Optional[threading.Thread] = None
        self.lightweight_logger_thread: Optional[threading.Thread] = None
        
        # Control flags
        self.running = False
        self.shutdown_event = threading.Event()
        self.reconciliation_stop_event = threading.Event()
        
        # Track start time for display
        self.start_time: Optional[datetime] = None
        
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
        # Execution tracer disabled to save storage space
        # tracer = get_tracer()
        # tracer.trace(...)
        
        try:
            # MANDATORY OBSERVABILITY: Test system events logging on startup
            from utils.logger_factory import get_system_event_logger
            system_event_logger = get_system_event_logger()
            try:
                system_event_logger.systemEvent("BOOT_TEST", {"message": "system_events logging verified"})
                logger.info("[OK] System events logging self-test: PASSED")
            except Exception as e:
                logger.critical(f"[CRITICAL][EVENT_LOGGER_FAILED] System events logging failed: {e}")
                error_logger.critical(f"[CRITICAL][EVENT_LOGGER_FAILED] System events logging failed: {e}", exc_info=True)
            
            logger.info("=" * 80)
            logger.info("TRADING SYSTEM STARTING")
            logger.info("=" * 80)
            logger.info(f"Reconciliation Interval: {self.reconciliation_interval} minutes")
            logger.info("")
            
            # Record start time
            self.start_time = datetime.now()
            
            # Initialize Trading Bot
            logger.info("Initializing Trading Bot...")
            # Execution tracer disabled to save storage space
            self.bot = TradingBot(self.config_path)
            
            # Connect to MT5
            if not self.bot.connect():
                error_msg = "Failed to connect to MT5. Exiting."
                logger.error(error_msg)
                print(f"[ERROR] {error_msg}")
                return False
            
            logger.info("[OK] Trading Bot initialized and connected")
            
            # Real-Time Monitor disabled to save storage space
            # logger.info("Initializing Real-Time Monitor...")
            # self.monitor = RealtimeBotMonitor(self.config, self.reconciliation_interval)
            # logger.info("[OK] Real-Time Monitor initialized")
            self.monitor = None
            
            # Broker Reconciliation disabled to save storage space
            # logger.info("Initializing Broker Reconciliation...")
            # bot_session_start = self.bot.session_start_time if self.bot and hasattr(self.bot, 'session_start_time') else None
            # self.reconciliation = RealtimeReconciliation(self.config, self.reconciliation_interval, session_start_time=bot_session_start)
            # logger.info("[OK] Broker Reconciliation initialized")
            self.reconciliation = None
            
            # Comprehensive Bot Monitor disabled to save storage space
            # logger.info("Initializing Comprehensive Bot Monitor...")
            # self.comprehensive_monitor = ComprehensiveBotMonitor(self.config_path)
            # logger.info("[OK] Comprehensive Bot Monitor initialized")
            self.comprehensive_monitor = None
            
            # Initialize SL Realtime Monitor
            logger.info("Initializing SL Realtime Monitor...")
            self.sl_monitor = SLRealtimeMonitor(self.bot)
            logger.info("[OK] SL Realtime Monitor initialized")
            
            # Set running flag
            self.running = True
            
            # Start Trading Bot in separate thread
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
            logger.info("[OK] Trading Bot thread started")
            time.sleep(0.5)  # Brief delay for bot initialization
            
            # Real-Time Monitor disabled to save storage space
            # logger.info("Starting Real-Time Monitor thread...")
            # self.monitor.start_monitoring()
            # self.monitor_thread = threading.Thread(...)
            # self.monitor_thread.start()
            # logger.info("[OK] Real-Time Monitor started")
            self.monitor_thread = None
            
            # Broker Reconciliation disabled to save storage space
            # logger.info("Starting Broker Reconciliation thread...")
            # self.reconciliation_thread = threading.Thread(...)
            # self.reconciliation_thread.start()
            # logger.info("[OK] Broker Reconciliation started")
            self.reconciliation_thread = None
            
            # Comprehensive Bot Monitor disabled to save storage space
            # logger.info("Starting Comprehensive Bot Monitor...")
            # self.comprehensive_monitor.start()
            # logger.info("[OK] Comprehensive Bot Monitor started")
            
            # Start SL Worker (500ms cadence)
            logger.info("Starting SL Worker...")
            if self.bot and self.bot.risk_manager and hasattr(self.bot.risk_manager, 'sl_manager'):
                sl_manager = self.bot.risk_manager.sl_manager
                
                # CRITICAL FIX: Check if sl_manager is not None
                if sl_manager is not None:
                    # Initialize and start watchdog
                    from monitor.sl_watchdog import SLWatchdog
                    watchdog = SLWatchdog(sl_manager)
                    watchdog.start()
                    
                    # CRITICAL SAFETY FIX #5: Initialize and start SL Safety Guard
                    from monitor.sl_safety_guard import SLSafetyGuard
                    sl_safety_guard = SLSafetyGuard(
                        order_manager=self.bot.order_manager,
                        risk_manager=self.bot.risk_manager,
                        trading_bot=self.bot
                    )
                    sl_safety_guard.start()
                    logger.info("[OK] SL Safety Guard started")
                    
                    # Start SL worker with watchdog
                    sl_manager.start_sl_worker(watchdog=watchdog)
                    logger.info("[OK] SL Worker started (500ms cadence)")
                else:
                    logger.warning("SL Manager is None - skipping SL Worker")
            else:
                logger.warning("SL Manager not available - skipping SL Worker")
            
            # Start SL Realtime Monitor (integrated mode, no separate console output)
            logger.info("Starting SL Realtime Monitor...")
            self.sl_monitor.start(standalone_display=False)
            logger.info("[OK] SL Realtime Monitor started")
            
            logger.info("=" * 80)
            logger.info("ALL SYSTEMS OPERATIONAL")
            logger.info("=" * 80)
            
            # Start trade summary display thread (ONLY console output)
            logger.info("Starting Trade Summary Display (display-only, updates every 5 seconds)")
            self.summary_thread = threading.Thread(
                target=self._display_trade_summary_loop,
                name="TradeSummaryDisplay",
                daemon=False
            )
            self.summary_thread.start()
            logger.info("Trade Summary Display started")
            
            # Start heartbeat monitoring thread (silent - no console output)
            logger.info("Starting System Health Monitor")
            self.heartbeat_thread = threading.Thread(
                target=self._heartbeat_monitor_loop,
                name="HeartbeatMonitor",
                daemon=True
            )
            self.heartbeat_thread.start()
            
            # Start lightweight real-time logger (file logging only, no console output)
            logger.info("Starting Lightweight Real-Time Logger (file logging only)")
            
            # Create a function to get bot state (called each second by logger)
            def get_bot_state():
                if self.bot:
                    return self.bot.get_bot_state()
                return {
                    'running': False,
                    'current_state': 'IDLE',
                    'current_symbol': 'N/A',
                    'last_action': 'N/A',
                    'last_action_time': None,
                    'sl_update_times': {},
                    'trade_stats': {}
                }
            
            # Get SL Manager for logger
            sl_manager = None
            if self.bot and self.bot.risk_manager and hasattr(self.bot.risk_manager, 'sl_manager'):
                sl_manager = self.bot.risk_manager.sl_manager
            
            # Start logger thread (file logging only, no console output)
            self.lightweight_logger_thread = start_realtime_logger(
                mt5_connector=self.bot.mt5_connector if self.bot else None,
                bot_state_getter=get_bot_state,
                shutdown_event=self.shutdown_event,
                sl_manager=sl_manager,
                console_output=False  # Disable console output
            )
            logger.info("[OK] Lightweight Real-Time Logger started (file logging only)")
            
            # Keep main thread alive until shutdown
            try:
                while self.running and not self.shutdown_event.is_set():
                    time.sleep(1)
                    
                    # Check if threads are still alive
                    if self.bot_thread and not self.bot_thread.is_alive():
                        logger.warning("Trading Bot thread died unexpectedly")
                        break
                    # Reconciliation thread disabled to save storage space
                    # if self.reconciliation_thread and not self.reconciliation_thread.is_alive():
                    #     logger.warning("Reconciliation thread died unexpectedly")
                    #     break
            
            except KeyboardInterrupt:
                print("\nKeyboard interrupt received")
                logger.info("Keyboard interrupt received")
                self.stop()
            
            return True
        
        except Exception as e:
            error_msg = f"Error starting system: {e}"
            logger.error(error_msg, exc_info=True)
            error_logger.critical(error_msg, exc_info=True)
            print(f"[ERROR] {error_msg}")
            self.stop()
            return False
    
    def _run_bot(self, cycle_interval: int):
        """Run trading bot in thread."""
        try:
            logger.info(f"Trading Bot thread started (cycle interval: {cycle_interval}s)")
            self.bot.run(cycle_interval_seconds=cycle_interval)
        except KeyboardInterrupt:
            logger.info("Trading Bot thread stopped by user (KeyboardInterrupt)")
            self.shutdown_event.set()
        except Exception as e:
            # CRITICAL FIX: Don't shut down on exceptions - the bot's internal loop should handle errors
            # Only set shutdown event for fatal errors that require system restart
            error_msg = f"Error in Trading Bot thread: {e}"
            logger.error(error_msg, exc_info=True)
            error_logger.error(error_msg, exc_info=True)
            # The bot's internal loop should continue running even if this thread encounters an error
            # Only shut down if it's a truly fatal error (e.g., bot.run() returned unexpectedly)
            logger.warning("Trading Bot thread encountered error, but bot should continue in its internal loop")
            # Don't set shutdown_event - let the bot's internal error handling manage recovery
    
    def _monitor_thread_wrapper(self):
        """Wrapper to keep monitor thread alive."""
        try:
            logger.info("Monitor wrapper thread started")
            while self.running and not self.shutdown_event.is_set():
                # Real-Time Monitor disabled to save storage space
                # if self.monitor and not self.monitor.monitoring_active:
                #     break
                break  # Exit immediately since monitor is disabled
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
        
        This summary only refreshes the display every 5 seconds for readability.
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
                    realized_pnl_today = 0.0
                    if self.bot:
                        trade_stats = getattr(self.bot, 'trade_stats', {})
                        daily_pnl = getattr(self.bot, 'daily_pnl', 0.0)
                        realized_pnl_today = getattr(self.bot, 'realized_pnl_today', 0.0)
                    
                    # Get monitoring summary
                    # Real-Time Monitor disabled to save storage space
                    monitor_summary = {}
                    # if self.monitor:
                    #     monitor_summary = self.monitor.get_monitoring_summary()
                    
                    # Calculate Session P/L early (needed for account information table)
                    # CRITICAL FIX: Calculate session P/L directly from balance difference for accuracy
                    if self.bot and account_info:
                        # Get session start balance
                        session_start_balance = getattr(self.bot, 'session_start_balance', None)
                        if session_start_balance is not None and session_start_balance > 0:
                            # Calculate session P/L as: Current Balance - Session Start Balance
                            current_balance = account_info.get('balance', 0)
                            session_pnl = current_balance - session_start_balance
                        else:
                            # Fallback: Try to get from bot's calculated value
                            session_pnl = getattr(self.bot, 'session_pnl', None)
                            if session_pnl is None:
                                # Final fallback to daily_pnl
                                session_pnl = daily_pnl
                    else:
                        # No bot or account info - use daily_pnl as fallback
                        session_pnl = daily_pnl
                    session_pnl_color = Colors.GREEN if session_pnl >= 0 else Colors.RED
                    
                    # Clear screen and display summary
                    clear_screen()
                    
                    # Header
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.HEADER}📊 TRADING BOT - REAL-TIME SUMMARY{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    
                    # Start time, total time, and current time in one line
                    if self.start_time:
                        start_time_str = self.start_time.strftime('%Y-%m-%d %H:%M:%S')
                        total_time = datetime.now() - self.start_time
                        total_hours = int(total_time.total_seconds() // 3600)
                        total_minutes = int((total_time.total_seconds() % 3600) // 60)
                        total_seconds = int(total_time.total_seconds() % 60)
                        total_time_str = f"{total_hours:02d}:{total_minutes:02d}:{total_seconds:02d}"
                        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        print(f"{Colors.BOLD}Start Time: {Colors.END}{start_time_str} | {Colors.BOLD}Total Time: {Colors.END}{total_time_str} | {Colors.BOLD}Current Time: {Colors.END}{current_time_str}")
                    else:
                        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        print(f"{Colors.BOLD}Current Time: {Colors.END}{current_time_str}")
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
                        
                        if TABULATE_AVAILABLE:
                            account_table = [
                                [f"${balance:,.2f} {currency}", 
                                 f"{equity_color}${equity:,.2f} {currency}{Colors.END}", 
                                 f"{profit_color}${profit:,.2f} {currency}{Colors.END}", 
                                 f"${free_margin:,.2f} {currency}", 
                                 f"${margin:,.2f} {currency}",
                                 f"{session_pnl_color}${session_pnl:,.2f} {currency}{Colors.END}"]
                            ]
                            headers = ["Balance", "Equity", "Floating P/L", "Free Margin", "Used Margin", "Session P/L"]
                            print(tabulate(account_table, headers=headers, tablefmt="grid", stralign="right"))
                        else:
                            # Fallback to simple format
                            print(f"{Colors.BOLD}{'Balance':<20} | {'Equity':<20} | {'Floating P/L':<20} | {'Free Margin':<20} | {'Used Margin':<20} | {'Session P/L':<20}{Colors.END}")
                            print(f"{Colors.BLUE}{'-' * 140}{Colors.END}")
                            print(f"${balance:>12,.2f} {currency:<6} | {equity_color}${equity:>12,.2f}{Colors.END} {currency:<6} | {profit_color}${profit:>12,.2f}{Colors.END} {currency:<6} | ${free_margin:>12,.2f} {currency:<6} | ${margin:>12,.2f} {currency:<6} | {session_pnl_color}${session_pnl:>12,.2f}{Colors.END} {currency:<6}")
                        print()
                    
                    # ==================== OPEN POSITIONS TABLE ====================
                    print(f"{Colors.BOLD}{Colors.BLUE}📊 OPEN POSITIONS ({len(positions)}){Colors.END}")
                    
                    # Initialize total_profit before positions check (needed for logging)
                    total_profit = 0.0
                    
                    if positions:
                        positions_table = []
                        
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
                            profit_symbol = "[+]" if profit >= 0 else "[-]"
                            
                            total_profit += profit
                            
                            # Determine HFT status
                            hft_status = ""
                            if 0.03 <= profit <= 0.10:
                                hft_status = f"{Colors.YELLOW}HFT Sweet Spot{Colors.END}"
                            elif profit > 0.10:
                                hft_status = f"{Colors.CYAN}HFT Target{Colors.END}"
                            else:
                                hft_status = "Active"
                            
                            # CRITICAL: Calculate effective SL in profit terms from ACTUAL broker-applied SL
                            # This reflects the real SL price applied in MT5, converted to profit terms
                            effective_sl_profit = -2.0  # Default to -$2.00 loss protection
                            sl_status_indicator = ""
                            is_verified = False
                            potential_pl_if_sl_hits = -2.0  # Default potential P/L
                            sl_violation = False
                            sl_violation_msg = ""
                            last_sl_update_time_str = "N/A"
                            last_sl_update_reason = "N/A"
                            
                            if self.bot and self.bot.risk_manager:
                                try:
                                    # Get fresh position data to ensure we use actual broker SL
                                    fresh_pos = self.bot.order_manager.get_position_by_ticket(pos.get('ticket', 0))
                                    if fresh_pos:
                                        # Calculate effective SL from ACTUAL broker SL price
                                        effective_sl_profit, is_verified = self.bot.risk_manager.calculate_effective_sl_in_profit_terms(fresh_pos, check_pending=True)
                                        
                                        # Calculate potential P/L if SL hits now
                                        potential_pl_if_sl_hits = self.bot.risk_manager.calculate_potential_pl_if_sl_hits(fresh_pos)
                                        
                                        # Integrated SL monitoring: Update tracking, detect violations, determine status
                                        ticket = fresh_pos.get('ticket')
                                        current_profit = fresh_pos.get('profit', profit)
                                        
                                        # Initialize default values
                                        last_sl_update_time_str = "N/A"
                                        last_sl_update_reason = "N/A"
                                        
                                        # Update SL tracking and check for violations (if SL monitor available)
                                        if self.sl_monitor:
                                            try:
                                                # Update tracking first
                                                self.sl_monitor._update_sl_tracking(fresh_pos, effective_sl_profit)
                                                
                                                # Get last update info
                                                if ticket in self.sl_monitor.last_sl_update_time:
                                                    time_diff = (datetime.now() - self.sl_monitor.last_sl_update_time[ticket]).total_seconds()
                                                    if time_diff < 60:
                                                        last_sl_update_time_str = f"{time_diff:.1f}s"
                                                    else:
                                                        last_sl_update_time_str = f"{time_diff/60:.1f}m"
                                                last_sl_update_reason = self.sl_monitor.last_sl_update_reason.get(ticket, "N/A")
                                                
                                                # Check for SL violations
                                                violations = self.sl_monitor._detect_violations(fresh_pos, effective_sl_profit)
                                                if violations:
                                                    sl_violation = True
                                                    critical = any(v.get('severity') == 'critical' for v in violations)
                                                    sl_violation_msg = violations[0].get('message', 'Violation detected')
                                                    # Log violations
                                                    self.sl_monitor._log_violations(violations)
                                                    # Violation takes priority - set status indicator
                                                    sl_status_indicator = f"{Colors.RED}[V]{Colors.END}" if critical else f"{Colors.YELLOW}[W]{Colors.END}"
                                                else:
                                                    # No violations - determine normal status based on profit and SL
                                                    if current_profit < 0:
                                                        # Negative P/L - check if at strict loss
                                                        sl_manager = self.bot.risk_manager.sl_manager if hasattr(self.bot.risk_manager, 'sl_manager') else None
                                                        max_risk = sl_manager.max_risk_usd if sl_manager else 2.0
                                                        if abs(effective_sl_profit + max_risk) < 0.05:
                                                            sl_status_indicator = f"{Colors.GREEN}[OK]{Colors.END}"  # Protected at -$2.00
                                                        else:
                                                            sl_status_indicator = f"{Colors.YELLOW}[W]{Colors.END}"  # Pending strict loss
                                                    elif 0.03 <= current_profit <= 0.10:
                                                        # In sweet spot - check verification status
                                                        if is_verified and effective_sl_profit >= 0.03:
                                                            sl_status_indicator = f"{Colors.GREEN}[OK]{Colors.END}"  # Locked and verified
                                                        else:
                                                            sl_status_indicator = f"{Colors.YELLOW}[W]{Colors.END}"  # Pending lock
                                                    elif effective_sl_profit >= 0.03:
                                                        # Above sweet spot - verified lock
                                                        sl_status_indicator = f"{Colors.GREEN}[OK]{Colors.END}" if is_verified else f"{Colors.YELLOW}[W]{Colors.END}"
                                                    else:
                                                        # Small positive profit (< $0.03) - check if break-even applied
                                                        if abs(effective_sl_profit) < 0.01:
                                                            sl_status_indicator = f"{Colors.GREEN}[OK]{Colors.END}"  # Break-even applied
                                                        else:
                                                            sl_status_indicator = f"{Colors.YELLOW}[W]{Colors.END}"  # Waiting for break-even
                                            except Exception as e:
                                                # If SL monitoring fails, use basic status
                                                logger.debug(f"Error in SL monitoring for ticket {ticket}: {e}")
                                                if current_profit < 0:
                                                    sl_status_indicator = f"{Colors.YELLOW}[W]{Colors.END}"
                                                elif current_profit >= 0.03:
                                                    sl_status_indicator = f"{Colors.GREEN}[OK]{Colors.END}" if is_verified else f"{Colors.YELLOW}[W]{Colors.END}"
                                                else:
                                                    sl_status_indicator = f"{Colors.YELLOW}[W]{Colors.END}"
                                        else:
                                            # SL monitor not available - use basic status determination
                                                if current_profit < 0:
                                                    sl_status_indicator = f"{Colors.YELLOW}[W]{Colors.END}"
                                                elif current_profit >= 0.03:
                                                    sl_status_indicator = f"{Colors.GREEN}[OK]{Colors.END}" if is_verified else f"{Colors.YELLOW}[W]{Colors.END}"
                                                else:
                                                    sl_status_indicator = f"{Colors.YELLOW}[W]{Colors.END}"
                                    else:
                                        # Position closed - use last known profit
                                        effective_sl_profit, is_verified = self.bot.risk_manager.calculate_effective_sl_in_profit_terms(pos, check_pending=True)
                                        potential_pl_if_sl_hits = self.bot.risk_manager.calculate_potential_pl_if_sl_hits(pos)
                                except Exception as e:
                                    # Fallback to calculation from current position data
                                    try:
                                        effective_sl_profit, is_verified = self.bot.risk_manager.calculate_effective_sl_in_profit_terms(pos, check_pending=True)
                                        potential_pl_if_sl_hits = self.bot.risk_manager.calculate_potential_pl_if_sl_hits(pos)
                                    except:
                                        pass  # Keep default
                            
                            # Format effective SL for display (reflects ACTUAL broker-applied SL)
                            # Check if in sweet spot for pending status
                            is_sweet_spot = (0.03 <= profit <= 0.10)
                            
                            if effective_sl_profit < 0:
                                effective_sl_str = f"{Colors.RED}${effective_sl_profit:,.2f}{Colors.END}"
                            elif abs(effective_sl_profit) < 0.01:
                                if is_sweet_spot and not is_verified:
                                    effective_sl_str = f"{Colors.YELLOW}$0.00{Colors.END}"
                                else:
                                    effective_sl_str = f"{Colors.YELLOW}$0.00{Colors.END}"
                            elif is_sweet_spot:
                                # In sweet spot range
                                if is_verified:
                                    effective_sl_str = f"{Colors.GREEN}${effective_sl_profit:,.2f}{Colors.END}"
                                else:
                                    effective_sl_str = f"{Colors.YELLOW}${effective_sl_profit:,.2f}{Colors.END}"
                            else:
                                # Positive = locked profit (above sweet spot)
                                effective_sl_str = f"{Colors.GREEN}${effective_sl_profit:,.2f}{Colors.END}"
                            
                            # Calculate effective TP profit (similar to effective SL)
                            effective_tp_profit = 0.0
                            tp_price = pos.get('tp', 0.0)
                            if tp_price > 0 and self.bot and self.bot.mt5_connector:
                                try:
                                    # Get symbol info for contract size or trade_tick_value
                                    symbol_info = self.bot.mt5_connector.get_symbol_info(symbol)
                                    if symbol_info:
                                        # CRITICAL FIX: Use trade_tick_value for indices/commodities (like XAUUSDm)
                                        # For indices: Profit = (price_diff_in_points) * lot_size * point_value
                                        # For forex: Profit = (price_diff) * lot_size * contract_size
                                        point_value = symbol_info.get('trade_tick_value', None)
                                        contract_size = symbol_info.get('contract_size', 100000)
                                        point = symbol_info.get('point', 0.00001)
                                        
                                        # Calculate price difference
                                        if pos_type == 'BUY' or (isinstance(pos_type, int) and pos_type == 0):
                                            price_diff = tp_price - entry
                                        else:  # SELL
                                            price_diff = entry - tp_price
                                        
                                        # Use trade_tick_value if available (for indices/commodities)
                                        if point_value is not None and point_value > 0:
                                            # For indices/commodities: convert price_diff to points, then multiply by point_value
                                            price_diff_in_points = price_diff / point
                                            effective_tp_profit = price_diff_in_points * volume * point_value
                                        else:
                                            # For forex: use contract_size
                                            effective_tp_profit = price_diff * volume * contract_size
                                except Exception as e:
                                    logger.debug(f"Error calculating effective TP for ticket {ticket}: {e}")
                            
                            # Format effective TP for display
                            if effective_tp_profit > 0:
                                effective_tp_str = f"{Colors.GREEN}${effective_tp_profit:,.2f}{Colors.END}"
                            elif effective_tp_profit == 0:
                                effective_tp_str = "N/A"
                            else:
                                effective_tp_str = f"{Colors.RED}${effective_tp_profit:,.2f}{Colors.END}"
                            
                            # Format entry and current prices based on symbol precision
                            entry_str = f"{entry:.5f}" if entry < 1000 else f"{entry:.2f}"
                            current_str = f"{current:.5f}" if current < 1000 else f"{current:.2f}"
                            
                            # Add SL violation indicator to status if needed
                            status_display = hft_status
                            if sl_violation:
                                status_display = f"{Colors.RED}[V] VIOLATION{Colors.END}"
                            
                            positions_table.append([
                                f"{profit_symbol} {ticket}",
                                symbol,
                                pos_type,
                                f"{volume:.4f}",
                                entry_str,
                                current_str,
                                f"{profit_color}${profit:,.2f}{Colors.END}",
                                effective_sl_str,
                                effective_tp_str,
                                sl_status_indicator,
                                status_display
                            ])
                        
                        if TABULATE_AVAILABLE:
                            headers = ["Ticket", "Symbol", "Type", "Lot", "Entry", "Current", "P/L", "Effective SL", "Effective TP", "SL Status", "Status"]
                            print(tabulate(positions_table, headers=headers, tablefmt="grid", stralign="left"))
                            
                            # Total row
                            total_color = Colors.GREEN if total_profit >= 0 else Colors.RED
                            total_row = [f"{Colors.BOLD}TOTAL{Colors.END}", "", "", "", "", "", 
                                       f"{total_color}${total_profit:,.2f}{Colors.END}", "", "", "", ""]
                            print(tabulate([total_row], tablefmt="grid", stralign="left"))
                        else:
                            # Fallback
                            print(f"{Colors.BOLD}{'Ticket':<10} | {'Symbol':<12} | {'Type':<6} | {'Lot':<8} | {'Entry':<12} | {'Current':<12} | {'P/L':<12} | {'Effective SL':<18} | {'Effective TP':<18} | {'SL Status':<12} | {'Status'}{Colors.END}")
                            print(f"{Colors.BLUE}{'-' * 180}{Colors.END}")
                            for row in positions_table:
                                print(" | ".join(f"{str(cell):<25}" if i == 6 else f"{str(cell):<15}" if i in [7, 8] else f"{str(cell):<12}" for i, cell in enumerate(row)))
                            total_color = Colors.GREEN if total_profit >= 0 else Colors.RED
                            print(f"{Colors.BOLD}{'TOTAL FLOATING P/L':<25} | {total_color}${total_profit:>12,.2f}{Colors.END}")
                    else:
                        print(f"{Colors.YELLOW}No open positions{Colors.END}")
                    
                    print()
                    
                    # ==================== TRADE STATISTICS TABLE (Combined with Win/Loss and HFT) ====================
                    total_trades = trade_stats.get('total_trades', 0)
                    successful = trade_stats.get('successful_trades', 0)
                    failed = trade_stats.get('failed_trades', 0)
                    filtered = trade_stats.get('filtered_opportunities', 0)
                    profitable_trades = trade_stats.get('profitable_trades', 0)
                    losing_trades = trade_stats.get('losing_trades', 0)
                    closed_trades = profitable_trades + losing_trades  # Total closed trades
                    success_rate = (successful / total_trades * 100) if total_trades > 0 else 0
                    
                    # Win/Loss statistics - ensure correct calculation
                    profitable_count = trade_stats.get('profitable_trades', 0)
                    losing_count = trade_stats.get('losing_trades', 0)
                    total_profit_sum = float(trade_stats.get('total_profit', 0.0))
                    # total_loss is stored as negative, convert to absolute value for display
                    total_loss_raw = float(trade_stats.get('total_loss', 0.0))
                    total_loss_sum = abs(total_loss_raw)  # Display as positive number
                    
                    # Micro-HFT Performance
                    hft_trades = monitor_summary.get('hft_trades', 0) if monitor_summary else 0
                    sweet_spot_rate = monitor_summary.get('hft_sweet_spot_rate', 0) if monitor_summary else 0
                    
                    # Session P/L already calculated above for account information table
                    # Calculate floating_pnl and calculated_daily_pnl for logging
                    floating_pnl = total_profit  # Floating P/L is the total profit from open positions
                    calculated_daily_pnl = realized_pnl_today  # Calculated daily P/L is the realized P/L for today
                    
                    success_rate_color = Colors.GREEN if success_rate >= 70 else Colors.YELLOW if success_rate >= 50 else Colors.RED
                    sweet_spot_color = Colors.GREEN if sweet_spot_rate >= 70 else Colors.YELLOW if sweet_spot_rate >= 50 else Colors.RED
                    
                    print(f"{Colors.BOLD}{Colors.CYAN}[STATS] TRADE STATISTICS{Colors.END}")
                    
                    # Win/Loss statistics
                    win_rate = (profitable_count / closed_trades * 100) if closed_trades > 0 else 0.0
                    win_rate_color = Colors.GREEN if win_rate >= 50 else Colors.YELLOW if win_rate >= 30 else Colors.RED
                    profit_color = Colors.GREEN if total_profit_sum > 0 else Colors.END
                    loss_color = Colors.RED if total_loss_sum > 0 else Colors.END
                    
                    if TABULATE_AVAILABLE:
                        # First table: Execution stats
                        stats_table = [[
                            total_trades,
                            f"{Colors.GREEN}{successful}{Colors.END}",
                            f"{Colors.RED}{failed}{Colors.END}",
                            f"{Colors.YELLOW}{filtered}{Colors.END}"
                        ]]
                        headers = ["Total Trades", "Successful", "Failed", "Filtered"]
                        print(tabulate(stats_table, headers=headers, tablefmt="grid", stralign="right"))
                        print()
                        
                        # Second table: Win/Loss stats
                        if closed_trades > 0:
                            winloss_table = [[
                                closed_trades,
                                f"{Colors.GREEN}{profitable_count}{Colors.END}",
                                f"{Colors.RED}{losing_count}{Colors.END}",
                                f"{win_rate_color}{win_rate:.1f}%{Colors.END}",
                                f"{profit_color}${total_profit_sum:,.2f}{Colors.END}",
                                f"{loss_color}${total_loss_sum:,.2f}{Colors.END}",
                                f"{Colors.CYAN}${total_profit_sum + total_loss_sum:,.2f}{Colors.END}"
                            ]]
                            headers = ["Closed", "Wins", "Losses", "Win Rate", "Total Profit", "Total Loss", "Net P/L"]
                            print(tabulate(winloss_table, headers=headers, tablefmt="grid", stralign="right"))
                    else:
                        # Fallback: Execution stats
                        print(f"{Colors.BOLD}{'Total Trades':<15} | {'Successful':<15} | {'Failed':<15} | {'Filtered':<15}{Colors.END}")
                        print(f"{Colors.CYAN}{'-' * 75}{Colors.END}")
                        print(f"{total_trades:<15} | {Colors.GREEN}{successful:<15}{Colors.END} | {Colors.RED}{failed:<15}{Colors.END} | {Colors.YELLOW}{filtered:<15}{Colors.END}")
                        
                        # Fallback: Win/Loss stats
                        if closed_trades > 0:
                            print()
                            print(f"{Colors.BOLD}{'Closed Trades':<15} | {'Wins':<15} | {'Losses':<15} | {'Win Rate':<15} | {'Total Profit':<18} | {'Total Loss':<18} | {'Net P/L':<18}{Colors.END}")
                            print(f"{Colors.CYAN}{'-' * 120}{Colors.END}")
                            print(f"{closed_trades:<15} | {Colors.GREEN}{profitable_count:<15}{Colors.END} | {Colors.RED}{losing_count:<15}{Colors.END} | {win_rate_color}{win_rate:>13.1f}%{Colors.END} | {profit_color}${total_profit_sum:>15,.2f}{Colors.END} | {loss_color}${total_loss_sum:>15,.2f}{Colors.END} | {Colors.CYAN}${total_profit_sum + total_loss_sum:>15,.2f}{Colors.END}")
                    print()
                    
                    # Footer
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    print(f"{Colors.YELLOW}Display updates every {summary_interval:.0f}s | Trading runs at millisecond speeds | Press Ctrl+C to stop{Colors.END}")
                    print(f"{Colors.YELLOW}SL Status: {Colors.GREEN}[OK]{Colors.END} = OK/Protected, {Colors.YELLOW}[W]{Colors.END} = Pending/Warning, {Colors.RED}[V]{Colors.END} = Violation | Updates tracked in real-time{Colors.END}")
                    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 120}{Colors.END}")
                    
                    # ==================== LOG SUMMARY METRICS TO FILE ====================
                    # Log all summary metrics every 5 seconds to a dedicated log file for review
                    try:
                        summary_logger = get_logger("summary_metrics", "logs/live/system/summary_metrics.log")
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        
                        # Prepare summary metrics dictionary
                        summary_metrics = {
                            'timestamp': timestamp,
                            'account': {
                                'balance': account_info.get('balance', 0) if account_info else 0,
                                'equity': account_info.get('equity', 0) if account_info else 0,
                                'floating_profit': account_info.get('profit', 0) if account_info else 0,
                                'free_margin': account_info.get('free_margin', 0) if account_info else 0,
                                'used_margin': account_info.get('margin', 0) if account_info else 0,
                                'currency': account_info.get('currency', 'USD') if account_info else 'USD'
                            },
                            'positions': {
                                'count': len(positions),
                                'total_floating_pnl': total_profit,  # Already initialized to 0.0 before positions check
                                'positions_detail': [
                                    {
                                        'ticket': pos.get('ticket', 0),
                                        'symbol': pos.get('symbol', 'N/A'),
                                        'type': pos.get('type', 'N/A'),
                                        'volume': pos.get('volume', 0.0),
                                        'entry_price': pos.get('price_open', 0.0),
                                        'current_price': pos.get('price_current', 0.0),
                                        'sl_price': pos.get('sl', 0.0),
                                        'profit': pos.get('profit', 0.0),
                                        'duration_seconds': (datetime.now() - (pos.get('time_open', datetime.now()) if isinstance(pos.get('time_open'), datetime) else datetime.now())).total_seconds()
                                    }
                                    for pos in positions
                                ]
                            },
                            'trade_statistics': {
                                'total_trades': total_trades,
                                'successful_trades': successful,
                                'failed_trades': failed,
                                'filtered_opportunities': filtered,
                                'closed_trades': closed_trades,
                                'success_rate_pct': success_rate,
                                'profitable_trades': profitable_count,
                                'losing_trades': losing_count,
                                'total_profit': total_profit_sum,
                                'total_loss': total_loss_sum
                            },
                            'pnl': {
                                'session_pnl': session_pnl,
                                'daily_pnl': daily_pnl,
                                'realized_pnl_today': realized_pnl_today,
                                'floating_pnl': floating_pnl,
                                'calculated_daily_pnl': calculated_daily_pnl
                            },
                            'monitoring': {
                                'bot_running': self.bot.running if self.bot else False,
                                'monitoring_active': self.monitor.monitoring_active if self.monitor else False,
                                'reconciliation_active': True,
                                'comprehensive_monitor_active': True
                            }
                        }
                        
                        # Add HFT metrics if available
                        if monitor_summary:
                            summary_metrics['hft'] = {
                                'hft_trades': monitor_summary.get('hft_trades', 0),
                                'sweet_spot_rate': monitor_summary.get('hft_sweet_spot_rate', 0)
                            }
                        
                        # Log as JSON for easy parsing
                        import json
                        summary_logger.info(f"SUMMARY_METRICS|{json.dumps(summary_metrics, default=str)}")
                        
                    except Exception as log_error:
                        # Don't let logging errors break the display
                        logger.debug(f"Error logging summary metrics: {log_error}")
                    
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
            
            # Real-Time Monitor disabled to save storage space
            monitor_summary = {}
            # if self.monitor:
            #     monitor_summary = self.monitor.get_monitoring_summary()
            
            # Display final summary (only to console, minimal output)
            print("\n" + "=" * 80)
            print("FINAL TRADE SUMMARY")
            print("=" * 80)
            print(f"Open Positions: {len(positions)}")
            print(f"Total Trades: {trade_stats.get('total_trades', 0)}")
            print(f"Successful: {trade_stats.get('successful_trades', 0)}")
            print(f"Failed: {trade_stats.get('failed_trades', 0)}")
            
            if monitor_summary:
                print(f"HFT Trades: {monitor_summary.get('hft_trades', 0)}")
                print(f"HFT Sweet Spot Rate: {monitor_summary.get('hft_sweet_spot_rate', 0):.1f}%")
            
            print("=" * 80 + "\n")
            
            # Log to file as well
            logger.info("FINAL TRADE SUMMARY")
            logger.info(f"Open Positions: {len(positions)}")
            logger.info(f"Total Trades: {trade_stats.get('total_trades', 0)}")
            logger.info(f"Successful: {trade_stats.get('successful_trades', 0)}")
            logger.info(f"Failed: {trade_stats.get('failed_trades', 0)}")
            
        except Exception as e:
            logger.error(f"Error displaying final summary: {e}", exc_info=True)
    
    def stop(self):
        """Stop all components gracefully."""
        if not self.running:
            return
        
        logger.info("=" * 80)
        logger.info("INITIATING GRACEFUL SHUTDOWN")
        logger.info("=" * 80)
        
        # Set shutdown flag
        self.running = False
        self.shutdown_event.set()
        self.reconciliation_stop_event.set()
        
        # Stop SL Worker and Watchdog
        logger.info("Stopping SL Worker and Watchdog...")
        if self.bot and self.bot.risk_manager and hasattr(self.bot.risk_manager, 'sl_manager'):
            sl_manager = self.bot.risk_manager.sl_manager
            if sl_manager is not None:  # CRITICAL FIX: Check if sl_manager is not None
                if hasattr(sl_manager, '_watchdog') and sl_manager._watchdog:
                    sl_manager._watchdog.stop()
                if hasattr(sl_manager, 'stop_sl_worker'):
                    sl_manager.stop_sl_worker()
                time.sleep(0.5)
        logger.info("[OK] SL Worker and Watchdog stopped")
        
        # Stop Trading Bot
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
                logger.info("[OK] Trading Bot stopped")
        
        # Real-Time Monitor disabled to save storage space
        # logger.info("Stopping Real-Time Monitor...")
        # if self.monitor:
        #     self.monitor.stop_monitoring()
        # if self.monitor_thread and self.monitor_thread.is_alive():
        #     self.monitor_thread.join(timeout=5.0)
        
        # Stop Trade Summary Display
        logger.info("Stopping Trade Summary Display...")
        if self.summary_thread and self.summary_thread.is_alive():
            self.summary_thread.join(timeout=3.0)
            if self.summary_thread.is_alive():
                logger.warning("Summary thread did not stop within timeout")
            else:
                logger.info("[OK] Trade Summary Display stopped")
        
        # Display final summary before stopping
        self._display_final_summary()
        
        # Comprehensive Bot Monitor disabled to save storage space
        # logger.info("Stopping Comprehensive Bot Monitor...")
        # if self.comprehensive_monitor:
        #     self.comprehensive_monitor.stop()
        
        # Stop SL Realtime Monitor (if running separately)
        if self.sl_monitor:
            self.sl_monitor.stop()
    
    def _heartbeat_monitor_loop(self):
        """
        Heartbeat monitor loop - prints system health every 5 seconds.
        
        Shows:
        - Total trades
        - Open positions
        - SL status
        - Errors in last 60 seconds
        - Kill switch status
        - SL violations
        """
        last_heartbeat = datetime.now()
        error_window_start = datetime.now()
        recent_errors = []
        
        while self.running and not self.shutdown_event.is_set():
            try:
                time.sleep(5)  # Heartbeat every 5 seconds
                
                current_time = datetime.now()
                
                # Collect system health data
                total_trades = 0
                open_positions = 0
                sl_violations_count = 0
                kill_switch_active = False
                sl_manager_available = False
                sl_updates_per_sec = 0.0
                last_error = "None"
                
                if self.bot:
                    # Get trade statistics
                    if hasattr(self.bot, 'trade_stats'):
                        total_trades = self.bot.trade_stats.get('total_trades', 0)
                    
                    # Get open positions
                    if hasattr(self.bot, 'order_manager'):
                        positions = self.bot.order_manager.get_open_positions()
                        open_positions = len(positions) if positions else 0
                    
                    # Check kill switch
                    if hasattr(self.bot, 'kill_switch_active'):
                        kill_switch_active = self.bot.kill_switch_active
                    
                    # Check SL manager
                    if hasattr(self.bot, 'risk_manager'):
                        sl_manager_available = (hasattr(self.bot.risk_manager, 'sl_manager') and 
                                               self.bot.risk_manager.sl_manager is not None)
                        
                        # Calculate SL updates per second
                        if sl_manager_available and hasattr(self.bot.risk_manager.sl_manager, '_last_sl_update'):
                            # Count updates in last second
                            sl_manager = self.bot.risk_manager.sl_manager
                            with sl_manager._tracking_lock:
                                updates_in_last_sec = sum(
                                    1 for update_time in sl_manager._last_sl_update.values()
                                    if (current_time - update_time).total_seconds() < 1.0
                                )
                            sl_updates_per_sec = updates_in_last_sec
                
                # Get SL violations
                if self.sl_monitor and hasattr(self.sl_monitor, 'violations'):
                    sl_violations_count = len(self.sl_monitor.violations) if self.sl_monitor.violations else 0
                
                # Get last error
                if recent_errors:
                    last_error = recent_errors[-1].get('message', 'Unknown error')[:50]  # Truncate
                elif hasattr(self, '_last_system_error'):
                    last_error = self._last_system_error[:50]
                
                # Collect recent errors (last 60 seconds)
                error_window = (current_time - error_window_start).total_seconds()
                if error_window > 60:
                    error_window_start = current_time - timedelta(seconds=60)
                    recent_errors = [e for e in recent_errors if (current_time - e['time']).total_seconds() < 60]
                
                # Print heartbeat (only if there are issues or every 30 seconds for normal status)
                time_since_last = (current_time - last_heartbeat).total_seconds()
                
                # Always show if there are violations, errors, or kill switch active
                show_heartbeat = (sl_violations_count > 0 or len(recent_errors) > 0 or 
                                kill_switch_active or time_since_last >= 30)
                
                # Heartbeat monitor is silent - only logs to file if critical issues
                # Log critical issues to file logger only
                if kill_switch_active:
                    logger.warning(f"KILL SWITCH ACTIVE - Trading Halted")
                if sl_violations_count > 0:
                    logger.warning(f"{sl_violations_count} SL Violation(s) Detected")
                if not sl_manager_available:
                    logger.warning("SL Manager Not Available - SL Enforcement May Not Work")
                if len(recent_errors) > 0:
                    logger.warning(f"{len(recent_errors)} Error(s) in Last 60 Seconds")
                
                if show_heartbeat:
                    last_heartbeat = current_time
            
            except Exception as e:
                logger.error(f"Error in heartbeat monitor: {e}", exc_info=True)
                time.sleep(5)  # Continue even on error
        
        # Broker Reconciliation disabled to save storage space
        # logger.info("Stopping Broker Reconciliation...")
        # if self.reconciliation_thread and self.reconciliation_thread.is_alive():
        #     self.reconciliation_thread.join(timeout=5.0)
        
        # Real-Time Monitor disabled to save storage space
        # logger.info("Generating monitoring summary...")
        # if self.monitor:
        #     summary = self.monitor.get_monitoring_summary()
        
        # Flush all logs
        logger.info("Flushing all logs...")
        try:
            import logging
            for handler in logging.root.handlers[:]:
                handler.flush()
            logger.info("[OK] Logs flushed")
        except Exception as e:
            logger.error(f"Error flushing logs: {e}", exc_info=True)
        
        # Shutdown MT5 connection
        if self.bot:
            try:
                self.bot.mt5_connector.shutdown()
            except:
                pass
        
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
    parser.add_argument(
        '--monitor-only',
        action='store_true',
        help='Run SL monitor only (for testing with mocked SLManager)'
    )
    
    args = parser.parse_args()
    
    # Validate reconciliation interval
    if args.reconciliation_interval < 1:
        print("[ERROR] Reconciliation interval must be at least 1 minute")
        sys.exit(1)
    
    # Handle monitor-only mode
    if args.monitor_only:
        print("🔍 Starting SL Monitor in Monitor-Only Mode (for testing)")
        print("   Using mocked SLManager and positions")
        print()
        
        # Create a mock bot for monitor-only mode
        class MockBot:
            def __init__(self):
                from unittest.mock import Mock
                self.risk_manager = Mock()
                self.risk_manager.sl_manager = Mock()
                self.risk_manager.sl_manager.max_risk_usd = 2.0
                self.risk_manager.sl_manager.get_effective_sl_profit = lambda pos: pos.get('profit', 0.0) - 2.0
                self.risk_manager.sl_manager._last_sl_update = {}
                self.risk_manager.sl_manager._tracking_lock = threading.Lock()
                self.risk_manager.sl_manager._position_tracking = {}
                
                self.order_manager = Mock()
                # Mock some positions for testing
                self.order_manager.get_open_positions = lambda exclude_dec8=True: [
                    {
                        'ticket': 1001,
                        'symbol': 'EURUSD',
                        'type': 'BUY',
                        'price_open': 1.20000,
                        'price_current': 1.19950,
                        'sl': 1.19900,
                        'volume': 0.01,
                        'profit': -0.50
                    },
                    {
                        'ticket': 1002,
                        'symbol': 'GBPUSD',
                        'type': 'SELL',
                        'price_open': 1.30000,
                        'price_current': 1.30050,
                        'sl': 1.30100,
                        'volume': 0.01,
                        'profit': -0.50
                    }
                ]
        
        mock_bot = MockBot()
        monitor = SLRealtimeMonitor(mock_bot)
        monitor.start(standalone_display=True)
        
        print("[OK] SL Monitor started in standalone mode")
        print("   Press Ctrl+C to stop")
        print()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping SL Monitor...")
            monitor.stop()
            print("[OK] SL Monitor stopped")
        
        sys.exit(0)
    
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
        print(f"[ERROR] Fatal error: {e}")
        launcher.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
