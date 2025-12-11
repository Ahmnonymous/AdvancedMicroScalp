"""
Micro-HFT Profit Engine
Closes positions immediately when profit is within the $0.03–$0.10 sweet spot range.
This is an ADD-ON module that does not interfere with existing risk management.
"""

import time
from typing import Dict, Any, Optional
from datetime import datetime
import MetaTrader5 as mt5

from utils.logger_factory import get_logger

logger = get_logger("hft_engine", "logs/engine/hft_engine.log")


class MicroProfitEngine:
    """
    Micro-HFT Profit Engine for instant profit taking in sweet spot range.
    
    This engine runs in the monitoring loop and closes positions immediately
    when profit is within $0.03–$0.10 (sweet spot range). It does NOT interfere with:
    - Stop loss enforcement (-$2.00)
    - Trailing stop engine
    - Risk calculations
    - Position closure tracking
    """
    
    def __init__(self, config: Dict[str, Any], order_manager, trade_logger, risk_manager=None):
        """
        Initialize Micro-HFT Profit Engine.
        
        Args:
            config: Configuration dictionary
            order_manager: OrderManager instance for closing positions
            trade_logger: TradeLogger instance for logging
            risk_manager: RiskManager instance (optional, for SL verification)
        """
        self.config = config
        self.order_manager = order_manager
        self.trade_logger = trade_logger
        self.risk_manager = risk_manager
        
        # Configuration
        micro_config = config.get('micro_profit_engine', {})
        self.enabled = micro_config.get('enabled', True)
        self.min_profit_threshold_usd = micro_config.get('min_profit_threshold_usd', 0.03)
        self.max_profit_threshold_usd = micro_config.get('max_profit_threshold_usd', 0.10)
        self.max_retries = micro_config.get('max_retries', 3)
        self.retry_delay_ms = micro_config.get('retry_delay_ms', 10)  # 10ms between retries
        
        # CRITICAL: Minimum profit buffer to account for spread/slippage
        # This ensures actual closing profit will be positive even after spread/slippage
        self.min_profit_buffer = 0.05  # $0.05 buffer to account for spread/slippage
        
        # Track positions being closed to prevent duplicate attempts
        self._closing_tickets = set()
        self._last_check_time = {}  # {ticket: timestamp} for rate limiting
    
    def check_and_close(self, position: Dict[str, Any], mt5_connector) -> bool:
        """
        Check if position should be closed and close it if profit is in sweet spot range.
        
        This method:
        - Only closes positions with profit >= $0.03 and <= $0.10 (sweet spot)
        - Uses actual MT5 profit (not estimated)
        - Closes immediately without delays
        - Retries on errors
        - Does NOT interfere with SL or trailing stops
        - NEVER closes trades in loss or at zero profit
        
        CRITICAL SAFETY: Multiple validation checkpoints ensure no negative-profit closures.
        
        Args:
            position: Position dictionary from order_manager (MUST be fresh data)
            mt5_connector: MT5Connector instance for getting fresh data
        
        Returns:
            True if position was closed, False otherwise
        """
        if not self.enabled:
            return False
        
        ticket = position.get('ticket')
        if not ticket:
            return False
        
        # Prevent duplicate close attempts
        if ticket in self._closing_tickets:
            return False
        
        # CRITICAL FIX 4.1: Get FRESHEST profit from MT5 position data
        # Do not trust cached or stale position data
        # Get actual profit from MT5 (not estimated) - use position parameter which should be fresh
        current_profit = position.get('profit', 0.0)
        
        # CRITICAL FIX 1.1: Strict negative-profit rejection BEFORE sweet-spot logic
        # If profit is zero or negative, immediately reject - never process sweet-spot logic
        # This is the FIRST checkpoint to prevent any negative profit processing
        # CRITICAL RULE: If P/L is in loss zone, NO early closure - trade will only close at -$2.00 stop loss
        
        # CRITICAL FIX: Account for spread/slippage - require minimum profit buffer
        # Spread can cause profit to go negative on close, so require profit > buffer to account for spread
        # This ensures actual closing profit will be positive even after spread/slippage
        if current_profit <= self.min_profit_buffer:
            logger.debug(f"Micro-HFT: Ticket {ticket} has profit ${current_profit:.2f} <= ${self.min_profit_buffer:.2f} buffer - "
                       f"REJECTING - Insufficient profit to cover spread/slippage (CHECKPOINT 1)")
            return False
        
        # CRITICAL: Verify SL is applied and verified before closing
        # Only close if sweet-spot SL is verified by broker
        # This prevents premature closures before profit is locked
        if hasattr(self, 'risk_manager') and self.risk_manager:
            if hasattr(self.risk_manager, '_profit_locking_engine') and self.risk_manager._profit_locking_engine:
                profit_locking_engine = self.risk_manager._profit_locking_engine
                # Check if trade is in sweet spot
                if self.min_profit_threshold_usd <= current_profit <= self.max_profit_threshold_usd:
                    # Trade is in sweet spot - verify SL is applied
                    if hasattr(profit_locking_engine, 'is_sl_verified'):
                        is_verified = profit_locking_engine.is_sl_verified(ticket)
                        if not is_verified:
                            logger.debug(f"Micro-HFT: Ticket {ticket} in sweet spot but SL not verified - waiting for verification before closing")
                            return False
            
            # CRITICAL SAFEGUARD: Check effective SL to ensure we're not closing a losing trade
            # If effective SL is negative (loss protection), NEVER close
            try:
                # Use new SL manager if available, otherwise fall back to legacy method
                if hasattr(self.risk_manager, 'sl_manager') and self.risk_manager.sl_manager:
                    effective_sl_profit = self.risk_manager.sl_manager.get_effective_sl_profit(position)
                else:
                    effective_sl_profit, _ = self.risk_manager.calculate_effective_sl_in_profit_terms(position, check_pending=False)
                
                if effective_sl_profit < 0:
                    logger.debug(f"Micro-HFT: Ticket {ticket} has loss protection SL (${effective_sl_profit:.2f}) - NEVER closing")
                    return False
            except Exception as e:
                logger.warning(f"Micro-HFT: Could not calculate effective SL for ticket {ticket}: {e}")
                # If we can't calculate, err on the side of caution - don't close
                return False
        
        # ENHANCED: Smart profit locking strategy - MAXIMUM PROFIT CAPTURE
        # Rule 1: NEVER close if profit > $0.10 unless trailing stop has locked it at that level or higher
        # Rule 2: ONLY close in sweet spot ($0.03–$0.10) if trailing stop hasn't locked profit yet
        # Rule 3: If profit is $0.20, $0.30, $0.40, etc., wait for trailing stop to lock at $0.10, $0.20, $0.30 respectively
        # Rule 4: Once trailing stop locks at a level, Micro-HFT can close at that locked level if profit drops back to it
        # Rule 5: Never close below $0.03 (let stop-loss handle it)
        
        # Check if trailing stop has locked profit at $0.10 or higher
        sl_price = position.get('sl', 0.0)
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        symbol = position.get('symbol', '')
        
        # Calculate SL profit (lock level) - how much profit is locked by trailing stop
        sl_profit_locked = 0.0
        if sl_price > 0 and entry_price > 0:
            symbol_info = mt5_connector.get_symbol_info(symbol, check_price_staleness=False) if mt5_connector else None
            if symbol_info:
                point = symbol_info.get('point', 0.00001)
                pip_value = point * 10 if symbol_info.get('digits', 5) in [5, 3] else point
                contract_size = symbol_info.get('contract_size', 1.0)
                lot_size = position.get('volume', 0.01)
                
                # Calculate SL distance in price terms
                # For BUY: SL below entry = profit locked (entry_price - sl_price is positive)
                # For SELL: SL above entry = profit locked (sl_price - entry_price is positive)
                if order_type == 'BUY':
                    sl_distance_price = entry_price - sl_price  # Positive if SL is below entry (profit locked)
                else:  # SELL
                    sl_distance_price = sl_price - entry_price  # Positive if SL is above entry (profit locked)
                
                # Calculate locked profit in USD
                # sl_distance_price is already in the correct direction (positive = profit locked)
                sl_profit_locked = lot_size * abs(sl_distance_price) * contract_size
        
        # ENHANCED LOGIC: Maximum Profit Capture Strategy
        # NEVER close trades at lower profits when they've reached higher levels
        # The trailing stop should lock profits at $0.10 increments when profit exceeds those levels
        
        # Determine what level the trailing stop should have locked at based on current profit
        # If profit is $0.40, trailing stop should lock at $0.30 (one increment below)
        # If profit is $0.30, trailing stop should lock at $0.20
        # If profit is $0.20, trailing stop should lock at $0.10
        # If profit is $0.10, trailing stop should lock at $0.00 (breakeven)
        
        increment_level = int(current_profit / 0.10) if current_profit >= 0.10 else 0
        expected_lock_level = max(0.0, (increment_level - 1) * 0.10) if increment_level >= 1 else 0.0
        
        # Determine if we should close based on profit level and trailing stop lock
        should_close = False
        
        # CRITICAL: Re-verify profit is above buffer after all calculations
        # This is a safety net in case profit changed during symbol info retrieval
        if current_profit <= self.min_profit_buffer:
            logger.debug(f"Micro-HFT: Ticket {ticket} profit ${current_profit:.2f} <= ${self.min_profit_buffer:.2f} buffer after calculations - rejecting")
            return False
        
        # Case 1: Sweet spot ($0.03–$0.10) - can close immediately ONLY if:
        # - Trailing stop hasn't locked at $0.10 yet (profit hasn't exceeded sweet spot)
        # - OR profit dropped back to sweet spot but trailing stop has locked at a lower level
        in_sweet_spot = (self.min_profit_threshold_usd <= current_profit <= self.max_profit_threshold_usd)
        
        if in_sweet_spot:
            # Only close in sweet spot if trailing stop hasn't locked at $0.10 yet
            # This means profit never exceeded $0.10, so we can safely close in sweet spot
            if sl_profit_locked < 0.08:  # Trailing stop hasn't locked at $0.10 yet
                should_close = True
                logger.debug(f"Micro-HFT: Ticket {ticket} in sweet spot ${current_profit:.2f}, trailing stop not locked - can close")
            else:
                # Trailing stop has locked at $0.10+ (profit was higher before)
                # Wait for profit to drop back to locked level before closing
                logger.debug(f"Micro-HFT: Ticket {ticket} in sweet spot ${current_profit:.2f} but trailing stop locked at ${sl_profit_locked:.2f} - waiting for profit to reach locked level")
                should_close = False
        
        # Case 2: Profit > $0.10 - NEVER close until trailing stop has locked at expected level
        # If profit is $0.40, wait until trailing stop locks at $0.30 before allowing close
        elif current_profit > 0.10:
            # Check if profit is still above the expected lock level
            # If profit is $0.40 and expected lock is $0.30, wait until profit drops to $0.30 or trailing stop locks at $0.30
            if current_profit > expected_lock_level + 0.05:  # Still well above expected lock level
                # Profit is still high - NEVER close, wait for trailing stop to lock it
                logger.debug(f"Micro-HFT: Ticket {ticket} profit ${current_profit:.2f} > expected lock ${expected_lock_level:.2f}, current lock ${sl_profit_locked:.2f} - waiting for trailing stop")
                should_close = False
                return False  # Early return to prevent any close attempts
            
            # Profit has dropped close to expected lock level
            # Only close if trailing stop has actually locked at that level or higher
            if sl_profit_locked >= expected_lock_level - 0.05:  # Trailing stop has locked at expected level
                # Check if profit is at a $0.10 multiple (clean level)
                remainder = current_profit % 0.10
                is_at_multiple = remainder < 0.02 or remainder > 0.08  # Within $0.02 of a $0.10 multiple
                
                if is_at_multiple:
                    # Profit is at a clean $0.10 multiple and trailing stop has locked - can close
                    should_close = True
                    logger.debug(f"Micro-HFT: Ticket {ticket} profit ${current_profit:.2f} at lock level ${expected_lock_level:.2f}, trailing stop locked ${sl_profit_locked:.2f} - can close")
                else:
                    # Not at a clean multiple yet - wait
                    should_close = False
                    logger.debug(f"Micro-HFT: Ticket {ticket} profit ${current_profit:.2f} not at clean $0.10 multiple, waiting")
            else:
                # Trailing stop hasn't locked yet - wait
                logger.debug(f"Micro-HFT: Ticket {ticket} profit ${current_profit:.2f}, but trailing stop only locked ${sl_profit_locked:.2f} (expected ${expected_lock_level:.2f}) - waiting")
                should_close = False
                return False  # Early return to prevent premature close
        
        if not should_close:
            return False
        
        # ENHANCEMENT: More aggressive checking for Micro-HFT
        # Reduce rate limiting for faster profit capture
        now = time.time()
        last_check = self._last_check_time.get(ticket, 0)
        if now - last_check < 0.05:  # 50ms minimum between checks (reduced from 100ms)
            return False
        
        self._last_check_time[ticket] = now
        
        # Get fresh position data to ensure profit is still >= threshold
        fresh_position = self.order_manager.get_position_by_ticket(ticket)
        if not fresh_position:
            # Position already closed
            return False
        
        fresh_profit = fresh_position.get('profit', 0.0)
        
        # ENHANCED: Double-check profit with enhanced logic
        # Rule 1: Close if profit is in sweet spot ($0.03–$0.10)
        # Rule 2: Close if profit is a multiple of $0.10 (but not below $0.03)
        # Rule 3: Never close below $0.03 (let stop-loss handle it)
        # Rule 4: Never close if profit < -$2.00 (stop-loss should handle)
        
        # CRITICAL: Never close if at or below stop-loss (-$2.00)
        # Micro-HFT only closes PROFITABLE trades, never losses
        if fresh_profit <= -2.0:
            logger.debug(f"Micro-HFT: Ticket {ticket} at stop-loss (profit: ${fresh_profit:.2f}) - not closing (let stop-loss handle)")
            return False
        
        # Additional safety: Never close if profit is negative or below buffer
        if fresh_profit < self.min_profit_buffer:
            logger.debug(f"Micro-HFT: Ticket {ticket} has profit ${fresh_profit:.2f} < ${self.min_profit_buffer:.2f} buffer - not closing (insufficient to cover spread/slippage)")
            return False
        
        in_sweet_spot = (self.min_profit_threshold_usd <= fresh_profit <= self.max_profit_threshold_usd)
        is_multiple_of_ten_cents = (fresh_profit > self.max_profit_threshold_usd and 
                                     abs(fresh_profit % 0.10) < 0.005)  # Allow small floating point errors
        
        if not (in_sweet_spot or is_multiple_of_ten_cents):
            return False
        
        # Mark as closing to prevent duplicate attempts
        self._closing_tickets.add(ticket)
        
        try:
            # Get position details for logging
            symbol = fresh_position.get('symbol', '')
            entry_price = fresh_position.get('price_open', 0.0)
            current_price = fresh_position.get('price_current', 0.0)
            
            # Get fresh tick data for accurate close price
            if mt5_connector and mt5_connector.ensure_connected():
                symbol_info = mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
                if symbol_info:
                    # Determine close price based on order type
                    order_type = fresh_position.get('type', '')
                    if order_type == 'BUY':
                        close_price_expected = symbol_info.get('bid', current_price)
                    else:  # SELL
                        close_price_expected = symbol_info.get('ask', current_price)
                else:
                    close_price_expected = current_price
            else:
                close_price_expected = current_price
            
            # Calculate spread for logging
            spread_points = 0.0
            if symbol_info:
                spread_points = symbol_info.get('spread', 0) * (10 if symbol_info.get('digits', 5) in [5, 3] else 1)
            
            # CRITICAL FIX 5.1: Use ONLY freshest MT5 position data for all decisions
            # Never use cached, stale, or estimated profit values
            # All profit checks must use data queried directly from MT5
            
            # ENHANCEMENT: More aggressive retry logic for Micro-HFT
            # Attempt to close position with fast retry logic
            execution_start = time.time()
            close_success = False
            
            # Use fresh profit for closing (most up-to-date from fresh_position query)
            target_profit = fresh_profit
            
            # Log initial decision for audit trail
            logger.info(f"Micro-HFT: Ticket {ticket} | Initial decision profit: ${target_profit:.2f} | "
                      f"Sweet spot: {self.min_profit_threshold_usd} - {self.max_profit_threshold_usd}")
            
            for attempt in range(self.max_retries):
                # Get latest position data before each attempt
                latest_position = self.order_manager.get_position_by_ticket(ticket)
                if not latest_position:
                    # Position already closed
                    close_success = True
                    break
                
                latest_profit = latest_position.get('profit', 0.0)
                
                # ENHANCEMENT: Be more flexible with profit range during execution
                # Rule 1: Close if in sweet spot ($0.03–$0.10)
                # Rule 2: Close if multiple of $0.10 (but not below $0.03)
                # Rule 3: Never close below $0.03 or at stop-loss
                
                # CRITICAL FIX 4.1: Never close if at stop-loss or below profit buffer
                # Micro-HFT only closes PROFITABLE trades, never losses
                # This is CHECKPOINT 2 in the retry loop
                if latest_profit <= -2.0:
                    logger.debug(f"Micro-HFT: Ticket {ticket} at stop-loss (profit: ${latest_profit:.2f}) - not closing (CHECKPOINT 2)")
                    self._closing_tickets.discard(ticket)  # Remove from closing set
                    return False  # Complete abort - do not use break
                
                if latest_profit < self.min_profit_buffer:
                    logger.warning(f"Micro-HFT: Ticket {ticket} has profit ${latest_profit:.2f} < ${self.min_profit_buffer:.2f} buffer - ABORTING close (CHECKPOINT 2)")
                    self._closing_tickets.discard(ticket)  # Remove from closing set
                    return False  # Complete abort - do not use break
                
                in_sweet_spot_retry = (self.min_profit_threshold_usd <= latest_profit <= self.max_profit_threshold_usd)
                is_multiple_retry = (latest_profit > self.max_profit_threshold_usd and 
                                     abs(latest_profit % 0.10) < 0.005)
                
                if not (in_sweet_spot_retry or is_multiple_retry):
                    # Profit moved outside acceptable range
                    logger.debug(f"Micro-HFT: Ticket {ticket} profit moved to ${latest_profit:.2f} (outside acceptable range), skipping close")
                    break
                
                # CRITICAL FIX 1.2: Final profit check immediately before executing close
                # Get the ABSOLUTE FRESHEST profit value right before close execution
                # This prevents race conditions where profit changes between decision and execution
                final_pre_close_position = self.order_manager.get_position_by_ticket(ticket)
                if not final_pre_close_position:
                    # Position already closed
                    close_success = True
                    break
                
                final_pre_close_profit = final_pre_close_position.get('profit', 0.0)
                
                # Abort close if profit is below buffer - use return False to completely cancel closure attempt
                if final_pre_close_profit < self.min_profit_buffer:
                    logger.warning(f"Micro-HFT: Ticket {ticket} ABORTING CLOSE - Final pre-close check shows profit ${final_pre_close_profit:.2f} < ${self.min_profit_buffer:.2f} buffer")
                    self._closing_tickets.discard(ticket)  # Remove from closing set since we're aborting
                    return False  # Complete abort - do not use break
                
                # Double-verify profit is still in acceptable range
                final_in_sweet_spot = (self.min_profit_threshold_usd <= final_pre_close_profit <= self.max_profit_threshold_usd)
                final_is_multiple = (final_pre_close_profit > self.max_profit_threshold_usd and 
                                    abs(final_pre_close_profit % 0.10) < 0.005)
                
                if not (final_in_sweet_spot or final_is_multiple):
                    logger.warning(f"Micro-HFT: Ticket {ticket} ABORTING CLOSE - Final pre-close profit ${final_pre_close_profit:.2f} outside acceptable range")
                    self._closing_tickets.discard(ticket)  # Remove from closing set since we're aborting
                    return False  # Complete abort
                
                # Log decision profit and final pre-close profit for verification
                logger.info(f"Micro-HFT: Ticket {ticket} | Decision profit: ${latest_profit:.2f} | Final pre-close profit: ${final_pre_close_profit:.2f} | Proceeding with close")
                
                # Close position using existing order_manager
                logger.info(f"🔥 MICRO PROFIT CLOSURE ATTEMPT: Ticket={ticket} Symbol={symbol} Profit=${final_pre_close_profit:.2f} SweetSpot={self.min_profit_threshold_usd}-${self.max_profit_threshold_usd}")
                close_success = self.order_manager.close_position(
                    ticket=ticket,
                    comment=f"Micro-HFT sweet spot profit (${final_pre_close_profit:.2f})"
                )
                
                if close_success:
                    logger.info(f"✅ MICRO PROFIT CLOSURE SUCCESS: Ticket={ticket} Symbol={symbol} Profit=${final_pre_close_profit:.2f}")
                    # Verify closure by checking position again
                    time.sleep(0.01)  # Small delay to allow MT5 to process
                    verify_position = self.order_manager.get_position_by_ticket(ticket)
                    if verify_position is None:
                        # Successfully closed - use final pre-close profit for now
                        # Will be updated with actual profit from deal history below
                        target_profit = final_pre_close_profit  # Use the final pre-close profit
                        break
                    else:
                        # Closure reported success but position still exists
                        logger.warning(f"⚠️ Micro-HFT: Close reported success but position {ticket} still exists, retrying...")
                        close_success = False
                else:
                    logger.warning(f"❌ MICRO PROFIT CLOSURE FAILED: Ticket={ticket} Symbol={symbol} Attempt={attempt+1}/{self.max_retries}")
                
                # If failed, check if it's a retryable error
                if attempt < self.max_retries - 1:
                    # Smaller delay before retry (5ms instead of 10ms for faster execution)
                    time.sleep(0.005)  # 5ms
            
            execution_time_ms = (time.time() - execution_start) * 1000
            
            if close_success:
                # CRITICAL FIX 1.3: Get ACTUAL closing profit from MT5 deal history
                # This is the authoritative source - not the pre-close estimate
                actual_close_price = close_price_expected  # Default
                # CRITICAL FIX: Initialize to a very negative value to ensure we detect if deal history fails
                actual_closing_profit = -999.0  # Sentinel value - will be replaced by deal history or target_profit
                
                if mt5_connector and mt5_connector.ensure_connected():
                    # Wait a moment for MT5 to process the close
                    time.sleep(0.05)  # 50ms delay to ensure deal is recorded
                    
                    # Get deal history for this position
                    deals = mt5.history_deals_get(position=ticket)
                    if deals and len(deals) > 0:
                        # Sort deals by time to get the most recent
                        deals_sorted = sorted(deals, key=lambda d: d.time, reverse=True)
                        
                        # Calculate total profit from all deals for this position
                        total_profit_from_deals = 0.0
                        for deal in deals_sorted:
                            total_profit_from_deals += deal.profit
                            # Find the close deal (DEAL_ENTRY_OUT) - most recent one
                            if deal.entry == mt5.DEAL_ENTRY_OUT:
                                actual_close_price = deal.price
                                break
                        
                        # Use actual profit from deal history if available
                        if total_profit_from_deals != 0.0:
                            actual_closing_profit = total_profit_from_deals
                
                # CRITICAL FIX: If deal history didn't provide profit, use target_profit as fallback
                # But log a warning since this is less reliable
                if actual_closing_profit == -999.0:
                    actual_closing_profit = target_profit
                    logger.warning(f"⚠️ Micro-HFT: Ticket {ticket} | Deal history unavailable, using pre-close estimate: ${actual_closing_profit:.2f}")
                
                # CRITICAL FIX 1.3: Determine close_reason based on ACTUAL closing profit
                # If actual profit is negative, this is an error condition
                # CRITICAL: Log actual profit for debugging before classification
                logger.info(f"🔍 Micro-HFT: Ticket {ticket} | Actual closing profit from deals: ${actual_closing_profit:.2f} | Pre-close estimate: ${target_profit:.2f}")
                if actual_closing_profit <= 0:
                    close_reason = f"Micro-HFT error prevented: negative profit attempted (actual: ${actual_closing_profit:.2f})"
                    logger.error(f"❌ Micro-HFT ERROR: Ticket {ticket} closed with negative profit ${actual_closing_profit:.2f} | "
                               f"Decision profit was ${target_profit:.2f} | "
                               f"This should never happen - closing profit validation failed")
                elif 0.03 <= actual_closing_profit <= 0.10:
                    close_reason = "Micro-HFT sweet spot profit ($0.03–$0.10)"
                else:
                    close_reason = f"Micro-HFT multiple of $0.10 (${actual_closing_profit:.2f})"
                
                # Log micro profit close using TradeLogger with ACTUAL profit from deal history
                self.trade_logger.log_micro_profit_close(
                    ticket=ticket,
                    symbol=symbol,
                    profit=actual_closing_profit,  # Use ACTUAL closing profit from deal history
                    entry_price_actual=entry_price,
                    close_price=actual_close_price,
                    spread_points=spread_points,
                    execution_time_ms=execution_time_ms
                )
                
                logger.info(f"✅ Micro-HFT: Closed {symbol} Ticket {ticket} | "
                          f"Decision profit: ${target_profit:.2f} | "
                          f"Actual closing profit: ${actual_closing_profit:.2f} | "
                          f"Reason: {close_reason} | "
                          f"Time: {execution_time_ms:.1f}ms")
                return True
            else:
                logger.warning(f"⚠️ Micro-HFT: Failed to close {symbol} Ticket {ticket} after {self.max_retries} attempts")
                return False
        
        except Exception as e:
            logger.error(f"❌ Micro-HFT: Error closing position {ticket}: {e}", exc_info=True)
            return False
        
        finally:
            # Remove from closing set after a delay (in case position still exists)
            # This allows retry if needed
            if ticket in self._closing_tickets:
                # Remove after 1 second (position should be closed by then)
                import threading
                def remove_after_delay():
                    time.sleep(1.0)
                    self._closing_tickets.discard(ticket)
                threading.Thread(target=remove_after_delay, daemon=True).start()
    
    def cleanup_closed_position(self, ticket: int):
        """
        Clean up tracking data for a closed position.
        
        Args:
            ticket: Position ticket number
        """
        self._closing_tickets.discard(ticket)
        self._last_check_time.pop(ticket, None)

