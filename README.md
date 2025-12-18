# MT5 Trading Bot

Deterministic MT5 trading bot with unified stop-loss (SL) management, strict risk controls, and a SIM_LIVE certification harness for validating live behavior under synthetic market conditions.

## 1. PROJECT OVERVIEW

- **What this is**: Python MT5 trading system that scans markets, opens and manages trades, and enforces SL/risk rules in real time. Core orchestration is in `bot/trading_bot.py`, with `launch_system.py` as the main live-system launcher and `backtest/run_backtest.py` as the backtest entrypoint.
- **Supported markets**: Whatever symbols are available via the configured MT5 broker and pass `risk/pair_filter.py` (FX, indices, metals, crypto, stocks, etc. as provided by the broker).
- **Design philosophy**:
  - **Deterministic core**: All trade, risk, and SL logic flows through well-defined components (`RiskManager`, `SLManager`, `OrderManager`, `TrendFilter`, filters).
  - **Safety-first**: Hard per-trade loss caps, monotonic SL movement, broker-constraint checks, lock-based SL updates, and watchdogs.
  - **Certified behavior**: SIM_LIVE scenarios (`sim_live/intent_driven_scenarios.py` + legacy scenarios in `sim_live/scenarios.py`) and backtest infrastructure (`backtest/`) validate that live-like behavior (especially SL and risk) is consistent and deterministic.

## 2. EXECUTION MODES

System behavior is controlled by `config.json` (and optional backtest configs) via the `mode` key:

- **LIVE**:
  - **Mode**: `mode` not equal to `"backtest"` or `"SIM_LIVE"` (typically default live mode).
  - **Connector**: Real `execution/mt5_connector.py` connected to MT5 terminal/broker.
  - **Execution**: Orders are sent to the real broker via `execution/order_manager.py`; fills, SL, and closures are real.
  - **Timing**:
    - Main bot cycle interval: `trading.cycle_interval_seconds` (default 60s).
    - SL worker: interval from `risk.trailing_cycle_interval_ms` (enforced minimum 50ms unless instant trailing is enabled).
  - **Entry point**: `launch_system.py` (recommended) or scripts in `scripts/`.

- **SIM_LIVE**:
  - **Mode**: `mode` set to `"SIM_LIVE"` in `config.json`.
  - **Connector**: `sim_live.sim_live_connector.SimLiveMT5Connector` replaces `MT5Connector`, backed by:
    - `sim_live.synthetic_market_engine.SyntheticMarketEngine` (synthetic candles, ticks).
    - `sim_live.synthetic_broker.SyntheticBroker` (simulated account and order execution).
  - **Execution**:
    - Bot runs its **normal live logic** (trend filtering, risk, SL, micro-profit, profit locking) against a synthetic broker.
    - MT5 direct calls are patched via `sim_live.synthetic_mt5_wrapper.inject_mt5_mock`.
  - **Timing**: Same main loop and SL worker cadence as live; scenario scripts drive price evolution deterministically.
  - **Purpose**: Certification harness to assert that entry, SL, trailing, and profit-locking behavior match declared intents under controlled synthetic scenarios. Entry point is `run_sim_live.py`.

- **BACKTEST**:
  - **Mode**: `mode` set to `"backtest"` (via `config.json` and/or `backtest/config_backtest.json`).
  - **Connector / Providers**:
    - Backtest integration layer (`backtest/integration_layer.py`) wraps the live-like interfaces, supplying historical prices and simulated order execution while preserving `TradingBot` interfaces.
  - **Execution**:
    - Historical replay via `backtest/historical_replay_engine.py`.
    - `backtest/backtest_runner.py` orchestrates bot initialization and replay, including optional stress-test modes (`backtest/stress_test_modes.py`).
  - **Timing**:
    - Driven by replay loop; `backtest/run_backtest.py` supports real-speed (`--real-speed`) or accelerated replay (`--speed`).
  - **Purpose**: Deterministic historical verification of strategy and SL behavior, with architecture aligned to live mode.

**Shared logic across modes**:
- `bot/trading_bot.py` (orchestrator), `risk/risk_manager.py`, `risk/sl_manager.py`, `execution/order_manager.py`, filters, and `strategies/trend_filter.py` are shared.
- SL, risk, and filters behave identically across LIVE, SIM_LIVE, and BACKTEST; only data providers and execution adapters differ.

