# Trading Bot System - Final Architecture Summary

## System Overview

The trading bot is a MetaTrader 5 (MT5) automated trading system that executes scalping trades with comprehensive risk management, stop-loss enforcement, and profit protection mechanisms. The system operates in two modes: live trading and backtesting, with complete parity between modes.

---

## Architecture

### Core Components

**TradingBot** (`bot/trading_bot.py`)
- Main orchestrator coordinating all system components
- Executes trading cycles, manages state, tracks performance
- Initializes and coordinates all sub-modules
- Handles both live and backtest modes

**RiskManager** (`risk/risk_manager.py`)
- Position sizing based on fixed $2.00 risk per trade
- Lot size calculation (default 0.01, up to 0.05 if broker minimum requires)
- Max open trades enforcement (configurable, supports unlimited)
- Coordinates with SLManager for stop-loss management

**SLManager** (`risk/sl_manager.py`)
- Unified stop-loss management system
- Single source of truth for all SL updates
- Thread-safe per-position locking mechanism
- Implements strict loss enforcement, sweet spot profit locking, and trailing stops

**OrderManager** (`execution/order_manager.py`)
- Order placement, modification, and closure
- Handles market orders and partial fills
- Interfaces with MT5 broker via MT5Connector
- Supports ORDER_FILLING_RETURN for partial fills

**MT5Connector** (`execution/mt5_connector.py`)
- MT5 broker connection management
- Market data retrieval (symbol info, prices, ticks)
- Account information access
- Connection state management and reconnection

**PositionMonitor** (`execution/position_monitor.py`)
- Continuously monitors open positions
- Detects position closures from broker
- Logs all closures to trade logs
- Runs in separate thread

### Strategy Components

**TrendFilter** (`strategies/trend_filter.py`)
- Calculates quality score (0-100) for trade setups
- Minimum quality score: 60 (configurable)
- Determines trade direction (LONG/SHORT)
- Provides trend analysis and signal strength

### Filter Components

**PairFilter** (`risk/pair_filter.py`)
- Symbol discovery and filtering
- Validates spread, commission, trade modes
- Supports auto-discovery of all Exness symbols
- Test mode for backtesting (bypasses restrictions)

**NewsFilter** (`news_filter/news_api.py`)
- Blocks trading ±10 minutes around high-impact news events
- Configurable block window (minimum 10 minutes)
- News event detection and scheduling

**MarketClosingFilter** (`filters/market_closing_filter.py`)
- Prevents trading 30 minutes before market close
- Configurable window (default 30 minutes)
- Market hours detection per symbol

**VolumeFilter** (`filters/volume_filter.py`)
- Validates minimum volume requirements
- Ensures adequate market liquidity

### Profit Protection Components

**ProfitLockingEngine** (`bot/profit_locking_engine.py`)
- Intelligent profit locking in sweet spot range ($0.03-$0.10)
- Integrated within SLManager (not a bypass)
- Step-based trailing locks
- SL verification before locking

**MicroProfitEngine** (`bot/micro_profit_engine.py`)
- Immediate profit taking in sweet spot range
- Intentional bypass for micro-profit capture
- Multiple safety checkpoints
- Verifies SL before closing

**HalalCompliance** (`risk/halal_compliance.py`)
- Islamic/Halal trading compliance
- Closes positions violating overnight hold rules
- Intentional bypass for compliance
- Properly logged with [HALAL] prefix

### Logging and Monitoring

**TradeLogger** (`trade_logging/trade_logger.py`)
- Symbol-based trade logging
- Logs all trade entries and exits
- Structured logging per symbol

**LoggerFactory** (`utils/logger_factory.py`)
- Centralized logger creation
- Mode-based log paths (live/backtest)
- Symbol-specific loggers

**Monitoring Modules** (`monitor/`)
- Real-time bot monitoring
- Position reconciliation
- SL update monitoring
- Performance tracking

### Backtest Components

**BacktestIntegration** (`backtest/integration_layer.py`)
- Injects backtest providers into bot
- Maintains interface compatibility
- Wrapper pattern for MT5Connector and OrderManager
- Enables test mode for filters

**BacktestRunner** (`backtest/backtest_runner.py`)
- Orchestrates backtest execution
- Historical data replay
- Simulates live threading behavior
- Performance reporting

---

## Execution Flow

### Trade Entry Cycle

1. **Symbol Discovery** (`TradingBot.scan_for_opportunities()`)
   - PairFilter discovers tradeable symbols (all Exness symbols)
   - Filters symbols based on spread, commission, trade modes
   - Returns list of candidate symbols

