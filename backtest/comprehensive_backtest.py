#!/usr/bin/env python3
"""
Comprehensive Multi-Symbol Backtest System
Downloads historical data, runs stress tests, tracks metrics, and generates reports.
"""

import os
import sys
import json
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.backtest_runner import BacktestRunner
from backtest.historical_replay_engine import HistoricalReplayEngine
from backtest.stress_test_modes import StressTestManager
from backtest.performance_reporter import PerformanceReporter
from utils.logger_factory import get_logger

logger = get_logger("comprehensive_backtest", "logs/backtest/comprehensive_backtest.log")


class ComprehensiveBacktest:
    """Comprehensive backtest system with multi-symbol support and stress testing."""
    
    # Symbol groups (use only symbols that actually exist in MT5)
    # Note: Many brokers use 'm' suffix for indices and crypto
    FOREX_SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'EURUSDm', 'GBPUSDm', 'USDJPYm']  # Try both versions
    INDICES_SYMBOLS = ['US500m', 'NAS100m', 'US30m']  # Use 'm' versions which are more common
    CRYPTO_SYMBOLS = ['BTCUSDm', 'ETHUSDm']  # Use 'm' versions which are more common
    
    # Stress test scenarios
    STRESS_SCENARIOS = [
        'high_volatility',
        'extreme_spread',
        'fast_reversals',
        'tick_gaps',
        'slippage_spikes',
        'market_dead',
        'candle_anomalies'
    ]
    
    def __init__(self, config_path: str = 'config.json', output_dir: str = None):
        """
        Initialize comprehensive backtest system.
        
        Args:
            config_path: Path to configuration file
            output_dir: Output directory for reports (default: logs/backtest/multi_symbol_stress_test)
        """
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Set output directory
        if output_dir is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            self.output_dir = Path(f"logs/backtest/multi_symbol_stress_test_{timestamp}")
        else:
            self.output_dir = Path(output_dir)
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        self.reports_dir = self.output_dir / 'reports'
        self.data_dir = self.output_dir / 'data'
        self.logs_dir = self.output_dir / 'logs'
        self.reports_dir.mkdir(exist_ok=True)
        self.data_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        
        # Initialize components
        self.stress_test_manager = StressTestManager()
        
        # Results storage
        self.results = {}  # {symbol: {scenario: {metrics, report}}}
        self.global_metrics = {}
        self.fixes_applied = []
        
        # Auto-fix configuration
        self.max_retries = 3
        self.auto_fix_enabled = True
        
        logger.info(f"Comprehensive backtest initialized. Output directory: {self.output_dir}")
    
    def discover_available_symbols(self) -> Dict[str, List[str]]:
        """
        Discover available symbols in MT5 by querying the broker.
        
        Returns:
            Dictionary with keys: 'forex', 'indices', 'crypto' containing lists of available symbols
        """
        import MetaTrader5 as mt5
        
        logger.info("Discovering available symbols from MT5...")
        
        # Initialize MT5
        mt5_config = self.config.get('mt5', {})
        if not mt5.initialize(path=mt5_config.get('path', '')):
            error = mt5.last_error()
            logger.error(f"Failed to initialize MT5 for symbol discovery: {error}")
            return {'forex': [], 'indices': [], 'crypto': []}
        
        # Login if credentials provided
        if mt5_config.get('account') and mt5_config.get('password'):
            account = int(mt5_config['account'])
            password = mt5_config['password']
            server = mt5_config.get('server', '')
            
            if not mt5.login(account, password=password, server=server):
                error = mt5.last_error()
                logger.warning(f"MT5 login failed for symbol discovery: {error}. Continuing without login...")
        
        # Get all symbols
        all_symbols = mt5.symbols_get()
        if all_symbols is None:
            logger.error("Failed to get symbols from MT5")
            mt5.shutdown()
            return {'forex': [], 'indices': [], 'crypto': []}
        
        # Convert to list of symbol names
        all_symbol_names = [s.name for s in all_symbols]
        
        logger.info(f"Found {len(all_symbol_names)} total symbols in MT5")
        
        # Define search patterns for each category
        forex_patterns = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'NZDUSD', 'USDCHF']
        indices_patterns = ['US500', 'NAS100', 'US30', 'SP500', 'NASDAQ', 'DOW', 'DJ30']
        crypto_patterns = ['BTCUSD', 'ETHUSD', 'BTC', 'ETH', 'XRP', 'LTC']
        
        # Find matching symbols (case-insensitive, partial match)
        found_symbols = {
            'forex': [],
            'indices': [],
            'crypto': []
        }
        
        # Search for Forex symbols
        for pattern in forex_patterns:
            matches = [s for s in all_symbol_names if pattern.upper() in s.upper()]
            for match in matches:
                if match not in found_symbols['forex']:
                    found_symbols['forex'].append(match)
        
        # Search for Indices symbols
        for pattern in indices_patterns:
            matches = [s for s in all_symbol_names if pattern.upper() in s.upper()]
            for match in matches:
                if match not in found_symbols['indices']:
                    found_symbols['indices'].append(match)
        
        # Search for Crypto symbols
        for pattern in crypto_patterns:
            matches = [s for s in all_symbol_names if pattern.upper() in s.upper()]
            for match in matches:
                if match not in found_symbols['crypto']:
                    found_symbols['crypto'].append(match)
        
        # Verify symbols have data by checking if we can get rates
        verified_symbols = {
            'forex': [],
            'indices': [],
            'crypto': []
        }
        
        for category in ['forex', 'indices', 'crypto']:
            for symbol in found_symbols[category]:
                # Try to get recent rates to verify symbol has data
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
                if rates is not None and len(rates) > 0:
                    verified_symbols[category].append(symbol)
                    logger.debug(f"[OK] {symbol} ({category}): Has data")
                else:
                    logger.debug(f"✗ {symbol} ({category}): No data available")
        
        mt5.shutdown()
        
        # Log summary
        logger.info(f"\n{'='*80}")
        logger.info(f"SYMBOL DISCOVERY SUMMARY")
        logger.info(f"{'='*80}")
        logger.info(f"Forex symbols found: {len(verified_symbols['forex'])}")
        if verified_symbols['forex']:
            logger.info(f"  {', '.join(verified_symbols['forex'])}")
        logger.info(f"Indices symbols found: {len(verified_symbols['indices'])}")
        if verified_symbols['indices']:
            logger.info(f"  {', '.join(verified_symbols['indices'])}")
        logger.info(f"Crypto symbols found: {len(verified_symbols['crypto'])}")
        if verified_symbols['crypto']:
            logger.info(f"  {', '.join(verified_symbols['crypto'])}")
        logger.info(f"{'='*80}\n")
        
        return verified_symbols
    
    def download_historical_data(self, symbols: List[str], start_date: datetime, 
                                end_date: datetime, timeframe: str = 'M1') -> Dict[str, pd.DataFrame]:
        """
        Download historical data for multiple symbols.
        
        Args:
            symbols: List of symbols to download
            start_date: Start date
            end_date: End date
            timeframe: Timeframe (M1, M5, H1, etc.)
        
        Returns:
            Dictionary of {symbol: DataFrame} with historical data
        """
        logger.info(f"Downloading historical data for {len(symbols)} symbols...")
        logger.info(f"Period: {start_date} to {end_date}")
        logger.info(f"Timeframe: {timeframe}")
        
        # Use HistoricalReplayEngine to load data
        replay_engine = HistoricalReplayEngine(
            config=self.config,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            timeframe=timeframe,
            use_ticks=False
        )
        
        if not replay_engine.load_historical_data():
            raise RuntimeError("Failed to load historical data")
        
        # Check data completeness
        data_completeness = {}
        for symbol in symbols:
            if symbol in replay_engine.historical_data:
                df = replay_engine.historical_data[symbol]
                if len(df) > 0:
                    actual_start = df['time'].min()
                    actual_end = df['time'].max()
                    expected_days = (end_date - start_date).days
                    actual_days = (actual_end - actual_start).days
                    completeness = (actual_days / expected_days * 100) if expected_days > 0 else 0
                    
                    data_completeness[symbol] = {
                        'bars': len(df),
                        'start': actual_start,
                        'end': actual_end,
                        'completeness_pct': completeness,
                        'missing_periods': []
                    }
                    
                    # Check for gaps
                    df_sorted = df.sort_values('time')
                    time_diffs = df_sorted['time'].diff()
                    if timeframe == 'M1':
                        expected_diff = timedelta(minutes=1)
                    elif timeframe == 'M5':
                        expected_diff = timedelta(minutes=5)
                    elif timeframe == 'H1':
                        expected_diff = timedelta(hours=1)
                    else:
                        expected_diff = timedelta(minutes=1)
                    
                    # Find gaps (differences > 2x expected)
                    gaps = time_diffs[time_diffs > expected_diff * 2]
                    if len(gaps) > 0:
                        gap_indices = gaps.index.tolist()
                        for idx in gap_indices[:10]:  # Log first 10 gaps
                            gap_start = df_sorted.iloc[idx - 1]['time']
                            gap_end = df_sorted.iloc[idx]['time']
                            data_completeness[symbol]['missing_periods'].append({
                                'start': gap_start,
                                'end': gap_end,
                                'duration_minutes': (gap_end - gap_start).total_seconds() / 60
                            })
                    
                    logger.info(f"{symbol}: {len(df)} bars, {completeness:.1f}% complete "
                              f"({actual_start} to {actual_end})")
                    if len(gaps) > 0:
                        logger.warning(f"{symbol}: {len(gaps)} data gaps detected")
                else:
                    logger.warning(f"{symbol}: No data loaded")
                    data_completeness[symbol] = {'bars': 0, 'completeness_pct': 0}
            else:
                logger.warning(f"{symbol}: Symbol not found in loaded data")
                data_completeness[symbol] = {'bars': 0, 'completeness_pct': 0}
        
        # Save data completeness report
        completeness_file = self.data_dir / 'data_completeness.json'
        with open(completeness_file, 'w') as f:
            json.dump(data_completeness, f, indent=2, default=str)
        
        logger.info(f"Data completeness report saved to {completeness_file}")
        
        # Save downloaded data to disk for verification/reuse
        saved_count = 0
        for symbol, df in replay_engine.historical_data.items():
            if len(df) > 0:
                data_file = self.data_dir / f"{symbol}_historical_data.csv"
                df.to_csv(data_file, index=False)
                saved_count += 1
                logger.debug(f"Saved {symbol} data to {data_file} ({len(df)} bars)")
        
        # Summary of download results
        found_symbols = [s for s in symbols if s in replay_engine.historical_data and len(replay_engine.historical_data[s]) > 0]
        missing_symbols = [s for s in symbols if s not in found_symbols]
        
        logger.info(f"\n{'='*80}")
        logger.info(f"DATA DOWNLOAD SUMMARY")
        logger.info(f"{'='*80}")
        logger.info(f"Successfully downloaded: {len(found_symbols)} symbols")
        if found_symbols:
            logger.info(f"  Found: {', '.join(found_symbols)}")
        if missing_symbols:
            logger.warning(f"  Missing: {', '.join(missing_symbols)}")
            logger.warning(f"  These symbols may not be available in your MT5 account or may have different names")
        logger.info(f"Data saved to: {self.data_dir}")
        logger.info(f"{'='*80}\n")
        
        return replay_engine.historical_data
    
    def run_backtest_with_stress(self, symbol: str, scenario: str, 
                                 historical_data: Dict[str, pd.DataFrame],
                                 start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """
        Run backtest for a symbol with a specific stress scenario.
        
        Args:
            symbol: Trading symbol
            scenario: Stress test scenario name
            historical_data: Historical data dictionary
            start_date: Start date
            end_date: End date
        
        Returns:
            Dictionary with metrics and report
        """
        logger.info(f"Running backtest: {symbol} with scenario: {scenario}")
        
        # Create modified config for this backtest
        backtest_config = self.config.copy()
        backtest_config['mode'] = 'backtest'
        backtest_config['backtest'] = {
            'symbols': [symbol],
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'timeframe': 'M1',
            'use_ticks': False,
            'stress_tests': [scenario] if scenario != 'baseline' else [],
            'initial_balance': 10000.0
        }
        
        # Apply stress test to data if not baseline
        stressed_data = historical_data.copy()
        if scenario != 'baseline' and symbol in stressed_data:
            original_df = stressed_data[symbol].copy()
            stressed_df = self.stress_test_manager.apply_stress(original_df, symbol, [scenario])
            stressed_data[symbol] = stressed_df
            logger.info(f"Applied stress test '{scenario}' to {symbol}")
        
        # Initialize replay engine with already-downloaded data
        replay_engine = HistoricalReplayEngine(
            config=backtest_config,
            symbols=[symbol],
            start_date=start_date,
            end_date=end_date,
            timeframe=backtest_config['backtest']['timeframe'],
            use_ticks=False
        )
        
        # Inject the already-downloaded (and possibly stressed) data
        replay_engine.historical_data = stressed_data
        
        # Set actual data start/end from the data we have
        if symbol in stressed_data and len(stressed_data[symbol]) > 0:
            df = stressed_data[symbol]
            replay_engine.actual_data_start = df['time'].min()
            replay_engine.actual_data_end = df['time'].max()
        else:
            # No data for this symbol, skip
            logger.warning(f"No data available for {symbol}, skipping backtest")
            return {
                'metrics': {},
                'report': {},
                'success': False,
                'error': f'No data available for {symbol}'
            }
        
        # Initialize backtest runner
        runner = BacktestRunner(config=backtest_config)
        
        # CRITICAL: Inject the replay engine with our already-downloaded data
        # This prevents BacktestRunner from trying to download data again from MT5
        runner.replay_engine = replay_engine
        
        # Setup backtest environment using our pre-downloaded data
        # We bypass setup_backtest_environment() to avoid re-downloading from MT5
        try:
            # Initialize market data provider and order execution provider directly
            # using the data we already downloaded
            actual_start = replay_engine.actual_data_start if replay_engine.actual_data_start else start_date
            
            # Initialize market data provider with our data
            from backtest.market_data_provider import HistoricalMarketDataProvider
            runner.market_data_provider = HistoricalMarketDataProvider(
                historical_data=stressed_data,
                current_time=actual_start,
                account_balance=backtest_config['backtest'].get('initial_balance', 10000.0)
            )
            
            # Initialize order execution provider
            from backtest.order_execution_provider import SimulatedOrderExecutionProvider
            runner.order_execution_provider = SimulatedOrderExecutionProvider(
                market_data_provider=runner.market_data_provider,
                config=backtest_config
            )
            
            # Register callbacks
            runner.replay_engine.register_tick_callback(runner._on_tick)
            runner.replay_engine.register_bar_callback(runner._on_tick)
            
            # Initialize trading bot
            logger.info(f"Initializing trading bot for {symbol} {scenario}...")
            try:
                runner.initialize_trading_bot()
                logger.info(f"Trading bot initialized successfully for {symbol} {scenario}")
            except Exception as e:
                logger.error(f"Failed to initialize trading bot for {symbol} {scenario}: {e}", exc_info=True)
                raise
            
            # Run backtest with error handling and progress logging
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    data_bars = len(stressed_data.get(symbol, pd.DataFrame()))
                    logger.info(f"Starting backtest replay for {symbol} {scenario} (attempt {retry_count + 1}/{self.max_retries})...")
                    logger.info(f"Data: {data_bars} bars to replay")
                    
                    # Estimate time (roughly 100 bars per second at 100x speed)
                    estimated_seconds = data_bars / 100.0
                    logger.info(f"Estimated replay time: {estimated_seconds:.1f} seconds")
                    
                    # Run backtest with progress callback
                    replay_start = time.time()
                    runner.run_backtest(speed=100.0)  # Very fast replay for backtesting (100x speed)
                    replay_duration = time.time() - replay_start
                    logger.info(f"Backtest replay completed for {symbol} {scenario} in {replay_duration:.1f}s")
                    break
                except Exception as e:
                    retry_count += 1
                    error_msg = str(e)
                    logger.error(f"Backtest failed (attempt {retry_count}/{self.max_retries}): {error_msg}")
                    
                    if retry_count < self.max_retries and self.auto_fix_enabled:
                        fix_applied = self._apply_auto_fix(error_msg, backtest_config)
                        if fix_applied:
                            logger.info(f"Auto-fix applied, retrying...")
                            # Reinitialize replay engine with same data
                            replay_engine = HistoricalReplayEngine(
                                config=backtest_config,
                                symbols=[symbol],
                                start_date=start_date,
                                end_date=end_date,
                                timeframe=backtest_config['backtest']['timeframe'],
                                use_ticks=False
                            )
                            replay_engine.historical_data = stressed_data
                            if symbol in stressed_data and len(stressed_data[symbol]) > 0:
                                df = stressed_data[symbol]
                                replay_engine.actual_data_start = df['time'].min()
                                replay_engine.actual_data_end = df['time'].max()
                            
                            # Reinitialize backtest runner
                            runner = BacktestRunner(config=backtest_config)
                            runner.replay_engine = replay_engine
                            
                            # Setup with our data (skip MT5 download)
                            actual_start = replay_engine.actual_data_start if replay_engine.actual_data_start else start_date
                            from backtest.market_data_provider import HistoricalMarketDataProvider
                            runner.market_data_provider = HistoricalMarketDataProvider(
                                historical_data=stressed_data,
                                current_time=actual_start,
                                account_balance=backtest_config['backtest'].get('initial_balance', 10000.0)
                            )
                            from backtest.order_execution_provider import SimulatedOrderExecutionProvider
                            runner.order_execution_provider = SimulatedOrderExecutionProvider(
                                market_data_provider=runner.market_data_provider,
                                config=backtest_config
                            )
                            runner.replay_engine.register_tick_callback(runner._on_tick)
                            runner.replay_engine.register_bar_callback(runner._on_tick)
                            runner.initialize_trading_bot()
                        else:
                            logger.warning("No auto-fix available, continuing with error...")
                            break
                    else:
                        raise
            
            # Generate report
            report = runner.generate_report()
            
            # Get metrics
            metrics = runner.performance_reporter.calculate_metrics()
            
            return {
                'metrics': metrics,
                'report': report,
                'success': True
            }
            
        except Exception as e:
            error_trace = traceback.format_exc()
            logger.error(f"Backtest failed for {symbol} scenario {scenario}: {e}")
            logger.error(error_trace)
            
            return {
                'metrics': {},
                'report': {},
                'success': False,
                'error': str(e),
                'traceback': error_trace
            }
    
    def _apply_auto_fix(self, error_msg: str, config: Dict[str, Any]) -> bool:
        """
        Apply automatic fixes based on error message.
        
        Args:
            error_msg: Error message
            config: Configuration dictionary to modify
        
        Returns:
            True if fix was applied, False otherwise
        """
        fix_applied = False
        
        # Fix: Increase lock timeout
        if 'lock' in error_msg.lower() and 'timeout' in error_msg.lower():
            if 'risk' not in config:
                config['risk'] = {}
            old_timeout = config['risk'].get('lock_acquisition_timeout_seconds', 1.0)
            new_timeout = old_timeout * 2.0
            config['risk']['lock_acquisition_timeout_seconds'] = new_timeout
            self.fixes_applied.append({
                'fix': 'increase_lock_timeout',
                'old_value': old_timeout,
                'new_value': new_timeout,
                'reason': error_msg
            })
            fix_applied = True
            logger.info(f"Auto-fix: Increased lock timeout from {old_timeout}s to {new_timeout}s")
        
        # Fix: Increase SL update interval
        if 'sl' in error_msg.lower() and ('rate' in error_msg.lower() or 'interval' in error_msg.lower()):
            if 'risk' not in config:
                config['risk'] = {}
            old_interval = config['risk'].get('sl_update_min_interval_ms', 100)
            new_interval = old_interval * 2
            config['risk']['sl_update_min_interval_ms'] = new_interval
            self.fixes_applied.append({
                'fix': 'increase_sl_interval',
                'old_value': old_interval,
                'new_value': new_interval,
                'reason': error_msg
            })
            fix_applied = True
            logger.info(f"Auto-fix: Increased SL update interval from {old_interval}ms to {new_interval}ms")
        
        # Fix: Reduce cycle interval
        if 'cycle' in error_msg.lower() or 'worker' in error_msg.lower():
            if 'trading' not in config:
                config['trading'] = {}
            old_interval = config['trading'].get('cycle_interval_seconds', 60)
            new_interval = max(10, old_interval // 2)
            config['trading']['cycle_interval_seconds'] = new_interval
            self.fixes_applied.append({
                'fix': 'reduce_cycle_interval',
                'old_value': old_interval,
                'new_value': new_interval,
                'reason': error_msg
            })
            fix_applied = True
            logger.info(f"Auto-fix: Reduced cycle interval from {old_interval}s to {new_interval}s")
        
        return fix_applied
    
    def run_comprehensive_backtest(self, months: int = 2, timeframe: str = 'M1'):
        """
        Run comprehensive backtest for all symbols and scenarios.
        
        Args:
            months: Number of months of historical data to use
            timeframe: Timeframe for data (M1, M5, H1)
        """
        logger.info("=" * 80)
        logger.info("STARTING COMPREHENSIVE BACKTEST")
        logger.info("=" * 80)
        
        # Calculate date range
        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=months * 30)
        
        logger.info(f"Date range: {start_date} to {end_date} ({months} months)")
        
        # Step 0: Discover available symbols from MT5
        logger.info("Step 0: Discovering available symbols from MT5...")
        discovered_symbols = self.discover_available_symbols()
        
        # Combine discovered symbols
        all_symbols = []
        seen = set()
        
        # Add discovered symbols (prioritize these as they're verified to exist)
        for category_symbols in discovered_symbols.values():
            for symbol in category_symbols:
                if symbol not in seen:
                    all_symbols.append(symbol)
                    seen.add(symbol)
        
        # If no symbols discovered, fall back to hardcoded list
        if not all_symbols:
            logger.warning("No symbols discovered from MT5, using fallback list...")
            for symbol_list in [self.FOREX_SYMBOLS, self.INDICES_SYMBOLS, self.CRYPTO_SYMBOLS]:
                for symbol in symbol_list:
                    if symbol not in seen:
                        all_symbols.append(symbol)
                        seen.add(symbol)
        
        logger.info(f"Will attempt to download data for {len(all_symbols)} symbols: {', '.join(all_symbols)}")
        
        # Download historical data
        logger.info("Step 1: Downloading historical data...")
        historical_data = self.download_historical_data(
            all_symbols, start_date, end_date, timeframe
        )
        
        # Filter symbols that have data
        available_symbols = [s for s in all_symbols if s in historical_data and len(historical_data[s]) > 0]
        logger.info(f"Available symbols with data: {available_symbols}")
        
        if not available_symbols:
            logger.error("No symbols with data available. Cannot run backtest.")
            logger.error("Tried symbols: " + ", ".join(all_symbols))
            logger.error("Loaded symbols: " + ", ".join(historical_data.keys()))
            return
        
        # Log summary of what will be tested
        total_scenarios = len(['baseline'] + self.STRESS_SCENARIOS)
        total_backtests = len(available_symbols) * total_scenarios
        logger.info(f"Will run {total_backtests} backtests ({len(available_symbols)} symbols × {total_scenarios} scenarios)")
        logger.info(f"Estimated time: ~{total_backtests * 2} minutes (2 min per backtest)")
        
        # Run backtests for each symbol and scenario
        logger.info("Step 2: Running backtests with stress scenarios...")
        
        scenarios = ['baseline'] + self.STRESS_SCENARIOS
        
        for symbol in available_symbols:
            logger.info(f"\n{'='*80}")
            logger.info(f"Processing symbol: {symbol}")
            logger.info(f"{'='*80}")
            
            self.results[symbol] = {}
            
            for scenario_idx, scenario in enumerate(scenarios, 1):
                logger.info(f"\n{'-'*80}")
                logger.info(f"Scenario {scenario_idx}/{len(scenarios)}: {scenario}")
                logger.info(f"{'-'*80}")
                
                scenario_start_time = time.time()
                try:
                    result = self.run_backtest_with_stress(
                        symbol, scenario, historical_data, start_date, end_date
                    )
                    self.results[symbol][scenario] = result
                    
                    scenario_duration = time.time() - scenario_start_time
                    
                    if result.get('success'):
                        metrics = result.get('metrics', {})
                        logger.info(f"[OK] {symbol} {scenario} completed in {scenario_duration:.1f}s: "
                                  f"Trades: {metrics.get('total_trades', 0)}, "
                                  f"SL Success: {metrics.get('sl_update_success_rate', 0):.1f}%, "
                                  f"Profit: ${metrics.get('net_profit', 0):.2f}")
                    else:
                        logger.error(f"✗ {symbol} {scenario} failed after {scenario_duration:.1f}s: "
                                   f"{result.get('error', 'Unknown error')}")
                
                except KeyboardInterrupt:
                    logger.warning(f"Backtest interrupted by user at {symbol} {scenario}")
                    raise
                except Exception as e:
                    scenario_duration = time.time() - scenario_start_time
                    logger.error(f"Error running {symbol} {scenario} after {scenario_duration:.1f}s: {e}", exc_info=True)
                    self.results[symbol][scenario] = {
                        'success': False,
                        'error': str(e),
                        'traceback': traceback.format_exc()
                    }
        
        # Generate comprehensive reports
        logger.info("\n" + "=" * 80)
        logger.info("Step 3: Generating comprehensive reports...")
        logger.info("=" * 80)
        
        self._generate_comprehensive_reports()
        
        logger.info("\n" + "=" * 80)
        logger.info("COMPREHENSIVE BACKTEST COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Results saved to: {self.output_dir}")
    
    def _generate_comprehensive_reports(self):
        """Generate comprehensive reports for all symbols and scenarios."""
        # Summary report
        summary = {
            'timestamp': datetime.now().isoformat(),
            'symbols_tested': list(self.results.keys()),
            'scenarios_tested': ['baseline'] + self.STRESS_SCENARIOS,
            'fixes_applied': self.fixes_applied,
            'results': {}
        }
        
        # Per-symbol summary
        for symbol, scenarios in self.results.items():
            summary['results'][symbol] = {}
            for scenario, result in scenarios.items():
                if result.get('success'):
                    metrics = result.get('metrics', {})
                    summary['results'][symbol][scenario] = {
                        'trades': metrics.get('total_trades', 0),
                        'sl_success_rate': metrics.get('sl_update_success_rate', 0.0),
                        'profit_lock_rate': metrics.get('profit_lock_activation_rate', 0.0),
                        'worker_loop_avg_ms': metrics.get('worker_loop_avg_duration_ms', 0.0),
                        'net_profit': metrics.get('net_profit', 0.0),
                        'win_rate': metrics.get('win_rate', 0.0),
                        'anomalies': metrics.get('total_anomalies', 0),
                        'exceptions': metrics.get('exceptions', 0)
                    }
                else:
                    summary['results'][symbol][scenario] = {
                        'success': False,
                        'error': result.get('error', 'Unknown error')
                    }
        
        # Save summary JSON
        summary_file = self.reports_dir / 'summary.json'
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f"Summary report saved: {summary_file}")
        
        # Generate CSV reports
        self._generate_csv_reports()
        
        # Generate detailed JSON reports per symbol/scenario
        for symbol, scenarios in self.results.items():
            for scenario, result in scenarios.items():
                if result.get('success'):
                    report_file = self.reports_dir / f"{symbol}_{scenario}_detailed.json"
                    with open(report_file, 'w') as f:
                        json.dump(result.get('report', {}), f, indent=2, default=str)
    
    def _generate_csv_reports(self):
        """Generate CSV reports for easy analysis."""
        # Trades summary CSV
        trades_data = []
        for symbol, scenarios in self.results.items():
            for scenario, result in scenarios.items():
                if result.get('success'):
                    metrics = result.get('metrics', {})
                    trades_data.append({
                        'symbol': symbol,
                        'scenario': scenario,
                        'total_trades': metrics.get('total_trades', 0),
                        'winning_trades': metrics.get('winning_trades', 0),
                        'losing_trades': metrics.get('losing_trades', 0),
                        'win_rate': metrics.get('win_rate', 0.0),
                        'net_profit': metrics.get('net_profit', 0.0),
                        'max_drawdown': metrics.get('max_drawdown', 0.0),
                        'profit_factor': metrics.get('profit_factor', 0.0)
                    })
        
        if trades_data:
            df_trades = pd.DataFrame(trades_data)
            csv_file = self.reports_dir / 'trades_summary.csv'
            df_trades.to_csv(csv_file, index=False)
            logger.info(f"Trades summary CSV saved: {csv_file}")
        
        # SL performance CSV
        sl_data = []
        for symbol, scenarios in self.results.items():
            for scenario, result in scenarios.items():
                if result.get('success'):
                    metrics = result.get('metrics', {})
                    sl_data.append({
                        'symbol': symbol,
                        'scenario': scenario,
                        'sl_success_rate': metrics.get('sl_update_success_rate', 0.0),
                        'sl_total_updates': metrics.get('sl_update_total', 0),
                        'sl_failed_updates': metrics.get('sl_update_failed', 0),
                        'sl_avg_delay_ms': metrics.get('sl_update_avg_delay_ms', 0.0),
                        'sl_max_delay_ms': metrics.get('sl_update_max_delay_ms', 0.0),
                        'duplicate_updates': metrics.get('sl_update_duplicate_updates', 0)
                    })
        
        if sl_data:
            df_sl = pd.DataFrame(sl_data)
            csv_file = self.reports_dir / 'sl_performance.csv'
            df_sl.to_csv(csv_file, index=False)
            logger.info(f"SL performance CSV saved: {csv_file}")
        
        # Worker loop performance CSV
        worker_data = []
        for symbol, scenarios in self.results.items():
            for scenario, result in scenarios.items():
                if result.get('success'):
                    metrics = result.get('metrics', {})
                    worker_data.append({
                        'symbol': symbol,
                        'scenario': scenario,
                        'avg_duration_ms': metrics.get('worker_loop_avg_duration_ms', 0.0),
                        'max_duration_ms': metrics.get('worker_loop_max_duration_ms', 0.0),
                        'min_duration_ms': metrics.get('worker_loop_min_duration_ms', 0.0),
                        'timing_violations': metrics.get('worker_loop_timing_violations', 0)
                    })
        
        if worker_data:
            df_worker = pd.DataFrame(worker_data)
            csv_file = self.reports_dir / 'worker_loop_performance.csv'
            df_worker.to_csv(csv_file, index=False)
            logger.info(f"Worker loop performance CSV saved: {csv_file}")
        
        # Anomalies CSV
        anomalies_data = []
        for symbol, scenarios in self.results.items():
            for scenario, result in scenarios.items():
                if result.get('success'):
                    metrics = result.get('metrics', {})
                    anomalies = metrics.get('anomalies', {})
                    anomalies_data.append({
                        'symbol': symbol,
                        'scenario': scenario,
                        'total_anomalies': metrics.get('total_anomalies', 0),
                        'exceptions': metrics.get('exceptions', 0),
                        **{f'anomaly_{k}': v for k, v in anomalies.items()}
                    })
        
        if anomalies_data:
            df_anomalies = pd.DataFrame(anomalies_data)
            csv_file = self.reports_dir / 'anomalies.csv'
            df_anomalies.to_csv(csv_file, index=False)
            logger.info(f"Anomalies CSV saved: {csv_file}")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run comprehensive multi-symbol backtest')
    parser.add_argument('--config', default='config.json', help='Config file path')
    parser.add_argument('--months', type=int, default=2, help='Number of months of historical data')
    parser.add_argument('--timeframe', default='M1', help='Timeframe (M1, M5, H1)')
    parser.add_argument('--output', help='Output directory')
    
    args = parser.parse_args()
    
    # Run comprehensive backtest
    backtest = ComprehensiveBacktest(config_path=args.config, output_dir=args.output)
    backtest.run_comprehensive_backtest(months=args.months, timeframe=args.timeframe)


if __name__ == "__main__":
    main()