## 3. HIGH-LEVEL SYSTEM FLOW

### 3.1 Startup

- **LIVE**:
  - `launch_system.py`:
    - Loads `config.json`.
    - Instantiates `TradingBot(config_path='config.json')`.
    - Connects to MT5 via `TradingBot.connect()`.
    - Starts:
      - Bot main loop thread (`TradingBot.run`).
      - Real-time monitoring (`monitor/realtime_bot_monitor.py`).
      - Reconciliation (`monitor/realtime_reconciliation.py`).
      - Comprehensive bot monitor, SL real-time monitor, SL worker (`SLManager.start_sl_worker()` with `monitor.sl_watchdog.SLWatchdog`).
      - Lightweight real-time logger (`monitor/lightweight_realtime_logger.py`).
- **SIM_LIVE**:
  - `run_sim_live.py`:
    - Loads `config.json` with `mode="SIM_LIVE"`.
    - Configures scenarios and synthetic environment.
    - Instantiates `TradingBot` (which internally selects `SimLiveMT5Connector`).
    - Runs deterministic scenario-driven execution and asserts behavior via `sim_live.assertive_validation.ScenarioValidator`.
- **BACKTEST**:
  - `backtest/run_backtest.py`:
    - Loads `config.json` plus optional `backtest/config_backtest.json`.
    - Forces `config['mode'] = 'backtest'`.
    - Validates configuration (`backtest/config_validator.py`).
    - Instantiates `backtest.BacktestRunner`, sets up backtest environment and bot, then runs replay.

### 3.2 Market Scanning

`TradingBot.run()` performs repeated cycles:

- **Pair discovery and filtering**:
  - `risk/pair_filter.py` discovers broker symbols and filters by:
    - Spread, commission, trade mode, swap rules, and broker-specific constraints.
    - In backtest mode, some strict checks can be relaxed (test mode).
  - Result is a candidate list of tradeable symbols.

- **Per-symbol screening** (for each candidate symbol):
  - **News filter** (`news_filter/news_api.py`):
    - Blocks trading around high-impact news within a configurable window (minimum ±10 minutes).
  - **Market closing filter** (`filters/market_closing_filter.py`):
    - Blocks trading within `market_closing.minutes_before_close` (default 30 minutes) of market close.
  - **Volume filter** (`filters/volume_filter.py`):
    - Ensures sufficient minimum volume/liquidity per symbol.

- **Trend and signal evaluation**:
  - `strategies/trend_filter.TrendFilter`:
    - Computes a quality score (0–100) and direction (LONG/SHORT).
    - Enforces minimum quality threshold (`trading.min_quality_score`, default 60).
  - Only symbols meeting filter and trend criteria are forwarded to risk/entry.

### 3.3 Signal Validation and Entry Decision

For each symbol that passes filters and achieves sufficient quality:
  - **RiskManager** (`risk/risk_manager.py`):
    - Verifies per-trade risk cap (`risk.max_risk_per_trade_usd`, default 2.0 USD).
    - Enforces `risk.max_open_trades` (supports unlimited via special values).
    - Computes lot size (`default_lot_size`, default 0.01; may increase up to 0.05 if broker minimum requires).
  - **Randomization / throttle**:
    - Optional randomness factor (`trading.randomness_factor`, default ~0.25) may skip some otherwise valid trades to control frequency.
  - **Order placement** (`execution/order_manager.py`):
    - Places market orders using `OrderType` and real MT5 connector (or SIM_LIVE/backtest equivalent).
    - Applies initial SL per the hard risk limit (see SL architecture) and any broker constraints.
  - **Logging**:
    - `trade_logging/trade_logger.py` records trade entry, including ticket, symbol, direction, price, SL, volume, and quality metrics.

### 3.4 Trade Lifecycle

- **Position monitoring** (`execution/position_monitor.PositionMonitor`):
  - Runs in its own thread, continuously polling broker positions.
  - Detects position openings/closures and records them to trade logs.

- **SL management**:
  - Centralized in `risk/sl_manager.SLManager` (see section 5).
  - Background worker thread applies SL updates and verifies them.

- **Profit protection**:
  - **ProfitLockingEngine** (`bot/profit_locking_engine.py`):
    - Advanced profit locking logic within sweet spot and beyond, integrated with `SLManager`.
  - **MicroProfitEngine** (`bot/micro_profit_engine.py`):
    - Optional micro-profit capturing logic used as an intentional SL bypass for very small gains (see section 5).

