"""
Order Management System
Handles order placement, modification, and tracking.
"""

import MetaTrader5 as mt5
import logging
import time
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

# Use logger factory for proper logging
from utils.logger_factory import get_logger

logger = get_logger("order_manager", "logs/live/system/order_manager.log")


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
        
        # Get mode from config if available
        mode = "UNKNOWN"
        if hasattr(self.mt5_connector, 'config'):
            mode = "BACKTEST" if self.mt5_connector.config.get('mode') == 'backtest' else "LIVE"
        
        logger.info(f"mode={mode} | symbol={symbol} | [ORDER_SENT] Sending order | "
                   f"Type: {order_type.name} | Volume: {lot_size} | Price: {price:.5f} | SL: {sl_price:.5f}")
        
        # Send order
        result = mt5.order_send(request)
        
        if result is None:
            error = mt5.last_error()
            logger.error(f"mode={mode} | symbol={symbol} | [ORDER_REJECTED] Order send returned None | MT5 error: {error}")
            # Return error dict to indicate connection/transient error (retryable)
            return {'error': -2}
        
        # Accept both full fills (TRADE_RETCODE_DONE) and partial fills (TRADE_RETCODE_PARTIAL)
        # For partial fills with ORDER_FILLING_RETURN: execute only filled portion, ignore remaining lots
        # MT5 retcodes: 10009 = DONE (full fill), 10008 = PARTIAL (partial fill)
        is_full_fill = result.retcode == mt5.TRADE_RETCODE_DONE
        # Check for partial fill (10008) - handle gracefully if constant doesn't exist
        try:
            is_partial_fill = result.retcode == mt5.TRADE_RETCODE_PARTIAL
        except AttributeError:
            # Fallback: use numeric value if constant doesn't exist
            is_partial_fill = result.retcode == 10008
        
        if not (is_full_fill or is_partial_fill):
            logger.error(f"mode={mode} | symbol={symbol} | [ORDER_REJECTED] Order rejected | "
                        f"Error code: {result.retcode} | Comment: {result.comment}")
            error_msg = f"Order failed: {result.retcode} - {result.comment}"
            
            # Detailed logging for stop loss errors (10016)
            if result.retcode == 10016:  # Invalid stops
                logger.error(f"[ERROR] {error_msg}")
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
                logger.error(f"[ERROR] {error_msg} - Volume {lot_size} is invalid for {symbol}")
                # Return error dict to indicate volume issue
                return {'error': -1}  # Special return value to indicate volume error (retryable once)
            elif result.retcode == 10027:  # Trade disabled
                logger.error(f"[ERROR] {error_msg} - Trading is disabled for {symbol}")
                logger.error(f"   This symbol cannot be traded (account or symbol restriction)")
                # Return error dict for trade disabled (non-retryable)
                return {'error': -5}
            elif result.retcode == 10044:  # Trading restriction (broker-specific, often means trading not allowed)
                logger.error(f"[ERROR] {error_msg} - Trading restriction for {symbol} {order_type.name}")
                logger.error(f"   Symbol: {symbol}, Order Type: {order_type.name}, Price: {price:.5f}, SL: {sl_price:.5f}, Lot: {lot_size}")
                logger.error(f"   This symbol/order type may not be tradeable due to account or broker restrictions")
                # Return error dict for trading restrictions (non-retryable)
                return {'error': -5}
            elif result.retcode == 10029:  # Too many requests
                logger.error(f"[ERROR] {error_msg} - Too many requests for {symbol}")
                logger.error(f"   Rate limit exceeded - wait before retrying")
                # Return error dict for rate limiting (retryable with backoff)
                return {'error': -2}
            else:
                logger.error(error_msg)
                # Log order details for other errors
                logger.error(f"   Symbol: {symbol}, Order Type: {order_type.name}, Price: {price:.5f}, SL: {sl_price:.5f}, Lot: {lot_size}")
                logger.error(f"   Error code: {result.retcode} | Comment: {result.comment}")
                
                # CRITICAL FIX: Handle specific error codes appropriately
                # 10031: Network connection error - this is transient and should be retried
                if result.retcode == 10031:
                    logger.warning(f"[WARNING] Network connection error (10031) - this is transient, will retry")
                    return {'error': -2}  # Retryable error
                
                # Check if error is likely a restriction (10000-10099 range, some are restrictions)
                # Exclude 10031 (network) and 10029 (rate limit) as they're retryable
                if 10027 <= result.retcode <= 10099 and result.retcode not in [10031, 10029]:
                    # Likely a trading restriction, don't retry
                    logger.error(f"   This appears to be a trading restriction - not retrying")
                    return {'error': -5}
                # Return error dict for other transient errors (retryable with backoff)
                return {'error': -2}
        
        # CRITICAL FIX: Get actual fill price and volume from deal history (not order request)
        # This accounts for slippage and partial fills
        actual_entry_price = price  # Default to requested price
        actual_filled_volume = lot_size  # Default to requested volume
        slippage = 0.0
        
        if result.order and result.order > 0:
            # Get deal for this order to get actual fill price and volume
            deals = mt5.history_deals_get(ticket=result.order)
            if deals and len(deals) > 0:
                # Get the entry deal (DEAL_ENTRY_IN) - this has the actual filled volume and price
                for deal in deals:
                    if deal.entry == mt5.DEAL_ENTRY_IN:
                        actual_entry_price = deal.price
                        actual_filled_volume = deal.volume
                        slippage = abs(actual_entry_price - price)
                        
                        # Log partial fill if volume differs
                        if abs(actual_filled_volume - lot_size) > 0.0001:
                            logger.info(f"[PARTIAL FILL] {symbol}: Requested {lot_size:.4f}, filled {actual_filled_volume:.4f} | "
                                      f"Remaining {lot_size - actual_filled_volume:.4f} ignored (as per requirement)")
                        
                        if slippage > 0.00001:  # Significant slippage
                            logger.warning(f"[WARNING] {symbol}: Entry slippage detected - Requested: {price:.5f}, Filled: {actual_entry_price:.5f}, Slippage: {slippage:.5f}")
                        break
        
        # Get mode from config if available
        mode = "UNKNOWN"
        if hasattr(self.mt5_connector, 'config'):
            mode = "BACKTEST" if self.mt5_connector.config.get('mode') == 'backtest' else "LIVE"
        
        fill_type = "PARTIAL FILL" if is_partial_fill else "FULL FILL"
        logger.info(f"mode={mode} | symbol={symbol} | ticket={result.order} | [ORDER_FILLED] "
                   f"{fill_type} | Type: {order_type.name} | "
                   f"Volume: {actual_filled_volume:.4f} (requested: {lot_size:.4f}) | "
                   f"SL: {sl_price:.5f} | Entry Price: {actual_entry_price:.5f} (requested: {price:.5f}) | "
                   f"Slippage: {slippage:.5f}")
        
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
        
        # Get position info - retry once if position not found (race condition handling)
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            # CRITICAL FIX: Retry once after short delay to handle race conditions
            # Position might have been temporarily unavailable due to broker processing
            import time
            time.sleep(0.05)  # 50ms delay
            position = mt5.positions_get(ticket=ticket)
            if position is None or len(position) == 0:
                # Position truly not found - likely closed or never existed
                logger.debug(f"Position {ticket} not found (after retry) - may have been closed")
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
        
        # CRITICAL FIX: Pre-validate position exists and price distance from market meets broker constraints
        # Get fresh tick data to check freeze level
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.warning(f"Cannot get tick data for {symbol} - skipping modification")
            return False
        
        current_bid = tick.bid
        current_ask = tick.ask
        freeze_level = symbol_info.get('freeze_level', 0)
        stops_level = symbol_info.get('trade_stops_level', 0)
        point = symbol_info.get('point', 0.00001)
        
        # Check if position still exists
        verify_position = mt5.positions_get(ticket=ticket)
        if verify_position is None or len(verify_position) == 0:
            logger.debug(f"Position {ticket} no longer exists - skipping modification")
            return False
        
        # Validate SL distance from market (freeze level check)
        if position.type == mt5.ORDER_TYPE_BUY:
            # For BUY: SL must be below current BID by at least freeze_level
            min_allowed_sl = current_bid - (freeze_level * point) if freeze_level > 0 else current_bid - (stops_level * point)
            if new_sl >= min_allowed_sl:
                logger.warning(f"SL {new_sl:.5f} too close to market (BID: {current_bid:.5f}, min allowed: {min_allowed_sl:.5f}, freeze_level: {freeze_level})")
                # Schedule retry with backoff - position may move away from market
                return False  # Will be retried by caller with backoff
        else:  # SELL
            # For SELL: SL must be above current ASK by at least freeze_level
            min_allowed_sl = current_ask + (freeze_level * point) if freeze_level > 0 else current_ask + (stops_level * point)
            if new_sl <= min_allowed_sl:
                logger.warning(f"SL {new_sl:.5f} too close to market (ASK: {current_ask:.5f}, min allowed: {min_allowed_sl:.5f}, freeze_level: {freeze_level})")
                # Schedule retry with backoff - position may move away from market
                return False  # Will be retried by caller with backoff
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
        }
        
        # CRITICAL FIX: Retry logic for SL modification (up to 3 attempts with exponential backoff + jitter)
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
                logger.info(f"[OK] SL UPDATE SUCCESS: Ticket={ticket} Symbol={symbol} NewSL={new_sl:.5f} OldSL={current_sl:.5f}")
                logger.info(f"Order {ticket} modified: SL {new_sl:.5f}, TP {new_tp:.5f}")
                return True
            
            # Handle specific error codes
            if result.retcode == 10025:  # No changes (already at desired level)
                logger.debug(f"Order {ticket} SL modification skipped: No changes (SL already at {new_sl:.5f})")
                return True  # Consider this success - SL is already at desired level
            
            if result.retcode == 10027:  # Trade disabled
                logger.error(f"Modify order failed for ticket {ticket}: Trading disabled (error {result.retcode})")
                return False
            
            # CRITICAL FIX: Handle error 10029 (position too close to market) and 10013 (invalid request)
            if result.retcode == 10029:  # Modification failed due to order or position being close to market
                # Verify position is still open before retrying
                verify_position = mt5.positions_get(ticket=ticket)
                if verify_position is None or len(verify_position) == 0:
                    logger.warning(f"Position {ticket} no longer exists (may have been closed) - skipping modification")
                    return False
                # Position still exists, but too close to market - retry with backoff
                if attempt < max_retries - 1:
                    logger.warning(f"Modify order failed for ticket {ticket} (attempt {attempt + 1}/{max_retries}): {result.retcode} - Position too close to market. Retrying...")
                    import time
                    import random
                    backoff = 0.2 * (2 ** attempt)  # Exponential: 0.2s, 0.4s, 0.8s
                    jitter = random.uniform(0, 0.1)  # Random jitter up to 100ms
                    time.sleep(backoff + jitter)
                    continue
                else:
                    logger.error(f"Modify order failed for ticket {ticket} after {max_retries} attempts: {result.retcode} - Position too close to market")
                    return False
            
            if result.retcode == 10013:  # Invalid request
                # Verify position state before retrying
                verify_position = mt5.positions_get(ticket=ticket)
                if verify_position is None or len(verify_position) == 0:
                    logger.warning(f"Position {ticket} no longer exists (may have been closed) - invalid request")
                    return False
                # Check if SL/TP values are within valid range
                symbol_info = self.mt5_connector.get_symbol_info(symbol)
                if symbol_info:
                    freeze_level = symbol_info.get('freeze_level', 0)
                    point = symbol_info.get('point', 0.00001)
                    current_price = verify_position[0].price_current
                    # Check if SL is too close to current price (within freeze level)
                    if abs(new_sl - current_price) < (freeze_level * point):
                        logger.warning(f"SL {new_sl:.5f} too close to current price {current_price:.5f} (freeze level: {freeze_level * point:.5f})")
                        return False
                # Invalid request for other reason - retry once
                if attempt < max_retries - 1:
                    logger.warning(f"Modify order failed for ticket {ticket} (attempt {attempt + 1}/{max_retries}): {result.retcode} - Invalid request. Retrying...")
                    import time
                    time.sleep(0.1 * (attempt + 1))
                    continue
                else:
                    logger.error(f"Modify order failed for ticket {ticket} after {max_retries} attempts: {result.retcode} - Invalid request")
                    return False
            
            if attempt < max_retries - 1:
                logger.warning(f"Modify order failed for ticket {ticket} (attempt {attempt + 1}/{max_retries}): {result.retcode} - {result.comment}. Retrying...")
                import time
                import random
                backoff = 0.1 * (2 ** attempt)  # Exponential: 0.1s, 0.2s, 0.4s
                jitter = random.uniform(0, 0.05)  # Random jitter up to 50ms
                time.sleep(backoff + jitter)
            else:
                logger.error(f"[ERROR] SL UPDATE FAILED: Ticket={ticket} Symbol={symbol} Code={result.retcode} Reason={result.comment} Attempts={max_retries}")
                logger.error(f"Modify order failed for ticket {ticket} after {max_retries} attempts: {result.retcode} - {result.comment}")
                return False
        
        return False
    
    def close_position(self, ticket: int, comment: str = "Close by bot") -> bool:
        """Close an open position."""
        from utils.execution_tracer import get_tracer
        tracer = get_tracer()
        
        tracer.trace(
            function_name="OrderManager.close_position",
            expected=f"Close position Ticket {ticket}",
            actual=f"Attempting to close position Ticket {ticket}",
            status="OK",
            ticket=ticket,
            comment=comment
        )
        
        if not self.mt5_connector.ensure_connected():
            tracer.trace(
                function_name="OrderManager.close_position",
                expected=f"Close position Ticket {ticket}",
                actual="MT5 not connected",
                status="ERROR",
                ticket=ticket,
                reason="MT5 connection failed"
            )
            return False
        
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            logger.error(f"Position {ticket} not found")
            tracer.trace(
                function_name="OrderManager.close_position",
                expected=f"Close position Ticket {ticket}",
                actual="Position not found",
                status="ERROR",
                ticket=ticket,
                reason="Position not found in MT5"
            )
            return False
        
        position = position[0]
        symbol = position.symbol
        profit = position.profit
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
        
        # Sanitize comment for MT5: Remove special characters, limit length (max 31 chars for some brokers)
        # Replace $, parentheses, and other special chars that might cause issues
        sanitized_comment = comment.replace('$', 'USD').replace('(', '').replace(')', '').replace('-', '_')
        sanitized_comment = ''.join(c for c in sanitized_comment if c.isalnum() or c in (' ', '_', '.'))  # Only allow alphanumeric, space, underscore, period
        sanitized_comment = sanitized_comment[:31]  # Limit to 31 characters (MT5 comment field limit)
        if not sanitized_comment or not sanitized_comment.strip():
            sanitized_comment = "Close by bot"  # Fallback to default
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": position.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": sanitized_comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        # Track execution time for slow execution detection
        execution_start = time.time()
        result = mt5.order_send(request)
        execution_time_ms = (time.time() - execution_start) * 1000
        
        if result is None:
            error = mt5.last_error()
            logger.error(f"Close position send returned None. MT5 error: {error}")
            tracer.trace(
                function_name="OrderManager.close_position",
                expected=f"Close position Ticket {ticket} ({symbol})",
                actual="Close order send returned None",
                status="ERROR",
                ticket=ticket,
                symbol=symbol,
                profit=profit,
                reason=f"MT5 error: {error}",
                execution_time_ms=execution_time_ms
            )
            return False
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Close position failed: {result.retcode} - {result.comment}")
            tracer.trace(
                function_name="OrderManager.close_position",
                expected=f"Close position Ticket {ticket} ({symbol})",
                actual=f"Close order failed with retcode {result.retcode}",
                status="ERROR",
                ticket=ticket,
                symbol=symbol,
                profit=profit,
                reason=f"MT5 retcode: {result.retcode} - {result.comment}",
                execution_time_ms=execution_time_ms
            )
            return False
        
        logger.info(f"Position {ticket} closed successfully")
        tracer.trace(
            function_name="OrderManager.close_position",
            expected=f"Close position Ticket {ticket} ({symbol})",
            actual=f"Position closed successfully",
            status="OK",
            ticket=ticket,
            symbol=symbol,
            profit=profit,
            execution_time_ms=execution_time_ms,
            comment=comment
        )
        
        # Log slow execution if > 300ms
        if execution_time_ms > 300:
            logger.warning(f"Slow close execution: {execution_time_ms:.0f}ms for ticket {ticket}")
        
        return True
    
    def get_open_positions(self, exclude_dec8: bool = True) -> List[Dict[str, Any]]:
        """
        Get all open positions from MT5.
        
        Args:
            exclude_dec8: If True, exclude positions opened on Dec 8, 2025 (locked positions)
        
        Returns:
            List of position dictionaries
        """
        if not self.mt5_connector.ensure_connected():
            return []
        
        positions = mt5.positions_get()
        if positions is None:
            return []
        
        from datetime import datetime, timedelta
        
        result = []
        dec8_date = datetime(2025, 12, 8).date()  # Dec 8, 2025 date
        today_date = datetime.now().date()  # Today's date
        
        # Exclude positions older than 12 hours (locked positions from previous day)
        max_age_hours = 12
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        
        excluded_count = 0
        for pos in positions:
            # Get position open time
            time_open = datetime.fromtimestamp(pos.time)
            time_open_date = time_open.date()
            
            # Skip positions from Dec 8, 2025 if exclude_dec8 is True (locked positions - markets closed)
            # Also skip positions older than 12 hours (likely from previous trading day)
            should_exclude = False
            exclusion_reason = ""
            
            if exclude_dec8:
                if time_open_date == dec8_date:
                    should_exclude = True
                    exclusion_reason = f"Dec 8, 2025 locked position (market closed)"
                elif time_open < cutoff_time:
                    should_exclude = True
                    exclusion_reason = f"Position older than {max_age_hours} hours (locked from previous day)"
                elif time_open_date < today_date:
                    should_exclude = True
                    exclusion_reason = f"Position from previous day ({time_open_date})"
            
            if should_exclude:
                excluded_count += 1
                logger.info(f"🚫 EXCLUDING {exclusion_reason}: Ticket {pos.ticket}, Symbol {pos.symbol}, Opened: {time_open} (Date: {time_open_date}, Age: {(datetime.now() - time_open).total_seconds()/3600:.2f}h)")
                continue
            
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
                'time_open': time_open,
                'comment': pos.comment
            })
        
        if excluded_count > 0:
            logger.info(f"[OK] Excluded {excluded_count} locked/old position(s) (Dec 8 or >{max_age_hours}h old). Showing {len(result)} active position(s).")
        
        return result
            
    
    def get_position_count(self, exclude_dec8: bool = True) -> int:
        """
        Get count of open positions.
        
        Args:
            exclude_dec8: If True, exclude positions opened on Dec 8, 2025 (locked positions)
        """
        positions = self.get_open_positions(exclude_dec8=exclude_dec8)
        return len(positions)
    
    def get_position_by_ticket(self, ticket: int, exclude_dec8: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get position by ticket number.
        
        Args:
            ticket: Position ticket number
            exclude_dec8: If True, exclude positions opened on Dec 8, 2025 (locked positions)
        """
        positions = self.get_open_positions(exclude_dec8=exclude_dec8)
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

