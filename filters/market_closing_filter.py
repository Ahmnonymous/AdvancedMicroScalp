#!/usr/bin/env python3
"""
Market Closing Filter
Checks if market for a symbol is closing within the next 30 minutes.
Skips trading for symbols too close to market close.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from execution.mt5_connector import MT5Connector

logger = logging.getLogger(__name__)


class MarketClosingFilter:
    """Filters trades based on market closing time."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector):
        """
        Initialize Market Closing Filter.
        
        Args:
            config: Configuration dictionary
            mt5_connector: MT5Connector instance
        """
        self.config = config
        self.mt5_connector = mt5_connector
        
        # Configuration
        filter_config = config.get('filters', {}).get('market_closing', {})
        self.enabled = filter_config.get('enabled', True)
        self.minutes_before_close = filter_config.get('minutes_before_close', 30)
        
        # Initialize skipped pairs logger
        # Ensure log directory exists before creating FileHandler
        log_dir = 'logs/backtest/system' if self.config.get('mode') == 'backtest' else 'logs/live/system'
        os.makedirs(log_dir, exist_ok=True)  # Create directory if it doesn't exist
        self.skipped_logger = logging.getLogger('skipped_pairs')
        skipped_handler = logging.FileHandler(f'{log_dir}/skipped_pairs.log')
        skipped_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.skipped_logger.addHandler(skipped_handler)
        self.skipped_logger.setLevel(logging.INFO)
    
    def is_market_closing_soon(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check if market is closing within the configured minutes.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            (is_closing_soon, reason) - True if market closing within threshold
        """
        if not self.enabled:
            return False, None
        
        if not self.mt5_connector.ensure_connected():
            return False, None
        
        try:
            import MetaTrader5 as mt5
            
            # Get symbol info
            symbol_info_obj = mt5.symbol_info(symbol)
            if not symbol_info_obj:
                return False, None
            
            # Get current time
            now = datetime.now()
            
            # For crypto symbols, market is 24/7 - no closing
            if any(crypto in symbol.upper() for crypto in ['BTC', 'ETH', 'XRP', 'ADA', 'BCH', 'LTC', 'BNB', 'BAT', 'DOGE', 'DOT', 'LINK', 'UNI']):
                return False, None
            
            # For forex, check trading session times
            # MT5 provides session times in symbol_info
            # Sessions are typically: ASIA, EUROPE, US
            
            # Get trading sessions
            sessions = self._get_trading_sessions(symbol_info_obj)
            
            if not sessions:
                # If no session info, check if we can determine from symbol type
                # For stocks/indices, check if it's near end of trading day
                if any(indicator in symbol.upper() for indicator in ['AUS200', 'USTEC', 'US500', 'UK100', 'GER40', 'FRA40']):
                    # Stock indices typically close at specific times
                    return self._check_stock_index_closing(symbol, now)
                
                # Default: assume 24/5 for forex if no session info
                return False, None
            
            # Check if any session is closing soon
            for session_name, session_times in sessions.items():
                if session_times.get('close'):
                    close_time = session_times['close']
                    
                    # Calculate time until close
                    time_until_close = (close_time - now).total_seconds() / 60.0
                    
                    if 0 < time_until_close <= self.minutes_before_close:
                        reason = f"Market closing in {time_until_close:.1f} minutes (session: {session_name})"
                        return True, reason
            
            return False, None
        
        except Exception as e:
            logger.error(f"Error checking market closing for {symbol}: {e}")
            return False, None
    
    def _get_trading_sessions(self, symbol_info_obj) -> Dict[str, Dict]:
        """
        Extract trading sessions from MT5 symbol info.
        
        Returns:
            Dict mapping session names to {'open': datetime, 'close': datetime}
        """
        sessions = {}
        
        try:
            # MT5 stores session times in symbol_info
            # Format varies by broker - check available attributes
            if hasattr(symbol_info_obj, 'sessions'):
                # Parse session times
                # This is broker-specific and may need adjustment
                pass
            
            # For now, use symbol-specific logic
            # Forex pairs typically have overlapping sessions
            # Most active: London (08:00-16:00 GMT) and New York (13:00-21:00 GMT)
            
            symbol = symbol_info_obj.name
            
            # Check current GMT time
            from datetime import timezone
            gmt_now = datetime.now(timezone.utc)
            gmt_hour = gmt_now.hour
            
            # Major forex sessions (GMT):
            # London: 08:00-16:00 GMT
            # New York: 13:00-21:00 GMT
            # Tokyo: 00:00-09:00 GMT
            # Sydney: 22:00-07:00 GMT (next day)
            
            # For forex, market is effectively 24/5 (Monday-Friday)
            # But liquidity is lowest at session transitions
            
            # Check if near end of trading week (Friday close)
            if gmt_now.weekday() == 4:  # Friday
                # Forex market typically closes Friday 22:00 GMT (some brokers 21:00)
                friday_close = gmt_now.replace(hour=22, minute=0, second=0, microsecond=0)
                if gmt_now < friday_close:
                    time_until_close = (friday_close - gmt_now).total_seconds() / 60.0
                    if 0 < time_until_close <= self.minutes_before_close:
                        sessions['WEEK'] = {
                            'close': datetime.now() + timedelta(minutes=time_until_close)
                        }
            
            return sessions
        
        except Exception as e:
            logger.debug(f"Could not parse trading sessions: {e}")
            return {}
    
    def _check_stock_index_closing(self, symbol: str, now: datetime) -> Tuple[bool, Optional[str]]:
        """
        Check if stock index is closing soon.
        
        Stock indices typically close:
        - US indices (USTEC, US500): 21:00 GMT (4 PM EST)
        - European indices: 16:30 GMT
        - Asian indices (AUS200): 07:00 GMT
        """
        from datetime import timezone
        
        gmt_now = now.utcnow() if hasattr(now, 'utcnow') else datetime.now(timezone.utc)
        gmt_hour = gmt_now.hour
        gmt_minute = gmt_now.minute
        
        # Determine closing time based on symbol
        close_hour = None
        close_minute = 0
        
        symbol_upper = symbol.upper()
        
        if any(idx in symbol_upper for idx in ['USTEC', 'US500', 'US30']):
            # US indices: 21:00 GMT (4 PM EST)
            close_hour = 21
        elif any(idx in symbol_upper for idx in ['UK100', 'GER40', 'FRA40', 'EU50']):
            # European indices: 16:30 GMT
            close_hour = 16
            close_minute = 30
        elif 'AUS200' in symbol_upper:
            # Australian index: 07:00 GMT
            close_hour = 7
        
        if close_hour is None:
            return False, None
        
        # Calculate closing time for today
        close_time = gmt_now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
        
        # If close time has passed today, check tomorrow (or Monday if weekend)
        if close_time < gmt_now:
            if gmt_now.weekday() < 4:  # Monday-Thursday
                close_time += timedelta(days=1)
            elif gmt_now.weekday() == 4:  # Friday
                # Market closed for weekend
                return True, "Market closed for weekend"
            else:  # Weekend
                # Next Monday
                days_until_monday = (7 - gmt_now.weekday()) % 7
                if days_until_monday == 0:
                    days_until_monday = 7
                close_time += timedelta(days=days_until_monday)
        
        # Convert back to local time for comparison
        time_until_close = (close_time - gmt_now).total_seconds() / 60.0
        
        if 0 < time_until_close <= self.minutes_before_close:
            reason = f"Stock index closing in {time_until_close:.1f} minutes"
            return True, reason
        
        return False, None
    
    def should_skip(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Determine if symbol should be skipped due to market closing.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            (should_skip, reason) - True if should skip
        """
        is_closing, reason = self.is_market_closing_soon(symbol)
        
        if is_closing:
            self.skipped_logger.info(f"{symbol}: MARKET_CLOSING - {reason}")
            logger.info(f"[SKIP] [SKIP] {symbol} | Reason: {reason}")
        
        return is_closing, reason