- **Halal compliance**:
  - `risk/halal_compliance.HalalCompliance`:
    - May close positions violating defined compliance constraints (e.g., overnight holds), bypassing normal SL but with strict logging.

### 3.5 Exit Paths

Positions can close via:

- **Broker-enforced exits**:
  - **Hard SL**: Price hits stop-loss; broker closes position.
  - **Take profit (TP)**: If configured, price reaches TP; broker closes.

- **SL-manager-driven exits**:
  - **Sweet spot profit lock**:
    - When profit enters the configured sweet spot (e.g., 0.03–0.10 USD), SL is raised to at least breakeven and may be further tightened by `ProfitLockingEngine`.
  - **Trailing SL**:
    - For larger profits (above sweet spot), SL follows price in increments, never moving backward.

- **Intentional bypass closures**:
  - **MicroProfitEngine**:
    - Closes positions directly when in micro profit range, after validating SL state and satisfying internal safety checks.
  - **HalalCompliance**:
    - Closes open positions that violate compliance rules, bypassing SL/TP; all actions are logged with clear prefixes for audit.

- **Logging and verification**:
  - Every closure path (SL hit, TP, trailing, micro-profit, compliance) is logged with reason and P/L.
  - Monitoring tools in `monitor/` reconcile broker data with internal logs.

## 4. THREADING & CONCURRENCY MODEL

The system is multi-threaded by design. Key concurrent components:

- **Main bot loop**:
  - Runs in a dedicated thread launched by `TradingSystemLauncher` (`launch_system.py`).
  - Executes scan → filter → entry logic once per `trading.cycle_interval_seconds`.

- **SLManager worker**:
  - `SLManager.start_sl_worker()` spawns a dedicated worker thread (`_sl_worker_loop`) plus a background worker for heavy tasks.
  - Cadence:
    - Interval derived from `risk.trailing_cycle_interval_ms` (converted to seconds).
    - If `risk.trailing.instant_trailing` (or `trigger_on_tick`) is enabled, interval may be forced to 0ms for tick-driven updates; otherwise, a minimum of 50ms is enforced to reduce contention.
  - Responsibilities:
    - Iterates over open positions.
    - Applies strict loss, sweet-spot, and trailing logic under per-ticket locks.
    - Handles SL verification and backoff/retry.

- **SL watchdog and real-time monitor**:
  - `monitor.sl_watchdog.SLWatchdog`:
    - Observes lock hold times and worker health; can trigger recovery if locks are stuck beyond configured time.
  - `monitor.sl_realtime_monitor.SLRealtimeMonitor`:
    - Monitors SL state, effective SL in P/L terms, and violation flags per ticket.

- **Profit locking and trailing monitors**:
  - Logic is encapsulated in `SLManager` and `ProfitLockingEngine` and executed in the SL worker thread.
  - Additional metrics and tracking are maintained in background worker queues.

- **Position monitor**:
  - `execution.position_monitor.PositionMonitor` runs in its own thread, polling broker positions and logging exits.

- **Monitoring and reconciliation**:
  - `monitor.realtime_bot_monitor.RealtimeBotMonitor` and `monitor.realtime_reconciliation.RealtimeReconciliation` run in background threads.
  - A separate summary display loop shows non-blocking real-time stats (console-only).

**Locking philosophy**:

- **Per-ticket locking**:
  - `SLManager` maintains a dictionary of threading locks keyed by ticket.
  - All SL updates per ticket go through `update_sl_atomic()` and must acquire the corresponding lock.

- **Global rate limiting and lock watchdog**:
  - Global RPC rate limiter controls the total number of SL modifications per second.
  - Lock acquisition timeouts and a lock watchdog enforce maximum hold times and prevent deadlocks.
  - Stale locks can be force-released within bounded time, with events logged for review.

- **Priority: safety over speed**:
  - SL updates are designed to be fast, but **never** at the expense of monotonic SL guarantees and thread safety.
  - Where contention is detected, the system backs off instead of risking inconsistent SL state.

## 5. RISK MANAGEMENT & SL ARCHITECTURE

### 5.1 Hard SL enforcement

- **Per-trade risk cap**:
  - `risk.max_risk_per_trade_usd` (default 2.0 USD) defines the maximum intended loss per trade.
  - `RiskManager` computes lot size so that initial SL corresponds approximately to this dollar risk, given symbol pip value and contract size.
  - Each order is opened with an SL configured to respect this cap.

