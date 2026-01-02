"""
Strategy Improvement Loop (Safe)

Designs a non-destructive strategy improvement loop:
- Shadow variants per strategy
- A/B testing without capital risk
- Promotion / demotion rules

Constraints:
- No live behavior changes without passing gates
- One variable changed at a time
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass
import json


class StrategyStatus(Enum):
    """Strategy status in the improvement loop."""
    LIVE = "live"  # Currently executing with real capital
    SHADOW = "shadow"  # Evaluating without execution
    PROMOTED = "promoted"  # Passed gates, ready for promotion
    DEMOTED = "demoted"  # Failed gates, disabled
    ARCHIVED = "archived"  # No longer active


class GateType(Enum):
    """Promotion gate types."""
    SAMPLE_SIZE = "sample_size"  # Minimum trades
    EXPECTANCY = "expectancy"  # Minimum expectancy
    WIN_RATE = "win_rate"  # Minimum win rate
    DRAWDOWN = "drawdown"  # Maximum drawdown
    CONSISTENCY = "consistency"  # Consistent performance over time


@dataclass
class StrategyVariant:
    """Represents a strategy variant (base or modified)."""
    variant_id: str
    base_strategy_id: str
    status: StrategyStatus
    modification: Dict[str, Any]  # What changed from base
    created_at: datetime
    promoted_at: Optional[datetime] = None
    demoted_at: Optional[datetime] = None
    
    # Performance tracking
    evaluated_count: int = 0
    executed_count: int = 0
    closed_count: int = 0
    total_profit_usd: float = 0.0
    expectancy_usd: float = 0.0
    
    # Gate results
    gate_results: Dict[str, bool] = None
    
    def __post_init__(self):
        if self.gate_results is None:
            self.gate_results = {}


class StrategyImprovementLoop:
    """
    Manages safe strategy evolution through shadow variants and A/B testing.
    
    This ensures no live behavior changes without passing validation gates.
    """
    
    def __init__(
        self,
        min_shadow_trades: int = 50,
        min_expectancy_improvement_pct: float = 10.0,  # 10% improvement required
        min_win_rate: float = 0.45,  # 45% minimum win rate
        max_drawdown_usd: float = 20.0,
        consistency_period_days: int = 7
    ):
        """
        Initialize improvement loop.
        
        Args:
            min_shadow_trades: Minimum trades in shadow mode before promotion
            min_expectancy_improvement_pct: Minimum % improvement over base
            min_win_rate: Minimum win rate to pass gates
            max_drawdown_usd: Maximum drawdown allowed
            consistency_period_days: Days to check for consistent performance
        """
        self.min_shadow_trades = min_shadow_trades
        self.min_expectancy_improvement_pct = min_expectancy_improvement_pct
        self.min_win_rate = min_win_rate
        self.max_drawdown_usd = max_drawdown_usd
        self.consistency_period_days = consistency_period_days
        
        self.variants: Dict[str, StrategyVariant] = {}
        self.base_strategies: Dict[str, str] = {}  # strategy_id -> base_variant_id
    
    def create_shadow_variant(
        self,
        base_strategy_id: str,
        modification: Dict[str, Any],
        variant_id: Optional[str] = None
    ) -> StrategyVariant:
        """
        Create a shadow variant of a base strategy.
        
        Args:
            base_strategy_id: Original strategy ID
            modification: Single variable change (e.g., {'min_quality_score': 55.0})
            variant_id: Optional custom variant ID
        
        Returns:
            StrategyVariant instance
        """
        if variant_id is None:
            # Generate variant ID from modification
            mod_str = json.dumps(modification, sort_keys=True)
            variant_id = f"{base_strategy_id}_SHADOW_{hash(mod_str) % 10000:04d}"
        
        variant = StrategyVariant(
            variant_id=variant_id,
            base_strategy_id=base_strategy_id,
            status=StrategyStatus.SHADOW,
            modification=modification,
            created_at=datetime.now()
        )
        
        self.variants[variant_id] = variant
        self.base_strategies[base_strategy_id] = variant_id
        
        return variant
    
    def evaluate_gates(self, variant_id: str, performance_metrics: Dict[str, Any]) -> Dict[str, bool]:
        """
        Evaluate promotion gates for a variant.
        
        Args:
            variant_id: Variant to evaluate
            performance_metrics: Performance metrics from attribution system
        
        Returns:
            Dictionary of gate results {gate_type: passed}
        """
        variant = self.variants.get(variant_id)
        if not variant:
            return {}
        
        gate_results = {}
        
        # Gate 1: Sample Size
        closed_count = performance_metrics.get('closed_count', 0)
        gate_results[GateType.SAMPLE_SIZE.value] = closed_count >= self.min_shadow_trades
        
        # Gate 2: Expectancy
        expectancy_usd = performance_metrics.get('expectancy_usd', 0.0)
        # Get base strategy expectancy for comparison
        base_expectancy = self._get_base_expectancy(variant.base_strategy_id)
        if base_expectancy is not None:
            improvement_pct = ((expectancy_usd - base_expectancy) / abs(base_expectancy)) * 100 if base_expectancy != 0 else 0
            gate_results[GateType.EXPECTANCY.value] = (
                expectancy_usd >= self.min_expectancy_improvement_pct and
                improvement_pct >= self.min_expectancy_improvement_pct
            )
        else:
            gate_results[GateType.EXPECTANCY.value] = expectancy_usd >= 0.0
        
        # Gate 3: Win Rate
        win_rate = performance_metrics.get('win_rate', 0.0)
        gate_results[GateType.WIN_RATE.value] = win_rate >= self.min_win_rate
        
        # Gate 4: Drawdown
        max_drawdown = performance_metrics.get('max_drawdown_usd', 0.0)
        gate_results[GateType.DRAWDOWN.value] = max_drawdown <= self.max_drawdown_usd
        
        # Gate 5: Consistency (simplified - would need time-series data)
        # For now, just check if performance is stable
        gate_results[GateType.CONSISTENCY.value] = True  # Placeholder
        
        variant.gate_results = gate_results
        return gate_results
    
    def _get_base_expectancy(self, base_strategy_id: str) -> Optional[float]:
        """Get base strategy expectancy for comparison."""
        # This would query the performance attribution system
        # For now, return None (no comparison)
        return None
    
    def check_promotion_ready(self, variant_id: str) -> Tuple[bool, List[str]]:
        """
        Check if variant is ready for promotion.
        
        Returns:
            (ready: bool, reasons: List[str])
        """
        variant = self.variants.get(variant_id)
        if not variant or variant.status != StrategyStatus.SHADOW:
            return False, ["Variant not in shadow mode"]
        
        if not variant.gate_results:
            return False, ["Gates not evaluated"]
        
        failed_gates = [
            gate for gate, passed in variant.gate_results.items()
            if not passed
        ]
        
        if failed_gates:
            return False, [f"Failed gates: {', '.join(failed_gates)}"]
        
        return True, ["All gates passed"]
    
    def promote_variant(self, variant_id: str) -> bool:
        """
        Promote a shadow variant to live.
        
        This should only be called after gates are passed.
        """
        variant = self.variants.get(variant_id)
        if not variant:
            return False
        
        ready, reasons = self.check_promotion_ready(variant_id)
        if not ready:
            return False
        
        # Update status
        variant.status = StrategyStatus.PROMOTED
        variant.promoted_at = datetime.now()
        
        # Demote base strategy (if exists)
        base_variant_id = self.base_strategies.get(variant.base_strategy_id)
        if base_variant_id and base_variant_id in self.variants:
            base_variant = self.variants[base_variant_id]
            if base_variant.status == StrategyStatus.LIVE:
                base_variant.status = StrategyStatus.DEMOTED
                base_variant.demoted_at = datetime.now()
        
        return True
    
    def demote_variant(self, variant_id: str, reason: str) -> bool:
        """
        Demote a variant (live or shadow).
        
        Args:
            variant_id: Variant to demote
            reason: Reason for demotion
        """
        variant = self.variants.get(variant_id)
        if not variant:
            return False
        
        variant.status = StrategyStatus.DEMOTED
        variant.demoted_at = datetime.now()
        
        return True
    
    def get_active_variants(self, status: Optional[StrategyStatus] = None) -> List[StrategyVariant]:
        """Get variants by status."""
        if status:
            return [v for v in self.variants.values() if v.status == status]
        return list(self.variants.values())
    
    def get_modification_suggestions(self, base_strategy_id: str) -> List[Dict[str, Any]]:
        """
        Suggest safe modifications to test.
        
        Returns list of single-variable modifications to try.
        """
        suggestions = [
            {
                'variable': 'min_quality_score',
                'modification': {'min_quality_score': 55.0},
                'description': 'Increase quality threshold by 5 points'
            },
            {
                'variable': 'min_quality_score',
                'modification': {'min_quality_score': 45.0},
                'description': 'Decrease quality threshold by 5 points'
            },
            {
                'variable': 'rsi_entry_range',
                'modification': {
                    'rsi_entry_range_min': 25.0,
                    'rsi_entry_range_max': 55.0
                },
                'description': 'Widen RSI entry range'
            },
            {
                'variable': 'min_trend_strength_pct',
                'modification': {'min_trend_strength_pct': 0.10},
                'description': 'Increase trend strength requirement'
            },
        ]
        
        return suggestions
    
    def export_state(self) -> Dict[str, Any]:
        """Export improvement loop state for persistence."""
        return {
            'variants': {
                vid: {
                    'variant_id': v.variant_id,
                    'base_strategy_id': v.base_strategy_id,
                    'status': v.status.value,
                    'modification': v.modification,
                    'created_at': v.created_at.isoformat(),
                    'promoted_at': v.promoted_at.isoformat() if v.promoted_at else None,
                    'demoted_at': v.demoted_at.isoformat() if v.demoted_at else None,
                    'gate_results': v.gate_results
                }
                for vid, v in self.variants.items()
            },
            'base_strategies': self.base_strategies
        }

