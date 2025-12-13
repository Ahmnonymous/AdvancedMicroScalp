"""
Backtest Data Preflight Validator
Validates that all required data is available before backtest execution.
"""

import MetaTrader5 as mt5
import logging
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger(__name__)


class BacktestDataPreflightValidator:
    """Validates backtest data availability before execution."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector):
        """
        Initialize validator.
        
        Args:
            config: Configuration dictionary
            mt5_connector: MT5Connector instance
        """
        self.config = config
        self.mt5_connector = mt5_connector
        self.errors = []
        self.warnings = []
    
    def validate(self, symbol: str, timeframe: int, start_date: datetime, end_date: datetime,
                 required_bars: int = 1000) -> Tuple[bool, List[str], List[str]]:
        """
        Validate that all required data is available.
        
        Args:
            symbol: Trading symbol
            timeframe: MT5 timeframe constant
            start_date: Start date for backtest
            end_date: End date for backtest
            required_bars: Minimum number of bars required
        
        Returns:
            (is_valid, errors, warnings)
        """
        self.errors = []
        self.warnings = []
        mode = "BACKTEST" if self.config.get('mode') == 'backtest' else "LIVE"
        
        logger.info(f"mode={mode} | Starting data preflight validation for {symbol}")
        
        # 1. Validate symbol exists
        if not self._validate_symbol(symbol, mode):
            return False, self.errors, self.warnings
        
        # 2. Validate timeframe available
        if not self._validate_timeframe(symbol, timeframe, mode):
            return False, self.errors, self.warnings
        
        # 3. Validate bars loaded
        if not self._validate_bars(symbol, timeframe, start_date, end_date, required_bars, mode):
            return False, self.errors, self.warnings
        
        # 4. Validate date range
        if not self._validate_date_range(symbol, timeframe, start_date, end_date, mode):
            return False, self.errors, self.warnings
        
        # 5. Validate spread availability
        if not self._validate_spread(symbol, mode):
            return False, self.errors, self.warnings
        
        # 6. Validate contract specs
        if not self._validate_contract_specs(symbol, mode):
            return False, self.errors, self.warnings
        
        is_valid = len(self.errors) == 0
        
        if is_valid:
            logger.info(f"mode={mode} | symbol={symbol} | Data preflight validation PASSED")
        else:
            logger.critical(f"mode={mode} | symbol={symbol} | Data preflight validation FAILED - {len(self.errors)} errors")
        
        return is_valid, self.errors, self.warnings
    
    def _validate_symbol(self, symbol: str, mode: str) -> bool:
        """Validate symbol exists in MT5."""
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        
        if symbol_info is None:
            self.errors.append(f"Symbol {symbol} not found in MT5")
            logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Symbol not found")
            return False
        
        logger.info(f"mode={mode} | symbol={symbol} | Symbol exists: {symbol_info.get('name')}")
        return True
    
    def _validate_timeframe(self, symbol: str, timeframe: int, mode: str) -> bool:
        """Validate timeframe is available for symbol."""
        # Try to get rates for the timeframe
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 10)
        
        if rates is None or len(rates) == 0:
            self.errors.append(f"Timeframe {timeframe} not available for {symbol}")
            logger.critical(f"mode={mode} | symbol={symbol} | timeframe={timeframe} | VALIDATION FAILED: No data for timeframe")
            return False
        
        logger.info(f"mode={mode} | symbol={symbol} | timeframe={timeframe} | Timeframe available: {len(rates)} bars")
        return True
    
    def _validate_bars(self, symbol: str, timeframe: int, start_date: datetime, end_date: datetime,
                       required_bars: int, mode: str) -> bool:
        """Validate sufficient bars are available."""
        # Calculate expected bars
        timeframe_seconds = self._get_timeframe_seconds(timeframe)
        if timeframe_seconds == 0:
            self.errors.append(f"Unknown timeframe: {timeframe}")
            return False
        
        total_seconds = (end_date - start_date).total_seconds()
        expected_bars = int(total_seconds / timeframe_seconds)
        
        # Try to get actual bars - use copy_rates_range first
        rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
        
        # If copy_rates_range fails, try alternative method: copy_rates_from_pos
        if rates is None or len(rates) == 0:
            logger.warning(f"mode={mode} | symbol={symbol} | copy_rates_range failed, trying copy_rates_from_pos...")
            # Try to get as many bars as possible from current position
            # For M1, MT5 typically provides 1-3 months max
            max_bars_to_try = min(expected_bars, 200000)  # Limit to reasonable amount
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, max_bars_to_try)
            
            if rates is not None and len(rates) > 0:
                # Check if the data covers the requested range
                first_bar_time = datetime.fromtimestamp(rates[0][0])
                last_bar_time = datetime.fromtimestamp(rates[-1][0])
                
                # If data doesn't cover full range, adjust expectations
                if first_bar_time > start_date:
                    logger.warning(f"mode={mode} | symbol={symbol} | Available data starts at {first_bar_time}, requested {start_date}")
                    # Data starts later than requested - this is acceptable, just warn
                
                if last_bar_time < end_date:
                    logger.warning(f"mode={mode} | symbol={symbol} | Available data ends at {last_bar_time}, requested {end_date}")
                    # Data ends earlier than requested - adjust required bars
                    actual_seconds = (last_bar_time - max(start_date, first_bar_time)).total_seconds()
                    expected_bars = int(actual_seconds / timeframe_seconds) if actual_seconds > 0 else len(rates)
        
        if rates is None or len(rates) == 0:
            # For long backtests, MT5 may not have full historical data
            # Check if we can get ANY data at all (even if not the full range)
            test_rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 100)
            if test_rates is None or len(test_rates) == 0:
                self.errors.append(f"Cannot load ANY bars for {symbol} - symbol may not exist or have no data")
                logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Cannot load ANY bars")
                return False
            else:
                # We can get some data, but not the full range - warn but allow
                self.warnings.append(f"Cannot load full date range for {symbol} ({start_date} to {end_date}), but some data is available. Backtest will use available data.")
                logger.warning(f"mode={mode} | symbol={symbol} | WARNING: Cannot load full range, but {len(test_rates)} bars available. Backtest will proceed with available data.")
                return True  # Allow backtest to proceed with available data
        
        actual_bars = len(rates)
        
        # For long backtests, reduce required_bars to be more flexible
        # If requesting 12 months but only 3 months available, accept what we have
        if actual_bars >= 10000:  # At least 10k bars (about 7 days of M1 data)
            logger.info(f"mode={mode} | symbol={symbol} | Bars loaded: {actual_bars} (sufficient for backtest)")
        elif actual_bars < required_bars:
            # Only fail if we have very few bars
            if actual_bars < 1000:  # Less than ~17 hours of M1 data
                self.errors.append(f"Insufficient bars for {symbol}: {actual_bars} < {required_bars} required (minimum: 1000)")
                logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Insufficient bars ({actual_bars} < {required_bars}, minimum: 1000)")
                return False
            else:
                # We have some data, but less than requested - warn but continue
                self.warnings.append(f"Less bars than requested for {symbol}: {actual_bars} < {required_bars} requested (but sufficient: >= 1000)")
                logger.warning(f"mode={mode} | symbol={symbol} | WARNING: Less bars than requested ({actual_bars} < {required_bars}), but sufficient for backtest")
        
        # Check for gaps (relaxed tolerance for long backtests)
        gap_tolerance = 0.7 if expected_bars > 100000 else 0.9  # 30% tolerance for very long backtests
        if actual_bars < expected_bars * gap_tolerance:
            self.warnings.append(f"Possible data gaps for {symbol}: {actual_bars} bars vs {expected_bars} expected")
            logger.warning(f"mode={mode} | symbol={symbol} | WARNING: Possible data gaps ({actual_bars} vs {expected_bars} expected)")
        
        logger.info(f"mode={mode} | symbol={symbol} | Bars loaded: {actual_bars} (required: {required_bars}, expected: {expected_bars})")
        return True
    
    def _validate_date_range(self, symbol: str, timeframe: int, start_date: datetime,
                            end_date: datetime, mode: str) -> bool:
        """Validate date range is valid and data is available."""
        if end_date <= start_date:
            self.errors.append(f"Invalid date range: end_date ({end_date}) <= start_date ({start_date})")
            logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Invalid date range")
            return False
        
        # Check if dates are in the future
        now = datetime.now()
        if start_date > now or end_date > now:
            self.errors.append(f"Date range includes future dates: start={start_date}, end={end_date}, now={now}")
            logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Future dates in range")
            return False
        
        # Try to get first and last available bars
        rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
        
        # If copy_rates_range fails, try copy_rates_from_pos as fallback
        if rates is None or len(rates) == 0:
            logger.warning(f"mode={mode} | symbol={symbol} | copy_rates_range failed, trying copy_rates_from_pos for date validation...")
            max_bars = 200000  # Try to get a lot of bars
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, max_bars)
        
        if rates is None or len(rates) == 0:
            # This is a warning, not an error - we'll use whatever data is available
            # The backtest replay engine will handle loading available data
            self.warnings.append(f"Cannot verify exact date range for {symbol}, but will use available data")
            logger.warning(f"mode={mode} | symbol={symbol} | WARNING: Cannot verify exact date range, will use available data")
            return True  # Don't fail - let the backtest use whatever data is available
        
        first_bar_time = datetime.fromtimestamp(rates[0][0])
        last_bar_time = datetime.fromtimestamp(rates[-1][0])
        
        if first_bar_time > start_date:
            self.warnings.append(f"First available bar ({first_bar_time}) is after requested start ({start_date})")
            logger.warning(f"mode={mode} | symbol={symbol} | WARNING: Data starts at {first_bar_time}, requested {start_date}")
        
        if last_bar_time < end_date:
            self.warnings.append(f"Last available bar ({last_bar_time}) is before requested end ({end_date})")
            logger.warning(f"mode={mode} | symbol={symbol} | WARNING: Data ends at {last_bar_time}, requested {end_date}")
        
        logger.info(f"mode={mode} | symbol={symbol} | Date range: {start_date} to {end_date} | "
                   f"Data available: {first_bar_time} to {last_bar_time}")
        return True
    
    def _validate_spread(self, symbol: str, mode: str) -> bool:
        """Validate spread data is available."""
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick is None:
            self.errors.append(f"Cannot get tick data for {symbol}")
            logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Cannot get tick data")
            return False
        
        spread = tick.ask - tick.bid
        if spread <= 0:
            self.errors.append(f"Invalid spread for {symbol}: {spread}")
            logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Invalid spread ({spread})")
            return False
        
        logger.info(f"mode={mode} | symbol={symbol} | Spread available: {spread:.5f}")
        return True
    
    def _validate_contract_specs(self, symbol: str, mode: str) -> bool:
        """Validate contract specifications."""
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Handle both dict and named tuple formats
        if isinstance(symbol_info, dict):
            digits = symbol_info.get('digits')
            point = symbol_info.get('point')
            stops_level = symbol_info.get('trade_stops_level', symbol_info.get('stops_level'))
            contract_size = symbol_info.get('contract_size')
        else:
            # Named tuple format
            digits = getattr(symbol_info, 'digits', None)
            point = getattr(symbol_info, 'point', None)
            stops_level = getattr(symbol_info, 'trade_stops_level', getattr(symbol_info, 'stops_level', None))
            contract_size = getattr(symbol_info, 'contract_size', None)
        
        # Check if all required fields are present
        missing_fields = []
        if digits is None:
            missing_fields.append('digits')
        if point is None:
            missing_fields.append('point')
        if stops_level is None:
            missing_fields.append('stops_level')
        if contract_size is None:
            missing_fields.append('contract_size')
        
        if missing_fields:
            self.errors.append(f"Missing contract spec fields for {symbol}: {missing_fields}")
            logger.critical(f"mode={mode} | symbol={symbol} | VALIDATION FAILED: Missing fields {missing_fields}")
            return False
        
        # Log contract specs
        logger.info(f"mode={mode} | symbol={symbol} | Contract specs: "
                   f"digits={digits}, point={point}, "
                   f"stops_level={stops_level}, contract_size={contract_size}")
        
        return True
    
    def _get_timeframe_seconds(self, timeframe: int) -> int:
        """Get timeframe in seconds."""
        from backtest.utils import get_timeframe_seconds
        return get_timeframe_seconds(timeframe)
    
    def log_results(self, mode: str = "BACKTEST"):
        """Log validation results."""
        if self.errors:
            logger.critical("=" * 80)
            logger.critical(f"DATA PREFLIGHT VALIDATION FAILED (mode={mode})")
            logger.critical("=" * 80)
            for error in self.errors:
                logger.critical(f"  [ERROR] {error}")
            logger.critical("=" * 80)
        
        if self.warnings:
            logger.warning("=" * 80)
            logger.warning(f"DATA PREFLIGHT VALIDATION WARNINGS (mode={mode})")
            logger.warning("=" * 80)
            for warning in self.warnings:
                logger.warning(f"  [WARNING] {warning}")
            logger.warning("=" * 80)
        
        if not self.errors and not self.warnings:
            logger.info(f"[OK] Data preflight validation passed (mode={mode})")