2. **Market Filtering**
   - NewsFilter: Check for high-impact news events (±10 minutes window)
   - MarketClosingFilter: Ensure not within 30 minutes of market close
   - VolumeFilter: Validate minimum volume requirements
   - All filters must pass for symbol to be considered

3. **Strategy Analysis** (`TrendFilter.calculate_quality_score()`)
   - Calculate quality score (0-100) for symbol
   - Determine trade direction (LONG/SHORT)
   - Minimum quality score: 60 (configurable via `min_quality_score`)

4. **Risk Assessment** (`RiskManager.can_open_trade()`)
   - Check max open trades limit (configurable, supports unlimited)
   - Verify position sizing constraints
   - Calculate lot size (default 0.01, up to 0.05 if broker minimum requires)

5. **Lot Size Determination** (`RiskManager.determine_lot_size_with_priority()`)
   - Default: 0.01 lots
   - Increase up to 0.05 only if broker minimum lot size requires it
   - Respects symbol-specific limits from config

6. **Order Placement** (`OrderManager.place_order()`)
   - Market order placement (limit orders optional, disabled by default)
   - Initial stop-loss set to -$2.00 USD (strict loss limit)
   - Handles partial fills (ORDER_FILLING_RETURN)
   - Executes only filled portion, ignores remaining lots

7. **Trade Logging** (`TradeLogger.log_trade_entry()`)
   - Log trade entry to symbol-specific log file
   - Record ticket, symbol, direction, entry price, lot size, SL, quality score

### SL Update Cycle

1. **SL Worker Loop** (`SLManager._sl_worker_loop()`)
   - Continuous thread running every 50ms (configurable via `trailing_cycle_interval_ms`)
   - Single source of truth for all SL updates
   - Processes all open positions

2. **Per-Position Processing** (`SLManager.update_sl_atomic()`)
   - Acquires per-position lock (thread-safe)
   - Lock timeout: 1.0s normal, 2.0s for profit locking (configurable)
   - Prevents simultaneous updates to same position

3. **SL Priority Logic** (in order):
   
   **a) Strict Loss Enforcement** (if profit < 0)
   - Enforces -$2.00 USD stop-loss limit
   - Emergency SL if current SL exceeds limit
   - Prevents losses exceeding configured risk

   **b) Sweet Spot Profit Locking** (if profit ≥ $0.03 and ≤ $0.10)
   - Triggers immediately when profit enters range
   - No break-even wait required
   - Locks profit at entry price (breakeven)
   - Integrated ProfitLockingEngine attempts sophisticated locking
   - Falls back to internal sweet spot logic if engine unavailable

   **c) Trailing Stop** (if profit > $0.10)
   - Trailing increment: $0.10 USD (configurable)
   - Moves SL up (BUY) or down (SELL) as profit increases
   - Elastic trailing with pullback tolerance (25%)
   - Big jump detection (≥$0.40) for aggressive locking

4. **SL Update Execution** (`OrderManager.modify_order()`)
   - Validates new SL against broker constraints (stops_level, spread)
   - Respects minimum SL distance requirements
   - Updates position stop-loss via MT5
   - Verifies update success

5. **Update Tracking and Metrics**
   - Logs all SL updates with structured logging (JSONL)
   - Tracks update success/failure rates
   - Records lock contention and timing
   - CSV summaries for analysis

### Trade Closure Cycle

1. **Normal Closure Paths:**
   - Stop-loss hit: Broker automatically closes position at SL price
   - Take-profit hit: Broker closes position at TP (if set)
   - Trailing stop: SL moves up/down until price hits trailing SL

2. **Position Monitor** (`PositionMonitor._position_monitor_loop()`)
   - Continuously monitors open positions
   - Detects closures from broker
   - Logs closures to trade logs
   - Runs in separate thread

3. **Intentional Bypass Closures:**

   **a) MicroProfitEngine** (`MicroProfitEngine.check_and_close()`)
   - Closes positions when profit in sweet spot ($0.03-$0.10)
   - Multiple safety checkpoints
   - Verifies SL applied before closing
   - Only closes if profit ≥ $0.05 buffer

   **b) HalalCompliance** (`HalalCompliance.check_all_positions()`)
   - Closes positions violating overnight hold rules
   - Compliance requirement
   - Properly logged with [HALAL] prefix

4. **Closure Logging** (`TradeLogger.log_trade_exit()`)
   - Logs closure reason, profit/loss, duration
   - Updates trade statistics
   - Records to symbol-specific log file

