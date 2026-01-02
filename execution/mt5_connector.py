"""
MT5 Connection and Reconnection Manager
Handles connection to MetaTrader 5 with automatic reconnection logic.
"""

import MetaTrader5 as mt5
import time
import threading
from typing import Optional, Dict, Any, Tuple, List
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
        
        # P1-7 FIX: Price Staleness Tightening - Reduced to 2 seconds for order placement
        # Price staleness check (reject prices older than 2 seconds for orders, 5 seconds for scanning)
        self._price_max_age_seconds = 2.0  # Reduced from 5.0 for order placement
        self._price_max_age_seconds_scanning = 5.0  # Keep 5 seconds for scanning
        
        # P2-13 FIX: Symbol Info Cache Invalidation - Track last prices for movement detection
        self._last_symbol_prices = {}  # {symbol: {'bid': float, 'ask': float, 'timestamp': float}}
        self._price_movement_threshold_pct = 0.1  # 0.1% price movement invalidates cache
        
        # P0-1 FIX: MT5 Connection Loss Protection - Track reconnection attempts
        self._reconnection_failure_count = 0
        self._max_reconnection_failures = 3  # Circuit breaker threshold
        self._last_position_snapshot = None  # Snapshot before reconnection
        self._position_snapshot_lock = threading.Lock()
        
        # P1-10 FIX: MT5 Reconnection Loop Prevention - Add max retry limit and backoff cap
        self._max_total_reconnect_attempts = 10  # Max total reconnection attempts
        self._total_reconnect_attempts = 0
        self._reconnect_backoff_cap_seconds = 60.0  # Max backoff delay (60 seconds)
        
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
        
        # P0-1 FIX: Check circuit breaker before attempting reconnection
        if self._reconnection_failure_count >= self._max_reconnection_failures:
            logger.critical(f"[CIRCUIT_BREAKER] MT5 reconnection circuit breaker active - {self._reconnection_failure_count} consecutive failures")
            return False
        
        # P1-10 FIX: Check total reconnection attempts limit
        if self._total_reconnect_attempts >= self._max_total_reconnect_attempts:
            logger.critical(f"[RECONNECT_LIMIT] MT5 reconnection limit reached - {self._total_reconnect_attempts}/{self._max_total_reconnect_attempts} total attempts")
            return False
        
        for attempt in range(1, self.reconnect_attempts + 1):
            # P1-10 FIX: Check total attempts limit before each attempt
            if self._total_reconnect_attempts >= self._max_total_reconnect_attempts:
                logger.critical(f"[RECONNECT_LIMIT] MT5 reconnection limit reached during attempt loop")
                return False
            
            self._total_reconnect_attempts += 1
            logger.info(f"Reconnection attempt {attempt}/{self.reconnect_attempts} (total: {self._total_reconnect_attempts}/{self._max_total_reconnect_attempts})")
            
            if self.connect():
                logger.info("Reconnection successful")
                # P0-1 FIX: Reset failure count on successful reconnection
                self._reconnection_failure_count = 0
                # P1-10 FIX: Reset total attempts on successful reconnection
                self._total_reconnect_attempts = 0
                return True
            
            # P1-10 FIX: Exponential backoff with cap
            backoff_delay = min(self.reconnect_delay * (2 ** (attempt - 1)), self._reconnect_backoff_cap_seconds)
            time.sleep(backoff_delay)
        
        # P0-1 FIX: Increment failure count on complete failure
        self._reconnection_failure_count += 1
        logger.error(f"Failed to reconnect after all attempts (failure count: {self._reconnection_failure_count}/{self._max_reconnection_failures}, total attempts: {self._total_reconnect_attempts}/{self._max_total_reconnect_attempts})")
        return False
    
    def ensure_connected(self) -> bool:
        """Ensure MT5 is connected, reconnect if needed."""
        if not self.connected:
            return self.connect()
        
        # Check if still connected
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            logger.warning("MT5 connection lost, attempting reconnect...")
            # P0-1 FIX: Check circuit breaker before reconnecting
            if self._reconnection_failure_count >= self._max_reconnection_failures:
                logger.critical(f"[CIRCUIT_BREAKER] MT5 reconnection circuit breaker active - not attempting reconnect")
                return False
            return self.reconnect()
        
        return True
    
    def get_position_snapshot(self) -> Optional[List[Dict[str, Any]]]:
        """
        P0-1 FIX: Get snapshot of current positions before reconnection.
        
        Returns:
            List of position dictionaries or None if unable to get positions
        """
        try:
            if not self.connected:
                return None
            
            positions = mt5.positions_get()
            if positions is None:
                return None
            
            snapshot = []
            for pos in positions:
                snapshot.append({
                    'ticket': pos.ticket,
                    'symbol': pos.symbol,
                    'type': pos.type,
                    'volume': pos.volume,
                    'price_open': pos.price_open,
                    'price_current': pos.price_current,
                    'profit': pos.profit,
                    'sl': pos.sl,
                    'tp': pos.tp,
                    'time': pos.time
                })
            
            with self._position_snapshot_lock:
                self._last_position_snapshot = snapshot
            
            return snapshot
        except Exception as e:
            logger.error(f"Error getting position snapshot: {e}")
            return None
    
    def verify_positions_after_reconnection(self, order_manager) -> Dict[str, Any]:
        """
        P0-1 FIX: Verify all positions after reconnection.
        
        Args:
            order_manager: OrderManager instance to get current positions
            
        Returns:
            Dictionary with verification results
        """
        results = {
            'verified': [],
            'missing': [],
            'exceeded_risk': [],
            'errors': []
        }
        
        try:
            # Get snapshot before reconnection
            snapshot = None
            with self._position_snapshot_lock:
                snapshot = self._last_position_snapshot
            
            if snapshot is None:
                logger.warning("[POSITION_VERIFICATION] No position snapshot available - skipping verification")
                return results
            
            # Get current positions after reconnection
            current_positions = order_manager.get_open_positions(exclude_dec8=False)
            current_tickets = {pos['ticket']: pos for pos in current_positions}
            
            # Verify each position from snapshot
            for snap_pos in snapshot:
                ticket = snap_pos['ticket']
                if ticket in current_tickets:
                    # Position still exists - verify it
                    current_pos = current_tickets[ticket]
                    results['verified'].append(ticket)
                    
                    # Check if position exceeds risk limits
                    profit = current_pos.get('profit', 0.0)
                    if profit < -3.0:  # Exceeds max risk
                        results['exceeded_risk'].append({
                            'ticket': ticket,
                            'symbol': current_pos.get('symbol', ''),
                            'profit': profit
                        })
                else:
                    # Position missing - may have been closed
                    results['missing'].append(ticket)
            
            return results
        except Exception as e:
            logger.error(f"Error verifying positions after reconnection: {e}", exc_info=True)
            results['errors'].append(str(e))
            return results
    
    def reset_circuit_breaker(self):
        """P0-1 FIX: Reset reconnection circuit breaker."""
        self._reconnection_failure_count = 0
        logger.info("[CIRCUIT_BREAKER] MT5 reconnection circuit breaker reset")
    
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
        
        # P2-13 FIX: Check cache with price movement detection
        with self._cache_lock:
            if symbol in self._symbol_info_cache:
                cached_data, cached_time = self._symbol_info_cache[symbol]
                
                # P2-13 FIX: Check if price moved significantly (invalidate cache)
                if symbol in self._last_symbol_prices:
                    last_price_data = self._last_symbol_prices[symbol]
                    cached_bid = cached_data.get('bid', 0)
                    cached_ask = cached_data.get('ask', 0)
                    last_bid = last_price_data.get('bid', 0)
                    last_ask = last_price_data.get('ask', 0)
                    
                    if cached_bid > 0 and last_bid > 0:
                        bid_movement_pct = abs(cached_bid - last_bid) / last_bid * 100
                        ask_movement_pct = abs(cached_ask - last_ask) / last_ask * 100 if cached_ask > 0 and last_ask > 0 else 0
                        
                        if bid_movement_pct > self._price_movement_threshold_pct or ask_movement_pct > self._price_movement_threshold_pct:
                            logger.debug(f"{symbol}: Price movement detected ({bid_movement_pct:.2f}% bid, {ask_movement_pct:.2f}% ask) - invalidating cache")
                            del self._symbol_info_cache[symbol]
                            # Continue to fetch fresh data
                        elif now - cached_time < self._symbol_cache_ttl:
                            # Return cached data if not stale and no significant movement
                            if not check_price_staleness:
                                return cached_data
                            # If checking staleness, verify even cached data
                            price_age = now - cached_time
                            staleness_threshold = self._price_max_age_seconds if check_price_staleness else self._price_max_age_seconds_scanning
                            if price_age > staleness_threshold:
                                logger.debug(f"{symbol}: Cached price is stale ({price_age:.2f}s > {staleness_threshold}s), fetching fresh data")
                            else:
                                return cached_data
                elif now - cached_time < self._symbol_cache_ttl:
                    # No previous price data - use TTL check
                    if not check_price_staleness:
                        return cached_data
                    price_age = now - cached_time
                    staleness_threshold = self._price_max_age_seconds if check_price_staleness else self._price_max_age_seconds_scanning
                    if price_age > staleness_threshold:
                        logger.debug(f"{symbol}: Cached price is stale ({price_age:.2f}s > {staleness_threshold}s), fetching fresh data")
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
        
        # P1-7 FIX: Check price staleness if requested (only when placing orders)
        # Use tighter threshold (2s) for order placement, looser (5s) for scanning
        if check_price_staleness and tick_time > 0:
            price_age = now - tick_time
            staleness_threshold = self._price_max_age_seconds if check_price_staleness else self._price_max_age_seconds_scanning
            if price_age > staleness_threshold:
                logger.warning(f"{symbol}: Price is stale ({price_age:.2f}s > {staleness_threshold}s), rejecting")
                return None
        
        # P2-13 FIX: Update cache and track price for movement detection
        with self._cache_lock:
            self._symbol_info_cache[symbol] = (result, now)
            # Track current prices for movement detection
            self._last_symbol_prices[symbol] = {
                'bid': result.get('bid', 0),
                'ask': result.get('ask', 0),
                'timestamp': now
            }
        
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

