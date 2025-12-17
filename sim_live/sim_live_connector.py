"""
SimLive MT5 Connector
Drop-in replacement for MT5Connector that uses synthetic market data and broker.
"""

import time
import threading
from typing import Optional, Dict, Any, Tuple
from utils.logger_factory import get_logger

from sim_live.synthetic_market_engine import SyntheticMarketEngine
from sim_live.synthetic_broker import SyntheticBroker

logger = get_logger("sim_live_connector", "logs/live/system/sim_live_connector.log")


class SimLiveMT5Connector:
    """
    Synthetic MT5 connector that replaces real MT5 with simulated market and broker.
    
    Implements the same interface as MT5Connector so the bot believes it's in live mode.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize synthetic MT5 connector.
        
        Args:
            config: Configuration dictionary (must have mode='SIM_LIVE')
        """
        self.config = config
        self.connected = False
        
        # Initialize synthetic market engine and broker (use _ prefix for internal, expose via properties)
        self._market_engine = SyntheticMarketEngine(config)
        self._broker = SyntheticBroker(config, self._market_engine)
        
        # Symbol info cache (matching MT5Connector behavior)
        self._symbol_info_cache = {}
        self._symbol_cache_ttl = 5.0  # seconds
        self._cache_lock = threading.Lock()
        self._price_max_age_seconds = 5.0
        
    def connect(self) -> bool:
        """Connect to synthetic broker (always succeeds)."""
        logger.info("Connecting to SIM_LIVE synthetic broker...")
        self.connected = True
        logger.info("[OK] SIM_LIVE broker connected (simulated)")
        return True
    
    def reconnect(self) -> bool:
        """Reconnect to synthetic broker (always succeeds)."""
        logger.info("Reconnecting to SIM_LIVE synthetic broker...")
        self.connected = True
        logger.info("[OK] SIM_LIVE broker reconnected (simulated)")
        return True
    
    def ensure_connected(self) -> bool:
        """Ensure connection is active (always true in simulation)."""
        if not self.connected:
            return self.connect()
        return True
    
    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account information from synthetic broker."""
        if not self.ensure_connected():
            return None
        
        return self._broker.get_account_info()
    
    def get_symbol_info(self, symbol: str, check_price_staleness: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get symbol information with caching (matching MT5Connector behavior).
        
        Args:
            symbol: Trading symbol
            check_price_staleness: If True, reject stale prices (>5s old)
        """
        if not self.ensure_connected():
            return None
        
        now = time.time()
        symbol_upper = symbol.upper()
        
        # Check cache first (matching MT5Connector logic)
        with self._cache_lock:
            if symbol_upper in self._symbol_info_cache:
                cached_data, cached_time = self._symbol_info_cache[symbol_upper]
                if now - cached_time < self._symbol_cache_ttl:
                    if not check_price_staleness:
                        return cached_data
                    # If checking staleness, verify even cached data
                    price_age = now - cached_time
                    if price_age > self._price_max_age_seconds:
                        logger.debug(f"{symbol}: Cached price is stale ({price_age:.2f}s > {self._price_max_age_seconds}s), fetching fresh data")
                    else:
                        return cached_data
        
        # Fetch fresh data from market engine
        symbol_info = self._market_engine.get_symbol_info(symbol_upper)
        
        if symbol_info is None:
            logger.error(f"Symbol {symbol_upper} not found in synthetic market")
            return None
        
        # Check price staleness if requested
        if check_price_staleness:
            tick_time = symbol_info.get('_tick_time', 0)
            if tick_time > 0:
                price_age = now - tick_time
                if price_age > self._price_max_age_seconds:
                    logger.warning(f"{symbol}: Price is stale ({price_age:.2f}s > {self._price_max_age_seconds}s), rejecting")
                    return None
        
        # Update cache
        with self._cache_lock:
            self._symbol_info_cache[symbol_upper] = (symbol_info, now)
        
        return symbol_info
    
    def is_symbol_tradeable_now(self, symbol: str, check_trade_allowed: bool = True) -> Tuple[bool, str]:
        """
        Check if symbol is tradeable right now.
        
        In simulation, symbols are always tradeable if they have prices.
        """
        if not self.ensure_connected():
            return False, "SIM_LIVE broker not connected"
        
        # Get symbol info to verify it exists
        symbol_info = self._market_engine.get_symbol_info(symbol)
        if symbol_info is None:
            return False, f"Symbol {symbol} not found in synthetic market"
        
        # Check if prices are valid
        bid = symbol_info.get('bid', 0.0)
        ask = symbol_info.get('ask', 0.0)
        
        if bid <= 0 or ask <= 0:
            return False, "Invalid prices (bid/ask <= 0)"
        
        if bid >= ask:
            return False, f"Invalid spread (bid {bid} >= ask {ask})"
        
        # In simulation, always tradeable if prices are valid
        return True, ""
    
    def get_symbol_info_tick(self, symbol: str):
        """
        Get current tick data (BID/ASK prices) for a symbol.
        
        Returns:
            Tick-like object with bid, ask, time attributes, or None
        """
        if not self.ensure_connected():
            return None
        
        tick_data = self._market_engine.get_current_tick(symbol)
        if tick_data is None:
            logger.debug(f"Tick data not available for {symbol}")
            return None
        
        # Create tick-like object
        class Tick:
            def __init__(self, bid, ask, time):
                self.bid = bid
                self.ask = ask
                self.time = time
                self.volume = 0
        
        return Tick(
            bid=tick_data['bid'],
            ask=tick_data['ask'],
            time=tick_data['time']
        )
    
    def is_swap_free(self, symbol: str) -> bool:
        """Check if symbol is swap-free (always true in simulation)."""
        return True
    
    def shutdown(self):
        """Shutdown synthetic broker connection."""
        self.connected = False
        logger.info("SIM_LIVE broker connection closed")
    
    def copy_rates_from_pos(self, symbol: str, timeframe: int, offset: int, count: int):
        """
        Get historical candle data (delegates to market engine).
        
        Returns NumPy structured array matching MT5 format for TrendFilter compatibility.
        
        This method is called by strategies/trend_filter.py when checking if
        mt5_connector has copy_rates_from_pos method.
        """
        candles = self._market_engine.copy_rates_from_pos(symbol, timeframe, offset, count)
        
        if candles is None or len(candles) == 0:
            return None
        
        # Convert to NumPy structured array matching MT5 format
        import numpy as np
        
        dtype = [
            ('time', 'int64'),
            ('open', 'float64'),
            ('high', 'float64'),
            ('low', 'float64'),
            ('close', 'float64'),
            ('tick_volume', 'int64'),
            ('spread', 'int32'),
            ('real_volume', 'int64')
        ]
        
        array_data = []
        for i, candle in enumerate(candles):
            array_data.append((
                candle['time'],
                candle['open'],
                candle['high'],
                candle['low'],
                candle['close'],
                candle.get('tick_volume', 0),
                candle.get('spread', 0),
                candle.get('real_volume', 0)
            ))
            
            # 🔍 Log first 14 closes being converted to NumPy array
            if i < 14 and self._market_engine.config.get('mode') == 'SIM_LIVE':
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    if i == 0:
                        logger.info(f"[SIM_LIVE] [NUMPY_CONVERSION] Converting {len(candles)} candles to NumPy array")
                    if i < 5 or (i >= 10 and i < 14):
                        logger.info(f"[SIM_LIVE] [NUMPY_CONVERSION] Candle {i}: time={candle['time']}, close={candle['close']}")
                except:
                    pass
        
        result_array = np.array(array_data, dtype=dtype)
        
        # 🔍 Verify the array after creation
        if self._market_engine.config.get('mode') == 'SIM_LIVE' and len(result_array) >= 14:
            try:
                from sim_live.sim_live_logger import get_sim_live_logger
                logger = get_sim_live_logger()
                first_14_closes_np = result_array['close'][:14].tolist()
                logger.info(f"[SIM_LIVE] [NUMPY_VERIFY] NumPy array first 14 closes: {first_14_closes_np}")
            except:
                pass
        
        return result_array
    
    # Properties for compatibility
    @property
    def market_engine(self):
        """Access to market engine (for scenario setup)."""
        return self._market_engine
    
    @property
    def broker(self):
        """Access to broker (for testing)."""
        return self._broker

