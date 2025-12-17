"""
Synthetic MT5 Module Wrapper
Module-level mock for MetaTrader5 module that redirects calls to SyntheticBroker.
"""

from typing import Optional, Dict, Any, List


class SyntheticMT5Wrapper:
    """
    Mock MT5 module that redirects calls to SyntheticBroker.
    
    This is injected at the module level in order_manager.py to intercept
    direct mt5.order_send() calls.
    """
    
    def __init__(self, broker):
        """
        Initialize MT5 wrapper.
        
        Args:
            broker: SyntheticBroker instance
        """
        self.broker = broker
        self._last_error_info = None
    
    # MT5 Constants (matching MetaTrader5 module)
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 5
    TRADE_ACTION_MODIFY = 4
    TRADE_ACTION_REMOVE = 3
    
    # CRITICAL FIX: Match real MT5 order type values
    # Real MT5: ORDER_TYPE_BUY = 0, ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PARTIAL = 10008
    TRADE_RETCODE_REJECT = 10004
    TRADE_RETCODE_INVALID = 10016
    TRADE_RETCODE_MARKET_CLOSED = 10018
    TRADE_RETCODE_INVALID_VOLUME = 10014
    TRADE_RETCODE_TRADE_DISABLED = 10027
    TRADE_RETCODE_TOO_MANY_REQUESTS = 10029
    TRADE_RETCODE_NO_MONEY = 10019
    TRADE_RETCODE_PRICE_CHANGED = 10027
    TRADE_RETCODE_PRICE_OFF = 10016
    TRADE_RETCODE_INVALID_STOPS = 10016
    TRADE_RETCODE_TRADE_RESTRICTED = 10044
    TRADE_RETCODE_NETWORK_ERROR = 10031
    
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    
    ORDER_TIME_GTC = 0
    ORDER_TIME_DAY = 1
    ORDER_TIME_SPECIFIED = 2
    ORDER_TIME_SPECIFIED_DAY = 3
    
    ORDER_FILLING_FOK = 1
    ORDER_FILLING_IOC = 2
    ORDER_FILLING_RETURN = 4
    
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408
    
    def order_send(self, request: Dict[str, Any]):
        """
        Send order request (redirects to SyntheticBroker).
        
        Args:
            request: Order request dict
        
        Returns:
            TradeResult object (matching MT5 order_send result format)
        """
        result = self.broker.order_send(request)
        
        # Store last error if failed
        if result.retcode != self.TRADE_RETCODE_DONE and result.retcode != self.TRADE_RETCODE_PARTIAL:
            self._last_error_info = (result.retcode, result.comment)
        else:
            self._last_error_info = None
        
        return result
    
    def history_deals_get(self, ticket: Optional[int] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Get deal history (redirects to SyntheticBroker).
        
        Args:
            ticket: Optional ticket filter
            **kwargs: Additional filters (ignored in simulation)
        
        Returns:
            List of deal dicts
        """
        return self.broker.history_deals_get(ticket=ticket)
    
    def last_error(self):
        """Get last error (returns tuple like MT5.last_error())."""
        if self._last_error_info:
            return self._last_error_info
        return (1, 'RES_S_OK')  # Success
    
    # Placeholder methods (not used by OrderManager but may be called elsewhere)
    def initialize(self, path: str = ""):
        """Initialize (no-op in simulation)."""
        return True
    
    def login(self, login: int, password: str, server: str, timeout: int = 60000):
        """Login (no-op in simulation)."""
        return True
    
    def shutdown(self):
        """Shutdown (no-op in simulation)."""
        pass
    
    def account_info(self):
        """Get account info (returns broker account info)."""
        account_dict = self.broker.get_account_info()
        
        # Create account-like object
        class AccountInfo:
            def __init__(self, data):
                for key, value in data.items():
                    setattr(self, key, value)
        
        return AccountInfo(account_dict)
    
    def terminal_info(self):
        """Get terminal info (returns minimal info)."""
        class TerminalInfo:
            def __init__(self):
                self.name = "SIM_LIVE"
                self.company = "Synthetic Trading"
                self.path = ""
                self.data_path = ""
        
        return TerminalInfo()
    
    def symbol_info(self, symbol: str):
        """Get symbol info (returns None - use connector's get_symbol_info instead)."""
        return None
    
    def symbol_info_tick(self, symbol: str):
        """Get tick info (returns None - use connector's get_symbol_info_tick instead)."""
        return None
    
    def positions_get(self, symbol: Optional[str] = None):
        """Get positions (redirects to broker)."""
        positions = self.broker.positions_get(symbol=symbol)
        
        # Convert to position-like objects
        class Position:
            def __init__(self, pos_dict):
                for key, value in pos_dict.items():
                    setattr(self, key, value)
        
        return [Position(p.to_dict()) for p in positions]
    
    def position_get(self, ticket: int):
        """Get position by ticket."""
        position = self.broker.position_get(ticket)
        if position:
            class Position:
                def __init__(self, pos_dict):
                    for key, value in pos_dict.items():
                        setattr(self, key, value)
            return Position(position.to_dict())
        return None
    
    def copy_rates_from_pos(self, symbol: str, timeframe: int, offset: int, count: int):
        """Copy rates (returns None - use connector's copy_rates_from_pos instead)."""
        return None
    
    def symbols_get(self):
        """
        Get symbols from active scenario.
        
        Returns list of symbol objects matching MT5 format (with .name and .visible attributes).
        """
        # Access scenario from market engine via broker
        if not hasattr(self.broker, 'market_engine'):
            return []
        
        market_engine = self.broker.market_engine
        
        # Get active scenario symbol
        with market_engine._scenario_lock:
            scenario = market_engine._scenario
        
        if not scenario:
            return []
        
        symbol_name = scenario.get('symbol', 'EURUSD')
        
        # Create a simple class that mimics MT5 SymbolInfo object
        class SymbolInfo:
            def __init__(self, name):
                self.name = name
                self.visible = True  # Symbols are visible/tradeable
                self.trade_mode = 4  # SYMBOL_TRADE_MODE_FULL (matching real MT5)
                self.spread = 2  # Default spread in points
                self.swap_mode = 0  # Swap-free by default
                self.swap_long = 0
                self.swap_short = 0
        
        # Return list with scenario symbol
        return [SymbolInfo(symbol_name)]


def inject_mt5_mock(broker):
    """
    Create and inject MT5 mock into modules that import MetaTrader5.
    
    This ensures all modules see the synthetic MT5 wrapper instead of the real one.
    Uses sys.modules to intercept MetaTrader5 imports globally.
    
    Args:
        broker: SyntheticBroker instance
    
    Returns:
        SyntheticMT5Wrapper instance
    """
    import sys
    wrapper = SyntheticMT5Wrapper(broker)
    
    # Inject into sys.modules to intercept all MetaTrader5 imports
    # This ensures both module-level and function-level imports use our wrapper
    sys.modules['MetaTrader5'] = wrapper
    
    # Also inject explicitly into modules that are already loaded
    import execution.order_manager as om_module
    om_module.mt5 = wrapper
    
    import risk.pair_filter as pf_module
    pf_module.mt5 = wrapper
    
    return wrapper

