"""
MT5 Connection and Reconnection Manager
Handles connection to MetaTrader 5 with automatic reconnection logic.
"""

import MetaTrader5 as mt5
import time
import logging
import threading
from typing import Optional, Dict, Any
import json

logger = logging.getLogger(__name__)


class MT5Connector:
    """Manages MT5 connection with auto-reconnect functionality."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mt5_config = config.get('mt5', {})
        self.connected = False
        self.reconnect_attempts = self.mt5_config.get('reconnect_attempts', 5)
        self.reconnect_delay = self.mt5_config.get('reconnect_delay', 5)
        
        # Symbol info caching (TTL: 5 seconds)
        self._symbol_info_cache = {}
        self._symbol_cache_ttl = 5.0  # seconds
        self._cache_lock = threading.Lock()
        
        # Price staleness check (reject prices older than 5 seconds)
        self._price_max_age_seconds = 5.0
        
    def connect(self) -> bool:
        """Connect to MT5 terminal."""
        if self.connected and mt5.terminal_info() is not None:
            return True
            
        # Try to initialize MT5
        if not mt5.initialize(path=self.mt5_config.get('path', '')):
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            return False
        
        # Try to login if credentials provided
        account = self.mt5_config.get('account', '')
        password = self.mt5_config.get('password', '')
        server = self.mt5_config.get('server', '')
        
        if account and password and server:
            try:
                account_num = int(account)
            except (ValueError, TypeError):
                logger.error(f"Invalid account number: {account}")
                mt5.shutdown()
                return False
            
            authorized = mt5.login(
                login=account_num,
                password=password,
                server=server,
                timeout=self.mt5_config.get('timeout', 60000)
            )
            
            if not authorized:
                logger.error(f"MT5 login failed: {mt5.last_error()}")
                mt5.shutdown()
                return False
        else:
            # Use existing terminal session
            account_info = mt5.account_info()
            if account_info is None:
                logger.warning("No MT5 credentials provided and no active session found")
                return False
        
        # Verify connection
        account_info = mt5.account_info()
        if account_info is None:
            logger.error("Failed to get account info after login")
            mt5.shutdown()
            return False
        
        self.connected = True
        logger.info(f"MT5 connected successfully. Account: {account_info.login}, Balance: {account_info.balance}")
        return True
    
    def reconnect(self) -> bool:
        """Attempt to reconnect to MT5."""
        logger.warning("Attempting to reconnect to MT5...")
        mt5.shutdown()
        self.connected = False
        
        for attempt in range(1, self.reconnect_attempts + 1):
            logger.info(f"Reconnection attempt {attempt}/{self.reconnect_attempts}")
            if self.connect():
                logger.info("Reconnection successful")
                return True
            time.sleep(self.reconnect_delay)
        
        logger.error("Failed to reconnect after all attempts")
        return False
    
    def ensure_connected(self) -> bool:
        """Ensure MT5 is connected, reconnect if needed."""
        if not self.connected:
            return self.connect()
        
        # Check if still connected
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            logger.warning("MT5 connection lost, attempting reconnect...")
            return self.reconnect()
        
        return True
    
    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get current account information."""
        if not self.ensure_connected():
            return None
        
        account_info = mt5.account_info()
        if account_info is None:
            return None
        
        # Get swap mode if available (for Islamic account detection)
        swap_mode = getattr(account_info, 'swap_mode', None)
        if swap_mode is None:
            # Try to infer from server name or other attributes
            server = account_info.server.lower() if account_info.server else ''
            if 'islamic' in server or 'swap' in server:
                swap_mode = 0  # Swap-free
            else:
                swap_mode = 1  # Default to swap-enabled (unknown)
        
        return {
            'login': account_info.login,
            'balance': account_info.balance,
            'equity': account_info.equity,
            'margin': account_info.margin,
            'free_margin': account_info.margin_free,
            'margin_level': account_info.margin_level,
            'profit': account_info.profit,
            'currency': account_info.currency,
            'server': account_info.server,
            'leverage': account_info.leverage,
            'trade_allowed': account_info.trade_allowed,
            'trade_expert': account_info.trade_expert,
            'swap_mode': swap_mode
        }
    
    def get_symbol_info(self, symbol: str, check_price_staleness: bool = True) -> Optional[Dict[str, Any]]:
        """Get symbol information with caching."""
        if not self.ensure_connected():
            return None
        
        now = time.time()
        
        # Check cache first
        with self._cache_lock:
            if symbol in self._symbol_info_cache:
                cached_data, cached_time = self._symbol_info_cache[symbol]
                if now - cached_time < self._symbol_cache_ttl:
                    # Return cached data if not stale
                    if check_price_staleness:
                        # Verify price staleness even for cached data
                        price_age = now - cached_time
                        if price_age > self._price_max_age_seconds:
                            logger.warning(f"{symbol}: Cached price is stale ({price_age:.2f}s > {self._price_max_age_seconds}s), fetching fresh data")
                        else:
                            return cached_data
                    else:
                        return cached_data
        
        # Fetch fresh data
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"Symbol {symbol} not found")
            return None
        
        # Get current tick time to check price staleness
        tick = mt5.symbol_info_tick(symbol)
        tick_time = 0
        if tick:
            tick_time = tick.time if hasattr(tick, 'time') else now
        
        result = {
            'name': symbol_info.name,
            'bid': symbol_info.bid,
            'ask': symbol_info.ask,
            'spread': symbol_info.spread,
            'point': symbol_info.point,
            'digits': symbol_info.digits,
            'trade_mode': symbol_info.trade_mode,
            'trade_stops_level': symbol_info.trade_stops_level,
            'trade_freeze_level': symbol_info.trade_freeze_level,
            'contract_size': symbol_info.trade_contract_size,
            'margin_initial': symbol_info.margin_initial,
            'swap_mode': symbol_info.swap_mode,
            'swap_long': symbol_info.swap_long,
            'swap_short': symbol_info.swap_short,
            'volume_min': symbol_info.volume_min,
            'volume_max': symbol_info.volume_max,
            'volume_step': symbol_info.volume_step,
            'filling_mode': symbol_info.filling_mode,
            '_fetched_time': now,  # Internal timestamp for staleness checks
            '_tick_time': tick_time  # MT5 tick time
        }
        
        # Check price staleness if requested
        if check_price_staleness and tick_time > 0:
            price_age = now - tick_time
            if price_age > self._price_max_age_seconds:
                logger.warning(f"{symbol}: Price is stale ({price_age:.2f}s > {self._price_max_age_seconds}s), rejecting")
                return None
        
        # Update cache
        with self._cache_lock:
            self._symbol_info_cache[symbol] = (result, now)
        
        return result
    
    def is_swap_free(self, symbol: str) -> bool:
        """Check if symbol is swap-free (Islamic account)."""
        symbol_info = self.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Check swap mode: 0 = disabled (swap-free)
        swap_mode = symbol_info.get('swap_mode', 1)
        return swap_mode == 0
    
    def shutdown(self):
        """Shutdown MT5 connection."""
        mt5.shutdown()
        self.connected = False
        logger.info("MT5 connection closed")

