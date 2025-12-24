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
        
        # Calculate price difference needed for target profit
        # Profit = (price_diff) * lot_size * contract_size
        # price_diff = Profit / (lot_size * contract_size)
        price_diff = self.tp_target_usd / (lot_size * contract_size)
        
        # Calculate TP price based on order type
        if order_type == 'BUY' or (isinstance(order_type, int) and order_type == mt5.ORDER_TYPE_BUY):
            tp_price = entry_price + price_diff
        else:  # SELL
            tp_price = entry_price - price_diff
        
        # Normalize to symbol's tick size
        if tp_price > 0:
            tp_price = round(tp_price / point) * point
            tp_price = round(tp_price, digits)
        
        return tp_price
    
    def apply_tp_to_position(self, ticket: int) -> Tuple[bool, str]:
        """
        Apply TP to a position if not already applied.
        
        Args:
            ticket: Position ticket
            
        Returns:
            (success, reason)
        """
        position = self.order_manager.get_position_by_ticket(ticket)
        if position is None:
            return False, "Position not found"
        
        with self._tracking_lock:
            if ticket in self._position_tracking and self._position_tracking[ticket].get('tp_applied', False):
                return True, "TP already applied"
        
        # Calculate TP price
        tp_price = self.calculate_tp_price(position)
        if tp_price is None:
            return False, "Failed to calculate TP price"
        
        # Apply TP via order modification
        success = self.order_manager.modify_order(
            ticket=ticket,
            take_profit_price=tp_price
        )
        
        if success:
            with self._tracking_lock:
                if ticket not in self._position_tracking:
                    self._position_tracking[ticket] = {}
                self._position_tracking[ticket]['tp_applied'] = True
                self._position_tracking[ticket]['tp_price'] = tp_price
                self._position_tracking[ticket]['partial_closed'] = False
            
            logger.info(f"[TP_APPLIED] Ticket {ticket} | Symbol {position.get('symbol')} | "
                        f"TP price: {tp_price:.5f} | Target profit: ${self.tp_target_usd:.2f}")
            return True, "TP applied successfully"
        else:
            return False, "Failed to modify order with TP"
    
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

