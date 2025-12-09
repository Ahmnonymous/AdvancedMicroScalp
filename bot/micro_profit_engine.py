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
    
    def __init__(self, config: Dict[str, Any], order_manager, trade_logger):
        """
        Initialize Micro-HFT Profit Engine.
        
        Args:
            config: Configuration dictionary
            order_manager: OrderManager instance for closing positions
            trade_logger: TradeLogger instance for logging
        """
        self.config = config
        self.order_manager = order_manager
        self.trade_logger = trade_logger
        
        # Configuration
        micro_config = config.get('micro_profit_engine', {})
        self.enabled = micro_config.get('enabled', True)
        self.min_profit_threshold_usd = micro_config.get('min_profit_threshold_usd', 0.03)
        self.max_profit_threshold_usd = micro_config.get('max_profit_threshold_usd', 0.10)
        self.max_retries = micro_config.get('max_retries', 3)
        self.retry_delay_ms = micro_config.get('retry_delay_ms', 10)  # 10ms between retries
        
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
        
        Args:
            position: Position dictionary from order_manager
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
        
        # Get actual profit from MT5 (not estimated)
        current_profit = position.get('profit', 0.0)
        
        # ENHANCED: Smart profit locking strategy
        # Rule 1: If profit >= $0.10, let trailing stop lock it at $0.10 first (don't close yet)
        # Rule 2: Only close if profit is exactly in sweet spot ($0.03–$0.10) AND declining
        # Rule 3: Close if profit is a multiple of $0.10 AND trailing stop has locked it (SL profit >= $0.10)
        # Rule 4: Never close below $0.03 (let stop-loss handle it)
        
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
        
        # CRITICAL: If profit >= $0.10 but trailing stop hasn't locked it yet, wait
        # Let trailing stop lock profit at $0.10 first before closing
        if current_profit >= 0.10:
            # Check if trailing stop has locked at least $0.09 (accounting for rounding)
            if sl_profit_locked < 0.09:
                # Trailing stop hasn't locked profit yet - don't close, let it lock first
                logger.debug(f"Micro-HFT: Ticket {ticket} profit ${current_profit:.2f} but SL only locked ${sl_profit_locked:.2f} - waiting for trailing stop")
                return False
        
        # If profit is in sweet spot ($0.03–$0.10), can close immediately
        in_sweet_spot = (self.min_profit_threshold_usd <= current_profit <= self.max_profit_threshold_usd)
        
        # If profit >= $0.10, only close if trailing stop has locked it at $0.10+
        # This ensures we capture $0.10 instead of closing at $0.03 when profit was $0.19
        is_multiple_and_locked = False
        if current_profit >= 0.10:
            # Check if it's at a $0.10 multiple AND trailing stop has locked it
            is_multiple = abs(current_profit % 0.10) < 0.005
            is_locked = sl_profit_locked >= 0.09  # Trailing stop locked at $0.10+
            is_multiple_and_locked = is_multiple and is_locked
        
        should_close = in_sweet_spot or is_multiple_and_locked
        
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
        
        # Safety: Don't close if at stop-loss
        if fresh_profit <= -2.0:
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
            
            # ENHANCEMENT: More aggressive retry logic for Micro-HFT
            # Attempt to close position with fast retry logic
            execution_start = time.time()
            close_success = False
            
            # Use fresh profit for closing (most up-to-date)
            target_profit = fresh_profit
            
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
                
                if latest_profit <= -2.0:
                    # At stop-loss, don't close (stop-loss will handle)
                    break
                
                in_sweet_spot_retry = (self.min_profit_threshold_usd <= latest_profit <= self.max_profit_threshold_usd)
                is_multiple_retry = (latest_profit > self.max_profit_threshold_usd and 
                                     abs(latest_profit % 0.10) < 0.005)
                
                if not (in_sweet_spot_retry or is_multiple_retry):
                    # Profit moved outside acceptable range
                    logger.debug(f"Micro-HFT: Ticket {ticket} profit moved to ${latest_profit:.2f} (outside acceptable range), skipping close")
                    break
                
                # Close position using existing order_manager
                close_success = self.order_manager.close_position(
                    ticket=ticket,
                    comment=f"Micro-HFT sweet spot profit (${latest_profit:.2f})"
                )
                
                if close_success:
                    # Verify closure by checking position again
                    time.sleep(0.01)  # Small delay to allow MT5 to process
                    verify_position = self.order_manager.get_position_by_ticket(ticket)
                    if verify_position is None:
                        # Successfully closed
                        target_profit = latest_profit  # Use the profit at closure time
                        break
                    else:
                        # Closure reported success but position still exists
                        logger.warning(f"Micro-HFT: Close reported success but position {ticket} still exists, retrying...")
                        close_success = False
                
                # If failed, check if it's a retryable error
                if attempt < self.max_retries - 1:
                    # Smaller delay before retry (5ms instead of 10ms for faster execution)
                    time.sleep(0.005)  # 5ms
            
            execution_time_ms = (time.time() - execution_start) * 1000
            
            if close_success:
                # Get actual close price from deal history
                actual_close_price = close_price_expected  # Default
                if mt5_connector and mt5_connector.ensure_connected():
                    # Get deal history for this position
                    deals = mt5.history_deals_get(position=ticket)
                    if deals and len(deals) > 0:
                        # Find the close deal (DEAL_ENTRY_OUT)
                        for deal in deals:
                            if deal.entry == mt5.DEAL_ENTRY_OUT:
                                actual_close_price = deal.price
                                break
                
                # Determine closure reason for logging
                if 0.03 <= target_profit <= 0.10:
                    close_reason = "Micro-HFT sweet spot profit ($0.03–$0.10)"
                else:
                    close_reason = f"Micro-HFT multiple of $0.10 (${target_profit:.2f})"
                
                # Log micro profit close using TradeLogger with actual profit captured
                self.trade_logger.log_micro_profit_close(
                    ticket=ticket,
                    symbol=symbol,
                    profit=target_profit,  # Use profit at closure time
                    entry_price_actual=entry_price,
                    close_price=actual_close_price,
                    spread_points=spread_points,
                    execution_time_ms=execution_time_ms
                )
                
                logger.info(f"✅ Micro-HFT: Closed {symbol} Ticket {ticket} | Profit: ${target_profit:.2f} | Reason: {close_reason} | Time: {execution_time_ms:.1f}ms")
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

