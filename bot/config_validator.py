"""
Configuration Validator
Validates config.json on startup and warns about issues.
"""

import logging
import json
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class ConfigValidator:
    """Validates trading bot configuration."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.errors = []
        self.warnings = []
    
    def validate(self) -> Tuple[bool, List[str], List[str]]:
        """
        Validate configuration.
        
        Returns:
            (is_valid, errors, warnings)
        """
        self.errors = []
        self.warnings = []
        
        # Validate required sections
        self._validate_mt5_config()
        self._validate_risk_config()
        self._validate_trading_config()
        self._validate_news_config()
        self._validate_pairs_config()
        self._validate_halal_config()
        
        is_valid = len(self.errors) == 0
        
        return is_valid, self.errors, self.warnings
    
    def _validate_mt5_config(self):
        """Validate MT5 configuration."""
        mt5_config = self.config.get('mt5', {})
        
        if not mt5_config:
            self.errors.append("Missing 'mt5' configuration section")
            return
        
        required_fields = ['account', 'password', 'server']
        for field in required_fields:
            if not mt5_config.get(field):
                self.errors.append(f"MT5 config missing required field: {field}")
        
        # Validate timeout
        timeout = mt5_config.get('timeout', 60000)
        if not isinstance(timeout, int) or timeout < 1000:
            self.warnings.append(f"MT5 timeout ({timeout}ms) seems low, consider >= 1000ms")
    
    def _validate_risk_config(self):
        """Validate risk management configuration."""
        risk_config = self.config.get('risk', {})
        
        if not risk_config:
            self.errors.append("Missing 'risk' configuration section")
            return
        
        # Validate risk per trade
        max_risk = risk_config.get('max_risk_per_trade_usd', 2.0)
        if not isinstance(max_risk, (int, float)) or max_risk <= 0:
            self.errors.append("risk.max_risk_per_trade_usd must be a positive number")
        elif max_risk > 100:
            self.warnings.append(f"Risk per trade (${max_risk}) is very high. Ensure this is intentional.")
        
        # Validate stop loss
        min_sl = risk_config.get('min_stop_loss_pips', 10)
        if not isinstance(min_sl, (int, float)) or min_sl <= 0:
            self.errors.append("risk.min_stop_loss_pips must be a positive number")
        elif min_sl < 5:
            self.warnings.append(f"Minimum stop loss ({min_sl} pips) is very tight. May cause frequent stop-outs.")
        
        # Validate trailing stop
        trailing = risk_config.get('trailing_stop_increment_usd', 0.1)
        if not isinstance(trailing, (int, float)) or trailing <= 0:
            self.errors.append("risk.trailing_stop_increment_usd must be a positive number")
        
        # Validate max_open_trades (allow up to 6 for medium-frequency trading)
        max_trades = risk_config.get('max_open_trades', 1)
        if not isinstance(max_trades, int) or max_trades < 1 or max_trades > 10:
            self.errors.append("risk.max_open_trades must be an integer between 1 and 10")
        elif max_trades > 6:
            self.warnings.append(f"Max open trades ({max_trades}) is high. Ensure sufficient capital and risk management.")
        
        # Validate staged open settings
        if risk_config.get('staged_open_enabled', False):
            window = risk_config.get('staged_open_window_seconds', 60)
            if not isinstance(window, (int, float)) or window <= 0:
                self.errors.append("risk.staged_open_window_seconds must be a positive number")
            
            quality_threshold = risk_config.get('staged_quality_threshold', 50.0)
            if not isinstance(quality_threshold, (int, float)) or quality_threshold < 0 or quality_threshold > 100:
                self.warnings.append("risk.staged_quality_threshold should be between 0 and 100")
        
        # Validate fast trailing settings
        fast_threshold = risk_config.get('fast_trailing_threshold_usd', 0.10)
        if not isinstance(fast_threshold, (int, float)) or fast_threshold <= 0:
            self.errors.append("risk.fast_trailing_threshold_usd must be a positive number")
        
        fast_interval = risk_config.get('fast_trailing_interval_ms', 300)
        if not isinstance(fast_interval, (int, float)) or fast_interval < 100:
            self.warnings.append("risk.fast_trailing_interval_ms should be at least 100ms")
        
        # Validate elastic trailing settings
        elastic = risk_config.get('elastic_trailing', {})
        if elastic.get('enabled', False):
            pullback = elastic.get('pullback_tolerance_pct', 0.40)
            if not isinstance(pullback, (int, float)) or pullback < 0 or pullback > 1:
                self.errors.append("risk.elastic_trailing.pullback_tolerance_pct must be between 0 and 1")
            
            min_lock = elastic.get('min_lock_increment_usd', 0.10)
            if not isinstance(min_lock, (int, float)) or min_lock <= 0:
                self.errors.append("risk.elastic_trailing.min_lock_increment_usd must be a positive number")
    
    def _validate_trading_config(self):
        """Validate trading configuration."""
        trading_config = self.config.get('trading', {})
        
        if not trading_config:
            self.errors.append("Missing 'trading' configuration section")
            return
        
        # Validate timeframe
        timeframe = trading_config.get('timeframe', 'M1')
        valid_timeframes = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
        if timeframe not in valid_timeframes:
            self.warnings.append(f"Timeframe '{timeframe}' may not be standard. Valid: {valid_timeframes}")
        
        # Validate cycle interval (medium-frequency)
        cycle_interval = trading_config.get('cycle_interval_seconds', 30)
        if not isinstance(cycle_interval, (int, float)) or cycle_interval < 10:
            self.warnings.append(f"Cycle interval ({cycle_interval}s) is very short. May cause high CPU usage.")
        
        # Validate randomness factor
        randomness = trading_config.get('randomness_factor', 0.05)
        if not isinstance(randomness, (int, float)) or randomness < 0 or randomness > 1:
            self.errors.append("trading.randomness_factor must be between 0 and 1")
        
        # Validate SMA periods
        sma_fast = trading_config.get('sma_fast', 20)
        sma_slow = trading_config.get('sma_slow', 50)
        if not isinstance(sma_fast, int) or sma_fast <= 0:
            self.errors.append("trading.sma_fast must be a positive integer")
        if not isinstance(sma_slow, int) or sma_slow <= 0:
            self.errors.append("trading.sma_slow must be a positive integer")
        if sma_fast >= sma_slow:
            self.errors.append(f"trading.sma_fast ({sma_fast}) must be less than sma_slow ({sma_slow})")
        
        # Validate RSI
        rsi_period = trading_config.get('rsi_period', 14)
        if not isinstance(rsi_period, int) or rsi_period <= 0:
            self.errors.append("trading.rsi_period must be a positive integer")
        
        rsi_overbought = trading_config.get('rsi_overbought', 70)
        rsi_oversold = trading_config.get('rsi_oversold', 30)
        if rsi_overbought <= rsi_oversold:
            self.errors.append(f"RSI overbought ({rsi_overbought}) must be greater than oversold ({rsi_oversold})")
        
        # Validate ATR
        atr_period = trading_config.get('atr_period', 14)
        if not isinstance(atr_period, int) or atr_period <= 0:
            self.warnings.append("trading.atr_period should be a positive integer")
        
        atr_multiplier = trading_config.get('atr_multiplier', 2.0)
        if not isinstance(atr_multiplier, (int, float)) or atr_multiplier <= 0:
            self.warnings.append("trading.atr_multiplier should be a positive number")
    
    def _validate_news_config(self):
        """Validate news filter configuration."""
        news_config = self.config.get('news', {})
        
        if not news_config:
            self.warnings.append("Missing 'news' configuration section")
            return
        
        # Validate block window
        block_window = news_config.get('block_window_minutes', 20)
        if not isinstance(block_window, (int, float)) or block_window < 0:
            self.errors.append("news.block_window_minutes must be a non-negative number")
        
        # Check API key if using Financial Modeling Prep
        api_provider = news_config.get('api_provider', 'financialmodelingprep')
        api_key = news_config.get('api_key', '')
        if api_provider == 'financialmodelingprep' and not api_key:
            self.warnings.append("Financial Modeling Prep API key not provided. News filter may not work. Consider using MT5 calendar fallback.")
    
    def _validate_pairs_config(self):
        """Validate pairs/symbols configuration."""
        pairs_config = self.config.get('pairs', {})
        
        if not pairs_config:
            self.warnings.append("Missing 'pairs' configuration section")
            return
        
        # Validate spread limits
        max_spread = pairs_config.get('max_spread_points', 15)
        if not isinstance(max_spread, (int, float)) or max_spread <= 0:
            self.errors.append("pairs.max_spread_points must be a positive number")
        elif max_spread > 50:
            self.warnings.append(f"Max spread ({max_spread} points) is very high. May result in high trading costs.")
        
        # Check if auto-discover is enabled
        auto_discover = pairs_config.get('auto_discover_symbols', True)
        allowed_symbols = pairs_config.get('allowed_symbols', [])
        if not auto_discover and not allowed_symbols:
            self.warnings.append("auto_discover_symbols is false but allowed_symbols is empty. Bot may not find any symbols to trade.")
    
    def _validate_halal_config(self):
        """Validate halal compliance configuration."""
        halal_config = self.config.get('halal', {})
        
        if not halal_config:
            self.warnings.append("Missing 'halal' configuration section")
            return
        
        # Validate max hold hours
        max_hold = halal_config.get('max_hold_hours', 24)
        if not isinstance(max_hold, (int, float)) or max_hold <= 0:
            self.errors.append("halal.max_hold_hours must be a positive number")
        elif max_hold > 24:
            self.warnings.append(f"Max hold hours ({max_hold}) exceeds 24 hours. May violate no-overnight rule.")
    
    def log_results(self):
        """Log validation results."""
        if self.errors:
            logger.error("=" * 60)
            logger.error("CONFIGURATION ERRORS (must fix):")
            for error in self.errors:
                logger.error(f"  ❌ {error}")
            logger.error("=" * 60)
        
        if self.warnings:
            logger.warning("=" * 60)
            logger.warning("CONFIGURATION WARNINGS:")
            for warning in self.warnings:
                logger.warning(f"  ⚠️  {warning}")
            logger.warning("=" * 60)
        
        if not self.errors and not self.warnings:
            logger.info("✅ Configuration validation passed with no issues")

