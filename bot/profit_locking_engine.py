"""
Intelligent Profit-Locking Engine
Performs micro-profit locking and step-based trailing locks on open positions.
This engine locks profits by adjusting stop-loss, ensuring gains are protected.
"""

import time
import threading
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from utils.logger_factory import get_logger

logger = get_logger("profit_locking", "logs/live/engine/profit_locking.log")


class ProfitLockingEngine:
    """
    Intelligent Profit-Locking Engine for automatic profit protection.
    
    This engine:
    - Locks profits in sweet spot ($0.03-$0.10) by moving stop-loss
    - Locks profits at step increments ($0.10, $0.20, $0.30, etc.) when profit exceeds $0.10
    - Respects broker constraints (freeze levels, min-distance)
    - Never worsens an existing stop-loss
    - Integrates with existing monitoring loop
    """
    
    def __init__(self, config: Dict[str, Any], order_manager, mt5_connector):
        """
        Initialize Profit-Locking Engine.
        
        Args:
            config: Configuration dictionary
            order_manager: OrderManager instance for modifying orders
            mt5_connector: MT5Connector instance for getting symbol info
        """
        self.config = config
        self.order_manager = order_manager
        self.mt5_connector = mt5_connector
        
        # Load configuration (profit_locking is under 'risk' section)
        risk_config = config.get('risk', {})
        lock_config = risk_config.get('profit_locking', {})
        self.enabled = lock_config.get('enabled', True)
        self.min_profit_threshold_usd = lock_config.get('min_profit_threshold_usd', 0.03)
        self.max_profit_threshold_usd = lock_config.get('max_profit_threshold_usd', 0.10)
        self.lock_step_usd = lock_config.get('lock_step_usd', 0.10)
        self.lock_tolerance_usd = lock_config.get('lock_tolerance_usd', 0.02)
        self.max_lock_retries = lock_config.get('max_lock_retries', 3)
        self.lock_retry_delay_ms = lock_config.get('lock_retry_delay_ms', 100)
        
        # Track locked positions to prevent duplicate attempts
        self._locked_positions = {}  # {ticket: {'last_lock_profit': float, 'last_attempt': float, 'sweet_spot_entry_time': float, 'sl_verified': bool}}
        
        # Dynamic sweet spot tracking - tracks minimum profit reached in sweet spot
        self._sweet_spot_min_profit = {}  # {ticket: min_profit_reached_in_sweet_spot}
        
        # Thread-safety: Lock per ticket for SL updates to prevent race conditions
        self._sl_update_locks = {}  # {ticket: threading.Lock()}
        self._sl_locks_lock = threading.Lock()  # Lock for managing ticket locks
        
        # Dynamic sweet spot configuration
        dynamic_config = lock_config.get('dynamic_sweet_spot', {})
        self.dynamic_sweet_spot_enabled = dynamic_config.get('enabled', True)
        self.min_duration_seconds = dynamic_config.get('min_duration_seconds', 0.0)  # 0 = immediate
        self.apply_immediately = dynamic_config.get('apply_immediately', True)
        self.poll_interval_ms = dynamic_config.get('poll_interval_ms', 50)  # Sub-second polling
        self.sweet_spot_min_profit = dynamic_config.get('sweet_spot_min_profit', 0.03)
        self.sweet_spot_max_profit = dynamic_config.get('sweet_spot_max_profit', 0.10)
        self.retry_attempts_sweet_spot = dynamic_config.get('retry_attempts', 3)
        self.retry_backoff_ms = dynamic_config.get('retry_backoff_ms', [50, 100, 200])  # Increasing backoff
        self.track_minimum_profit = dynamic_config.get('track_minimum_profit', True)
        self.never_decrease_sl = dynamic_config.get('never_decrease_sl', True)
        
        if self.enabled:
            logger.info(f"Profit-Locking Engine initialized | Sweet spot: ${self.min_profit_threshold_usd}-${self.max_profit_threshold_usd} | Step: ${self.lock_step_usd}")
            if self.dynamic_sweet_spot_enabled:
                logger.info(f"Dynamic Sweet Spot Locking: Enabled | Poll interval: {self.poll_interval_ms}ms | Track minimum: {self.track_minimum_profit} | Apply immediately: {self.apply_immediately}")
    
    def check_and_lock_profit(self, position: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if position should have profit locked and attempt to lock it.
        
        ENHANCED: Immediate sweet-spot profit-locking with dynamic updates.
        - Applies SL immediately when trade enters sweet spot (no duration delay)
        - Tracks minimum profit reached to prevent SL decrease
        - Updates SL dynamically as profit moves higher within sweet spot
        
        Args:
            position: Position dictionary from order_manager (MUST be fresh MT5 data)
        
        Returns:
            (success: bool, reason: str)
        """
        ticket = position.get('ticket')
        symbol = position.get('symbol', '')
        current_profit = position.get('profit', 0.0)
        
        # MANDATORY LOGGING: LOCK_ATTEMPT - Log every attempt with all decision variables
        logger.info(f"[LOCK_ATTEMPT] Profit-Locking Engine | Ticket={ticket} Symbol={symbol} "
                   f"Profit=${current_profit:.2f} Enabled={self.enabled}")
        
        if not self.enabled:
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Reason=Engine disabled")
            return False, "Engine disabled"
        
        if not ticket:
            logger.warning(f"[LOCK_BLOCKED] Ticket=None Symbol={symbol} Reason=No ticket")
            return False, "No ticket"
        
        entry_price = position.get('price_open', 0.0)
        current_sl = position.get('sl', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        
        # CRITICAL: Get FRESH profit from position (from MT5, not cached)
        current_profit = position.get('profit', 0.0)
        
        # Check if profit is in sweet spot range (use configured values)
        sweet_spot_min = self.sweet_spot_min_profit if hasattr(self, 'sweet_spot_min_profit') else self.min_profit_threshold_usd
        sweet_spot_max = self.sweet_spot_max_profit if hasattr(self, 'sweet_spot_max_profit') else self.max_profit_threshold_usd
        
        # CRITICAL: Don't lock if profit is negative or too low
        # If P/L is in loss zone, profit-locking should NEVER run - strict SL enforcement handles losses
        if current_profit < 0:
            # Trade is in loss - profit-locking should not run
            # Reset sweet spot tracking if profit goes negative
            if ticket in self._sweet_spot_min_profit:
                self._sweet_spot_min_profit.pop(ticket)
            reason = f"Trade in loss zone (${current_profit:.2f}) - profit-locking blocked, strict SL enforcement active"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Profit=${current_profit:.2f} Reason={reason}")
            return False, reason
        
        if current_profit < sweet_spot_min:
            # Reset sweet spot tracking if profit goes below threshold
            if ticket in self._sweet_spot_min_profit:
                self._sweet_spot_min_profit.pop(ticket)
            reason = f"Profit ${current_profit:.2f} below threshold ${sweet_spot_min}"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Profit=${current_profit:.2f} Reason={reason}")
            return False, reason
        
        if not symbol or entry_price <= 0 or lot_size <= 0:
            reason = "Invalid position data"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Reason={reason}")
            return False, reason
        
        # Get symbol info for calculations
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            reason = "Symbol info not available"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Reason={reason}")
            return False, reason
        
        # CRITICAL: Calculate current locked profit from ACTUAL broker SL (not estimated)
        current_locked_profit = self._calculate_locked_profit(
            entry_price, current_sl, order_type, lot_size, symbol_info
        )
        
        # Dynamic Sweet Spot Tracking: Track minimum profit reached in sweet spot
        # This must happen BEFORE calculating target lock to ensure minimum is tracked
        if self.dynamic_sweet_spot_enabled and self.track_minimum_profit:
            if sweet_spot_min <= current_profit <= sweet_spot_max:
                # Trade is in sweet spot - track minimum profit reached
                symbol = position.get('symbol', '')  # Get symbol before logging
                if ticket not in self._sweet_spot_min_profit:
                    # First time entering sweet spot - initialize with current profit
                    self._sweet_spot_min_profit[ticket] = current_profit
                    logger.info(f"🎯 SWEET SPOT ENTRY: {symbol} Ticket {ticket} | "
                              f"Entered sweet spot at ${current_profit:.2f} | "
                              f"Applying IMMEDIATE SL lock to prevent negative P/L")
                else:
                    # Update minimum if current profit is lower
                    if current_profit < self._sweet_spot_min_profit[ticket]:
                        self._sweet_spot_min_profit[ticket] = current_profit
                        logger.debug(f"📉 Sweet Spot Min Updated: {symbol} Ticket {ticket} | "
                                   f"New minimum: ${current_profit:.2f}")
            elif current_profit > self.max_profit_threshold_usd:
                # Profit exceeded sweet spot - keep tracking but don't update
                # The minimum profit in sweet spot is preserved
                pass
        
        # CRITICAL: Check if this is a sweet spot lock BEFORE using the variable
        # This must be defined early so it can be used in logging
        is_sweet_spot_lock = (sweet_spot_min <= current_profit <= sweet_spot_max)
        
        # Determine target lock profit (pass ticket for dynamic sweet spot tracking)
        target_lock_profit = self._calculate_target_lock_profit(current_profit, current_locked_profit, ticket)
        
        if target_lock_profit is None:
            # Log skipped update with reason
            reason = "No lock needed (target lock calculation returned None)"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Profit=${current_profit:.2f} "
                         f"CurrentLocked=${current_locked_profit:.2f} Reason={reason}")
            return False, reason
        
        # Ensure target lock is an improvement (at least $0.01 better)
        if target_lock_profit <= current_locked_profit + 0.01:
            # Log skipped update with reason
            reason = f"Target lock ${target_lock_profit:.2f} not better than current ${current_locked_profit:.2f} (difference: ${target_lock_profit - current_locked_profit:.2f})"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Profit=${current_profit:.2f} "
                         f"TargetLock=${target_lock_profit:.2f} CurrentLocked=${current_locked_profit:.2f} Reason={reason}")
            return False, reason
        
        # CRITICAL: For sweet spot, apply immediately (min_duration = 0)
        # Skip rate limiting for immediate sweet spot locks
        now = time.time()
        # Note: is_sweet_spot_lock is already defined above
        
        if ticket in self._locked_positions:
            last_attempt = self._locked_positions[ticket].get('last_attempt', 0)
            last_lock_profit = self._locked_positions[ticket].get('last_lock_profit', 0.0)
            
            # For sweet spot locks: Apply immediately (no duration delay, no rate limiting)
            # Only skip if we just attempted with the same target lock (within 100ms)
            if is_sweet_spot_lock:
                # Immediate application: Skip only if we just attempted with same target (within 100ms)
                if now - last_attempt < 0.1 and abs(last_lock_profit - target_lock_profit) < 0.01:
                    reason = "Recently attempted (within 100ms)"
                    logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Reason={reason}")
                    return False, reason
                # No duration check for immediate sweet spot locks
            else:
                # Non-sweet spot locks: Use normal rate limiting
                if now - last_attempt < (self.lock_retry_delay_ms / 1000.0) and abs(last_lock_profit - target_lock_profit) < 0.01:
                    reason = "Recently attempted (rate limited)"
                    logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Reason={reason}")
                    return False, reason
        
        # CRITICAL: Calculate target SL price from target lock profit
        target_sl_price = self._calculate_target_sl_price(
            entry_price, target_lock_profit, order_type, lot_size, symbol_info
        )
        
        if target_sl_price is None:
            reason = "Cannot calculate target SL price"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Reason={reason}")
            return False, reason
        
        # Adjust target SL to respect broker constraints (freeze level, min distance)
        target_sl_price = self._adjust_sl_for_broker_constraints(
            target_sl_price, current_sl, order_type, symbol_info
        )
        
        if target_sl_price is None:
            reason = "Cannot adjust SL for broker constraints"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Reason={reason}")
            return False, reason
        
        # Validate target SL doesn't worsen current SL (after broker adjustments)
        if not self._validate_sl_improvement(current_sl, target_sl_price, order_type, entry_price, symbol_info):
            reason = "Target SL would worsen current SL"
            logger.warning(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} OldSL={current_sl:.5f} NewSL={target_sl_price:.5f} Reason={reason}")
            return False, reason
        
        # CRITICAL: Attempt to lock profit with enhanced retry logic
        # This ensures SL is applied immediately to broker (MT5)
        success = self._apply_profit_lock(
            ticket, symbol, target_sl_price, target_lock_profit, current_profit
        )
        
        if success:
            # Update tracking
            if ticket not in self._locked_positions:
                self._locked_positions[ticket] = {}
            self._locked_positions[ticket]['last_lock_profit'] = target_lock_profit
            self._locked_positions[ticket]['last_attempt'] = now
            
            # Track sweet spot entry time for duration checking
            if is_sweet_spot_lock and 'sweet_spot_entry_time' not in self._locked_positions[ticket]:
                self._locked_positions[ticket]['sweet_spot_entry_time'] = now
            
            # MANDATORY LOGGING: LOCK_SUCCESS - Log successful lock application
            if is_sweet_spot_lock:
                min_profit_info = ""
                if self.dynamic_sweet_spot_enabled and ticket in self._sweet_spot_min_profit:
                    min_profit_info = f" | Min in sweet spot: ${self._sweet_spot_min_profit[ticket]:.2f}"
                verification_status = "[OK] VERIFIED" if self._locked_positions[ticket].get('sl_verified', False) else "[W] PENDING"
                logger.info(f"[LOCK_SUCCESS] Ticket={ticket} Symbol={symbol} Profit=${current_profit:.2f} "
                          f"LockedAt=${target_lock_profit:.2f} TargetSL={target_sl_price:.5f}{min_profit_info} "
                          f"Status={verification_status}")
            else:
                logger.info(f"[LOCK_SUCCESS] Ticket={ticket} Symbol={symbol} Profit=${current_profit:.2f} "
                          f"LockedAt=${target_lock_profit:.2f} TargetSL={target_sl_price:.5f}")
            return True, f"Locked at ${target_lock_profit:.2f}"
        else:
            reason = "Lock attempt failed - broker SL not updated"
            logger.error(f"[LOCK_BLOCKED] Ticket={ticket} Symbol={symbol} Profit=${current_profit:.2f} "
                        f"TargetLock=${target_lock_profit:.2f} TargetSL={target_sl_price:.5f} "
                        f"RetryCount={self.max_lock_retries} Reason={reason}")
            return False, reason
    
    def _calculate_target_lock_profit(self, current_profit: float, current_locked_profit: float, ticket: int = None) -> Optional[float]:
        """
        Calculate target lock profit based on current profit and existing lock.
        
        Enhanced with dynamic sweet spot locking that tracks minimum profit reached.
        
        Args:
            current_profit: Current profit in USD
            current_locked_profit: Currently locked profit in USD
            ticket: Position ticket (optional, for sweet spot tracking)
        
        Returns:
            Target lock profit in USD, or None if no lock needed
        """
        # Dynamic Sweet Spot Lock: Lock at current profit if in sweet spot, tracking minimum
        sweet_spot_min = self.sweet_spot_min_profit if hasattr(self, 'sweet_spot_min_profit') else self.min_profit_threshold_usd
        sweet_spot_max = self.sweet_spot_max_profit if hasattr(self, 'sweet_spot_max_profit') else self.max_profit_threshold_usd
        
        if sweet_spot_min <= current_profit <= sweet_spot_max:
            if self.dynamic_sweet_spot_enabled and ticket is not None and ticket in self._sweet_spot_min_profit:
                # Use minimum profit reached in sweet spot to ensure we never decrease SL
                min_profit_reached = self._sweet_spot_min_profit[ticket]
                
                # Dynamic Sweet Spot Locking: Lock at current profit as it moves higher
                # Target lock should lock at current profit (with tolerance), but never below minimum reached
                # This ensures SL updates dynamically as profit increases within sweet spot
                if self.never_decrease_sl:
                    # Never decrease: target should be max(current profit - tolerance, minimum reached, current locked)
                    # This locks at current profit when it's higher, but never drops below minimum or current lock
                    target = max(current_profit - self.lock_tolerance_usd, min_profit_reached, current_locked_profit)
                else:
                    # Allow dynamic updates: lock at current profit (with tolerance)
                    target = current_profit - self.lock_tolerance_usd
                    # Still ensure we don't drop below minimum reached
                    target = max(target, min_profit_reached)
                
                # Only lock if it's an improvement over current lock (by at least $0.01)
                if target > current_locked_profit + 0.01:
                    # Don't lock more than current profit (safety check)
                    return min(target, current_profit)
                return None
            else:
                # Standard sweet spot lock (no dynamic tracking)
                target = current_profit - self.lock_tolerance_usd
                # Only lock if it's an improvement over current lock
                if target > current_locked_profit + 0.01:
                    return min(target, current_profit)  # Don't lock more than current profit
                return None
        
        # Step-based lock: lock at step increments when profit > sweet spot
        if current_profit > self.max_profit_threshold_usd:
            # Calculate which step we should lock at
            # Example: profit $0.44 -> lock at $0.30 (one step below), profit $0.30 -> lock at $0.20
            step_level = int(current_profit / self.lock_step_usd)
            
            # Lock one step below current profit level
            # If profit is $0.40 (level 4), lock at $0.30 (level 3)
            # If profit is $0.30 (level 3), lock at $0.20 (level 2)
            if step_level >= 1:
                target_lock = (step_level - 1) * self.lock_step_usd
            else:
                target_lock = 0.0
            
            # Ensure minimum lock is at least $0.10 if profit exceeded it
            if current_profit >= self.max_profit_threshold_usd and target_lock < self.max_profit_threshold_usd:
                target_lock = self.max_profit_threshold_usd
            
            # Ensure we're improving the lock
            if target_lock > current_locked_profit + 0.01:
                return min(target_lock, current_profit - self.lock_tolerance_usd)
        
        return None
    
    def _calculate_locked_profit(self, entry_price: float, sl_price: float, order_type: str, 
                                 lot_size: float, symbol_info: Dict[str, Any]) -> float:
        """
        Calculate how much profit is currently locked by the stop-loss.
        
        Args:
            entry_price: Entry price
            sl_price: Current stop-loss price
            order_type: 'BUY' or 'SELL'
            lot_size: Lot size
            symbol_info: Symbol information dictionary
        
        Returns:
            Locked profit in USD (0.0 if no lock or loss)
        """
        if sl_price <= 0:
            return 0.0
        
        contract_size = symbol_info.get('contract_size', 1.0)
        
        # Calculate locked profit in USD
        # CRITICAL FIX: Properly handle BUY vs SELL order types
        if order_type == 'BUY':
            # For BUY: 
            # - SL below entry = loss (not locked profit)
            # - SL at/above entry = profit locked
            #   Profit = -(entry_price - sl_price) * lot * contract
            #   If sl_price >= entry_price, result is positive (profit locked)
            locked_profit = -(entry_price - sl_price) * lot_size * contract_size
        else:  # SELL
            # For SELL:
            # - SL above entry = loss (not locked profit)
            # - SL at/below entry = profit locked
            #   Profit = -(sl_price - entry_price) * lot * contract
            #   If sl_price <= entry_price, result is positive (profit locked)
            locked_profit = -(sl_price - entry_price) * lot_size * contract_size
        
        # Return only positive values (profit locked), 0 for loss or break-even
        return max(0.0, locked_profit)
    
    def _calculate_target_sl_price(self, entry_price: float, target_lock_profit: float, 
                                   order_type: str, lot_size: float, 
                                   symbol_info: Dict[str, Any]) -> Optional[float]:
        """
        Calculate target stop-loss price from target lock profit.
        
        Args:
            entry_price: Entry price
            target_lock_profit: Target locked profit in USD
            order_type: 'BUY' or 'SELL'
            lot_size: Lot size
            symbol_info: Symbol information dictionary
        
        Returns:
            Target SL price, or None if calculation fails
        """
        if target_lock_profit <= 0:
            return None
        
        contract_size = symbol_info.get('contract_size', 1.0)
        
        if contract_size <= 0 or lot_size <= 0:
            return None
        
        # Calculate price difference needed for target lock profit
        price_diff = target_lock_profit / (lot_size * contract_size)
        
        if price_diff <= 0:
            return None
        
        # Calculate target SL price based on order type
        if order_type == 'BUY':
            # For BUY: SL above entry locks profit (SL moves up as profit increases)
            # To lock $X profit, SL should be entry_price + price_diff
            target_sl = entry_price + price_diff
        else:  # SELL
            # For SELL: SL BELOW entry locks profit (SL moves down as profit increases)
            # To lock $X profit, SL should be entry_price - price_diff
            # This matches _calculate_locked_profit logic: if sl_price <= entry_price, profit is locked
            target_sl = entry_price - price_diff
        
        return target_sl
    
    def _adjust_sl_for_broker_constraints(self, target_sl: float, current_sl: float,
                                         order_type: str, symbol_info: Dict[str, Any]) -> Optional[float]:
        """
        Adjust target SL to respect broker constraints (freeze level, min distance).
        
        Args:
            target_sl: Calculated target stop-loss price
            current_sl: Current stop-loss price
            order_type: 'BUY' or 'SELL'
            symbol_info: Symbol information dictionary
        
        Returns:
            Adjusted SL price, or None if adjustment would worsen current SL
        """
        point = symbol_info.get('point', 0.00001)
        stops_level = symbol_info.get('trade_stops_level', 0)
        min_distance = stops_level * point if stops_level > 0 else 0
        
        # Get current price for min distance check
        if order_type == 'BUY':
            current_price = symbol_info.get('bid', 0)
        else:  # SELL
            current_price = symbol_info.get('ask', 0)
        
        if current_price <= 0:
            return target_sl  # Can't adjust without current price
        
        # Adjust for min distance constraint
        if min_distance > 0:
            if order_type == 'BUY':
                # For BUY: SL must be at least min_distance below current price
                min_allowed_sl = current_price - min_distance
                if target_sl > min_allowed_sl:
                    # Adjust to minimum allowed
                    target_sl = min_allowed_sl
                    # If this makes it worse than current SL, return None
                    if current_sl > 0 and target_sl < current_sl:
                        return None
            else:  # SELL
                # For SELL: SL must be at least min_distance above current price
                max_allowed_sl = current_price + min_distance
                if target_sl < max_allowed_sl:
                    # Adjust to minimum allowed
                    target_sl = max_allowed_sl
                    # If this makes it worse than current SL, return None
                    if current_sl > 0 and target_sl > current_sl:
                        return None
        
        return target_sl
    
    def _validate_sl_improvement(self, current_sl: float, target_sl: float, order_type: str,
                                entry_price: float, symbol_info: Dict[str, Any]) -> bool:
        """
        Validate that target SL is an improvement over current SL.
        
        Args:
            current_sl: Current stop-loss price
            target_sl: Target stop-loss price
            order_type: 'BUY' or 'SELL'
            entry_price: Entry price
            symbol_info: Symbol information dictionary
        
        Returns:
            True if target SL is an improvement, False otherwise
        """
        if current_sl <= 0:
            # No current SL, any positive lock is improvement
            return True
        
        # Get freeze level and min distance
        point = symbol_info.get('point', 0.00001)
        stops_level = symbol_info.get('trade_stops_level', 0)
        min_distance = stops_level * point if stops_level > 0 else 0
        
        if order_type == 'BUY':
            # For BUY: Higher SL = better (closer to entry = more profit locked)
            # Target SL must be >= current SL (not worse)
            if target_sl < current_sl:
                return False
            
            # Ensure target SL respects min distance from current price
            current_price = symbol_info.get('bid', entry_price)
            if current_price - target_sl < min_distance and min_distance > 0:
                # Adjust target SL to respect min distance
                target_sl = current_price - min_distance
                if target_sl < current_sl:
                    return False
        else:  # SELL
            # For SELL: Lower SL = better (closer to entry = more profit locked)
            # Target SL must be <= current SL (not worse)
            if target_sl > current_sl:
                return False
            
            # Ensure target SL respects min distance from current price
            current_price = symbol_info.get('ask', entry_price)
            if target_sl - current_price < min_distance and min_distance > 0:
                # Adjust target SL to respect min distance
                target_sl = current_price + min_distance
                if target_sl > current_sl:
                    return False
        
        return True
    
    def _apply_profit_lock(self, ticket: int, symbol: str, target_sl_price: float,
                          target_lock_profit: float, current_profit: float) -> bool:
        """
        Apply profit lock by modifying stop-loss with enhanced retry logic.
        
        ENHANCED: Gets fresh position data before each retry and handles broker rejections.
        Ensures SL is applied immediately to broker (MT5) with proper error handling.
        
        Args:
            ticket: Position ticket
            symbol: Trading symbol
            target_sl_price: Target stop-loss price
            target_lock_profit: Target locked profit in USD
            current_profit: Current profit in USD (from fresh position data)
        
        Returns:
            True if lock was applied successfully to broker, False otherwise
        """
        # Get symbol info for final validation
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            logger.warning(f"[WARNING] Cannot get symbol info for {symbol} Ticket {ticket} during profit lock")
            return False
        
        point = symbol_info.get('point', 0.00001)
        digits = symbol_info.get('digits', 5)
        
        # Normalize SL price to point precision
        if digits in [5, 3]:
            target_sl_price = round(target_sl_price / point) * point
        else:
            target_sl_price = round(target_sl_price, digits)
        
        # Get or create thread-safe lock for this ticket
        with self._sl_locks_lock:
            if ticket not in self._sl_update_locks:
                self._sl_update_locks[ticket] = threading.Lock()
            ticket_lock = self._sl_update_locks[ticket]
        
        # Use ticket-specific lock to prevent race conditions with Micro-HFT/Trailing Stop
        with ticket_lock:
            # Enhanced retry logic with fresh position data checks and increasing backoff
            last_error = None
            retry_count = self.retry_attempts_sweet_spot if hasattr(self, 'retry_attempts_sweet_spot') else self.max_lock_retries
            
            for attempt in range(retry_count):
                # CRITICAL: Get fresh position data before each attempt to ensure accuracy
                fresh_position = self.order_manager.get_position_by_ticket(ticket)
                if not fresh_position:
                    logger.warning(f"[WARNING] Position {ticket} not found during profit lock attempt {attempt + 1}")
                    return False  # Position closed
                
                # Verify profit is still in acceptable range
                fresh_profit = fresh_position.get('profit', 0.0)
                if fresh_profit < self.min_profit_threshold_usd:
                    logger.debug(f"[WARNING] Profit dropped below threshold during lock attempt: ${fresh_profit:.2f}")
                    return False
                
                # Attempt to modify order with broker
                # CRITICAL FIX: Log SL update attempt before calling order_manager
                # Get fresh position to get current SL
                fresh_position_for_log = self.order_manager.get_position_by_ticket(ticket)
                current_sl = fresh_position_for_log.get('sl', 0.0) if fresh_position_for_log else 0.0
                logger.info(f"🔥 SL UPDATE ATTEMPT (ProfitLockingEngine): Ticket={ticket} Symbol={symbol} OldSL={current_sl:.5f} NewSL={target_sl_price:.5f} TargetLock=${target_lock_profit:.2f}")
                success = self.order_manager.modify_order(ticket, stop_loss_price=target_sl_price)
                if success:
                    logger.info(f"[OK] SL UPDATE SUCCESS (ProfitLockingEngine): Ticket={ticket} Symbol={symbol} NewSL={target_sl_price:.5f}")
                else:
                    logger.error(f"[ERROR] SL UPDATE FAILED (ProfitLockingEngine): Ticket={ticket} Symbol={symbol} TargetSL={target_sl_price:.5f}")
                
                if success:
                    # Verify SL was actually applied by getting fresh position
                    time.sleep(self.poll_interval_ms / 1000.0)  # Use configured poll interval
                    verify_position = self.order_manager.get_position_by_ticket(ticket)
                    if verify_position:
                        applied_sl = verify_position.get('sl', 0.0)
                        # Check if SL was actually applied (with small tolerance for rounding)
                        sl_tolerance = point * 10  # 1 pip tolerance
                        if abs(applied_sl - target_sl_price) <= sl_tolerance:
                            # Mark SL as verified in tracking
                            if ticket not in self._locked_positions:
                                self._locked_positions[ticket] = {}
                            self._locked_positions[ticket]['sl_verified'] = True
                            self._locked_positions[ticket]['applied_sl_price'] = applied_sl
                            
                            logger.info(f"[OK] SWEET SPOT LOCK APPLIED (BROKER VERIFIED): {symbol} Ticket {ticket} | "
                                      f"Attempt {attempt + 1}/{retry_count} | "
                                      f"Current profit: ${current_profit:.2f} | "
                                      f"Locked at: ${target_lock_profit:.2f} | "
                                      f"Target SL: {target_sl_price:.5f} | "
                                      f"Applied SL: {applied_sl:.5f} | SUCCESS")
                            return True
                        else:
                            logger.warning(f"[WARNING] SL modification reported success but SL not updated correctly: "
                                         f"Target: {target_sl_price:.5f}, Applied: {applied_sl:.5f} | Retrying...")
                            # Mark as unverified
                            if ticket not in self._locked_positions:
                                self._locked_positions[ticket] = {}
                            self._locked_positions[ticket]['sl_verified'] = False
                    else:
                        # Position closed during verification
                        return False
                
                # Get error details for logging
                import MetaTrader5 as mt5
                error = mt5.last_error()
                if error and error[0] != 0:
                    last_error = error
                    logger.debug(f"[WARNING] Lock attempt {attempt + 1}/{retry_count} failed: "
                               f"{symbol} Ticket {ticket} | Error: {error} | "
                               f"Target lock: ${target_lock_profit:.2f} | Target SL: {target_sl_price:.5f}")
                
                # Retry with configured backoff array
                if attempt < retry_count - 1:
                    backoff_ms = self.retry_backoff_ms[attempt] if attempt < len(self.retry_backoff_ms) else self.retry_backoff_ms[-1]
                    time.sleep(backoff_ms / 1000.0)
            
            # All retries failed - mark as unverified
            if ticket not in self._locked_positions:
                self._locked_positions[ticket] = {}
            self._locked_positions[ticket]['sl_verified'] = False
            
            # Log failure
            logger.error(f"[ERROR] SWEET SPOT LOCK FAILED: {symbol} Ticket {ticket} | "
                       f"After {retry_count} attempts | "
                       f"Target lock: ${target_lock_profit:.2f} | "
                       f"Target SL: {target_sl_price:.5f} | "
                       f"Last error: {last_error if last_error else 'Unknown'}")
            return False
    
    def cleanup_closed_position(self, ticket: int):
        """
        Clean up tracking data for a closed position.
        
        Args:
            ticket: Position ticket number
        """
        self._locked_positions.pop(ticket, None)
        self._sweet_spot_min_profit.pop(ticket, None)
        # Clean up thread lock
        with self._sl_locks_lock:
            self._sl_update_locks.pop(ticket, None)
    
    def is_sl_verified(self, ticket: int) -> bool:
        """
        Check if SL has been verified as applied by broker.
        
        Args:
            ticket: Position ticket number
        
        Returns:
            True if SL is verified, False if pending or unknown
        """
        if ticket in self._locked_positions:
            return self._locked_positions[ticket].get('sl_verified', False)
        return False
    
    def get_minimum_tracked_profit(self, ticket: int) -> Optional[float]:
        """
        Get the minimum profit tracked for a position in sweet spot.
        
        Args:
            ticket: Position ticket number
        
        Returns:
            Minimum profit tracked in sweet spot, or None if not tracked
        """
        return self._sweet_spot_min_profit.get(ticket)

