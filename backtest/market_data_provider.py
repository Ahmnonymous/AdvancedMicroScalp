"""
Market Data Provider Abstraction Layer
Provides unified interface for live and historical market data.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
import threading
import time
import math

from utils.logger_factory import get_logger

logger = get_logger("backtest_market_data", "logs/backtest/market_data.log")


class MarketDataProvider(ABC):
    """Abstract base class for market data providers."""
    
    @abstractmethod
    def get_symbol_info(self, symbol: str, check_price_staleness: bool = False) -> Optional[Dict[str, Any]]:
        """Get symbol information."""
        pass
    
    @abstractmethod
    def get_symbol_info_tick(self, symbol: str):
        """Get current tick data."""
        pass
    
    @abstractmethod
    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account information."""
        pass
    
    @abstractmethod
    def ensure_connected(self) -> bool:
        """Ensure connection is active."""
        pass
    
    @abstractmethod
    def is_symbol_tradeable_now(self, symbol: str, check_trade_allowed: bool = True) -> Tuple[bool, str]:
        """Check if symbol is tradeable."""
        pass


class LiveMarketDataProvider(MarketDataProvider):
    """Live market data provider using real MT5 connection."""
    
    def __init__(self, mt5_connector):
        self.mt5_connector = mt5_connector
    
    def get_symbol_info(self, symbol: str, check_price_staleness: bool = False) -> Optional[Dict[str, Any]]:
        return self.mt5_connector.get_symbol_info(symbol, check_price_staleness)
    
    def get_symbol_info_tick(self, symbol: str):
        return self.mt5_connector.get_symbol_info_tick(symbol)
    
    def get_account_info(self) -> Optional[Dict[str, Any]]:
        return self.mt5_connector.get_account_info()
    
    def ensure_connected(self) -> bool:
        return self.mt5_connector.ensure_connected()
    
    def is_symbol_tradeable_now(self, symbol: str, check_trade_allowed: bool = True) -> Tuple[bool, str]:
        return self.mt5_connector.is_symbol_tradeable_now(symbol, check_trade_allowed)


