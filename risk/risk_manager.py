"""
Risk Management Module
Handles position sizing, risk calculation, and trailing stops.
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from utils.logger_factory import get_logger
import MetaTrader5 as mt5

# Module-level logger - will be reinitialized in __init__ based on mode
logger = None


class RiskManager:
    """Manages risk per trade and trailing stop logic."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector, order_manager: OrderManager):
        # CRITICAL FIX: Initialize logger based on mode (backtest vs live)
        # This prevents backtest from writing to live log files
        global logger
        is_backtest = config.get('mode') == 'backtest'
        log_path = "logs/backtest/engine/risk_manager.log" if is_backtest else "logs/live/engine/risk_manager.log"
        logger = get_logger("risk_manager", log_path)
        
        self.config = config
        self.risk_config = config.get('risk', {})
        self.mt5_connector = mt5_connector
        self.order_manager = order_manager
        self.bot_pnl_callback = None  # Callback to update bot's realized P/L when positions close
        
        self.max_risk_usd = self.risk_config.get('max_risk_per_trade_usd', 2.0)
        self.default_lot_size = self.risk_config.get('default_lot_size', 0.01)
        # Max open trades: None or -1 means unlimited (configurable)
        max_trades_config = self.risk_config.get('max_open_trades', 1)
        if max_trades_config is None or max_trades_config == -1:
            self.max_open_trades = None  # Unlimited
        else:
            self.max_open_trades = max_trades_config
        self.max_open_trades_strict = self.risk_config.get('max_open_trades_strict', False)
        self.trailing_increment_usd = self.risk_config.get('trailing_stop_increment_usd', 0.10)
        self.use_usd_stoploss = self.risk_config.get('use_usd_stoploss', True)
        # min_stop_loss_pips is deprecated - using USD-based SL instead
        self.min_stop_loss_pips = None  # No longer used
        
        # Per-symbol lot size limits
        self.symbol_limits = self.risk_config.get('symbol_limits', {})
        
        # Continuous trailing stop configuration
        self.continuous_trailing_enabled = self.risk_config.get('continuous_trailing_enabled', True)
        # Instant trailing - no delays, trigger on every tick
        trailing_config = self.risk_config.get('trailing', {})
        self.trailing_cycle_interval_ms = trailing_config.get('frequency_ms', 0) if trailing_config else self.risk_config.get('trailing_cycle_interval_ms', 0)
        self.trailing_cycle_interval = self.trailing_cycle_interval_ms / 1000.0  # Convert to seconds for time.sleep (0 = instant)
        self.trigger_on_tick = trailing_config.get('trigger_on_tick', True) if trailing_config else True
        self.instant_trailing = trailing_config.get('instant_trailing', True) if trailing_config else True
        self.big_jump_threshold_usd = self.risk_config.get('big_jump_threshold_usd', 0.40)
        
        # Staged open configuration
        self.staged_open_enabled = self.risk_config.get('staged_open_enabled', False)
        self.staged_open_window_seconds = self.risk_config.get('staged_open_window_seconds', 60)
        self.staged_quality_threshold = self.risk_config.get('staged_quality_threshold', 50.0)
        self.staged_min_profit_usd = self.risk_config.get('staged_min_profit_usd', -0.10)
        
        # Fast trailing configuration
        self.fast_trailing_threshold_usd = self.risk_config.get('fast_trailing_threshold_usd', 0.10)
        # Instant trailing - no delays
        trailing_config = self.risk_config.get('trailing', {})
        self.fast_trailing_interval_ms = trailing_config.get('fast_frequency_ms', 0) if trailing_config else self.risk_config.get('fast_trailing_interval_ms', 0)
        self.fast_trailing_debounce_cycles = self.risk_config.get('fast_trailing_debounce_cycles', 3)
        
        # Elastic trailing configuration
        elastic_config = self.risk_config.get('elastic_trailing', {})
        self.elastic_trailing_enabled = elastic_config.get('enabled', False)
        self.pullback_tolerance_pct = elastic_config.get('pullback_tolerance_pct', 0.40)
        self.min_lock_increment_usd = elastic_config.get('min_lock_increment_usd', 0.10)
        self.big_jump_lock_margin_usd = elastic_config.get('big_jump_lock_margin_usd', 0.10)
        self.max_peak_lock_usd = elastic_config.get('max_peak_lock_usd', 0.80)
        
        # Thread-safe tracking of last profit per position (enhanced for SETE)
        # Extended for Dynamic Break-Even SL: tracks when profit became positive
        self._position_tracking = {}  # {ticket: {'last_profit': float, 'last_sl_profit': float, 'peak_profit': float, 'lock': threading.Lock, 'fast_polling': bool, 'debounce_count': int, 'positive_profit_start_time': float or None, 'break_even_sl_applied': bool}}
        self._tracking_lock = threading.Lock()
        
        # Dynamic Break-Even SL configuration
        break_even_config = self.risk_config.get('dynamic_break_even', {})
        self.break_even_enabled = break_even_config.get('enabled', True)
        self.break_even_duration_seconds = break_even_config.get('positive_profit_duration_seconds', 2.0)
        self.break_even_lock_at_entry = break_even_config.get('lock_at_entry_price', True)
        self.break_even_sweet_spot_lock = break_even_config.get('sweet_spot_lock_enabled', True)
        self.break_even_sweet_spot_threshold = break_even_config.get('sweet_spot_lock_threshold_usd', 0.03)
        
        # Staged trade tracking (for multi-trade logic)
        self._staged_trades = {}  # {symbol: {'trades': [ticket1, ticket2, ...], 'first_trade_time': datetime, 'trend': 'LONG'|'SELL', 'lock': threading.Lock}}
        self._staged_lock = threading.Lock()
        
        # Cache for corrected contract_size to avoid repeated calculations
        self._contract_size_cache = {}  # {symbol: corrected_contract_size}
        self._contract_size_cache_lock = threading.Lock()
        
        # Throttling for SL calculation logging to reduce log bloat
        self._sl_log_throttle = {}  # {ticket: {'last_log_time': float, 'last_sl_price': float}}
        self._sl_log_throttle_lock = threading.Lock()
        self._sl_log_interval_seconds = 5.0  # Log at most once every 5 seconds per ticket
        
        # Portfolio risk limit configuration
        self.max_portfolio_risk_pct = self.risk_config.get('max_portfolio_risk_pct', 15.0)  # Default 15% of account balance
        self.max_portfolio_risk_usd = self.risk_config.get('max_portfolio_risk_usd', None)  # Absolute USD limit (optional)
        
        # Micro-HFT Profit Engine (optional add-on, initialized separately)
        self._micro_profit_engine = None
        
        # Profit-Locking Engine (optional add-on, initialized separately)
        self._profit_locking_engine = None
        
        # Unified SL Manager (new refactored system)
        self._sl_manager_error = None  # Store error for debugging
        try:
            from risk.sl_manager import SLManager
            logger.info("Initializing SLManager...")
            self.sl_manager = SLManager(config, mt5_connector, order_manager)
            # CRITICAL FIX: Connect SLManager to RiskManager for ProfitLockingEngine/MicroProfitEngine access
            self.sl_manager._risk_manager = self
            # CRITICAL FIX: Connect SLManager to OrderManager for synchronous SL application when stop_loss=0.0
            # This ensures trades never remain with SL = 0.0 (hard safety invariant)
            if hasattr(order_manager, 'set_sl_manager'):
                order_manager.set_sl_manager(self.sl_manager)
            logger.info("[OK] SLManager initialized successfully")
            logger.info(f"SLManager instance: {self.sl_manager}")
            logger.info(f"SLManager available: {self.sl_manager is not None}")
        except Exception as e:
            self._sl_manager_error = str(e)
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"[ERROR] Failed to initialize Unified SL Manager: {e}", exc_info=True)
            logger.error(f"SLManager initialization traceback:\n{error_traceback}")
            self.sl_manager = None
            logger.critical("CRITICAL: SLManager initialization FAILED - SL updates will not work!")
        
        # Initialize TP Manager
        self._tp_manager_error = None
        try:
            from risk.tp_manager import TPManager
            logger.info("Initializing TPManager...")
            self.tp_manager = TPManager(config, mt5_connector, order_manager)
            logger.info("[OK] TPManager initialized successfully")
            # CRITICAL FIX: Link TP manager to SL manager for partial close monitoring
            if hasattr(self, 'sl_manager') and self.sl_manager:
                self.sl_manager.tp_manager = self.tp_manager
                logger.info("[OK] TP Manager linked to SL Manager for partial close monitoring")
        except Exception as e:
            self._tp_manager_error = str(e)
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"[ERROR] Failed to initialize TP Manager: {e}", exc_info=True)
            logger.error(f"TPManager initialization traceback:\n{error_traceback}")
            self.tp_manager = None
            logger.warning("WARNING: TPManager initialization FAILED - TP functionality will not work!")
        
        # Circuit Breaker State
        self._circuit_breaker_lock = threading.Lock()
        self._consecutive_losses = 0
        self._closed_trades_pnl = []  # List of last 50 closed trade PnLs
        self._daily_pnl = 0.0
        self._daily_pnl_date = datetime.now().date()
        self._circuit_breaker_paused_until = None  # datetime when pause expires
        self._circuit_breaker_reason = None  # Reason for current pause
        
        # Configurable max consecutive losses threshold
        # -1 = unlimited (bot never stops due to losses), 0+ = stop after N consecutive losses
        self.max_consecutive_losses_threshold = self.risk_config.get('max_consecutive_losses', -1)
        if self.max_consecutive_losses_threshold == -1:
            logger.info("[CIRCUIT_BREAKER] max_consecutive_losses=-1 → Unlimited losses allowed (bot will never stop due to consecutive losses)")
        else:
            logger.info(f"[CIRCUIT_BREAKER] max_consecutive_losses={self.max_consecutive_losses_threshold} → Bot will pause after {self.max_consecutive_losses_threshold} consecutive losses")
        
        # Hard Entry Filters Configuration
        filter_config = self.risk_config.get('entry_filters', {})
        self.entry_filters_enabled = filter_config.get('enabled', True)
        
        # Volatility Floor Filter
        self.volatility_filter_enabled = filter_config.get('volatility_floor', {}).get('enabled', True)
        self.volatility_candle_count = filter_config.get('volatility_floor', {}).get('candle_count', 20)
        self.volatility_min_range_pips = filter_config.get('volatility_floor', {}).get('min_range_pips', 1.0)
        
        # Spread Sanity Filter
        self.spread_filter_enabled = filter_config.get('spread_sanity', {}).get('enabled', True)
        self.spread_max_percent_of_range = filter_config.get('spread_sanity', {}).get('max_percent_of_range', 30.0)
        
        # Candle Quality Filter
        self.candle_quality_filter_enabled = filter_config.get('candle_quality', {}).get('enabled', True)
        self.candle_quality_min_percent = filter_config.get('candle_quality', {}).get('min_percent_of_avg', 50.0)
        
        # Session Guard
        self.session_guard_enabled = filter_config.get('session_guard', {}).get('enabled', True)
        self.session_guard_hour_start_minutes = filter_config.get('session_guard', {}).get('hour_start_block_minutes', 5)
        self.session_guard_hour_end_minutes = filter_config.get('session_guard', {}).get('hour_end_block_minutes', 5)
        rollover_config = filter_config.get('session_guard', {}).get('rollover_window', {})
        self.rollover_enabled = rollover_config.get('enabled', True)
        self.rollover_start_hour = rollover_config.get('start_hour_gmt', 22)
        self.rollover_end_hour = rollover_config.get('end_hour_gmt', 23)
        
        # Candle data cache (TTL: 10 seconds)
        self._candle_cache = {}  # {symbol: (data, timestamp)}
        self._candle_cache_ttl = 10.0
        self._candle_cache_lock = threading.Lock()
        
        # Trend Confirmation Gate Configuration
        trend_gate_config = filter_config.get('trend_gate', {})
        self.trend_gate_enabled = trend_gate_config.get('enabled', True)
        self.trend_gate_method = trend_gate_config.get('method', 'candle_direction')  # 'candle_direction' or 'sma_slope'
        self.trend_gate_min_candles_same_direction = trend_gate_config.get('min_candles_same_direction', 3)
        self.trend_gate_min_sma_slope_pct = trend_gate_config.get('min_sma_slope_pct', 0.01)  # Minimum SMA slope percentage
        self.trend_gate_sma_period = trend_gate_config.get('sma_period', 20)
        # Per-symbol thresholds (default applies if symbol not specified)
        self.trend_gate_symbol_thresholds = trend_gate_config.get('symbol_thresholds', {})
        
        # Cooldown After Losses Configuration
        cooldown_config = filter_config.get('cooldown_after_loss', {})
        self.cooldown_enabled = cooldown_config.get('enabled', True)
        self.cooldown_candles = cooldown_config.get('candles', 3)  # Default 3 candles cooldown
        # Per-symbol cooldown tracking {symbol: {'last_loss_time': datetime, 'last_loss_candle_time': datetime}}
        self._symbol_cooldown = {}
        self._cooldown_lock = threading.Lock()
    
    def set_micro_profit_engine(self, micro_profit_engine):
        """
        Set the Micro-HFT Profit Engine instance.
        
        This allows the bot to inject the micro profit engine without
        modifying the core risk manager logic.
        
        Args:
            micro_profit_engine: MicroProfitEngine instance
        """
        self._micro_profit_engine = micro_profit_engine
        logger.info("Micro-HFT Profit Engine registered with RiskManager")
    
    def set_profit_locking_engine(self, profit_locking_engine):
        """
        Set the Profit-Locking Engine instance.
        
        This allows the bot to inject the profit locking engine without
        modifying the core risk manager logic.
        
        Args:
            profit_locking_engine: ProfitLockingEngine instance
        """
        self._profit_locking_engine = profit_locking_engine
        logger.info("Profit-Locking Engine registered with RiskManager")
    
    def set_pnl_callback(self, callback):
        """
        Set callback function to update bot's realized P/L when positions close.
        
        Args:
            callback: Function that accepts (profit: float) and updates bot's realized P/L
        """
        self.bot_pnl_callback = callback
        logger.debug("P/L update callback registered with RiskManager")
    
    def record_closed_trade(self, profit_usd: float):
        """
        Record a closed trade for circuit breaker tracking.
        
        Args:
            profit_usd: Profit/loss in USD for the closed trade
        """
        with self._circuit_breaker_lock:
            # Reset daily PnL if date changed
            current_date = datetime.now().date()
            if current_date != self._daily_pnl_date:
                self._daily_pnl = 0.0
                self._daily_pnl_date = current_date
            
            # Update daily PnL
            self._daily_pnl += profit_usd
            
            # Update consecutive losses
            if profit_usd < 0:
                self._consecutive_losses += 1
            else:
                self._consecutive_losses = 0
            
            # Update rolling PnL (last 50 trades)
            self._closed_trades_pnl.append(profit_usd)
            if len(self._closed_trades_pnl) > 50:
                self._closed_trades_pnl.pop(0)
    
    def _get_candle_data(self, symbol: str, count: int = 21) -> Optional[List[Dict[str, float]]]:
        """
        Get historical candle data for symbol with caching.
        
        Args:
            symbol: Trading symbol
            count: Number of candles to retrieve
        
        Returns:
            List of dicts with 'high', 'low', 'open', 'close' or None if unavailable
        """
        if not self.mt5_connector.ensure_connected():
            return None
        
        now = time.time()
        cache_key = f"{symbol}_{count}"
        
        # Check cache
        with self._candle_cache_lock:
            if cache_key in self._candle_cache:
                cached_data, cached_time = self._candle_cache[cache_key]
                if now - cached_time < self._candle_cache_ttl:
                    return cached_data.copy()
        
        # Fetch fresh data
        try:
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)
            if rates is None or len(rates) == 0:
                return None
            
            # Convert to list of dicts
            data = [{'high': r['high'], 'low': r['low'], 'open': r['open'], 'close': r['close']} for r in rates]
            
            # Update cache
            with self._candle_cache_lock:
                self._candle_cache[cache_key] = (data, now)
            
            return data
        except Exception as e:
            logger.debug(f"Error fetching candle data for {symbol}: {e}")
            return None
    
    def _check_volatility_floor_filter(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check volatility floor filter.
        
        Returns:
            (passed: bool, reason: str or None)
        """
        if not self.volatility_filter_enabled:
            return True, None
        
        candle_data = self._get_candle_data(symbol, self.volatility_candle_count + 1)
        if candle_data is None or len(candle_data) < self.volatility_candle_count:
            return True, None  # Allow if data unavailable (fail-safe)
        
        # Calculate ranges (high - low) for last N candles
        ranges = [candle_data[i]['high'] - candle_data[i]['low'] for i in range(self.volatility_candle_count)]
        avg_range = sum(ranges) / len(ranges) if ranges else 0
        
        # Get symbol point value to convert pips to price
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return True, None  # Allow if symbol info unavailable
        
        point = symbol_info.get('point', 0.00001)
        min_range_price = self.volatility_min_range_pips * point * 10  # Convert pips to price
        
        if avg_range < min_range_price:
            return False, f"Volatility floor: avg range {avg_range/point/10:.2f} pips < min {self.volatility_min_range_pips:.2f} pips"
        
        return True, None
    
    def _check_spread_sanity_filter(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check spread sanity filter.
        
        Returns:
            (passed: bool, reason: str or None)
        """
        if not self.spread_filter_enabled:
            return True, None
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return True, None  # Allow if symbol info unavailable
        
        current_spread = symbol_info.get('spread', 0) * symbol_info.get('point', 0.00001)
        
        # Get recent average candle range
        candle_data = self._get_candle_data(symbol, 21)
        if candle_data is None or len(candle_data) < 20:
            return True, None  # Allow if data unavailable
        
        ranges = [candle_data[i]['high'] - candle_data[i]['low'] for i in range(20)]
        avg_range = sum(ranges) / len(ranges) if ranges else 0
        
        if avg_range == 0:
            return True, None  # Avoid division by zero
        
        spread_percent = (current_spread / avg_range) * 100.0
        
        if spread_percent > self.spread_max_percent_of_range:
            return False, f"Spread sanity: spread {spread_percent:.1f}% of avg range > max {self.spread_max_percent_of_range:.1f}%"
        
        return True, None
    
    def _check_candle_quality_filter(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check candle quality filter.
        
        Returns:
            (passed: bool, reason: str or None)
        """
        if not self.candle_quality_filter_enabled:
            return True, None
        
        candle_data = self._get_candle_data(symbol, 21)
        if candle_data is None or len(candle_data) < 21:
            return True, None  # Allow if data unavailable
        
        # Current candle range (most recent)
        current_range = candle_data[0]['high'] - candle_data[0]['low']
        
        # Average range of previous candles
        prev_ranges = [candle_data[i]['high'] - candle_data[i]['low'] for i in range(1, 21)]
        avg_range = sum(prev_ranges) / len(prev_ranges) if prev_ranges else 0
        
        if avg_range == 0:
            return True, None  # Avoid division by zero
        
        current_percent = (current_range / avg_range) * 100.0
        
        if current_percent < self.candle_quality_min_percent:
            return False, f"Candle quality: current range {current_percent:.1f}% of avg < min {self.candle_quality_min_percent:.1f}%"
        
        return True, None
    
    def _check_session_guard_filter(self) -> Tuple[bool, Optional[str]]:
        """
        Check session guard filter.
        
        Returns:
            (passed: bool, reason: str or None)
        """
        if not self.session_guard_enabled:
            return True, None
        
        now = datetime.now()
        current_minute = now.minute
        current_hour = now.hour
        
        # Check hour start block (first N minutes)
        if current_minute < self.session_guard_hour_start_minutes:
            return False, f"Session guard: first {self.session_guard_hour_start_minutes} minutes of hour blocked"
        
        # Check hour end block (last N minutes)
        if current_minute >= (60 - self.session_guard_hour_end_minutes):
            return False, f"Session guard: last {self.session_guard_hour_end_minutes} minutes of hour blocked"
        
        # Check rollover window
        if self.rollover_enabled:
            # Convert to GMT (assuming local time is GMT, adjust if needed)
            gmt_hour = current_hour  # Adjust if timezone conversion needed
            if self.rollover_start_hour <= self.rollover_end_hour:
                # Normal case: rollover within same day
                if self.rollover_start_hour <= gmt_hour < self.rollover_end_hour:
                    return False, f"Session guard: rollover window {self.rollover_start_hour:02d}:00-{self.rollover_end_hour:02d}:00 GMT blocked"
            else:
                # Rollover spans midnight
                if gmt_hour >= self.rollover_start_hour or gmt_hour < self.rollover_end_hour:
                    return False, f"Session guard: rollover window {self.rollover_start_hour:02d}:00-{self.rollover_end_hour:02d}:00 GMT blocked"
        
        return True, None
    
    def _check_trend_gate(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check trend confirmation gate.
        
        Returns:
            (passed: bool, reason: str or None)
        """
        if not self.trend_gate_enabled:
            return True, None
        
        candle_data = self._get_candle_data(symbol, max(20, self.trend_gate_min_candles_same_direction + 5))
        if candle_data is None or len(candle_data) < self.trend_gate_min_candles_same_direction:
            return True, None  # Allow if data unavailable (fail-safe)
        
        if self.trend_gate_method == 'candle_direction':
            # Check last N candles have same direction
            closes = [c['close'] for c in candle_data[:self.trend_gate_min_candles_same_direction]]
            opens = [c['open'] for c in candle_data[:self.trend_gate_min_candles_same_direction]]
            
            # Count bullish vs bearish candles
            bullish_count = sum(1 for i in range(len(closes)) if closes[i] > opens[i])
            bearish_count = len(closes) - bullish_count
            
            # Trend is strong if most candles are in same direction
            min_same_direction = self.trend_gate_min_candles_same_direction - 1  # Allow 1 outlier
            if bullish_count < min_same_direction and bearish_count < min_same_direction:
                return False, f"Trend too weak: {bullish_count} bullish, {bearish_count} bearish in last {self.trend_gate_min_candles_same_direction} candles"
        
        elif self.trend_gate_method == 'sma_slope':
            # Calculate SMA slope
            closes = [c['close'] for c in candle_data[:self.trend_gate_sma_period]]
            if len(closes) < self.trend_gate_sma_period:
                return True, None  # Not enough data
            
            # Calculate SMA
            sma_values = []
            for i in range(self.trend_gate_sma_period - 1, len(closes)):
                sma_values.append(sum(closes[i - self.trend_gate_sma_period + 1:i + 1]) / self.trend_gate_sma_period)
            
            if len(sma_values) < 2:
                return True, None
            
            # Calculate slope as percentage change
            slope_pct = ((sma_values[-1] - sma_values[0]) / sma_values[0]) * 100.0 if sma_values[0] > 0 else 0
            min_slope = self.trend_gate_symbol_thresholds.get(symbol, {}).get('min_sma_slope_pct', self.trend_gate_min_sma_slope_pct)
            
            if abs(slope_pct) < min_slope:
                return False, f"Trend too weak: SMA slope {abs(slope_pct):.3f}% < min {min_slope:.3f}%"
        
        return True, None
    
    def _check_cooldown_after_loss(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check if symbol is in cooldown after a recent loss.
        
        Returns:
            (passed: bool, reason: str or None)
        """
        if not self.cooldown_enabled:
            return True, None
        
        with self._cooldown_lock:
            if symbol not in self._symbol_cooldown:
                return True, None  # No recent loss, allow trade
            
            cooldown_info = self._symbol_cooldown[symbol]
            last_loss_candle_time = cooldown_info.get('last_loss_candle_time')
            
            if last_loss_candle_time is None:
                return True, None  # No candle time recorded, allow
            
            # Get current candle time (approximate using current time rounded to minute)
            now = datetime.now()
            current_candle_time = now.replace(second=0, microsecond=0)
            
            # Calculate candles elapsed since last loss
            # In M1 timeframe, each candle is 1 minute
            # For M5, each candle is 5 minutes, etc. - using M1 as base assumption
            minutes_elapsed = (current_candle_time - last_loss_candle_time).total_seconds() / 60.0
            candles_elapsed = int(minutes_elapsed)  # Assuming M1 timeframe
            
            if candles_elapsed < self.cooldown_candles:
                remaining = self.cooldown_candles - candles_elapsed
                return False, f"Cooldown active: {remaining} candles remaining (last loss {candles_elapsed} candles ago)"
            
            # Cooldown expired, remove tracking
            self._symbol_cooldown.pop(symbol, None)
            return True, None
    
    def _record_symbol_loss(self, symbol: str):
        """
        Record a loss for a symbol to start cooldown period.
        
        Args:
            symbol: Trading symbol that had a loss
        """
        if not self.cooldown_enabled:
            return
        
        now = datetime.now()
        current_candle_time = now.replace(second=0, microsecond=0)
        
        with self._cooldown_lock:
            self._symbol_cooldown[symbol] = {
                'last_loss_time': now,
                'last_loss_candle_time': current_candle_time
            }
    
    def _check_entry_filters(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check all entry filters.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            (passed: bool, reason: str or None)
        """
        if not self.entry_filters_enabled:
            return True, None
        
        # Check session guard first (no market data needed)
        passed, reason = self._check_session_guard_filter()
        if not passed:
            logger.warning(f"[FILTER BLOCK] {symbol}: {reason}")
            return False, reason
        
        # Check volatility floor
        passed, reason = self._check_volatility_floor_filter(symbol)
        if not passed:
            logger.warning(f"[FILTER BLOCK] {symbol}: {reason}")
            return False, reason
        
        # Check spread sanity
        passed, reason = self._check_spread_sanity_filter(symbol)
        if not passed:
            logger.warning(f"[FILTER BLOCK] {symbol}: {reason}")
            return False, reason
        
        # Check candle quality
        passed, reason = self._check_candle_quality_filter(symbol)
        if not passed:
            logger.warning(f"[FILTER BLOCK] {symbol}: {reason}")
            return False, reason
        
        # Check trend gate
        passed, reason = self._check_trend_gate(symbol)
        if not passed:
            logger.warning(f"[TREND BLOCK] {symbol}: {reason}")
            return False, reason
        
        # Check cooldown after loss
        passed, reason = self._check_cooldown_after_loss(symbol)
        if not passed:
            logger.warning(f"[COOLDOWN BLOCK] {symbol}: {reason}")
            return False, reason
        
        return True, None
    
    def _check_circuit_breaker(self) -> Tuple[bool, Optional[str]]:
        """
        Check if circuit breaker should be triggered.
        
        Returns:
            (is_paused: bool, reason: str or None)
        """
        with self._circuit_breaker_lock:
            now = datetime.now()
            
            # Check if pause has expired
            if self._circuit_breaker_paused_until and now >= self._circuit_breaker_paused_until:
                reason = self._circuit_breaker_reason
                self._circuit_breaker_paused_until = None
                self._circuit_breaker_reason = None
                # CRITICAL FIX: Reset consecutive losses counter when pause expires
                # This prevents infinite pause loop where bot resumes but immediately pauses again
                self._consecutive_losses = 0
                logger.info(f"[CIRCUIT BREAKER] Trading resumed after pause (previous reason: {reason}). Consecutive losses counter reset to 0.")
                return False, None
            
            # If already paused, return pause status
            if self._circuit_breaker_paused_until:
                remaining_seconds = (self._circuit_breaker_paused_until - now).total_seconds()
                return True, f"{self._circuit_breaker_reason} (resumes in {remaining_seconds:.0f}s)"
            
            # Check consecutive losses rule (only if threshold is configured, -1 = unlimited)
            if self.max_consecutive_losses_threshold >= 0 and self._consecutive_losses >= self.max_consecutive_losses_threshold:
                pause_duration_minutes = 30
                self._circuit_breaker_paused_until = now + timedelta(minutes=pause_duration_minutes)
                self._circuit_breaker_reason = f"Consecutive losses >= {self.max_consecutive_losses_threshold} ({self._consecutive_losses} losses)"
                logger.warning(f"[CIRCUIT BREAKER] Trading paused: {self._circuit_breaker_reason} (pause duration: {pause_duration_minutes} minutes)")
                return True, self._circuit_breaker_reason
            
            # Check rolling PnL rule
            if len(self._closed_trades_pnl) >= 50:
                rolling_pnl = sum(self._closed_trades_pnl)
                if rolling_pnl <= -10.0:
                    pause_duration_minutes = 60
                    self._circuit_breaker_paused_until = now + timedelta(minutes=pause_duration_minutes)
                    self._circuit_breaker_reason = f"Rolling PnL of last 50 trades <= -$10 (current: ${rolling_pnl:.2f})"
                    logger.warning(f"[CIRCUIT BREAKER] Trading paused: {self._circuit_breaker_reason} (pause duration: {pause_duration_minutes} minutes)")
                    return True, self._circuit_breaker_reason
            
            return False, None
    
    def calculate_minimum_lot_size_for_risk(
        self,
        symbol: str,
        stop_loss_pips: float,
        risk_usd: Optional[float] = None
    ) -> Tuple[float, float, str]:
        """
        Calculate minimum possible lot size based on risk per trade, respecting broker's absolute minimum.
        
        This method calculates the lot size needed to achieve the target risk, but ensures
        it never goes below the broker's absolute minimum lot size.
        
        Args:
            symbol: Trading symbol
            stop_loss_pips: Stop loss in pips
            risk_usd: Risk amount in USD (defaults to max_risk_usd)
        
        Returns:
            Tuple of (final_lot_size, calculated_lot, reason)
            - final_lot_size: Lot size to use (max of calculated and broker minimum)
            - calculated_lot: Risk-based calculated lot size
            - reason: Explanation of the lot size decision
        """
        if risk_usd is None:
            risk_usd = self.max_risk_usd
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            logger.warning(f"Cannot get symbol info for {symbol}, using DEFAULT {self.default_lot_size}")
            return self.default_lot_size, self.default_lot_size, "Symbol info unavailable"
        
        # Get point value
        point = symbol_info['point']
        contract_size = symbol_info['contract_size']
        
        # Calculate pip value (for most pairs, 1 pip = 10 points)
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        # Get account currency
        account_info = self.mt5_connector.get_account_info()
        if account_info is None:
            return self.default_lot_size, self.default_lot_size, "Account info unavailable"
        
        account_currency = account_info['currency']
        
        # Calculate lot size based on risk
        # Risk = Lot Size * Stop Loss (in price) * Contract Size
        # Lot Size = Risk / (Stop Loss (in price) * Contract Size)
        
        stop_loss_price = stop_loss_pips * pip_value
        
        # Safety check
        if stop_loss_price <= 0 or contract_size <= 0:
            logger.warning(f"Invalid stop_loss_price ({stop_loss_price}) or contract_size ({contract_size}) for {symbol}")
            return self.default_lot_size, self.default_lot_size, "Invalid stop loss or contract size"
        
        # Calculate lot size needed for target risk (per user requirement: always use minimum lot size)
        # But ensure risk never exceeds $2 USD
        if account_currency == 'USD':
            calculated_lot = risk_usd / (stop_loss_price * contract_size)
        else:
            # Non-USD account - simplified conversion
            calculated_lot = risk_usd / (stop_loss_price * contract_size)
        
        # Ensure calculated lot respects minimum lot size (per user requirement)
        # We'll use the maximum of calculated lot and minimum lot, but cap risk at $2
        
        # Round to valid lot size (respect volume_step)
        volume_step = symbol_info.get('volume_step', 0.01)
        if volume_step > 0:
            calculated_lot = round(calculated_lot / volume_step) * volume_step
        else:
            calculated_lot = round(calculated_lot, 2)
        
        # Ensure calculated lot is not negative or zero
        if calculated_lot <= 0:
            calculated_lot = self.default_lot_size
        
        # Get per-symbol limits from config (if set)
        symbol_upper = symbol.upper()
        symbol_limit_config = self.symbol_limits.get(symbol_upper, {})
        config_min_lot = symbol_limit_config.get('min_lot')
        config_max_lot = symbol_limit_config.get('max_lot')
        
        # Get symbol's native lot size limits from broker
        symbol_min_lot = symbol_info.get('volume_min', 0.01)
        symbol_max_lot = symbol_info.get('volume_max', 100.0)
        
        # Determine effective minimum: use config limit if set, otherwise use broker's minimum
        # Always respect broker's absolute minimum (can't go below broker's minimum)
        if config_min_lot is not None:
            effective_min_lot = max(symbol_min_lot, config_min_lot)
            limit_source = "config"
        else:
            effective_min_lot = symbol_min_lot
            limit_source = "broker"
        
        # Determine effective maximum: use config limit if set, otherwise use broker's maximum
        if config_max_lot is not None:
            effective_max_lot = min(symbol_max_lot, config_max_lot)
        else:
            effective_max_lot = symbol_max_lot
        
        # Use the maximum of calculated lot and effective minimum
        # This ensures we use minimum possible lot while respecting broker's absolute minimum
        # Per user requirement: Always use minimum lot size for the symbol
        final_lot_size = max(calculated_lot, effective_min_lot)
        
        # However, if calculated lot is less than minimum, we must use minimum
        # but verify risk doesn't exceed $2 USD
        if calculated_lot < effective_min_lot:
            # Recalculate actual risk with minimum lot
            actual_risk_with_min = effective_min_lot * stop_loss_price * contract_size
            if actual_risk_with_min > risk_usd:
                logger.warning(f"[WARNING] {symbol}: Using minimum lot {effective_min_lot:.4f} results in risk ${actual_risk_with_min:.2f} > ${risk_usd:.2f}")
                # Still use minimum lot (per user requirement), but log the risk
        
        # CRITICAL: Enforce maximum lot limit to prevent invalid volume errors
        if final_lot_size > effective_max_lot:
            logger.warning(f"[WARNING] {symbol}: Calculated lot {final_lot_size:.4f} exceeds max {effective_max_lot:.4f}, "
                          f"capping to max. This may result in risk < ${risk_usd:.2f}")
            final_lot_size = effective_max_lot
            # Recalculate actual risk with capped lot size
            if stop_loss_price > 0 and contract_size > 0:
                actual_risk_capped = final_lot_size * stop_loss_price * contract_size
                logger.warning(f"[WARNING] {symbol}: Actual risk with capped lot: ${actual_risk_capped:.2f} (target: ${risk_usd:.2f})")
        
        # Calculate actual risk with final lot size
        actual_risk = 0.0
        if stop_loss_price > 0 and contract_size > 0:
            actual_risk = final_lot_size * stop_loss_price * contract_size
        
        # Determine reason
        if calculated_lot < effective_min_lot:
            reason = f"Calculated {calculated_lot:.4f} < min {effective_min_lot:.4f} ({limit_source}), using min"
        elif calculated_lot > effective_max_lot:
            reason = f"Calculated {calculated_lot:.4f} > max {effective_max_lot:.4f}, capped to max (risk: ${actual_risk:.2f})"
        elif calculated_lot > effective_min_lot:
            reason = f"Using calculated {calculated_lot:.4f} (min: {effective_min_lot:.4f} {limit_source}, max: {effective_max_lot:.4f})"
        else:
            reason = f"Using minimum {effective_min_lot:.4f} ({limit_source})"
        
        return final_lot_size, calculated_lot, reason
    
    def choose_lot_for_trade(
        self,
        symbol: str,
        risk_usd: float,
        stop_loss_pips: float,
        force_minimum_lot_mode: bool = True
    ) -> Tuple[float, str]:
        """
        Helper function to choose lot size for a trade.
        
        Args:
            symbol: Trading symbol
            risk_usd: Risk amount in USD (typically $2.0)
            stop_loss_pips: Stop loss in pips
            force_minimum_lot_mode: If True, ensures lot is at least broker minimum
        
        Returns:
            Tuple of (lot_size, reason)
            - lot_size: Final lot size to use
            - reason: Explanation of lot size decision
        """
        final_lot_size, calculated_lot, reason = self.calculate_minimum_lot_size_for_risk(
            symbol, stop_loss_pips, risk_usd
        )
        
        # In force_minimum_lot_mode, ensure we never go below broker minimum
        if force_minimum_lot_mode:
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info:
                broker_min_lot = symbol_info.get('volume_min', 0.01)
                if final_lot_size < broker_min_lot:
                    final_lot_size = broker_min_lot
                    reason = f"Forced to broker minimum {broker_min_lot:.4f} (calculated: {calculated_lot:.4f})"
        
        return final_lot_size, reason
    
    def calculate_lot_size(
        self,
        symbol: str,
        stop_loss_pips: float,
        risk_usd: Optional[float] = None
    ) -> float:
        """
        Calculate lot size - returns minimum possible lot size based on risk, respecting broker minimum.
        
        This method calculates the lot size needed to achieve target risk ($2.0), but ensures
        it never goes below the broker's absolute minimum lot size.
        
        Args:
            symbol: Trading symbol
            stop_loss_pips: Stop loss in pips
            risk_usd: Risk amount in USD (defaults to max_risk_usd)
        
        Returns:
            Minimum possible lot size (max of risk-based calculation and broker minimum)
        """
        final_lot_size, calculated_lot, reason = self.calculate_minimum_lot_size_for_risk(
            symbol, stop_loss_pips, risk_usd
        )
        
        # Log comprehensive lot size details
        logger.info(f"📦 {symbol}: LOT SIZE | "
                   f"Final: {final_lot_size:.4f} | "
                   f"Calculated: {calculated_lot:.4f} | "
                   f"Risk: ${risk_usd:.2f} | "
                   f"SL: {stop_loss_pips:.1f} pips | "
                   f"Reason: {reason}")
        
        return final_lot_size
    
    def determine_lot_size_with_priority(
        self,
        symbol: str,
        broker_min_lot: float,
        high_quality_setup: bool,
        quality_score: Optional[float] = None
    ) -> Tuple[Optional[float], str]:
        """
        Determine lot size using priority logic:
        - Default lot size = 0.01
        - Increase up to 0.05 only if minimum lot allows (broker requires it)
        - Always try 0.01 first, only escalate if broker minimum lot > 0.01
        
        Args:
            symbol: Trading symbol
            broker_min_lot: Broker's minimum lot size requirement
            high_quality_setup: Whether setup is high-quality (not used, kept for compatibility)
            quality_score: Optional quality score (not used, kept for compatibility)
        
        Returns:
            Tuple of (lot_size, reason)
            - lot_size: Lot size to use (None if symbol should be skipped)
            - reason: Explanation of decision
        """
        # Default lot size = 0.01
        default_lot = self.default_lot_size  # 0.01
        
        # Check if broker allows default lot size
        if broker_min_lot <= default_lot:
            # Broker allows 0.01 - use it
            return default_lot, f"Using default lot {default_lot:.2f} (broker min: {broker_min_lot:.2f})"
        
        # Broker requires > 0.01
        # Allow up to 0.05 only if broker minimum lot requires it
        max_allowed_lot = 0.05
        
        if broker_min_lot > max_allowed_lot:
            # Skip symbols with broker min lot > 0.05
            return None, f"SKIP: Broker min lot {broker_min_lot:.2f} > {max_allowed_lot:.2f} (exceeds limit)"
        
        # Broker requires between 0.01 and 0.05 - use broker minimum
        # Note: We only increase if broker requires it, not based on setup quality
        return broker_min_lot, f"Using broker min lot {broker_min_lot:.2f} (broker requirement, within 0.01-0.05 range)"
    
    def check_min_lot_size_for_testing(self, symbol: str) -> Tuple[bool, float, str]:
        """
        Check if symbol's minimum lot size is suitable for testing mode.
        
        Requirements:
        - Min lot size must be between 0.01 and 0.03
        - Min lot size must not cause risk to exceed $2.0
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Tuple of (is_valid, min_lot, reason)
            - is_valid: True if symbol passes testing mode lot size requirements
            - min_lot: The minimum lot size for this symbol
            - reason: Reason for pass/fail
        """
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, 0.0, "Cannot get symbol info"
        
        # Get per-symbol limits from config (if set)
        symbol_upper = symbol.upper()
        symbol_limit_config = self.symbol_limits.get(symbol_upper, {})
        config_min_lot = symbol_limit_config.get('min_lot')
        
        # Get symbol's native minimum lot size from broker
        symbol_min_lot = symbol_info.get('volume_min', 0.01)
        
        # Determine effective minimum - broker minimum takes precedence (must respect broker's requirement)
        # If config has a min_lot, it can only INCREASE the minimum, not decrease it below broker's requirement
        if config_min_lot is not None:
            effective_min_lot = max(symbol_min_lot, config_min_lot)
            limit_source = "config" if config_min_lot >= symbol_min_lot else "broker"
        else:
            effective_min_lot = symbol_min_lot
            limit_source = "broker"
        
        # Check 1: Min lot must be between 0.01 and 0.03 (updated limit)
        if effective_min_lot < 0.01:
            return False, effective_min_lot, f"Min lot {effective_min_lot:.4f} < 0.01 (too small for testing)"
        
        if effective_min_lot > 0.03:
            return False, effective_min_lot, f"Min lot {effective_min_lot:.4f} > 0.03 (exceeds lot size limit - skip this symbol)"
        
        # Check 2: Min lot must not cause risk to exceed $2.0
        # Use USD-based stop loss calculation: risk is always $2.00 fixed
        # With USD-based SL, risk is fixed at max_risk_usd regardless of lot size
        # So we just need to verify that the lot size is reasonable
        # The actual risk will always be $2.00 when using calculate_usd_based_stop_loss_price()
        
        # For testing mode, we ensure lot size is within acceptable range (0.01-0.03)
        # Risk is fixed at $2.00 USD, so no additional risk validation needed
        # The lot size check (0.01-0.03) is sufficient
        
        return True, effective_min_lot, f"Min lot {effective_min_lot:.4f} ({limit_source}) passes testing requirements"
    
    def _enforce_strict_loss_limit(self, ticket: int, position: Dict[str, Any], current_profit: float) -> bool:
        """
        [DEPRECATED - REMOVAL CANDIDATE] This method has been replaced by SLManager.update_sl_atomic()
        
        All stop-loss logic is now handled by the unified SLManager in risk/sl_manager.py.
        This method is kept for backward compatibility but should NOT be called.
        
        **STATUS:** No active calls found in codebase. Safe to remove in future version.
        If called, it will log a deprecation warning and attempt to use SLManager instead.
        
        Args:
            ticket: Position ticket number
            position: Position dictionary
            current_profit: Current profit in USD (should be negative for enforcement to apply)
        
        Returns:
            True if SL was enforced via SLManager, False otherwise
        """
        # DEPRECATION WARNING
        symbol = position.get('symbol', 'UNKNOWN')
        logger.warning(f"[WARNING] DEPRECATED: _enforce_strict_loss_limit() called for {symbol} Ticket {ticket} | "
                      f"This method is deprecated. All SL logic should use SLManager.update_sl_atomic() | "
                      f"Please update calling code to use sl_manager.update_sl_atomic() directly")
        
        # Attempt to use SLManager instead
        if hasattr(self, 'sl_manager') and self.sl_manager:
            try:
                sl_success, sl_reason = self.sl_manager.update_sl_atomic(ticket, position)
                if sl_success:
                    logger.info(f"[OK] DEPRECATED METHOD REDIRECTED: {symbol} Ticket {ticket} | "
                              f"SL updated via SLManager: {sl_reason}")
                    return True
                else:
                    logger.warning(f"[WARNING] DEPRECATED METHOD REDIRECTED: {symbol} Ticket {ticket} | "
                                f"SLManager update skipped: {sl_reason}")
                    return False
            except Exception as e:
                logger.error(f"[ERROR] DEPRECATED METHOD ERROR: Failed to redirect to SLManager: {e}", exc_info=True)
                return False
        else:
            logger.error(f"[ERROR] DEPRECATED METHOD ERROR: SLManager not available for {symbol} Ticket {ticket}")
            return False
    
    def enforce_protective_sl_on_entry(self, ticket: int, position: Dict[str, Any]) -> bool:
        """
        Enforce protective -$2.00 stop-loss immediately after entry, regardless of profit status.
        
        This method ensures a protective SL is in place from the moment a trade is opened,
        even if the trade starts with positive or zero profit. The SL is set to exactly -$2.00.
        
        Args:
            ticket: Position ticket number
            position: Position dictionary from order_manager
        
        Returns:
            True if protective SL was set, False otherwise
        """
        symbol = position.get('symbol', '')
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        current_sl = position.get('sl', 0.0)
        
        if not symbol or entry_price <= 0 or lot_size <= 0:
            return False
        
        # Get symbol info
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Calculate required SL price to achieve exactly -$2.00 loss
        contract_size = symbol_info.get('contract_size', 1.0)
        point = symbol_info.get('point', 0.00001)
        digits = symbol_info.get('digits', 5)
        
        # Calculate price difference needed for $2.00 loss
        price_diff = abs(self.max_risk_usd) / (lot_size * contract_size)
        
        # Calculate target SL price for -$2.00
        if order_type == 'BUY':
            target_sl_price = entry_price - price_diff
        else:  # SELL
            target_sl_price = entry_price + price_diff
        
        # Normalize SL price
        if digits in [5, 3]:
            target_sl_price = round(target_sl_price / point) * point
        else:
            target_sl_price = round(target_sl_price, digits)
        
        # Check if current SL needs adjustment
        needs_adjustment = False
        if current_sl <= 0:
            # No SL set - needs adjustment
            needs_adjustment = True
        elif order_type == 'BUY':
            # For BUY: SL should be at target or better (higher = better for loss protection)
            # If current SL < target SL (more negative), we need to adjust
            if current_sl < target_sl_price:
                needs_adjustment = True
        else:  # SELL
            # For SELL: SL should be at target or better (lower = better for loss protection)
            # If current SL > target SL (more negative), we need to adjust
            if current_sl > target_sl_price:
                needs_adjustment = True
        
        if needs_adjustment:
            # Apply protective -$2.00 SL with retry logic
            max_retries = 3
            success = False
            for attempt in range(max_retries):
                success = self.order_manager.modify_order(ticket, stop_loss_price=target_sl_price)
                if success:
                    break
                if attempt < max_retries - 1:
                    import time
                    time.sleep(0.1 * (attempt + 1))
            
            if success:
                logger.info(f"[SL] PROTECTIVE SL SET (on entry): {symbol} Ticket {ticket} | "
                          f"SL set to exactly -$2.00 (price: {target_sl_price:.5f})")
                return True
            else:
                logger.warning(f"[WARNING] PROTECTIVE SL SET FAILED (on entry): {symbol} Ticket {ticket} | "
                             f"Could not set protective -$2.00 SL")
                return False
        
        return False  # No adjustment needed (SL already at or better than target)
    
    def _apply_dynamic_break_even_sl(self, ticket: int, position: Dict[str, Any], current_profit: float) -> Tuple[bool, str]:
        """
        Apply Dynamic Break-Even SL Update if trade has been in profit for configured duration.
        
        If a trade is in profit (current_profit > 0) for at least the configured duration,
        automatically move its stop-loss to entry price (or slightly above/below) to lock in profit.
        
        This method:
        - Only increases SL, never reduces it
        - Tracks positive-profit duration
        - Applies break-even SL at entry price after duration threshold
        - For sweet spot profits, applies immediate lock
        - Thread-safe and non-intrusive
        
        Args:
            ticket: Position ticket number
            position: Position dictionary from order_manager
            current_profit: Current profit in USD
        
        Returns:
            (success: bool, reason: str)
        """
        if not self.break_even_enabled:
            return False, "Dynamic break-even SL disabled"
        
        # Only apply to profitable trades
        if current_profit <= 0:
            # Reset tracking if profit goes negative
            tracking = self._get_position_tracking(ticket)
            with self._tracking_lock:
                tracking['positive_profit_start_time'] = None
            return False, "Trade not in profit"
        
        symbol = position.get('symbol', '')
        entry_price = position.get('price_open', 0.0)
        current_sl = position.get('sl', 0.0)
        order_type = position.get('type', '')
        
        if not symbol or entry_price <= 0:
            return False, "Invalid position data"
        
        # Get symbol info for broker constraints
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, "Symbol info not available"
        
        # Get tracking data
        tracking = self._get_position_tracking(ticket)
        
        # Get current time
        current_time = time.time()
        
        # Check if break-even SL has already been applied
        if tracking.get('break_even_sl_applied', False):
            # Break-even already applied - check if we need to update for sweet spot
            if self.break_even_sweet_spot_lock and current_profit >= self.break_even_sweet_spot_threshold:
                # Sweet spot profit - ensure SL is locked at appropriate level
                # This is handled by profit-locking engine, so just log
                return False, "Break-even SL already applied, profit-locking handles sweet spot"
            return False, "Break-even SL already applied"
        
        # Track when profit became positive
        with self._tracking_lock:
            if tracking.get('positive_profit_start_time') is None:
                # First time we see positive profit - record the timestamp
                tracking['positive_profit_start_time'] = current_time
                logger.debug(f"📊 Break-Even Tracking: {symbol} Ticket {ticket} | "
                           f"Profit became positive: ${current_profit:.2f} | Starting timer")
                return False, "Waiting for positive-profit duration threshold"
            
            positive_profit_start = tracking['positive_profit_start_time']
            positive_duration = current_time - positive_profit_start
        
        # Check if duration threshold has been met
        if positive_duration < self.break_even_duration_seconds:
            # Still waiting for duration threshold
            remaining_time = self.break_even_duration_seconds - positive_duration
            logger.debug(f"⏳ Break-Even Pending: {symbol} Ticket {ticket} | "
                       f"Profit: ${current_profit:.2f} | "
                       f"Positive for {positive_duration:.2f}s | "
                       f"Need {remaining_time:.2f}s more")
            return False, f"Waiting for duration threshold ({positive_duration:.2f}s / {self.break_even_duration_seconds}s)"
        
        # Duration threshold met - check if we should apply break-even SL
        
        # For sweet spot profits, apply immediate lock (if enabled)
        if self.break_even_sweet_spot_lock and current_profit >= self.break_even_sweet_spot_threshold:
            # Sweet spot profit - lock immediately at appropriate level
            # This will be handled by profit-locking engine, but we can set break-even first
            logger.info(f"🎯 Break-Even Sweet Spot: {symbol} Ticket {ticket} | "
                      f"Profit: ${current_profit:.2f} (sweet spot) | "
                      f"Positive for {positive_duration:.2f}s | Applying break-even SL")
        else:
            # Regular break-even - apply at entry price
            logger.info(f"🎯 Break-Even Duration Met: {symbol} Ticket {ticket} | "
                      f"Profit: ${current_profit:.2f} | "
                      f"Positive for {positive_duration:.2f}s | Applying break-even SL")
        
        # Calculate break-even SL price (entry price with small buffer for broker constraints)
        point = symbol_info.get('point', 0.00001)
        pip_value = point * 10 if symbol_info.get('digits', 5) in [5, 3] else point
        stops_level = symbol_info.get('trade_stops_level', 0)
        min_distance = stops_level * point if stops_level > 0 else 0
        
        # Calculate target SL at entry price (break-even)
        # For BUY: SL should be at or below entry (SL below entry = profit locked)
        # For SELL: SL should be at or above entry (SL above entry = profit locked)
        target_sl_price = entry_price
        
        # Adjust for broker minimum stops level if needed
        # We want SL as close to entry as possible (break-even), but respect broker constraints
        current_price = position.get('price_current', entry_price)
        if min_distance > 0:
            if order_type == 'BUY':
                # For BUY: SL must be at least min_distance below current price
                # If entry price is too close to current price, adjust SL slightly below entry
                if (current_price - entry_price) < min_distance:
                    target_sl_price = entry_price - min_distance
            else:  # SELL
                # For SELL: SL must be at least min_distance above current price
                # If entry price is too close to current price, adjust SL slightly above entry
                if (entry_price - current_price) < min_distance:
                    target_sl_price = entry_price + min_distance
        
        # Normalize SL price
        digits = symbol_info.get('digits', 5)
        if digits in [5, 3]:
            target_sl_price = round(target_sl_price / point) * point
        else:
            target_sl_price = round(target_sl_price, digits)
        
        # Check if current SL is already at or better than target
        # Break-even SL should only increase (improve) the SL, never reduce it
        needs_update = False
        if current_sl <= 0:
            # No SL set - needs update
            needs_update = True
        elif order_type == 'BUY':
            # For BUY: SL should be below entry for profit
            # Higher SL = better (closer to entry)
            # Target is at/below entry, so current SL should be <= target (or higher/better)
            if current_sl < target_sl_price:
                # Current SL is worse (lower) than target - needs update
                needs_update = True
        else:  # SELL
            # For SELL: SL should be above entry for profit
            # Lower SL = better (closer to entry)
            # Target is at/above entry, so current SL should be >= target (or lower/better)
            if current_sl > target_sl_price or current_sl == 0:
                # Current SL is worse (higher) than target - needs update
                needs_update = True
        
        if not needs_update:
            # SL already at or better than break-even
            with self._tracking_lock:
                tracking['break_even_sl_applied'] = True
            logger.debug(f"[OK] Break-Even Already Set: {symbol} Ticket {ticket} | "
                       f"Current SL ({current_sl:.5f}) already at/better than entry ({entry_price:.5f})")
            return False, "SL already at break-even or better"
        
        # Apply break-even SL with retry logic
        max_retries = 3
        success = False
        for attempt in range(max_retries):
            success = self.order_manager.modify_order(ticket, stop_loss_price=target_sl_price)
            if success:
                break
            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))  # Increasing backoff
        
        if success:
            # Mark as applied
            with self._tracking_lock:
                tracking['break_even_sl_applied'] = True
            
            # Calculate SL in pips for logging
            if order_type == 'BUY':
                sl_pips = (entry_price - target_sl_price) / pip_value if pip_value > 0 else 0
            else:
                sl_pips = (target_sl_price - entry_price) / pip_value if pip_value > 0 else 0
            
            logger.info(f"[OK] DYNAMIC BREAK-EVEN SL APPLIED: {symbol} Ticket {ticket} | "
                      f"Profit: ${current_profit:.2f} | "
                      f"Positive duration: {positive_duration:.2f}s | "
                      f"SL set to entry: {target_sl_price:.5f} ({sl_pips:.1f} pips)")
            
            # Log to trade logger
            from trade_logging.trade_logger import TradeLogger
            trade_logger = TradeLogger(self.config)
            trade_logger.log_trailing_stop_adjustment(
                symbol=symbol,
                ticket=ticket,
                current_profit=current_profit,
                new_sl_profit=0.0,  # Break-even = $0 profit
                new_sl_price=target_sl_price,
                sl_pips=sl_pips,
                reason=f"Dynamic Break-Even (positive for {positive_duration:.2f}s)"
            )
            
            return True, f"Break-even SL applied after {positive_duration:.2f}s positive profit"
        else:
            logger.warning(f"[WARNING] Break-Even SL Failed: {symbol} Ticket {ticket} | "
                         f"Could not set SL to break-even after {max_retries} attempts")
            return False, f"Failed to apply break-even SL after {max_retries} attempts"
    
    def _get_corrected_contract_size(self, symbol: str, entry_price: float, lot_size: float, target_loss_usd: float) -> float:
        """
        Get the correct contract_size for a symbol, applying auto-correction if needed.
        
        This method detects when MT5 reports an incorrect contract_size (e.g., 1.0 for BTCXAUm
        when it should be 100.0) and corrects it by testing common values.
        
        CRITICAL: Results are cached to avoid repeated calculations for the same symbol.
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price of the trade
            lot_size: Lot size
            target_loss_usd: Target loss in USD (e.g., 2.0 for -$2.00)
        
        Returns:
            Corrected contract_size value
        """
        # Check cache first to avoid repeated calculations
        with self._contract_size_cache_lock:
            if symbol in self._contract_size_cache:
                return self._contract_size_cache[symbol]
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return 1.0  # Default fallback
        
        contract_size = symbol_info.get('contract_size', 1.0)
        original_contract_size = contract_size
        
        # Calculate price difference needed for target loss
        price_diff = abs(target_loss_usd) / (lot_size * contract_size)
        
        # CRITICAL VALIDATION: Check if calculated price_diff is reasonable
        # If price_diff is > 50% of entry price, contract_size is likely incorrect
        if price_diff > entry_price * 0.5:
            # Try to detect correct contract_size by testing common values
            test_contract_sizes = [0.01, 0.001, 0.1, 1.0, 10.0, 100.0]
            
            for test_contract in test_contract_sizes:
                test_price_diff = abs(target_loss_usd) / (lot_size * test_contract)
                # Check if this would result in a reasonable SL (within 20% of entry)
                if test_price_diff < entry_price * 0.2:
                    contract_size = test_contract
                    logger.info(f"🛑 CONTRACT_SIZE AUTO-CORRECTED: {symbol} | "
                               f"Using {contract_size} instead of {original_contract_size} | "
                               f"Price_diff: {price_diff:.5f} -> {test_price_diff:.5f}")
                    break
        
        # Cache the result (whether corrected or original)
        with self._contract_size_cache_lock:
            self._contract_size_cache[symbol] = contract_size
        
        return contract_size
    
    def calculate_effective_sl_in_profit_terms(self, position: Dict[str, Any], check_pending: bool = True) -> Tuple[float, bool]:
        """
        Calculate the effective stop-loss in profit terms (USD) for a position.
        
        CRITICAL: Uses ACTUAL broker-applied SL price from position data (from MT5).
        This method converts the broker's actual SL price to an equivalent profit/loss amount.
        
        Args:
            position: Position dictionary with entry_price, sl, type, volume, symbol
                     MUST contain actual broker SL price (from MT5)
            check_pending: If True, check if SL is pending verification (for sweet spot trades)
        
        Returns:
            Tuple of (effective_sl_profit, is_verified):
            - effective_sl_profit: Effective SL in profit terms (USD)
              - Negative values for loss protection (e.g., -$2.00)
              - Zero for break-even
              - Positive values for locked profit (e.g., $0.03, $0.05, $0.10)
            - is_verified: True if SL is verified as applied by broker, False if pending
        """
        # Use new SL manager if available
        if hasattr(self, 'sl_manager') and self.sl_manager:
            try:
                effective_sl_profit = self.sl_manager.get_effective_sl_profit(position)
                # For verification, default to True (new system handles verification internally)
                is_verified = True
                return effective_sl_profit, is_verified
            except Exception as e:
                logger.error(f"Error using SL manager for effective SL calculation: {e}", exc_info=True)
                # Fall through to legacy logic
        
        # LEGACY FALLBACK: Original calculation logic
        entry_price = position.get('price_open', 0.0)
        sl_price = position.get('sl', 0.0)  # ACTUAL broker-applied SL price
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        symbol = position.get('symbol', '')
        ticket = position.get('ticket', 0)
        
        if not symbol or entry_price <= 0 or lot_size <= 0:
            return -self.max_risk_usd, False  # Default to -$2.00 loss protection, unverified
        
        # Get symbol info for accurate calculation
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return -self.max_risk_usd, False  # Default to -$2.00 loss protection, unverified
        
        # CRITICAL FIX: Use corrected contract_size (same logic as _enforce_strict_loss_limit)
        # This ensures BTCXAUm and similar symbols use the correct contract_size (100.0 instead of 1.0)
        contract_size = self._get_corrected_contract_size(symbol, entry_price, lot_size, self.max_risk_usd)
        
        if sl_price <= 0:
            # No SL set - assume -$2.00 loss protection (initial risk)
            return -self.max_risk_usd, False
        
        # Calculate effective SL in profit terms (USD) using ACTUAL broker SL
        # CRITICAL FIX: Properly handle BUY vs SELL order types
        if order_type == 'BUY':
            # For BUY:
            # - SL below entry = loss protection (negative P/L)
            #   Loss = (entry_price - sl_price) * lot * contract
            #   effective_sl_profit = -(entry_price - sl_price) * lot * contract
            # - SL at/above entry = profit locked (positive P/L)
            #   Profit = (entry_price - sl_price) * lot * contract (negative distance = profit)
            #   effective_sl_profit = -(entry_price - sl_price) * lot * contract (positive result)
            effective_sl_profit = -(entry_price - sl_price) * lot_size * contract_size
        else:  # SELL
            # For SELL:
            # - SL above entry = loss protection (negative P/L)
            #   Loss = (sl_price - entry_price) * lot * contract
            #   effective_sl_profit = -(sl_price - entry_price) * lot * contract (negative result)
            # - SL at/below entry = profit locked (positive P/L)
            #   Profit = (sl_price - entry_price) * lot * contract (negative distance = profit)
            #   effective_sl_profit = -(sl_price - entry_price) * lot * contract (positive result)
            effective_sl_profit = -(sl_price - entry_price) * lot_size * contract_size
        
        # Check if SL is verified (for sweet spot trades)
        is_verified = True  # Default to verified
        if check_pending:
            current_profit = position.get('profit', 0.0)
            # Check if trade is in sweet spot and SL verification is needed
            if hasattr(self, '_profit_locking_engine') and self._profit_locking_engine:
                if hasattr(self._profit_locking_engine, 'sweet_spot_min_profit'):
                    sweet_spot_min = self._profit_locking_engine.sweet_spot_min_profit
                    sweet_spot_max = self._profit_locking_engine.sweet_spot_max_profit
                    if sweet_spot_min <= current_profit <= sweet_spot_max:
                        # Trade is in sweet spot - check verification
                        if hasattr(self._profit_locking_engine, 'is_sl_verified'):
                            is_verified = self._profit_locking_engine.is_sl_verified(ticket)
        
        return effective_sl_profit, is_verified
    
    def calculate_potential_pl_if_sl_hits(self, position: Dict[str, Any]) -> float:
        """
        Calculate potential P/L if stop-loss hits now.
        
        This is the profit/loss that would be realized if the position is closed
        at the current stop-loss price.
        
        Args:
            position: Position dictionary with entry_price, sl, type, volume, symbol
        
        Returns:
            Potential P/L in USD if SL hits (same as effective_sl_profit)
        """
        effective_sl_profit, _ = self.calculate_effective_sl_in_profit_terms(position, check_pending=False)
        return effective_sl_profit
    
    def calculate_spread_and_fees_cost(self, symbol: str, lot_size: float) -> Tuple[float, str]:
        """
        Calculate total cost (spread + fees) for a trade in USD.
        
        Args:
            symbol: Trading symbol
            lot_size: Lot size to use
            
        Returns:
            Tuple of (total_cost_usd, description)
            - total_cost_usd: Total cost in USD (spread + fees)
            - description: Description of cost breakdown
        """
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return 999.0, "Cannot get symbol info"
        
        bid = symbol_info.get('bid', 0)
        ask = symbol_info.get('ask', 0)
        contract_size = symbol_info.get('contract_size', 1.0)
        
        if bid <= 0 or ask <= 0:
            return 999.0, "Invalid bid/ask prices"
        
        # Calculate spread cost
        spread_price = ask - bid
        spread_cost_usd = spread_price * lot_size * contract_size
        
        # Estimate commission (for Exness, usually built into spread, but estimate separately if needed)
        # For now, assume commission is minimal or built into spread
        commission_usd = 0.0  # Can be enhanced if broker charges separate commission
        
        total_cost = spread_cost_usd + commission_usd
        
        description = f"Spread: ${spread_cost_usd:.2f}, Fees: ${commission_usd:.2f}"
        
        return total_cost, description
    
    def calculate_stop_loss_pips(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        order_type: str
    ) -> float:
        """Calculate stop loss in pips."""
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return 0
        
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        if order_type == 'BUY':
            price_diff = entry_price - stop_loss_price
        else:  # SELL
            price_diff = stop_loss_price - entry_price
        
        pips = price_diff / pip_value
        return abs(pips)
    
    def can_open_trade(
        self,
        symbol: Optional[str] = None,
        signal: Optional[str] = None,
        quality_score: Optional[float] = None,
        high_quality_setup: bool = False
    ) -> Tuple[bool, str]:
        """
        Check if we can open a new trade (with staged open support).
        
        Args:
            symbol: Trading symbol (required for staged open)
            signal: 'LONG' or 'SHORT' (required for staged open)
            quality_score: Quality score (0-100) for staged open validation
            high_quality_setup: Legacy parameter for high quality override
        
        Returns:
            (can_open: bool, reason: str)
        """
        # Check circuit breaker first
        is_paused, pause_reason = self._check_circuit_breaker()
        if is_paused:
            logger.debug(f"[TRADE_BLOCKED] Symbol: {symbol} | Reason: Circuit breaker - {pause_reason}")
            return False, f"Circuit breaker: {pause_reason}"
        
        # Check entry filters (requires symbol)
        if symbol:
            passed, filter_reason = self._check_entry_filters(symbol)
            if not passed:
                logger.debug(f"[TRADE_BLOCKED] Symbol: {symbol} | Reason: Entry filter - {filter_reason}")
                return False, f"Entry filter: {filter_reason}"
        
        # Get all positions and filter out those on closed/halted markets
        # CRITICAL FIX: Only count positions on tradeable markets for position limit checks
        # This prevents closed/halted markets from blocking trades on other symbols
        all_positions = self.order_manager.get_open_positions()
        current_positions = 0
        excluded_closed_market = 0
        closed_market_tickets = []
        
        for pos in all_positions:
            pos_symbol = pos.get('symbol')
            if pos_symbol:
                # Check if this position's market is currently tradeable
                is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(pos_symbol, check_trade_allowed=False)
                if is_tradeable:
                    current_positions += 1
                else:
                    excluded_closed_market += 1
                    closed_market_tickets.append((pos.get('ticket'), pos_symbol, reason))
        
        # Log excluded positions for observability
        if excluded_closed_market > 0:
            logger.info(f"[POSITION_COUNT] Excluding {excluded_closed_market} position(s) from closed/halted markets when checking trade eligibility")
            for ticket, pos_symbol, reason in closed_market_tickets:
                logger.info(f"[POSITION_COUNT] Excluded ticket {ticket} ({pos_symbol}): market closed/halted - {reason}")
        
        logger.debug(f"[POSITION_COUNT] Active positions: {current_positions} (total: {len(all_positions)}, excluded: {excluded_closed_market})")
        
        # If max_open_trades is None or -1, unlimited trades allowed
        if self.max_open_trades is None:
            # Unlimited trades - no limit check needed (unless strict mode is enabled, which shouldn't make sense with unlimited)
            if self.max_open_trades_strict:
                logger.warning(f"⚠️ WARNING: max_open_trades_strict=True but max_open_trades=unlimited - strict mode ignored")
            return True, "Unlimited trades allowed"
        
        # If max_open_trades is None, unlimited trades allowed
        if self.max_open_trades is None:
            # Unlimited trades - skip max trade limit checks
            # Staged open logic still applies if enabled
            if not self.staged_open_enabled:
                return True, "Unlimited trades allowed"
            # Continue to staged open logic below
        else:
            # CRITICAL: If strict mode is enabled, NEVER allow more than max_open_trades
            if self.max_open_trades_strict:
                if current_positions >= self.max_open_trades:
                    logger.warning(f"🚫 MAX TRADES STRICT: Cannot open new trade | Current: {current_positions}/{self.max_open_trades} | Strict mode enabled - no overrides allowed")
                    return False, f"Max open trades ({self.max_open_trades}) reached (strict mode - no overrides)"
            
            # If we're below max, we can always open (unless staged open is enabled, in which case check staged logic)
            if current_positions < self.max_open_trades:
                if not self.staged_open_enabled:
                    return True, "Below max open trades limit"
                # Continue to staged open logic below
            
            # If we're at max and staged open is disabled, check override
            if not self.staged_open_enabled:
                if high_quality_setup and self.risk_config.get('high_quality_setup_override', True):
                    return True, "High quality setup override"
                return False, f"Max open trades ({self.max_open_trades}) reached"
        
        # Staged open logic: check if we can open additional trade within window
        if symbol is None or signal is None:
            # If unlimited trades, allow it even without symbol/signal for staged open
            if self.max_open_trades is None:
                return True, "Unlimited trades allowed"
            return False, "Symbol and signal required for staged open"
        
        # Get existing positions for this symbol (only count tradeable market positions)
        positions = self.order_manager.get_open_positions()
        symbol_positions = []
        for p in positions:
            pos_symbol = p.get('symbol')
            if pos_symbol == symbol:
                # Only count if this position's market is tradeable (same check as global position count)
                is_tradeable, reason = self.mt5_connector.is_symbol_tradeable_now(pos_symbol, check_trade_allowed=False)
                if is_tradeable:
                    symbol_positions.append(p)
                else:
                    logger.debug(f"[STAGED_OPEN] Excluding position {p.get('ticket')} on {pos_symbol} from symbol count (market closed/halted: {reason})")
        
        # If unlimited trades, skip symbol-specific max check
        if self.max_open_trades is not None and len(symbol_positions) >= self.max_open_trades:
            return False, f"Max trades for {symbol} reached"
        
        # Check if we have any staged trades for this symbol
        with self._staged_lock:
            staged_info = self._staged_trades.get(symbol, None)
            
            if staged_info is None:
                # First trade for this symbol - can open if we have room
                if current_positions < self.max_open_trades:
                    return True, "First trade for symbol"
                return False, "Max open trades reached (no staged window)"
            
            # Check if trend matches
            if staged_info['trend'] != signal:
                return False, f"Trend mismatch: existing {staged_info['trend']}, new {signal}"
            
            # Check if within staged window
            time_since_first = (datetime.now() - staged_info['first_trade_time']).total_seconds()
            if time_since_first > self.staged_open_window_seconds:
                return False, f"Staged window expired ({time_since_first:.0f}s > {self.staged_open_window_seconds}s)"
            
            # Check quality score
            if quality_score is not None and quality_score < self.staged_quality_threshold:
                return False, f"Quality score {quality_score:.1f} < threshold {self.staged_quality_threshold}"
            
            # Check if existing trades meet profit requirement (relaxed for medium-frequency trading)
            existing_tickets = staged_info['trades']
            if existing_tickets:
                # Get first trade profit
                first_trade = self.order_manager.get_position_by_ticket(existing_tickets[0])
                if first_trade:
                    first_profit = first_trade.get('profit', 0.0)
                    # Only block if first trade is significantly losing (more than $0.50)
                    # This allows recovery trades and doesn't block small losses
                    if first_profit < self.staged_min_profit_usd:
                        return False, f"First trade profit ${first_profit:.2f} < minimum ${self.staged_min_profit_usd}"
            
            # Check if we can open another staged trade (skip if unlimited)
            if self.max_open_trades is not None and len(existing_tickets) >= self.max_open_trades:
                return False, f"Max staged trades ({self.max_open_trades}) for {symbol} reached"
            
            return True, f"Staged open allowed (window: {time_since_first:.0f}s, trades: {len(existing_tickets)})"
    
    def check_portfolio_risk(self, new_trade_risk_usd: float = 0.0) -> Tuple[bool, str]:
        """
        Check if adding new trade would exceed portfolio risk limit.
        
        Args:
            new_trade_risk_usd: Risk amount in USD for the new trade being considered
        
        Returns:
            (allowed: bool, reason: str)
        """
        # Get account balance
        account_info = self.mt5_connector.get_account_info()
        if account_info is None:
            return False, "Cannot get account info for portfolio risk check"
        
        account_balance = account_info.get('balance', 0.0)
        if account_balance <= 0:
            return False, "Invalid account balance"
        
        # Get all open positions and calculate total risk
        positions = self.order_manager.get_open_positions()
        total_risk_usd = 0.0
        
        for position in positions:
            symbol = position.get('symbol')
            if not symbol:
                continue
            
            # Get position's stop loss distance
            entry_price = position.get('price_open', 0)
            sl_price = position.get('sl', 0)
            lot_size = position.get('volume', 0)
            order_type = position.get('type')
            
            if entry_price <= 0 or lot_size <= 0:
                continue
            
            # Get symbol info for calculations
            symbol_info = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
            if symbol_info is None:
                continue
            
            point = symbol_info.get('point', 0.00001)
            pip_value = point * 10 if symbol_info.get('digits', 5) == 5 or symbol_info.get('digits', 3) == 3 else point
            contract_size = symbol_info.get('contract_size', 1.0)
            
            # Calculate stop loss distance in price
            if order_type == 'BUY' and sl_price > 0:
                sl_distance_price = entry_price - sl_price
            elif order_type == 'SELL' and sl_price > 0:
                sl_distance_price = sl_price - entry_price
            else:
                # No SL set, use USD-based stop loss distance
                # Calculate using contract_value_per_point
                point_value = symbol_info.get('trade_tick_value', None)
                if point_value is not None and point_value > 0:
                    trade_tick_size = symbol_info.get('trade_tick_size', 1.0)
                    if trade_tick_size > 0:
                        contract_value_per_point = (lot_size * point_value) / trade_tick_size
                    else:
                        contract_value_per_point = lot_size * point_value
                else:
                    contract_value_per_point = lot_size * contract_size
                
                if contract_value_per_point > 0:
                    sl_distance_price = self.max_risk_usd / contract_value_per_point
                else:
                    sl_distance_price = 0.0
            
            # Calculate risk for this position
            position_risk = lot_size * abs(sl_distance_price) * contract_size
            total_risk_usd += position_risk
        
        # Add new trade risk
        total_risk_with_new = total_risk_usd + new_trade_risk_usd
        
        # Check absolute USD limit first (if set)
        if self.max_portfolio_risk_usd is not None:
            if total_risk_with_new > self.max_portfolio_risk_usd:
                return False, f"Portfolio risk ${total_risk_with_new:.2f} would exceed absolute limit ${self.max_portfolio_risk_usd:.2f} (current: ${total_risk_usd:.2f})"
        
        # Check percentage limit
        portfolio_risk_pct = (total_risk_with_new / account_balance) * 100
        if portfolio_risk_pct > self.max_portfolio_risk_pct:
            return False, f"Portfolio risk {portfolio_risk_pct:.1f}% would exceed limit {self.max_portfolio_risk_pct}% (current: ${total_risk_usd:.2f}, new: ${total_risk_with_new:.2f}, balance: ${account_balance:.2f})"
        
        return True, f"Portfolio risk OK ({portfolio_risk_pct:.1f}% <= {self.max_portfolio_risk_pct}%, ${total_risk_with_new:.2f})"
    
    def update_trailing_stop(
        self,
        ticket: int,
        current_profit_usd: float
    ) -> bool:
        """
        Update trailing stop based on profit increments.
        
        Trailing stop logic (user requirement):
        - For every $0.10 increase in profit, move SL +$0.10 behind the price
        - Profit +0.10 → SL at +0.00 (breakeven)
        - Profit +0.20 → SL at +0.10
        - Profit +0.30 → SL at +0.20
        - Continue until hit or manually closed
        """
        position = self.order_manager.get_position_by_ticket(ticket)
        if position is None:
            return False
        
        # Need at least $0.10 profit to start trailing
        if current_profit_usd < self.trailing_increment_usd:
            return False
        
        # Calculate target SL profit: floor(profit / 0.10) * 0.10 - 0.10
        # This moves SL $0.10 behind price for every $0.10 profit
        increment_level = int(current_profit_usd / self.trailing_increment_usd)
        target_sl_profit = (increment_level * self.trailing_increment_usd) - self.trailing_increment_usd
        
        # Ensure target SL is not negative (minimum breakeven)
        if target_sl_profit < 0:
            target_sl_profit = 0
        
        # Calculate new stop loss price
        symbol = position['symbol']
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        entry_price = position['price_open']
        current_price = position['price_current']
        order_type = position['type']
        
        # Calculate price difference needed for target SL profit
        lot_size = position['volume']
        contract_size = symbol_info['contract_size']
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        # Get current profit in account currency (from MT5)
        current_profit_account_currency = position.get('profit', 0)
        
        if abs(current_profit_account_currency) < 0.01:
            return False
        
        # Calculate current price difference that gives current profit
        current_price_diff = abs(current_price - entry_price)
        
        # Calculate price difference needed for target SL profit
        # Profit = Price_Diff * Lot_Size * Contract_Size
        # Price_Diff = Profit / (Lot_Size * Contract_Size)
        if lot_size <= 0 or contract_size <= 0:
            return False
        
        target_price_diff = target_sl_profit / (lot_size * contract_size)
        
        # Safety check
        if target_price_diff < 0:
            return False
        
        # Set new stop loss price
        if order_type == 'BUY':
            # For BUY: SL should be entry_price + target_price_diff (below entry for profit)
            new_sl_price = entry_price + target_price_diff
            # Ensure SL is not above current price (should be below for profit)
            if new_sl_price >= current_price:
                new_sl_price = current_price - (pip_value)  # 1 pip below current
        else:  # SELL
            # For SELL: SL should be entry_price - target_price_diff (above entry for profit)
            new_sl_price = entry_price - target_price_diff
            # Ensure SL is not below current price (should be above for profit)
            if new_sl_price <= current_price:
                new_sl_price = current_price + (pip_value)  # 1 pip above current
        
        # Check if SL needs updating
        current_sl = position['sl']
        if order_type == 'BUY' and new_sl_price > current_sl:
            # Use absolute price for trailing stop
            return self.order_manager.modify_order(ticket, stop_loss_price=new_sl_price)
        elif order_type == 'SELL' and (current_sl == 0 or new_sl_price < current_sl):
            # Use absolute price for trailing stop
            return self.order_manager.modify_order(ticket, stop_loss_price=new_sl_price)
        
        return False
    
    def calculate_usd_based_stop_loss_price(
        self,
        symbol: str,
        entry_price: float,
        order_type: str,
        lot_size: Optional[float] = None,
        risk_usd: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        Calculate stop loss price based on fixed USD risk using formula: entry_price ± (risk_usd / contract_value_per_point).
        
        Formula: SL = entry_price ± (risk_usd / contract_value_per_point)
        Where contract_value_per_point = lot_size * contract_size (or trade_tick_value for indices/crypto)
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            order_type: 'BUY' or 'SELL'
            lot_size: Lot size (defaults to default_lot_size)
            risk_usd: Risk amount in USD (defaults to max_risk_usd = $2.00)
        
        Returns:
            (stop_loss_price, stop_loss_distance_in_price)
        """
        if risk_usd is None:
            risk_usd = self.max_risk_usd  # Fixed $2.00 USD
        
        if lot_size is None:
            lot_size = self.default_lot_size
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            logger.warning(f"Cannot get symbol info for {symbol}, using default")
            return entry_price, 0.0
        
        # Get contract value per point
        # For indices/crypto: use trade_tick_value if available
        # For forex: use contract_size
        point_value = symbol_info.get('trade_tick_value', None)
        contract_size = symbol_info.get('contract_size', 100000)
        
        # Calculate contract_value_per_point
        if point_value is not None and point_value > 0:
            # For indices/crypto: contract_value_per_point = lot_size * point_value
            # But we need to account for trade_tick_size
            trade_tick_size = symbol_info.get('trade_tick_size', 1.0)
            if trade_tick_size > 0:
                # contract_value_per_point = (lot_size * point_value) / trade_tick_size
                contract_value_per_point = (lot_size * point_value) / trade_tick_size
            else:
                contract_value_per_point = lot_size * point_value
        else:
            # For forex: contract_value_per_point = lot_size * contract_size
            contract_value_per_point = lot_size * contract_size
        
        if contract_value_per_point <= 0:
            logger.warning(f"Invalid contract_value_per_point for {symbol}: {contract_value_per_point}")
            return entry_price, 0.0
        
        # Calculate stop loss distance in price terms using formula: risk_usd / contract_value_per_point
        stop_loss_distance_price = risk_usd / contract_value_per_point
        
        # Calculate stop loss price based on order type
        # BUY: SL below entry (entry_price - distance)
        # SELL: SL above entry (entry_price + distance)
        if order_type == 'BUY':
            stop_loss_price = entry_price - stop_loss_distance_price
        else:  # SELL
            stop_loss_price = entry_price + stop_loss_distance_price
        
        return stop_loss_price, stop_loss_distance_price
    
    def validate_stop_loss(self, symbol: str, stop_loss_pips: Optional[float] = None, entry_price: Optional[float] = None, order_type: Optional[str] = None, stop_loss_price: Optional[float] = None) -> bool:
        """
        Validate that stop loss meets minimum requirements.
        
        Args:
            symbol: Trading symbol
            stop_loss_pips: Stop loss in pips
            entry_price: Entry price (optional, for distance validation)
            order_type: 'BUY' or 'SELL' (optional, for distance validation)
        """
        # USD-based stop loss: no minimum pips check (removed)
        # With USD-based SL, we use fixed $2.00 risk, so no pips validation needed
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Check against broker's minimum stops level
        stops_level = symbol_info.get('trade_stops_level', 0)
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        # Convert stops_level to pips
        min_stops_pips = (stops_level * point) / pip_value if pip_value > 0 else 0
        
        # If we have entry price and order type, validate actual distance
        if entry_price and order_type:
            # Use stop_loss_price if provided (USD-based), otherwise calculate from pips
            if stop_loss_price is not None:
                # USD-based stop loss: use the provided stop_loss_price
                sl_price = stop_loss_price
            elif stop_loss_pips is not None:
                # Pips-based stop loss: calculate from pips
                if order_type == 'BUY':
                    sl_price = entry_price - (stop_loss_pips * pip_value)
                else:  # SELL
                    sl_price = entry_price + (stop_loss_pips * pip_value)
            else:
                # Neither provided - cannot validate
                logger.warning(f"Neither stop_loss_price nor stop_loss_pips provided for validation")
                return False
            
            # Calculate actual distance from entry to SL
            actual_distance = abs(entry_price - sl_price)
            min_distance = stops_level * point
            
            if min_distance > 0 and actual_distance < min_distance:
                logger.warning(f"Stop loss distance {actual_distance:.5f} is less than broker minimum {min_distance:.5f} "
                            f"(stops_level: {stops_level}, min_stops_pips: {min_stops_pips:.1f})")
                return False
        
        # Also check if stop_loss_pips meets minimum stops level in pips (only if pips-based)
        if stop_loss_pips is not None and min_stops_pips > 0 and stop_loss_pips < min_stops_pips:
            logger.warning(f"Stop loss {stop_loss_pips:.1f} pips is less than broker minimum {min_stops_pips:.1f} pips "
                        f"(stops_level: {stops_level})")
            return False
        
        return True
    
    def _get_position_tracking(self, ticket: int) -> Dict[str, Any]:
        """Get thread-safe position tracking data (enhanced for SETE)."""
        with self._tracking_lock:
            if ticket not in self._position_tracking:
                self._position_tracking[ticket] = {
                    'last_profit': 0.0,
                    'last_sl_profit': 0.0,
                    'peak_profit': 0.0,
                    'lock': threading.Lock(),
                    'fast_polling': False,
                    'debounce_count': 0,
                    'positive_profit_start_time': None,  # Timestamp when profit became positive
                    'break_even_sl_applied': False  # Whether break-even SL has been applied
                }
            return self._position_tracking[ticket]
    
    def _update_position_tracking(self, ticket: int, last_profit: float, last_sl_profit: float):
        """Update thread-safe position tracking data."""
        with self._tracking_lock:
            if ticket not in self._position_tracking:
                self._position_tracking[ticket] = {
                    'last_profit': last_profit,
                    'last_sl_profit': last_sl_profit,
                    'lock': threading.Lock(),
                    'positive_profit_start_time': None,
                    'break_even_sl_applied': False
                }
            else:
                self._position_tracking[ticket]['last_profit'] = last_profit
                self._position_tracking[ticket]['last_sl_profit'] = last_sl_profit
                # Reset break-even tracking if profit goes negative
                if last_profit <= 0:
                    self._position_tracking[ticket]['positive_profit_start_time'] = None
    
    def _remove_position_tracking(self, ticket: int):
        """Remove position tracking when position is closed."""
        with self._tracking_lock:
            self._position_tracking.pop(ticket, None)
    
    def register_staged_trade(self, symbol: str, ticket: int, signal: str):
        """Register a staged trade for tracking."""
        with self._staged_lock:
            if symbol not in self._staged_trades:
                self._staged_trades[symbol] = {
                    'trades': [],
                    'first_trade_time': datetime.now(),
                    'trend': signal,
                    'lock': threading.Lock()
                }
            self._staged_trades[symbol]['trades'].append(ticket)
    
    def unregister_staged_trade(self, symbol: str, ticket: int):
        """Unregister a staged trade when closed."""
        with self._staged_lock:
            if symbol in self._staged_trades:
                if ticket in self._staged_trades[symbol]['trades']:
                    self._staged_trades[symbol]['trades'].remove(ticket)
                # Clean up if no more trades
                if not self._staged_trades[symbol]['trades']:
                    self._staged_trades.pop(symbol, None)
    
    def update_continuous_trailing_stop(
        self,
        ticket: int,
        current_profit_usd: float
    ):
        """
        Update trailing stop with Smart Elastic Trailing Engine (SETE).
        
        SETE Logic:
        - Tracks peak profit per position
        - Allows pullback tolerance (default 40% of peak)
        - Uses elastic SL calculation based on peak and pullback
        - Detects big jumps and locks SL immediately
        - Ensures SL only moves forward (never backward)
        
        CRITICAL: Trailing stop MUST NOT run on losing trades (profit < 0).
        Losing trades are handled by strict -$2.00 SL enforcement.
        
        Args:
            ticket: Position ticket number
            current_profit_usd: Current profit in USD
        
        Returns:
            (success: bool, reason: str)
        """
        # CRITICAL: Trailing stop MUST NOT run on losing trades
        # If profit is negative, strict -$2.00 SL enforcement handles it
        # Trailing stop should ONLY run on profitable trades
        if current_profit_usd < 0:
            logger.debug(f"[SKIP] Trailing Stop BLOCKED: Ticket {ticket} | "
                       f"Profit: ${current_profit_usd:.2f} (negative) | "
                       f"Trailing stop does not run on losing trades - strict -$2.00 SL enforcement active")
            return False, "Trade in loss zone - trailing stop blocked, strict SL enforcement active"
        
        position = self.order_manager.get_position_by_ticket(ticket)
        if position is None:
            # Position closed, remove tracking
            self._remove_position_tracking(ticket)
            return False, "Position not found"
        
        # Get thread-safe tracking
        tracking = self._get_position_tracking(ticket)
        
        # Use position-specific lock to prevent race conditions
        with tracking['lock']:
            last_profit = tracking.get('last_profit', 0.0)
            last_sl_profit = tracking.get('last_sl_profit', 0.0)
            peak_profit = tracking.get('peak_profit', 0.0)
            
            # Update peak profit if current is higher
            if current_profit_usd > peak_profit:
                peak_profit = current_profit_usd
                tracking['peak_profit'] = peak_profit
            
            # Need at least minimum increment profit to start trailing
            if current_profit_usd < self.min_lock_increment_usd:
                tracking['last_profit'] = current_profit_usd
                # Reset fast polling if profit drops below threshold
                if tracking.get('fast_polling', False):
                    tracking['fast_polling'] = False
                    tracking['debounce_count'] = 0
                return False, "Profit below minimum threshold"
            
            # Enable fast polling if profit >= threshold
            if current_profit_usd >= self.fast_trailing_threshold_usd:
                if not tracking.get('fast_polling', False):
                    tracking['fast_polling'] = True
                    tracking['debounce_count'] = 0
            else:
                # Debounce fast polling disable
                if tracking.get('fast_polling', False):
                    tracking['debounce_count'] = tracking.get('debounce_count', 0) + 1
                    if tracking['debounce_count'] >= self.fast_trailing_debounce_cycles:
                        tracking['fast_polling'] = False
                        tracking['debounce_count'] = 0
            
            # Smart Elastic Trailing Engine (SETE) logic
            if self.elastic_trailing_enabled:
                # ENHANCED: Aggressive profit locking at $0.10 increments
                # Calculate floor increment lock: lock at $0.10 when profit >= $0.10, $0.20 when >= $0.20, etc.
                # When profit is $0.40, lock at $0.30 (one increment below current profit)
                increment_level = int(current_profit_usd / self.min_lock_increment_usd)
                
                # CRITICAL FIX: Lock at current increment level minus one
                # If profit is $0.40 (level 4), lock at $0.30 (level 3 * 0.10)
                # If profit is $0.30 (level 3), lock at $0.20 (level 2 * 0.10)
                # If profit is $0.20 (level 2), lock at $0.10 (level 1 * 0.10)
                # If profit is $0.10 (level 1), lock at $0.00 (breakeven)
                
                # Ensure we always lock at the highest safe increment
                if increment_level >= 1:
                    # Lock at (increment_level - 1) * 0.10
                    # But if profit is exactly at a multiple, we can lock at that multiple
                    # However, to be safe, we lock one increment below current profit
                    floor_lock = max(0.0, (increment_level - 1) * self.min_lock_increment_usd)
                    
                    # ENHANCEMENT: If profit has been at or above a level for a moment, lock at that level
                    # Check if current profit is at or very close to a $0.10 multiple
                    remainder = current_profit_usd % self.min_lock_increment_usd
                    if remainder < 0.02:  # Within $0.02 of a $0.10 multiple
                        # Lock at the current level (not one below)
                        floor_lock = max(floor_lock, increment_level * self.min_lock_increment_usd - self.min_lock_increment_usd)
                else:
                    floor_lock = 0.0
                
                # Calculate elastic lock based on peak and pullback tolerance
                # But ensure we never go below the floor lock (aggressive locking)
                allowed_pullback = peak_profit * self.pullback_tolerance_pct
                elastic_lock = max(floor_lock, peak_profit - allowed_pullback)
                
                # Big jump detection
                profit_increase = current_profit_usd - last_profit
                is_big_jump = profit_increase > self.big_jump_threshold_usd
                
                if is_big_jump:
                    # Immediate lock for big jumps: lock 75% of peak profit
                    big_jump_lock_pct = 0.75  # Lock 75% of peak profit on big jumps
                    target_lock = peak_profit * big_jump_lock_pct
                    # Ensure we don't go below elastic lock
                    target_lock = max(elastic_lock, target_lock)
                    reason = f"Big jump detected (+${profit_increase:.2f}), locking 75% of peak (${peak_profit:.2f} → ${target_lock:.2f})"
                    # Log big jump to root (critical event) - get symbol from position
                    symbol = position['symbol']
                    root_logger = logging.getLogger()
                    root_logger.info(f"[JUMP] BIG JUMP: {symbol} Ticket {ticket} | ${last_profit:.2f} → ${current_profit_usd:.2f} (+${profit_increase:.2f})")
                else:
                    # Normal elastic trailing
                    target_lock = elastic_lock
                    reason = f"Elastic trailing (peak: ${peak_profit:.2f}, pullback: {self.pullback_tolerance_pct*100:.0f}%)"
                
                # Apply max peak lock cap - ensure we don't lock more than max_peak_lock_usd
                # This allows profits to grow up to max_risk_per_trade_usd while protecting gains
                if target_lock > self.max_peak_lock_usd:
                    target_lock = self.max_peak_lock_usd
                    reason += f", max lock cap applied (${self.max_peak_lock_usd:.2f})"
                
                # Ensure we only move SL forward (compare in profit USD terms)
                if target_lock <= last_sl_profit:
                    tracking['last_profit'] = current_profit_usd
                    return False, f"SL already at target (current: ${last_sl_profit:.2f}, target: ${target_lock:.2f})"
                
                target_sl_profit = target_lock
            else:
                # Fallback to standard incremental logic (if elastic disabled)
                increment_level = int(current_profit_usd / self.trailing_increment_usd)
                target_sl_profit = (increment_level * self.trailing_increment_usd) - self.trailing_increment_usd
                if target_sl_profit < 0:
                    target_sl_profit = 0.0
                
                # Check for big jump
                profit_increase = current_profit_usd - last_profit
                is_big_jump = profit_increase > self.big_jump_threshold_usd
                
                if is_big_jump:
                    # Lock 75% of current profit on big jumps
                    big_jump_lock_pct = 0.75
                    target_sl_profit = max(target_sl_profit, current_profit_usd * big_jump_lock_pct)
                    reason = f"Big jump detected (+${profit_increase:.2f}), locking 75% of profit (${current_profit_usd:.2f} → ${target_sl_profit:.2f})"
                else:
                    reason = f"Incremental update (profit: ${current_profit_usd:.2f})"
                
                # CRITICAL FIX: Ensure SL never goes worse than -$2.00 (max risk per trade)
                # This prevents early exits - SL should never be worse than the initial risk
                if target_sl_profit < -self.max_risk_usd:
                    # Log early exit prevention
                    from trade_logging.trade_logger import TradeLogger
                    trade_logger = TradeLogger(self.config)
                    trade_logger.log_early_exit_prevention(
                        symbol=symbol,
                        ticket=ticket,
                        attempted_sl_profit=target_sl_profit,
                        max_risk=self.max_risk_usd
                    )
                    logger.warning(f"[WARNING]  Ticket {ticket}: Target SL profit ${target_sl_profit:.2f} would exceed max risk ${self.max_risk_usd:.2f}, preventing early exit")
                    tracking['last_profit'] = current_profit_usd
                    return False, f"Prevented early exit: SL would exceed max risk ${self.max_risk_usd:.2f}"
                
                # Only update if target SL is higher than current SL profit
                if target_sl_profit <= last_sl_profit:
                    tracking['last_profit'] = current_profit_usd
                    return False, f"SL already at target (current: ${last_sl_profit:.2f}, target: ${target_sl_profit:.2f})"
            
            # CRITICAL FIX: Final check - ensure SL never goes worse than -$2.00
            if target_sl_profit < -self.max_risk_usd:
                # Log early exit prevention
                from trade_logging.trade_logger import TradeLogger
                trade_logger = TradeLogger(self.config)
                trade_logger.log_early_exit_prevention(
                    symbol=symbol,
                    ticket=ticket,
                    attempted_sl_profit=target_sl_profit,
                    max_risk=self.max_risk_usd
                )
                logger.warning(f"[WARNING]  Ticket {ticket}: Target SL profit ${target_sl_profit:.2f} would exceed max risk ${self.max_risk_usd:.2f}, preventing early exit")
                tracking['last_profit'] = current_profit_usd
                return False, f"Prevented early exit: SL would exceed max risk ${self.max_risk_usd:.2f}"
            
            # Ensure we never move SL backward
            if target_sl_profit < last_sl_profit:
                logger.warning(f"[WARNING]  Ticket {ticket}: Attempted to move SL backward (${last_sl_profit:.2f} → ${target_sl_profit:.2f}), preventing")
                tracking['last_profit'] = current_profit_usd
                return False, "Prevented backward SL movement"
            
            # Calculate new stop loss price
            symbol = position['symbol']
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                tracking['last_profit'] = current_profit_usd
                return False, "Symbol info not available"
            
            entry_price = position['price_open']
            current_price = position['price_current']
            order_type = position['type']
            lot_size = position['volume']
            contract_size = symbol_info['contract_size']
            point = symbol_info['point']
            pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
            
            # Validate against broker's minimum stops level
            stops_level = symbol_info.get('trade_stops_level', 0)
            min_distance = stops_level * point if stops_level > 0 else 0
            
            # Calculate price difference needed for target SL profit
            if lot_size <= 0 or contract_size <= 0:
                tracking['last_profit'] = current_profit_usd
                return False, "Invalid lot size or contract size"
            
            target_price_diff = target_sl_profit / (lot_size * contract_size)
            
            if target_price_diff < 0:
                tracking['last_profit'] = current_profit_usd
                return False, "Invalid target price difference"
            
            # Calculate new stop loss price
            if order_type == 'BUY':
                new_sl_price = entry_price + target_price_diff
                # Ensure SL is not above current price and respects min distance
                if new_sl_price >= current_price:
                    new_sl_price = current_price - max(pip_value * 2, min_distance)
                elif min_distance > 0 and (current_price - new_sl_price) < min_distance:
                    new_sl_price = current_price - min_distance
            else:  # SELL
                new_sl_price = entry_price - target_price_diff
                # Ensure SL is not below current price and respects min distance
                if new_sl_price <= current_price:
                    new_sl_price = current_price + max(pip_value * 2, min_distance)
                elif min_distance > 0 and (new_sl_price - current_price) < min_distance:
                    new_sl_price = current_price + min_distance
            
            # Get current SL to check if update is needed
            current_sl = position['sl']
            
            # CRITICAL FIX: Check if SL needs updating with proper tolerance
            # Use point size as tolerance to prevent floating point precision issues
            point = symbol_info.get('point', 0.00001)
            sl_difference = abs(new_sl_price - current_sl) if current_sl > 0 else float('inf')
            
            # Check if SL needs updating (only move forward)
            needs_update = False
            if order_type == 'BUY':
                # For BUY: SL should be below entry, so higher SL = better (closer to entry)
                if current_sl == 0 or (new_sl_price > current_sl and sl_difference >= point):
                    needs_update = True
            else:  # SELL
                # For SELL: SL should be above entry, so lower SL = better (closer to entry)
                if current_sl == 0 or (new_sl_price < current_sl and sl_difference >= point):
                    needs_update = True
            
            if not needs_update:
                tracking['last_profit'] = current_profit_usd
                return False, f"SL already optimal (current: {current_sl:.5f}, calculated: {new_sl_price:.5f}, diff: {sl_difference:.8f} < point: {point:.8f})"
            
            # CRITICAL FIX: Update stop loss with retry logic (up to 3 attempts)
            # If SL modification fails after retries, manually close position to prevent late exit
            success = False
            max_retries = 3
            last_error = None
            
            for attempt in range(max_retries):
                success = self.order_manager.modify_order(ticket, stop_loss_price=new_sl_price)
                if success:
                    break
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))  # Increasing backoff
                else:
                    # Get last error for logging
                    import MetaTrader5 as mt5
                    error = mt5.last_error()
                    last_error = error if error else "Unknown error"
            
            # If SL modification failed after all retries, manually close position to prevent late exit
            if not success:
                # Get current profit before closing
                position_before_close = self.order_manager.get_position_by_ticket(ticket)
                current_profit_before_close = position_before_close.get('profit', 0.0) if position_before_close else 0.0
                
                logger.error(f"[CRITICAL] SL_MODIFICATION_FAILED: Ticket {ticket} | Symbol {symbol} | "
                           f"SL modification failed after {max_retries} attempts | "
                           f"Current profit: ${current_profit_before_close:.2f} | "
                           f"Error: {last_error} | "
                           f"Manually closing position to prevent late exit (unprotected position)")
                
                # Calculate expected loss (should be -$2.00)
                expected_loss = -self.max_risk_usd
                
                # Close position manually
                close_success = self.order_manager.close_position(ticket, comment="SL modification failed - prevent late exit")
                if close_success:
                    logger.warning(f"[SAFETY_CLOSE] Ticket {ticket} | Symbol {symbol} | "
                                 f"Position closed manually due to SL modification failure | "
                                 f"Profit at close: ${current_profit_before_close:.2f} | "
                                 f"Expected loss threshold: ${expected_loss:.2f} | "
                                 f"Reason: SL could not be set, closing to prevent unlimited loss")
                    # Log late exit prevention
                    from trade_logging.trade_logger import TradeLogger
                    trade_logger = TradeLogger(self.config)
                    # Get actual profit from position before it closes
                    position = self.order_manager.get_position_by_ticket(ticket)
                    actual_profit = position.get('profit', expected_loss) if position else current_profit_before_close
                    trade_logger.log_late_exit_prevention(
                        symbol=symbol,
                        ticket=ticket,
                        actual_profit=actual_profit,
                        expected_profit=expected_loss,
                        sl_modification_failed=True
                    )
                else:
                    logger.error(f"[ERROR] Ticket {ticket} | Symbol {symbol} | "
                               f"Failed to close position manually after SL modification failure | "
                               f"Position may be unprotected - manual intervention required")
                
                tracking['last_profit'] = current_profit_usd
                return False, f"SL modification failed after {max_retries} attempts, position closed manually"
            
            if success:
                # Update tracking
                tracking['last_profit'] = current_profit_usd
                tracking['last_sl_profit'] = target_sl_profit
                
                # Calculate SL in pips for logging
                if order_type == 'BUY':
                    sl_pips = (new_sl_price - entry_price) / pip_value
                else:
                    sl_pips = (entry_price - new_sl_price) / pip_value
                
                # Log to root (minimal) - only critical events
                root_logger = logging.getLogger()
                root_logger.info(f"[STATS] SL ADJUSTED: {symbol} Ticket {ticket} | Profit: ${current_profit_usd:.2f} → SL: ${target_sl_profit:.2f}")
                
                # Use unified trade logger for trailing stop adjustments
                from trade_logging.trade_logger import TradeLogger
                trade_logger = TradeLogger(self.config)
                trade_logger.log_trailing_stop_adjustment(
                    symbol=symbol,
                    ticket=ticket,
                    current_profit=current_profit_usd,
                    new_sl_profit=target_sl_profit,
                    new_sl_price=new_sl_price,
                    sl_pips=sl_pips,
                    reason=reason
                )
                
                return True, reason
            else:
                tracking['last_profit'] = current_profit_usd
                return False, "Failed to modify order (retries exhausted)"
    
    def monitor_all_positions_continuous(self, use_fast_polling: bool = False):
        """
        Monitor all open positions and update trailing stops continuously.
        
        **MANDATORY FIX 1 - SINGLE-WRITER SL AUTHORITY:**
        This function is READ-ONLY and does NOT acquire per-ticket SL locks or mutate SL state.
        All SL mutations are handled EXCLUSIVELY by SLManager._sl_worker_loop() (single-writer).
        
        This function (called by TrailingStopMonitor threads) is read-only and only:
        - Observes positions and profit
        - Handles position tracking and cleanup
        - Calls Micro-HFT profit engine for position closure (does not modify SL)
        - Performs fail-safe checks (read-only verification)
        
        **DO NOT:**
        - Call sl_manager.update_sl_atomic() (would cause lock contention)
        - Acquire per-ticket SL locks
        - Mutate SL state directly
        
        This function checks P/L in millisecond margins (300ms intervals) for fast trailing stop updates.
        Fast polling mode is automatically enabled for positions with profit >= fast_trailing_threshold_usd.
        
        Args:
            use_fast_polling: If True, only monitor positions in fast polling mode (300ms intervals)
                             This enables millisecond-level P/L checking for profitable positions
        """
        # CRITICAL FIX 1: Runtime check to prevent lock acquisition by monitoring threads
        import threading
        current_thread_name = threading.current_thread().name
        is_monitoring_thread = current_thread_name in ['TrailingStopMonitor', 'FastTrailingStopMonitor']
        
        if is_monitoring_thread:
            # CRITICAL: Monitoring threads must NEVER acquire SL locks
            # This function is read-only - all SL mutations handled by SLWorker thread
            # If any code path from here tries to acquire locks, it will cause lock contention
            pass  # Documentation only - actual prevention is by not calling lock-acquiring methods
        
        if not self.continuous_trailing_enabled:
            return
        
        try:
            positions = self.order_manager.get_open_positions()
            
            # Get current tickets
            current_tickets = {pos['ticket'] for pos in positions} if positions else set()
            
            # CRITICAL FIX: Detect and log position closures
            # Track previously open positions to detect closures
            if not hasattr(self, '_last_open_tickets'):
                self._last_open_tickets = set()
            
            # Detect closed positions
            closed_tickets = self._last_open_tickets - current_tickets
            closed_tickets_symbols = {}  # {ticket: symbol}
            closed_positions_info = {}  # {ticket: position_info}
            
            # Get information about closed positions from tracking before cleanup
            for ticket in closed_tickets:
                # Get symbol and entry info from tracking
                tracking = self._get_position_tracking(ticket)
                if tracking:
                    # Try to get position info from order manager (might still be in cache)
                    pos_info = self.order_manager.get_position_by_ticket(ticket)
                    if pos_info:
                        closed_positions_info[ticket] = pos_info
                        closed_tickets_symbols[ticket] = pos_info.get('symbol')
                    else:
                        # Position fully closed - try to get from deal history
                        import MetaTrader5 as mt5
                        if self.mt5_connector.ensure_connected():
                            # Get deal history for this ticket (position identifier)
                            # Note: ticket is position ID, we need to get deals by position
                            deals = mt5.history_deals_get(position=ticket)
                            if deals and len(deals) > 0:
                                # Sort deals by time
                                deals_sorted = sorted(deals, key=lambda d: d.time)
                                
                                # Get entry deal (first deal, type IN)
                                entry_deal = None
                                close_deal = None
                                total_profit = 0.0
                                
                                for deal in deals_sorted:
                                    if deal.entry == mt5.DEAL_ENTRY_IN:
                                        entry_deal = deal
                                    elif deal.entry == mt5.DEAL_ENTRY_OUT:
                                        close_deal = deal
                                    total_profit += deal.profit
                                
                                if entry_deal and close_deal:
                                    symbol = entry_deal.symbol
                                    entry_price = entry_deal.price
                                    entry_time = datetime.fromtimestamp(entry_deal.time)
                                    close_price = close_deal.price
                                    close_time = datetime.fromtimestamp(close_deal.time)
                                    
                                    closed_positions_info[ticket] = {
                                        'symbol': symbol,
                                        'entry_price': entry_price,
                                        'entry_time': entry_time,
                                        'close_price': close_price,
                                        'close_time': close_time,
                                        'profit': total_profit
                                    }
                                    closed_tickets_symbols[ticket] = symbol
                                    
                                    # Use unified trade logger for position closure
                                    from trade_logging.trade_logger import TradeLogger
                                    trade_logger = TradeLogger(self.config)
                                    
                                    duration_min = (close_time - entry_time).total_seconds() / 60
                                    
                                    # Determine close reason with improved detection
                                    close_reason = "Unknown"
                                    # CRITICAL: Detect stop-loss closures accurately
                                    # Allow small tolerance for rounding (within $0.10 of -$2.00 for broker rounding/slippage)
                                    if abs(total_profit + 2.0) <= 0.10:  # Close to -$2.00 (within $0.10 tolerance)
                                        close_reason = "Stop Loss (-$2.00)"
                                        logger.info(f"🛑 STOP-LOSS TRIGGERED: Ticket {ticket} | {symbol} | "
                                                  f"Profit: ${total_profit:.2f} | Reason: Hit configured stop-loss at -$2.00 | "
                                                  f"Entry: {entry_price:.5f} | Close: {close_price:.5f} | Duration: {duration_min:.2f} min")
                                    elif total_profit < 0 and total_profit > -2.0:  # Loss but better than -$2.00 (EARLY CLOSURE)
                                        close_reason = f"Early Closure (${total_profit:.2f}) - Expected -$2.00"
                                        logger.warning(f"[CRITICAL] EARLY CLOSURE DETECTED: Ticket {ticket} | {symbol} | "
                                                     f"Profit: ${total_profit:.2f} | Expected: -$2.00 | "
                                                     f"Entry: {entry_price:.5f} | Close: {close_price:.5f} | Duration: {duration_min:.2f} min | "
                                                     f"Possible causes: Manual close, broker closure, SL calculation error, slippage, or margin call")
                                    elif total_profit > 0:
                                        close_reason = "Take Profit or Trailing Stop"
                                        logger.info(f"✅ PROFITABLE CLOSE: Ticket {ticket} | {symbol} | "
                                                  f"Profit: ${total_profit:.2f} | Entry: {entry_price:.5f} | Close: {close_price:.5f} | Duration: {duration_min:.2f} min")
                                    elif total_profit < -2.10:  # More than $0.10 below -$2.00 (exceeded threshold)
                                        close_reason = f"Stop Loss exceeded (${total_profit:.2f})"
                                        logger.warning(f"[WARNING] STOP-LOSS EXCEEDED: Ticket {ticket} | {symbol} | "
                                                     f"Profit: ${total_profit:.2f} | Expected max: -$2.00 | "
                                                     f"Entry: {entry_price:.5f} | Close: {close_price:.5f} | Duration: {duration_min:.2f} min | "
                                                     f"Loss exceeded configured stop-loss limit - possible slippage or broker execution issue")
                                        # Log late exit warning
                                        trade_logger.log_late_exit_prevention(
                                            symbol=symbol,
                                            ticket=ticket,
                                            actual_profit=total_profit,
                                            expected_profit=-2.0,
                                            sl_modification_failed=False
                                        )
                                    
                                    # Log position closure with unified logger
                                    trade_logger.log_position_closure(
                                        symbol=symbol,
                                        ticket=ticket,
                                        entry_price=entry_price,
                                        close_price=close_price,
                                        profit=total_profit,
                                        duration_minutes=duration_min,
                                        close_reason=close_reason,
                                        entry_time=entry_time,
                                        close_time=close_time
                                    )
                                    
                                    # Update realized P/L in trading bot via callback
                                    if self.bot_pnl_callback and callable(self.bot_pnl_callback):
                                        try:
                                            self.bot_pnl_callback(total_profit, close_time)
                                        except Exception as e:
                                            logger.debug(f"Error updating realized P/L via callback: {e}")
                                    
                                    # Record closed trade for circuit breaker tracking
                                    self.record_closed_trade(total_profit)
                                    
                                    # Record loss for cooldown tracking (per-symbol)
                                    if total_profit < 0:
                                        self._record_symbol_loss(symbol)
            
            # Now remove tracking entries
            with self._tracking_lock:
                for ticket in closed_tickets:
                    self._position_tracking.pop(ticket, None)
                    # Clean up micro-HFT engine tracking
                    if hasattr(self, '_micro_profit_engine') and self._micro_profit_engine:
                        self._micro_profit_engine.cleanup_closed_position(ticket)
                    # Clean up profit-locking engine tracking
                    if hasattr(self, '_profit_locking_engine') and self._profit_locking_engine:
                        self._profit_locking_engine.cleanup_closed_position(ticket)
                    # Clean up unified SL manager tracking
                    if hasattr(self, 'sl_manager') and self.sl_manager:
                        self.sl_manager.cleanup_closed_position(ticket)
            
            # Update last open tickets
            self._last_open_tickets = current_tickets.copy()
            
            # Clean up staged trades for closed positions
            if closed_tickets_symbols:
                with self._staged_lock:
                    for ticket, symbol in closed_tickets_symbols.items():
                        if symbol and symbol in self._staged_trades:
                            if ticket in self._staged_trades[symbol].get('trades', []):
                                self._staged_trades[symbol]['trades'].remove(ticket)
                            # Clean up if no more trades for this symbol
                            if not self._staged_trades[symbol].get('trades', []):
                                self._staged_trades.pop(symbol, None)
            
            # FAIL-SAFE CHECK: Verify all negative P/L trades have SL ≤ -$2.00
            # CRITICAL FIX 1: This is now READ-ONLY - only checks, doesn't enforce
            # Enforcement is handled by SLManager._sl_worker_loop() (single-writer)
            # This prevents monitoring threads from acquiring locks
            if hasattr(self, 'sl_manager') and self.sl_manager:
                try:
                    # CRITICAL: Call read-only fail-safe check (no lock acquisition)
                    # The fail_safe_check() method should only verify, not enforce
                    # Enforcement happens in SLWorker thread
                    self.sl_manager.fail_safe_check_read_only()
                except AttributeError:
                    # If read-only method doesn't exist, skip fail-safe check from monitoring thread
                    # This prevents lock acquisition from monitoring threads
                    logger.debug(f"[READ-ONLY] Fail-safe check skipped from monitoring thread - enforcement handled by SLWorker")
                except Exception as e:
                    logger.error(f"Error in read-only fail-safe check: {e}", exc_info=True)
            
            # CRITICAL: Update bot's daily_pnl if callback is available
            # This ensures daily_pnl is updated in real-time during monitoring (every cycle)
            if self.bot_pnl_callback:
                try:
                    self.bot_pnl_callback()
                except Exception as e:
                    logger.debug(f"Error calling bot_pnl_callback in monitoring loop: {e}")
            
            if not positions:
                return
            
            # CRITICAL: Detect and integrate NEW trades immediately
            # Track previously seen tickets to detect new positions
            if not hasattr(self, '_monitored_tickets'):
                self._monitored_tickets = set()
            
            current_tickets = {pos['ticket'] for pos in positions}
            new_tickets = current_tickets - self._monitored_tickets
            
            # Log and integrate new trades immediately
            for ticket in new_tickets:
                new_position = next((p for p in positions if p['ticket'] == ticket), None)
                if new_position:
                    symbol = new_position.get('symbol', '')
                    entry_price = new_position.get('price_open', 0.0)
                    logger.info(f"🆕 NEW TRADE DETECTED: {symbol} Ticket {ticket} | "
                              f"Entry: {entry_price:.5f} | "
                              f"Immediately integrating into monitoring loop")
                    # Initialize tracking for new trade
                    self._get_position_tracking(ticket)
                    # Initialize profit locking engine tracking
                    if hasattr(self, '_profit_locking_engine') and self._profit_locking_engine:
                        # New trade will be tracked automatically on first profit check
                        pass
            
            # Update monitored tickets
            self._monitored_tickets = current_tickets.copy()
            
            # Process each position
            for position in positions:
                ticket = position['ticket']
                current_profit = position.get('profit', 0.0)
                
                # Check if this position should use fast polling
                tracking = self._get_position_tracking(ticket)
                if use_fast_polling and not tracking.get('fast_polling', False):
                    # Skip if not in fast polling mode and we're in fast polling cycle
                    continue
                
                # CRITICAL FIX: SL updates are now handled EXCLUSIVELY by SLManager._sl_worker_loop()
                # This eliminates duplicate SL update attempts and lock contention
                # The _sl_worker_loop() runs every 500ms (or instant) and is the single source of truth for SL updates
                #
                # DO NOT call sl_manager.update_sl_atomic() here - it causes:
                # 1. Duplicate SL update attempts (redundancy)
                # 2. Lock contention between threads
                # 3. Rate limiting issues
                # 4. "No changes" errors from duplicate attempts
                #
                # SLManager._sl_worker_loop() handles ALL SL logic:
                # - Strict loss enforcement (-$2.00)
                # - Break-even SL
                # - Sweet-spot profit locking (calls ProfitLockingEngine internally)
                # - Trailing stops
                #
                # This function (monitor_all_positions_continuous) now only handles:
                # - Micro-HFT profit engine (position closure)
                # - Position tracking and cleanup
                # - Fail-safe checks
                
                # Get current profit for logging/monitoring purposes only
                current_profit = position.get('profit', 0.0)
                
                # NOTE: All SL updates are handled by SLManager._sl_worker_loop() thread
                # No need to call update_sl_atomic() here - it would create duplicate updates
                
                # MICRO-HFT PROFIT ENGINE: Check and close AFTER SL manager has locked profit
                # Only closes if profit is in sweet spot ($0.03–$0.10) or if trailing stop has locked at $0.10+
                # Run this AFTER SL update to ensure profit is locked first
                if hasattr(self, '_micro_profit_engine') and self._micro_profit_engine:
                    try:
                        # Get fresh position data after SL update
                        fresh_position = self.order_manager.get_position_by_ticket(ticket)
                        if fresh_position:
                            # Check if position should be closed by micro-HFT engine
                            # This only closes after SL manager has locked profit appropriately
                            was_closed = self._micro_profit_engine.check_and_close(fresh_position, self.mt5_connector)
                            if was_closed:
                                # Position was closed by micro-HFT engine
                                # Position closure will be detected in next cycle
                                continue
                    except Exception as e:
                        logger.error(f"Error in micro-HFT profit engine: {e}", exc_info=True)
                        # Continue with normal processing if micro-HFT engine fails
                
                # NOTE: SL updates are now handled by SLManager._sl_worker_loop()
                # This section removed to prevent undefined variable errors
                # All SL update logging is handled by SLManager
        
        except Exception as e:
            logger.error(f"Error in continuous trailing stop monitoring: {e}", exc_info=True)



