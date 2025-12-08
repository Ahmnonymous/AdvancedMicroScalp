# Trading Bot - Automated Scalping System

An automated MetaTrader 5 (MT5) trading bot designed for scalping with advanced trailing stop management, risk controls, and halal compliance features.

## Project Overview

This trading bot is a sophisticated automated trading system that:

- **Executes scalping trades** on forex and crypto markets via MetaTrader 5
- **Implements trailing stops** with elastic trailing engine and fast polling for profitable positions
- **Manages risk** with configurable lot sizes, max open trades, and per-trade risk limits
- **Filters opportunities** using trend analysis, RSI, ADX, and volatility indicators
- **Blocks trading during news events** to avoid high volatility periods
- **Ensures halal compliance** with swap-free mode and time-based position limits
- **Supports test and live modes** for safe testing before live trading

## Features

### Core Trading Features
- **Trend-based entry signals** using SMA crossovers, RSI, and ADX
- **Multi-trade staged opens** for scaling into positions
- **Elastic trailing stops** with pullback tolerance and big jump detection
- **Fast trailing mode** (300ms polling) when positions are profitable
- **News event filtering** to avoid trading during high-impact news
- **Symbol filtering** with spread, commission, and swap-free checks

### Risk Management
- Configurable maximum risk per trade (USD)
- Maximum open trades limit
- Symbol-specific lot size limits
- Stop-loss validation
- Kill switch for emergency shutdown
- Supervisor with error cooldown and auto-restart

### Halal Compliance
- Swap-free mode enforcement
- Maximum hold time limits
- No overnight position holds
- Automatic position closure before swap time

### Monitoring & Logging
- Real-time position monitoring
- Detailed per-symbol logging
- Trade execution logs
- Performance analysis tools
- Daily P/L reports

## Installation

### Prerequisites

1. **Python 3.6-3.10** installed on your system (Python 3.11+ is NOT supported by MetaTrader5)
   - Check version: `python --version`
   - If you have Python 3.11+, you must use Python 3.10 or create a virtual environment with Python 3.10
2. **Windows Operating System** (MetaTrader5 is Windows-only)
3. **MetaTrader 5** installed and configured
4. **MT5 account credentials** (demo or live)

### Step 1: Clone or Download the Repository

```bash
git clone <repository-url>
cd TRADING
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

**Troubleshooting MetaTrader5 Installation:**

If you encounter `ERROR: Could not find a version that satisfies the requirement MetaTrader5`:

1. **Check Python version** (CRITICAL):
   ```bash
   python --version
   ```
   - MetaTrader5 only supports Python 3.6-3.10
   - Python 3.11+ will NOT work - no wheels available
   - If you have Python 3.11+, you must use Python 3.10

2. **Solution for Python 3.11+ users:**
   
   **Option A: Install Python 3.10 and use it:**
   ```bash
   # Download Python 3.10 from python.org
   # Then use it specifically:
   py -3.10 -m pip install -r requirements.txt
   ```
   
   **Option B: Create virtual environment with Python 3.10:**
   ```bash
   # If you have Python 3.10 installed:
   py -3.10 -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Ensure you're on Windows** - MetaTrader5 is Windows-only

4. **Upgrade pip first**: `python -m pip install --upgrade pip`

5. **Try installing directly**: `pip install MetaTrader5`

This will install:
- `MetaTrader5` - MT5 Python API (Windows-only, Python 3.8-3.11)
- `pandas` - Data processing
- `numpy` - Numerical computations
- `requests` - HTTP requests for news API
- `beautifulsoup4` & `lxml` - Web scraping for news filtering

### Step 3: Configure the Bot

Create or edit `config.json` with your MT5 credentials and trading parameters:

```json
{
  "mt5": {
    "account": "YOUR_ACCOUNT_NUMBER",
    "password": "YOUR_PASSWORD",
    "server": "YOUR_BROKER_SERVER",
    "path": "",
    "timeout": 60000,
    "reconnect_attempts": 5,
    "reconnect_delay": 5
  },
  "risk": {
    "max_risk_per_trade_usd": 2.0,
    "default_lot_size": 0.01,
    "max_open_trades": 6,
    "trailing_stop_increment_usd": 0.1,
    "continuous_trailing_enabled": true,
    "trailing_cycle_interval_seconds": 3.0
  },
  "trading": {
    "timeframe": "M1",
    "cycle_interval_seconds": 20,
    "sma_fast": 20,
    "sma_slow": 50,
    "rsi_period": 14
  },
  "news": {
    "enabled": true,
    "api_provider": "financialmodelingprep",
    "api_key": "YOUR_API_KEY",
    "block_window_minutes": 10
  },
  "halal": {
    "enabled": true,
    "swap_free_mode": true,
    "max_hold_hours": 24
  }
}
```

**⚠️ Important:** Never commit `config.json` with real credentials to version control. Use a template or environment variables for sensitive data.

## Configuration Guide

### Key Configuration Sections

#### MT5 Connection (`mt5`)
- `account`: Your MT5 account number
- `password`: Your MT5 account password
- `server`: Broker server name (e.g., "Exness-MT5Trial15")
- `path`: Path to MT5 terminal (empty for auto-detect)
- `timeout`: Connection timeout in milliseconds
- `reconnect_attempts`: Number of reconnection attempts
- `reconnect_delay`: Delay between reconnection attempts (seconds)

#### Risk Management (`risk`)
- `max_risk_per_trade_usd`: Maximum USD risk per trade
- `default_lot_size`: Default lot size for trades
- `max_open_trades`: Maximum concurrent open positions
- `trailing_stop_increment_usd`: Minimum increment for trailing stops
- `trailing_cycle_interval_seconds`: How often trailing stops are checked
- `fast_trailing_threshold_usd`: Profit threshold to enable fast trailing (300ms)
- `elastic_trailing.enabled`: Enable elastic trailing with pullback tolerance

#### Trading Parameters (`trading`)
- `timeframe`: Chart timeframe (M1, M5, M15, etc.)
- `cycle_interval_seconds`: Main trading cycle interval
- `sma_fast` / `sma_slow`: Fast and slow SMA periods
- `rsi_period`: RSI indicator period
- `rsi_overbought` / `rsi_oversold`: RSI thresholds
- `min_quality_score`: Minimum setup quality score required

#### News Filtering (`news`)
- `enabled`: Enable/disable news filtering
- `api_provider`: News API provider (e.g., "financialmodelingprep")
- `api_key`: API key for news provider
- `block_window_minutes`: Minutes before/after news to block trading
- `high_impact_only`: Only block high-impact news events

#### Halal Compliance (`halal`)
- `enabled`: Enable halal compliance checks
- `swap_free_mode`: Require swap-free symbols only
- `max_hold_hours`: Maximum position hold time in hours
- `no_overnight_holds`: Close positions before swap time

#### Pairs/Symbols (`pairs`)
- `test_mode`: Enable test mode (ignores restrictions)
- `test_mode_ignore_restrictions`: Ignore spread/commission checks in test mode
- `auto_discover_symbols`: Automatically discover tradeable symbols
- `max_spread_points`: Maximum spread in points
- `max_spread_percent_crypto`: Maximum spread percentage for crypto

See `migration/README.md` for detailed configuration options and advanced features.

## Usage

### Running the Bot

#### Option 1: Launch System (Recommended)
Runs bot in background with real-time monitor in foreground:

```bash
python launch_system.py
```

For test mode (ignores restrictions):
```bash
python launch_system.py --test-mode
```

#### Option 2: Run Bot Only
Runs bot in background mode without monitor:

```bash
python run_bot.py
```

### Test Mode vs Live Mode

**Test Mode:**
- Ignores spread restrictions
- Ignores commission checks
- Ignores exotic currency restrictions
- Ignores halal compliance (for testing)
- Useful for testing strategies and configurations

**Live Mode:**
- All restrictions and filters active
- Real money trading
- Full risk management enforced
- Halal compliance active

Enable test mode by setting `"test_mode": true` in `config.json` under `pairs` section, or use `--test-mode` flag with `launch_system.py`.

### Monitoring

The bot creates several log files:

- **`bot_log.txt`**: Main log file with critical events (trades, SL adjustments, errors)
- **`logs/symbols/{SYMBOL}_YYYY-MM-DD.log`**: Detailed per-symbol logs with DEBUG-level information

