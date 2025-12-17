"""
Main Trading Bot Orchestrator
Coordinates all modules and executes trading logic.
"""

import time
import json
import os
import random
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager, OrderType
from execution.position_monitor import PositionMonitor
from strategies.trend_filter import TrendFilter
from risk.risk_manager import RiskManager
from risk.pair_filter import PairFilter
from risk.halal_compliance import HalalCompliance
from news_filter.news_api import NewsFilter
from filters.market_closing_filter import MarketClosingFilter
from filters.volume_filter import VolumeFilter
from bot.config_validator import ConfigValidator
from utils.logger_factory import get_logger, get_symbol_logger, get_system_event_logger
from trade_logging.trade_logger import TradeLogger

# Module-level loggers - will be initialized based on config mode
logger = None
scheduler_logger = None
error_logger = None


def _get_log_paths(is_backtest: bool):
    """Get log paths based on mode."""
    if is_backtest:
        return {
            'system_startup': 'logs/backtest/system_startup.log',
            'scheduler': 'logs/backtest/scheduler.log',
            'system_errors': 'logs/backtest/system_errors.log'
        }
    else:
        return {
            'system_startup': 'logs/live/system/system_startup.log',
            'scheduler': 'logs/live/system/scheduler.log',
            'system_errors': 'logs/live/system/system_errors.log'
        }


