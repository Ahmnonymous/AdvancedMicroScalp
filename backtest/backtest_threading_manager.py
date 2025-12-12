"""
Backtest Threading Manager
Simulates live threading behavior in backtest mode to ensure 1:1 equivalence.

This module creates simulated threads that run at the exact same intervals as live:
- SL worker thread: 50ms (or 0ms for instant trailing)
- Trailing stop thread: Same as SL worker
- Profit locking: Integrated into SL worker
- Position monitor: Continuous
- run_cycle: 60 seconds

All threads are synchronized with the historical replay time.
"""

import threading
import time
import queue
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime, timedelta

from utils.logger_factory import get_logger

logger = get_logger("backtest_threading", "logs/backtest/threading.log")


class BacktestThreadingManager:
    """
    Manages simulated threads in backtest mode.
    
    In backtest, we can't use real threads because we're replaying historical data.
    Instead, we simulate thread execution by calling their work functions at the
    correct intervals based on the replay time.
    """
    
    def __init__(self, config: Dict[str, Any], market_data_provider, trading_bot, order_execution_provider=None):
        """
        Initialize backtest threading manager.
        
        Args:
            config: Configuration dictionary
            market_data_provider: HistoricalMarketDataProvider instance
            trading_bot: TradingBot instance
            order_execution_provider: Order execution provider (for checking positions)
        """
        self.config = config
        self.market_data_provider = market_data_provider
        self.trading_bot = trading_bot
        self.order_execution_provider = order_execution_provider
        self.risk_config = config.get('risk', {})
        
        # Thread execution tracking
        self._thread_execution_times = {}  # {thread_name: last_execution_time}
        self._thread_intervals = {}  # {thread_name: interval_seconds}
        self._thread_callbacks = {}  # {thread_name: callback_function}
        self._thread_enabled = {}  # {thread_name: enabled}
        
        # Current simulation time (updated by replay engine)
        self._current_sim_time = None
        self._last_sim_time = None
        self._sim_time_lock = threading.Lock()
        
        # Execution queue for thread work
        self._execution_queue = queue.Queue()
        
        # Initialize thread configurations from config
        self._initialize_thread_configs()
        
        logger.info("BacktestThreadingManager initialized")
    
    def _initialize_thread_configs(self):
        """Initialize thread configurations from config."""
        # SL worker thread interval
        trailing_config = self.risk_config.get('trailing', {})
        configured_interval_ms = self.risk_config.get('trailing_cycle_interval_ms', 50)
        
        # If instant_trailing is enabled, force interval to 0ms
        if trailing_config.get('instant_trailing', False) or trailing_config.get('trigger_on_tick', False):
            configured_interval_ms = 0
            logger.info("[OK] Instant trailing enabled - SL updates will trigger instantly (0ms interval)")
        
        sl_worker_interval = configured_interval_ms / 1000.0  # Convert to seconds
        
        # Register threads
        self._thread_intervals['sl_worker'] = sl_worker_interval
        self._thread_intervals['run_cycle'] = 60.0  # 60 seconds
        self._thread_intervals['position_monitor'] = 1.0  # 1 second
        self._thread_intervals['trailing_stop'] = sl_worker_interval  # Same as SL worker
        self._thread_intervals['profit_locking'] = sl_worker_interval  # Integrated into SL worker
        
        # All threads enabled by default
        for thread_name in self._thread_intervals:
            self._thread_enabled[thread_name] = True
            self._thread_execution_times[thread_name] = None
        
        logger.info(f"Thread intervals configured: SL worker={sl_worker_interval*1000:.0f}ms, "
                   f"run_cycle=60s, position_monitor=1s")
    
    def update_simulation_time(self, current_time: datetime):
        """
        Update current simulation time.
        
        This is called by the replay engine on each step.
        """
        with self._sim_time_lock:
            self._last_sim_time = self._current_sim_time
            self._current_sim_time = current_time
    
    def get_current_simulation_time(self) -> Optional[datetime]:
        """Get current simulation time."""
        with self._sim_time_lock:
            return self._current_sim_time
    
    def register_thread_callback(self, thread_name: str, callback: Callable):
        """
        Register a callback function for a thread.
        
        Args:
            thread_name: Name of the thread ('sl_worker', 'run_cycle', etc.)
            callback: Function to call when thread should execute
        """
        self._thread_callbacks[thread_name] = callback
        logger.info(f"Registered callback for thread: {thread_name}")
    
    def execute_threads(self, current_time: datetime):
        """
        Execute all threads that should run at the current simulation time.
        
        This is called by the replay engine on each step.
        It checks which threads should execute based on their intervals
        and the elapsed simulation time.
        
        Args:
            current_time: Current simulation time
        """
        self.update_simulation_time(current_time)
        
        if self._last_sim_time is None:
            # First execution - initialize all threads
            self._last_sim_time = current_time
            for thread_name in self._thread_intervals:
                self._thread_execution_times[thread_name] = current_time
        
        # Calculate time delta since last execution
        time_delta = (current_time - self._last_sim_time).total_seconds()
        
        if time_delta <= 0:
            # No time has passed, don't execute any threads
            return
        
        # OPTIMIZATION: For instant SL worker (0ms interval), check if there are positions first
        # This avoids unnecessary callback execution when there are no positions
        has_positions = None  # Cache check result
        
        # Check each thread to see if it should execute
        for thread_name, interval in self._thread_intervals.items():
            if not self._thread_enabled.get(thread_name, False):
                continue
            
            # OPTIMIZATION: Skip instant SL worker if no positions exist
            if thread_name == 'sl_worker' and interval == 0:
                if has_positions is None:
                    # Check if there are any open positions (only once per step)
                    try:
                        if self.order_execution_provider:
                            positions = self.order_execution_provider.get_open_positions()
                            has_positions = len(positions) > 0 if positions else False
                        elif hasattr(self.trading_bot, 'order_manager'):
                            positions = self.trading_bot.order_manager.get_open_positions()
                            has_positions = len(positions) > 0 if positions else False
                        else:
                            has_positions = False
                    except Exception:
                        has_positions = False
                
                if not has_positions:
                    # Skip SL worker execution when no positions exist
                    continue
            
            last_exec = self._thread_execution_times.get(thread_name)
            if last_exec is None:
                # First execution for this thread
                self._thread_execution_times[thread_name] = current_time
                self._execute_thread(thread_name, current_time)
            else:
                # Check if enough time has passed
                elapsed = (current_time - last_exec).total_seconds()
                
                # For instant threads (interval = 0), execute on every step
                if interval == 0:
                    self._execute_thread(thread_name, current_time)
                    self._thread_execution_times[thread_name] = current_time
                elif elapsed >= interval:
                    # Enough time has passed, execute thread
                    self._execute_thread(thread_name, current_time)
                    self._thread_execution_times[thread_name] = current_time
        
        self._last_sim_time = current_time
    
    def _execute_thread(self, thread_name: str, current_time: datetime):
        """
        Execute a specific thread's work function.
        
        Args:
            thread_name: Name of the thread to execute
            current_time: Current simulation time
        """
        callback = self._thread_callbacks.get(thread_name)
        if callback is None:
            return
        
        try:
            # Execute the callback
            if thread_name == 'sl_worker':
                # SL worker loop - call one iteration
                callback()
            elif thread_name == 'run_cycle':
                # Trading cycle - call run_cycle
                callback()
            elif thread_name == 'position_monitor':
                # Position monitor - call monitoring function
                callback()
            elif thread_name == 'trailing_stop':
                # Trailing stop - integrated into SL worker, but can be called separately
                callback()
            elif thread_name == 'profit_locking':
                # Profit locking - integrated into SL worker
                callback()
            else:
                # Generic callback
                callback()
        except Exception as e:
            logger.error(f"Error executing thread {thread_name}: {e}", exc_info=True)
    
    def enable_thread(self, thread_name: str):
        """Enable a thread."""
        self._thread_enabled[thread_name] = True
        logger.info(f"Thread enabled: {thread_name}")
    
    def disable_thread(self, thread_name: str):
        """Disable a thread."""
        self._thread_enabled[thread_name] = False
        logger.info(f"Thread disabled: {thread_name}")
    
    def get_thread_status(self) -> Dict[str, Any]:
        """Get status of all threads."""
        status = {}
        for thread_name in self._thread_intervals:
            status[thread_name] = {
                'enabled': self._thread_enabled.get(thread_name, False),
                'interval': self._thread_intervals.get(thread_name, 0),
                'last_execution': self._thread_execution_times.get(thread_name),
                'has_callback': thread_name in self._thread_callbacks
            }
        return status

