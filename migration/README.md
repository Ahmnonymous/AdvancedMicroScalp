# Trading Bot Migration Guide

## Overview
This document describes the changes made to implement medium-frequency trading, multi-trade staged opens, Smart Elastic Trailing Engine (SETE), and improved logging.

## Configuration Changes

### New Config Keys Added

#### `risk.max_open_trades`
- **Type**: Integer (1-3)
- **Default**: 2
- **Description**: Maximum number of concurrent trades allowed. Supports up to 3 trades with staged open logic.

#### `risk.staged_open_enabled`
- **Type**: Boolean
- **Default**: true
- **Description**: Enable staged open logic for multiple trades on the same symbol.

#### `risk.staged_open_window_seconds`
- **Type**: Integer
- **Default**: 60
- **Description**: Time window (in seconds) within which additional staged trades can be opened after the first trade.

#### `risk.staged_quality_threshold`
- **Type**: Float (0-100)
- **Default**: 50.0
- **Description**: Minimum quality score required for staged trades (trade #2 and #3).

#### `risk.staged_min_profit_usd`
- **Type**: Float
- **Default**: -0.10
- **Description**: Minimum profit (or maximum loss) required on the first trade before allowing a second staged trade. Negative values allow small drawdowns.

#### `risk.fast_trailing_threshold_usd`
- **Type**: Float
- **Default**: 0.10
- **Description**: Profit threshold (in USD) above which fast polling is enabled for trailing stops.

#### `risk.fast_trailing_interval_ms`
- **Type**: Integer
- **Default**: 300
- **Description**: Fast polling interval (in milliseconds) for positions with profit >= fast_trailing_threshold_usd.

#### `risk.fast_trailing_debounce_cycles`
- **Type**: Integer
- **Default**: 3
- **Description**: Number of cycles below threshold before disabling fast polling.

#### `risk.elastic_trailing.enabled`
- **Type**: Boolean
- **Default**: true
- **Description**: Enable Smart Elastic Trailing Engine (SETE).

#### `risk.elastic_trailing.pullback_tolerance_pct`
- **Type**: Float (0-1)
- **Default**: 0.40
- **Description**: Percentage of peak profit allowed as pullback before SL is adjusted. 0.40 = 40% pullback tolerance.

#### `risk.elastic_trailing.min_lock_increment_usd`
- **Type**: Float
- **Default**: 0.10
- **Description**: Base increment for floor lock calculation.

#### `risk.elastic_trailing.big_jump_threshold_usd`
- **Type**: Float
- **Default**: 0.40
- **Description**: Profit increase threshold (in USD) that triggers immediate SL lock.

#### `risk.elastic_trailing.big_jump_lock_margin_usd`
- **Type**: Float
- **Default**: 0.10
- **Description**: Margin below peak profit to lock SL when big jump is detected.

#### `risk.elastic_trailing.max_peak_lock_usd`
- **Type**: Float
- **Default**: 0.80
- **Description**: Maximum SL lock when profit >= 1.0 USD.

#### `trading.cycle_interval_seconds`
- **Type**: Integer
- **Default**: 30 (changed from 60)
- **Description**: Main trading cycle interval in seconds (medium-frequency).

#### `trading.randomness_factor`
- **Type**: Float (0-1)
- **Default**: 0.05 (changed from 0.1)
- **Description**: Probability of skipping a trade even when conditions are met. Lower = more aggressive.

#### `logging.root_level`
- **Type**: String
- **Default**: "INFO"
- **Description**: Log level for root logger (bot_log.txt). Only critical events are logged.

#### `logging.symbol_log_level`
- **Type**: String
- **Default**: "DEBUG"
- **Description**: Log level for symbol-specific loggers (logs/symbols/{SYMBOL}_YYYY-MM-DD.log).

#### `logging.symbol_log_dir`
- **Type**: String
- **Default**: "logs/symbols"
- **Description**: Directory for symbol-specific log files.

## How Staged Open Works

### Parameters
- `staged_open_enabled`: Enable/disable staged opens
- `staged_open_window_seconds`: Time window for staged trades (default: 60s)
- `staged_quality_threshold`: Minimum quality score (default: 50.0)
- `staged_min_profit_usd`: Minimum profit on first trade (default: -0.10)

### Logic Flow

1. **Trade #1**: Opens when a valid opportunity passes all checks (normal flow).

2. **Trade #2**: Allowed only if:
   - Within `staged_open_window_seconds` of trade #1 OR same cycle
   - Trend direction matches trade #1
   - Spread for symbol is still acceptable
   - `quality_score >= staged_quality_threshold`
   - Trade #1 profit >= `staged_min_profit_usd` (can be negative to allow averaging)

3. **Trade #3**: Same checks as trade #2, plus:
   - Either trade #1 or trade #2 has at least +$0.05 profit OR breakeven
   - Trend continues in same direction

### Example
```
10:00:00 - Trade #1 opened: EURUSD LONG, Ticket 1001
10:00:15 - Trade #2 opened: EURUSD LONG, Ticket 1002 (within 60s window, trend matches)
10:00:45 - Trade #3 blocked: Window expired (45s < 60s but trend check failed)
```

## How Elastic Trailing Works

### Parameters
- `pullback_tolerance_pct`: 40% default (allows 40% retracement from peak)
- `min_lock_increment_usd`: $0.10 base increment
- `big_jump_threshold_usd`: $0.40 threshold for big jump detection
- `max_peak_lock_usd`: $0.80 maximum lock when profit >= $1.0

### Behavior

1. **Peak Tracking**: System tracks the highest profit (`peak_profit`) for each position.

2. **Pullback Tolerance**: When profit decreases from peak:
   - If `current_profit >= peak_profit * (1 - pullback_tolerance_pct)`: SL is NOT moved down
   - Example: Peak $0.56, tolerance 40% → allowed pullback = $0.56 * 0.40 = $0.224
   - If profit drops to $0.34, it's still above $0.336 (peak - pullback), so SL maintained

3. **Elastic SL Calculation**:
   ```
   floor_lock = floor(profit / min_lock_increment) * min_lock_increment - min_lock_increment
   allowed_pullback = peak_profit * pullback_tolerance_pct
   elastic_lock = max(floor_lock, peak_profit - allowed_pullback)
   ```

4. **Big Jump Detection**: If profit increases by > `big_jump_threshold_usd`:
   - Immediately lock SL to `peak_profit - big_jump_lock_margin_usd`
   - Example: Profit jumps from $0.20 to $0.70 (+$0.50) → Lock SL at $0.60

5. **Max Peak Lock**: When `peak_profit >= 1.0`:
   - SL is locked at minimum `max_peak_lock_usd` ($0.80)

### Example Sequence
```
Profit: $0.56 → Peak: $0.56, SL: $0.46 (elastic lock)
Profit: $0.34 → Peak: $0.56, SL: $0.46 (maintained, within pullback tolerance)
Profit: $0.88 → Peak: $0.88, SL: $0.78 (big jump detected, immediate lock)
Profit: $0.64 → Peak: $0.88, SL: $0.78 (maintained, within pullback tolerance)
Profit: $1.10 → Peak: $1.10, SL: $0.80 (max peak lock applied)
```

## Logging Structure

### Root Logs (`bot_log.txt`)
Minimal logging - only critical events:
- Trade executed (entry)
- Trailing SL adjustments (every change)
- Trade closed (profit/loss)
- Kill switch changes
- Major errors

### Symbol Logs (`logs/symbols/{SYMBOL}_YYYY-MM-DD.log`)
Detailed DEBUG-level logs per symbol:
- All symbol analysis steps
- Trend signals
- Spread checks
- Quality scores
- Detailed trailing stop calculations
- All symbol-specific events

### Example
```
# bot_log.txt (root)
2024-01-15 10:00:00 - INFO - ✅ TRADE EXECUTED: EURUSD LONG | Ticket: 1001 | Entry: 1.10000 | Lot: 0.01 | SL: 10.0pips
2024-01-15 10:00:15 - INFO - 📈 SL ADJUSTED: EURUSD Ticket 1001 | Profit: $0.56 → SL: $0.46

# logs/symbols/EURUSD_2024-01-15.log (symbol)
2024-01-15 10:00:00 - DEBUG - 📊 Analyzing EURUSD...
2024-01-15 10:00:00 - DEBUG - ✅ EURUSD: No news blocking
2024-01-15 10:00:00 - INFO - ✅ TRADE EXECUTED SUCCESSFULLY
2024-01-15 10:00:00 - INFO -    Symbol: EURUSD
2024-01-15 10:00:00 - INFO -    Ticket: 1001
...
```

## Fast P/L Reaction

### Fast Polling Mode
When any position has `profit >= fast_trailing_threshold_usd` (default $0.10):
- Fast polling thread checks profit every `fast_trailing_interval_ms` (default 300ms)
- Normal polling continues at 3.0s interval for positions below threshold
- When profit drops below threshold for `fast_trailing_debounce_cycles` (default 3), fast polling is disabled

### Benefits
- Ultra-fast SL adjustments when profit is active
- Reduces unnecessary polling for positions with low/no profit
- Adaptive: automatically enables/disables based on profit level

## Backward Compatibility

All new features are **opt-in** via configuration:
- If `staged_open_enabled = false`, behavior matches previous version (max 1 trade)
- If `elastic_trailing.enabled = false`, uses standard incremental trailing
- If `fast_trailing_threshold_usd` is set very high, fast polling is effectively disabled

## Migration Steps

1. **Backup current config.json**
   ```bash
   cp config.json config.json.backup
   ```

2. **Update config.json** with new keys (see examples above)

3. **Test in test mode first**:
   ```bash
   python launch_system.py --test-mode
   ```

4. **Monitor logs**:
   - Check `bot_log.txt` for critical events
   - Check `logs/symbols/` for detailed per-symbol activity

5. **Run tests**:
   ```bash
   python tests/run_all_tests.py
   ```

6. **Gradually enable features**:
   - Start with `max_open_trades = 2`
   - Enable `staged_open_enabled = true`
   - Enable `elastic_trailing.enabled = true`
   - Monitor for 1 hour before increasing to 3 trades

## Troubleshooting

### Staged trades not opening
- Check `staged_open_window_seconds` is sufficient
- Verify `staged_quality_threshold` is not too high
- Check first trade profit meets `staged_min_profit_usd`

### SL not adjusting fast enough
- Verify `fast_trailing_threshold_usd` is set appropriately
- Check `fast_trailing_interval_ms` is not too high
- Ensure `elastic_trailing.enabled = true`

### Too many logs
- Reduce `symbol_log_level` to "INFO" instead of "DEBUG"
- Check `root_level` is "INFO" (not "DEBUG")

## Safety Features Preserved

All safety features remain active:
- ✅ News filter
- ✅ Stop-loss validation
- ✅ Kill switch
- ✅ Halal compliance checks
- ✅ Thread-safety
- ✅ Max risk per trade enforcement

