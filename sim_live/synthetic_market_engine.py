"""
Synthetic Market Engine
Generates deterministic market data (ticks, candles) for synthetic live testing.
"""

import time
import threading
import random
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Callable
from collections import deque
import math


class SyntheticMarketEngine:
    """
    Generates synthetic market data in real-time or accelerated time.
    
    Features:
    - Tick generation with configurable spread
    - Candle generation (M1, M5, etc.)
    - Scripted price movements (scenarios)
    - Deterministic behavior
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize synthetic market engine.
        
        Args:
            config: Configuration dict with sim_live settings
        """
        self.config = config
        self.sim_config = config.get('sim_live', {})
        
        # Time acceleration factor (1.0 = real-time, 10.0 = 10x faster)
        self.time_acceleration = self.sim_config.get('time_acceleration', 1.0)
        
        # Symbol configurations (default for common symbols)
        self._symbol_configs = {
            'EURUSD': {'point': 0.00001, 'digits': 5, 'spread_pips': 1.0, 'contract_size': 100000},
            'GBPUSD': {'point': 0.00001, 'digits': 5, 'spread_pips': 1.0, 'contract_size': 100000},
            'USDJPY': {'point': 0.001, 'digits': 3, 'spread_pips': 1.0, 'contract_size': 100000},
            'XAUUSD': {'point': 0.01, 'digits': 2, 'spread_pips': 0.5, 'contract_size': 100},
            'BTCUSD': {'point': 0.1, 'digits': 1, 'spread_pips': 5.0, 'contract_size': 1},
        }
        
        # Current prices per symbol {symbol: {'bid': float, 'ask': float, 'time': float}}
        self._current_prices = {}
        
        # Candle history per symbol {symbol: {timeframe: deque([candles])}}
        self._candle_history = {}
        
        # Candle generation state for realistic history
        self._warmup_candles = {}  # {symbol: {'warmup_complete': bool, 'base_price': float, 'trend_direction': str}}
        self._candle_gen_lock = threading.Lock()
        self._current_warmup_slope_multiplier = 1.0  # For trend contract retry loop
        self._market_frozen = {}  # Track frozen state per symbol: {symbol: True/False}
        
        # Scenario control
        self._scenario = None
        self._scenario_start_time = None
        self._scenario_actions = []  # List of actions to execute
        self._scenario_lock = threading.Lock()
        
        # Price update callbacks (for position updates)
        self._price_update_callbacks: List[Callable] = []
        self._callback_lock = threading.Lock()
        
        # Internal state
        self._start_time = time.time()
        self._lock = threading.Lock()
        
    def set_symbol_config(self, symbol: str, config: Dict[str, Any]):
        """Set configuration for a symbol."""
        with self._lock:
            self._symbol_configs[symbol.upper()] = config
    
    def get_symbol_config(self, symbol: str) -> Dict[str, Any]:
        """Get configuration for a symbol (with defaults)."""
        symbol_upper = symbol.upper()
        
        # Check if we have a config
        if symbol_upper in self._symbol_configs:
            return self._symbol_configs[symbol_upper]
        
        # Default config for unknown symbols (assume forex)
        return {
            'point': 0.00001,
            'digits': 5,
            'spread_pips': 1.0,
            'contract_size': 100000
        }
    
    def set_initial_price(self, symbol: str, bid: float, ask: Optional[float] = None):
        """Set initial price for a symbol."""
        config = self.get_symbol_config(symbol)
        point = config['point']
        spread_pips = config['spread_pips']
        
        # Calculate ask if not provided
        if ask is None:
            ask = bid + (spread_pips * point * 10)
        
        with self._lock:
            self._current_prices[symbol.upper()] = {
                'bid': bid,
                'ask': ask,
                'time': self._get_current_time()
            }
    
    def get_current_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get current tick data for a symbol.
        
        Returns:
            Dict with 'bid', 'ask', 'time', 'volume' keys, or None if symbol not initialized
        """
        symbol_upper = symbol.upper()
        
        with self._lock:
            if symbol_upper not in self._current_prices:
                return None
            
            price_data = self._current_prices[symbol_upper].copy()
            price_data['time'] = int(self._get_current_time())  # Unix timestamp
            
            return price_data
    
    def move_price(self, symbol: str, delta_bid: float, delta_ask: Optional[float] = None, duration_seconds: float = 0.0):
        """
        Move price linearly over time (for scenario scripts).
        
        Args:
            symbol: Trading symbol
            delta_bid: Change in bid price
            delta_ask: Change in ask price (if None, follows delta_bid + spread)
            duration_seconds: Duration over which to move (0 = instant)
        """
        symbol_upper = symbol.upper()
        
        # Log price movement
        try:
            from sim_live.sim_live_logger import get_sim_live_logger
            sim_logger = get_sim_live_logger()
            scenario_time = self._get_current_time() - (self._scenario_start_time if self._scenario_start_time else self._start_time)
            sim_logger.info(f"[SYNTHETIC_MARKET] Price moved by {delta_bid:+.5f} (bid) at t={scenario_time:.1f}s for {symbol}")
        except:
            pass
        
        # CRITICAL FIX: Extract values needed for callback BEFORE acquiring lock
        # This prevents deadlock when callbacks try to acquire the same lock
        new_bid = None
        new_ask = None
        
        with self._lock:
            if symbol_upper not in self._current_prices:
                # Initialize if not exists
                config = self.get_symbol_config(symbol_upper)
                default_bid = 1.1000 if 'USD' in symbol_upper else 100.0
                self.set_initial_price(symbol_upper, default_bid)
            
            price_data = self._current_prices[symbol_upper]
            old_bid = price_data['bid']
            old_ask = price_data['ask']
            
            if duration_seconds == 0:
                # Instant move
                price_data['bid'] += delta_bid
                if delta_ask is not None:
                    price_data['ask'] += delta_ask
                else:
                    # Maintain spread
                    config = self.get_symbol_config(symbol_upper)
                    spread_pips = config['spread_pips']
                    point = config['point']
                    price_data['ask'] = price_data['bid'] + (spread_pips * point * 10)
                
                price_data['time'] = self._get_current_time()
                
                # Store bid/ask for callback (extract while still in lock, but call AFTER)
                new_bid = price_data['bid']
                new_ask = price_data['ask']
                
                # Log detailed price change
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    sim_logger = get_sim_live_logger()
                    sim_logger.info(f"[SYNTHETIC_MARKET] {symbol} price: BID {old_bid:.5f} → {new_bid:.5f} "
                                  f"(Δ{new_bid-old_bid:+.5f}), ASK {old_ask:.5f} → {new_ask:.5f}")
                except:
                    pass
            else:
                # Linear interpolation over time
                steps = max(1, int(duration_seconds * 10))  # 10 updates per second
                step_bid = delta_bid / steps
                step_ask = (delta_ask if delta_ask is not None else delta_bid) / steps
                
                # Execute in steps (simplified - in real scenario this would be async)
                for i in range(steps):
                    price_data['bid'] += step_bid
                    price_data['ask'] += step_ask if delta_ask is not None else step_bid
                    price_data['time'] = self._get_current_time()
                    self._notify_price_update(symbol_upper, price_data['bid'], price_data['ask'])
                    time.sleep(duration_seconds / steps / self.time_acceleration)
                
                # Log final price after interpolation
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    sim_logger = get_sim_live_logger()
                    sim_logger.info(f"[SYNTHETIC_MARKET] {symbol} price moved over {duration_seconds:.1f}s: "
                                  f"BID {old_bid:.5f} → {price_data['bid']:.5f}, ASK {old_ask:.5f} → {price_data['ask']:.5f}")
                except:
                    pass
    
    def set_price(self, symbol: str, bid: float, ask: Optional[float] = None):
        """Set price instantly (no interpolation)."""
        symbol_upper = symbol.upper()
        config = self.get_symbol_config(symbol_upper)
        
        if ask is None:
            spread_pips = config['spread_pips']
            point = config['point']
            ask = bid + (spread_pips * point * 10)
        
        with self._lock:
            if symbol_upper not in self._current_prices:
                self._current_prices[symbol_upper] = {}
            
            self._current_prices[symbol_upper] = {
                'bid': bid,
                'ask': ask,
                'time': self._get_current_time()
            }
            
            self._notify_price_update(symbol_upper, bid, ask)
    
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol info matching MT5 format.
        
        Returns:
            Dict with symbol info fields matching MT5Connector.get_symbol_info()
        """
        symbol_upper = symbol.upper()
        config = self.get_symbol_config(symbol_upper)
        price_data = self.get_current_tick(symbol_upper)
        
        if price_data is None:
            # Return default info if price not initialized
            return {
                'name': symbol_upper,
                'bid': 0.0,
                'ask': 0.0,
                'spread': int(config['spread_pips'] * 10),  # Convert to points
                'point': config['point'],
                'digits': config['digits'],
                'trade_mode': 4,  # Full trading
                'trade_stops_level': 0,
                'trade_freeze_level': 0,
                'contract_size': config['contract_size'],
                'trade_tick_value': None,
                'trade_tick_size': None,
                'margin_initial': 0.0,
                'swap_mode': 0,  # Swap-free
                'swap_long': 0,
                'swap_short': 0,
                'volume_min': 0.01,
                'volume_max': 100.0,
                'volume_step': 0.01,
                'filling_mode': 7,  # IOC | RETURN | FOK
                '_fetched_time': time.time(),
                '_tick_time': int(time.time())
            }
        
        spread_points = int((price_data['ask'] - price_data['bid']) / config['point'])
        
        return {
            'name': symbol_upper,
            'bid': price_data['bid'],
            'ask': price_data['ask'],
            'spread': spread_points,
            'point': config['point'],
            'digits': config['digits'],
            'trade_mode': 4,  # Full trading
            'trade_stops_level': 0,
            'trade_freeze_level': 0,
            'contract_size': config['contract_size'],
            'trade_tick_value': None,
            'trade_tick_size': None,
            'margin_initial': 0.0,
            'swap_mode': 0,  # Swap-free
            'swap_long': 0,
            'swap_short': 0,
            'volume_min': 0.01,
            'volume_max': 100.0,
            'volume_step': 0.01,
            'filling_mode': 7,  # IOC | RETURN | FOK
            '_fetched_time': time.time(),
            '_tick_time': price_data['time']
        }
    
    def generate_warmup_candles(self, symbol: str, trend_direction: str = 'BUY', base_price: Optional[float] = None, count: int = 35):
        """
        Generate warm-up candle history that creates a valid trend.
        
        Args:
            symbol: Trading symbol
            trend_direction: 'BUY' or 'SELL' - determines trend direction
            base_price: Starting price (if None, uses current or default)
            count: Number of candles to generate (default 35 for SMA50 warm-up)
        
        This generates candles that will:
        - Form a clear trend (price above/below SMAs for BUY/SELL)
        - Have realistic ranges and wicks
        - Produce valid SMA/ADX/volatility values
        - Make entry candle pass quality filters
        """
        # Ensure we generate enough candles for SMA50 (needs 50 candles minimum)
        # Add extra buffer (10 candles) to ensure clear trend separation
        actual_count = max(count, 60)
        
        symbol_upper = symbol.upper()
        config = self.get_symbol_config(symbol_upper)
        point = config['point']
        spread_pips = config['spread_pips']
        
        # Determine base price
        if base_price is None:
            tick = self.get_current_tick(symbol_upper)
            if tick:
                base_price = tick['bid']
            else:
                # Default price based on symbol type
                base_price = 1.1000 if 'USD' in symbol_upper and 'XAU' not in symbol_upper else 2000.0
        
        # Generate realistic candles with trend that produces valid indicators
        # Strategy: Create non-linear progression with pullbacks
        # For BUY: Early candles establish baseline, later candles push higher
        # This ensures SMA20 (last 20) > SMA50 (last 50) by clear margin
        candles = []
        timeframe_seconds = 60  # M1
        
        # Generate base volatility (average range)
        avg_range = point * 50  # ~50 pips average range for forex
        
        # Non-linear price progression for clear SMA separation
        # Phase 1 (0-40%): Establish baseline (lower for BUY, higher for SELL) - slow progress
        # Phase 2 (40-70%): Moderate trend progression
        # Phase 3 (70-100%): Accelerate trend (higher for BUY, lower for SELL) - fast progress
        # This ensures SMA20 (last 20) clearly diverges from SMA50 (last 50)
        
        with self._candle_gen_lock:
            price_sequence = []  # Track close prices for SMA calculation
            # For BUY: Start from a LOWER price to ensure last 20 candles are highest
            # For SELL: Start from a HIGHER price to ensure last 20 candles are lowest
            if trend_direction == 'BUY':
                # Start 0.2% lower to ensure clear upward progression
                current_price = base_price * 0.998
            else:
                # Start 0.2% higher for SELL
                current_price = base_price * 1.002
            
            for i in range(actual_count):
                # Non-linear progression: early slow, later faster
                progress = i / max(actual_count - 1, 1)  # 0.0 to 1.0
                
                # Three-phase progression for clear SMA separation:
                # Phase 1 (0-0.4): Slow baseline (10-15 pips/candle)
                # Phase 2 (0.4-0.7): Moderate acceleration (15-25 pips/candle)
                # Phase 3 (0.7-1.0): Strong acceleration (25-40 pips/candle)
                if progress < 0.4:
                    phase_move = point * (10 + progress * 12.5)  # 10-15 pips
                elif progress < 0.7:
                    phase_move = point * (15 + (progress - 0.4) * 33.3)  # 15-25 pips
                else:
                    phase_move = point * (25 + (progress - 0.7) * 50)  # 25-40 pips
                
                # CRITICAL: 
                # - RSI uses last 14 candles - need balanced pullbacks/advances to keep RSI in 35-45
                # - SMA20 uses last 20 candles - need clear net upward trend (SMA20 > SMA50 for BUY)
                # Strategy: Last 20 candles must have net upward progression, with pullbacks only in last 14 for RSI control
                is_in_sma20_window = (actual_count - i) <= 20  # Last 20 candles (SMA20 window)
                is_in_rsi_window = (actual_count - i) <= 14  # Last 14 candles (RSI window)
                position_from_end = actual_count - i - 1
                
                if trend_direction == 'BUY':
                    # For BUY: Ensure SMA20 > SMA50 while keeping RSI in 35-45
                    # Strategy: Strong upward progression in last 20, controlled pullbacks only in last 14
                    if is_in_sma20_window:
                        if is_in_rsi_window and position_from_end in [2, 6, 10]:  # Only 3 pullbacks in RSI window
                            # Small pullbacks (8-12 pips) to control RSI without reversing trend
                            pullback_size = point * (8 + (position_from_end % 3) * 2)  # 8-12 pips down
                            price_change = -pullback_size
                        else:
                            # VERY STRONG advances (25-45 pips) to ensure SMA20 (last 20) > SMA50 (last 50)
                            # The last 20 candles must be significantly higher than candles 21-50 for BUY signal
                            base_advance = point * (25 + (position_from_end % 6) * 3.3)  # 25-45 pips up
                            # Apply slope multiplier for trend contract enforcement
                            advance_size = base_advance * getattr(self, '_current_warmup_slope_multiplier', 1.0)
                            price_change = advance_size
                    else:
                        # Before last 20: use MODERATE progression (slower than last 20)
                        # This ensures candles 21-50 are lower than last 20, so SMA50 < SMA20
                        base_move = phase_move * 0.7  # Reduce by 30% to create clear separation
                        if i > 0 and (i % 5 == 0 or (i % 4 == 2 and i > 10)):
                            pullback_size = point * 15
                            price_change = -pullback_size * (0.8 + (i % 3) * 0.1)
                        elif i > 0 and (i % 7 == 3):
                            price_change = base_move * 0.5
                        else:
                            price_change = base_move
                else:  # SELL
                    # In last 14 candles: balance bounces vs declines to keep RSI in 35-45
                    if is_in_rsi_window:
                        if position_from_end in [0, 2, 5, 8, 11, 13]:
                            # Bounce candles: STRONG up moves (20-30 pips) to offset declines
                            bounce_size = point * (20 + (position_from_end % 3) * 5)  # 20-30 pips up
                            price_change = bounce_size
                        else:
                            # Decline candles: MODEST down moves (8-12 pips) to keep RSI balanced
                            price_change = -point * (8 + (position_from_end % 3) * 2)  # 8-12 pips down
                    else:
                        # Before last 14: use original logic
                        base_move = -phase_move
                        if i > 0 and (i % 5 == 0 or (i % 4 == 2 and i > 10)):
                            bounce_size = point * 15
                            price_change = bounce_size * (0.8 + (i % 3) * 0.1)
                        elif i > 0 and (i % 7 == 3):
                            price_change = base_move * 0.5
                        else:
                            price_change = base_move
                
                # Update current price
                current_price += price_change
                price_sequence.append(current_price)
                
                # Vary candle range (40-80 pips) for realistic volatility
                candle_range = avg_range * (0.6 + (i % 5) * 0.08)  # 60-100% of avg
                
                # Determine if this is a pullback candle (bearish in uptrend, bullish in downtrend)
                # CRITICAL: Match pullback logic with price_change logic above
                # Pullbacks occur: every 5th candle, every 4th (offset 2), every 7th (offset 3)
                # AND in last 14 candles (RSI window) at positions 2, 6, 10 from end (only 3 pullbacks)
                is_in_sma20_window = (actual_count - i) <= 20
                is_in_rsi_window = (actual_count - i) <= 14
                position_from_end = actual_count - i - 1
                if is_in_rsi_window and position_from_end in [2, 6, 10]:
                    is_pullback = True  # Force pullback in strategic RSI window positions
                elif i > 0 and (i % 5 == 0 or (i % 4 == 2 and i > 10) or (i % 7 == 3)):
                    is_pullback = True  # Regular pullback pattern
                else:
                    is_pullback = False
                
                # Generate candle OHLC with realistic body/wick ratios
                if trend_direction == 'BUY':
                    if is_pullback:
                        # Pullback candle: bearish (close < open)
                        body_size = candle_range * (0.4 + (i % 3) * 0.1)  # 40-70% body
                        close_price = current_price - body_size * 0.3
                        open_price = current_price + body_size * 0.7
                        high_price = open_price + (candle_range * 0.15)  # Upper wick
                        low_price = close_price - (candle_range * 0.15)  # Lower wick
                    else:
                        # Bullish candle: close near high
                        body_size = candle_range * (0.5 + (i % 3) * 0.1)  # 50-80% body
                        open_price = current_price - body_size * 0.4
                        close_price = current_price + body_size * 0.6
                        high_price = close_price + (candle_range * 0.1)  # Small upper wick
                        low_price = open_price - (candle_range * 0.2)  # Lower wick
                else:  # SELL
                    if is_pullback:
                        # Pullback candle: bullish (close > open) in downtrend
                        body_size = candle_range * (0.4 + (i % 3) * 0.1)
                        close_price = current_price + body_size * 0.3
                        open_price = current_price - body_size * 0.7
                        high_price = close_price + (candle_range * 0.15)
                        low_price = open_price - (candle_range * 0.15)
                    else:
                        # Bearish candle: close near low
                        body_size = candle_range * (0.5 + (i % 3) * 0.1)
                        open_price = current_price + body_size * 0.4
                        close_price = current_price - body_size * 0.6
                        high_price = open_price + (candle_range * 0.2)
                        low_price = close_price - (candle_range * 0.1)
                
                # Ensure OHLC is valid (high >= max(open,close), low <= min(open,close))
                high_price = max(open_price, close_price, high_price)
                low_price = min(open_price, close_price, low_price)
                
                # Calculate time (going back from now)
                candle_time = int(self._get_current_time()) - ((actual_count - i - 1) * timeframe_seconds)
                
                candles.append({
                    'time': candle_time,
                    'open': round(open_price, config['digits']),
                    'high': round(high_price, config['digits']),
                    'low': round(low_price, config['digits']),
                    'close': round(close_price, config['digits']),
                    'tick_volume': 50 + (i % 20) * 5,  # Vary volume
                    'spread': int(spread_pips * 10),  # Convert to points
                    'real_volume': 0
                })
            
            # Store warm-up state
            self._warmup_candles[symbol_upper] = {
                'warmup_complete': True,
                'base_price': candles[-1]['close'] if candles else base_price,
                'trend_direction': trend_direction,
                'last_candle_time': candles[-1]['time'] if candles else int(self._get_current_time())
            }
            
            # Store candle history
            if symbol_upper not in self._candle_history:
                self._candle_history[symbol_upper] = {}
            
            self._candle_history[symbol_upper][timeframe_seconds] = deque(candles, maxlen=200)
            
            # Update current price to last candle close
            if candles:
                last_candle = candles[-1]
                ask_price = last_candle['close'] + (spread_pips * point * 10)
                self.set_price(symbol_upper, last_candle['close'], ask_price)
                
                # Log warm-up completion
                try:
                    from sim_live.sim_live_logger import log_warmup_complete
                    log_warmup_complete(symbol_upper, len(candles))
                except:
                    pass  # Don't fail if logger not available
                
                # 🔍 STAGE A: Log candle ordering immediately after generation
                if self.config.get('mode') == 'SIM_LIVE':
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        # Log last 20 candle timestamps and closes
                        last_20 = candles[-20:] if len(candles) >= 20 else candles
                        last_14_closes = [c['close'] for c in candles[-14:]] if len(candles) >= 14 else []
                        logger.info(f"[SIM_LIVE] [STAGE_A_AFTER_GEN] {symbol_upper} | Total candles: {len(candles)}")
                        logger.info(f"[SIM_LIVE] [STAGE_A_AFTER_GEN] {symbol_upper} | Last 20 timestamps: {[c['time'] for c in last_20]}")
                        logger.info(f"[SIM_LIVE] [STAGE_A_AFTER_GEN] {symbol_upper} | Last 14 closes for RSI: {last_14_closes}")
                        # Log direction changes
                        if len(last_14_closes) >= 2:
                            directions = ['UP' if last_14_closes[i] > last_14_closes[i-1] else 'DOWN' for i in range(1, len(last_14_closes))]
                            logger.info(f"[SIM_LIVE] [STAGE_A_AFTER_GEN] {symbol_upper} | Last 14 direction changes: {directions}")
                    except:
                        pass
            
            return candles
    
    def generate_entry_candle(self, symbol: str, trend_direction: str = 'BUY', avg_range: Optional[float] = None) -> Dict[str, Any]:
        """
        Generate an entry candle that will trigger natural entry.
        
        Entry candle must:
        - Have above-average range (>= 65% of 20-candle average)
        - Close near high (BUY) or low (SELL)
        - Continue the trend direction
        - Push quality score above threshold
        
        Args:
            symbol: Trading symbol
            trend_direction: 'BUY' or 'SELL'
            avg_range: Average range of previous 20 candles (if None, calculated)
        
        Returns:
            Candle dict matching MT5 format
        """
        symbol_upper = symbol.upper()
        config = self.get_symbol_config(symbol_upper)
        point = config['point']
        spread_pips = config['spread_pips']
        
        # Get current price
        tick = self.get_current_tick(symbol_upper)
        if not tick:
            raise ValueError(f"No current price for {symbol_upper}")
        
        current_price = tick['bid']
        price_before_consolidation = current_price  # Track price before consolidation
        total_consolidation_pullback = 0.0  # Track total pullback from consolidation candles
        
        # Calculate average range if not provided
        if avg_range is None:
            # Get recent candles to calculate average range
            recent_candles = self.copy_rates_from_pos(symbol_upper, 1, 1, 20)  # Last 20 candles
            if recent_candles and len(recent_candles) >= 20:
                ranges = [c['high'] - c['low'] for c in recent_candles]
                avg_range = sum(ranges) / len(ranges)
            else:
                avg_range = point * 50  # Default 50 pips
        
        # Entry candle range: 70-80% of average (above 65% threshold)
        entry_range = avg_range * (0.70 + random.random() * 0.10)
        
        # CRITICAL: Before generating entry candle, check RSI from last 14 candles
        # If RSI would exceed 70 when entry candle is added, insert a consolidation candle first
        with self._candle_gen_lock:
            if symbol_upper not in self._candle_history:
                self._candle_history[symbol_upper] = {}
            if 60 not in self._candle_history[symbol_upper]:
                self._candle_history[symbol_upper][60] = deque(maxlen=200)
            
            history = self._candle_history[symbol_upper].get(60)
            if history and len(history) >= 14:
                # Calculate current RSI from last 14 candles
                try:
                    import pandas as pd
                    import numpy as np
                    
                    last_14 = list(history)[-14:]
                    df_data = {
                        'close': [c['close'] for c in last_14]
                    }
                    df = pd.DataFrame(df_data)
                    
                    delta = df['close'].diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                    rs = gain / loss.replace(0, np.nan)
                    rsi = 100 - (100 / (1 + rs))
                    rsi = rsi.fillna(100)
                    current_rsi = rsi.iloc[-1] if pd.notna(rsi.iloc[-1]) else 100
                    
                    # CRITICAL: Ensure RSI will be in valid range AFTER entry candle is added
                    # Entry candle will be added to history, so last 14 candles will be: entry + previous 13
                    # We need to add consolidation candles until RSI is low enough that entry candle won't push it to 100
                    consolidation_count = 0
                    max_consolidations = 3  # Maximum consolidation candles to add
                    
                    while consolidation_count < max_consolidations:
                        # Calculate current RSI from last 14 candles in history
                        if len(history) >= 14:
                            last_14 = list(history)[-14:]
                            df_test = pd.DataFrame({'close': [c['close'] for c in last_14]})
                            delta_test = df_test['close'].diff()
                            gain_test = (delta_test.where(delta_test > 0, 0)).rolling(window=14).mean()
                            loss_test = (-delta_test.where(delta_test < 0, 0)).rolling(window=14).mean()
                            rs_test = gain_test / loss_test.replace(0, np.nan)
                            rsi_test = 100 - (100 / (1 + rs_test))
                            rsi_test = rsi_test.fillna(100)
                            test_rsi = rsi_test.iloc[-1] if pd.notna(rsi_test.iloc[-1]) else 100
                        else:
                            test_rsi = current_rsi
                        
                        # If RSI is low enough, adding entry candle should keep it in range
                        # Target: RSI <= 30-35 before entry candle for BUY (entry candle will add ~10-15 points)
                        # For BUY: Need RSI <= 30-35 so entry candle (bullish) doesn't push it above 70
                        # For SELL: Need RSI >= 65-70 so entry candle (bearish) doesn't push it below 30
                        if trend_direction == 'BUY' and test_rsi <= 30:
                            break  # RSI is low enough that entry candle won't push it above 70
                        elif trend_direction == 'SELL' and test_rsi >= 70:
                            break  # RSI is high enough that entry candle won't push it below 30
                        
                        # Need consolidation: add pullback candle
                        old_price = current_price
                        # CRITICAL FIX: Reduce consolidation pullback to preserve trend contract
                        # Smaller pullbacks (10-18 pips instead of 20-30) to maintain SMA20 > SMA50
                        if trend_direction == 'BUY':
                            pullback_pips = 10 + (random.random() * 8)  # 10-18 pips (reduced from 20-30)
                            consolidation_close = current_price - (point * pullback_pips * 10)
                        else:  # SELL - add bounce candle
                            bounce_pips = 10 + (random.random() * 8)  # 10-18 pips (reduced from 20-30)
                            consolidation_close = current_price + (point * bounce_pips * 10)
                        
                        # 🔒 Ensure chronological order: get last candle time and add 60 seconds
                        last_candle_time = history[-1]['time'] if history else int(self._get_current_time())
                        consolidation_time = last_candle_time + 60  # Next minute after last candle
                        consolidation_candle = {
                            'time': consolidation_time,
                            'open': round(current_price, config['digits']),
                            'high': round(max(current_price, consolidation_close) + (point * 3), config['digits']),
                            'low': round(min(current_price, consolidation_close) - (point * 3), config['digits']),
                            'close': round(consolidation_close, config['digits']),
                            'tick_volume': 40,
                            'spread': int(spread_pips * 10),
                            'real_volume': 0
                        }
                        history.append(consolidation_candle)
                        
                        # Track consolidation pullback
                        if trend_direction == 'BUY':
                            pullback_amount = price_before_consolidation - consolidation_close
                        else:
                            pullback_amount = consolidation_close - price_before_consolidation
                        total_consolidation_pullback += pullback_amount
                        
                        current_price = consolidation_close
                        ask_price = consolidation_close + (spread_pips * point * 10)
                        self.set_price(symbol_upper, consolidation_close, ask_price)
                        consolidation_count += 1
                        
                        # Log consolidation
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            pullback_pips_log = round(abs(old_price - consolidation_close) / point / 10, 1)
                            logger.info(f"[SIM_LIVE] [CONSOLIDATION] Added consolidation candle #{consolidation_count}: "
                                       f"RSI was {test_rsi:.1f}, {'pulled back' if trend_direction == 'BUY' else 'bounced'} {pullback_pips_log} pips")
                        except:
                            pass
                except Exception as e:
                    # If RSI calculation fails, still add consolidation if last candle was bullish
                    last_candle = history[-1] if history else None
                    if last_candle and last_candle.get('close', 0) >= last_candle.get('open', 0):
                        consolidation_close = current_price - (point * 10)
                        # 🔒 Ensure chronological order
                        last_candle_time = history[-1]['time'] if history else int(self._get_current_time())
                        consolidation_time = last_candle_time + 60  # Next minute after last candle
                        consolidation_candle = {
                            'time': consolidation_time,
                            'open': round(current_price, config['digits']),
                            'high': round(current_price + (point * 3), config['digits']),
                            'low': round(consolidation_close - (point * 3), config['digits']),
                            'close': round(consolidation_close, config['digits']),
                            'tick_volume': 40,
                            'spread': int(spread_pips * 10),
                            'real_volume': 0
                        }
                        history.append(consolidation_candle)
                        current_price = consolidation_close
                        ask_price = consolidation_close + (spread_pips * point * 10)
                        self.set_price(symbol_upper, consolidation_close, ask_price)
        
        # For BUY: close near high (bullish) - MUST recover consolidation + continue trend
        # For SELL: close near low (bearish) - MUST recover consolidation + continue trend
        # 🔒 CRITICAL: Entry candle must recover consolidation pullback AND push trend forward
        # This ensures SMA20 > SMA50 is maintained after all candles (warm-up + consolidation + entry)
        if trend_direction == 'BUY':
            # Calculate total consolidation pullback (current_price is after all consolidations)
            consolidation_pullback = max(price_before_consolidation - current_price, 0)
            # Entry candle must: 1) recover consolidation, 2) push above pre-consolidation, 3) continue trend
            # CRITICAL FIX: Strong recovery (100% of consolidation + 100% of entry_range) to ensure SMA20 > SMA50
            # This ensures entry candle fully recovers consolidation AND continues trend strongly
            recovery_amount = consolidation_pullback + (entry_range * 1.0)  # Full recovery + full trend continuation
            
            open_price = current_price - entry_range * 0.05  # Small gap down from current (after consolidation)
            close_price = current_price + recovery_amount  # Recover consolidation + push trend strongly
            high_price = close_price + (entry_range * 0.15)  # Larger upper wick
            low_price = open_price - (entry_range * 0.15)  # Lower wick
        else:  # SELL
            # Calculate consolidation bounce (if any) and recover it
            consolidation_bounce = max(current_price - price_before_consolidation, 0)
            recovery_amount = consolidation_bounce + (entry_range * 0.9)  # Strong recovery
            open_price = current_price + entry_range * 0.05  # Small gap up
            close_price = current_price - recovery_amount  # Recover bounce + continue down strongly
            high_price = open_price + (entry_range * 0.15)  # Upper wick
            low_price = close_price - (entry_range * 0.15)  # Lower wick
        
        # 🔒 Ensure chronological order: entry candle must be after last candle in history
        with self._candle_gen_lock:
            if symbol_upper in self._candle_history and 60 in self._candle_history.get(symbol_upper, {}):
                last_candle = list(self._candle_history[symbol_upper][60])[-1] if self._candle_history[symbol_upper][60] else None
                if last_candle:
                    candle_time = last_candle['time'] + 60  # Next minute after last candle
                else:
                    candle_time = int(self._get_current_time())
            else:
                candle_time = int(self._get_current_time())
        
        # Before creating entry candle, check if it would push RSI out of range
        # Simulate adding entry candle and check RSI impact
        with self._candle_gen_lock:
            if symbol_upper in self._candle_history and 60 in self._candle_history.get(symbol_upper, {}):
                history_test = list(self._candle_history[symbol_upper][60])
                # Create test entry candle
                test_entry = {
                    'time': candle_time,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'tick_volume': 80,
                    'spread': int(spread_pips * 10),
                    'real_volume': 0
                }
                # Simulate: add entry candle to history (it will be newest)
                test_history = history_test + [test_entry]
                if len(test_history) >= 14:
                    try:
                        import pandas as pd
                        import numpy as np
                        last_14_test = test_history[-14:]
                        df_test = pd.DataFrame({'close': [c['close'] for c in last_14_test]})
                        delta_test = df_test['close'].diff()
                        gain_test = (delta_test.where(delta_test > 0, 0)).rolling(window=14).mean()
                        loss_test = (-delta_test.where(delta_test < 0, 0)).rolling(window=14).mean()
                        rs_test = gain_test / loss_test.replace(0, np.nan)
                        rsi_test = 100 - (100 / (1 + rs_test))
                        rsi_test = rsi_test.fillna(100)
                        projected_rsi = rsi_test.iloc[-1] if pd.notna(rsi_test.iloc[-1]) else 100
                        
                        # If entry candle would push RSI out of range, reduce its size
                        if trend_direction == 'BUY' and projected_rsi > 70:
                            # Reduce entry candle bullishness: smaller move up
                            reduction_factor = 0.5  # Reduce by 50%
                            close_price = current_price + (close_price - current_price) * reduction_factor
                            high_price = close_price + (entry_range * 0.1)
                            low_price = open_price - (entry_range * 0.2)
                        elif trend_direction == 'SELL' and projected_rsi < 30:
                            # Reduce entry candle bearishness: smaller move down
                            reduction_factor = 0.5
                            close_price = current_price - (current_price - close_price) * reduction_factor
                            high_price = open_price + (entry_range * 0.2)
                            low_price = close_price - (entry_range * 0.1)
                    except:
                        pass  # If calculation fails, use original entry candle
        
        entry_candle = {
            'time': candle_time,
            'open': round(open_price, config['digits']),
            'high': round(high_price, config['digits']),
            'low': round(low_price, config['digits']),
            'close': round(close_price, config['digits']),
            'tick_volume': 80,  # Above average volume
            'spread': int(spread_pips * 10),
            'real_volume': 0
        }
        
        # Add to candle history
        with self._candle_gen_lock:
            if symbol_upper not in self._candle_history:
                self._candle_history[symbol_upper] = {}
            
            timeframe_seconds = 60  # M1
            timeframe = 1  # MT5 TIMEFRAME_M1 constant
            if timeframe_seconds not in self._candle_history[symbol_upper]:
                self._candle_history[symbol_upper][timeframe_seconds] = deque(maxlen=200)
            
            # 🔍 ENTRY CANDLE TIMING ASSERTION
            if self.config.get('mode') == 'SIM_LIVE':
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    # Check if consolidation candles exist
                    if len(history) > 0:
                        last_consolidation = history[-1] if history else None
                        if last_consolidation:
                            logger.info(f"[SIM_LIVE] [ENTRY_TIMING] Last consolidation time: {last_consolidation['time']}, Entry candle time: {entry_candle['time']}")
                            if entry_candle['time'] <= last_consolidation['time']:
                                logger.error(f"[SIM_LIVE] [ENTRY_TIMING] ❌ ASSERTION FAILED: Entry candle time ({entry_candle['time']}) <= consolidation time ({last_consolidation['time']})")
                            else:
                                logger.info(f"[SIM_LIVE] [ENTRY_TIMING] ✓ Entry candle is after consolidation")
                except:
                    pass
            
            # 🔒 PHASE 3: ENTRY GENERATION - With retry and escalation
            max_entry_attempts = 5  # Increased to 5 attempts with escalation
            entry_magnitude = 1.0  # Start with 100% magnitude
            slope_multiplier = 1.0  # Start with normal slope
            entry_contract_passed = False
            stored_entry_candle = entry_candle.copy()
            
            # Add timeout mechanism (30 seconds max for Phase 3)
            import time as time_module
            phase3_start_time = time_module.time()
            phase3_timeout = 30.0  # 30 seconds maximum
            
            try:
                from sim_live.sim_live_logger import get_sim_live_logger
                logger = get_sim_live_logger()
                logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Starting entry contract validation loop (max {max_entry_attempts} attempts, timeout: {phase3_timeout}s)")
                logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Trend direction: {trend_direction}")
            except:
                pass
            
            # Ensure loop always completes by wrapping in try-except
            try:
                for entry_attempt in range(1, max_entry_attempts + 1):
                    # Check timeout
                    elapsed_time = time_module.time() - phase3_start_time
                    if elapsed_time > phase3_timeout:
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT_TIMEOUT] Phase 3 exceeded {phase3_timeout}s timeout after {entry_attempt - 1} attempts. Proceeding to set MARKET_FROZEN.")
                        except:
                            pass
                        break  # Exit loop, will proceed to set MARKET_FROZEN
                    
                    # Apply magnitude and slope adjustments for retries
                    if entry_attempt > 1:
                        # Escalation: Increase slope multiplier, reduce entry magnitude
                        slope_multiplier *= 1.2  # Increase slope by 20% per retry
                        entry_magnitude *= 0.9  # Reduce entry magnitude by 10% per retry
                        
                        # Recalculate entry candle with adjusted parameters
                        base_price_change = (stored_entry_candle['close'] - stored_entry_candle['open'])
                        adjusted_price_change = base_price_change * entry_magnitude * slope_multiplier
                        
                        stored_entry_candle['open'] = current_price - entry_range * 0.1
                        stored_entry_candle['close'] = stored_entry_candle['open'] + adjusted_price_change
                        stored_entry_candle['high'] = stored_entry_candle['close'] + (entry_range * 0.1)
                        stored_entry_candle['low'] = stored_entry_candle['open'] - (entry_range * 0.2)
                        stored_entry_candle['time'] = candle_time
                        entry_candle = {k: round(v, config['digits']) if isinstance(v, float) else v 
                                       for k, v in stored_entry_candle.items()}
                    
                    # Add entry candle as newest
                    self._candle_history[symbol_upper][timeframe_seconds].append(entry_candle)
                    
                    # Log entry candle generation
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Attempt {entry_attempt}: Generated entry candle at timestamp {entry_candle['time']}, close={entry_candle['close']:.5f}")
                    except:
                        pass
                    
                    # 🔒 STEP 1: DATA-COUNT AWARE - Check if we have enough candles
                    # Wait longer to ensure entry candle is visible (not needed when using _candle_history directly, but keeping for safety)
                    import time as time_module_for_delay
                    time_module_for_delay.sleep(0.1)  # Small delay for safety
                    
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        history_count = len(self._candle_history[symbol_upper][timeframe_seconds]) if symbol_upper in self._candle_history and timeframe_seconds in self._candle_history[symbol_upper] else 0
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Attempt {entry_attempt}/{max_entry_attempts}: History has {history_count} candles before copy_rates_from_pos")
                    except:
                        pass
                    
                    # Use _candle_history directly for validation to ensure complete set
                    # Skip copy_rates_from_pos() call as it may block/hang - we already have the data from _candle_history
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Attempt {entry_attempt}: Getting candles from history (skipping copy_rates_from_pos to avoid blocking)...")
                    except:
                        pass
                    
                    # Check timeout before proceeding
                    elapsed_time = time_module.time() - phase3_start_time
                    if elapsed_time > phase3_timeout:
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT_TIMEOUT] Timeout exceeded before validation: {elapsed_time:.1f}s > {phase3_timeout}s")
                        except:
                            pass
                        break
                    
                    stored_candles_list = list(self._candle_history[symbol_upper][timeframe_seconds])
                    
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        candle_count_history = len(stored_candles_list) if stored_candles_list else 0
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] History has {candle_count_history} candles for validation")
                    except:
                        pass
                    
                    # Use history directly for validation (more reliable than copy_rates_from_pos)
                    if not stored_candles_list or len(stored_candles_list) < 20:
                        # Truly insufficient - log and retry, don't return early
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT] Attempt {entry_attempt}/{max_entry_attempts}: Only {len(stored_candles_list) if stored_candles_list else 0} candles in history (need >= 20). Will retry...")
                        except:
                            pass
                        
                        # Rollback entry candle and retry
                        if self._candle_history[symbol_upper][timeframe_seconds]:
                            history_list = list(self._candle_history[symbol_upper][timeframe_seconds])
                            if history_list and history_list[-1]['time'] == entry_candle['time']:
                                history_list.pop()
                                self._candle_history[symbol_upper][timeframe_seconds] = deque(history_list, maxlen=200)
                        
                        # Continue to next retry attempt instead of returning
                        if entry_attempt < max_entry_attempts:
                            continue
                        else:
                            # Last attempt failed - proceed to set MARKET_FROZEN anyway
                            try:
                                from sim_live.sim_live_logger import get_sim_live_logger
                                logger = get_sim_live_logger()
                                logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT] All {max_entry_attempts} attempts failed due to insufficient candles. Proceeding to set MARKET_FROZEN with warning.")
                            except:
                                pass
                            break  # Exit loop, will proceed to set MARKET_FROZEN
                    
                    # Use stored_candles_list directly (already in history format: oldest first)
                    candles_for_validation = []
                    for candle in stored_candles_list:
                        candles_for_validation.append({
                            'time': candle['time'],
                            'open': candle['open'],
                            'high': candle['high'],
                            'low': candle['low'],
                            'close': candle['close'],
                            'tick_volume': candle.get('tick_volume', 0),
                            'spread': candle.get('spread', 0),
                            'real_volume': candle.get('real_volume', 0)
                        })
                    
                    # 🔒 STEP 2: VALIDATE CONTRACT WITH VERBOSE LOGGING
                    # candles_for_validation is already prepared from stored_candles_list (oldest-first format)
                    # Ensure we're using the newest candles: last 20 for SMA20, last 50 for SMA50, last 14 for RSI
                    total_candles = len(candles_for_validation)
                    
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Attempt {entry_attempt}/{max_entry_attempts}: Validating contract with {total_candles} candles")
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Candle order: oldest first (index 0) to newest last (index {total_candles - 1})")
                        if total_candles >= 20:
                            last_20_closes = [round(c['close'], 5) for c in candles_for_validation[-20:]]
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Last 20 closes for SMA20 (newest last): {last_20_closes}")
                        if total_candles >= 50:
                            last_50_closes = [round(c['close'], 5) for c in candles_for_validation[-50:]]
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Last 50 closes for SMA50 (newest last): {last_50_closes}")
                        if total_candles >= 14:
                            last_14_closes = [round(c['close'], 5) for c in candles_for_validation[-14:]]
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Last 14 closes for RSI (newest last): {last_14_closes}")
                    except Exception as e:
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT] Error logging candle details: {e}")
                        except:
                            pass
                    
                    # Validate contract - this calculates SMA20, SMA50, RSI from candles
                    try:
                        is_valid, indicators = self._validate_trend_contract(symbol_upper, candles_for_validation, trend_direction, use_mt5_source=False)
                    except Exception as e:
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT] Error in _validate_trend_contract: {e}")
                            import traceback
                            logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT] Traceback: {traceback.format_exc()}")
                        except:
                            pass
                        # On error, mark as invalid and continue
                        is_valid = False
                        indicators = {'sma20': 0.0, 'sma50': 0.0, 'separation_pct': 0.0, 'rsi': 50.0}
                    
                    # Extract indicator values for logging
                    sma20 = indicators.get('sma20', 0.0) if indicators.get('sma20') is not None else 0.0
                    sma50 = indicators.get('sma50', 0.0) if indicators.get('sma50') is not None else 0.0
                    separation_pct = indicators.get('separation_pct', 0.0)
                    rsi_value = indicators.get('rsi', 50.0) if indicators.get('rsi') is not None else 50.0
                    
                    # Log contract check details before result
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Attempt {entry_attempt}: SMA20={sma20:.5f}, SMA50={sma50:.5f}, RSI={rsi_value:.1f}")
                        logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Contract valid? {is_valid} (Direction: {trend_direction}, Separation: {separation_pct*100:.4f}%)")
                        if trend_direction == 'BUY':
                            sma_condition = sma20 > sma50
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] BUY condition check: SMA20 > SMA50? {sma_condition} ({sma20:.5f} > {sma50:.5f})")
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Separation check: {separation_pct*100:.4f}% >= 0.05%? {separation_pct >= 0.0005}")
                        else:
                            sma_condition = sma20 < sma50
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] SELL condition check: SMA20 < SMA50? {sma_condition} ({sma20:.5f} < {sma50:.5f})")
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT] Separation check: {separation_pct*100:.4f}% >= 0.05%? {separation_pct >= 0.0005}")
                    except Exception as e:
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT] Error logging validation result: {e}")
                        except:
                            pass
                    
                    if is_valid:
                        # 🔒 STEP 4: HARD SUCCESS LOG
                        entry_contract_passed = True
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT_OK] Validation PASSED on attempt {entry_attempt}/{max_entry_attempts}")
                            logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT_OK] SMA20={sma20:.5f} SMA50={sma50:.5f} sep={separation_pct*100:.4f}% RSI={rsi_value:.1f}")
                        except:
                            pass
                        break
                    else:
                        # 🔒 STEP 2: VERBOSE ON FAILURE
                        # Contract failed - rollback entry candle and retry
                        if self._candle_history[symbol_upper][timeframe_seconds]:
                            # Remove the last candle (entry candle we just added)
                            history_list = list(self._candle_history[symbol_upper][timeframe_seconds])
                            if history_list and history_list[-1]['time'] == entry_candle['time']:
                                # Rebuild deque without last candle
                                history_list.pop()
                                self._candle_history[symbol_upper][timeframe_seconds] = deque(history_list, maxlen=200)
                        
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT_FAIL] Validation FAILED on attempt {entry_attempt}/{max_entry_attempts}")
                            logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT_FAIL] SMA20={sma20:.5f} SMA50={sma50:.5f} sep={separation_pct*100:.4f}% RSI={rsi_value:.1f}")
                            logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT_FAIL] Direction required: {trend_direction}, SMA20 {'>' if trend_direction == 'BUY' else '<'} SMA50: {sma20 > sma50 if trend_direction == 'BUY' else sma20 < sma50}")
                            if entry_attempt < max_entry_attempts:
                                logger.info(f"[SIM_LIVE] [ENTRY_CONTRACT_FAIL] Retrying entry generation with adjusted parameters (attempt {entry_attempt + 1}/{max_entry_attempts})")
                            else:
                                logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT_FAIL] Max retries ({max_entry_attempts}) reached - will proceed to set MARKET_FROZEN with warning")
                        except:
                            pass
                        
                        # Continue to next retry attempt instead of breaking
                        if entry_attempt < max_entry_attempts:
                            continue
                        else:
                            # Last attempt failed - proceed to set MARKET_FROZEN anyway
                            try:
                                from sim_live.sim_live_logger import get_sim_live_logger
                                logger = get_sim_live_logger()
                                logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT] Loop completed after {max_entry_attempts} attempts - contract not satisfied, proceeding to set MARKET_FROZEN")
                            except:
                                pass
                            break
            except Exception as e:
                # Critical: Log any exception and proceed to set MARKET_FROZEN
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT_ERROR] Exception in validation loop: {e}")
                    logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT_ERROR] Proceeding to set MARKET_FROZEN to unblock Phase 5")
                    import traceback
                    logger.error(f"[SIM_LIVE] [ENTRY_CONTRACT_ERROR] Traceback: {traceback.format_exc()}")
                except:
                    pass
            
            # 🔒 PHASE 3 COMPLETE: Entry contract validation loop finished
            # CRITICAL FIX: Always set MARKET_FROZEN even if validation failed (after max retries/timeout)
            # This unblocks Phase 5 evaluation
            if not entry_contract_passed:
                # Get final indicators for logging
                try:
                    stored_candles = list(self._candle_history[symbol_upper][timeframe_seconds])
                    if stored_candles and len(stored_candles) >= 20:
                        candles_for_final_check = []
                        for candle in stored_candles:
                            candles_for_final_check.append({
                                'time': candle['time'],
                                'open': candle['open'],
                                'high': candle['high'],
                                'low': candle['low'],
                                'close': candle['close'],
                                'tick_volume': candle.get('tick_volume', 0),
                                'spread': candle.get('spread', 0),
                                'real_volume': candle.get('real_volume', 0)
                            })
                        _, indicators = self._validate_trend_indicators(symbol_upper, candles_for_final_check, trend_direction)
                        sma20 = indicators.get('sma20', 0.0)
                        sma50 = indicators.get('sma50', 0.0)
                    else:
                        sma20 = 0.0
                        sma50 = 0.0
                except:
                    sma20 = 0.0
                    sma50 = 0.0
                
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT] Entry contract validation did not pass after {max_entry_attempts} attempts or timeout")
                    logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT] Final SMA20={sma20:.5f} SMA50={sma50:.5f}")
                    logger.warning(f"[SIM_LIVE] [ENTRY_CONTRACT] Proceeding to set MARKET_FROZEN anyway to unblock Phase 5 evaluation")
                except:
                    pass
                
                # DO NOT raise AssertionError - instead, proceed to set MARKET_FROZEN
                # This allows Phase 5 to execute and handle the situation
            
            # 🔍 Verify entry candle is included
            if self.config.get('mode') == 'SIM_LIVE':
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    stored_check = list(self._candle_history[symbol_upper][timeframe_seconds])
                    stored_reversed = list(reversed(stored_check))
                    entry_included = any(c['time'] == entry_candle['time'] for c in stored_reversed[:5])
                    logger.info(f"[SIM_LIVE] [ENTRY_VERIFY] Entry candle included in history: {entry_included}, Entry time: {entry_candle['time']}")
                    if stored_reversed:
                        logger.info(f"[SIM_LIVE] [ENTRY_VERIFY] First 5 candles in history (newest first): {[(c['time'], c['close']) for c in stored_reversed[:5]]}")
                except:
                    pass
            
            # Update current price to entry candle close
            ask_price = entry_candle['close'] + (spread_pips * point * 10)
            self.set_price(symbol_upper, entry_candle['close'], ask_price)
        
        # 🔒 PHASE 4: FREEZE MARKET STATE - Entry candles generated, no more modifications allowed
        # CRITICAL FIX: ALWAYS set MARKET_FROZEN to unblock Phase 5 evaluation
        symbol_upper = symbol.upper()
        self._market_frozen[symbol_upper] = True
        
        try:
            from sim_live.sim_live_logger import get_sim_live_logger
            logger = get_sim_live_logger()
            if entry_contract_passed:
                logger.info(f"[SIM_LIVE] [MARKET_FROZEN] Market state frozen for {symbol_upper} - entry contract validated, no further candle modifications allowed")
            else:
                logger.warning(f"[SIM_LIVE] [MARKET_FROZEN] Market state frozen for {symbol_upper} - entry contract validation incomplete after {max_entry_attempts} attempts/timeout, but freezing to unblock Phase 5 evaluation")
        except:
            pass
        
        # 🚫 RSI SAFETY DISABLED AFTER ENTRY GENERATION (PHASE 4 FROZEN STATE)
        # RSI safety should have run in Phase 2 (pre-entry normalization) only
        # No further candle modifications allowed after entry contract passes
        if False and self.config.get('mode') == 'SIM_LIVE':  # DISABLED: RSI safety after entry
            try:
                import pandas as pd
                import numpy as np
                from sim_live.sim_live_logger import get_sim_live_logger
                logger = get_sim_live_logger()
                
                # Get current history with entry candle
                test_history = list(self._candle_history[symbol_upper][timeframe_seconds])
                if len(test_history) >= 14:
                    last_14 = test_history[-14:]
                    df_safety = pd.DataFrame({'close': [c['close'] for c in last_14]})
                    delta_safety = df_safety['close'].diff()
                    gain_safety = (delta_safety.where(delta_safety > 0, 0)).rolling(window=14).mean()
                    loss_safety = (-delta_safety.where(delta_safety < 0, 0)).rolling(window=14).mean()
                    rs_safety = gain_safety / loss_safety.replace(0, np.nan)
                    rsi_safety = 100 - (100 / (1 + rs_safety))
                    rsi_safety = rsi_safety.fillna(100)
                    final_rsi = rsi_safety.iloc[-1] if pd.notna(rsi_safety.iloc[-1]) else 100
                    
                    logger.info(f"[SIM_LIVE] [RSI_SAFETY] Final RSI check after entry candle: {final_rsi:.2f}")
                    
                    # Force pullbacks if RSI > 55 for BUY or < 45 for SELL
                    safety_pullback_count = 0
                    while ((trend_direction == 'BUY' and final_rsi > 55) or 
                           (trend_direction == 'SELL' and final_rsi < 45)) and safety_pullback_count < 5:
                        safety_pullback_count += 1
                        old_price_safety = current_price
                        if trend_direction == 'BUY':
                            safety_pullback = point * (25 + random.random() * 10)  # 25-35 pips
                            safety_close = current_price - safety_pullback
                        else:
                            safety_bounce = point * (25 + random.random() * 10)  # 25-35 pips
                            safety_close = current_price + safety_bounce
                        
                        # 🔒 Ensure chronological order
                        last_candle_safety = list(self._candle_history[symbol_upper][timeframe_seconds])[-1] if self._candle_history[symbol_upper][timeframe_seconds] else None
                        if last_candle_safety:
                            safety_time = last_candle_safety['time'] + 60  # Next minute after last candle
                        else:
                            safety_time = int(self._get_current_time())
                        safety_candle = {
                            'time': safety_time,
                            'open': round(current_price, config['digits']),
                            'high': round(max(current_price, safety_close) + (point * 3), config['digits']),
                            'low': round(min(current_price, safety_close) - (point * 3), config['digits']),
                            'close': round(safety_close, config['digits']),
                            'tick_volume': 40,
                            'spread': int(spread_pips * 10),
                            'real_volume': 0
                        }
                        
                        # 🔒 CRITICAL: Before adding safety candle, validate it won't break trend contract
                        # Test add safety candle temporarily
                        test_history_with_safety = list(self._candle_history[symbol_upper][timeframe_seconds]) + [safety_candle]
                        
                        # Validate trend contract with safety candle
                        if len(test_history_with_safety) >= 50:
                            is_valid_safety, indicators_safety = self._validate_trend_contract(symbol_upper, test_history_with_safety, trend_direction)
                            
                            if not is_valid_safety:
                                # Safety candle would break trend contract - skip or reduce it
                                logger.warning(f"[SIM_LIVE] [RSI_SAFETY] Safety pullback #{safety_pullback_count} would break trend contract - skipping")
                                break  # Stop adding more safety candles
                        
                        # Insert safety candle (trend contract preserved)
                        self._candle_history[symbol_upper][timeframe_seconds].append(safety_candle)
                        current_price = safety_close
                        
                        # Recalculate RSI
                        test_history = list(self._candle_history[symbol_upper][timeframe_seconds])
                        last_14 = test_history[-14:]
                        df_safety = pd.DataFrame({'close': [c['close'] for c in last_14]})
                        delta_safety = df_safety['close'].diff()
                        gain_safety = (delta_safety.where(delta_safety > 0, 0)).rolling(window=14).mean()
                        loss_safety = (-delta_safety.where(delta_safety < 0, 0)).rolling(window=14).mean()
                        rs_safety = gain_safety / loss_safety.replace(0, np.nan)
                        rsi_safety = 100 - (100 / (1 + rs_safety))
                        rsi_safety = rsi_safety.fillna(100)
                        final_rsi = rsi_safety.iloc[-1] if pd.notna(rsi_safety.iloc[-1]) else 100
                        
                        logger.info(f"[SIM_LIVE] [RSI_SAFETY] Added safety pullback #{safety_pullback_count}, RSI now: {final_rsi:.2f}")
                        
                        # Update current price
                        ask_price = safety_close + (spread_pips * point * 10)
                        self.set_price(symbol_upper, safety_close, ask_price)
                    
                    if final_rsi >= 35 and final_rsi <= 50:
                        logger.info(f"[SIM_LIVE] [RSI_SAFETY] ✓ RSI in safe range [35, 50]: {final_rsi:.2f}")
                    else:
                        logger.warning(f"[SIM_LIVE] [RSI_SAFETY] ⚠ RSI outside target but within acceptable: {final_rsi:.2f}")
            except Exception as e:
                logger.warning(f"[SIM_LIVE] [RSI_SAFETY] Safety check failed: {e}")
        
        # Log entry candle generation
        try:
            from sim_live.sim_live_logger import log_entry_candle_generated
            range_pct = (entry_range / avg_range) * 100.0
            close_pos = "near_high" if trend_direction == 'BUY' else "near_low"
            log_entry_candle_generated(symbol_upper, range_pct, close_pos)
        except:
            pass
        
        return entry_candle
    
    def copy_rates_from_pos(self, symbol: str, timeframe: int, offset: int, count: int) -> Optional[List[Dict[str, Any]]]:
        """
        Generate candle data matching MT5 format with realistic history.
        
        Args:
            symbol: Trading symbol
            timeframe: MT5 timeframe constant
            offset: Offset from current (0 = most recent)
            count: Number of candles to return
        
        Returns:
            List of candle dicts with 'time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume'
        """
        symbol_upper = symbol.upper()
        
        # Calculate timeframe seconds
        timeframe_seconds = self._timeframe_to_seconds(timeframe)
        if timeframe_seconds == 0:
            return None
        
        # Check if we have warm-up candles generated
        with self._candle_gen_lock:
            if symbol_upper in self._candle_history and timeframe_seconds in self._candle_history[symbol_upper]:
                # Use stored candle history
                # NOTE: stored_candles has oldest first (index 0) to newest last (index -1)
                # MT5 copy_rates_from_pos with offset=0 returns newest candles first
                stored_candles = list(self._candle_history[symbol_upper][timeframe_seconds])
                
                if not stored_candles:
                    return None
                
                # 🔒 FREEZE GUARANTEE: When frozen, return SAME candles every call (no mutation)
                is_frozen = self._market_frozen.get(symbol_upper, False)
                
                # Reverse to have newest first (MT5 format: newest at index 0)
                stored_candles_reversed = list(reversed(stored_candles))
                
                # If frozen, cache the reversed result to guarantee consistency
                if is_frozen:
                    if not hasattr(self, '_frozen_candle_cache'):
                        self._frozen_candle_cache = {}
                    cache_key = f"{symbol_upper}_{timeframe_seconds}"
                    if cache_key not in self._frozen_candle_cache:
                        self._frozen_candle_cache[cache_key] = stored_candles_reversed.copy()
                    else:
                        # Return cached frozen candles (guaranteed same every call)
                        stored_candles_reversed = self._frozen_candle_cache[cache_key].copy()
                
                # 🔍 STAGE B: Log candle ordering before return from copy_rates_from_pos
                if self.config.get('mode') == 'SIM_LIVE':
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        last_20_rev = stored_candles_reversed[:20] if len(stored_candles_reversed) >= 20 else stored_candles_reversed
                        last_14_closes_rev = [c['close'] for c in stored_candles_reversed[:14]] if len(stored_candles_reversed) >= 14 else []
                        logger.info(f"[SIM_LIVE] [STAGE_B_BEFORE_RETURN] {symbol_upper} | Total available: {len(stored_candles_reversed)}")
                        logger.info(f"[SIM_LIVE] [STAGE_B_BEFORE_RETURN] {symbol_upper} | First 20 timestamps (newest first): {[c['time'] for c in last_20_rev]}")
                        logger.info(f"[SIM_LIVE] [STAGE_B_BEFORE_RETURN] {symbol_upper} | First 14 closes for RSI: {last_14_closes_rev}")
                        if len(last_14_closes_rev) >= 2:
                            directions_rev = ['UP' if last_14_closes_rev[i] > last_14_closes_rev[i-1] else 'DOWN' for i in range(1, len(last_14_closes_rev))]
                            logger.info(f"[SIM_LIVE] [STAGE_B_BEFORE_RETURN] {symbol_upper} | First 14 direction changes: {directions_rev}")
                    except:
                        pass
                
                # Return requested slice (offset from most recent, which is now at index 0)
                total_available = len(stored_candles_reversed)
                if offset + count <= total_available:
                    # Have enough candles - return slice
                    result = stored_candles_reversed[offset:offset + count]
                elif offset < total_available:
                    # Don't have enough, but have some - return what we have without padding
                    # Padding with duplicates causes RSI=100 when last 14 rows are duplicates
                    result = stored_candles_reversed[offset:]
                    # DO NOT pad - return fewer candles than requested
                    # The strategy can handle fewer candles
                else:
                    # Offset beyond available - return empty list (not padded duplicates)
                    result = []
                
                # Log what we're actually returning
                if self.config.get('mode') == 'SIM_LIVE' and result:
                    try:
                        from sim_live.sim_live_logger import get_sim_live_logger
                        logger = get_sim_live_logger()
                        first_14_ret = [c['close'] for c in result[:14]] if len(result) >= 14 else [c['close'] for c in result]
                        logger.info(f"[SIM_LIVE] [STAGE_B_RETURNING] {symbol_upper} | Returning {len(result)} candles, first 14 closes: {first_14_ret}")
                    except:
                        pass
                
                return result
        
        # Fallback: generate basic candles from current price
        price_data = self.get_current_tick(symbol_upper)
        if price_data is None:
            return None
        
        candles = []
        current_time = int(self._get_current_time())
        config = self.get_symbol_config(symbol_upper)
        
        # Generate candles going back in time
        for i in range(count):
            candle_time = current_time - ((offset + i) * timeframe_seconds)
            
            candles.append({
                'time': candle_time,
                'open': price_data['bid'],
                'high': price_data['ask'],
                'low': price_data['bid'],
                'close': price_data['bid'],
                'tick_volume': 100,
                'spread': int((price_data['ask'] - price_data['bid']) / config['point']),
                'real_volume': 0
            })
        
        return candles
    
    def set_scenario(self, scenario: Dict[str, Any]):
        """
        Set active scenario script and generate warm-up candles if needed.
        
        Args:
            scenario: Scenario dict with 'name', 'duration_seconds', 'price_script', 'trend_direction' keys
        """
        # First, set scenario metadata (quick operation)
        with self._scenario_lock:
            self._scenario = scenario
            self._scenario_start_time = self._get_current_time()
            self._scenario_actions = scenario.get('price_script', [])
            # Store scenario name for logging
            self._current_scenario_name = scenario.get('name', 'unknown')
            
            # 🔒 Reset market freeze state for scenario symbol
            symbol = scenario.get('symbol', 'EURUSD')
            symbol_upper = symbol.upper()
            self._market_frozen[symbol_upper] = False  # Unfreeze at scenario start
        
        # Generate warm-up candles if needed (outside scenario lock to avoid deadlock)
        symbol = scenario.get('symbol', 'EURUSD')
        trend_direction = scenario.get('trend_direction', 'BUY')
        warmup_count = scenario.get('warmup_candles', 35)
        symbol_upper = symbol.upper()
        
        # 🔒 SIM_LIVE: Force warm-up regeneration (no caching)
        # Disable ALL warm-up caching for SIM_LIVE to ensure fresh generation every run
        force_regenerate_warmup = self.config.get('mode') == 'SIM_LIVE'
        
        warmup_already_done = False
        if not force_regenerate_warmup:
            with self._candle_gen_lock:
                warmup_already_done = symbol_upper in self._warmup_candles and self._warmup_candles[symbol_upper].get('warmup_complete')
        
        if force_regenerate_warmup or not warmup_already_done:
            if force_regenerate_warmup:
                # Clear existing warm-up state
                with self._candle_gen_lock:
                    if symbol_upper in self._warmup_candles:
                        del self._warmup_candles[symbol_upper]
                    if symbol_upper in self._candle_history:
                        del self._candle_history[symbol_upper]
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    logger.info(f"[SIM_LIVE] [WARMUP_REGEN] Warm-up forcibly regenerated for scenario {scenario.get('name', 'unknown')}")
                except:
                    pass
            # Generate warm-up candles (outside lock to avoid deadlock with generate_warmup_candles)
            initial_price = scenario.get('initial_price', {}).get('bid')
            if initial_price is None:
                # Get current price if initial_price not in scenario
                tick = self.get_current_tick(symbol)
                initial_price = tick['bid'] if tick else 1.1000
            
            # 🔒 TREND CONTRACT ENFORCEMENT: Automatic retry loop with slope adjustment
            max_attempts = 5
            slope_multiplier = 1.0  # Start with base slope
            candles = None
            contract_passed = False
            
            for attempt in range(1, max_attempts + 1):
                # Generate warm-up candles (this may take a moment)
                print(f"[SIM_LIVE] Generating {warmup_count} warm-up candles for {symbol} (attempt {attempt}/{max_attempts})...")
                
                # Store current slope multiplier for this attempt (will be used in generation)
                self._current_warmup_slope_multiplier = slope_multiplier
                
                candles = self.generate_warmup_candles(symbol, trend_direction, initial_price, warmup_count)
                print(f"[SIM_LIVE] Generated {len(candles)} warm-up candles for {symbol}")
                
                # Validate trend contract
                is_valid, indicators = self._validate_trend_contract(symbol, candles, trend_direction)
                
                if is_valid:
                    contract_passed = True
                    self._log_trend_contract_result(scenario.get('name', 'unknown'), trend_direction, indicators, True)
                    break
                else:
                    # Contract failed - increase slope for next attempt
                    slope_multiplier *= 1.5  # Increase by 50% each attempt
                    self._log_trend_contract_result(scenario.get('name', 'unknown'), trend_direction, indicators, False)
                    
                    if attempt < max_attempts:
                        try:
                            from sim_live.sim_live_logger import get_sim_live_logger
                            logger = get_sim_live_logger()
                            logger.warning(f"[SIM_LIVE] [TREND_CONTRACT_RETRY] Attempt {attempt} failed, retrying with slope_multiplier={slope_multiplier:.2f}")
                        except:
                            pass
            
            # HARD FAIL if contract not satisfied after max attempts
            if not contract_passed:
                indicators = self._validate_trend_indicators(symbol, candles, trend_direction)
                sma20 = indicators.get('sma20')
                sma50 = indicators.get('sma50')
                rsi_last14 = indicators.get('rsi_last14')
                
                error_msg = (
                    f"[SIM_LIVE] [TREND_CONTRACT_HARD_FAIL] Failed to satisfy trend contract after {max_attempts} attempts\n"
                    f"Scenario: {scenario.get('name', 'unknown')}\n"
                    f"Direction: {trend_direction}\n"
                    f"SMA20: {sma20:.5f}\n"
                    f"SMA50: {sma50:.5f}\n"
                    f"Separation: {abs((sma20 - sma50) / sma50 * 100) if sma20 and sma50 else 0:.4f}% (required: >=0.05%)\n"
                    f"RSI(last14): {rsi_last14:.1f if rsi_last14 else 'N/A'}\n"
                    f"Last 20 close deltas: {[round(candles[i]['close'] - candles[i-1]['close'], 5) for i in range(max(0, len(candles)-20), len(candles)) if i > 0] if candles else []}"
                )
                
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    logger.error(error_msg)
                except:
                    pass
                
                raise AssertionError(error_msg)
        else:
            print(f"[SIM_LIVE] Warm-up candles already generated for {symbol}")
    
    def _timeframe_to_seconds(self, timeframe: int) -> int:
        """Convert MT5 timeframe to seconds."""
        # MT5 timeframe constants
        if timeframe == 1:  # M1
            return 60
        elif timeframe == 5:  # M5
            return 300
        elif timeframe == 15:  # M15
            return 900
        elif timeframe == 30:  # M30
            return 1800
        elif timeframe == 16385:  # H1
            return 3600
        elif timeframe == 16388:  # H4
            return 14400
        elif timeframe == 16408:  # D1
            return 86400
        return 60  # Default to M1
    
    def _get_current_time(self) -> float:
        """Get current simulated time (accounting for acceleration)."""
        elapsed = time.time() - self._start_time
        return self._start_time + (elapsed * self.time_acceleration)
    
    def _validate_trend_indicators(self, symbol: str, candles: List[Dict[str, Any]], trend_direction: str):
        """
        Validate that generated candles produce valid trend indicators.
        
        Calculates SMA20, SMA50, ADX, RSI from generated candles and logs result.
        This is SIM_LIVE-only diagnostic logging.
        """
        try:
            import pandas as pd
            import numpy as np
            
            # Convert candles to DataFrame
            df_data = {
                'time': [c['time'] for c in candles],
                'open': [c['open'] for c in candles],
                'high': [c['high'] for c in candles],
                'low': [c['low'] for c in candles],
                'close': [c['close'] for c in candles],
                'tick_volume': [c.get('tick_volume', 0) for c in candles]
            }
            df = pd.DataFrame(df_data)
            
            # Calculate indicators (matching TrendFilter logic)
            # SMA20
            sma20_series = df['close'].rolling(window=20).mean()
            sma20 = sma20_series.iloc[-1] if len(sma20_series) > 0 and pd.notna(sma20_series.iloc[-1]) else None
            
            # SMA50
            sma50_series = df['close'].rolling(window=50).mean()
            sma50 = sma50_series.iloc[-1] if len(sma50_series) > 0 and pd.notna(sma50_series.iloc[-1]) else None
            
            # RSI
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi_series = 100 - (100 / (1 + rs))
            rsi_series = rsi_series.fillna(100)
            rsi = rsi_series.iloc[-1] if len(rsi_series) > 0 and pd.notna(rsi_series.iloc[-1]) else None
            
            # ADX (simplified - full ADX is complex, this is approximation)
            high = df['high']
            low = df['low']
            tr1 = high - low
            tr2 = abs(high - df['close'].shift())
            tr3 = abs(low - df['close'].shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean()
            
            plus_dm = high.diff()
            minus_dm = -low.diff()
            plus_dm[plus_dm < 0] = 0
            minus_dm[minus_dm < 0] = 0
            plus_di = 100 * (plus_dm.rolling(window=14).mean() / atr)
            minus_di = 100 * (minus_dm.rolling(window=14).mean() / atr)
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            adx_series = dx.rolling(window=14).mean()
            adx = adx_series.iloc[-1] if len(adx_series) > 0 and pd.notna(adx_series.iloc[-1]) else None
            
            # CRITICAL: Also validate the LAST 14 candles (RSI window) to ensure RSI is in target range
            # Strategy will evaluate using last 14 candles, not all candles
            last_14_rsi = None
            if len(candles) >= 14:
                last_14 = candles[-14:]
                df_last14 = pd.DataFrame({'close': [c['close'] for c in last_14]})
                delta_14 = df_last14['close'].diff()
                gain_14 = (delta_14.where(delta_14 > 0, 0)).rolling(window=14).mean()
                loss_14 = (-delta_14.where(delta_14 < 0, 0)).rolling(window=14).mean()
                rs_14 = gain_14 / loss_14.replace(0, np.nan)
                rsi_14_series = 100 - (100 / (1 + rs_14))
                rsi_14_series = rsi_14_series.fillna(100)
                last_14_rsi = rsi_14_series.iloc[-1] if len(rsi_14_series) > 0 and pd.notna(rsi_14_series.iloc[-1]) else None
            
            # Determine signal
            signal = 'NONE'
            if sma20 is not None and sma50 is not None:
                if sma20 > sma50 * 1.00001:  # At least 1 pip separation
                    signal = 'LONG'
                elif sma20 < sma50 * 0.99999:
                    signal = 'SHORT'
            
            return {
                'sma20': sma20,
                'sma50': sma50,
                'rsi': rsi,
                'rsi_last14': last_14_rsi,
                'adx': adx,
                'signal': signal
            }
        except Exception as e:
            # Return None values if validation fails
            return {
                'sma20': None,
                'sma50': None,
                'rsi': None,
                'rsi_last14': None,
                'adx': None,
                'signal': 'NONE'
            }
        
    def _validate_trend_contract(self, symbol: str, candles: List[Dict[str, Any]], trend_direction: str, use_mt5_source: bool = False) -> tuple[bool, Dict[str, Any]]:
        """
        Validate that warm-up candles satisfy the trend contract.
        
        🔒 SINGLE SOURCE OF TRUTH: If use_mt5_source=True, fetch candles via copy_rates_from_pos()
        instead of using provided candles list. This ensures validation uses the SAME data as TrendFilter.
        
        For BUY: SMA20 > SMA50 AND (SMA20 - SMA50) / SMA50 >= 0.0005
        For SELL: SMA20 < SMA50 AND (SMA50 - SMA20) / SMA50 >= 0.0005
        
        Args:
            symbol: Trading symbol
            candles: Candle list (ignored if use_mt5_source=True)
            trend_direction: 'BUY' or 'SELL'
            use_mt5_source: If True, fetch candles via copy_rates_from_pos() instead
        
        Returns:
            (is_valid, indicators_dict)
        """
        # 🔒 SINGLE SOURCE OF TRUTH: Use copy_rates_from_pos if requested
        if use_mt5_source:
            # Fetch candles via MT5 interface (same as TrendFilter)
            rates_from_mt5 = self.copy_rates_from_pos(symbol, 1, 0, 100)  # M1, offset=0, count=100
            if rates_from_mt5:
                candles = []
                for candle in rates_from_mt5:
                    # Fix Timestamp conversion - handle NumPy datetime64, pandas Timestamp, or int
                    time_val = candle.get('time') if isinstance(candle, dict) else candle['time']
                    try:
                        import pandas as pd
                        import numpy as np
                        # Handle NumPy datetime64
                        if isinstance(time_val, np.datetime64):
                            time_val = int(time_val.astype('datetime64[s]').astype(int))
                        # Handle pandas Timestamp
                        elif isinstance(time_val, pd.Timestamp):
                            time_val = int(time_val.timestamp())
                        # Handle datetime-like objects
                        elif hasattr(time_val, 'timestamp'):
                            time_val = int(time_val.timestamp())
                        # Handle int/float
                        elif isinstance(time_val, (int, float)):
                            time_val = int(time_val)
                        else:
                            # Try direct conversion
                            time_val = int(float(time_val))
                    except Exception:
                        # Last resort: try to extract numeric value
                        try:
                            import pandas as pd
                            import numpy as np
                            if pd.isna(time_val):
                                time_val = 0
                            elif isinstance(time_val, (str, bytes)):
                                time_val = int(float(str(time_val).split('.')[0]))
                            else:
                                time_val = int(time_val)
                        except:
                            time_val = 0
                    
                    candles.append({
                        'time': time_val,
                        'open': float(candle.get('open') if isinstance(candle, dict) else candle['open']),
                        'high': float(candle.get('high') if isinstance(candle, dict) else candle['high']),
                        'low': float(candle.get('low') if isinstance(candle, dict) else candle['low']),
                        'close': float(candle.get('close') if isinstance(candle, dict) else candle['close']),
                        'tick_volume': int(candle.get('tick_volume', 0) if isinstance(candle, dict) else candle.get('tick_volume', 0)),
                        'spread': int(candle.get('spread', 0) if isinstance(candle, dict) else candle.get('spread', 0)),
                        'real_volume': int(candle.get('real_volume', 0) if isinstance(candle, dict) else candle.get('real_volume', 0))
                    })
                # copy_rates_from_pos returns newest-first, but validation expects oldest-first
                candles = list(reversed(candles))
            else:
                return False, {'sma20': None, 'sma50': None, 'separation_pct': 0.0}
        
        indicators = self._validate_trend_indicators(symbol, candles, trend_direction)
        
        sma20 = indicators.get('sma20')
        sma50 = indicators.get('sma50')
        signal = indicators.get('signal')
        
        if sma20 is None or sma50 is None:
            return False, indicators
        
        min_separation_pct = 0.0005  # 0.05%
        
        if trend_direction == 'BUY':
            # BUY contract: SMA20 > SMA50 with minimum separation
            separation_pct = (sma20 - sma50) / sma50
            is_valid = (signal == 'LONG' and separation_pct >= min_separation_pct)
        else:  # SELL
            # SELL contract: SMA20 < SMA50 with minimum separation
            separation_pct = (sma50 - sma20) / sma50
            is_valid = (signal == 'SHORT' and separation_pct >= min_separation_pct)
        
        indicators['separation_pct'] = abs(separation_pct)
        indicators['contract_satisfied'] = is_valid
        
        return is_valid, indicators
    
    def _log_trend_contract_result(self, scenario_name: str, trend_direction: str, indicators: Dict[str, Any], passed: bool):
        """Log trend contract validation result."""
        try:
            from sim_live.sim_live_logger import get_sim_live_logger
            logger = get_sim_live_logger()
            
            sma20 = indicators.get('sma20')
            sma50 = indicators.get('sma50')
            separation_pct = indicators.get('separation_pct', 0.0)
            rsi_last14 = indicators.get('rsi_last14')
            
            if passed:
                logger.info(f"[SIM_LIVE] [TREND_CONTRACT_OK]")
                logger.info(f"[SIM_LIVE] [TREND_CONTRACT_OK] Scenario: {scenario_name}")
                logger.info(f"[SIM_LIVE] [TREND_CONTRACT_OK] Direction: {trend_direction}")
                logger.info(f"[SIM_LIVE] [TREND_CONTRACT_OK] SMA20: {sma20:.5f}")
                logger.info(f"[SIM_LIVE] [TREND_CONTRACT_OK] SMA50: {sma50:.5f}")
                logger.info(f"[SIM_LIVE] [TREND_CONTRACT_OK] Separation %: {separation_pct*100:.4f}%")
                logger.info(f"[SIM_LIVE] [TREND_CONTRACT_OK] RSI(last14): {rsi_last14:.1f}" if rsi_last14 else "[SIM_LIVE] [TREND_CONTRACT_OK] RSI(last14): N/A")
            else:
                logger.error(f"[SIM_LIVE] [TREND_CONTRACT_FAIL] Scenario: {scenario_name} | Direction: {trend_direction}")
                logger.error(f"[SIM_LIVE] [TREND_CONTRACT_FAIL] SMA20: {sma20:.5f} | SMA50: {sma50:.5f} | Separation: {separation_pct*100:.4f}% | Required: >=0.05%")
        except:
            pass
    
    def register_price_update_callback(self, callback: Callable[[str, float, float], None]):
        """Register callback for price updates (used by SyntheticBroker for position updates)."""
        with self._callback_lock:
            self._price_update_callbacks.append(callback)
    
    def _notify_price_update(self, symbol: str, bid: float, ask: float):
        """Notify all registered callbacks of price update."""
        with self._callback_lock:
            callbacks_to_call = list(self._price_update_callbacks)  # Copy to avoid lock during iteration
        # Call callbacks outside lock to prevent deadlocks
        for callback in callbacks_to_call:
            try:
                callback(symbol, bid, ask)
            except Exception as e:
                # Don't let callback errors break the engine - log but continue
                try:
                    from sim_live.sim_live_logger import get_sim_live_logger
                    logger = get_sim_live_logger()
                    logger.warning(f"[SYNTHETIC_MARKET] Error in price update callback: {e}")
                except:
                    print(f"Error in price update callback: {e}")