Monitor real-time activity:
```bash
python monitor/monitor.py
```

Or use the integrated monitor with `launch_system.py` (recommended).

## Project Structure

```
TRADING/
├── bot/                    # Core bot logic
│   ├── trading_bot.py     # Main orchestrator
│   ├── logger_setup.py    # Logging configuration
│   └── config_validator.py # Config validation
├── execution/              # Order execution
│   ├── mt5_connector.py   # MT5 connection handler
│   └── order_manager.py   # Order placement and management
├── risk/                   # Risk management
│   ├── risk_manager.py    # Risk calculations and trailing stops
│   ├── pair_filter.py     # Symbol filtering
│   └── halal_compliance.py # Halal compliance checks
├── strategies/             # Trading strategies
│   └── trend_filter.py    # Trend analysis and entry signals
├── news_filter/            # News event filtering
│   └── news_api.py        # News API integration
├── monitor/                # Monitoring tools
│   ├── monitor.py         # Real-time monitor
│   └── analyze_bot_performance.py # Performance analysis
├── checks/                 # Diagnostic tools
├── find/                   # Symbol discovery tools
├── tests/                  # Test suite
├── config.json            # Configuration file
├── run_bot.py             # Bot entry point
└── launch_system.py       # System launcher with monitor
```

## Logs and Error Handling

### Logging Levels

- **Root Logger** (`bot_log.txt`): INFO level - Critical events only
  - Trade executions
  - Trailing stop adjustments
  - Trade closures
  - Kill switch changes
  - Major errors

- **Symbol Loggers** (`logs/symbols/`): DEBUG level - Detailed analysis
  - Symbol analysis steps
  - Trend signals
  - Spread checks
  - Quality scores
  - Trailing stop calculations

### Error Handling

The bot includes several safety mechanisms:

1. **Supervisor System**: Monitors for consecutive errors and implements cooldown periods
2. **Kill Switch**: Emergency shutdown mechanism
3. **Connection Retry**: Automatic reconnection to MT5 on connection loss
4. **Exception Handling**: Comprehensive error catching and logging
5. **Config Validation**: Validates configuration before starting

### Common Issues

**Connection Failed:**
- Verify MT5 credentials in `config.json`
- Ensure MT5 terminal is running
- Check broker server name is correct

**No Trades Executed:**
- Check symbol filters (spread, commission, swap-free)
- Verify trend signals are being generated
- Check news filter isn't blocking all trading
- Review symbol logs for detailed analysis

**Trailing Stops Not Working:**
- Verify `continuous_trailing_enabled` is `true`
- Check `trailing_cycle_interval_seconds` is reasonable (3.0s recommended)
- Ensure positions have sufficient profit for trailing

## Updates and Maintenance

### Updating the Bot

1. Pull latest changes:
   ```bash
   git pull origin main
   ```

2. Update dependencies:
   ```bash
   pip install -r requirements.txt --upgrade
   ```

3. Review `migration/README.md` for configuration changes

4. Test in test mode before going live:
   ```bash
   python launch_system.py --test-mode
   ```

### Backup Recommendations

- Regularly backup `config.json` (without credentials)
- Archive log files periodically
- Keep track of configuration changes

## Testing

Run the test suite:

```bash
python tests/run_all_tests.py
```

Individual tests:
- `test_connection.py` - Test MT5 connection
- `test_trade_placement.py` - Test order placement
- `test_trailing_behavior.py` - Test trailing stop logic
- `test_staged_open.py` - Test staged open functionality

## Safety Warnings

⚠️ **IMPORTANT DISCLAIMERS:**

1. **Trading involves risk** - Only trade with money you can afford to lose
2. **Test thoroughly** - Always test in demo/test mode before live trading
3. **Monitor actively** - Don't leave the bot unattended for extended periods
4. **Review configurations** - Understand all settings before enabling
5. **Backup regularly** - Keep backups of configurations and logs
6. **No guarantees** - Past performance does not guarantee future results

## License

[Specify your license here]

## Support

For issues, questions, or contributions:
- Check `migration/README.md` for detailed feature documentation
- Review log files for error details
- Run diagnostic tools in `checks/` directory

---

**Happy Trading! 📈**

