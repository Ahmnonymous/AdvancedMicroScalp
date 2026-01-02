# Strategy Identification and Tracking System

## Overview

This system provides complete visibility into trading strategy behavior, enabling:
- **Strategy Identification**: Every opportunity gets a unique `strategy_id`
- **Performance Attribution**: Metrics computed per strategy
- **Safe Evolution**: Shadow variants and A/B testing
- **Regime Awareness**: Strategies adapt to market conditions

## 1. Strategy Fingerprinting

### Purpose
Assigns a unique `strategy_id` to every trade opportunity, even when filters reject it.

### Naming Convention
```
strategy_id = "{direction}_{entry_condition_cluster}_{filter_stack_hash}"
```

### Example IDs
- `LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1`
- `SHORT_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1`

### Components
1. **Direction**: `LONG` | `SHORT`
2. **Entry Cluster**: Primary entry logic (e.g., `SMA20x50`)
3. **Filter Hash**: Abbreviated filter configuration
   - `RSI{min}-{max}`: RSI entry range
   - `Q{bucket}`: Quality score bucket
   - `F{flags}`: Filter flags (V=volatility, C=candle, S=spread, T=trend, I=timing)

### Usage
```python
from strategies.strategy_fingerprint import StrategyFingerprint

fingerprint = StrategyFingerprint()

# Generate strategy_id for opportunity
strategy_id = fingerprint.generate_strategy_id(opportunity, filter_results)

# Log opportunity with fingerprint
log_entry = fingerprint.log_opportunity_fingerprint(
    symbol="EURUSD",
    opportunity=opportunity,
    filter_results=filter_results,
    decision="REJECTED",
    rejection_reason="Quality score too low"
)
```

## 2. Strategy Decision Graph

### Purpose
Maps bot logic into an explicit decision graph showing all evaluation paths.

### Graph Structure
```
START → SYMBOL_DISCOVERY → SYMBOL_LOOP → SYMBOL_TRADEABLE
  ↓
MARKET_CLOSING → VOLUME_FILTER → NEWS_FILTER → TREND_SIGNAL
  ↓
RSI_FILTER → HALAL_CHECK → SETUP_VALIDATION → QUALITY_SCORE
  ↓
TIMING_GUARDS → PORTFOLIO_RISK → ENTRY_FILTERS → EXECUTION
```

### Filter Stack (in order)
1. **SYMBOL_AVAILABILITY**: Symbol tradeable now?
2. **MARKET_TIMING**: Market closing filter
3. **VOLUME**: Volume/liquidity sufficient?
4. **NEWS**: High-impact news blocking?
5. **TREND_SIGNAL**: SMA20 vs SMA50 signal
6. **RSI**: RSI in entry range (30-50)?
7. **HALAL**: Halal compliance
8. **SETUP_VALIDATION**: Setup valid for scalping
9. **TREND_STRENGTH**: SMA separation >= 0.05%
10. **QUALITY_SCORE**: Quality score >= 50
11. **TIMING_GUARDS**: Trend maturity, impulse exhaustion
12. **RISK_CHECKS**: Portfolio risk, max open trades
13. **ENTRY_FILTERS**: Volatility, spread, candle quality

### Usage
```python
from strategies.strategy_graph import StrategyGraphMapper

mapper = StrategyGraphMapper()

# Get textual graph representation
graph_text = mapper.get_graph_text()
print(graph_text)

# Export as JSON
graph_json = mapper.export_graph_json()
```

## 3. Performance Attribution

### Purpose
Computes performance metrics per strategy to answer:
- Which strategies are being evaluated?
- Which ones are executing?
- Which ones are profitable?

