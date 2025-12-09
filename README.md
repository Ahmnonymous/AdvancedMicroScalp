# Trading Bot System

A comprehensive Python-based trading bot system with real-time monitoring, broker reconciliation, and automated optimization.

## Quick Start

### Launch Full System (Recommended)

```bash
python launch_system.py
```

This command will:
- ✅ Start the trading bot with all trading logic active
- ✅ Enable real-time trade monitoring
- ✅ Start automatic broker reconciliation (default: every 30 minutes)
- ✅ Generate logs and reports automatically

### Custom Reconciliation Interval

```bash
# Reconciliation every 15 minutes
python launch_system.py --reconciliation-interval 15

# Reconciliation every 60 minutes
python launch_system.py --reconciliation-interval 60
```

### Alternative Entry Points

```bash
# Standard bot without monitoring
python run_bot.py

# Manual approval mode
python run_bot_manual.py

# Bot with monitoring (alternative to launch_system.py)
python run_bot_with_monitoring.py
```

## Features

- ✅ **Real-Time Broker Reconciliation** - Direct MT5 data fetching and comparison
- ✅ **Enhanced Micro-HFT Engine** - Sweet spot ($0.03–$0.10) + $0.10 multiples
- ✅ **Market Closing Filter** - 30-minute buffer before market close
- ✅ **Volume/Liquidity Filter** - Ensures sufficient market activity
- ✅ **Comprehensive Monitoring** - Real-time performance tracking
- ✅ **Automated Optimization** - Performance-based suggestions
- ✅ **Thread-Safe Operations** - All components run in parallel safely

## Project Structure

```
TRADING/
├── launch_system.py              # Main launcher (bot + monitoring + reconciliation)
├── run_bot.py                    # Standard bot runner
├── run_bot_manual.py             # Manual approval mode
├── run_bot_with_monitoring.py    # Bot with monitoring
├── config.json                   # Configuration file
├── README.md                     # This file
├── requirements.txt              # Python dependencies
│
├── bot/                          # Core bot logic
├── execution/                    # MT5 execution layer
├── filters/                      # Trading filters
├── strategies/                   # Trading strategies
├── risk/                         # Risk management
├── monitor/                      # Monitoring & analysis
├── verify/                       # Verification scripts
├── trade_logging/                # Trade logging
├── utils/                        # Utilities
└── logs/                         # Log files (generated during runtime)
```

## Configuration

Edit `config.json` to customize:
- MT5 connection settings
- Risk management parameters
- Filter thresholds
- Micro-HFT settings
- Monitoring intervals

## Logs

All logs are automatically generated during runtime in the `logs/` directory:

- **Trade Logs**: `logs/trades/[SYMBOL].log` (JSONL format)
- **System Logs**: `logs/system/*.log`
- **Reports**: `logs/reports/*.txt`
- **Engine Logs**: `logs/engine/*.log`

## Requirements

Install dependencies:

```bash
pip install -r requirements.txt
```

## Safety Features

- Read-only monitoring (never modifies trades)
- Automatic config backups
- Thread-safe operations
- Error handling with fallbacks
- Graceful shutdown on Ctrl+C

## Usage

1. **Ensure MT5 Terminal is running and logged in**
2. **Verify `config.json` has correct MT5 credentials**
3. **Run the system**:
   ```bash
   python launch_system.py
   ```
4. **Monitor output**:
   - Real-time trade summary updates every 15 seconds (display only)
   - Trading executes at millisecond speeds independently
   - Micro-HFT checks every 50ms
   - Fast trailing stops every 300ms
5. **Check logs** in `logs/` directory
6. **Stop with Ctrl+C** (graceful shutdown)

## Trading Speed

**Important**: The 15-second summary display interval does NOT affect trading speed.

**Actual Trading Execution Speeds**:
- ✅ **Micro-HFT checks**: Every 50ms (milliseconds)
- ✅ **Fast trailing stop**: Every 300ms (for profitable positions)
- ✅ **Normal trailing stop**: Every 3 seconds
- ✅ **Trade execution**: Instant (milliseconds)
- ✅ **Bot scanning cycle**: 20 seconds (configurable)

The summary display is purely visual and runs in a separate thread, completely independent of trading execution.

## License

See project license file for details.