- **SL verification in P/L terms**:
  - `SLManager` computes effective SL in profit units for each position to validate that the actual broker SL aligns with the intended loss limit.
  - If the effective SL indicates greater than permitted loss, emergency enforcement is triggered to move SL closer to the limit, with strict retry and circuit-breaker behavior for failing tickets.

### 5.2 Trailing SL logic

- **Activation**:
  - Trailing logic activates when profit exceeds the sweet-spot range (exact thresholds from `risk.profit_locking.*`).
  - Trailing increments are controlled via `risk.trailing_stop_increment_usd` and `risk.elastic_trailing` parameters.

- **Behavior**:
  - SL moves in discrete increments as price moves favorably, never decreasing (see monotonic guarantee below).
  - Elastic trailing logic can allow controlled pullbacks without immediate SL tightening, to avoid premature exits, while still tightening SL as net profit grows.
  - Large favorable jumps may trigger more aggressive profit locks.

### 5.3 Profit locking (sweet spot)

- **Sweet spot range**:
  - Configured via `risk.profit_locking.min_profit_threshold_usd` and `.max_profit_threshold_usd` (e.g., 0.03–0.10 USD).
  - When a position’s profit enters this range:
    - `SLManager` immediately attempts to move SL to at least breakeven.
    - `ProfitLockingEngine` may further refine the SL placement within or above the sweet spot.

- **No break-even delay**:
  - Break-even logic is **disabled** by configuration; there is no requirement for profit to remain positive for a duration.
  - SL is moved as soon as conditions indicate safe locking in the sweet spot.

### 5.4 Micro-profit engine and intentional bypasses

- **MicroProfitEngine** (`bot/micro_profit_engine.py`):
  - Operates on **profitable positions only**, never losses:
    - Enforces a minimum profit buffer of **\$0.05** (`min_profit_buffer`) before any close attempt.
    - Explicitly rejects any position at or below the hard SL (≤ -\$2.00).
  - Intentionally bypasses normal SL-managed exits by closing the position directly when **micro profit** conditions are met:
    - Primary band: profit in the configured sweet spot range (e.g. \$0.03–\$0.10).
    - Extended band: profit above the sweet spot when near clean \$0.10 multiples (micro-HFT style).
  - Includes multiple safety checks:
    - Verifies SL and effective SL state before closing (to ensure the position is in a valid, protected state).
    - Re-checks profit against the buffer at several checkpoints (pre-close, after fresh MT5 fetch, final pre-close).
    - Cancels closure if profit drops below the buffer at any checkpoint.

- **HalalCompliance**:
  - Monitors positions for compliance constraints (e.g., overnight holds).
  - Closes positions that violate rules, bypassing SL/TP; all actions are logged with clear prefixes for audit.

### 5.5 Emergency & fallback mechanisms

- **Emergency SL enforcement**:
  - If verification detects that SL is not within acceptable loss bounds, `SLManager` can:
    - Apply emergency SL updates.
    - Use exponential backoff and retry logic on failure up to `sl_update_max_retries`.

- **Circuit breakers and disabled symbols**:
  - Repeated failures for a ticket can trigger a per-ticket circuit breaker that temporarily disables SL updates for that ticket.
  - Symbols with persistent issues may be temporarily disabled from SL updates and flagged for manual review.

- **Verification tolerances**:
  - Configured tolerances (e.g., `effective_profit_tolerance_usd`, `price_tolerance_multiplier`) determine how strict verification is against broker-applied SL.

### 5.6 Guarantees

- **Monotonic SL**:
  - Within SL-managed paths (strict loss, sweet spot, trailing), SL is **never moved in a direction that increases potential loss**.
  - For BUY positions:
    - SL is only moved up or stays unchanged.
  - For SELL positions:
    - SL is only moved down or stays unchanged.

- **No backward movement**:
  - SL update logic checks previous SL before applying a new one; any candidate SL that would reduce protection is rejected.

## 6. CERTIFIED SCENARIOS (CRITICAL)

SIM_LIVE certification is encoded primarily in **intent-driven scenarios** in `sim_live/intent_driven_scenarios.py`, with validation logic in `sim_live/assertive_validation.py`. The following scenarios and behaviors are **CERTIFIED**:

