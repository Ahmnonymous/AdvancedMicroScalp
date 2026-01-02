"""
Strategy Tracking Integration

Integrates all strategy identification and tracking components:
- Fingerprinting
- Performance attribution
- Improvement loop
- Market regime awareness

This is the main entry point for strategy tracking in the bot.
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from pathlib import Path
import json

from .strategy_fingerprint import StrategyFingerprint
from .performance_attribution import StrategyPerformanceAttribution
from .improvement_loop import StrategyImprovementLoop, StrategyStatus
from .market_regime import MarketRegimeDetector


class StrategyTrackingSystem:
    """
    Complete strategy tracking and evolution system.
    
    This system answers:
    - What strategies are being evaluated?
    - Which ones are executing?
    - Which ones are profitable?
    - Under what market conditions?
    """
    
    def __init__(
        self,
        log_dir: Path = Path("logs/live/strategies"),
        min_sample_size: int = 30,
        min_execution_rate: float = 0.05
    ):
        """
        Initialize strategy tracking system.
        
        Args:
            log_dir: Directory for strategy logs
            min_sample_size: Minimum trades for strategy validity
            min_execution_rate: Minimum execution rate
        """
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.fingerprint = StrategyFingerprint()
        self.attribution = StrategyPerformanceAttribution(
            min_sample_size=min_sample_size,
            min_execution_rate=min_execution_rate
        )
        self.improvement_loop = StrategyImprovementLoop()
        self.regime_detector = MarketRegimeDetector()
        
        # Log files
        self.opportunities_log = self.log_dir / "opportunities.jsonl"
        self.executions_log = self.log_dir / "executions.jsonl"
        self.closed_trades_log = self.log_dir / "closed_trades.jsonl"
    
    def log_opportunity(
        self,
        symbol: str,
        opportunity: Dict[str, Any],
        filter_results: Optional[Dict[str, Any]] = None,
        decision: str = "REJECTED",  # EXECUTED | REJECTED | PENDING
        rejection_reason: Optional[str] = None
    ) -> str:
        """
        Log an opportunity evaluation with strategy fingerprint.
        
        Returns:
            strategy_id: Generated strategy identifier
        """
        # Generate fingerprint
        log_entry = self.fingerprint.log_opportunity_fingerprint(
            symbol=symbol,
            opportunity=opportunity,
            filter_results=filter_results,
            decision=decision,
            rejection_reason=rejection_reason
        )
        
        strategy_id = log_entry['strategy_id']
        
        # Record in attribution system
        self.attribution.record_opportunity(
            strategy_id=strategy_id,
            symbol=symbol,
            timestamp=datetime.fromisoformat(log_entry['timestamp']),
            decision=decision,
            opportunity_data=opportunity,
            rejection_reason=rejection_reason
        )
        
        # Write to log file
        with open(self.opportunities_log, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
        
        return strategy_id
    
    def log_execution(
        self,
        symbol: str,
        ticket: int,
        opportunity: Dict[str, Any],
        execution_result: Dict[str, Any],
        filter_results: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Log a trade execution with strategy fingerprint.
        
        Returns:
            strategy_id: Strategy identifier
        """
        # Generate fingerprint
        strategy_id = self.fingerprint.generate_strategy_id(opportunity, filter_results)
        
        # Record in attribution system
        self.attribution.record_execution(
            strategy_id=strategy_id,
            symbol=symbol,
            ticket=ticket,
            timestamp=datetime.now(),
            execution_data={
                'entry_price_actual': execution_result.get('entry_price_actual', 0.0),
                'lot_size': execution_result.get('lot_size', 0.01),
                'risk_usd': execution_result.get('risk_usd', 0.0),
                'quality_score': opportunity.get('quality_score', 0.0)
            }
        )
        
        # Write to log file
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'strategy_id': strategy_id,
            'symbol': symbol,
            'ticket': ticket,
            'execution_result': execution_result,
            'opportunity': opportunity
        }
        
        with open(self.executions_log, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
        
        return strategy_id
    
    def log_trade_closed(
        self,
        ticket: int,
        close_time: datetime,
        profit_usd: float,
        close_reason: str
    ):
        """Log a closed trade with P&L."""
        # Record in attribution system
        self.attribution.record_trade_closed(
            ticket=ticket,
            close_time=close_time,
            profit_usd=profit_usd,
            close_reason=close_reason
        )
        
        # Write to log file
        log_entry = {
            'timestamp': close_time.isoformat(),
            'ticket': ticket,
            'profit_usd': profit_usd,
            'close_reason': close_reason
        }
        
        with open(self.closed_trades_log, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    
    def get_strategy_metrics(self, strategy_id: str) -> Dict[str, Any]:
        """Get performance metrics for a strategy."""
        return self.attribution.compute_strategy_metrics(strategy_id)
    
    def get_all_strategy_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all strategies."""
        return self.attribution.get_all_strategy_metrics()
    
    def get_strategy_ranking(self, sort_by: str = 'expectancy_usd') -> List[Dict[str, Any]]:
        """Get ranked list of strategies."""
        return self.attribution.get_strategy_ranking(sort_by=sort_by)
    
    def check_strategy_enabled(
        self,
        symbol: str,
        strategy_id: str,
        market_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Check if strategy should be enabled based on regime and improvement loop.
        
        Returns:
            (enabled: bool, reason: str)
        """
        # Check improvement loop status
        variants = self.improvement_loop.get_active_variants(StrategyStatus.LIVE)
        variant_ids = [v.variant_id for v in variants]
        
        # If strategy has a live variant, check if it's the current one
        base_variant_id = self.improvement_loop.base_strategies.get(strategy_id)
        if base_variant_id and base_variant_id not in variant_ids:
            return False, "Strategy has been demoted or replaced"
        
        # Check market regime
        if market_data:
            regimes = self.regime_detector.detect_regime(symbol, market_data)
            adjustments = self.regime_detector.get_strategy_adjustments(
                symbol, strategy_id, {}
            )
            if not adjustments['enabled']:
                return False, adjustments['reason']
        
        return True, "Strategy enabled"
    
    def generate_performance_report(self, output_path: Optional[Path] = None) -> Dict[str, Any]:
        """Generate comprehensive performance report."""
        if output_path is None:
            output_path = self.log_dir / f"performance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        report = self.attribution.export_report(output_path)
        
        # Add improvement loop state
        report['improvement_loop'] = self.improvement_loop.export_state()
        
        # Add top strategies
        report['top_strategies'] = self.attribution.get_strategy_ranking()[:10]
        
        return report
    
    def suggest_strategy_improvements(self, strategy_id: str) -> List[Dict[str, Any]]:
        """Suggest strategy improvements to test in shadow mode."""
        return self.improvement_loop.get_modification_suggestions(strategy_id)

