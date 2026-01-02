# Strategy Identification and Tracking System

Complete system for identifying, tracking, and improving trading strategies.

## Quick Start

```python
from strategies.strategy_tracking import StrategyTrackingSystem
from pathlib import Path

# Initialize
tracking = StrategyTrackingSystem(log_dir=Path("logs/live/strategies"))

# Log opportunity
strategy_id = tracking.log_opportunity(
    symbol="EURUSD",
    opportunity=opportunity,
    filter_results=filter_results,
    decision="REJECTED",
    rejection_reason="Quality score too low"
)

# Log execution
tracking.log_execution(
    symbol="EURUSD",
    ticket=12345,
    opportunity=opportunity,
    execution_result=execution_result
)

# Log closed trade
tracking.log_trade_closed(
    ticket=12345,
    close_time=datetime.now(),
    profit_usd=1.50,
    close_reason="TP"
)

# Get metrics
metrics = tracking.get_strategy_metrics(strategy_id)
print(f"Expectancy: ${metrics['expectancy_usd']:.2f}")
```

## Components

1. **strategy_fingerprint.py**: Assigns unique strategy_id to every opportunity
2. **strategy_graph.py**: Maps bot logic into decision graph
3. **performance_attribution.py**: Computes metrics per strategy
4. **improvement_loop.py**: Safe strategy evolution through shadow variants
5. **market_regime.py**: Regime-aware strategy adjustments
6. **strategy_tracking.py**: Integrated tracking system

## Documentation

See `STRATEGY_SYSTEM_DOCUMENTATION.md` for complete documentation.

## Examples

See `example_usage.py` for usage examples.