---

## Live vs Backtest Parity

### Architecture Equivalence

The system maintains complete parity between live and backtest modes through wrapper pattern integration:

1. **Interface Compatibility**
   - Backtest providers implement same interfaces as live components
   - `BacktestMT5ConnectorWrapper` mirrors `MT5Connector` interface
   - `BacktestOrderManagerWrapper` mirrors `OrderManager` interface
   - All bot components operate identically in both modes

2. **Component Initialization**
   - TradingBot initializes all components identically
   - Same configuration used (validated for alignment)
   - Same risk parameters, thresholds, and logic
   - BacktestIntegration injects providers after initialization

3. **Core Logic Preservation**
   - All trading logic remains unchanged
   - SLManager logic identical in both modes
   - Risk management identical
   - Filter and strategy logic identical
   - Only data sources swapped (MT5 ↔ Historical data)

### Execution Equivalence

1. **SL Update Mechanism**
   - **Live:** `_sl_worker_loop()` thread calls `update_sl_atomic()` every 50ms
   - **Backtest:** `BacktestThreadingManager` simulates thread behavior, calls `update_sl_atomic()` directly
   - Same method, same logic, equivalent behavior

2. **Trade Entry Logic**
   - Same filters applied (NewsFilter, MarketClosingFilter, VolumeFilter)
   - Same quality score threshold (60)
   - Same lot sizing logic (0.01 default, up to 0.05 if required)
   - Same risk enforcement

3. **Profit Locking Logic**
   - Sweet spot range identical: $0.03-$0.10
   - Trailing stop increment identical: $0.10
   - Break-even disabled in both modes
   - Same triggering conditions

4. **Configuration Alignment**
   - Backtest mode validates config alignment with live
   - Critical parameters must match exactly
   - Ensures deterministic backtest reproduction

### Filter Behavior in Backtest

- **PairFilter:** Test mode enabled, bypasses spread/commission/exotic checks (for flexibility)
- **NewsFilter:** Same logic, uses historical news data
- **MarketClosingFilter:** Same logic, uses historical market hours
- **TrendFilter:** Same quality score calculation
- **RSI Filter:** Skipped in backtest (allows execution without RSI data)

### Data Flow Equivalence

- **Live:** MT5 provides real-time market data and executes orders
- **Backtest:** Historical data provider supplies market data, simulated execution provider processes orders
- **Bot Logic:** Identical in both modes, unaware of data source

---

## Safety Guarantees and Invariants

### SL Update Invariants

1. **Single Source of Truth**
   - All SL updates flow through `SLManager.update_sl_atomic()`
   - No direct SL modifications bypass SLManager
   - Per-position locks prevent race conditions

2. **Strict Loss Limit**
   - Maximum loss per trade: -$2.00 USD (configurable)
   - Enforced immediately if position exceeds limit
   - Emergency SL application if needed

3. **Break-Even Disabled**
   - No break-even logic active
   - Profit locking triggers immediately at sweet spot ($0.03)
   - No waiting period for profit to stabilize

4. **Sweet Spot Profit Locking**
   - Range: $0.03 - $0.10 USD (configurable)
   - Triggers immediately when profit enters range
   - Locks profit at entry price (breakeven SL)

5. **Trailing Stop Invariant**
   - Only activates when profit > $0.10
   - Trailing increment: $0.10 USD (configurable)
   - Never moves SL against position (only locks profit)

### Position Safety Invariants

1. **Initial SL Guarantee**
   - Every position opened with initial SL of -$2.00 USD
   - SL set during order placement
   - Cannot open position without SL

2. **SL Update Rate Limiting**
   - Minimum interval between SL updates: 100ms (configurable)
   - Prevents excessive broker requests
   - Per-ticket rate limiting

3. **Broker Constraint Respect**
   - All SL updates respect broker `stops_level` requirement
   - Accounts for spread when calculating valid SL
   - Never attempts invalid SL updates

4. **Lock Safety**
   - Per-position locks with timeouts (1.0s normal, 2.0s profit locking)
   - Automatic stale lock recovery (500ms max hold time)
   - Prevents deadlocks and blocking

### Trade Entry Safety Invariants

1. **Quality Score Threshold**
   - Minimum quality score: 60 (configurable)
   - No trades below threshold
   - Scalping strategy enforcement

2. **Risk Per Trade**
   - Fixed $2.00 USD risk per trade (configurable)
   - Lot size calculated to achieve exact risk
   - Never exceeds configured risk

