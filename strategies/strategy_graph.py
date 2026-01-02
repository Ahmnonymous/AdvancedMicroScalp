"""
Strategy Decision Graph Mapping

Converts bot logic into a readable decision graph showing:
- Entry condition clusters
- Filter stack
- Execution paths

This makes implicit strategies explicit and measurable.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class FilterType(Enum):
    """Filter categories in the decision pipeline."""
    SYMBOL_AVAILABILITY = "symbol_availability"
    MARKET_TIMING = "market_timing"
    NEWS = "news"
    VOLUME = "volume"
    TREND_SIGNAL = "trend_signal"
    RSI = "rsi"
    HALAL = "halal"
    SETUP_VALIDATION = "setup_validation"
    TREND_STRENGTH = "trend_strength"
    QUALITY_SCORE = "quality_score"
    TIMING_GUARDS = "timing_guards"
    RISK_CHECKS = "risk_checks"
    ENTRY_FILTERS = "entry_filters"


@dataclass
class DecisionNode:
    """Represents a decision point in the strategy graph."""
    node_id: str
    node_type: str  # 'filter' | 'condition' | 'execution'
    description: str
    filter_type: Optional[FilterType] = None
    threshold: Optional[Any] = None
    pass_path: Optional[str] = None  # Next node if passed
    fail_path: Optional[str] = None  # Next node if failed
    metadata: Dict[str, Any] = None


class StrategyGraphMapper:
    """
    Maps current bot logic into a strategy decision graph.
    
    This creates an explicit representation of all decision paths,
    making it possible to identify which strategies are actually running.
    """
    
    def __init__(self):
        self.nodes = {}
        self._build_graph()
    
    def _build_graph(self):
        """Build the complete decision graph from bot logic."""
        
        # START: Symbol Discovery
        self.nodes['START'] = DecisionNode(
            node_id='START',
            node_type='condition',
            description='Begin opportunity scan',
            pass_path='SYMBOL_DISCOVERY'
        )
        
        # SYMBOL_DISCOVERY: Get tradeable symbols
        self.nodes['SYMBOL_DISCOVERY'] = DecisionNode(
            node_id='SYMBOL_DISCOVERY',
            node_type='condition',
            description='Get tradeable symbols from pair_filter',
            pass_path='SYMBOL_LOOP'
        )
        
        # SYMBOL_LOOP: For each symbol
        self.nodes['SYMBOL_LOOP'] = DecisionNode(
            node_id='SYMBOL_LOOP',
            node_type='condition',
            description='Iterate through symbols',
            pass_path='SYMBOL_TRADEABLE'
        )
        
        # SYMBOL_TRADEABLE: Check if symbol is tradeable now
        self.nodes['SYMBOL_TRADEABLE'] = DecisionNode(
            node_id='SYMBOL_TRADEABLE',
            node_type='filter',
            filter_type=FilterType.SYMBOL_AVAILABILITY,
            description='Is symbol tradeable/executable right now?',
            threshold='Market open, trade mode enabled',
            pass_path='MARKET_CLOSING',
            fail_path='SYMBOL_SKIP'
        )
        
        # MARKET_CLOSING: Market closing filter
        self.nodes['MARKET_CLOSING'] = DecisionNode(
            node_id='MARKET_CLOSING',
            node_type='filter',
            filter_type=FilterType.MARKET_TIMING,
            description='Market closing filter (30 min before close)',
            threshold='30 minutes before market close',
            pass_path='VOLUME_FILTER',
            fail_path='SYMBOL_SKIP'
        )
        
        # VOLUME_FILTER: Volume/liquidity check
        self.nodes['VOLUME_FILTER'] = DecisionNode(
            node_id='VOLUME_FILTER',
            node_type='filter',
            filter_type=FilterType.VOLUME,
            description='Volume/liquidity sufficient?',
            threshold='Minimum volume required',
            pass_path='NEWS_FILTER',
            fail_path='SYMBOL_SKIP'
        )
        
        # NEWS_FILTER: News blocking check
        self.nodes['NEWS_FILTER'] = DecisionNode(
            node_id='NEWS_FILTER',
            node_type='filter',
            filter_type=FilterType.NEWS,
            description='High-impact news blocking?',
            threshold='±10 minutes window',
            pass_path='PENDING_SIGNAL_CHECK',
            fail_path='SYMBOL_SKIP'
        )
        
        # PENDING_SIGNAL_CHECK: One-candle confirmation
        self.nodes['PENDING_SIGNAL_CHECK'] = DecisionNode(
            node_id='PENDING_SIGNAL_CHECK',
            node_type='condition',
            description='Check for pending signal (one-candle confirmation)',
            pass_path='TREND_SIGNAL',
            fail_path='TREND_SIGNAL'  # Continue to trend signal if no pending
        )
        
        # TREND_SIGNAL: Get trend signal (SMA20 vs SMA50)
        self.nodes['TREND_SIGNAL'] = DecisionNode(
            node_id='TREND_SIGNAL',
            node_type='condition',
            description='Get trend signal: SMA20 vs SMA50',
            pass_path='TREND_SIGNAL_VALID'
        )
        
        # TREND_SIGNAL_VALID: Signal != NONE
        self.nodes['TREND_SIGNAL_VALID'] = DecisionNode(
            node_id='TREND_SIGNAL_VALID',
            node_type='filter',
            filter_type=FilterType.TREND_SIGNAL,
            description='Trend signal is LONG or SHORT?',
            threshold='signal != NONE',
            pass_path='RSI_FILTER',
            fail_path='SYMBOL_SKIP'
        )
        
        # RSI_FILTER: RSI entry range check
        self.nodes['RSI_FILTER'] = DecisionNode(
            node_id='RSI_FILTER',
            node_type='filter',
            filter_type=FilterType.RSI,
            description='RSI in entry range?',
            threshold='RSI 30-50 (configurable)',
            pass_path='HALAL_CHECK',
            fail_path='SYMBOL_SKIP'
        )
        
        # HALAL_CHECK: Halal compliance
        self.nodes['HALAL_CHECK'] = DecisionNode(
            node_id='HALAL_CHECK',
            node_type='filter',
            filter_type=FilterType.HALAL,
            description='Halal compliance check',
            threshold='Swap rules, direction validation',
            pass_path='MIN_LOT_CHECK',
            fail_path='SYMBOL_SKIP'
        )
        
        # MIN_LOT_CHECK: Minimum lot size (test mode)
        self.nodes['MIN_LOT_CHECK'] = DecisionNode(
            node_id='MIN_LOT_CHECK',
            node_type='filter',
            description='Minimum lot size check (test mode)',
            threshold='0.01-0.1, risk <= $2',
            pass_path='SETUP_VALIDATION',
            fail_path='SYMBOL_SKIP'
        )
        
        # SETUP_VALIDATION: Setup valid for scalping
        self.nodes['SETUP_VALIDATION'] = DecisionNode(
            node_id='SETUP_VALIDATION',
            node_type='filter',
            filter_type=FilterType.SETUP_VALIDATION,
            description='Setup validation (signal != NONE)',
            threshold='signal must be LONG or SHORT',
            pass_path='TREND_STRENGTH',
            fail_path='SYMBOL_SKIP'
        )
        
        # TREND_STRENGTH: Minimum trend strength
        self.nodes['TREND_STRENGTH'] = DecisionNode(
            node_id='TREND_STRENGTH',
            node_type='filter',
            filter_type=FilterType.TREND_STRENGTH,
            description='SMA separation >= minimum?',
            threshold='min_trend_strength_pct (default 0.05%)',
            pass_path='QUALITY_SCORE',
            fail_path='SYMBOL_SKIP'
        )
        
        # QUALITY_SCORE: Quality score threshold
        self.nodes['QUALITY_SCORE'] = DecisionNode(
            node_id='QUALITY_SCORE',
            node_type='filter',
            filter_type=FilterType.QUALITY_SCORE,
            description='Quality score >= minimum?',
            threshold='min_quality_score (default 50.0)',
            pass_path='TIMING_GUARDS',
            fail_path='SYMBOL_SKIP'
        )
        
        # TIMING_GUARDS: Trend maturity and impulse exhaustion
        self.nodes['TIMING_GUARDS'] = DecisionNode(
            node_id='TIMING_GUARDS',
            node_type='filter',
            filter_type=FilterType.TIMING_GUARDS,
            description='Timing guards (trend maturity, impulse exhaustion)',
            threshold='Heuristic checks',
            pass_path='PORTFOLIO_RISK',
            fail_path='SYMBOL_SKIP'
        )
        
        # PORTFOLIO_RISK: Portfolio risk limit
        self.nodes['PORTFOLIO_RISK'] = DecisionNode(
            node_id='PORTFOLIO_RISK',
            node_type='filter',
            filter_type=FilterType.RISK_CHECKS,
            description='Portfolio risk limit check',
            threshold='max_portfolio_risk_pct or max_portfolio_risk_usd',
            pass_path='CAN_OPEN_TRADE',
            fail_path='SYMBOL_SKIP'
        )
        
        # CAN_OPEN_TRADE: Can open new trade
        self.nodes['CAN_OPEN_TRADE'] = DecisionNode(
            node_id='CAN_OPEN_TRADE',
            node_type='filter',
            filter_type=FilterType.RISK_CHECKS,
            description='Can open new trade? (max_open_trades, cooldown)',
            threshold='max_open_trades limit, symbol cooldown',
            pass_path='ENTRY_FILTERS',
            fail_path='SYMBOL_SKIP'
        )
        
        # ENTRY_FILTERS: Risk manager entry filters
        self.nodes['ENTRY_FILTERS'] = DecisionNode(
            node_id='ENTRY_FILTERS',
            node_type='filter',
            filter_type=FilterType.ENTRY_FILTERS,
            description='Entry filters (volatility floor, spread sanity, candle quality, trend gate, cooldown)',
            threshold='Multiple sub-filters',
            pass_path='OPPORTUNITY_CREATED',
            fail_path='SYMBOL_SKIP'
        )
        
        # OPPORTUNITY_CREATED: Add to opportunities list
        self.nodes['OPPORTUNITY_CREATED'] = DecisionNode(
            node_id='OPPORTUNITY_CREATED',
            node_type='condition',
            description='Opportunity added to list',
            pass_path='EXECUTION'
        )
        
        # EXECUTION: Execute trade
        self.nodes['EXECUTION'] = DecisionNode(
            node_id='EXECUTION',
            node_type='execution',
            description='Execute trade via order_manager',
            pass_path='END'
        )
        
        # SYMBOL_SKIP: Skip this symbol
        self.nodes['SYMBOL_SKIP'] = DecisionNode(
            node_id='SYMBOL_SKIP',
            node_type='condition',
            description='Skip symbol, continue to next',
            pass_path='SYMBOL_LOOP'
        )
        
        # END: End of cycle
        self.nodes['END'] = DecisionNode(
            node_id='END',
            node_type='condition',
            description='End of opportunity scan cycle'
        )
    
    def get_graph_text(self) -> str:
        """Generate readable textual representation of the graph."""
        lines = []
        lines.append("=" * 80)
        lines.append("STRATEGY DECISION GRAPH")
        lines.append("=" * 80)
        lines.append("")
        lines.append("This graph shows the complete decision path for each symbol evaluation.")
        lines.append("")
        
        # Main path
        main_path = [
            'START',
            'SYMBOL_DISCOVERY',
            'SYMBOL_LOOP',
            'SYMBOL_TRADEABLE',
            'MARKET_CLOSING',
            'VOLUME_FILTER',
            'NEWS_FILTER',
            'PENDING_SIGNAL_CHECK',
            'TREND_SIGNAL',
            'TREND_SIGNAL_VALID',
            'RSI_FILTER',
            'HALAL_CHECK',
            'MIN_LOT_CHECK',
            'SETUP_VALIDATION',
            'TREND_STRENGTH',
            'QUALITY_SCORE',
            'TIMING_GUARDS',
            'PORTFOLIO_RISK',
            'CAN_OPEN_TRADE',
            'ENTRY_FILTERS',
            'OPPORTUNITY_CREATED',
            'EXECUTION',
            'END'
        ]
        
        lines.append("MAIN EXECUTION PATH:")
        lines.append("-" * 80)
        for i, node_id in enumerate(main_path):
            node = self.nodes.get(node_id)
            if node:
                prefix = "  -> " if i > 0 else ""
                lines.append(f"{prefix}{i+1}. {node_id}: {node.description}")
                if node.threshold:
                    lines.append(f"     Threshold: {node.threshold}")
                if node.filter_type:
                    lines.append(f"     Filter Type: {node.filter_type.value}")
        
        lines.append("")
        lines.append("FILTER STACK (in order):")
        lines.append("-" * 80)
        filter_order = [
            ('SYMBOL_TRADEABLE', FilterType.SYMBOL_AVAILABILITY),
            ('MARKET_CLOSING', FilterType.MARKET_TIMING),
            ('VOLUME_FILTER', FilterType.VOLUME),
            ('NEWS_FILTER', FilterType.NEWS),
            ('TREND_SIGNAL_VALID', FilterType.TREND_SIGNAL),
            ('RSI_FILTER', FilterType.RSI),
            ('HALAL_CHECK', FilterType.HALAL),
            ('SETUP_VALIDATION', FilterType.SETUP_VALIDATION),
            ('TREND_STRENGTH', FilterType.TREND_STRENGTH),
            ('QUALITY_SCORE', FilterType.QUALITY_SCORE),
            ('TIMING_GUARDS', FilterType.TIMING_GUARDS),
            ('PORTFOLIO_RISK', FilterType.RISK_CHECKS),
            ('CAN_OPEN_TRADE', FilterType.RISK_CHECKS),
            ('ENTRY_FILTERS', FilterType.ENTRY_FILTERS),
        ]
        
        for i, (node_id, filter_type) in enumerate(filter_order, 1):
            node = self.nodes.get(node_id)
            if node:
                lines.append(f"{i}. {filter_type.value.upper()}")
                lines.append(f"   Node: {node_id}")
                lines.append(f"   Description: {node.description}")
                if node.threshold:
                    lines.append(f"   Threshold: {node.threshold}")
                lines.append("")
        
        lines.append("IMPLICIT STRATEGIES:")
        lines.append("-" * 80)
        lines.append("Based on this graph, the following strategies are implicitly running:")
        lines.append("")
        lines.append("1. SMA20x50_TREND_FOLLOWING")
        lines.append("   - Entry: SMA20 > SMA50 (LONG) or SMA20 < SMA50 (SHORT)")
        lines.append("   - Filters: RSI 30-50, Quality >= 50, Trend strength >= 0.05%")
        lines.append("   - Timing: Blocks late trends, impulse exhaustion")
        lines.append("")
        lines.append("2. QUALITY_SCORED_SCALPING")
        lines.append("   - Entry: Quality score >= min_quality_score (default 50)")
        lines.append("   - Components: Trend (35), RSI (20), Volatility (10), Candle (15), Choppiness (20)")
        lines.append("   - Penalty: Spread cost (up to -15)")
        lines.append("")
        lines.append("3. RISK_MANAGED_ENTRY")
        lines.append("   - Portfolio risk limits")
        lines.append("   - Max open trades")
        lines.append("   - Entry filters (volatility, spread, candle quality)")
        lines.append("")
        
        return "\n".join(lines)
    
    def get_node_path(self, node_id: str) -> List[str]:
        """Get the path from START to a given node."""
        path = []
        current = node_id
        
        # Build reverse path
        visited = set()
        while current and current not in visited:
            visited.add(current)
            path.insert(0, current)
            # Find parent
            parent = None
            for nid, node in self.nodes.items():
                if node.pass_path == current or node.fail_path == current:
                    parent = nid
                    break
            current = parent
        
        return path
    
    def export_graph_json(self) -> Dict[str, Any]:
        """Export graph as JSON-serializable dictionary."""
        return {
            'nodes': {nid: asdict(node) for nid, node in self.nodes.items()},
            'graph_text': self.get_graph_text()
        }

