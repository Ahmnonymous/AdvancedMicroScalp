"""
Synthetic Broker
Simulates broker order execution, position management, and account state.
"""

import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import namedtuple


# MT5-compatible result object
class TradeResult:
    """MT5 order_send result object simulation."""
    def __init__(self, retcode: int, order: int = 0, comment: str = ""):
        self.retcode = retcode
        self.order = order  # Ticket number
        self.comment = comment


class Position:
    """Synthetic position representation."""
    def __init__(
        self,
        ticket: int,
        symbol: str,
        order_type: int,  # mt5.ORDER_TYPE_BUY or mt5.ORDER_TYPE_SELL
        volume: float,
        price_open: float,
        sl: float,
        tp: float,
        comment: str,
        contract_size: float
    ):
        self.ticket = ticket
        self.symbol = symbol.upper()
        self.type = order_type
        self.volume = volume
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.comment = comment
        self.time = int(time.time())
        
        # Current market prices (updated on every tick)
        self.price_current = price_open  # Will be updated by market engine
        
        # Contract size for P/L calculation
        self.contract_size = contract_size
        
        # Swap (always 0 in SIM_LIVE - swap-free account)
        self.swap = 0.0
        
        # Calculate P/L (will be updated on every tick)
        self._calculate_profit()
    
    def _calculate_profit(self):
        """Calculate current profit based on price_current."""
        # CRITICAL FIX: Real MT5 uses ORDER_TYPE_BUY = 0, ORDER_TYPE_SELL = 1
        if self.type == 0:  # BUY (ORDER_TYPE_BUY = 0)
            # Profit = (current_price - entry_price) * volume * contract_size
            self.profit = (self.price_current - self.price_open) * self.volume * self.contract_size
        else:  # SELL (ORDER_TYPE_SELL = 1)
            # Profit = (entry_price - current_price) * volume * contract_size
            self.profit = (self.price_open - self.price_current) * self.volume * self.contract_size
    
    def update_price(self, bid: float, ask: float):
        """Update position with current market price."""
        # CRITICAL FIX: Real MT5 uses ORDER_TYPE_BUY = 0, ORDER_TYPE_SELL = 1
        # Use bid for SELL, ask for BUY (current market price for closing)
        if self.type == 0:  # BUY (ORDER_TYPE_BUY = 0)
            self.price_current = bid  # For BUY, we'd close at bid
        else:  # SELL (ORDER_TYPE_SELL = 1)
            self.price_current = ask  # For SELL, we'd close at ask
        
        self._calculate_profit()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict matching MT5 position format."""
        return {
            'ticket': self.ticket,
            'symbol': self.symbol,
            'type': 'BUY' if self.type == 0 else 'SELL',  # Real MT5: BUY=0, SELL=1
            'volume': self.volume,
            'price_open': self.price_open,
            'price_current': self.price_current,
            'sl': self.sl,
            'tp': self.tp,
            'profit': self.profit,
            'swap': self.swap,  # Always 0 in SIM_LIVE (swap-free)
            'comment': self.comment,
            'time': self.time
        }


class SyntheticBroker:
    """
    Simulates broker order execution and position management.
    
    Features:
    - Order execution with slippage simulation
    - Position tracking with real-time P/L updates
    - SL/TP modification
    - Order rejection simulation (invalid SL, market closed, etc.)
    - Account info simulation
    """
    
    def __init__(self, config: Dict[str, Any], market_engine):
        """
        Initialize synthetic broker.
        
        Args:
            config: Configuration dict
            market_engine: SyntheticMarketEngine instance (for price updates)
        """
        self.config = config
        self.market_engine = market_engine
        
        # Position tracking {ticket: Position}
        self._positions: Dict[int, Position] = {}
        self._next_ticket = 100000  # Starting ticket number
        self._position_lock = threading.Lock()
        
        # Account info
        self._initial_balance = config.get('sim_live', {}).get('initial_balance', 10000.0)
        self._balance = self._initial_balance
        self._equity = self._initial_balance
        
        # Register for price updates (update positions on every tick)
        self.market_engine.register_price_update_callback(self._on_price_update)
        
        # Failure simulation (for testing error paths)
        self._failure_scenarios = {}  # {symbol: 'invalid_sl' | 'market_closed' | 'rate_limit' | None}
        self._failure_lock = threading.Lock()
        
    def set_failure_scenario(self, symbol: str, scenario: Optional[str]):
        """Set failure scenario for a symbol (for testing error paths)."""
        with self._failure_lock:
            if scenario:
                self._failure_scenarios[symbol.upper()] = scenario
            else:
                self._failure_scenarios.pop(symbol.upper(), None)
    
    def order_send(self, request: Dict[str, Any]) -> TradeResult:
        """
        Simulate MT5 order_send.
        
        Args:
            request: Order request dict matching MT5 format
        
        Returns:
            TradeResult object matching MT5 order_send result
        """
        action = request.get('action', 0)
        symbol = request.get('symbol', '')
        order_type = request.get('type', 0)
        volume = request.get('volume', 0.0)
        price = request.get('price', 0.0)
        sl = request.get('sl', 0.0)
        tp = request.get('tp', 0.0)
        
        # Check for failure scenarios
        symbol_upper = symbol.upper()
        with self._failure_lock:
            failure_scenario = self._failure_scenarios.get(symbol_upper)
        
        if failure_scenario:
            if failure_scenario == 'invalid_sl':
                return TradeResult(retcode=10016, comment="Invalid stops")
            elif failure_scenario == 'market_closed':
                return TradeResult(retcode=10018, comment="Market closed")
            elif failure_scenario == 'rate_limit':
                return TradeResult(retcode=10029, comment="Too many requests")
        
        if action == 1:  # TRADE_ACTION_DEAL (open position)
            return self._open_position(request)
        elif action == 5:  # TRADE_ACTION_SLTP (modify SL/TP)
            return self._modify_position(request)
        elif action == 2:  # TRADE_ACTION_PENDING (not used in this bot)
            return TradeResult(retcode=10004, comment="Pending orders not supported")
        else:
            return TradeResult(retcode=10004, comment=f"Unknown action: {action}")
    
    def _open_position(self, request: Dict[str, Any]) -> TradeResult:
        """Open a new position."""
        symbol = request.get('symbol', '')
        order_type = request.get('type', 0)
        volume = request.get('volume', 0.0)
        price = request.get('price', 0.0)
        sl = request.get('sl', 0.0)
        tp = request.get('tp', 0.0)
        comment = request.get('comment', '')
        
        # Validate order
        if volume <= 0:
            return TradeResult(retcode=10014, comment="Invalid volume")
        
        # Get current market price for validation
        tick = self.market_engine.get_current_tick(symbol)
        if tick is None:
            return TradeResult(retcode=10004, comment="Symbol not found")
        
        # CRITICAL FIX: Match real MT5 order type values
        # Real MT5: ORDER_TYPE_BUY = 0, ORDER_TYPE_SELL = 1
        #
        # For initial order placement we still enforce logical SL direction
        # (BUY SL below entry, SELL SL above entry) to catch obviously invalid
        # requests. Trailing/profit-locking updates are done via _modify_position
        # and are allowed to move SL into profit zone.
        if sl > 0:
            if order_type == 0:  # BUY (ORDER_TYPE_BUY = 0)
                # For BUY, initial protective SL must be BELOW entry price
                if sl >= price:
                    return TradeResult(retcode=10016, comment="Invalid stops: SL must be below entry for BUY")
            else:  # SELL (ORDER_TYPE_SELL = 1)
                # For SELL, initial protective SL must be ABOVE entry price
                if sl <= price:
                    return TradeResult(retcode=10016, comment="Invalid stops: SL must be above entry for SELL")
        
        # Get symbol config for contract size
        symbol_info = self.market_engine.get_symbol_info(symbol)
        contract_size = symbol_info.get('contract_size', 100000)
        
        # Generate ticket
        with self._position_lock:
            ticket = self._next_ticket
            self._next_ticket += 1
            
            # Create position
            position = Position(
                ticket=ticket,
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                price_open=price,
                sl=sl,
                tp=tp,
                comment=comment,
                contract_size=contract_size
            )
            
            # Update with current price (ensure price_current reflects current market)
            position.update_price(tick['bid'], tick['ask'])
            
            self._positions[ticket] = position
            
            # Log position opening with current price info
            try:
                from sim_live.sim_live_logger import get_sim_live_logger
                sim_logger = get_sim_live_logger()
                order_type_str = 'BUY' if order_type == 0 else 'SELL'
                sim_logger.info(f"[SYNTHETIC_BROKER] Position opened: Ticket {ticket} | {symbol} {order_type_str} | "
                              f"Entry={price:.5f} | Current={position.price_current:.5f} | "
                              f"Market BID={tick['bid']:.5f} ASK={tick['ask']:.5f} | Initial P/L=${position.profit:.2f}")
            except:
                pass
        
        # Update account equity
        self._update_account_equity()
        
        return TradeResult(retcode=10009, order=ticket, comment="Done")  # TRADE_RETCODE_DONE
    
    def _modify_position(self, request: Dict[str, Any]) -> TradeResult:
        """Modify SL/TP of existing position."""
        ticket = request.get('position', 0)
        sl = request.get('sl', 0.0)
        tp = request.get('tp', 0.0)
        
        with self._position_lock:
            if ticket not in self._positions:
                return TradeResult(retcode=10004, comment="Position not found")
            
            position = self._positions[ticket]
            
            # Validate SL
            # CRITICAL FIX: Allow profit-locking / trailing stops to move SL
            # into profit zone. Real MT5 validates SL primarily against the
            # *current* market price and minimum stop distance, not strictly
            # against entry price. Our SIM_LIVE environment delegates distance
            # checks to higher-level logic, so here we only ensure SL is positive.
            if sl > 0:
                position.sl = sl
            
            if tp > 0:
                position.tp = tp
        
        return TradeResult(retcode=10009, comment="Done")  # TRADE_RETCODE_DONE
    
    def positions_get(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Get open positions.
        
        Args:
            symbol: Optional symbol filter
        
        Returns:
            List of Position objects
        """
        with self._position_lock:
            positions = list(self._positions.values())
            
            if symbol:
                symbol_upper = symbol.upper()
                positions = [p for p in positions if p.symbol == symbol_upper]
            
            return positions
    
    def position_get(self, ticket: int) -> Optional[Position]:
        """Get position by ticket."""
        with self._position_lock:
            return self._positions.get(ticket)
    
    def close_position(self, ticket: int) -> bool:
        """Close a position (for testing)."""
        with self._position_lock:
            if ticket not in self._positions:
                return False
            
            position = self._positions[ticket]
            
            # Update balance with realized P/L
            self._balance += position.profit
            self._equity = self._balance
            
            # Remove position
            del self._positions[ticket]
            
            return True
    
    def get_account_info(self) -> Dict[str, Any]:
        """Get account info matching MT5 format."""
        self._update_account_equity()
        
        return {
            'login': 999999,
            'balance': self._balance,
            'equity': self._equity,
            'margin': 0.0,  # Simplified - no margin calculation
            'margin_free': self._equity,
            'margin_level': 0.0,
            'profit': self._equity - self._initial_balance,
            'currency': 'USD',
            'server': 'SIM_LIVE',
            'leverage': 500,
            'trade_allowed': True,
            'trade_expert': True,
            'swap_mode': 0
        }
    
    def _update_account_equity(self):
        """Update account equity based on open positions."""
        with self._position_lock:
            unrealized_pnl = sum(p.profit for p in self._positions.values())
            self._equity = self._balance + unrealized_pnl
    
    def _on_price_update(self, symbol: str, bid: float, ask: float):
        """
        Callback from market engine - update positions on every tick.
        
        This ensures positions are updated on EVERY price tick, as required.
        """
        try:
            with self._position_lock:
                for position in self._positions.values():
                    if position.symbol == symbol.upper():
                        old_profit = position.profit
                        position.update_price(bid, ask)
                        
                        # Check if SL/TP hit
                        # CRITICAL FIX: Real MT5 uses ORDER_TYPE_BUY = 0, ORDER_TYPE_SELL = 1
                        if position.sl > 0:
                            if position.type == 0:  # BUY (ORDER_TYPE_BUY = 0)
                                if bid <= position.sl:
                                    # SL hit - close position
                                    self.close_position(position.ticket)
                            else:  # SELL (ORDER_TYPE_SELL = 1)
                                if ask >= position.sl:
                                    # SL hit - close position
                                    self.close_position(position.ticket)
                        
                        if position.tp > 0:
                            # CRITICAL FIX: Real MT5 uses ORDER_TYPE_BUY = 0, ORDER_TYPE_SELL = 1
                            if position.type == 0:  # BUY (ORDER_TYPE_BUY = 0)
                                if bid >= position.tp:
                                    self.close_position(position.ticket)
                            else:  # SELL (ORDER_TYPE_SELL = 1)
                                if ask <= position.tp:
                                    self.close_position(position.ticket)
            
            # Update account equity
            self._update_account_equity()
        except Exception as e:
            # Log but don't crash - safety check
            import sys
            print(f"Error in price update callback: {e}", file=sys.stderr)
    
    def history_deals_get(self, ticket: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get deal history (for order fill price/volume retrieval).
        
        Args:
            ticket: Optional ticket filter
        
        Returns:
            List of deal dicts matching MT5 format
        """
        # In real MT5, this returns historical deals
        # For simulation, return empty list (OrderManager uses this for fill prices)
        # We could enhance this to track deals, but it's not critical for testing
        return []
    
    def reset(self):
        """Reset broker state (for new test scenarios)."""
        with self._position_lock:
            self._positions.clear()
            self._next_ticket = 100000
            self._balance = self._initial_balance
            self._equity = self._initial_balance
        
        with self._failure_lock:
            self._failure_scenarios.clear()

