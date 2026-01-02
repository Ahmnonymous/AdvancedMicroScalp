"""
Strategy Evolution Locks
Enforces rules for strategy evolution with time-based and trade-count locks.
"""

from typing import Dict, Any, Tuple
from monitor.expectancy_gate import ExpectancyGate
from utils.logger_factory import get_logger

logger = get_logger("strategy_evolution_locks", "logs/live/system/strategy_evolution_locks.log")


class StrategyEvolutionLocks:
    """Enforces rules for strategy evolution."""
    
    # Time-based locks
    MIN_DAYS_MICRO_HFT_DOMINANT = 30
    MIN_DAYS_BEFORE_HYBRID = 60
    MIN_DAYS_BEFORE_RE_EVALUATE = 90
    
    # Trade count minimums
    MIN_TRADES_MICRO_HFT = 500
    MIN_TRADES_BEFORE_HYBRID = 1000
    MIN_TRADES_BEFORE_RE_EVALUATE = 2000
    
    # Data sufficiency
    MIN_SYMBOLS_ACTIVE = 5
    MIN_TRADES_PER_SYMBOL = 20
    
    def __init__(self):
        self.expectancy_gate = ExpectancyGate()
    
    def can_remain_micro_hft_dominant(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if Micro-HFT can remain dominant strategy.
        
        Requirements:
        - Time lock: 30 days active
        - Trade count lock: 500 trades
        - Expectancy gate: Must pass position size scaling criteria
        - Data sufficiency: At least 5 symbols with 20+ trades each
        """
        # Time lock
        days_active = metrics.get('days_active', 0)
        if days_active < self.MIN_DAYS_MICRO_HFT_DOMINANT:
            return False, f"Time lock: {days_active} days < {self.MIN_DAYS_MICRO_HFT_DOMINANT} days required"
        
        # Trade count lock
        total_trades = metrics.get('total_trades', 0)
        if total_trades < self.MIN_TRADES_MICRO_HFT:
            return False, f"Trade count lock: {total_trades} < {self.MIN_TRADES_MICRO_HFT} required"
        
        # Expectancy gate
        can_scale, reason = self.expectancy_gate.can_scale_position_size(metrics)
        if not can_scale:
            return False, f"Expectancy gate failed: {reason}"
        
        # Data sufficiency - active symbols
        active_symbols = metrics.get('active_symbols', 0)
        if active_symbols < self.MIN_SYMBOLS_ACTIVE:
            return False, f"Insufficient symbols: {active_symbols} < {self.MIN_SYMBOLS_ACTIVE}"
        
        # Data sufficiency - symbols with sufficient trades
        symbol_metrics_dict = metrics.get('symbol_metrics', {})
        symbols_with_sufficient_trades = sum(
            1 for symbol_metrics in symbol_metrics_dict.values()
            if symbol_metrics.get('trade_count', 0) >= self.MIN_TRADES_PER_SYMBOL
        )
        
        if symbols_with_sufficient_trades < self.MIN_SYMBOLS_ACTIVE:
            return False, f"Insufficient symbols with data: {symbols_with_sufficient_trades} < {self.MIN_SYMBOLS_ACTIVE}"
        
        return True, "All locks satisfied for Micro-HFT dominant strategy"
    
    def can_enable_hybrid_strategy(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if hybrid strategy (Micro-HFT + Runners) can be enabled.
        
        Requirements:
        - Time lock: 60 days active
        - Trade count lock: 1000 trades
        - Expectancy gate: Must pass runner enablement criteria
        - Micro-HFT must still be profitable: Expectancy ≥ $0.10
        """
        # Time lock
        days_active = metrics.get('days_active', 0)
        if days_active < self.MIN_DAYS_BEFORE_HYBRID:
            return False, f"Time lock: {days_active} days < {self.MIN_DAYS_BEFORE_HYBRID} days required"
        
        # Trade count lock
        total_trades = metrics.get('total_trades', 0)
        if total_trades < self.MIN_TRADES_BEFORE_HYBRID:
            return False, f"Trade count lock: {total_trades} < {self.MIN_TRADES_BEFORE_HYBRID} required"
        
        # Expectancy gate for runners
        can_enable_runners, reason = self.expectancy_gate.can_enable_runners(metrics)
        if not can_enable_runners:
            return False, f"Runner expectancy gate failed: {reason}"
        
        # Micro-HFT must still be profitable
        micro_hft_metrics = metrics.get('micro_hft_metrics', {})
        if not micro_hft_metrics:
            # Fallback to overall metrics if micro_hft_metrics not available
            micro_hft_expectancy = self.expectancy_gate.calculate_expectancy_per_trade(metrics)
        else:
            micro_hft_expectancy = self.expectancy_gate.calculate_expectancy_per_trade(micro_hft_metrics)
        
        if micro_hft_expectancy < 0.10:
            return False, f"Micro-HFT expectancy too low: ${micro_hft_expectancy:.2f} < $0.10"
        
        return True, "All locks satisfied for hybrid strategy"
    
    def must_re_evaluate_micro_hft(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if Micro-HFT must be re-evaluated (consider phasing out).
        
        Requirements:
        - Time lock: 90 days active
        - Trade count lock: 2000 trades
        - Performance check: If any of these are true:
          * Expectancy < $0.05 per trade
          * Win rate < 70%
          * Risk:Reward < 1:0.20
        """
        # Time lock
        days_active = metrics.get('days_active', 0)
        if days_active < self.MIN_DAYS_BEFORE_RE_EVALUATE:
            return False, f"Time lock: {days_active} days < {self.MIN_DAYS_BEFORE_RE_EVALUATE} days required"
        
        # Trade count lock
        total_trades = metrics.get('total_trades', 0)
        if total_trades < self.MIN_TRADES_BEFORE_RE_EVALUATE:
            return False, f"Trade count lock: {total_trades} < {self.MIN_TRADES_BEFORE_RE_EVALUATE} required"
        
        # Check if Micro-HFT is underperforming
        expectancy = self.expectancy_gate.calculate_expectancy_per_trade(metrics)
        if expectancy < 0.05:  # Less than $0.05 per trade
            return True, f"Expectancy below threshold: ${expectancy:.2f} < $0.05"
        
        # Check win rate
        win_rate = metrics.get('wins', 0) / metrics.get('total_trades', 1) if metrics.get('total_trades', 0) > 0 else 0
        if win_rate < 0.70:  # Less than 70%
            return True, f"Win rate below threshold: {win_rate*100:.1f}% < 70%"
        
        # Check risk:reward
        avg_win = metrics.get('total_profit', 0) / metrics.get('wins', 1) if metrics.get('wins', 0) > 0 else 0
        avg_loss = abs(metrics.get('total_loss', 0) / metrics.get('losses', 1)) if metrics.get('losses', 0) > 0 else 0
        
        if avg_loss > 0:
            risk_reward = avg_win / avg_loss
            if risk_reward < 0.20:  # Less than 1:0.2
                return True, f"Risk:Reward below threshold: 1:{risk_reward:.2f} < 1:0.20"
        
        return False, "Micro-HFT performance acceptable"

