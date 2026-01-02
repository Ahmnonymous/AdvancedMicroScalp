"""
Shadow Mode Decision Engine
Binary decision engine for shadow mode filter relaxation evaluation.
"""

from typing import Dict, Any, Tuple
from utils.logger_factory import get_logger

logger = get_logger("shadow_mode_decision", "logs/live/system/shadow_mode_decision.log")


class ShadowModeDecisionEngine:
    """Binary decision engine for shadow mode filter relaxation."""
    
    # Thresholds
    MIN_EXPECTANCY_INCREASE = 0.20      # $0.20 increase required
    MAX_DRAWDOWN_INCREASE = 0.10        # 10% max increase
    MIN_TRADES_SHADOW = 500             # Minimum shadow trades analyzed
    MIN_DAYS_SHADOW = 7                 # Minimum days of shadow data
    MIN_WIN_RATE_SHADOW = 0.75          # 75% win rate in shadow trades
    MAX_REJECTION_RATE = 0.95           # 95% max rejection (5% execution)
    
    def evaluate_shadow_mode(self, shadow_data: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Evaluate shadow mode data and return (approve, details).
        
        Decision Rules:
        1. Expectancy increase ≥ $0.20
        2. Drawdown increase ≤ 10%
        3. Shadow win rate ≥ 75%
        4. Shadow rejection rate ≤ 95%
        
        Args:
            shadow_data: Dictionary containing:
                - total_opportunities: int
                - days_collected: int
                - actual_metrics: Dict with actual system metrics
                - shadow_metrics: Dict with shadow mode metrics
                - filter_analysis: Dict with filter blocking analysis
                
        Returns:
            Tuple of (approved: bool, details: Dict)
        """
        # Check data sufficiency
        if shadow_data.get('total_opportunities', 0) < self.MIN_TRADES_SHADOW:
            return False, {
                'approved': False,
                'reason': f"Insufficient shadow data: {shadow_data.get('total_opportunities', 0)} < {self.MIN_TRADES_SHADOW}",
                'required_trades': self.MIN_TRADES_SHADOW
            }
        
        if shadow_data.get('days_collected', 0) < self.MIN_DAYS_SHADOW:
            return False, {
                'approved': False,
                'reason': f"Insufficient days: {shadow_data.get('days_collected', 0)} < {self.MIN_DAYS_SHADOW}",
                'required_days': self.MIN_DAYS_SHADOW
            }
        
        # Extract metrics
        actual_metrics = shadow_data.get('actual_metrics', {})
        shadow_metrics = shadow_data.get('shadow_metrics', {})
        
        # Calculate actual vs shadow metrics
        actual_expectancy = actual_metrics.get('expectancy_per_trade', 0)
        shadow_expectancy = shadow_metrics.get('expectancy_per_trade', 0)
        expectancy_increase = shadow_expectancy - actual_expectancy
        
        actual_drawdown = abs(actual_metrics.get('max_drawdown', 0))
        shadow_drawdown = abs(shadow_metrics.get('max_drawdown', 0))
        
        if actual_drawdown > 0:
            drawdown_increase = abs(shadow_drawdown - actual_drawdown) / actual_drawdown
        else:
            drawdown_increase = 0.0
        
        actual_rejection_rate = actual_metrics.get('rejection_rate', 0.9999)
        shadow_rejection_rate = shadow_metrics.get('rejection_rate', 0.9999)
        rejection_rate_improvement = actual_rejection_rate - shadow_rejection_rate
        
        shadow_win_rate = shadow_metrics.get('win_rate', 0)
        
        # Decision Rule 1: Expectancy increase
        if expectancy_increase < self.MIN_EXPECTANCY_INCREASE:
            return False, {
                'approved': False,
                'reason': f"Expectancy increase too small: ${expectancy_increase:.2f} < ${self.MIN_EXPECTANCY_INCREASE}",
                'actual_expectancy': actual_expectancy,
                'shadow_expectancy': shadow_expectancy,
                'expectancy_increase': expectancy_increase
            }
        
        # Decision Rule 2: Drawdown increase
        if drawdown_increase > self.MAX_DRAWDOWN_INCREASE:
            return False, {
                'approved': False,
                'reason': f"Drawdown increase too large: {drawdown_increase*100:.1f}% > {self.MAX_DRAWDOWN_INCREASE*100}%",
                'actual_drawdown': actual_drawdown,
                'shadow_drawdown': shadow_drawdown,
                'drawdown_increase': drawdown_increase
            }
        
        # Decision Rule 3: Win rate in shadow
        if shadow_win_rate < self.MIN_WIN_RATE_SHADOW:
            return False, {
                'approved': False,
                'reason': f"Shadow win rate too low: {shadow_win_rate*100:.1f}% < {self.MIN_WIN_RATE_SHADOW*100}%",
                'shadow_win_rate': shadow_win_rate
            }
        
        # Decision Rule 4: Rejection rate improvement
        if shadow_rejection_rate > self.MAX_REJECTION_RATE:
            return False, {
                'approved': False,
                'reason': f"Shadow rejection rate still too high: {shadow_rejection_rate*100:.1f}% > {self.MAX_REJECTION_RATE*100}%",
                'shadow_rejection_rate': shadow_rejection_rate
            }
        
        # All rules passed
        recommendations = self._recommend_filters(shadow_data)
        
        return True, {
            'approved': True,
            'expectancy_increase': expectancy_increase,
            'drawdown_increase': drawdown_increase,
            'rejection_rate_improvement': rejection_rate_improvement,
            'recommended_filters': recommendations,
            'metrics': {
                'actual_expectancy': actual_expectancy,
                'shadow_expectancy': shadow_expectancy,
                'actual_rejection_rate': actual_rejection_rate,
                'shadow_rejection_rate': shadow_rejection_rate,
                'actual_drawdown': actual_drawdown,
                'shadow_drawdown': shadow_drawdown
            }
        }
    
    def _recommend_filters(self, shadow_data: Dict) -> Dict[str, Any]:
        """
        Recommend which filters to relax based on shadow data.
        
        Analyzes which filters blocked most profitable opportunities.
        """
        filter_blocking_analysis = shadow_data.get('filter_analysis', {})
        
        recommendations = {}
        for filter_name, stats in filter_blocking_analysis.items():
            blocked_profitable = stats.get('blocked_profitable', 0)
            blocked_unprofitable = stats.get('blocked_unprofitable', 0)
            
            # If filter is blocking more profitable than unprofitable trades (1.5x ratio)
            if blocked_unprofitable > 0 and blocked_profitable > blocked_unprofitable * 1.5:
                recommendations[filter_name] = {
                    'action': 'relax',
                    'current_threshold': stats.get('current_threshold'),
                    'suggested_threshold': stats.get('suggested_threshold'),
                    'expected_impact': stats.get('expected_expectancy_increase', 0),
                    'blocked_profitable': blocked_profitable,
                    'blocked_unprofitable': blocked_unprofitable,
                    'ratio': blocked_profitable / blocked_unprofitable if blocked_unprofitable > 0 else 0
                }
            elif blocked_profitable > 0 and blocked_unprofitable == 0:
                # Filter only blocking profitable trades
                recommendations[filter_name] = {
                    'action': 'relax',
                    'current_threshold': stats.get('current_threshold'),
                    'suggested_threshold': stats.get('suggested_threshold'),
                    'expected_impact': stats.get('expected_expectancy_increase', 0),
                    'blocked_profitable': blocked_profitable,
                    'blocked_unprofitable': 0,
                    'ratio': float('inf')
                }
        
        return recommendations

