"""
Order Management System
Handles order placement, modification, and tracking.
"""

import MetaTrader5 as mt5
import logging
import time
import random
import threading
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
        
        # Position cache for faster fetches (TTL: 30ms for high-frequency SL worker)
        self._position_cache = {}  # {ticket: (position, timestamp)}
        self._position_cache_ttl = 0.030  # 30ms TTL
        self._position_cache_lock = threading.Lock()
        
        # CRITICAL FIX: Reference to SLManager for synchronous SL application when stop_loss=0.0
        # This ensures trades never remain with SL = 0.0 (hard safety invariant)
        self._sl_manager = None  # Set via set_sl_manager() after initialization
        
        # Reference to trading_bot for governance checks (set after initialization)
        self._trading_bot = None
    
    def set_sl_manager(self, sl_manager):
        """
        Set the SLManager reference for atomic SL calculation.
        
        CRITICAL: This must be called after OrderManager initialization to enable atomic SL.
        Without this, calculate_initial_sl_price() will fail and all orders will be rejected.
        
        Args:
            sl_manager: SLManager instance
        """
        self._sl_manager = sl_manager
        logger.info(f"[ORDER_MANAGER] SLManager reference set: {sl_manager is not None}")
        if sl_manager is None:
            logger.warning("[ORDER_MANAGER] WARNING: SLManager is None - atomic SL will not work!")
    
    def _get_position_by_ticket(self, ticket: int, use_cache: bool = True):
        """
        Helper method to get position by ticket, handling both SIM_LIVE and live MT5.
        Uses caching to reduce MT5 API calls for high-frequency operations.
        
        Args:
            ticket: Position ticket number
            use_cache: If True, use cached position if available and fresh (default: True)
        
        Returns:
            Position object or None if not found
        """
        # Check cache first for fast path
        if use_cache:
            with self._position_cache_lock:
                if ticket in self._position_cache:
                    cached_position, cached_time = self._position_cache[ticket]
                    cache_age = time.time() - cached_time
                    if cache_age < self._position_cache_ttl:
                        # CRITICAL FIX: Validate cached position before returning
                        # This prevents returning invalid cached data (e.g., integers)
                        if isinstance(cached_position, (int, float, str)):
                            logger.warning(f"_get_position_by_ticket: Invalid cached position type for ticket {ticket}: {type(cached_position)}. Clearing cache.")
                            del self._position_cache[ticket]
                        elif not hasattr(cached_position, 'ticket') and not hasattr(cached_position, 'symbol'):
                            logger.warning(f"_get_position_by_ticket: Cached position for ticket {ticket} missing required attributes. Clearing cache.")
                            del self._position_cache[ticket]
                        else:
                            return cached_position
                    else:
                        # Cache expired, remove it
                        del self._position_cache[ticket]
        
        position = None
        
        # Try position_get first (works for SIM_LIVE)
        if hasattr(mt5, 'position_get'):
            try:
                position = mt5.position_get(ticket)
                if position is not None:
                    # CRITICAL FIX: mt5.position_get() may return a tuple, extract first element if needed
                    if isinstance(position, (tuple, list)) and len(position) > 0:
                        position = position[0]
                    
                    # CRITICAL FIX: Validate that position is not an integer or other invalid type
                    # This prevents returning the ticket itself instead of a position object
                    if isinstance(position, (int, float, str)):
                        logger.warning(f"_get_position_by_ticket: mt5.position_get({ticket}) returned invalid type: {type(position)} (got {position})")
                        return None
                    
                    # Validate that position has required attributes
                    if not hasattr(position, 'ticket') and not hasattr(position, 'symbol'):
                        logger.warning(f"_get_position_by_ticket: mt5.position_get({ticket}) returned object without required attributes: {type(position)}")
                        return None
                    
                    # Update cache
                    if use_cache:
                        with self._position_cache_lock:
                            self._position_cache[ticket] = (position, time.time())
                    return position
            except (TypeError, AttributeError) as e:
                logger.debug(f"_get_position_by_ticket: mt5.position_get({ticket}) failed: {e}")
                pass
        
        # If position_get didn't work, try positions_get(ticket=ticket) for live MT5
        try:
            position_list = mt5.positions_get(ticket=ticket)
            if position_list is not None and len(position_list) > 0:
                position = position_list[0]
                
                # CRITICAL FIX: Validate position type
                if isinstance(position, (int, float, str)):
                    logger.warning(f"_get_position_by_ticket: mt5.positions_get(ticket={ticket}) returned invalid type: {type(position)}")
                    return None
                
                # Validate that position has required attributes
                if not hasattr(position, 'ticket') and not hasattr(position, 'symbol'):
                    logger.warning(f"_get_position_by_ticket: mt5.positions_get(ticket={ticket}) returned object without required attributes: {type(position)}")
                    return None
                
                # Update cache
                if use_cache:
                    with self._position_cache_lock:
                        self._position_cache[ticket] = (position, time.time())
                return position
        except (TypeError, AttributeError):
            # positions_get doesn't accept ticket parameter (SIM_LIVE case)
            # Fallback: get all positions and filter by ticket
            try:
                all_positions = mt5.positions_get()
                if all_positions:
                    for pos in all_positions:
                        if hasattr(pos, 'ticket') and pos.ticket == ticket:
                            # CRITICAL FIX: Validate position type before caching
                            if isinstance(pos, (int, float, str)):
                                logger.warning(f"_get_position_by_ticket: Found position with ticket {ticket} but invalid type: {type(pos)}")
                                continue
                            
                            # Update cache
                            if use_cache:
                                with self._position_cache_lock:
                                    self._position_cache[ticket] = (pos, time.time())
                            return pos
            except Exception as e:
                logger.debug(f"_get_position_by_ticket: Error getting all positions: {e}")
                pass
        
        # Position not found - clear cache entry
        if use_cache:
            with self._position_cache_lock:
                self._position_cache.pop(ticket, None)
        
        return None
    
    def _is_buy_position(self, position) -> bool:
        """
        Helper method to check if position is BUY, handling both string and integer types.
        
        Args:
            position: Position object
        
        Returns:
            True if BUY, False if SELL
        """
        pos_type = position.type if hasattr(position, 'type') else None
        if pos_type is None:
            return False
        
        # Handle string type ('BUY'/'SELL') from SIM_LIVE
        if isinstance(pos_type, str):
            return pos_type.upper() == 'BUY'
        
        # Handle integer type (0/1) from live MT5
        if isinstance(pos_type, int):
            return pos_type == mt5.ORDER_TYPE_BUY
        
        return False
    
    def _perform_trade_gating_checks(self, symbol: str, order_type: OrderType, lot_size: float) -> Dict[str, Any]:
        """
        CRITICAL SAFETY FIX #9: Trade gating - final safety checks before opening trade.
        
        Performs all mandatory safety checks:
        1. SL distance valid (checked in calculate_initial_sl_price)
        2. SL accepted synchronously (will be checked after order fill)
        3. SL visible on position immediately (will be checked after order fill)
        4. No watchdog restart in progress
        5. No worker backlog
        6. System marked SAFE
        
        Args:
            symbol: Trading symbol
            order_type: Order type
            lot_size: Lot size
            
        Returns:
            Dict with 'allowed' (bool) and 'reason' (str)
        """
        checks = {
            'allowed': True,
            'reason': None
        }
        
        # Check 4: No watchdog restart in progress
        # (This would be checked via system health, but we'll check if SL manager is available)
        if self._sl_manager:
            try:
                worker_status = self._sl_manager.get_worker_status()
                is_running = worker_status.get('running', False)
                system_type = worker_status.get('system_type', 'legacy')
                is_thread_alive = worker_status.get('thread_alive', False)
                active_positions = worker_status.get('last_position_count', 0)
                
                # For synchronous system, running=True and thread_alive=False is expected
                # Only check thread_alive for legacy thread-based systems
                if system_type == 'synchronous':
                    # Synchronous system - only check if running flag is False
                    if not is_running:
                        # Synchronous system should always be running
                        logger.warning(f"[TRADE_GATING] Synchronous SL system reported not running - blocking trade")
                        checks['sl_worker_available'] = False
                        checks['block_reason'] = "Synchronous SL system not available"
                        return checks
                elif not is_running or not is_thread_alive:
                    # Get timing stats to check if worker was recently active
                    timing_stats = self._sl_manager.get_timing_stats()
                    last_update_time = timing_stats.get('last_update_time')
                    
                    # Grace period: Allow trades if:
                    # 1. Worker was active within last 8 seconds (likely restarting), OR
                    # 2. No active positions (no immediate SL management needed)
                    from datetime import datetime
                    if last_update_time:
                        time_since_update = (datetime.now() - last_update_time).total_seconds()
                        if time_since_update <= 8.0:  # Worker was active within last 8 seconds
                            logger.info(f"[TRADE_GATING] SL worker restarting (was active {time_since_update:.1f}s ago) - allowing trade during restart window")
                            # Allow trade during brief restart window
                        elif active_positions == 0:
                            # No positions open, so no immediate SL management needed
                            logger.info(f"[TRADE_GATING] SL worker not running but no active positions - allowing trade")
                            # Allow trade if no positions
                        else:
                            # Worker has been down for > 8 seconds and we have positions - block trade
                            checks['allowed'] = False
                            checks['reason'] = f"SL worker not running (down for {time_since_update:.1f}s, {active_positions} positions)"
                            return checks
                    elif active_positions == 0:
                        # No update time available but no positions - allow trade
                        logger.info(f"[TRADE_GATING] SL worker not running but no active positions - allowing trade")
                        # Allow trade if no positions
                    else:
                        # No update time and we have positions - block trade
                        checks['allowed'] = False
                        checks['reason'] = f"SL worker not running (no recent activity, {active_positions} positions)"
                        return checks
            except Exception as e:
                logger.warning(f"[TRADE_GATING] Could not check SL worker status: {e}")
                # Don't block trade if check fails - log warning only
        
        # Check 5: No worker backlog (check if worker is processing normally)
        if self._sl_manager:
            try:
                worker_status = self._sl_manager.get_worker_status()
                active_positions = worker_status.get('last_position_count', 0)
                timing_stats = self._sl_manager.get_timing_stats()
                # Check if last update was recent (within 5 seconds)
                last_update_time = timing_stats.get('last_update_time')
                if last_update_time:
                    from datetime import datetime, timedelta
                    time_since_update = (datetime.now() - last_update_time).total_seconds()
                    # CRITICAL FIX: Only block trades if worker backlog AND we have positions
                    # When there are 0 positions, worker is idle and may not update timing stats
                    # This is normal behavior - allow trades when no positions exist
                    if time_since_update > 10.0 and active_positions > 0:  # No updates for 10 seconds AND we have positions
                        checks['allowed'] = False
                        checks['reason'] = f"SL worker backlog detected (no updates for {time_since_update:.1f}s, {active_positions} positions)"
                        return checks
                    elif time_since_update > 10.0 and active_positions == 0:
                        # Worker idle (no positions) - this is normal, allow trades
                        logger.info(f"[TRADE_GATING] SL worker idle (no updates for {time_since_update:.1f}s, 0 positions) - allowing trade")
            except Exception as e:
                logger.warning(f"[TRADE_GATING] Could not check worker backlog: {e}")
                # Don't block trade if check fails
        
        # Check 6: System marked SAFE (check if trading is blocked)
        try:
            from utils.system_health import is_trading_allowed, is_system_ready
            if not is_trading_allowed() or not is_system_ready():
                checks['allowed'] = False
                checks['reason'] = "System marked UNSAFE or not ready - trading blocked"
                return checks
        except Exception as e:
            logger.warning(f"[TRADE_GATING] Could not check system safety: {e}")
            # Don't block trade if check fails - assume safe
        
        # All checks passed
        return checks
    
    def calculate_initial_sl_price(self, symbol: str, order_type: OrderType, lot_size: float, 
                                    entry_price: float, max_risk_usd: float) -> Optional[float]:
        """
        Calculate initial SL price for order placement using SLManager logic.
        
        CRITICAL SAFETY FIX: This ensures SL is calculated BEFORE order is placed,
        allowing us to reject the order if SL cannot be set.
        
        Args:
            symbol: Trading symbol
            order_type: OrderType.BUY or OrderType.SELL
            lot_size: Lot size
            entry_price: Entry price (ASK for BUY, BID for SELL)
            max_risk_usd: Maximum risk in USD (negative value, e.g., -3.0)
        
        Returns:
            SL price if calculation succeeds, None if calculation fails
        """
        if not self._sl_manager:
            logger.error(f"[ATOMIC_SL] Cannot calculate SL: SLManager not available")
            return None
        
        try:
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if not symbol_info:
                logger.error(f"[ATOMIC_SL] Cannot get symbol info for {symbol}")
                return None
            
            order_type_str = 'BUY' if order_type == OrderType.BUY else 'SELL'
            
            # Use SLManager's calculation method
            target_sl = self._sl_manager._calculate_target_sl_price(
                entry_price=entry_price,
                target_profit_usd=max_risk_usd,  # Negative value for loss protection
                order_type=order_type_str,
                lot_size=lot_size,
                symbol_info=symbol_info,
                position=None  # No position yet
            )
            
            # Validate SL against broker constraints BEFORE order placement
            point = symbol_info.get('point', 0.00001)
            stops_level = symbol_info.get('trade_stops_level', 0)
            
            # Get current market prices for validation
            if hasattr(self.mt5_connector, 'get_symbol_info_tick'):
                tick = self.mt5_connector.get_symbol_info_tick(symbol)
            else:
                tick = mt5.symbol_info_tick(symbol)
            
            if not tick:
                logger.error(f"[ATOMIC_SL] Cannot get tick data for {symbol}")
                return None
            
            current_bid = tick.bid
            current_ask = tick.ask
            
            # Adjust SL for broker constraints
            adjusted_sl = self._sl_manager._adjust_sl_for_broker_constraints(
                target_sl=target_sl,
                current_sl=0.0,
                order_type=order_type_str,
                symbol_info=symbol_info,
                current_bid=current_bid,
                current_ask=current_ask,
                entry_price=entry_price
            )
            
            # Validate adjusted SL is still valid
            if order_type == OrderType.BUY:
                validation_price = current_ask
                if adjusted_sl >= validation_price:
                    logger.error(f"[ATOMIC_SL] Invalid SL for BUY: {adjusted_sl:.5f} >= {validation_price:.5f}")
                    return None
            else:  # SELL
                validation_price = current_ask
                if adjusted_sl <= validation_price:
                    logger.error(f"[ATOMIC_SL] Invalid SL for SELL: {adjusted_sl:.5f} <= {validation_price:.5f}")
                    return None
            
            # Check stops_level constraint
            if stops_level > 0:
                min_distance = stops_level * point
                actual_distance = abs(validation_price - adjusted_sl)
                if actual_distance < min_distance:
                    logger.error(f"[ATOMIC_SL] SL too close to market: {actual_distance:.5f} < {min_distance:.5f} (stops_level: {stops_level})")
                    return None
            
            # CRITICAL SAFETY FIX: Validate adjusted SL is not too close to entry price
            # If SL is too close, it will cause immediate early closure
            # For BUY: SL should be below entry, distance should be entry - sl
            # For SELL: SL should be above entry, distance should be sl - entry
            if order_type == OrderType.BUY:
                actual_sl_distance = entry_price - adjusted_sl
                expected_sl_distance = entry_price - target_sl
            else:  # SELL
                actual_sl_distance = adjusted_sl - entry_price
                expected_sl_distance = target_sl - entry_price
            
            # CRITICAL: Validate that adjusted SL distance is not significantly less than target
            # If adjusted SL is more than 50% closer than target, it's too close (would cause early closure)
            # This works for both forex (percentage-based) and indices/commodities (point-based)
            if expected_sl_distance > 0:
                distance_ratio = actual_sl_distance / expected_sl_distance
                if distance_ratio < 0.5:  # Adjusted SL is less than 50% of target distance
                    logger.error(f"[ATOMIC_SL] Adjusted SL too close to entry: {adjusted_sl:.5f} "
                               f"(entry: {entry_price:.5f}, actual distance: {actual_sl_distance:.5f}) | "
                               f"Target SL: {target_sl:.5f}, expected distance: {expected_sl_distance:.5f} | "
                               f"Distance ratio: {distance_ratio:.2%} | "
                               f"REJECTING ORDER - would cause early closure")
                    return None
            else:
                # Fallback: If target SL calculation is wrong, use absolute minimum
                # For indices/commodities: minimum 0.1% of entry
                # For forex: minimum 0.5% of entry
                point = symbol_info.get('point', 0.00001)
                is_index_or_commodity = (point >= 0.01) or symbol_info.get('trade_tick_value', None) is not None
                
                if is_index_or_commodity:
                    min_safe_distance = entry_price * 0.001  # 0.1% for indices/commodities
                else:
                    min_safe_distance = entry_price * 0.005  # 0.5% for forex
                
                if actual_sl_distance < min_safe_distance:
                    logger.error(f"[ATOMIC_SL] Adjusted SL too close to entry: {adjusted_sl:.5f} "
                               f"(entry: {entry_price:.5f}, distance: {actual_sl_distance:.5f}) | "
                               f"Min safe distance: {min_safe_distance:.5f} | "
                               f"REJECTING ORDER - would cause immediate early closure")
                    return None
            
            # Additional check: If adjusted SL is significantly different from target, warn
            sl_adjustment_error = abs(adjusted_sl - target_sl)
            if sl_adjustment_error > entry_price * 0.01:  # More than 1% difference
                logger.warning(f"[ATOMIC_SL] SL adjusted significantly: target {target_sl:.5f} -> adjusted {adjusted_sl:.5f} "
                             f"(diff: {sl_adjustment_error:.5f}) | This may affect risk calculation")
            
            logger.info(f"[ATOMIC_SL] Calculated SL for {symbol} {order_type_str}: {adjusted_sl:.5f} (target: {target_sl:.5f}, distance from entry: {actual_sl_distance:.5f})")
            return adjusted_sl
            
        except Exception as e:
            logger.error(f"[ATOMIC_SL] Exception calculating SL for {symbol}: {e}", exc_info=True)
            return None
    
    def place_order(
        self,
        symbol: str,
        order_type: OrderType,
        lot_size: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        comment: str = "Trading Bot",
        max_risk_usd: Optional[float] = None,
        strategy_sl_price: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Place a market order with ATOMIC SL (SL included in order request).
        
        CRITICAL SAFETY FIX: Orders MUST include SL in the request. If SL cannot be calculated
        or validated, the order is REJECTED and not placed.
        
        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            order_type: OrderType.BUY or OrderType.SELL
            lot_size: Lot size (e.g., 0.01)
            stop_loss: Stop loss in pips (DEPRECATED - will be calculated from max_risk_usd)
            take_profit: Take profit in pips (optional)
            comment: Order comment
            max_risk_usd: Maximum risk in USD (negative value, e.g., -3.0). If provided, SL is calculated from this.
        
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
        # Use connector method to support both live and SIM_LIVE modes
        if hasattr(self.mt5_connector, 'get_symbol_info_tick'):
            tick = self.mt5_connector.get_symbol_info_tick(symbol)
        else:
            # Fallback to direct MT5 call for live mode
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
        
        # CRITICAL SAFETY FIX #9: TRADE GATING - Final safety checks before opening trade
        # These checks are the last line of defense before placing an order
        gate_checks = self._perform_trade_gating_checks(symbol, order_type, lot_size)
        if not gate_checks['allowed']:
            logger.error(f"[TRADE_GATING_REJECTED] {symbol} | Order rejected by trade gate | "
                        f"Reason: {gate_checks['reason']}")
            return {'error': -8, 'error_type': 'trade_gating_failed', 
                   'mt5_comment': f"Trade gating check failed: {gate_checks['reason']}"}
        
        # CRITICAL SAFETY FIX #1: Calculate SL BEFORE order placement
        # Priority: strategy_sl_price > max_risk_usd > stop_loss
        digits = symbol_info.get('digits', 5)
        if strategy_sl_price is not None and strategy_sl_price > 0:
            # Use strategy-based SL price directly
            sl_price = strategy_sl_price
            # Normalize to symbol's tick size
            sl_price = round(sl_price / point) * point
            sl_price = round(sl_price, digits)
            
            # Validate strategy SL is in correct direction
            if order_type == OrderType.BUY:
                if sl_price >= price:
                    logger.error(f"[STRATEGY_SL_INVALID] Strategy SL ({sl_price:.5f}) >= entry ({price:.5f}) for BUY - REJECTING ORDER")
                    return {'error': -6, 'error_type': 'strategy_sl_invalid', 'mt5_comment': 'Strategy SL invalid for BUY order'}
            else:  # SELL
                if sl_price <= price:
                    logger.error(f"[STRATEGY_SL_INVALID] Strategy SL ({sl_price:.5f}) <= entry ({price:.5f}) for SELL - REJECTING ORDER")
                    return {'error': -6, 'error_type': 'strategy_sl_invalid', 'mt5_comment': 'Strategy SL invalid for SELL order'}
            
            # Calculate TP if provided
            if take_profit and take_profit > 0:
                if order_type == OrderType.BUY:
                    tp_price = price + (take_profit * pip_value)
                else:  # SELL
                    tp_price = price - (take_profit * pip_value)
            else:
                tp_price = 0
            
            # Normalize TP to symbol's tick size
            if tp_price > 0:
                tp_price = round(tp_price / point) * point
                tp_price = round(tp_price, digits)
            
            # Validation price for constraint checking
            validation_price = ask_price if order_type == OrderType.SELL else price
            
            logger.info(f"[ATOMIC_SL] {symbol} {order_type.name}: Using strategy SL={sl_price:.5f}")
        elif max_risk_usd is not None and max_risk_usd < 0:
            # Calculate SL using SLManager logic (fallback to USD-based)
            sl_price = self.calculate_initial_sl_price(symbol, order_type, lot_size, price, max_risk_usd)
            if sl_price is None:
                logger.error(f"[ATOMIC_SL_FAILED] Cannot calculate valid SL for {symbol} - REJECTING ORDER")
                return {'error': -6, 'error_type': 'sl_calculation_failed', 'mt5_comment': 'SL calculation failed - order rejected for safety'}
            
            # Calculate TP if provided
            if take_profit and take_profit > 0:
                if order_type == OrderType.BUY:
                    tp_price = price + (take_profit * pip_value)
                else:  # SELL
                    tp_price = price - (take_profit * pip_value)
            else:
                tp_price = 0
            
            # Normalize TP to symbol's tick size
            if tp_price > 0:
                tp_price = round(tp_price / point) * point
                tp_price = round(tp_price, digits)
            
            # Validation price for constraint checking
            validation_price = ask_price if order_type == OrderType.SELL else price
            
            logger.info(f"[ATOMIC_SL] {symbol} {order_type.name}: SL={sl_price:.5f} calculated from max_risk=${max_risk_usd:.2f}")
        elif stop_loss == 0.0:
            # CRITICAL: Orders MUST have SL. Reject if stop_loss=0.0 and no max_risk_usd provided.
            logger.error(f"[ATOMIC_SL_REJECTED] Order rejected: stop_loss=0.0 and no max_risk_usd provided. Orders MUST have SL.")
            return {'error': -6, 'error_type': 'sl_required', 'mt5_comment': 'SL is required - order rejected for safety'}
        else:
            # Calculate stop loss price when stop_loss > 0
            stop_loss_price = stop_loss * pip_value
            
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
        # In SIM_LIVE mode, use connector's get_symbol_info() which returns a dict
        # In live mode, use mt5.symbol_info() which returns an object
        filling_type = None
        filling_modes = None
        
        # Try to get filling_mode from connector's symbol_info dict first (works for SIM_LIVE)
        if hasattr(self.mt5_connector, 'get_symbol_info'):
            symbol_info_dict = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
            if symbol_info_dict and isinstance(symbol_info_dict, dict):
                filling_modes = symbol_info_dict.get('filling_mode')
                if filling_modes is not None:
                    logger.debug(f"{symbol}: Got filling_mode from connector: {filling_modes}")
        
        # Fallback to mt5.symbol_info() for live mode (returns object with attributes)
        if filling_modes is None:
            symbol_info_obj = mt5.symbol_info(symbol)
            if symbol_info_obj is not None:
                filling_modes = symbol_info_obj.filling_mode if hasattr(symbol_info_obj, 'filling_mode') else None
        
        # Determine filling type from bitmask
        if filling_modes is not None:
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
        
        # If still None, use default for SIM_LIVE or error for live
        if filling_type is None:
            # Check if we're in SIM_LIVE mode
            is_sim_live = hasattr(self.mt5_connector, '_market_engine') or hasattr(self.mt5_connector, 'get_symbol_info')
            if is_sim_live:
                # Default to RETURN for SIM_LIVE (most common for forex)
                filling_type = mt5.ORDER_FILLING_RETURN
                logger.info(f"{symbol}: Using default filling mode ORDER_FILLING_RETURN for SIM_LIVE mode")
            else:
                logger.error(f"{symbol}: No supported filling mode found! Symbol filling_mode: {filling_modes if filling_modes is not None else 'N/A'}")
                return None
        
        # Prepare order request
        # For MT5, SL and TP must be omitted if not set (MT5 rejects sl=0 or tp=0)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type.value,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        # Only include SL if it's > 0 (MT5 rejects sl=0)
        if sl_price > 0:
            request["sl"] = sl_price
        
        # Only include TP if it's > 0 (some brokers don't accept TP=0)
        if tp_price > 0:
            request["tp"] = tp_price
        
        # Get mode from config if available
        mode = "UNKNOWN"
        if hasattr(self.mt5_connector, 'config'):
            mode = "BACKTEST" if self.mt5_connector.config.get('mode') == 'backtest' else "LIVE"
        
        sl_display = f"{sl_price:.5f}" if sl_price > 0 else "None (will be applied later)"
        logger.info(f"mode={mode} | symbol={symbol} | [ORDER_SENT] Sending order | "
                   f"Type: {order_type.name} | Volume: {lot_size} | Price: {price:.5f} | SL: {sl_display}")
        
        # Send order
        result = mt5.order_send(request)
        
        if result is None:
            error = mt5.last_error()
            # Extract error code and description
            error_code = None
            error_description = str(error)
            if isinstance(error, tuple) and len(error) >= 2:
                error_code = error[0]
                error_description = error[1]
            elif hasattr(error, 'retcode'):
                error_code = error.retcode
                if hasattr(error, 'description'):
                    error_description = error.description
            
            logger.error(f"mode={mode} | symbol={symbol} | [ORDER_REJECTED] Order send returned None | MT5 error code: {error_code} | Description: {error_description}")
            logger.error(f"   Full error details: {error}")
            # Return error dict to indicate connection/transient error (retryable)
            return {'error': -2, 'mt5_retcode': error_code, 'mt5_comment': error_description, 'error_type': 'connection_error'}
        
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
            # CRITICAL: Log detailed error information
            logger.error(f"mode={mode} | symbol={symbol} | [ORDER_REJECTED] Order rejected | "
                        f"MT5 Error Code: {result.retcode} | Comment: {result.comment}")
            logger.error(f"   Order details: Symbol={symbol}, Type={order_type.name}, Lot={lot_size:.4f}, Price={price:.5f}, SL={sl_price:.5f}")
            logger.error(f"   Request details: {request}")
            
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
                return {'error': -3, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'invalid_stops'}
            elif result.retcode == 10018:  # Market closed
                logger.warning(f"⏰ {error_msg} - Market is closed for {symbol}, skipping order placement")
                # Don't retry when market is closed - it won't help
                # Return error dict for market closed (non-retryable)
                return {'error': -4, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'market_closed'}
            elif result.retcode == 10014:  # Invalid volume
                # Get symbol info for detailed logging
                symbol_info_detail = self.mt5_connector.get_symbol_info(symbol)
                volume_min = symbol_info_detail.get('volume_min', 'N/A') if symbol_info_detail else 'N/A'
                volume_max = symbol_info_detail.get('volume_max', 'N/A') if symbol_info_detail else 'N/A'
                volume_step = symbol_info_detail.get('volume_step', 'N/A') if symbol_info_detail else 'N/A'
                
                logger.error(f"[ERROR] {error_msg} - Volume {lot_size} is invalid for {symbol}")
                logger.error(f"   Attempted lot size: {lot_size:.4f}")
                logger.error(f"   Symbol requirements: min={volume_min}, max={volume_max}, step={volume_step}")
                logger.error(f"   Order details: Type={order_type.name}, Price={price:.5f}, SL={sl_price:.5f}")
                
                # Return error dict to indicate volume issue
                return {'error': -1, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'invalid_volume'}
            elif result.retcode == 10027:  # Trade disabled
                logger.error(f"[ERROR] {error_msg} - Trading is disabled for {symbol}")
                logger.error(f"   This symbol cannot be traded (account or symbol restriction)")
                # Return error dict for trade disabled (non-retryable)
                return {'error': -5, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'trade_disabled'}
            elif result.retcode == 10044:  # Trading restriction (broker-specific, often means trading not allowed)
                logger.error(f"[ERROR] {error_msg} - Trading restriction for {symbol} {order_type.name}")
                logger.error(f"   Symbol: {symbol}, Order Type: {order_type.name}, Price: {price:.5f}, SL: {sl_price:.5f}, Lot: {lot_size}")
                logger.error(f"   This symbol/order type may not be tradeable due to account or broker restrictions")
                # Return error dict for trading restrictions (non-retryable)
                return {'error': -5, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'trading_restriction'}
            elif result.retcode == 10029:  # Too many requests
                logger.error(f"[ERROR] {error_msg} - Too many requests for {symbol}")
                logger.error(f"   Rate limit exceeded - wait before retrying")
                # Return error dict for rate limiting (retryable with backoff)
                return {'error': -2, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'rate_limit'}
            else:
                logger.error(error_msg)
                # Log order details for other errors
                logger.error(f"   Symbol: {symbol}, Order Type: {order_type.name}, Price: {price:.5f}, SL: {sl_price:.5f}, Lot: {lot_size}")
                logger.error(f"   Error code: {result.retcode} | Comment: {result.comment}")
                
                # CRITICAL FIX: Handle specific error codes appropriately
                # 10031: Network connection error - this is transient and should be retried
                if result.retcode == 10031:
                    logger.warning(f"[WARNING] Network connection error (10031) - this is transient, will retry")
                    return {'error': -2, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'network_error'}
                
                # Check if error is likely a restriction (10000-10099 range, some are restrictions)
                # Exclude 10031 (network) and 10029 (rate limit) as they're retryable
                if 10027 <= result.retcode <= 10099 and result.retcode not in [10031, 10029]:
                    # Likely a trading restriction, don't retry
                    logger.error(f"   This appears to be a trading restriction - not retrying")
                    return {'error': -5, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'trading_restriction'}
                # Return error dict for other transient errors (retryable with backoff)
                return {'error': -2, 'mt5_retcode': result.retcode, 'mt5_comment': result.comment, 'error_type': 'transient_error'}
        
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
                        
                        # P2-11 FIX: Partial Fill Risk Adjustment - Recalculate risk after fill
                        is_partial_fill = abs(actual_filled_volume - lot_size) > 0.0001
                        if is_partial_fill:
                            logger.info(f"[PARTIAL FILL] {symbol}: Requested {lot_size:.4f}, filled {actual_filled_volume:.4f} | "
                                      f"Remaining {lot_size - actual_filled_volume:.4f} ignored (as per requirement)")
                            
                            # P2-11 FIX: Log risk adjustment for partial fills
                            # Risk is proportional to filled volume, so actual risk is less than calculated
                            risk_adjustment_factor = actual_filled_volume / lot_size if lot_size > 0 else 1.0
                            logger.info(f"[PARTIAL_FILL_RISK] {symbol} Ticket {ticket} | "
                                      f"Risk adjustment factor: {risk_adjustment_factor:.4f} (filled {actual_filled_volume:.4f} of {lot_size:.4f})")
                        
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
        
        # P1-6 FIX: Order Execution Verification Loop - Enhanced verification with retry logic
        # CRITICAL SAFETY FIX #3: Synchronously verify SL was applied by broker
        # If SL is missing or incorrect, immediately close the position (fail-safe)
        ticket = result.order
        import time
        
        # P1-6 FIX: Verification loop with retry (max 3 attempts, 1s delay)
        max_verification_attempts = 3
        verification_delay = 1.0  # 1 second delay between attempts
        position = None
        verification_success = False
        
        for verification_attempt in range(max_verification_attempts):
            time.sleep(verification_delay if verification_attempt > 0 else 0.05)  # 50ms for first attempt, 1s for retries
            
            position = self._get_position_by_ticket(ticket, use_cache=False)
            if position:
                # Get actual SL from position
                actual_sl = position.sl if hasattr(position, 'sl') else position.get('sl', 0.0)
                
                # CRITICAL: If SL is 0.0 or missing, this is a SAFETY VIOLATION
                if actual_sl == 0.0 or actual_sl is None:
                    if verification_attempt < max_verification_attempts - 1:
                        logger.warning(f"[ATOMIC_SL_VERIFICATION] {symbol} Ticket {ticket} | "
                                     f"SL verification failed (attempt {verification_attempt + 1}/{max_verification_attempts}) - retrying...")
                        continue  # Retry verification
                    else:
                        # Final attempt failed - close position
                        logger.critical(f"[ATOMIC_SL_VERIFICATION_FAILED] {symbol} Ticket {ticket} | "
                                      f"CRITICAL: Position opened with SL=0.0 despite being in order request! "
                                      f"Closing position immediately for safety.")
                        self.close_position(ticket, comment="SL verification failed - safety violation")
                        return {'error': -7, 'error_type': 'sl_verification_failed', 
                               'mt5_comment': 'SL verification failed - position closed for safety'}
                
                # Verify SL is within tolerance (allowing for broker rounding)
                sl_tolerance = point * 10  # Allow 10 points tolerance for broker rounding
                if abs(actual_sl - sl_price) > sl_tolerance:
                    if verification_attempt < max_verification_attempts - 1:
                        logger.warning(f"[ATOMIC_SL_VERIFICATION] {symbol} Ticket {ticket} | "
                                     f"SL mismatch (attempt {verification_attempt + 1}/{max_verification_attempts}) - retrying...")
                        continue  # Retry verification
                    else:
                        logger.warning(f"[ATOMIC_SL_VERIFICATION_WARNING] {symbol} Ticket {ticket} | "
                                     f"SL mismatch: Expected {sl_price:.5f}, Got {actual_sl:.5f} (diff: {abs(actual_sl - sl_price):.5f})")
                        # Log warning but don't close - broker may have adjusted SL slightly
                else:
                    logger.info(f"[ATOMIC_SL_VERIFIED] {symbol} Ticket {ticket} | "
                              f"SL verified: {actual_sl:.5f} (expected: {sl_price:.5f})")
                
                verification_success = True
                break  # Verification successful
        
        if not verification_success:
            # P1-6 FIX: Mark order as pending if verification fails
            logger.warning(f"[ORDER_VERIFICATION] {symbol} Ticket {ticket} | "
                         f"Position verification failed after {max_verification_attempts} attempts - marking as pending")
            # Note: Position may still exist but verification failed - will retry in next cycle
            # Don't return error - let position monitoring handle it
        
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
        
        # CRITICAL FIX: Use helper method to get position by ticket (handles both SIM_LIVE and live MT5)
        position = self._get_position_by_ticket(ticket)
        if position is None:
            # Retry once after short delay
            time.sleep(0.05)  # 50ms delay
            position = self._get_position_by_ticket(ticket)
            if position is None:
                # Position truly not found - likely closed or never existed
                logger.debug(f"Position {ticket} not found (after retry) - may have been closed")
                return False
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
            
            # CRITICAL FIX: Use helper method to handle both string and integer position types
            if self._is_buy_position(position):
                current_price = symbol_info['ask']
                new_sl = current_price - (stop_loss * pip_value)
            else:  # SELL
                current_price = symbol_info['bid']
                new_sl = current_price + (stop_loss * pip_value)
        else:
            new_sl = position.sl
        
        # CRITICAL FIX: Check if new SL is effectively the same as current SL before modifying
        # This prevents error 10025 (No changes)
        # BUT: Skip this check if we're only modifying TP (stop_loss_price and stop_loss are both None)
        # Otherwise we return True before modifying TP!
        current_sl = position.sl
        is_sl_modification = stop_loss_price is not None or stop_loss is not None
        if current_sl > 0 and is_sl_modification:
            point = symbol_info.get('point', 0.00001)
            # Use point size as tolerance (if prices differ by less than 1 point, consider them equal)
            sl_difference = abs(new_sl - current_sl)
            if sl_difference < point:
                logger.debug(f"Skip SL modification for ticket {ticket}: new SL {new_sl:.5f} equals current SL {current_sl:.5f} (diff: {sl_difference:.8f} < point: {point:.8f})")
                # CRITICAL FIX: Only return True if we're not also modifying TP
                # If we're only modifying TP, continue with the modification
                if take_profit_price is None and take_profit is None:
                    return True  # Return True since SL is already at desired level and no TP to modify
                # Otherwise, continue to modify TP even though SL is unchanged
        
        if take_profit_price is not None:
            new_tp = take_profit_price
        elif take_profit is not None:
            # Convert pips to price relative to current price
            point = symbol_info['point']
            pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
            
            # CRITICAL FIX: Use helper method to handle both string and integer position types
            if self._is_buy_position(position):
                current_price = symbol_info['ask']
                new_tp = current_price + (take_profit * pip_value)
            else:  # SELL
                current_price = symbol_info['bid']
                new_tp = current_price - (take_profit * pip_value)
        else:
            new_tp = position.tp
        
        # CRITICAL FIX: Pre-validate position exists and price distance from market meets broker constraints
        # Get fresh tick data to check freeze level
        # Use connector method to support both live and SIM_LIVE modes
        if hasattr(self.mt5_connector, 'get_symbol_info_tick'):
            tick = self.mt5_connector.get_symbol_info_tick(symbol)
        else:
            # Fallback to direct MT5 call for live mode
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
        verify_position = self._get_position_by_ticket(ticket)
        if verify_position is None:
            logger.debug(f"Position {ticket} no longer exists - skipping modification")
            return False
        
        # Validate SL distance from market (freeze level check)
        # CRITICAL FIX: Use helper method to handle both string and integer position types
        if self._is_buy_position(position):
            # For BUY: SL must be below current BID by at least freeze_level
            # When freeze_level=0 and stops_level=0, allow SL anywhere below BID (no minimum distance)
            if freeze_level > 0:
                min_allowed_sl = current_bid - (freeze_level * point)
            elif stops_level > 0:
                min_allowed_sl = current_bid - (stops_level * point)
            else:
                # No freeze/stops level - SL just needs to be below BID (but allow small tolerance for rounding)
                min_allowed_sl = current_bid - (point * 0.1)  # Very small buffer for rounding
            
            if new_sl >= min_allowed_sl:
                logger.warning(f"SL {new_sl:.5f} too close to market (BID: {current_bid:.5f}, min allowed: {min_allowed_sl:.5f}, freeze_level: {freeze_level}, stops_level: {stops_level})")
                # CRITICAL FIX: Adjust SL to minimum allowed distance with larger buffer to prevent repeated failures
                # Use at least 2 points buffer (or 10% of stops_level, whichever is larger) to prevent "Invalid stops" errors
                buffer_points = max(2, int(stops_level * 0.1)) if stops_level > 0 else 2
                new_sl = min_allowed_sl - (point * buffer_points)  # Larger buffer below minimum
                # Normalize to symbol's point precision
                digits = symbol_info.get('digits', 5)
                new_sl = round(new_sl / point) * point
                new_sl = round(new_sl, digits)
                logger.info(f"[SL_ADJUSTED] SL adjusted to minimum allowed distance with {buffer_points} point buffer: {new_sl:.5f}")
        else:  # SELL
            # For SELL: SL must be above current ASK by at least freeze_level
            # When freeze_level=0 and stops_level=0, allow SL anywhere above ASK (no minimum distance)
            if freeze_level > 0:
                min_allowed_sl = current_ask + (freeze_level * point)
            elif stops_level > 0:
                min_allowed_sl = current_ask + (stops_level * point)
            else:
                # No freeze/stops level - SL just needs to be above ASK (but allow small tolerance for rounding)
                min_allowed_sl = current_ask + (point * 0.1)  # Very small buffer for rounding
            
            if new_sl <= min_allowed_sl:
                logger.warning(f"SL {new_sl:.5f} too close to market (ASK: {current_ask:.5f}, min allowed: {min_allowed_sl:.5f}, freeze_level: {freeze_level}, stops_level: {stops_level})")
                # CRITICAL FIX: Adjust SL to minimum allowed distance with larger buffer to prevent repeated failures
                # Use at least 2 points buffer (or 10% of stops_level, whichever is larger) to prevent "Invalid stops" errors
                buffer_points = max(2, int(stops_level * 0.1)) if stops_level > 0 else 2
                new_sl = min_allowed_sl + (point * buffer_points)  # Larger buffer above minimum
                # Normalize to symbol's point precision
                digits = symbol_info.get('digits', 5)
                new_sl = round(new_sl / point) * point
                new_sl = round(new_sl, digits)
                logger.info(f"[SL_ADJUSTED] SL adjusted to minimum allowed distance with {buffer_points} point buffer: {new_sl:.5f}")
        
        # CRITICAL FIX: Validate TP distance from market (MT5 requires TP to be minimum distance from market)
        # This is the core issue - TP was being rejected silently by MT5 if too close to market
        if take_profit_price is not None and new_tp > 0:
            if self._is_buy_position(position):
                # For BUY: TP must be above current ASK by at least freeze_level/stops_level
                if freeze_level > 0:
                    min_allowed_tp = current_ask + (freeze_level * point)
                elif stops_level > 0:
                    min_allowed_tp = current_ask + (stops_level * point)
                else:
                    # No freeze/stops level - TP just needs to be above ASK (but allow small tolerance for rounding)
                    min_allowed_tp = current_ask + (point * 0.1)  # Very small buffer for rounding
                
                if new_tp <= min_allowed_tp:
                    logger.warning(f"[TP_VALIDATION] TP {new_tp:.5f} too close to market (ASK: {current_ask:.5f}, min allowed: {min_allowed_tp:.5f}, freeze_level: {freeze_level}, stops_level: {stops_level})")
                    # Adjust TP to minimum allowed distance
                    new_tp = min_allowed_tp
                    # Normalize to symbol's point precision
                    digits = symbol_info.get('digits', 5)
                    new_tp = round(new_tp / point) * point
                    new_tp = round(new_tp, digits)
                    logger.info(f"[TP_ADJUSTED] TP adjusted to minimum allowed distance: {new_tp:.5f}")
                else:
                    # TP passed validation - still normalize to ensure correct precision
                    digits = symbol_info.get('digits', 5)
                    new_tp = round(new_tp / point) * point
                    new_tp = round(new_tp, digits)
            else:  # SELL
                # For SELL: TP must be below current BID by at least freeze_level/stops_level
                if freeze_level > 0:
                    min_allowed_tp = current_bid - (freeze_level * point)
                elif stops_level > 0:
                    min_allowed_tp = current_bid - (stops_level * point)
                else:
                    # No freeze/stops level - TP just needs to be below BID (but allow small tolerance for rounding)
                    min_allowed_tp = current_bid - (point * 0.1)  # Very small buffer for rounding
                
                if new_tp >= min_allowed_tp:
                    logger.warning(f"[TP_VALIDATION] TP {new_tp:.5f} too close to market (BID: {current_bid:.5f}, min allowed: {min_allowed_tp:.5f}, freeze_level: {freeze_level}, stops_level: {stops_level})")
                    # Adjust TP to minimum allowed distance
                    new_tp = min_allowed_tp
                    # Normalize to symbol's point precision
                    digits = symbol_info.get('digits', 5)
                    new_tp = round(new_tp / point) * point
                    new_tp = round(new_tp, digits)
                    logger.info(f"[TP_ADJUSTED] TP adjusted to minimum allowed distance: {new_tp:.5f}")
                else:
                    # TP passed validation - still normalize to ensure correct precision
                    digits = symbol_info.get('digits', 5)
                    new_tp = round(new_tp / point) * point
                    new_tp = round(new_tp, digits)
        
        # CRITICAL FIX: Only include SL/TP in request if they are > 0 (MT5 rejects 0 values)
        # This is the core issue - MT5 silently ignores TP=0 in the request
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
        }
        
        # Only include SL if it's > 0
        if new_sl > 0:
            request["sl"] = new_sl
        
        # Only include TP if it's > 0 (CRITICAL FIX: MT5 silently ignores TP=0)
        if new_tp > 0:
            request["tp"] = new_tp
        
        # Log the request details for debugging
        logger.info(f"[MODIFY_ORDER] Ticket {ticket} | Symbol {symbol} | "
                   f"SL: {new_sl:.5f} (included: {new_sl > 0}) | "
                   f"TP: {new_tp:.5f} (included: {new_tp > 0})")
        
        # PHASE 1 FIX 1.2: Add timeout protection to MT5 API call
        # Use 2 second timeout to allow for network delays while still failing fast
        modify_start_time = time.time()
        modify_timeout_seconds = 2.0  # 2 second timeout for MT5 API call (increased from 1.0s for reliability)
        
        # PHASE 1 FIX 1.2: Pre-check MT5 connection health before attempting
        # If MT5 is unresponsive, skip immediately to prevent blocking
        try:
            # Quick health check: try to get terminal info (non-blocking check)
            terminal_info = mt5.terminal_info()
            if terminal_info is None:
                logger.warning(f"[MT5_UNRESPONSIVE] Ticket {ticket} | MT5 terminal_info() returned None - skipping modify")
                return False
            
            # Check if terminal is connected
            if not terminal_info.connected:
                logger.warning(f"[MT5_DISCONNECTED] Ticket {ticket} | MT5 terminal not connected - skipping modify")
                return False
        except Exception as health_check_error:
            logger.warning(f"[MT5_HEALTH_CHECK_FAILED] Ticket {ticket} | Health check error: {health_check_error} - skipping modify")
            return False
        
        # CRITICAL FIX: Retry logic for SL modification (up to 2 attempts with fast timeout)
        # Reduced retries from 3 to 2 to fail faster
        max_retries = 2
        for attempt in range(max_retries):
            # PHASE 1 FIX 1.2: Check if we've already exceeded timeout before attempting
            elapsed_time = time.time() - modify_start_time
            if elapsed_time > modify_timeout_seconds:
                logger.warning(f"[MODIFY_TIMEOUT] Ticket {ticket} | MT5 modify_order timeout: {elapsed_time:.2f}s > {modify_timeout_seconds}s limit")
                return False  # Timeout - return False immediately
            
            # PHASE 1 FIX 1.2: Quick connection check before each attempt
            if not self.mt5_connector.ensure_connected():
                logger.warning(f"[MT5_NOT_CONNECTED] Ticket {ticket} | MT5 not connected on attempt {attempt + 1}")
                return False
            
            # Make MT5 API call with timeout tracking
            call_start = time.time()
            result = mt5.order_send(request)
            call_duration = time.time() - call_start
            
            # PHASE 1 FIX 1.2: Check if call took too long (even if it succeeded)
            if call_duration > modify_timeout_seconds:
                logger.warning(f"[MODIFY_SLOW] Ticket {ticket} | MT5 modify_order took {call_duration:.2f}s (exceeded {modify_timeout_seconds}s limit)")
                # If call succeeded but was slow, return True but log warning
                # If call failed, continue to retry or return False
                if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.warning(f"[MODIFY_SLOW_SUCCESS] Ticket {ticket} | Modify succeeded but was slow ({call_duration:.2f}s)")
                    # Continue to success handling below
                else:
                    # Call failed and was slow - retry if attempts remaining
                    if attempt < max_retries - 1:
                        logger.warning(f"[MODIFY_SLOW_RETRY] Ticket {ticket} | Retrying after slow call (attempt {attempt + 1}/{max_retries})")
                        time.sleep(0.05)  # Short delay before retry
                        continue
                    else:
                        return False
            
            if result is None:
                error = mt5.last_error()
                if attempt < max_retries - 1:
                    logger.warning(f"Modify order send returned None for ticket {ticket} (attempt {attempt + 1}/{max_retries}). MT5 error: {error}. Retrying...")
                    time.sleep(0.1 * (attempt + 1))  # Increasing backoff
                    continue
                else:
                    logger.error(f"Modify order send returned None for ticket {ticket} after {max_retries} attempts. MT5 error: {error}")
                    return False
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"[OK] MODIFY SUCCESS: Ticket={ticket} Symbol={symbol} | "
                           f"SL: {new_sl:.5f} (old: {current_sl:.5f}) | "
                           f"TP: {new_tp:.5f} | "
                           f"Result: retcode={result.retcode}, deal={result.deal}, order={result.order}, "
                           f"volume={result.volume if hasattr(result, 'volume') else 'N/A'}, "
                           f"price={result.price if hasattr(result, 'price') else 'N/A'}, "
                           f"comment={result.comment if hasattr(result, 'comment') else 'N/A'}")
                
                # CRITICAL FIX: Verify SL was actually applied (MT5 broker bug - reports success but SL unchanged)
                # FIX: Add SL verification similar to TP verification
                if new_sl > 0:
                    # Wait for broker to process (shorter delay for SL verification)
                    time.sleep(0.2)  # 200ms delay for SL processing
                    verify_position = self._get_position_by_ticket(ticket)
                    if verify_position:
                        applied_sl = verify_position.sl if hasattr(verify_position, 'sl') else verify_position.get('sl', 0.0)
                        point = symbol_info.get('point', 0.00001)
                        sl_tolerance = point * 10  # 1 pip tolerance
                        if abs(applied_sl - new_sl) > sl_tolerance:
                            # MT5 reported success but SL not applied - broker bug
                            logger.warning(f"[SL_VERIFY_FAIL] Ticket {ticket} | MT5 success but SL not applied | "
                                         f"Expected: {new_sl:.5f} | Applied: {applied_sl:.5f} | "
                                         f"Diff: {abs(applied_sl - new_sl):.5f}")
                            # Return False to trigger retry
                            if attempt < max_retries - 1:
                                logger.warning(f"Retrying SL modification (attempt {attempt + 1}/{max_retries})...")
                                time.sleep(0.2 * (attempt + 1))  # Exponential backoff
                                continue
                            else:
                                logger.error(f"[SL_VERIFY_FAIL] Ticket {ticket} | SL verification failed after {max_retries} attempts")
                                return False  # Return False so caller knows SL wasn't set
                        else:
                            logger.info(f"[SL_VERIFIED] Ticket {ticket} | SL verified: {applied_sl:.5f} (expected: {new_sl:.5f})")
                
                # CRITICAL FIX: Verify TP was actually applied (MT5 broker bug - reports success but TP=0)
                if take_profit_price is not None and new_tp > 0:
                    # Wait for broker to process (longer delay for TP verification)
                    time.sleep(0.3)  # 300ms delay for TP processing
                    verify_position = self._get_position_by_ticket(ticket)
                    if verify_position:
                        applied_tp = verify_position.tp if hasattr(verify_position, 'tp') else verify_position.get('tp', 0.0)
                        point = symbol_info.get('point', 0.00001)
                        tp_tolerance = point * 10  # 1 pip tolerance
                        if abs(applied_tp - new_tp) > tp_tolerance:
                            # MT5 reported success but TP not applied - broker bug
                            logger.warning(f"[TP_VERIFY_FAIL] Ticket {ticket} | MT5 success but TP not applied | "
                                         f"Expected: {new_tp:.5f} | Applied: {applied_tp:.5f} | "
                                         f"Diff: {abs(applied_tp - new_tp):.5f}")
                            
                            # CRITICAL FIX: Retry with adjusted TP if too close to market
                            # Get fresh tick data for retry (market may have moved)
                            retry_tick = self.mt5_connector.get_tick(symbol)
                            if retry_tick:
                                retry_ask = retry_tick.ask
                                retry_bid = retry_tick.bid
                                
                                # Check if TP is too close and adjust before retry
                                if self._is_buy_position(position):
                                    if freeze_level > 0:
                                        min_allowed_tp = retry_ask + (freeze_level * point)
                                    elif stops_level > 0:
                                        min_allowed_tp = retry_ask + (stops_level * point)
                                    else:
                                        min_allowed_tp = retry_ask + (point * 0.1)
                                    
                                    if new_tp <= min_allowed_tp:
                                        # Adjust TP to minimum allowed
                                        new_tp = min_allowed_tp
                                        digits = symbol_info.get('digits', 5)
                                        new_tp = round(new_tp / point) * point
                                        new_tp = round(new_tp, digits)
                                        logger.info(f"[TP_RETRY_ADJUSTED] TP adjusted for retry: {new_tp:.5f}")
                                        # Update request with adjusted TP for retry
                                        request["tp"] = new_tp
                                else:  # SELL
                                    if freeze_level > 0:
                                        min_allowed_tp = retry_bid - (freeze_level * point)
                                    elif stops_level > 0:
                                        min_allowed_tp = retry_bid - (stops_level * point)
                                    else:
                                        min_allowed_tp = retry_bid - (point * 0.1)
                                    
                                    if new_tp >= min_allowed_tp:
                                        # Adjust TP to minimum allowed
                                        new_tp = min_allowed_tp
                                        digits = symbol_info.get('digits', 5)
                                        new_tp = round(new_tp / point) * point
                                        new_tp = round(new_tp, digits)
                                        logger.info(f"[TP_RETRY_ADJUSTED] TP adjusted for retry: {new_tp:.5f}")
                                        # Update request with adjusted TP for retry
                                        request["tp"] = new_tp
                            
                            # Return False to trigger retry
                            if attempt < max_retries - 1:
                                logger.warning(f"Retrying TP modification (attempt {attempt + 1}/{max_retries})...")
                                time.sleep(0.2 * (attempt + 1))  # Exponential backoff
                                continue
                            else:
                                logger.error(f"[TP_VERIFY_FAIL] Ticket {ticket} | TP verification failed after {max_retries} attempts")
                                return False  # Return False so caller knows TP wasn't set
                        else:
                            logger.info(f"[TP_VERIFIED] Ticket {ticket} | TP verified: {applied_tp:.5f} (expected: {new_tp:.5f})")
                # Invalidate position cache to force refresh on next fetch
                with self._position_cache_lock:
                    self._position_cache.pop(ticket, None)
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
                verify_position = self._get_position_by_ticket(ticket)
                if verify_position is None:
                    logger.warning(f"Position {ticket} no longer exists (may have been closed) - skipping modification")
                    return False
                # Position still exists, but too close to market - retry with backoff
                if attempt < max_retries - 1:
                    logger.warning(f"Modify order failed for ticket {ticket} (attempt {attempt + 1}/{max_retries}): {result.retcode} - Position too close to market. Retrying...")
                    backoff = 0.2 * (2 ** attempt)  # Exponential: 0.2s, 0.4s, 0.8s
                    jitter = random.uniform(0, 0.1)  # Random jitter up to 100ms
                    time.sleep(backoff + jitter)
                    continue
                else:
                    logger.error(f"Modify order failed for ticket {ticket} after {max_retries} attempts: {result.retcode} - Position too close to market")
                    return False
            
            if result.retcode == 10013:  # Invalid request
                # Verify position state before retrying
                verify_position = self._get_position_by_ticket(ticket)
                if verify_position is None:
                    logger.warning(f"Position {ticket} no longer exists (may have been closed) - invalid request")
                    return False
                    # Check if SL/TP values are within valid range
                    symbol_info = self.mt5_connector.get_symbol_info(symbol)
                    if symbol_info and verify_position:
                        freeze_level = symbol_info.get('freeze_level', 0)
                        point = symbol_info.get('point', 0.00001)
                        current_price = verify_position.price_current if hasattr(verify_position, 'price_current') else None
                    # Check if SL is too close to current price (within freeze level)
                    if abs(new_sl - current_price) < (freeze_level * point):
                        logger.warning(f"SL {new_sl:.5f} too close to current price {current_price:.5f} (freeze level: {freeze_level * point:.5f})")
                        return False
                # Invalid request for other reason - retry once
                if attempt < max_retries - 1:
                    logger.warning(f"Modify order failed for ticket {ticket} (attempt {attempt + 1}/{max_retries}): {result.retcode} - Invalid request. Retrying...")
                    time.sleep(0.1 * (attempt + 1))
                    continue
                else:
                    logger.error(f"Modify order failed for ticket {ticket} after {max_retries} attempts: {result.retcode} - Invalid request")
                    return False
            
            if attempt < max_retries - 1:
                logger.warning(f"Modify order failed for ticket {ticket} (attempt {attempt + 1}/{max_retries}): {result.retcode} - {result.comment}. Retrying...")
                backoff = 0.1 * (2 ** attempt)  # Exponential: 0.1s, 0.2s, 0.4s
                jitter = random.uniform(0, 0.05)  # Random jitter up to 50ms
                time.sleep(backoff + jitter)
            else:
                logger.error(f"[ERROR] SL UPDATE FAILED: Ticket={ticket} Symbol={symbol} Code={result.retcode} Reason={result.comment} Attempts={max_retries}")
                logger.error(f"Modify order failed for ticket {ticket} after {max_retries} attempts: {result.retcode} - {result.comment}")
                # FALLBACK: Try direct MT5 API call as last resort
                logger.warning(f"[FALLBACK] Attempting direct MT5 API call for ticket {ticket}")
                try:
                    direct_request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": symbol,
                        "position": ticket,
                        "sl": new_sl,
                        "tp": new_tp,
                    }
                    direct_result = mt5.order_send(direct_request)
                    if direct_result and direct_result.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"[FALLBACK SUCCESS] Direct MT5 API call succeeded for ticket {ticket}")
                        # Invalidate cache
                        with self._position_cache_lock:
                            self._position_cache.pop(ticket, None)
                        return True
                    else:
                        logger.error(f"[FALLBACK FAILED] Direct MT5 API call failed for ticket {ticket}: {direct_result.retcode if direct_result else 'None'}")
                except Exception as e:
                    logger.error(f"[FALLBACK EXCEPTION] Direct MT5 API call exception for ticket {ticket}: {e}", exc_info=True)
                return False
        
        # Final fallback: if we exhausted all retries, try direct MT5 API one more time
        logger.warning(f"[FINAL FALLBACK] All retries exhausted, attempting direct MT5 API call for ticket {ticket}")
        try:
            direct_request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "position": ticket,
                "sl": new_sl,
                "tp": new_tp,
            }
            direct_result = mt5.order_send(direct_request)
            if direct_result and direct_result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"[FINAL FALLBACK SUCCESS] Direct MT5 API call succeeded for ticket {ticket}")
                # Invalidate cache
                with self._position_cache_lock:
                    self._position_cache.pop(ticket, None)
                return True
            else:
                logger.error(f"[FINAL FALLBACK FAILED] Direct MT5 API call failed for ticket {ticket}: {direct_result.retcode if direct_result else 'None'}")
        except Exception as e:
            logger.error(f"[FINAL FALLBACK EXCEPTION] Direct MT5 API call exception for ticket {ticket}: {e}", exc_info=True)
        
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
        
        position = self._get_position_by_ticket(ticket)
        if position is None:
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
        
        # CRITICAL FIX: Handle both tuple/list and single position object
        # mt5.position_get() may return a tuple, while other methods return a single object
        if isinstance(position, (tuple, list)) and len(position) > 0:
            position = position[0]
        
        # CRITICAL FIX: Check if position is a valid position object (not an integer or other invalid type)
        # This prevents 'int' object has no attribute 'symbol' errors
        if isinstance(position, (int, float, str)):
            logger.error(f"Position {ticket} returned invalid type: {type(position)} (got {position}). Expected position object.")
            tracer.trace(
                function_name="OrderManager.close_position",
                expected=f"Close position Ticket {ticket}",
                actual=f"Position returned invalid type: {type(position)}",
                status="ERROR",
                ticket=ticket,
                reason="Invalid position type returned"
            )
            return False
        
        # Check if position has required attributes
        if not hasattr(position, 'symbol'):
            # If position doesn't have 'symbol' attribute, it's not a valid position object
            logger.error(f"Position {ticket} returned invalid format: {type(position)} (missing 'symbol' attribute)")
            tracer.trace(
                function_name="OrderManager.close_position",
                expected=f"Close position Ticket {ticket}",
                actual=f"Position missing 'symbol' attribute: {type(position)}",
                status="ERROR",
                ticket=ticket,
                reason="Position object missing required attributes"
            )
            return False
        
        symbol = position.symbol
        profit = position.profit
        ticket = position.ticket
        
        # CRITICAL: Log trade closure with comprehensive details
        # Get entry price and SL for logging
        entry_price = position.price_open
        sl_price = position.sl
        tp_price = position.tp if hasattr(position, 'tp') else 0.0
        volume = position.volume
        
        logger.info(f"[TRADE_CLOSE_REQUEST] Ticket {ticket} | Symbol {symbol} | "
                   f"Entry: {entry_price:.5f} | SL: {sl_price:.5f} | TP: {tp_price:.5f} | "
                   f"Volume: {volume} | Current Profit: ${profit:.2f} | Comment: {comment}")
        
        # CRITICAL: Validate loss threshold before closing
        # If profit is negative but better than -$2.00, log warning (but allow close - may be manual/other reason)
        if profit < 0 and profit > -2.0:
            logger.warning(f"[EARLY_CLOSURE_WARNING] Ticket {ticket} | Symbol {symbol} | "
                          f"Trade closing at ${profit:.2f} (better than configured -$2.00 threshold) | "
                          f"This may indicate: manual close, broker closure, or SL calculation issue | "
                          f"Comment: {comment}")
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"[TRADE_CLOSE_ERROR] Ticket {ticket} | Symbol {symbol} | Cannot get symbol info")
            return False
        
        # Determine close price and type
        if position.type == mt5.ORDER_TYPE_BUY:
            price = symbol_info['bid']
            order_type = mt5.ORDER_TYPE_SELL
        else:
            price = symbol_info['ask']
            order_type = mt5.ORDER_TYPE_BUY
        
        # Determine filling type
        # Use same logic as place_order() to support SIM_LIVE mode
        filling_type = None
        filling_modes = None
        
        # Try to get filling_mode from connector's symbol_info dict first (works for SIM_LIVE)
        if hasattr(self.mt5_connector, 'get_symbol_info'):
            symbol_info_dict = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
            if symbol_info_dict and isinstance(symbol_info_dict, dict):
                filling_modes = symbol_info_dict.get('filling_mode')
        
        # Fallback to mt5.symbol_info() for live mode
        if filling_modes is None:
            symbol_info_obj = mt5.symbol_info(symbol)
            if symbol_info_obj is not None:
                filling_modes = symbol_info_obj.filling_mode if hasattr(symbol_info_obj, 'filling_mode') else None
        
        # Determine filling type from bitmask
        if filling_modes is not None:
            if filling_modes & 1:
                filling_type = mt5.ORDER_FILLING_FOK
            elif filling_modes & 2:
                filling_type = mt5.ORDER_FILLING_IOC
            elif filling_modes & 4:
                filling_type = mt5.ORDER_FILLING_RETURN
        
        # Default to RETURN if still None (for SIM_LIVE or compatibility)
        if filling_type is None:
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
            logger.error(f"[TRADE_CLOSE_FAILED] Ticket {ticket} | Symbol {symbol} | "
                        f"MT5 retcode: {result.retcode} | Comment: {result.comment} | "
                        f"Floating profit at close attempt: ${profit:.2f}")
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
        
        # CRITICAL: Log successful close with comprehensive details
        # Get deal history to get actual realized profit
        deal_info = self.get_deal_history(ticket)
        realized_profit = profit  # Default to floating profit if deal history unavailable
        if deal_info and deal_info.get('exit_deal'):
            realized_profit = deal_info.get('total_profit', profit)
        
        logger.info(f"[TRADE_CLOSED] Ticket {ticket} | Symbol {symbol} | "
                   f"Entry: {entry_price:.5f} | SL: {sl_price:.5f} | "
                   f"Floating Profit: ${profit:.2f} | Realized Profit: ${realized_profit:.2f} | "
                   f"Comment: {comment} | Execution Time: {execution_time_ms:.0f}ms")
        
        # CRITICAL: Validate loss threshold - log warning if closing before -$2.00
        if realized_profit < 0 and realized_profit > -2.0:
            logger.warning(f"[EARLY_CLOSURE_DETECTED] Ticket {ticket} | Symbol {symbol} | "
                          f"Trade closed at ${realized_profit:.2f} (better than configured -$2.00 threshold) | "
                          f"Expected loss threshold: -$2.00 | "
                          f"Possible causes: Manual close, broker closure, SL calculation error, or slippage | "
                          f"Comment: {comment}")
        
        # Validate if loss is close to -$2.00 (within tolerance)
        if abs(realized_profit + 2.0) <= 0.10:
            logger.info(f"[STOP_LOSS_HIT] Ticket {ticket} | Symbol {symbol} | "
                       f"Trade closed at ${realized_profit:.2f} (within $0.10 of configured -$2.00 stop loss) | "
                       f"Stop loss threshold correctly enforced")
        
        # Clear position cache for closed position
        with self._position_cache_lock:
            self._position_cache.pop(ticket, None)
        
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
    
    def close_position_partial(self, ticket: int, close_percent: float = 0.5) -> bool:
        """
        Close partial position (Phase 5 feature).
        
        Args:
            ticket: Position ticket number
            close_percent: Percentage of position to close (0.0 to 1.0)
            
        Returns:
            True if successful, False otherwise
        """
        # Check master kill switch (governance - Phase 5)
        if self._is_feature_disabled_by_kill_switch('partial_profit_taking'):
            logger.warning(f"[FEATURE_DISABLED] Partial close disabled by master kill switch for ticket {ticket}")
            return False
        
        # Get position
        position = self.get_position_by_ticket(ticket)
        if not position:
            logger.error(f"[PARTIAL_CLOSE_FAILED] Ticket {ticket} not found")
            return False
        
        current_volume = position.volume
        close_volume = current_volume * close_percent
        
        # Round to lot size
        symbol_info = self.mt5_connector.get_symbol_info(position.symbol)
        if not symbol_info:
            logger.error(f"[PARTIAL_CLOSE_FAILED] Cannot get symbol info for {position.symbol}")
            return False
        
        min_lot = symbol_info.get('volume_min', 0.01)
        close_volume = round(close_volume / min_lot) * min_lot
        
        if close_volume < min_lot:
            logger.warning(f"[PARTIAL_CLOSE_FAILED] Close volume {close_volume:.4f} < min lot {min_lot:.4f}")
            return False
        
        if close_volume >= current_volume:
            # Would close entire position, use regular close instead
            return self.close_position(ticket, comment="Partial close (full)")
        
        # Close partial position
        # Note: MT5 doesn't support partial closes directly - this would need to be implemented
        # For now, log that it's disabled
        logger.warning(f"[PARTIAL_CLOSE] Partial close not yet implemented - would close {close_volume:.4f} of {current_volume:.4f} for ticket {ticket}")
        return False
    
    def _is_feature_disabled_by_kill_switch(self, feature_name: str) -> bool:
        """Check if feature is disabled by master kill switch (governance)."""
        try:
            if self._trading_bot:
                return not self._trading_bot.is_feature_enabled(feature_name)
            # Fallback: check config directly
            if hasattr(self.mt5_connector, 'config'):
                governance = self.mt5_connector.config.get('governance', {})
                kill_switch = governance.get('master_kill_switch', {})
                if kill_switch.get('enabled', False):
                    disabled_features = kill_switch.get('disable_features', [])
                    return feature_name in disabled_features
        except Exception as e:
            logger.warning(f"Failed to check kill switch for {feature_name}: {e}")
        return False
    
    def set_trading_bot(self, trading_bot):
        """Set trading bot reference for governance checks."""
        self._trading_bot = trading_bot
    
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
            
            # CRITICAL FIX: Handle both string type ('BUY'/'SELL') from SIM_LIVE and integer type (0/1) from live MT5
            pos_type = pos.type
            if isinstance(pos_type, str):
                # Already a string (SIM_LIVE)
                type_str = pos_type
            elif isinstance(pos_type, int):
                # Integer type (live MT5) - convert to string
                type_str = 'BUY' if pos_type == mt5.ORDER_TYPE_BUY else 'SELL'
            else:
                # Fallback: try to get string representation
                type_str = 'BUY' if pos_type == mt5.ORDER_TYPE_BUY else 'SELL'
            
            result.append({
                'ticket': pos.ticket,
                'symbol': pos.symbol,
                'type': type_str,
                'volume': pos.volume,
                'price_open': pos.price_open,
                'price_current': pos.price_current,
                'sl': pos.sl,
                'tp': pos.tp,
                'profit': pos.profit,
                'swap': getattr(pos, 'swap', 0.0),  # Handle missing swap attribute (SIM_LIVE compatibility)
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
            
            # CRITICAL FIX: Only label as Micro-HFT if profit is POSITIVE
            # Micro-HFT only closes profitable trades, never losses
            if duration_minutes < 5 and total_profit > 0 and 0.01 <= total_profit <= 0.50:
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

