# Trading Bot Runbook

## Quick Start

### Starting the Bot

```bash
python launch_system.py
```

The bot will:
1. Connect to MT5
2. Initialize all modules
3. Start trading cycle (30s interval)
4. Start trailing stop monitors (normal: 3s, fast: 300ms)
5. Display real-time monitor in console

### Starting in Test Mode

```bash
python launch_system.py --test-mode
```

Test mode:
- Tests ALL available symbols (ignores spread/exotic/halal restrictions)
- Still applies trend/RSI/quality filters
- Useful for testing staged opens and trailing logic

## Configuration

### Key Settings

**Medium-Frequency Trading:**
```json
{
  "trading": {
    "cycle_interval_seconds": 30,
    "randomness_factor": 0.05
  }
}
```

**Multi-Trade (Staged Opens):**
```json
{
  "risk": {
    "max_open_trades": 2,
    "staged_open_enabled": true,
    "staged_open_window_seconds": 60,
    "staged_quality_threshold": 50.0,
    "staged_min_profit_usd": -0.10
  }
}
```

**Smart Elastic Trailing:**
```json
{
  "risk": {
    "elastic_trailing": {
      "enabled": true,
      "pullback_tolerance_pct": 0.40,
      "min_lock_increment_usd": 0.10,
      "big_jump_threshold_usd": 0.40,
      "big_jump_lock_margin_usd": 0.10,
      "max_peak_lock_usd": 0.80
    }
  }
}
```

**Fast P/L Reaction:**
```json
{
  "risk": {
    "fast_trailing_threshold_usd": 0.10,
    "fast_trailing_interval_ms": 300,
    "fast_trailing_debounce_cycles": 3
  }
}
```

## Monitoring

### Console Monitor
The launch system displays:
- Open positions with live P/L
- Recent trailing stop adjustments
- Big jump detections
- Trade executions
- Account status

### Log Files

**Root Log (`bot_log.txt`):**
- Minimal critical events only
- Trade entries/exits
- SL adjustments
- Kill switch changes

**Symbol Logs (`logs/symbols/{SYMBOL}_YYYY-MM-DD.log`):**
- Detailed DEBUG logs per symbol
- All analysis steps
- Trend signals
- Quality scores
- Detailed trailing calculations

### Viewing Logs

```bash
# Watch root log (critical events)
tail -f bot_log.txt

# Watch specific symbol log
tail -f logs/symbols/EURUSD_2024-01-15.log

# Search for trades
grep "TRADE EXECUTED" bot_log.txt

# Search for SL adjustments
grep "SL ADJUSTED" bot_log.txt
```

## Kill Switch

### Activating Kill Switch

The kill switch activates automatically when:
- Too many consecutive errors (default: 5)
- Manual activation via code

### Resetting Kill Switch

**Method 1: Restart bot**
- Kill switch resets on restart

**Method 2: Programmatic reset**
```python
from bot.trading_bot import TradingBot
bot = TradingBot('config.json')
bot.reset_kill_switch()
```

**Method 3: Edit config (not recommended)**
- Kill switch state is in memory, not config
- Restart required

### Checking Kill Switch Status

Check `bot_log.txt` for:
```
KILL SWITCH ACTIVE - Trading halted
```

Or check console monitor - it will show kill switch status.

## Testing

### Run Unit Tests

```bash
python tests/run_all_tests.py
```

Tests:
- `test_trailing_behavior.py`: SETE logic simulation
- `test_staged_open.py`: Staged open logic

### Test Mode

```bash
python launch_system.py --test-mode
```

Test mode:
- Ignores spread/exotic/halal restrictions
- Still applies trend/quality filters
- Useful for testing logic without real restrictions

### Verification Checklist

After starting bot:
- [ ] MT5 connection established
- [ ] Trailing stop threads started (normal + fast)
- [ ] Symbol logs directory created
- [ ] Root log file created
- [ ] No errors in initial logs
- [ ] Monitor displays correctly

## Troubleshooting

### Bot Not Trading

1. **Check kill switch:**
   ```bash
   grep "KILL SWITCH" bot_log.txt
   ```

2. **Check connection:**
   ```bash
   grep "MT5 connection" bot_log.txt
   ```

3. **Check opportunities:**
   ```bash
   grep "OPPORTUNITY FOUND" bot_log.txt
   ```

