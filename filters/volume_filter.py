#!/usr/bin/env python3
"""
Volume/Liquidity Filter
Checks if symbol has sufficient market volume before trading.
Skips low-volume symbols to avoid execution issues.
"""

import os
import logging
from typing import Dict, Any, Optional, Tuple
from execution.mt5_connector import MT5Connector

logger = logging.getLogger(__name__)


class VolumeFilter:
    """Filters trades based on market volume/liquidity."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector):
        """
        Initialize Volume Filter.
        
        Args:
            config: Configuration dictionary
            mt5_connector: MT5Connector instance
        """
        self.config = config
        self.mt5_connector = mt5_connector
        
        # Configuration
        filter_config = config.get('filters', {}).get('volume', {})
        self.enabled = filter_config.get('enabled', True)
        self.volume_filter_mode = filter_config.get('volume_filter', 'medium')  # 'low', 'medium', 'high'
        self.min_tick_volume = filter_config.get('min_tick_volume', 10)
        self.check_period_minutes = filter_config.get('check_period_minutes', 5)
        self.min_avg_volume = filter_config.get('min_avg_volume', 3.0)
        
        # Adjust thresholds based on filter mode
        if self.volume_filter_mode == 'medium':
            # Medium sensitivity: slightly higher threshold than low
            self.min_tick_volume = max(self.min_tick_volume, 5)
            self.min_avg_volume = max(self.min_avg_volume, 3.0)
        elif self.volume_filter_mode == 'high':
            # High sensitivity: higher threshold
            self.min_tick_volume = max(self.min_tick_volume, 10)
            self.min_avg_volume = max(self.min_avg_volume, 5.0)
        # 'low' mode uses defaults (more permissive)
        
        # Initialize skipped pairs logger
        os.makedirs('logs/system', exist_ok=True)
        self.skipped_logger = logging.getLogger('skipped_pairs_volume')
        skipped_handler = logging.FileHandler('logs/system/skipped_pairs.log')
        skipped_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.skipped_logger.addHandler(skipped_handler)
        self.skipped_logger.setLevel(logging.INFO)
    
    def has_sufficient_volume(self, symbol: str) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        Check if symbol has sufficient volume/liquidity.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            (has_sufficient_volume, reason, volume_value) - True if volume is sufficient
        """
        if not self.enabled:
            return True, None, None
        
        if not self.mt5_connector.ensure_connected():
            return True, None, None  # Fail-safe: allow if can't check
        
        try:
            import MetaTrader5 as mt5
            
            # Method 1: Check recent tick volume (most reliable for MT5)
            tick_volume = self._get_recent_tick_volume(symbol)
            
            if tick_volume is not None:
                if tick_volume < self.min_tick_volume:
                    reason = f"Insufficient tick volume: {tick_volume} < {self.min_tick_volume}"
                    return False, reason, tick_volume
                else:
                    logger.debug(f"{symbol}: Tick volume OK: {tick_volume}")
                    return True, None, tick_volume
            
            # Method 2: Check real volume if available
            real_volume = self._get_recent_real_volume(symbol)
            
            if real_volume is not None:
                # Real volume is typically much larger than tick volume
                # Use a proportionally higher threshold
                min_real_volume = self.min_tick_volume * 100  # Rough conversion
                
                if real_volume < min_real_volume:
                    reason = f"Insufficient real volume: {real_volume:.0f} < {min_real_volume:.0f}"
                    return False, reason, real_volume
                else:
                    logger.debug(f"{symbol}: Real volume OK: {real_volume:.0f}")
                    return True, None, real_volume
            
            # Method 3: Check tick count (number of price updates)
            tick_count = self._get_recent_tick_count(symbol)
            
            if tick_count is not None:
                if tick_count < self.min_tick_volume:
                    reason = f"Insufficient tick activity: {tick_count} ticks in {self.check_period_minutes} minutes"
                    return False, reason, float(tick_count)
                else:
                    logger.debug(f"{symbol}: Tick activity OK: {tick_count} ticks")
                    return True, None, float(tick_count)
            
            # If all methods fail, allow trading (fail-safe)
            logger.debug(f"{symbol}: Could not determine volume - allowing trade (fail-safe)")
            return True, None, None
        
        except Exception as e:
            logger.warning(f"Error checking volume for {symbol}: {e}")
            # Fail-safe: allow trading if volume check fails
            return True, None, None
    
    def _get_recent_tick_volume(self, symbol: str) -> Optional[int]:
        """
        Get recent tick volume from MT5.
        
        Tick volume is the number of price updates (more reliable than real volume in MT5).
        """
        try:
            import MetaTrader5 as mt5
            
            # Get recent tick data
            timeframe = mt5.TIMEFRAME_M1  # 1-minute timeframe
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, self.check_period_minutes)
            
            if rates is None or len(rates) == 0:
                return None
            
            # Sum tick volume over the period
            total_volume = sum(rate['tick_volume'] for rate in rates)
            
            # Average per minute
            avg_volume = total_volume / len(rates) if len(rates) > 0 else 0
            
            return int(avg_volume)
        
        except Exception as e:
            logger.debug(f"Could not get tick volume for {symbol}: {e}")
            return None
    
    def _get_recent_real_volume(self, symbol: str) -> Optional[float]:
        """
        Get recent real volume from MT5 (if available).
        
        Real volume is actual trading volume (may not be available for all symbols).
        """
        try:
            import MetaTrader5 as mt5
            
            # Get recent tick data
            timeframe = mt5.TIMEFRAME_M1
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, self.check_period_minutes)
            
            if rates is None or len(rates) == 0:
                return None
            
            # Real volume is in 'real_volume' field (if available)
            if 'real_volume' in rates.dtype.names:
                total_volume = sum(rate['real_volume'] for rate in rates)
                avg_volume = total_volume / len(rates) if len(rates) > 0 else 0
                return float(avg_volume)
            
            return None
        
        except Exception as e:
            logger.debug(f"Could not get real volume for {symbol}: {e}")
            return None
    
    def _get_recent_tick_count(self, symbol: str) -> Optional[int]:
        """
        Get count of recent ticks (price updates) as proxy for liquidity.
        
        This is a fallback method if volume data is not available.
        """
        try:
            import MetaTrader5 as mt5
            
            # Get tick history (if available)
            from datetime import datetime, timedelta
            
            ticks = mt5.copy_ticks_from(
                symbol,
                datetime.now() - timedelta(minutes=self.check_period_minutes),
                self.check_period_minutes * 60,  # Number of ticks to request
                mt5.COPY_TICKS_ALL
            )
            
            if ticks is None:
                return None
            
            return len(ticks)
        
        except Exception as e:
            logger.debug(f"Could not get tick count for {symbol}: {e}")
            return None
    
    def should_skip(self, symbol: str) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        Determine if symbol should be skipped due to low volume.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            (should_skip, reason, volume_value) - True if should skip
        """
        has_volume, reason, volume_value = self.has_sufficient_volume(symbol)
        
        if not has_volume:
            self.skipped_logger.info(f"{symbol}: LOW_VOLUME - {reason} (Volume: {volume_value})")
            logger.info(f"⛔ [SKIP] {symbol} | Reason: {reason}")
        
        return not has_volume, reason, volume_value

