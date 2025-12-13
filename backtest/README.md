# Backtest Module - Consolidated Backtest Engine

## Overview

This module provides a consolidated backtest engine for testing the trading bot against historical market data. The engine simulates live trading behavior including SLManager, RiskManager, ProfitLockingEngine, and all filters.

## Folder Structure

```
backtest/
├── integration_layer.py       # MT5 wrapper for backtesting (CORE)
├── backtest_runner.py         # Main orchestrator (CORE)
├── utils.py                   # Consolidated helper functions
├── run_backtest.py            # Entry point script
├── config_backtest.json       # Backtest-specific configuration
│
├── Core Engine Components:
├── market_data_provider.py    # Market data abstraction
├── order_execution_provider.py # Order execution abstraction
├── historical_replay_engine.py # Historical data replay
├── performance_reporter.py    # Performance metrics and reporting
├── backtest_threading_manager.py # Threading simulation
├── stress_test_modes.py       # Stress testing scenarios
│
├── Validation & Utilities:
├── data_preflight_validator.py # Data availability validation
├── config_validator.py        # Configuration validation
├── equivalence_validator.py   # Live/backtest equivalence validation
├── test_scenarios.py          # Pre-defined test scenarios
│
└── archived/                  # Legacy/duplicate scripts (archived)
```

## Core Files (DO NOT DELETE)

- **`integration_layer.py`**: Injects backtest providers into bot components
- **`backtest_runner.py`**: Main orchestrator that coordinates the entire backtest
- **`market_data_provider.py`**: Provides market data interface
- **`order_execution_provider.py`**: Provides order execution interface
- **`historical_replay_engine.py`**: Replays historical data
- **`performance_reporter.py`**: Tracks and reports metrics
- **`backtest_threading_manager.py`**: Simulates threading behavior
- **`stress_test_modes.py`**: Stress testing functionality

## Quick Start

### Basic Usage

```bash
# Run with default config (from config.json and config_backtest.json)
python backtest/run_backtest.py

# Run with custom date range
python backtest/run_backtest.py --start 2024-01-01 --end 2024-12-31

# Run with specific symbols
python backtest/run_backtest.py --symbols EURUSDm GBPUSDm XAUUSDm

# Run with real-time speed (matches broker timing - slow)
python backtest/run_backtest.py --real-speed

# Run accelerated (default - fast)
python backtest/run_backtest.py --speed 10

# Run with stress tests
python backtest/run_backtest.py --stress-tests high_volatility extreme_spread
```

### Configuration

The backtest uses `config.json` as the base configuration, with backtest-specific settings in `backtest/config_backtest.json`. The `config_backtest.json` file extends the main config with:

- **Symbols**: List of symbols to test (forex, crypto, indices, commodities)
- **Date Range**: Start and end dates for historical data
- **Timeframe**: Data timeframe (M1, M5, H1, etc.)
- **Real Speed**: `true` = match broker timing, `false` = accelerated replay
- **Stress Tests**: Optional stress test scenarios

Example `config_backtest.json`:
```json
{
  "backtest": {
    "symbols": ["EURUSDm", "GBPUSDm", "XAUUSDm", "BTCUSDm"],
    "start_date": "2024-01-01T00:00:00",
    "end_date": "2024-12-31T23:59:59",
    "timeframe": "M1",
    "real_speed": false,
    "stress_tests": []
  }
}
```

## Features

### Production-Like Environment

The consolidated backtest engine maintains full production logic:

- ✅ **SLManager**: Stop-loss updates and profit locking
- ✅ **RiskManager**: Position sizing and risk management
- ✅ **ProfitLockingEngine**: Sweet spot and trailing stop logic
- ✅ **MicroProfitEngine**: Micro-profit optimization (if enabled)
- ✅ **All Filters**: TrendFilter, NewsFilter, MarketClosingFilter, VolumeFilter
- ✅ **Threading Simulation**: Simulates live threading behavior
- ✅ **Logging**: Same structure as live trades (JSONL format)

### Multi-Symbol Support

Supports all symbol types from Exness:
- **Forex**: EURUSDm, GBPUSDm, USDJPYm, etc.
- **Metals**: XAUUSDm, XAGUSDm
- **Crypto**: BTCUSDm, ETHUSDm
- **Indices**: US30m, NAS100m, US500m, JP225m, AUS200m

### Replay Speed Control

- **`real_speed: true`**: Matches broker timing (slow, realistic)
- **`real_speed: false`**: Accelerated replay (fast, default)
- **`speed` multiplier**: Custom speed multiplier (e.g., 10x faster)

### Stress Testing

Optional stress test scenarios:
- `high_volatility`: Increased price volatility
- `extreme_spread`: Wider spreads
- `fast_reversals`: Rapid price reversals
- `tick_gaps`: Missing tick data simulation
- `slippage_spikes`: Slippage spikes
- `market_dead`: Low liquidity periods
- `candle_anomalies`: Abnormal candle patterns

## Utilities (utils.py)

The `utils.py` module consolidates common helper functions:

- `parse_timeframe()`: Convert timeframe string to MT5 constant
- `get_timeframe_seconds()`: Get timeframe duration in seconds
- `calculate_date_range()`: Calculate date ranges for backtesting
- `iterate_symbols()`: Iterate over symbols with callback
- `load_symbol_data()`: Load historical data for a symbol
- `validate_backtest_config()`: Validate backtest configuration
- `format_duration()`: Format duration to human-readable string

## Archived Files

The following files have been archived to `backtest/archived/`:

- `run_backtest.py` (old version)
- `run_comprehensive_backtest.py`
- `run_comprehensive_test.py`
- `run_comprehensive_test_single_symbol.py`
- `quick_test.py`
- `exhaustive_backtest_automation.py`
- `comprehensive_backtest.py`
- `test_symbol_data_download.py`

These files are preserved for reference but are not used by the consolidated engine.

## Verification

After cleanup, verify the backtest engine:

1. **Compilation**: All modules compile without errors
2. **Import Check**: All imports resolve correctly
3. **Configuration**: Config loads and validates successfully
4. **Symbol Support**: All configured symbols can be tested
5. **Date Range**: Configurable start/end dates work correctly
6. **Speed Control**: Real-time and accelerated modes function
7. **Production Logic**: All production logic (SL, risk, filters) are active

## Running Backtests

### Example 1: Quick Test (1 Month, Accelerated)

```bash
python backtest/run_backtest.py --months 1 --symbols EURUSDm GBPUSDm
```

### Example 2: Full Year Test (Real-Time Speed)

```bash
python backtest/run_backtest.py --start 2024-01-01 --end 2024-12-31 --real-speed
```

### Example 3: Stress Test

```bash
python backtest/run_backtest.py --stress-tests high_volatility extreme_spread
```

## Logs and Reports

Backtest logs and reports are generated in:
- **Logs**: `logs/backtest/`
- **Reports**: Generated by `PerformanceReporter` (typically in logs/backtest/reports/)

Logs follow the same structure as live trades, allowing for easy comparison and analysis.

## Notes

- The backtest engine requires MT5 terminal to be running and logged in for data access
- Historical data availability depends on broker and symbol
- Real-time speed mode is slow but provides realistic timing simulation
- Accelerated mode is faster but may not catch all timing-related edge cases
- All production logic is preserved in backtest mode to ensure equivalence