3. **Lot Size Limits**
   - Default: 0.01 lots
   - Maximum: 0.05 lots (only if broker minimum requires)
   - Symbol-specific limits respected

4. **Max Open Trades**
   - Configurable limit (supports unlimited via None or -1)
   - Enforced before trade entry
   - Prevents over-leveraging

### Market Filtering Invariants

1. **News Avoidance**
   - Blocks trading ±10 minutes around high-impact news (minimum)
   - Configurable block window
   - Prevents trading during volatile periods

2. **Market Close Avoidance**
   - Blocks trading 30 minutes before market close (configurable)
   - Prevents overnight holds if not desired
   - Respects market hours per symbol

3. **Volume Validation**
   - Minimum volume requirements enforced
   - Ensures adequate liquidity
   - Prevents trading in illiquid markets

### Thread Safety Guarantees

1. **Per-Position Locks**
   - Each position has dedicated lock
   - Prevents simultaneous SL updates
   - Lock timeout prevents blocking

2. **State Tracking Thread Safety**
   - Lock-protected state dictionaries
   - Thread-safe access to position tracking
   - No race conditions in state management

3. **Continuous Threads**
   - SL worker loop runs continuously (every 50ms)
   - Position monitor runs continuously
   - Graceful shutdown on termination

### Intentional Bypass Safety

1. **MicroProfitEngine**
   - Multiple validation checkpoints
   - Verifies SL applied before closing
   - Never closes losing trades
   - Minimum profit buffer ($0.05) for slippage

2. **HalalCompliance**
   - Only closes on compliance violations
   - Does not modify SL
   - Properly logged and traceable

---

## Configuration

### Critical Parameters

**Risk Management:**
- `max_risk_per_trade_usd`: 2.0 (fixed risk per trade)
- `default_lot_size`: 0.01 (default lot size)
- `max_open_trades`: 6 (configurable, supports unlimited)
- `trailing_stop_increment_usd`: 0.10 (trailing stop step)

**SL Management:**
- `trailing_cycle_interval_ms`: 50 (SL worker loop frequency)
- `lock_acquisition_timeout_seconds`: 1.0 (normal lock timeout)
- `profit_locking_lock_timeout_seconds`: 2.0 (profit locking timeout)
- `sl_update_min_interval_ms`: 100 (minimum SL update interval)

**Profit Locking:**
- `min_profit_threshold_usd`: 0.03 (sweet spot minimum)
- `max_profit_threshold_usd`: 0.10 (sweet spot maximum)

**Trading:**
- `min_quality_score`: 60 (minimum setup quality)
- `cycle_interval_seconds`: 60 (main trading cycle interval)

**Market Filters:**
- `news.block_window_minutes`: 10 (minimum news avoidance window)
- `market_closing.minutes_before_close`: 30 (market close avoidance)

### Mode Configuration

- `mode`: "live" or "backtest"
- Backtest mode validates alignment with live config
- Same risk parameters in both modes
- Same thresholds and logic in both modes

---

## File Structure

```
TRADING/
├── bot/                    # Core trading bot logic
├── risk/                   # Risk management and SL enforcement
├── execution/              # Order execution and MT5 connection
├── strategies/             # Trading strategies
├── filters/                # Market filters
├── news_filter/            # News event filtering
├── trade_logging/          # Trade logging system
├── backtest/               # Backtest infrastructure
├── monitor/                # Monitoring and reconciliation
├── utils/                  # Utility modules
├── tests/                  # Test suite
├── docs/                   # Documentation
├── config.json             # Main configuration
├── launch_system.py        # System launcher
└── README.md               # Project documentation
```

---

## Summary

The trading bot system is a production-ready automated trading platform with:

- **Unified SL Management:** Single source of truth for all stop-loss updates
- **Comprehensive Risk Management:** Fixed $2.00 risk per trade, configurable lot sizing
- **Profit Protection:** Sweet spot locking ($0.03-$0.10) and trailing stops
- **Market Filtering:** News avoidance, market close avoidance, volume validation
- **Complete Parity:** Identical logic in live and backtest modes
- **Thread Safety:** Per-position locks, continuous monitoring threads
- **Safety Guarantees:** Strict loss limits, broker constraint respect, rate limiting
- **Comprehensive Logging:** Structured logs, metrics tracking, performance monitoring

The system operates continuously, executing scalping trades with quality score ≥60, managing risk at $2.00 per trade, and protecting profits through immediate sweet spot locking and trailing stops.

---

**Document Status:** Final Production Reference  
**Last Updated:** System Refactoring Completion  
**Version:** Production Ready

