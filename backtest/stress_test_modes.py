"""
Stress Testing Modes
Provides various stress testing scenarios for backtesting.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime, timedelta

from utils.logger_factory import get_logger

logger = get_logger("backtest_stress", "logs/backtest/stress.log")


class StressTestMode:
    """Base class for stress test modes."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply stress test modifications to data."""
        raise NotImplementedError


class HighVolatilityMode(StressTestMode):
    """Increase volatility by amplifying price movements."""
    
    def __init__(self, volatility_multiplier: float = 2.0):
        super().__init__(
            "High Volatility",
            f"Increase volatility by {volatility_multiplier}x"
        )
        self.volatility_multiplier = volatility_multiplier
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply high volatility stress."""
        df = data.copy()
        
        # Calculate price changes
        df['price_change'] = df['close'].diff()
        
        # Amplify changes
        df['price_change'] *= self.volatility_multiplier
        
        # Reconstruct prices
        df['close'] = df['open'] + df['price_change'].cumsum()
        df['high'] = df[['open', 'high', 'close']].max(axis=1)
        df['low'] = df[['open', 'low', 'close']].min(axis=1)
        
        return df


class ExtremeSpreadExpansionMode(StressTestMode):
    """Simulate extreme spread widening."""
    
    def __init__(self, max_spread_multiplier: float = 5.0, probability: float = 0.1):
        super().__init__(
            "Extreme Spread Expansion",
            f"Randomly expand spreads up to {max_spread_multiplier}x with {probability*100}% probability"
        )
        self.max_spread_multiplier = max_spread_multiplier
        self.probability = probability
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply spread expansion stress."""
        df = data.copy()
        
        # Randomly expand spreads
        random_mask = np.random.random(len(df)) < self.probability
        spread_multipliers = np.ones(len(df))
        spread_multipliers[random_mask] = np.random.uniform(
            2.0, self.max_spread_multiplier, size=random_mask.sum()
        )
        
        # Apply to spread column if exists
        if 'spread' in df.columns:
            df['spread'] *= spread_multipliers
        
        return df


class FastTrendReversalMode(StressTestMode):
    """Simulate rapid trend reversals."""
    
    def __init__(self, reversal_frequency: int = 10):
        super().__init__(
            "Fast Trend Reversals",
            f"Reverse trend every {reversal_frequency} bars"
        )
        self.reversal_frequency = reversal_frequency
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply trend reversal stress."""
        df = data.copy()
        
        # Reverse price direction periodically
        for i in range(self.reversal_frequency, len(df), self.reversal_frequency):
            # Invert price changes for next period
            if i < len(df):
                period_end = min(i + self.reversal_frequency, len(df))
                period_data = df.iloc[i:period_end].copy()
                
                # Reverse direction
                price_changes = period_data['close'].diff()
                price_changes = -price_changes  # Reverse
                
                # Reconstruct
                new_closes = period_data['open'].iloc[0] + price_changes.cumsum()
                df.loc[df.index[i:period_end], 'close'] = new_closes.values
                df.loc[df.index[i:period_end], 'high'] = df.loc[df.index[i:period_end], ['open', 'high', 'close']].max(axis=1)
                df.loc[df.index[i:period_end], 'low'] = df.loc[df.index[i:period_end], ['open', 'low', 'close']].min(axis=1)
        
        return df


class TickGapsMode(StressTestMode):
    """Simulate missing ticks/gaps in data."""
    
    def __init__(self, gap_probability: float = 0.05, max_gap_size: int = 5):
        super().__init__(
            "Tick Gaps / Missing Ticks",
            f"Randomly remove {gap_probability*100}% of ticks, max gap size: {max_gap_size}"
        )
        self.gap_probability = gap_probability
        self.max_gap_size = max_gap_size
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply tick gaps stress."""
        df = data.copy()
        
        # Randomly remove rows
        gap_mask = np.random.random(len(df)) < self.gap_probability
        df = df[~gap_mask].reset_index(drop=True)
        
        return df