4. **Check staged open settings:**
   - Verify `max_open_trades >= 1`
   - Check `staged_open_enabled` if expecting multiple trades

### SL Not Adjusting

1. **Check trailing enabled:**
   ```json
   "continuous_trailing_enabled": true
   ```

2. **Check profit threshold:**
   - SL only adjusts when profit >= `min_lock_increment_usd` (default $0.10)

3. **Check fast polling:**
   - Verify `fast_trailing_threshold_usd` is set appropriately
   - Check symbol logs for trailing stop calculations

4. **Check elastic trailing:**
   - Verify `elastic_trailing.enabled = true`
   - Check pullback tolerance settings

### Too Many/Few Trades

1. **Adjust randomness:**
   ```json
   "randomness_factor": 0.05  // Lower = more trades
   ```

2. **Adjust cycle interval:**
   ```json
   "cycle_interval_seconds": 30  // Lower = more frequent scans
   ```

3. **Check quality threshold:**
   ```json
   "staged_quality_threshold": 50.0  // Lower = more staged trades
   ```

### Log Files Too Large

1. **Reduce symbol log level:**
   ```json
   "symbol_log_level": "INFO"  // Instead of "DEBUG"
   ```

2. **Archive old logs:**
   ```bash
   mkdir logs/archive
   mv logs/symbols/*.log logs/archive/
   ```

## Performance Tuning

### Medium-Frequency Settings

**Aggressive (more trades):**
```json
{
  "trading": {
    "cycle_interval_seconds": 20,
    "randomness_factor": 0.02
  }
}
```

**Conservative (fewer trades):**
```json
{
  "trading": {
    "cycle_interval_seconds": 45,
    "randomness_factor": 0.10
  }
}
```

### Fast Trailing Settings

**Ultra-Fast (more responsive):**
```json
{
  "risk": {
    "fast_trailing_threshold_usd": 0.05,
    "fast_trailing_interval_ms": 200
  }
}
```

**Balanced (default):**
```json
{
  "risk": {
    "fast_trailing_threshold_usd": 0.10,
    "fast_trailing_interval_ms": 300
  }
}
```

### Elastic Trailing Settings

**Tight (less pullback tolerance):**
```json
{
  "risk": {
    "elastic_trailing": {
      "pullback_tolerance_pct": 0.20  // 20% tolerance
    }
  }
}
```

**Loose (more pullback tolerance):**
```json
{
  "risk": {
    "elastic_trailing": {
      "pullback_tolerance_pct": 0.60  // 60% tolerance
    }
  }
}
```

## Maintenance

### Daily Tasks

1. **Check logs:**
   ```bash
   tail -n 100 bot_log.txt
   ```

2. **Review positions:**
   - Check console monitor
   - Verify SL adjustments are working

3. **Archive logs:**
   ```bash
   # Archive yesterday's logs
   date=$(date -d yesterday +%Y-%m-%d)
   mkdir -p logs/archive
   mv logs/symbols/*_${date}.log logs/archive/
   ```

### Weekly Tasks

1. **Review performance:**
   - Analyze trade statistics
   - Check trailing stop effectiveness
   - Review staged open success rate

2. **Clean up logs:**
   ```bash
   # Keep only last 7 days
   find logs/symbols -name "*.log" -mtime +7 -delete
   ```

3. **Update config if needed:**
   - Adjust thresholds based on performance
   - Fine-tune elastic trailing parameters

## Emergency Procedures

### Stop All Trading

1. **Kill switch (automatic):**
   - Bot activates kill switch on errors
   - Check logs for reason

2. **Manual stop:**
   - Press `Ctrl+C` in console
   - Bot will close gracefully

3. **Force stop:**
   ```bash
   pkill -f launch_system.py
   ```

### Close All Positions

Kill switch automatically closes all positions. To manually close:

```python
from execution.order_manager import OrderManager
from execution.mt5_connector import MT5Connector
import json

with open('config.json') as f:
    config = json.load(f)

connector = MT5Connector(config)
order_manager = OrderManager(connector)
connector.connect()

positions = order_manager.get_open_positions()
for pos in positions:
    order_manager.close_position(pos['ticket'], comment="Manual close")
```

## Support

For issues:
1. Check logs (`bot_log.txt` and symbol logs)
2. Review this runbook
3. Check `migration/README.md` for detailed explanations
4. Run tests: `python tests/run_all_tests.py`