- **Scenario: `certified_buy_profit_trailing_exit`** (status: **CERTIFIED**)
  - **Purpose**: BUY trend continuation where the strategy naturally enters long, then experiences profit, profit lock, trailing, and exit via SL in profit.
  - **Expected behavior**:
    - After a warm-up, the bot opens a BUY trade within `max_cycles_to_entry` (5 scan cycles).
    - Price moves through the sweet spot (\$0.03–\$0.10), SL is locked as sweet-spot profit, then trailing activates as profit grows.
    - Scenario checks (`verify_sl_lock`, `verify_trailing`) confirm that SL has advanced and remained monotonic, and that the eventual exit is via profit SL.

- **Scenario: `certified_sell_sl_lock_reversal_profit`** (status: **CERTIFIED**)
  - **Purpose**: SELL trend where SL locks in profit and a subsequent reversal exits the trade at the locked/trailing SL, not at the original hard SL.
  - **Expected behavior**:
    - A SELL trade opens within 5 scan cycles into a strong downtrend.
    - Price moves into profit, sweet-spot SL lock is applied, then trailing progresses.
    - A scripted reversal moves price back against the position; the trade exits at the tightened SL with profit (`expect_exit='SL_PROFIT'`), never falling back to the original hard SL.

- **Scenario: `certified_false_trend_rejected`** (status: **CERTIFIED**)
  - **Purpose**: Ensure that weak/false trend conditions are **rejected** by the entry pipeline.
  - **Expected behavior**:
    - Market context is deliberately weak (low SMA separation, low candle quality, low ADX).
    - No trade is opened (`expect_trade=False`); rejection occurs within `max_cycles_to_reject` (3 cycles).
    - The final rejection reason contains `QUALITY_SCORE` (or equivalent quality-based rejection text).

- **Scenario: `certified_high_spread_rejected`** (status: **CERTIFIED**)
  - **Purpose**: Validate that high-spread conditions are rejected at the risk check level.
  - **Expected behavior**:
    - Scenario sets an abnormally high spread (e.g. `spread_points: 20`) during otherwise valid trend conditions.
    - No trade is opened; rejection occurs quickly with `rejection_reason` including `RISK_CHECK_SPREAD`.
    - Confirms that spread-based risk checks have priority over otherwise good setups.

- **Scenario: `certified_lock_contention_stress`** (status: **CERTIFIED**)
  - **Purpose**: Stress-test SL lock contention with rapid price updates while still enforcing sweet-spot locking.
  - **Expected behavior**:
    - A BUY trade opens and price is moved in many small, rapid increments to stress per-ticket locks and rate limits.
    - SL is still locked in the sweet spot (`expected_lock_type='sweet_spot'`), with no deadlocks or SL regressions.
    - Confirms that SLManager’s locking and rate limiting hold under contention.

- **Scenario: `certified_buy_hard_sl_loss`** (status: **CERTIFIED**)
  - **Purpose**: Validate **hard SL loss** behavior for an adverse move without any profit-locking or trailing.
  - **Expected behavior**:
    - A BUY entry opens under strong-trend conditions.
    - Price then moves deterministically and substantially against the position (e.g. ~20 pips for EURUSD) until the hard SL is hit.
    - Exit is via hard SL (`expect_exit='HARD_SL'`), confirming strict-loss enforcement without accidental profit locking.

Legacy price-script scenarios in `sim_live/scenarios.py` (such as `natural_buy_trend_continuation`, `natural_sell_trend_continuation`, `profit_zone_then_reversal`, `trailing_stop_multiple_updates`, `lock_contention_stress`, and the legacy alias `profit_zone_entry`) are still available and used for additional validation, but **only the `certified_*` scenarios above are treated as CERTIFIED invariants**.

**Assertion system invariants** (`sim_live/assertive_validation.ScenarioValidator`):

- Contract satisfaction (SMA separation, RSI range, candle quality) must pass before entry is validated; if not, the scenario fails loudly as a **data/contract** problem.
- When `intent.expect_trade` is `True`, a trade **must** open within `intent.max_cycles_to_entry` (or equivalent bound), or the scenario fails with detailed rejection reasons.
- When `intent.expect_trade` is `False`, no trade may open; if one does, the scenario fails and logs the unexpected entry.

⚠️ **These scenarios and assertions define system invariants and MUST NOT be broken.** Any change that causes these scenarios or assertions to fail invalidates the certification of the SL and entry pipeline.

