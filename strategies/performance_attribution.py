"""
Strategy Performance Attribution

Computes performance metrics per strategy:
- Trades attempted per strategy
- Execution rate per strategy
- Expectancy per strategy
- Drawdown per strategy

Includes minimum sample size rules and strategy validity checks.
"""

import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
import statistics


class StrategyPerformanceAttribution:
    """
    Analyzes performance metrics per strategy_id.
    
    This enables answering:
    - Which strategies are being evaluated?
    - Which ones are executing?
    - Which ones are profitable?
    - Under what market conditions?
    """
    
    def __init__(
        self,
        min_sample_size: int = 30,
        min_execution_rate: float = 0.05,  # 5% minimum execution rate
        min_expectancy_usd: float = 0.0,
        lookback_days: int = 30
    ):
        """
        Initialize performance attribution analyzer.
        
        Args:
            min_sample_size: Minimum trades needed to consider strategy valid
            min_execution_rate: Minimum execution rate (executed / evaluated)
            min_expectancy_usd: Minimum expectancy in USD to consider profitable
            lookback_days: Days to look back for analysis
        """
        self.min_sample_size = min_sample_size
        self.min_execution_rate = min_execution_rate
        self.min_expectancy_usd = min_expectancy_usd
        self.lookback_days = lookback_days
        
        # In-memory storage (could be replaced with database)
        self._opportunities = []  # All opportunities (evaluated)
        self._executions = []  # Executed trades
        self._closed_trades = []  # Closed trades with P&L
    
    def record_opportunity(
        self,
        strategy_id: str,
        symbol: str,
        timestamp: datetime,
        decision: str,  # 'EXECUTED' | 'REJECTED' | 'PENDING'
        opportunity_data: Dict[str, Any],
        rejection_reason: Optional[str] = None
    ):
        """Record an opportunity evaluation."""
        self._opportunities.append({
            'strategy_id': strategy_id,
            'symbol': symbol,
            'timestamp': timestamp,
            'decision': decision,
            'rejection_reason': rejection_reason,
            'quality_score': opportunity_data.get('quality_score', 0.0),
            'market_conditions': opportunity_data.get('market_conditions', {})
        })
    
    def record_execution(
        self,
        strategy_id: str,
        symbol: str,
        ticket: int,
        timestamp: datetime,
        execution_data: Dict[str, Any]
    ):
        """Record a trade execution."""
        self._executions.append({
            'strategy_id': strategy_id,
            'symbol': symbol,
            'ticket': ticket,
            'timestamp': timestamp,
            'entry_price': execution_data.get('entry_price_actual', 0.0),
            'lot_size': execution_data.get('lot_size', 0.01),
            'risk_usd': execution_data.get('risk_usd', 0.0),
            'quality_score': execution_data.get('quality_score', 0.0)
        })
    
    def record_trade_closed(
        self,
        ticket: int,
        close_time: datetime,
        profit_usd: float,
        close_reason: str
    ):
        """Record a closed trade with P&L."""
        # Find matching execution
        execution = next(
            (e for e in self._executions if e['ticket'] == ticket),
            None
        )
        
        if execution:
            self._closed_trades.append({
                'strategy_id': execution['strategy_id'],
                'symbol': execution['symbol'],
                'ticket': ticket,
                'entry_time': execution['timestamp'],
                'close_time': close_time,
                'profit_usd': profit_usd,
                'close_reason': close_reason,
                'risk_usd': execution['risk_usd'],
                'quality_score': execution['quality_score']
            })
    
    def compute_strategy_metrics(self, strategy_id: str) -> Dict[str, Any]:
        """
        Compute comprehensive metrics for a strategy.
        
        Returns:
            {
                'strategy_id': str,
                'valid': bool,
                'evaluated_count': int,
                'executed_count': int,
                'execution_rate': float,
                'closed_count': int,
                'win_rate': float,
                'total_profit_usd': float,
                'total_loss_usd': float,
                'expectancy_usd': float,
                'avg_win_usd': float,
                'avg_loss_usd': float,
                'max_drawdown_usd': float,
                'profit_factor': float,
                'sharpe_ratio': float (if enough data),
                'market_conditions': Dict
            }
        """
        # Filter by strategy_id and lookback period
        cutoff_time = datetime.now() - timedelta(days=self.lookback_days)
        
        opportunities = [
            o for o in self._opportunities
            if o['strategy_id'] == strategy_id and o['timestamp'] >= cutoff_time
        ]
        
        executions = [
            e for e in self._executions
            if e['strategy_id'] == strategy_id and e['timestamp'] >= cutoff_time
        ]
        
        closed_trades = [
            t for t in self._closed_trades
            if t['strategy_id'] == strategy_id and t['close_time'] >= cutoff_time
        ]
        
        evaluated_count = len(opportunities)
        executed_count = len(executions)
        closed_count = len(closed_trades)
        
        # Execution rate
        execution_rate = executed_count / evaluated_count if evaluated_count > 0 else 0.0
        
        # Win rate and P&L
        wins = [t for t in closed_trades if t['profit_usd'] > 0]
        losses = [t for t in closed_trades if t['profit_usd'] < 0]
        breakeven = [t for t in closed_trades if t['profit_usd'] == 0]
        
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / closed_count if closed_count > 0 else 0.0
        
        total_profit = sum(t['profit_usd'] for t in wins)
        total_loss = abs(sum(t['profit_usd'] for t in losses))
        avg_win = total_profit / win_count if win_count > 0 else 0.0
        avg_loss = total_loss / loss_count if loss_count > 0 else 0.0
        
        # Expectancy
        expectancy_usd = (win_rate * avg_win) - ((1 - win_rate) * avg_loss) if closed_count > 0 else 0.0
        
        # Profit factor
        profit_factor = total_profit / total_loss if total_loss > 0 else (float('inf') if total_profit > 0 else 0.0)
        
        # Drawdown calculation
        cumulative_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0
        
        for trade in sorted(closed_trades, key=lambda x: x['close_time']):
            cumulative_pnl += trade['profit_usd']
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = peak - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # Sharpe ratio (simplified, annualized)
        returns = [t['profit_usd'] / t['risk_usd'] if t['risk_usd'] > 0 else 0.0 for t in closed_trades]
        sharpe_ratio = None
        if len(returns) >= self.min_sample_size and statistics.stdev(returns) > 0:
            avg_return = statistics.mean(returns)
            std_return = statistics.stdev(returns)
            # Annualized (assuming ~252 trading days)
            sharpe_ratio = (avg_return / std_return) * (252 ** 0.5) if std_return > 0 else 0.0
        
        # Market conditions summary
        market_conditions = self._summarize_market_conditions(opportunities)
        
        # Validity check
        valid = (
            closed_count >= self.min_sample_size and
            execution_rate >= self.min_execution_rate and
            expectancy_usd >= self.min_expectancy_usd
        )
        
        return {
            'strategy_id': strategy_id,
            'valid': valid,
            'evaluated_count': evaluated_count,
            'executed_count': executed_count,
            'execution_rate': execution_rate,
            'closed_count': closed_count,
            'win_rate': win_rate,
            'total_profit_usd': total_profit,
            'total_loss_usd': total_loss,
            'expectancy_usd': expectancy_usd,
            'avg_win_usd': avg_win,
            'avg_loss_usd': avg_loss,
            'max_drawdown_usd': max_drawdown,
            'profit_factor': profit_factor,
            'sharpe_ratio': sharpe_ratio,
            'market_conditions': market_conditions,
            'sample_size_ok': closed_count >= self.min_sample_size,
            'execution_rate_ok': execution_rate >= self.min_execution_rate,
            'expectancy_ok': expectancy_usd >= self.min_expectancy_usd
        }
    
    def _summarize_market_conditions(self, opportunities: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize market conditions for opportunities."""
        if not opportunities:
            return {}
        
        quality_scores = [o.get('quality_score', 0.0) for o in opportunities]
        spreads = [
            o.get('market_conditions', {}).get('spread_points', 0.0)
            for o in opportunities
        ]
        
        return {
            'avg_quality_score': statistics.mean(quality_scores) if quality_scores else 0.0,
            'min_quality_score': min(quality_scores) if quality_scores else 0.0,
            'max_quality_score': max(quality_scores) if quality_scores else 0.0,
            'avg_spread_points': statistics.mean(spreads) if spreads else 0.0,
            'symbols': list(set(o['symbol'] for o in opportunities))
        }
    
    def get_all_strategy_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all strategies."""
        strategy_ids = set()
        strategy_ids.update(o['strategy_id'] for o in self._opportunities)
        strategy_ids.update(e['strategy_id'] for e in self._executions)
        
        return {
            strategy_id: self.compute_strategy_metrics(strategy_id)
            for strategy_id in strategy_ids
        }
    
    def get_strategy_ranking(self, sort_by: str = 'expectancy_usd') -> List[Dict[str, Any]]:
        """
        Get ranked list of strategies.
        
        Args:
            sort_by: 'expectancy_usd' | 'win_rate' | 'profit_factor' | 'execution_rate'
        """
        all_metrics = self.get_all_strategy_metrics()
        
        # Filter to valid strategies only
        valid_strategies = [
            metrics for metrics in all_metrics.values()
            if metrics['valid']
        ]
        
        # Sort
        reverse = True  # Higher is better
        if sort_by == 'max_drawdown_usd':
            reverse = False  # Lower drawdown is better
        
        valid_strategies.sort(key=lambda x: x.get(sort_by, 0.0), reverse=reverse)
        
        return valid_strategies
    
    def load_from_logs(self, log_dir: Path):
        """Load opportunity and trade data from log files."""
        # Implementation would parse log files
        # For now, this is a placeholder
        pass
    
    def export_report(self, output_path: Path):
        """Export performance attribution report to JSON."""
        report = {
            'generated_at': datetime.now().isoformat(),
            'lookback_days': self.lookback_days,
            'min_sample_size': self.min_sample_size,
            'min_execution_rate': self.min_execution_rate,
            'min_expectancy_usd': self.min_expectancy_usd,
            'strategies': self.get_all_strategy_metrics(),
            'ranking': self.get_strategy_ranking()
        }
        
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        return report

