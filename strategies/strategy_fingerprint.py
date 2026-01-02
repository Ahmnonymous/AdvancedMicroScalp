"""
Strategy Fingerprinting System

Assigns a unique strategy_id to every trade opportunity, even when filters reject it.
This enables tracking which strategies are being evaluated vs executed.

Naming Convention:
    strategy_id = "{direction}_{entry_condition_cluster}_{filter_stack_hash}"

Example:
    - "LONG_SMA20x50_RSI30-50_Q60-80_FV1C1S1"
    - "SHORT_SMA20x50_RSI30-50_Q60-80_FV1C1S1"

Where:
    - direction: LONG | SHORT
    - entry_condition_cluster: Primary entry logic (e.g., SMA20x50)
    - filter_stack_hash: Abbreviated filter configuration
"""

import hashlib
from typing import Dict, Any, Optional, List
from datetime import datetime
from collections import defaultdict


class StrategyFingerprint:
    """
    Generates strategy fingerprints for trade opportunities.
    
    A strategy fingerprint uniquely identifies a trading strategy configuration
    based on entry conditions and filter stack, regardless of execution outcome.
    """
    
    def __init__(self):
        self._fingerprint_cache = {}  # Cache for performance
        self._strategy_registry = defaultdict(int)  # Track strategy occurrences
    
    def generate_strategy_id(
        self,
        opportunity: Dict[str, Any],
        filter_results: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate a unique strategy_id for an opportunity.
        
        Args:
            opportunity: Trade opportunity dictionary
            filter_results: Optional filter evaluation results
            
        Returns:
            strategy_id: Unique strategy identifier
        """
        # Extract core components
        direction = opportunity.get('signal', 'NONE')
        if direction == 'NONE':
            return "NONE_NO_SIGNAL"
        
        # Entry condition cluster (primary logic)
        entry_cluster = self._extract_entry_cluster(opportunity)
        
        # Filter stack hash (abbreviated)
        filter_hash = self._extract_filter_hash(opportunity, filter_results)
        
        # Construct strategy_id
        strategy_id = f"{direction}_{entry_cluster}_{filter_hash}"
        
        # Cache and track
        cache_key = self._get_cache_key(opportunity, filter_results)
        if cache_key not in self._fingerprint_cache:
            self._fingerprint_cache[cache_key] = strategy_id
            self._strategy_registry[strategy_id] += 1
        
        return strategy_id
    
    def _extract_entry_cluster(self, opportunity: Dict[str, Any]) -> str:
        """
        Extract entry condition cluster identifier.
        
        Current system uses SMA20 vs SMA50, so cluster is "SMA20x50".
        Future: Could be "SMA20x50_RSI", "MACD_CROSS", etc.
        """
        trend_signal = opportunity.get('trend_signal', {})
        sma_fast = trend_signal.get('sma_fast', 0)
        sma_slow = trend_signal.get('sma_slow', 0)
        
        # Default SMA periods (from config or hardcoded)
        if sma_fast > 0 and sma_slow > 0:
            # Use actual periods if available
            fast_period = int(sma_fast) if sma_fast == int(sma_fast) else 20
            slow_period = int(sma_slow) if sma_slow == int(sma_slow) else 50
            return f"SMA{fast_period}x{slow_period}"
        
        # Fallback to default
        return "SMA20x50"
    
    def _extract_filter_hash(self, opportunity: Dict[str, Any], filter_results: Optional[Dict[str, Any]]) -> str:
        """
        Extract abbreviated filter stack hash.
        
        Format: "RSI{min}-{max}_Q{min}-{max}_F{vol}{candle}{spread}{trend}{timing}"
        
        Where:
            RSI: RSI entry range (e.g., RSI30-50)
            Q: Quality score range (e.g., Q60-80)
            F: Filter flags (V=volatility, C=candle, S=spread, T=trend, I=timing)
        """
        parts = []
        
        # RSI filter range
        rsi_min = opportunity.get('rsi_entry_range_min', 30)
        rsi_max = opportunity.get('rsi_entry_range_max', 50)
        parts.append(f"RSI{int(rsi_min)}-{int(rsi_max)}")
        
        # Quality score range
        quality_score = opportunity.get('quality_score', 0)
        min_quality = opportunity.get('min_quality_score', 50)
        # Map quality score to bucket
        quality_bucket = self._bucket_quality_score(quality_score, min_quality)
        parts.append(f"Q{quality_bucket}")
        
        # Filter flags (abbreviated)
        filter_flags = []
        
        # Volatility floor (V)
        volatility_ok = filter_results.get('volatility_floor', {}).get('passed', True) if filter_results else True
        filter_flags.append('V1' if volatility_ok else 'V0')
        
        # Candle quality (C)
        candle_ok = filter_results.get('candle_quality', {}).get('passed', True) if filter_results else True
        filter_flags.append('C1' if candle_ok else 'C0')
        
        # Spread sanity (S)
        spread_ok = filter_results.get('spread_sanity', {}).get('passed', True) if filter_results else True
        filter_flags.append('S1' if spread_ok else 'S0')
        
        # Trend gate (T)
        trend_ok = filter_results.get('trend_gate', {}).get('passed', True) if filter_results else True
        filter_flags.append('T1' if trend_ok else 'T0')
        
        # Timing guards (I)
        timing_ok = filter_results.get('timing_guards', {}).get('passed', True) if filter_results else True
        filter_flags.append('I1' if timing_ok else 'I0')
        
        parts.append(''.join(filter_flags))
        
        return '_'.join(parts)
    
    def _bucket_quality_score(self, quality_score: float, min_quality: float) -> str:
        """Bucket quality score into range identifier."""
        if quality_score >= min_quality + 20:
            return f"{int(min_quality)}+20"
        elif quality_score >= min_quality:
            return f"{int(min_quality)}+"
        elif quality_score >= min_quality - 10:
            return f"{int(min_quality)}-10"
        else:
            return f"{int(min_quality)}-20"
    
    def _get_cache_key(self, opportunity: Dict[str, Any], filter_results: Optional[Dict[str, Any]]) -> str:
        """Generate cache key for fingerprint lookup."""
        key_parts = [
            opportunity.get('signal', 'NONE'),
            opportunity.get('rsi_entry_range_min', 30),
            opportunity.get('rsi_entry_range_max', 50),
            opportunity.get('min_quality_score', 50),
        ]
        if filter_results:
            key_parts.append(str(sorted(filter_results.items())))
        return str(key_parts)
    
    def get_strategy_metadata(self, strategy_id: str) -> Dict[str, Any]:
        """
        Get metadata for a strategy_id.
        
        Returns:
            {
                'strategy_id': str,
                'occurrences': int,
                'components': {
                    'direction': str,
                    'entry_cluster': str,
                    'filter_hash': str
                }
            }
        """
        parts = strategy_id.split('_')
        if len(parts) < 3:
            return {
                'strategy_id': strategy_id,
                'occurrences': self._strategy_registry.get(strategy_id, 0),
                'components': {}
            }
        
        return {
            'strategy_id': strategy_id,
            'occurrences': self._strategy_registry.get(strategy_id, 0),
            'components': {
                'direction': parts[0],
                'entry_cluster': parts[1],
                'filter_hash': '_'.join(parts[2:])
            }
        }
    
    def log_opportunity_fingerprint(
        self,
        symbol: str,
        opportunity: Dict[str, Any],
        filter_results: Optional[Dict[str, Any]],
        decision: str,  # 'EXECUTED' | 'REJECTED' | 'PENDING'
        rejection_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate complete fingerprint log entry.
        
        Returns log entry dictionary ready for JSON logging.
        """
        strategy_id = self.generate_strategy_id(opportunity, filter_results)
        timestamp = datetime.now().isoformat()
        
        log_entry = {
            'timestamp': timestamp,
            'symbol': symbol,
            'strategy_id': strategy_id,
            'decision': decision,  # EXECUTED | REJECTED | PENDING
            'rejection_reason': rejection_reason,
            'opportunity': {
                'signal': opportunity.get('signal', 'NONE'),
                'quality_score': opportunity.get('quality_score', 0.0),
                'trend_strength': opportunity.get('trend_strength', 0.0),
                'rsi': opportunity.get('rsi', 50.0),
                'sma_fast': opportunity.get('sma_fast', 0.0),
                'sma_slow': opportunity.get('sma_slow', 0.0),
            },
            'filters': filter_results or {},
            'market_conditions': {
                'spread_points': opportunity.get('spread_points', 0.0),
                'atr': opportunity.get('atr', 0.0),
            }
        }
        
        return log_entry

