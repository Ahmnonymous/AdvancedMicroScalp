"""
Take Profit (TP) Management System
Handles TP enforcement, partial closes at 50% TP, and full closes at 100% TP.

This module provides:
- TP calculation and application
- Partial close at 50% TP target
- Full close at 100% TP target
- Thread-safe position tracking
- Comprehensive logging
"""

import threading
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from collections import defaultdict

from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from utils.logger_factory import get_logger
import MetaTrader5 as mt5

# Module-level logger
logger = None


class TPManager:
    """
    Take Profit Manager
    
    Handles TP logic:
    1. Calculate and apply TP when trade opens ($1.00 default)
    2. Monitor profit and execute partial close at 50% TP ($0.50)
    3. Execute full close at 100% TP ($1.00)
    """
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector, order_manager: OrderManager):
        """
        Initialize TP Manager.
        
        Args:
            config: Configuration dictionary
            mt5_connector: MT5Connector instance
            order_manager: OrderManager instance
        """
        global logger
        is_backtest = config.get('mode') == 'backtest'
        log_path = "logs/backtest/engine/tp_manager.log" if is_backtest else "logs/live/engine/tp_manager.log"
        logger = get_logger("tp_manager", log_path)
        
        self.config = config
        self.risk_config = config.get('risk', {})
        self.mt5_connector = mt5_connector
        self.order_manager = order_manager
        
        # Configuration
        self.tp_target_usd = self.risk_config.get('take_profit_usd', 1.0)
        self.partial_close_pct = self.risk_config.get('take_profit_partial_close_pct', 50)
        
        # Tracking
        self._position_tracking = {}  # {ticket: {'tp_applied': bool, 'partial_closed': bool, 'tp_price': float}}
        self._tracking_lock = threading.RLock()
        
        # CRITICAL SAFETY FIX #8: Soft TP monitoring for broker bug workaround
        self._soft_tp_tracking = {}  # {ticket: {'tp_price': float, 'tp_target_usd': float, 'enabled': bool}}
        self._soft_tp_running = False
        self._soft_tp_thread: Optional[threading.Thread] = None
        self._soft_tp_check_interval = 0.1  # 100ms check interval
        
        # CRITICAL FIX: Persistent TP retry mechanism - keeps trying until TP is set
        self._persistent_retry_running = False
        self._persistent_retry_thread: Optional[threading.Thread] = None
        self._persistent_retry_interval = 5.0  # Retry every 5 seconds
        
        logger.info(f"TP Manager initialized | TP target: ${self.tp_target_usd:.2f} | Partial close: {self.partial_close_pct}%")
    
    def calculate_tp_price(self, position: Dict[str, Any]) -> Optional[float]:
        """
        Calculate TP price for a position to achieve target profit.
        
        Args:
            position: Position dictionary
            
        Returns:
            TP price or None if calculation fails
        """
        symbol = position.get('symbol', '')
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        
        if not symbol or entry_price <= 0 or lot_size <= 0:
            return None
        
        # Get symbol info
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return None
        
        contract_size = symbol_info.get('contract_size', 100000)
        point = symbol_info.get('point', 0.00001)
        digits = symbol_info.get('digits', 5)
        tick_value = symbol_info.get('tick_value', 0.0)
        tick_size = symbol_info.get('tick_size', point)
        
        # CRITICAL FIX: Use tick_value for crypto/special symbols (same logic as SL manager)
        # For symbols with contract_size = 1 or very small contract_size, use tick_value
        # This handles crypto pairs like BTCXAUm where contract_size is reported as 1 but effective is 1000
        # Profit = (price_diff_in_points) * lot_size * tick_value
        # For standard forex: Profit = (price_diff) * lot_size * contract_size
        
        # Determine if we should use tick_value (crypto/special symbols)
        use_tick_value = False
        if contract_size == 1 and tick_value > 0:
            use_tick_value = True
        elif contract_size < 100 and tick_value > 0:
            # For symbols with small contract_size, check if tick_value gives more reasonable calculation
            # If using contract_size would give price_diff > 10% of entry, prefer tick_value
            price_diff_contract = self.tp_target_usd / (lot_size * contract_size) if contract_size > 0 else float('inf')
            if price_diff_contract > entry_price * 0.10:
                use_tick_value = True
        
        if use_tick_value and tick_value > 0:
            # Crypto or special symbols: use tick_value
            # price_diff_in_points = Profit / (lot_size * tick_value)
            price_diff_in_points = self.tp_target_usd / (lot_size * tick_value)
            price_diff = price_diff_in_points * tick_size
            logger.debug(f"[TP_CALC] {symbol} using tick_value method | tick_value={tick_value:.8f} | price_diff={price_diff:.5f}")
        else:
            # Standard forex: use contract_size
            # Profit = (price_diff) * lot_size * contract_size
            # price_diff = Profit / (lot_size * contract_size)
            price_diff = self.tp_target_usd / (lot_size * contract_size) if contract_size > 0 else 0.0
            logger.debug(f"[TP_CALC] {symbol} using contract_size method | contract_size={contract_size} | price_diff={price_diff:.5f}")
        
        # Calculate TP price based on order type
        if order_type == 'BUY' or (isinstance(order_type, int) and order_type == mt5.ORDER_TYPE_BUY):
            tp_price = entry_price + price_diff
        else:  # SELL
            tp_price = entry_price - price_diff
        
        # CRITICAL FIX: Validate TP price is reasonable (not negative, not too far from entry)
        # For SELL: TP must be below entry but positive
        # For BUY: TP must be above entry
        if order_type == 'BUY' or (isinstance(order_type, int) and order_type == mt5.ORDER_TYPE_BUY):
            if tp_price <= entry_price:
                logger.error(f"[TP_CALC_ERROR] BUY order TP ({tp_price:.5f}) must be above entry ({entry_price:.5f})")
                return None
        else:  # SELL
            if tp_price <= 0:
                logger.error(f"[TP_CALC_ERROR] SELL order TP ({tp_price:.5f}) is negative or zero")
                return None
            if tp_price >= entry_price:
                logger.error(f"[TP_CALC_ERROR] SELL order TP ({tp_price:.5f}) must be below entry ({entry_price:.5f})")
                return None
        
        # Normalize to symbol's tick size
        tp_price = round(tp_price / point) * point
        tp_price = round(tp_price, digits)
        
        return tp_price
    
    def apply_tp_to_position(self, ticket: int, max_attempts: int = 10) -> Tuple[bool, str]:
        """
        Apply TP to a position if not already applied.
        
        CRITICAL FIX: Persistent retry mechanism - keeps trying until TP is set or position closes.
        This ensures TP is ALWAYS set, even if broker has issues.
        
        Args:
            ticket: Position ticket
            max_attempts: Maximum number of attempts (default 10, but will keep retrying in background)
            
        Returns:
            (success, reason)
        """
        position = self.order_manager.get_position_by_ticket(ticket)
        if position is None:
            return False, "Position not found"
        
        # Check if TP is already applied and verified
        with self._tracking_lock:
            if ticket in self._position_tracking and self._position_tracking[ticket].get('tp_applied', False):
                # Verify TP is still set
                fresh_check = self.order_manager.get_position_by_ticket(ticket)
                if fresh_check:
                    applied_tp = fresh_check.get('tp', 0.0)
                    expected_tp = self._position_tracking[ticket].get('tp_price', 0.0)
                    if applied_tp != 0.0 and abs(applied_tp - expected_tp) / max(abs(expected_tp), 0.00001) < 0.001:
                        return True, "TP already applied and verified"
                    else:
                        # TP was removed or changed - need to reapply
                        logger.warning(f"[TP_REAPPLY] Ticket {ticket} | TP was removed/changed | "
                                     f"Expected: {expected_tp:.5f} | Current: {applied_tp:.5f} | Reapplying...")
                        self._position_tracking[ticket]['tp_applied'] = False
        
        # Calculate TP price
        tp_price = self.calculate_tp_price(position)
        if tp_price is None:
            return False, "Failed to calculate TP price"
        
        # CRITICAL FIX: Persistent retry with increasing delays - keep trying until TP is set
        # Start with shorter delays, then increase for persistent retries
        retry_delays = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0]  # Progressive delays up to 10s
        max_retries = min(max_attempts, len(retry_delays))
        
        for attempt in range(max_retries):
            # CRITICAL FIX: Get fresh position data before each attempt
            # Position data may have changed (lot size, entry price, etc.)
            fresh_position_before = self.order_manager.get_position_by_ticket(ticket)
            if fresh_position_before is None:
                return False, "Position closed during TP application"
            
            # Recalculate TP from fresh position data (in case position changed)
            recalculated_tp = self.calculate_tp_price(fresh_position_before)
            if recalculated_tp is None:
                logger.error(f"[TP_CALC_FAIL] Ticket {ticket} | Failed to recalculate TP on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
                    continue
                else:
                    return False, "Failed to calculate TP price after all attempts"
            
            # Use recalculated TP if different from original
            current_tp_price = recalculated_tp if recalculated_tp != tp_price else tp_price
            if recalculated_tp != tp_price:
                logger.info(f"[TP_RECALC] Ticket {ticket} | TP recalculated: {tp_price:.5f} -> {current_tp_price:.5f}")
            
            # Apply TP via order modification
            logger.info(f"[TP_ATTEMPT] Ticket {ticket} | Attempt {attempt + 1}/{max_retries} | "
                        f"Setting TP: {current_tp_price:.5f}")
            success = self.order_manager.modify_order(
                ticket=ticket,
                take_profit_price=current_tp_price
            )
            
            if success:
                # CRITICAL FIX: Enhanced verification with multiple checks
                # Wait progressively longer for broker to process (broker latency varies)
                verification_delay = min(0.5 + (attempt * 0.2), 2.0)  # 0.5s to 2.0s
                time.sleep(verification_delay)
                
                # Verify TP was actually applied - check multiple times if needed
                verification_passed = False
                for verify_attempt in range(3):  # Up to 3 verification attempts
                    fresh_position = self.order_manager.get_position_by_ticket(ticket)
                    if fresh_position is None:
                        return False, "Position closed during TP verification"
                    
                    applied_tp = fresh_position.get('tp', 0.0)
                    point = symbol_info.get('point', 0.00001) if 'symbol_info' in locals() else 0.00001
                    if not 'symbol_info' in locals():
                        symbol_info = self.mt5_connector.get_symbol_info(fresh_position.get('symbol', ''))
                        if symbol_info:
                            point = symbol_info.get('point', 0.00001)
                    
                    # CRITICAL FIX: Check if TP is close to target (within 1 pip tolerance)
                    # For SELL orders, TP is below entry but still positive - check absolute difference
                    tp_tolerance = point * 10  # 1 pip tolerance
                    if applied_tp != 0.0 and abs(applied_tp - current_tp_price) <= tp_tolerance:
                        verification_passed = True
                        with self._tracking_lock:
                            if ticket not in self._position_tracking:
                                self._position_tracking[ticket] = {}
                            self._position_tracking[ticket]['tp_applied'] = True
                            self._position_tracking[ticket]['tp_price'] = current_tp_price
                            self._position_tracking[ticket]['partial_closed'] = False
                        
                        logger.info(f"[TP_APPLIED] Ticket {ticket} | Symbol {fresh_position.get('symbol', 'N/A')} | "
                                    f"TP price: {current_tp_price:.5f} | Applied: {applied_tp:.5f} | "
                                    f"Target profit: ${self.tp_target_usd:.2f} | "
                                    f"Attempt: {attempt + 1}/{max_retries} | Verify: {verify_attempt + 1}/3")
                        return True, f"TP applied successfully: {current_tp_price:.5f}"
                    else:
                        # CRITICAL FIX: TP is wrong or not set - auto-correct immediately
                        if applied_tp == 0.0:
                            logger.warning(f"[TP_AUTO_CORRECT] Ticket {ticket} | TP not set (0.0) | Expected: {current_tp_price:.5f} | Auto-correcting...")
                        elif abs(applied_tp - current_tp_price) > tp_tolerance:
                            logger.warning(f"[TP_AUTO_CORRECT] Ticket {ticket} | TP is wrong | Applied: {applied_tp:.5f} | Expected: {current_tp_price:.5f} | Diff: {abs(applied_tp - current_tp_price):.5f} | Auto-correcting...")
                        
                        # TP not applied yet or wrong - wait and retry verification
                        if verify_attempt < 2:
                            logger.debug(f"[TP_VERIFY_RETRY] Ticket {ticket} | Verify attempt {verify_attempt + 1}/3 | "
                                       f"Expected: {current_tp_price:.5f} | Applied: {applied_tp:.5f} | "
                                       f"Waiting 300ms before next check...")
                            time.sleep(0.3)  # Wait 300ms before next verification
                            continue
                
                # Verification failed after all attempts - TP is wrong, will retry in next attempt
                if not verification_passed:
                    logger.critical(f"[TP_BROKER_BUG] Ticket {ticket} | "
                                  f"MT5 reported success but TP not applied/wrong after {verify_attempt + 1} verification attempts | "
                                  f"Expected: {current_tp_price:.5f} | Applied: {applied_tp:.5f} | "
                                  f"Will retry in next attempt")
            
            # If modify_order returned False, log and continue to retry
            if not success:
                logger.warning(f"[TP_MODIFY_FAILED] Ticket {ticket} | "
                             f"modify_order returned False | "
                             f"Attempt: {attempt + 1}/{max_retries}")
            
            # If not last attempt, wait before retry
            if attempt < max_retries - 1:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                logger.warning(f"[TP_RETRY] Ticket {ticket} | "
                             f"TP application failed, retrying in {delay:.1f}s | "
                             f"Attempt: {attempt + 1}/{max_retries}")
                time.sleep(delay)
        
        # All retries failed - try one final recalculation from fresh position
        logger.warning(f"[TP_APPLY_FAILED] Ticket {ticket} | "
                      f"TP application failed after {max_retries} attempts | "
                      f"Attempting final recalculation from fresh position data")
        
        # CRITICAL FIX: Final attempt - get fresh position and recalculate TP
        fresh_position = self.order_manager.get_position_by_ticket(ticket)
        if fresh_position:
            final_tp_price = self.calculate_tp_price(fresh_position)
            if final_tp_price:
                logger.info(f"[TP_FINAL_RECALC] Ticket {ticket} | "
                          f"Final TP recalculation: {final_tp_price:.5f} | "
                          f"Attempting one more modification")
                
                final_success = self.order_manager.modify_order(
                    ticket=ticket,
                    take_profit_price=final_tp_price
                )
                
                if final_success:
                    time.sleep(1.0)  # Longer delay for final verification
                    verify_position = self.order_manager.get_position_by_ticket(ticket)
                    if verify_position:
                        verify_tp = verify_position.get('tp', 0.0)
                        symbol_info = self.mt5_connector.get_symbol_info(verify_position.get('symbol', ''))
                        point = symbol_info.get('point', 0.00001) if symbol_info else 0.00001
                        tp_tolerance = point * 10  # 1 pip tolerance
                        if verify_tp > 0 and abs(verify_tp - final_tp_price) <= tp_tolerance:
                            logger.info(f"[TP_APPLIED_FINAL] Ticket {ticket} | "
                                      f"TP applied successfully on final attempt | "
                                      f"TP price: {final_tp_price:.5f}")
                            with self._tracking_lock:
                                if ticket not in self._position_tracking:
                                    self._position_tracking[ticket] = {}
                                self._position_tracking[ticket]['tp_applied'] = True
                                self._position_tracking[ticket]['tp_price'] = final_tp_price
                                self._position_tracking[ticket]['partial_closed'] = False
                            return True, f"TP applied on final attempt: {final_tp_price:.5f}"
        
        # CRITICAL FIX: If all attempts failed, enable persistent background retry
        # This ensures TP will be set eventually, even if broker has temporary issues
        logger.error(f"[TP_PERSISTENT_RETRY] Ticket {ticket} | "
                    f"All immediate attempts failed - enabling persistent background retry | "
                    f"TP will be retried every 5 seconds until set or position closes")
        
        # Add to persistent retry tracking
        with self._tracking_lock:
            if ticket not in self._position_tracking:
                self._position_tracking[ticket] = {}
            self._position_tracking[ticket]['tp_applied'] = False
            self._position_tracking[ticket]['needs_persistent_retry'] = True
            self._position_tracking[ticket]['tp_price'] = final_tp_price if 'final_tp_price' in locals() and final_tp_price else tp_price
        
        # Start persistent retry thread if not already running
        if not hasattr(self, '_persistent_retry_running') or not self._persistent_retry_running:
            self._start_persistent_retry_thread()
        
        return False, f"TP application failed - persistent retry enabled"
    
    def check_and_execute_partial_close(self, position: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if position has reached 50% TP and execute partial close if needed.
        
        Args:
            position: Position dictionary
            
        Returns:
            (success, reason)
        """
        ticket = position.get('ticket', 0)
        current_profit = position.get('profit', 0.0)
        symbol = position.get('symbol', '')
        volume = position.get('volume', 0.0)
        
        # Check if already partially closed
        with self._tracking_lock:
            if ticket in self._position_tracking and self._position_tracking[ticket].get('partial_closed', False):
                return True, "Already partially closed"
        
        # Check if profit reached 50% TP target
        partial_tp_target = self.tp_target_usd * (self.partial_close_pct / 100.0)
        
        if current_profit < partial_tp_target:
            return False, f"Profit ${current_profit:.2f} below partial TP target ${partial_tp_target:.2f}"
        
        # Calculate volume to close (50% of position)
        close_volume = volume * (self.partial_close_pct / 100.0)
        
        # Get symbol info for volume step
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, "Cannot get symbol info"
        
        volume_step = symbol_info.get('volume_step', 0.01)
        if volume_step > 0:
            close_volume = round(close_volume / volume_step) * volume_step
        
        # Ensure close volume is at least minimum lot size
        min_lot = symbol_info.get('volume_min', 0.01)
        if close_volume < min_lot:
            close_volume = min_lot
        
        # Ensure we don't close more than available
        if close_volume >= volume:
            close_volume = volume * 0.5  # Close exactly 50%
            if volume_step > 0:
                close_volume = round(close_volume / volume_step) * volume_step
        
        # Execute partial close
        success = self._partial_close_position(ticket, symbol, close_volume)
        
        if success:
            with self._tracking_lock:
                if ticket not in self._position_tracking:
                    self._position_tracking[ticket] = {}
                self._position_tracking[ticket]['partial_closed'] = True
            
            logger.info(f"[TP_PARTIAL_CLOSE] Ticket {ticket} | Symbol {symbol} | "
                       f"Closed {close_volume:.4f} lots ({self.partial_close_pct}%) | "
                       f"Profit at close: ${current_profit:.2f} | Target: ${partial_tp_target:.2f}")
            return True, f"Partial close executed: {close_volume:.4f} lots"
        else:
            return False, "Failed to execute partial close"
    
    def _partial_close_position(self, ticket: int, symbol: str, volume: float) -> bool:
        """
        Execute partial close of a position.
        
        Args:
            ticket: Position ticket
            symbol: Trading symbol
            volume: Volume to close
            
        Returns:
            True if successful
        """
        if not self.mt5_connector.ensure_connected():
            return False
        
        position = self.order_manager.get_position_by_ticket(ticket)
        if position is None:
            return False
        
        # Get current price
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
        
        # Get filling type
        filling_type = None
        filling_modes = symbol_info.get('filling_mode')
        if filling_modes is None:
            symbol_info_obj = mt5.symbol_info(symbol)
            if symbol_info_obj is not None:
                filling_modes = symbol_info_obj.filling_mode if hasattr(symbol_info_obj, 'filling_mode') else None
        
        if filling_modes is not None:
            if filling_modes & 1:
                filling_type = mt5.ORDER_FILLING_FOK
            elif filling_modes & 2:
                filling_type = mt5.ORDER_FILLING_IOC
            elif filling_modes & 4:
                filling_type = mt5.ORDER_FILLING_RETURN
        
        if filling_type is None:
            filling_type = mt5.ORDER_FILLING_RETURN
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "TP Partial Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        result = mt5.order_send(request)
        
        if result is None:
            logger.error(f"Partial close send returned None for ticket {ticket}")
            return False
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Partial close failed for ticket {ticket}: {result.retcode} - {result.comment}")
            return False
        
        logger.info(f"Partial close successful for ticket {ticket}: {volume:.4f} lots")
        return True
    
    def check_tp_hit(self, position: Dict[str, Any]) -> bool:
        """
        Check if TP has been hit (for full close).
        
        Args:
            position: Position dictionary
            
        Returns:
            True if TP hit
        """
        tp_price = position.get('tp', 0.0)
        if tp_price <= 0:
            return False
        
        current_price = position.get('price_current', 0.0)
        order_type = position.get('type', '')
        
        if current_price <= 0:
            return False
        
        # Check if TP hit based on order type
        if order_type == 'BUY' or (isinstance(order_type, int) and order_type == mt5.ORDER_TYPE_BUY):
            return current_price >= tp_price
        else:  # SELL
            return current_price <= tp_price
    
    def cleanup_ticket(self, ticket: int):
        """Remove tracking for closed position."""
        with self._tracking_lock:
            self._position_tracking.pop(ticket, None)
            self._soft_tp_tracking.pop(ticket, None)
    
    def _start_persistent_retry_thread(self):
        """Start background thread that persistently retries TP application for positions that failed."""
        if self._persistent_retry_running:
            return
        
        self._persistent_retry_running = True
        self._persistent_retry_thread = threading.Thread(
            target=self._persistent_retry_loop,
            name="TPPersistentRetry",
            daemon=True
        )
        self._persistent_retry_thread.start()
        logger.info("[TP_PERSISTENT_RETRY_STARTED] Background thread started for persistent TP retry")
    
    def _persistent_retry_loop(self):
        """Background loop that persistently retries TP application for positions that need it."""
        logger.info("[TP_PERSISTENT_RETRY_LOOP] Persistent retry loop started")
        while self._persistent_retry_running:
            try:
                # Get all tickets that need persistent retry
                tickets_to_retry = []
                with self._tracking_lock:
                    for ticket, tracking in self._position_tracking.items():
                        if tracking.get('needs_persistent_retry', False) and not tracking.get('tp_applied', False):
                            tickets_to_retry.append(ticket)
                
                # Retry TP for each ticket
                for ticket in tickets_to_retry:
                    # Check if position still exists
                    position = self.order_manager.get_position_by_ticket(ticket)
                    if position is None:
                        # Position closed - remove from tracking
                        with self._tracking_lock:
                            self._position_tracking.pop(ticket, None)
                        logger.info(f"[TP_PERSISTENT_RETRY] Ticket {ticket} | Position closed - removing from retry list")
                        continue
                    
                    # Check if TP is already set (maybe set manually or by another process)
                    current_tp = position.get('tp', 0.0)
                    expected_tp = self._position_tracking.get(ticket, {}).get('tp_price', 0.0)
                    if current_tp > 0 and expected_tp > 0:
                        symbol_info = self.mt5_connector.get_symbol_info(position.get('symbol', ''))
                        point = symbol_info.get('point', 0.00001) if symbol_info else 0.00001
                        tp_tolerance = point * 10  # 1 pip tolerance
                        if abs(current_tp - expected_tp) <= tp_tolerance:
                            # TP is set - mark as applied
                            with self._tracking_lock:
                                if ticket in self._position_tracking:
                                    self._position_tracking[ticket]['tp_applied'] = True
                                    self._position_tracking[ticket]['needs_persistent_retry'] = False
                            logger.info(f"[TP_PERSISTENT_RETRY] Ticket {ticket} | TP is now set: {current_tp:.5f} - removing from retry list")
                            continue
                    
                    # Try to apply TP again
                    logger.info(f"[TP_PERSISTENT_RETRY] Ticket {ticket} | Attempting to apply TP (persistent retry)")
                    success, reason = self.apply_tp_to_position(ticket, max_attempts=3)  # Quick retry in background
                    if success:
                        logger.info(f"[TP_PERSISTENT_RETRY] Ticket {ticket} | TP applied successfully: {reason}")
                        with self._tracking_lock:
                            if ticket in self._position_tracking:
                                self._position_tracking[ticket]['needs_persistent_retry'] = False
                    else:
                        logger.warning(f"[TP_PERSISTENT_RETRY] Ticket {ticket} | TP application failed: {reason} | Will retry in {self._persistent_retry_interval}s")
                
                # Wait before next retry cycle
                time.sleep(self._persistent_retry_interval)
                
            except Exception as e:
                logger.error(f"[TP_PERSISTENT_RETRY_ERROR] Error in persistent retry loop: {e}", exc_info=True)
                time.sleep(self._persistent_retry_interval)
    
    def _enable_soft_tp_monitoring(self, ticket: int, tp_price: float, tp_target_usd: float):
        """
        CRITICAL SAFETY FIX #8: Enable soft TP monitoring for broker bug workaround.
        
        When broker TP fails (TP_BROKER_BUG), monitor price manually and close when target reached.
        
        Args:
            ticket: Position ticket
            tp_price: Target TP price
            tp_target_usd: Target profit in USD
        """
        with self._tracking_lock:
            if ticket not in self._soft_tp_tracking:
                self._soft_tp_tracking[ticket] = {
                    'tp_price': tp_price,
                    'tp_target_usd': tp_target_usd,
                    'enabled': True
                }
                logger.info(f"[SOFT_TP_ENABLED] Ticket {ticket} | "
                          f"Soft TP monitoring enabled | TP price: {tp_price:.5f} | Target: ${tp_target_usd:.2f} | "
                          f"Check interval: {self._soft_tp_check_interval}s")
            else:
                # Update existing tracking
                self._soft_tp_tracking[ticket].update({
                    'tp_price': tp_price,
                    'tp_target_usd': tp_target_usd,
                    'enabled': True
                })
                logger.info(f"[SOFT_TP_UPDATED] Ticket {ticket} | "
                          f"Soft TP monitoring updated | TP price: {tp_price:.5f} | Target: ${tp_target_usd:.2f}")
        
        # Start monitoring thread if not already running
        if not self._soft_tp_running:
            self._start_soft_tp_monitoring()
        else:
            logger.debug(f"[SOFT_TP_MONITOR] Monitoring thread already running | Active tickets: {len(self._soft_tp_tracking)}")
    
    def _start_soft_tp_monitoring(self):
        """Start soft TP monitoring thread."""
        if self._soft_tp_running:
            return
        
        self._soft_tp_running = True
        self._soft_tp_thread = threading.Thread(
            target=self._soft_tp_monitor_loop,
            daemon=True,
            name="SoftTPMonitor"
        )
        self._soft_tp_thread.start()
        logger.info("[SOFT_TP_MONITOR_STARTED] Soft TP monitoring thread started")
    
    def _soft_tp_monitor_loop(self):
        """Monitor positions with soft TP and close when target reached."""
        logger.info("[SOFT_TP_LOOP] Soft TP monitoring loop started")
        loop_iteration = 0
        
        while self._soft_tp_running:
            try:
                loop_iteration += 1
                
                # Get tickets to monitor
                with self._tracking_lock:
                    tickets_to_monitor = [
                        (ticket, info['tp_price'], info['tp_target_usd'])
                        for ticket, info in self._soft_tp_tracking.items()
                        if info.get('enabled', False)
                    ]
                
                if not tickets_to_monitor:
                    # Log periodically that we're waiting for positions
                    if loop_iteration % 60 == 0:  # Every 60 iterations (30 seconds if interval is 0.5s)
                        logger.debug(f"[SOFT_TP_LOOP] No positions to monitor (iteration {loop_iteration})")
                    time.sleep(self._soft_tp_check_interval)
                    continue
                
                logger.debug(f"[SOFT_TP_CHECK] Checking {len(tickets_to_monitor)} position(s) | Iteration: {loop_iteration}")
                
                # Check each position
                for ticket, tp_price, tp_target_usd in tickets_to_monitor:
                    position = self.order_manager.get_position_by_ticket(ticket)
                    if not position:
                        # Position closed - remove from tracking
                        logger.info(f"[SOFT_TP_POSITION_CLOSED] Ticket {ticket} | Position no longer exists - removing from tracking")
                        with self._tracking_lock:
                            self._soft_tp_tracking.pop(ticket, None)
                        continue
                    
                    current_profit = position.get('profit', 0.0)
                    symbol = position.get('symbol', '')
                    order_type = position.get('type', '')
                    entry_price = position.get('price_open', 0.0)
                    
                    # Get current price
                    symbol_info = self.mt5_connector.get_symbol_info(symbol)
                    if not symbol_info:
                        logger.warning(f"[SOFT_TP_CHECK] Ticket {ticket} | Cannot get symbol info for {symbol}")
                        continue
                    
                    tick = self.mt5_connector.get_symbol_info_tick(symbol)
                    if not tick:
                        logger.warning(f"[SOFT_TP_CHECK] Ticket {ticket} | Cannot get tick data for {symbol}")
                        continue
                    
                    # Check if TP target reached
                    # Handle both string and int order types
                    is_buy = (order_type == 'BUY') or (isinstance(order_type, int) and order_type == mt5.ORDER_TYPE_BUY)
                    
                    if is_buy:
                        current_price = tick.bid
                        tp_hit = current_price >= tp_price
                        price_status = "ABOVE" if tp_hit else "BELOW"
                    else:  # SELL
                        current_price = tick.ask
                        tp_hit = current_price <= tp_price
                        price_status = "BELOW" if tp_hit else "ABOVE"
                    
                    # Log order type for debugging
                    if loop_iteration % 20 == 0:  # Every 20 iterations
                        logger.debug(f"[SOFT_TP_DEBUG] Ticket {ticket} | Order type: {order_type} (type: {type(order_type).__name__}) | Is BUY: {is_buy}")
                    
                    # Also check profit-based target (more reliable)
                    profit_target_reached = current_profit >= tp_target_usd
                    profit_status = "REACHED" if profit_target_reached else "NOT_REACHED"
                    
                    # Log detailed status every 10 iterations (or when close to target)
                    should_log = (loop_iteration % 10 == 0) or (current_profit >= tp_target_usd * 0.8) or profit_target_reached
                    
                    if should_log:
                        logger.info(f"[SOFT_TP_STATUS] Ticket {ticket} | {symbol} | "
                                  f"Current price: {current_price:.5f} | TP price: {tp_price:.5f} | Price {price_status} TP | "
                                  f"Current profit: ${current_profit:.2f} | Target: ${tp_target_usd:.2f} | Profit {profit_status} | "
                                  f"Entry: {entry_price:.5f} | Iteration: {loop_iteration}")
                    
                    if tp_hit or profit_target_reached:
                        logger.info(f"[SOFT_TP_HIT] Ticket {ticket} | {symbol} | "
                                  f"TP target reached | Price: {current_price:.5f} (target: {tp_price:.5f}) | "
                                  f"Profit: ${current_profit:.2f} (target: ${tp_target_usd:.2f}) | "
                                  f"Closing position manually")
                        
                        # Close position manually
                        success = self.order_manager.close_position(
                            ticket=ticket,
                            comment=f"Soft TP: Target reached (${tp_target_usd:.2f})"
                        )
                        
                        if success:
                            with self._tracking_lock:
                                self._soft_tp_tracking.pop(ticket, None)
                            logger.info(f"[SOFT_TP_CLOSED] Ticket {ticket} | Position closed successfully")
                        else:
                            logger.error(f"[SOFT_TP_CLOSE_FAILED] Ticket {ticket} | Failed to close position")
                
                time.sleep(self._soft_tp_check_interval)
                
            except Exception as e:
                logger.error(f"[SOFT_TP_ERROR] Exception in soft TP monitor loop: {e}", exc_info=True)
                time.sleep(self._soft_tp_check_interval)
        
        logger.info("[SOFT_TP_LOOP] Soft TP monitoring loop stopped")
    
    def stop_soft_tp_monitoring(self):
        """Stop soft TP monitoring thread."""
        self._soft_tp_running = False
        if self._soft_tp_thread:
            self._soft_tp_thread.join(timeout=2.0)
        logger.info("[SOFT_TP_MONITOR_STOPPED] Soft TP monitoring thread stopped")

