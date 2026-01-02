"""
Example Usage of Strategy Tracking System

This demonstrates how to integrate strategy tracking into the trading bot.
"""

from datetime import datetime
from pathlib import Path
from strategies.strategy_tracking import StrategyTrackingSystem
from strategies.strategy_graph import StrategyGraphMapper


def example_opportunity_logging():
    """Example: Log opportunity evaluations."""
    tracking = StrategyTrackingSystem(log_dir=Path("logs/live/strategies"))
    
    # Simulate opportunity evaluation
    opportunity = {
        'signal': 'LONG',
        'quality_score': 65.0,
        'trend_strength': 0.08,
        'rsi': 42.0,
        'sma_fast': 1.08500,
        'sma_slow': 1.08450,
        'atr': 0.00015,
        'spread_points': 2.0,
        'min_quality_score': 50.0,
        'rsi_entry_range_min': 30.0,
        'rsi_entry_range_max': 50.0
    }
    
    filter_results = {
        'quality_score': {'passed': True, 'value': 65.0},
        'volatility_floor': {'passed': True},
        'candle_quality': {'passed': True},
        'spread_sanity': {'passed': True},
        'trend_gate': {'passed': True},
        'timing_guards': {'passed': True}
    }
    
    # Log opportunity (executed)
    strategy_id = tracking.log_opportunity(
        symbol="EURUSD",
        opportunity=opportunity,
        filter_results=filter_results,
        decision="EXECUTED"
    )
    
    print(f"Strategy ID: {strategy_id}")
    
    # Log opportunity (rejected)
    opportunity_rejected = opportunity.copy()
    opportunity_rejected['quality_score'] = 45.0
    
    filter_results_rejected = filter_results.copy()
    filter_results_rejected['quality_score'] = {'passed': False, 'value': 45.0}
    
    strategy_id_rejected = tracking.log_opportunity(
        symbol="EURUSD",
        opportunity=opportunity_rejected,
        filter_results=filter_results_rejected,
        decision="REJECTED",
        rejection_reason="Quality score 45.0 < 50.0"
    )
    
    print(f"Rejected Strategy ID: {strategy_id_rejected}")


def example_execution_logging():
    """Example: Log trade executions."""
    tracking = StrategyTrackingSystem(log_dir=Path("logs/live/strategies"))
    
    opportunity = {
        'signal': 'LONG',
        'quality_score': 65.0,
        'trend_strength': 0.08,
        'rsi': 42.0,
        'sma_fast': 1.08500,
        'sma_slow': 1.08450,
        'atr': 0.00015,
        'spread_points': 2.0
    }
    
    execution_result = {
        'entry_price_actual': 1.08520,
        'lot_size': 0.01,
        'risk_usd': 2.00,
        'quality_score': 65.0
    }
    
    strategy_id = tracking.log_execution(
        symbol="EURUSD",
        ticket=12345,
        opportunity=opportunity,
        execution_result=execution_result
    )
    
    print(f"Execution logged for Strategy ID: {strategy_id}")


def example_performance_analysis():
    """Example: Analyze strategy performance."""
    tracking = StrategyTrackingSystem(log_dir=Path("logs/live/strategies"))
    
    # Simulate some trades
    strategy_id = "LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1"
    
    # Record some closed trades
    for i in range(10):
        tracking.log_trade_closed(
            ticket=12340 + i,
            close_time=datetime.now(),
            profit_usd=1.50 if i % 2 == 0 else -0.80,
            close_reason="TP" if i % 2 == 0 else "SL"
        )
    
    # Get metrics
    metrics = tracking.get_strategy_metrics(strategy_id)
    
    print(f"Strategy: {metrics['strategy_id']}")
    print(f"Valid: {metrics['valid']}")
    print(f"Evaluated: {metrics['evaluated_count']}")
    print(f"Executed: {metrics['executed_count']}")
    print(f"Execution Rate: {metrics['execution_rate']:.1%}")
    print(f"Win Rate: {metrics['win_rate']:.1%}")
    print(f"Expectancy: ${metrics['expectancy_usd']:.2f}")
    print(f"Profit Factor: {metrics['profit_factor']:.2f}")


def example_strategy_graph():
    """Example: Generate strategy decision graph."""
    mapper = StrategyGraphMapper()
    
    # Get textual representation
    graph_text = mapper.get_graph_text()
    print(graph_text)
    
    # Export as JSON
    graph_json = mapper.export_graph_json()
    print(f"\nGraph exported with {len(graph_json['nodes'])} nodes")


def example_improvement_loop():
    """Example: Create shadow variant for testing."""
    from strategies.improvement_loop import StrategyImprovementLoop
    
    improvement_loop = StrategyImprovementLoop()
    
    # Create shadow variant
    variant = improvement_loop.create_shadow_variant(
        base_strategy_id="LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
        modification={'min_quality_score': 55.0}  # Increase by 5 points
    )
    
    print(f"Created shadow variant: {variant.variant_id}")
    print(f"Status: {variant.status.value}")
    print(f"Modification: {variant.modification}")
    
    # Get suggestions
    suggestions = improvement_loop.get_modification_suggestions(
        "LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1"
    )
    
    print("\nSuggested modifications:")
    for suggestion in suggestions:
        print(f"  - {suggestion['description']}")
        print(f"    Variable: {suggestion['variable']}")
        print(f"    Modification: {suggestion['modification']}")


def example_market_regime():
    """Example: Detect market regime and get adjustments."""
    from strategies.market_regime import MarketRegimeDetector
    
    regime_detector = MarketRegimeDetector()
    
    # Simulate market data
    market_data = {
        'atr_pct': 0.015,  # 1.5% ATR
        'spread_points': 2.5,
        'sma_separation_pct': 0.08,  # 0.08% separation
        'choppiness': 0.3,  # Low choppiness (trending)
        'session_hour': 14,
        'news_active': False
    }
    
    # Detect regime
    regimes = regime_detector.detect_regime("EURUSD", market_data)
    
    print("Detected regimes:")
    for regime in regimes:
        print(f"  - {regime.regime_type.value}: {regime.confidence:.1%} confidence")
    
    # Get strategy adjustments
    adjustments = regime_detector.get_strategy_adjustments(
        symbol="EURUSD",
        strategy_id="LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
        base_config={'min_quality_score': 50.0}
    )
    
    print(f"\nStrategy Adjustments:")
    print(f"  Enabled: {adjustments['enabled']}")
    print(f"  Scale Factor: {adjustments['scale_factor']:.2f}")
    print(f"  Throttle Factor: {adjustments['throttle_factor']:.2f}")
    print(f"  Reason: {adjustments['reason']}")


if __name__ == "__main__":
    print("=" * 80)
    print("STRATEGY TRACKING SYSTEM - EXAMPLES")
    print("=" * 80)
    
    print("\n1. Opportunity Logging")
    print("-" * 80)
    example_opportunity_logging()
    
    print("\n2. Execution Logging")
    print("-" * 80)
    example_execution_logging()
    
    print("\n3. Strategy Graph")
    print("-" * 80)
    example_strategy_graph()
    
    print("\n4. Improvement Loop")
    print("-" * 80)
    example_improvement_loop()
    
    print("\n5. Market Regime")
    print("-" * 80)
    example_market_regime()
    
    print("\n6. Performance Analysis")
    print("-" * 80)
    example_performance_analysis()

