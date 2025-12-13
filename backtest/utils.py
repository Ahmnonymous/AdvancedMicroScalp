"""
Backtest Utility Functions
Consolidated helper functions for backtesting operations.
"""

import MetaTrader5 as mt5
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np


def parse_timeframe(timeframe_str: str) -> int:
    """
    Convert timeframe string to MT5 constant.
    
    Args:
        timeframe_str: Timeframe string (e.g., 'M1', 'M5', 'H1', 'D1')
    
    Returns:
        MT5 timeframe constant
    """
    timeframe_map = {
        'M1': mt5.TIMEFRAME_M1,
        'M5': mt5.TIMEFRAME_M5,
        'M15': mt5.TIMEFRAME_M15,
        'M30': mt5.TIMEFRAME_M30,
        'H1': mt5.TIMEFRAME_H1,
        'H4': mt5.TIMEFRAME_H4,
        'D1': mt5.TIMEFRAME_D1
    }
    return timeframe_map.get(timeframe_str.upper(), mt5.TIMEFRAME_M1)


def get_timeframe_seconds(timeframe: int) -> int:
    """
    Get timeframe duration in seconds.
    
    Args:
        timeframe: MT5 timeframe constant
    
    Returns:
        Duration in seconds
    """
    timeframe_map = {
        mt5.TIMEFRAME_M1: 60,
        mt5.TIMEFRAME_M5: 300,
        mt5.TIMEFRAME_M15: 900,
        mt5.TIMEFRAME_M30: 1800,
        mt5.TIMEFRAME_H1: 3600,
        mt5.TIMEFRAME_H4: 14400,
        mt5.TIMEFRAME_D1: 86400,
    }
    return timeframe_map.get(timeframe, 0)


def get_timeframe_string(timeframe: int) -> str:
    """
    Convert MT5 timeframe constant to string.
    
    Args:
        timeframe: MT5 timeframe constant
    
    Returns:
        Timeframe string (e.g., 'M1', 'H1')
    """
    timeframe_map = {
        mt5.TIMEFRAME_M1: 'M1',
        mt5.TIMEFRAME_M5: 'M5',
        mt5.TIMEFRAME_M15: 'M15',
        mt5.TIMEFRAME_M30: 'M30',
        mt5.TIMEFRAME_H1: 'H1',
        mt5.TIMEFRAME_H4: 'H4',
        mt5.TIMEFRAME_D1: 'D1'
    }
    return timeframe_map.get(timeframe, 'M1')


def calculate_date_range(months: int = 1, days: int = None, end_date: datetime = None) -> Tuple[datetime, datetime]:
    """
    Calculate date range for backtesting.
    
    Args:
        months: Number of months to go back (default: 1)
        days: Number of days to go back (overrides months if provided)
        end_date: End date (default: yesterday)
    
    Returns:
        (start_date, end_date) tuple
    """
    if end_date is None:
        end_date = datetime.now() - timedelta(days=1)  # Yesterday
    
    if days is not None:
        start_date = end_date - timedelta(days=days)
    else:
        start_date = end_date - timedelta(days=months * 30)  # Approximate months as 30 days
    
    return start_date, end_date


def iterate_symbols(symbols: List[str], callback: callable, *args, **kwargs) -> Dict[str, Any]:
    """
    Iterate over symbols and execute callback for each.
    
    Args:
        symbols: List of symbol names
        callback: Function to call for each symbol
        *args: Additional positional arguments for callback
        **kwargs: Additional keyword arguments for callback
    
    Returns:
        Dictionary mapping symbol to callback result
    """
    results = {}
    for symbol in symbols:
        try:
            results[symbol] = callback(symbol, *args, **kwargs)
        except Exception as e:
            results[symbol] = {'error': str(e)}
    return results


def load_symbol_data(symbol: str, timeframe: int, start_date: datetime, end_date: datetime,
                     use_ticks: bool = False, config: Dict[str, Any] = None) -> Optional[pd.DataFrame]:
    """
    Load historical data for a single symbol.
    
    Args:
        symbol: Trading symbol
        timeframe: MT5 timeframe constant
        start_date: Start date
        end_date: End date
        use_ticks: If True, load tick data (default: False)
        config: Configuration dictionary (for MT5 connection)
    
    Returns:
        DataFrame with historical data, or None if failed
    """
    mt5_config = config.get('mt5', {}) if config else {}
    
    # Initialize MT5 if needed
    if not mt5.initialize(path=mt5_config.get('path', '')):
        return None
    
    # Login if credentials provided
    if mt5_config.get('account') and mt5_config.get('password'):
        account = int(mt5_config['account'])
        password = mt5_config['password']
        server = mt5_config.get('server', '')
        mt5.login(account, password=password, server=server)
    
    try:
        # Verify symbol exists
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return None
        
        # Enable symbol if needed
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)
        
        if use_ticks:
            # Load tick data
            ticks = mt5.copy_ticks_range(symbol, start_date, end_date, mt5.COPY_TICKS_ALL)
            if ticks is None or len(ticks) == 0:
                return None
            return pd.DataFrame(ticks)
        else:
            # Load candle data
            rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
            if rates is None or len(rates) == 0:
                return None
            return pd.DataFrame(rates)
    except Exception:
        return None


def ensure_directory(path: str) -> Path:
    """
    Ensure directory exists, create if needed.
    
    Args:
        path: Directory path
    
    Returns:
        Path object
    """
    dir_path = Path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string.
    
    Args:
        seconds: Duration in seconds
    
    Returns:
        Formatted string (e.g., "1h 23m 45s")
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or len(parts) == 0:
        parts.append(f"{secs}s")
    
    return " ".join(parts)


def validate_backtest_config(config: Dict[str, Any]) -> Tuple[bool, List[str], List[str]]:
    """
    Validate backtest configuration.
    
    Args:
        config: Configuration dictionary
    
    Returns:
        (is_valid, errors, warnings) tuple
    """
    errors = []
    warnings = []
    
    backtest_config = config.get('backtest', {})
    
    # Check mode
    mode = config.get('mode', 'live')
    if mode != 'backtest':
        errors.append(f"Mode must be 'backtest' for backtesting, got '{mode}'")
    
    # Check required fields
    if 'symbols' not in backtest_config:
        errors.append("backtest.symbols is required")
    elif not isinstance(backtest_config['symbols'], list) or len(backtest_config['symbols']) == 0:
        errors.append("backtest.symbols must be a non-empty list")
    
    if 'start_date' not in backtest_config:
        errors.append("backtest.start_date is required")
    else:
        try:
            datetime.fromisoformat(backtest_config['start_date'])
        except Exception as e:
            errors.append(f"backtest.start_date is invalid: {e}")
    
    if 'end_date' not in backtest_config:
        errors.append("backtest.end_date is required")
    else:
        try:
            end_date = datetime.fromisoformat(backtest_config['end_date'])
            start_date = datetime.fromisoformat(backtest_config.get('start_date', '2024-01-01T00:00:00'))
            if end_date <= start_date:
                errors.append("backtest.end_date must be after backtest.start_date")
        except Exception as e:
            errors.append(f"backtest.end_date is invalid: {e}")
    
    # Check timeframe
    timeframe = backtest_config.get('timeframe', 'M1')
    valid_timeframes = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
    if timeframe not in valid_timeframes:
        warnings.append(f"Unusual timeframe '{timeframe}', expected one of {valid_timeframes}")
    
    return len(errors) == 0, errors, warnings