class SlippageSpikesMode(StressTestMode):
    """Simulate slippage spikes."""
    
    def __init__(self, spike_probability: float = 0.1, max_slippage_pips: float = 10.0):
        super().__init__(
            "Slippage Spikes",
            f"Random slippage spikes up to {max_slippage_pips} pips with {spike_probability*100}% probability"
        )
        self.spike_probability = spike_probability
        self.max_slippage_pips = max_slippage_pips
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply slippage spikes stress."""
        # This is handled in the execution simulator, not data modification
        return data


class RandomCandleAnomaliesMode(StressTestMode):
    """Simulate random candle anomalies (wicks, spikes, etc.)."""
    
    def __init__(self, anomaly_probability: float = 0.05):
        super().__init__(
            "Random Candle Anomalies",
            f"Add random anomalies to {anomaly_probability*100}% of candles"
        )
        self.anomaly_probability = anomaly_probability
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply candle anomalies stress."""
        df = data.copy()
        
        # Randomly add wicks/spikes
        anomaly_mask = np.random.random(len(df)) < self.anomaly_probability
        
        for idx in df.index[anomaly_mask]:
            # Add random wick (extend high or low)
            if np.random.random() > 0.5:
                # Upper wick
                wick_size = df.loc[idx, 'close'] * 0.01  # 1% wick
                df.loc[idx, 'high'] = df.loc[idx, 'close'] + wick_size
            else:
                # Lower wick
                wick_size = df.loc[idx, 'close'] * 0.01
                df.loc[idx, 'low'] = df.loc[idx, 'close'] - wick_size
        
        return df


class MarketDeadMode(StressTestMode):
    """Simulate market dead periods (no ticks)."""
    
    def __init__(self, dead_periods: List[tuple] = None):
        """
        Initialize market dead mode.
        
        Args:
            dead_periods: List of (start_idx, end_idx) tuples for dead periods
        """
        super().__init__(
            "Market Dead / No Ticks",
            "Simulate periods with no market data"
        )
        self.dead_periods = dead_periods or []
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply market dead stress."""
        df = data.copy()
        
        # Remove data in dead periods
        if self.dead_periods:
            mask = pd.Series([True] * len(df))
            for start_idx, end_idx in self.dead_periods:
                if start_idx < len(df) and end_idx < len(df):
                    mask.iloc[start_idx:end_idx] = False
            df = df[mask].reset_index(drop=True)
        else:
            # Default: remove middle 10% of data
            start_idx = len(df) // 3
            end_idx = 2 * len(df) // 3
            df = pd.concat([df.iloc[:start_idx], df.iloc[end_idx:]]).reset_index(drop=True)
        
        return df


class CircuitBreakerStressMode(StressTestMode):
    """Simulate circuit breaker scenarios (rapid failures)."""
    
    def __init__(self, failure_rate: float = 0.3):
        super().__init__(
            "Circuit Breaker Stress",
            f"Simulate {failure_rate*100}% failure rate to trigger circuit breakers"
        )
        self.failure_rate = failure_rate
    
    def apply(self, data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Apply circuit breaker stress."""
        # This is handled in the execution simulator
        return data


class StressTestManager:
    """Manages stress test modes."""
    
    def __init__(self):
        self.modes = {
            'high_volatility': HighVolatilityMode(),
            'extreme_spread': ExtremeSpreadExpansionMode(),
            'fast_reversals': FastTrendReversalMode(),
            'tick_gaps': TickGapsMode(),
            'slippage_spikes': SlippageSpikesMode(),
            'candle_anomalies': RandomCandleAnomaliesMode(),
            'market_dead': MarketDeadMode(),
            'circuit_breaker': CircuitBreakerStressMode()
        }
    
    def get_mode(self, name: str) -> Optional[StressTestMode]:
        """Get stress test mode by name."""
        return self.modes.get(name)
    
    def list_modes(self) -> List[str]:
        """List all available stress test modes."""
        return list(self.modes.keys())
    
    def apply_stress(self, data: pd.DataFrame, symbol: str, mode_names: List[str]) -> pd.DataFrame:
        """Apply multiple stress test modes to data."""
        result = data.copy()
        
        for mode_name in mode_names:
            mode = self.get_mode(mode_name)
            if mode:
                logger.info(f"Applying stress test: {mode.name}")
                result = mode.apply(result, symbol)
            else:
                logger.warning(f"Unknown stress test mode: {mode_name}")
        
        return result