## 7. DIRECTORY STRUCTURE

High-level structure (selected directories):

```
.
├── bot/            # Main trading bot orchestrator and profit engines
├── execution/      # MT5 connector, order placement, position monitoring
├── risk/           # Risk management, pair filtering, SL manager, compliance
├── strategies/     # Strategy logic (e.g., TrendFilter)
├── filters/        # Market closing and volume filters
├── news_filter/    # News-based trading filter
├── sim_live/       # SIM_LIVE synthetic broker, engine, scenarios, validation, logging
├── backtest/       # Backtest engine, integration layer, stress modes, reports
├── monitor/        # Real-time monitoring, reconciliation, watchdogs, SL monitors
├── trade_logging/  # Trade logging per symbol
├── config/         # Symbol overrides and config helpers
├── docs/           # System documentation (including `FINAL_SYSTEM_SUMMARY.md`)
├── logs/           # Live and backtest logs (engine, system, trades, reports)
├── scripts/        # Convenience scripts to run bot and monitoring
├── utils/          # Utility modules (logging factory, execution tracer, etc.)
├── tests/          # Test suite
├── verification/   # Verification helpers and analysis tools
├── launch_system.py
├── run_sim_live.py
└── backtest/run_backtest.py
```

**Responsibilities**:

- **`execution/`**:
  - `mt5_connector.py`: Real MT5 connectivity for LIVE/BACKTEST.
  - `order_manager.py`: Order placement/modify/close; enforces SL at order level.
  - `position_monitor.py`: Monitors open positions and logs exits (threaded).

- **`risk/`**:
  - `risk_manager.py`: Position sizing, max open trades, risk caps, integration with SL and profit engines.
  - `sl_manager.py`: Unified SL management (strict loss, sweet spot, trailing) with per-ticket locks and verification.
  - `pair_filter.py`: Symbol discovery and per-broker constraints.
  - `halal_compliance.py`: Compliance-based closures.

- **`engine/` (conceptually)**:
  - Core execution, SL, and risk logic spans `execution/` + `risk/` + `bot/` orchestrator; there is no single `engine/` directory, but `docs/FINAL_SYSTEM_SUMMARY.md` describes the engine architecture.

- **`sim_live/`**:
  - `synthetic_market_engine.py`: Synthetic market data and candles; supports deterministic scripts.
  - `synthetic_broker.py`: Synthetic order execution, account, and position states.
  - `synthetic_mt5_wrapper.py`: Injects MT5 mocks for parts of code calling MetaTrader API directly.
  - `sim_live_connector.py`: Drop-in MT5Connector replacement for SIM_LIVE (uses synthetic broker/engine).
  - `intent_driven_scenarios.py`: **Certified** intent-driven scenarios (see section 6).
  - `scenarios.py`: Legacy deterministic price-script scenarios (used in addition to certified ones).
  - `assertive_validation.py`: Contract- and intent-based assertions for SIM_LIVE scenarios.
  - `sim_live_logger.py`: SIM_LIVE-specific logging helpers.

- **`backtest/`**:
  - `backtest_runner.py`, `historical_replay_engine.py`, `integration_layer.py`, `market_data_provider.py`, `order_execution_provider.py`, `performance_reporter.py`, `stress_test_modes.py`, `test_scenarios.py`: Historical replay, environment setup, and reporting.

- **`monitor/`**:
  - `realtime_bot_monitor.py`, `realtime_reconciliation.py`, `comprehensive_bot_monitor.py`, `sl_realtime_monitor.py`, `sl_watchdog.py`, and related tools for live monitoring and reconciliation of SL state and trades.

## 8. LOGGING & DEBUGGING

- **Log locations**:
  - **Live engine logs**: `logs/live/engine/` (e.g., `sl_manager.log`, trend detector logs).
  - **Live system logs**: `logs/live/system/` (e.g., `system_startup.log`, `system_errors.log`, `summary_metrics.log`).
  - **Trade logs**: `logs/live/trades/` (per-symbol entry/exit logs).
  - **Backtest logs**: `logs/backtest/...` (mirrors live logging structure for backtests).
  - **Reports**: `logs/reports/` (JSON/summary outputs).

