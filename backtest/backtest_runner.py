"""
Main Backtest Runner
Orchestrates the entire backtesting process.
"""

import json
import os
import sys
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import threading
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.market_data_provider import LiveMarketDataProvider, HistoricalMarketDataProvider
from backtest.order_execution_provider import LiveOrderExecutionProvider, SimulatedOrderExecutionProvider, OrderType
from backtest.historical_replay_engine import HistoricalReplayEngine
from backtest.performance_reporter import PerformanceReporter
from backtest.stress_test_modes import StressTestManager
from backtest.integration_layer import BacktestIntegration
from backtest.backtest_threading_manager import BacktestThreadingManager
from bot.trading_bot import TradingBot
from utils.logger_factory import get_logger

logger = get_logger("backtest_runner", "logs/backtest/runner.log")


class BacktestRunner:
    """Main backtest runner that orchestrates the entire backtesting process."""
    
    def __init__(self, config_path: str = 'config.json', config: Dict[str, Any] = None):
        """Initialize backtest runner."""
        # Load configuration
        if config is not None:
            self.config = config
        elif config_path:
            with open(config_path, 'r') as f:
                self.config = json.load(f)
        else:
            raise ValueError("Either config_path or config must be provided")
        
        # Check mode (allow override for testing)
        self.mode = self.config.get('mode', 'live')
        if self.mode != 'backtest':
            # Allow override for testing, but warn
            logger.warning(f"Config mode is '{self.mode}', but BacktestRunner expects 'backtest'. Proceeding anyway...")
        
        # Backtest configuration
        self.backtest_config = self.config.get('backtest', {})
        self.symbols = self.backtest_config.get('symbols', ['EURUSD'])
        self.start_date = datetime.fromisoformat(self.backtest_config.get('start_date', '2024-01-01T00:00:00'))
        self.end_date = datetime.fromisoformat(self.backtest_config.get('end_date', '2024-01-02T00:00:00'))
        self.timeframe = self.backtest_config.get('timeframe', 'M1')
        self.use_ticks = self.backtest_config.get('use_ticks', False)
        self.stress_tests = self.backtest_config.get('stress_tests', [])
        
        # Initialize components
        self.replay_engine = None
        self.market_data_provider = None
        self.order_execution_provider = None
        self.performance_reporter = PerformanceReporter(self.config)
        self.stress_test_manager = StressTestManager()
        
        # Trading bot (will be initialized with backtest providers)
        self.trading_bot = None
        
        # Backtest threading manager (simulates live threading behavior)
        self.threading_manager = None
        
        # State
        self.is_running = False
        self._lock = threading.Lock()
        self._last_progress_log_time = None
        self._last_step_count = 0
    
    def setup_backtest_environment(self):
        """Set up the backtest environment."""
        logger.info("Setting up backtest environment...")
        
        # Initialize replay engine
        self.replay_engine = HistoricalReplayEngine(
            config=self.config,
            symbols=self.symbols,
            start_date=self.start_date,
            end_date=self.end_date,
            timeframe=self.timeframe,
            use_ticks=self.use_ticks
        )
        
        # Load historical data
        if not self.replay_engine.load_historical_data():
            raise RuntimeError("Failed to load historical data. Make sure MT5 terminal is running and logged in.")
        
        # Check if we have data for at least one symbol
        if not self.replay_engine.historical_data and not self.replay_engine.tick_data:
            raise RuntimeError(f"No historical data loaded for any symbol. Symbols requested: {self.symbols}")
        
        # Apply stress tests if specified
        if self.stress_tests:
            logger.info(f"Applying stress tests: {self.stress_tests}")
            for symbol in self.symbols:
                if symbol in self.replay_engine.historical_data:
                    original_data = self.replay_engine.historical_data[symbol]
                    stressed_data = self.stress_test_manager.apply_stress(
                        original_data, symbol, self.stress_tests
                    )
                    self.replay_engine.historical_data[symbol] = stressed_data
        
        # Get actual data start time from replay engine
        actual_start = self.replay_engine.actual_data_start if hasattr(self.replay_engine, 'actual_data_start') and self.replay_engine.actual_data_start else self.start_date
        
        # Initialize market data provider
        self.market_data_provider = HistoricalMarketDataProvider(
            historical_data=self.replay_engine.historical_data,
            current_time=actual_start,
            account_balance=self.backtest_config.get('initial_balance', 10000.0)
        )
        
        logger.info(f"Market data provider initialized with start time: {actual_start}")
        
        # Initialize order execution provider
        self.order_execution_provider = SimulatedOrderExecutionProvider(
            market_data_provider=self.market_data_provider,
            config=self.config
        )
        
        # Register callbacks for both ticks and bars
        self.replay_engine.register_tick_callback(self._on_tick)
        self.replay_engine.register_bar_callback(self._on_tick)  # Use same callback for bars
        
        logger.info("Backtest environment setup complete")
    
    def _on_tick(self, current_time: datetime):
        """Callback when replay engine advances time."""
        # Update market data provider time
        self.market_data_provider.set_current_time(current_time)
        
        # Check for SL/TP hits
        closed_positions = self.order_execution_provider.check_sl_tp_hits()
        for closed_pos in closed_positions:
            self.performance_reporter.record_trade_closed(
                ticket=closed_pos['ticket'],
                close_price=closed_pos['close_price'],
                close_reason=closed_pos['close_reason'],
                profit=closed_pos['profit'],
                time=current_time
            )
        
        # Update account
        account_info = self.market_data_provider.get_account_info()
        if account_info:
            self.performance_reporter.record_account_snapshot(
                balance=account_info['balance'],
                equity=account_info['equity'],
                profit=account_info['profit'],
                time=current_time
            )
    
    def initialize_trading_bot(self):
        """Initialize trading bot with backtest providers."""
        logger.info("Initializing trading bot for backtest mode...")
        
        # Create modified config for backtest
        backtest_config = self.config.copy()
        backtest_config['mode'] = 'backtest'
        
        # Save original config path
        import tempfile
        import os
        temp_config_path = tempfile.mktemp(suffix='.json')
        with open(temp_config_path, 'w') as f:
            json.dump(backtest_config, f, indent=2)
        
        # Initialize bot normally (it will use MT5Connector and OrderManager)
        self.trading_bot = TradingBot(config_path=temp_config_path)
        
        # Inject backtest providers
        BacktestIntegration.inject_providers(
            self.trading_bot,
            self.market_data_provider,
            self.order_execution_provider,
            backtest_symbols=self.symbols  # Pass backtest symbols to PairFilter
        )
        
        # Connect bot (will use backtest providers)
        if not self.trading_bot.connect():
            raise RuntimeError("Failed to connect bot in backtest mode")
        
        # Initialize threading manager to simulate live thread behavior
        self.threading_manager = BacktestThreadingManager(
            config=self.config,
            market_data_provider=self.market_data_provider,
            trading_bot=self.trading_bot,
            order_execution_provider=self.order_execution_provider
        )
        
        # Register thread callbacks
        if hasattr(self.trading_bot, 'risk_manager') and hasattr(self.trading_bot.risk_manager, 'sl_manager'):
            sl_manager = self.trading_bot.risk_manager.sl_manager
            if sl_manager:
                # Create wrapper function that executes one iteration of SL worker logic
                def sl_worker_iteration():
                    """Execute one iteration of SL worker loop logic."""
                    try:
                        positions = self.order_execution_provider.get_open_positions()
                        if not positions:
                            return
                        
                        for position in positions:
                            ticket = position.get('ticket', 0)
                            if ticket:
                                # Call update_sl_atomic for each position (this is what the worker loop does)
                                sl_manager.update_sl_atomic(ticket, position)
                    except Exception as e:
                        logger.error(f"Error in SL worker iteration: {e}", exc_info=True)
                
                # Register SL worker callback
                self.threading_manager.register_thread_callback('sl_worker', sl_worker_iteration)
                logger.info("Registered SL worker loop callback")
        
        # Register run_cycle callback
        if hasattr(self.trading_bot, 'run_cycle'):
            self.threading_manager.register_thread_callback('run_cycle', self.trading_bot.run_cycle)
            logger.info("Registered run_cycle callback")
        
        logger.info("Trading bot initialized for backtest")
    
    def run_backtest(self, speed: float = 1.0):
        """Run the backtest."""
        logger.info("=" * 80)
        logger.info("STARTING BACKTEST")
        logger.info("=" * 80)
        logger.info(f"Symbols: {self.symbols}")
        logger.info(f"Period: {self.start_date} to {self.end_date}")
        logger.info(f"Timeframe: {self.timeframe}")
        logger.info(f"Speed: {speed}x")
        
        self.is_running = True
        start_time = time.time()
        
        try:
            # Run replay
            self.replay_engine.replay(speed=speed, step_callback=self._on_step)
            
        except Exception as e:
            logger.error(f"Error during backtest: {e}", exc_info=True)
            raise
        finally:
            self.is_running = False
            duration = time.time() - start_time
            logger.info(f"Backtest completed in {duration:.2f}s")
    
    def _on_step(self, current_time: datetime, step_count: int):
        """
        Callback for each replay step.
        
        CRITICAL: This now uses BacktestThreadingManager to simulate exact
        thread timing from live trading (50ms SL worker, 60s run_cycle, etc.)
        """
        # CRITICAL: Update market data provider time FIRST so all data queries use correct time
        if self.market_data_provider:
            self.market_data_provider.set_current_time(current_time)
        
        # Time-based progress logging (Issue #10 fix)
        current_time_sec = time.time()
        if self._last_progress_log_time is None:
            self._last_progress_log_time = current_time_sec
            self._last_step_count = step_count
        
        time_since_last_log = current_time_sec - self._last_progress_log_time
        steps_since_last_log = step_count - self._last_step_count
        
        # Log progress every 30 seconds OR every 5000 steps (whichever comes first)
        should_log = False
        if step_count % 5000 == 0 or step_count == 1:
            should_log = True
            log_level = 'info'
        elif time_since_last_log >= 30.0:  # Every 30 seconds
            should_log = True
            log_level = 'info'
        elif step_count % 1000 == 0:
            should_log = True
            log_level = 'debug'
        
        if should_log:
            # Calculate speed and ETA
            if steps_since_last_log > 0 and time_since_last_log > 0:
                steps_per_sec = steps_since_last_log / time_since_last_log
                total_steps = getattr(self.replay_engine, 'total_steps', None)
                if total_steps:
                    remaining_steps = total_steps - step_count
                    if steps_per_sec > 0:
                        eta_seconds = remaining_steps / steps_per_sec
                        eta_minutes = eta_seconds / 60.0
                        progress_pct = (step_count / total_steps) * 100
                        msg = f"Backtest progress: Step {step_count}/{total_steps} ({progress_pct:.1f}%) | Time: {current_time} | Speed: {steps_per_sec:.1f} steps/sec | ETA: {eta_minutes:.1f} min"
                    else:
                        msg = f"Backtest progress: Step {step_count}/{total_steps} | Time: {current_time} | Speed: {steps_per_sec:.1f} steps/sec"
                else:
                    msg = f"Backtest progress: Step {step_count} | Time: {current_time} | Speed: {steps_per_sec:.1f} steps/sec"
            else:
                msg = f"Backtest progress: Step {step_count} | Time: {current_time}"
            
            if log_level == 'info':
                logger.info(msg)
            else:
                logger.debug(msg)
            
            self._last_progress_log_time = current_time_sec
            self._last_step_count = step_count
        
        # Timeout detection (Issue #11 fix)
        if steps_since_last_log == 0 and time_since_last_log >= 300.0:  # 5 minutes without progress
            logger.error(f"TIMEOUT DETECTED: No progress for {time_since_last_log:.1f} seconds. Backtest may be stuck.")
            raise RuntimeError(f"Backtest timeout: No progress for {time_since_last_log:.1f} seconds at step {step_count}")
        
        # Memory monitoring (Issue #12 fix)
        if step_count % 10000 == 0:  # Check memory every 10k steps
            try:
                import psutil
                import os
                process = psutil.Process(os.getpid())
                memory_mb = process.memory_info().rss / 1024 / 1024
                if memory_mb > 1000:  # Warn if memory > 1GB
                    logger.warning(f"High memory usage detected: {memory_mb:.1f} MB at step {step_count}")
                else:
                    logger.debug(f"Memory usage: {memory_mb:.1f} MB at step {step_count}")
            except ImportError:
                pass  # psutil not available, skip memory monitoring
            except Exception as e:
                logger.debug(f"Error checking memory: {e}")
        
        # CRITICAL FIX: Use threading manager to execute threads at correct intervals
        # This ensures backtest matches live timing exactly (50ms SL worker, 60s run_cycle, etc.)
        if self.threading_manager:
            try:
                # Execute all threads that should run at this simulation time
                # The threading manager checks intervals and only executes threads that are due
                self.threading_manager.execute_threads(current_time)
            except Exception as e:
                logger.error(f"Error executing threads: {e}", exc_info=True)
        
        # Track SL updates for performance reporting
        if self.trading_bot and hasattr(self.trading_bot, 'risk_manager'):
            if hasattr(self.trading_bot.risk_manager, 'sl_manager'):
                sl_manager = self.trading_bot.risk_manager.sl_manager
                if sl_manager:
                    try:
                        # Get positions to track updates
                        positions = self.order_execution_provider.get_open_positions()
                        if positions:
                            for position in positions:
                                ticket = position.get('ticket', 0)
                                if ticket:
                                    # Record SL state for reporting
                                    current_profit = position.get('profit', 0.0)
                                    current_sl = position.get('sl', 0.0)
                                    
                                    # Check if profit locking was triggered (sweet spot range)
                                    if current_profit >= 0.03 and current_profit <= 0.10:
                                        self.performance_reporter.record_profit_lock(
                                            ticket, position.get('symbol', ''),
                                            current_profit, current_sl, current_time, True
                                        )
                    except Exception as e:
                        logger.debug(f"Error tracking SL updates: {e}")
    
    def generate_report(self, output_path: str = None) -> Dict[str, Any]:
        """Generate and save performance report."""
        if output_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = f"logs/backtest/report_{timestamp}.json"
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self.performance_reporter.save_report(output_path)
        
        report = self.performance_reporter.generate_report()
        
        # Print summary
        self._print_summary(report)
        
        return report
    
    def cleanup(self):
        """
        Clean up resources and close file handles.
        Call this after backtest completes to prevent file locking issues.
        """
        try:
            # Close integration layer if it exists
            if hasattr(self, 'integration') and self.integration:
                if hasattr(self.integration, 'shutdown'):
                    self.integration.shutdown()
            
            # Close threading manager if it exists
            if hasattr(self, 'threading_manager') and self.threading_manager:
                # Threading manager doesn't have file handles, but we can reset it
                self.threading_manager = None
            
            # Note: We don't close loggers here because they might be used elsewhere
            # The comprehensive test script handles logger cleanup explicitly
            logger.info("BacktestRunner cleanup completed")
        except Exception as e:
            logger.warning(f"Error during BacktestRunner cleanup: {e}")
    
    def _print_summary(self, report: Dict[str, Any]):
        """Print backtest summary to console."""
        summary = report['summary']
        print("\n" + "=" * 80)
        print("BACKTEST SUMMARY")
        print("=" * 80)
        print(f"Total Trades: {summary['total_trades']}")
        print(f"Winning Trades: {summary['winning_trades']}")
        print(f"Losing Trades: {summary['losing_trades']}")
        print(f"Win Rate: {summary['win_rate']:.2f}%")
        print(f"Net Profit: ${summary['net_profit']:.2f}")
        print(f"Max Drawdown: ${summary['max_drawdown']:.2f} ({summary['max_drawdown_pct']:.2f}%)")
        print(f"Profit Factor: {summary['profit_factor']:.2f}")
        print(f"Avg R:R: {summary['avg_rr']:.2f}")
        print("\n" + "-" * 80)
        print("SL PERFORMANCE")
        print("-" * 80)
        sl_perf = report['sl_performance']
        print(f"Success Rate: {sl_perf['success_rate']:.2f}%")
        print(f"Avg Delay: {sl_perf['avg_delay_ms']:.2f}ms")
        print(f"Max Delay: {sl_perf['max_delay_ms']:.2f}ms")
        print(f"Duplicate Updates: {sl_perf['duplicate_updates']}")
        print("\n" + "-" * 80)
        print("ANOMALIES")
        print("-" * 80)
        anomalies = report['anomalies']
        print(f"Total Anomalies: {anomalies['total']}")
        print(f"Early Exits: {anomalies['early_exits']}")
        print(f"Late Exits: {anomalies['late_exits']}")
        print(f"Missed SL Updates: {anomalies['missed_sl_updates']}")
        print(f"Duplicate Updates: {anomalies['duplicate_updates']}")
        print("=" * 80 + "\n")


def main():
    """Main entry point for backtest runner."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run historical backtest')
    parser.add_argument('--config', default='config.json', help='Config file path')
    parser.add_argument('--speed', type=float, default=1.0, help='Replay speed multiplier')
    parser.add_argument('--output', help='Output report path')
    
    args = parser.parse_args()
    
    # Check config mode
    with open(args.config, 'r') as f:
        config = json.load(f)
    
    if config.get('mode') != 'backtest':
        print("ERROR: Config must have mode='backtest'")
        print("Add to config.json:")
        print('  "mode": "backtest",')
        print('  "backtest": { ... }')
        sys.exit(1)
    
    # Run backtest
    runner = BacktestRunner(config_path=args.config)
    runner.setup_backtest_environment()
    runner.initialize_trading_bot()
    runner.run_backtest(speed=args.speed)
    runner.generate_report(output_path=args.output)


if __name__ == "__main__":
    main()

