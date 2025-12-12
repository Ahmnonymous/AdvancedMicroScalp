"""
MT5 Connection and Reconnection Manager
Handles connection to MetaTrader 5 with automatic reconnection logic.
"""

import MetaTrader5 as mt5
import time
import threading
from typing import Optional, Dict, Any, Tuple
import json
from utils.logger_factory import get_logger

logger = get_logger("mt5_connection", "logs/live/system/mt5_connection.log")


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
        
        # Check if MT5 terminal is running first
        mt5_path = self.mt5_config.get('path', '')
        if mt5_path:
            logger.info(f"Attempting to connect to MT5 at: {mt5_path}")
        else:
            logger.info("Attempting to connect to MT5 (using default installation path)")
            
        # Try to initialize MT5
        if not mt5.initialize(path=mt5_path):
            error_info = mt5.last_error()
            
            # MT5.last_error() returns a tuple (retcode, description)
            retcode = None
            description = str(error_info)
            
            if isinstance(error_info, tuple) and len(error_info) >= 2:
                retcode = error_info[0]
                description = error_info[1]
            elif hasattr(error_info, 'retcode'):
                retcode = error_info.retcode
                if hasattr(error_info, 'description'):
                    description = error_info.description
            
            # Get more detailed error information
            error_msg = f"MT5 initialization failed"
            if retcode is not None:
                error_msg += f" (Error code: {retcode})"
            if description:
                error_msg += f": {description}"
            
            # Common error codes:
            # - (1, 'RES_S_OK') = Success
            # - (10004, 'Failed to connect to MetaTrader 5 terminal') = Terminal not running
            # - (10019, 'Wrong version of the terminal') = Wrong MT5 version
            
            if retcode == 10004:
                error_msg += "\n   → MT5 Terminal is not running. Please start MetaTrader 5 terminal first."
            elif retcode == 10019:
                error_msg += "\n   → Wrong MT5 version. Please update MetaTrader 5 terminal."
            elif retcode is not None and retcode != 0:
                error_msg += f"\n   → Error code: {retcode}. Check MT5 terminal installation and path."
            
            if mt5_path:
                error_msg += f"\n   → Tried path: {mt5_path}"
                error_msg += "\n   → Tip: Make sure the path points to terminal64.exe or terminal.exe"
            else:
                error_msg += "\n   → Tip: If MT5 is installed in a non-standard location, specify the path in config.json"
                error_msg += "\n   → Example: \"path\": \"C:\\Program Files\\MetaTrader 5\\terminal64.exe\""
            
            logger.error(error_msg)
            print(f"[ERROR] {error_msg}")
            return False
        
        # Try to login if credentials provided
        account = self.mt5_config.get('account', '')
        password = self.mt5_config.get('password', '')
        server = self.mt5_config.get('server', '')
        
        if account and password and server:
            try:
                account_num = int(account)
            except (ValueError, TypeError):
                error_msg = f"Invalid account number: {account}"
                logger.error(error_msg)
                print(f"[ERROR] {error_msg}")
                mt5.shutdown()
                return False
            
            logger.info(f"Attempting to login to account {account_num} on server {server}...")
            authorized = mt5.login(
                login=account_num,
                password=password,
                server=server,
                timeout=self.mt5_config.get('timeout', 60000)
            )
            
            if not authorized:
                error_info = mt5.last_error()
                
                # MT5.last_error() returns a tuple (retcode, description)
                retcode = None
                description = str(error_info)
                
                if isinstance(error_info, tuple) and len(error_info) >= 2:
                    retcode = error_info[0]
                    description = error_info[1]
                elif hasattr(error_info, 'retcode'):
                    retcode = error_info.retcode
                    if hasattr(error_info, 'description'):
                        description = error_info.description
                
                error_msg = f"MT5 login failed"
                
                if retcode == 10004:
                    error_msg += ": Failed to connect to terminal"
                elif retcode == 10013:
                    error_msg += ": Invalid account or password"
                elif retcode == 10014:
                    error_msg += ": Invalid server name"
                elif retcode == 10015:
                    error_msg += ": Connection timeout"
                elif retcode is not None:
                    error_msg += f" (Error code: {retcode})"
                
                if description and retcode is None:
                    error_msg += f": {description}"
                
                error_msg += f"\n   → Account: {account_num}"
                error_msg += f"\n   → Server: {server}"
                error_msg += "\n   → Please verify your credentials in config.json"
                
                logger.error(error_msg)
                print(f"[ERROR] {error_msg}")
                mt5.shutdown()
                return False
        else:
            # Use existing terminal session
            logger.info("No credentials provided, using existing MT5 terminal session...")
            account_info = mt5.account_info()
            if account_info is None:
                error_msg = "No MT5 credentials provided and no active session found"
                error_msg += "\n   → Either provide credentials in config.json, or login to MT5 terminal manually first"
                logger.warning(error_msg)
                print(f"[WARNING]  {error_msg}")
                mt5.shutdown()
                return False
        
        # Verify connection
        account_info = mt5.account_info()
        if account_info is None:
            error_msg = "Failed to get account info after login"
            logger.error(error_msg)
            print(f"[ERROR] {error_msg}")
            mt5.shutdown()
            return False
        
        self.connected = True
        success_msg = f"[OK] MT5 connected successfully. Account: {account_info.login}, Balance: {account_info.balance}"
        logger.info(success_msg)
        print(success_msg)
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
    
    def get_symbol_info(self, symbol: str, check_price_staleness: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get symbol information with caching.
        
        Args:
            symbol: Trading symbol
            check_price_staleness: If True, reject stale prices (>5s old). 
                                  Default False - only check when placing orders, not during scanning.
        """
        if not self.ensure_connected():
            return None
        
        now = time.time()
        
        # Check cache first
        with self._cache_lock:
            if symbol in self._symbol_info_cache:
                cached_data, cached_time = self._symbol_info_cache[symbol]
                if now - cached_time < self._symbol_cache_ttl:
                    # Return cached data if not stale
                    if not check_price_staleness:
                        return cached_data
                    # If checking staleness, verify even cached data
                    price_age = now - cached_time
                    if price_age > self._price_max_age_seconds:
                        logger.debug(f"{symbol}: Cached price is stale ({price_age:.2f}s > {self._price_max_age_seconds}s), fetching fresh data")
                    else:
                        return cached_data
        
        # Fetch fresh data
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"Symbol {symbol} not found")
            return None
        
        # Get current tick time to check price staleness (only if requested)
        tick_time = 0
        if check_price_staleness:
            tick = mt5.symbol_info_tick(symbol)
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
            'trade_tick_value': getattr(symbol_info, 'trade_tick_value', None),  # For indices/crypto
            'trade_tick_size': getattr(symbol_info, 'trade_tick_size', None),  # For indices/crypto
            'margin_initial': symbol_info.margin_initial,
            'swap_mode': symbol_info.swap_mode,
            'swap_long': symbol_info.swap_long,
            'swap_short': symbol_info.swap_short,
            'volume_min': symbol_info.volume_min,
            'volume_max': symbol_info.volume_max,
            'volume_step': symbol_info.volume_step,
            'filling_mode': symbol_info.filling_mode,
            '_fetched_time': now,  # Internal timestamp for staleness checks
            '_tick_time': tick_time  # MT5 tick time (0 if not checked)
        }
        
        # Check price staleness if requested (only when placing orders)
        if check_price_staleness and tick_time > 0:
            price_age = now - tick_time
            if price_age > self._price_max_age_seconds:
                logger.warning(f"{symbol}: Price is stale ({price_age:.2f}s > {self._price_max_age_seconds}s), rejecting")
                return None
        
        # Update cache
        with self._cache_lock:
            self._symbol_info_cache[symbol] = (result, now)
        
        return result
    
    def is_symbol_tradeable_now(self, symbol: str, check_trade_allowed: bool = True) -> Tuple[bool, str]:
        """
        Check if symbol is tradeable right now (market is open and trading is allowed).
        
        Args:
            symbol: Trading symbol
            check_trade_allowed: If True, also check account trade permissions
        
        Returns:
            Tuple of (is_tradeable, reason)
            - is_tradeable: True if market is open and tradeable
            - reason: Reason string if not tradeable, empty if tradeable
        """
        if not self.ensure_connected():
            return False, "MT5 not connected"
        
        # Check account trade permissions if requested
        if check_trade_allowed:
            account_info = self.get_account_info()
            if account_info:
                if not account_info.get('trade_allowed', False):
                    return False, "Trading disabled on account - check account settings"
                if not account_info.get('trade_expert', False):
                    return False, "Expert advisor trading disabled - enable in MT5 terminal"
        
        # Get symbol info
        symbol_info_obj = mt5.symbol_info(symbol)
        if symbol_info_obj is None:
            # Try to add symbol to Market Watch if not found
            if not mt5.symbol_select(symbol, True):
                return False, f"Symbol {symbol} not found and cannot be added to Market Watch"
            # Retry getting symbol info after adding to Market Watch
            symbol_info_obj = mt5.symbol_info(symbol)
            if symbol_info_obj is None:
                return False, f"Symbol {symbol} not found even after adding to Market Watch"
        
        # Ensure symbol is in Market Watch (required for trading)
        if not mt5.symbol_select(symbol, True):
            return False, f"Symbol {symbol} cannot be added to Market Watch"
        
        # Refresh symbol data after adding to Market Watch
        symbol_info_obj = mt5.symbol_info(symbol)
        if symbol_info_obj is None:
            return False, f"Symbol {symbol} info unavailable after Market Watch addition"
        
        # Check trade mode (must be 4 = full trading enabled)
        if symbol_info_obj.trade_mode != 4:
            mode_descriptions = {
                0: "Disabled",
                1: "Long only",
                2: "Short only",
                3: "Close only",
                4: "Full trading"
            }
            mode_desc = mode_descriptions.get(symbol_info_obj.trade_mode, f"Unknown mode {symbol_info_obj.trade_mode}")
            return False, f"Trade mode {symbol_info_obj.trade_mode} ({mode_desc}) - not fully tradeable"
        
        # Validate contract size, min volume, step, stop level, freeze level
        if symbol_info_obj.trade_contract_size <= 0:
            return False, f"Invalid contract size ({symbol_info_obj.trade_contract_size})"
        
        if symbol_info_obj.volume_min <= 0:
            return False, f"Invalid minimum volume ({symbol_info_obj.volume_min})"
        
        if symbol_info_obj.volume_step <= 0:
            return False, f"Invalid volume step ({symbol_info_obj.volume_step})"
        
        # Get current tick to check if market is open
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return False, "Cannot get tick data - market may be closed"
        
        # Check if bid/ask are valid (market is open)
        if tick.bid <= 0 or tick.ask <= 0:
            return False, "Invalid prices (bid/ask <= 0) - market closed"
        
        if tick.bid >= tick.ask:
            return False, f"Invalid spread (bid {tick.bid} >= ask {tick.ask}) - market closed"
        
        # Check if prices are stale (no recent update)
        import time
        if hasattr(tick, 'time') and tick.time > 0:
            tick_age_seconds = time.time() - tick.time
            if tick_age_seconds > 60:  # No update for 60 seconds suggests market closed
                return False, f"Price stale ({tick_age_seconds:.0f}s old) - market may be closed"
        
        # Check trading hours if available (MT5 doesn't always provide this)
        # For now, we rely on bid/ask validity which is the most reliable indicator
        
        return True, ""
    
    def get_symbol_info_tick(self, symbol: str):
        """
        Get current tick data (BID/ASK prices) for a symbol.
        
        This method returns the raw MT5 tick object which contains:
        - bid: Current BID price
        - ask: Current ASK price
        - time: Tick timestamp
        - volume: Tick volume
        
        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
        
        Returns:
            MT5 tick object with bid, ask, time attributes, or None if:
            - MT5 is not connected
            - Symbol is not found
            - Tick data is unavailable
        """
        if not self.ensure_connected():
            return None
        
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.debug(f"Tick data not available for {symbol}")
            return None
        
        # Validate tick data
        if tick.bid <= 0 or tick.ask <= 0:
            logger.warning(f"Invalid tick data for {symbol}: bid={tick.bid}, ask={tick.ask}")
            return None
        
        if tick.bid >= tick.ask:
            logger.warning(f"Invalid spread for {symbol}: bid={tick.bid} >= ask={tick.ask}")
            return None
        
        return tick
    
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

