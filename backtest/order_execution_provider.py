"""
Order Execution Provider Abstraction Layer
Provides unified interface for live and simulated order execution.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from abc import ABC, abstractmethod
from enum import Enum
import threading
import time
import math

from utils.logger_factory import get_logger

logger = get_logger("backtest_execution", "logs/backtest/execution.log")


class OrderType(Enum):
    BUY = 0
    SELL = 1


class OrderExecutionProvider(ABC):
    """Abstract base class for order execution providers."""
    
    @abstractmethod
    def place_order(self, symbol: str, order_type: OrderType, lot_size: float,
                   stop_loss: float, take_profit: Optional[float] = None,
                   comment: str = "Trading Bot") -> Optional[Dict[str, Any]]:
        """Place a market order."""
        pass
    
    @abstractmethod
    def modify_order(self, ticket: int, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    stop_loss_price: Optional[float] = None,
                    take_profit_price: Optional[float] = None) -> bool:
        """Modify an existing order."""
        pass
    
    @abstractmethod
    def get_open_positions(self, exclude_dec8: bool = True) -> List[Dict[str, Any]]:
        """Get all open positions."""
        pass
    
    @abstractmethod
    def get_position_by_ticket(self, ticket: int, exclude_dec8: bool = True) -> Optional[Dict[str, Any]]:
        """Get position by ticket number."""
        pass
    
    @abstractmethod
    def close_position(self, ticket: int, comment: str = None) -> bool:
        """Close a position."""
        pass


class LiveOrderExecutionProvider(OrderExecutionProvider):
    """Live order execution using real MT5."""
    
    def __init__(self, order_manager):
        self.order_manager = order_manager
    
    def place_order(self, symbol: str, order_type: OrderType, lot_size: float,
                   stop_loss: float, take_profit: Optional[float] = None,
                   comment: str = "Trading Bot") -> Optional[Dict[str, Any]]:
        from execution.order_manager import OrderType as LiveOrderType
        live_order_type = LiveOrderType.BUY if order_type == OrderType.BUY else LiveOrderType.SELL
        return self.order_manager.place_order(symbol, live_order_type, lot_size, stop_loss, take_profit, comment)
    
    def modify_order(self, ticket: int, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    stop_loss_price: Optional[float] = None,
                    take_profit_price: Optional[float] = None) -> bool:
        return self.order_manager.modify_order(ticket, stop_loss, take_profit, stop_loss_price, take_profit_price)
    
    def get_open_positions(self, exclude_dec8: bool = True) -> List[Dict[str, Any]]:
        return self.order_manager.get_open_positions(exclude_dec8)
    
    def get_position_by_ticket(self, ticket: int, exclude_dec8: bool = True) -> Optional[Dict[str, Any]]:
        return self.order_manager.get_position_by_ticket(ticket, exclude_dec8)
    
    def close_position(self, ticket: int, comment: str = None) -> bool:
        # Accept comment parameter for compatibility, but don't use it in live mode
        return self.order_manager.close_position(ticket)


class SimulatedOrderExecutionProvider(OrderExecutionProvider):
    """Simulated order execution for backtesting."""
    
    def __init__(self, market_data_provider, config: Dict[str, Any]):
        """
        Initialize simulated order execution provider.
        
        Args:
            market_data_provider: HistoricalMarketDataProvider instance
            config: Configuration dictionary
        """
        self.market_data_provider = market_data_provider
        self.config = config
        self.execution_config = config.get('execution', {})
        self.backtest_config = config.get('backtest', {})
        
        # Simulation parameters
        self.slippage_pips = self.backtest_config.get('slippage_pips', 1.0)
        self.spread_multiplier = self.backtest_config.get('spread_multiplier', 1.0)
        self.fill_delay_ms = self.backtest_config.get('fill_delay_ms', 0)
        self.partial_fills_enabled = self.backtest_config.get('partial_fills_enabled', False)
        
        # Track positions
        self.positions = {}  # {ticket: position_dict}
        self.next_ticket = 1000
        self._lock = threading.Lock()
        
        # CRITICAL: Track closure reasons to prevent premature closures
        self._closure_reasons = {}  # {ticket: 'SL'|'TP'|'MANUAL'|'UNKNOWN'}
        self._positions_closed_by_check = set()  # Track positions closed by check_sl_tp_hits()
        
        # Track execution metrics
        self.execution_metrics = {
            'orders_placed': 0,
            'orders_filled': 0,
            'orders_rejected': 0,
            'modifications': 0,
            'total_slippage': 0.0,
            'total_spread_cost': 0.0
        }
    
    def place_order(self, symbol: str, order_type: OrderType, lot_size: float,
                   stop_loss: float, take_profit: Optional[float] = None,
                   comment: str = "Trading Bot") -> Optional[Dict[str, Any]]:
        """
        Simulate order placement with MT5 error simulation.
        
        This method validates orders exactly like MT5 does and returns appropriate
        error codes for rejections (10013, 10016, 10029, etc.)
        """
        with self._lock:
            # Get current market data
            symbol_info = self.market_data_provider.get_symbol_info(symbol)
            if symbol_info is None:
                logger.error(f"Cannot place order: Symbol {symbol} not found")
                self.execution_metrics['orders_rejected'] += 1
                return None
            
            tick = self.market_data_provider.get_symbol_info_tick(symbol)
            if tick is None:
                logger.error(f"Cannot place order: No tick data for {symbol}")
                self.execution_metrics['orders_rejected'] += 1
                return None
            
            # CRITICAL FIX: Validate tick data to prevent NaN values
            tick_bid = tick.get('bid') if isinstance(tick, dict) else getattr(tick, 'bid', None)
            tick_ask = tick.get('ask') if isinstance(tick, dict) else getattr(tick, 'ask', None)
            
            # Validate tick prices are valid numbers
            if tick_bid is None or tick_ask is None or math.isnan(tick_bid) or math.isnan(tick_ask):
                logger.error(f"Cannot place order: Invalid tick data for {symbol} (bid: {tick_bid}, ask: {tick_ask})")
                self.execution_metrics['orders_rejected'] += 1
                return None
            
            if tick_bid <= 0 or tick_ask <= 0 or tick_ask <= tick_bid:
                logger.error(f"Cannot place order: Invalid tick prices for {symbol} (bid: {tick_bid}, ask: {tick_ask})")
                self.execution_metrics['orders_rejected'] += 1
                return None
            
            # CRITICAL: Validate order like MT5 does
            point = symbol_info.get('point', 0.00001)
            pip_value = point * 10 if symbol_info.get('digits', 5) == 5 else point
            stops_level = symbol_info.get('trade_stops_level', 0)
            freeze_level = symbol_info.get('trade_freeze_level', 0)
            volume_min = symbol_info.get('volume_min', 0.01)
            volume_max = symbol_info.get('volume_max', 100.0)
            volume_step = symbol_info.get('volume_step', 0.01)
            
            # Validate volume (error 10014: Invalid volume)
            if lot_size < volume_min or lot_size > volume_max:
                logger.error(f"Order rejected: Invalid volume {lot_size} for {symbol} (min: {volume_min}, max: {volume_max})")
                self.execution_metrics['orders_rejected'] += 1
                return {'error': -1}  # Volume error
            
            # Validate volume step
            if lot_size % volume_step != 0:
                logger.error(f"Order rejected: Volume {lot_size} not multiple of step {volume_step} for {symbol}")
                self.execution_metrics['orders_rejected'] += 1
                return {'error': -1}  # Volume error
            
            # Calculate entry price with slippage
            if order_type == OrderType.BUY:
                base_price = tick_ask
                validation_price = tick_ask  # For BUY, validate against ASK
                slippage = self.slippage_pips * point * 10
                entry_price = base_price + slippage
            else:  # SELL
                base_price = tick_bid
                validation_price = tick_ask  # For SELL, MT5 validates against ASK
                slippage = self.slippage_pips * point * 10
                entry_price = base_price - slippage
            
            # CRITICAL FIX: Validate calculated prices are not NaN
            if math.isnan(entry_price) or entry_price <= 0:
                logger.error(f"Cannot place order: Invalid entry price calculated for {symbol} (entry: {entry_price})")
                self.execution_metrics['orders_rejected'] += 1
                return None
            
            # Calculate stop loss price
            if order_type == OrderType.BUY:
                sl_price = entry_price - (stop_loss * pip_value)
            else:  # SELL
                # For SELL, SL is above entry (validation_price is ASK)
                sl_price = validation_price + (stop_loss * pip_value)
            
            # CRITICAL: Validate stop loss distance (error 10016: Invalid stops)
            if stops_level > 0:
                min_distance = stops_level * point
                if order_type == OrderType.BUY:
                    actual_distance = validation_price - sl_price
                else:  # SELL
                    actual_distance = sl_price - validation_price
                
                if actual_distance < min_distance:
                    logger.error(f"[ERROR] Order rejected: Stop loss too close (error 10016) for {symbol}")
                    logger.error(f"   Distance: {actual_distance:.5f}, Required: {min_distance:.5f}, Stops level: {stops_level}")
                    self.execution_metrics['orders_rejected'] += 1
                    return {'error': -3}  # Invalid stops error
            
            # CRITICAL: Validate freeze level (error 10029: Too close to market)
            if freeze_level > 0:
                if order_type == OrderType.BUY:
                    min_allowed_sl = tick_bid - (freeze_level * point)
                    if sl_price > min_allowed_sl:
                        logger.error(f"Order rejected: SL too close to market (error 10029) for {symbol}")
                        logger.error(f"   SL: {sl_price:.5f}, Min allowed: {min_allowed_sl:.5f}, Freeze level: {freeze_level}")
                        self.execution_metrics['orders_rejected'] += 1
                        return {'error': -2}  # Too close to market (retryable)
                else:  # SELL
                    min_allowed_sl = tick_ask + (freeze_level * point)
                    if sl_price < min_allowed_sl:
                        logger.error(f"Order rejected: SL too close to market (error 10029) for {symbol}")
                        logger.error(f"   SL: {sl_price:.5f}, Min allowed: {min_allowed_sl:.5f}, Freeze level: {freeze_level}")
                        self.execution_metrics['orders_rejected'] += 1
                        return {'error': -2}  # Too close to market (retryable)
            
            # CRITICAL FIX: Validate SL price is not NaN
            if math.isnan(sl_price) or sl_price <= 0:
                logger.error(f"Cannot place order: Invalid SL price calculated for {symbol} (SL: {sl_price})")
                self.execution_metrics['orders_rejected'] += 1
                return None
            
            # Calculate take profit price
            tp_price = None
            if take_profit:
                if order_type == OrderType.BUY:
                    tp_price = entry_price + (take_profit * pip_value)
                else:  # SELL
                    tp_price = entry_price - (take_profit * pip_value)
            
            # Normalize prices to point precision (MT5 requirement)
            digits = symbol_info.get('digits', 5)
            if sl_price > 0:
                sl_price = round(sl_price / point) * point
                sl_price = round(sl_price, digits)
            if tp_price and tp_price > 0:
                tp_price = round(tp_price / point) * point
                tp_price = round(tp_price, digits)
            
            # Simulate execution latency
            if self.fill_delay_ms > 0:
                time.sleep(self.fill_delay_ms / 1000.0)
            
            # Generate ticket
            ticket = self.next_ticket
            self.next_ticket += 1
            
            # Create position
            current_time = self.market_data_provider.get_current_time()
            position = {
                'ticket': ticket,
                'symbol': symbol,
                'type': 'BUY' if order_type == OrderType.BUY else 'SELL',
                'volume': lot_size,
                'price_open': entry_price,
                'price_current': entry_price,  # Will be updated as market moves
                'sl': sl_price,
                'tp': tp_price if tp_price else 0.0,
                'profit': 0.0,  # Will be calculated
                'swap': 0.0,
                'time_open': current_time,
                'comment': comment,
                '_entry_slippage': slippage,
                '_base_price': base_price
            }
            
            self.positions[ticket] = position
            self.execution_metrics['orders_placed'] += 1
            self.execution_metrics['orders_filled'] += 1
            self.execution_metrics['total_slippage'] += abs(slippage)
            
            # Calculate spread cost
            spread = (tick_ask - tick_bid) * self.spread_multiplier
            spread_cost = spread * lot_size * symbol_info.get('contract_size', 100000)
            
            # CRITICAL FIX: Validate spread cost is not NaN
            if math.isnan(spread_cost):
                spread_cost = 0.0
                logger.warning(f"Invalid spread cost calculated for {symbol}, using 0.0")
            
            self.execution_metrics['total_spread_cost'] += spread_cost
            
            logger.info(f"SIMULATED ORDER PLACED: Ticket {ticket} | {symbol} {position['type']} | "
                       f"Lot: {lot_size} | Entry: {entry_price:.5f} | SL: {sl_price:.5f} | "
                       f"Slippage: {slippage:.5f} | Spread Cost: ${spread_cost:.2f}")
            
            return {
                'ticket': ticket,
                'entry_price_actual': entry_price,
                'entry_price_requested': base_price,
                'slippage': slippage
            }
    
    def modify_order(self, ticket: int, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    stop_loss_price: Optional[float] = None,
                    take_profit_price: Optional[float] = None) -> bool:
        """
        Simulate order modification with MT5 error simulation.
        
        This method validates modifications exactly like MT5 does and returns
        False for rejections (errors 10013, 10029, etc.)
        """
        with self._lock:
            if ticket not in self.positions:
                logger.warning(f"Cannot modify order: Ticket {ticket} not found")
                return False
            
            position = self.positions[ticket]
            symbol = position['symbol']
            
            # Get symbol info for calculations
            symbol_info = self.market_data_provider.get_symbol_info(symbol)
            if symbol_info is None:
                return False
            
            point = symbol_info.get('point', 0.00001)
            pip_value = point * 10 if symbol_info.get('digits', 5) == 5 else point
            digits = symbol_info.get('digits', 5)
            stops_level = symbol_info.get('trade_stops_level', 0)
            freeze_level = symbol_info.get('trade_freeze_level', 0)
            
            # Get current market price for validation
            tick = self.market_data_provider.get_symbol_info_tick(symbol)
            if not tick:
                logger.warning(f"Cannot modify order: No tick data for {symbol}")
                return False
            
            # Calculate new stop loss price
            new_sl = None
            if stop_loss_price is not None:
                new_sl = stop_loss_price
            elif stop_loss is not None:
                entry_price = position['price_open']
                if position['type'] == 'BUY':
                    new_sl = entry_price - (stop_loss * pip_value)
                else:  # SELL
                    new_sl = entry_price + (stop_loss * pip_value)
            
            # CRITICAL: Validate new SL like MT5 does
            if new_sl is not None:
                # Normalize to point precision
                new_sl = round(new_sl / point) * point
                new_sl = round(new_sl, digits)
                
                # CRITICAL FIX: Handle both dict and object tick formats
                tick_bid = tick.get('bid') if isinstance(tick, dict) else getattr(tick, 'bid', None)
                tick_ask = tick.get('ask') if isinstance(tick, dict) else getattr(tick, 'ask', None)
                
                # Validate tick data
                if tick_bid is None or tick_ask is None or math.isnan(tick_bid) or math.isnan(tick_ask):
                    logger.error(f"Cannot modify order: Invalid tick data for {symbol} Ticket {ticket}")
                    return False
                
                # Validate stops level
                if stops_level > 0:
                    min_distance = stops_level * point
                    if position['type'] == 'BUY':
                        current_price = tick_bid
                        actual_distance = current_price - new_sl
                    else:  # SELL
                        current_price = tick_ask
                        actual_distance = new_sl - current_price
                    
                    if actual_distance < min_distance:
                        logger.error(f"SL modification rejected: Too close (error 10016) for {symbol} Ticket {ticket}")
                        logger.error(f"   Distance: {actual_distance:.5f}, Required: {min_distance:.5f}")
                        return False
                
                # CRITICAL: Validate freeze level (error 10029: Too close to market)
                if freeze_level > 0:
                    if position['type'] == 'BUY':
                        current_price = tick_bid
                        min_allowed_sl = current_price - (freeze_level * point)
                        if new_sl > min_allowed_sl:
                            logger.error(f"SL modification rejected: Too close to market (error 10029) for {symbol} Ticket {ticket}")
                            logger.error(f"   SL: {new_sl:.5f}, Min allowed: {min_allowed_sl:.5f}, Freeze level: {freeze_level}")
                            return False
                    else:  # SELL
                        current_price = tick_ask
                        min_allowed_sl = current_price + (freeze_level * point)
                        if new_sl < min_allowed_sl:
                            logger.error(f"SL modification rejected: Too close to market (error 10029) for {symbol} Ticket {ticket}")
                            logger.error(f"   SL: {new_sl:.5f}, Min allowed: {min_allowed_sl:.5f}, Freeze level: {freeze_level}")
                            return False
                
                # CRITICAL: Validate that new SL is better than current (error 10013: Invalid request)
                current_sl = position.get('sl', 0.0)
                if current_sl > 0:
                    if position['type'] == 'BUY':
                        # For BUY, higher SL is better (closer to entry = more profit locked)
                        if new_sl < current_sl:
                            logger.error(f"[ERROR] SL modification rejected: Would worsen SL (error 10013) for {symbol} Ticket {ticket}")
                            logger.error(f"   Current SL: {current_sl:.5f}, New SL: {new_sl:.5f}")
                            return False
                    else:  # SELL
                        # For SELL, lower SL is better (closer to entry = more profit locked)
                        if new_sl > current_sl:
                            logger.error(f"[ERROR] SL modification rejected: Would worsen SL (error 10013) for {symbol} Ticket {ticket}")
                            logger.error(f"   Current SL: {current_sl:.5f}, New SL: {new_sl:.5f}")
                            return False
                
                # Update stop loss
                position['sl'] = new_sl
            
            # Update take profit
            if take_profit_price is not None:
                position['tp'] = take_profit_price
            elif take_profit is not None:
                entry_price = position['price_open']
                if position['type'] == 'BUY':
                    position['tp'] = entry_price + (take_profit * pip_value)
                else:  # SELL
                    position['tp'] = entry_price - (take_profit * pip_value)
                
                # Normalize TP
                if position['tp'] > 0:
                    position['tp'] = round(position['tp'] / point) * point
                    position['tp'] = round(position['tp'], digits)
            
            self.execution_metrics['modifications'] += 1
            logger.debug(f"[OK] SIMULATED ORDER MODIFIED: Ticket {ticket} | SL: {position['sl']:.5f} | TP: {position['tp']:.5f}")
            
            return True
    
    def get_open_positions(self, exclude_dec8: bool = True) -> List[Dict[str, Any]]:
        """Get all open simulated positions."""
        with self._lock:
            # Update current prices and profit for all positions
            current_time = self.market_data_provider.get_current_time()
            open_positions = []
            
            for ticket, position in list(self.positions.items()):
                # Update current price
                symbol = position['symbol']
                tick = self.market_data_provider.get_symbol_info_tick(symbol)
                if tick:
                    # CRITICAL FIX: Handle both dict and object tick formats
                    tick_bid = tick.get('bid') if isinstance(tick, dict) else getattr(tick, 'bid', None)
                    tick_ask = tick.get('ask') if isinstance(tick, dict) else getattr(tick, 'ask', None)
                    
                    # Validate tick data
                    if tick_bid is not None and tick_ask is not None and not math.isnan(tick_bid) and not math.isnan(tick_ask):
                        if position['type'] == 'BUY':
                            position['price_current'] = tick_bid
                        else:  # SELL
                            position['price_current'] = tick_ask
                        
                        # Calculate profit
                        entry_price = position['price_open']
                        current_price = position['price_current']
                        volume = position['volume']
                        contract_size = 100000  # Default
                        
                        symbol_info = self.market_data_provider.get_symbol_info(symbol)
                        if symbol_info:
                            contract_size = symbol_info.get('contract_size', 100000)
                        
                        if position['type'] == 'BUY':
                            price_diff = current_price - entry_price
                        else:  # SELL
                            price_diff = entry_price - current_price
                        
                        profit = price_diff * volume * contract_size
                        # CRITICAL FIX: Validate profit is not NaN
                        if not math.isnan(profit):
                            position['profit'] = profit
                        else:
                            position['profit'] = 0.0
                
                open_positions.append(position.copy())
            
            return open_positions
    
    def get_position_by_ticket(self, ticket: int, exclude_dec8: bool = True) -> Optional[Dict[str, Any]]:
        """Get position by ticket number."""
        with self._lock:
            if ticket not in self.positions:
                return None
            
            position = self.positions[ticket]
            
            # Update current price and profit
            symbol = position['symbol']
            tick = self.market_data_provider.get_symbol_info_tick(symbol)
            if tick:
                # CRITICAL FIX: Handle both dict and object tick formats
                tick_bid = tick.get('bid') if isinstance(tick, dict) else getattr(tick, 'bid', None)
                tick_ask = tick.get('ask') if isinstance(tick, dict) else getattr(tick, 'ask', None)
                
                # Validate tick data
                if tick_bid is None or tick_ask is None or math.isnan(tick_bid) or math.isnan(tick_ask):
                    # Use cached position data if tick is invalid
                    logger.warning(f"Invalid tick data for {symbol} Ticket {ticket}, using cached price")
                    return position.copy()
                
                if position['type'] == 'BUY':
                    position['price_current'] = tick_bid
                else:  # SELL
                    position['price_current'] = tick_ask
                
                # Calculate profit
                entry_price = position['price_open']
                current_price = position['price_current']
                volume = position['volume']
                contract_size = 100000
                
                symbol_info = self.market_data_provider.get_symbol_info(symbol)
                if symbol_info:
                    contract_size = symbol_info.get('contract_size', 100000)
                
                if position['type'] == 'BUY':
                    price_diff = current_price - entry_price
                else:  # SELL
                    price_diff = entry_price - current_price
                
                profit = price_diff * volume * contract_size
                # CRITICAL FIX: Validate profit is not NaN
                if not math.isnan(profit):
                    position['profit'] = profit
                else:
                    position['profit'] = 0.0
                    logger.warning(f"Invalid profit calculated for {symbol} Ticket {ticket}, using 0.0")
            
            return position.copy()
    
    def close_position(self, ticket: int, comment: str = None) -> bool:
        """
        Close a simulated position.
        
        CRITICAL: This method should ONLY be called for manual closures or when we're certain
        the position should be closed. For SL/TP hits, use check_sl_tp_hits() instead.
        
        If position is being closed and SL/TP was hit, use SL/TP price for profit calculation.
        Otherwise, use current market price.
        
        WARNING: If a position was already closed by check_sl_tp_hits(), this will return False
        to prevent duplicate closures.
        """
        with self._lock:
            # CRITICAL: Check if position was already closed by check_sl_tp_hits()
            if ticket in self._positions_closed_by_check:
                logger.warning(f"[WARNING] Attempted to close position {ticket} that was already closed by check_sl_tp_hits() | "
                             f"Ignoring duplicate close request | Comment: {comment}")
                return False
            
            if ticket not in self.positions:
                # Position might have been closed by check_sl_tp_hits() already
                if ticket in self._closure_reasons:
                    logger.debug(f"Position {ticket} already closed (reason: {self._closure_reasons.get(ticket, 'UNKNOWN')})")
                return False
            
            position = self.positions.pop(ticket)
            symbol = position['symbol']
            entry_price = position['price_open']
            volume = position['volume']
            sl_price = position.get('sl', 0.0)
            tp_price = position.get('tp', 0.0)
            
            # Get symbol info for contract size
            symbol_info = self.market_data_provider.get_symbol_info(symbol)
            contract_size = 100000  # Default
            if symbol_info:
                contract_size = symbol_info.get('contract_size', 100000)
            
            # CRITICAL FIX: ALWAYS check if SL/TP was hit before closing
            # If SL/TP was hit, use SL/TP price for profit calculation (ensures exact $2.00 loss)
            # This is essential because positions might be closed by other mechanisms (SL worker, position monitor)
            # but we must still use the SL price if it was hit, not the current market price
            tick = self.market_data_provider.get_symbol_info_tick(symbol)
            current_bar = None
            if hasattr(self.market_data_provider, '_get_current_bar'):
                current_bar = self.market_data_provider._get_current_bar(symbol)
            
            # Check if SL/TP was hit using candle high/low (most accurate for backtesting)
            hit_sl = False
            hit_tp = False
            close_price = position.get('price_current', entry_price)
            
            # Get candle high/low for accurate SL/TP detection
            candle_low = None
            candle_high = None
            if current_bar:
                candle_low = current_bar.get('low')
                candle_high = current_bar.get('high')
            
            # Get tick data as fallback
            tick_bid = None
            tick_ask = None
            if tick:
                tick_bid = tick.get('bid') if isinstance(tick, dict) else getattr(tick, 'bid', None)
                tick_ask = tick.get('ask') if isinstance(tick, dict) else getattr(tick, 'ask', None)
            
            # CRITICAL: Check SL/TP hits using candle high/low (preferred) or tick (fallback)
            if position['type'] == 'BUY':
                # BUY: SL below entry, TP above entry
                if sl_price > 0:
                    # Check if candle low touched SL (most accurate)
                    if candle_low is not None and not math.isnan(candle_low):
                        if candle_low <= sl_price:
                            hit_sl = True
                            close_price = sl_price
                            logger.debug(f"[SL HIT] Ticket {ticket} | BUY | Candle low {candle_low:.5f} <= SL {sl_price:.5f}")
                    # Fallback to tick if no candle data
                    elif tick_bid is not None and not math.isnan(tick_bid) and tick_bid <= sl_price:
                        hit_sl = True
                        close_price = sl_price
                        logger.debug(f"[SL HIT] Ticket {ticket} | BUY | Tick bid {tick_bid:.5f} <= SL {sl_price:.5f}")
                
                if not hit_sl and tp_price > 0:
                    # Check if candle high touched TP
                    if candle_high is not None and not math.isnan(candle_high):
                        if candle_high >= tp_price:
                            hit_tp = True
                            close_price = tp_price
                            logger.debug(f"[TP HIT] Ticket {ticket} | BUY | Candle high {candle_high:.5f} >= TP {tp_price:.5f}")
                    # Fallback to tick if no candle data
                    elif tick_bid is not None and not math.isnan(tick_bid) and tick_bid >= tp_price:
                        hit_tp = True
                        close_price = tp_price
                        logger.debug(f"[TP HIT] Ticket {ticket} | BUY | Tick bid {tick_bid:.5f} >= TP {tp_price:.5f}")
            else:  # SELL
                # SELL: SL above entry, TP below entry
                if sl_price > 0:
                    # Check if candle high touched SL (most accurate)
                    if candle_high is not None and not math.isnan(candle_high):
                        if candle_high >= sl_price:
                            hit_sl = True
                            close_price = sl_price
                            logger.debug(f"[SL HIT] Ticket {ticket} | SELL | Candle high {candle_high:.5f} >= SL {sl_price:.5f}")
                    # Fallback to tick if no candle data
                    elif tick_ask is not None and not math.isnan(tick_ask) and tick_ask >= sl_price:
                        hit_sl = True
                        close_price = sl_price
                        logger.debug(f"[SL HIT] Ticket {ticket} | SELL | Tick ask {tick_ask:.5f} >= SL {sl_price:.5f}")
                
                if not hit_sl and tp_price > 0:
                    # Check if candle low touched TP
                    if candle_low is not None and not math.isnan(candle_low):
                        if candle_low <= tp_price:
                            hit_tp = True
                            close_price = tp_price
                            logger.debug(f"[TP HIT] Ticket {ticket} | SELL | Candle low {candle_low:.5f} <= TP {tp_price:.5f}")
                    # Fallback to tick if no candle data
                    elif tick_ask is not None and not math.isnan(tick_ask) and tick_ask <= tp_price:
                        hit_tp = True
                        close_price = tp_price
                        logger.debug(f"[TP HIT] Ticket {ticket} | SELL | Tick ask {tick_ask:.5f} <= TP {tp_price:.5f}")
            
            # Calculate profit using close_price (SL/TP if hit, otherwise current price)
            if position['type'] == 'BUY':
                price_diff = close_price - entry_price
            else:  # SELL
                price_diff = entry_price - close_price
            
            profit = price_diff * volume * contract_size
            
            # Validate profit is not NaN
            if math.isnan(profit):
                profit = 0.0
            
            close_reason_str = ""
            if hit_sl:
                close_reason_str = " (SL hit)"
            elif hit_tp:
                close_reason_str = " (TP hit)"
            
            # CRITICAL: Mark closure reason
            closure_reason = 'SL' if hit_sl else ('TP' if hit_tp else 'MANUAL')
            self._closure_reasons[ticket] = closure_reason
            
            comment_str = f" ({comment})" if comment else ""
            logger.info(f"[OK] SIMULATED POSITION CLOSED: Ticket {ticket} | {position['symbol']} | "
                       f"Profit: ${profit:.2f}{close_reason_str}{comment_str} | "
                       f"Closure method: close_position() | "
                       f"SL hit: {hit_sl} | TP hit: {hit_tp}")
            
            return True
    
    def check_sl_tp_hits(self) -> List[Dict[str, Any]]:
        """
        Check if any positions hit SL or TP.
        
        CRITICAL: For backtesting, we must check if the candle's LOW (for BUY) or HIGH (for SELL)
        touched the SL/TP, not just the current close price. This ensures accurate SL enforcement.
        
        Returns:
            List of closed positions with closure reason
        """
        closed_positions = []
        
        with self._lock:
            for ticket, position in list(self.positions.items()):
                symbol = position['symbol']
                
                # CRITICAL FIX: Get current candle data (not just tick) to check high/low
                # This is essential for accurate SL enforcement in backtesting
                current_bar = None
                if hasattr(self.market_data_provider, '_get_current_bar'):
                    current_bar = self.market_data_provider._get_current_bar(symbol)
                
                # Fallback to tick if candle data not available
                tick = self.market_data_provider.get_symbol_info_tick(symbol)
                if not tick and not current_bar:
                    continue
                
                # CRITICAL FIX: Handle both dict and object tick formats
                tick_bid = None
                tick_ask = None
                if tick:
                    tick_bid = tick.get('bid') if isinstance(tick, dict) else getattr(tick, 'bid', None)
                    tick_ask = tick.get('ask') if isinstance(tick, dict) else getattr(tick, 'ask', None)
                
                # Get candle high/low if available
                candle_low = None
                candle_high = None
                if current_bar:
                    candle_low = current_bar.get('low')
                    candle_high = current_bar.get('high')
                    # Use tick as fallback if candle data incomplete
                    if tick_bid is None and candle_low:
                        tick_bid = candle_low
                    if tick_ask is None and candle_high:
                        tick_ask = candle_high
                
                # Validate we have price data
                if tick_bid is None or tick_ask is None or math.isnan(tick_bid) or math.isnan(tick_ask):
                    continue
                
                sl_price = position.get('sl', 0.0)
                tp_price = position.get('tp', 0.0)
                entry_price = position['price_open']
                
                # CRITICAL FIX: Check SL/TP hits using candle high/low, not just close price
                # For BUY: Check if candle LOW touched SL (price went below SL)
                # For SELL: Check if candle HIGH touched SL (price went above SL)
                hit_sl = False
                hit_tp = False
                close_reason = ""
                
                if position['type'] == 'BUY':
                    # BUY: SL below entry, TP above entry
                    # Check if candle low touched SL (most accurate for backtesting)
                    if sl_price > 0:
                        if candle_low is not None and not math.isnan(candle_low):
                            # Use candle low for accurate SL check
                            if candle_low <= sl_price:
                                hit_sl = True
                                close_reason = "SL"
                        elif tick_bid <= sl_price:
                            # Fallback to tick if no candle data
                            hit_sl = True
                            close_reason = "SL"
                    
                    if not hit_sl and tp_price > 0:
                        if candle_high is not None and not math.isnan(candle_high):
                            # Use candle high for TP check
                            if candle_high >= tp_price:
                                hit_tp = True
                                close_reason = "TP"
                        elif tick_bid >= tp_price:
                            # Fallback to tick if no candle data
                            hit_tp = True
                            close_reason = "TP"
                else:  # SELL
                    # SELL: SL above entry, TP below entry
                    # Check if candle high touched SL (most accurate for backtesting)
                    if sl_price > 0:
                        if candle_high is not None and not math.isnan(candle_high):
                            # Use candle high for accurate SL check
                            if candle_high >= sl_price:
                                hit_sl = True
                                close_reason = "SL"
                        elif tick_ask >= sl_price:
                            # Fallback to tick if no candle data
                            hit_sl = True
                            close_reason = "SL"
                    
                    if not hit_sl and tp_price > 0:
                        if candle_low is not None and not math.isnan(candle_low):
                            # Use candle low for TP check
                            if candle_low <= tp_price:
                                hit_tp = True
                                close_reason = "TP"
                        elif tick_ask <= tp_price:
                            # Fallback to tick if no candle data
                            hit_tp = True
                            close_reason = "TP"
                
                if hit_sl or hit_tp:
                    # CRITICAL: Close at SL/TP price, not current market price
                    # This ensures exact $2.00 loss when SL is hit
                    if position['type'] == 'BUY':
                        position['price_current'] = sl_price if hit_sl else tp_price
                    else:  # SELL
                        position['price_current'] = sl_price if hit_sl else tp_price
                    
                    # Calculate final profit using SL/TP price
                    entry_price = position['price_open']
                    current_price = position['price_current']
                    volume = position['volume']
                    contract_size = 100000
                    
                    symbol_info = self.market_data_provider.get_symbol_info(symbol)
                    if symbol_info:
                        contract_size = symbol_info.get('contract_size', 100000)
                    
                    if position['type'] == 'BUY':
                        price_diff = current_price - entry_price
                    else:  # SELL
                        price_diff = entry_price - current_price
                    
                    profit = price_diff * volume * contract_size
                    position['profit'] = profit
                    
                    # CRITICAL: Mark this position as closed by check_sl_tp_hits()
                    # This prevents other mechanisms from closing it prematurely
                    self._positions_closed_by_check.add(ticket)
                    self._closure_reasons[ticket] = close_reason
                    
                    # Log SL/TP hit for debugging
                    logger.info(f"[SL/TP HIT] Ticket {ticket} | {symbol} {position['type']} | "
                               f"Reason: {close_reason} | Entry: {entry_price:.5f} | "
                               f"Close: {current_price:.5f} | Profit: ${profit:.2f}")
                    
                    closed_positions.append({
                        'ticket': ticket,
                        'position': position.copy(),
                        'close_reason': close_reason,
                        'close_price': position['price_current'],
                        'profit': profit
                    })
                    
                    # Remove from open positions
                    del self.positions[ticket]
        
        return closed_positions
    
    def get_execution_metrics(self) -> Dict[str, Any]:
        """Get execution metrics."""
        with self._lock:
            return self.execution_metrics.copy()

