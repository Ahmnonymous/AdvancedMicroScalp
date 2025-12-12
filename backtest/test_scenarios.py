"""
Automated Test Scenarios for Backtesting
Pre-defined scenarios to test bot behavior.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import json

from utils.logger_factory import get_logger

logger = get_logger("backtest_scenarios", "logs/backtest/scenarios.log")


class TestScenario:
    """Base class for test scenarios."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration for this scenario.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        raise NotImplementedError
    
    def get_expected_results(self) -> Dict[str, Any]:
        """Get expected results for validation."""
        raise NotImplementedError


class TrendingUpScenario(TestScenario):
    """1 hour trending up scenario."""
    
    def __init__(self):
        super().__init__(
            "Trending Up (1h)",
            "Strong uptrend for 1 hour - bot should enter LONG and lock profits"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use real historical data that trends up (use past dates for reliable data)
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': [],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'min_trades': 1,
            'expected_direction': 'LONG',
            'min_profit_locks': 1,
            'max_drawdown_pct': 5.0
        }


class TrendingDownScenario(TestScenario):
    """1 hour trending down scenario."""
    
    def __init__(self):
        super().__init__(
            "Trending Down (1h)",
            "Strong downtrend for 1 hour - bot should enter SHORT and lock profits"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use past dates for reliable historical data
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': [],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'min_trades': 1,
            'expected_direction': 'SHORT',
            'min_profit_locks': 1,
            'max_drawdown_pct': 5.0
        }


class SidewaysChopScenario(TestScenario):
    """30 minutes sideways/choppy market."""
    
    def __init__(self):
        super().__init__(
            "Sideways Chop (30m)",
            "Choppy sideways market - bot should filter out or exit quickly"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use past dates for reliable historical data
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': [],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'max_trades': 3,  # Should filter most opportunities
            'max_drawdown_pct': 3.0
        }


class FlashCrashScenario(TestScenario):
    """Flash crash scenario."""
    
    def __init__(self):
        super().__init__(
            "Flash Crash",
            "Rapid price drop - test SL enforcement and circuit breakers"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use past dates for reliable historical data
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': ['high_volatility', 'fast_reversals'],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'max_loss_per_trade': -2.5,  # Should not exceed -$2.50
            'sl_update_success_rate_min': 90.0
        }


class SpikeCandleScenario(TestScenario):
    """Spike candle (wick-heavy) scenario."""
    
    def __init__(self):
        super().__init__(
            "Spike Candle",
            "Candles with large wicks - test SL/TP hit detection"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use past dates for reliable historical data
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': ['candle_anomalies'],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'sl_hit_detection_rate': 95.0,  # Should detect SL hits correctly
            'tp_hit_detection_rate': 95.0
        }


class SpreadWideningScenario(TestScenario):
    """Spread widening event."""
    
    def __init__(self):
        super().__init__(
            "Spread Widening",
            "Extreme spread expansion - test execution and slippage handling"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use past dates for reliable historical data
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': ['extreme_spread'],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'max_slippage_pips': 5.0,
            'execution_success_rate': 80.0
        }


class NewsVolatilityScenario(TestScenario):
    """News candle volatility scenario."""
    
    def __init__(self):
        super().__init__(
            "News Volatility",
            "High volatility around news events - test filters and risk management"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use past dates for reliable historical data
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': ['high_volatility'],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'news_filter_effectiveness': True,  # Should filter trades during news
            'max_drawdown_pct': 5.0
        }


class MarketDeadScenario(TestScenario):
    """Market dead/no ticks scenario."""
    
    def __init__(self):
        super().__init__(
            "Market Dead",
            "Periods with no ticks - test handling of missing data"
        )
    
    def get_config(self, months: int = 1) -> Dict[str, Any]:
        """
        Get backtest configuration.
        
        Args:
            months: Number of months of historical data (default 1, can be extended to 12-24)
        """
        # Use past dates for reliable historical data
        end_date = datetime.now() - timedelta(days=7)  # 1 week ago
        start_date = end_date - timedelta(days=months * 30)  # Extendable period
        
        return {
            'symbols': ['EURUSDm'],  # Broker-specific symbol name
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': ['market_dead'],
            'initial_balance': 10000.0
        }
    
    def get_expected_results(self) -> Dict[str, Any]:
        return {
            'no_crashes': True,
            'graceful_handling': True
        }


class TestScenarioManager:
    """Manages test scenarios."""
    
    def __init__(self):
        self.scenarios = {
            'trending_up': TrendingUpScenario(),
            'trending_down': TrendingDownScenario(),
            'sideways_chop': SidewaysChopScenario(),
            'flash_crash': FlashCrashScenario(),
            'spike_candle': SpikeCandleScenario(),
            'spread_widening': SpreadWideningScenario(),
            'news_volatility': NewsVolatilityScenario(),
            'market_dead': MarketDeadScenario()
        }
    
    def get_scenario(self, name: str) -> Optional[TestScenario]:
        """Get scenario by name."""
        return self.scenarios.get(name)
    
    def list_scenarios(self) -> List[str]:
        """List all available scenarios."""
        return list(self.scenarios.keys())
    
    def run_scenario(self, name: str, config_base: Dict[str, Any], months: int = 1) -> Dict[str, Any]:
        """
        Run a test scenario.
        
        Args:
            name: Scenario name
            config_base: Base configuration
            months: Number of months of historical data (default 1, can be extended to 12-24)
        
        Returns:
            Dictionary with scenario config and expected results
        """
        scenario = self.get_scenario(name)
        if not scenario:
            raise ValueError(f"Unknown scenario: {name}")
        
        scenario_config = scenario.get_config(months=months)
        expected_results = scenario.get_expected_results()
        
        # Merge with base config
        merged_config = config_base.copy()
        if 'backtest' not in merged_config:
            merged_config['backtest'] = {}
        merged_config['backtest'].update(scenario_config)
        
        return {
            'scenario_name': name,
            'scenario_description': scenario.description,
            'config': merged_config,
            'expected_results': expected_results
        }