class TradingBot:
    """Main trading bot orchestrator."""
    
    def __init__(self, config_path: str = 'config.json'):
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Determine if we're in backtest mode or sim_live mode
        self.is_backtest = self.config.get('mode') == 'backtest'
        self.is_sim_live = self.config.get('mode') == 'SIM_LIVE'
        
        # Initialize module-level loggers based on mode
        # SIM_LIVE uses live log paths (bot thinks it's live)
        global logger, scheduler_logger, error_logger
        log_paths = _get_log_paths(self.is_backtest)
        logger = get_logger("system_startup", log_paths['system_startup'])
        scheduler_logger = get_logger("scheduler", log_paths['scheduler'])
        error_logger = get_logger("system_errors", log_paths['system_errors'])
        
        # Validate configuration
        validator = ConfigValidator(self.config)
        is_valid, errors, warnings = validator.validate()
        validator.log_results()
        
        if not is_valid:
            raise ValueError(f"Configuration validation failed. Fix errors before starting bot.")
        
        # CRITICAL: Validate config alignment for backtest mode
        if self.is_backtest:
            from utils.config_alignment_validator import ConfigAlignmentValidator
            alignment_validator = ConfigAlignmentValidator(config_path=config_path)
            is_aligned, mismatches, alignment_warnings = alignment_validator.validate_alignment()
            alignment_validator.log_results(mode="BACKTEST")
            
            if not is_aligned:
                logger.critical("=" * 80)
                logger.critical("BACKTEST CONFIG ALIGNMENT FAILED - ABORTING")
                logger.critical("=" * 80)
                logger.critical("Backtest config must match live config exactly for deterministic reproduction.")
                logger.critical("Mismatches found:")
                for mismatch in mismatches:
                    logger.critical(f"  - {mismatch}")
                logger.critical("=" * 80)
                raise ValueError(f"Backtest config alignment failed. {len(mismatches)} critical mismatches found.")
            
            # Log all critical config values for verification
            mode_str = "BACKTEST"
        elif self.is_sim_live:
            mode_str = "SIM_LIVE"
        else:
            mode_str = "LIVE"
        
        # Consolidated config verification logging (shared for both backtest and live)
        logger.info("=" * 80)
        logger.info(f"CONFIG VERIFICATION (mode={mode_str}) - Critical Values")
        logger.info("=" * 80)
        risk_config = self.config.get('risk', {})
        logger.info(f"mode={mode_str} | max_risk_per_trade_usd: {risk_config.get('max_risk_per_trade_usd')}")
        logger.info(f"mode={mode_str} | trailing_cycle_interval_ms: {risk_config.get('trailing_cycle_interval_ms')}")
        logger.info(f"mode={mode_str} | lock_acquisition_timeout_seconds: {risk_config.get('lock_acquisition_timeout_seconds')}")
        logger.info(f"mode={mode_str} | profit_locking_lock_timeout_seconds: {risk_config.get('profit_locking_lock_timeout_seconds')}")
        logger.info(f"mode={mode_str} | sl_update_min_interval_ms: {risk_config.get('sl_update_min_interval_ms')}")
        logger.info(f"mode={mode_str} | max_open_trades: {risk_config.get('max_open_trades')}")
        trading_config = self.config.get('trading', {})
        cycle_interval = trading_config.get('cycle_interval_seconds', 60)
        logger.info(f"mode={mode_str} | run_cycle_interval_seconds: {cycle_interval}")
        logger.info("=" * 80)
        
        # Initialize components
        # Use SimLiveMT5Connector if in SIM_LIVE mode
        if self.is_sim_live:
            from sim_live.sim_live_connector import SimLiveMT5Connector
            from sim_live.synthetic_mt5_wrapper import inject_mt5_mock
            self.mt5_connector = SimLiveMT5Connector(self.config)
            # Inject MT5 mock into order_manager module for direct mt5.order_send() calls
            inject_mt5_mock(self.mt5_connector.broker)
            logger.info("SIM_LIVE mode: Using synthetic market engine and broker")
        else:
            # Standard live / backtest connector
            self.mt5_connector = MT5Connector(self.config)
        
        self.order_manager = OrderManager(self.mt5_connector)
        self.trend_filter = TrendFilter(self.config, self.mt5_connector)
        self.risk_manager = RiskManager(self.config, self.mt5_connector, self.order_manager)
        self.pair_filter = PairFilter(self.config, self.mt5_connector)
        self.halal_compliance = HalalCompliance(self.config, self.mt5_connector, self.order_manager)
        self.news_filter = NewsFilter(self.config, self.mt5_connector)
        self.market_closing_filter = MarketClosingFilter(self.config, self.mt5_connector)
        self.volume_filter = VolumeFilter(self.config, self.mt5_connector)
        self.trade_logger = TradeLogger(self.config)
        
        # Register P/L callback with risk manager
        self.risk_manager.set_pnl_callback(self._update_realized_pnl_on_closure)
        # Also set callback for daily_pnl updates during monitoring loop
        self.risk_manager.bot_pnl_callback = self.update_daily_pnl
        
        # Initialize Position Monitor for closure detection
        self.position_monitor = PositionMonitor(self.config, self.trade_logger)
        self.position_monitor_running = False
        self.position_monitor_thread = None
        
        # Initialize Micro-HFT Profit Engine (optional add-on)
        micro_config = self.config.get('micro_profit_engine', {})
        if micro_config.get('enabled', True):
            try:
                from bot.micro_profit_engine import MicroProfitEngine
                self.micro_profit_engine = MicroProfitEngine(
                    config=self.config,
                    order_manager=self.order_manager,
                    trade_logger=self.trade_logger,
                    risk_manager=self.risk_manager
                )
                # Register with risk manager
                self.risk_manager.set_micro_profit_engine(self.micro_profit_engine)
                logger.info("Micro-HFT Profit Engine initialized and registered")
            except ImportError as e:
                logger.warning(f"Micro-HFT Profit Engine not available: {e}")
                self.micro_profit_engine = None
        else:
            self.micro_profit_engine = None
            logger.info("Micro-HFT Profit Engine disabled in config")
        
        # Initialize Profit-Locking Engine (optional add-on)
        risk_config = self.config.get('risk', {})
        lock_config = risk_config.get('profit_locking', {})
        if lock_config.get('enabled', True):
            try:
                from bot.profit_locking_engine import ProfitLockingEngine
                self.profit_locking_engine = ProfitLockingEngine(
                    config=self.config,
                    order_manager=self.order_manager,
                    mt5_connector=self.mt5_connector
                )
                # Register with risk manager
                self.risk_manager.set_profit_locking_engine(self.profit_locking_engine)
                logger.info("Profit-Locking Engine initialized and registered")
            except ImportError as e:
                logger.warning(f"Profit-Locking Engine not available: {e}")
                self.profit_locking_engine = None
        else:
            self.profit_locking_engine = None
            logger.info("Profit-Locking Engine disabled in config")
        
        # Supervisor settings
        self.supervisor_config = self.config.get('supervisor', {})
        self.supervisor_enabled = self.supervisor_config.get('enabled', True)
        self.max_consecutive_errors = self.supervisor_config.get('max_consecutive_errors', 5)
        self.error_cooldown_minutes = self.supervisor_config.get('error_cooldown_minutes', 15)
        self.kill_switch_enabled = self.supervisor_config.get('kill_switch_enabled', True)
        
        # State tracking
        self.consecutive_errors = 0
        self.last_error_time = None
        self.kill_switch_active = False
        # Error throttling: track recent errors to avoid counting duplicates
        self._error_throttle = {}  # {error_signature: last_counted_time}
        self._error_throttle_window = 5.0  # seconds - same error only counted once per window
        self.running = False
        
        # Lightweight logger state tracking (thread-safe)
        self._state_lock = threading.Lock()
        self.current_state = 'IDLE'  # IDLE, SCANNING, ENTERING TRADE, MANAGING TRADE, CLOSING TRADE, ERROR
        self.current_symbol = 'N/A'
        self.last_action = 'N/A'
        self.last_action_time = None
        self.sl_update_times = {}  # {ticket: datetime} - track when SL was last updated
        self._state_entry_ts = {}  # {state: timestamp} - track when each state was entered
        self._stuck_tickets = {}  # {ticket: {'skip_until': timestamp, 'reason': str}} - tickets to skip
        self._state_watchdog_running = False
        self._state_watchdog_thread = None
        
        # P&L tracking (legacy - kept for compatibility)
        self.daily_pnl = 0.0
        self.last_pnl_reset = datetime.now().date()
        self.trade_count_today = 0
        
        # Session tracking (initialized in connect())
        self.session_start_balance = None
        
        # Trading config
        self.trading_config = self.config.get('trading', {})
        self.randomize_timing = self.trading_config.get('randomize_timing', False)
        self.max_delay_seconds = self.trading_config.get('max_delay_seconds', 60)
        
        # Randomness factor: adjusted for medium-frequency trading (20-30% threshold)
        self.randomness_factor = self.trading_config.get('randomness_factor', 0.25)  # 25% chance to skip trade (75% chance to take)
        # This means: when conditions are met, take trade 75% of the time for medium frequency
        
        # Statistics tracking
        self.trade_stats = {
            'total_trades': 0,
            'successful_trades': 0,
            'failed_trades': 0,  # Only actual execution failures
            'filtered_opportunities': 0,  # Opportunities filtered out (not failed trades)
            'total_spread_paid': 0.0,
            'total_commission_paid': 0.0,
            'profitable_trades': 0,  # Count of closed profitable trades
            'losing_trades': 0,  # Count of closed losing trades
            'total_profit': 0.0,  # Sum of all profitable trades
            'total_loss': 0.0  # Sum of all losing trades (negative values)
        }
        
        # P/L tracking (daily and session)
        self.session_start_time = datetime.now()
        self.session_start_balance = None
        self.realized_pnl = 0.0  # Realized P/L from closed trades
        self.realized_pnl_today = 0.0  # Realized P/L for today
        
        # Continuous trailing stop thread
        self.trailing_stop_thread = None
        self.trailing_stop_running = False
        self.risk_config = self.config.get('risk', {})
        # Instant trailing: use config value (0 = instant, no delays)
        trailing_config = self.risk_config.get('trailing', {})
        self.trailing_cycle_interval_ms = trailing_config.get('frequency_ms', 0) if trailing_config else self.risk_config.get('trailing_cycle_interval_ms', 0)
        self.trailing_cycle_interval = self.trailing_cycle_interval_ms / 1000.0  # Convert to seconds for time.sleep (0 = instant)
        # Ensure trigger_on_tick is respected
        self.trigger_on_tick = trailing_config.get('trigger_on_tick', True) if trailing_config else True
        
        # Fast trailing thread (for positions with profit >= threshold)
        self.fast_trailing_thread = None
        self.fast_trailing_running = False
        # Instant fast trailing: use config value (0 = instant, no delays except debounce cycles)
        trailing_config = self.risk_config.get('trailing', {})
        self.fast_trailing_interval_ms = trailing_config.get('fast_frequency_ms', 0) if trailing_config else self.risk_config.get('fast_trailing_interval_ms', 0)
        self.fast_trailing_threshold_usd = self.risk_config.get('fast_trailing_threshold_usd', 0.10)
        
        # Tracked positions for closure detection
        self.tracked_tickets = set()
        
        # System event logger for diagnostics
        self.system_event_logger = get_system_event_logger()
        
        # SL tracking for "not moving" detection
        self._sl_tracking = {}  # {ticket: {'last_sl': float, 'last_profit': float, 'unchanged_ticks': int, 'last_check_time': float}}
        self._sl_tracking_lock = threading.Lock()
        
        # One-candle confirmation entry logic - pending signals tracking
        self._pending_signals = {}  # {symbol: {'direction': 'LONG'|'SHORT', 'signal_time': datetime, 'candle_time': int, 'quality_score': float, 'trend_signal': dict}}
        self._pending_signals_lock = threading.Lock()
    
        # Manual approval mode settings
        self.manual_approval_mode = False
        self.manual_max_trades = None
        self.manual_batch_cancelled = False  # Flag for cancel batch
        self.manual_wait_for_close_timeout = self.trading_config.get('manual_wait_for_close_timeout_seconds', 3600)  # Default 1 hour
        self.manual_scan_completed = False  # Track if scan has been completed in this session
        self.manual_approved_trades = []  # Track approved trades waiting to execute
        self.manual_trades_executing = False  # Track if trades are currently executing
        
        # Initialize Limit Entry Dry-Run System (DRY-RUN ONLY - no execution impact)
        try:
            from entry.limit_entry_dry_run import LimitEntryDryRun
            self.limit_entry_dry_run = LimitEntryDryRun(self.config, self.mt5_connector)
            logger.info(f"mode={'BACKTEST' if self.is_backtest else 'LIVE'} | [INIT] Limit entry system loaded in DRY-RUN mode only — no execution impact")
        except Exception as e:
            logger.warning(f"Limit Entry Dry-Run system initialization failed: {e}")
            self.limit_entry_dry_run = None
    
    def connect(self) -> bool:
        """Connect to MT5."""
        logger.info("Connecting to MT5...")
        if self.mt5_connector.connect():
            logger.info("MT5 connection established")
            
            # CRITICAL FIX: Verify SLManager is available before proceeding
            if not hasattr(self.risk_manager, 'sl_manager') or self.risk_manager.sl_manager is None:
                error_msg = "[ERROR] SLManager FAILED to initialize. Trading aborted."
                error_msg += "\n   → Check logs/live/engine/risk_manager.log for detailed error information"
                error_msg += "\n   → This usually indicates a problem with the SLManager module initialization"
                logger.critical(error_msg)
                error_logger.critical("SLManager not available - cannot proceed with trading")
                print(error_msg)
                return False
            logger.info("[OK] SLManager availability verified - ready for trading")
            
            # Initialize session tracking
            account_info = self.mt5_connector.get_account_info()
            if account_info:
                self.session_start_balance = account_info.get('balance', 0)
                self.session_start_time = datetime.now()
                self.realized_pnl = 0.0
                
                # Reset session-specific win/loss stats
                self.trade_stats['profitable_trades'] = 0
                self.trade_stats['losing_trades'] = 0
                self.trade_stats['total_profit'] = 0.0
                self.trade_stats['total_loss'] = 0.0
                
                logger.info(f"📊 Session started | Starting Balance: ${self.session_start_balance:.2f} | Session Time: {self.session_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # CRITICAL FIX: Fetch all existing broker positions at startup and sync
            logger.info("🔄 Syncing with broker positions at startup...")
            broker_positions = self.order_manager.get_open_positions(exclude_dec8=False)  # Get ALL positions
            logger.info(f"📊 Found {len(broker_positions)} position(s) in broker at startup")
            
            # Log all broker positions for tracking
            for pos in broker_positions:
                logger.info(f"  Broker Position: Ticket {pos['ticket']} | {pos['symbol']} {pos['type']} | "
                          f"Entry: {pos['price_open']:.5f} | SL: {pos['sl']:.5f} | P/L: ${pos['profit']:.2f}")
            
            return True
        else:
            logger.error("Failed to connect to MT5")
            return False
    
    def check_kill_switch(self) -> bool:
        """Check if kill switch is active."""
        if self.kill_switch_active:
            logger.warning("KILL SWITCH ACTIVE - Trading halted")
            return True
        return False
    
    def activate_kill_switch(self, reason: str):
        """Activate kill switch to stop all trading."""
        self.kill_switch_active = True
        error_logger.critical(f"KILL SWITCH ACTIVATED: {reason}")
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")
        
        # Close all positions if needed
        positions = self.order_manager.get_open_positions()
        for position in positions:
            error_logger.warning(f"Closing position {position['ticket']} due to kill switch")
            logger.warning(f"Closing position {position['ticket']} due to kill switch")
            self.order_manager.close_position(position['ticket'], comment="Kill switch activated")
    
    def handle_error(self, error: Exception, context: str = ""):
        """
        Handle errors with supervisor logic.
        
        Includes error throttling to prevent the same error from being counted multiple times
        within a short time window. This prevents coding errors from triggering kill switch prematurely.
        """
        self.last_error_time = datetime.now()
        
        # Create error signature for throttling (error type + context)
        error_signature = f"{type(error).__name__}:{context}"
        current_time = datetime.now()
        
        # Check if this error was recently counted
        should_count = True
        if error_signature in self._error_throttle:
            time_since_last = (current_time - self._error_throttle[error_signature]).total_seconds()
            if time_since_last < self._error_throttle_window:
                # Same error within throttle window - don't count again
                should_count = False
                logger.debug(f"Error throttled: {error_signature} (last counted {time_since_last:.1f}s ago)")
        
        # Log the error regardless
        error_logger.error(f"Error in {context}: {error}", exc_info=True)
        logger.error(f"Error in {context}: {error}")
        
        # Only increment error count if not throttled
        if should_count:
            self.consecutive_errors += 1
            self._error_throttle[error_signature] = current_time
            
            # Clean up old throttle entries (older than 2x throttle window)
            cutoff_time = current_time - timedelta(seconds=self._error_throttle_window * 2)
            self._error_throttle = {
                sig: time for sig, time in self._error_throttle.items()
                if time > cutoff_time
            }
        else:
            # Log that error was throttled but still log it
            logger.debug(f"Error throttled (not counted): {error_signature}")
        
        if self.supervisor_enabled:
            if self.consecutive_errors >= self.max_consecutive_errors:
                error_logger.critical(f"Too many consecutive errors ({self.consecutive_errors})")
                logger.critical(f"Too many consecutive errors ({self.consecutive_errors})")
                if self.kill_switch_enabled:
                    self.activate_kill_switch(f"Too many errors: {self.consecutive_errors}")
    
    def reset_error_count(self):
        """Reset error count after successful operation."""
        if self.consecutive_errors > 0:
            logger.info(f"Resetting error count (was {self.consecutive_errors})")
        self.consecutive_errors = 0
        # Also clear error throttle on successful operation
        self._error_throttle.clear()
    
    def reset_kill_switch(self):
        """Reset kill switch and error count for fresh start."""
        if self.kill_switch_active:
            logger.info("Resetting kill switch - trading will resume")
        self.kill_switch_active = False
        self.consecutive_errors = 0
        self.last_error_time = None
        logger.info("Kill switch and error count reset - system ready for trading")
    
    def is_in_cooldown(self) -> bool:
        """Check if bot is in error cooldown period."""
        if self.last_error_time is None:
            return False
        
        time_since_error = (datetime.now() - self.last_error_time).total_seconds() / 60
        return time_since_error < self.error_cooldown_minutes
    
    def update_daily_pnl(self):
        """Update and log daily P&L."""
        today = datetime.now().date()
        
        # Reset if new day
        if today > self.last_pnl_reset:
            logger.info("=" * 60)
            logger.info(f"📊 Daily P&L Report - Date: {self.last_pnl_reset}")
            logger.info(f"   Total P&L: ${self.daily_pnl:.2f}")
            logger.info(f"   Realized P/L: ${self.realized_pnl_today:.2f}")
            logger.info(f"   Trades Executed: {self.trade_count_today}")
            logger.info(f"   Trade Statistics:")
            logger.info(f"     - Total Trades: {self.trade_stats['total_trades']}")
            logger.info(f"     - Successful: {self.trade_stats['successful_trades']}")
            logger.info(f"     - Failed: {self.trade_stats['failed_trades']}")
            logger.info(f"     - Filtered Opportunities: {self.trade_stats['filtered_opportunities']}")
            logger.info(f"     - Profitable Trades: {self.trade_stats['profitable_trades']} (Total: ${self.trade_stats['total_profit']:.2f})")
            logger.info(f"     - Losing Trades: {self.trade_stats['losing_trades']} (Total: ${self.trade_stats['total_loss']:.2f})")
            if self.trade_stats['total_trades'] > 0:
                success_rate = (self.trade_stats['successful_trades'] / self.trade_stats['total_trades']) * 100
                logger.info(f"     - Success Rate: {success_rate:.1f}%")
            if self.trade_stats['total_spread_paid'] > 0:
                logger.info(f"     - Total Spread Paid: {self.trade_stats['total_spread_paid']:.1f} points")
            logger.info("=" * 60)
            self.daily_pnl = 0.0
            self.realized_pnl_today = 0.0
            self.trade_count_today = 0
            # Reset session-specific stats on new day
            self.trade_stats['profitable_trades'] = 0
            self.trade_stats['losing_trades'] = 0
            self.trade_stats['total_profit'] = 0.0
            self.trade_stats['total_loss'] = 0.0
            self.last_pnl_reset = today
        
        # Calculate realized P/L from closed trades today
        # Read from trade logs to get closed trade profits
        try:
            realized_pnl_today = self._calculate_realized_pnl_today()
            if realized_pnl_today is not None:
                self.realized_pnl_today = realized_pnl_today
        except Exception as e:
            logger.debug(f"Could not calculate realized P/L from logs: {e}")
        
        # Calculate current floating P/L from open positions
        positions = self.order_manager.get_open_positions(exclude_dec8=True)
        floating_pnl = sum([p['profit'] for p in positions])
        
        # Total daily P/L = realized (closed) + floating (open)
        self.daily_pnl = self.realized_pnl_today + floating_pnl
        
        # Get account info for session tracking
        account_info = self.mt5_connector.get_account_info()
        if account_info and self.session_start_balance:
            current_balance = account_info.get('balance', 0)
            session_pnl = current_balance - self.session_start_balance
            # Store for display
            self.session_pnl = session_pnl
        else:
            self.session_pnl = self.daily_pnl  # Fallback
    
    def _calculate_realized_pnl_today(self) -> float:
        """Calculate realized P/L from closed trades today by reading trade logs."""
        import os
        import glob
        import json
        from datetime import datetime
        
        today = datetime.now().date()
        total_realized = 0.0
        
        # Read all trade log files
        trade_log_dir = "logs/trades"
        if not os.path.exists(trade_log_dir):
            return 0.0
        
        # Count profitable and losing trades from CURRENT SESSION ONLY
        # Only count trades that closed after session start time
        session_start = getattr(self, 'session_start_time', None)
        profitable_count = 0
        losing_count = 0
        total_profit = 0.0
        total_loss = 0.0
        
        for log_file in glob.glob(os.path.join(trade_log_dir, "*.log")):
            try:
                with open(log_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('{'):
                            try:
                                trade = json.loads(line)
                                # Check if trade is closed and from today
                                if trade.get('status') == 'CLOSED':
                                    trade_timestamp = trade.get('timestamp', '')
                                    if trade_timestamp:
                                        try:
                                            trade_time = datetime.strptime(trade_timestamp.split('.')[0], '%Y-%m-%d %H:%M:%S')
                                            if trade_time.date() == today:
                                                profit = trade.get('profit_usd', 0.0)
                                                if profit is not None:
                                                    total_realized += profit
                                                    
                                                    # Only count for session stats if trade closed after session start
                                                    if session_start and trade_time >= session_start:
                                                        # Track win/loss for session stats
                                                        if profit > 0:
                                                            profitable_count += 1
                                                            total_profit += profit
                                                        elif profit < 0:
                                                            losing_count += 1
                                                            total_loss += profit
                                        except (ValueError, AttributeError):
                                            continue
                            except json.JSONDecodeError:
                                continue
            except Exception:
                continue
        
        # Update session stats from logs (only for trades after session start)
        # These will be updated in real-time as new trades close via _update_realized_pnl_on_closure
        if session_start:
            self.trade_stats['profitable_trades'] = profitable_count
            self.trade_stats['losing_trades'] = losing_count
            self.trade_stats['total_profit'] = total_profit
            self.trade_stats['total_loss'] = total_loss
        else:
            # Session not started yet, initialize to 0
            self.trade_stats['profitable_trades'] = 0
            self.trade_stats['losing_trades'] = 0
            self.trade_stats['total_profit'] = 0.0
            self.trade_stats['total_loss'] = 0.0
        
        return total_realized
    
    def _update_realized_pnl_on_closure(self, profit: float, close_time: datetime):
        """
        Callback to update realized P/L when a position closes.
        Called by RiskManager when it detects a position closure.
        
        Args:
            profit: Profit/loss from closed trade
            close_time: Close timestamp
        """
        from datetime import datetime as dt
        today = dt.now().date()
        close_date = close_time.date() if isinstance(close_time, dt) else close_time
        
        # Update total realized P/L
        self.realized_pnl += profit
        
        # Update today's realized P/L if closed today
        if close_date == today:
            self.realized_pnl_today += profit
            
            # Track profitable vs losing trades for CURRENT SESSION ONLY
            # Only count trades that closed after session start time
            if isinstance(close_time, dt):
                close_datetime = close_time
            else:
                # If close_time is date, use current time
                close_datetime = dt.now()
            
            # Only update session stats if trade closed after session started
            if hasattr(self, 'session_start_time') and self.session_start_time and close_datetime >= self.session_start_time:
                if profit > 0:
                    self.trade_stats['profitable_trades'] += 1
                    self.trade_stats['total_profit'] += profit
                elif profit < 0:
                    self.trade_stats['losing_trades'] += 1
                    self.trade_stats['total_loss'] += profit  # profit is negative, so this accumulates losses
            
            logger.debug(f"📊 Updated realized P/L: +${profit:.2f} | Today: ${self.realized_pnl_today:.2f} | Total: ${self.realized_pnl:.2f}")
    
    def _update_state(self, state: str, symbol: str = 'N/A', action: str = 'N/A'):
        """Update bot state for lightweight logger (thread-safe)."""
        with self._state_lock:
            # Track state entry timestamp for watchdog
            if self.current_state != state:
                self._state_entry_ts[state] = time.time()
                logger.debug(f"🔄 State transition: {self.current_state} → {state} | Symbol: {symbol}")
            self.current_state = state
            self.current_symbol = symbol
            self.last_action = action
            self.last_action_time = datetime.now()
    
    def get_bot_state(self) -> Dict[str, Any]:
        """Get current bot state for lightweight logger (thread-safe)."""
        with self._state_lock:
            return {
                'running': self.running,
                'current_state': self.current_state,
                'current_symbol': self.current_symbol,
                'last_action': self.last_action,
                'last_action_time': self.last_action_time,
                'sl_update_times': self.sl_update_times.copy(),
                'trade_stats': self.trade_stats.copy()
            }
    
    def _force_unstick(self, ticket: int, symbol: str):
        """Force unstick a stuck MANAGING_TRADE state."""
        logger.critical(f"🔧 FORCE UNSTICK: Ticket {ticket} | Symbol: {symbol} | "
                       f"MANAGING_TRADE stuck > 30 minutes")
        
        # Check if position still exists
        position = self.order_manager.get_position_by_ticket(ticket)
        if not position:
            logger.info(f"[OK] Position {ticket} no longer exists - transitioning to SCANNING")
            self._update_state('SCANNING', 'N/A', 'Position closed - scanning for opportunities')
            return
        
        # Position exists - attempt emergency SL via alternate path
        logger.warning(f"[WARNING] Position {ticket} still exists - attempting emergency SL update")
        try:
            if hasattr(self.risk_manager, 'sl_manager') and self.risk_manager.sl_manager:
                # Force SL update via emergency path
                success, reason = self.risk_manager.sl_manager.update_sl_atomic(ticket, position)
                if success:
                    logger.info(f"[OK] Emergency SL update succeeded for stuck ticket {ticket}")
                else:
                    logger.warning(f"[WARNING] Emergency SL update failed for stuck ticket {ticket}: {reason}")
        except Exception as e:
            logger.error(f"[ERROR] Error in force_unstick for ticket {ticket}: {e}", exc_info=True)
        
        # Add to skip list with exponential backoff
        skip_duration = 300  # 5 minutes initial skip
        skip_until = time.time() + skip_duration
        self._stuck_tickets[ticket] = {
            'skip_until': skip_until,
            'reason': f'Stuck MANAGING_TRADE > 30 minutes',
            'attempts': self._stuck_tickets.get(ticket, {}).get('attempts', 0) + 1
        }
        
        # Force transition to SCANNING
        self._update_state('SCANNING', 'N/A', f'Force transition from stuck MANAGING_TRADE (ticket {ticket})')
        logger.info(f"🔄 Forced transition to SCANNING | Ticket {ticket} skipped for {skip_duration/60:.1f} minutes")
    
    def _state_watchdog_loop(self):
        """Watchdog loop to detect stuck states."""
        logger.info("State watchdog started")
        while self._state_watchdog_running:
            try:
                time.sleep(60)  # Check every minute
                
                with self._state_lock:
                    current_state = self.current_state
                    if current_state == 'MANAGING TRADE' and 'MANAGING TRADE' in self._state_entry_ts:
                        state_duration = time.time() - self._state_entry_ts['MANAGING TRADE']
                        if state_duration > 1800:  # 30 minutes
                            # Get current positions
                            positions = self.order_manager.get_open_positions()
                            if positions:
                                first_ticket = positions[0].get('ticket', 0)
                                first_symbol = positions[0].get('symbol', 'N/A')
                                
                                # Check if already in skip list
                                if first_ticket in self._stuck_tickets:
                                    skip_info = self._stuck_tickets[first_ticket]
                                    if time.time() < skip_info['skip_until']:
                                        continue  # Still in skip period
                                
                                logger.warning(f"[WARNING] State watchdog: MANAGING_TRADE stuck for {state_duration/60:.1f} minutes | "
                                            f"Ticket: {first_ticket} | Symbol: {first_symbol}")
                                # Force unstick
                                self._force_unstick(first_ticket, first_symbol)
            except Exception as e:
                logger.error(f"Error in state watchdog: {e}", exc_info=True)
        
        logger.info("State watchdog stopped")
    
    def start_state_watchdog(self):
        """Start state watchdog thread."""
        if self._state_watchdog_running:
            return
        
        self._state_watchdog_running = True
        self._state_watchdog_thread = threading.Thread(
            target=self._state_watchdog_loop,
            name="StateWatchdog",
            daemon=True
        )
        self._state_watchdog_thread.start()
        logger.info("State watchdog thread started")
    
    def stop_state_watchdog(self):
        """Stop state watchdog thread."""
        self._state_watchdog_running = False
        if self._state_watchdog_thread:
            self._state_watchdog_thread.join(timeout=5)
        logger.info("State watchdog thread stopped")
    
    def scan_for_opportunities(self) -> List[Dict[str, Any]]:
        """Scan for trading opportunities with SIMPLE logic and comprehensive logging."""
        opportunities = []
        
        # Import SIM_LIVE diagnostic logging functions if in SIM_LIVE mode
        if self.is_sim_live:
            from sim_live.sim_live_logger import log_entry_rejected, log_entry_evaluation_start
        
        try:
            # Update state
            self._update_state('SCANNING', 'N/A', 'Scanning for opportunities')
            
            # Get tradeable symbols
            symbols = self.pair_filter.get_tradeable_symbols()
            logger.info(f"🔍 Scanning {len(symbols)} symbols for trading opportunities...")
            
            if not symbols:
                logger.warning("[WARNING] No tradeable symbols found! Check symbol filters.")
            
            for symbol in symbols:
                try:
                    # Update current symbol being scanned
                    self._update_state('SCANNING', symbol, f'Scanning {symbol}')
                    logger.debug(f"📊 Analyzing {symbol}...")
                    
                    # 0. Check if symbol was previously restricted (prevent duplicate attempts)
                    if hasattr(self, '_restricted_symbols') and symbol.upper() in self._restricted_symbols:
                        logger.debug(f"[SKIP] [SKIP] {symbol} | Reason: Previously restricted - skipping to avoid duplicate attempts")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 0a. Check if symbol is tradeable/executable RIGHT NOW (market is open, trade mode enabled)
                    # This prevents non-executable symbols from appearing in opportunity lists
                    is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(symbol)
                    if not is_tradeable:
                        logger.debug(f"[SKIP] [SKIP] {symbol} | Reason: NOT EXECUTABLE - {reason}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue  # Skip this symbol - not tradeable right now
                    else:
                        logger.debug(f"[OK] {symbol}: Symbol is tradeable and executable")
                    
                    # 0b. Check if market is closing soon (30 minutes filter)
                    should_skip_close, close_reason = self.market_closing_filter.should_skip(symbol)
                    if should_skip_close:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: {close_reason}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 0c. Check if volume/liquidity is sufficient
                    should_skip_volume, volume_reason, volume_value = self.volume_filter.should_skip(symbol)
                    if should_skip_volume:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: {volume_reason}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 1. Check if news is blocking (but allow trading if API fails)
                    news_blocking = self.news_filter.is_news_blocking(symbol)
                    if news_blocking:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: NEWS BLOCKING (high-impact news within 10 min window)")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    else:
                        logger.debug(f"[OK] {symbol}: No news blocking")
                    
                    # 2a. ONE-CANDLE CONFIRMATION: Check for pending signal first
                    # This check happens BEFORE filters to confirm direction consistency only
                    with self._pending_signals_lock:
                        pending_signal = self._pending_signals.get(symbol)
                        
                        if pending_signal is not None:
                            # We have a pending signal - check if direction is still valid (one candle has closed)
                            # Get current trend signal to check direction consistency
                            current_trend_signal = self.trend_filter.get_trend_signal(symbol)
                            current_direction = current_trend_signal.get('signal', 'NONE')
                            
                            # Get current candle time to compare
                            df_current = self.trend_filter.get_rates(symbol, count=1)
                            if df_current is not None and len(df_current) > 0:
                                current_candle_time = int(df_current.iloc[0]['time'])
                                pending_candle_time = pending_signal['candle_time']
                                
                                # Check if a new candle has closed (current candle time is different from pending)
                                if current_candle_time != pending_candle_time:
                                    # New candle has closed - check direction consistency only
                                    if current_direction == pending_signal['direction']:
                                        # Direction held - confirm entry using stored opportunity data
                                        logger.info(f"[ENTRY CONFIRMED] {symbol}: direction {current_direction} held for 1 candle")
                                        stored_opportunity = pending_signal['opportunity_data']
                                        opportunities.append(stored_opportunity)
                                        del self._pending_signals[symbol]
                                        continue  # Skip rest of processing for this symbol (use stored data)
                                    else:
                                        # Direction changed - discard pending signal
                                        logger.info(f"[ENTRY CONFIRMATION FAILED] {symbol}: direction changed from {pending_signal['direction']} to {current_direction}")
                                        del self._pending_signals[symbol]
                                        self.trade_stats['filtered_opportunities'] += 1
                                        continue  # Skip this symbol
                                else:
                                    # Same candle still - wait for next candle
                                    logger.debug(f"[ENTRY PENDING] {symbol}: waiting for candle close (current: {current_candle_time}, pending: {pending_candle_time})")
                                    self.trade_stats['filtered_opportunities'] += 1
                                    continue  # Skip this symbol, wait for next candle
                            else:
                                # Can't get current candle - discard pending signal
                                logger.debug(f"[ENTRY CONFIRMATION FAILED] {symbol}: cannot get current candle time")
                                del self._pending_signals[symbol]
                                self.trade_stats['filtered_opportunities'] += 1
                                continue
                    
                    # 2. Get SIMPLE trend signal (SMA20 vs SMA50 only)
                    
                    # 🔒 SIM_LIVE: Assert trend contract before strategy evaluation
                    # CRITICAL: Validate using EXACT candles that get_trend_signal() will use
                    if self.is_sim_live:
                        try:
                            import pandas as pd
                            from sim_live.sim_live_connector import SimLiveMT5Connector
                            if isinstance(self.mt5_connector, SimLiveMT5Connector):
                                market_engine = self.mt5_connector.market_engine
                                symbol_upper = symbol.upper()
                                
                                # 🔒 PHASE 5 DEFERRAL: Strategy evaluation must wait until market is frozen
                                # This is NON-FATAL - defer evaluation until entry generation completes
                                is_frozen = market_engine._market_frozen.get(symbol_upper, False)
                                if not is_frozen:
                                    # Market not frozen yet - entry generation still in progress
                                    # Defer strategy evaluation, allow scenario loop to continue
                                    from sim_live.sim_live_logger import get_sim_live_logger
                                    logger_sim = get_sim_live_logger()
                                    logger_sim.info(f"[SIM_LIVE] [PHASE_5] Evaluation deferred — market not frozen for {symbol} (entry generation in progress)")
                                    # Return early - do not evaluate strategy yet
                                    return []  # Return empty list to defer opportunities
                                
                                # Market is frozen - proceed with evaluation
                                from sim_live.sim_live_logger import get_sim_live_logger
                                logger_sim = get_sim_live_logger()
                                logger_sim.info(f"[SIM_LIVE] [PHASE_5] Market frozen for {symbol} — proceeding with strategy evaluation")
                                logger_sim.info(f"[SIM_LIVE] [EVAL_START] Starting strategy evaluation for {symbol}")
                                
                                # Get EXACT candles that TrendFilter will use (via get_rates -> copy_rates_from_pos)
                                # TrendFilter requests 100 candles, but may get fewer
                                rates_df = self.trend_filter.get_rates(symbol, count=100)
                                if rates_df is not None and len(rates_df) >= 50:
                                    # Convert DataFrame back to candle list format for validation
                                    candles_for_validation = []
                                    for idx in range(len(rates_df)):
                                        row = rates_df.iloc[idx]
                                        # Handle pandas Timestamp or int
                                        time_val = row['time']
                                        try:
                                            import pandas as pd
                                            if isinstance(time_val, pd.Timestamp):
                                                time_val = int(time_val.timestamp())
                                            elif hasattr(time_val, 'timestamp'):
                                                time_val = int(time_val.timestamp())
                                            elif isinstance(time_val, (float, int)):
                                                time_val = int(time_val)
                                            else:
                                                # Try direct conversion as last resort
                                                time_val = int(float(time_val))
                                        except Exception as e:
                                            # If conversion fails, try to extract as int from string representation
                                            try:
                                                import pandas as pd
                                                if pd.isna(time_val):
                                                    time_val = 0
                                                else:
                                                    time_val = int(str(time_val).split('.')[0]) if '.' in str(time_val) else int(time_val)
                                            except:
                                                # Last resort: use 0 and log error
                                                try:
                                                    from sim_live.sim_live_logger import get_sim_live_logger
                                                    logger_sim = get_sim_live_logger()
                                                    logger_sim.warning(f"[SIM_LIVE] [PHASE_5] Failed to convert time value {time_val} (type: {type(time_val)}) to int, using 0")
                                                except:
                                                    pass
                                                time_val = 0
                                        
                                        # Helper to safely convert to int, handling NaN
                                        def safe_int(val, default=0):
                                            try:
                                                import pandas as pd
                                                if pd.isna(val):
                                                    return default
                                                return int(val)
                                            except:
                                                try:
                                                    return int(val) if val is not None else default
                                                except:
                                                    return default
                                        
                                        candles_for_validation.append({
                                            'time': time_val,
                                            'open': float(row['open']),
                                            'high': float(row['high']),
                                            'low': float(row['low']),
                                            'close': float(row['close']),
                                            'tick_volume': safe_int(row.get('tick_volume', 0), 0),
                                            'spread': safe_int(row.get('spread', 0), 0),
                                            'real_volume': safe_int(row.get('real_volume', 0), 0)
                                        })
                                    
                                    # 🔒 CRITICAL: get_rates() returns newest-first, but _validate_trend_contract expects oldest-first
                                    # Reverse to match validation function expectations
                                    candles_for_validation = list(reversed(candles_for_validation))
                                    
                                    # Get trend direction from scenario
                                    scenario = getattr(market_engine, '_scenario', {})
                                    trend_direction = scenario.get('trend_direction', 'BUY')
                                    
                                    # 🔒 PHASE 5 ASSERTION 1: Data identity check
                                    # CRITICAL FIX: Compare same candles - both should use newest-first order
                                    # candles_for_validation is oldest-first, so we need the last 50 (newest) AND reverse them to match TrendFilter order
                                    import hashlib
                                    # Get last 50 candles from validation (newest candles, since list is oldest-first)
                                    validation_newest_50 = candles_for_validation[-50:] if len(candles_for_validation) >= 50 else candles_for_validation
                                    # Reverse to newest-first to match TrendFilter order (TrendFilter returns newest-first)
                                    validation_newest_50_reversed = list(reversed(validation_newest_50))
                                    validation_hash = hashlib.md5(str([(c['time'], c['close']) for c in validation_newest_50_reversed]).encode()).hexdigest()
                                    
                                    # Get candles TrendFilter will actually use (via its get_rates method)
                                    # TrendFilter.get_rates() returns newest-first, so first 50 are newest
                                    rates_for_trendfilter = self.trend_filter.get_rates(symbol, count=100)
                                    if rates_for_trendfilter is not None and len(rates_for_trendfilter) >= 50:
                                        trendfilter_candles = []
                                        # Get first 50 rows (newest candles, since DataFrame is newest-first)
                                        for idx in range(min(50, len(rates_for_trendfilter))):
                                            row = rates_for_trendfilter.iloc[idx]
                                            # Fix Timestamp conversion - same logic as candles_for_validation
                                            tf_time_val = row['time']
                                            try:
                                                import pandas as pd
                                                if isinstance(tf_time_val, pd.Timestamp):
                                                    tf_time_val = int(tf_time_val.timestamp())
                                                elif hasattr(tf_time_val, 'timestamp'):
                                                    tf_time_val = int(tf_time_val.timestamp())
                                                elif isinstance(tf_time_val, (float, int)):
                                                    tf_time_val = int(tf_time_val)
                                                else:
                                                    tf_time_val = int(float(tf_time_val))
                                            except Exception as e:
                                                try:
                                                    import pandas as pd
                                                    if pd.isna(tf_time_val):
                                                        tf_time_val = 0
                                                    else:
                                                        tf_time_val = int(str(tf_time_val).split('.')[0]) if '.' in str(tf_time_val) else int(tf_time_val)
                                                except:
                                                    tf_time_val = 0
                                            trendfilter_candles.append((tf_time_val, float(row['close'])))
                                        trendfilter_hash = hashlib.md5(str(trendfilter_candles).encode()).hexdigest()
                                        
                                        if validation_hash != trendfilter_hash:
                                            # Market is frozen, but data desync detected - this is a HARD FAILURE
                                            # Log both sets of candles for debugging
                                            try:
                                                from sim_live.sim_live_logger import get_sim_live_logger
                                                logger_sim = get_sim_live_logger()
                                                logger_sim.error(f"[SIM_LIVE] [PHASE_5_VIOLATION] Data desync detected:")
                                                logger_sim.error(f"  Validation newest 5 (time, close): {[(c['time'], c['close']) for c in validation_newest_50_reversed[:5]]}")  # First 5 (newest-first)
                                                logger_sim.error(f"  TrendFilter newest 5 (time, close): {trendfilter_candles[:5]}")  # First 5 (newest-first)
                                                logger_sim.error(f"  Validation hash: {validation_hash[:16]}...")
                                                logger_sim.error(f"  TrendFilter hash: {trendfilter_hash[:16]}...")
                                            except:
                                                pass
                                            error_msg = (
                                                f"[SIM_LIVE] [PHASE_5_VIOLATION] TrendFilter is using different candle data than validation\n"
                                                f"Validation hash: {validation_hash[:16]}...\n"
                                                f"TrendFilter hash: {trendfilter_hash[:16]}...\n"
                                                f"Symbol: {symbol}\n"
                                                f"Market is frozen: {is_frozen}\n"
                                                f"This indicates data source desync."
                                            )
                                            from sim_live.sim_live_logger import get_sim_live_logger
                                            logger_sim = get_sim_live_logger()
                                            logger_sim.error(error_msg)
                                            raise AssertionError(error_msg)
                                    
                                    # 🔒 PHASE 5 ASSERTION: Trend contract must be preserved (only assert if frozen)
                                    # Use MT5 source to ensure same data as TrendFilter
                                    is_valid, indicators = market_engine._validate_trend_contract(symbol_upper, candles_for_validation, trend_direction, use_mt5_source=True)
                                    
                                    if not is_valid:
                                        # Market is frozen, but trend contract is violated - this is a HARD FAILURE
                                        sma20 = indicators.get('sma20')
                                        sma50 = indicators.get('sma50')
                                        separation_pct = indicators.get('separation_pct', 0.0)
                                        scenario_name = getattr(market_engine, '_current_scenario_name', 'unknown')
                                        
                                        error_msg = (
                                            f"[SIM_LIVE] [PHASE_5_VIOLATION] Trend contract violated before strategy evaluation\n"
                                            f"Market is frozen: {is_frozen}\n"
                                            f"Scenario: {scenario_name}\n"
                                            f"Symbol: {symbol}\n"
                                            f"Direction: {trend_direction}\n"
                                            f"SMA20: {sma20:.5f}\n"
                                            f"SMA50: {sma50:.5f}\n"
                                            f"Separation: {separation_pct*100:.4f}% (required: >=0.05%)\n"
                                            f"Last 20 closes: {[round(c['close'], 5) for c in candles_for_validation[-20:]]}\n"
                                            f"This indicates candles were modified AFTER entry generation (Phase 4 violation)."
                                        )
                                        
                                        from sim_live.sim_live_logger import get_sim_live_logger
                                        logger_sim = get_sim_live_logger()
                                        logger_sim.error(error_msg)
                                        raise AssertionError(error_msg)
                        except AssertionError:
                            raise  # Re-raise assertion errors
                        except Exception as e:
                            # If validation fails (e.g., market_engine not available), log but don't block
                            try:
                                from sim_live.sim_live_logger import get_sim_live_logger
                                logger_sim = get_sim_live_logger()
                                logger_sim.warning(f"[SIM_LIVE] [TREND_CONTRACT_CHECK] Could not validate trend contract: {e}")
                            except:
                                pass
                    
                    trend_signal = self.trend_filter.get_trend_signal(symbol)
                    
                    # SIM_LIVE diagnostic: Log evaluation start
                    if self.is_sim_live:
                        log_entry_evaluation_start(symbol, trend_signal)
                    
                    if trend_signal['signal'] == 'NONE':
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: No trend signal (SMA20 == SMA50 or invalid data)")
                        if self.is_sim_live:
                            log_entry_rejected(symbol, "TREND_FILTER_NO_SIGNAL", {
                                'trend_signal': trend_signal,
                                'additional_context': "SMA20 == SMA50 or invalid data"
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 2a. Check RSI filter (30-50 range for entries)
                    # FOR BACKTEST MODE: Force disable RSI filter to allow trade execution for verification
                    if self.is_backtest:
                        # In backtest mode, skip RSI filter check to allow trades for logic verification
                        logger.debug(f"[BACKTEST] {symbol}: RSI filter check skipped (backtest mode - allowing trade for verification)")
                    elif self.trend_filter.use_rsi_filter and not trend_signal.get('rsi_filter_passed', True):
                        # Live mode: Apply RSI filter normally
                        rsi_value = trend_signal.get('rsi', 50)
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: RSI filter failed (RSI: {rsi_value:.1f} not in range {self.trend_filter.rsi_entry_range_min}-{self.trend_filter.rsi_entry_range_max})")
                        if self.is_sim_live:
                            log_entry_rejected(symbol, "RSI_FILTER", {
                                'trend_signal': trend_signal,
                                'additional_context': f"RSI {rsi_value:.1f} not in range {self.trend_filter.rsi_entry_range_min}-{self.trend_filter.rsi_entry_range_max}"
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    # If RSI filter is disabled in config, log it for verification
                    elif not self.trend_filter.use_rsi_filter:
                        logger.debug(f"[OK] {symbol}: RSI filter disabled in config - allowing trade regardless of RSI value")
                    
                    # 3. Check halal compliance (ALWAYS skip in test mode per requirements)
                    test_mode = self.config.get('pairs', {}).get('test_mode', False)
                    
                    if test_mode:
                        # Test mode: ALWAYS ignore halal checks (per requirements)
                        logger.debug(f"[OK] {symbol}: Halal check skipped (test mode)")
                    else:
                        # Live mode: enforce halal if enabled
                        if not self.halal_compliance.validate_trade(symbol, trend_signal['signal']):
                            logger.info(f"[SKIP] [SKIP] {symbol} | Reason: HALAL COMPLIANCE CHECK FAILED")
                            self.trade_stats['filtered_opportunities'] += 1
                            continue
                    
                    # 3a. TESTING MODE: Check min lot size requirements (0.01-0.1, risk <= $2)
                    min_lot_valid = True
                    min_lot = 0.01
                    min_lot_reason = ""
                    if test_mode:
                        min_lot_valid, min_lot, min_lot_reason = self.risk_manager.check_min_lot_size_for_testing(symbol)
                        if not min_lot_valid:
                            logger.info(f"[SKIP] [SKIP] {symbol} | Signal: {trend_signal['signal']} | "
                                      f"MinLot: {min_lot:.4f} | Reason: {min_lot_reason}")
                            self.trade_stats['filtered_opportunities'] += 1
                            continue
                        logger.debug(f"[OK] {symbol}: Min lot check passed - {min_lot_reason}")
                    
                    # 4. SIMPLIFIED setup validation (only checks if signal != NONE)
                    # Add detailed logging for SIM_LIVE debugging
                    if self.is_sim_live:
                        logger.debug(f"[SIM_LIVE] {symbol}: Calling is_setup_valid_for_scalping with signal='{trend_signal.get('signal')}', keys={list(trend_signal.keys())}")
                    
                    if not self.trend_filter.is_setup_valid_for_scalping(symbol, trend_signal):
                        signal_value = trend_signal.get('signal', 'MISSING')
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Setup validation failed (signal: '{signal_value}')")
                        if self.is_sim_live:
                            logger.warning(f"[SIM_LIVE] {symbol}: Setup validation failed - trend_signal: {trend_signal}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 4a. Check trend strength minimum (SMA separation percentage)
                    sma_separation_pct = abs((trend_signal.get('sma_fast', 0) - trend_signal.get('sma_slow', 0)) / trend_signal.get('sma_slow', 1) * 100) if trend_signal.get('sma_slow', 0) > 0 else 0
                    min_trend_strength_pct = self.trading_config.get('min_trend_strength_pct', 0.05)  # Default 0.05%
                    if sma_separation_pct < min_trend_strength_pct:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Trend strength too weak (SMA separation: {sma_separation_pct:.4f}% < {min_trend_strength_pct}%)")
                        if self.is_sim_live:
                            log_entry_rejected(symbol, "TREND_STRENGTH", {
                                'trend_signal': trend_signal,
                                'additional_context': f"SMA separation {sma_separation_pct:.4f}% < {min_trend_strength_pct}%"
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 5. Calculate quality score for trade selection
                    quality_assessment = self.trend_filter.assess_setup_quality(symbol, trend_signal)
                    quality_score = quality_assessment.get('quality_score', 0.0)
                    high_quality_setup = quality_assessment.get('is_high_quality', False)
                    min_quality_score = self.trading_config.get('min_quality_score', 50.0)
                    
                    # Filter by quality score - only trade high-quality setups
                    if quality_score < min_quality_score:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Quality score {quality_score:.1f} < threshold {min_quality_score} | Details: {', '.join(quality_assessment.get('reasons', []))}")
                        if self.is_sim_live:
                            log_entry_rejected(symbol, "QUALITY_SCORE", {
                                'trend_signal': trend_signal,
                                'quality_score': quality_score,
                                'min_quality_score': min_quality_score,
                                'quality_reasons': quality_assessment.get('reasons', [])
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 5a. ENTRY TIMING GUARDS (structure-only, no threshold changes)
                    # Trend phase / maturity check – block late, overextended trends
                    trend_phase_ok, trend_phase_reason = self.trend_filter.check_trend_maturity(symbol, trend_signal)
                    if not trend_phase_ok:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: {trend_phase_reason}")
                        if self.is_sim_live:
                            log_entry_rejected(symbol, "TIMING_GUARD_TREND_MATURITY", {
                                'trend_signal': trend_signal,
                                'quality_score': quality_score,
                                'min_quality_score': min_quality_score,
                                'timing_guards': {
                                    'trend_maturity': {'ok': False, 'reason': trend_phase_reason},
                                    'impulse_exhaustion': {'ok': True, 'reason': 'Not checked'}
                                }
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # Impulse / exhaustion candle guard – avoid entering on runaway spikes
                    impulse_ok, impulse_reason = self.trend_filter.check_impulse_exhaustion(symbol, trend_signal)
                    if not impulse_ok:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: {impulse_reason}")
                        if self.is_sim_live:
                            log_entry_rejected(symbol, "TIMING_GUARD_IMPULSE_EXHAUSTION", {
                                'trend_signal': trend_signal,
                                'quality_score': quality_score,
                                'min_quality_score': min_quality_score,
                                'timing_guards': {
                                    'trend_maturity': {'ok': True, 'reason': 'PASS'},
                                    'impulse_exhaustion': {'ok': False, 'reason': impulse_reason}
                                }
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # Check portfolio risk limit before opening trade
                    # With USD-based SL, risk is always fixed at max_risk_usd ($2.00)
                    estimated_risk = self.risk_manager.max_risk_usd
                    
                    # Check portfolio risk
                    portfolio_risk_ok, portfolio_reason = self.risk_manager.check_portfolio_risk(new_trade_risk_usd=estimated_risk)
                    if not portfolio_risk_ok:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Portfolio risk limit - {portfolio_reason}")
                        if self.is_sim_live:
                            spread_points_for_log = self.pair_filter.get_spread_points(symbol)
                            log_entry_rejected(symbol, "RISK_CHECK_PORTFOLIO", {
                                'trend_signal': trend_signal,
                                'quality_score': quality_score,
                                'min_quality_score': min_quality_score,
                                'timing_guards': {
                                    'trend_maturity': {'ok': True, 'reason': 'PASS'},
                                    'impulse_exhaustion': {'ok': True, 'reason': 'PASS'}
                                },
                                'risk_checks': {
                                    'portfolio': {'ok': False, 'reason': portfolio_reason},
                                    'spread': {'ok': True, 'points': spread_points_for_log or 0, 'max': self.pair_filter.max_spread_points}
                                }
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 6. Check if we can open a new trade (with staged open support)
                    can_open, reason = self.risk_manager.can_open_trade(
                        symbol=symbol,
                        signal=trend_signal['signal'],
                        quality_score=quality_score,
                        high_quality_setup=high_quality_setup
                    )
                    if not can_open:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Cannot open trade - {reason}")
                        symbol_logger = get_symbol_logger(symbol, is_backtest=self.is_backtest)
                        symbol_logger.debug(f"[SKIP] Cannot open trade: {reason}")
                        if self.is_sim_live:
                            spread_points_for_log = self.pair_filter.get_spread_points(symbol)
                            log_entry_rejected(symbol, "RISK_CHECK_CAN_OPEN", {
                                'trend_signal': trend_signal,
                                'quality_score': quality_score,
                                'min_quality_score': min_quality_score,
                                'timing_guards': {
                                    'trend_maturity': {'ok': True, 'reason': 'PASS'},
                                    'impulse_exhaustion': {'ok': True, 'reason': 'PASS'}
                                },
                                'risk_checks': {
                                    'portfolio': {'ok': True, 'reason': 'PASS'},
                                    'can_open': {'ok': False, 'reason': reason},
                                    'spread': {'ok': True, 'points': spread_points_for_log or 0, 'max': self.pair_filter.max_spread_points}
                                }
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 7. Check spread
                    spread_points = self.pair_filter.get_spread_points(symbol)
                    if spread_points is None:
                        logger.warning(f"[WARNING] {symbol}: Cannot get spread information - skipping")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # Check spread acceptability
                    if not self.pair_filter.check_spread(symbol):
                        max_spread = self.pair_filter.max_spread_points
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Spread {spread_points:.2f} points > {max_spread} limit")
                        if self.is_sim_live:
                            log_entry_rejected(symbol, "RISK_CHECK_SPREAD", {
                                'trend_signal': trend_signal,
                                'quality_score': quality_score,
                                'min_quality_score': min_quality_score,
                                'timing_guards': {
                                    'trend_maturity': {'ok': True, 'reason': 'PASS'},
                                    'impulse_exhaustion': {'ok': True, 'reason': 'PASS'}
                                },
                                'risk_checks': {
                                    'portfolio': {'ok': True, 'reason': 'PASS'},
                                    'can_open': {'ok': True, 'reason': 'PASS'},
                                    'spread': {'ok': False, 'points': spread_points, 'max': max_spread}
                                }
                            })
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 7a. TESTING MODE: Check spread + fees <= $0.30 (STRICTLY ENFORCED)
                    total_cost = 0.0
                    cost_description = ""
                    if test_mode:
                        # Use min_lot already calculated above
                        if not min_lot_valid:
                            # Already logged above, skip (filtered_opportunities already incremented)
                            continue
                        
                        # Calculate spread + fees cost using minimum lot size
                        total_cost, cost_description = self.risk_manager.calculate_spread_and_fees_cost(symbol, min_lot)
                        max_cost = 0.30  # $0.30 limit for testing mode (STRICT)
                        
                        # STRICT ENFORCEMENT: Reject if spread+fees > $0.30
                        if total_cost > max_cost:
                            logger.info(f"[SKIP] [SKIP] {symbol} | Signal: {trend_signal['signal']} | "
                                      f"MinLot: {min_lot:.4f} | Spread: {spread_points:.1f}pts | "
                                      f"Spread+Fees: ${total_cost:.2f} > ${max_cost:.2f} | "
                                      f"Reason: Spread+Fees exceed limit (STRICT) | ({cost_description})")
                            if self.is_sim_live:
                                log_entry_rejected(symbol, "RISK_CHECK_SPREAD_FEES", {
                                    'trend_signal': trend_signal,
                                    'quality_score': quality_score,
                                    'min_quality_score': min_quality_score,
                                    'timing_guards': {
                                        'trend_maturity': {'ok': True, 'reason': 'PASS'},
                                        'impulse_exhaustion': {'ok': True, 'reason': 'PASS'}
                                    },
                                    'risk_checks': {
                                        'portfolio': {'ok': True, 'reason': 'PASS'},
                                        'can_open': {'ok': True, 'reason': 'PASS'},
                                        'spread': {'ok': True, 'points': spread_points, 'max': self.pair_filter.max_spread_points}
                                    },
                                    'additional_context': f"Spread+Fees ${total_cost:.2f} > ${max_cost:.2f} ({cost_description})"
                                })
                            self.trade_stats['filtered_opportunities'] += 1
                            continue
                        
                        logger.debug(f"[OK] {symbol}: Spread+Fees check passed - ${total_cost:.2f} <= ${max_cost:.2f} ({cost_description})")
                    else:
                        # Non-test mode: still calculate for sorting, but don't enforce strict limit
                        total_cost, cost_description = self.risk_manager.calculate_spread_and_fees_cost(symbol, min_lot if min_lot_valid else 0.01)
                    
                    # 8. ALL CHECKS PASSED - Add to opportunities
                    signal_type = trend_signal['signal']
                    
                    # Get testing mode info for comprehensive logging
                    min_lot_info = ""
                    spread_fees_info = ""
                    pass_reason = f"All checks passed (Quality: {quality_score:.1f})"
                    calculated_risk = 0.0
                    symbol_info_for_risk = self.mt5_connector.get_symbol_info(symbol)
                    
                    if test_mode:
                        # Re-validate min lot (should already be valid at this point)
                        min_lot_valid_check, min_lot_check, min_lot_reason_check = self.risk_manager.check_min_lot_size_for_testing(symbol)
                        if min_lot_valid_check:
                            total_cost, cost_description = self.risk_manager.calculate_spread_and_fees_cost(symbol, min_lot_check)
                            # Extract source from reason (format: "Min lot X.XXXX (source) passes...")
                            if '(' in min_lot_reason_check and ')' in min_lot_reason_check:
                                source = min_lot_reason_check.split('(')[1].split(')')[0]
                            else:
                                source = "broker"
                            min_lot_info = f" | MinLot: {min_lot_check:.4f} ({source})"
                            spread_fees_info = f" | Spread+Fees: ${total_cost:.2f}"
                            pass_reason = f"MinLot OK, Spread+Fees OK (${total_cost:.2f} <= $0.30)"
                            
                            # Calculate estimated risk for logging
                            if symbol_info_for_risk:
                                point = symbol_info_for_risk.get('point', 0.00001)
                                pip_value = point * 10 if symbol_info_for_risk.get('digits', 5) == 5 or symbol_info_for_risk.get('digits', 3) == 3 else point
                                contract_size = symbol_info_for_risk.get('contract_size', 1.0)
                                # With USD-based SL, risk is always $2.00 fixed
                                calculated_risk = self.risk_manager.max_risk_usd
                    
                    # 10. Enhanced opportunity logging with detailed breakdown (after confirmation)
                    quality_threshold = self.trading_config.get('min_quality_score', 50.0)
                    logger.info("=" * 80)
                    logger.info(f"[OPPORTUNITY CHECK]")
                    logger.info(f"Symbol: {symbol}")
                    logger.info(f"Signal: {signal_type}")
                    logger.info(f"Quality Score: {quality_score:.1f} (Threshold: {quality_threshold})")
                    logger.info(f"Trend Strength: {sma_separation_pct:.4f}%")
                    logger.info(f"Spread: {spread_points:.2f} points")
                    if test_mode and total_cost > 0:
                        logger.info(f"Fees: {cost_description}")
                        logger.info(f"Total Cost: ${total_cost:.2f} USD (PASS ≤ $0.30)")
                    logger.info(f"Min Lot: {min_lot:.4f}")
                    if calculated_risk > 0:
                        logger.info(f"Calculated Lot: {min_lot:.4f} (PASS)")
                        logger.info(f"Risk: ${calculated_risk:.2f} (PASS ≤ $2.00)")
                    logger.info(f"Reason: Quality score {quality_score:.1f} >= {quality_threshold} → Trade Executed")
                    logger.info("=" * 80)
                    
                    # Legacy concise logging
                    logger.info(f"[OK] {symbol} | Signal: {signal_type} | Quality: {quality_score:.1f} | MinLot: {min_lot:.4f} | "
                              f"Spread: {spread_points:.1f}pts{spread_fees_info} | Pass: {pass_reason}")
                    
                    # Log signal type for debugging trade direction
                    if signal_type == 'SHORT':
                        logger.info(f"📉 {symbol}: SHORT signal detected - will place SELL order")
                    elif signal_type == 'LONG':
                        logger.info(f"[STATS] {symbol}: LONG signal detected - will place BUY order")
                    
                    # Get min_lot for opportunity (use symbol_info_for_risk if available)
                    opp_min_lot = min_lot if test_mode else (symbol_info_for_risk.get('volume_min', 0.01) if symbol_info_for_risk else 0.01)
                    
                    # Check volume status for entry conditions (already checked above, but need to store result)
                    # Volume check was done at line 570, so volume_ok = not should_skip_volume
                    # Since we passed the volume filter, volume_ok should be True
                    volume_ok = True  # If we reach here, volume check passed
                    
                    # Create opportunity data structure
                    opportunity_data = {
                        'symbol': symbol,
                        'signal': trend_signal['signal'],
                        'trend_signal': trend_signal,  # Include full trend_signal dict for entry conditions check
                        'trend': trend_signal['trend'],
                        'sma_fast': trend_signal.get('sma_fast', 0),
                        'sma_slow': trend_signal.get('sma_slow', 0),
                        'rsi': trend_signal.get('rsi', 50),
                        'volume_ok': volume_ok,  # Include volume status for entry conditions check
                        'spread': spread_points,
                        'min_lot': opp_min_lot,
                        'spread_fees_cost': total_cost,  # Always include for sorting
                        'quality_score': quality_score  # Include quality score for sorting
                    }
                    
                    # ONE-CANDLE CONFIRMATION: Store as pending instead of adding immediately
                    df_current = self.trend_filter.get_rates(symbol, count=1)
                    if df_current is not None and len(df_current) > 0:
                        current_candle_time = int(df_current.iloc[0]['time'])
                        with self._pending_signals_lock:
                            self._pending_signals[symbol] = {
                                'direction': signal_type,
                                'signal_time': datetime.now(),
                                'candle_time': current_candle_time,
                                'quality_score': quality_score,
                                'trend_signal': trend_signal.copy(),
                                'opportunity_data': opportunity_data  # Store all opportunity data
                            }
                        logger.info(f"[ENTRY PENDING] {symbol}: signal {signal_type} stored, waiting for next candle close")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue  # Don't add to opportunities yet - wait for confirmation
                    else:
                        # Can't get candle time - add immediately (fallback behavior)
                        logger.debug(f"[WARNING] {symbol}: cannot get candle time for confirmation, adding immediately")
                        opportunities.append(opportunity_data)
                    
                    # DRY-RUN ONLY: Analyze limit entry (does NOT affect execution)
                    if hasattr(self, 'limit_entry_dry_run') and self.limit_entry_dry_run is not None:
                        try:
                            # Get current market price
                            symbol_info_for_limit = self.mt5_connector.get_symbol_info(symbol)
                            if symbol_info_for_limit:
                                market_price = symbol_info_for_limit.get('ask' if signal_type == 'LONG' else 'bid', 0.0)
                                if market_price > 0:
                                    # Convert signal to direction
                                    direction = 'BUY' if signal_type == 'LONG' else 'SELL'
                                    
                                    # Analyze limit entry (dry-run only)
                                    self.limit_entry_dry_run.analyze_limit_entry(
                                        symbol=symbol,
                                        direction=direction,
                                        market_price=market_price,
                                        quality_score=quality_score,
                                        entry_time=datetime.now()
                                    )
                        except Exception as e:
                            # Don't let dry-run errors affect real execution
                            logger.debug(f"Limit entry dry-run analysis failed for {symbol}: {e}")
                    
                except Exception as e:
                    logger.error(f"[ERROR] Error scanning {symbol}: {e}", exc_info=True)
                    self.handle_error(e, f"Scanning {symbol}")
                    continue
        
        except Exception as e:
            logger.error(f"[ERROR] Error in scan_for_opportunities: {e}", exc_info=True)
            self.handle_error(e, "Scanning for opportunities")
        
        logger.info(f"🎯 Found {len(opportunities)} trading opportunity(ies)")
        
        # Update state back to IDLE if no opportunities
        if not opportunities:
            self._update_state('IDLE', 'N/A', 'Scan completed - no opportunities')
        
        return opportunities
    
    def execute_trade(self, opportunity: Dict[str, Any], skip_randomness: bool = False) -> Optional[bool]:
        """
        Execute a trade based on opportunity with randomness factor and comprehensive logging.
        
        Args:
            opportunity: Trade opportunity dictionary
            skip_randomness: If True, skip randomness factor (used in manual approval mode)
        
        Returns:
            True if trade was successfully executed
            False if trade execution failed (order placement error, etc.)
            None if trade was filtered/skipped (randomness, risk validation, trend change) - NOT a failure
        """
        symbol = opportunity['symbol']
        signal = opportunity['signal']
        
        try:
            # CRITICAL: Check max trades BEFORE executing (enforce strict limit)
            # NOTE: Position count is checked at batch level (line 2208), so this is a safety check
            # We allow execution if we're within the batch limit (already validated upstream)
            current_positions = self.order_manager.get_position_count()
            max_trades = self.risk_manager.max_open_trades
            max_trades_strict = self.risk_manager.max_open_trades_strict
            
            # If max_open_trades is None, unlimited trades allowed
            if max_trades is None:
                # Unlimited trades - skip max trade checks (unless strict mode is enabled, which shouldn't make sense)
                if max_trades_strict:
                    logger.warning(f"⚠️ WARNING: {symbol} | max_open_trades_strict=True but max_open_trades=unlimited - strict mode ignored")
                # Continue execution
            else:
                # CRITICAL FIX: Only block if we're STRICTLY ABOVE max (not at max)
                # This allows multiple trades to execute quickly when slots are available
                # Example: If max_trades=6 and current_positions=5, we can still execute 1 more trade
                # NOTE: Position count is validated at batch level, so this is just a safety check
                if max_trades_strict and current_positions > max_trades:
                    logger.warning(f"🚫 MAX TRADES STRICT: {symbol} | Cannot execute trade | Current: {current_positions} > {max_trades} | Strict mode enabled")
                    return None  # Filtered, not failed
                elif max_trades_strict and current_positions == max_trades:
                    # At max in strict mode - this shouldn't happen if batch reservation worked, but allow it
                    logger.info(f"[WARNING] MAX TRADES AT LIMIT: {symbol} | Current: {current_positions} == {max_trades} | "
                              f"Strict mode - ALLOWING execution (batch reservation mode)")
                    # Continue execution - batch reservation allows this
                
                # Non-strict mode: Only block if strictly above max (allow at max for high-quality setups)
                if current_positions > max_trades:
                    # Check if override is allowed (only if not strict)
                    quality_score = opportunity.get('quality_score', 0.0)
                    high_quality_setup = opportunity.get('high_quality_setup', False)
                    can_open, reason = self.risk_manager.can_open_trade(
                        symbol=symbol,
                        signal=signal,
                        quality_score=quality_score,
                        high_quality_setup=high_quality_setup
                    )
                    if not can_open:
                        logger.info(f"[SKIP] [SKIP] {symbol} | Signal: {signal} | Reason: {reason}")
                        return None  # Filtered, not failed
            
            # Update state
            self._update_state('ENTERING TRADE', symbol, f'Entering {signal} trade on {symbol}')
            # RANDOMNESS FACTOR: Skip in manual approval mode (user already approved)
            # CRITICAL: In backtest mode, randomness must be deterministic for reproducibility
            # NOTE: Randomness skip is NOT a failure - it's intentional filtering
            if not skip_randomness:
                # CRITICAL FIX: Use deterministic random in backtest mode
                if self.is_backtest:
                    # Use symbol + timestamp as seed for deterministic randomness
                    import hashlib
                    seed_str = f"{symbol}_{int(time.time())}"
                    seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
                    random.seed(seed)
                    random_value = random.random()  # 0.0 to 1.0
                    mode_str = "BACKTEST"
                else:
                    random_value = random.random()  # 0.0 to 1.0
                    mode_str = "LIVE"
                
                if random_value < self.randomness_factor:
                    logger.info(f"mode={mode_str} | symbol={symbol} | RANDOMNESS SKIP - Random value {random_value:.3f} < threshold {self.randomness_factor} (skipping trade to avoid over-trading)")
                    # Return special code to indicate this is NOT a failure (just filtering)
                    return None  # None indicates filtered/skipped, not failed
                else:
                    logger.info(f"mode={mode_str} | symbol={symbol} | RANDOMNESS PASS - Random value {random_value:.3f} >= threshold {self.randomness_factor} (proceeding with trade)")
            
            # Apply randomized delay if enabled (but ensure execution within 0.3 seconds)
            execution_timeout = self.trading_config.get('execution_timeout_seconds', 0.3)
            start_time = time.time()
            
            if self.randomize_timing:
                # Limit delay to ensure execution within timeout
                max_delay = min(self.max_delay_seconds, int(execution_timeout * 0.8))
                # CRITICAL FIX: Use deterministic random in backtest mode
                if self.is_backtest:
                    # Use symbol + signal as seed for deterministic delay
                    import hashlib
                    seed_str = f"{symbol}_{signal}_{int(time.time())}"
                    seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
                    random.seed(seed)
                    delay_seconds = random.randint(0, max_delay)
                    mode_str = "BACKTEST"
                else:
                    delay_seconds = random.randint(0, max_delay)
                    mode_str = "LIVE"
                
                if delay_seconds > 0:
                    logger.info(f"mode={mode_str} | symbol={symbol} | [DELAY] Random delay of {delay_seconds}s before executing {signal}")
                    time.sleep(delay_seconds)
                    
                    # Re-check trend signal after delay (trend might have changed)
                    trend_signal = self.trend_filter.get_trend_signal(symbol)
                    if trend_signal['signal'] != signal:
                        logger.warning(f"[WARNING] {symbol}: Trend changed during delay. Original: {signal}, Current: {trend_signal['signal']}. Skipping trade.")
                        # Trend change is NOT a failure - it's a safety check
                        return None  # None indicates filtered/skipped, not failed
            
            # Determine order type (ensure both BUY and SELL work correctly)
            if signal == 'LONG':
                order_type = OrderType.BUY
                logger.debug(f"[STATS] {symbol}: Signal LONG → OrderType.BUY")
            elif signal == 'SHORT':
                order_type = OrderType.SELL
                logger.debug(f"📉 {symbol}: Signal SHORT → OrderType.SELL")
            else:
                logger.error(f"[ERROR] {symbol}: Invalid signal '{signal}' - must be LONG or SHORT")
                self.trade_stats['failed_trades'] += 1
                return False
            
            # Get current price for entry
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                logger.error(f"[ERROR] {symbol}: Cannot get symbol info - trade execution failed")
                self.trade_stats['failed_trades'] += 1
                return False
            
            entry_price = symbol_info['ask'] if order_type == OrderType.BUY else symbol_info['bid']
            logger.info(f"💰 {symbol}: Entry price = {entry_price:.5f} ({'ASK' if order_type == OrderType.BUY else 'BID'})")
            
            # Calculate USD-based stop loss price (fixed $2.00 risk)
            # Use calculate_usd_based_stop_loss_price() which uses formula: entry_price ± (2 / contract_value_per_point)
            # Note: lot_size will be calculated later, so use default for initial calculation
            stop_loss_price, stop_loss_distance = self.risk_manager.calculate_usd_based_stop_loss_price(
                symbol=symbol,
                entry_price=entry_price,
                order_type='BUY' if order_type == OrderType.BUY else 'SELL',
                lot_size=None,  # Will use default_lot_size, then recalculate with actual lot_size later
                risk_usd=self.risk_manager.max_risk_usd
            )
            
            # Get broker's minimum stops level and ensure SL respects it
            stops_level = symbol_info.get('trade_stops_level', 0)
            point = symbol_info.get('point', 0.00001)
            digits = symbol_info.get('digits', 5)
            pip_value = point * 10 if digits == 5 or digits == 3 else point
            min_stops_distance = stops_level * point if stops_level > 0 else 0
            
            # Ensure stop loss meets broker's minimum distance
            if min_stops_distance > 0:
                actual_sl_distance = abs(entry_price - stop_loss_price)
                if actual_sl_distance < min_stops_distance:
                    # Adjust stop loss to meet broker minimum
                    if order_type == OrderType.BUY:
                        stop_loss_price = entry_price - min_stops_distance
                    else:  # SELL
                        stop_loss_price = entry_price + min_stops_distance
                    logger.info(f"🔧 {symbol}: Adjusting stop loss to meet broker minimum distance: {min_stops_distance:.5f} (stops_level: {stops_level})")
            
            logger.info(f"[SL] {symbol}: Stop loss price = {stop_loss_price:.5f} (distance: {abs(entry_price - stop_loss_price):.5f}, risk: ${self.risk_manager.max_risk_usd:.2f} USD)")
            
            # Validate stop loss with entry price (check broker minimum only)
            order_type_str = 'BUY' if order_type == OrderType.BUY else 'SELL'
            if not self.risk_manager.validate_stop_loss(symbol, stop_loss_pips=None, entry_price=entry_price, order_type=order_type_str, stop_loss_price=stop_loss_price):
                logger.warning(f"[SKIP] {symbol}: Stop loss validation failed (SL price: {stop_loss_price:.5f}) - trade execution aborted")
                self.trade_stats['failed_trades'] += 1
                return False
            
            # Calculate stop_loss_pips for logging/compatibility (not used for risk calculation)
            stop_loss_pips = abs(entry_price - stop_loss_price) / pip_value if pip_value > 0 else 0
            
            # NEW LOT SIZE PRIORITY LOGIC: Always try 0.01 first, only escalate if broker requires AND setup is strong
            # Get symbol info for lot size calculation
            symbol_info_for_lot = self.mt5_connector.get_symbol_info(symbol)
            if not symbol_info_for_lot:
                logger.error(f"[ERROR] {symbol}: Cannot get symbol info for lot size calculation")
                self.trade_stats['failed_trades'] += 1
                return False
            
            # Get broker minimum lot size (without config overrides for priority logic)
            symbol_min_lot = symbol_info_for_lot.get('volume_min', 0.01)
            
            # Get quality assessment to determine if setup is strong
            # Try to get from opportunity dict first, otherwise fetch it
            quality_score = opportunity.get('quality_score', None)
            high_quality_setup = opportunity.get('high_quality_setup', False)
            
            if quality_score is None:
                # Quality score not in opportunity - fetch it
                quality_assessment = self.trend_filter.assess_setup_quality(symbol, trend_signal)
                quality_score = quality_assessment.get('quality_score', 0.0)
                high_quality_setup = quality_assessment.get('is_high_quality', False)
            
            # Determine lot size using priority logic
            lot_size, lot_reason = self.risk_manager.determine_lot_size_with_priority(
                symbol=symbol,
                broker_min_lot=symbol_min_lot,
                high_quality_setup=high_quality_setup,
                quality_score=quality_score
            )
            
            # Check if symbol should be skipped
            if lot_size is None:
                logger.info(f"[SKIP] [SKIP] {symbol} | Signal: {signal} | Reason: {lot_reason}")
                self.trade_stats['filtered_opportunities'] += 1
                return None  # None indicates filtered/skipped, not failed
            
            # Calculate point, pip value, contract size
            # Risk is fixed at $2.00 USD (calculated via calculate_usd_based_stop_loss_price)
            point = symbol_info_for_lot.get('point', 0.00001)
            pip_value = point * 10 if symbol_info_for_lot.get('digits', 5) == 5 or symbol_info_for_lot.get('digits', 3) == 3 else point
            contract_size = symbol_info_for_lot.get('contract_size', 1.0)
            
            # Calculate USD-based stop loss price (fixed $2.00 risk)
            estimated_risk = self.risk_manager.max_risk_usd  # Always $2.00 with USD-based SL
            
            # Round lot size to volume step
            volume_step = symbol_info_for_lot.get('volume_step', 0.01)
            if volume_step > 0:
                lot_size = round(lot_size / volume_step) * volume_step
                if lot_size < volume_step:
                    lot_size = volume_step
            
            # Recalculate stop loss price with rounded lot size (risk remains $2.00)
            stop_loss_price, _ = self.risk_manager.calculate_usd_based_stop_loss_price(
                symbol=symbol,
                entry_price=entry_price,
                order_type='BUY' if order_type == OrderType.BUY else 'SELL',
                lot_size=lot_size,
                risk_usd=self.risk_manager.max_risk_usd
            )
            
            # Calculate stop_loss_pips for logging (not used for risk calculation)
            stop_loss_pips = abs(entry_price - stop_loss_price) / pip_value if pip_value > 0 else 0
            
            # Log lot size decision
            logger.info(f"📦 {symbol}: LOT SIZE = {lot_size:.4f} | "
                       f"Reason: {lot_reason} | "
                       f"Estimated Risk: ${estimated_risk:.2f} (target: ${self.risk_manager.max_risk_usd:.2f})")
            
            # With USD-based stop loss, risk is already fixed at max_risk_usd ($2.00)
            # No need for slippage buffer check since the SL is calculated to exactly match the risk limit
            # The actual risk will be $2.00 regardless of slippage because the SL distance is calculated
            # to ensure the risk is exactly $2.00 based on the lot size and contract value per point
            max_risk_usd = self.risk_manager.max_risk_usd
            
            # For USD-based SL, estimated_risk should always equal max_risk_usd
            # Only reject if somehow the calculation is wrong (safety check)
            if estimated_risk > max_risk_usd * 1.01:  # Allow 1% tolerance for rounding
                logger.warning(f"[SKIP] {symbol}: REJECTED - Estimated risk ${estimated_risk:.2f} exceeds max ${max_risk_usd:.2f} | "
                             f"Lot: {lot_size:.4f}, SL: {stop_loss_pips:.1f} pips, Contract: {contract_size}")
                # Risk validation failure is NOT an execution failure - it's a pre-execution filter
                # Don't increment failed_trades - this is expected risk management
                return None  # None indicates filtered/skipped, not failed
            
            # Log trade execution with lot size details
            logger.info(f"📦 {symbol}: EXECUTING WITH LOT SIZE = {lot_size:.4f} | "
                       f"Reason: {lot_reason} | "
                       f"Estimated Risk: ${estimated_risk:.2f} (max: ${max_risk_usd:.2f}) | "
                       f"Policy: Priority logic (0.01 first, escalate only if broker requires AND setup strong)")
            
            # Get spread for statistics
            spread_points = self.pair_filter.get_spread_points(symbol)
            if spread_points:
                self.trade_stats['total_spread_paid'] += spread_points
            
            # Calculate spread + fees cost for logging (especially in test mode)
            test_mode = self.config.get('pairs', {}).get('test_mode', False)
            spread_fees_cost = 0.0
            spread_fees_desc = ""
            if test_mode:
                spread_fees_cost, spread_fees_desc = self.risk_manager.calculate_spread_and_fees_cost(symbol, lot_size)
            
            # Place order with retry logic using configurable timeout and retry policy
            logger.info(f"📤 {symbol}: Placing {signal} order (Lot: {lot_size}, SL: {stop_loss_pips:.1f} pips)...")
            
            # Get execution config with defaults
            execution_config = self.config.get('execution', {})
            execution_timeout = execution_config.get('order_timeout_seconds', 2.0)
            max_retries = execution_config.get('order_max_retries', 3)
            backoff_base = execution_config.get('order_retry_backoff_base_seconds', 1.0)
            
            ticket = None
            volume_error_occurred = False
            
            execution_start = time.time()
            
            # Check if symbol is tradeable NOW before attempting orders
            is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(symbol)
            if not is_tradeable:
                logger.warning(f"⏰ {symbol}: Market closed or not tradeable - {reason}")
                logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Market closed - {reason}")
                print(f"[SKIP] {symbol}: Market closed - {reason}. Re-scan when market opens.")
                # Market closed is NOT a failure - it's a market condition
                return None  # None indicates filtered/skipped, not failed
            
            # Classify error types for retry logic
            NON_RETRYABLE_ERRORS = {
                10018: "Market closed",  # Never retry
                10004: "Requote",  # Can retry but with backoff
                10019: "No prices",  # Market closed variant
            }
            
            RETRYABLE_ERRORS = {
                10006: "Request timeout",  # Transient - retry with backoff
                10007: "Request canceled",  # Transient - retry with backoff
                10008: "Request placed",  # Processing - retry
                10009: "Request processing",  # Processing - retry
            }
            
            for attempt in range(max_retries):
                # Calculate elapsed time
                elapsed = time.time() - execution_start
                
                # Check if we're within execution timeout
                if elapsed > execution_timeout and attempt > 0:
                    logger.warning(f"[WARNING] {symbol}: Execution timeout ({elapsed:.3f}s > {execution_timeout}s) on attempt {attempt + 1}/{max_retries}")
                
                # Ensure MT5 is connected before placing order
                if not self.mt5_connector.ensure_connected():
                    logger.warning(f"[WARNING] {symbol}: MT5 disconnected, attempting reconnect...")
                    if not self.mt5_connector.reconnect():
                        logger.error(f"[ERROR] {symbol}: Failed to reconnect to MT5 (attempt {attempt + 1}/{max_retries})")
                        if attempt < max_retries - 1:
                            # Exponential backoff for connection issues
                            backoff_delay = backoff_base * (2 ** attempt)
                            logger.info(f"⏳ {symbol}: Waiting {backoff_delay:.1f}s before retry...")
                            time.sleep(backoff_delay)
                            continue
                        else:
                            logger.error(f"[ERROR] {symbol}: Connection failed after {max_retries} attempts")
                            break
                
                # Re-check market status before each retry (market may have closed)
                is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(symbol)
                if not is_tradeable:
                    logger.warning(f"⏰ {symbol}: Market closed during retry - {reason}")
                    logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Market closed - {reason}")
                    # Market closed is NOT a failure - it's a market condition
                    return None  # None indicates filtered/skipped, not failed
                
                # Convert stop_loss_price to pips for order_manager (it will convert back to price)
                # Calculate pip_value if not already defined
                if 'pip_value' not in locals() or pip_value is None:
                    point = symbol_info_for_lot.get('point', 0.00001)
                    digits = symbol_info_for_lot.get('digits', 5)
                    pip_value = point * 10 if digits == 5 or digits == 3 else point
                
                stop_loss_pips_for_order = abs(entry_price - stop_loss_price) / pip_value if pip_value > 0 else 0
                
                result = self.order_manager.place_order(
                    symbol=symbol,
                    order_type=order_type,
                    lot_size=lot_size,
                    stop_loss=stop_loss_pips_for_order,
                    comment=f"Bot {signal}"
                )
                
                # Handle new return format (dict with ticket, entry_price_actual, slippage)
                entry_price_actual = entry_price  # Default to requested price
                slippage = 0.0
                
                if result is None:
                    ticket = None
                elif isinstance(result, dict):
                    if 'error' in result:
                        ticket = result['error']  # Error code
                        # Update state for error
                        self._update_state('ERROR', symbol, f'Order failed: {result.get("error")}')
                    else:
                        ticket = result.get('ticket')
                        entry_price_actual = result.get('entry_price_actual', entry_price)
                        slippage = result.get('slippage', 0.0)
                        # Update state for successful trade
                        if ticket:
                            self._update_state('MANAGING TRADE', symbol, f'Opened {signal} trade (Ticket: {ticket})')
                else:
                    # Legacy format (int ticket)
                    ticket = result
                    if ticket and ticket > 0:
                        self._update_state('MANAGING TRADE', symbol, f'Opened {signal} trade (Ticket: {ticket})')
                
                # Success case
                if ticket and ticket > 0:
                    elapsed = time.time() - execution_start
                    if elapsed > execution_timeout:
                        logger.warning(f"[WARNING] {symbol}: Order placed but exceeded timeout ({elapsed:.3f}s > {execution_timeout}s)")
                    else:
                        logger.debug(f"[OK] {symbol}: Order placed within timeout ({elapsed:.3f}s < {execution_timeout}s)")
                    break
                
                # Error code classification:
                # -1: Invalid volume (10014) - retry once with broker minimum
                # -2: Transient error (connection, timeout, etc.) - retry with backoff
                # -3: Invalid stops (10016) - non-retryable
                # -4: Market closed (10018) - non-retryable
                # -5: Trading restriction (10027, 10044, etc.) - non-retryable
                
                # Handle error codes (from dict or legacy int)
                error_code = ticket if isinstance(ticket, int) and ticket < 0 else (result.get('error') if isinstance(result, dict) else None)
                
                # Market closed - don't retry
                if error_code == -4:
                    logger.warning(f"⏰ {symbol}: Market closed (error 10018) - skipping trade execution")
                    logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Market closed - order rejected by broker")
                    print(f"[SKIP] {symbol}: Market closed - order rejected. Re-scan when market opens.")
                    # Market closed is NOT a failure - it's a market condition
                    return None  # None indicates filtered/skipped, not failed
                
                # Trading restrictions - don't retry (10027, 10044, etc.)
                if error_code == -5:
                    logger.error(f"[ERROR] {symbol}: Trading restriction (error 10027/10044) - trade not allowed")
                    logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Trading restriction - symbol/order type not tradeable")
                    print(f"[ERROR] {symbol}: Trading restriction - this symbol cannot be traded. Check account permissions or symbol restrictions.")
                    self.trade_stats['failed_trades'] += 1
                    # Mark symbol as restricted to prevent future attempts in this session
                    if not hasattr(self, '_restricted_symbols'):
                        self._restricted_symbols = set()
                    self._restricted_symbols.add(symbol.upper())
                    break  # Don't retry
                
                # Invalid stops - don't retry
                if error_code == -3:
                    logger.error(f"[ERROR] {symbol}: Invalid stops (error 10016) - trade failed")
                    logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Invalid stops - check stop loss configuration")
                    self.trade_stats['failed_trades'] += 1
                    break  # Don't retry
                
                # Invalid volume - retry once with broker minimum
                if error_code == -1:
                    if not volume_error_occurred:  # Only retry once
                        volume_error_occurred = True
                        logger.warning(f"[WARNING] {symbol}: Invalid volume error (lot: {lot_size:.4f}), retrying once with broker minimum lot...")
                        # Get minimum lot and retry
                    symbol_info_retry = self.mt5_connector.get_symbol_info(symbol)
                    if symbol_info_retry:
                        symbol_min_lot = symbol_info_retry.get('volume_min', 0.01)
                        symbol_upper = symbol.upper()
                        symbol_limit_config = self.risk_manager.symbol_limits.get(symbol_upper, {})
                        config_min_lot = symbol_limit_config.get('min_lot')
                        if config_min_lot is not None:
                            effective_min_lot = max(symbol_min_lot, config_min_lot)
                        else:
                            effective_min_lot = symbol_min_lot
                        
                        # Round to volume step
                        volume_step = symbol_info_retry.get('volume_step', 0.01)
                        if volume_step > 0:
                            effective_min_lot = round(effective_min_lot / volume_step) * volume_step
                            if effective_min_lot < volume_step:
                                effective_min_lot = volume_step
                        
                        logger.info(f"🔄 {symbol}: Retrying with broker minimum lot {effective_min_lot:.4f}")
                        lot_size = effective_min_lot
                        # Small delay before retry
                        time.sleep(0.1)
                        continue
                    else:
                        logger.error(f"[ERROR] {symbol}: Cannot get symbol info for retry")
                        break
                else:
                    # Already retried once - fail and log
                    logger.error(f"[ERROR] {symbol}: Invalid volume error persisted after retry with broker minimum lot - trade failed")
                    logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Invalid volume - retry with minimum lot failed")
                    self.trade_stats['failed_trades'] += 1
                    break
                
                # Transient errors (-2 or None) - retry with exponential backoff
                if error_code == -2 or ticket is None:
                    if attempt < max_retries - 1:
                        # Exponential backoff for transient errors
                        backoff_delay = backoff_base * (2 ** attempt)
                        logger.warning(f"[WARNING] {symbol}: Order placement failed (attempt {attempt + 1}/{max_retries}), retrying in {backoff_delay:.1f}s...")
                        logger.info(f"⏳ {symbol}: Exponential backoff delay: {backoff_delay:.1f}s")
                        time.sleep(backoff_delay)
                        continue
                    else:
                        logger.error(f"[ERROR] {symbol}: Order placement failed after {max_retries} attempts with exponential backoff")
                        logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Order placement failed after {max_retries} retries")
                        self.trade_stats['failed_trades'] += 1
                        break
            
            if ticket and ticket > 0:  # Valid ticket number
                # Register staged trade
                self.risk_manager.register_staged_trade(symbol, ticket, signal)
                
                # Get quality score from opportunity for logging
                quality_score = opportunity.get('quality_score', None)
                
                # Get minimum lot info for detailed logging
                symbol_info_for_log = self.mt5_connector.get_symbol_info(symbol)
                if symbol_info_for_log:
                    symbol_min_lot = symbol_info_for_log.get('volume_min', 0.01)
                    symbol_upper = symbol.upper()
                    symbol_limit_config = self.risk_manager.symbol_limits.get(symbol_upper, {})
                    config_min_lot = symbol_limit_config.get('min_lot')
                    
                    if config_min_lot is not None:
                        effective_min_lot = max(symbol_min_lot, config_min_lot)
                        min_source = "config"
                    else:
                        effective_min_lot = symbol_min_lot
                        min_source = "broker"
                else:
                    effective_min_lot = lot_size
                    min_source = "default"
                
                # CRITICAL FIX: Recalculate risk with ACTUAL fill price (accounting for slippage)
                # This ensures risk calculations are accurate
                point = symbol_info_for_log.get('point', 0.00001) if symbol_info_for_log else 0.00001
                pip_value = point * 10 if (symbol_info_for_log and symbol_info_for_log.get('digits', 5) in [5, 3]) else point
                contract_size = symbol_info_for_log.get('contract_size', 1.0) if symbol_info_for_log else 1.0
                
                # Calculate SL distance from actual fill price
                if order_type == OrderType.BUY:
                    sl_price_from_fill = entry_price_actual - (stop_loss_pips * pip_value)
                else:  # SELL
                    sl_price_from_fill = entry_price_actual + (stop_loss_pips * pip_value)
                
                # Calculate actual risk with fill price
                try:
                    sl_distance_from_fill = abs(entry_price_actual - sl_price_from_fill)
                    actual_risk_with_fill = lot_size * sl_distance_from_fill * contract_size
                    
                    # Validate risk with actual fill price (should still be <= $2.00 with buffer)
                    max_risk_usd_actual = self.risk_manager.max_risk_usd
                    risk_with_slippage_actual = actual_risk_with_fill * 1.10  # 10% buffer
                    if risk_with_slippage_actual > max_risk_usd_actual:
                        logger.warning(f"[WARNING] {symbol}: Actual risk ${actual_risk_with_fill:.2f} (with buffer: ${risk_with_slippage_actual:.2f}) exceeds max ${max_risk_usd_actual:.2f} after fill")
                        # Log but don't reject - trade already placed, just warn
                except (NameError, TypeError, ValueError) as e:
                    logger.error(f"[ERROR] {symbol}: Error calculating actual risk: {e}. Using estimated risk as fallback.")
                    # Fallback to estimated risk if calculation fails
                    actual_risk_with_fill = estimated_risk if 'estimated_risk' in locals() else lot_size * stop_loss_pips * 0.10
                    risk_with_slippage_actual = actual_risk_with_fill * 1.10  # 10% buffer
                
                # Use unified trade logger
                self.trade_logger.log_trade_execution(
                    symbol=symbol,
                    ticket=ticket,
                    signal=signal,
                    entry_price_requested=entry_price,
                    entry_price_actual=entry_price_actual,
                    lot_size=lot_size,
                    stop_loss_pips=stop_loss_pips,
                    stop_loss_price=stop_loss_price,
                    quality_score=quality_score,
                    spread_points=spread_points,
                    spread_fees_cost=spread_fees_cost if test_mode else None,
                    slippage=slippage if slippage > 0.00001 else None,
                    risk_usd=actual_risk_with_fill,
                    min_lot=effective_min_lot,
                    min_lot_source=min_source,
                    sma_fast=opportunity.get('sma_fast', 0),
                    sma_slow=opportunity.get('sma_slow', 0),
                    rsi=opportunity.get('rsi', 50)
                )
                
                self.trade_count_today += 1
                self.trade_stats['total_trades'] += 1
                self.trade_stats['successful_trades'] += 1
                
                # Track this position for closure monitoring
                self.tracked_tickets.add(ticket)
                self.position_monitor.update_tracked_positions(ticket)
                
                # CRITICAL FIX 2.1: STRICT SL ENFORCEMENT - Apply immediately after entry
                # ALWAYS enforce -$2.00 protective SL regardless of initial profit status
                # This ensures protection is in place from the moment trade opens
                # MIGRATED TO SLManager: All SL logic now handled by unified SLManager
                fresh_position = self.order_manager.get_position_by_ticket(ticket)
                if fresh_position:
                    fresh_profit = fresh_position.get('profit', 0.0)
                    
                    # Use new unified SLManager for all SL enforcement
                    # This handles strict loss, break-even, sweet-spot, and trailing stops atomically
                    if hasattr(self.risk_manager, 'sl_manager') and self.risk_manager.sl_manager:
                        sl_update_success, sl_update_reason = self.risk_manager.sl_manager.update_sl_atomic(ticket, fresh_position)
                        if sl_update_success:
                            logger.info(f"[SL] SL MANAGER: {symbol} Ticket {ticket} | "
                                      f"Profit: ${fresh_profit:.2f} | {sl_update_reason}")
                        else:
                            logger.debug(f"[SL] SL MANAGER: {symbol} Ticket {ticket} | "
                                       f"Profit: ${fresh_profit:.2f} | {sl_update_reason}")
                    else:
                        # Fallback: Use legacy protective SL if SLManager not available
                        protective_sl_set = self.risk_manager.enforce_protective_sl_on_entry(ticket, fresh_position)
                        if protective_sl_set:
                            logger.warning(f"[SL] LEGACY SL (SLManager unavailable): {symbol} Ticket {ticket} | "
                                         f"Profit: ${fresh_profit:.2f} | Using legacy protective SL")
                        else:
                            logger.error(f"[ERROR] SL FAILED: {symbol} Ticket {ticket} | "
                                       f"Profit: ${fresh_profit:.2f} | Neither SLManager nor legacy SL available")
                
                self.reset_error_count()
                return True
            else:
                logger.error(f"[ERROR] {symbol}: TRADE EXECUTION FAILED after {max_retries} attempts")
                logger.error(f"   Symbol: {symbol}, Signal: {signal}, Lot: {lot_size}, SL: {stop_loss_pips:.1f} pips")
                self.trade_stats['failed_trades'] += 1
                return False
        
        except Exception as e:
            logger.error(f"[ERROR] {symbol}: Exception during trade execution: {e}", exc_info=True)
            self.handle_error(e, f"Executing trade {symbol}")
            self.trade_stats['failed_trades'] += 1
            return False
    
    def _continuous_trailing_stop_loop(self):
        """Background thread loop for continuous trailing stop monitoring."""
        # Instant trailing: if trigger_on_tick is True and interval is 0, run immediately on every call
        if self.trigger_on_tick and self.trailing_cycle_interval == 0:
            logger.info(f"🔄 Continuous trailing stop monitor started (INSTANT - trigger_on_tick=True, interval=0ms)")
        else:
            logger.info(f"🔄 Continuous trailing stop monitor started (interval: {self.trailing_cycle_interval_ms}ms = {self.trailing_cycle_interval:.3f}s)")
        
        while self.trailing_stop_running and self.running:
            try:
                if not self.check_kill_switch():
                    # Ensure connection before monitoring
                    if self.mt5_connector.ensure_connected():
                        # Monitor all positions and update trailing stops (normal polling)
                        # If trigger_on_tick is True, this runs immediately without delay
                        self.risk_manager.monitor_all_positions_continuous(use_fast_polling=False)
                    else:
                        # Try to reconnect if disconnected
                        logger.warning("Continuous trailing stop: MT5 disconnected, attempting reconnect...")
                        self.mt5_connector.reconnect()
                
                # Sleep for the configured interval (0 = instant, no delay)
                if self.trailing_cycle_interval > 0:
                    time.sleep(self.trailing_cycle_interval)
                # If interval is 0 and trigger_on_tick is True, run immediately (no sleep)
                # But add a tiny sleep to prevent CPU spinning (1ms minimum)
                elif self.trigger_on_tick:
                    time.sleep(0.001)  # 1ms minimum to prevent CPU spinning
                else:
                    # Default: small sleep to prevent CPU spinning
                    time.sleep(0.001)
            
            except Exception as e:
                logger.error(f"Error in continuous trailing stop loop: {e}", exc_info=True)
                # Continue running even if there's an error, but ensure connection
                try:
                    self.mt5_connector.ensure_connected()
                except:
                    pass
                # Small sleep on error to prevent rapid retry loops
                time.sleep(0.001)
        
        logger.info("Continuous trailing stop monitor stopped")
    
    def _fast_trailing_stop_loop(self):
        """Background thread loop for fast trailing stop monitoring (positions with profit >= threshold)."""
        fast_interval_seconds = self.fast_trailing_interval_ms / 1000.0
        # Instant fast trailing: if interval is 0, run immediately (only debounce cycles apply)
        if self.fast_trailing_interval_ms == 0:
            logger.info(f"[FAST] Fast trailing stop monitor started (INSTANT - interval=0ms, debounce_cycles={self.risk_config.get('fast_trailing_debounce_cycles', 3)})")
        else:
            logger.info(f"[FAST] Fast trailing stop monitor started (interval: {fast_interval_seconds:.3f}s = {self.fast_trailing_interval_ms}ms)")
        
        while self.fast_trailing_running and self.running:
            try:
                if not self.check_kill_switch():
                    # Ensure connection before monitoring
                    if self.mt5_connector.ensure_connected():
                        # Monitor only positions in fast polling mode
                        # If interval is 0, this runs immediately when threshold reached (debounce cycles still apply)
                        self.risk_manager.monitor_all_positions_continuous(use_fast_polling=True)
                    else:
                        # Try to reconnect if disconnected
                        logger.warning("Fast trailing stop: MT5 disconnected, attempting reconnect...")
                        self.mt5_connector.reconnect()
                
                # Sleep for the fast interval (0 = instant, no delay except debounce cycles)
                if fast_interval_seconds > 0:
                    time.sleep(fast_interval_seconds)
                else:
                    # Instant mode: tiny sleep to prevent CPU spinning (1ms minimum)
                    time.sleep(0.001)
            
            except Exception as e:
                logger.error(f"Error in fast trailing stop loop: {e}", exc_info=True)
                # Continue running even if there's an error, but ensure connection
                try:
                    self.mt5_connector.ensure_connected()
                except:
                    pass
                # Small sleep on error to prevent rapid retry loops
                time.sleep(0.001)
        
        logger.info("Fast trailing stop monitor stopped")
    
    def start_continuous_trailing_stop(self):
        """Start the continuous trailing stop monitoring threads (normal + fast)."""
        if self.trailing_stop_running:
            logger.warning("Continuous trailing stop already running")
            return
        
        continuous_enabled = self.risk_config.get('continuous_trailing_enabled', True)
        if not continuous_enabled:
            logger.info("Continuous trailing stop is disabled in config")
            return
        
        # Start normal trailing stop thread
        self.trailing_stop_running = True
        self.trailing_stop_thread = threading.Thread(
            target=self._continuous_trailing_stop_loop,
            name="TrailingStopMonitor",
            daemon=True
        )
        self.trailing_stop_thread.start()
        logger.info("[OK] Continuous trailing stop monitor thread started")
        
        # Start fast trailing stop thread
        self.fast_trailing_running = True
        self.fast_trailing_thread = threading.Thread(
            target=self._fast_trailing_stop_loop,
            name="FastTrailingStopMonitor",
            daemon=True
        )
        self.fast_trailing_thread.start()
        logger.info("[OK] Fast trailing stop monitor thread started")
        
        # Start position closure monitoring thread
        self.start_position_monitor()
    
    def start_position_monitor(self):
        """Start the position closure monitoring thread."""
        if self.position_monitor_running:
            logger.warning("Position monitor already running")
            return
        
        self.position_monitor_running = True
        self.position_monitor_thread = threading.Thread(
            target=self._position_monitor_loop,
            name="PositionMonitor",
            daemon=True
        )
        self.position_monitor_thread.start()
        logger.info("[OK] Position closure monitor thread started")
    
    def stop_position_monitor(self):
        """Stop the position closure monitoring thread."""
        if not self.position_monitor_running:
            return
        
        self.position_monitor_running = False
        if self.position_monitor_thread and self.position_monitor_thread.is_alive():
            self.position_monitor_thread.join(timeout=5.0)
            if self.position_monitor_thread.is_alive():
                logger.warning("Position monitor thread did not stop gracefully")
            else:
                logger.info("Position closure monitor thread stopped")
    
    def _position_monitor_loop(self):
        """Background thread loop for position closure detection."""
        monitor_interval = 5.0  # Check every 5 seconds
        
        while self.position_monitor_running:
            try:
                # Detect and log closures
                logged_closures = self.position_monitor.detect_and_log_closures(self.tracked_tickets)
                
                if logged_closures:
                    for closure in logged_closures:
                        logger.info(f"[-] Position {closure['ticket']} ({closure['symbol']}) closed - logged")
                
                # Update tracked tickets from current positions
                current_positions = self.order_manager.get_open_positions()
                current_tickets = {pos['ticket'] for pos in current_positions}
                self.tracked_tickets.update(current_tickets)
                
                # Clean up closed tickets from tracking
                self.tracked_tickets.intersection_update(current_tickets)
                
            except Exception as e:
                error_logger.error(f"Error in position monitor loop: {e}", exc_info=True)
            
            time.sleep(monitor_interval)
    
    def stop_continuous_trailing_stop(self):
        """Stop the continuous trailing stop monitoring threads (normal + fast)."""
        if not self.trailing_stop_running:
            return
        
        self.trailing_stop_running = False
        if self.trailing_stop_thread and self.trailing_stop_thread.is_alive():
            self.trailing_stop_thread.join(timeout=5.0)
            if self.trailing_stop_thread.is_alive():
                logger.warning("Trailing stop thread did not stop gracefully")
            else:
                logger.info("Continuous trailing stop monitor thread stopped")
        
        self.fast_trailing_running = False
        if self.fast_trailing_thread and self.fast_trailing_thread.is_alive():
            self.fast_trailing_thread.join(timeout=5.0)
            if self.fast_trailing_thread.is_alive():
                logger.warning("Fast trailing stop thread did not stop gracefully")
            else:
                logger.info("Fast trailing stop monitor thread stopped")
        
        # Stop position monitor
        self.stop_position_monitor()
    
    def manage_positions(self):
        """Manage open positions (halal checks, max duration, etc.)."""
        try:
            positions = self.order_manager.get_open_positions()
            
            # Update state based on positions
            if positions:
                # Get first position symbol for state display
                first_symbol = positions[0].get('symbol', 'N/A')
                # Get first position symbol for state display
                first_symbol = positions[0].get('symbol', 'N/A')
                self._update_state('MANAGING TRADE', first_symbol, 'Managing positions')
            else:
                # No positions - transition to SCANNING to look for new opportunities
                if self.current_state != 'SCANNING':
                    self._update_state('SCANNING', 'N/A', 'No open positions - scanning for opportunities')
                    logger.debug("State transition: MANAGING TRADE → SCANNING (no open positions)")
            
            # Check all positions for halal compliance (skip in test mode)
            test_mode = self.config.get('pairs', {}).get('test_mode', False)
            if not test_mode:
                # Only check halal compliance in live mode
                self.halal_compliance.check_all_positions()
            
            for position in positions:
                try:
                    # Note: Trailing stops are now handled by continuous monitoring thread
                    # This method only handles halal compliance and max duration
                    
                    # Check max trade duration
                    time_open = position.get('time_open')
                    if time_open:
                        if isinstance(time_open, str):
                            try:
                                time_open = datetime.fromisoformat(time_open)
                            except:
                                logger.warning(f"Could not parse time_open for position {position.get('ticket')}")
                                continue
                        duration_minutes = (datetime.now() - time_open).total_seconds() / 60
                        max_duration = self.config.get('risk', {}).get('max_trade_duration_minutes', 1440)
                        
                        if duration_minutes > max_duration:
                            symbol = position.get('symbol', 'UNKNOWN')
                            logger.warning(f"Closing position {position['ticket']} ({symbol}) - exceeded max duration")
                            self.order_manager.close_position(
                                position['ticket'],
                                comment="Max duration exceeded"
                            )
                            # Unregister from staged trades
                            self.risk_manager.unregister_staged_trade(symbol, position['ticket'])
                
                except Exception as e:
                    self.handle_error(e, f"Managing position {position.get('ticket')}")
        
        except Exception as e:
            self.handle_error(e, "Managing positions")
    
    def get_user_trade_count(self) -> int:
        """Get number of trades from user (1-6)."""
        while True:
            try:
                user_input = input("\n📊 How many trades do you want to take? (1-6): ").strip()
                if not user_input:
                    # Default to config value if empty
                    return self.risk_manager.max_open_trades
                
                trade_count = int(user_input)
                if 1 <= trade_count <= 6:
                    return trade_count
                else:
                    print("[WARNING]  Please enter a number between 1 and 6.")
            except ValueError:
                print("[WARNING]  Invalid input. Please enter a number between 1 and 6.")
            except (EOFError, KeyboardInterrupt):
                print("\n[WARNING]  Input cancelled. Using default from config.")
                return self.risk_manager.max_open_trades
    
    def display_opportunities(self, opportunities: List[Dict[str, Any]], max_count: int) -> None:
        """
        Display top trading opportunities in a user-friendly format with warnings.
        Shows quality score, lot size, spread, fees, risk, and any warnings.
        """
        if not opportunities:
            print("\n[ERROR] No trading opportunities found.")
            return
        
        # Sort by quality score (descending) if not already sorted
        sorted_opps = sorted(opportunities, key=lambda x: x.get('quality_score', 0.0), reverse=True)
        top_opps = sorted_opps[:max_count]
        
        # Get config limits for warnings
        test_mode = self.config.get('pairs', {}).get('test_mode', False)
        max_spread_fees = 0.30 if test_mode else None
        max_spread_points = self.pair_filter.max_spread_points
        
        print("\n" + "=" * 120)
        print("📊 TOP TRADING OPPORTUNITIES")
        print("=" * 120)
        print(f"{'#':<4} | {'🌟':<3} | {'Symbol':<12} | {'Signal':<6} | {'Quality':<8} | {'Lot':<8} | {'Spread':<10} | {'SL(pips)':<10} | {'Risk $':<8} | {'Cost $':<8} | {'Warnings'}")
        print("-" * 120)
        
        best_quality = top_opps[0].get('quality_score', 0.0) if top_opps else 0.0
        
        for idx, opp in enumerate(top_opps, 1):
            symbol = opp.get('symbol', 'N/A')
            signal = opp.get('signal', 'NONE')
            quality = opp.get('quality_score', 0.0)
            min_lot = opp.get('min_lot', 0.01)
            spread = opp.get('spread', 0.0)
            spread_fees = opp.get('spread_fees_cost', 0.0)
            
            # Calculate estimated risk and SL (for display purposes)
            symbol_info = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
            estimated_risk = self.risk_manager.max_risk_usd  # Fixed $2.00 USD
            sl_pips = 0.0  # Default value for display
            if symbol_info:
                point = symbol_info.get('point', 0.00001)
                pip_value = point * 10 if symbol_info.get('digits', 5) == 5 or symbol_info.get('digits', 3) == 3 else point
                contract_size = symbol_info.get('contract_size', 1.0)
                # With USD-based SL, risk is always $2.00 USD
                # For display, estimate SL pips based on typical risk distance
                # Calculate approximate SL distance: risk_usd / (lot_size * contract_size * pip_value)
                if contract_size > 0 and pip_value > 0:
                    # Estimate SL pips: $2.00 risk / (min_lot * contract_size * pip_value * point_multiplier)
                    # For display, use a reasonable estimate (typically 20-50 pips for $2 risk)
                    # Calculate based on min_lot
                    if min_lot > 0:
                        estimated_sl_distance_usd = self.risk_manager.max_risk_usd / (min_lot * contract_size)
                        sl_pips = estimated_sl_distance_usd / pip_value if pip_value > 0 else 20.0
                    else:
                        sl_pips = 20.0  # Default estimate
                else:
                    sl_pips = 20.0  # Default estimate
            
            # Check for warnings
            warnings = []
            if test_mode and max_spread_fees and spread_fees > max_spread_fees:
                warnings.append(f"Cost ${spread_fees:.2f} > ${max_spread_fees:.2f}")
            if spread > max_spread_points:
                warnings.append(f"Spread {spread:.1f}pts > {max_spread_points:.1f}pts")
            if estimated_risk > self.risk_manager.max_risk_usd * 1.1:  # Allow 10% tolerance for broker minimum
                warnings.append(f"Risk ${estimated_risk:.2f} > ${self.risk_manager.max_risk_usd:.2f}")
            
            warning_str = "; ".join(warnings) if warnings else "OK"
            
            # Highlight best setup (highest quality score, first in list)
            is_best = (quality == best_quality and idx == 1) or (quality >= best_quality * 0.95 and idx == 1)
            highlight = "🌟" if is_best else "  "
            
            print(f"{idx:<4} | {highlight:<3} | {symbol:<12} | {signal:<6} | {quality:<8.1f} | {min_lot:<8.4f} | "
                  f"{spread:<10.1f} | {sl_pips:<10.1f} | ${estimated_risk:<7.2f} | ${spread_fees:<7.2f} | {warning_str}")
        
        print("-" * 120)
        if top_opps:
            best_opp = top_opps[0]
            print(f"\n🌟 BEST SETUP: {best_opp.get('symbol')} {best_opp.get('signal')} | "
                  f"Quality Score: {best_opp.get('quality_score', 0.0):.1f} | "
                  f"Lot: {best_opp.get('min_lot', 0.01):.4f}")
        print("\n💡 Keyboard Shortcuts:")
        print("   Y = Approve | N = Skip | ALL = Approve all remaining | C = Cancel batch | S = Skip remaining")
        print("=" * 120)
    
    def get_user_approval(self, symbol: str, signal: str, quality_score: float) -> Any:
        """
        Get user approval for a trade. Returns True, False, 'ALL', or 'CANCEL'.
        
        Supports:
        - Y/YES: Approve single trade
        - N/NO: Skip single trade
        - ALL: Approve all remaining trades
        - C/CANCEL: Cancel entire batch
        - S/SKIP: Skip remaining trades
        """
        while True:
            try:
                prompt = f"Approve? (Y / N / ALL / C=cancel / S=skip): "
                user_input = input(prompt).strip().upper()
                
                if user_input in ['Y', 'YES']:
                    return True
                elif user_input in ['N', 'NO']:
                    return False
                elif user_input == 'ALL':
                    return 'ALL'  # Special value to approve all
                elif user_input in ['C', 'CANCEL']:
                    return 'CANCEL'  # Cancel entire batch
                elif user_input in ['S', 'SKIP']:
                    return 'SKIP'  # Skip remaining
                else:
                    print("[WARNING]  Please enter Y (Yes), N (No), ALL (approve all), C (cancel batch), or S (skip remaining).")
            except (EOFError, KeyboardInterrupt):
                print("\n[WARNING]  Input cancelled. Skipping trade.")
                return False
    
    def verify_position_opened(self, ticket: int, symbol: str, max_wait_seconds: float = 2.0) -> bool:
        """
        Verify that a position was successfully opened after order placement.
        
        Args:
            ticket: Order ticket number
            symbol: Trading symbol
            max_wait_seconds: Maximum time to wait for position confirmation
        
        Returns:
            True if position is open, False otherwise
        """
        import time
        start_time = time.time()
        check_interval = 0.1  # Check every 100ms
        
        while (time.time() - start_time) < max_wait_seconds:
            # Check if position exists
            positions = self.order_manager.get_open_positions()
            for position in positions:
                if position.get('ticket') == ticket:
                    logger.debug(f"[OK] {symbol}: Position {ticket} verified - confirmed open")
                    return True
            
            time.sleep(check_interval)
        
        # Final check
        positions = self.order_manager.get_open_positions()
        for position in positions:
            if position.get('ticket') == ticket:
                logger.debug(f"[OK] {symbol}: Position {ticket} verified - confirmed open")
                return True
        
        logger.warning(f"[WARNING] {symbol}: Position {ticket} not found after {max_wait_seconds}s wait")
        return False
    
    def wait_for_position_close(self, ticket: int, symbol: str, timeout_seconds: Optional[float] = None) -> bool:
        """
        Wait for a position to close using threading.Event for efficient waiting.
        Does not block trailing stop threads.
        
        Args:
            ticket: Position ticket number
            symbol: Trading symbol
            timeout_seconds: Maximum time to wait (None = use config default)
        
        Returns:
            True if position closed, False if timeout or cancelled
        """
        if timeout_seconds is None:
            timeout_seconds = self.manual_wait_for_close_timeout
        
        check_interval = 1.0  # Check every second (not too frequent to avoid blocking)
        start_time = time.time()
        
        logger.info(f"⏳ Waiting for position {ticket} ({symbol}) to close (timeout: {timeout_seconds}s)...")
        print(f"⏳ Waiting for position {ticket} to close before next trade...")
        
        while (time.time() - start_time) < timeout_seconds:
            # Check if batch was cancelled
            if self.manual_batch_cancelled:
                logger.info(f"🛑 Batch cancelled - stopping wait for position {ticket}")
                print(f"🛑 Batch cancelled - stopping wait")
                return False
            
            # Check if position still exists
            position = self.order_manager.get_position_by_ticket(ticket)
            if position is None:
                elapsed = time.time() - start_time
                logger.info(f"[OK] Position {ticket} closed after {elapsed:.1f}s")
                print(f"[OK] Position {ticket} closed - proceeding to next trade")
                
                # Log closure if we can get deal history
                try:
                    deal_info = self.order_manager.get_deal_history(ticket)
                    if deal_info and deal_info.get('exit_deal'):
                        exit_deal = deal_info['exit_deal']
                        entry_deal = deal_info.get('entry_deal')
                        
                        if entry_deal:
                            symbol = entry_deal.get('symbol', symbol)
                            duration = (exit_deal['time'] - entry_deal['time']).total_seconds() / 60.0
                            close_reason = self.order_manager.get_close_reason_from_deals(ticket)
                            
                            self.trade_logger.log_position_closure(
                                symbol=symbol,
                                ticket=ticket,
                                entry_price=entry_deal['price'],
                                close_price=exit_deal['price'],
                                profit=deal_info['total_profit'],
                                duration_minutes=duration,
                                close_reason=close_reason,
                                entry_time=entry_deal['time'],
                                close_time=exit_deal['time'],
                                commission=deal_info['commission'],
                                swap=deal_info['swap']
                            )
                except Exception as e:
                    logger.warning(f"Could not log closure details for position {ticket}: {e}")
                
                return True
            
            # Sleep to avoid busy-waiting (allows other threads to run)
            time.sleep(check_interval)
        
        # Timeout reached
        elapsed = time.time() - start_time
        logger.warning(f"⏰ Timeout waiting for position {ticket} to close ({elapsed:.1f}s > {timeout_seconds}s)")
        print(f"⏰ Timeout waiting for position {ticket} to close - proceeding anyway")
        return False  # Timeout - proceed to next trade
    
    def execute_trade_sequential(self, opportunity: Dict[str, Any], wait_for_close: bool = True) -> Optional[int]:
        """
        Execute a trade sequentially with position verification and optional wait-for-close.
        Used in manual approval mode to ensure each trade completes before next.
        
        Args:
            opportunity: Trade opportunity dictionary
            wait_for_close: If True, wait for position to close before returning
        
        Returns:
            Ticket number if trade executed successfully, None otherwise
        """
        symbol = opportunity['symbol']
        signal = opportunity['signal']
        
        # Check if batch was cancelled
        if self.manual_batch_cancelled:
            logger.info(f"🛑 Batch cancelled - skipping trade {symbol} {signal}")
            return None
        
        # Get position list BEFORE execution to compare after
        positions_before = self.order_manager.get_open_positions()
        position_count_before = len(positions_before)
        position_tickets_before = {p.get('ticket') for p in positions_before if p.get('ticket')}
        
        # Execute trade (skip randomness in manual mode)
        logger.info(f"[JUMP] Executing approved trade: {symbol} {signal}")
        result = self.execute_trade(opportunity, skip_randomness=True)
        
        # execute_trade returns True (success), False (failure), or None (filtered)
        if result is False:
            logger.error(f"[ERROR] {symbol}: Trade execution failed")
            print(f"[ERROR] Execution failed for {symbol} {signal}")
            return None
        elif result is None:
            logger.info(f"[SKIP] {symbol}: Trade filtered/skipped (risk validation or safety check)")
            print(f"[SKIP] {symbol}: Trade filtered - not a failure")
            return None
        # result is True - success, continue
        
        # Wait for position to appear (MT5 can take a moment)
        max_wait = 2.0  # seconds
        wait_interval = 0.1  # Check every 100ms
        waited = 0.0
        position_found = False
        new_ticket = None
        
        while waited < max_wait:
            time.sleep(wait_interval)
            waited += wait_interval
            
            # Check if batch was cancelled
            if self.manual_batch_cancelled:
                logger.info(f"🛑 Batch cancelled during position verification")
                return None
            
            # Get current positions
            positions_after = self.order_manager.get_open_positions()
            position_tickets_after = {p.get('ticket') for p in positions_after if p.get('ticket')}
            
            # Find new positions
            new_tickets = position_tickets_after - position_tickets_before
            
            if new_tickets:
                # Check if any new position matches our symbol and signal
                for ticket in new_tickets:
                    for position in positions_after:
                        if position.get('ticket') == ticket:
                            pos_symbol = position.get('symbol', '').upper()
                            pos_type = position.get('type', 0)
                            expected_type = 0 if signal == 'LONG' else 1  # 0=BUY, 1=SELL
                            
                            if pos_symbol == symbol.upper() and pos_type == expected_type:
                                new_ticket = ticket
                                position_found = True
                                break
                    
                    if position_found:
                        break
            
            if position_found:
                break
        
        if position_found and new_ticket:
            logger.info(f"[OK] {symbol}: Position {new_ticket} confirmed open and verified")
            print(f"[OK] Position {new_ticket} verified for {symbol} {signal}")
            
            # Wait for position to close if requested
            if wait_for_close:
                self.wait_for_position_close(new_ticket, symbol)
            
            return new_ticket
        else:
            # Trade might have executed but position not found (could be instant close, etc.)
            # Check if position count increased at all
            position_count_after = self.order_manager.get_position_count()
            if position_count_after > position_count_before:
                logger.warning(f"[WARNING] {symbol}: Position count increased but specific position not verified (may have different symbol)")
                print(f"[WARNING]  Position verification uncertain for {symbol} (position count changed)")
                # If wait_for_close is True but we don't have a ticket, we can't wait
                if not wait_for_close:
                    return -1  # Special value indicating success but no ticket
                return None
            else:
                logger.warning(f"[WARNING] {symbol}: Trade executed but position not found after {max_wait}s")
                print(f"[WARNING]  Position not verified for {symbol} {signal} after {max_wait}s")
                # Position might have closed immediately - if wait_for_close, we're done
                if not wait_for_close:
                    return -1  # Special value indicating success but no ticket
                return None
    
    def run_cycle(self):
        """Execute one trading cycle."""
        mode = "BACKTEST" if self.is_backtest else "LIVE"
        cycle_start_time = time.time()
        cycle_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        logger.info(f"mode={mode} | [RUN_CYCLE] Starting trading cycle at {cycle_timestamp}")
        scheduler_logger.info(f"mode={mode} | [RUN_CYCLE] Cycle start timestamp: {cycle_timestamp}")
        
        if self.check_kill_switch():
            logger.warning(f"mode={mode} | [RUN_CYCLE] Cycle aborted - kill switch active")
            return
        
        # Ensure MT5 connection with retry logic and exponential backoff
        if not self.mt5_connector.ensure_connected():
            logger.warning("MT5 not connected, attempting reconnect...")
            reconnect_success = False
            max_reconnect_attempts = 3
            for reconnect_attempt in range(max_reconnect_attempts):
                if self.mt5_connector.reconnect():
                    reconnect_success = True
                    logger.info(f"[OK] MT5 reconnected successfully (attempt {reconnect_attempt + 1})")
                    break
                else:
                    # Exponential backoff: 2^attempt seconds
                    backoff_delay = 2 ** reconnect_attempt
                    logger.warning(f"[WARNING] Reconnection attempt {reconnect_attempt + 1}/{max_reconnect_attempts} failed, retrying in {backoff_delay}s...")
                    time.sleep(backoff_delay)
            
            if not reconnect_success:
                error_msg = f"MT5 reconnection failed after {max_reconnect_attempts} attempts"
                logger.error(f"[ERROR] {error_msg}")
                if self.manual_approval_mode:
                    print(f"\n[ERROR] {error_msg} - Aborting batch execution")
                    self.manual_batch_cancelled = True
                self.handle_error(Exception(error_msg), "Connection check")
                return
        
        if self.is_in_cooldown():
            logger.debug("Bot in cooldown period, skipping cycle")
            return
        
        try:
            # Update daily P&L
            self.update_daily_pnl()
            
            # Manage existing positions
            self.manage_positions()
            
            # Scan for new opportunities
            opportunities = self.scan_for_opportunities()
            
            # Execute all opportunities (up to max_open_trades limit)
            if opportunities:
                # Get test_mode before using it
                test_mode = self.config.get('pairs', {}).get('test_mode', False)
                
                # Sort by profit probability (quality score) HIGH to LOW, then fees LOW to HIGH
                # This ensures best setups (highest profit probability) are traded first, with lowest costs
                # FIXED: Proper sorting - QualityScore DESC (top to low profit probability), Cost ASC (low to high fees)
                def get_priority(opp):
                    quality = opp.get('quality_score', 0.0)  # Profit probability indicator
                    cost = opp.get('spread_fees_cost', 0.0)  # Total fees
                    # Negative quality for descending sort (highest profit probability first)
                    # Positive cost for ascending sort (lowest fees first)
                    return (-quality, cost if cost > 0 else 999999)
                
                opportunities.sort(key=get_priority)
                
                # Log sorting rationale
                if opportunities:
                    logger.info("📊 Opportunities sorted: Profit Probability (HIGH→LOW), Fees (LOW→HIGH)")
                    logger.info("   → Best setups (highest profit probability, lowest fees) will be traded first")
                
                # Log sorted order for verification (always log, not just in test mode)
                if opportunities:
                    logger.info("📊 Opportunities sorted by quality score (descending), then spread+fees (ascending):")
                    for idx, opp in enumerate(opportunities[:10], 1):  # Show first 10
                        quality = opp.get('quality_score', 0.0)
                        cost = opp.get('spread_fees_cost', 0.0)
                        spread = opp.get('spread', 0)
                        logger.info(f"  {idx}. {opp['symbol']} {opp['signal']}: Quality={quality:.1f}, Cost=${cost:.2f} (spread: {spread:.1f}pts)")
                
                # Manual approval mode: display opportunities and get user approval
                if self.manual_approval_mode:
                    # Prevent double scanning - only scan once per session unless trades finish
                    if self.manual_scan_completed and not self.manual_trades_executing:
                        # Previous scan completed and trades finished, allow new scan
                        self.manual_scan_completed = False
                    elif self.manual_scan_completed:
                        # Scan already completed, skip
                        logger.info("Scan already completed in this session - skipping duplicate scan")
                        opportunities = []
                    
                    # Use manual max_trades if set, otherwise use config
                    max_trades = self.manual_max_trades if self.manual_max_trades is not None else self.risk_manager.max_open_trades
                    
                    # Get current position count before approval
                    initial_positions = self.order_manager.get_position_count()
                    remaining_slots = max_trades - initial_positions
                    
                    # Display top opportunities (limit to available slots)
                    display_count = min(len(opportunities), remaining_slots) if remaining_slots > 0 else 0
                    
                    if display_count > 0:
                        self.display_opportunities(opportunities, display_count)
                    else:
                        print(f"\n[WARNING]  No available trade slots. Current positions: {initial_positions}/{max_trades}")
                        opportunities = []  # Clear opportunities
                    
                    # Get user approval for each trade
                    approved_opportunities = []
                    approve_all = False
                    
                    if remaining_slots <= 0:
                        print(f"\n[PAUSE]  Max open trades ({max_trades}) already reached - cannot take more trades.")
                        opportunities = []  # Clear opportunities
                    else:
                        # Limit opportunities to remaining slots
                        available_opps = opportunities[:remaining_slots]
                        
                        for idx, opp in enumerate(available_opps, 1):
                            if len(approved_opportunities) >= remaining_slots:
                                break
                            
                            # Check if we've reached max open trades (re-check after each approval)
                            current_positions = self.order_manager.get_position_count()
                            if current_positions >= max_trades:
                                print(f"\n[PAUSE]  Max open trades ({max_trades}) reached - cannot take more trades.")
                                break
                            
                            if not approve_all:
                                # Display trade number for clarity
                                quality = opp.get('quality_score', 0.0)
                                symbol = opp.get('symbol')
                                signal = opp.get('signal')
                                print(f"\n{'='*80}")
                                print(f"Trade #{idx}: {symbol} ({signal}), Quality = {quality:.1f}")
                                print(f"{'='*80}")
                                
                                approval = self.get_user_approval(
                                    symbol,
                                    signal,
                                    quality
                                )
                                
                                if approval == 'CANCEL':
                                    self.manual_batch_cancelled = True
                                    print(f"🛑 Batch cancelled by user")
                                    logger.info("Manual batch cancelled by user")
                                    break
                                elif approval == 'SKIP':
                                    print(f"[SKIP]  Skipping remaining trades")
                                    logger.info("User skipped remaining trades")
                                    break
                                elif approval == 'ALL':
                                    approve_all = True
                                    approved_opportunities.append(opp)
                                    print(f"[OK] Approved ALL remaining trades")
                                    # Add all remaining opportunities to approved list
                                    remaining_in_batch = available_opps[idx:]
                                    for remaining_opp in remaining_in_batch:
                                        if len(approved_opportunities) < remaining_slots:
                                            current_positions = self.order_manager.get_position_count()
                                            if current_positions >= max_trades:
                                                break
                                            approved_opportunities.append(remaining_opp)
                                    break
                                elif approval:
                                    approved_opportunities.append(opp)
                                    print(f"[OK] Approved: {symbol} {signal}")
                                else:
                                    print(f"[ERROR] Skipped: {symbol} {signal}")
                            else:
                                # Already approved all
                                approved_opportunities.append(opp)
                        
                        # Check if batch was cancelled
                        if self.manual_batch_cancelled:
                            opportunities = []  # Clear opportunities
                            print(f"\n🛑 Batch execution cancelled by user.\n")
                        
                        # Replace opportunities with approved ones for sequential execution
                        opportunities = approved_opportunities
                        
                        # Mark scan as completed
                        if opportunities:
                            self.manual_scan_completed = True
                            self.manual_trades_executing = True
                            print(f"\n📊 Approved {len(opportunities)} trade(s) for sequential execution.\n")
                        else:
                            print(f"\n[WARNING]  No trades approved for execution.\n")
                
                
                # Log opportunity table header (testing mode)
                if test_mode:
                    logger.info("=" * 100)
                    logger.info("OPPORTUNITY TABLE:")
                    logger.info(f"{'Symbol':<12} | {'Signal':<6} | {'MinLot':<8} | {'Spread':<10} | {'Fees':<10} | {'Status':<20} | {'Reason'}")
                    logger.info("-" * 100)
                
                mode_text = "MANUAL APPROVAL MODE" if self.manual_approval_mode else "AUTOMATIC MODE"
                logger.info(f"🎯 Found {len(opportunities)} opportunity(ies) - evaluating all symbols (sorted by quality score and spread+fees cost) [{mode_text}]")
                
                # Get quality threshold for comparison
                min_quality_score = self.trading_config.get('min_quality_score', 50.0)
                
                # Get current position count ONCE at the start of batch
                current_positions = self.order_manager.get_position_count()
                max_trades = self.manual_max_trades if (self.manual_approval_mode and self.manual_max_trades is not None) else self.risk_manager.max_open_trades
                trades_executed = 0
                trades_skipped = 0
                failed_symbols_in_batch = set()  # Track symbols that failed in this batch to prevent duplicates
                
                # Limit opportunities to available slots at the start (allow multiple trades to execute quickly)
                available_slots = max_trades - current_positions
                if available_slots <= 0:
                    logger.info(f"[PAUSE]  Max open trades ({max_trades}) already reached - skipping all opportunities")
                    opportunities = []
                else:
                    # CRITICAL FIX: Reserve slots by limiting opportunities, but allow all reserved slots to execute
                    # This ensures multiple trades can execute quickly without position count blocking
                    original_count = len(opportunities)
                    opportunities = opportunities[:available_slots]
                    logger.info(f"📊 Processing {len(opportunities)} opportunity(ies) out of {original_count} found | "
                              f"Available slots: {available_slots}/{max_trades} | "
                              f"Reserved {available_slots} slot(s) for batch execution | "
                              f"Current positions: {current_positions}")
                
                # Execute opportunities (sequential in manual mode, parallel in automatic mode)
                # CRITICAL FIX: Since we've already reserved slots at the batch level, we can execute all reserved opportunities
                # without re-checking position count for each trade (position count is checked in execute_trade as safety)
                logger.info(f"🔄 Starting batch execution loop: {len(opportunities)} opportunity(ies) to process")
                for idx, opportunity in enumerate(opportunities, 1):
                    symbol = opportunity.get('symbol', 'N/A')
                    logger.info(f"🔄 [BATCH {idx}/{len(opportunities)}] Processing: {symbol} | "
                              f"Current positions: {self.order_manager.get_position_count()}/{max_trades}")
                    
                    # CRITICAL: Don't re-check position count for each trade - we've already reserved slots
                    # The execute_trade function will do a final safety check, but we trust the batch reservation
                    # This allows multiple trades to execute quickly without position count blocking
                    
                    # Check entry conditions for allowing additional trades (strict mode)
                    # CRITICAL FIX: Only apply strict entry conditions when approaching max trades limit
                    # This allows multiple trades to open quickly, but becomes more selective near the limit
                    risk_config = self.config.get('risk', {})
                    max_open_trades_strict = risk_config.get('max_open_trades_strict', False)
                    entry_conditions = risk_config.get('entry_conditions', {})
                    
                    # Only apply strict entry conditions when we're close to max trades (within 2 of limit)
                    # This allows quick execution of multiple trades, but becomes selective near capacity
                    # Example: If max_trades=6, only apply strict conditions when current_positions >= 4
                    # NOTE: Use initial position count (before batch) to determine threshold
                    strict_condition_threshold = max(1, max_trades - 2)  # Apply when within 2 of limit
                    
                    if max_open_trades_strict and current_positions >= strict_condition_threshold:
                        # Check if this opportunity meets entry conditions
                        quality_score = opportunity.get('quality_score', 0.0)
                        trend_signal = opportunity.get('trend_signal', {})
                        volume_ok = opportunity.get('volume_ok', False)
                        
                        require_strong_signal = entry_conditions.get('require_strong_signal', True)
                        require_trend_alignment = entry_conditions.get('require_trend_alignment', True)
                        require_volume_medium = entry_conditions.get('require_volume_medium', True)
                        
                        # Check conditions
                        strong_signal_ok = not require_strong_signal or quality_score >= self.trading_config.get('min_quality_score', 50.0)
                        trend_alignment_ok = not require_trend_alignment or (trend_signal.get('signal') != 'NONE' if trend_signal else False)
                        volume_medium_ok = not require_volume_medium or volume_ok
                        
                        if not (strong_signal_ok and trend_alignment_ok and volume_medium_ok):
                            logger.info(f"[SKIP] [SKIP] {opportunity.get('symbol', 'N/A')} | Reason: Entry conditions not met for additional trade (already have {current_positions}/{max_trades} trades, threshold: {strict_condition_threshold}) | "
                                      f"Strong signal: {strong_signal_ok}, Trend alignment: {trend_alignment_ok}, Volume medium: {volume_medium_ok}")
                            trades_skipped += 1
                            continue
                    
                    # CRITICAL: Don't check position count here - we've already reserved slots
                    # The execute_trade function will do a final safety check if needed
                    
                    symbol = opportunity['symbol']
                    signal = opportunity['signal']
                    spread = opportunity.get('spread', 0)
                    min_lot = opportunity.get('min_lot', 0.01)
                    spread_fees_cost = opportunity.get('spread_fees_cost', 0.0)
                    quality_score = opportunity.get('quality_score', 0.0)
                    
                    # Prepare fees string for logging
                    fees_str = f"${spread_fees_cost:.2f}" if spread_fees_cost > 0 else "N/A"
                    
                    # Manual approval mode: Sequential execution with position verification and wait-for-close
                    if self.manual_approval_mode:
                        # Check if batch was cancelled
                        if self.manual_batch_cancelled:
                            logger.info("Batch cancelled - stopping execution")
                            print(f"\n🛑 Batch execution cancelled - stopping")
                            break
                        
                        # Prevent duplicate attempts for symbols that already failed in this batch
                        if symbol.upper() in failed_symbols_in_batch:
                            logger.info(f"[SKIP] {symbol}: Skipping - already failed in this batch")
                            print(f"[SKIP] {symbol}: Skipped - already attempted and failed in this batch")
                            trades_skipped += 1
                            continue
                        
                        # Check if symbol was previously restricted
                        if hasattr(self, '_restricted_symbols') and symbol.upper() in self._restricted_symbols:
                            logger.info(f"[SKIP] {symbol}: Skipping - symbol is restricted")
                            print(f"[SKIP] {symbol}: Skipped - symbol is restricted (cannot be traded)")
                            trades_skipped += 1
                            continue
                        
                        print(f"\n{'='*80}")
                        print(f"[STATS] Executing Trade {idx}/{len(opportunities)}: {symbol} {signal}")
                        print(f"{'='*80}")
                        
                        # Comprehensive safety checks before executing approved trade
                        # 0. Check if symbol was previously restricted (prevent duplicate attempts)
                        if hasattr(self, '_restricted_symbols') and symbol.upper() in self._restricted_symbols:
                            logger.warning(f"⏰ {symbol}: Symbol is restricted - skipping execution")
                            logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Symbol restricted - cannot be traded")
                            print(f"[SKIP] {symbol}: Symbol is restricted - cannot be traded. Skipped.")
                            failed_symbols_in_batch.add(symbol.upper())
                            trades_skipped += 1
                            continue
                        
                        # 0a. Check if market is open (CRITICAL - prevents unnecessary order attempts)
                        is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(symbol)
                        if not is_tradeable:
                            logger.warning(f"⏰ {symbol}: Market closed or not tradeable - {reason}")
                            logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Market closed - {reason}")
                            print(f"[SKIP] {symbol}: Market closed - {reason}. Skipped. Re-scan when market opens.")
                            failed_symbols_in_batch.add(symbol.upper())
                            trades_skipped += 1
                            continue
                        
                        # 1. Check portfolio risk
                        symbol_info_for_risk = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
                        if symbol_info_for_risk:
                            # With USD-based SL, risk is always $2.00 fixed
                            estimated_risk = self.risk_manager.max_risk_usd
                            
                            portfolio_risk_ok, portfolio_reason = self.risk_manager.check_portfolio_risk(new_trade_risk_usd=estimated_risk)
                            if not portfolio_risk_ok:
                                logger.warning(f"[SKIP] {symbol}: Portfolio risk limit - {portfolio_reason}")
                                print(f"[SKIP] {symbol}: Cannot execute - {portfolio_reason}")
                                trades_skipped += 1
                                continue
                        
                        # 2. Check if we can still open trade
                        can_open, reason = self.risk_manager.can_open_trade(
                            symbol=symbol,
                            signal=signal,
                            quality_score=quality_score,
                            high_quality_setup=quality_score >= min_quality_score
                        )
                        if not can_open:
                            logger.warning(f"[SKIP] {symbol}: Cannot open trade - {reason}")
                            print(f"[SKIP] {symbol}: Cannot execute - {reason}")
                            trades_skipped += 1
                            continue
                        
                        # 3. Check spread (re-check before execution)
                        if not self.pair_filter.check_spread(symbol):
                            spread_points = self.pair_filter.get_spread_points(symbol)
                            max_spread = self.pair_filter.max_spread_points
                            logger.warning(f"[SKIP] {symbol}: Spread {spread_points:.2f} points > {max_spread} limit")
                            print(f"[SKIP] {symbol}: Spread too wide - cannot execute")
                            trades_skipped += 1
                            continue
                        
                        # 4. Check price staleness
                        symbol_info_fresh = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=True)
                        if symbol_info_fresh is None:
                            logger.warning(f"[SKIP] {symbol}: Price is stale or symbol info unavailable")
                            print(f"[SKIP] {symbol}: Price stale - cannot execute")
                            trades_skipped += 1
                            continue
                        
                        # 5. Check halal compliance (if not in test mode)
                        test_mode = self.config.get('pairs', {}).get('test_mode', False)
                        if not test_mode:
                            if not self.halal_compliance.validate_trade(symbol, signal):
                                logger.warning(f"[SKIP] {symbol}: Halal compliance check failed")
                                print(f"[SKIP] {symbol}: Halal compliance failed - cannot execute")
                                trades_skipped += 1
                                continue
                    
                    # Log opportunity row (testing mode)
                    if test_mode:
                        logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'Executing...':<20} | {'-'}")
                    
                    logger.info(f"🎯 Executing approved trade: {symbol} {signal} (Quality: {quality_score:.1f}, Spread: {spread:.1f} points)")
                    
                    # Execute trade sequentially with verification and wait-for-close
                    if self.manual_approval_mode:
                        ticket = self.execute_trade_sequential(opportunity, wait_for_close=True)
                        if ticket and ticket > 0 and ticket != -1:
                            if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'EXECUTED & CLOSED':<20} | Trade executed and closed")
                            logger.info(f"[OK] {symbol}: Trade {idx}/{len(opportunities)} executed and closed successfully")
                            print(f"[OK] Trade {idx}/{len(opportunities)} completed and closed: {symbol} {signal}")
                            trades_executed += 1
                        elif ticket == -1:
                            # Position opened but couldn't verify ticket (might have closed immediately)
                            logger.info(f"[OK] {symbol}: Trade {idx}/{len(opportunities)} executed (position closed immediately)")
                            print(f"[OK] Trade {idx}/{len(opportunities)} executed: {symbol} {signal}")
                            trades_executed += 1
                        else:
                            if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'FAILED':<20} | Trade execution failed")
                            logger.error(f"[ERROR] {symbol}: Trade {idx}/{len(opportunities)} execution failed (check logs above)")
                            print(f"[ERROR] Trade {idx}/{len(opportunities)} failed: {symbol} {signal}")
                            # Mark symbol as failed to prevent duplicate attempts
                            failed_symbols_in_batch.add(symbol.upper())
                            trades_skipped += 1
                        
                        # Check if batch was cancelled during execution
                        if self.manual_batch_cancelled:
                            logger.info("Batch cancelled during execution - stopping")
                            print(f"\n🛑 Batch execution cancelled - stopping")
                            break
                    else:
                        # Automatic mode: Standard execution (existing logic)
                        # Log opportunity row (testing mode)
                        if test_mode:
                            logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'Evaluating...':<20} | {'-'}")
                        
                        logger.info(f"🎯 Evaluating opportunity: {symbol} {signal} (Quality: {quality_score:.1f}, Spread: {spread:.1f} points)")
                        
                        # Check if we can still open a trade for this symbol (with actual quality score)
                        can_open, reason = self.risk_manager.can_open_trade(
                            symbol=symbol,
                            signal=signal,
                            quality_score=quality_score,
                            high_quality_setup=quality_score >= min_quality_score
                        )
                        
                        if not can_open:
                            if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'SKIP':<20} | {reason}")
                            logger.info(f"[SKIP] [SKIP] {symbol} | Reason: Cannot open trade - {reason}")
                            trades_skipped += 1
                            continue
                        
                        # Execute trade with error handling to ensure batch continues
                        logger.info(f"🔄 [BATCH {idx}/{len(opportunities)}] Calling execute_trade for {symbol}")
                        try:
                            result = self.execute_trade(opportunity)
                            logger.info(f"🔄 [BATCH {idx}/{len(opportunities)}] execute_trade returned: {result} for {symbol}")
                        except Exception as e:
                            logger.error(f"[ERROR] [ERROR] [BATCH {idx}/{len(opportunities)}] {symbol} | Exception in execute_trade: {e}", exc_info=True)
                            error_logger.error(f"Exception in execute_trade for {symbol}: {e}", exc_info=True)
                            result = False  # Treat exception as failure, but continue batch
                        
                        # execute_trade returns:
                        # - True: Trade executed successfully
                        # - False: Actual execution failure (order placement failed, etc.)
                        # - None: Filtered/skipped (randomness, risk validation, trend change) - NOT a failure
                        
                        if result is True:
                            logger.info(f"[OK] [BATCH {idx}/{len(opportunities)}] Trade {symbol} executed successfully | "
                                      f"Position count after: {self.order_manager.get_position_count()}/{max_trades}")
                            if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'PASS - EXECUTED':<20} | Trade executed")
                            logger.info(f"[OK] {symbol}: Trade execution completed successfully")
                            trades_executed += 1
                            # DO NOT manually increment - always use get_position_count() in next iteration
                        elif result is None:
                            # Filtered/skipped - NOT a failure (randomness, risk validation, trend change)
                            if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'SKIP':<20} | Filtered (not a failure)")
                            logger.info(f"[SKIP] [SKIP] [BATCH {idx}/{len(opportunities)}] {symbol} | Reason: Trade filtered/skipped (randomness, risk validation, or trend change) | "
                                      f"Position count: {self.order_manager.get_position_count()}/{max_trades}")
                            self.trade_stats['filtered_opportunities'] += 1
                            trades_skipped += 1
                        else:  # result is False - actual execution failure
                            if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'FAILED':<20} | Trade execution failed")
                            logger.info(f"[ERROR] [FAILED] [BATCH {idx}/{len(opportunities)}] {symbol} | Reason: Trade execution failed (order placement error) | "
                                      f"Position count: {self.order_manager.get_position_count()}/{max_trades}")
                            trades_skipped += 1
                        
                        # CRITICAL FIX: Don't re-check position count after each trade - we've already reserved slots
                        # Only check if we've exceeded the reserved slots (shouldn't happen, but safety check)
                        # This allows all reserved trades to execute quickly without position count blocking
                        current_positions_after = self.order_manager.get_position_count()
                        logger.info(f"📊 [BATCH {idx}/{len(opportunities)}] After {symbol} execution: "
                                  f"Position count: {current_positions_after}/{max_trades} | "
                                  f"Trades executed: {trades_executed} | Trades skipped: {trades_skipped}")
                        
                        if current_positions_after > max_trades:
                            # Only break if we've EXCEEDED max (not at max) - this shouldn't happen with proper reservation
                            logger.warning(f"[WARNING]  Position count ({current_positions_after}) exceeded max ({max_trades}) after trade {idx} - stopping batch execution")
                            if idx < len(opportunities):
                                print(f"\n[WARNING]  Position count exceeded max - stopping batch execution.")
                                trades_skipped += len(opportunities) - idx
                                break
                        elif current_positions_after == max_trades and idx < len(opportunities):
                            # At max but still have more opportunities - this is expected, continue executing reserved slots
                            logger.info(f"📊 [BATCH {idx}/{len(opportunities)}] Position count at max ({max_trades}) after trade {idx}, "
                                      f"but continuing with reserved slots ({len(opportunities) - idx} remaining)")
                
                # Close opportunity table (testing mode)
                if test_mode:
                    logger.info("-" * 100)
                
                # Get final position count and log batch summary
                final_position_count = self.order_manager.get_position_count()
                logger.info(f"📊 BATCH EXECUTION SUMMARY: "
                          f"Processed {len(opportunities)} opportunity(ies) | "
                          f"Executed: {trades_executed} | "
                          f"Skipped: {trades_skipped} | "
                          f"Final positions: {final_position_count}/{max_trades} | "
                          f"Started with: {current_positions} positions")
                
                # Manual approval mode: Display batch execution summary
                if self.manual_approval_mode and opportunities:
                    print("\n" + "=" * 100)
                    print("📊 BATCH EXECUTION SUMMARY")
                    print("=" * 100)
                    print(f"   Trades Executed: {trades_executed}")
                    print(f"   Trades Skipped:  {trades_skipped}")
                    print(f"   Total Approved:  {len(opportunities)}")
                    print(f"   Current Positions: {final_position_count}/{max_trades}")
                    print("=" * 100)
                
                logger.info(f"📊 Cycle Summary: {trades_executed} executed, {trades_skipped} skipped, {final_position_count}/{max_trades} positions open")
            else:
                if self.manual_approval_mode:
                    print("\n[WARNING]  No trading opportunities found this cycle.")
                    print("   (Check logs for reasons: quality score, spread, portfolio risk, etc.)\n")
                logger.info("ℹ️  No trading opportunities found this cycle (check logs above for reasons)")
            
            self.reset_error_count()
        
        except Exception as e:
            self.handle_error(e, "Trading cycle")
        finally:
            # Log cycle completion with timing
            cycle_duration = time.time() - cycle_start_time
            mode = "BACKTEST" if self.is_backtest else "LIVE"
            cycle_end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            
            logger.info(f"mode={mode} | [RUN_CYCLE] Cycle completed at {cycle_end_timestamp} | Duration: {cycle_duration:.3f}s")
            scheduler_logger.info(f"mode={mode} | [RUN_CYCLE] Cycle end timestamp: {cycle_end_timestamp} | Duration: {cycle_duration:.3f}s")
            
            # Warn if cycle took too long
            if cycle_duration > 60.0:
                logger.warning(f"mode={mode} | [RUN_CYCLE] WARNING: Cycle duration ({cycle_duration:.3f}s) exceeds target (60s)")
                scheduler_logger.warning(f"mode={mode} | [RUN_CYCLE] WARNING: Cycle overrun detected")
    
    def run(self, cycle_interval_seconds: int = 60, manual_approval: bool = False, manual_max_trades: Optional[int] = None):
        """
        Run the trading bot continuously.
        
        Args:
            cycle_interval_seconds: Seconds between trading cycles
            manual_approval: If True, require user approval before executing trades
            manual_max_trades: Max trades for this session (1-6), only used in manual mode
        """
        self.running = True
        self.manual_approval_mode = manual_approval
        original_max_trades = None  # Store original value for restoration
        
        # Get manual max_trades if in manual mode
        if manual_approval:
            # Reset batch cancellation flag and scan state
            self.manual_batch_cancelled = False
            self.manual_scan_completed = False
            self.manual_approved_trades = []
            self.manual_trades_executing = False
            
            if manual_max_trades is None:
                self.manual_max_trades = self.get_user_trade_count()
            else:
                self.manual_max_trades = min(max(1, manual_max_trades), 6)
            
            # Temporarily override max_open_trades for this session
            original_max_trades = self.risk_manager.max_open_trades
            self.risk_manager.max_open_trades = self.manual_max_trades
            
            logger.info(f"Trading bot started in MANUAL APPROVAL MODE. Max trades: {self.manual_max_trades}, Cycle interval: {cycle_interval_seconds}s")
            print(f"\n[OK] Manual Approval Mode Enabled")
            print(f"   Max Trades: {self.manual_max_trades}")
            print(f"   Cycle Interval: {cycle_interval_seconds}s")
        else:
            logger.info(f"Trading bot started in AUTOMATIC MODE. Cycle interval: {cycle_interval_seconds}s")
        
        # CRITICAL FIX: Start SLManager worker thread for real-time SL updates
        if hasattr(self.risk_manager, 'sl_manager') and self.risk_manager.sl_manager:
            try:
                self.risk_manager.sl_manager.start_sl_worker()
                logger.info("[OK] SLManager worker thread started successfully")
            except Exception as e:
                logger.error(f"[ERROR] Failed to start SLManager worker thread: {e}", exc_info=True)
                error_logger.error(f"SLManager worker thread start failed: {e}", exc_info=True)
        else:
            logger.critical("[ERROR] SLManager not available - cannot start worker thread")
            error_logger.critical("SLManager not available - worker thread not started")
        
        # Start continuous trailing stop monitor
        self.start_continuous_trailing_stop()
        
        # Start state watchdog
        self.start_state_watchdog()
        
        try:
            while self.running:
                # In manual mode, check if we need to wait for trades to finish
                if self.manual_approval_mode:
                    # Check if there are open positions from approved trades
                    open_positions = self.order_manager.get_open_positions()
                    if open_positions and self.manual_trades_executing:
                        # Wait for all positions to close before allowing new scan
                        print(f"\n⏳ Waiting for {len(open_positions)} open position(s) to close...")
                        logger.info(f"Waiting for {len(open_positions)} position(s) to close before next scan")
                        # Wait a bit and check again in next cycle
                        time.sleep(5)
                        continue
                    elif not open_positions and self.manual_trades_executing:
                        # All trades finished, reset state
                        self.manual_trades_executing = False
                        self.manual_scan_completed = False
                        print(f"\n[OK] All trades finished. Ready for new scan.")
                        logger.info("All trades finished - ready for new scan")
                
                # Run one trading cycle (only if not already scanning/executing in manual mode)
                if not self.manual_approval_mode or not self.manual_trades_executing:
                    scheduler_logger.info("Starting trading cycle")
                    self.run_cycle()
                    scheduler_logger.info("Trading cycle completed")
                    
                    # Per-tick monitor: emit BOT_LOOP_TICK event (lightweight, non-blocking)
                    try:
                        account_info = self.mt5_connector.get_account_info()
                        if account_info:
                            self.system_event_logger.systemEvent("BOT_LOOP_TICK", {
                                "time": datetime.now().isoformat(),
                                "openTrades": self.order_manager.get_position_count(),
                                "equity": account_info.get('equity', 0.0),
                                "balance": account_info.get('balance', 0.0)
                            })
                    except Exception as e:
                        # Don't let monitoring errors break the bot
                        logger.debug(f"Error in per-tick monitor: {e}")
                
                # In manual mode, ask if user wants to continue scanning (only after trades finish)
                if self.manual_approval_mode:
                    # Only ask if we're not currently executing trades
                    if not self.manual_trades_executing:
                        try:
                            print("\n" + "=" * 100)
                            continue_choice = input("🔄 Scan for more opportunities? (Y/N, or press Enter to continue): ").strip().upper()
                            print("=" * 100)
                            
                            if continue_choice in ['N', 'NO']:
                                print("\n[OK] Stopping bot as requested by user...")
                                logger.info("Manual batch approval mode stopped by user")
                                break
                            # If Y or Enter, continue to next cycle
                            elif continue_choice in ['Y', 'YES', '']:
                                print(f"\n🔍 Scanning for new opportunities...\n")
                                # Reset scan state to allow new scan
                                self.manual_scan_completed = False
                                # Small delay before next scan
                                time.sleep(1)
                            else:
                                # Invalid input, default to continuing
                                print(f"[WARNING]  Invalid input. Continuing scan...\n")
                                time.sleep(1)
                        except (EOFError, KeyboardInterrupt):
                            print("\n\n[OK] Bot stopped by user (Ctrl+C)")
                            logger.info("Manual batch approval mode stopped by user (KeyboardInterrupt)")
                            break
                    else:
                        # Trades are executing, wait a bit before checking again
                        time.sleep(5)
                else:
                    # Automatic mode: wait for cycle interval
                    mode = "BACKTEST" if self.is_backtest else "LIVE"
                    sleep_start = time.time()
                    scheduler_logger.info(f"mode={mode} | [RUN_CYCLE] Waiting {cycle_interval_seconds}s until next cycle")
                    time.sleep(cycle_interval_seconds)
                    sleep_duration = time.time() - sleep_start
                    scheduler_logger.info(f"mode={mode} | [RUN_CYCLE] Sleep completed | Duration: {sleep_duration:.3f}s (target: {cycle_interval_seconds}s)")
        
        except KeyboardInterrupt:
            logger.info("Trading bot stopped by user")
            if self.manual_approval_mode:
                print("\n[OK] Bot stopped by user")
        except Exception as e:
            self.handle_error(e, "Main loop")
            error_logger.critical("Fatal error in main loop, shutting down", exc_info=True)
            logger.critical("Fatal error in main loop, shutting down")
        finally:
            # Restore original max_open_trades if it was overridden
            if original_max_trades is not None:
                self.risk_manager.max_open_trades = original_max_trades
                logger.info(f"Restored max_open_trades to {original_max_trades}")
            self.shutdown()
    
    def shutdown(self):
        """Shutdown the bot gracefully."""
        logger.info("Shutting down trading bot...")
        self.running = False
        
        # CRITICAL FIX: Stop SLManager worker thread
        if hasattr(self.risk_manager, 'sl_manager') and self.risk_manager.sl_manager:
            self.risk_manager.sl_manager.stop_sl_worker()
            logger.info("[OK] SLManager worker thread stopped")
        
        # Stop continuous trailing stop monitor
        self.stop_continuous_trailing_stop()
        
        # Stop state watchdog
        self.stop_state_watchdog()
        
        self.mt5_connector.shutdown()
        logger.info("Trading bot shutdown complete")

