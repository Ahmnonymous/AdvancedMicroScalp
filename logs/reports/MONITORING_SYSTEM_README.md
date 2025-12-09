# Comprehensive Bot Monitoring System

## Overview

The Comprehensive Bot Monitor (`monitor/comprehensive_bot_monitor.py`) provides continuous real-time analysis of your trading bot's performance, automatically detecting issues, tracking metrics, and generating actionable recommendations.

## Features

### 1. **Session Analysis**
- **Active Positions Tracking**: Continuously monitors all open positions from MT5
- **Trade Log Analysis**: Reads and analyzes all trade logs from `logs/trades/`
- **Performance Metrics**: Calculates total profit, lot size compliance, symbol distribution

### 2. **Lot Size Enforcement**
- **Automatic Validation**: Checks if positions comply with 0.01-0.03 lot size limit
- **Broker Minimum Detection**: Identifies symbols where broker minimum exceeds system limit
- **Real-time Alerts**: Logs violations immediately when detected
- **Skip Recommendations**: Automatically suggests skipping symbols with incompatible lot sizes

### 3. **Micro-HFT Performance Tracking**
- **Sweet Spot Capture Rate**: Calculates percentage of closures within $0.03-$0.10 range
- **Profit Distribution Analysis**: Tracks min, max, and average profit per closure
- **Performance Status**: Compares against 80% target capture rate
- **Closure Analysis**: Identifies reasons for missed sweet spot (early stop-loss, delayed closure, etc.)

### 4. **Filter Effectiveness Monitoring**
- **Rejection Statistics**: Tracks why symbols are being filtered out:
  - Trend strength too weak
  - RSI outside acceptable range
  - Insufficient tick volume
  - Market closing soon
  - Lot size exceeds limit
  - Max open trades reached
- **Opportunity Detection**: Monitors how many opportunities are found vs rejected

### 5. **Error & Failure Detection**
- **System Error Monitoring**: Scans `logs/system/system_errors.log` for critical issues
- **Root Cause Analysis**: Categorizes errors by severity and type
- **Safety Limit Verification**: Ensures stop-loss ($2), max trades, and lot size limits are enforced

### 6. **Automated Reporting**
- **Periodic Reports**: Generates comprehensive analysis every 5 minutes
- **JSON Reports**: Saved to `logs/reports/comprehensive_analysis_YYYYMMDD_HHMMSS.json`
- **CSV Summaries**: Provides structured data for further analysis
- **Final Report**: Generates complete report on system shutdown

### 7. **Real-time Recommendations**
The monitor provides actionable recommendations including:

**High Priority:**
- Lot size violations requiring immediate attention
- System errors that need resolution

**Medium Priority:**
- Micro-HFT performance below 80% target
- Filter settings that may be too restrictive
- Configuration adjustments to improve trade frequency

## Integration

The Comprehensive Monitor is automatically started when using `launch_system.py`:

```bash
python launch_system.py --reconciliation-interval 30
```

It runs in parallel with:
- Trading Bot
- Real-Time Bot Monitor
- Broker Reconciliation
- Trade Summary Display

## Report Structure

### JSON Report Fields

```json
{
  "report_timestamp": "2025-12-09T17:30:00",
  "session_duration_minutes": 120.5,
  "active_positions": {
    "total": 5,
    "positions": [...],
    "lot_size_issues": [...],
    "symbols": {...},
    "total_profit": 0.45
  },
  "trade_statistics": {
    "total_trades": 12,
    "closed_trades": 7,
    "open_trades": 5
  },
  "micro_hft_performance": {
    "capture_rate": 85.71,
    "total_closures": 7,
    "sweet_spot_closures": 6,
    "missed_sweet_spot": 1,
    "average_profit": 0.065,
    "status": "MEETS TARGET"
  },
  "filter_effectiveness": {
    "statistics": {
      "trend_strength_rejected": 45,
      "rsi_rejected": 32,
      "volume_rejected": 12,
      "opportunities_found": 3
    }
  },
  "recommendations": [...]
}
```

## Monitoring Intervals

- **Monitor Check**: Every 10 seconds
- **Full Report Generation**: Every 5 minutes
- **Real-time Alerts**: Immediate when issues detected

## File Organization

All reports are saved to:
- `logs/reports/comprehensive_analysis_*.json` - Full JSON reports
- `logs/reports/comprehensive_analysis_*.csv` - CSV summaries
- `logs/system/comprehensive_monitor.log` - Monitor activity logs

## Usage

### Standalone Mode

You can also run the monitor independently:

```python
from monitor.comprehensive_bot_monitor import ComprehensiveBotMonitor

monitor = ComprehensiveBotMonitor('config.json')
monitor.start()

# Monitor runs in background thread
# Generate immediate report
report = monitor.generate_report()

# Stop when done
monitor.stop()
```

### Reading Reports

```python
import json

with open('logs/reports/comprehensive_analysis_20251209_173000.json', 'r') as f:
    report = json.load(f)
    
print(f"Sweet spot capture: {report['micro_hft_performance']['capture_rate']:.2f}%")
print(f"Active positions: {report['active_positions']['total']}")
print(f"Lot size violations: {len(report['lot_size_compliance']['violations'])}")

for rec in report['recommendations']:
    print(f"[{rec['priority']}] {rec['category']}: {rec['issue']}")
```

## Recommendations Interpretation

### High Priority Actions
- **Lot Size Violations**: Symbols with lot sizes > 0.03 should be skipped
- **System Errors**: Review error logs and resolve critical issues

### Medium Priority Actions
- **Low Capture Rate**: Adjust Micro-HFT closure timing or profit tolerance
- **No Opportunities**: Consider relaxing filter thresholds if markets are active
- **Filter Trends**: Adjust RSI or trend strength filters if too many rejections

## Safety Features

- **Non-intrusive**: Only analyzes data, never modifies trades
- **Thread-safe**: Designed to run in parallel with trading bot
- **Error Handling**: Continues monitoring even if individual checks fail
- **Graceful Shutdown**: Generates final report on exit

## Performance Impact

- **Minimal CPU Usage**: Efficient log parsing and data aggregation
- **Low Memory Footprint**: Processes logs incrementally
- **No Trading Delay**: Runs in separate thread, does not affect trading execution

## Next Steps

1. **Monitor First Session**: Run bot with comprehensive monitor active
2. **Review Initial Reports**: Check `logs/reports/` for analysis findings
3. **Implement Recommendations**: Adjust filters, lot size validation, or Micro-HFT settings
4. **Track Improvements**: Compare capture rates and trade frequency over time

