"""
Market Regime Awareness

Defines how strategies should be:
- Enabled / disabled
- Scaled / throttled

Based on:
- Volatility
- Spread
- Session
- Trend strength
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
from dataclasses import dataclass


class RegimeType(Enum):
    """Market regime types."""
    TRENDING = "trending"  # Strong directional movement
    RANGING = "ranging"  # Sideways, choppy
    HIGH_VOLATILITY = "high_volatility"  # Elevated volatility
    LOW_VOLATILITY = "low_volatility"  # Low volatility
    WIDE_SPREAD = "wide_spread"  # Wide spreads
    NARROW_SPREAD = "narrow_spread"  # Narrow spreads
    NEWS_EVENT = "news_event"  # News-driven
    SESSION_OPEN = "session_open"  # Session opening
    SESSION_CLOSE = "session_close"  # Session closing


@dataclass
class RegimeState:
    """Current market regime state."""
    regime_type: RegimeType
    confidence: float  # 0.0 to 1.0
    detected_at: datetime
    metrics: Dict[str, Any]  # Supporting metrics
    
    def is_active(self, min_confidence: float = 0.7) -> bool:
        """Check if regime is active with sufficient confidence."""
        return self.confidence >= min_confidence


class MarketRegimeDetector:
    """
    Detects current market regime and recommends strategy adjustments.
    """
    
    def __init__(
        self,
        volatility_threshold_high: float = 0.02,  # 2% ATR
        volatility_threshold_low: float = 0.005,  # 0.5% ATR
        spread_threshold_wide: float = 5.0,  # 5 points
        spread_threshold_narrow: float = 1.0,  # 1 point
        trend_strength_threshold: float = 0.1  # 0.1% SMA separation
    ):
        """
        Initialize regime detector.
        
        Args:
            volatility_threshold_high: High volatility threshold (ATR %)
            volatility_threshold_low: Low volatility threshold (ATR %)
            spread_threshold_wide: Wide spread threshold (points)
            spread_threshold_narrow: Narrow spread threshold (points)
            trend_strength_threshold: Strong trend threshold (SMA separation %)
        """
        self.volatility_threshold_high = volatility_threshold_high
        self.volatility_threshold_low = volatility_threshold_low
        self.spread_threshold_wide = spread_threshold_wide
        self.spread_threshold_narrow = spread_threshold_narrow
        self.trend_strength_threshold = trend_strength_threshold
        
        self.current_regimes: Dict[str, List[RegimeState]] = {}  # symbol -> regimes
    
    def detect_regime(
        self,
        symbol: str,
        market_data: Dict[str, Any]
    ) -> List[RegimeState]:
        """
        Detect current market regime for a symbol.
        
        Args:
            symbol: Trading symbol
            market_data: {
                'atr': float,
                'atr_pct': float,  # ATR as % of price
                'spread_points': float,
                'sma_separation_pct': float,
                'choppiness': float,  # 0-1, higher = more choppy
                'session_hour': int,  # Current hour (GMT)
                'news_active': bool
            }
        
        Returns:
            List of detected regimes with confidence scores
        """
        regimes = []
        
        atr_pct = market_data.get('atr_pct', 0.0)
        spread_points = market_data.get('spread_points', 0.0)
        sma_separation_pct = market_data.get('sma_separation_pct', 0.0)
        choppiness = market_data.get('choppiness', 0.5)
        session_hour = market_data.get('session_hour', 12)
        news_active = market_data.get('news_active', False)
        
        # Volatility regimes
        if atr_pct >= self.volatility_threshold_high:
            confidence = min(1.0, (atr_pct / self.volatility_threshold_high) * 0.8)
            regimes.append(RegimeState(
                regime_type=RegimeType.HIGH_VOLATILITY,
                confidence=confidence,
                detected_at=datetime.now(),
                metrics={'atr_pct': atr_pct}
            ))
        elif atr_pct <= self.volatility_threshold_low:
            confidence = min(1.0, (self.volatility_threshold_low / atr_pct) * 0.8) if atr_pct > 0 else 1.0
            regimes.append(RegimeState(
                regime_type=RegimeType.LOW_VOLATILITY,
                confidence=confidence,
                detected_at=datetime.now(),
                metrics={'atr_pct': atr_pct}
            ))
        
        # Spread regimes
        if spread_points >= self.spread_threshold_wide:
            confidence = min(1.0, (spread_points / self.spread_threshold_wide) * 0.8)
            regimes.append(RegimeState(
                regime_type=RegimeType.WIDE_SPREAD,
                confidence=confidence,
                detected_at=datetime.now(),
                metrics={'spread_points': spread_points}
            ))
        elif spread_points <= self.spread_threshold_narrow:
            confidence = min(1.0, (self.spread_threshold_narrow / spread_points) * 0.8) if spread_points > 0 else 1.0
            regimes.append(RegimeState(
                regime_type=RegimeType.NARROW_SPREAD,
                confidence=confidence,
                detected_at=datetime.now(),
                metrics={'spread_points': spread_points}
            ))
        
        # Trend regimes
        if sma_separation_pct >= self.trend_strength_threshold:
            confidence = min(1.0, (sma_separation_pct / self.trend_strength_threshold) * 0.8)
            regimes.append(RegimeState(
                regime_type=RegimeType.TRENDING,
                confidence=confidence,
                detected_at=datetime.now(),
                metrics={'sma_separation_pct': sma_separation_pct}
            ))
        elif choppiness > 0.7:
            confidence = choppiness
            regimes.append(RegimeState(
                regime_type=RegimeType.RANGING,
                confidence=confidence,
                detected_at=datetime.now(),
                metrics={'choppiness': choppiness}
            ))
        
        # News regime
        if news_active:
            regimes.append(RegimeState(
                regime_type=RegimeType.NEWS_EVENT,
                confidence=0.9,
                detected_at=datetime.now(),
                metrics={'news_active': True}
            ))
        
        # Session regimes
        if session_hour in [0, 1, 2]:  # Session opening
            regimes.append(RegimeState(
                regime_type=RegimeType.SESSION_OPEN,
                confidence=0.8,
                detected_at=datetime.now(),
                metrics={'session_hour': session_hour}
            ))
        elif session_hour in [21, 22, 23]:  # Session closing
            regimes.append(RegimeState(
                regime_type=RegimeType.SESSION_CLOSE,
                confidence=0.8,
                detected_at=datetime.now(),
                metrics={'session_hour': session_hour}
            ))
        
        self.current_regimes[symbol] = regimes
        return regimes
    
    def get_strategy_adjustments(
        self,
        symbol: str,
        strategy_id: str,
        base_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Get recommended strategy adjustments based on current regime.
        
        Returns:
            {
                'enabled': bool,
                'scale_factor': float,  # 0.0 to 1.0 (position sizing)
                'throttle_factor': float,  # 0.0 to 1.0 (execution frequency)
                'parameter_adjustments': Dict,
                'reason': str
            }
        """
        regimes = self.current_regimes.get(symbol, [])
        active_regimes = [r for r in regimes if r.is_active()]
        
        if not active_regimes:
            # No active regimes - use base config
            return {
                'enabled': True,
                'scale_factor': 1.0,
                'throttle_factor': 1.0,
                'parameter_adjustments': {},
                'reason': 'No active regime detected'
            }
        
        # Combine regime effects
        enabled = True
        scale_factor = 1.0
        throttle_factor = 1.0
        parameter_adjustments = {}
        reasons = []
        
        for regime in active_regimes:
            if regime.regime_type == RegimeType.HIGH_VOLATILITY:
                # Reduce position size, increase SL
                scale_factor *= 0.7
                parameter_adjustments['sl_multiplier'] = 1.2
                reasons.append('High volatility: reduced size, wider SL')
            
            elif regime.regime_type == RegimeType.LOW_VOLATILITY:
                # Normal operation, but may want tighter SL
                parameter_adjustments['sl_multiplier'] = 0.9
                reasons.append('Low volatility: tighter SL')
            
            elif regime.regime_type == RegimeType.WIDE_SPREAD:
                # Throttle execution, require higher quality
                throttle_factor *= 0.5
                parameter_adjustments['min_quality_score'] = base_config.get('min_quality_score', 50.0) + 10.0
                reasons.append('Wide spread: throttled, higher quality required')
            
            elif regime.regime_type == RegimeType.NARROW_SPREAD:
                # Normal operation
                reasons.append('Narrow spread: normal operation')
            
            elif regime.regime_type == RegimeType.TRENDING:
                # Increase position size slightly, normal throttle
                scale_factor *= 1.1
                reasons.append('Trending: slightly increased size')
            
            elif regime.regime_type == RegimeType.RANGING:
                # Throttle execution, require higher quality
                throttle_factor *= 0.6
                parameter_adjustments['min_quality_score'] = base_config.get('min_quality_score', 50.0) + 5.0
                reasons.append('Ranging: throttled, higher quality required')
            
            elif regime.regime_type == RegimeType.NEWS_EVENT:
                # Disable or heavily throttle
                enabled = False
                reasons.append('News event: disabled')
            
            elif regime.regime_type == RegimeType.SESSION_OPEN:
                # Slight throttle during opening
                throttle_factor *= 0.8
                reasons.append('Session open: slight throttle')
            
            elif regime.regime_type == RegimeType.SESSION_CLOSE:
                # Throttle during closing
                throttle_factor *= 0.5
                reasons.append('Session close: throttled')
        
        # Cap scale and throttle factors
        scale_factor = max(0.1, min(1.0, scale_factor))
        throttle_factor = max(0.1, min(1.0, throttle_factor))
        
        return {
            'enabled': enabled,
            'scale_factor': scale_factor,
            'throttle_factor': throttle_factor,
            'parameter_adjustments': parameter_adjustments,
            'reason': '; '.join(reasons)
        }
    
    def should_enable_strategy(
        self,
        symbol: str,
        strategy_id: str
    ) -> Tuple[bool, str]:
        """
        Check if strategy should be enabled for a symbol.
        
        Returns:
            (enabled: bool, reason: str)
        """
        adjustments = self.get_strategy_adjustments(symbol, strategy_id, {})
        return adjustments['enabled'], adjustments['reason']

