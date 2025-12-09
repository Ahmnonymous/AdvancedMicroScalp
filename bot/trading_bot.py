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
from utils.logger_factory import get_logger, get_symbol_logger
from trade_logging.trade_logger import TradeLogger

# System startup logger
logger = get_logger("system_startup", "logs/system/system_startup.log")
# Scheduler logger for main loops
scheduler_logger = get_logger("scheduler", "logs/system/scheduler.log")
# System errors logger
error_logger = get_logger("system_errors", "logs/system/system_errors.log")


class TradingBot:
    """Main trading bot orchestrator."""
    
    def __init__(self, config_path: str = 'config.json'):
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Validate configuration
        validator = ConfigValidator(self.config)
        is_valid, errors, warnings = validator.validate()
        validator.log_results()
        
        if not is_valid:
            raise ValueError(f"Configuration validation failed. Fix errors before starting bot.")
        
        # Initialize components
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
                    trade_logger=self.trade_logger
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
        self.running = False
        
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
            'total_commission_paid': 0.0
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
        self.trailing_cycle_interval_ms = self.risk_config.get('trailing_cycle_interval_ms', 300)  # Default 300ms
        self.trailing_cycle_interval = self.trailing_cycle_interval_ms / 1000.0  # Convert to seconds for time.sleep
        
        # Fast trailing thread (for positions with profit >= threshold)
        self.fast_trailing_thread = None
        self.fast_trailing_running = False
        self.fast_trailing_interval_ms = self.risk_config.get('fast_trailing_interval_ms', 300)
        self.fast_trailing_threshold_usd = self.risk_config.get('fast_trailing_threshold_usd', 0.10)
        
        # Tracked positions for closure detection
        self.tracked_tickets = set()
    
        # Manual approval mode settings
        self.manual_approval_mode = False
        self.manual_max_trades = None
        self.manual_batch_cancelled = False  # Flag for cancel batch
        self.manual_wait_for_close_timeout = self.trading_config.get('manual_wait_for_close_timeout_seconds', 3600)  # Default 1 hour
        self.manual_scan_completed = False  # Track if scan has been completed in this session
        self.manual_approved_trades = []  # Track approved trades waiting to execute
        self.manual_trades_executing = False  # Track if trades are currently executing
    
    def connect(self) -> bool:
        """Connect to MT5."""
        logger.info("Connecting to MT5...")
        if self.mt5_connector.connect():
            logger.info("MT5 connection established")
            
            # Initialize session tracking
            account_info = self.mt5_connector.get_account_info()
            if account_info:
                self.session_start_balance = account_info.get('balance', 0)
                self.session_start_time = datetime.now()
                self.realized_pnl = 0.0
                logger.info(f"📊 Session started | Starting Balance: ${self.session_start_balance:.2f}")
            
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
        """Handle errors with supervisor logic."""
        self.consecutive_errors += 1
        self.last_error_time = datetime.now()
        
        error_logger.error(f"Error in {context}: {error}", exc_info=True)
        logger.error(f"Error in {context}: {error}")
        
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
            if self.trade_stats['total_trades'] > 0:
                success_rate = (self.trade_stats['successful_trades'] / self.trade_stats['total_trades']) * 100
                logger.info(f"     - Success Rate: {success_rate:.1f}%")
            if self.trade_stats['total_spread_paid'] > 0:
                logger.info(f"     - Total Spread Paid: {self.trade_stats['total_spread_paid']:.1f} points")
            logger.info("=" * 60)
            self.daily_pnl = 0.0
            self.realized_pnl_today = 0.0
            self.trade_count_today = 0
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
                                        except (ValueError, AttributeError):
                                            continue
                            except json.JSONDecodeError:
                                continue
            except Exception:
                continue
        
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
            logger.debug(f"📊 Updated realized P/L: +${profit:.2f} | Today: ${self.realized_pnl_today:.2f} | Total: ${self.realized_pnl:.2f}")
    
    def scan_for_opportunities(self) -> List[Dict[str, Any]]:
        """Scan for trading opportunities with SIMPLE logic and comprehensive logging."""
        opportunities = []
        
        try:
            # Get tradeable symbols
            symbols = self.pair_filter.get_tradeable_symbols()
            logger.info(f"🔍 Scanning {len(symbols)} symbols for trading opportunities...")
            
            if not symbols:
                logger.warning("⚠️ No tradeable symbols found! Check symbol filters.")
            
            for symbol in symbols:
                try:
                    logger.debug(f"📊 Analyzing {symbol}...")
                    
                    # 0. Check if symbol was previously restricted (prevent duplicate attempts)
                    if hasattr(self, '_restricted_symbols') and symbol.upper() in self._restricted_symbols:
                        logger.debug(f"⛔ [SKIP] {symbol} | Reason: Previously restricted - skipping to avoid duplicate attempts")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 0a. Check if symbol is tradeable/executable RIGHT NOW (market is open, trade mode enabled)
                    # This prevents non-executable symbols from appearing in opportunity lists
                    is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(symbol)
                    if not is_tradeable:
                        logger.debug(f"⛔ [SKIP] {symbol} | Reason: NOT EXECUTABLE - {reason}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue  # Skip this symbol - not tradeable right now
                    else:
                        logger.debug(f"✅ {symbol}: Symbol is tradeable and executable")
                    
                    # 0b. Check if market is closing soon (30 minutes filter)
                    should_skip_close, close_reason = self.market_closing_filter.should_skip(symbol)
                    if should_skip_close:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: {close_reason}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 0c. Check if volume/liquidity is sufficient
                    should_skip_volume, volume_reason, volume_value = self.volume_filter.should_skip(symbol)
                    if should_skip_volume:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: {volume_reason}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 1. Check if news is blocking (but allow trading if API fails)
                    news_blocking = self.news_filter.is_news_blocking(symbol)
                    if news_blocking:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: NEWS BLOCKING (high-impact news within 10 min window)")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    else:
                        logger.debug(f"✅ {symbol}: No news blocking")
                    
                    # 2. Get SIMPLE trend signal (SMA20 vs SMA50 only)
                    trend_signal = self.trend_filter.get_trend_signal(symbol)
                    
                    if trend_signal['signal'] == 'NONE':
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: No trend signal (SMA20 == SMA50 or invalid data)")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 2a. Check RSI filter (30-50 range for entries)
                    if self.trend_filter.use_rsi_filter and not trend_signal.get('rsi_filter_passed', True):
                        rsi_value = trend_signal.get('rsi', 50)
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: RSI filter failed (RSI: {rsi_value:.1f} not in range {self.trend_filter.rsi_entry_range_min}-{self.trend_filter.rsi_entry_range_max})")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 3. Check halal compliance (ALWAYS skip in test mode per requirements)
                    test_mode = self.config.get('pairs', {}).get('test_mode', False)
                    
                    if test_mode:
                        # Test mode: ALWAYS ignore halal checks (per requirements)
                        logger.debug(f"✅ {symbol}: Halal check skipped (test mode)")
                    else:
                        # Live mode: enforce halal if enabled
                        if not self.halal_compliance.validate_trade(symbol, trend_signal['signal']):
                            logger.info(f"⛔ [SKIP] {symbol} | Reason: HALAL COMPLIANCE CHECK FAILED")
                            self.trade_stats['filtered_opportunities'] += 1
                            continue
                    
                    # 3a. TESTING MODE: Check min lot size requirements (0.01-0.1, risk <= $2)
                    min_lot_valid = True
                    min_lot = 0.01
                    min_lot_reason = ""
                    if test_mode:
                        min_lot_valid, min_lot, min_lot_reason = self.risk_manager.check_min_lot_size_for_testing(symbol)
                        if not min_lot_valid:
                            logger.info(f"⛔ [SKIP] {symbol} | Signal: {trend_signal['signal']} | "
                                      f"MinLot: {min_lot:.4f} | Reason: {min_lot_reason}")
                            self.trade_stats['filtered_opportunities'] += 1
                            continue
                        logger.debug(f"✅ {symbol}: Min lot check passed - {min_lot_reason}")
                    
                    # 4. SIMPLIFIED setup validation (only checks if signal != NONE)
                    if not self.trend_filter.is_setup_valid_for_scalping(symbol, trend_signal):
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Setup validation failed (signal is NONE)")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 4a. Check trend strength minimum (SMA separation percentage)
                    sma_separation_pct = abs((trend_signal.get('sma_fast', 0) - trend_signal.get('sma_slow', 0)) / trend_signal.get('sma_slow', 1) * 100) if trend_signal.get('sma_slow', 0) > 0 else 0
                    min_trend_strength_pct = self.trading_config.get('min_trend_strength_pct', 0.05)  # Default 0.05%
                    if sma_separation_pct < min_trend_strength_pct:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Trend strength too weak (SMA separation: {sma_separation_pct:.4f}% < {min_trend_strength_pct}%)")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 5. Calculate quality score for trade selection
                    quality_assessment = self.trend_filter.assess_setup_quality(symbol, trend_signal)
                    quality_score = quality_assessment.get('quality_score', 0.0)
                    high_quality_setup = quality_assessment.get('is_high_quality', False)
                    min_quality_score = self.trading_config.get('min_quality_score', 50.0)
                    
                    # Filter by quality score - only trade high-quality setups
                    if quality_score < min_quality_score:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Quality score {quality_score:.1f} < threshold {min_quality_score} | Details: {', '.join(quality_assessment.get('reasons', []))}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # Check portfolio risk limit before opening trade
                    symbol_info_for_risk_check = self.mt5_connector.get_symbol_info(symbol)
                    if symbol_info_for_risk_check:
                        # Estimate risk for this trade
                        min_sl_pips = self.risk_manager.min_stop_loss_pips
                        point = symbol_info_for_risk_check.get('point', 0.00001)
                        pip_value = point * 10 if symbol_info_for_risk_check.get('digits', 5) == 5 or symbol_info_for_risk_check.get('digits', 3) == 3 else point
                        contract_size = symbol_info_for_risk_check.get('contract_size', 1.0)
                        min_sl_price = min_sl_pips * pip_value
                        estimated_risk = min_lot * min_sl_price * contract_size if min_sl_price > 0 and contract_size > 0 else self.risk_manager.max_risk_usd
                        
                        # Check portfolio risk
                        portfolio_risk_ok, portfolio_reason = self.risk_manager.check_portfolio_risk(new_trade_risk_usd=estimated_risk)
                        if not portfolio_risk_ok:
                            logger.info(f"⛔ [SKIP] {symbol} | Reason: Portfolio risk limit - {portfolio_reason}")
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
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Cannot open trade - {reason}")
                        symbol_logger = get_symbol_logger(symbol)
                        symbol_logger.debug(f"⛔ Cannot open trade: {reason}")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # 7. Check spread
                    spread_points = self.pair_filter.get_spread_points(symbol)
                    if spread_points is None:
                        logger.warning(f"⚠️ {symbol}: Cannot get spread information - skipping")
                        self.trade_stats['filtered_opportunities'] += 1
                        continue
                    
                    # Check spread acceptability
                    if not self.pair_filter.check_spread(symbol):
                        max_spread = self.pair_filter.max_spread_points
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Spread {spread_points:.2f} points > {max_spread} limit")
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
                            logger.info(f"⛔ [SKIP] {symbol} | Signal: {trend_signal['signal']} | "
                                      f"MinLot: {min_lot:.4f} | Spread: {spread_points:.1f}pts | "
                                      f"Spread+Fees: ${total_cost:.2f} > ${max_cost:.2f} | "
                                      f"Reason: Spread+Fees exceed limit (STRICT) | ({cost_description})")
                            self.trade_stats['filtered_opportunities'] += 1
                            continue
                            continue
                        
                        logger.debug(f"✅ {symbol}: Spread+Fees check passed - ${total_cost:.2f} <= ${max_cost:.2f} ({cost_description})")
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
                                min_sl_pips = self.risk_manager.min_stop_loss_pips
                                min_sl_price = min_sl_pips * pip_value
                                if min_sl_price > 0 and contract_size > 0:
                                    calculated_risk = min_lot_check * min_sl_price * contract_size
                    
                    # Enhanced opportunity logging with detailed breakdown
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
                    logger.info(f"✅ {symbol} | Signal: {signal_type} | Quality: {quality_score:.1f} | MinLot: {min_lot:.4f} | "
                              f"Spread: {spread_points:.1f}pts{spread_fees_info} | Pass: {pass_reason}")
                    
                    # Log signal type for debugging trade direction
                    if signal_type == 'SHORT':
                        logger.info(f"📉 {symbol}: SHORT signal detected - will place SELL order")
                    elif signal_type == 'LONG':
                        logger.info(f"📈 {symbol}: LONG signal detected - will place BUY order")
                    
                    # Get min_lot for opportunity (use symbol_info_for_risk if available)
                    opp_min_lot = min_lot if test_mode else (symbol_info_for_risk.get('volume_min', 0.01) if symbol_info_for_risk else 0.01)
                    
                    opportunities.append({
                        'symbol': symbol,
                        'signal': trend_signal['signal'],
                        'trend': trend_signal['trend'],
                        'sma_fast': trend_signal.get('sma_fast', 0),
                        'sma_slow': trend_signal.get('sma_slow', 0),
                        'rsi': trend_signal.get('rsi', 50),
                        'spread': spread_points,
                        'min_lot': opp_min_lot,
                        'spread_fees_cost': total_cost,  # Always include for sorting
                        'quality_score': quality_score  # Include quality score for sorting
                    })
                    
                except Exception as e:
                    logger.error(f"❌ Error scanning {symbol}: {e}", exc_info=True)
                    self.handle_error(e, f"Scanning {symbol}")
                    continue
        
        except Exception as e:
            logger.error(f"❌ Error in scan_for_opportunities: {e}", exc_info=True)
            self.handle_error(e, "Scanning for opportunities")
        
        logger.info(f"🎯 Found {len(opportunities)} trading opportunity(ies)")
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
            # RANDOMNESS FACTOR: Skip in manual approval mode (user already approved)
            # NOTE: Randomness skip is NOT a failure - it's intentional filtering
            if not skip_randomness:
                random_value = random.random()  # 0.0 to 1.0
                
                if random_value < self.randomness_factor:
                    logger.info(f"🎲 {symbol}: RANDOMNESS SKIP - Random value {random_value:.3f} < threshold {self.randomness_factor} (skipping trade to avoid over-trading)")
                    # Return special code to indicate this is NOT a failure (just filtering)
                    return None  # None indicates filtered/skipped, not failed
                else:
                    logger.info(f"🎲 {symbol}: RANDOMNESS PASS - Random value {random_value:.3f} >= threshold {self.randomness_factor} (proceeding with trade)")
            
            # Apply randomized delay if enabled (but ensure execution within 0.3 seconds)
            execution_timeout = self.trading_config.get('execution_timeout_seconds', 0.3)
            start_time = time.time()
            
            if self.randomize_timing:
                # Limit delay to ensure execution within timeout
                max_delay = min(self.max_delay_seconds, int(execution_timeout * 0.8))
                delay_seconds = random.randint(0, max_delay)
                if delay_seconds > 0:
                    logger.info(f"⏱️ {symbol}: Random delay of {delay_seconds}s before executing {signal}")
                    time.sleep(delay_seconds)
                    
                    # Re-check trend signal after delay (trend might have changed)
                    trend_signal = self.trend_filter.get_trend_signal(symbol)
                    if trend_signal['signal'] != signal:
                        logger.warning(f"⚠️ {symbol}: Trend changed during delay. Original: {signal}, Current: {trend_signal['signal']}. Skipping trade.")
                        # Trend change is NOT a failure - it's a safety check
                        return None  # None indicates filtered/skipped, not failed
            
            # Determine order type (ensure both BUY and SELL work correctly)
            if signal == 'LONG':
                order_type = OrderType.BUY
                logger.debug(f"📈 {symbol}: Signal LONG → OrderType.BUY")
            elif signal == 'SHORT':
                order_type = OrderType.SELL
                logger.debug(f"📉 {symbol}: Signal SHORT → OrderType.SELL")
            else:
                logger.error(f"❌ {symbol}: Invalid signal '{signal}' - must be LONG or SHORT")
                self.trade_stats['failed_trades'] += 1
                return False
            
            # Get current price for entry
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                logger.error(f"❌ {symbol}: Cannot get symbol info - trade execution failed")
                self.trade_stats['failed_trades'] += 1
                return False
            
            entry_price = symbol_info['ask'] if order_type == OrderType.BUY else symbol_info['bid']
            logger.info(f"💰 {symbol}: Entry price = {entry_price:.5f} ({'ASK' if order_type == OrderType.BUY else 'BID'})")
            
            # Calculate dynamic stop loss based on ATR/volatility
            min_stop_loss_pips = self.config.get('risk', {}).get('min_stop_loss_pips', 10)
            stop_loss_pips = self.trend_filter.calculate_dynamic_stop_loss(symbol, min_stop_loss_pips)
            
            # Get broker's minimum stops level and ensure SL respects it
            stops_level = symbol_info.get('trade_stops_level', 0)
            point = symbol_info['point']
            pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
            min_stops_pips = (stops_level * point) / pip_value if pip_value > 0 and stops_level > 0 else 0
            
            # Ensure stop loss meets broker's minimum
            if min_stops_pips > 0 and stop_loss_pips < min_stops_pips:
                logger.info(f"🔧 {symbol}: Adjusting stop loss from {stop_loss_pips:.1f} to {min_stops_pips:.1f} pips (broker minimum stops_level: {stops_level})")
                stop_loss_pips = max(stop_loss_pips, min_stops_pips)
            
            logger.info(f"🛡️ {symbol}: Stop loss = {stop_loss_pips:.1f} pips (broker min: {min_stops_pips:.1f} pips)")
            
            # Validate stop loss with entry price
            order_type_str = 'BUY' if order_type == OrderType.BUY else 'SELL'
            if not self.risk_manager.validate_stop_loss(symbol, stop_loss_pips, entry_price, order_type_str):
                logger.warning(f"⛔ {symbol}: Stop loss validation failed (SL: {stop_loss_pips:.1f} pips) - trade execution aborted")
                self.trade_stats['failed_trades'] += 1
                return False
            
            # CRITICAL FIX: Calculate lot size to achieve $2 risk (not always use minimum)
            # Get symbol info for lot size calculation
            symbol_info_for_lot = self.mt5_connector.get_symbol_info(symbol)
            if not symbol_info_for_lot:
                logger.error(f"❌ {symbol}: Cannot get symbol info for lot size calculation")
                self.trade_stats['failed_trades'] += 1
                return False
            
            # Calculate point, pip value, and contract size
            point = symbol_info_for_lot.get('point', 0.00001)
            pip_value = point * 10 if symbol_info_for_lot.get('digits', 5) == 5 or symbol_info_for_lot.get('digits', 3) == 3 else point
            contract_size = symbol_info_for_lot.get('contract_size', 1.0)
            stop_loss_price = stop_loss_pips * pip_value
            
            # Get broker minimum lot size and config overrides
            symbol_min_lot = symbol_info_for_lot.get('volume_min', 0.01)
            symbol_upper = symbol.upper()
            symbol_limit_config = self.risk_manager.symbol_limits.get(symbol_upper, {})
            config_min_lot = symbol_limit_config.get('min_lot')
            
            # Determine effective minimum lot (config override or broker minimum)
            if config_min_lot is not None:
                effective_min_lot = max(symbol_min_lot, config_min_lot)
                min_source = "config"
            else:
                effective_min_lot = symbol_min_lot
                min_source = "broker"
            
            # Calculate lot size needed to achieve $2 risk
            max_risk_usd = self.risk_manager.max_risk_usd  # Should be $2.0
            if stop_loss_price > 0 and contract_size > 0:
                calculated_lot_for_risk = max_risk_usd / (stop_loss_price * contract_size)
            else:
                calculated_lot_for_risk = effective_min_lot
            
            # Round to volume step
            volume_step = symbol_info_for_lot.get('volume_step', 0.01)
            if volume_step > 0:
                calculated_lot_for_risk = round(calculated_lot_for_risk / volume_step) * volume_step
                effective_min_lot = round(effective_min_lot / volume_step) * volume_step
                if effective_min_lot < volume_step:
                    effective_min_lot = volume_step
            
            # Use the MAXIMUM of calculated lot (for $2 risk) and broker minimum
            # This ensures we get $2 risk if possible, but never go below broker minimum
            lot_size = max(calculated_lot_for_risk, effective_min_lot)
            
            # Calculate actual risk with chosen lot size
            estimated_risk = lot_size * stop_loss_price * contract_size
            
            # CRITICAL: Log lot size decision
            if calculated_lot_for_risk >= effective_min_lot:
                lot_reason = f"Calculated for ${max_risk_usd:.2f} risk ({calculated_lot_for_risk:.4f})"
            else:
                lot_reason = f"Using minimum lot {effective_min_lot:.4f} ({min_source}) - calculated {calculated_lot_for_risk:.4f} was below minimum"
            
            logger.info(f"📦 {symbol}: LOT SIZE = {lot_size:.4f} | "
                       f"Reason: {lot_reason} | "
                       f"Estimated Risk: ${estimated_risk:.2f} (target: ${max_risk_usd:.2f})")
            
            # CRITICAL FIX: Reject trade if estimated risk (with slippage buffer) exceeds max_risk_usd
            # Add 10% slippage buffer to account for execution slippage
            max_risk_usd = self.risk_manager.max_risk_usd
            slippage_buffer = 1.10  # 10% buffer for slippage
            risk_with_slippage = estimated_risk * slippage_buffer
            
            if risk_with_slippage > max_risk_usd:
                logger.warning(f"⛔ {symbol}: REJECTED - Estimated risk ${estimated_risk:.2f} (with slippage: ${risk_with_slippage:.2f}) exceeds max ${max_risk_usd:.2f} | "
                             f"Lot: {lot_size:.4f}, SL: {stop_loss_pips:.1f} pips, Contract: {contract_size}")
                # Risk validation failure is NOT an execution failure - it's a pre-execution filter
                # Don't increment failed_trades - this is expected risk management
                return None  # None indicates filtered/skipped, not failed
            
            # Log trade execution with lot size details
            # Use estimated_risk here (actual risk will be calculated after order placement with fill price)
            try:
                logger.info(f"📦 {symbol}: EXECUTING WITH MINIMUM LOT SIZE = {lot_size:.4f} | "
                           f"Source: {min_source} | "
                           f"Estimated Risk: ${estimated_risk:.2f} (with slippage: ${risk_with_slippage:.2f}, max: ${max_risk_usd:.2f}) | "
                           f"Policy: Always use LEAST POSSIBLE lot size per symbol")
            except (NameError, UnboundLocalError) as e:
                # Fallback if variables are undefined (should not happen, but safety check)
                logger.warning(f"⚠️ {symbol}: Error logging trade details: {e}. Using fallback values.")
                fallback_risk = estimated_risk if 'estimated_risk' in locals() else 0.0
                fallback_slippage = risk_with_slippage if 'risk_with_slippage' in locals() else 0.0
                fallback_max = max_risk_usd if 'max_risk_usd' in locals() else 2.0
                logger.info(f"📦 {symbol}: EXECUTING WITH MINIMUM LOT SIZE = {lot_size:.4f} | "
                           f"Source: {min_source} | "
                           f"Estimated Risk: ${fallback_risk:.2f} (with slippage: ${fallback_slippage:.2f}, max: ${fallback_max:.2f}) | "
                           f"Policy: Always use LEAST POSSIBLE lot size per symbol")
            
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
                logger.info(f"⛔ [SKIP] {symbol} | Reason: Market closed - {reason}")
                print(f"⛔ {symbol}: Market closed - {reason}. Re-scan when market opens.")
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
                    logger.warning(f"⚠️ {symbol}: Execution timeout ({elapsed:.3f}s > {execution_timeout}s) on attempt {attempt + 1}/{max_retries}")
                
                # Ensure MT5 is connected before placing order
                if not self.mt5_connector.ensure_connected():
                    logger.warning(f"⚠️ {symbol}: MT5 disconnected, attempting reconnect...")
                    if not self.mt5_connector.reconnect():
                        logger.error(f"❌ {symbol}: Failed to reconnect to MT5 (attempt {attempt + 1}/{max_retries})")
                        if attempt < max_retries - 1:
                            # Exponential backoff for connection issues
                            backoff_delay = backoff_base * (2 ** attempt)
                            logger.info(f"⏳ {symbol}: Waiting {backoff_delay:.1f}s before retry...")
                            time.sleep(backoff_delay)
                            continue
                        else:
                            logger.error(f"❌ {symbol}: Connection failed after {max_retries} attempts")
                            break
                
                # Re-check market status before each retry (market may have closed)
                is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(symbol)
                if not is_tradeable:
                    logger.warning(f"⏰ {symbol}: Market closed during retry - {reason}")
                    logger.info(f"⛔ [SKIP] {symbol} | Reason: Market closed - {reason}")
                    # Market closed is NOT a failure - it's a market condition
                    return None  # None indicates filtered/skipped, not failed
                
                order_result = self.order_manager.place_order(
                    symbol=symbol,
                    order_type=order_type,
                    lot_size=lot_size,
                    stop_loss=stop_loss_pips,
                    comment=f"Bot {signal}"
                )
                
                # Handle new return format (dict with ticket, entry_price_actual, slippage)
                entry_price_actual = entry_price  # Default to requested price
                slippage = 0.0
                
                if order_result is None:
                    ticket = None
                elif isinstance(order_result, dict):
                    if 'error' in order_result:
                        ticket = order_result['error']  # Error code
                    else:
                        ticket = order_result.get('ticket')
                        entry_price_actual = order_result.get('entry_price_actual', entry_price)
                        slippage = order_result.get('slippage', 0.0)
                else:
                    # Legacy format (int ticket)
                    ticket = order_result
                
                # Success case
                if ticket and ticket > 0:
                    elapsed = time.time() - execution_start
                    if elapsed > execution_timeout:
                        logger.warning(f"⚠️ {symbol}: Order placed but exceeded timeout ({elapsed:.3f}s > {execution_timeout}s)")
                    else:
                        logger.debug(f"✅ {symbol}: Order placed within timeout ({elapsed:.3f}s < {execution_timeout}s)")
                    break
                
                # Error code classification:
                # -1: Invalid volume (10014) - retry once with broker minimum
                # -2: Transient error (connection, timeout, etc.) - retry with backoff
                # -3: Invalid stops (10016) - non-retryable
                # -4: Market closed (10018) - non-retryable
                # -5: Trading restriction (10027, 10044, etc.) - non-retryable
                
                # Handle error codes (from dict or legacy int)
                error_code = ticket if isinstance(ticket, int) and ticket < 0 else (order_result.get('error') if isinstance(order_result, dict) else None)
                
                # Market closed - don't retry
                if error_code == -4:
                    logger.warning(f"⏰ {symbol}: Market closed (error 10018) - skipping trade execution")
                    logger.info(f"⛔ [SKIP] {symbol} | Reason: Market closed - order rejected by broker")
                    print(f"⛔ {symbol}: Market closed - order rejected. Re-scan when market opens.")
                    # Market closed is NOT a failure - it's a market condition
                    return None  # None indicates filtered/skipped, not failed
                
                # Trading restrictions - don't retry (10027, 10044, etc.)
                if error_code == -5:
                    logger.error(f"❌ {symbol}: Trading restriction (error 10027/10044) - trade not allowed")
                    logger.info(f"⛔ [SKIP] {symbol} | Reason: Trading restriction - symbol/order type not tradeable")
                    print(f"❌ {symbol}: Trading restriction - this symbol cannot be traded. Check account permissions or symbol restrictions.")
                    self.trade_stats['failed_trades'] += 1
                    # Mark symbol as restricted to prevent future attempts in this session
                    if not hasattr(self, '_restricted_symbols'):
                        self._restricted_symbols = set()
                    self._restricted_symbols.add(symbol.upper())
                    break  # Don't retry
                
                # Invalid stops - don't retry
                if error_code == -3:
                    logger.error(f"❌ {symbol}: Invalid stops (error 10016) - trade failed")
                    logger.info(f"⛔ [SKIP] {symbol} | Reason: Invalid stops - check stop loss configuration")
                    self.trade_stats['failed_trades'] += 1
                    break  # Don't retry
                
                # Invalid volume - retry once with broker minimum
                if error_code == -1:
                    if not volume_error_occurred:  # Only retry once
                        volume_error_occurred = True
                        logger.warning(f"⚠️ {symbol}: Invalid volume error (lot: {lot_size:.4f}), retrying once with broker minimum lot...")
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
                        logger.error(f"❌ {symbol}: Cannot get symbol info for retry")
                        break
                else:
                    # Already retried once - fail and log
                    logger.error(f"❌ {symbol}: Invalid volume error persisted after retry with broker minimum lot - trade failed")
                    logger.info(f"⛔ [SKIP] {symbol} | Reason: Invalid volume - retry with minimum lot failed")
                    self.trade_stats['failed_trades'] += 1
                    break
                
                # Transient errors (-2 or None) - retry with exponential backoff
                if error_code == -2 or ticket is None:
                    if attempt < max_retries - 1:
                        # Exponential backoff for transient errors
                        backoff_delay = backoff_base * (2 ** attempt)
                        logger.warning(f"⚠️ {symbol}: Order placement failed (attempt {attempt + 1}/{max_retries}), retrying in {backoff_delay:.1f}s...")
                        logger.info(f"⏳ {symbol}: Exponential backoff delay: {backoff_delay:.1f}s")
                        time.sleep(backoff_delay)
                        continue
                    else:
                        logger.error(f"❌ {symbol}: Order placement failed after {max_retries} attempts with exponential backoff")
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Order placement failed after {max_retries} retries")
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
                        logger.warning(f"⚠️ {symbol}: Actual risk ${actual_risk_with_fill:.2f} (with buffer: ${risk_with_slippage_actual:.2f}) exceeds max ${max_risk_usd_actual:.2f} after fill")
                        # Log but don't reject - trade already placed, just warn
                except (NameError, TypeError, ValueError) as e:
                    logger.error(f"❌ {symbol}: Error calculating actual risk: {e}. Using estimated risk as fallback.")
                    # Fallback to estimated risk if calculation fails
                    actual_risk_with_fill = estimated_risk if 'estimated_risk' in locals() else lot_size * stop_loss_pips * 0.10
                    risk_with_slippage_actual = risk_with_slippage if 'risk_with_slippage' in locals() else actual_risk_with_fill * 1.10
                
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
                
                self.reset_error_count()
                return True
            else:
                logger.error(f"❌ {symbol}: TRADE EXECUTION FAILED after {max_retries} attempts")
                logger.error(f"   Symbol: {symbol}, Signal: {signal}, Lot: {lot_size}, SL: {stop_loss_pips:.1f} pips")
                self.trade_stats['failed_trades'] += 1
                return False
        
        except Exception as e:
            logger.error(f"❌ {symbol}: Exception during trade execution: {e}", exc_info=True)
            self.handle_error(e, f"Executing trade {symbol}")
            self.trade_stats['failed_trades'] += 1
            return False
    
    def _continuous_trailing_stop_loop(self):
        """Background thread loop for continuous trailing stop monitoring."""
        logger.info(f"🔄 Continuous trailing stop monitor started (interval: {self.trailing_cycle_interval_ms}ms = {self.trailing_cycle_interval:.3f}s)")
        
        while self.trailing_stop_running and self.running:
            try:
                if not self.check_kill_switch():
                    # Ensure connection before monitoring
                    if self.mt5_connector.ensure_connected():
                        # Monitor all positions and update trailing stops (normal polling)
                        self.risk_manager.monitor_all_positions_continuous(use_fast_polling=False)
                    else:
                        # Try to reconnect if disconnected
                        logger.warning("Continuous trailing stop: MT5 disconnected, attempting reconnect...")
                        self.mt5_connector.reconnect()
                
                # Sleep for the configured interval
                time.sleep(self.trailing_cycle_interval)
            
            except Exception as e:
                logger.error(f"Error in continuous trailing stop loop: {e}", exc_info=True)
                # Continue running even if there's an error, but ensure connection
                try:
                    self.mt5_connector.ensure_connected()
                except:
                    pass
                time.sleep(self.trailing_cycle_interval)
        
        logger.info("Continuous trailing stop monitor stopped")
    
    def _fast_trailing_stop_loop(self):
        """Background thread loop for fast trailing stop monitoring (positions with profit >= threshold)."""
        fast_interval_seconds = self.fast_trailing_interval_ms / 1000.0
        logger.info(f"⚡ Fast trailing stop monitor started (interval: {fast_interval_seconds:.3f}s = {self.fast_trailing_interval_ms}ms)")
        
        while self.fast_trailing_running and self.running:
            try:
                if not self.check_kill_switch():
                    # Ensure connection before monitoring
                    if self.mt5_connector.ensure_connected():
                        # Monitor only positions in fast polling mode (300ms updates for profitable positions)
                        self.risk_manager.monitor_all_positions_continuous(use_fast_polling=True)
                    else:
                        # Try to reconnect if disconnected
                        logger.warning("Fast trailing stop: MT5 disconnected, attempting reconnect...")
                        self.mt5_connector.reconnect()
                
                # Sleep for the fast interval (300ms for milliseconds-level updates)
                time.sleep(fast_interval_seconds)
            
            except Exception as e:
                logger.error(f"Error in fast trailing stop loop: {e}", exc_info=True)
                # Continue running even if there's an error, but ensure connection
                try:
                    self.mt5_connector.ensure_connected()
                except:
                    pass
                time.sleep(fast_interval_seconds)
        
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
        logger.info("✅ Continuous trailing stop monitor thread started")
        
        # Start fast trailing stop thread
        self.fast_trailing_running = True
        self.fast_trailing_thread = threading.Thread(
            target=self._fast_trailing_stop_loop,
            name="FastTrailingStopMonitor",
            daemon=True
        )
        self.fast_trailing_thread.start()
        logger.info("✅ Fast trailing stop monitor thread started")
        
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
        logger.info("✅ Position closure monitor thread started")
    
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
                        logger.info(f"🔴 Position {closure['ticket']} ({closure['symbol']}) closed - logged")
                
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
            # Check all positions for halal compliance (skip in test mode)
            test_mode = self.config.get('pairs', {}).get('test_mode', False)
            if not test_mode:
                # Only check halal compliance in live mode
                self.halal_compliance.check_all_positions()
            
            positions = self.order_manager.get_open_positions()
            
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
                    print("⚠️  Please enter a number between 1 and 6.")
            except ValueError:
                print("⚠️  Invalid input. Please enter a number between 1 and 6.")
            except (EOFError, KeyboardInterrupt):
                print("\n⚠️  Input cancelled. Using default from config.")
                return self.risk_manager.max_open_trades
    
    def display_opportunities(self, opportunities: List[Dict[str, Any]], max_count: int) -> None:
        """
        Display top trading opportunities in a user-friendly format with warnings.
        Shows quality score, lot size, spread, fees, risk, and any warnings.
        """
        if not opportunities:
            print("\n❌ No trading opportunities found.")
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
            estimated_risk = 2.0  # Default target
            sl_pips = self.risk_manager.min_stop_loss_pips
            if symbol_info:
                point = symbol_info.get('point', 0.00001)
                pip_value = point * 10 if symbol_info.get('digits', 5) == 5 or symbol_info.get('digits', 3) == 3 else point
                contract_size = symbol_info.get('contract_size', 1.0)
                min_sl_price = sl_pips * pip_value
                if min_sl_price > 0 and contract_size > 0:
                    # Calculate lot size for $2 risk
                    calculated_lot = self.risk_manager.max_risk_usd / (min_sl_price * contract_size)
                    # Use max of calculated and minimum lot
                    effective_lot = max(calculated_lot, min_lot)
                    estimated_risk = effective_lot * min_sl_price * contract_size
            
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
                    print("⚠️  Please enter Y (Yes), N (No), ALL (approve all), C (cancel batch), or S (skip remaining).")
            except (EOFError, KeyboardInterrupt):
                print("\n⚠️  Input cancelled. Skipping trade.")
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
                    logger.debug(f"✅ {symbol}: Position {ticket} verified - confirmed open")
                    return True
            
            time.sleep(check_interval)
        
        # Final check
        positions = self.order_manager.get_open_positions()
        for position in positions:
            if position.get('ticket') == ticket:
                logger.debug(f"✅ {symbol}: Position {ticket} verified - confirmed open")
                return True
        
        logger.warning(f"⚠️ {symbol}: Position {ticket} not found after {max_wait_seconds}s wait")
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
                logger.info(f"✅ Position {ticket} closed after {elapsed:.1f}s")
                print(f"✅ Position {ticket} closed - proceeding to next trade")
                
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
        logger.info(f"🚀 Executing approved trade: {symbol} {signal}")
        result = self.execute_trade(opportunity, skip_randomness=True)
        
        # execute_trade returns True (success), False (failure), or None (filtered)
        if result is False:
            logger.error(f"❌ {symbol}: Trade execution failed")
            print(f"❌ Execution failed for {symbol} {signal}")
            return None
        elif result is None:
            logger.info(f"⛔ {symbol}: Trade filtered/skipped (risk validation or safety check)")
            print(f"⛔ {symbol}: Trade filtered - not a failure")
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
            logger.info(f"✅ {symbol}: Position {new_ticket} confirmed open and verified")
            print(f"✅ Position {new_ticket} verified for {symbol} {signal}")
            
            # Wait for position to close if requested
            if wait_for_close:
                self.wait_for_position_close(new_ticket, symbol)
            
            return new_ticket
        else:
            # Trade might have executed but position not found (could be instant close, etc.)
            # Check if position count increased at all
            position_count_after = self.order_manager.get_position_count()
            if position_count_after > position_count_before:
                logger.warning(f"⚠️ {symbol}: Position count increased but specific position not verified (may have different symbol)")
                print(f"⚠️  Position verification uncertain for {symbol} (position count changed)")
                # If wait_for_close is True but we don't have a ticket, we can't wait
                if not wait_for_close:
                    return -1  # Special value indicating success but no ticket
                return None
            else:
                logger.warning(f"⚠️ {symbol}: Trade executed but position not found after {max_wait}s")
                print(f"⚠️  Position not verified for {symbol} {signal} after {max_wait}s")
                # Position might have closed immediately - if wait_for_close, we're done
                if not wait_for_close:
                    return -1  # Special value indicating success but no ticket
                return None
    
    def run_cycle(self):
        """Execute one trading cycle."""
        if self.check_kill_switch():
            return
        
        # Ensure MT5 connection with retry logic and exponential backoff
        if not self.mt5_connector.ensure_connected():
            logger.warning("MT5 not connected, attempting reconnect...")
            reconnect_success = False
            max_reconnect_attempts = 3
            for reconnect_attempt in range(max_reconnect_attempts):
                if self.mt5_connector.reconnect():
                    reconnect_success = True
                    logger.info(f"✅ MT5 reconnected successfully (attempt {reconnect_attempt + 1})")
                    break
                else:
                    # Exponential backoff: 2^attempt seconds
                    backoff_delay = 2 ** reconnect_attempt
                    logger.warning(f"⚠️ Reconnection attempt {reconnect_attempt + 1}/{max_reconnect_attempts} failed, retrying in {backoff_delay}s...")
                    time.sleep(backoff_delay)
            
            if not reconnect_success:
                error_msg = f"MT5 reconnection failed after {max_reconnect_attempts} attempts"
                logger.error(f"❌ {error_msg}")
                if self.manual_approval_mode:
                    print(f"\n❌ {error_msg} - Aborting batch execution")
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
                        print(f"\n⚠️  No available trade slots. Current positions: {initial_positions}/{max_trades}")
                        opportunities = []  # Clear opportunities
                    
                    # Get user approval for each trade
                    approved_opportunities = []
                    approve_all = False
                    
                    if remaining_slots <= 0:
                        print(f"\n⏸️  Max open trades ({max_trades}) already reached - cannot take more trades.")
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
                                print(f"\n⏸️  Max open trades ({max_trades}) reached - cannot take more trades.")
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
                                    print(f"⏭️  Skipping remaining trades")
                                    logger.info("User skipped remaining trades")
                                    break
                                elif approval == 'ALL':
                                    approve_all = True
                                    approved_opportunities.append(opp)
                                    print(f"✅ Approved ALL remaining trades")
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
                                    print(f"✅ Approved: {symbol} {signal}")
                                else:
                                    print(f"❌ Skipped: {symbol} {signal}")
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
                            print(f"\n⚠️  No trades approved for execution.\n")
                
                
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
                
                # Get current position count
                current_positions = self.order_manager.get_position_count()
                max_trades = self.manual_max_trades if (self.manual_approval_mode and self.manual_max_trades is not None) else self.risk_manager.max_open_trades
                trades_executed = 0
                trades_skipped = 0
                failed_symbols_in_batch = set()  # Track symbols that failed in this batch to prevent duplicates
                
                # Execute opportunities (sequential in manual mode, parallel in automatic mode)
                for idx, opportunity in enumerate(opportunities, 1):
                    # Always get fresh position count to prevent exceeding max
                    current_positions = self.order_manager.get_position_count()
                    
                    # Check if we've reached max open trades
                    if current_positions >= max_trades:
                        logger.info(f"⏸️  Max open trades ({max_trades}) reached - skipping remaining opportunities")
                        trades_skipped += len(opportunities) - idx + 1
                        break
                    
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
                            logger.info(f"⏭️ {symbol}: Skipping - already failed in this batch")
                            print(f"⏭️ {symbol}: Skipped - already attempted and failed in this batch")
                            trades_skipped += 1
                            continue
                        
                        # Check if symbol was previously restricted
                        if hasattr(self, '_restricted_symbols') and symbol.upper() in self._restricted_symbols:
                            logger.info(f"⏭️ {symbol}: Skipping - symbol is restricted")
                            print(f"⏭️ {symbol}: Skipped - symbol is restricted (cannot be traded)")
                            trades_skipped += 1
                            continue
                        
                        print(f"\n{'='*80}")
                        print(f"📈 Executing Trade {idx}/{len(opportunities)}: {symbol} {signal}")
                        print(f"{'='*80}")
                        
                        # Comprehensive safety checks before executing approved trade
                        # 0. Check if symbol was previously restricted (prevent duplicate attempts)
                        if hasattr(self, '_restricted_symbols') and symbol.upper() in self._restricted_symbols:
                            logger.warning(f"⏰ {symbol}: Symbol is restricted - skipping execution")
                            logger.info(f"⛔ [SKIP] {symbol} | Reason: Symbol restricted - cannot be traded")
                            print(f"⛔ {symbol}: Symbol is restricted - cannot be traded. Skipped.")
                            failed_symbols_in_batch.add(symbol.upper())
                            trades_skipped += 1
                            continue
                        
                        # 0a. Check if market is open (CRITICAL - prevents unnecessary order attempts)
                        is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(symbol)
                        if not is_tradeable:
                            logger.warning(f"⏰ {symbol}: Market closed or not tradeable - {reason}")
                            logger.info(f"⛔ [SKIP] {symbol} | Reason: Market closed - {reason}")
                            print(f"⛔ {symbol}: Market closed - {reason}. Skipped. Re-scan when market opens.")
                            failed_symbols_in_batch.add(symbol.upper())
                            trades_skipped += 1
                            continue
                        
                        # 1. Check portfolio risk
                        symbol_info_for_risk = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
                        if symbol_info_for_risk:
                            min_sl_pips = self.risk_manager.min_stop_loss_pips
                            point = symbol_info_for_risk.get('point', 0.00001)
                            pip_value = point * 10 if symbol_info_for_risk.get('digits', 5) == 5 or symbol_info_for_risk.get('digits', 3) == 3 else point
                            contract_size = symbol_info_for_risk.get('contract_size', 1.0)
                            min_sl_price = min_sl_pips * pip_value
                            estimated_risk = min_lot * min_sl_price * contract_size if min_sl_price > 0 and contract_size > 0 else self.risk_manager.max_risk_usd
                            
                            portfolio_risk_ok, portfolio_reason = self.risk_manager.check_portfolio_risk(new_trade_risk_usd=estimated_risk)
                            if not portfolio_risk_ok:
                                logger.warning(f"⛔ {symbol}: Portfolio risk limit - {portfolio_reason}")
                                print(f"⛔ {symbol}: Cannot execute - {portfolio_reason}")
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
                            logger.warning(f"⛔ {symbol}: Cannot open trade - {reason}")
                            print(f"⛔ {symbol}: Cannot execute - {reason}")
                            trades_skipped += 1
                            continue
                        
                        # 3. Check spread (re-check before execution)
                        if not self.pair_filter.check_spread(symbol):
                            spread_points = self.pair_filter.get_spread_points(symbol)
                            max_spread = self.pair_filter.max_spread_points
                            logger.warning(f"⛔ {symbol}: Spread {spread_points:.2f} points > {max_spread} limit")
                            print(f"⛔ {symbol}: Spread too wide - cannot execute")
                            trades_skipped += 1
                            continue
                        
                        # 4. Check price staleness
                        symbol_info_fresh = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=True)
                        if symbol_info_fresh is None:
                            logger.warning(f"⛔ {symbol}: Price is stale or symbol info unavailable")
                            print(f"⛔ {symbol}: Price stale - cannot execute")
                            trades_skipped += 1
                            continue
                        
                        # 5. Check halal compliance (if not in test mode)
                        test_mode = self.config.get('pairs', {}).get('test_mode', False)
                        if not test_mode:
                            if not self.halal_compliance.validate_trade(symbol, signal):
                                logger.warning(f"⛔ {symbol}: Halal compliance check failed")
                                print(f"⛔ {symbol}: Halal compliance failed - cannot execute")
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
                            logger.info(f"✅ {symbol}: Trade {idx}/{len(opportunities)} executed and closed successfully")
                            print(f"✅ Trade {idx}/{len(opportunities)} completed and closed: {symbol} {signal}")
                            trades_executed += 1
                        elif ticket == -1:
                            # Position opened but couldn't verify ticket (might have closed immediately)
                            logger.info(f"✅ {symbol}: Trade {idx}/{len(opportunities)} executed (position closed immediately)")
                            print(f"✅ Trade {idx}/{len(opportunities)} executed: {symbol} {signal}")
                            trades_executed += 1
                        else:
                            if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'FAILED':<20} | Trade execution failed")
                            logger.error(f"❌ {symbol}: Trade {idx}/{len(opportunities)} execution failed (check logs above)")
                            print(f"❌ Trade {idx}/{len(opportunities)} failed: {symbol} {signal}")
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
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Cannot open trade - {reason}")
                        trades_skipped += 1
                        continue
                    
                    # Execute trade
                    result = self.execute_trade(opportunity)
                    
                    # execute_trade returns:
                    # - True: Trade executed successfully
                    # - False: Actual execution failure (order placement failed, etc.)
                    # - None: Filtered/skipped (randomness, risk validation, trend change) - NOT a failure
                    
                    if result is True:
                        if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'PASS - EXECUTED':<20} | Trade executed")
                        logger.info(f"✅ {symbol}: Trade execution completed successfully")
                        trades_executed += 1
                            # DO NOT manually increment - always use get_position_count() in next iteration
                    elif result is None:
                        # Filtered/skipped - NOT a failure (randomness, risk validation, trend change)
                        if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'SKIP':<20} | Filtered (not a failure)")
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Trade filtered/skipped (randomness, risk validation, or trend change)")
                        self.trade_stats['filtered_opportunities'] += 1
                        trades_skipped += 1
                    else:  # result is False - actual execution failure
                        if test_mode:
                                logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'FAILED':<20} | Trade execution failed")
                        logger.info(f"❌ [FAILED] {symbol} | Reason: Trade execution failed (order placement error)")
                        trades_skipped += 1
                    
                    # Re-check position count after execution
                    current_positions_after = self.order_manager.get_position_count()
                    if current_positions_after >= max_trades:
                        logger.info(f"⏸️  Max open trades ({max_trades}) reached after trade {idx}")
                        if idx < len(opportunities):
                            print(f"\n⏸️  Max open trades reached - stopping batch execution.")
                            trades_skipped += len(opportunities) - idx
                            break
                
                # Close opportunity table (testing mode)
                if test_mode:
                    logger.info("-" * 100)
                
                # Get final position count
                final_position_count = self.order_manager.get_position_count()
                
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
                    print("\n⚠️  No trading opportunities found this cycle.")
                    print("   (Check logs for reasons: quality score, spread, portfolio risk, etc.)\n")
                logger.info("ℹ️  No trading opportunities found this cycle (check logs above for reasons)")
            
            self.reset_error_count()
        
        except Exception as e:
            self.handle_error(e, "Trading cycle")
    
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
            print(f"\n✅ Manual Approval Mode Enabled")
            print(f"   Max Trades: {self.manual_max_trades}")
            print(f"   Cycle Interval: {cycle_interval_seconds}s")
        else:
            logger.info(f"Trading bot started in AUTOMATIC MODE. Cycle interval: {cycle_interval_seconds}s")
        
        # Start continuous trailing stop monitor
        self.start_continuous_trailing_stop()
        
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
                        print(f"\n✅ All trades finished. Ready for new scan.")
                        logger.info("All trades finished - ready for new scan")
                
                # Run one trading cycle (only if not already scanning/executing in manual mode)
                if not self.manual_approval_mode or not self.manual_trades_executing:
                    scheduler_logger.info("Starting trading cycle")
                    self.run_cycle()
                    scheduler_logger.info("Trading cycle completed")
                
                # In manual mode, ask if user wants to continue scanning (only after trades finish)
                if self.manual_approval_mode:
                    # Only ask if we're not currently executing trades
                    if not self.manual_trades_executing:
                        try:
                            print("\n" + "=" * 100)
                            continue_choice = input("🔄 Scan for more opportunities? (Y/N, or press Enter to continue): ").strip().upper()
                            print("=" * 100)
                            
                            if continue_choice in ['N', 'NO']:
                                print("\n✅ Stopping bot as requested by user...")
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
                                print(f"⚠️  Invalid input. Continuing scan...\n")
                                time.sleep(1)
                        except (EOFError, KeyboardInterrupt):
                            print("\n\n✅ Bot stopped by user (Ctrl+C)")
                            logger.info("Manual batch approval mode stopped by user (KeyboardInterrupt)")
                            break
                    else:
                        # Trades are executing, wait a bit before checking again
                        time.sleep(5)
                else:
                    # Automatic mode: wait for cycle interval
                    scheduler_logger.debug(f"Waiting {cycle_interval_seconds}s until next cycle")
                    time.sleep(cycle_interval_seconds)
        
        except KeyboardInterrupt:
            logger.info("Trading bot stopped by user")
            if self.manual_approval_mode:
                print("\n✅ Bot stopped by user")
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
        
        # Stop continuous trailing stop monitor
        self.stop_continuous_trailing_stop()
        
        self.mt5_connector.shutdown()
        logger.info("Trading bot shutdown complete")