- **Major log roles**:
  - **SL logs** (`logs/**/engine/sl_manager.log`):
    - SL updates, effective SL calculations, verification results, emergency enforcement, and circuit breaker events.
  - **Monitor logs** (`logs/live/monitor/*.log`):
    - SL real-time monitor, watchdog alerts, health checks, HFT metrics.
  - **System logs**:
    - Startup and error logs track component initialization and fatal/non-fatal errors.
  - **Trade logs**:
    - Entry and exit details per symbol, including reason codes (SL hit, TP, trailing, micro-profit, Halal, etc.).

- **Diagnosing SL/trailing/profit lock issues**:
  - Use:
    - `logs/live/engine/sl_manager.log` for rule evaluation and SL failures.
    - `logs/live/monitor/sl_monitor.log` for violations and effective SL states.
    - `logs/live/system/summary_metrics.log` for periodic account and position summaries.
  - Cross-check with per-symbol trade logs to confirm the exact exit path.

## 9. COMMON FAILURE MODES & SAFETY GUARDS

- **Lock contention and stale locks**:
  - **Protections**:
    - Per-ticket lock acquisition timeouts (`lock_acquisition_timeout_seconds`, `profit_locking_lock_timeout_seconds`).
    - Lock watchdog checks for `lock_max_hold_time` breaches and can force-release locks.
  - **Behavior**:
    - When contention is detected, SL updates may be skipped for that cycle; invariants remain preserved through monotonic SL rules and rate limits.

- **Rate limit / RPC saturation**:
  - **Protections**:
    - Global RPC limiter (`_global_rpc_max_per_second`) and per-ticket `sl_update_min_interval_ms` to prevent MT5 or connector overload.
  - **Behavior**:
    - Updates beyond rate limits are postponed, not forced; SL state remains safe under hard SL and last-valid SL invariants.

- **Broker-side temp errors / rejections**:
  - **Protections**:
    - Bounded retry loops with exponential backoff for SL modifications.
    - Circuit-breaker to avoid infinite loops on persistently failing tickets.
  - **Behavior**:
    - Failures are logged; if exceeding thresholds, SL updates for specific tickets/symbols may be disabled temporarily.

- **Data issues (stale prices, symbol issues)**:
  - **Protections**:
    - `MT5Connector` and `SimLiveMT5Connector` check price staleness and reject invalid quotes.
    - `PairFilter` blocks symbols with invalid broker metadata (e.g., spread, trade mode).
  - **Behavior**:
    - Symbols failing checks are excluded; system continues trading on healthy symbols.

- **Why retries do not halt execution**:
  - Retries and circuit breakers are local to specific tickets/symbols.
  - Global trading continues where safe, under the assumption that transient broker failures should not bring down the entire bot.

## 10. CONTRIBUTION RULES (FOR HUMANS & AI)

**What MUST NOT be changed without re-certification**:

- **Certified SL and risk invariants**:
  - Core behavior of `risk/sl_manager.py`, `risk/risk_manager.py`, and `execution/order_manager.py` that enforces:
    - Hard per-trade loss cap.
    - Sweet-spot profit locking range and immediate lock semantics.
    - Monotonic SL updates (no backward movement).
    - Locking and rate-limiting behavior.
- **Certified SIM_LIVE scenarios and assertions**:
  - Scenario definitions in `sim_live/scenarios.py`.
  - Assertion logic in `sim_live/assertive_validation.py`.
  - Any change that causes these to fail or become inconsistent with how the bot behaves is not acceptable.

**Safe modification guidelines**:

- Do **not** introduce any new code paths that bypass:
  - `SLManager.update_sl_atomic()` for SL updates.
  - `RiskManager` for position sizing and trade permission.
- If you add or modify strategies, filters, or execution logic:
  - Keep interfaces consistent with `TradingBot`, `MT5Connector`/`SimLiveMT5Connector`, `OrderManager`, `RiskManager`, and `SLManager`.
  - Ensure new behavior is compatible with existing SL/risk invariants.

**Required validation after changes**:

- After any change in:
  - Risk or SL logic.
  - Order management or connectors.
  - Trend filter behavior that affects entry timing.
- You **must**:
  - Re-run relevant SIM_LIVE scenarios (`run_sim_live.py`) and confirm they pass.
  - Re-run backtests (`backtest/run_backtest.py`) for a representative symbol set and timeframe.
  - Inspect SL-related logs for violations or anomalies.

Failure to re-run and pass certified scenarios invalidates the system’s safety guarantees and is not permitted for production use.
