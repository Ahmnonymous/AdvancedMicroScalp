"""
Micro-HFT Profit Engine
Closes positions immediately when profit is within the $0.03–$0.10 sweet spot range.
This is an ADD-ON module that does not interfere with existing risk management.
"""

import time
from typing import Dict, Any, Optional
from datetime import datetime
import MetaTrader5 as mt5

# Import standard logging module (avoid conflict with logging/ folder)
# Use absolute import to ensure we get Python's logging, not the local logging/ package
import logging

logger = logging.getLogger(__name__)


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
        
        # CRITICAL: Only close if profit is within sweet spot range ($0.03–$0.10)
        if current_profit < self.min_profit_threshold_usd or current_profit > self.max_profit_threshold_usd:
            return False
        
        # Rate limiting: Don't check same position too frequently
        now = time.time()
        last_check = self._last_check_time.get(ticket, 0)
        if now - last_check < 0.1:  # 100ms minimum between checks
            return False
        
        self._last_check_time[ticket] = now
        
        # Get fresh position data to ensure profit is still >= threshold
        fresh_position = self.order_manager.get_position_by_ticket(ticket)
        if not fresh_position:
            # Position already closed
            return False
        
        fresh_profit = fresh_position.get('profit', 0.0)
        
        # Double-check profit is within sweet spot range (may have changed)
        if fresh_profit < self.min_profit_threshold_usd or fresh_profit > self.max_profit_threshold_usd:
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
            
            # Attempt to close position with retry logic
            execution_start = time.time()
            close_success = False
            
            for attempt in range(self.max_retries):
                # Close position using existing order_manager
                close_success = self.order_manager.close_position(
                    ticket=ticket,
                    comment=f"Micro-HFT sweet spot profit (${fresh_profit:.2f})"
                )
                
                if close_success:
                    break
                
                # If failed, check if it's a retryable error
                if attempt < self.max_retries - 1:
                    # Small delay before retry (10ms)
                    time.sleep(self.retry_delay_ms / 1000.0)
                    
                    # Re-check profit before retry (may have dropped)
                    retry_position = self.order_manager.get_position_by_ticket(ticket)
                    if not retry_position:
                        # Position closed by someone else
                        close_success = True
                        break
                    
                    retry_profit = retry_position.get('profit', 0.0)
                    if retry_profit < self.min_profit_threshold_usd or retry_profit > self.max_profit_threshold_usd:
                        # Profit moved outside sweet spot range, don't retry
                        logger.debug(f"Micro-HFT: Ticket {ticket} profit moved to ${retry_profit:.2f} (outside $0.03–$0.10 range), skipping close")
                        break
            
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
                
                # Log micro profit close using TradeLogger
                self.trade_logger.log_micro_profit_close(
                    ticket=ticket,
                    symbol=symbol,
                    profit=fresh_profit,
                    entry_price_actual=entry_price,
                    close_price=actual_close_price,
                    spread_points=spread_points,
                    execution_time_ms=execution_time_ms
                )
                
                logger.info(f"✅ Micro-HFT: Closed {symbol} Ticket {ticket} | Profit: ${fresh_profit:.2f} (sweet spot $0.03–$0.10) | Time: {execution_time_ms:.1f}ms")
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

