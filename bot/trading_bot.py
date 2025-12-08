"""
Main Trading Bot Orchestrator
Coordinates all modules and executes trading logic.
"""

import logging
import time
import json
import os
import random
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager, OrderType
from strategies.trend_filter import TrendFilter
from risk.risk_manager import RiskManager
from risk.pair_filter import PairFilter
from risk.halal_compliance import HalalCompliance
from news_filter.news_api import NewsFilter
from bot.config_validator import ConfigValidator
from bot.logger_setup import get_symbol_logger

logger = logging.getLogger(__name__)


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
        
        # P&L tracking
        self.daily_pnl = 0.0
        self.last_pnl_reset = datetime.now().date()
        self.trade_count_today = 0
        
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
            'failed_trades': 0,
            'total_spread_paid': 0.0,
            'total_commission_paid': 0.0
        }
        
        # Continuous trailing stop thread
        self.trailing_stop_thread = None
        self.trailing_stop_running = False
        self.risk_config = self.config.get('risk', {})
        self.trailing_cycle_interval = self.risk_config.get('trailing_cycle_interval_seconds', 3.0)
        
        # Fast trailing thread (for positions with profit >= threshold)
        self.fast_trailing_thread = None
        self.fast_trailing_running = False
        self.fast_trailing_interval_ms = self.risk_config.get('fast_trailing_interval_ms', 300)
        self.fast_trailing_threshold_usd = self.risk_config.get('fast_trailing_threshold_usd', 0.10)
    
    def connect(self) -> bool:
        """Connect to MT5."""
        logger.info("Connecting to MT5...")
        if self.mt5_connector.connect():
            logger.info("MT5 connection established")
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
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")
        
        # Close all positions if needed
        positions = self.order_manager.get_open_positions()
        for position in positions:
            logger.warning(f"Closing position {position['ticket']} due to kill switch")
            self.order_manager.close_position(position['ticket'], comment="Kill switch activated")
    
    def handle_error(self, error: Exception, context: str = ""):
        """Handle errors with supervisor logic."""
        self.consecutive_errors += 1
        self.last_error_time = datetime.now()
        
        logger.error(f"Error in {context}: {error}", exc_info=True)
        
        if self.supervisor_enabled:
            if self.consecutive_errors >= self.max_consecutive_errors:
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
            logger.info(f"   Trades Executed: {self.trade_count_today}")
            logger.info(f"   Trade Statistics:")
            logger.info(f"     - Total Trades: {self.trade_stats['total_trades']}")
            logger.info(f"     - Successful: {self.trade_stats['successful_trades']}")
            logger.info(f"     - Failed: {self.trade_stats['failed_trades']}")
            if self.trade_stats['total_trades'] > 0:
                success_rate = (self.trade_stats['successful_trades'] / self.trade_stats['total_trades']) * 100
                logger.info(f"     - Success Rate: {success_rate:.1f}%")
            if self.trade_stats['total_spread_paid'] > 0:
                logger.info(f"     - Total Spread Paid: {self.trade_stats['total_spread_paid']:.1f} points")
            logger.info("=" * 60)
            self.daily_pnl = 0.0
            self.trade_count_today = 0
            self.last_pnl_reset = today
        
        # Calculate current P&L from open positions
        positions = self.order_manager.get_open_positions()
        current_pnl = sum([p['profit'] for p in positions])
        
        # Get account info for total equity change
        account_info = self.mt5_connector.get_account_info()
        if account_info:
            # This is a simplified calculation
            # In production, track starting balance vs current equity
            pass
    
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
                    
                    # 1. Check if news is blocking (but allow trading if API fails)
                    news_blocking = self.news_filter.is_news_blocking(symbol)
                    if news_blocking:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: NEWS BLOCKING (high-impact news within 10 min window)")
                        continue
                    else:
                        logger.debug(f"✅ {symbol}: No news blocking")
                    
                    # 2. Get SIMPLE trend signal (SMA20 vs SMA50 only)
                    trend_signal = self.trend_filter.get_trend_signal(symbol)
                    
                    if trend_signal['signal'] == 'NONE':
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: No trend signal (SMA20 == SMA50 or invalid data)")
                        continue
                    
                    # 2a. Check RSI filter (30-50 range for entries)
                    if self.trend_filter.use_rsi_filter and not trend_signal.get('rsi_filter_passed', True):
                        rsi_value = trend_signal.get('rsi', 50)
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: RSI filter failed (RSI: {rsi_value:.1f} not in range {self.trend_filter.rsi_entry_range_min}-{self.trend_filter.rsi_entry_range_max})")
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
                            continue
                        logger.debug(f"✅ {symbol}: Min lot check passed - {min_lot_reason}")
                    
                    # 4. SIMPLIFIED setup validation (only checks if signal != NONE)
                    if not self.trend_filter.is_setup_valid_for_scalping(symbol, trend_signal):
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Setup validation failed (signal is NONE)")
                        continue
                    
                    # 4a. Check trend strength minimum (SMA separation percentage)
                    sma_separation_pct = abs((trend_signal.get('sma_fast', 0) - trend_signal.get('sma_slow', 0)) / trend_signal.get('sma_slow', 1) * 100) if trend_signal.get('sma_slow', 0) > 0 else 0
                    min_trend_strength_pct = self.trading_config.get('min_trend_strength_pct', 0.05)  # Default 0.05%
                    if sma_separation_pct < min_trend_strength_pct:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Trend strength too weak (SMA separation: {sma_separation_pct:.4f}% < {min_trend_strength_pct}%)")
                        continue
                    
                    # 5. Calculate quality score for trade selection
                    quality_assessment = self.trend_filter.assess_setup_quality(symbol, trend_signal)
                    quality_score = quality_assessment.get('quality_score', 0.0)
                    high_quality_setup = quality_assessment.get('is_high_quality', False)
                    min_quality_score = self.trading_config.get('min_quality_score', 50.0)
                    
                    # Filter by quality score - only trade high-quality setups
                    if quality_score < min_quality_score:
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Quality score {quality_score:.1f} < threshold {min_quality_score} | Details: {', '.join(quality_assessment.get('reasons', []))}")
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
                        symbol_logger = get_symbol_logger(symbol, self.config)
                        symbol_logger.debug(f"⛔ Cannot open trade: {reason}")
                        continue
                    
                    # 7. Check spread
                    spread_points = self.pair_filter.get_spread_points(symbol)
                    if spread_points is None:
                        logger.warning(f"⚠️ {symbol}: Cannot get spread information - skipping")
                        continue
                    
                    # Check spread acceptability
                    if not self.pair_filter.check_spread(symbol):
                        max_spread = self.pair_filter.max_spread_points
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Spread {spread_points:.2f} points > {max_spread} limit")
                        continue
                    
                    # 7a. TESTING MODE: Check spread + fees <= $0.30 (STRICTLY ENFORCED)
                    total_cost = 0.0
                    cost_description = ""
                    if test_mode:
                        # Use min_lot already calculated above
                        if not min_lot_valid:
                            # Already logged above, skip
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
                    logger.info("=" * 80)
                    logger.info(f"[OPPORTUNITY CHECK]")
                    logger.info(f"Symbol: {symbol}")
                    logger.info(f"Signal: {signal_type}")
                    logger.info(f"Quality Score: {quality_score:.1f} (Threshold: {min_quality_score})")
                    logger.info(f"Trend Strength: {sma_separation_pct:.4f}%")
                    logger.info(f"Spread: {spread_points:.2f} points")
                    if test_mode and total_cost > 0:
                        logger.info(f"Fees: {cost_description}")
                        logger.info(f"Total Cost: ${total_cost:.2f} USD (PASS ≤ $0.30)")
                    logger.info(f"Min Lot: {min_lot:.4f}")
                    if calculated_risk > 0:
                        logger.info(f"Calculated Lot: {min_lot:.4f} (PASS)")
                        logger.info(f"Risk: ${calculated_risk:.2f} (PASS ≤ $2.00)")
                    logger.info(f"Reason: Quality score {quality_score:.1f} >= {min_quality_score} → Trade Executed")
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
    
    def execute_trade(self, opportunity: Dict[str, Any]) -> bool:
        """Execute a trade based on opportunity with randomness factor and comprehensive logging."""
        symbol = opportunity['symbol']
        signal = opportunity['signal']
        
        try:
            # RANDOMNESS FACTOR: Small randomness to prevent trading every single candle
            # But NOT too restrictive - should allow frequent trading
            random_value = random.random()  # 0.0 to 1.0
            
            if random_value < self.randomness_factor:
                logger.info(f"🎲 {symbol}: RANDOMNESS SKIP - Random value {random_value:.3f} < threshold {self.randomness_factor} (skipping trade to avoid over-trading)")
                return False
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
                        return False
            
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
            
            # Calculate lot size - ALWAYS use minimum lot size per symbol (per user requirement)
            # Get minimum lot size for this symbol
            symbol_info_for_lot = self.mt5_connector.get_symbol_info(symbol)
            if not symbol_info_for_lot:
                logger.error(f"❌ {symbol}: Cannot get symbol info for lot size calculation")
                self.trade_stats['failed_trades'] += 1
                return False
            
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
            
            # Round to volume step
            volume_step = symbol_info_for_lot.get('volume_step', 0.01)
            if volume_step > 0:
                effective_min_lot = round(effective_min_lot / volume_step) * volume_step
                if effective_min_lot < volume_step:
                    effective_min_lot = volume_step
            
            # Use minimum lot size (per user requirement: always use minimum lot size)
            lot_size = effective_min_lot
            
            # Verify risk doesn't exceed $2 USD with minimum lot
            point = symbol_info_for_lot.get('point', 0.00001)
            pip_value = point * 10 if symbol_info_for_lot.get('digits', 5) == 5 or symbol_info_for_lot.get('digits', 3) == 3 else point
            contract_size = symbol_info_for_lot.get('contract_size', 1.0)
            stop_loss_price = stop_loss_pips * pip_value
            actual_risk = lot_size * stop_loss_price * contract_size
            
            if actual_risk > self.risk_manager.max_risk_usd:
                logger.warning(f"⚠️ {symbol}: Using minimum lot {lot_size:.4f} results in risk ${actual_risk:.2f} > ${self.risk_manager.max_risk_usd:.2f} (using minimum anyway per requirement)")
            
            # Log trade execution with lot size details
            logger.info(f"📦 {symbol}: EXECUTING WITH MINIMUM LOT SIZE = {lot_size:.4f} | "
                       f"Source: {min_source} | "
                       f"Estimated Risk: ${actual_risk:.2f} | "
                       f"Policy: Always use minimum lot size per symbol")
            
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
            
            # Place order with retry logic (ensure execution within 0.3 seconds)
            logger.info(f"📤 {symbol}: Placing {signal} order (Lot: {lot_size}, SL: {stop_loss_pips:.1f} pips)...")
            max_retries = 3
            ticket = None
            volume_error_occurred = False
            execution_timeout = self.trading_config.get('execution_timeout_seconds', 0.3)
            execution_start = time.time()
            
            for attempt in range(max_retries):
                # Check if we're within execution timeout
                elapsed = time.time() - execution_start
                if elapsed > execution_timeout:
                    logger.warning(f"⚠️ {symbol}: Execution timeout ({elapsed:.3f}s > {execution_timeout}s), attempting final order...")
                
                # Ensure MT5 is connected before placing order
                if not self.mt5_connector.ensure_connected():
                    logger.warning(f"⚠️ {symbol}: MT5 disconnected, attempting reconnect...")
                    if not self.mt5_connector.reconnect():
                        logger.error(f"❌ {symbol}: Failed to reconnect to MT5")
                        if attempt < max_retries - 1:
                            time.sleep(2)
                            continue
                        else:
                            break
                
                ticket = self.order_manager.place_order(
                    symbol=symbol,
                    order_type=order_type,
                    lot_size=lot_size,
                    stop_loss=stop_loss_pips,
                    comment=f"Bot {signal}"
                )
                
                if ticket and ticket > 0:
                    elapsed = time.time() - execution_start
                    if elapsed > execution_timeout:
                        logger.warning(f"⚠️ {symbol}: Order placed but exceeded timeout ({elapsed:.3f}s > {execution_timeout}s)")
                    else:
                        logger.debug(f"✅ {symbol}: Order placed within timeout ({elapsed:.3f}s < {execution_timeout}s)")
                    break
                
                # Check if volume error occurred (special return value -1)
                if ticket == -1:
                    volume_error_occurred = True
                    logger.warning(f"⚠️ {symbol}: Invalid volume error (lot: {lot_size:.4f}), retrying with broker minimum lot...")
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
                
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ {symbol}: Order placement failed (attempt {attempt + 1}/{max_retries}), retrying...")
                    time.sleep(1)
            
            if ticket:
                # Register staged trade
                self.risk_manager.register_staged_trade(symbol, ticket, signal)
                
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
                
                # Log to root (minimal) and symbol logger (detailed)
                spread_fees_log = f" | Spread+Fees: ${spread_fees_cost:.2f}" if test_mode and spread_fees_cost > 0 else ""
                logger.info(f"✅ TRADE EXECUTED: {symbol} {signal} | Ticket: {ticket} | "
                           f"Entry: {entry_price:.5f} | Lot: {lot_size:.4f} (min: {effective_min_lot:.4f} {min_source}) | "
                           f"SL: {stop_loss_pips:.1f}pips{spread_fees_log} | Reason: Minimum lot for $2.0 risk")
                
                symbol_logger = get_symbol_logger(symbol, self.config)
                symbol_logger.info("=" * 80)
                symbol_logger.info(f"✅ TRADE EXECUTED SUCCESSFULLY")
                symbol_logger.info(f"   Symbol: {symbol}")
                symbol_logger.info(f"   Direction: {signal}")
                symbol_logger.info(f"   Ticket: {ticket}")
                symbol_logger.info(f"   Entry Price: {entry_price:.5f}")
                symbol_logger.info(f"   Lot Size: {lot_size:.4f} (minimum possible for $2.0 risk, respecting broker minimum)")
                symbol_logger.info(f"   Min Lot Size: {effective_min_lot:.4f} (source: {min_source})")
                symbol_logger.info(f"   Reason: Testing mode - minimum lot size calculated from risk, respecting broker minimum")
                symbol_logger.info(f"   Stop Loss: {stop_loss_pips:.1f} pips")
                symbol_logger.info(f"   Spread: {spread_points:.1f} points")
                if test_mode and spread_fees_cost > 0:
                    symbol_logger.info(f"   Spread+Fees Cost: ${spread_fees_cost:.2f} ({spread_fees_desc})")
                symbol_logger.info(f"   SMA20: {opportunity.get('sma_fast', 0):.5f}")
                symbol_logger.info(f"   SMA50: {opportunity.get('sma_slow', 0):.5f}")
                symbol_logger.info(f"   RSI: {opportunity.get('rsi', 50):.1f}")
                symbol_logger.info("=" * 80)
                
                self.trade_count_today += 1
                self.trade_stats['total_trades'] += 1
                self.trade_stats['successful_trades'] += 1
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
        logger.info(f"🔄 Continuous trailing stop monitor started (interval: {self.trailing_cycle_interval}s)")
        
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
    
    def run_cycle(self):
        """Execute one trading cycle."""
        if self.check_kill_switch():
            return
        
        # Ensure MT5 connection with retry logic
        if not self.mt5_connector.ensure_connected():
            logger.warning("MT5 not connected, attempting reconnect...")
            reconnect_success = False
            for reconnect_attempt in range(3):
                if self.mt5_connector.reconnect():
                    reconnect_success = True
                    logger.info(f"✅ MT5 reconnected successfully (attempt {reconnect_attempt + 1})")
                    break
                else:
                    logger.warning(f"⚠️ Reconnection attempt {reconnect_attempt + 1}/3 failed, retrying...")
                    time.sleep(2)
            
            if not reconnect_success:
                logger.error("❌ MT5 reconnection failed after 3 attempts")
                self.handle_error(Exception("MT5 reconnection failed after 3 attempts"), "Connection check")
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
                
                # Sort by quality score (highest first), then by spread+fees cost (lowest first) for prioritization
                # This ensures we execute highest quality setups first, then most cost-effective
                def get_priority(opp):
                    quality = opp.get('quality_score', 0.0)
                    cost = opp.get('spread_fees_cost')
                    if cost is not None and cost > 0:
                        # Negative quality for descending sort, positive cost for ascending sort
                        return (-quality, cost)
                    # Fallback: use quality score primarily
                    spread = opp.get('spread', 999999)
                    return (-quality, spread * 0.0001 + 1000000)
                
                opportunities.sort(key=get_priority)
                
                # Log sorted order for verification
                if test_mode and opportunities:
                    logger.info("📊 Opportunities sorted by spread+fees (ascending):")
                    for idx, opp in enumerate(opportunities[:10], 1):  # Show first 10
                        cost = opp.get('spread_fees_cost', 0.0)
                        spread = opp.get('spread', 0)
                        logger.info(f"  {idx}. {opp['symbol']} {opp['signal']}: ${cost:.2f} (spread: {spread:.1f}pts)")
                
                # Log opportunity table header (testing mode)
                if test_mode:
                    logger.info("=" * 100)
                    logger.info("OPPORTUNITY TABLE:")
                    logger.info(f"{'Symbol':<12} | {'Signal':<6} | {'MinLot':<8} | {'Spread':<10} | {'Fees':<10} | {'Status':<20} | {'Reason'}")
                    logger.info("-" * 100)
                
                logger.info(f"🎯 Found {len(opportunities)} opportunity(ies) - evaluating all symbols (sorted by spread+fees cost, lowest first)")
                
                # Get current position count
                current_positions = self.order_manager.get_position_count()
                max_trades = self.risk_manager.max_open_trades
                trades_executed = 0
                trades_skipped = 0
                
                for opportunity in opportunities:
                    # Always get fresh position count to prevent exceeding max
                    current_positions = self.order_manager.get_position_count()
                    
                    # Check if we've reached max open trades
                    if current_positions >= max_trades:
                        logger.info(f"⏸️  Max open trades ({max_trades}) reached - skipping remaining opportunities")
                        trades_skipped += len(opportunities) - opportunities.index(opportunity)
                        break
                    
                    symbol = opportunity['symbol']
                    signal = opportunity['signal']
                    spread = opportunity.get('spread', 0)
                    min_lot = opportunity.get('min_lot', 0.01)
                    spread_fees_cost = opportunity.get('spread_fees_cost', 0.0)
                    quality_score = opportunity.get('quality_score', 0.0)
                    
                    # Prepare fees string for logging
                    fees_str = f"${spread_fees_cost:.2f}" if spread_fees_cost > 0 else "N/A"
                    
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
                    if self.execute_trade(opportunity):
                        if test_mode:
                            logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'PASS - EXECUTED':<20} | Trade executed")
                        logger.info(f"✅ {symbol}: Trade execution completed successfully")
                        trades_executed += 1
                        # DO NOT manually increment - always use get_position_count() in next iteration
                    else:
                        if test_mode:
                            logger.info(f"{symbol:<12} | {signal:<6} | Q:{quality_score:.1f} | {min_lot:<8.4f} | {spread:<10.1f}pts | {fees_str:<10} | {'SKIP':<20} | Trade execution failed")
                        logger.info(f"⛔ [SKIP] {symbol} | Reason: Trade execution failed (check logs above)")
                        trades_skipped += 1
                
                # Close opportunity table (testing mode)
                if test_mode:
                    logger.info("-" * 100)
                
                # Get final position count
                final_position_count = self.order_manager.get_position_count()
                logger.info(f"📊 Cycle Summary: {trades_executed} executed, {trades_skipped} skipped, {final_position_count}/{max_trades} positions open")
            else:
                logger.info("ℹ️  No trading opportunities found this cycle (check logs above for reasons)")
            
            self.reset_error_count()
        
        except Exception as e:
            self.handle_error(e, "Trading cycle")
    
    def run(self, cycle_interval_seconds: int = 60):
        """Run the trading bot continuously."""
        self.running = True
        logger.info(f"Trading bot started. Cycle interval: {cycle_interval_seconds}s")
        
        # Start continuous trailing stop monitor
        self.start_continuous_trailing_stop()
        
        try:
            while self.running:
                self.run_cycle()
                time.sleep(cycle_interval_seconds)
        
        except KeyboardInterrupt:
            logger.info("Trading bot stopped by user")
        except Exception as e:
            self.handle_error(e, "Main loop")
            logger.critical("Fatal error in main loop, shutting down")
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Shutdown the bot gracefully."""
        logger.info("Shutting down trading bot...")
        self.running = False
        
        # Stop continuous trailing stop monitor
        self.stop_continuous_trailing_stop()
        
        self.mt5_connector.shutdown()
        logger.info("Trading bot shutdown complete")

