"""
Backtest Configuration Validator
Validates backtest configuration before running.
"""

from typing import Dict, Any, List, Tuple
from datetime import datetime

from utils.logger_factory import get_logger

logger = get_logger("backtest_validator", "logs/backtest/validator.log")


class BacktestConfigValidator:
    """Validates backtest configuration."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.errors = []
        self.warnings = []
    
    def validate(self) -> Tuple[bool, List[str], List[str]]:
        """Validate backtest configuration."""
        backtest_config = self.config.get('backtest', {})
        
        # Check mode
        mode = self.config.get('mode', 'live')
        if mode != 'backtest':
            self.errors.append(f"Mode must be 'backtest' for backtesting, got '{mode}'")
        
        # Check required fields
        if 'symbols' not in backtest_config:
            self.errors.append("backtest.symbols is required")
        elif not isinstance(backtest_config['symbols'], list) or len(backtest_config['symbols']) == 0:
            self.errors.append("backtest.symbols must be a non-empty list")
        
        if 'start_date' not in backtest_config:
            self.errors.append("backtest.start_date is required")
        else:
            try:
                datetime.fromisoformat(backtest_config['start_date'])
            except Exception as e:
                self.errors.append(f"backtest.start_date is invalid: {e}")
        
        if 'end_date' not in backtest_config:
            self.errors.append("backtest.end_date is required")
        else:
            try:
                end_date = datetime.fromisoformat(backtest_config['end_date'])
                start_date = datetime.fromisoformat(backtest_config.get('start_date', '2024-01-01T00:00:00'))
                if end_date <= start_date:
                    self.errors.append("backtest.end_date must be after start_date")
            except Exception as e:
                self.errors.append(f"backtest.end_date is invalid: {e}")
        
        # Check optional fields
        timeframe = backtest_config.get('timeframe', 'M1')
        valid_timeframes = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
        if timeframe not in valid_timeframes:
            self.warnings.append(f"backtest.timeframe '{timeframe}' may not be optimal, consider: {', '.join(valid_timeframes)}")
        
        # Check stress tests
        stress_tests = backtest_config.get('stress_tests', [])
        if stress_tests:
            valid_stress_tests = [
                'high_volatility', 'extreme_spread', 'fast_reversals',
                'tick_gaps', 'slippage_spikes', 'candle_anomalies',
                'market_dead', 'circuit_breaker'
            ]
            for test in stress_tests:
                if test not in valid_stress_tests:
                    self.warnings.append(f"Unknown stress test: {test}")
        
        # Check simulation parameters
        slippage = backtest_config.get('slippage_pips', 1.0)
        if slippage < 0 or slippage > 10:
            self.warnings.append(f"backtest.slippage_pips ({slippage}) seems unusual")
        
        spread_mult = backtest_config.get('spread_multiplier', 1.0)
        if spread_mult < 0.5 or spread_mult > 5.0:
            self.warnings.append(f"backtest.spread_multiplier ({spread_mult}) seems unusual")
        
        return len(self.errors) == 0, self.errors, self.warnings
    
    def log_results(self):
        """Log validation results."""
        if self.errors:
            logger.error("Backtest configuration validation FAILED:")
            for error in self.errors:
                logger.error(f"  [ERROR] {error}")
        
        if self.warnings:
            logger.warning("Backtest configuration warnings:")
            for warning in self.warnings:
                logger.warning(f"  [WARNING]  {warning}")
        
        if not self.errors:
            logger.info("[OK] Backtest configuration validation passed")