### Metrics Computed
- **Evaluated Count**: Opportunities evaluated
- **Executed Count**: Trades executed
- **Execution Rate**: Executed / Evaluated
- **Win Rate**: Winning trades / Total closed
- **Expectancy**: (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
- **Profit Factor**: Total Profit / Total Loss
- **Max Drawdown**: Maximum peak-to-trough decline
- **Sharpe Ratio**: Risk-adjusted return (if enough data)

### Validity Rules
A strategy is considered **valid** if:
1. **Sample Size**: >= 30 closed trades
2. **Execution Rate**: >= 5% (executed / evaluated)
3. **Expectancy**: >= $0.00 USD

### Usage
```python
from strategies.performance_attribution import StrategyPerformanceAttribution

attribution = StrategyPerformanceAttribution(
    min_sample_size=30,
    min_execution_rate=0.05,
    min_expectancy_usd=0.0
)

# Record opportunity
attribution.record_opportunity(
    strategy_id="LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
    symbol="EURUSD",
    timestamp=datetime.now(),
    decision="REJECTED",
    opportunity_data=opportunity,
    rejection_reason="Quality score too low"
)

# Record execution
attribution.record_execution(
    strategy_id="LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
    symbol="EURUSD",
    ticket=12345,
    timestamp=datetime.now(),
    execution_data=execution_result
)

# Record closed trade
attribution.record_trade_closed(
    ticket=12345,
    close_time=datetime.now(),
    profit_usd=1.50,
    close_reason="TP"
)

# Get metrics
metrics = attribution.compute_strategy_metrics("LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1")
print(f"Expectancy: ${metrics['expectancy_usd']:.2f}")
print(f"Win Rate: {metrics['win_rate']:.1%}")

# Get ranking
ranking = attribution.get_strategy_ranking(sort_by='expectancy_usd')
```

## 4. Strategy Improvement Loop

### Purpose
Enables safe strategy evolution through shadow variants and A/B testing.

### Workflow
1. **Create Shadow Variant**: Test single-variable modification
2. **Evaluate Gates**: Check promotion criteria
3. **Promote**: Move to live if gates passed
4. **Demote**: Disable if performance degrades

### Promotion Gates
1. **Sample Size**: >= 50 trades in shadow mode
2. **Expectancy**: >= base expectancy + 10% improvement
3. **Win Rate**: >= 45%
4. **Drawdown**: <= $20 USD
5. **Consistency**: Stable performance over 7 days

### Constraints
- **One Variable**: Only one parameter changed per variant
- **No Live Changes**: All modifications tested in shadow first
- **Gate Validation**: Must pass all gates before promotion

### Usage
```python
from strategies.improvement_loop import StrategyImprovementLoop, StrategyStatus

improvement_loop = StrategyImprovementLoop(
    min_shadow_trades=50,
    min_expectancy_improvement_pct=10.0,
    min_win_rate=0.45
)

# Create shadow variant
variant = improvement_loop.create_shadow_variant(
    base_strategy_id="LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
    modification={'min_quality_score': 55.0}  # Increase by 5 points
)

# Evaluate gates (after shadow trades executed)
gate_results = improvement_loop.evaluate_gates(
    variant_id=variant.variant_id,
    performance_metrics=metrics
)

# Check if ready for promotion
ready, reasons = improvement_loop.check_promotion_ready(variant.variant_id)
if ready:
    improvement_loop.promote_variant(variant.variant_id)

# Get suggestions
suggestions = improvement_loop.get_modification_suggestions("LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1")
```

## 5. Market Regime Awareness

### Purpose
Enables/disables strategies and adjusts parameters based on market conditions.

### Regime Types
- **TRENDING**: Strong directional movement
- **RANGING**: Sideways, choppy
- **HIGH_VOLATILITY**: Elevated volatility
- **LOW_VOLATILITY**: Low volatility
- **WIDE_SPREAD**: Wide spreads
- **NARROW_SPREAD**: Narrow spreads
- **NEWS_EVENT**: News-driven
- **SESSION_OPEN**: Session opening
- **SESSION_CLOSE**: Session closing

### Adjustments
- **Enabled/Disabled**: Strategy on/off
- **Scale Factor**: Position sizing (0.0 to 1.0)
- **Throttle Factor**: Execution frequency (0.0 to 1.0)
- **Parameter Adjustments**: Dynamic parameter changes

### Usage
```python
from strategies.market_regime import MarketRegimeDetector

regime_detector = MarketRegimeDetector(
    volatility_threshold_high=0.02,  # 2% ATR
    volatility_threshold_low=0.005,  # 0.5% ATR
    spread_threshold_wide=5.0,  # 5 points
    spread_threshold_narrow=1.0  # 1 point
)

# Detect regime
market_data = {
    'atr_pct': 0.015,
    'spread_points': 2.5,
    'sma_separation_pct': 0.08,
    'choppiness': 0.3,
    'session_hour': 14,
    'news_active': False
}

regimes = regime_detector.detect_regime("EURUSD", market_data)

# Get strategy adjustments
adjustments = regime_detector.get_strategy_adjustments(
    symbol="EURUSD",
    strategy_id="LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
    base_config={'min_quality_score': 50.0}
)

print(f"Enabled: {adjustments['enabled']}")
print(f"Scale Factor: {adjustments['scale_factor']}")
print(f"Throttle Factor: {adjustments['throttle_factor']}")
print(f"Reason: {adjustments['reason']}")
```

## 6. Complete Integration

### Usage
```python
from strategies.strategy_tracking import StrategyTrackingSystem
from pathlib import Path

# Initialize system
tracking = StrategyTrackingSystem(
    log_dir=Path("logs/live/strategies"),
    min_sample_size=30,
    min_execution_rate=0.05
)

# Log opportunity (evaluated but rejected)
strategy_id = tracking.log_opportunity(
    symbol="EURUSD",
    opportunity=opportunity,
    filter_results=filter_results,
    decision="REJECTED",
    rejection_reason="Quality score too low"
)

# Log execution
strategy_id = tracking.log_execution(
    symbol="EURUSD",
    ticket=12345,
    opportunity=opportunity,
    execution_result=execution_result,
    filter_results=filter_results
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

# Generate report
report = tracking.generate_performance_report()
```

## Integration with Trading Bot

### Step 1: Initialize in TradingBot
```python
# In bot/trading_bot.py __init__
from strategies.strategy_tracking import StrategyTrackingSystem

self.strategy_tracking = StrategyTrackingSystem(
    log_dir=Path("logs/live/strategies"),
    min_sample_size=30
)
```

### Step 2: Log Opportunities in scan_for_opportunities
```python
# In bot/trading_bot.py scan_for_opportunities

# Before each filter check, collect filter results
filter_results = {}

# After quality score check
if quality_score < min_quality_score:
    filter_results['quality_score'] = {'passed': False, 'value': quality_score}
    strategy_id = self.strategy_tracking.log_opportunity(
        symbol=symbol,
        opportunity=opportunity_data,
        filter_results=filter_results,
        decision="REJECTED",
        rejection_reason=f"Quality score {quality_score:.1f} < {min_quality_score}"
    )
    continue
else:
    filter_results['quality_score'] = {'passed': True, 'value': quality_score}

# When opportunity is created
if opportunity_created:
    strategy_id = self.strategy_tracking.log_opportunity(
        symbol=symbol,
        opportunity=opportunity_data,
        filter_results=filter_results,
        decision="EXECUTED"
    )
```

### Step 3: Log Executions
```python
# In bot/trading_bot.py execute_trade

# After successful execution
strategy_id = self.strategy_tracking.log_execution(
    symbol=symbol,
    ticket=ticket,
    opportunity=opportunity,
    execution_result=execution_result,
    filter_results=filter_results
)
```

### Step 4: Log Closed Trades
```python
# In position monitor or SL/TP handler

# When trade closes
self.strategy_tracking.log_trade_closed(
    ticket=ticket,
    close_time=close_time,
    profit_usd=profit_usd,
    close_reason=close_reason
)
```

## Log Files

All logs are written to `logs/live/strategies/`:

- **opportunities.jsonl**: Every opportunity evaluation
- **executions.jsonl**: Every trade execution
- **closed_trades.jsonl**: Every closed trade with P&L
- **performance_report_*.json**: Periodic performance reports

## Example Log Entry

### Opportunity Log
```json
{
  "timestamp": "2024-01-15T10:30:00",
  "symbol": "EURUSD",
  "strategy_id": "LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
  "decision": "REJECTED",
  "rejection_reason": "Quality score 45.0 < 50.0",
  "opportunity": {
    "signal": "LONG",
    "quality_score": 45.0,
    "trend_strength": 0.08,
    "rsi": 42.0,
    "sma_fast": 1.08500,
    "sma_slow": 1.08450
  },
  "filters": {
    "quality_score": {"passed": false, "value": 45.0}
  },
  "market_conditions": {
    "spread_points": 2.0,
    "atr": 0.00015
  }
}
```

## Performance Report Example

```json
{
  "generated_at": "2024-01-15T12:00:00",
  "lookback_days": 30,
  "strategies": {
    "LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1": {
      "strategy_id": "LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
      "valid": true,
      "evaluated_count": 150,
      "executed_count": 45,
      "execution_rate": 0.30,
      "closed_count": 42,
      "win_rate": 0.57,
      "total_profit_usd": 38.50,
      "total_loss_usd": 12.00,
      "expectancy_usd": 0.63,
      "avg_win_usd": 1.61,
      "avg_loss_usd": 0.86,
      "max_drawdown_usd": 5.20,
      "profit_factor": 3.21,
      "sharpe_ratio": 1.85
    }
  },
  "ranking": [
    {
      "strategy_id": "LONG_SMA20x50_RSI30-50_Q50+_FV1C1S1T1I1",
      "expectancy_usd": 0.63,
      "win_rate": 0.57
    }
  ]
}
```

## Next Steps

1. **Integrate into bot**: Add logging calls in `scan_for_opportunities` and `execute_trade`
2. **Generate reports**: Run `generate_performance_report()` daily
3. **Analyze results**: Review strategy metrics and identify improvements
4. **Create shadow variants**: Test modifications safely
5. **Promote winners**: Move successful variants to live

