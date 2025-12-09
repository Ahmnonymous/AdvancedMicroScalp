"""
Order Management System
Handles order placement, modification, and tracking.
"""

import MetaTrader5 as mt5
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class OrderType(Enum):
    BUY = mt5.ORDER_TYPE_BUY
    SELL = mt5.ORDER_TYPE_SELL


class OrderManager:
    """Manages trading orders through MT5."""
    
    def __init__(self, mt5_connector):
        self.mt5_connector = mt5_connector
    
    def place_order(
        self,
        symbol: str,
        order_type: OrderType,
        lot_size: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        comment: str = "Trading Bot"
    ) -> Optional[Dict[str, Any]]:
        """
        Place a market order.
        
        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            order_type: OrderType.BUY or OrderType.SELL
            lot_size: Lot size (e.g., 0.01)
            stop_loss: Stop loss in pips (e.g., 20 for 20 pips)
            take_profit: Take profit in pips (optional)
            comment: Order comment
        
        Returns:
            Dictionary with 'ticket' (int), 'entry_price_actual' (float), 'slippage' (float) if successful.
            Returns error code dict with 'error' key for failures.
            Returns None for connection errors.
        """
        if not self.mt5_connector.ensure_connected():
            logger.error("Cannot place order: MT5 not connected")
            return None  # Connection error
        
        # Get fresh symbol info right before order placement (critical for accurate prices)
        symbol_info = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=True)
        if symbol_info is None:
            logger.error(f"Cannot place order: Symbol {symbol} not found or price is stale")
            return None
        
        # Normalize symbol
        symbol = symbol_info['name']
        
        # Get fresh tick data for most current prices (MT5 requirement for accurate order placement)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Cannot get tick data for {symbol}")
            return None
        
        # Check if market is open (bid and ask must be valid)
        if tick.bid <= 0 or tick.ask <= 0 or tick.bid >= tick.ask:
            logger.warning(f"⏰ {symbol}: Market appears closed (bid: {tick.bid}, ask: {tick.ask}) - skipping order placement")
            return None
        
        # Use fresh tick prices (more accurate than cached symbol_info)
        if order_type == OrderType.BUY:
            price = tick.ask
            bid_price = tick.bid
            ask_price = tick.ask
        else:  # SELL
            price = tick.bid
            bid_price = tick.bid
            ask_price = tick.ask
        
        if price <= 0:
            logger.error(f"Invalid price {price} for {symbol} (order_type: {order_type.name})")
            return None
        
        # Convert stop_loss from pips to price
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        stop_loss_price = stop_loss * pip_value
        
        # Check price staleness (if timestamp available)
        fetched_time = symbol_info.get('_fetched_time', 0)
        tick_time = symbol_info.get('_tick_time', 0)
        if fetched_time > 0 or tick_time > 0:
            import time
            now = time.time()
            if tick_time > 0:
                price_age = now - tick_time
                if price_age > 5.0:  # 5 seconds max age
                    logger.warning(f"{symbol}: Price is stale ({price_age:.2f}s old), rejecting order")
                    return None
            elif fetched_time > 0:
                price_age = now - fetched_time
                if price_age > 5.0:
                    logger.warning(f"{symbol}: Symbol info is stale ({price_age:.2f}s old), rejecting order")
                    return None
        
        # Calculate stop loss price
        # CRITICAL: For SELL orders, MT5 validates stops against ASK price (broker requirement)
        # For BUY orders, validate against ASK (entry price)
        
        if order_type == OrderType.BUY:
            sl_price = price - stop_loss_price
            # Ensure SL is valid (not negative or zero)
            if sl_price <= 0:
                logger.error(f"Invalid stop loss price {sl_price} for {symbol}")
                return None
            # Use entry price (ASK) for validation
            validation_price = price  # ASK for BUY
            if take_profit and take_profit > 0:
                tp_price = price + (take_profit * pip_value)
            else:
                tp_price = 0
        else:  # SELL
            # CRITICAL: For SELL orders, MT5 validates stop loss against ASK price
            # Therefore, calculate SL relative to ASK, not BID (entry price)
            validation_price = ask_price if ask_price > 0 else price
            sl_price = validation_price + stop_loss_price  # SL above ASK for SELL orders
            # Ensure SL is valid (not negative or zero)
            if sl_price <= 0:
                logger.error(f"Invalid stop loss price {sl_price} for {symbol}")
                return None
            if take_profit and take_profit > 0:
                tp_price = price - (take_profit * pip_value)
            else:
                tp_price = 0
        
        # Normalize prices to symbol's tick size (MT5 requirement)
        # Normalize SL and TP to point precision
        digits = symbol_info.get('digits', 5)
        if sl_price > 0:
            sl_price = round(sl_price / point) * point
            # Round to correct decimal places
            sl_price = round(sl_price, digits)
        
        if tp_price > 0:
            tp_price = round(tp_price / point) * point
            tp_price = round(tp_price, digits)
        
        # Validate stop loss distance against broker's minimum stops level
        stops_level = symbol_info.get('trade_stops_level', 0)
        min_distance = 0.0
        actual_distance = abs(validation_price - sl_price)
        
        if stops_level > 0:
            min_distance = stops_level * point
            # Use validation_price (ASK for SELL, ASK for BUY) to check distance
            
            if actual_distance < min_distance:
                logger.error(f"Stop loss distance {actual_distance:.5f} is less than broker minimum {min_distance:.5f} "
                           f"(stops_level: {stops_level}, validation_price: {validation_price:.5f}, sl_price: {sl_price:.5f}) for {symbol}")
                return None
            
            logger.debug(f"{symbol}: Stop loss validated - distance {actual_distance:.5f} >= minimum {min_distance:.5f} "
                        f"(validation_price: {validation_price:.5f}, sl_price: {sl_price:.5f})")
        else:
            logger.debug(f"{symbol}: Stop loss distance: {actual_distance:.5f} (no stops_level requirement)")
        
        # Additional validation: Ensure SL is on the correct side for order type
        if order_type == OrderType.BUY and sl_price >= validation_price:
            logger.error(f"Invalid stop loss for BUY order: SL {sl_price:.5f} >= entry price {validation_price:.5f} for {symbol}")
            return None
        
        if order_type == OrderType.SELL and sl_price <= validation_price:
            logger.error(f"Invalid stop loss for SELL order: SL {sl_price:.5f} <= validation price {validation_price:.5f} (ASK) for {symbol}")
            return None
        
        # Determine filling type based on symbol
        # CRITICAL: Must use the EXACT filling mode the symbol supports
        filling_type = None
        symbol_info_obj = mt5.symbol_info(symbol)
        if symbol_info_obj is not None:
            # Check symbol's filling modes (bitmask: 1=FOK, 2=IOC, 4=RETURN)
            filling_modes = symbol_info_obj.filling_mode
            
            # Priority order: IOC (works for crypto 24/7), then RETURN (forex), then FOK
            # IOC (bit 1 = 2) - works for crypto and many symbols, try first
            if filling_modes & 2:
                filling_type = mt5.ORDER_FILLING_IOC
            # RETURN (bit 2 = 4) - most common for forex and indices
            elif filling_modes & 4:
                filling_type = mt5.ORDER_FILLING_RETURN
            # FOK (bit 0 = 1) - often required for crypto
            elif filling_modes & 1:
                filling_type = mt5.ORDER_FILLING_FOK
            
            logger.debug(f"{symbol}: Filling modes available: {filling_modes} (bitmask), using: {filling_type}")
        
        # If still None, this is a problem - symbol doesn't support any filling mode
        if filling_type is None:
            logger.error(f"{symbol}: No supported filling mode found! Symbol filling_mode: {symbol_info_obj.filling_mode if symbol_info_obj else 'N/A'}")
            return None
        
        # Prepare order request
        # For MT5, SL and TP must be 0 if not set, or valid prices
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type.value,
            "price": price,
            "sl": sl_price,
            "tp": tp_price if tp_price > 0 else 0,
            "deviation": 20,
            "magic": 234000,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        # Remove TP from request if it's 0 (some brokers don't accept TP=0)
        if tp_price <= 0:
            request.pop('tp', None)
        
        # Send order
        result = mt5.order_send(request)
        
        if result is None:
            error = mt5.last_error()
            logger.error(f"Order send returned None. MT5 error: {error}")
            # Return error dict to indicate connection/transient error (retryable)
            return {'error': -2}
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            error_msg = f"Order failed: {result.retcode} - {result.comment}"
            
            # Detailed logging for stop loss errors (10016)
            if result.retcode == 10016:  # Invalid stops
                logger.error(f"❌ {error_msg}")
                logger.error(f"   Symbol: {symbol}, Order Type: {order_type.name}")
                logger.error(f"   Entry Price: {price:.5f} ({'ASK' if order_type == OrderType.BUY else 'BID'})")
                logger.error(f"   Stop Loss Price: {sl_price:.5f} ({'below entry' if order_type == OrderType.BUY else 'above entry'})")
                logger.error(f"   Stop Loss Pips: {stop_loss}")
                logger.error(f"   Validation Price: {validation_price:.5f} ({'ASK' if order_type == OrderType.SELL else 'ASK'})")
                logger.error(f"   Stop Distance: {abs(validation_price - sl_price):.5f}")
                logger.error(f"   Stops Level: {stops_level}, Min Distance: {min_distance:.5f}" if stops_level > 0 else f"   Stops Level: {stops_level} (no minimum)")
                logger.error(f"   Point: {point}, Digits: {digits}, Pip Value: {pip_value}")
                logger.error(f"   Ask: {ask_price:.5f}, Bid: {bid_price:.5f}, Spread: {abs(ask_price - bid_price):.5f}")
                # Return error dict for invalid stops (non-retryable)
                return {'error': -3}
            elif result.retcode == 10018:  # Market closed
                logger.warning(f"⏰ {error_msg} - Market is closed for {symbol}, skipping order placement")
                # Don't retry when market is closed - it won't help
                # Return error dict for market closed (non-retryable)
                return {'error': -4}
            elif result.retcode == 10014:  # Invalid volume
                logger.error(f"❌ {error_msg} - Volume {lot_size} is invalid for {symbol}")
                # Return error dict to indicate volume issue
                return {'error': -1}  # Special return value to indicate volume error (retryable once)
            elif result.retcode == 10027:  # Trade disabled
                logger.error(f"❌ {error_msg} - Trading is disabled for {symbol}")
                logger.error(f"   This symbol cannot be traded (account or symbol restriction)")
                # Return error dict for trade disabled (non-retryable)
                return {'error': -5}
            elif result.retcode == 10044:  # Trading restriction (broker-specific, often means trading not allowed)
                logger.error(f"❌ {error_msg} - Trading restriction for {symbol} {order_type.name}")
                logger.error(f"   Symbol: {symbol}, Order Type: {order_type.name}, Price: {price:.5f}, SL: {sl_price:.5f}, Lot: {lot_size}")
                logger.error(f"   This symbol/order type may not be tradeable due to account or broker restrictions")
                # Return error dict for trading restrictions (non-retryable)
                return {'error': -5}
            elif result.retcode == 10029:  # Too many requests
                logger.error(f"❌ {error_msg} - Too many requests for {symbol}")
                logger.error(f"   Rate limit exceeded - wait before retrying")
                # Return error dict for rate limiting (retryable with backoff)
                return {'error': -2}
            else:
                logger.error(error_msg)
                # Log order details for other errors
                logger.error(f"   Symbol: {symbol}, Order Type: {order_type.name}, Price: {price:.5f}, SL: {sl_price:.5f}, Lot: {lot_size}")
                logger.error(f"   Error code: {result.retcode} | Comment: {result.comment}")
                # Check if error is likely a restriction (10000-10099 range, some are restrictions)
                if 10027 <= result.retcode <= 10099:
                    # Likely a trading restriction, don't retry
                    logger.error(f"   This appears to be a trading restriction - not retrying")
                    return {'error': -5}
                # Return error dict for other transient errors (retryable with backoff)
                return {'error': -2}
        
        # CRITICAL FIX: Get actual fill price from deal history (not order request)
        # This accounts for slippage
        actual_entry_price = price  # Default to requested price
        slippage = 0.0
        
        if result.order and result.order > 0:
            # Get deal for this order to get actual fill price
            deals = mt5.history_deals_get(ticket=result.order)
            if deals and len(deals) > 0:
                # Get the entry deal (DEAL_ENTRY_IN)
                for deal in deals:
                    if deal.entry == mt5.DEAL_ENTRY_IN:
                        actual_entry_price = deal.price
                        slippage = abs(actual_entry_price - price)
                        if slippage > 0.00001:  # Significant slippage
                            logger.warning(f"⚠️ {symbol}: Entry slippage detected - Requested: {price:.5f}, Filled: {actual_entry_price:.5f}, Slippage: {slippage:.5f}")
                        break
        
        logger.info(f"Order placed successfully: Ticket {result.order}, Symbol {symbol}, "
                   f"Type {order_type.name}, Volume {lot_size}, SL {sl_price:.5f}, "
                   f"Entry Price: {actual_entry_price:.5f} (requested: {price:.5f})")
        
        # Return comprehensive result with actual fill price
        return {
            'ticket': result.order,
            'entry_price_actual': actual_entry_price,
            'entry_price_requested': price,
            'slippage': slippage
        }
    
    def modify_order(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None
    ) -> bool:
        """
        Modify an existing order's SL/TP.
        
        Args:
            ticket: Position ticket number
            stop_loss: Stop loss in pips (relative to current price, if stop_loss_price not provided)
            take_profit: Take profit in pips (relative to current price, if take_profit_price not provided)
            stop_loss_price: Absolute stop loss price (takes precedence over stop_loss)
            take_profit_price: Absolute take profit price (takes precedence over take_profit)
        """
        if not self.mt5_connector.ensure_connected():
            return False
        
        # Get position info
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            logger.error(f"Position {ticket} not found")
            return False
        
        position = position[0]
        symbol = position.symbol
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Use absolute prices if provided, otherwise calculate from pips
        if stop_loss_price is not None:
            new_sl = stop_loss_price
        elif stop_loss is not None:
            # Convert pips to price relative to current price
            point = symbol_info['point']
            pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
            
            if position.type == mt5.ORDER_TYPE_BUY:
                current_price = symbol_info['ask']
                new_sl = current_price - (stop_loss * pip_value)
            else:  # SELL
                current_price = symbol_info['bid']
                new_sl = current_price + (stop_loss * pip_value)
        else:
            new_sl = position.sl
        
        # CRITICAL FIX: Check if new SL is effectively the same as current SL before modifying
        # This prevents error 10025 (No changes)
        current_sl = position.sl
        if current_sl > 0:
            point = symbol_info.get('point', 0.00001)
            # Use point size as tolerance (if prices differ by less than 1 point, consider them equal)
            sl_difference = abs(new_sl - current_sl)
            if sl_difference < point:
                logger.debug(f"Skip SL modification for ticket {ticket}: new SL {new_sl:.5f} equals current SL {current_sl:.5f} (diff: {sl_difference:.8f} < point: {point:.8f})")
                return True  # Return True since SL is already at desired level
        
        if take_profit_price is not None:
            new_tp = take_profit_price
        elif take_profit is not None:
            # Convert pips to price relative to current price
            point = symbol_info['point']
            pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
            
            if position.type == mt5.ORDER_TYPE_BUY:
                current_price = symbol_info['ask']
                new_tp = current_price + (take_profit * pip_value)
            else:  # SELL
                current_price = symbol_info['bid']
                new_tp = current_price - (take_profit * pip_value)
        else:
            new_tp = position.tp
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
        }
        
        # CRITICAL FIX: Retry logic for SL modification (up to 3 attempts)
        max_retries = 3
        for attempt in range(max_retries):
            result = mt5.order_send(request)
            
            if result is None:
                error = mt5.last_error()
                if attempt < max_retries - 1:
                    logger.warning(f"Modify order send returned None for ticket {ticket} (attempt {attempt + 1}/{max_retries}). MT5 error: {error}. Retrying...")
                    import time
                    time.sleep(0.1 * (attempt + 1))  # Increasing backoff
                    continue
                else:
                    logger.error(f"Modify order send returned None for ticket {ticket} after {max_retries} attempts. MT5 error: {error}")
                    return False
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Order {ticket} modified: SL {new_sl:.5f}, TP {new_tp:.5f}")
                return True
            
            # Handle specific error codes
            if result.retcode == 10025:  # No changes (already at desired level)
                logger.debug(f"Order {ticket} SL modification skipped: No changes (SL already at {new_sl:.5f})")
                return True  # Consider this success - SL is already at desired level
            
            if result.retcode == 10027:  # Trade disabled
                logger.error(f"Modify order failed for ticket {ticket}: Trading disabled (error {result.retcode})")
                return False
            
            if attempt < max_retries - 1:
                logger.warning(f"Modify order failed for ticket {ticket} (attempt {attempt + 1}/{max_retries}): {result.retcode} - {result.comment}. Retrying...")
                import time
                time.sleep(0.1 * (attempt + 1))  # Increasing backoff
            else:
                logger.error(f"Modify order failed for ticket {ticket} after {max_retries} attempts: {result.retcode} - {result.comment}")
                return False
        
        return False
    
    def close_position(self, ticket: int, comment: str = "Close by bot") -> bool:
        """Close an open position."""
        if not self.mt5_connector.ensure_connected():
            return False
        
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            logger.error(f"Position {ticket} not found")
            return False
        
        position = position[0]
        symbol = position.symbol
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Determine close price and type
        if position.type == mt5.ORDER_TYPE_BUY:
            price = symbol_info['bid']
            order_type = mt5.ORDER_TYPE_SELL
        else:
            price = symbol_info['ask']
            order_type = mt5.ORDER_TYPE_BUY
        
        # Determine filling type
        filling_type = mt5.ORDER_FILLING_RETURN
        symbol_info_obj = mt5.symbol_info(symbol)
        if symbol_info_obj is not None:
            # Check symbol's filling modes (bitmask: 1=FOK, 2=IOC, 4=RETURN)
            filling_modes = symbol_info_obj.filling_mode
            if filling_modes & 1:
                filling_type = mt5.ORDER_FILLING_FOK
            elif filling_modes & 2:
                filling_type = mt5.ORDER_FILLING_IOC
            elif filling_modes & 4:
                filling_type = mt5.ORDER_FILLING_RETURN
            else:
                filling_type = mt5.ORDER_FILLING_RETURN
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": position.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        result = mt5.order_send(request)
        
        if result is None:
            error = mt5.last_error()
            logger.error(f"Close position send returned None. MT5 error: {error}")
            return False
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Close position failed: {result.retcode} - {result.comment}")
            return False
        
        logger.info(f"Position {ticket} closed successfully")
        return True
    
    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions."""
        if not self.mt5_connector.ensure_connected():
            return []
        
        positions = mt5.positions_get()
        if positions is None:
            return []
        
        result = []
        for pos in positions:
            result.append({
                'ticket': pos.ticket,
                'symbol': pos.symbol,
                'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                'volume': pos.volume,
                'price_open': pos.price_open,
                'price_current': pos.price_current,
                'sl': pos.sl,
                'tp': pos.tp,
                'profit': pos.profit,
                'swap': pos.swap,
                'time_open': datetime.fromtimestamp(pos.time),
                'comment': pos.comment
            })
        
        return result
    
    def get_position_count(self) -> int:
        """Get count of open positions."""
        positions = self.get_open_positions()
        return len(positions)
    
    def get_position_by_ticket(self, ticket: int) -> Optional[Dict[str, Any]]:
        """Get position by ticket number."""
        positions = self.get_open_positions()
        for pos in positions:
            if pos['ticket'] == ticket:
                return pos
        return None
    
    def get_deal_history(self, ticket: int) -> Optional[Dict[str, Any]]:
        """
        Get deal history for a position ticket.
        
        Returns dict with entry and exit deal information.
        """
        if not self.mt5_connector.ensure_connected():
            return None
        
        try:
            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                return None
            
            entry_deal = None
            exit_deal = None
            total_profit = 0.0
            commission = 0.0
            swap = 0.0
            
            for deal in deals:
                if deal.entry == mt5.DEAL_ENTRY_IN:
                    entry_deal = {
                        'ticket': deal.ticket,
                        'time': datetime.fromtimestamp(deal.time),
                        'price': deal.price,
                        'volume': deal.volume,
                        'symbol': deal.symbol,
                        'type': deal.type,
                        'profit': deal.profit,
                        'commission': deal.commission,
                        'swap': deal.swap
                    }
                elif deal.entry == mt5.DEAL_ENTRY_OUT:
                    exit_deal = {
                        'ticket': deal.ticket,
                        'time': datetime.fromtimestamp(deal.time),
                        'price': deal.price,
                        'volume': deal.volume,
                        'symbol': deal.symbol,
                        'type': deal.type,
                        'profit': deal.profit,
                        'commission': deal.commission,
                        'swap': deal.swap
                    }
                
                total_profit += deal.profit
                commission += deal.commission
                swap += deal.swap
            
            return {
                'entry_deal': entry_deal,
                'exit_deal': exit_deal,
                'total_profit': total_profit,
                'commission': commission,
                'swap': swap,
                'status': 'CLOSED' if exit_deal else 'OPEN'
            }
        
        except Exception as e:
            logger.error(f"Error getting deal history for position {ticket}: {e}", exc_info=True)
            return None
    
    def get_close_reason_from_deals(self, ticket: int) -> str:
        """
        Determine close reason from deal history.
        
        Returns descriptive string like "Stop Loss", "Take Profit", "Manual Close", etc.
        """
        deal_info = self.get_deal_history(ticket)
        if not deal_info or not deal_info.get('exit_deal'):
            return "Unknown"
        
        exit_deal = deal_info['exit_deal']
        total_profit = deal_info['total_profit']
        entry_deal = deal_info.get('entry_deal')
        
        # Check duration for micro-HFT
        if entry_deal:
            duration = exit_deal['time'] - entry_deal['time']
            duration_minutes = duration.total_seconds() / 60.0
            
            if duration_minutes < 5 and 0.01 <= abs(total_profit) <= 0.50:
                return "Micro-HFT sweet spot profit"
        
        # Determine by profit
        if total_profit > 0.10:
            return "Take Profit or Trailing Stop"
        elif total_profit < -0.10:
            return "Stop Loss"
        elif abs(total_profit) <= 0.10:
            return "Small profit target or slippage"
        else:
            return "Broker Confirmed"