class HistoricalMarketDataProvider(MarketDataProvider):
    """Historical market data provider for backtesting."""
    
    def __init__(self, historical_data: Dict[str, pd.DataFrame], current_time: datetime,
                 account_balance: float = 10000.0):
        """
        Initialize historical market data provider.
        
        Args:
            historical_data: {symbol: DataFrame with OHLCV data}
            current_time: Current simulation time
            account_balance: Starting account balance
        """
        self.historical_data = historical_data
        self.current_time = current_time
        self.account_balance = account_balance
        self.account_equity = account_balance
        self.account_profit = 0.0
        self._lock = threading.Lock()
        
        # Cache symbol info
        self._symbol_info_cache = {}
        self._initialize_symbol_info()
    
    def _initialize_symbol_info(self):
        """Initialize symbol info cache from historical data."""
        for symbol, df in self.historical_data.items():
            if len(df) > 0:
                # Get first row to extract symbol properties
                first_row = df.iloc[0]
                self._symbol_info_cache[symbol] = {
                    'name': symbol,
                    'point': 0.00001 if 'point' not in df.columns else df['point'].iloc[0],
                    'digits': 5,
                    'spread': 20,  # Default spread in points
                    'trade_mode': 4,  # Full trading
                    'trade_stops_level': 10,
                    'trade_freeze_level': 0,
                    'contract_size': 100000,
                    'margin_initial': 0,
                    'swap_mode': 0,
                    'swap_long': 0,
                    'swap_short': 0,
                    'volume_min': 0.01,
                    'volume_max': 100,
                    'volume_step': 0.01,
                    'filling_mode': 4,  # RETURN
                    'trade_tick_value': None,
                    'trade_tick_size': None
                }
    
    def set_current_time(self, current_time: datetime):
        """Update current simulation time."""
        with self._lock:
            self.current_time = current_time
    
    def get_current_time(self) -> datetime:
        """Get current simulation time."""
        with self._lock:
            return self.current_time
    
    def get_symbol_info(self, symbol: str, check_price_staleness: bool = False) -> Optional[Dict[str, Any]]:
        """Get symbol information at current simulation time."""
        if symbol not in self.historical_data:
            return None
        
        df = self.historical_data[symbol]
        if len(df) == 0:
            return None
        
        # Get current bar data
        current_bar = self._get_current_bar(symbol)
        if current_bar is None:
            return None
        
        # Get base symbol info
        base_info = self._symbol_info_cache.get(symbol, {}).copy()
        
        # CRITICAL FIX: Spread changes every tick in live trading
        # Use spread from historical data if available, otherwise use base spread with variation
        historical_spread = current_bar.get('spread', None)
        if historical_spread is not None:
            # Use actual spread from historical data (changes every bar/tick)
            spread_points = historical_spread
        else:
            # Fallback: Use base spread with small random variation to simulate tick-by-tick changes
            base_spread = base_info.get('spread', 20)
            # Add small random variation (±10% of base spread) to simulate spread changes
            spread_variation = base_spread * 0.1 * (random.random() - 0.5) * 2
            spread_points = max(1, int(base_spread + spread_variation))
        
        # Update spread in cache for next time
        base_info['spread'] = spread_points
        
        # Calculate bid/ask from close price and spread
        point = base_info.get('point', 0.00001)
        spread_price = spread_points * point
        close_price = current_bar.get('close', 0)
        
        # CRITICAL FIX: Validate close_price is not NaN
        if close_price is None or math.isnan(close_price) or close_price <= 0:
            # Try to get a valid price from the bar
            if 'open' in current_bar and not math.isnan(current_bar.get('open', 0)):
                close_price = current_bar.get('open', 0)
            elif 'high' in current_bar and not math.isnan(current_bar.get('high', 0)):
                close_price = current_bar.get('high', 0)
            elif 'low' in current_bar and not math.isnan(current_bar.get('low', 0)):
                close_price = current_bar.get('low', 0)
            else:
                logger.warning(f"Invalid close price for {symbol} at {self.current_time}: {current_bar.get('close', 0)}")
                # Use a fallback price (last known good price or default)
                close_price = base_info.get('bid', 1.0) + (spread_price / 2) if 'bid' in base_info else 1.0
        
        bid = close_price - (spread_price / 2)
        ask = close_price + (spread_price / 2)
        
        # CRITICAL FIX: Validate calculated bid/ask are not NaN
        if math.isnan(bid) or math.isnan(ask) or bid <= 0 or ask <= 0 or ask <= bid:
            logger.warning(f"Invalid bid/ask calculated for {symbol} at {self.current_time}: bid={bid}, ask={ask}")
            # Use fallback values
            if 'bid' in base_info and 'ask' in base_info:
                bid = base_info['bid']
                ask = base_info['ask']
            else:
                bid = close_price - (spread_price / 2)
                ask = close_price + (spread_price / 2)
        
        base_info['bid'] = bid
        base_info['ask'] = ask
        base_info['_fetched_time'] = self.current_time.timestamp()
        base_info['_tick_time'] = self.current_time.timestamp()
        
        return base_info
    
    def get_symbol_info_tick(self, symbol: str):
        """Get current tick data at simulation time."""
        if symbol not in self.historical_data:
            return None
        
        current_bar = self._get_current_bar(symbol)
        if current_bar is None:
            return None
        
        # Create tick-like object
        class Tick:
            def __init__(self, bid, ask, time):
                self.bid = bid
                self.ask = ask
                self.time = time
                self.volume = 0
        
        point = self._symbol_info_cache.get(symbol, {}).get('point', 0.00001)
        
        # CRITICAL FIX: Spread changes every tick in live trading
        # Use spread from historical data if available
        historical_spread = current_bar.get('spread', None)
        if historical_spread is not None:
            spread_points = historical_spread
        else:
            # Fallback: Use cached spread (updated in get_symbol_info)
            spread_points = self._symbol_info_cache.get(symbol, {}).get('spread', 20)
        
        spread_price = spread_points * point
        
        close_price = current_bar.get('close', 0)
        
        # CRITICAL FIX: Validate close_price is not NaN
        if close_price is None or math.isnan(close_price) or close_price <= 0:
            logger.warning(f"Invalid close price for {symbol} at {self.current_time}: {close_price}")
            # Try to get a valid price from the bar
            if 'open' in current_bar and not math.isnan(current_bar.get('open', 0)):
                close_price = current_bar.get('open', 0)
            elif 'high' in current_bar and not math.isnan(current_bar.get('high', 0)):
                close_price = current_bar.get('high', 0)
            elif 'low' in current_bar and not math.isnan(current_bar.get('low', 0)):
                close_price = current_bar.get('low', 0)
            else:
                logger.error(f"Cannot get valid price for {symbol} at {self.current_time}, returning None")
                return None
        
        bid = close_price - spread_price / 2
        ask = close_price + spread_price / 2
        
        # CRITICAL FIX: Validate calculated bid/ask are not NaN
        if math.isnan(bid) or math.isnan(ask) or bid <= 0 or ask <= 0 or ask <= bid:
            logger.error(f"Invalid bid/ask calculated for {symbol} at {self.current_time}: bid={bid}, ask={ask}")
            return None
        
        tick = Tick(
            bid=bid,
            ask=ask,
            time=int(self.current_time.timestamp())
        )
        
        return tick
    
    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account information."""
        return {
            'login': 999999,
            'balance': self.account_balance,
            'equity': self.account_equity,
            'margin': 0.0,
            'free_margin': self.account_equity,
            'margin_level': 0.0,
            'profit': self.account_profit,
            'currency': 'USD',
            'server': 'Backtest',
            'leverage': 500,
            'trade_allowed': True,
            'trade_expert': True,
            'swap_mode': 0
        }
    
    def ensure_connected(self) -> bool:
        """Always connected in backtest mode."""
        return True
    
    def is_symbol_tradeable_now(self, symbol: str, check_trade_allowed: bool = True) -> Tuple[bool, str]:
        """Check if symbol is tradeable at current time."""
        if symbol not in self.historical_data:
            return False, f"Symbol {symbol} not in historical data"
        
        current_bar = self._get_current_bar(symbol)
        if current_bar is None:
            return False, "No data available at current time"
        
        return True, ""
    
    def _get_current_bar(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current bar data for symbol at simulation time."""
        if symbol not in self.historical_data:
            return None
        
        df = self.historical_data[symbol]
        if len(df) == 0:
            return None
        
        # Find bar at or before current time
        # Assuming DataFrame has 'time' column as datetime
        if 'time' in df.columns:
            # First try exact match
            exact_match = df[df['time'] == self.current_time]
            if len(exact_match) > 0:
                return exact_match.iloc[0].to_dict()
            
            # If no exact match, get most recent bar <= current_time
            mask = df['time'] <= self.current_time
            if not mask.any():
                # If no bar before current time, get first bar (shouldn't happen in normal replay)
                logger.warning(f"No bar found at or before {self.current_time} for {symbol}, using first bar")
                return df.iloc[0].to_dict()
            
            # Get most recent bar
            current_bar = df[mask].iloc[-1]
        else:
            # If no time column, use index
            if len(df) == 0:
                return None
            current_bar = df.iloc[-1]
        
        return current_bar.to_dict()
    
    def get_historical_rates(self, symbol: str, count: int = 100) -> Optional[pd.DataFrame]:
        """
        Get historical rates up to current simulation time.
        
        This mimics mt5.copy_rates_from_pos() behavior for backtesting.
        
        Args:
            symbol: Symbol to get rates for
            count: Number of bars to return (from current time backwards)
        
        Returns:
            DataFrame with OHLCV data, or None if symbol not found
        """
        if symbol not in self.historical_data:
            return None
        
        df = self.historical_data[symbol]
        if len(df) == 0:
            return None
        
        # Get bars up to and including current time
        if 'time' in df.columns:
            # Ensure time column is datetime for comparison
            time_col = df['time']
            if not pd.api.types.is_datetime64_any_dtype(time_col):
                # Try to convert to datetime
                try:
                    if time_col.dtype in ['int64', 'int32', 'int']:
                        # Unix timestamp
                        time_col = pd.to_datetime(time_col, unit='s')
                    else:
                        time_col = pd.to_datetime(time_col)
                    df['time'] = time_col
                except Exception as e:
                    logger.warning(f"Could not convert time column to datetime for {symbol}: {e}")
                    # Fallback: use last 'count' bars without time filtering
                    if len(df) > count:
                        result_df = df.tail(count).copy()
                    else:
                        result_df = df.copy()
                    # Skip time filtering if conversion failed
                    if 'time' in result_df.columns:
                        result_df['time'] = pd.to_datetime(result_df['time'], unit='s', errors='coerce')
                    return result_df
            
            # Filter to bars <= current_time
            mask = df['time'] <= self.current_time
            if not mask.any():
                # No data before current time, return empty
                logger.debug(f"No data before {self.current_time} for {symbol}")
                return pd.DataFrame()
            
            # Get the last 'count' bars up to current time
            filtered_df = df[mask]
            if len(filtered_df) > count:
                # Return last 'count' bars
                result_df = filtered_df.tail(count).copy()
            else:
                # Return all available bars
                result_df = filtered_df.copy()
        else:
            # No time column, just return last 'count' bars
            if len(df) > count:
                result_df = df.tail(count).copy()
            else:
                result_df = df.copy()
            
            # Add time column if missing (for compatibility)
            if 'time' not in result_df.columns:
                result_df['time'] = pd.date_range(
                    start=self.current_time - timedelta(minutes=len(result_df)),
                    periods=len(result_df),
                    freq='1min'
                )
        
        # Ensure DataFrame has required columns (time, open, high, low, close, volume)
        required_cols = ['time', 'open', 'high', 'low', 'close']
        for col in required_cols:
            if col not in result_df.columns:
                if col == 'time':
                    # Create time index if missing
                    result_df['time'] = pd.date_range(start=self.current_time - timedelta(minutes=len(result_df)),
                                                     periods=len(result_df), freq='1min')
                else:
                    logger.warning(f"Missing required column '{col}' in historical data for {symbol}")
                    return None
        
        # Add volume column if missing (not critical for trend analysis)
        if 'volume' not in result_df.columns:
            result_df['volume'] = 0
        
        # Validate data quality - ensure no NaN in critical columns
        critical_cols = ['open', 'high', 'low', 'close']
        for col in critical_cols:
            if col in result_df.columns:
                nan_count = result_df[col].isna().sum()
                if nan_count > 0:
                    logger.warning(f"{symbol}: Found {nan_count} NaN values in {col}, filling with forward/backward fill")
                    result_df[col] = result_df[col].ffill().bfill()
                    # If still NaN, use previous row's close price
                    if result_df[col].isna().any():
                        if col == 'close' and len(result_df) > 1:
                            result_df[col] = result_df[col].fillna(result_df[col].shift(1))
                        else:
                            result_df[col] = result_df[col].fillna(result_df['close'] if 'close' in result_df.columns else 0)
        
        # Final validation - ensure we have valid numeric data
        if result_df['close'].isna().all() or (result_df['close'] == 0).all():
            logger.error(f"{symbol}: All close prices are invalid (NaN or zero)")
            return None
        
        # Convert time to Unix timestamp (seconds) to match MT5 format
        # Note: If time is already in Unix timestamp format, keep it
        if 'time' in result_df.columns:
            if pd.api.types.is_datetime64_any_dtype(result_df['time']):
                # Convert datetime to Unix timestamp (seconds)
                result_df['time'] = ((result_df['time'] - pd.Timestamp('1970-01-01')) // pd.Timedelta('1s')).astype('int64')
            elif result_df['time'].dtype in ['int64', 'int32', 'int']:
                # Already in Unix timestamp format, ensure it's int64
                result_df['time'] = result_df['time'].astype('int64')
            else:
                # Try to convert to int64
                try:
                    result_df['time'] = result_df['time'].astype('int64')
                except:
                    logger.warning(f"Could not convert time column to int64 for {symbol}")
                    return None
        
        return result_df
    
    def update_account(self, balance: float = None, equity: float = None, profit: float = None):
        """Update account state."""
        with self._lock:
            if balance is not None:
                self.account_balance = balance
            if equity is not None:
                self.account_equity = equity
            if profit is not None:
                self.account_profit = profit


