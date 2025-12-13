"""
Historical Data Replay Engine
Replays historical market data tick-by-tick or candle-by-candle.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta
import threading
import time

from utils.logger_factory import get_logger

logger = get_logger("backtest_replay", "logs/backtest/replay.log")


class HistoricalReplayEngine:
    """Replays historical market data for backtesting."""
    
    def __init__(self, config: Dict[str, Any], symbols: List[str], 
                 start_date: datetime, end_date: datetime,
                 timeframe: str = 'M1', use_ticks: bool = False):
        """
        Initialize historical replay engine.
        
        Args:
            config: Configuration dictionary
            symbols: List of symbols to replay
            start_date: Start date for historical data
            end_date: End date for historical data
            timeframe: Timeframe ('M1', 'M5', 'H1', etc.)
            use_ticks: If True, replay tick-by-tick (slower but more accurate)
        """
        self.config = config
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.timeframe = self._parse_timeframe(timeframe)
        self.use_ticks = use_ticks
        
        # Historical data storage
        self.historical_data = {}  # {symbol: DataFrame}
        self.tick_data = {}  # {symbol: List of ticks} (if use_ticks)
        
        # Replay state - start from actual data start, not requested start
        self.current_time = start_date
        self.actual_data_start = None  # Will be set after loading data
        self.actual_data_end = None
        self.is_replaying = False
        self.replay_speed = 1.0  # 1.0 = real-time, >1.0 = faster
        self._lock = threading.Lock()
        
        # Callbacks
        self.on_tick_callbacks = []  # Called on each tick/bar
        self.on_bar_callbacks = []  # Called on each new bar
        
        # Statistics
        self.replay_stats = {
            'bars_processed': 0,
            'ticks_processed': 0,
            'time_elapsed': 0.0,
            'replay_duration': 0.0
        }
    
    def _parse_timeframe(self, tf: str) -> int:
        """Convert timeframe string to MT5 constant."""
        from backtest.utils import parse_timeframe
        return parse_timeframe(tf)
    
    def load_historical_data(self) -> bool:
        """Load historical data for all symbols."""
        logger.info(f"Loading historical data from {self.start_date} to {self.end_date}")
        
        # Get MT5 config
        mt5_config = self.config.get('mt5', {})
        
        # Try to initialize MT5 with login
        if not mt5.initialize(path=mt5_config.get('path', '')):
            error_code = mt5.last_error()
            logger.error(f"Failed to initialize MT5 for historical data loading. Error: {error_code}")
            logger.error("Make sure MT5 terminal is running")
            return False
        
        # Login if credentials provided
        if mt5_config.get('account') and mt5_config.get('password'):
            account = int(mt5_config['account'])
            password = mt5_config['password']
            server = mt5_config.get('server', '')
            
            if not mt5.login(account, password=password, server=server):
                error_code = mt5.last_error()
                logger.warning(f"MT5 login failed. Error: {error_code}. Continuing without login (may have limited data access)")
            else:
                logger.info(f"MT5 logged in successfully to account {account}")
        
        try:
            data_loaded = False
            for symbol in self.symbols:
                logger.info(f"Loading data for {symbol}...")
                
                # Verify symbol exists
                symbol_info = mt5.symbol_info(symbol)
                if symbol_info is None:
                    logger.error(f"Symbol {symbol} does not exist in MT5")
                    continue
                
                # Check if symbol is available
                if not symbol_info.visible:
                    logger.warning(f"Symbol {symbol} is not visible. Attempting to enable...")
                    if not mt5.symbol_select(symbol, True):
                        logger.error(f"Failed to enable symbol {symbol}")
                        continue
                
                if self.use_ticks:
                    # Load tick data in chunks (MT5 may limit large ranges)
                    all_ticks = []
                    chunk_days = 30  # Load 30 days at a time
                    current_start = self.start_date
                    
                    while current_start < self.end_date:
                        chunk_end = min(current_start + timedelta(days=chunk_days), self.end_date)
                        logger.debug(f"Loading ticks from {current_start} to {chunk_end}")
                        
                        ticks = mt5.copy_ticks_range(symbol, current_start, chunk_end, mt5.COPY_TICKS_ALL)
                        if ticks is not None and len(ticks) > 0:
                            all_ticks.extend(ticks)
                            logger.debug(f"Loaded {len(ticks)} ticks for chunk")
                        else:
                            error_code = mt5.last_error()
                            logger.warning(f"No tick data for chunk {current_start} to {chunk_end}. Error: {error_code}")
                        
                        current_start = chunk_end
                    
                    if len(all_ticks) == 0:
                        error_code = mt5.last_error()
                        logger.warning(f"No tick data found for {symbol}. MT5 Error: {error_code}")
                        logger.warning(f"Date range: {self.start_date} to {self.end_date}")
                        continue
                    
                    # Convert to DataFrame
                    df = pd.DataFrame(all_ticks)
                    df['time'] = pd.to_datetime(df['time'], unit='s')
                    self.tick_data[symbol] = df.sort_values('time').reset_index(drop=True)
                    logger.info(f"Loaded {len(df)} ticks for {symbol}")
                    data_loaded = True
                else:
                    # Load bar data - try multiple methods
                    all_rates = []
                    
                    # Ensure dates are timezone-naive (MT5 expects naive datetime)
                    start_date_naive = self.start_date.replace(tzinfo=None) if self.start_date.tzinfo else self.start_date
                    end_date_naive = self.end_date.replace(tzinfo=None) if self.end_date.tzinfo else self.end_date
                    
                    # Don't request future dates - cap at today
                    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    if end_date_naive > today:
                        logger.warning(f"End date {end_date_naive} is in the future, capping to {today}")
                        end_date_naive = today
                    
                    # Test if symbol has any data available and check available range
                    test_rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, 10)
                    if test_rates is None or len(test_rates) == 0:
                        logger.warning(f"Symbol {symbol} has no recent data available. Skipping...")
                        continue
                    
                    # Check available data range and adjust if needed
                    try:
                        if hasattr(test_rates, 'dtype') and test_rates.dtype.names and 'time' in test_rates.dtype.names:
                            test_df = pd.DataFrame(test_rates)
                            latest_time = pd.to_datetime(test_df['time'].max(), unit='s')
                            logger.debug(f"Symbol {symbol} latest data: {latest_time}")
                            
                            # If requested end date is after latest available data, adjust it
                            if end_date_naive > latest_time:
                                logger.warning(f"Requested end date {end_date_naive} is after latest available data {latest_time}. Adjusting...")
                                end_date_naive = latest_time.replace(hour=0, minute=0, second=0, microsecond=0)
                                # Recalculate start if needed
                                if start_date_naive >= end_date_naive:
                                    start_date_naive = (end_date_naive - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
                                    logger.info(f"Adjusted date range to {start_date_naive} to {end_date_naive}")
                    except Exception as e:
                        logger.warning(f"Could not check available data range: {e}. Continuing with requested range...")
                    
                    # Method 1: Try copy_rates_from_pos first (more reliable for recent data)
                    logger.info(f"Attempting to load data using copy_rates_from_pos...")
                    days_diff = (end_date_naive - start_date_naive).days
                    
                    # Calculate approximate bar count needed
                    if self.timeframe == mt5.TIMEFRAME_M1:
                        count = days_diff * 24 * 60
                    elif self.timeframe == mt5.TIMEFRAME_M5:
                        count = days_diff * 24 * 12
                    elif self.timeframe == mt5.TIMEFRAME_M15:
                        count = days_diff * 24 * 4
                    elif self.timeframe == mt5.TIMEFRAME_M30:
                        count = days_diff * 24 * 2
                    elif self.timeframe == mt5.TIMEFRAME_H1:
                        count = days_diff * 24
                    elif self.timeframe == mt5.TIMEFRAME_H4:
                        count = days_diff * 6
                    elif self.timeframe == mt5.TIMEFRAME_D1:
                        count = days_diff
                    else:
                        count = days_diff * 24 * 60  # Default to M1
                    
                    # MT5 limit is typically 100,000 bars, but start smaller for reliability
                    count = min(count, 100000)
                    
                    # Try with calculated count first
                    rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, count)
                    
                    # If that fails, try with a smaller count (last 30 days)
                    if rates is None or len(rates) == 0:
                        logger.info(f"Trying with smaller count (30 days)...")
                        if self.timeframe == mt5.TIMEFRAME_M1:
                            count = 30 * 24 * 60
                        elif self.timeframe == mt5.TIMEFRAME_M5:
                            count = 30 * 24 * 12
                        elif self.timeframe == mt5.TIMEFRAME_H1:
                            count = 30 * 24
                        else:
                            count = 30
                        count = min(count, 100000)
                        rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, count)
                    if rates is not None and len(rates) > 0:
                        # Convert numpy structured array to DataFrame properly
                        if hasattr(rates, 'dtype') and rates.dtype.names:
                            # It's a numpy structured array - convert to DataFrame with proper column names
                            df_temp = pd.DataFrame(rates)
                        else:
                            # Regular array - try to convert
                            df_temp = pd.DataFrame(rates)
                            # If columns are integers, MT5 returns structured array - try again
                            if len(df_temp.columns) > 0 and isinstance(df_temp.columns[0], (int, np.integer)):
                                # Reconstruct from numpy array
                                import numpy as np
                                if isinstance(rates, np.ndarray) and rates.dtype.names:
                                    df_temp = pd.DataFrame(rates)
                                else:
                                    logger.warning(f"Unexpected data format from copy_rates_from_pos. Type: {type(rates)}")
                                    rates = None
                                    df_temp = None
                        
                        if df_temp is not None and len(df_temp) > 0:
                            # Check for time column
                            if 'time' in df_temp.columns:
                                df_temp['time'] = pd.to_datetime(df_temp['time'], unit='s')
                                
                                # Filter by date range
                                mask = (df_temp['time'] >= start_date_naive) & (df_temp['time'] <= end_date_naive)
                                filtered_df = df_temp[mask]
                                
                                if len(filtered_df) > 0:
                                    all_rates = filtered_df.to_dict('records')
                                    logger.info(f"Loaded {len(all_rates)} bars using copy_rates_from_pos (filtered from {len(rates)} total)")
                                else:
                                    logger.warning(f"copy_rates_from_pos returned {len(rates)} bars but none in date range {start_date_naive} to {end_date_naive}")
                                    # Try copy_rates_range as fallback
                                    rates = None
                            else:
                                logger.warning(f"copy_rates_from_pos returned data but no 'time' column. Columns: {list(df_temp.columns)}")
                                rates = None
                        else:
                            rates = None
                    else:
                        error_code = mt5.last_error()
                        logger.warning(f"copy_rates_from_pos failed. Error: {error_code}. Trying copy_rates_range...")
                        rates = None
                    
                    # Method 2: Fallback to copy_rates_range in chunks if copy_rates_from_pos failed
                    if rates is None or len(all_rates) == 0:
                        logger.info(f"Trying copy_rates_range in chunks...")
                        chunk_days = 30  # Smaller chunks for better reliability
                        current_start = start_date_naive
                        
                        while current_start < end_date_naive:
                            chunk_end = min(current_start + timedelta(days=chunk_days), end_date_naive)
                            logger.debug(f"Loading bars from {current_start} to {chunk_end}")
                            
                            rates = mt5.copy_rates_range(symbol, self.timeframe, current_start, chunk_end)
                            if rates is not None and len(rates) > 0:
                                # MT5 returns numpy structured array - convert to list of dicts immediately
                                # This preserves column names (time, open, high, low, close, etc.)
                                try:
                                    if hasattr(rates, 'dtype') and rates.dtype.names:
                                        # It's a numpy structured array - convert to list of dicts
                                        rates_list = []
                                        for row in rates:
                                            row_dict = {}
                                            for name in rates.dtype.names:
                                                row_dict[name] = row[name]
                                            rates_list.append(row_dict)
                                        all_rates.extend(rates_list)
                                    else:
                                        # Try to convert to DataFrame first, then to dicts
                                        temp_df = pd.DataFrame(rates)
                                        if len(temp_df.columns) > 0:
                                            # If columns are integers, assign MT5 standard names
                                            if isinstance(temp_df.columns[0], (int, np.integer)):
                                                if len(temp_df.columns) >= 8:
                                                    temp_df.columns = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']
                                                elif len(temp_df.columns) >= 5:
                                                    temp_df.columns = ['time', 'open', 'high', 'low', 'close'][:len(temp_df.columns)]
                                            all_rates.extend(temp_df.to_dict('records'))
                                        else:
                                            all_rates.extend(rates)
                                    logger.debug(f"Loaded {len(rates)} bars for chunk")
                                except Exception as e:
                                    logger.error(f"Error converting rates data for chunk {current_start} to {chunk_end}: {e}")
                                    error_code = mt5.last_error()
                                    logger.warning(f"Failed to process bar data for chunk. Error: {error_code}")
                            else:
                                error_code = mt5.last_error()
                                logger.warning(f"No bar data for chunk {current_start} to {chunk_end}. Error: {error_code}")
                            
                            current_start = chunk_end
                            
                            # If we've loaded some data, continue; otherwise try smaller chunks
                            if len(all_rates) == 0 and chunk_days > 7:
                                chunk_days = max(7, chunk_days // 2)
                                logger.info(f"Reducing chunk size to {chunk_days} days")
                                current_start = start_date_naive  # Restart with smaller chunks
                                all_rates = []
                                break
                    
                    if len(all_rates) == 0:
                        error_code = mt5.last_error()
                        logger.warning(f"No bar data found for {symbol}. MT5 Error: {error_code}")
                        logger.warning(f"Date range: {start_date_naive} to {end_date_naive}")
                        logger.warning(f"Timeframe: {self.timeframe}")
                        
                        # Check what data is actually available
                        available_from = symbol_info.time
                        if available_from:
                            available_from_dt = datetime.fromtimestamp(available_from)
                            logger.info(f"Symbol {symbol} exists. Available from: {available_from_dt}")
                            
                            # Try to get just the most recent data to verify symbol works
                            test_rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, 100)
                            if test_rates is not None and len(test_rates) > 0:
                                test_df = pd.DataFrame(test_rates)
                                test_df['time'] = pd.to_datetime(test_df['time'], unit='s')
                                latest = test_df['time'].max()
                                oldest = test_df['time'].min()
                                logger.info(f"Symbol has data available. Latest bar: {latest}, Oldest bar in recent data: {oldest}")
                                logger.warning(f"Requested range {start_date_naive} to {end_date_naive} may be outside available data range")
                                
                                # If requested start is before available data, try loading from available start
                                if start_date_naive < available_from_dt:
                                    logger.info(f"Adjusting start date to available data start: {available_from_dt}")
                                    start_date_naive = available_from_dt
                                    # Retry with adjusted date
                                    days_diff = (end_date_naive - start_date_naive).days
                                    if self.timeframe == mt5.TIMEFRAME_M1:
                                        count = min(days_diff * 24 * 60, 100000)
                                    elif self.timeframe == mt5.TIMEFRAME_M5:
                                        count = min(days_diff * 24 * 12, 100000)
                                    elif self.timeframe == mt5.TIMEFRAME_H1:
                                        count = min(days_diff * 24, 100000)
                                    else:
                                        count = min(days_diff, 100000)
                                    
                                    retry_rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, count)
                                    if retry_rates is not None and len(retry_rates) > 0:
                                        # Convert numpy structured array properly
                                        if hasattr(retry_rates, 'dtype') and retry_rates.dtype.names:
                                            retry_df = pd.DataFrame(retry_rates)
                                        else:
                                            retry_df = pd.DataFrame(retry_rates)
                                        
                                        if 'time' in retry_df.columns:
                                            retry_df['time'] = pd.to_datetime(retry_df['time'], unit='s')
                                            mask = (retry_df['time'] >= start_date_naive) & (retry_df['time'] <= end_date_naive)
                                            filtered_df = retry_df[mask]
                                            if len(filtered_df) > 0:
                                                all_rates = filtered_df.to_dict('records')
                                                logger.info(f"Successfully loaded {len(all_rates)} bars with adjusted date range")
                                        else:
                                            logger.warning(f"Retry data has no 'time' column. Columns: {list(retry_df.columns)}")
                            else:
                                logger.error(f"Symbol {symbol} exists but has no historical data available")
                        continue
                    
                    # Convert to DataFrame - handle different data formats
                    if len(all_rates) == 0:
                        logger.warning(f"No rates data collected for {symbol}")
                        continue
                    
                    # Check if first item is a dict (from our conversion) or numpy array
                    first_item = all_rates[0] if all_rates else None
                    if isinstance(first_item, dict):
                        # Already converted to dicts - create DataFrame directly
                        df = pd.DataFrame(all_rates)
                    elif hasattr(first_item, 'dtype') and first_item.dtype.names:
                        # It's a numpy structured array - convert properly
                        df = pd.DataFrame(all_rates)
                    else:
                        # Try to convert - might be list of tuples or arrays
                        try:
                            df = pd.DataFrame(all_rates)
                            # If columns are integers, try to infer from MT5 structure
                            if len(df.columns) > 0 and isinstance(df.columns[0], (int, np.integer)):
                                # MT5 returns: time, open, high, low, close, tick_volume, spread, real_volume
                                if len(df.columns) >= 8:
                                    df.columns = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']
                                elif len(df.columns) >= 5:
                                    df.columns = ['time', 'open', 'high', 'low', 'close'][:len(df.columns)]
                                else:
                                    logger.error(f"Cannot infer column names for {symbol}. Got {len(df.columns)} columns")
                                    continue
                        except Exception as e:
                            logger.error(f"Failed to convert rates to DataFrame for {symbol}: {e}")
                            continue
                    
                    if len(df) == 0:
                        logger.warning(f"Empty DataFrame for {symbol}")
                        continue
                    
                    # Check for time column (handle both string and integer column names)
                    time_col = None
                    for col in df.columns:
                        if isinstance(col, str) and col.lower() == 'time':
                            time_col = col
                            break
                        elif col == 'time' or (isinstance(col, (int, np.integer)) and df.columns.get_loc(col) == 0):
                            # First column is usually time in MT5 data
                            time_col = col
                            break
                    
                    if time_col is None:
                        # Check if time is in a different format (case-insensitive)
                        for col in df.columns:
                            if isinstance(col, str) and 'time' in col.lower():
                                time_col = col
                                break
                    
                    if time_col is None:
                        # Try using first column as time (MT5 standard format)
                        if len(df.columns) > 0:
                            time_col = df.columns[0]
                            logger.info(f"Using first column '{time_col}' as time for {symbol}")
                        else:
                            logger.error(f"No 'time' column found in rates data for {symbol}. Columns: {list(df.columns)}")
                            continue
                    
                    # Convert time column to datetime
                    try:
                        df['time'] = pd.to_datetime(df[time_col], unit='s')
                    except Exception as e:
                        logger.error(f"Failed to convert time column for {symbol}: {e}")
                        continue
                    
                    # Remove duplicates and sort
                    df = df.drop_duplicates(subset=['time']).sort_values('time').reset_index(drop=True)
                    
                    # Final date range check
                    if len(df) > 0:
                        actual_start = df['time'].min()
                        actual_end = df['time'].max()
                        logger.info(f"Loaded {len(df)} bars for {symbol} (from {actual_start} to {actual_end})")
                        
                        # Warn if date range doesn't match
                        if actual_start > start_date_naive:
                            logger.warning(f"Actual start date {actual_start} is later than requested {start_date_naive}")
                        if actual_end < end_date_naive:
                            logger.warning(f"Actual end date {actual_end} is earlier than requested {end_date_naive}")
                        
                        self.historical_data[symbol] = df
                        data_loaded = True
                        
                        # Track actual data range
                        if self.actual_data_start is None or actual_start < self.actual_data_start:
                            self.actual_data_start = actual_start
                        if self.actual_data_end is None or actual_end > self.actual_data_end:
                            self.actual_data_end = actual_end
                    else:
                        logger.warning(f"DataFrame is empty after processing for {symbol}")
            
            mt5.shutdown()
            
            if not data_loaded:
                logger.error("No historical data loaded for any symbol")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error loading historical data: {e}", exc_info=True)
            mt5.shutdown()
            return False
    
    def register_tick_callback(self, callback: Callable):
        """Register callback for each tick/bar."""
        self.on_tick_callbacks.append(callback)
    
    def register_bar_callback(self, callback: Callable):
        """Register callback for each new bar."""
        self.on_bar_callbacks.append(callback)
    
    def get_current_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current bar/tick data for symbol at current replay time."""
        if self.use_ticks:
            if symbol not in self.tick_data:
                return None
            
            df = self.tick_data[symbol]
            mask = df['time'] <= self.current_time
            if not mask.any():
                return None
            
            tick = df[mask].iloc[-1]
            return {
                'time': tick['time'],
                'bid': tick['bid'],
                'ask': tick['ask'],
                'volume': tick.get('volume', 0),
                'flags': tick.get('flags', 0)
            }
        else:
            if symbol not in self.historical_data:
                return None
            
            df = self.historical_data[symbol]
            mask = df['time'] <= self.current_time
            if not mask.any():
                return None
            
            bar = df[mask].iloc[-1]
            return {
                'time': bar['time'],
                'open': bar['open'],
                'high': bar['high'],
                'low': bar['low'],
                'close': bar['close'],
                'tick_volume': bar.get('tick_volume', 0),
                'spread': bar.get('spread', 0),
                'real_volume': bar.get('real_volume', 0)
            }
    
    def step_forward(self, step_size: timedelta = None) -> bool:
        """
        Step forward in time by one bar or tick.
        
        Args:
            step_size: Optional custom step size (default: 1 bar or 1 tick)
        
        Returns:
            True if stepped forward, False if reached end
        """
        with self._lock:
            if self.current_time >= self.end_date:
                return False
            
            if self.use_ticks:
                # Find next tick across all symbols
                next_tick_time = None
                for symbol, df in self.tick_data.items():
                    if len(df) == 0:
                        continue
                    mask = df['time'] > self.current_time
                    if mask.any():
                        next_tick = df[mask].iloc[0]
                        tick_time = next_tick['time']
                        if next_tick_time is None or tick_time < next_tick_time:
                            next_tick_time = tick_time
                
                if next_tick_time is None:
                    self.current_time = self.end_date
                    return False
                
                self.current_time = next_tick_time
                self.replay_stats['ticks_processed'] += 1
                
                # Call tick callbacks
                for callback in self.on_tick_callbacks:
                    try:
                        callback(self.current_time)
                    except Exception as e:
                        logger.error(f"Error in tick callback: {e}", exc_info=True)
            else:
                # Find next bar across all symbols - use actual data, not just time increment
                next_bar_time = None
                for symbol, df in self.historical_data.items():
                    if len(df) == 0:
                        continue
                    mask = df['time'] > self.current_time
                    if mask.any():
                        next_bar = df[mask].iloc[0]
                        bar_time = next_bar['time']
                        if next_bar_time is None or bar_time < next_bar_time:
                            next_bar_time = bar_time
                
                if next_bar_time is None:
                    # No more bars available - check if we're before data starts
                    if self.actual_data_start and self.current_time < self.actual_data_start:
                        # Jump to actual data start
                        logger.info(f"No data at {self.current_time}, jumping to actual data start {self.actual_data_start}")
                        self.current_time = self.actual_data_start
                        # Try again
                        return self.step_forward(step_size)
                    else:
                        # Reached end of data
                        self.current_time = self.end_date
                        return False
                
                self.current_time = next_bar_time
                self.replay_stats['bars_processed'] += 1
                
                # Call callbacks
                for callback in self.on_tick_callbacks:
                    try:
                        callback(self.current_time)
                    except Exception as e:
                        logger.error(f"Error in tick callback: {e}", exc_info=True)
                
                # Always call bar callbacks since we're stepping through actual bars
                for callback in self.on_bar_callbacks:
                    try:
                        callback(self.current_time)
                    except Exception as e:
                        logger.error(f"Error in bar callback: {e}", exc_info=True)
            
            return True
    
    def replay(self, speed: float = 1.0, step_callback: Optional[Callable] = None):
        """
        Replay historical data from start to end.
        
        Args:
            speed: Replay speed multiplier (1.0 = real-time, >1.0 = faster)
            step_callback: Optional callback called after each step
        """
        self.is_replaying = True
        self.replay_speed = speed
        start_replay_time = time.time()
        
        # Adjust start time to actual data start if available
        if self.actual_data_start:
            logger.info(f"Adjusting replay start from {self.start_date} to actual data start {self.actual_data_start}")
            self.current_time = self.actual_data_start
        
        logger.info(f"Starting historical replay from {self.current_time} to {self.end_date}")
        logger.info(f"Replay speed: {speed}x")
        
        # Calculate total expected steps
        if self.use_ticks:
            total_ticks = sum(len(df) for df in self.tick_data.values())
            logger.info(f"Total ticks to replay: {total_ticks}")
        else:
            total_bars = sum(len(df) for df in self.historical_data.values())
            logger.info(f"Total bars to replay: {total_bars}")
        
        step_count = 0
        last_log_time = time.time()
        last_log_step = 0
        
        while self.step_forward():
            step_count += 1
            
            # Log progress every 1000 steps or every 10 seconds
            current_time_log = time.time()
            if step_count % 1000 == 0 or (current_time_log - last_log_time) >= 10.0:
                elapsed = current_time_log - start_replay_time
                steps_per_sec = (step_count - last_log_step) / max(current_time_log - last_log_time, 0.1)
                progress_pct = (step_count / max(total_bars if not self.use_ticks else total_ticks, 1)) * 100
                logger.info(f"Replay progress: {step_count}/{total_bars if not self.use_ticks else total_ticks} ({progress_pct:.1f}%) | Time: {self.current_time} | Speed: {steps_per_sec:.1f} steps/sec | Elapsed: {elapsed:.1f}s")
                last_log_time = current_time_log
                last_log_step = step_count
            
            # Call step callback if provided
            if step_callback:
                try:
                    step_callback(self.current_time, step_count)
                except Exception as e:
                    logger.error(f"Error in step callback: {e}", exc_info=True)
            
            # Sleep to control replay speed (if not instant and speed is reasonable)
            # For backtesting, we want maximum speed, so only sleep if speed is very low (< 1.0)
            # At higher speeds, skip sleep entirely for performance
            if speed > 0 and speed < 1.0 and not self.use_ticks:
                # Only sleep for very slow speeds (< 1.0x)
                step_duration = timedelta(minutes=1).total_seconds() / speed
                if step_duration > 0:
                    time.sleep(step_duration)
            # For speed >= 1.0, skip sleep entirely - process as fast as possible
        
        replay_duration = time.time() - start_replay_time
        self.replay_stats['replay_duration'] = replay_duration
        self.replay_stats['time_elapsed'] = (self.end_date - self.start_date).total_seconds()
        
        self.is_replaying = False
        logger.info(f"Replay complete. Processed {step_count} steps in {replay_duration:.2f}s")
        logger.info(f"Average speed: {step_count / max(replay_duration, 0.1):.1f} steps/sec")
    
    def get_current_time(self) -> datetime:
        """Get current replay time."""
        with self._lock:
            return self.current_time
    
    def set_current_time(self, time: datetime):
        """Set current replay time (for manual control)."""
        with self._lock:
            self.current_time = time
    
    def get_replay_stats(self) -> Dict[str, Any]:
        """Get replay statistics."""
        with self._lock:
            return self.replay_stats.copy()

