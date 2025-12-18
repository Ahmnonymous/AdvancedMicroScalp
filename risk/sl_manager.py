"""
Unified Stop-Loss (SL) Management System
Handles all SL logic: strict loss, break-even, sweet-spot, and trailing stops.

This module provides:
- Atomic SL updates (all rules applied in one operation)
- Thread-safe per-ticket locking
- Contract size auto-correction
- BUY/SELL symmetric handling
- Broker constraint respect (stops_level, spread)
- Comprehensive logging
"""

import threading
import time
import logging
import json as json_module
import csv
import queue
import math
import traceback
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, Callable
from collections import defaultdict
from pathlib import Path

from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from utils.logger_factory import get_logger, get_system_event_logger
from utils.execution_tracer import get_tracer
from utils import system_health

# Module-level logger - will be reinitialized in __init__ based on mode
logger = None
system_event_logger = get_system_event_logger()


class SLManager:
    """
    Unified Stop-Loss Manager
    
    Handles all SL logic in priority order:
    1. Strict loss enforcement (-$2.00) if P/L < 0
    2. Sweet-spot profit locking if profit ≥ $0.03 and ≤ $0.10 (immediate, no break-even)
    3. Trailing stop if profit > $0.10
    
    NOTE: No break-even logic - lock profit immediately at sweet spot or above per Step 2c requirement
    """
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector, order_manager: OrderManager):
        """
        Initialize SL Manager.
        
        Args:
            config: Configuration dictionary
            mt5_connector: MT5Connector instance
            order_manager: OrderManager instance
        """
        # CRITICAL FIX: Initialize logger based on mode (backtest vs live)
        # This prevents backtest from writing to live log files
        global logger
        is_backtest = config.get('mode') == 'backtest'
        log_path = "logs/backtest/engine/sl_manager.log" if is_backtest else "logs/live/engine/sl_manager.log"
        logger = get_logger("sl_manager", log_path)
        
        # MANDATORY OBSERVABILITY: Log initialization immediately to prove SLManager is alive
        trailing_config = config.get('risk', {}).get('trailing', {})
        instant_trailing = trailing_config.get('instant_trailing', False) or trailing_config.get('trigger_on_tick', False)
        strict_loss = config.get('risk', {}).get('max_risk_per_trade_usd', 2.0)
        logger.info(f"[OK][SL_MANAGER_INIT] instant_trailing={instant_trailing} strict_loss=${strict_loss:.2f} log_path={log_path}")
        
        self.config = config
        self.risk_config = config.get('risk', {})
        self.mt5_connector = mt5_connector
        self.order_manager = order_manager
        
        # Configuration
        self.max_risk_usd = self.risk_config.get('max_risk_per_trade_usd', 2.0)
        self.trailing_increment_usd = self.risk_config.get('trailing_stop_increment_usd', 0.10)
        self.min_lock_increment_usd = self.risk_config.get('elastic_trailing', {}).get('min_lock_increment_usd', 0.10)
        
        # SL update configuration
        execution_config = config.get('execution', {})
        self.sl_update_max_retries = execution_config.get('order_max_retries', 3)
        self.sl_update_retry_backoff_base = execution_config.get('order_retry_backoff_base_seconds', 0.1)
        # FIX: Increase verification delay to allow MT5 to process SL update
        # 100ms was too short, causing verification failures even when SL was correctly applied
        self.sl_update_verification_delay = execution_config.get('sl_verification_delay_seconds', 0.2)  # 200ms (increased from 100ms)
        self.use_exponential_backoff = execution_config.get('verification', {}).get('use_exponential_backoff', True)
        
        # Verification tolerance configuration
        verification_config = execution_config.get('verification', {})
        # FIX: Relax tolerance for profitable trades to prevent false verification failures
        self.verification_effective_profit_tolerance_usd = verification_config.get('effective_profit_tolerance_usd', 1.5)  # Default 1.5 (increased from 1.0)
        self.verification_price_tolerance_multiplier = verification_config.get('price_tolerance_multiplier', 1.0)  # Multiplier for price tolerance
        
        # CRITICAL: Emergency safety - disable SL updates for problematic symbols until fix validated
        self._disabled_symbols = set()  # Symbols with SL updates disabled
        self._sl_update_rate_limit = {}  # {ticket: last_update_time} for rate limiting
        # NOTE: _sl_update_min_interval is initialized from config on line 185-186
        # Do NOT hardcode here - always use config value
        
        # Break-even configuration
        # CRITICAL: Break-even is DISABLED per requirement - lock profit immediately at sweet spot or above
        # No break-even logic; lock profit immediately at sweet spot ($0.03) or above
        break_even_config = self.risk_config.get('dynamic_break_even', {})
        self.break_even_enabled = False  # DISABLED: No break-even logic per Step 2c requirement
        self.break_even_duration_seconds = break_even_config.get('positive_profit_duration_seconds', 2.0)
        
        # Sweet-spot configuration
        profit_locking_config = self.risk_config.get('profit_locking', {})
        self.sweet_spot_min = profit_locking_config.get('min_profit_threshold_usd', 0.03)
        self.sweet_spot_max = profit_locking_config.get('max_profit_threshold_usd', 0.10)
        
        # Thread safety - OPTIMIZED: Use defaultdict for lock-free ticket lock access
        # Only acquire global lock when creating new ticket lock
        self._ticket_locks = {}  # {ticket: Lock}
        self._locks_lock = threading.Lock()  # Protects _ticket_locks dict (minimal use)
        self._locks_lock_optimized = False  # Flag to track if we've initialized locks dict
        # CRITICAL FIX: Load lock timeouts from config with proper defaults and SAFE CAPS
        # FIX: Reduce timeouts to prevent excessive delays (50+ seconds observed in logs)
        # Use 1.0s for standard, 2.0s for profit-locking (not 20s from config)
        # CRITICAL: Cap config values to prevent dangerous timeouts (>5s can cause 50+ second delays)
        config_lock_timeout = self.risk_config.get('lock_acquisition_timeout_seconds', 1.0)
        config_profit_timeout = self.risk_config.get('profit_locking_lock_timeout_seconds', 2.0)
        
        # Enforce safe maximums: 2.0s standard, 3.0s profit-locking (prevents 20s config from causing 50+ second delays)
        self._lock_acquisition_timeout = min(config_lock_timeout, 2.0)  # Cap at 2.0s
        self._profit_locking_lock_timeout = min(config_profit_timeout, 3.0)  # Cap at 3.0s
        
        # Log if config values were capped
        if config_lock_timeout > 2.0:
            logger.warning(f"[CONFIG_OVERRIDE] lock_acquisition_timeout_seconds capped from {config_lock_timeout}s to 2.0s (safe maximum)")
        if config_profit_timeout > 3.0:
            logger.warning(f"[CONFIG_OVERRIDE] profit_locking_lock_timeout_seconds capped from {config_profit_timeout}s to 3.0s (safe maximum)")
        self._lock_hold_times = {}  # {ticket: acquisition_time} for watchdog
        self._lock_holders = {}  # {ticket: {'thread_id': int, 'thread_name': str, 'acquired_at': float, 'is_profit_locking': bool}}
        self._lock_watchdog_interval = 0.05  # Check for stale locks every 50ms (very aggressive to catch stale locks immediately)
        self._lock_max_hold_time = 0.2  # Maximum time a lock can be held before force release (200ms - prevent blocking)
        self._lock_force_release_enabled = True  # Enable automatic stale lock recovery
        
        # Contract size cache (for auto-correction) with TTL
        self._contract_size_cache = {}  # {symbol: {'size': corrected_size, 'timestamp': time.time()}}
        self._contract_size_cache_ttl = 6 * 3600  # 6 hours TTL
        self._contract_size_lock = threading.Lock()
        
        # Load symbol overrides
        self._symbol_overrides = {}
        try:
            overrides_path = Path(__file__).parent.parent / 'config' / 'symbol_overrides.json'
            if overrides_path.exists():
                with open(overrides_path, 'r') as f:
                    overrides_data = json_module.load(f)
                    self._symbol_overrides = overrides_data.get('symbols', {})
                    logger.info(f"Loaded {len(self._symbol_overrides)} symbol overrides from config/symbol_overrides.json")
        except Exception as e:
            logger.warning(f"Could not load symbol overrides: {e}")
        
        # Position tracking
        self._position_tracking = {}  # {ticket: {profit_history, break_even_start_time, etc.}}
        self._tracking_lock = threading.Lock()
        
        # Profit zone entry tracking - CRITICAL for monitoring SL updates
        self._profit_zone_entry = {}  # {ticket: {'entry_time': datetime, 'entry_profit': float, 'sl_updated': bool, 'update_attempts': int, 'last_update_time': datetime, 'last_update_reason': str}}
        
        # SL update tracking
        self._last_sl_update = {}  # {ticket: datetime}
        self._last_sl_price = {}  # {ticket: float}
        self._last_sl_reason = {}  # {ticket: str}
        self._last_sl_attempt = {}  # {ticket: datetime} - tracks last attempt (success or failure)
        self._last_sl_success = {}  # {ticket: datetime} - tracks last successful update only
        self._consecutive_failures = defaultdict(int)  # {ticket: count} - tracks consecutive failures
        
        # CRITICAL: Track first-time eligibility per ticket to bypass blocking mechanisms
        self._first_eligible_update = {}  # {ticket: {'state': str, 'authority': str, 'first_seen_time': float}}
        # States: 'NONE' (no eligible update yet), 'PENDING' (eligible but not applied), 'APPLIED' (first update applied)
        
        # SL oscillation prevention
        self._sl_update_cooldown = {}  # {ticket: cooldown_until_timestamp}
        self._sl_update_cooldown_seconds = 0.5  # 500ms cooldown between updates for same ticket
        self._min_sl_delta_pct = 0.01  # 1% minimum change required to update SL (prevents oscillation)
        
        # Emergency enforcement tracking - prevent infinite loops
        self._emergency_enforcement_count = {}  # {ticket: count} - track emergency enforcements per ticket
        self._emergency_enforcement_max = 3  # Max emergency enforcements per ticket per violation
        self._ticket_circuit_breaker = {}  # {ticket: disabled_until_time} - circuit breaker for failing tickets
        self._circuit_breaker_threshold = 10  # 10 failures before activating (increased from 5)
        self._circuit_breaker_cooldown_base = 10.0  # Base cooldown 10 seconds
        self._circuit_breaker_cooldown = 60.0  # Default cooldown 60 seconds (used in emergency SL enforcement)
        
        # Error throttling for fail-safe check (prevent log spam)
        self._fail_safe_error_throttle = {}  # {error_signature: last_logged_time}
        self._fail_safe_throttle_window = 1.0  # Log same error at most once per second
        
        # CRITICAL: Real-time SL worker configuration
        trailing_config = self.risk_config.get('trailing', {})
        configured_interval_ms = self.risk_config.get('trailing_cycle_interval_ms', 500)
        
        # CRITICAL FIX: If instant_trailing is enabled, force interval to 0ms for instant updates
        if trailing_config.get('instant_trailing', False) or trailing_config.get('trigger_on_tick', False):
            configured_interval_ms = 0  # Force instant (0ms) when instant_trailing enabled
            logger.info(f"Instant trailing enabled - SL updates will trigger instantly (0ms interval)")
        else:
            # Enforce minimum 50ms only if NOT instant trailing (to prevent lock contention)
            if configured_interval_ms > 0 and configured_interval_ms < 50:
                logger.warning(f"Worker interval {configured_interval_ms}ms is too low, enforcing minimum 50ms to prevent lock contention")
                configured_interval_ms = 50
            logger.info(f"SL worker interval: {configured_interval_ms}ms")
        
        self._sl_worker_interval = configured_interval_ms / 1000.0  # Convert ms to seconds
        self._sl_worker_running = False
        self._sl_worker_thread: Optional[threading.Thread] = None
        self._sl_worker_shutdown_event = threading.Event()
        # Cached metrics for timer-based heartbeat (no MT5 calls from heartbeat thread)
        self._sl_worker_last_position_count: int = 0
        self._sl_worker_last_active_tickets: int = 0
        
        # OPTIMIZATION: Background task queue for heavy operations
        # This allows the main worker loop to stay under 50ms by offloading:
        # - Fail-safe checks (can scan all positions)
        # - CSV writing (file I/O)
        # - Heavy logging operations
        # - Stale lock checks (when many locks exist)
        self._background_task_queue = queue.Queue(maxsize=100)  # Limit queue size to prevent memory growth
        self._background_worker_thread: Optional[threading.Thread] = None
        self._background_worker_running = False
        self._background_worker_shutdown_event = threading.Event()
        
        # OPTIMIZATION: Batch CSV writes to reduce I/O overhead
        self._csv_write_queue = queue.Queue(maxsize=50)
        self._csv_batch_size = 10  # Write in batches of 10
        self._csv_batch_timeout = 0.5  # Flush batch after 500ms even if not full
        
        # Global rate limiting: max 50 SL updates per second system-wide (configurable, increased for reliability)
        self._global_rpc_lock = threading.Lock()
        self._global_rpc_timestamps = []  # List of timestamps for last 50 updates
        execution_config = config.get('execution', {})
        self._global_rpc_max_per_second = execution_config.get('global_rpc_max_per_second', 50)  # Configurable, default 50
        self._global_rpc_queue = []  # Queue for non-emergency updates (FIFO)
        self._emergency_backoff_base = 0.05  # Short exponential backoff for emergency (50ms base)
        
        # Per-ticket rate limiting: Load from config or use default 100ms
        sl_update_min_interval_ms = self.risk_config.get('sl_update_min_interval_ms', 100)
        self._sl_update_min_interval = sl_update_min_interval_ms / 1000.0  # Convert ms to seconds
        
        # Timing instrumentation
        self._timing_stats = {
            'loop_durations': [],  # Per-loop durations
            'ticket_update_times': {},  # {ticket: [latencies]}
            'update_counts': defaultdict(int),  # {ticket: count}
            'last_loop_time': None,
            'last_update_time': None
        }
        self._timing_lock = threading.Lock()
        
        # Manual review tracking (for emergency failures)
        self._manual_review_tickets = set()  # Tickets requiring manual review
        
        # Error debouncing (1 error/sec per signature)
        self._error_debounce = {}  # {error_signature: last_logged_time}
        self._error_debounce_window = 1.0
        self._error_occurrence_metrics = defaultdict(int)  # Track occurrences even when debounced
        
        # CRITICAL: Verification hooks and metrics tracking
        # These track system health and ensure SL updates work correctly
        self._verification_metrics = {
            'sl_update_attempts': 0,  # Total SL update attempts
            'sl_update_successes': 0,  # Successful SL updates
            'sl_update_failures': 0,  # Failed SL updates
            'profit_locking_activations': 0,  # Profit locking activations
            'profit_locking_times': [],  # Time from profit entry to SL lock (ms)
            'duplicate_update_attempts': 0,  # Duplicate update attempts detected
            'lock_acquisition_failures': 0,  # Lock acquisition failures
            'lock_timeouts': 0,  # Lock timeout occurrences
            'lock_contention_count': 0,  # Lock contention occurrences
            'last_metrics_reset': datetime.now()  # Last time metrics were reset
        }
        self._verification_lock = threading.Lock()  # Lock for metrics updates
        
        # Structured logging for SL updates
        self._structured_log_enabled = True
        self._structured_log_file = None
        self._structured_log_lock = threading.Lock()
        self._init_structured_logging()
        
        # CSV summary writer
        self._csv_summary_enabled = True
        self._csv_summary_file = None
        self._csv_summary_writer = None
        self._csv_summary_lock = threading.Lock()
        self._init_csv_summary()
        
        # Lock diagnostics logging
        self._lock_diagnostics_enabled = True
        self._lock_diagnostics_file = None
        self._lock_diagnostics_lock = threading.Lock()
        self._init_lock_diagnostics()
        
        logger.info(f"SL Manager initialized | Max risk: ${self.max_risk_usd:.2f} | "
                   f"Break-even: {self.break_even_enabled} ({self.break_even_duration_seconds}s) | "
                   f"Sweet-spot: ${self.sweet_spot_min:.2f}-${self.sweet_spot_max:.2f} | "
                   f"Trailing increment: ${self.trailing_increment_usd:.2f} | "
                   f"Worker interval: {self._sl_worker_interval*1000:.0f}ms | "
                   f"Min update interval: {self._sl_update_min_interval*1000:.0f}ms | "
                   f"Lock timeout: {self._lock_acquisition_timeout*1000:.0f}ms | "
                   f"Profit-locking timeout: {self._profit_locking_lock_timeout*1000:.0f}ms")
        
        # CRITICAL FIX: Load disabled symbols from config instead of hardcoding
        # This allows configuration-driven symbol disable list
        disabled_symbols_config = self.risk_config.get('disabled_symbols', [])
        for sym in disabled_symbols_config:
            self._disabled_symbols.add(sym)
        if self._disabled_symbols:
            logger.info(f"📋 SL updates disabled for symbols (from config): {', '.join(self._disabled_symbols)}")
    
    def _init_structured_logging(self):
        """Initialize structured JSON logging for SL updates."""
        if not self._structured_log_enabled:
            return
        
        try:
            log_dir = Path(__file__).parent.parent / 'logs' / 'runtime'
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            log_file = log_dir / f'sl_updates_{timestamp}.jsonl'
            self._structured_log_file = open(log_file, 'a', encoding='utf-8')
            logger.info(f"[LOG] Structured SL logging enabled: {log_file}")
        except Exception as e:
            logger.warning(f"Could not initialize structured logging: {e}")
            self._structured_log_enabled = False
    
    def _init_csv_summary(self):
        """Initialize CSV summary writer for per-ticket state."""
        if not self._csv_summary_enabled:
            return
        
        try:
            log_dir = Path(__file__).parent.parent / 'logs' / 'runtime'
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_file = log_dir / f'sl_summary_{timestamp}.csv'
            self._csv_summary_file = open(csv_file, 'w', newline='', encoding='utf-8')
            self._csv_summary_writer = csv.writer(self._csv_summary_file)
            # Write header
            self._csv_summary_writer.writerow([
                'timestamp', 'ticket', 'symbol', 'entry_price', 'current_price', 'profit',
                'target_sl', 'applied_sl', 'effective_sl_profit', 'last_update_time',
                'last_update_result', 'failure_reason', 'consecutive_failures', 'thread_id'
            ])
            self._csv_summary_file.flush()
            logger.info(f"📊 CSV summary logging enabled: {csv_file}")
        except Exception as e:
            logger.warning(f"Could not initialize CSV summary: {e}")
            self._csv_summary_enabled = False
    
    def _init_lock_diagnostics(self):
        """Initialize lock diagnostics JSONL logging."""
        if not self._lock_diagnostics_enabled:
            return
        
        try:
            log_dir = Path(__file__).parent.parent / 'logs' / ('backtest' if self.config.get('mode') == 'backtest' else 'live') / 'engine'
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / 'lock_diagnostics.jsonl'
            self._lock_diagnostics_file = open(log_file, 'a', encoding='utf-8')
            logger.info(f"[LOG] Lock diagnostics logging enabled: {log_file}")
        except Exception as e:
            logger.warning(f"Could not initialize lock diagnostics: {e}")
            self._lock_diagnostics_enabled = False
    
    def _log_lock_diagnostics(self, ticket: int, event: str, thread_name: str, thread_id: int,
                              duration_ms: float, is_profit_locking: bool, success: bool,
                              holder_thread: Optional[str] = None, holder_stack: Optional[str] = None):
        """Log lock diagnostics to JSONL file."""
        if not self._lock_diagnostics_enabled or not self._lock_diagnostics_file:
            return
        
        try:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'ticket': ticket,
                'event': event,  # acquire_attempt, acquired, released, forced_release
                'thread_name': thread_name,
                'thread_id': thread_id,
                'duration_ms': duration_ms,
                'is_profit_locking': is_profit_locking,
                'success': success,
                'holder_thread': holder_thread,
                'holder_stack': holder_stack[:500] if holder_stack else None  # Truncate long stacks
            }
            
            with self._lock_diagnostics_lock:
                self._lock_diagnostics_file.write(json_module.dumps(log_entry) + '\n')
                self._lock_diagnostics_file.flush()
        except Exception as e:
            logger.debug(f"Could not write lock diagnostics: {e}")
    
    def _log_structured_update(self, ticket: int, symbol: str, entry_price: float, target_sl: float,
                               applied_sl: float, attempt_number: int, retry_backoff_ms: float,
                               applied_sl_reason: str, broker_error_code: Optional[int],
                               effective_profit_target: float, effective_profit_applied: float,
                               success: bool, thread_id: Optional[str] = None):
        """Log SL update in structured JSON format."""
        if not self._structured_log_enabled or not self._structured_log_file:
            return
        
        try:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'ticket': ticket,
                'symbol': symbol,
                'entry_price': entry_price,
                'target_sl': target_sl,
                'applied_sl': applied_sl,
                'attempt_number': attempt_number,
                'retry_backoff_ms': retry_backoff_ms,
                'applied_sl_reason': applied_sl_reason,
                'broker_error_code': broker_error_code,
                'effective_profit_target': effective_profit_target,
                'effective_profit_applied': effective_profit_applied,
                'success': success,
                'thread_id': thread_id or threading.current_thread().name
            }
            
            with self._structured_log_lock:
                self._structured_log_file.write(json_module.dumps(log_entry) + '\n')
                self._structured_log_file.flush()
        except Exception as e:
            logger.debug(f"Could not write structured log: {e}")
    
    def _write_csv_summary(self, ticket: int, symbol: str, entry_price: float, current_price: float,
                          profit: float, target_sl: float, applied_sl: float, effective_sl_profit: float,
                          last_update_time: Optional[datetime], last_update_result: str,
                          failure_reason: Optional[str], consecutive_failures: int):
        """Write per-ticket state to CSV summary."""
        if not self._csv_summary_enabled or not self._csv_summary_writer:
            return
        
        try:
            with self._csv_summary_lock:
                self._csv_summary_writer.writerow([
                    datetime.now().isoformat(),
                    ticket,
                    symbol,
                    entry_price,
                    current_price,
                    profit,
                    target_sl,
                    applied_sl,
                    effective_sl_profit,
                    last_update_time.isoformat() if last_update_time else '',
                    last_update_result,
                    failure_reason or '',
                    consecutive_failures,
                    threading.current_thread().name
                ])
                self._csv_summary_file.flush()
        except Exception as e:
            logger.debug(f"Could not write CSV summary: {e}")
    
    def _get_ticket_lock(self, ticket: int) -> threading.RLock:
        """
        Get or create a reentrant lock for a specific ticket.
        OPTIMIZED: Use double-checked locking to minimize global lock contention.
        """
        # Fast path: try to get existing lock without global lock
        if ticket in self._ticket_locks:
            return self._ticket_locks[ticket]
        
        # Slow path: acquire global lock only to create new lock
        with self._locks_lock:
            # Double-check after acquiring lock
            if ticket not in self._ticket_locks:
                # CRITICAL FIX: Use RLock (reentrant lock) to prevent deadlocks in backtest mode
                # In backtest, both run_cycle and sl_worker run on the same thread (MainThread)
                # Without reentrant locks, this causes deadlocks when both try to update the same position
                self._ticket_locks[ticket] = threading.RLock()
            return self._ticket_locks[ticket]
    
    def _acquire_ticket_lock_with_timeout(self, ticket: int, is_profit_locking: bool = False, force_non_blocking_first: bool = False) -> Tuple[bool, Optional[threading.Lock], Optional[str]]:
        """
        Attempt to acquire ticket lock with non-blocking + exponential backoff.
        
        CRITICAL FIX: Uses non-blocking first attempt, then exponential backoff retries.
        Does NOT block caller - returns quickly if lock unavailable.
        
        Args:
            ticket: Position ticket number
            is_profit_locking: If True, this is a profitable trade needing SL update (use longer timeout)
            force_non_blocking_first: If True, force non-blocking first attempt (for first eligible updates)
        
        Returns:
            (success, lock, reason) tuple. If success is False, lock is None and reason explains why.
        """
        lock = self._get_ticket_lock(ticket)
        acquisition_start = time.time()
        
        # CRITICAL: Profitable trades need longer timeout - they MUST lock profit
        # Use configured profit locking timeout for profitable trades, standard timeout for others
        base_timeout = self._profit_locking_lock_timeout if is_profit_locking else self._lock_acquisition_timeout
        
        # FIX: Use non-blocking first attempt, then short timeouts (not exponential backoff)
        # This prevents 50+ second delays observed in logs
        retries = 3
        backoff_base = 0.01  # 10ms base (reduced from 50ms)
        last_error = None
        
        for attempt in range(retries):
            attempt_start = time.time()
            
            # First attempt: non-blocking (immediate) - ALWAYS for first eligible updates
            if attempt == 0 or force_non_blocking_first:
                acquired = lock.acquire(blocking=False)
                if force_non_blocking_first and not acquired:
                    # For first eligible, force-release stale locks and retry immediately
                    with self._locks_lock:
                        if ticket in self._lock_hold_times:
                            hold_duration = time.time() - self._lock_hold_times[ticket]
                            if hold_duration > 0.05:  # 50ms threshold
                                logger.critical(f"[FIRST_ELIGIBLE] Ticket={ticket} | "
                                              f"Force-releasing stale lock (held {hold_duration*1000:.1f}ms)")
                                del self._lock_hold_times[ticket]
                                if ticket in self._lock_holders:
                                    del self._lock_holders[ticket]
                                # Retry non-blocking
                                acquired = lock.acquire(blocking=False)
            else:
                # Subsequent attempts: use SHORT timeout (not exponential)
                # Use base_timeout directly, not multiplied (prevents excessive delays)
                timeout = base_timeout  # Use base timeout directly (0.5s or 1.0s)
                acquired = lock.acquire(timeout=timeout)
            
            acquisition_time = (time.time() - attempt_start) * 1000  # Convert to ms
            
            if acquired:
                # Record lock acquisition time for watchdog
                hold_start = time.time()
                thread_id = threading.current_thread().ident
                thread_name = threading.current_thread().name
                stack_trace = ''.join(traceback.format_stack()[-5:-1])  # Last 4 frames
                
                with self._locks_lock:
                    self._lock_hold_times[ticket] = hold_start
                    if ticket not in self._lock_holders:
                        self._lock_holders = {}
                    self._lock_holders[ticket] = {
                        'thread_id': thread_id,
                        'thread_name': thread_name,
                        'acquired_at': hold_start,
                        'is_profit_locking': is_profit_locking,
                        'stack': stack_trace
                    }
                    if not hasattr(self, '_lock_holder_stack_traces'):
                        self._lock_holder_stack_traces = {}
                    self._lock_holder_stack_traces[ticket] = stack_trace
                
                # Log lock diagnostics
                self._log_lock_diagnostics(ticket, 'acquired', thread_name, thread_id, acquisition_time, is_profit_locking, True)
                
                logger.debug(f"🔒 Lock acquired | Ticket {ticket} | Thread: {thread_name}({thread_id}) | "
                            f"Acquisition: {acquisition_time:.1f}ms | Attempt: {attempt+1}/{retries} | "
                            f"{'Profit-locking priority' if is_profit_locking else 'Standard'}")
                return True, lock, None
            else:
                # Lock acquisition failed - check current holder
                with self._locks_lock:
                    holder_info = self._lock_holders.get(ticket)
                    if ticket in self._lock_hold_times:
                        hold_duration = time.time() - self._lock_hold_times[ticket]
                        holder_thread_id = holder_info.get('thread_id') if holder_info else None
                        holder_thread = holder_info.get('thread_name', 'Unknown') if holder_info else 'Unknown'
                        holder_stack = holder_info.get('stack', 'N/A') if holder_info else 'N/A'
                        
                        # CRITICAL FIX: Check if holder thread is dead (e.g., SLWorker stopped)
                        # If thread is dead, lock is orphaned and must be force-released immediately
                        holder_thread_dead = False
                        if holder_thread_id is not None:
                            try:
                                # Check if thread is still alive by searching active threads
                                # Note: threading is already imported at module level
                                active_thread_ids = {t.ident for t in threading.enumerate()}
                                if holder_thread_id not in active_thread_ids:
                                    holder_thread_dead = True
                                    logger.critical(f"🔓 DEAD THREAD LOCK DETECTED: Ticket {ticket} | "
                                                  f"Holder thread {holder_thread} (ID: {holder_thread_id}) is DEAD | "
                                                  f"Lock held for {hold_duration:.2f}s | Force releasing immediately...")
                            except Exception as e:
                                logger.debug(f"Error checking thread alive status: {e}")
                        
                        last_error = f"Lock held by {holder_thread} for {hold_duration:.2f}s"
                        
                        # Force release stale locks if held > 200ms OR if holder thread is dead
                        if holder_thread_dead or hold_duration > 0.2:  # 200ms threshold (reduced from 500ms)
                            reason = "dead thread" if holder_thread_dead else f"held for {hold_duration:.2f}s (threshold: 0.2s)"
                            logger.critical(f"🔓 STALE LOCK DETECTED: Ticket {ticket} | "
                                          f"Reason: {reason} | "
                                          f"Holder: {holder_thread} | "
                                          f"Attempting force release...")
                            
                            # Try to force release by removing from tracking
                            if ticket in self._lock_hold_times:
                                del self._lock_hold_times[ticket]
                            if ticket in self._lock_holders:
                                del self._lock_holders[ticket]
                            
                            # Try non-blocking acquisition - if it succeeds, the lock was actually stale
                            if lock.acquire(blocking=False):
                                lock.release()
                                logger.critical(f"STALE LOCK FORCE RELEASED: Ticket {ticket} | Reason: {reason} | Retrying...")
                                # Don't retry here - let next attempt try
                            else:
                                logger.warning(f"STALE LOCK ACTIVE: Ticket {ticket} | "
                                            f"Lock is held by {'dead' if holder_thread_dead else 'active'} thread {holder_thread}, removed from tracking")
                                # Log holder stack for diagnostics
                                if holder_stack != 'N/A':
                                    logger.debug(f"Lock holder stack trace:\n{holder_stack}")
                    else:
                        last_error = "Lock not in tracking (may be held by external thread)"
                
                # Short backoff before retry (not exponential to prevent excessive delays)
                if attempt < retries - 1:
                    backoff = backoff_base * (attempt + 1)  # 10ms, 20ms, 30ms (linear, not exponential)
                    time.sleep(backoff)
        
        # All retries failed - log diagnostics
        with self._locks_lock:
            holder_info = self._lock_holders.get(ticket)
            holder_thread = holder_info.get('thread_name', 'Unknown') if holder_info else 'Unknown'
            holder_stack = holder_info.get('stack', 'N/A') if holder_info else 'N/A'
        
        timeout_ms = base_timeout * 1000
        reason = f"Lock acquisition timeout ({timeout_ms:.0f}ms) after {retries} attempts"
        
        # Log lock diagnostics
        self._log_lock_diagnostics(ticket, 'acquire_attempt', threading.current_thread().name,
                                   threading.current_thread().ident, (time.time() - acquisition_start) * 1000,
                                   is_profit_locking, False, holder_thread, holder_stack)
        
        # Track metrics
        with self._verification_lock:
            self._verification_metrics['lock_timeouts'] += 1
            self._verification_metrics['lock_acquisition_failures'] += 1
        
        logger.warning(f"[DELAY] LOCK TIMEOUT: Ticket {ticket} | "
                     f"Could not acquire after {retries} attempts | "
                     f"Total time: {(time.time() - acquisition_start)*1000:.1f}ms | "
                     f"{'Profit-locking' if is_profit_locking else 'Standard'} | "
                     f"Holder: {holder_thread}")
        
        return False, None, reason
    
    def _release_ticket_lock(self, ticket: int, lock: threading.Lock):
        """Release ticket lock and log diagnostics."""
        hold_duration = 0.0
        is_profit_locking = False
        thread_name = threading.current_thread().name
        thread_id = threading.current_thread().ident
        
        with self._locks_lock:
            if ticket in self._lock_hold_times:
                hold_duration = (time.time() - self._lock_hold_times[ticket]) * 1000  # ms
                del self._lock_hold_times[ticket]
            if ticket in self._lock_holders:
                holder_info = self._lock_holders[ticket]
                is_profit_locking = holder_info.get('is_profit_locking', False)
                del self._lock_holders[ticket]
            if ticket in self._lock_holder_stack_traces:
                del self._lock_holder_stack_traces[ticket]
        
        lock.release()
        
        # Log lock diagnostics
        self._log_lock_diagnostics(ticket, 'released', thread_name, thread_id, hold_duration, is_profit_locking, True)
        
        if hold_duration > 500:  # Warn if held > 500ms
            logger.warning(f"Lock held for {hold_duration:.1f}ms | Ticket {ticket} | Thread: {thread_name}")
    
    def _check_stale_locks(self):
        """Check for stale locks and log warnings. Optionally force release if held too long."""
        current_time = time.time()
        stale_locks = []
        force_released = []
        
        with self._locks_lock:
            for ticket, acquisition_time in list(self._lock_hold_times.items()):
                hold_duration = current_time - acquisition_time
                if hold_duration > self._lock_watchdog_interval:
                    stale_locks.append((ticket, hold_duration))
                    
                    # Force release if lock held beyond maximum threshold
                    # CRITICAL: Use shorter threshold for stale locks to prevent blocking profitable trades
                    # FIX: Use 200ms threshold (very aggressive) to catch stale locks immediately
                    if self._lock_force_release_enabled and hold_duration > 0.2:  # 200ms threshold (very aggressive)
                        if ticket in self._ticket_locks:
                            lock = self._ticket_locks[ticket]
                            # Try to release if we can acquire it (means it's actually stale)
                            if lock.acquire(blocking=False):
                                lock.release()
                                force_released.append((ticket, hold_duration))
                                logger.critical(f"🔓 FORCE RELEASED STALE LOCK: Ticket {ticket} | Lock held for {hold_duration:.2f}s (threshold: {self._lock_max_hold_time}s)")
                                # Log system event
                                system_event_logger.systemEvent("SL_UPDATE_FAILED", {
                                    "ticket": ticket,
                                    "error": f"Stale lock force released after {hold_duration:.2f}s",
                                    "reason": "Lock timeout exceeded"
                                })
                            else:
                                # Lock is actually held - try to force release by removing from tracking
                                # This allows next attempt to acquire the lock
                                logger.warning(f"[WARNING] STALE LOCK ACTIVE: Ticket {ticket} | Lock held for {hold_duration:.2f}s | "
                                            f"Removing from tracking to allow retry")
                                # Remove from tracking so next attempt can try again
                                if ticket in self._lock_hold_times:
                                    del self._lock_hold_times[ticket]
                                    continue  # Skip the deletion below since we already deleted it
                    
                    # Remove from tracking (only if not already deleted above)
                    if ticket in self._lock_hold_times:
                        del self._lock_hold_times[ticket]
        
        if stale_locks:
            for ticket, duration in stale_locks:
                if (ticket, duration) not in force_released:
                    logger.warning(f"STALE LOCK DETECTED: Ticket {ticket} | Lock held for {duration:.2f}s (threshold: {self._lock_watchdog_interval}s)")
        
        return len(force_released) > 0
    
    def _get_corrected_contract_size(self, symbol: str, entry_price: float, lot_size: float, target_loss_usd: float, position: Optional[Dict[str, Any]] = None) -> float:
        """
        Simplified contract size correction (Fix F).
        
        Strategy:
        1. Check symbol overrides first
        2. Check cache (with TTL validation)
        3. Prefer broker-reported contract_size if it produces reasonable SL (<10% of entry)
        4. Only use reverse engineering when contract_size leads to absurd result AND current_profit available
        5. Limit multipliers to [10, 100, 1000, 10000] (no 100k default)
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            lot_size: Lot size
            target_loss_usd: Target loss in USD
            position: Optional position dict (for reverse engineering from current_profit)
        
        Returns:
            Corrected contract size
        """
        # Step 1: Check symbol overrides
        if symbol in self._symbol_overrides:
            override = self._symbol_overrides[symbol]
            if override.get('contract_size') is not None:
                logger.info(f"🔧 Using manual contract_size override for {symbol}: {override['contract_size']}")
                return float(override['contract_size'])
        
        # Step 2: Check cache (with TTL validation)
        current_time = time.time()
        with self._contract_size_lock:
            if symbol in self._contract_size_cache:
                cached_entry = self._contract_size_cache[symbol]
                if isinstance(cached_entry, dict):
                    cached_size = cached_entry.get('size')
                    cached_timestamp = cached_entry.get('timestamp', 0)
                    if current_time - cached_timestamp < self._contract_size_cache_ttl:
                        return cached_size
                    # TTL expired, remove from cache
                    del self._contract_size_cache[symbol]
                else:
                    # Legacy format (direct value), convert to new format
                    cached_size = cached_entry
                    self._contract_size_cache[symbol] = {'size': cached_size, 'timestamp': current_time}
                    return cached_size
        
        # Step 3: Get broker-reported contract_size
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return 1.0  # Default fallback
        
        reported_contract_size = symbol_info.get('contract_size', 1.0)
        point = symbol_info.get('point', 0.00001)
        point_value = symbol_info.get('trade_tick_value', None)
        
        # Step 4: Test if reported size produces reasonable SL (<10% of entry)
        if reported_contract_size > 0 and lot_size > 0:
            price_diff_reported = abs(target_loss_usd) / (lot_size * reported_contract_size)
            
            # If price difference is reasonable (<10% of entry), use reported size
            if price_diff_reported < entry_price * 0.10:
                with self._contract_size_lock:
                    self._contract_size_cache[symbol] = {'size': reported_contract_size, 'timestamp': current_time}
                return reported_contract_size
        
        # Step 5: Reported size produces absurd result (>10% of entry)
        # Try reverse engineering ONLY if current_profit is available
        if position is not None:
            current_profit = position.get('profit', None)
            current_price = position.get('price_current', 0.0)
            pos_entry = position.get('price_open', 0.0)
            pos_lot_size = position.get('volume', 0.01)
            order_type = position.get('type', '')
            
            if current_profit is not None and current_price > 0 and pos_entry > 0 and abs(current_profit) > 0.01:
                # Calculate what contract_size would give us the current profit
                if order_type == 'BUY':
                    current_price_diff = current_price - pos_entry
                else:  # SELL
                    current_price_diff = pos_entry - current_price
                
                if abs(current_price_diff) > 0.00001 and pos_lot_size > 0:
                    # Reverse-engineer: current_profit = current_price_diff * lot_size * effective_contract_size
                    effective_contract_size = abs(current_profit) / (abs(current_price_diff) * pos_lot_size)
                    
                    if 0.1 <= effective_contract_size <= 1000000:
                        # Verify this size produces reasonable SL
                        price_diff_test = abs(target_loss_usd) / (lot_size * effective_contract_size)
                        if price_diff_test < entry_price * 0.10:
                            with self._contract_size_lock:
                                self._contract_size_cache[symbol] = {'size': effective_contract_size, 'timestamp': current_time}
                            logger.info(f"🔧 CONTRACT_SIZE REVERSE-ENGINEERED: {symbol} | "
                                      f"Reported: {reported_contract_size} → Reverse: {effective_contract_size:.2f} | "
                                      f"From current profit: ${current_profit:.2f}")
                            return effective_contract_size
        
        # Step 6: Try limited multipliers [10, 100, 1000, 10000] (no 100k)
        multipliers = [10.0, 100.0, 1000.0, 10000.0]
        for multiplier in multipliers:
            corrected_size = reported_contract_size * multiplier
            if corrected_size > 0 and lot_size > 0:
                price_diff_corrected = abs(target_loss_usd) / (lot_size * corrected_size)
                
                # If corrected size gives reasonable price difference (<10% of entry), use it
                if price_diff_corrected < entry_price * 0.10:
                    with self._contract_size_lock:
                        self._contract_size_cache[symbol] = {'size': corrected_size, 'timestamp': current_time}
                    logger.info(f"🔧 CONTRACT_SIZE AUTO-CORRECTED: {symbol} | "
                              f"{reported_contract_size} → {corrected_size} (multiplier: {multiplier}x) | "
                              f"Price_diff: {price_diff_reported:.5f} → {price_diff_corrected:.5f}")
                    return corrected_size
        
        # Step 7: Fallback to reported size (even if it seems wrong)
        logger.warning(f"CONTRACT_SIZE: Could not correct {symbol} | "
                      f"Reported: {reported_contract_size} | Price_diff: {price_diff_reported:.5f} ({price_diff_reported/entry_price*100:.1f}% of entry)")
        with self._contract_size_lock:
            self._contract_size_cache[symbol] = {'size': reported_contract_size, 'timestamp': current_time}
        return reported_contract_size
    
    def _calculate_target_sl_price(self, entry_price: float, target_profit_usd: float,
                                   order_type: str, lot_size: float, symbol_info: Dict[str, Any],
                                   position: Optional[Dict[str, Any]] = None) -> float:
        """
        Calculate target SL price to achieve target profit/loss in USD.
        
        CRITICAL FIX: For indices, account for proper entry price (BUY uses ASK, SELL uses BID).
        Also handle contract_size correction for indices properly.
        
        Args:
            entry_price: Entry price of the position (from MT5, may be BID for both types)
            target_profit_usd: Target profit/loss in USD (negative for loss)
            order_type: 'BUY' or 'SELL'
            lot_size: Lot size
            symbol_info: Symbol information dictionary
        
        Returns:
            Target SL price
        """
        symbol = symbol_info.get('name', '')
        point = symbol_info.get('point', 0.00001)
        digits = symbol_info.get('digits', 5)
        
        # CRITICAL FIX: For indices, get current market prices to calculate correct entry
        # MT5's price_open might be BID, but for BUY we need ASK, for SELL we need BID
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick:
            current_bid = tick.bid
            current_ask = tick.ask
            
            # For BUY: entry should be ASK (what we paid), for SELL: entry should be BID (what we received)
            # If entry_price is close to current BID, it's likely BID (MT5 default)
            # Adjust entry_price based on order type if needed
            if order_type == 'BUY':
                # For BUY, if entry_price is close to BID, use ASK instead
                if abs(entry_price - current_bid) < abs(entry_price - current_ask):
                    # Entry is closer to BID, likely MT5 gave us BID, but we need ASK
                    # Use current ASK as approximation (or entry + spread)
                    effective_entry = entry_price + (current_ask - current_bid) if current_ask > current_bid else entry_price
                else:
                    effective_entry = entry_price
            else:  # SELL
                # For SELL, entry should be BID (what we received)
                effective_entry = entry_price
        else:
            effective_entry = entry_price
        
        # Get corrected contract size (pass position for reverse engineering if available)
        contract_size = self._get_corrected_contract_size(symbol, effective_entry, lot_size, abs(target_profit_usd), position=position)
        
        # CRITICAL FIX: For indices and crypto, use point_value if available
        # For indices like US30m: Profit = (price_diff_in_points) * lot_size * point_value
        # For crypto: Similar calculation may apply
        # For forex: Profit = (price_diff) * lot_size * contract_size
        
        # Detect if this is likely a crypto, index, or commodity symbol
        # CRITICAL: Also check if trade_tick_value exists - commodities like USOILm use it
        point_value = symbol_info.get('trade_tick_value', None)
        is_crypto_or_index = (point >= 0.01) or (point < 0.0001 and entry_price > 100) or (point_value is not None and point_value > 0)
        
        # CRITICAL FIX: For symbols with trade_tick_value, ALWAYS use it (most accurate)
        # Only reverse-engineer if trade_tick_value is not available
        symbol_upper = symbol.upper()
        is_crypto_by_name = any(crypto in symbol_upper for crypto in ['BTC', 'ETH', 'LTC', 'XRP', 'ADA', 'DOGE', 'XAU', 'XAG'])
        
        # Try to get current position for reverse-engineering (ONLY if trade_tick_value not available)
        effective_contract_size = None
        # CRITICAL FIX: Only reverse-engineer if trade_tick_value is NOT available
        # trade_tick_value from MT5 is the most accurate source
        if (is_crypto_by_name or is_crypto_or_index) and (not point_value or point_value <= 0):
            # CRITICAL: Only reverse-engineer when trade_tick_value is not available
            # This ensures we use the correct multiplier that matches broker's actual calculation
            try:
                # First, try to use the position passed as parameter (most accurate)
                if position is not None:
                    current_profit = position.get('profit', None)
                    current_price = position.get('price_current', 0.0)
                    pos_entry = position.get('price_open', 0.0)
                    pos_lot_size = position.get('volume', 0.01)
                    
                    if current_profit is not None and current_price > 0 and pos_entry > 0 and abs(current_profit) > 0.01:
                        # Calculate what contract_size would give us the current profit
                        if order_type == 'BUY':
                            current_price_diff = current_price - pos_entry
                        else:  # SELL
                            current_price_diff = pos_entry - current_price
                        
                        if abs(current_price_diff) > 0.00001 and pos_lot_size > 0:
                            # Reverse-engineer: current_profit = current_price_diff * lot_size * effective_contract_size
                            effective_contract_size = abs(current_profit) / (abs(current_price_diff) * pos_lot_size)
                            
                            # If this gives a reasonable value, use it
                            if 0.1 <= effective_contract_size <= 1000000:
                                logger.info(f"🔧 SL CALCULATION: Using reverse-engineered contract_size for {symbol}: {effective_contract_size:.2f} | "
                                          f"From current profit: ${current_profit:.2f} | Price diff: {current_price_diff:.5f}")
                
                # Fallback: Get all open positions and find matching symbol
                if effective_contract_size is None:
                    positions = self.order_manager.get_open_positions()
                    for pos in positions:
                        if pos.get('symbol', '') == symbol and pos.get('type', '') == order_type:
                            current_profit = pos.get('profit', None)
                            current_price = pos.get('price_current', 0.0)
                            pos_entry = pos.get('price_open', 0.0)
                            pos_lot_size = pos.get('volume', 0.01)
                            
                            if current_profit is not None and current_price > 0 and pos_entry > 0 and abs(current_profit) > 0.01:
                                # Calculate what contract_size would give us the current profit
                                if order_type == 'BUY':
                                    current_price_diff = current_price - pos_entry
                                else:  # SELL
                                    current_price_diff = pos_entry - current_price
                                
                                if abs(current_price_diff) > 0.00001 and pos_lot_size > 0:
                                    # Reverse-engineer: current_profit = current_price_diff * lot_size * effective_contract_size
                                    effective_contract_size = abs(current_profit) / (abs(current_price_diff) * pos_lot_size)
                                    
                                    # If this gives a reasonable value, use it
                                    if 0.1 <= effective_contract_size <= 1000000:
                                        logger.info(f"🔧 SL CALCULATION: Using reverse-engineered contract_size for {symbol}: {effective_contract_size:.2f} | "
                                                  f"From current profit: ${current_profit:.2f} | Price diff: {current_price_diff:.5f}")
                                        break
            except Exception as e:
                logger.debug(f"Could not reverse-engineer contract_size for {symbol}: {e}")
        
        # CRITICAL: Use trade_tick_value if available (regardless of symbol type)
        # This handles commodities like USOILm that have trade_tick_value but don't match typical index/crypto patterns
        if point_value and point_value > 0:
            # Use trade_tick_value FIRST (most accurate, directly from broker)
            # For indices/commodities: Profit = price_diff_in_points * lot_size * point_value
            # So: price_diff_in_points = target_profit / (lot_size * point_value)
            price_diff_in_points = target_profit_usd / (lot_size * point_value)
            price_diff = price_diff_in_points * point
            logger.info(f"🔧 SL CALCULATION: Using trade_tick_value: {point_value} | "
                       f"Price diff: {price_diff:.5f} ({price_diff_in_points:.1f} points) | Target profit: ${target_profit_usd:.2f}")
        elif is_crypto_or_index and effective_contract_size is not None:
            # CRITICAL: Use reverse-engineered contract_size as fallback (only if trade_tick_value not available)
            price_diff = target_profit_usd / (lot_size * effective_contract_size)
            logger.info(f"🔧 SL CALCULATION: Using reverse-engineered contract_size: {effective_contract_size:.2f} | "
                       f"Price diff: {price_diff:.5f} | Target profit: ${target_profit_usd:.2f}")
        elif is_crypto_or_index and contract_size == 1.0:
            # Crypto without point_value and no reverse-engineered size - estimate
            target_price_diff_pct = 0.02  # Target 2% of entry price for SL
            target_price_diff = effective_entry * target_price_diff_pct
            
            if target_price_diff > 0 and lot_size > 0:
                estimated_contract_size = abs(target_profit_usd) / (target_price_diff * lot_size)
                
                if estimated_contract_size > contract_size * 10:
                    price_diff = target_profit_usd / (lot_size * estimated_contract_size)
                    logger.debug(f"🔧 CRYPTO CONTRACT_SIZE ESTIMATED: {symbol} | "
                               f"Reported: {contract_size} | Estimated: {estimated_contract_size:.2f}")
                else:
                    price_diff = target_profit_usd / (lot_size * contract_size)
            else:
                price_diff = target_profit_usd / (lot_size * contract_size)
        else:
            # Forex calculation: use contract_size
            # Profit/Loss = (price_diff) * lot_size * contract_size
            # price_diff = Profit/Loss / (lot_size * contract_size)
            price_diff = target_profit_usd / (lot_size * contract_size)
        
        # CRITICAL FIX: Ensure price_diff sign is correct
        # For BUY: loss when price goes DOWN, so SL should be BELOW entry
        # For SELL: loss when price goes UP, so SL should be ABOVE entry
        if order_type == 'BUY':
            # For BUY: SL triggers when BID reaches SL
            # Loss = (entry_ask - sl_bid) * lot * contract
            # price_diff is negative for loss, so: sl_bid = entry_ask + price_diff (since price_diff is negative)
            target_sl = effective_entry + price_diff  # price_diff is negative, so this makes SL lower
        else:  # SELL
            # For SELL: SL triggers when ASK reaches SL
            # Loss = (sl_ask - entry_bid) * lot * contract
            # price_diff is negative for loss, so: sl_ask = entry_bid - price_diff (since price_diff is negative)
            target_sl = effective_entry - price_diff  # price_diff is negative, so this makes SL higher
        
        # Normalize to point precision
        if digits in [5, 3]:
            target_sl = round(target_sl / point) * point
        else:
            target_sl = round(target_sl, digits)
        
        # CRITICAL VALIDATION: Ensure calculated SL makes sense
        # For LOSS-protection SL:
        #   - BUY: SL must be below entry (price goes down = loss)
        #   - SELL: SL must be above entry (price goes up = loss)
        # For PROFIT-LOCKING / TRAILING SL:
        #   - Allow SL to move into profit zone (above entry for BUY, below entry for SELL)
        if order_type == 'BUY':
            if target_profit_usd <= 0:
                # Loss-protection validation
                if target_sl >= effective_entry:
                    logger.error(f"INVALID SL CALCULATION (LOSS ZONE): {symbol} BUY | "
                               f"Entry: {effective_entry:.5f} | Target SL: {target_sl:.5f} | "
                               f"SL must be BELOW entry for BUY when protecting loss")
                    # Calculate a safe SL (1% below entry as fallback)
                    target_sl = effective_entry * 0.99
                elif target_sl <= 0:
                    logger.error(f"INVALID SL CALCULATION (LOSS ZONE): {symbol} BUY | "
                               f"Entry: {effective_entry:.5f} | Target SL: {target_sl:.5f} | "
                               f"SL cannot be negative or zero")
                    # Calculate a safe SL (1% below entry as fallback, but ensure positive)
                    target_sl = max(effective_entry * 0.99, point)
            else:
                # Profit-locking: only ensure SL is positive
                if target_sl <= 0:
                    logger.error(f"INVALID SL CALCULATION (PROFIT LOCK): {symbol} BUY | "
                               f"Entry: {effective_entry:.5f} | Target SL: {target_sl:.5f} | "
                               f"SL cannot be negative or zero")
                    target_sl = max(effective_entry * 0.99, point)
        else:  # SELL
            if target_profit_usd <= 0:
                # Loss-protection validation
                if target_sl <= effective_entry:
                    logger.error(f"INVALID SL CALCULATION (LOSS ZONE): {symbol} SELL | "
                               f"Entry: {effective_entry:.5f} | Target SL: {target_sl:.5f} | "
                               f"SL must be ABOVE entry for SELL when protecting loss")
                    # Calculate a safe SL (1% above entry as fallback)
                    target_sl = effective_entry * 1.01
                elif target_sl <= 0:
                    logger.error(f"INVALID SL CALCULATION (LOSS ZONE): {symbol} SELL | "
                               f"Entry: {effective_entry:.5f} | Target SL: {target_sl:.5f} | "
                               f"SL cannot be negative or zero")
                    # Calculate a safe SL (1% above entry as fallback, but ensure positive)
                    target_sl = max(effective_entry * 1.01, point)
            else:
                # Profit-locking: only ensure SL is positive
                if target_sl <= 0:
                    logger.error(f"INVALID SL CALCULATION (PROFIT LOCK): {symbol} SELL | "
                               f"Entry: {effective_entry:.5f} | Target SL: {target_sl:.5f} | "
                               f"SL cannot be negative or zero")
                    target_sl = max(effective_entry * 1.01, point)
        
        # CRITICAL VALIDATION: Check if SL difference is reasonable (< 10% of entry price)
        sl_diff_pct = abs(target_sl - effective_entry) / effective_entry if effective_entry > 0 else 0
        if sl_diff_pct > 0.10:  # More than 10% difference
            logger.error(f"SUSPICIOUS SL CALCULATION: {symbol} {order_type} | "
                       f"Entry: {effective_entry:.5f} | Target SL: {target_sl:.5f} | "
                       f"Difference: {sl_diff_pct*100:.1f}% | This seems wrong, blocking SL update")
            # Block this update - calculation is likely wrong
            raise ValueError(f"SL calculation produced suspicious result: {sl_diff_pct*100:.1f}% difference from entry")
        
        # CRITICAL FIX: Validate contract size produces correct effective SL
        # If position is available, verify the calculated SL will produce the target profit
        if position is not None:
            try:
                # Calculate what the effective SL profit would be with this target_sl
                test_position = position.copy()
                test_position['sl'] = target_sl
                test_effective_sl = self.get_effective_sl_profit(test_position)
                test_error = abs(test_effective_sl - target_profit_usd)
                
                # If error is too large, log warning but don't block (contract size may need adjustment)
                # Use configurable tolerance (default 1.0, but use 0.50 for contract size validation warnings)
                contract_size_warning_tolerance = 0.50  # Warning threshold for contract size validation
                if test_error > contract_size_warning_tolerance:
                    logger.warning(f"CONTRACT SIZE VALIDATION: {symbol} {order_type} | "
                                 f"Target profit: ${target_profit_usd:.2f} | "
                                 f"Calculated effective SL: ${test_effective_sl:.2f} | "
                                 f"Error: ${test_error:.2f} | "
                                 f"Contract size may need adjustment (current: {contract_size:.2f})")
            except Exception as e:
                logger.debug(f"Could not validate contract size for {symbol}: {e}")
        
        return target_sl
    
    def _adjust_sl_for_broker_constraints(self, target_sl: float, current_sl: float,
                                          order_type: str, symbol_info: Dict[str, Any],
                                          current_bid: float, current_ask: float,
                                          entry_price: Optional[float] = None) -> float:
        """
        Adjust SL price to respect broker constraints (stops_level, spread).
        
        Args:
            target_sl: Desired SL price
            current_sl: Current SL price (0.0 if not set)
            order_type: 'BUY' or 'SELL'
            symbol_info: Symbol information
            current_bid: Current BID price
            current_ask: Current ASK price
        
        Returns:
            Adjusted SL price that respects broker constraints
        """
        point = symbol_info.get('point', 0.00001)
        stops_level = symbol_info.get('trade_stops_level', 0)
        spread = current_ask - current_bid
        
        if stops_level > 0:
            min_distance = stops_level * point
            
            if order_type == 'BUY':
                # For BUY: SL must be at least min_distance below current BID
                min_allowed_sl = current_bid - min_distance
                if target_sl > min_allowed_sl:
                    target_sl = min_allowed_sl
            else:  # SELL
                # For SELL: SL must be at least min_distance above current ASK
                min_allowed_sl = current_ask + min_distance
                if target_sl < min_allowed_sl:
                    target_sl = min_allowed_sl
        
        # Ensure SL is valid for order type and respects stops_level
        if order_type == 'BUY':
            # BUY SL must be below entry (and below current BID)
            if target_sl >= current_bid:
                # Calculate minimum distance based on stops_level
                if stops_level > 0:
                    min_distance = stops_level * point
                    target_sl = current_bid - min_distance
            else:
                target_sl = current_bid - (point * 10)  # At least 1 pip below
        else:  # SELL
            # SELL SL must be above entry (and above current ASK)
            if target_sl <= current_ask:
                # Calculate minimum distance based on stops_level
                if stops_level > 0:
                    min_distance = stops_level * point
                    target_sl = current_ask + min_distance
                else:
                    target_sl = current_ask + (point * 10)  # At least 1 pip above
        
        # CRITICAL FIX: Never decrease SL (only move in favorable direction)
        # BUT: Allow moving from loss zone to profit zone (this is always favorable)
        # For BUY: Higher SL = better (less loss or more profit), Lower SL = worse (more loss)
        # For SELL: Lower SL = better (less loss or more profit), Higher SL = worse (more loss)
        if current_sl > 0 and entry_price is not None and entry_price > 0:
            # Check if we're moving from loss zone to profit zone (always allow this)
            if order_type == 'BUY':
                # For BUY: SL below entry = loss zone, SL above entry = profit zone
                current_in_loss = current_sl < entry_price
                target_in_profit = target_sl > entry_price
                moving_to_profit = current_in_loss and target_in_profit
                
                if not moving_to_profit:
                    # Not moving to profit zone - apply normal constraint
                    # For BUY: SL can only move UP (closer to entry, less loss or more profit)
                    # target_sl should be >= current_sl (higher or equal)
                    if target_sl < current_sl:
                        # Target is lower (worse), keep current SL
                        logger.debug(f"SL adjustment blocked for BUY: target {target_sl:.5f} < current {current_sl:.5f}")
                        target_sl = current_sl
                else:
                    # Moving from loss zone to profit zone - always allow
                    logger.debug(f"SL adjustment allowed for BUY: moving from loss zone ({current_sl:.5f}) to profit zone ({target_sl:.5f})")
            else:  # SELL
                # For SELL: SL above entry = loss zone, SL below entry = profit zone
                current_in_loss = current_sl > entry_price
                target_in_profit = target_sl < entry_price
                moving_to_profit = current_in_loss and target_in_profit
                
                if not moving_to_profit:
                    # Not moving to profit zone - apply normal constraint
                    # For SELL: SL can only move DOWN (closer to entry, less loss or more profit)
                    # target_sl should be <= current_sl (lower or equal)
                    if target_sl > current_sl:
                        # Target is higher (worse), keep current SL
                        logger.debug(f"SL adjustment blocked for SELL: target {target_sl:.5f} > current {current_sl:.5f}")
                        target_sl = current_sl
                else:
                    # Moving from loss zone to profit zone - always allow
                    logger.debug(f"SL adjustment allowed for SELL: moving from loss zone ({current_sl:.5f}) to profit zone ({target_sl:.5f})")
        elif current_sl > 0:
            # Fallback: apply normal constraint if entry_price not available
            if order_type == 'BUY':
                if target_sl < current_sl:
                    logger.debug(f"SL adjustment blocked for BUY: target {target_sl:.5f} < current {current_sl:.5f}")
                    target_sl = current_sl
            else:  # SELL
                if target_sl > current_sl:
                    logger.debug(f"SL adjustment blocked for SELL: target {target_sl:.5f} > current {current_sl:.5f}")
                    target_sl = current_sl
        
        return target_sl
    
    def _enforce_strict_loss_emergency_lockfree(self, position: Dict[str, Any]) -> Tuple[bool, str, Optional[float]]:
        """
        EMERGENCY lock-free strict loss enforcement.
        
        EMERGENCY LOGIC RESTRICTION: This MUST NOT override:
        - Trailing SL
        - Profit lock SL
        
        Emergency logic only applies when:
        - No better SL exists (no trailing/profit lock applicable)
        - Trade is in loss (profit < 0)
        
        This method bypasses ALL locks and directly modifies the order via MT5.
        Used ONLY when lock acquisition fails for losing trades to ensure positions
        are NEVER left unprotected.
        
        CRITICAL: This method does NOT acquire any locks - it directly calls MT5.
        
        Returns:
            (success, reason, target_sl_price)
        """
        current_profit = position.get('profit', 0.0)
        symbol = position.get('symbol', '')
        ticket = position.get('ticket', 0)
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        current_sl = position.get('sl', 0.0)
        
        mode = "BACKTEST" if self.config.get('mode') == 'backtest' else "LIVE"
        
        logger.critical(f"[EMERGENCY LOCK-FREE] Starting emergency strict loss enforcement: "
                     f"mode={mode} | ticket={ticket} | symbol={symbol} | profit=${current_profit:.2f}")
        
        # EMERGENCY LOGIC RESTRICTION: Check if better SL exists
        # Use compute_authoritative_sl to check if trailing/profit lock should apply
        authoritative_result = self.compute_authoritative_sl(position)
        authority_source = authoritative_result.get('authority_source')
        
        # If trailing or profit lock should apply, DO NOT override with emergency logic
        if authority_source in ['TRAILING', 'PROFIT_LOCK']:
            logger.critical(f"[EMERGENCY LOCK-FREE] BLOCKED: {symbol} Ticket {ticket} | "
                          f"Better SL exists (authority: {authority_source}) | "
                          f"Emergency logic cannot override trailing/profit lock")
            return False, f"Better SL exists ({authority_source}) - emergency logic blocked", None
        
        if current_profit >= 0:
            return False, "Trade not in loss (emergency path)", None
        
        # Get symbol info (no locks needed)
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, "Cannot get symbol info (emergency)", None
        
        # Get current market prices (no locks needed)
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick is None:
            return False, "Cannot get market prices (emergency)", None
        
        # Calculate target SL for -$2.00 loss
        try:
            target_sl = self._calculate_target_sl_price(
                entry_price, -self.max_risk_usd, order_type, lot_size, symbol_info, position=position
            )
        except Exception as e:
            logger.critical(f"[EMERGENCY LOCK-FREE] SL calculation failed: mode={mode} | "
                         f"ticket={ticket} | symbol={symbol} | error={e}")
            return False, f"SL calculation error (emergency): {e}", None
        
        # Adjust for broker constraints
        target_sl = self._adjust_sl_for_broker_constraints(
            target_sl, current_sl, order_type, symbol_info, tick.bid, tick.ask, entry_price=entry_price
        )
        
        # EMERGENCY LOGIC RESTRICTION: Ensure we don't worsen existing SL
        if current_sl > 0:
            would_worsen = False
            if order_type == 'BUY':
                # For BUY: SL should not decrease (higher = better)
                if target_sl < current_sl:
                    would_worsen = True
            else:  # SELL
                # For SELL: SL should not increase (lower = better)
                if target_sl > current_sl:
                    would_worsen = True
            
            if would_worsen:
                logger.critical(f"[EMERGENCY LOCK-FREE] BLOCKED: {symbol} Ticket {ticket} | "
                              f"Emergency SL {target_sl:.5f} would worsen current SL {current_sl:.5f} | "
                              f"Emergency logic only applies when no better SL exists")
                return False, "Emergency SL would worsen current SL - blocked", None
        
        # DIRECT MT5 modification - NO LOCKS
        try:
            success = self.order_manager.modify_order(ticket, stop_loss_price=target_sl)
            
            if success:
                logger.critical(f"[EMERGENCY LOCK-FREE] HARD SL ENFORCED: mode={mode} | "
                             f"ticket={ticket} | symbol={symbol} | "
                             f"sl_price={target_sl:.5f} | reason=Emergency lock-free strict loss enforcement (-${self.max_risk_usd:.2f})")
                return True, f"Emergency lock-free strict loss enforcement (-${self.max_risk_usd:.2f})", target_sl
            else:
                logger.critical(f"[EMERGENCY LOCK-FREE] HARD SL FAILED: mode={mode} | "
                             f"ticket={ticket} | symbol={symbol} | "
                             f"target_sl={target_sl:.5f} | reason=Direct MT5 modification failed")
                return False, "Direct MT5 modification failed (emergency)", None
        except Exception as e:
            logger.critical(f"[EMERGENCY LOCK-FREE] HARD SL EXCEPTION: mode={mode} | "
                         f"ticket={ticket} | symbol={symbol} | error={e}", exc_info=True)
            return False, f"Emergency lock-free exception: {e}", None
    
    def _enforce_strict_loss_limit(self, position: Dict[str, Any]) -> Tuple[bool, str, Optional[float]]:
        """
        Enforce strict -$2.00 stop-loss for losing trades.
        
        Returns:
            (success, reason, target_sl_price)
        """
        tracer = get_tracer()
        current_profit = position.get('profit', 0.0)
        symbol = position.get('symbol', '')
        ticket = position.get('ticket', 0)
        
        # Only enforce for losing trades
        if current_profit >= 0:
            tracer.trace(
                function_name="SLManager._enforce_strict_loss_limit",
                expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                actual=f"Trade not in loss (profit: ${current_profit:.2f}), skipping",
                status="OK",
                ticket=ticket,
                symbol=symbol,
                profit=current_profit,
                reason="Trade not in loss"
            )
            return False, "Trade not in loss", None
        
        tracer.trace(
            function_name="SLManager._enforce_strict_loss_limit",
            expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
            actual=f"Starting strict loss enforcement (profit: ${current_profit:.2f})",
            status="OK",
            ticket=ticket,
            symbol=symbol,
            profit=current_profit
        )
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        current_sl = position.get('sl', 0.0)
        current_profit = position.get('profit', 0.0)
        current_price = position.get('price_current', 0.0)
        
        if not symbol or entry_price <= 0 or lot_size <= 0:
            return False, "Invalid position data", None
        
        # Get symbol info
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, "Cannot get symbol info", None
        
        # Get current market prices
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick is None:
            return False, "Cannot get market prices", None
        
        current_bid = tick.bid
        current_ask = tick.ask
        
        # CRITICAL FIX: Use correct entry price based on order type
        # For BUY: use ASK (what we paid)
        # For SELL: use BID (what we received)
        # MT5's price_open might be BID for BUY orders, so we need to correct it
        point = symbol_info.get('point', 0.00001)
        
        if order_type == 'BUY':
            # For BUY, entry should be ASK
            if abs(entry_price - current_bid) < abs(entry_price - current_ask):
                # Entry is closer to BID, but for BUY we need ASK
                spread = current_ask - current_bid
                if spread > 0:
                    effective_entry = current_ask
                    logger.info(f"🔧 ENTRY PRICE CORRECTED (BUY): {symbol} Ticket {ticket} | "
                              f"Original: {entry_price:.5f} | Corrected (ASK): {effective_entry:.5f} | "
                              f"Spread: {spread:.5f}")
                else:
                    effective_entry = entry_price
            else:
                effective_entry = entry_price
        else:  # SELL
            # For SELL, entry should be BID
            if abs(entry_price - current_ask) < abs(entry_price - current_bid):
                # Entry is closer to ASK, but for SELL we need BID
                spread = current_ask - current_bid
                if spread > 0:
                    effective_entry = current_bid
                    logger.info(f"🔧 ENTRY PRICE CORRECTED (SELL): {symbol} Ticket {ticket} | "
                              f"Original: {entry_price:.5f} | Corrected (BID): {effective_entry:.5f} | "
                              f"Spread: {spread:.5f}")
                else:
                    effective_entry = entry_price
            else:
                effective_entry = entry_price
        
        # Use corrected entry for calculation
        entry_price = effective_entry
        
        # Calculate target SL for -$2.00 loss
        try:
            target_sl = self._calculate_target_sl_price(
                entry_price, -self.max_risk_usd, order_type, lot_size, symbol_info, position=position
            )
            
            # CRITICAL VALIDATION: Log calculation details for debugging
            logger.debug(f"🔍 SL CALCULATION DEBUG: {symbol} Ticket {ticket} | "
                        f"Entry: {entry_price:.5f} | Target SL: {target_sl:.5f} | "
                        f"Order: {order_type} | Lot: {lot_size} | "
                        f"Current Price: {current_price:.5f} | Current Profit: ${current_profit:.2f}")
        except ValueError as e:
            # SL calculation produced suspicious result - disable symbol and return
            logger.critical(f"CRITICAL: SL calculation failed for {symbol} Ticket {ticket}: {e}")
            logger.critical(f"DISABLING SL updates for {symbol} until fix validated")
            self._disabled_symbols.add(symbol)
            return False, f"SL calculation error: {e}", None
        
        # Adjust for broker constraints (pass entry_price to allow loss->profit zone transitions)
        target_sl = self._adjust_sl_for_broker_constraints(
            target_sl, current_sl, order_type, symbol_info, current_bid, current_ask, entry_price=entry_price
        )
        
        # Check if SL needs updating
        # CRITICAL FIX: Don't just check price difference - verify effective SL matches target
        # IMPORTANT: If current_sl == 0.0, we MUST apply SL (no SL is set, so we need to set one)
        # CRITICAL: If effective SL is WORSE than -$2.00, ALWAYS update regardless of tolerance
        if current_sl > 0:
            # Calculate effective SL for current SL to verify it matches target
            current_effective_sl = self.get_effective_sl_profit(position)
            target_effective_sl = -self.max_risk_usd
            
            # CRITICAL: If effective SL is WORSE than target (more negative), ALWAYS update
            # This ensures we never allow risk to exceed -$2.00, even if broker adjusted SL
            if current_effective_sl < target_effective_sl:
                # Effective SL is WORSE than -$2.00 - MUST update immediately
                logger.critical(f"CRITICAL: Effective SL ${current_effective_sl:.2f} is WORSE than target ${target_effective_sl:.2f} | "
                             f"{symbol} Ticket {ticket} | "
                             f"Current SL price: {current_sl:.5f} | Target SL price: {target_sl:.5f} | "
                             f"MUST update to correct effective SL immediately")
                # Continue to apply SL update below
            else:
                # Effective SL is better than or equal to target - check if within tolerance
                effective_sl_error = abs(current_effective_sl - target_effective_sl)
                
                # For forex: $0.50 tolerance, for indices/crypto: max(1.0, point * 100) but never exceed $1.00
                point = symbol_info.get('point', 0.00001)
                if point >= 0.01:  # Index/Crypto
                    tolerance = min(1.0, max(0.5, point * 100 / 100))  # Max $1.00, prefer $0.50
                else:  # Forex
                    tolerance = 0.50  # $0.50 for forex
                
                if effective_sl_error < tolerance:
                    # Effective SL is correct - no update needed
                    logger.debug(f"SL already correct: {symbol} Ticket {ticket} | "
                               f"Effective SL: ${current_effective_sl:.2f} (target: ${target_effective_sl:.2f}, error: ${effective_sl_error:.2f})")
                    return False, f"SL already at strict loss limit (effective: ${current_effective_sl:.2f})", None
                else:
                    # Effective SL doesn't match - need to update
                    logger.warning(f"SL PRICE CLOSE BUT EFFECTIVE SL MISMATCH: {symbol} Ticket {ticket} | "
                                 f"Current SL price: {current_sl:.5f} | Target SL price: {target_sl:.5f} | "
                                 f"Effective SL: ${current_effective_sl:.2f} (target: ${target_effective_sl:.2f}, error: ${effective_sl_error:.2f}) | "
                                 f"Will update to correct effective SL")
        else:
            # current_sl == 0.0 - no SL is set, we MUST apply one for losing trades
            logger.debug(f"No SL set (current_sl=0.0) for losing trade {symbol} Ticket {ticket} | Will apply strict loss SL")
        
        # Apply SL update (pass position for emergency strict SL if this fails)
        # CRITICAL: For losing trades, this MUST succeed - use emergency bypass if needed
        success = self._apply_sl_update(ticket, symbol, target_sl, -self.max_risk_usd,
                                        "Strict loss enforcement (-$2.00)", position=position)
        
        if success:
            # Verify the update was successful by checking effective SL
            time.sleep(0.1)  # Brief delay for broker to process
            verify_position = self.order_manager.get_position_by_ticket(ticket)
            if verify_position:
                verify_effective_sl = self.get_effective_sl_profit(verify_position)
                verify_error = abs(verify_effective_sl - (-self.max_risk_usd))
                if verify_error < 0.50:  # Within tolerance
                    mode = "BACKTEST" if self.config.get('mode') == 'backtest' else "LIVE"
                    logger.info(f"[HARD SL] STRICT LOSS ENFORCED: mode={mode} | ticket={ticket} | symbol={symbol} | "
                              f"sl_price={target_sl:.5f} | effective_sl=${verify_effective_sl:.2f} | "
                              f"reason=Strict loss enforcement (-${self.max_risk_usd:.2f})")
                    tracer.trace(
                        function_name="SLManager._enforce_strict_loss_limit",
                        expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                        actual=f"Strict loss enforced and verified (effective SL: ${verify_effective_sl:.2f})",
                        status="OK",
                        ticket=ticket,
                        symbol=symbol,
                        effective_sl=verify_effective_sl,
                        target_sl=target_sl
                    )
                    return True, f"Strict loss enforcement (-${self.max_risk_usd:.2f})", target_sl
                else:
                    logger.warning(f"STRICT LOSS VERIFICATION FAILED: {symbol} Ticket {ticket} | "
                                 f"Effective SL: ${verify_effective_sl:.2f} (target: ${-self.max_risk_usd:.2f}, error: ${verify_error:.2f}) | "
                                 f"Will retry next cycle")
                    tracer.trace(
                        function_name="SLManager._enforce_strict_loss_limit",
                        expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                        actual=f"Strict loss applied but verification failed (effective: ${verify_effective_sl:.2f}, error: ${verify_error:.2f})",
                        status="WARNING",
                        ticket=ticket,
                        symbol=symbol,
                        effective_sl=verify_effective_sl,
                        target_effective_sl=-self.max_risk_usd,
                        error=verify_error,
                        reason="Verification failed"
                    )
                    return False, f"Strict loss SL applied but verification failed (effective: ${verify_effective_sl:.2f})", None
            else:
                logger.warning(f"Cannot verify strict loss SL for {symbol} Ticket {ticket}")
                tracer.trace(
                    function_name="SLManager._enforce_strict_loss_limit",
                    expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                    actual=f"Cannot verify strict loss SL (position not found)",
                    status="WARNING",
                    ticket=ticket,
                    symbol=symbol,
                    reason="Cannot verify position"
                )
                return False, "Cannot verify strict loss SL", None
        else:
            # CRITICAL: If normal update failed, try emergency fallback immediately
            logger.error(f"STRICT LOSS UPDATE FAILED: {symbol} Ticket {ticket} | "
                        f"Target SL: {target_sl:.5f} | Attempting emergency fallback...")
            
            # Emergency fallback: Direct MT5 modification with corrected calculation
            try:
                # Recalculate with fresh market data
                fresh_tick = self.mt5_connector.get_symbol_info_tick(symbol)
                if fresh_tick:
                    # Use emergency calculation path
                    emergency_sl = self._calculate_target_sl_price(
                        entry_price, -self.max_risk_usd, order_type, lot_size, symbol_info, position=position
                    )
                    
                    # Direct modification without verification delay
                    emergency_success = self.order_manager.modify_order(
                        ticket, stop_loss_price=emergency_sl
                    )
                    
                    if emergency_success:
                        logger.critical(f"EMERGENCY STRICT SL APPLIED: {symbol} Ticket {ticket} | "
                                      f"Emergency SL: {emergency_sl:.5f} | "
                                      f"Direct modification succeeded")
                        return True, f"Emergency strict loss enforcement (-${self.max_risk_usd:.2f})", emergency_sl
                    else:
                        logger.critical(f"EMERGENCY STRICT SL FAILED: {symbol} Ticket {ticket} | "
                                      f"Emergency SL: {emergency_sl:.5f} | "
                                      f"Direct modification failed")
                        return False, "Emergency strict loss SL failed", None
                else:
                    return False, "Cannot get market prices for emergency SL", None
            except Exception as emergency_error:
                logger.critical(f"EMERGENCY STRICT SL EXCEPTION: {symbol} Ticket {ticket} | "
                              f"Error: {emergency_error}", exc_info=True)
                return False, f"Emergency strict loss SL exception: {emergency_error}", None
    
    def _apply_break_even_sl(self, position: Dict[str, Any], current_profit: float) -> Tuple[bool, str, Optional[float]]:
        """
        [DISABLED] Break-even SL - DISABLED per Step 2c requirement.
        
        No break-even logic; lock profit immediately at sweet spot ($0.03) or above.
        This method always returns False and is never executed in the normal flow.
        
        Returns:
            (success, reason, target_sl_price)
        """
        # Break-even is permanently disabled - always return False
        # Per Step 2c: No break-even logic; lock profit immediately at sweet spot or above
        return False, "Break-even disabled per Step 2c requirement (lock profit immediately at sweet spot)", None
        
        # Original implementation below (kept for reference, never executed due to early return):
        if not self.break_even_enabled:
            return False, "Break-even disabled", None
        
        if current_profit <= 0 or current_profit >= self.sweet_spot_min:
            return False, "Profit outside break-even range", None
        
        ticket = position.get('ticket', 0)
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        current_sl = position.get('sl', 0.0)
        
        # Track break-even start time
        # CRITICAL: Reset start time if profit was negative in previous cycle
        # This ensures we only count time when profit is continuously positive
        with self._tracking_lock:
            tracking = self._position_tracking.get(ticket, {})
            last_profit = tracking.get('last_profit', None)
            
            # If profit was negative or None in previous cycle, reset start time
            if last_profit is None or last_profit <= 0:
                tracking['break_even_start_time'] = datetime.now()
                # Format last_profit safely (avoid conditional in format specifier)
                last_profit_display = f"${last_profit:.2f}" if last_profit is not None else "N/A"
                logger.info(f"🔄 BREAK-EVEN: {position.get('symbol', '')} Ticket {ticket} | "
                           f"Profit became positive (${current_profit:.2f}) | "
                           f"Reset start time (previous profit: {last_profit_display}) | "
                           f"Will trigger break-even after {self.break_even_duration_seconds}s")
            
            # Update last profit for next cycle
            tracking['last_profit'] = current_profit
            
            if 'break_even_start_time' not in tracking:
                tracking['break_even_start_time'] = datetime.now()
            
            self._position_tracking[ticket] = tracking
            start_time = tracking['break_even_start_time']
            duration = (datetime.now() - start_time).total_seconds()
        
        if duration < self.break_even_duration_seconds:
            logger.info(f"⏳ BREAK-EVEN: {position.get('symbol', '')} Ticket {ticket} | "
                       f"Profit: ${current_profit:.2f} | Waiting for duration ({duration:.1f}s < {self.break_even_duration_seconds}s) | "
                       f"Start time: {start_time.strftime('%H:%M:%S')}")
            return False, f"Break-even duration not met ({duration:.1f}s < {self.break_even_duration_seconds}s)", None
        
        # Check if already at break-even
        if current_sl > 0:
            sl_diff = abs(current_sl - entry_price)
            symbol_info = self.mt5_connector.get_symbol_info(position.get('symbol', ''))
            if symbol_info:
                point = symbol_info.get('point', 0.00001)
                if sl_diff < (point * 10):  # Within 1 pip, consider at break-even
                    return False, "SL already at break-even", None
        
        # Apply break-even SL (SL = entry price)
        symbol_info = self.mt5_connector.get_symbol_info(position.get('symbol', ''))
        if symbol_info is None:
            return False, "Cannot get symbol info", None
        
        tick = self.mt5_connector.get_symbol_info_tick(position.get('symbol', ''))
        if tick is None:
            return False, "Cannot get market prices", None
        
        target_sl = entry_price
        
        # Adjust for broker constraints (pass entry_price to allow loss->profit zone transitions)
        target_sl = self._adjust_sl_for_broker_constraints(
            target_sl, current_sl, order_type, symbol_info, tick.bid, tick.ask, entry_price=entry_price
        )
        
        success = self._apply_sl_update(ticket, position.get('symbol', ''), target_sl, 0.0,
                                        "Break-even SL (profit > $0 but < $0.03 for 2+ seconds)", position=position)
        
        if success:
            # Track locked profit for next comparison
            with self._tracking_lock:
                tracking = self._position_tracking.get(ticket, {})
                tracking['last_locked_profit'] = current_profit
                self._position_tracking[ticket] = tracking
            return True, "Break-even SL applied", target_sl
        else:
            return False, "Failed to apply break-even SL", None
    
    def _apply_sweet_spot_lock(self, position: Dict[str, Any], current_profit: float) -> Tuple[bool, str, Optional[float]]:
        """
        Apply sweet-spot profit locking if profit ≥ $0.03 and ≤ $0.10.
        
        SL can only increase (never decrease) in this range.
        
        Returns:
            (success, reason, target_sl_price)
        """
        if current_profit < self.sweet_spot_min or current_profit > self.sweet_spot_max:
            return False, "Profit outside sweet-spot range", None
        
        symbol = position.get('symbol', '')
        ticket = position.get('ticket', 0)
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        current_sl = position.get('sl', 0.0)
        
        # Get symbol info
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, "Cannot get symbol info", None
        
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick is None:
            return False, "Cannot get market prices", None
        
        # Calculate target SL to lock in current profit
        # CRITICAL: Lock in the ACTUAL current profit, not just minimum $0.03
        # This ensures we preserve as much profit as possible in the sweet spot range
        profit_to_lock = min(current_profit, self.sweet_spot_max)  # Lock in current profit, up to $0.10 max
        
        logger.info(f"🔍 SWEET SPOT CALCULATION: {symbol} Ticket {ticket} | "
                   f"Current profit: ${current_profit:.2f} | "
                   f"Profit to lock: ${profit_to_lock:.2f} | "
                   f"Entry: {entry_price:.5f} | Order: {order_type}")
        
        try:
            target_sl = self._calculate_target_sl_price(
                entry_price, profit_to_lock, order_type, lot_size, symbol_info, position=position
            )
            logger.info(f"🔍 SWEET SPOT TARGET SL: {symbol} Ticket {ticket} | "
                       f"Calculated target SL: {target_sl:.5f}")
        except Exception as e:
            logger.error(f"SWEET SPOT CALCULATION ERROR: {symbol} Ticket {ticket} | {e}", exc_info=True)
            return False, f"SL calculation error: {e}", None
        
        # Adjust for broker constraints (pass entry_price to allow loss->profit zone transitions)
        target_sl = self._adjust_sl_for_broker_constraints(
            target_sl, current_sl, order_type, symbol_info, tick.bid, tick.ask, entry_price=entry_price
        )
        
        # Check if SL needs updating
        # CRITICAL: Allow moving from loss zone to profit zone (always favorable)
        if current_sl > 0:
            # Check if we're moving from loss zone to profit zone
            current_in_loss = False
            target_in_profit = False
            
            if order_type == 'BUY':
                # For BUY: SL below entry = loss zone, SL above entry = profit zone
                current_in_loss = current_sl < entry_price
                target_in_profit = target_sl > entry_price
            else:  # SELL
                # For SELL: SL above entry = loss zone, SL below entry = profit zone
                current_in_loss = current_sl > entry_price
                target_in_profit = target_sl < entry_price
            
            moving_to_profit = current_in_loss and target_in_profit
            
            if not moving_to_profit:
                # Not moving to profit zone - apply normal constraint
                # CRITICAL: For sweet spot, we want to lock in profit, so we need to check if target SL
                # would actually lock in MORE profit than current SL
                current_effective_sl = self.get_effective_sl_profit(position)
                target_effective_sl = profit_to_lock  # This is the profit we want to lock
                
                logger.info(f"🔍 SWEET SPOT COMPARISON: {symbol} Ticket {ticket} | "
                           f"Current SL: {current_sl:.5f} (effective: ${current_effective_sl:.2f}) | "
                           f"Target SL: {target_sl:.5f} (target profit: ${target_effective_sl:.2f}) | "
                           f"Current profit: ${current_profit:.2f}")
                
                if order_type == 'BUY':
                    # For BUY: SL can only move UP (higher = better)
                    # CRITICAL: Always update if profit increased - lock in more profit
                    if target_sl <= current_sl:
                        # Check if current SL is already locking in enough profit
                        # BUT: If current profit increased, we MUST update to lock in more profit
                        if current_effective_sl >= profit_to_lock - 0.01:  # Within $0.01 tolerance
                            # Check if profit has increased since last update
                            with self._tracking_lock:
                                tracking = self._position_tracking.get(ticket, {})
                                last_locked_profit = tracking.get('last_locked_profit', 0.0)
                            
                            # If profit increased, always update to lock in more
                            if current_profit > last_locked_profit + 0.01:
                                logger.info(f"🔄 SWEET SPOT: {symbol} Ticket {ticket} | "
                                          f"Profit increased from ${last_locked_profit:.2f} to ${current_profit:.2f} | "
                                          f"Updating SL to lock in more profit")
                                # Allow update - profit increased
                            else:
                                logger.debug(f"[OK] SWEET SPOT: {symbol} Ticket {ticket} | "
                                           f"Current SL already locks in ${current_effective_sl:.2f} (target: ${profit_to_lock:.2f})")
                                return False, f"SL already locks in sufficient profit (${current_effective_sl:.2f} >= ${profit_to_lock:.2f})", None
                        else:
                            # Target SL is lower but should lock in more profit - allow update
                            logger.info(f"🔄 SWEET SPOT: {symbol} Ticket {ticket} | "
                                      f"Updating SL to lock in more profit (current: ${current_effective_sl:.2f}, target: ${profit_to_lock:.2f})")
                            # Allow the update
                else:  # SELL
                    # For SELL: SL can only move DOWN (lower = better)
                    # CRITICAL: Always update if profit increased - lock in more profit
                    if target_sl >= current_sl:
                        # Check if current SL is already locking in enough profit
                        # BUT: If current profit increased, we MUST update to lock in more profit
                        if current_effective_sl >= profit_to_lock - 0.01:  # Within $0.01 tolerance
                            # Check if profit has increased since last update
                            with self._tracking_lock:
                                tracking = self._position_tracking.get(ticket, {})
                                last_locked_profit = tracking.get('last_locked_profit', 0.0)
                            
                            # If profit increased, always update to lock in more
                            if current_profit > last_locked_profit + 0.01:
                                logger.info(f"🔄 SWEET SPOT: {symbol} Ticket {ticket} | "
                                          f"Profit increased from ${last_locked_profit:.2f} to ${current_profit:.2f} | "
                                          f"Updating SL to lock in more profit")
                                # Allow update - profit increased
                            else:
                                logger.debug(f"[OK] SWEET SPOT: {symbol} Ticket {ticket} | "
                                           f"Current SL already locks in ${current_effective_sl:.2f} (target: ${profit_to_lock:.2f})")
                                return False, f"SL already locks in sufficient profit (${current_effective_sl:.2f} >= ${profit_to_lock:.2f})", None
                        else:
                            # Target SL is higher but should lock in more profit - allow update
                            logger.info(f"🔄 SWEET SPOT: {symbol} Ticket {ticket} | "
                                      f"Updating SL to lock in more profit (current: ${current_effective_sl:.2f}, target: ${profit_to_lock:.2f})")
                            # Allow the update
            else:
                # Moving from loss zone to profit zone - always allow
                logger.info(f"[OK] SWEET SPOT: {symbol} Ticket {ticket} | Moving from loss zone ({current_sl:.5f}) to profit zone ({target_sl:.5f})")
        
        # Apply SL update
        success = self._apply_sl_update(ticket, symbol, target_sl, profit_to_lock,
                                        f"Sweet-spot profit locking (${current_profit:.2f} in range ${self.sweet_spot_min:.2f}-${self.sweet_spot_max:.2f})", position=position)
        
        if success:
            # Track locked profit for next comparison
            with self._tracking_lock:
                tracking = self._position_tracking.get(ticket, {})
                tracking['last_locked_profit'] = current_profit
                self._position_tracking[ticket] = tracking
            return True, f"Sweet-spot lock applied (${current_profit:.2f})", target_sl
        else:
            return False, "Failed to apply sweet-spot lock", None
    
    def _apply_trailing_stop(self, position: Dict[str, Any], current_profit: float) -> Tuple[bool, str, Optional[float]]:
        """
        Apply trailing stop if profit > $0.10.
        
        Locks in profit in $0.10 increments, trailing $0.10 behind current price.
        
        Returns:
            (success, reason, target_sl_price)
        """
        if current_profit <= self.trailing_increment_usd:
            return False, f"Profit (${current_profit:.2f}) below trailing threshold (${self.trailing_increment_usd:.2f})", None
        
        symbol = position.get('symbol', '')
        ticket = position.get('ticket', 0)
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        current_sl = position.get('sl', 0.0)
        
        # Get symbol info
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, "Cannot get symbol info", None
        
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick is None:
            return False, "Cannot get market prices", None
        
        # Calculate how much profit to lock in
        # Lock in profit in $0.10 increments, trailing $0.10 behind
        # Example: If profit is $0.25, lock in $0.15 (trailing $0.10 behind)
        profit_to_lock = current_profit - self.trailing_increment_usd
        # Round down to nearest $0.10 increment
        profit_to_lock = (profit_to_lock // self.trailing_increment_usd) * self.trailing_increment_usd
        profit_to_lock = max(profit_to_lock, self.trailing_increment_usd)  # At least $0.10
        
        # Calculate target SL price
        target_sl = self._calculate_target_sl_price(
            entry_price, profit_to_lock, order_type, lot_size, symbol_info
        )
        
        # Adjust for broker constraints (pass entry_price to allow loss->profit zone transitions)
        target_sl = self._adjust_sl_for_broker_constraints(
            target_sl, current_sl, order_type, symbol_info, tick.bid, tick.ask, entry_price=entry_price
        )
        
        # Check if SL needs updating (only if it would increase locked profit)
        # CRITICAL: Allow moving from loss zone to profit zone (always favorable)
        if current_sl > 0:
            # Check if we're moving from loss zone to profit zone
            current_in_loss = False
            target_in_profit = False
            
            if order_type == 'BUY':
                # For BUY: SL below entry = loss zone, SL above entry = profit zone
                current_in_loss = current_sl < entry_price
                target_in_profit = target_sl > entry_price
            else:  # SELL
                # For SELL: SL above entry = loss zone, SL below entry = profit zone
                current_in_loss = current_sl > entry_price
                target_in_profit = target_sl < entry_price
            
            moving_to_profit = current_in_loss and target_in_profit
            
            if not moving_to_profit:
                # Not moving to profit zone - check if locked profit would increase
                # Calculate current locked profit
                current_locked_profit = self.get_effective_sl_profit(position)
                
                # CRITICAL FIX: Always check if profit has increased - if so, always update
                with self._tracking_lock:
                    tracking = self._position_tracking.get(ticket, {})
                    last_locked_profit = tracking.get('last_locked_profit', 0.0)
                
                # If profit increased by more than $0.01, always update to lock in more profit
                if current_profit > last_locked_profit + 0.01:
                    logger.info(f"🔄 TRAILING STOP: {symbol} Ticket {ticket} | "
                              f"Profit increased from ${last_locked_profit:.2f} to ${current_profit:.2f} | "
                              f"Updating SL to lock in more profit (target: ${profit_to_lock:.2f})")
                    # Allow update - profit increased
                elif profit_to_lock <= current_locked_profit:
                    return False, f"SL already locks in ${current_locked_profit:.2f} (target: ${profit_to_lock:.2f})", None
            else:
                # Moving from loss zone to profit zone - always allow
                logger.info(f"[OK] TRAILING STOP: {symbol} Ticket {ticket} | Moving from loss zone ({current_sl:.5f}) to profit zone ({target_sl:.5f}) | Locking ${profit_to_lock:.2f}")
        
        # Apply SL update
        success = self._apply_sl_update(ticket, symbol, target_sl, profit_to_lock,
                                        f"Trailing stop (profit: ${current_profit:.2f}, locking: ${profit_to_lock:.2f})", position=position)
        
        if success:
            # Track locked profit for next comparison
            with self._tracking_lock:
                tracking = self._position_tracking.get(ticket, {})
                tracking['last_locked_profit'] = current_profit
                self._position_tracking[ticket] = tracking
            return True, f"Trailing stop applied (locking ${profit_to_lock:.2f})", target_sl
        else:
            return False, "Failed to apply trailing stop", None
    
    def _apply_sl_update(self, ticket: int, symbol: str, target_sl_price: float,
                        target_profit_usd: float, reason: str, position: Optional[Dict[str, Any]] = None) -> bool:
        """
        Apply SL update to broker with retry logic and emergency strict SL enforcement.
        
        Apply-and-Verify Flow:
        1. Validate StopLevel and spread before modifying
        2. Apply SL via order_manager.modify_order
        3. Sleep configurable backoff (default 100ms)
        4. Verify via get_position_by_ticket
        5. Retry up to max_retries with 100ms intervals
        6. If still failing and trade is losing, enforce emergency strict SL
        
        Args:
            ticket: Position ticket number
            symbol: Trading symbol
            target_sl_price: Target SL price
            target_profit_usd: Target profit/loss in USD
            reason: Reason for SL update
            position: Optional position dict (for emergency strict SL enforcement)
        
        Returns:
            True if SL was successfully applied, False otherwise
        """
        tracer = get_tracer()  # CRITICAL FIX: Initialize tracer at function start
        max_retries = 3  # Fixed to 3 retries as requested
        retry_delay = 0.1  # Fixed to 100ms (0.1 seconds) as requested
        verification_delay = self.sl_update_verification_delay
        
        apply_start_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        logger.debug(f"[{apply_start_timestamp}] 🎯 _apply_sl_update START | Ticket: {ticket} | Symbol: {symbol} | Target SL: {target_sl_price:.5f}")
        
        # CRITICAL: Check cooldown and minimum delta to prevent oscillation
        # BUT: NEVER block first eligible SL update - cooldown only applies after first update
        current_time_float = time.time()
        is_first_eligible = False
        with self._tracking_lock:
            # Check if this is the first eligible update for this ticket
            first_eligible_info = self._first_eligible_update.get(ticket, {})
            first_eligible_state = first_eligible_info.get('state', 'NONE')
            is_first_eligible = (first_eligible_state == 'NONE' or first_eligible_state == 'PENDING')
            
            # Check cooldown - SKIP for first eligible update
            if ticket in self._sl_update_cooldown and not is_first_eligible:
                cooldown_until = self._sl_update_cooldown[ticket]
                if current_time_float < cooldown_until:
                    remaining = cooldown_until - current_time_float
                    logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                              f"BLOCKED=COOLDOWN | Remaining={remaining*1000:.1f}ms | "
                              f"TargetSL={target_sl_price:.5f} | Reason={reason}")
                    logger.debug(f"[{apply_start_timestamp}] 🚫 SL UPDATE COOLDOWN | Ticket: {ticket} | "
                               f"Cooldown active: {remaining*1000:.1f}ms remaining | Target: {target_sl_price:.5f}")
                    return False
            elif is_first_eligible:
                logger.info(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                          f"Bypassing cooldown check for first eligible SL update | TargetSL={target_sl_price:.5f}")
            
            # Check minimum delta (prevent oscillation)
            if ticket in self._last_sl_price:
                last_sl = self._last_sl_price[ticket]
                sl_delta = abs(target_sl_price - last_sl)
                
                # Get entry price for percentage calculation
                entry_price = position.get('price_open', 0.0) if position else 0.0
                if entry_price > 0:
                    sl_delta_pct = (sl_delta / entry_price) * 100.0
                    if sl_delta_pct < self._min_sl_delta_pct:
                        logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                                  f"BLOCKED=DELTA_TOO_SMALL | Delta={sl_delta:.5f} ({sl_delta_pct:.3f}%) < min {self._min_sl_delta_pct:.3f}% | "
                                  f"LastSL={last_sl:.5f} TargetSL={target_sl_price:.5f} | Reason={reason}")
                        logger.debug(f"[{apply_start_timestamp}] 🚫 SL UPDATE DELTA TOO SMALL | Ticket: {ticket} | "
                                   f"Delta: {sl_delta:.5f} ({sl_delta_pct:.3f}%) < min {self._min_sl_delta_pct:.3f}% | "
                                   f"Last: {last_sl:.5f} | Target: {target_sl_price:.5f}")
                        return False
        
        # CRITICAL: Validate StopLevel and spread BEFORE modifying
        try:
            validate_start = time.time()
            validate_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            logger.debug(f"[{validate_timestamp}] 🔍 Validating StopLevel and spread for {symbol}")
            
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                          f"BLOCKED=SYMBOL_INFO_UNAVAILABLE | TargetSL={target_sl_price:.5f} | Reason={reason}")
                logger.error(f"[{validate_timestamp}] Cannot get symbol info for {symbol}")
                return False
            
            tick = self.mt5_connector.get_symbol_info_tick(symbol)
            if tick is None:
                logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                          f"BLOCKED=MARKET_PRICES_UNAVAILABLE | TargetSL={target_sl_price:.5f} | Reason={reason}")
                logger.error(f"[{validate_timestamp}] Cannot get market prices for {symbol}")
                return False
            
            # CRITICAL FIX: Handle both dict and object tick formats
            tick_bid = tick.get('bid') if isinstance(tick, dict) else getattr(tick, 'bid', None)
            tick_ask = tick.get('ask') if isinstance(tick, dict) else getattr(tick, 'ask', None)
            
            # Validate tick data
            if tick_bid is None or tick_ask is None or math.isnan(tick_bid) or math.isnan(tick_ask):
                logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                          f"BLOCKED=INVALID_TICK_DATA | TargetSL={target_sl_price:.5f} | Reason={reason}")
                logger.error(f"[{validate_timestamp}] Cannot validate: Invalid tick data for {symbol}")
                return False
            
            current_bid = tick_bid
            current_ask = tick_ask
            spread = current_ask - current_bid
            stops_level = symbol_info.get('trade_stops_level', 0)
            point = symbol_info.get('point', 0.00001)
            
            # Get current position to determine order type
            current_position = self.order_manager.get_position_by_ticket(ticket)
            if not current_position:
                # CRITICAL FIX: Position may have been closed - this is not necessarily an error
                logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                          f"BLOCKED=POSITION_NOT_FOUND_VALIDATION | TargetSL={target_sl_price:.5f} | Reason={reason}")
                logger.debug(f"[{validate_timestamp}] Cannot get position {ticket} for validation (position may be closed)")
                return False
            
            order_type = current_position.get('type', '')
            current_sl = current_position.get('sl', 0.0)
            point = symbol_info.get('point', 0.00001)
            
            # CRITICAL FIX: Check if SL is already at target value (prevent oscillations)
            # Use point size as tolerance to account for floating point precision
            sl_difference = abs(current_sl - target_sl_price)
            if sl_difference < point * 2:  # Within 2 points = effectively the same
                logger.debug(f"[{validate_timestamp}] [OK] SL already at target | Ticket: {ticket} | "
                           f"Current: {current_sl:.5f} | Target: {target_sl_price:.5f} | "
                           f"Diff: {sl_difference:.8f} < tolerance {point*2:.8f} | Skipping update")
                return True  # Consider this success - SL is already correct
            
            # CRITICAL FIX: Debounce mechanism - prevent rapid SL changes (oscillation prevention)
            # BUT: NEVER debounce profitable trades - they MUST be able to lock profit
            # Check if this is a profitable trade (profit > 0) - if so, skip debounce
            current_position_for_profit_check = self.order_manager.get_position_by_ticket(ticket)
            is_profitable = False
            if current_position_for_profit_check:
                current_profit_check = current_position_for_profit_check.get('profit', 0.0)
                is_profitable = current_profit_check > 0
            
            # Only apply debounce for non-profitable trades
            if not is_profitable:
                current_time = time.time()
                if ticket in self._last_sl_price and ticket in self._last_sl_update:
                    last_applied_sl = self._last_sl_price[ticket]
                    last_update_datetime = self._last_sl_update[ticket]
                    
                    # Convert datetime to timestamp if needed
                    if isinstance(last_update_datetime, datetime):
                        last_update_time = last_update_datetime.timestamp()
                    else:
                        last_update_time = last_update_datetime
                    
                    time_since_last = current_time - last_update_time
                    
                    # Calculate difference from last applied SL
                    last_sl_diff = abs(target_sl_price - last_applied_sl)
                    
                    # If the new target is very close to the last applied SL AND it's been less than 1 second
                    # This prevents oscillations where different systems calculate slightly different targets
                    min_oscillation_interval = 1.0  # 1 second minimum between similar SL updates
                    oscillation_tolerance = point * 10  # 10 points tolerance (more lenient than current check)
                    
                    if last_sl_diff < oscillation_tolerance and time_since_last < min_oscillation_interval:
                        logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                                  f"BLOCKED=OSCILLATION_PREVENTED | TargetSL={target_sl_price:.5f} LastSL={last_applied_sl:.5f} | "
                                  f"Diff={last_sl_diff:.8f} < tolerance {oscillation_tolerance:.8f} | "
                                  f"TimeSinceLast={time_since_last:.2f}s < {min_oscillation_interval}s | Reason={reason}")
                        logger.debug(f"[{validate_timestamp}] 🚫 SL OSCILLATION PREVENTED | Ticket: {ticket} | "
                                   f"Target: {target_sl_price:.5f} | Last applied: {last_applied_sl:.5f} | "
                                   f"Diff: {last_sl_diff:.8f} < tolerance {oscillation_tolerance:.8f} | "
                                   f"Time since last: {time_since_last:.2f}s < {min_oscillation_interval}s")
                        return True  # Skip this update to prevent oscillation
            else:
                # Profitable trade - allow update even if similar to last (profit locking is critical)
                logger.debug(f"[{validate_timestamp}] [OK] PROFITABLE TRADE - DEBOUNCE BYPASSED | Ticket: {ticket} | "
                           f"Profit: ${current_profit_check:.2f} | Allowing SL update to lock profit")
            
            # Validate StopLevel
            if stops_level > 0:
                min_distance = stops_level * point
                if order_type == 'BUY':
                    min_allowed_sl = current_bid - min_distance
                    if target_sl_price > min_allowed_sl:
                        logger.warning(f"[{validate_timestamp}] [WARNING] SL violates StopLevel: Target {target_sl_price:.5f} > Min allowed {min_allowed_sl:.5f} (stops_level: {stops_level})")
                        target_sl_price = min_allowed_sl
                else:  # SELL
                    min_allowed_sl = current_ask + min_distance
                    if target_sl_price < min_allowed_sl:
                        logger.warning(f"[{validate_timestamp}] [WARNING] SL violates StopLevel: Target {target_sl_price:.5f} < Min allowed {min_allowed_sl:.5f} (stops_level: {stops_level})")
                        target_sl_price = min_allowed_sl
            
            # Validate spread
            if spread > 0:
                spread_pct = (spread / current_bid) * 100 if current_bid > 0 else 0
                if spread_pct > 1.0:  # Spread > 1%
                    logger.warning(f"[{validate_timestamp}] [WARNING] Wide spread detected: {spread:.5f} ({spread_pct:.2f}%)")
            
            validate_end_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            logger.debug(f"[{validate_end_timestamp}] [OK] Validation complete | StopLevel: {stops_level} | Spread: {spread:.5f} | Adjusted SL: {target_sl_price:.5f} (took {(time.time() - validate_start)*1000:.1f}ms)")
            
        except Exception as validate_error:
            validate_error_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            logger.error(f"[{validate_error_timestamp}] Validation exception: {validate_error}", exc_info=True)
            return False
        
        # CRITICAL: Check if this is first eligible update - apply immediately, retries only after first attempt
        with self._tracking_lock:
            first_eligible_info = self._first_eligible_update.get(ticket, {})
            is_first_eligible = (first_eligible_info.get('state', 'NONE') == 'PENDING')
        
        for attempt in range(max_retries):
            attempt_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            is_first_attempt = (attempt == 0)  # Define inside loop
            logger.debug(f"[{attempt_timestamp}] 🔄 SL Update Attempt {attempt + 1}/{max_retries} | "
                        f"Ticket: {ticket} | {'FIRST_ELIGIBLE (immediate)' if is_first_eligible and is_first_attempt else 'Standard'}")
            
            try:
                # Get fresh position before modifying
                read_position_start = time.time()
                read_position_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                logger.debug(f"[{read_position_timestamp}] 📖 Reading position {ticket} before SL update")
                
                pre_update_position = self.order_manager.get_position_by_ticket(ticket)
                if not pre_update_position:
                    logger.error(f"[{read_position_timestamp}] Cannot read position {ticket}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    return False
                
                entry_price = pre_update_position.get('price_open', 0.0)
                read_position_end_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                logger.debug(f"[{read_position_end_timestamp}] [OK] Position read | Entry: {entry_price:.5f} (took {(time.time() - read_position_start)*1000:.1f}ms)")
                
                # Modify order with new SL
                modify_start = time.time()
                modify_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                
                # Get current SL for logging
                current_sl = pre_update_position.get('sl', 0.0)
                logger.info(f"🔥 SL UPDATE ATTEMPT: Ticket={ticket} Symbol={symbol} OldSL={current_sl:.5f} NewSL={target_sl_price:.5f} Reason={reason}")
                logger.debug(f"[{modify_timestamp}] [JUMP] Placing SL modification | Ticket: {ticket} | Target SL: {target_sl_price:.5f}")
                
                success = self.order_manager.modify_order(
                    ticket, stop_loss_price=target_sl_price
                )
                
                modify_end = time.time()
                modify_end_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                modify_latency = (modify_end - modify_start) * 1000
                logger.debug(f"[{modify_end_timestamp}] 📤 MT5 order_send returned | Success: {success} | Latency: {modify_latency:.1f}ms")
                
                if success:
                    # CRITICAL: Check if this is first eligible update - minimize verification delay
                    with self._tracking_lock:
                        first_eligible_info = self._first_eligible_update.get(ticket, {})
                        is_first_eligible = (first_eligible_info.get('state', 'NONE') == 'PENDING')
                    
                    # Sleep before verification (configurable backoff)
                    # FIX: Use longer delay for profitable trades to ensure MT5 has processed
                    # BUT: For first eligible updates, use minimal delay (never block application)
                    verify_delay = verification_delay
                    if is_first_eligible:
                        # First eligible: minimal delay (50ms) - never block application
                        verify_delay = 0.05  # 50ms minimum for MT5 to process
                        logger.info(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                                  f"Using minimal verification delay {verify_delay*1000:.0f}ms (never blocking)")
                    elif target_profit_usd > 0:  # Profitable trade
                        verify_delay = max(verification_delay, 0.2)  # At least 200ms for profitable trades
                    verify_sleep_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    logger.debug(f"[{verify_sleep_timestamp}] ⏳ Sleeping {verify_delay*1000:.0f}ms before verification")
                    time.sleep(verify_delay)
                    
                    # Verify SL was applied by getting fresh position
                    verify_start = time.time()
                    verify_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    logger.debug(f"[{verify_timestamp}] 🔍 Verifying SL update | Ticket: {ticket}")
                    
                    fresh_position = self.order_manager.get_position_by_ticket(ticket)
                    if fresh_position:
                        applied_sl = fresh_position.get('sl', 0.0)
                        sl_diff = abs(applied_sl - target_sl_price)
                        
                        # Get symbol info for point precision
                        symbol_info = self.mt5_connector.get_symbol_info(symbol)
                        point = symbol_info.get('point', 0.00001) if symbol_info else 0.00001
                        
                        # CRITICAL FIX: Use appropriate tolerance based on symbol type (configurable)
                        # For forex: point * 10 (1 pip)
                        # For indices/crypto: max(1.0, point * 100) but never exceed reasonable bounds
                        if point >= 0.01:  # Index/Crypto
                            base_tolerance = max(point * 100, 1.0)  # At least 1.0 point for indices
                        else:  # Forex
                            base_tolerance = point * 10  # 1 pip for forex
                        
                        # Apply symbol-specific tolerance multiplier if available
                        symbol_tolerance_multiplier = self.verification_price_tolerance_multiplier
                        if symbol in self._symbol_overrides:
                            override_multiplier = self._symbol_overrides[symbol].get('verification_tolerance_multiplier')
                            if override_multiplier is not None:
                                symbol_tolerance_multiplier = override_multiplier
                        
                        tolerance = base_tolerance * symbol_tolerance_multiplier
                        
                        verify_end_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        logger.debug(f"[{verify_end_timestamp}] [OK] Verification complete | Applied SL: {applied_sl:.5f} | Diff: {sl_diff:.5f} | Tolerance: {tolerance:.5f} (took {(time.time() - verify_start)*1000:.1f}ms)")
                        
                        if sl_diff < tolerance:
                            # Verify effective SL profit is within tolerance
                            effective_sl_profit = self.get_effective_sl_profit(fresh_position)
                            effective_error = abs(effective_sl_profit - target_profit_usd)
                            
                            # Tolerance for effective SL profit (configurable)
                            # RELAXED TOLERANCE FOR PROFITABLE TRADES: Allow looser tolerance when moving SL in profit direction
                            base_effective_tolerance = self.verification_effective_profit_tolerance_usd
                            
                            # Check if this is a profitable trade and SL is moving in profit direction
                            is_profitable_trade = target_profit_usd > 0  # Positive target profit means locking in profit
                            is_moving_towards_profit = effective_sl_profit > -self.max_risk_usd  # Better than max loss
                            
                            if is_profitable_trade and is_moving_towards_profit:
                                # FIX: Relax tolerance by 100% for profitable trades (allows for contract size calculation differences)
                                effective_tolerance = base_effective_tolerance * 2.0  # Double tolerance for profitable trades
                                logger.debug(f"🔓 RELAXED TOLERANCE: {symbol} Ticket {ticket} | "
                                           f"Profitable trade detected | Base: ${base_effective_tolerance:.2f} | "
                                           f"Relaxed: ${effective_tolerance:.2f}")
                            else:
                                effective_tolerance = base_effective_tolerance
                            
                            if effective_error < effective_tolerance:
                                # Update tracking and set cooldown
                                with self._tracking_lock:
                                    self._last_sl_update[ticket] = datetime.now()
                                    self._last_sl_price[ticket] = applied_sl
                                    self._last_sl_reason[ticket] = reason
                                    
                                    # Mark first eligible update as applied
                                    if ticket in self._first_eligible_update:
                                        self._first_eligible_update[ticket]['state'] = 'APPLIED'
                                        self._first_eligible_update[ticket]['applied_time'] = time.time()
                                        logger.info(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                                                  f"First eligible SL update VERIFIED successfully | "
                                                  f"Time from first seen: {(time.time() - self._first_eligible_update[ticket].get('first_seen_time', time.time()))*1000:.1f}ms")
                                    
                                    # Set cooldown to prevent oscillation - BUT skip for first eligible (already applied)
                                    if ticket not in self._first_eligible_update or self._first_eligible_update[ticket].get('state') != 'PENDING':
                                        self._sl_update_cooldown[ticket] = current_time_float + self._sl_update_cooldown_seconds
                                
                                success_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                                logger.info(f"[OK] SL UPDATE SUCCESS: Ticket={ticket} Symbol={symbol} NewSL={applied_sl:.5f} TargetSL={target_sl_price:.5f} Reason={reason}")
                                logger.info(f"[{success_timestamp}] [OK] SL APPLIED: {symbol} Ticket {ticket} | "
                                          f"Entry: {entry_price:.5f} | Target: {target_sl_price:.5f} | Applied: {applied_sl:.5f} | "
                                          f"Effective profit: ${effective_sl_profit:.2f} (target: ${target_profit_usd:.2f}, error: ${effective_error:.2f}) | "
                                          f"Reason: {reason} | Attempt: {attempt + 1}/{max_retries}")
                                
                                # Structured logging
                                backoff_ms = (retry_delay * (2 ** attempt) * 1000) if self.use_exponential_backoff and attempt > 0 else 0
                                self._log_structured_update(
                                    ticket=ticket, symbol=symbol, entry_price=entry_price,
                                    target_sl=target_sl_price, applied_sl=applied_sl,
                                    attempt_number=attempt + 1, retry_backoff_ms=backoff_ms,
                                    applied_sl_reason=reason, broker_error_code=None,
                                    effective_profit_target=target_profit_usd,
                                    effective_profit_applied=effective_sl_profit,
                                    success=True
                                )
                                
                                # Track update for watchdog
                                if hasattr(self, '_watchdog') and self._watchdog:
                                    self._watchdog.track_sl_update(ticket)
                                
                                # Trace successful SL update
                                tracer.trace(
                                    function_name="SLManager._apply_sl_update",
                                    expected=f"Apply SL update for {symbol} Ticket {ticket}",
                                    actual=f"SL update applied and verified successfully",
                                    status="OK",
                                    ticket=ticket,
                                    symbol=symbol,
                                    target_sl=target_sl_price,
                                    applied_sl=applied_sl,
                                    effective_profit=effective_sl_profit,
                                    target_profit=target_profit_usd,
                                    attempt=attempt + 1,
                                    reason=reason
                                )
                                
                                return True
                            else:
                                logger.warning(f"[WARNING] SL EFFECTIVE MISMATCH: {symbol} Ticket {ticket} | "
                                             f"Target profit: ${target_profit_usd:.2f} | Effective: ${effective_sl_profit:.2f} | "
                                             f"Error: ${effective_error:.2f} (tolerance: ${effective_tolerance:.2f})")
                                tracer.trace(
                                    function_name="SLManager._apply_sl_update",
                                    expected=f"Apply SL update for {symbol} Ticket {ticket}",
                                    actual=f"SL effective profit mismatch (target: ${target_profit_usd:.2f}, applied: ${effective_sl_profit:.2f}, error: ${effective_error:.2f})",
                                    status="WARNING",
                                    ticket=ticket,
                                    symbol=symbol,
                                    target_sl=target_sl_price,
                                    applied_sl=applied_sl,
                                    effective_profit=effective_sl_profit,
                                    target_profit=target_profit_usd,
                                    error=effective_error,
                                    tolerance=effective_tolerance,
                                    attempt=attempt + 1,
                                    reason="Effective profit mismatch"
                                )
                        else:
                            logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                                      f"BLOCKED=SL_PRICE_MISMATCH | Attempt={attempt+1}/{max_retries} | "
                                      f"TargetSL={target_sl_price:.5f} AppliedSL={applied_sl:.5f} | "
                                      f"Diff={sl_diff:.5f} Tolerance={tolerance:.5f} | Reason={reason}")
                            logger.warning(f"[WARNING] SL MISMATCH: {symbol} Ticket {ticket} | "
                                         f"Target: {target_sl_price:.5f} | Applied: {applied_sl:.5f} | "
                                         f"Difference: {sl_diff:.5f} (tolerance: {tolerance:.5f})")
                            tracer.trace(
                                function_name="SLManager._apply_sl_update",
                                expected=f"Apply SL update for {symbol} Ticket {ticket}",
                                actual=f"SL price mismatch (target: {target_sl_price:.5f}, applied: {applied_sl:.5f}, diff: {sl_diff:.5f})",
                                status="WARNING",
                                ticket=ticket,
                                symbol=symbol,
                                target_sl=target_sl_price,
                                applied_sl=applied_sl,
                                difference=sl_diff,
                                tolerance=tolerance,
                                attempt=attempt + 1,
                                reason="SL price mismatch"
                            )
                    else:
                        logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                                  f"BLOCKED=POSITION_VERIFICATION_FAILED | Attempt={attempt+1}/{max_retries} | "
                                  f"TargetSL={target_sl_price:.5f} | Reason={reason}")
                        logger.warning(f"[WARNING] Cannot verify position {ticket} after SL update")
                        tracer.trace(
                            function_name="SLManager._apply_sl_update",
                            expected=f"Apply SL update for {symbol} Ticket {ticket}",
                            actual="Cannot verify position after SL update",
                            status="WARNING",
                            ticket=ticket,
                            symbol=symbol,
                            attempt=attempt + 1,
                            reason="Position verification failed"
                        )
                    
                    # If we get here, SL might have been applied but verification failed
                    # CRITICAL: For first eligible updates, never block - only log failure and continue
                    if is_first_eligible and is_first_attempt:
                        logger.warning(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                                     f"Verification failed on first attempt - SL may have been applied | "
                                     f"Logging failure but NOT blocking (first eligible update)")
                        # Don't retry immediately for first eligible - allow next cycle to retry
                        return False  # Return False but don't block - next cycle will retry
                    
                    # Log and retry with exponential backoff (only for non-first-eligible or subsequent attempts)
                    if attempt < max_retries - 1:
                        if self.use_exponential_backoff:
                            backoff_delay = retry_delay * (2 ** attempt)  # Exponential: 100ms, 200ms, 400ms
                        else:
                            backoff_delay = retry_delay  # Fixed delay
                        retry_sleep_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        logger.debug(f"[{retry_sleep_timestamp}] ⏳ Retrying in {backoff_delay*1000:.0f}ms (attempt {attempt + 1}/{max_retries}, exponential: {self.use_exponential_backoff})")
                        time.sleep(backoff_delay)
                        continue
                    else:
                        # All retries exhausted for verification failure
                        failure_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                                  f"FAILED=VERIFICATION_FAILED_ALL_RETRIES | Attempts={max_retries} | "
                                  f"TargetSL={target_sl_price:.5f} | Reason={reason}")
                        logger.error(f"[{failure_timestamp}] SL UPDATE FAILED: {symbol} Ticket {ticket} | "
                                   f"Target: {target_sl_price:.5f} | Reason: Verification failed | "
                                   f"Failed after {max_retries} attempts")
                        # Log system event
                        system_event_logger.systemEvent("SL_UPDATE_FAILED", {
                            "ticket": ticket,
                            "symbol": symbol,
                            "error": "Verification failed after all retries",
                            "targetSL": target_sl_price
                        })
                        tracer.trace(
                            function_name="SLManager._apply_sl_update",
                            expected=f"Apply SL update for {symbol} Ticket {ticket}",
                            actual=f"SL update FAILED after {max_retries} attempts - verification failed",
                            status="FAILED",
                            ticket=ticket,
                            symbol=symbol,
                            target_sl=target_sl_price,
                            attempts=max_retries,
                            reason="Verification failed after all retries"
                        )
                else:
                    # modify_order returned False
                    tracer.trace(
                        function_name="SLManager._apply_sl_update",
                        expected=f"Apply SL update for {symbol} Ticket {ticket}",
                        actual=f"modify_order returned False (attempt {attempt + 1}/{max_retries})",
                        status="WARNING",
                        ticket=ticket,
                        symbol=symbol,
                        target_sl=target_sl_price,
                        attempt=attempt + 1,
                        reason="modify_order returned False"
                    )
                    logger.warning(f"[WARNING] modify_order returned False for Ticket {ticket} | Attempt {attempt + 1}/{max_retries}")
                    
                    # CRITICAL: For first eligible updates, never block - only log failure
                    if is_first_eligible and is_first_attempt:
                        logger.warning(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                                     f"modify_order failed on first attempt - logging failure but NOT blocking")
                        return False  # Return False but don't block - next cycle will retry
                    
                    if attempt < max_retries - 1:
                        if self.use_exponential_backoff:
                            backoff_delay = retry_delay * (2 ** attempt)  # Exponential: 100ms, 200ms, 400ms
                        else:
                            backoff_delay = retry_delay  # Fixed delay
                        retry_sleep_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        logger.debug(f"[{retry_sleep_timestamp}] ⏳ Retrying in {backoff_delay*1000:.0f}ms (attempt {attempt + 1}/{max_retries})")
                        time.sleep(backoff_delay)
                        continue
                    else:
                        # All retries exhausted for modify_order failure
                        failure_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                                  f"FAILED=MODIFY_ORDER_FAILED_ALL_RETRIES | Attempts={max_retries} | "
                                  f"TargetSL={target_sl_price:.5f} | Reason={reason}")
                        logger.error(f"SL UPDATE FAILED: Ticket={ticket} Symbol={symbol} TargetSL={target_sl_price:.5f} Reason=modify_order returned False Attempts={max_retries}")
                        logger.error(f"[{failure_timestamp}] SL UPDATE FAILED: {symbol} Ticket {ticket} | "
                                   f"Target: {target_sl_price:.5f} | Reason: modify_order returned False | "
                                   f"Failed after {max_retries} attempts")
                        # Log system event
                        system_event_logger.systemEvent("SL_UPDATE_FAILED", {
                            "ticket": ticket,
                            "symbol": symbol,
                            "error": "modify_order returned False after all retries",
                            "targetSL": target_sl_price
                        })
            
            except Exception as e:
                exception_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                logger.error(f"[{exception_timestamp}] Exception applying SL update for {symbol} Ticket {ticket}: {e}", exc_info=True)
                if attempt < max_retries - 1:
                    if self.use_exponential_backoff:
                        backoff_delay = retry_delay * (2 ** attempt)  # Exponential: 100ms, 200ms, 400ms
                    else:
                        backoff_delay = retry_delay  # Fixed delay
                    retry_sleep_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    logger.debug(f"[{retry_sleep_timestamp}] ⏳ Retrying after exception in {backoff_delay*1000:.0f}ms")
                    time.sleep(backoff_delay)
        
        # EMERGENCY STRICT SL ENFORCEMENT: Expanded triggers
        # Trigger emergency fallback if:
        # 1. Trade is losing (current_profit < 0) OR
        # 2. Update has failed verification 2 consecutive cycles OR
        # 3. Applied SL is worse than strict loss limit (effective_sl < -max_risk)
        # NOTE: We directly modify order here (no retries) to avoid recursion with _enforce_strict_loss_limit
        should_trigger_emergency = False
        emergency_reason = ""
        
        if position is not None:
            current_profit = position.get('profit', 0.0)
            consecutive_failures = self._consecutive_failures.get(ticket, 0)
            
            # Check if applied SL is worse than strict loss limit
            current_sl_price = position.get('sl', 0.0)
            effective_sl_profit = None
            if current_sl_price > 0:
                try:
                    effective_sl_profit = self.get_effective_sl_profit(position)
                except:
                    pass
            
            # Trigger condition 1: Losing trade
            if current_profit < 0:
                should_trigger_emergency = True
                emergency_reason = f"Losing trade (P/L: ${current_profit:.2f})"
            
            # Trigger condition 2: Failed verification 2+ consecutive cycles
            elif consecutive_failures >= 2:
                should_trigger_emergency = True
                emergency_reason = f"Failed verification {consecutive_failures} consecutive cycles"
            
            # Trigger condition 3: Applied SL worse than strict loss limit
            elif effective_sl_profit is not None and effective_sl_profit < -self.max_risk_usd:
                should_trigger_emergency = True
                emergency_reason = f"Applied SL (${effective_sl_profit:.2f}) worse than strict limit (-${self.max_risk_usd:.2f})"
            
            if should_trigger_emergency:
                logger.critical(f"🚨 EMERGENCY STRICT SL ENFORCEMENT: {symbol} Ticket {ticket} | "
                              f"Reason: {emergency_reason} | "
                              f"Normal SL update failed after {max_retries} attempts | "
                              f"Current P/L: ${current_profit:.2f} | "
                              f"Enforcing emergency strict SL (-${self.max_risk_usd:.2f})")
                
                # CRITICAL FIX: Calculate emergency strict SL with correct entry price
                # For SELL: entry should be BID (what we received)
                # For BUY: entry should be ASK (what we paid)
                entry_price = position.get('price_open', 0.0)
                order_type = position.get('type', '')
                lot_size = position.get('volume', 0.01)
                
                if entry_price > 0 and lot_size > 0:
                    # Get symbol info for emergency SL calculation
                    symbol_info = self.mt5_connector.get_symbol_info(symbol)
                    if symbol_info:
                        # Get current market prices to verify/correct entry price
                        tick = self.mt5_connector.get_symbol_info_tick(symbol)
                        if tick:
                            current_bid = tick.bid
                            current_ask = tick.ask
                            
                            # CRITICAL: For SELL, if entry_price is close to ASK, it's likely wrong
                            # MT5 sometimes gives ASK as price_open for SELL, but we need BID
                            if order_type == 'SELL':
                                # If entry is closer to ASK than BID, use BID as effective entry
                                if abs(entry_price - current_ask) < abs(entry_price - current_bid):
                                    effective_entry = current_bid
                                    logger.debug(f"🔧 EMERGENCY SL: Corrected SELL entry from {entry_price:.5f} to {effective_entry:.5f} (BID)")
                                else:
                                    effective_entry = entry_price
                            else:  # BUY
                                # If entry is closer to BID than ASK, use ASK as effective entry
                                if abs(entry_price - current_bid) < abs(entry_price - current_ask):
                                    effective_entry = current_ask
                                    logger.debug(f"🔧 EMERGENCY SL: Corrected BUY entry from {entry_price:.5f} to {effective_entry:.5f} (ASK)")
                                else:
                                    effective_entry = entry_price
                            
                            # CRITICAL FIX: For crypto symbols, reverse-engineer contract_size from current profit
                            # This ensures emergency SL results in exactly -$2.00
                            effective_contract_size = None
                            # Get current price from position or use bid/ask based on order type
                            current_price = position.get('price_current', 0.0)
                            if current_price <= 0:
                                # Fallback to bid/ask if price_current not available
                                if order_type == 'BUY':
                                    current_price = current_bid
                                else:  # SELL
                                    current_price = current_ask
                            
                            if current_profit is not None and current_price > 0:
                                # Calculate what contract_size would give us the current profit
                                if order_type == 'BUY':
                                    current_price_diff = current_price - effective_entry
                                else:  # SELL
                                    current_price_diff = effective_entry - current_price
                                
                                if abs(current_price_diff) > 0.00001 and lot_size > 0:
                                    # Reverse-engineer: current_profit = current_price_diff * lot_size * effective_contract_size
                                    effective_contract_size = abs(current_profit) / (abs(current_price_diff) * lot_size)
                                    
                                    if 0.1 <= effective_contract_size <= 1000000:
                                        logger.info(f"🔧 EMERGENCY SL: Reverse-engineered contract_size: {effective_contract_size:.2f} | "
                                                   f"From current profit: ${current_profit:.2f}")
                            
                            # Calculate emergency strict SL price with corrected entry
                            # CRITICAL FIX: Iteratively find the best SL that respects broker constraints AND gives us -$2.00
                            try:
                                # Step 1: Calculate initial target SL for -$2.00
                                initial_target_sl = self._calculate_target_sl_price(
                                    effective_entry, -self.max_risk_usd, order_type, lot_size, symbol_info, position=position
                                )
                                
                                # Step 2: Adjust for broker constraints
                                current_sl = position.get('sl', 0.0)
                                emergency_sl = self._adjust_sl_for_broker_constraints(
                                    initial_target_sl, current_sl, order_type, symbol_info, current_bid, current_ask, entry_price=effective_entry
                                )
                                
                                # Step 3: If broker adjusted the SL, verify effective SL and try to get closer to -$2.00
                                if abs(emergency_sl - initial_target_sl) > (symbol_info.get('point', 0.00001) * 10):
                                    # Broker adjusted the SL significantly - verify what effective SL this gives us
                                    # Create a mock position to calculate effective SL
                                    mock_position = position.copy()
                                    mock_position['sl'] = emergency_sl
                                    mock_effective_sl = self.get_effective_sl_profit(mock_position)
                                    
                                    target_loss = -self.max_risk_usd
                                    effective_error = abs(mock_effective_sl - target_loss)
                                    
                                    # If effective SL is too far from target (>$0.30 error), try to adjust
                                    if effective_error > 0.30:
                                        logger.warning(f"[WARNING] EMERGENCY SL ADJUSTMENT NEEDED: {symbol} Ticket {ticket} | "
                                                     f"Broker-adjusted SL {emergency_sl:.5f} gives effective ${mock_effective_sl:.2f} "
                                                     f"(target: ${target_loss:.2f}, error: ${effective_error:.2f}) | "
                                                     f"Attempting to find better SL...")
                                        
                                        # Try to find a better SL by adjusting in small increments
                                        point = symbol_info.get('point', 0.00001)
                                        best_sl = emergency_sl
                                        best_effective_sl = mock_effective_sl
                                        best_error = effective_error
                                        
                                        # Try adjusting SL in both directions (within broker constraints)
                                        for adjustment_factor in [-10, -5, -2, -1, 1, 2, 5, 10]:
                                            test_sl = emergency_sl + (adjustment_factor * point)
                                            
                                            # Re-adjust for broker constraints
                                            test_sl = self._adjust_sl_for_broker_constraints(
                                                test_sl, current_sl, order_type, symbol_info, current_bid, current_ask, entry_price=effective_entry
                                            )
                                            
                                            # Calculate effective SL for this test SL
                                            test_mock_position = position.copy()
                                            test_mock_position['sl'] = test_sl
                                            test_effective_sl = self.get_effective_sl_profit(test_mock_position)
                                            test_error = abs(test_effective_sl - target_loss)
                                            
                                            # If this is better (closer to -$2.00), use it
                                            if test_error < best_error:
                                                best_sl = test_sl
                                                best_effective_sl = test_effective_sl
                                                best_error = test_error
                                        
                                        if best_error < effective_error:
                                            emergency_sl = best_sl
                                            logger.info(f"[OK] EMERGENCY SL OPTIMIZED: {symbol} Ticket {ticket} | "
                                                       f"Optimized SL: {emergency_sl:.5f} | "
                                                       f"Effective SL: ${best_effective_sl:.2f} (error: ${best_error:.2f}, improved from ${effective_error:.2f})")
                                
                                logger.info(f"🚨 EMERGENCY SL CALCULATED: {symbol} Ticket {ticket} | "
                                          f"Entry: {effective_entry:.5f} | Target SL: {emergency_sl:.5f} | "
                                          f"Order Type: {order_type}")
                            except Exception as calc_error:
                                logger.error(f"[ERROR] EMERGENCY SL CALCULATION FAILED: {symbol} Ticket {ticket} | "
                                            f"Error: {calc_error}", exc_info=True)
                                # Fallback: use a safe SL (1% from entry)
                                if order_type == 'BUY':
                                    emergency_sl = effective_entry * 0.99
                                else:  # SELL
                                    emergency_sl = effective_entry * 1.01
                                logger.warning(f"[WARNING] EMERGENCY SL: Using fallback SL {emergency_sl:.5f}")
                            
                            # CRITICAL FIX: Direct emergency SL update with proper price format
                            # Use stop_loss_price parameter to ensure absolute price is used
                            # Apply-and-Verify flow: modify -> sleep -> verify -> confirm
                            try:
                                # Step 1: Apply SL modification
                                emergency_success = self.order_manager.modify_order(
                                    ticket, stop_loss_price=emergency_sl
                                )
                                
                                if emergency_success:
                                    # Step 2: Sleep for verification delay
                                    time.sleep(self.sl_update_verification_delay)
                                    
                                    # Step 3: Verify by fetching fresh position
                                    verify_position = self.order_manager.get_position_by_ticket(ticket)
                                    if verify_position:
                                        applied_sl = verify_position.get('sl', 0.0)
                                        point = symbol_info.get('point', 0.00001)
                                        sl_diff = abs(applied_sl - emergency_sl)
                                        
                                        # CRITICAL FIX: Use appropriate tolerance based on symbol type
                                        # For forex: $0.50 tolerance
                                        # For indices/crypto: max(1.0 USD, point * 100) but never exceed $1.00 more than target
                                        if point >= 0.01:  # Index/Crypto
                                            tolerance_price = max(point * 100, 1.0)  # At least 1.0 point for indices
                                        else:  # Forex
                                            tolerance_price = point * 10  # 1 pip for forex
                                        
                                        if sl_diff < tolerance_price:
                                            # Step 4: Verify effective SL is within tolerance
                                            effective_sl_profit = self.get_effective_sl_profit(verify_position)
                                            
                                            # Check if effective SL is within acceptable range
                                            # For forex: $0.50 tolerance
                                            # For indices/crypto: max(1.0 USD, point * 100) but never exceed $1.00 more than target
                                            target_loss = -self.max_risk_usd
                                            sl_error = abs(effective_sl_profit - target_loss)
                                            
                                            if point >= 0.01:  # Index/Crypto
                                                effective_tolerance = min(1.0, max(0.5, point * 100 / 100))  # Max $1.00, prefer $0.50
                                            else:  # Forex
                                                effective_tolerance = 0.50  # $0.50 for forex
                                            
                                            if sl_error < effective_tolerance:
                                                logger.info(f"[OK] EMERGENCY STRICT SL APPLIED: {symbol} Ticket {ticket} | "
                                                          f"Entry: {effective_entry:.5f} | Target SL: {emergency_sl:.5f} | Applied: {applied_sl:.5f} | "
                                                          f"Effective profit: ${effective_sl_profit:.2f} (target: ${target_loss:.2f}, error: ${sl_error:.2f})")
                                                
                                                # Update tracking
                                                with self._tracking_lock:
                                                    self._last_sl_update[ticket] = datetime.now()
                                                    self._last_sl_price[ticket] = applied_sl
                                                    self._last_sl_reason[ticket] = f"Emergency strict SL enforcement (-${self.max_risk_usd:.2f})"
                                                
                                                return True
                                            else:
                                                # CRITICAL FIX: If effective SL is still too far, try one more iteration
                                                # This can happen if broker applies additional constraints after our adjustment
                                                logger.warning(f"[WARNING] EMERGENCY SL EFFECTIVE MISMATCH: {symbol} Ticket {ticket} | "
                                                             f"Target: ${target_loss:.2f} | Effective: ${effective_sl_profit:.2f} | "
                                                             f"Error: ${sl_error:.2f} (tolerance: ${effective_tolerance:.2f}) | "
                                                             f"Attempting one more adjustment...")
                                                
                                                # Try to adjust the applied SL to get closer to target
                                                # Calculate how much we need to adjust
                                                profit_diff = effective_sl_profit - target_loss  # Negative if we need to move SL further
                                                
                                                # Estimate how much to adjust SL price to get closer to target
                                                # This is approximate - we'll verify after
                                                point = symbol_info.get('point', 0.00001)
                                                point_value = symbol_info.get('trade_tick_value', None)
                                                
                                                if point_value and point_value > 0:
                                                    # For indices/crypto: estimate adjustment needed
                                                    # profit_diff = price_diff_points * lot_size * point_value
                                                    # price_diff_points = profit_diff / (lot_size * point_value)
                                                    estimated_points_adjustment = profit_diff / (lot_size * point_value)
                                                    estimated_price_adjustment = estimated_points_adjustment * point
                                                    
                                                    # Adjust SL (for BUY: if profit_diff is negative, we need to move SL down)
                                                    if order_type == 'BUY':
                                                        retry_sl = applied_sl - abs(estimated_price_adjustment)
                                                    else:  # SELL
                                                        retry_sl = applied_sl + abs(estimated_price_adjustment)
                                                    
                                                    # Re-adjust for broker constraints
                                                    retry_sl = self._adjust_sl_for_broker_constraints(
                                                        retry_sl, applied_sl, order_type, symbol_info, current_bid, current_ask, entry_price=effective_entry
                                                    )
                                                    
                                                    # Try one more time if the adjustment is significant
                                                    if abs(retry_sl - applied_sl) > (point * 5):
                                                        logger.info(f"🔄 EMERGENCY SL RETRY: {symbol} Ticket {ticket} | "
                                                                   f"Retrying with adjusted SL: {retry_sl:.5f} (was {applied_sl:.5f})")
                                                        
                                                        retry_success = self.order_manager.modify_order(
                                                            ticket, stop_loss_price=retry_sl
                                                        )
                                                        
                                                        if retry_success:
                                                            time.sleep(self.sl_update_verification_delay)
                                                            retry_position = self.order_manager.get_position_by_ticket(ticket)
                                                            if retry_position:
                                                                retry_applied_sl = retry_position.get('sl', 0.0)
                                                                retry_effective_sl = self.get_effective_sl_profit(retry_position)
                                                                retry_error = abs(retry_effective_sl - target_loss)
                                                                
                                                                if retry_error < sl_error:  # Better than before
                                                                    logger.info(f"[OK] EMERGENCY SL RETRY SUCCESS: {symbol} Ticket {ticket} | "
                                                                               f"Applied SL: {retry_applied_sl:.5f} | "
                                                                               f"Effective SL: ${retry_effective_sl:.2f} (error: ${retry_error:.2f}, improved from ${sl_error:.2f})")
                                                                    
                                                                    # Update tracking
                                                                    with self._tracking_lock:
                                                                        self._last_sl_update[ticket] = datetime.now()
                                                                        self._last_sl_price[ticket] = retry_applied_sl
                                                                        self._last_sl_reason[ticket] = f"Emergency strict SL enforcement (-${self.max_risk_usd:.2f}) - retry"
                                                                    
                                                                    return True
                                                
                                                # If retry didn't work or wasn't attempted, mark for manual review
                                                logger.warning(f"[WARNING] EMERGENCY SL FINAL MISMATCH: {symbol} Ticket {ticket} | "
                                                             f"Target: ${target_loss:.2f} | Effective: ${effective_sl_profit:.2f} | "
                                                             f"Error: ${sl_error:.2f} (tolerance: ${effective_tolerance:.2f}) | "
                                                             f"Broker constraints may prevent exact -$2.00 SL")
                                                # Mark for manual review and add circuit breaker
                                                self._manual_review_tickets.add(ticket)
                                                disabled_until = time.time() + self._circuit_breaker_cooldown  # Use configurable cooldown
                                                self._ticket_circuit_breaker[ticket] = disabled_until
                                                logger.critical(f"🚨 MANUAL REVIEW REQUIRED: {symbol} Ticket {ticket} | "
                                                              f"Emergency SL effective mismatch exceeds tolerance | "
                                                              f"Circuit breaker: disabled for 60s")
                                        else:
                                            logger.warning(f"[WARNING] EMERGENCY SL MISMATCH: {symbol} Ticket {ticket} | "
                                                         f"Target: {emergency_sl:.5f} | Applied: {applied_sl:.5f} | "
                                                         f"Diff: {sl_diff:.5f} (tolerance: {tolerance_price:.5f})")
                                            
                                            # Log broker rejection reason if available
                                            logger.warning(f"   Broker may have rejected SL due to constraints (stops_level, spread, etc.)")
                                            # Mark for manual review and add circuit breaker
                                            self._manual_review_tickets.add(ticket)
                                            disabled_until = time.time() + self._circuit_breaker_cooldown  # Use configurable cooldown
                                            self._ticket_circuit_breaker[ticket] = disabled_until
                                            logger.critical(f"🚨 MANUAL REVIEW REQUIRED: {symbol} Ticket {ticket} | "
                                                          f"Emergency SL price mismatch exceeds tolerance | "
                                                          f"Circuit breaker: disabled for 60s")
                                    else:
                                        logger.warning(f"[WARNING] EMERGENCY SL: Could not verify position {ticket}")
                                        # Mark for manual review and add circuit breaker
                                        self._manual_review_tickets.add(ticket)
                                        disabled_until = time.time() + 60.0  # 60 second cooldown
                                        self._ticket_circuit_breaker[ticket] = disabled_until
                                        logger.critical(f"🚨 MANUAL REVIEW REQUIRED: {symbol} Ticket {ticket} | "
                                                      f"Could not verify emergency SL position | "
                                                      f"Circuit breaker: disabled for 60s")
                                else:
                                    logger.error(f"EMERGENCY STRICT SL FAILED: {symbol} Ticket {ticket} | "
                                               f"modify_order returned False | "
                                               f"Trade may be at risk - manual intervention required")
                                    # Mark for manual review and add circuit breaker
                                    self._manual_review_tickets.add(ticket)
                                    disabled_until = time.time() + 60.0  # 60 second cooldown
                                    self._ticket_circuit_breaker[ticket] = disabled_until
                                    logger.critical(f"🚨 MANUAL REVIEW REQUIRED: {symbol} Ticket {ticket} | "
                                                  f"Emergency SL modification failed | "
                                                  f"Circuit breaker: disabled for 60s")
                            except Exception as e:
                                logger.error(f"[ERROR] EMERGENCY STRICT SL EXCEPTION: {symbol} Ticket {ticket} | "
                                           f"Error: {e} | "
                                           f"Trade may be at risk - manual intervention required", exc_info=True)
                                # Mark for manual review and add circuit breaker
                                self._manual_review_tickets.add(ticket)
                                disabled_until = time.time() + 60.0  # 60 second cooldown
                                self._ticket_circuit_breaker[ticket] = disabled_until
                                logger.critical(f"🚨 MANUAL REVIEW REQUIRED: {symbol} Ticket {ticket} | "
                                              f"Emergency SL exception: {e} | "
                                              f"Circuit breaker: disabled for 60s")
                        else:
                            logger.error(f"[ERROR] EMERGENCY STRICT SL: Cannot get market prices for {symbol}")
                    else:
                        logger.error(f"[ERROR] EMERGENCY STRICT SL: Cannot get symbol info for {symbol}")
                else:
                    logger.error(f"[ERROR] EMERGENCY STRICT SL: Invalid position data (entry_price={entry_price}, lot_size={lot_size})")
        
        return False
    
    def compute_authoritative_sl(self, position: Dict[str, Any]) -> Dict[str, Any]:
        """
        SINGLE SL AUTHORITY - Computes the authoritative SL with strict priority.
        
        Priority (STRICT - NEVER VIOLATED):
            TRAILING_SL > PROFIT_LOCK_SL > HARD_SL
        
        This function is LOCK-FREE - it only calculates, never modifies.
        Lock is acquired ONLY for the final MT5 modification call.
        
        Emergency logic may ONLY apply if it does NOT worsen SL.
        
        Args:
            position: Position dictionary (must be fresh from order_manager)
        
        Returns:
            Dict with keys:
                - target_sl_price: float or None (authoritative SL price)
                - target_profit_usd: float (target profit/loss in USD)
                - authority_source: str ("TRAILING", "PROFIT_LOCK", "HARD", or None)
                - reason: str (reason for SL update)
                - state: str (explicit state: SWEET_SPOT, TRAILING_ACTIVE, PROFIT_LOCKED, MANAGING, etc.)
                - is_trailing: bool
                - is_profit_lock: bool
                - violations: list of violation strings if any
        """
        symbol = position.get('symbol', '')
        ticket = position.get('ticket', 0)
        current_profit = position.get('profit', 0.0)
        current_sl = position.get('sl', 0.0)
        entry_price = position.get('price_open', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        
        result = {
            'target_sl_price': None,
            'target_profit_usd': 0.0,
            'authority_source': None,
            'reason': None,
            'state': 'MANAGING',
            'is_trailing': False,
            'is_profit_lock': False,
            'violations': []
        }
        
        # Get symbol info (lock-free - no locks needed for calculation)
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            result['reason'] = "Cannot get symbol info"
            return result
        
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick is None:
            result['reason'] = "Cannot get market prices"
            return result
        
        # STEP 1: Calculate TRAILING_SL (highest priority)
        trailing_result = None
        if current_profit > self.trailing_increment_usd:  # Profit > $0.10
            try:
                # Calculate trailing stop
                profit_to_lock = current_profit - self.trailing_increment_usd
                profit_to_lock = (profit_to_lock // self.trailing_increment_usd) * self.trailing_increment_usd
                profit_to_lock = max(profit_to_lock, self.trailing_increment_usd)
                
                trailing_sl = self._calculate_target_sl_price(
                    entry_price, profit_to_lock, order_type, lot_size, symbol_info, position=position
                )
                
                if trailing_sl:
                    # Adjust for broker constraints
                    trailing_sl = self._adjust_sl_for_broker_constraints(
                        trailing_sl, current_sl, order_type, symbol_info, tick.bid, tick.ask, entry_price=entry_price
                    )
                    
                    if trailing_sl:
                        trailing_result = {
                            'target_sl_price': trailing_sl,
                            'target_profit_usd': profit_to_lock,
                            'authority_source': 'TRAILING',
                            'reason': f"Trailing stop (profit: ${current_profit:.2f}, locking: ${profit_to_lock:.2f})",
                            'state': 'TRAILING_ACTIVE',
                            'is_trailing': True
                        }
            except Exception as e:
                logger.error(f"[ERROR] Trailing SL calculation failed for {symbol} Ticket {ticket}: {e}", exc_info=True)
        
        # STEP 2: Calculate PROFIT_LOCK_SL (second priority - only if no trailing)
        # CRITICAL FIX: Try ProfitLockingEngine first (more sophisticated sweet-spot logic)
        profit_lock_result = None
        if not trailing_result and self.sweet_spot_min <= current_profit <= self.sweet_spot_max:
            # CRITICAL FIX: Try ProfitLockingEngine first (before internal calculation)
            # This ensures profit locking engine is called in authoritative path
            profit_locking_engine = None
            if hasattr(self, '_risk_manager') and self._risk_manager:
                profit_locking_engine = getattr(self._risk_manager, '_profit_locking_engine', None)
            
            if profit_locking_engine:
                try:
                    # MANDATORY LOGGING: Log profit locking attempt in authoritative path
                    logger.info(f"[LOCK_ATTEMPT] Authoritative Path | Ticket={ticket} Symbol={symbol} "
                               f"Profit=${current_profit:.2f} | Calling ProfitLockingEngine...")
                    
                    # Call profit locking engine
                    profit_locking_success, profit_locking_reason = profit_locking_engine.check_and_lock_profit(position)
                    
                    if profit_locking_success:
                        # Profit locking engine succeeded - get fresh position to verify SL
                        fresh_position_after_ple = self.order_manager.get_position_by_ticket(ticket)
                        if fresh_position_after_ple:
                            applied_sl = fresh_position_after_ple.get('sl', 0.0)
                            if applied_sl > 0:
                                # Calculate effective profit locked
                                effective_locked = self.get_effective_sl_profit(fresh_position_after_ple)
                                profit_lock_result = {
                                    'target_sl_price': applied_sl,
                                    'target_profit_usd': effective_locked,
                                    'authority_source': 'PROFIT_LOCK',
                                    'reason': f"ProfitLockingEngine: {profit_locking_reason}",
                                    'state': 'SWEET_SPOT',
                                    'is_profit_lock': True
                                }
                                logger.info(f"[LOCK_SUCCESS] Authoritative Path | Ticket={ticket} Symbol={symbol} "
                                           f"ProfitLockingEngine succeeded: {profit_locking_reason}")
                            else:
                                logger.warning(f"[LOCK_BLOCKED] Authoritative Path | Ticket={ticket} Symbol={symbol} "
                                             f"ProfitLockingEngine reported success but SL not applied (SL={applied_sl})")
                        else:
                            logger.warning(f"[LOCK_BLOCKED] Authoritative Path | Ticket={ticket} Symbol={symbol} "
                                         f"ProfitLockingEngine reported success but position not found")
                    else:
                        # Profit locking engine failed - log reason and fall back to internal calculation
                        logger.warning(f"[LOCK_BLOCKED] Authoritative Path | Ticket={ticket} Symbol={symbol} "
                                     f"ProfitLockingEngine failed: {profit_locking_reason} | Falling back to internal calculation")
                except Exception as e:
                    logger.error(f"[ERROR] ProfitLockingEngine error in authoritative path for {symbol} Ticket {ticket}: {e}", exc_info=True)
            
            # Fallback to internal calculation if ProfitLockingEngine not available or failed
            if not profit_lock_result:
                try:
                    profit_to_lock = min(current_profit, self.sweet_spot_max)
                    
                    profit_lock_sl = self._calculate_target_sl_price(
                        entry_price, profit_to_lock, order_type, lot_size, symbol_info, position=position
                    )
                    
                    if profit_lock_sl:
                        # Adjust for broker constraints
                        profit_lock_sl = self._adjust_sl_for_broker_constraints(
                            profit_lock_sl, current_sl, order_type, symbol_info, tick.bid, tick.ask, entry_price=entry_price
                        )
                        
                        if profit_lock_sl:
                            profit_lock_result = {
                                'target_sl_price': profit_lock_sl,
                                'target_profit_usd': profit_to_lock,
                                'authority_source': 'PROFIT_LOCK',
                                'reason': f"Sweet-spot profit locking (${current_profit:.2f} in range ${self.sweet_spot_min:.2f}-${self.sweet_spot_max:.2f})",
                                'state': 'SWEET_SPOT',
                                'is_profit_lock': True
                            }
                except Exception as e:
                    logger.error(f"[ERROR] Profit lock SL calculation failed for {symbol} Ticket {ticket}: {e}", exc_info=True)
        
        # STEP 3: Calculate HARD_SL (lowest priority - only if no trailing/profit lock AND profit < 0)
        hard_sl_result = None
        if not trailing_result and not profit_lock_result and current_profit < 0:
            try:
                hard_sl = self._calculate_target_sl_price(
                    entry_price, -self.max_risk_usd, order_type, lot_size, symbol_info, position=position
                )
                
                if hard_sl:
                    # Adjust for broker constraints
                    hard_sl = self._adjust_sl_for_broker_constraints(
                        hard_sl, current_sl, order_type, symbol_info, tick.bid, tick.ask, entry_price=entry_price
                    )
                    
                    if hard_sl:
                        hard_sl_result = {
                            'target_sl_price': hard_sl,
                            'target_profit_usd': -self.max_risk_usd,
                            'authority_source': 'HARD',
                            'reason': f"Strict loss enforcement (-${self.max_risk_usd:.2f})",
                            'state': 'MANAGING'
                        }
            except Exception as e:
                logger.error(f"[ERROR] Hard SL calculation failed for {symbol} Ticket {ticket}: {e}", exc_info=True)
        
        # STEP 4: SELECT AUTHORITATIVE SL (strict priority)
        authoritative_result = trailing_result or profit_lock_result or hard_sl_result
        
        if authoritative_result:
            result.update(authoritative_result)
            
            # STEP 5: MONOTONIC SL GUARD - Ensure SL never moves backwards
            if current_sl > 0:
                would_regress = False
                
                if order_type == 'BUY':
                    # For BUY: SL must not decrease (higher = better)
                    if authoritative_result['target_sl_price'] < current_sl:
                        would_regress = True
                        violation_msg = f"[CRITICAL][SL_VIOLATION] SL regression attempt: {symbol} Ticket {ticket} | Current SL: {current_sl:.5f} | Target SL: {authoritative_result['target_sl_price']:.5f} | Authority: {authoritative_result['authority_source']} | Profit: ${current_profit:.2f}"
                        result['violations'].append(violation_msg)
                        logger.critical(violation_msg)
                else:  # SELL
                    # For SELL: SL must not increase (lower = better)
                    if authoritative_result['target_sl_price'] > current_sl:
                        would_regress = True
                        violation_msg = f"[CRITICAL][SL_VIOLATION] SL regression attempt: {symbol} Ticket {ticket} | Current SL: {current_sl:.5f} | Target SL: {authoritative_result['target_sl_price']:.5f} | Authority: {authoritative_result['authority_source']} | Profit: ${current_profit:.2f}"
                        result['violations'].append(violation_msg)
                        logger.critical(violation_msg)
                
                # If regression detected, abort the update
                if would_regress:
                    result['target_sl_price'] = None
                    result['reason'] = f"SL regression blocked (current: {current_sl:.5f}, target: {authoritative_result['target_sl_price']:.5f})"
                    result['authority_source'] = None
        else:
            result['reason'] = "No SL update needed"
            result['state'] = 'MANAGING'
        
        return result
    
    def _detect_sl_violations(self, position: Dict[str, Any], authoritative_result: Dict[str, Any]) -> list:
        """
        VIOLATION DETECTOR - Non-optional watchdog.
        
        Detects violations when:
        - Profit > threshold AND SL unchanged
        - Trailing/profit lock should apply but SL not moving
        - SL regression attempts
        
        Args:
            position: Position dictionary
            authoritative_result: Result from compute_authoritative_sl
        
        Returns:
            List of violation strings (empty if no violations)
        """
        violations = []
        symbol = position.get('symbol', '')
        ticket = position.get('ticket', 0)
        current_profit = position.get('profit', 0.0)
        current_sl = position.get('sl', 0.0)
        
        # Violation 1: Profit > threshold but SL unchanged when trailing/profit lock should apply
        if authoritative_result.get('is_trailing') or authoritative_result.get('is_profit_lock'):
            target_sl = authoritative_result.get('target_sl_price')
            if target_sl is not None and current_sl > 0:
                # Check if SL should have moved but didn't
                order_type = position.get('type', '')
                entry_price = position.get('price_open', 0.0)
                
                if order_type == 'BUY':
                    should_move = target_sl > current_sl
                else:  # SELL
                    should_move = target_sl < current_sl
                
                if should_move:
                    # SL should move but hasn't - check if it's been stuck
                    with self._tracking_lock:
                        last_update = self._last_sl_success.get(ticket)
                        if last_update:
                            time_since_update = (datetime.now() - last_update).total_seconds()
                            if time_since_update > 0.25:  # 250ms threshold
                                violation = f"[CRITICAL][SL_VIOLATION] SL not moving: {symbol} Ticket {ticket} | " \
                                          f"Profit: ${current_profit:.2f} | " \
                                          f"Current SL: {current_sl:.5f} | " \
                                          f"Target SL: {target_sl:.5f} | " \
                                          f"Authority: {authoritative_result.get('authority_source')} | " \
                                          f"Time since last update: {time_since_update*1000:.0f}ms"
                                violations.append(violation)
                                logger.critical(violation)
        
        # Violation 2: Trailing/profit lock active but SL unchanged for >250ms
        if (authoritative_result.get('is_trailing') or authoritative_result.get('is_profit_lock')) and \
           authoritative_result.get('target_sl_price') is not None:
            with self._tracking_lock:
                last_attempt = self._last_sl_attempt.get(ticket)
                if last_attempt:
                    time_since_attempt = (datetime.now() - last_attempt).total_seconds()
                    if time_since_attempt > 0.25:  # 250ms threshold
                        violation = f"[CRITICAL][SL_NOT_APPLIED] Trailing/profit lock not applied within 250ms: {symbol} Ticket {ticket} | " \
                                  f"Profit: ${current_profit:.2f} | " \
                                  f"State: {authoritative_result.get('state')} | " \
                                  f"Time since attempt: {time_since_attempt*1000:.0f}ms"
                        violations.append(violation)
                        logger.critical(violation)
        
        return violations
    
    def update_sl_atomic(self, ticket: int, position: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Atomic SL update - applies authoritative SL with guaranteed execution.
        
        Uses compute_authoritative_sl() for lock-free SL decision, then applies with lock.
        
        Priority (STRICT):
            TRAILING_SL > PROFIT_LOCK_SL > HARD_SL
        
        Guaranteed execution: If trailing=true OR sweet_spot=true, SL MUST move within MAX 250ms.
        
        Args:
            ticket: Position ticket number
            position: Position dictionary (must be fresh from order_manager)
        
        Returns:
            (success, reason) tuple
        """
        tracer = get_tracer()
        symbol = position.get('symbol', '')
        current_profit = position.get('profit', 0.0)
        
        # CRITICAL: Initialize _last_sl_reason if not already set (prevents "N/A" in logs)
        with self._tracking_lock:
            if ticket not in self._last_sl_reason:
                self._last_sl_reason[ticket] = "Initializing SL update"
        
        # Trace function entry
        tracer.trace(
            function_name="SLManager.update_sl_atomic",
            expected=f"Update SL for {symbol} Ticket {ticket} based on profit ${current_profit:.2f}",
            actual=f"Starting SL update for {symbol} Ticket {ticket}",
            status="OK",
            ticket=ticket,
            symbol=symbol,
            profit=current_profit
        )
        
        # CRITICAL SAFETY: Check if symbol is disabled
        # FIX: Allow profit-locking updates even for disabled symbols (only block loss protection)
        if symbol in self._disabled_symbols:
            current_profit = position.get('profit', 0.0)
            # Allow profit-locking updates even for disabled symbols
            if current_profit <= 0:
                # Block only loss protection updates for disabled symbols
                reason = f"Symbol {symbol} disabled for safety (loss protection blocked)"
                logger.warning(f"🚫 SL UPDATE BLOCKED: {symbol} Ticket {ticket} | {reason}")
                # CRITICAL: Set _last_sl_reason even on early return
                with self._tracking_lock:
                    self._last_sl_reason[ticket] = reason
                return False, reason
            else:
                # Profitable trades can still update SL even if symbol is disabled
                logger.info(f"[WARNING] Symbol {symbol} disabled but allowing profit-locking update (profit: ${current_profit:.2f})")
        
        # CRITICAL SAFETY: Check circuit breaker - but allow emergency, profit-locking, and first eligible updates
        # FIX: Check profit BEFORE circuit breaker to ensure profitable trades always bypass
        current_profit = position.get('profit', 0.0)
        is_emergency = current_profit < 0  # Losing trades need emergency enforcement
        # CRITICAL FIX: ALL profitable trades (profit > 0) need priority, not just sweet spot
        is_profit_locking = current_profit > 0  # ANY profit needs priority - break-even, sweet spot, or trailing
        
        # Check if this is the first eligible update for this ticket
        with self._tracking_lock:
            first_eligible_info = self._first_eligible_update.get(ticket, {})
            first_eligible_state = first_eligible_info.get('state', 'NONE')
            is_first_eligible = (first_eligible_state == 'NONE' or first_eligible_state == 'PENDING')
        
        # FIX: Reset circuit breaker on successful profit-locking update OR first eligible update
        # This prevents circuit breaker from blocking profitable trades or first updates indefinitely
        if (is_profit_locking or is_first_eligible) and ticket in self._ticket_circuit_breaker:
            logger.info(f"🔄 CIRCUIT BREAKER RESET: {symbol} Ticket {ticket} | "
                      f"{'Profitable trade' if is_profit_locking else 'First eligible update'} "
                      f"(${current_profit:.2f}) - resetting circuit breaker to allow SL update")
            del self._ticket_circuit_breaker[ticket]
        
        if ticket in self._ticket_circuit_breaker:
            disabled_until = self._ticket_circuit_breaker[ticket]
            if time.time() < disabled_until:
                # Circuit breaker is active - but allow emergency, profit-locking, and first eligible updates
                if not is_emergency and not is_profit_locking and not is_first_eligible:
                    reason = f"Circuit breaker active (cooldown: {disabled_until - time.time():.1f}s remaining)"
                    logger.debug(f"[PAUSE] CIRCUIT BREAKER: {symbol} Ticket {ticket} | "
                               f"Update blocked (cooldown until {disabled_until - time.time():.1f}s remaining) | "
                               f"Emergency: {is_emergency}, Profit-locking: {is_profit_locking}, First-eligible: {is_first_eligible}")
                    # CRITICAL: Set _last_sl_reason even on circuit breaker
                    with self._tracking_lock:
                        self._last_sl_reason[ticket] = reason
                    return False, reason
                else:
                    # Allow emergency, profit-locking, or first eligible updates even during circuit breaker
                    update_type = 'emergency' if is_emergency else ('profit-locking' if is_profit_locking else 'first-eligible')
                    logger.info(f"🔄 CIRCUIT BREAKER BYPASS: {symbol} Ticket {ticket} | "
                              f"Allowing {update_type} update despite circuit breaker")
            else:
                # Cooldown expired, remove circuit breaker
                del self._ticket_circuit_breaker[ticket]
                logger.info(f"🔄 Circuit breaker expired for {symbol} Ticket {ticket}, allowing updates")
        
        # CRITICAL SAFETY: Minimal rate limiting - only prevent rapid-fire updates (100ms minimum)
        # BUT: NEVER rate limit profitable trades OR first eligible updates - they MUST lock profit immediately
        
        # Check if this is the first eligible update for this ticket
        with self._tracking_lock:
            first_eligible_info = self._first_eligible_update.get(ticket, {})
            first_eligible_state = first_eligible_info.get('state', 'NONE')
            is_first_eligible = (first_eligible_state == 'NONE' or first_eligible_state == 'PENDING')
        
        current_time = time.time()
        if ticket in self._sl_update_rate_limit:
            time_since_last = current_time - self._sl_update_rate_limit[ticket]
            # CRITICAL: Never rate limit profitable trades OR first eligible updates - they need immediate profit locking
            if time_since_last < self._sl_update_min_interval and not is_emergency and not is_profit_locking and not is_first_eligible:
                # Only rate limit if less than 100ms has passed (very minimal) AND not profitable AND not first eligible
                reason = f"Rate limited (last update {time_since_last*1000:.1f}ms ago)"
                logger.debug(f"[DELAY] SL UPDATE RATE LIMITED: {symbol} Ticket {ticket} | "
                             f"Last update {time_since_last*1000:.1f}ms ago, minimum {self._sl_update_min_interval*1000:.0f}ms")
                # CRITICAL: Set _last_sl_reason even on rate limit
                with self._tracking_lock:
                    self._last_sl_reason[ticket] = reason
                return False, reason
            elif is_first_eligible:
                logger.info(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                          f"Bypassing rate limit for first eligible SL update | TimeSinceLast={time_since_last*1000:.1f}ms")
        
        # Check global RPC rate limit (configurable, default 50/sec)
        consecutive_failures = self._consecutive_failures.get(ticket, 0)
        allowed, backoff_delay = self._check_global_rpc_rate_limit(is_emergency=is_emergency, consecutive_failures=consecutive_failures)
        
        if not allowed:
            # Queue for later (non-emergency) - but log at debug level only
            reason = "Global rate limit exceeded (queued)"
            logger.debug(f"[DELAY] SL UPDATE QUEUED (global rate limit): {symbol} Ticket {ticket}")
            # CRITICAL: Set _last_sl_reason even on global rate limit
            with self._tracking_lock:
                self._last_sl_reason[ticket] = reason
            return False, reason
        
        # Apply emergency backoff if needed
        if backoff_delay > 0:
            time.sleep(backoff_delay)
        
        # ============================================================================
        # SINGLE SL AUTHORITY - LOCK-FREE DECISION PATH
        # ============================================================================
        # STEP 1: Compute authoritative SL WITHOUT locks (lock-free decision path)
        # This ensures SL calculation is never blocked by lock contention
        fresh_position_for_auth = self.order_manager.get_position_by_ticket(ticket)
        if fresh_position_for_auth:
            position.update(fresh_position_for_auth)
        
        # Get initial profit for lock timeout determination
        initial_profit = position.get('profit', 0.0)
        
        authoritative_result = self.compute_authoritative_sl(position)
        
        # STEP 2: Detect violations
        violations = self._detect_sl_violations(position, authoritative_result)
        if violations:
            # Log all violations
            for violation in violations:
                logger.critical(violation)
            # Continue to apply correction
        
        # STEP 3: Check if we have a valid authoritative SL
        has_authoritative_sl = authoritative_result.get('target_sl_price') is not None
        authority_source = authoritative_result.get('authority_source')
        is_trailing = authoritative_result.get('is_trailing', False)
        is_profit_lock = authoritative_result.get('is_profit_lock', False)
        state = authoritative_result.get('state', 'MANAGING')
        
        # MANDATORY LOGGING: Log every SL decision gate
        logger.info(f"[SL_DECISION_GATE] Ticket={ticket} Symbol={symbol} | "
                   f"HasAuthoritativeSL={has_authoritative_sl} Authority={authority_source} | "
                   f"IsTrailing={is_trailing} IsProfitLock={is_profit_lock} State={state} | "
                   f"TargetSL={authoritative_result.get('target_sl_price')} | "
                   f"CurrentProfit=${current_profit:.2f} CurrentSL={position.get('sl', 0.0):.5f}")
        
        # STEP 4: If we have authoritative SL, apply it with guaranteed execution
        if has_authoritative_sl and not authoritative_result.get('violations'):
            # CRITICAL: Determine if this needs guaranteed execution (trailing or profit lock)
            needs_guaranteed_execution = is_trailing or is_profit_lock
            is_profit_locking = initial_profit > 0  # For lock timeout determination
            
            # CRITICAL: Check if this is the first eligible update - mark it as pending
            with self._tracking_lock:
                first_eligible_info = self._first_eligible_update.get(ticket, {})
                first_eligible_state = first_eligible_info.get('state', 'NONE')
                is_first_eligible = (first_eligible_state == 'NONE' or first_eligible_state == 'PENDING')
                
                if is_first_eligible and first_eligible_state == 'NONE':
                    # Mark as pending first eligible update
                    self._first_eligible_update[ticket] = {
                        'state': 'PENDING',
                        'authority': authority_source,
                        'first_seen_time': time.time(),
                        'target_sl': authoritative_result.get('target_sl_price')
                    }
                    logger.info(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                              f"First eligible SL update detected | Authority={authority_source} | "
                              f"TargetSL={authoritative_result.get('target_sl_price'):.5f} | "
                              f"Bypassing all blocking mechanisms")
            
            # CRITICAL: For first eligible updates, force-release stale locks immediately and use non-blocking
            if is_first_eligible:
                # Force-release any stale locks before attempting acquisition
                lock = self._get_ticket_lock(ticket)
                with self._locks_lock:
                    if ticket in self._lock_hold_times:
                        hold_duration = time.time() - self._lock_hold_times[ticket]
                        if hold_duration > 0.05:  # 50ms threshold for first eligible (very aggressive)
                            logger.critical(f"[FIRST_ELIGIBLE] Ticket={ticket} | "
                                          f"Force-releasing stale lock (held {hold_duration*1000:.1f}ms) for first eligible update")
                            del self._lock_hold_times[ticket]
                            if ticket in self._lock_holders:
                                del self._lock_holders[ticket]
                            # Try non-blocking acquisition
                            if lock.acquire(blocking=False):
                                lock.release()
                                logger.info(f"[FIRST_ELIGIBLE] Ticket={ticket} | Stale lock force-released successfully")
            
            # Acquire lock ONLY for MT5 modification (lock-free decision path)
            # CRITICAL: For first eligible updates, use non-blocking first attempt (immediate)
            lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(
                ticket, is_profit_locking=is_profit_locking, force_non_blocking_first=is_first_eligible
            )
            
            if not lock_acquired:
                # Lock failed - check if we need guaranteed execution OR if this is first eligible
                if needs_guaranteed_execution or is_first_eligible:
                    # CRITICAL: For trailing/profit lock OR first eligible, we MUST retry immediately
                    logger.warning(f"[WARNING] Lock failed for {'guaranteed execution' if needs_guaranteed_execution else 'first eligible update'}: "
                                 f"{symbol} Ticket {ticket} | Retrying immediately...")
                    # Retry once more with immediate retry
                    lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(
                        ticket, is_profit_locking=is_profit_locking, force_non_blocking_first=is_first_eligible
                    )
                
                if not lock_acquired:
                    timeout_ms = (self._profit_locking_lock_timeout * 1000) if is_profit_locking else (self._lock_acquisition_timeout * 1000)
                    reason = f"Lock acquisition timeout ({timeout_ms:.0f}ms) - authoritative SL: {authority_source}"
                    logger.warning(f"[DELAY] LOCK TIMEOUT: {symbol} Ticket {ticket} | {reason}")
                    with self._tracking_lock:
                        self._last_sl_reason[ticket] = reason
                    return False, reason
            
            # Apply authoritative SL with guaranteed execution loop if needed
            target_sl_price = authoritative_result['target_sl_price']
            target_profit_usd = authoritative_result['target_profit_usd']
            reason_str = authoritative_result['reason']
            old_sl = position.get('sl', 0.0)
            current_profit = position.get('profit', 0.0)
            
            # Explicit state logging
            logger.info(f"[STATE={state}] {symbol} Ticket {ticket} | "
                       f"Authority: {authority_source} | "
                       f"OldSL: {old_sl:.5f} | NewSL: {target_sl_price:.5f} | "
                       f"Profit: ${current_profit:.2f} | "
                       f"Reason: {reason_str}")
            
            # GUARANTEED EXECUTION LOOP: If trailing=true OR sweet_spot=true, SL MUST move within MAX 250ms
            if needs_guaranteed_execution:
                execution_start_time = time.time()
                max_execution_time = 0.25  # 250ms max
                
                with lock:
                    success = self._apply_sl_update(ticket, symbol, target_sl_price, target_profit_usd, reason_str, position=position)
                    
                    execution_time = time.time() - execution_start_time
                    
                    if success:
                        # Update tracking
                        with self._tracking_lock:
                            self._last_sl_update[ticket] = datetime.now()
                            self._last_sl_price[ticket] = target_sl_price
                            self._last_sl_reason[ticket] = reason_str
                            self._last_sl_success[ticket] = datetime.now()
                            
                            # Mark first eligible update as applied
                            if ticket in self._first_eligible_update:
                                self._first_eligible_update[ticket]['state'] = 'APPLIED'
                                self._first_eligible_update[ticket]['applied_time'] = time.time()
                                logger.info(f"[FIRST_ELIGIBLE] Ticket={ticket} Symbol={symbol} | "
                                          f"First eligible SL update APPLIED successfully | "
                                          f"Time from first seen: {(time.time() - self._first_eligible_update[ticket].get('first_seen_time', time.time()))*1000:.1f}ms")
                        
                        # Log explicit events
                        if is_trailing:
                            system_event_logger.systemEvent("TRAILING_EXECUTED", {
                                "ticket": ticket,
                                "symbol": symbol,
                                "old_sl": old_sl,
                                "new_sl": target_sl_price,
                                "profit": current_profit,
                                "authority_source": authority_source,
                                "state": state,
                                "execution_time_ms": execution_time * 1000
                            })
                            logger.info(f"[OK] TRAILING_EXECUTED: {symbol} Ticket {ticket} | "
                                      f"OldSL: {old_sl:.5f} | NewSL: {target_sl_price:.5f} | "
                                      f"Profit: ${current_profit:.2f} | Execution: {execution_time*1000:.1f}ms")
                        elif is_profit_lock:
                            system_event_logger.systemEvent("LOCK_APPLIED", {
                                "ticket": ticket,
                                "symbol": symbol,
                                "old_sl": old_sl,
                                "new_sl": target_sl_price,
                                "profit": current_profit,
                                "authority_source": authority_source,
                                "state": state,
                                "execution_time_ms": execution_time * 1000
                            })
                            logger.info(f"[OK] LOCK_APPLIED: {symbol} Ticket {ticket} | "
                                      f"OldSL: {old_sl:.5f} | NewSL: {target_sl_price:.5f} | "
                                      f"Profit: ${current_profit:.2f} | Execution: {execution_time*1000:.1f}ms")
                        
                        return True, reason_str
                    else:
                        # SL update failed - check if we exceeded execution time
                        if execution_time > max_execution_time:
                            logger.critical(f"[CRITICAL][SL_NOT_APPLIED] {symbol} Ticket {ticket} | "
                                          f"Trailing/profit lock not applied within 250ms | "
                                          f"Execution time: {execution_time*1000:.1f}ms | "
                                          f"Authority: {authority_source} | State: {state}")
                        
                        # Log failure
                        if is_profit_lock:
                            system_event_logger.systemEvent("LOCK_FAILED", {
                                "ticket": ticket,
                                "symbol": symbol,
                                "old_sl": old_sl,
                                "target_sl": target_sl_price,
                                "profit": current_profit,
                                "authority_source": authority_source,
                                "state": state
                            })
                            logger.error(f"[ERROR] LOCK_FAILED: {symbol} Ticket {ticket} | "
                                       f"TargetSL: {target_sl_price:.5f} | Authority: {authority_source}")
                        
                        with self._tracking_lock:
                            self._last_sl_reason[ticket] = f"SL update failed: {reason_str}"
                        return False, f"SL update failed: {reason_str}"
            else:
                # Non-guaranteed execution (HARD SL) - apply normally
                with lock:
                    success = self._apply_sl_update(ticket, symbol, target_sl_price, target_profit_usd, reason_str, position=position)
                    
                    if success:
                        with self._tracking_lock:
                            self._last_sl_update[ticket] = datetime.now()
                            self._last_sl_price[ticket] = target_sl_price
                            self._last_sl_reason[ticket] = reason_str
                            self._last_sl_success[ticket] = datetime.now()
                        return True, reason_str
                    else:
                        with self._tracking_lock:
                            self._last_sl_reason[ticket] = f"SL update failed: {reason_str}"
                        return False, f"SL update failed: {reason_str}"
        
        # If authoritative SL had violations or no valid SL, continue with old logic for backward compatibility
        # (This ensures we don't break existing functionality)
        
        # CRITICAL: Check if this is a profitable trade BEFORE acquiring lock
        # This allows us to use longer timeout for profitable trades
        # CRITICAL FIX: ALL profitable trades (profit > 0) need priority, not just sweet spot
        initial_profit = position.get('profit', 0.0)
        is_profit_locking = initial_profit > 0  # ANY profit needs priority - break-even, sweet spot, or trailing
        
        # CRITICAL SAFETY FIX: For losing trades, attempt lock-free emergency strict loss enforcement
        # if lock acquisition fails. This ensures positions are NEVER left unprotected.
        if initial_profit < 0:
            # This is a losing trade - strict loss enforcement is CRITICAL
            # Try to acquire lock, but if it fails, use emergency lock-free path
            lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=False)
            
            if not lock_acquired:
                # CRITICAL: Lock failed for losing trade - use emergency lock-free path
                # BUT: Prevent infinite loops by tracking emergency enforcements
                emergency_count = self._emergency_enforcement_count.get(ticket, 0)
                if emergency_count >= self._emergency_enforcement_max:
                    # Already used max emergency enforcements - skip to prevent loop
                    timeout_ms = self._lock_acquisition_timeout * 1000
                    reason = f"Emergency enforcement limit reached ({self._emergency_enforcement_max}) - lock timeout ({timeout_ms:.0f}ms)"
                    logger.warning(f"[WARNING] EMERGENCY LIMIT: {symbol} Ticket {ticket} | {reason}")
                    with self._tracking_lock:
                        self._last_sl_reason[ticket] = reason
                    self._track_update_metrics(ticket, symbol, False, reason, initial_profit)
                    return False, reason
                
                timeout_ms = self._lock_acquisition_timeout * 1000
                logger.critical(f"[EMERGENCY] LOCK TIMEOUT FOR LOSING TRADE: {symbol} Ticket {ticket} | "
                             f"Profit: ${initial_profit:.2f} | "
                             f"Lock timeout ({timeout_ms:.0f}ms) - Using EMERGENCY LOCK-FREE strict loss enforcement | "
                             f"Count: {emergency_count + 1}/{self._emergency_enforcement_max}")
                
                # Track emergency enforcement attempt
                self._emergency_enforcement_count[ticket] = emergency_count + 1
                
                # Use emergency lock-free strict loss enforcement
                emergency_success, emergency_reason, emergency_sl = self._enforce_strict_loss_emergency_lockfree(position)
                
                if emergency_success:
                    # Emergency enforcement succeeded - log and return
                    # Reset emergency count on success
                    self._emergency_enforcement_count.pop(ticket, None)
                    
                    mode = "BACKTEST" if self.config.get('mode') == 'backtest' else "LIVE"
                    logger.critical(f"[EMERGENCY] HARD SL ENFORCED (LOCK-FREE): mode={mode} | "
                                 f"ticket={ticket} | symbol={symbol} | "
                                 f"sl_price={emergency_sl:.5f} | reason={emergency_reason}")
                    
                    # Track metrics
                    with self._tracking_lock:
                        self._last_sl_reason[ticket] = f"Emergency lock-free: {emergency_reason}"
                    self._track_update_metrics(ticket, symbol, True, emergency_reason, initial_profit)
                    
                    return True, emergency_reason
                else:
                    # Emergency enforcement also failed - CRITICAL ERROR
                    mode = "BACKTEST" if self.config.get('mode') == 'backtest' else "LIVE"
                    logger.critical(f"[CRITICAL] EMERGENCY LOCK-FREE SL FAILED: mode={mode} | "
                                 f"ticket={ticket} | symbol={symbol} | "
                                 f"reason={emergency_reason} | "
                                 f"POSITION MAY BE UNPROTECTED")
                    
                    with self._tracking_lock:
                        self._last_sl_reason[ticket] = f"Emergency lock-free failed: {emergency_reason}"
                    self._track_update_metrics(ticket, symbol, False, emergency_reason, initial_profit)
                    
                    return False, emergency_reason
        else:
            # Profitable trade - normal lock acquisition
            lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=is_profit_locking)
        
        if not lock_acquired:
            # Lock acquisition timeout - log and return
            # CRITICAL FIX: Use actual configured timeout values
            timeout_ms = (self._profit_locking_lock_timeout * 1000) if is_profit_locking else (self._lock_acquisition_timeout * 1000)
            reason = lock_reason or f"Lock acquisition timeout ({timeout_ms:.0f}ms)"
            logger.warning(f"[DELAY] LOCK TIMEOUT: {symbol} Ticket {ticket} | "
                         f"Could not acquire lock within {timeout_ms:.0f}ms | "
                         f"SL update skipped to prevent blocking | "
                         f"Profit: ${initial_profit:.2f} {'(PROFIT-LOCKING PRIORITY)' if is_profit_locking else ''}")
            # Track lock contention metrics
            self._track_lock_contention(ticket, timeout=True)
            # CRITICAL: Set _last_sl_reason even on failure
            with self._tracking_lock:
                self._last_sl_reason[ticket] = reason
                # Update profit zone tracking with lock timeout
                if ticket in self._profit_zone_entry:
                    self._profit_zone_entry[ticket]['last_update_reason'] = f"Lock timeout ({timeout_ms:.0f}ms)"
            # Track metrics for failed update
            self._track_update_metrics(ticket, symbol, False, reason, initial_profit)
            # Log system event
            system_event_logger.systemEvent("SL_UPDATE_FAILED", {
                "ticket": ticket,
                "symbol": symbol,
                "error": f"Lock acquisition timeout ({timeout_ms:.0f}ms)",
                "reason": "Lock timeout",
                "profit": initial_profit,
                "is_profit_locking": is_profit_locking
            })
            return False, reason
        
        try:
            # CRITICAL FIX: Get fresh position data BEFORE acquiring lock to minimize lock hold time
            # This prevents network calls from blocking the lock
            fresh_position = self.order_manager.get_position_by_ticket(ticket)
            if fresh_position:
                current_profit = fresh_position.get('profit', 0.0)
                # Update position dict with fresh data
                position.update(fresh_position)
            else:
                current_profit = position.get('profit', 0.0)
                logger.warning(f"[WARNING] Could not get fresh position for {symbol} Ticket {ticket}, using cached data")
            
            # Flag to track if we need to do network calls outside lock
            needs_strict_loss_update = False
            needs_break_even_update = False
            needs_sweet_spot_update = False
            needs_trailing_update = False
            
            with lock:
                # Use the fresh profit value
                current_profit = position.get('profit', 0.0)
                
                # CRITICAL: Track profit zone entry - detect when trade first enters profit zone
                with self._tracking_lock:
                    tracking = self._position_tracking.get(ticket, {})
                    last_profit = tracking.get('last_profit', 0.0)
                    
                    # Detect profit zone entry (transition from negative/zero to positive)
                    if current_profit > 0 and last_profit <= 0:
                        # Trade just entered profit zone - log and track
                        if ticket not in self._profit_zone_entry:
                            self._profit_zone_entry[ticket] = {
                                'entry_time': datetime.now(),
                                'entry_profit': current_profit,
                                'sl_updated': False,
                                'update_attempts': 0,
                                'last_update_time': None,
                                'last_update_reason': None,
                                'symbol': symbol
                            }
                            logger.info(f"🎯 PROFIT ZONE ENTRY: {symbol} Ticket {ticket} | "
                                      f"Entered profit zone at ${current_profit:.2f} | "
                                      f"Time: {self._profit_zone_entry[ticket]['entry_time'].strftime('%H:%M:%S')}")
                            # Log system event
                            system_event_logger.systemEvent("PROFIT_ZONE_ENTERED", {
                                "ticket": ticket,
                                "symbol": symbol,
                                "profit": current_profit,
                                "entry_time": self._profit_zone_entry[ticket]['entry_time'].isoformat()
                            })
                    
                    # Update profit zone tracking if already in profit zone
                    if ticket in self._profit_zone_entry:
                        self._profit_zone_entry[ticket]['update_attempts'] += 1
                        self._profit_zone_entry[ticket]['last_update_time'] = datetime.now()
                        
                        # CRITICAL: Check if SL update is required but not done yet
                        # Force update if trade has been in profit zone for >5 seconds without SL update
                        entry_data = self._profit_zone_entry[ticket]
                        time_in_profit = (datetime.now() - entry_data['entry_time']).total_seconds()
                        
                        # If SL hasn't been updated and it's been >5 seconds, mark for force update
                        # We'll check the actual SL status outside the lock to avoid network calls here
                        if not entry_data['sl_updated'] and time_in_profit > 5.0:
                            # Mark that we need to force update - actual check will happen outside lock
                            entry_data['force_update'] = True
                            entry_data['force_update_reason'] = f"SL not updated after {time_in_profit:.1f}s in profit zone (${current_profit:.2f})"
                            entry_data['force_update_profit'] = current_profit  # Store current profit for later check
                    
                    if current_profit < 0:
                        # Profit is negative - reset break-even start time and clear profit zone entry
                        if 'break_even_start_time' in tracking:
                            del tracking['break_even_start_time']
                            logger.debug(f"🔄 BREAK-EVEN RESET: {symbol} Ticket {ticket} | "
                                       f"Profit went negative (${current_profit:.2f}), resetting start time")
                        # Clear profit zone entry if trade went back to loss
                        if ticket in self._profit_zone_entry:
                            entry_duration = (datetime.now() - self._profit_zone_entry[ticket]['entry_time']).total_seconds()
                            logger.warning(f"[WARNING] PROFIT ZONE EXIT: {symbol} Ticket {ticket} | "
                                         f"Exited profit zone after {entry_duration:.1f}s | "
                                         f"SL Updated: {self._profit_zone_entry[ticket]['sl_updated']} | "
                                         f"Attempts: {self._profit_zone_entry[ticket]['update_attempts']}")
                            del self._profit_zone_entry[ticket]
                        # Also reset last_profit to track state
                        tracking['last_profit'] = current_profit
                        self._position_tracking[ticket] = tracking
                    else:
                        # Update last_profit for tracking
                        tracking['last_profit'] = current_profit
                        self._position_tracking[ticket] = tracking
                
                # Check if force update is needed (store flag, verify outside lock)
                needs_force_update_check = False
                force_update_data = None
                if current_profit > 0 and ticket in self._profit_zone_entry:
                    entry_data = self._profit_zone_entry[ticket]
                    if entry_data.get('force_update', False):
                        needs_force_update_check = True
                        force_update_data = {
                            'reason': entry_data.get('force_update_reason', 'SL not updated in profit zone'),
                            'profit': entry_data.get('force_update_profit', current_profit),
                            'entry_data': entry_data
                        }
            
            # Exit lock - verify force update outside lock (may need network calls)
            if needs_force_update_check:
                # Get fresh position for verification
                fresh_position_force = self.order_manager.get_position_by_ticket(ticket)
                if fresh_position_force:
                    current_sl = fresh_position_force.get('sl', 0.0)
                    entry_price = fresh_position_force.get('price_open', 0.0)
                    force_profit = force_update_data['profit']
                    sl_needs_update = False
                    
                    try:
                        # Check if SL needs updating based on profit level
                        # NOTE: No break-even logic - profit < $0.03: No action, profit ≥ $0.03: Lock immediately
                        if self.sweet_spot_min <= force_profit <= self.sweet_spot_max:
                            # Sweet spot - SL should lock in profit
                            current_effective_sl = self.get_effective_sl_profit(fresh_position_force)
                            if current_effective_sl < 0.01:  # SL doesn't lock in profit
                                sl_needs_update = True
                        elif force_profit > self.trailing_increment_usd:
                            # Trailing stop - SL should trail properly
                            current_effective_sl = self.get_effective_sl_profit(fresh_position_force)
                            expected_locked = force_profit - self.trailing_increment_usd
                            if current_effective_sl < expected_locked - 0.01:  # SL doesn't trail properly
                                sl_needs_update = True
                    except Exception as e:
                        # If we can't check, assume update is needed
                        logger.warning(f"[WARNING] Could not verify SL status for force update: {e}")
                        sl_needs_update = True
                    
                    if sl_needs_update:
                        logger.warning(f"🔄 FORCING SL UPDATE: {symbol} Ticket {ticket} | "
                                     f"Reason: {force_update_data['reason']} | "
                                     f"Profit: ${force_profit:.2f} | "
                                     f"Current SL: {current_sl:.5f} | "
                                     f"SL needs update: YES - Will proceed with normal SL update flow")
                        # Clear force flag - normal checks will handle the update
                        with self._tracking_lock:
                            force_update_data['entry_data']['force_update'] = False
                        # Update position data and continue with normal flow
                        position.update(fresh_position_force)
                        current_profit = fresh_position_force.get('profit', 0.0)
                    else:
                        # SL is actually correct - mark as updated
                        logger.info(f"[OK] FORCE UPDATE CHECK: {symbol} Ticket {ticket} | "
                                   f"SL is already correct, marking as updated")
                        with self._tracking_lock:
                            force_update_data['entry_data']['sl_updated'] = True
                            force_update_data['entry_data']['force_update'] = False
                            self._last_sl_success[ticket] = datetime.now()
                            self._last_sl_attempt[ticket] = datetime.now()
                        # SL is correct, no need to continue
                        return False, "SL already correct (force update check)"
            
            # Re-acquire lock to continue with normal SL update flow
            lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=(current_profit > 0))
            if not lock_acquired:
                return False, lock_reason or "Could not re-acquire lock after force update check"
            
            # Get fresh position data for normal flow
            fresh_position_normal = self.order_manager.get_position_by_ticket(ticket)
            if fresh_position_normal:
                current_profit = fresh_position_normal.get('profit', 0.0)
                position.update(fresh_position_normal)
            
            with lock:
                
                # PRIORITY 1: Strict loss enforcement (-$2.00) if P/L < 0
                # CRITICAL: This MUST work for ALL losing trades - continuously check and enforce
                # NO EXCEPTIONS - this is the most important safety mechanism
                if current_profit < 0:
                    tracer.trace(
                        function_name="SLManager.update_sl_atomic",
                        expected=f"Enforce strict loss -$2.00 for losing trade {symbol} Ticket {ticket}",
                        actual=f"Checking strict loss enforcement for {symbol} Ticket {ticket} (P/L: ${current_profit:.2f})",
                        status="OK",
                        ticket=ticket,
                        symbol=symbol,
                        profit=current_profit,
                        priority="STRICT_LOSS"
                    )
                    # ALWAYS check effective SL for losing trades - this is non-negotiable
                    current_sl_price = position.get('sl', 0.0)
                    current_effective_sl = None
                    calculation_error = None
                    
                    # CRITICAL: Always try to calculate effective SL, even if current_sl == 0
                    try:
                        current_effective_sl = self.get_effective_sl_profit(position)
                        logger.info(f"🔍 STRICT LOSS CHECK: {symbol} Ticket {ticket} | "
                                   f"Current SL price: {current_sl_price:.5f} | "
                                   f"Current effective SL: ${current_effective_sl:.2f} | "
                                   f"Current profit: ${current_profit:.2f}")
                    except Exception as e:
                        calculation_error = str(e)
                        logger.error(f"[ERROR] ERROR calculating effective SL for {symbol} Ticket {ticket}: {e}", exc_info=True)
                        # If we can't calculate, assume it's wrong and force update
                        current_effective_sl = None
                    
                    # CRITICAL: If effective SL is not -$2.00 (within tolerance), force update
                    target_effective_sl = -self.max_risk_usd
                    needs_update = False
                    update_reason = ""
                    
                    if current_effective_sl is None or current_sl_price == 0.0:
                        # No SL set or can't calculate - must set one
                        needs_update = True
                        update_reason = f"No SL or invalid SL (calculation error: {calculation_error})" if calculation_error else "No SL set"
                        logger.critical(f"🚨 STRICT LOSS: {symbol} Ticket {ticket} | {update_reason} | Must enforce -$2.00")
                    else:
                        # CRITICAL: Check if effective SL is WORSE than target (more negative)
                        # If effective SL < target, ALWAYS update regardless of tolerance
                        # This ensures we never allow risk to exceed -$2.00
                        # Use small tolerance for floating point comparison (0.01)
                        sl_diff = current_effective_sl - target_effective_sl
                        if sl_diff < -0.01:  # More negative (worse) by at least $0.01
                            # Effective SL is WORSE than -$2.00 - MUST update immediately
                            needs_update = True
                            violation_amount = abs(sl_diff)
                            update_reason = f"Effective SL ${current_effective_sl:.2f} is WORSE than target ${target_effective_sl:.2f} (violation: ${violation_amount:.2f})"
                            logger.critical(f"🚨 CRITICAL STRICT LOSS VIOLATION: {symbol} Ticket {ticket} | {update_reason} | MUST enforce -$2.00 immediately")
                        else:
                            # Effective SL is better than or equal to target - check if within tolerance
                            effective_sl_error = abs(current_effective_sl - target_effective_sl)
                            tolerance = 0.50  # $0.50 tolerance
                            
                            logger.info(f"🔍 STRICT LOSS CHECK: {symbol} Ticket {ticket} | "
                                       f"Current effective SL: ${current_effective_sl:.2f} | "
                                       f"Target: ${target_effective_sl:.2f} | "
                                       f"Error: ${effective_sl_error:.2f} | "
                                       f"Tolerance: ${tolerance:.2f} | "
                                       f"Needs update: {effective_sl_error >= tolerance}")
                            
                            if effective_sl_error >= tolerance:
                                needs_update = True
                                update_reason = f"Effective SL ${current_effective_sl:.2f} != ${target_effective_sl:.2f} (error: ${effective_sl_error:.2f})"
                                logger.warning(f"[WARNING] STRICT LOSS: {symbol} Ticket {ticket} | {update_reason} | Will enforce -$2.00")
                    
                    if needs_update:
                        # CRITICAL FIX: Store position data and release lock before network calls
                        strict_loss_position = position.copy()
                        strict_loss_update_reason = update_reason
                        strict_loss_target_effective_sl = target_effective_sl
                        strict_loss_current_effective_sl = current_effective_sl
                        # Exit lock context to release it
                        needs_strict_loss_update = True
                    else:
                        # Effective SL is correct - log success and update tracking
                        logger.debug(f"[OK] STRICT LOSS OK: {symbol} Ticket {ticket} | Effective SL: ${current_effective_sl:.2f} (target: ${target_effective_sl:.2f})")
                        # CRITICAL FIX: Update _last_sl_success when SL is already correct (no update needed)
                        # This ensures the logger shows "[OK]" instead of "[W]"
                        with self._tracking_lock:
                            self._last_sl_success[ticket] = datetime.now()
                            self._last_sl_attempt[ticket] = datetime.now()
                        needs_strict_loss_update = False
            
            # Exit lock context - lock is automatically released
            # Now check if we need to do network calls for strict loss
            if needs_strict_loss_update:
                # Release lock tracking
                with self._locks_lock:
                    if ticket in self._lock_hold_times:
                        del self._lock_hold_times[ticket]
                
                # Now enforce strict loss OUTSIDE the lock (all network calls happen here)
                # CRITICAL: For strict loss, we MUST succeed - retry up to 3 times immediately
                max_immediate_retries = 3
                tracer.trace(
                    function_name="SLManager.update_sl_atomic",
                    expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                    actual=f"Attempting strict loss enforcement (reason: {strict_loss_update_reason})",
                    status="OK",
                    ticket=ticket,
                    symbol=symbol,
                    reason=strict_loss_update_reason,
                    max_retries=max_immediate_retries
                )
                
                success = False
                reason = ""
                for immediate_retry in range(max_immediate_retries):
                    success, reason, _ = self._enforce_strict_loss_limit(strict_loss_position)
                    if success:
                        # Reset emergency count on success
                        self._emergency_enforcement_count.pop(ticket, None)
                        # Re-acquire lock briefly to update tracking
                        lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=False)
                        if lock_acquired:
                            try:
                                with lock:
                                    # Update rate limit on success
                                    self._sl_update_rate_limit[ticket] = current_time
                                    logger.info(f"[OK] STRICT LOSS ENFORCED: {symbol} Ticket {ticket} | Attempt {immediate_retry + 1}/{max_immediate_retries} | Reason: {strict_loss_update_reason}")
                                    tracer.trace(
                                        function_name="SLManager.update_sl_atomic",
                                        expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                                        actual=f"Strict loss enforced successfully (attempt {immediate_retry + 1}/{max_immediate_retries})",
                                        status="OK",
                                        ticket=ticket,
                                        symbol=symbol,
                                        attempt=immediate_retry + 1,
                                        reason=reason
                                    )
                                    # Track metrics for strict loss enforcement
                                    self._track_update_metrics(ticket, symbol, True, reason,
                                                              strict_loss_position.get('profit', 0.0))
                                    return True, reason
                            except Exception as e:
                                logger.error(f"Error updating tracking after strict loss: {e}", exc_info=True)
                        else:
                            # Lock re-acquisition failed but SL update succeeded
                            logger.warning(f"[WARNING] Could not re-acquire lock for tracking, but strict loss was enforced")
                            # Track metrics even if lock re-acquisition failed
                            self._track_update_metrics(ticket, symbol, True, reason,
                                                      strict_loss_position.get('profit', 0.0))
                            return True, reason
                    else:
                        if immediate_retry < max_immediate_retries - 1:
                            logger.warning(f"[WARNING] STRICT LOSS RETRY: {symbol} Ticket {ticket} | Attempt {immediate_retry + 1}/{max_immediate_retries} failed: {reason} | Retrying immediately...")
                            tracer.trace(
                                function_name="SLManager.update_sl_atomic",
                                expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                                actual=f"Strict loss enforcement failed (attempt {immediate_retry + 1}/{max_immediate_retries})",
                                status="WARNING",
                                ticket=ticket,
                                symbol=symbol,
                                attempt=immediate_retry + 1,
                                reason=reason,
                                will_retry=True
                            )
                            time.sleep(0.2)  # Brief delay before retry
                        else:
                            # All immediate retries failed - log CRITICAL and will retry next cycle
                            # Fix: Calculate formatted value outside f-string to avoid format specifier error
                            current_sl_str = f"${strict_loss_current_effective_sl:.2f}" if strict_loss_current_effective_sl is not None else "N/A"
                            logger.critical(f"CRITICAL: Strict loss enforcement FAILED after {max_immediate_retries} attempts for {symbol} Ticket {ticket}: {reason} | "
                                          f"Current effective SL: {current_sl_str} | "
                                          f"Target: ${strict_loss_target_effective_sl:.2f} | "
                                          f"Will retry next cycle")
                            # Track metrics for strict loss failure
                            self._track_update_metrics(ticket, symbol, False,
                                                      f"Strict loss failed after {max_immediate_retries} attempts: {reason}",
                                                      strict_loss_position.get('profit', 0.0) if 'strict_loss_position' in locals() else 0.0)
                            tracer.trace(
                                function_name="SLManager.update_sl_atomic",
                                expected=f"Enforce strict loss -$2.00 for {symbol} Ticket {ticket}",
                                actual=f"Strict loss enforcement FAILED after {max_immediate_retries} attempts",
                                status="FAILED",
                                ticket=ticket,
                                symbol=symbol,
                                attempts=max_immediate_retries,
                                reason=reason,
                                current_effective_sl=strict_loss_current_effective_sl,
                                target_effective_sl=strict_loss_target_effective_sl,
                                will_retry_next_cycle=True
                            )
            
            # Re-acquire lock to continue with other checks (strict loss failed or didn't need update)
            # CRITICAL FIX: Check if profitable to use proper timeout
            fresh_position_check = self.order_manager.get_position_by_ticket(ticket)
            current_profit_check = fresh_position_check.get('profit', 0.0) if fresh_position_check else 0.0
            is_profit_locking_check = (current_profit_check >= 0.01)
            lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=is_profit_locking_check)
            if not lock_acquired:
                # Can't continue without lock - return failure
                # Track lock contention
                self._track_lock_contention(ticket, timeout=False)
                self._track_update_metrics(ticket, symbol, False,
                                         f"Could not re-acquire lock after strict loss enforcement attempts",
                                         strict_loss_position.get('profit', 0.0) if 'strict_loss_position' in locals() else 0.0)
                return False, f"Could not re-acquire lock after strict loss enforcement attempts"
            
            # Get fresh position data for next checks
            fresh_position_strict = self.order_manager.get_position_by_ticket(ticket)
            if fresh_position_strict:
                current_profit = fresh_position_strict.get('profit', 0.0)
                position.update(fresh_position_strict)
            
            # Initialize break-even variables to avoid UnboundLocalError
            break_even_position = None
            break_even_profit = None
            needs_break_even = False
            
            with lock:
                
                # PRIORITY 2: Sweet-spot profit locking if profit ≥ $0.03 and ≤ $0.10
                # NOTE: Break-even logic is DISABLED per Step 2c requirement
                # Profit < $0.03: No action (wait until profit reaches sweet spot)
                # Profit ≥ $0.03: Immediately lock profit (sweet spot)
                # No break-even logic; lock profit immediately at sweet spot or above
                
                # CRITICAL FIX: Initialize variables before conditional block to prevent UnboundLocalError
                sweet_spot_position = None
                sweet_spot_profit = 0.0
                profit_locking_start_time = None
                is_first_sweet_spot_entry = False
            
            # Get fresh position data for sweet spot check
            fresh_position_sweet_spot = self.order_manager.get_position_by_ticket(ticket)
            if fresh_position_sweet_spot:
                current_profit = fresh_position_sweet_spot.get('profit', 0.0)
                position.update(fresh_position_sweet_spot)
            
            # Re-acquire lock for sweet spot check
            lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=(current_profit >= self.sweet_spot_min))
            if not lock_acquired:
                # Can't continue without lock
                return False, "Could not acquire lock for sweet spot check"
            
            with lock:
                # PRIORITY 3: Sweet-spot profit locking if profit ≥ $0.03 and ≤ $0.10
                if self.sweet_spot_min <= current_profit <= self.sweet_spot_max:
                    # Track if this is first entry into sweet spot
                    # Also track activation time for profit locking metrics
                    profit_locking_start_time = time.time()
                    is_first_sweet_spot_entry = False
                    with self._tracking_lock:
                        tracking = self._position_tracking.get(ticket, {})
                        last_profit = tracking.get('last_profit', 0.0)
                        if last_profit < self.sweet_spot_min or last_profit > self.sweet_spot_max:
                            # First entry into sweet spot - log event and track activation time
                            is_first_sweet_spot_entry = True
                            tracking['sweet_spot_entry_time'] = profit_locking_start_time
                            system_event_logger.systemEvent("SWEET_SPOT_ENTERED", {
                                "ticket": ticket,
                                "symbol": symbol,
                                "profit": current_profit
                            })
                        tracking['last_profit'] = current_profit
                        self._position_tracking[ticket] = tracking
                    
                    logger.info(f"SWEET SPOT CHECK: {symbol} Ticket {ticket} | "
                               f"Profit: ${current_profit:.2f} (range: ${self.sweet_spot_min:.2f}-${self.sweet_spot_max:.2f}) | "
                               f"Attempting to lock profit...")
                    tracer.trace(
                        function_name="SLManager.update_sl_atomic",
                        expected=f"Lock sweet spot profit for {symbol} Ticket {ticket}",
                        actual=f"Checking sweet spot lock (profit: ${current_profit:.2f})",
                        status="OK",
                        ticket=ticket,
                        symbol=symbol,
                        profit=current_profit,
                        priority="SWEET_SPOT"
                    )
                    # CRITICAL FIX: Store position data and release lock before network calls
                    sweet_spot_position = position.copy()
                    sweet_spot_profit = current_profit
            # Release lock before making network calls (sweet spot case)
            self._release_ticket_lock(ticket, lock)
            
            # CRITICAL FIX: Only proceed with sweet spot logic if we're actually in sweet spot range
            if sweet_spot_position is not None and sweet_spot_profit > 0:
                # CRITICAL FIX: Try ProfitLockingEngine first (more sophisticated sweet-spot logic)
                # This must happen BEFORE internal sweet-spot logic
                profit_locking_success = False
                profit_locking_reason = ""
                if hasattr(self, '_risk_manager') and self._risk_manager:
                    profit_locking_engine = getattr(self._risk_manager, '_profit_locking_engine', None)
                    if profit_locking_engine:
                        try:
                            # CRITICAL FIX: Add explicit logging before calling profit locking engine
                            logger.info(f"Profit locking triggered for ticket {ticket} | Symbol={symbol} | Profit=${sweet_spot_profit:.2f} | "
                                       f"Sweet spot range: ${self.sweet_spot_min:.2f}-${self.sweet_spot_max:.2f}")
                            logger.info(f"PROFIT LOCKING ENGINE: {symbol} Ticket {ticket} | "
                                       f"Profit=${sweet_spot_profit:.2f} | Attempting sweet-spot lock via ProfitLockingEngine...")
                            profit_locking_success, profit_locking_reason = profit_locking_engine.check_and_lock_profit(sweet_spot_position)
                            logger.info(f"PROFIT LOCKING ENGINE RESULT: {symbol} Ticket {ticket} | Success={profit_locking_success} | Reason={profit_locking_reason}")
                            if profit_locking_success:
                                logger.info(f"PROFIT LOCKING ENGINE SUCCESS: {symbol} Ticket {ticket} | {profit_locking_reason}")
                                # Get fresh position to verify SL was updated
                                fresh_position_after_ple = self.order_manager.get_position_by_ticket(ticket)
                                if fresh_position_after_ple:
                                    applied_sl = fresh_position_after_ple.get('sl', 0.0)
                                    target_sl = applied_sl  # Use applied SL as target
                                    # Re-acquire lock to update tracking
                                    lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=True)
                                    if lock_acquired:
                                        try:
                                            with lock:
                                                self._sl_update_rate_limit[ticket] = current_time
                                                # Update profit zone tracking
                                                with self._tracking_lock:
                                                    if ticket in self._profit_zone_entry:
                                                        self._profit_zone_entry[ticket]['sl_updated'] = True
                                                        self._profit_zone_entry[ticket]['last_update_reason'] = f"ProfitLockingEngine: {profit_locking_reason}"
                                                logger.info(f"SWEET SPOT APPLIED (via ProfitLockingEngine): {symbol} Ticket {ticket} | {profit_locking_reason}")
                                                # Log sweet spot locked event
                                                system_event_logger.systemEvent("SWEET_SPOT_LOCKED", {
                                                    "ticket": ticket,
                                                    "symbol": symbol,
                                                    "sl": target_sl,
                                                    "profit": sweet_spot_profit,
                                                    "method": "ProfitLockingEngine"
                                                })
                                                # Track metrics: calculate activation time if first entry
                                                activation_time_ms = None
                                                if is_first_sweet_spot_entry:
                                                    activation_time_ms = (time.time() - profit_locking_start_time) * 1000
                                                self._track_update_metrics(ticket, symbol, True, f"ProfitLockingEngine: {profit_locking_reason}",
                                                                          sweet_spot_profit, activation_time_ms, is_profit_locking=True)
                                                return True, f"ProfitLockingEngine: {profit_locking_reason}"
                                        except Exception as e:
                                            logger.error(f"Error updating tracking after ProfitLockingEngine: {e}", exc_info=True)
                                            # Still return success since SL was updated
                                            return True, f"ProfitLockingEngine: {profit_locking_reason}"
                                    else:
                                        # Lock failed but SL was updated - return success
                                        return True, f"ProfitLockingEngine: {profit_locking_reason}"
                            else:
                                logger.debug(f"PROFIT LOCKING ENGINE SKIPPED: {symbol} Ticket {ticket} | {profit_locking_reason} | Falling back to internal logic")
                        except Exception as e:
                            logger.warning(f"ProfitLockingEngine error: {e} | Falling back to internal sweet-spot logic", exc_info=True)
                
                # Fallback to internal sweet spot logic if ProfitLockingEngine didn't succeed
                # Now apply sweet spot SL OUTSIDE the lock (all network calls happen here)
                if sweet_spot_position is not None:
                    success, reason, target_sl = self._apply_sweet_spot_lock(sweet_spot_position, sweet_spot_profit)
                else:
                    # Not in sweet spot range, skip
                    success = False
                    reason = "Not in sweet spot range"
                    target_sl = None
            else:
                # Sweet spot position was None - initialize variables to prevent UnboundLocalError
                success = False
                reason = "Not in sweet spot range"
                target_sl = None
            
            # Re-acquire lock briefly to update tracking
            lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=True)
            if lock_acquired:
                try:
                    with lock:
                        if success:
                            self._sl_update_rate_limit[ticket] = current_time
                            # Update profit zone tracking
                            with self._tracking_lock:
                                if ticket in self._profit_zone_entry:
                                    self._profit_zone_entry[ticket]['sl_updated'] = True
                                    self._profit_zone_entry[ticket]['last_update_reason'] = f"Sweet spot: {reason}"
                            logger.info(f"SWEET SPOT APPLIED: {symbol} Ticket {ticket} | {reason}")
                            # Log sweet spot locked event
                            system_event_logger.systemEvent("SWEET_SPOT_LOCKED", {
                                "ticket": ticket,
                                "symbol": symbol,
                                "sl": target_sl,
                                "profit": sweet_spot_profit
                            })
                            # Track metrics: calculate activation time if first entry
                            activation_time_ms = None
                            if is_first_sweet_spot_entry:
                                activation_time_ms = (time.time() - profit_locking_start_time) * 1000
                            self._track_update_metrics(ticket, symbol, True, reason, sweet_spot_profit,
                                                      activation_time_ms, is_profit_locking=True)
                            tracer.trace(
                                function_name="SLManager.update_sl_atomic",
                                expected=f"Lock sweet spot profit for {symbol} Ticket {ticket}",
                                actual=f"Sweet spot lock applied successfully",
                                status="OK",
                                ticket=ticket,
                                symbol=symbol,
                                reason=reason
                            )
                            return True, reason
                        else:
                            # Update profit zone tracking with failure
                            with self._tracking_lock:
                                if ticket in self._profit_zone_entry:
                                    self._profit_zone_entry[ticket]['last_update_reason'] = f"Sweet spot failed: {reason}"
                            logger.warning(f"[WARNING] SWEET SPOT FAILED: {symbol} Ticket {ticket} | {reason}")
                            # Track metrics for sweet spot failure
                            self._track_update_metrics(ticket, symbol, False, reason, sweet_spot_profit,
                                                      None, is_profit_locking=True)
                            tracer.trace(
                                function_name="SLManager.update_sl_atomic",
                                expected=f"Lock sweet spot profit for {symbol} Ticket {ticket}",
                                actual=f"Sweet spot lock application failed: {reason}",
                                status="WARNING",
                                ticket=ticket,
                                symbol=symbol,
                                reason=reason
                            )
                except Exception as e:
                    logger.error(f"Error updating tracking after sweet spot SL: {e}", exc_info=True)
            else:
                # Lock re-acquisition failed - log but don't fail the SL update
                logger.warning(f"[WARNING] Could not re-acquire lock for tracking update: {symbol} Ticket {ticket}, but SL update may have succeeded")
                # CRITICAL: Ensure success is defined before checking it
                if 'success' in locals() and success:
                    return True, reason
                # If success is not defined, log error and continue to trailing stop logic
                logger.error(f"[ERROR] Lock re-acquisition failed and success variable not defined: {symbol} Ticket {ticket}")
            
            # If we get here, sweet spot failed or wasn't applicable - check trailing stop
            # Get fresh position data for trailing check
            fresh_position_trailing = self.order_manager.get_position_by_ticket(ticket)
            if fresh_position_trailing:
                trailing_profit_check = fresh_position_trailing.get('profit', 0.0)
            else:
                trailing_profit_check = sweet_spot_profit if 'sweet_spot_profit' in locals() else current_profit
            
            # PRIORITY 4: Trailing stop if profit > $0.10
            if trailing_profit_check > self.trailing_increment_usd:
                # Check if fast trailing threshold reached
                fast_trailing_threshold = self.risk_config.get('fast_trailing_threshold_usd', 0.10)
                is_fast_trailing = trailing_profit_check >= fast_trailing_threshold
                
                tracer.trace(
                    function_name="SLManager.update_sl_atomic",
                    expected=f"Apply trailing stop for {symbol} Ticket {ticket}",
                    actual=f"Checking trailing stop (profit: ${trailing_profit_check:.2f})",
                    status="OK",
                    ticket=ticket,
                    symbol=symbol,
                    profit=trailing_profit_check,
                    priority="TRAILING"
                )
                # Use fresh position if available, otherwise use cached
                trailing_position = fresh_position_trailing if fresh_position_trailing else (sweet_spot_position if 'sweet_spot_position' in locals() else position)
                
                # Apply trailing stop OUTSIDE the lock (all network calls happen here)
                success, reason, target_sl = self._apply_trailing_stop(trailing_position, trailing_profit_check)
                
                # Re-acquire lock briefly to update tracking
                lock_acquired, lock, lock_reason = self._acquire_ticket_lock_with_timeout(ticket, is_profit_locking=True)
                if lock_acquired:
                    try:
                        with lock:
                            if success:
                                self._sl_update_rate_limit[ticket] = current_time
                                # Update profit zone tracking
                                with self._tracking_lock:
                                    if ticket in self._profit_zone_entry:
                                        self._profit_zone_entry[ticket]['sl_updated'] = True
                                        self._profit_zone_entry[ticket]['last_update_reason'] = f"Trailing: {reason}"
                                # Log trailing executed event
                                if is_fast_trailing:
                                    system_event_logger.systemEvent("FAST_TRAILING_EXECUTED", {
                                        "symbol": symbol,
                                        "ticket": ticket,
                                        "oldSL": trailing_position.get('sl', 0.0),
                                        "newSL": target_sl,
                                        "profitUSD": trailing_profit_check,
                                        "reason": "fast_trailing"
                                    })
                                else:
                                    system_event_logger.systemEvent("TRAILING_EXECUTED", {
                                        "symbol": symbol,
                                        "ticket": ticket,
                                        "oldSL": trailing_position.get('sl', 0.0),
                                        "newSL": target_sl,
                                        "profitUSD": trailing_profit_check,
                                        "reason": "trailing"
                                    })
                                # Track metrics for trailing stop
                                self._track_update_metrics(ticket, symbol, True, reason, trailing_profit_check,
                                                          None, is_profit_locking=(trailing_profit_check > 0))
                                tracer.trace(
                                    function_name="SLManager.update_sl_atomic",
                                    expected=f"Apply trailing stop for {symbol} Ticket {ticket}",
                                    actual=f"Trailing stop applied successfully",
                                    status="OK",
                                    ticket=ticket,
                                    symbol=symbol,
                                    reason=reason
                                )
                                return True, reason
                            else:
                                # Update profit zone tracking with failure
                                with self._tracking_lock:
                                    if ticket in self._profit_zone_entry:
                                        self._profit_zone_entry[ticket]['last_update_reason'] = f"Trailing failed: {reason}"
                                logger.warning(f"[WARNING] TRAILING STOP FAILED: {symbol} Ticket {ticket} | {reason}")
                                # Track metrics for trailing stop failure
                                self._track_update_metrics(ticket, symbol, False, reason, trailing_profit_check,
                                                          None, is_profit_locking=(trailing_profit_check > 0))
                                tracer.trace(
                                    function_name="SLManager.update_sl_atomic",
                                    expected=f"Apply trailing stop for {symbol} Ticket {ticket}",
                                    actual=f"Trailing stop application failed",
                                    status="WARNING",
                                    ticket=ticket,
                                    symbol=symbol,
                                    reason=reason
                                )
                    except Exception as e:
                        logger.error(f"Error updating tracking after trailing stop: {e}", exc_info=True)
                else:
                    # Lock re-acquisition failed - log but don't fail the SL update
                    logger.warning(f"[WARNING] Could not re-acquire lock for tracking update: {symbol} Ticket {ticket}, but SL update may have succeeded")
                    if success:
                        return True, reason
            
            # Update rate limit even if no update was needed (to prevent false rate limiting)
            self._sl_update_rate_limit[ticket] = current_time
            
            # CRITICAL FIX: Update _last_sl_success and _last_sl_reason when no update is needed (SL is already correct)
            # This ensures the logger shows "[OK]" instead of "[W]" when SL is already at the correct value
            # AND ensures _last_sl_reason is always set (not "N/A")
            reason = "No SL update needed (all conditions checked)"
            with self._tracking_lock:
                self._last_sl_success[ticket] = datetime.now()
                self._last_sl_attempt[ticket] = datetime.now()
                self._last_sl_reason[ticket] = reason  # CRITICAL: Always set reason, even when no update needed
            
            # Track metrics for "no update needed" case
            self._track_update_metrics(ticket, symbol, False, "No update needed", current_profit)
            
            # No update needed - get fresh profit for logging
            final_position = self.order_manager.get_position_by_ticket(ticket)
            final_profit = final_position.get('profit', 0.0) if final_position else 0.0
            tracer.trace(
                function_name="SLManager.update_sl_atomic",
                expected=f"Update SL for {symbol} Ticket {ticket}",
                actual=f"No SL update needed (all conditions checked)",
                status="OK",
                ticket=ticket,
                symbol=symbol,
                profit=final_profit
            )
            return False, reason
        
        except Exception as e:
            reason = f"Exception: {e}"
            logger.error(f"Error in update_sl_atomic for {symbol} Ticket {ticket}: {e}", exc_info=True)
            # CRITICAL: Set _last_sl_reason even on exception
            with self._tracking_lock:
                self._last_sl_reason[ticket] = reason
            tracer.trace(
                function_name="SLManager.update_sl_atomic",
                expected=f"Update SL for {symbol} Ticket {ticket}",
                actual=f"Exception occurred during SL update",
                status="ERROR",
                ticket=ticket,
                symbol=symbol,
                reason=str(e),
                exception_type=type(e).__name__
            )
            return False, reason
        finally:
            # Always clear lock tracking when done
            with self._locks_lock:
                if ticket in self._lock_hold_times:
                    del self._lock_hold_times[ticket]
    
    def _calculate_effective_sl_profit(self, entry_price: float, sl_price: float,
                                      order_type: str, lot_size: float, contract_size: float,
                                      symbol_info: Optional[Dict[str, Any]] = None) -> float:
        """
        Calculate effective SL profit from individual parameters (helper for testing).
        
        This is a private helper method used primarily for testing. The public API
        is get_effective_sl_profit(position) which takes a position dict.
        
        Args:
            entry_price: Entry price of the position
            sl_price: Stop loss price
            order_type: 'BUY' or 'SELL'
            lot_size: Lot size
            contract_size: Contract size (corrected)
            symbol_info: Optional symbol info (for indices point_value calculation)
        
        Returns:
            Effective SL as profit/loss in USD (negative for loss, positive for profit)
        """
        if sl_price <= 0 or entry_price <= 0:
            # No SL set - assume -$2.00 loss protection
            return -self.max_risk_usd
        
        # CRITICAL FIX: For indices and crypto, use point_value if available
        if symbol_info:
            point_value = symbol_info.get('trade_tick_value', None)
            point = symbol_info.get('point', 0.00001)
            
            # Detect if this is likely a crypto or index symbol
            is_crypto_or_index = (point >= 0.01) or (point < 0.0001 and entry_price > 100)
            
            if point_value and point_value > 0:
                # Index/crypto calculation: use point_value
                if order_type == 'BUY':
                    # For BUY: SL below entry = loss, SL above entry = profit
                    price_diff = entry_price - sl_price  # Negative if SL above entry (profit)
                else:  # SELL
                    # For SELL: SL above entry = profit, SL below entry = loss
                    price_diff = sl_price - entry_price  # Positive if SL above entry (profit)
                
                # Convert to points
                price_diff_points = price_diff / point if point > 0 else 0
                
                # CRITICAL FIX: Profit calculation - negative sign is correct for BUY (SL below = loss)
                # But for SELL, when SL is above entry (positive price_diff), profit should be positive
                # For BUY: price_diff negative when SL above (profit) -> need to negate to get positive profit
                # For SELL: price_diff positive when SL above (profit) -> need to negate to get... wait, that's wrong!
                # Actually: profit = -price_diff_points * lot_size * point_value
                # For BUY: price_diff negative (SL above) -> profit positive [OK]
                # For SELL: price_diff positive (SL above) -> profit negative ✗ WRONG!
                # FIX: For SELL, we need to negate the sign
                if order_type == 'BUY':
                    profit = -price_diff_points * lot_size * point_value
                else:  # SELL
                    profit = price_diff_points * lot_size * point_value  # Positive when SL above entry
                return profit
            elif is_crypto_or_index:
                # Crypto/index but no point_value - use contract_size (which should be corrected)
                # For crypto, profit = price_diff * lot_size * contract_size
                if order_type == 'BUY':
                    price_diff = entry_price - sl_price  # Negative if SL above entry (profit)
                    profit = -price_diff * lot_size * contract_size
                else:  # SELL
                    price_diff = sl_price - entry_price  # Positive if SL above entry (profit)
                    profit = price_diff * lot_size * contract_size  # Positive when SL above entry
                return profit
        
        # Forex calculation: use contract_size
        # CRITICAL FIX: For forex, contract_size should typically be 100000 (standard lot)
        # But we use corrected_contract_size which may have been adjusted
        # Calculate profit/loss if SL hits
        if order_type == 'BUY':
            # For BUY: SL triggers when BID reaches SL
            # Entry is at ASK, SL triggers at BID
            # Loss = (entry_ask - sl_bid) * lot * contract
            # If SL above entry, price_diff is negative, profit should be positive
            price_diff = entry_price - sl_price  # Negative if SL above entry (profit)
            profit = -price_diff * lot_size * contract_size  # Negate to get positive profit
        else:  # SELL
            # For SELL: SL triggers when ASK reaches SL
            # Entry is at BID, SL triggers at ASK
            # Loss = (sl_ask - entry_bid) * lot * contract
            # If SL above entry, price_diff is positive, profit should be positive
            price_diff = sl_price - entry_price  # Positive if SL above entry (profit)
            profit = price_diff * lot_size * contract_size  # Positive when SL above entry
        
        return profit
    
    def get_effective_sl_profit(self, position: Dict[str, Any]) -> float:
        """
        Calculate effective SL in USD profit terms.
        
        Returns:
            Effective SL as profit/loss in USD (negative for loss, positive for profit)
        """
        symbol = position.get('symbol', '')
        entry_price = position.get('price_open', 0.0)
        sl_price = position.get('sl', 0.0)
        order_type = position.get('type', '')
        lot_size = position.get('volume', 0.01)
        
        if sl_price <= 0 or entry_price <= 0:
            # No SL set - return a very negative value to indicate no protection
            # This ensures the system knows an SL needs to be set
            # Use a value much worse than max_risk to force SL application
            # CRITICAL: Never return 0.0 - always return a meaningful value
            return -999.0  # Very negative to indicate no SL protection
        
        # Get symbol info
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            # CRITICAL: Never return 0.0 - return a meaningful default
            # If we can't calculate, assume worst case (no protection)
            return -self.max_risk_usd  # Default fallback
        
        # CRITICAL FIX: Correct entry price for BUY orders (use ASK instead of BID)
        # This matches the logic in _calculate_target_sl_price() to ensure consistent calculations
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick:
            current_bid = tick.bid
            current_ask = tick.ask
            
            if order_type == 'BUY':
                # For BUY, if entry_price is close to BID, use ASK instead
                if abs(entry_price - current_bid) < abs(entry_price - current_ask):
                    # Entry is closer to BID, likely MT5 gave us BID, but we need ASK
                    effective_entry = entry_price + (current_ask - current_bid) if current_ask > current_bid else entry_price
                else:
                    effective_entry = entry_price
            else:  # SELL
                effective_entry = entry_price
        else:
            effective_entry = entry_price
        
        # CRITICAL FIX: Get corrected contract_size FIRST - this is essential for crypto/index symbols
        # The raw contract_size from symbol_info might be wrong (1.0 instead of 1000.0)
        # CRITICAL: Use effective_entry (corrected for BUY) instead of entry_price
        test_loss = abs(effective_entry - sl_price) * lot_size * symbol_info.get('contract_size', 1.0)
        corrected_contract_size = self._get_corrected_contract_size(symbol, effective_entry, lot_size, max(test_loss, self.max_risk_usd), position=position)
        
        point_value = symbol_info.get('trade_tick_value', None)
        point = symbol_info.get('point', 0.00001)
        
        # Detect if this is likely a crypto, index, or commodity symbol
        # CRITICAL: Also check if trade_tick_value exists - commodities like USOILm use it
        is_crypto_or_index = (point >= 0.01) or (point < 0.0001 and entry_price > 100) or (point_value is not None and point_value > 0)
        
        # For indices/crypto/commodities, try point_value calculation first
        # CRITICAL: Use trade_tick_value if available (regardless of symbol type)
        if point_value and point_value > 0:
            # Index/crypto calculation: use point_value
            # Calculate price difference in points
            # CRITICAL FIX: Use effective_entry (corrected for BUY) instead of entry_price
            # For consistency, use effective_entry for both BUY and SELL (it equals entry_price for SELL)
            if order_type == 'BUY':
                price_diff = effective_entry - sl_price
            else:  # SELL
                price_diff = sl_price - effective_entry
                
            # Convert to points
            price_diff_points = price_diff / point if point > 0 else 0
            
            # Profit = price_diff_points * lot_size * point_value
            profit = -price_diff_points * lot_size * point_value
            
            # CRITICAL: Verify the calculated profit is reasonable
            # If it's way off from target (-$2.00), the point_value might be wrong
            # Try reverse-engineering from current profit if available
            if abs(profit) < 0.10 or abs(profit) > 10.0:  # Suspiciously small or large
                current_profit = position.get('profit', None)
                current_price = position.get('price_current', 0.0)
                
                if current_profit is not None and current_price > 0:
                    # Calculate what point_value should be from current profit
                    # CRITICAL FIX: Use effective_entry (corrected for BUY) instead of entry_price
                    if order_type == 'BUY':
                        current_price_diff = current_price - effective_entry
                    else:  # SELL
                        current_price_diff = entry_price - current_price
                    
                    current_price_diff_points = current_price_diff / point if point > 0 else 0
                    
                    if current_price_diff_points != 0 and lot_size > 0:
                        # Reverse-engineer point_value: current_profit = current_price_diff_points * lot_size * point_value
                        reverse_point_value = current_profit / (current_price_diff_points * lot_size)
                        
                        # If reverse-engineered point_value is reasonable (0.1 to 100), use it
                        if 0.1 <= abs(reverse_point_value) <= 100:
                            # Recalculate with reverse-engineered point_value
                            profit = -price_diff_points * lot_size * reverse_point_value
                            logger.debug(f"🔧 POINT_VALUE REVERSE-ENGINEERED: {symbol} | "
                                       f"Original: {point_value} | Reverse: {reverse_point_value:.4f} | "
                                       f"Current profit: ${current_profit:.2f}")
            
            return profit
        elif is_crypto_or_index:
            # Crypto/index but no point_value - use corrected contract_size
            # CRITICAL: For crypto like BTCXAUm, ALWAYS reverse-engineer from current profit if available
            # This ensures accurate calculation regardless of broker-reported contract_size
            # CRITICAL FIX: Use effective_entry (corrected for BUY) instead of entry_price
            if order_type == 'BUY':
                price_diff = effective_entry - sl_price
            else:  # SELL
                price_diff = sl_price - entry_price
            
            # CRITICAL FIX: Always try to reverse-engineer from current profit first
            # This is the most reliable method for crypto symbols
            # CRITICAL FIX: Use effective_entry (corrected for BUY) instead of entry_price
            current_profit = position.get('profit', None)
            current_price = position.get('price_current', 0.0)
            
            if current_profit is not None and current_price > 0:
                # Calculate what contract_size would give us the current profit
                # CRITICAL FIX: Use effective_entry (corrected for BUY) instead of entry_price
                if order_type == 'BUY':
                    current_price_diff = current_price - effective_entry
                else:  # SELL
                    current_price_diff = entry_price - current_price
                
                if abs(current_price_diff) > 0.00001 and lot_size > 0:  # Avoid division by zero
                    # Reverse-engineer: current_profit = current_price_diff * lot_size * effective_contract_size
                    effective_contract_size = abs(current_profit) / (abs(current_price_diff) * lot_size)
                    
                    # If this gives a reasonable value (between 0.1 and 1000000), use it
                    # This covers all possible contract sizes from micro to standard
                    if 0.1 <= effective_contract_size <= 1000000:
                        # Use this for SL calculation
                        profit = -price_diff * lot_size * effective_contract_size
                        logger.info(f"🔧 CRYPTO CONTRACT_SIZE REVERSE-ENGINEERED: {symbol} | "
                                   f"From current profit: {effective_contract_size:.2f} | "
                                   f"Current profit: ${current_profit:.2f} | "
                                   f"Price diff: {current_price_diff:.5f} | "
                                   f"Calculated SL profit: ${profit:.2f}")
                        return profit
            
            # Fallback: Use corrected contract_size for calculation
            # But log a warning if we're using fallback
            if corrected_contract_size == 1.0 or corrected_contract_size > 100000:
                logger.warning(f"[WARNING] CRYPTO CONTRACT_SIZE FALLBACK: {symbol} | "
                             f"Using corrected size: {corrected_contract_size} | "
                             f"This may result in incorrect SL calculation. "
                             f"Current profit not available for reverse-engineering.")
            
            profit = -price_diff * lot_size * corrected_contract_size
            return profit
        
        # Forex calculation: use corrected contract_size
        # Use helper method for calculation (pass symbol_info for indices)
        return self._calculate_effective_sl_profit(entry_price, sl_price, order_type, lot_size, corrected_contract_size, symbol_info)
    
    def fail_safe_check(self):
        """
        Fail-safe check: Verify all negative P/L trades have SL ≤ -$2.00.
        
        Runs periodically to ensure strict loss limit is never violated.
        CRITICAL: If effective SL is worse than -$2.00, immediately force correction.
        Includes error debouncing to prevent log spam from repeated errors.
        """
        try:
            positions = self.order_manager.get_open_positions()
            
            for position in positions:
                current_profit = position.get('profit', 0.0)
                
                # Only check losing trades
                if current_profit >= 0:
                    continue
                
                ticket = position.get('ticket', 0)
                symbol = position.get('symbol', '')
                
                # Get effective SL
                effective_sl_profit = self.get_effective_sl_profit(position)
                
                # CRITICAL: Check if SL is worse than -$2.00 (more negative)
                # If so, IMMEDIATELY force strict loss enforcement with retries
                if effective_sl_profit < -self.max_risk_usd:
                    violation_amount = abs(effective_sl_profit - (-self.max_risk_usd))
                    logger.critical(f"🚨 CRITICAL FAIL-SAFE VIOLATION: {symbol} Ticket {ticket} | "
                                  f"Current P/L: ${current_profit:.2f} | "
                                  f"Effective SL: ${effective_sl_profit:.2f} | "
                                  f"Exceeds limit: ${-self.max_risk_usd:.2f} by ${violation_amount:.2f} | "
                                  f"FORCING IMMEDIATE CORRECTION")
                    
                    # CRITICAL: Force strict loss enforcement with immediate retries
                    # Try up to 3 times immediately to correct the violation
                    max_retries = 3
                    for attempt in range(max_retries):
                        success, reason, _ = self._enforce_strict_loss_limit(position)
                        if success:
                            logger.info(f"[OK] FAIL-SAFE CORRECTION SUCCESS: {symbol} Ticket {ticket} | "
                                      f"Attempt {attempt + 1}/{max_retries} | Effective SL corrected to -$2.00")
                            break
                        else:
                            if attempt < max_retries - 1:
                                logger.warning(f"[WARNING] FAIL-SAFE RETRY: {symbol} Ticket {ticket} | "
                                             f"Attempt {attempt + 1}/{max_retries} failed: {reason} | Retrying immediately...")
                                time.sleep(0.2)  # Brief delay before retry
                            else:
                                logger.critical(f"🚨 FAIL-SAFE CORRECTION FAILED: {symbol} Ticket {ticket} | "
                                              f"All {max_retries} attempts failed: {reason} | "
                                              f"Will retry in next cycle")
        
        except Exception as e:
            # Error debouncing: Only log same error once per throttle window (1 second)
            error_signature = f"{type(e).__name__}:{str(e)[:100]}"  # Truncate long messages
            current_time = time.time()
            
            # Check if we should throttle this error
            should_log = True
            if error_signature in self._fail_safe_error_throttle:
                time_since_last = current_time - self._fail_safe_error_throttle[error_signature]
                if time_since_last < self._fail_safe_throttle_window:
                    # Same error within throttle window - don't log again
                    should_log = False
                    logger.debug(f"Fail-safe error throttled: {error_signature} (last logged {time_since_last:.1f}s ago)")
            
            if should_log:
                logger.error(f"Error in fail-safe check: {e}", exc_info=True)
                self._fail_safe_error_throttle[error_signature] = current_time
                
                # Clean up old throttle entries (older than 2x throttle window)
                cutoff_time = current_time - (self._fail_safe_throttle_window * 2)
                self._fail_safe_error_throttle = {
                    sig: t for sig, t in self._fail_safe_error_throttle.items()
                    if t > cutoff_time
                }
    
    def cleanup_closed_position(self, ticket: int):
        """
        Clean up tracking data for a closed position.
        
        Args:
            ticket: Position ticket number
        """
        with self._tracking_lock:
            if ticket in self._position_tracking:
                del self._position_tracking[ticket]
            if ticket in self._last_sl_update:
                del self._last_sl_update[ticket]
            if ticket in self._last_sl_price:
                del self._last_sl_price[ticket]
            if ticket in self._last_sl_reason:
                del self._last_sl_reason[ticket]
            # Clean up first eligible update tracking
            if ticket in self._first_eligible_update:
                del self._first_eligible_update[ticket]
            # Clean up profit zone entry tracking
            if ticket in self._profit_zone_entry:
                entry_data = self._profit_zone_entry[ticket]
                entry_duration = (datetime.now() - entry_data['entry_time']).total_seconds()
                duration_str = f"{int(entry_duration // 60)}m {int(entry_duration % 60)}s"
                logger.info(f"📊 PROFIT ZONE EXIT (Position Closed): {entry_data['symbol']} Ticket {ticket} | "
                          f"Duration in profit: {duration_str} | "
                          f"SL Updated: {entry_data['sl_updated']} | "
                          f"Attempts: {entry_data['update_attempts']} | "
                          f"Last Reason: {entry_data.get('last_update_reason', 'N/A')}")
                del self._profit_zone_entry[ticket]
        
        with self._locks_lock:
            if ticket in self._ticket_locks:
                del self._ticket_locks[ticket]
    
    def is_sl_verified(self, position: Dict[str, Any]) -> bool:
        """
        Check if SL is verified (applied and confirmed by broker).
        
        Returns:
            True if SL is set and verified, False otherwise
        """
        sl_price = position.get('sl', 0.0)
        if sl_price <= 0:
            return False
        
        ticket = position.get('ticket', 0)
        with self._tracking_lock:
            # SL is verified if we have a recent update record
            if ticket in self._last_sl_update:
                return True
        
        return False
    
    def start_sl_worker(self, watchdog=None):
        """
        Start the real-time SL worker thread (250ms cadence).
        
        Args:
            watchdog: Optional SLWatchdog instance for monitoring
        """
        # CRITICAL FIX: Check both flag and thread state to prevent duplicate starts
        if self._sl_worker_running:
            if self._sl_worker_thread and self._sl_worker_thread.is_alive():
                logger.warning("SL worker already running (thread is alive)")
                return
            else:
                # Flag is set but thread is dead - reset flag and continue
                logger.warning("SL worker flag set but thread is dead - resetting and restarting")
                self._sl_worker_running = False
        
        self._watchdog = watchdog
        self._sl_worker_running = True
        self._sl_worker_shutdown_event.clear()
        self._sl_worker_thread = threading.Thread(
            target=self._sl_worker_loop,
            name="SLWorker",
            daemon=True
        )
        
        # Register SLWorker with global system health monitor (for timer-based heartbeat)
        try:
            system_health.register_critical_thread(
                "SLWorker",
                self._sl_worker_thread,
                metrics_provider=self._get_sl_worker_metrics,
            )
        except Exception:
            # Health tracking must never prevent the worker from starting
            pass
        
        # OPTIMIZATION: Start background worker thread for heavy operations
        self._background_worker_running = True
        self._background_worker_shutdown_event.clear()
        self._background_worker_thread = threading.Thread(
            target=self._background_worker_loop,
            name="SLBackgroundWorker",
            daemon=True
        )
        self._background_worker_thread.start()
        import os
        pid = os.getpid()
        bg_tid = self._background_worker_thread.ident if self._background_worker_thread.ident else 'unknown'
        logger.info(f"[THREAD_START] SLBackgroundWorker pid={pid} tid={bg_tid}")
        logger.info("[OK] Background worker thread started for heavy operations")
        
        logger.info("SLManager worker loop starting now...")
        
        # MANDATORY OBSERVABILITY: Log thread start with thread ID and process ID
        import os
        pid = os.getpid()
        self._sl_worker_thread.start()
        tid = self._sl_worker_thread.ident if self._sl_worker_thread.ident else 'unknown'
        logger.info(f"[THREAD_START] SLWorker pid={pid} tid={tid}")
        logger.info(f"[OK][SL_WORKER_STARTED] polling_interval={self._sl_worker_interval*1000:.0f}ms")
        logger.info("[OK] SLManager worker thread started successfully")
    
    def stop_sl_worker(self):
        """Stop the real-time SL worker thread."""
        if not self._sl_worker_running:
            return
        
        self._sl_worker_running = False
        self._sl_worker_shutdown_event.set()
        
        # OPTIMIZATION: Stop background worker thread
        self._background_worker_running = False
        self._background_worker_shutdown_event.set()
        
        if self._sl_worker_thread and self._sl_worker_thread.is_alive():
            self._sl_worker_thread.join(timeout=2.0)
        
        if self._background_worker_thread and self._background_worker_thread.is_alive():
            self._background_worker_thread.join(timeout=2.0)
        
        # Close logging files
        self._close_logging_files()
        
        import os
        pid = os.getpid()
        tid = self._sl_worker_thread.ident if self._sl_worker_thread and self._sl_worker_thread.ident else 'unknown'
        logger.info(f"[THREAD_STOP] SLWorker pid={pid} tid={tid} reason=shutdown_requested")
        logger.info("SL Worker stopped")
    
    def _close_logging_files(self):
        """Close structured log and CSV summary files."""
        try:
            if self._structured_log_file:
                with self._structured_log_lock:
                    self._structured_log_file.close()
                    self._structured_log_file = None
        except Exception as e:
            logger.debug(f"Error closing structured log: {e}")
        
        try:
            if self._csv_summary_file:
                with self._csv_summary_lock:
                    self._csv_summary_file.close()
                    self._csv_summary_file = None
                    self._csv_summary_writer = None
        except Exception as e:
            logger.debug(f"Error closing CSV summary: {e}")
    
    def _background_worker_loop(self):
        """
        Background worker loop for heavy operations.
        
        This loop processes tasks queued from the main worker loop:
        - Fail-safe checks (scan all positions)
        - Stale lock checks
        - CSV batch writing
        - Other heavy operations
        
        This allows the main loop to stay under 50ms by offloading blocking operations.
        """
        logger.info("Background worker loop started")
        
        csv_batch = []
        last_csv_flush = time.time()
        
        while self._background_worker_running and not self._background_worker_shutdown_event.is_set():
            try:
                # Process background tasks with timeout to allow periodic CSV flushing
                try:
                    task_type, task_data = self._background_task_queue.get(timeout=0.1)
                    
                    if task_type == 'fail_safe_check':
                        # OPTIMIZATION: Fail-safe check moved to background
                        # This can scan all positions and perform heavy calculations
                        try:
                            self.fail_safe_check()
                        except Exception as e:
                            logger.error(f"Error in background fail-safe check: {e}", exc_info=True)
                    
                    elif task_type == 'check_stale_locks':
                        # OPTIMIZATION: Stale lock check moved to background
                        # This can be slow when many locks exist
                        try:
                            self._check_stale_locks()
                        except Exception as e:
                            logger.debug(f"Error in background stale lock check: {e}")
                    
                    elif task_type == 'micro_profit_check':
                        # OPTIMIZATION: MicroProfitEngine check moved to background
                        # This involves position retrieval and profit calculations
                        try:
                            ticket = task_data['ticket']
                            micro_profit_engine = task_data['micro_profit_engine']
                            logger.debug(f"🔍 MicroProfitEngine: Checking position {ticket} for sweet spot closure (background)")
                            # Get fresh position after SL update
                            fresh_position_for_micro = self.order_manager.get_position_by_ticket(ticket)
                            if fresh_position_for_micro:
                                current_profit = fresh_position_for_micro.get('profit', 0.0)
                                logger.debug(f"🔍 MicroProfitEngine: Position {ticket} profit=${current_profit:.2f}, checking sweet spot range")
                                # Check if position should be closed (sweet spot profit taking)
                                was_closed = micro_profit_engine.check_and_close(fresh_position_for_micro, self.mt5_connector)
                                if was_closed:
                                    logger.info(f"[OK] MicroProfitEngine closed position {ticket} in sweet spot after SL update")
                                else:
                                    logger.debug(f"🔍 MicroProfitEngine: Position {ticket} not closed (not in sweet spot or other reason)")
                            else:
                                logger.debug(f"[WARNING] MicroProfitEngine: Could not get fresh position {ticket}")
                        except Exception as e:
                            logger.warning(f"[WARNING] MicroProfitEngine error in background: {e}", exc_info=True)
                    
                    self._background_task_queue.task_done()
                    
                except queue.Empty:
                    # No tasks - continue to CSV batch processing
                    pass
                
                # OPTIMIZATION: Batch CSV writes to reduce I/O overhead
                # Collect CSV writes and flush in batches
                current_time = time.time()
                should_flush = False
                
                # Flush if batch is full or timeout reached
                if len(csv_batch) >= self._csv_batch_size:
                    should_flush = True
                elif csv_batch and (current_time - last_csv_flush) >= self._csv_batch_timeout:
                    should_flush = True
                
                # Try to get more CSV writes (non-blocking)
                while len(csv_batch) < self._csv_batch_size:
                    try:
                        csv_entry = self._csv_write_queue.get_nowait()
                        csv_batch.append(csv_entry)
                    except queue.Empty:
                        break
                
                # Flush CSV batch if needed
                if should_flush and csv_batch:
                    try:
                        for entry in csv_batch:
                            self._write_csv_summary(
                                ticket=entry['ticket'],
                                symbol=entry['symbol'],
                                entry_price=entry['entry_price'],
                                current_price=entry['current_price'],
                                profit=entry['profit'],
                                target_sl=entry['target_sl'],
                                applied_sl=entry['applied_sl'],
                                effective_sl_profit=entry['effective_sl_profit'],
                                last_update_time=entry['last_update_time'],
                                last_update_result=entry['last_update_result'],
                                failure_reason=entry['failure_reason'],
                                consecutive_failures=entry['consecutive_failures']
                            )
                        csv_batch.clear()
                        last_csv_flush = current_time
                    except Exception as e:
                        logger.debug(f"Error flushing CSV batch: {e}")
                        csv_batch.clear()  # Clear on error to prevent memory growth
                
                # Small sleep to prevent CPU spinning
                time.sleep(0.01)  # 10ms
                
            except Exception as e:
                logger.error(f"Error in background worker loop: {e}", exc_info=True)
                time.sleep(0.1)  # Longer sleep on error
        
        # Flush remaining CSV entries
        if csv_batch:
            try:
                for entry in csv_batch:
                    self._write_csv_summary(
                        ticket=entry['ticket'],
                        symbol=entry['symbol'],
                        entry_price=entry['entry_price'],
                        current_price=entry['current_price'],
                        profit=entry['profit'],
                        target_sl=entry['target_sl'],
                        applied_sl=entry['applied_sl'],
                        effective_sl_profit=entry['effective_sl_profit'],
                        last_update_time=entry['last_update_time'],
                        last_update_result=entry['last_update_result'],
                        failure_reason=entry['failure_reason'],
                        consecutive_failures=entry['consecutive_failures']
                    )
            except Exception as e:
                logger.debug(f"Error flushing final CSV batch: {e}")
        
        logger.info("Background worker loop stopped")
    
    def _log_profit_zone_summary(self):
        """Log summary of all trades currently in profit zone."""
        with self._tracking_lock:
            if not self._profit_zone_entry:
                return
            
            current_time = datetime.now()
            summary_lines = []
            summary_lines.append("=" * 100)
            summary_lines.append(f"📊 PROFIT ZONE SUMMARY - {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
            summary_lines.append("=" * 100)
            
            for ticket, entry_data in self._profit_zone_entry.items():
                entry_time = entry_data['entry_time']
                duration = (current_time - entry_time).total_seconds()
                duration_str = f"{int(duration // 60)}m {int(duration % 60)}s"
                
                status = "[OK] SL UPDATED" if entry_data['sl_updated'] else "[ERROR] SL NOT UPDATED"
                attempts = entry_data['update_attempts']
                last_reason = entry_data.get('last_update_reason', 'N/A')
                
                summary_lines.append(
                    f"Ticket: {ticket} | Symbol: {entry_data['symbol']} | "
                    f"Entry Profit: ${entry_data['entry_profit']:.2f} | "
                    f"Duration: {duration_str} | "
                    f"{status} | "
                    f"Attempts: {attempts} | "
                    f"Last Reason: {last_reason}"
                )
            
            summary_lines.append("=" * 100)
            summary_text = "\n".join(summary_lines)
            logger.info(f"\n{summary_text}")
    
    def _sl_worker_loop(self):
        """
        Main SL worker loop - optimized for speed and non-blocking operation.
        
        Interval: Configurable via trailing_cycle_interval_ms (0ms for instant trailing)
        Target Performance: <10ms per iteration (ideal), <50ms (acceptable)
        
        Optimizations:
        1. Single position fetch per loop (get_open_positions() called once)
        2. Reuses position data (no duplicate get_position_by_ticket() calls)
        3. All heavy operations queued to background thread
        4. Minimal logging (only failures, slow updates, or periodic)
        5. Non-blocking lock acquisition with timeouts
        
        This loop:
        1. Collects snapshot of open positions (single MT5 API call)
        2. For each position, uses cached data (no additional API calls)
        3. Performs SL update calculations with full error handling
        4. Submits SL update with retry logic (network calls outside locks)
        5. Queues heavy operations to background thread
        """
        # MANDATORY OBSERVABILITY: Wrap entire loop in try-except to catch fatal crashes
        try:
            logger.info(f"SLManager worker loop started (interval: {self._sl_worker_interval*1000:.0f}ms)")
            logger.info("SLManager worker loop starting now")
            tracer = get_tracer()
            # Notify global health monitor that SLWorker has started
            try:
                system_health.mark_thread_started("SLWorker")
            except Exception:
                pass
            
            iteration = 0
            last_summary_time = time.time()
            summary_interval = 30.0  # Log summary every 30 seconds
            
            mode = "BACKTEST" if self.config.get('mode') == 'backtest' else "LIVE"
            
            while self._sl_worker_running and not self._sl_worker_shutdown_event.is_set():
                iteration += 1
                loop_start_time = time.time()
                loop_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                
                # Log profit zone summary periodically
                if time.time() - last_summary_time >= summary_interval:
                    self._log_profit_zone_summary()
                    # Also log verification metrics for system health monitoring
                    self._log_verification_metrics()
                    last_summary_time = time.time()
                
                tracer.trace(
                    function_name="SLManager._sl_worker_loop",
                    expected=f"Process all open positions in iteration {iteration}",
                    actual=f"Starting worker loop iteration {iteration}",
                    status="OK",
                    iteration=iteration
                )
                
                # OPTIMIZATION: Reduce debug logging noise - only log every 100 iterations or on slow loops
                should_log_debug = (iteration % 100 == 0) or (iteration <= 5)
                if should_log_debug:
                    logger.debug(f"mode={mode} | [{loop_timestamp}] [SL_WORKER] Loop iteration {iteration} started")
                    logger.info(f"mode={mode} | [SL_WORKER] Loop start timestamp: {loop_timestamp} | Iteration: {iteration}")
                    
                    # OPTIMIZATION: Get snapshot of open positions ONCE per loop
                    # This is the only blocking network call in the main loop
                    positions_fetch_start = time.time()
                    positions = self.order_manager.get_open_positions()
                    positions_fetch_duration = (time.time() - positions_fetch_start) * 1000
                    # Cache metrics for timer-based heartbeat (no MT5 calls from heartbeat thread)
                    position_count = len(positions) if positions else 0
                    with self._tracking_lock:
                        self._sl_worker_last_position_count = position_count
                        self._sl_worker_last_active_tickets = len(self._last_sl_attempt)
                    
                    # Warn if position fetch is slow (should be <10ms)
                    if positions_fetch_duration > 10:
                        logger.warning(f"mode={mode} | [{loop_timestamp}] [SL_WORKER] WARNING: Slow position fetch: {positions_fetch_duration:.1f}ms (target: <10ms)")
                    
                    tracer.trace(
                        function_name="SLManager._sl_worker_loop",
                        expected=f"Get all open positions for iteration {iteration}",
                        actual=f"Retrieved {len(positions)} open positions in {positions_fetch_duration:.1f}ms",
                        status="OK",
                        iteration=iteration,
                        position_count=len(positions),
                        fetch_duration_ms=positions_fetch_duration
                    )
                    
                    if not positions:
                        # MANDATORY OBSERVABILITY: Log idle state when no positions exist
                        logger.info(f"[IDLE][SL_WORKER] no_positions=true")
                        # No positions - check if instant trailing (no sleep) or wait
                        if self._sl_worker_interval > 0:
                            sleep_start = time.time()
                            time.sleep(self._sl_worker_interval)
                            sleep_duration = (time.time() - sleep_start) * 1000
                            logger.debug(f"mode={mode} | [SL_WORKER] Sleep duration: {sleep_duration:.1f}ms (target: {self._sl_worker_interval*1000:.1f}ms)")
                        # If instant trailing (interval = 0), continue immediately
                        continue
                    
                    if should_log_debug:
                        logger.debug(f"mode={mode} | [{loop_timestamp}] [SL_WORKER] Found {len(positions)} open position(s)")
                    
                    # OPTIMIZATION: Queue fail-safe check to background thread instead of blocking main loop
                    # Fail-safe check can scan all positions and perform heavy calculations
                    # Moving it to background ensures main loop stays under 50ms
                    try:
                        self._background_task_queue.put_nowait(('fail_safe_check', None))
                    except queue.Full:
                        # Queue full - skip this cycle's fail-safe check (will run next cycle)
                        logger.debug("Background task queue full, skipping fail-safe check this cycle")
                    except Exception as e:
                        logger.debug(f"Error queueing fail-safe check: {e}")
                    
                    # Process each position
                    for position in positions:
                        if not self._sl_worker_running:
                            break
                        
                        ticket = position.get('ticket', 0)
                        if ticket == 0:
                            continue
                        
                        # Skip if in manual review
                        if ticket in self._manual_review_tickets:
                            continue
                        
                        # OPTIMIZATION: Reduce debug logging noise - only log for first position or on errors
                        should_log_position = (ticket == positions[0].get('ticket', 0)) if positions else False
                        if should_log_position:
                            position_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                            logger.debug(f"[{position_timestamp}] 🔍 Processing {len(positions)} position(s), starting with Ticket {ticket}")
                        
                        # Check circuit breaker (but bypass for profit-locking trades)
                        # CRITICAL FIX: Circuit breaker should NOT block profit-locking trades
                        if ticket in self._ticket_circuit_breaker:
                            disabled_until = self._ticket_circuit_breaker[ticket]
                            current_profit_check = position.get('profit', 0.0)
                            is_profit_locking_check = (current_profit_check >= 0.01)
                            
                            # Bypass circuit breaker for profit-locking trades
                            if is_profit_locking_check:
                                logger.debug(f"🔄 Circuit breaker bypassed for profit-locking Ticket {ticket} (profit: ${current_profit_check:.2f})")
                                del self._ticket_circuit_breaker[ticket]
                            elif time.time() < disabled_until:
                                # Still in cooldown for non-profitable trades
                                continue
                            else:
                                # Cooldown expired, allow one trial update
                                del self._ticket_circuit_breaker[ticket]
                                logger.info(f"🔄 Circuit breaker expired for Ticket {ticket}, allowing trial update")
                        
                        # OPTIMIZATION: Queue stale lock check to background thread instead of blocking main loop
                        # Stale lock check can be slow when many locks exist
                        # Only check on first position to avoid duplicate checks
                        if len(positions) > 0 and ticket == positions[0].get('ticket', 0):
                            try:
                                self._background_task_queue.put_nowait(('check_stale_locks', None))
                            except queue.Full:
                                pass  # Skip if queue full
                            except Exception:
                                pass  # Ignore errors
                        
                        # CRITICAL OPTIMIZATION: Use position data from get_open_positions() instead of calling get_position_by_ticket()
                        # get_position_by_ticket() calls get_open_positions() again, causing duplicate MT5 API calls
                        # This eliminates N additional blocking network calls (where N = number of positions)
                        # The position data from get_open_positions() is already fresh and sufficient
                        fresh_position = position  # Use position from the list we already fetched
                        
                        # CRITICAL FIX: Determine if profitable before acquiring lock to use proper timeout
                        current_profit = fresh_position.get('profit', 0.0)
                        is_profit_locking = (current_profit >= 0.01)  # $0.01 threshold for profit locking priority
                        fresh_profit = current_profit
                        
                        # CRITICAL FIX: Don't acquire lock in worker loop - update_sl_atomic handles its own locking
                        # This prevents worker loop from blocking on lock acquisition
                        # update_sl_atomic will acquire locks internally only when needed and release them quickly
                        
                        # Perform SL update (atomic) with full error handling
                        # CRITICAL: update_sl_atomic will handle all network calls OUTSIDE locks
                        update_start = time.time()
                        # OPTIMIZATION: Only log debug for first position or if logging is enabled
                        if should_log_position:
                            update_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                            logger.debug(f"mode={mode} | [{update_timestamp}] [SL_WORKER] Starting SL update for Ticket {ticket}")
                        
                        # Track attempt timestamp
                        attempt_time = datetime.now()
                        with self._tracking_lock:
                            self._last_sl_attempt[ticket] = attempt_time
                        
                        try:
                            tracer.trace(
                                function_name="SLManager._sl_worker_loop",
                                expected=f"Update SL for {fresh_position.get('symbol', 'N/A')} Ticket {ticket}",
                                actual=f"Calling update_sl_atomic for Ticket {ticket}",
                                status="OK",
                                iteration=iteration,
                                ticket=ticket,
                                symbol=fresh_position.get('symbol', 'N/A'),
                                profit=fresh_position.get('profit', 0.0)
                            )
                            
                            # Call update_sl_atomic - it will acquire its own locks as needed
                            success, reason = self.update_sl_atomic(ticket, fresh_position)
                            update_duration = (time.time() - update_start) * 1000
                            logger.debug(f"mode={mode} | [SL_WORKER] SL update for Ticket {ticket} completed | "
                                       f"Duration: {update_duration:.1f}ms | Success: {success} | Reason: {reason}")
                            tracer.trace(
                                function_name="SLManager._sl_worker_loop",
                                expected=f"Update SL for {fresh_position.get('symbol', 'N/A')} Ticket {ticket}",
                                actual=f"update_sl_atomic returned: success={success}, reason={reason}, duration={update_duration:.1f}ms",
                                status="OK" if success else "WARNING",
                                iteration=iteration,
                                ticket=ticket,
                                symbol=fresh_position.get('symbol', 'N/A'),
                                success=success,
                                reason=reason
                            )
                            update_latency = (time.time() - update_start) * 1000  # Convert to ms
                            
                            # OPTIMIZATION: Only log slow updates (>20ms) or failures
                            should_log_update = (update_latency > 20) or not success
                            if should_log_update:
                                update_end_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                                logger.debug(f"[{update_end_timestamp}] [OK] SL update completed for Ticket {ticket} | Success: {success} | Latency: {update_latency:.1f}ms")
                            
                            # Track success/failure
                            with self._tracking_lock:
                                if success:
                                    self._last_sl_success[ticket] = attempt_time
                                    self._consecutive_failures[ticket] = 0  # Reset failure counter
                                else:
                                    self._consecutive_failures[ticket] += 1
                                    failures = self._consecutive_failures[ticket]
                                    
                                    # Circuit breaker: disable after 10 consecutive failures (but NOT for profit-locking trades)
                                    # CRITICAL FIX: Bypass circuit breaker for profit-locking trades to ensure SL updates always execute
                                    # Exponential cooldown: 10s, 30s, 60s, 120s based on failure bucket
                                    if failures >= self._circuit_breaker_threshold and not is_profit_locking:
                                        # Calculate exponential cooldown based on failure count
                                        failure_bucket = min((failures - self._circuit_breaker_threshold) // 5, 3)  # 0, 1, 2, 3
                                        cooldown = self._circuit_breaker_cooldown_base * (3 ** failure_bucket)  # 10, 30, 90, 270
                                        disabled_until = time.time() + cooldown
                                        self._ticket_circuit_breaker[ticket] = disabled_until
                                        logger.critical(f"🚨 CIRCUIT BREAKER: Ticket {ticket} disabled for {cooldown:.0f}s after {failures} consecutive failures (bucket: {failure_bucket})")
                                    elif failures >= self._circuit_breaker_threshold and is_profit_locking:
                                        logger.warning(f"[WARNING] Profit-locking Ticket {ticket} has {failures} failures, but circuit breaker bypassed for profit-locking trades")
                            
                            # OPTIMIZATION: Queue MicroProfitEngine check to background thread
                            # This involves position retrieval and profit calculations which can be slow
                            if hasattr(self, '_risk_manager') and self._risk_manager:
                                micro_profit_engine = getattr(self._risk_manager, '_micro_profit_engine', None)
                                if micro_profit_engine:
                                    try:
                                        # Queue to background instead of blocking main loop
                                        self._background_task_queue.put_nowait(('micro_profit_check', {
                                            'ticket': ticket,
                                            'micro_profit_engine': micro_profit_engine
                                        }))
                                    except queue.Full:
                                        logger.debug(f"Background queue full, skipping MicroProfitEngine check for ticket {ticket}")
                                    except Exception as e:
                                        logger.debug(f"Error queueing MicroProfitEngine check: {e}")
                        
                        except Exception as update_error:
                            update_error_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                            logger.error(f"[{update_error_timestamp}] SL update exception for Ticket {ticket}: {update_error}", exc_info=True)
                            success = False
                            reason = f"Exception: {str(update_error)}"
                            update_latency = (time.time() - update_start) * 1000
                            
                            # Track failure
                            with self._tracking_lock:
                                self._consecutive_failures[ticket] += 1
                                failures = self._consecutive_failures[ticket]
                                
                                # Circuit breaker: disable after 5 consecutive failures (but NOT for profit-locking trades)
                                # CRITICAL FIX: Bypass circuit breaker for profit-locking trades
                                if failures >= 5 and not is_profit_locking:
                                    disabled_until = time.time() + self._circuit_breaker_cooldown
                                    self._ticket_circuit_breaker[ticket] = disabled_until
                                    logger.critical(f"🚨 CIRCUIT BREAKER: Ticket {ticket} disabled for {self._circuit_breaker_cooldown}s after {failures} consecutive failures")
                                elif failures >= 5 and is_profit_locking:
                                    logger.warning(f"[WARNING] Profit-locking Ticket {ticket} has {failures} failures, but circuit breaker bypassed for profit-locking trades")
                            
                            # Track timing
                            with self._timing_lock:
                                if ticket not in self._timing_stats['ticket_update_times']:
                                    self._timing_stats['ticket_update_times'][ticket] = []
                                self._timing_stats['ticket_update_times'][ticket].append(update_latency)
                                # Keep only last 100 measurements per ticket
                                if len(self._timing_stats['ticket_update_times'][ticket]) > 100:
                                    self._timing_stats['ticket_update_times'][ticket].pop(0)
                                
                                self._timing_stats['update_counts'][ticket] += 1
                                self._timing_stats['last_update_time'] = datetime.now()
                            
                            # Log update with full details
                            symbol = fresh_position.get('symbol', 'N/A')
                            entry_price = fresh_position.get('price_open', 0.0)
                            current_price = fresh_position.get('price_current', 0.0)
                            profit = fresh_position.get('profit', 0.0)
                            applied_sl = fresh_position.get('sl', 0.0)
                            effective_sl_profit = self.get_effective_sl_profit(fresh_position)
                            
                            # Replace "Unknown" with concrete reason
                            if reason == "Unknown" or "unknown" in reason.lower():
                                reason = f"SL update completed (success: {success})"
                            
                            # OPTIMIZATION: Only log SL updates if:
                            # 1. Update failed (always log failures)
                            # 2. Update was slow (>20ms latency)
                            # 3. Profit-locking trade (important to track)
                            # 4. First position in loop (to show loop is working)
                            should_log_sl_update = (not success) or (update_latency > 20) or is_profit_locking or should_log_position
                            if should_log_sl_update:
                                log_level = logger.warning if not success else logger.info
                                log_level(f"🔄 SL UPDATE | Ticket: {ticket} | Symbol: {symbol} | "
                                         f"Entry: {entry_price:.5f} | Target SL: {applied_sl:.5f} | "
                                         f"Applied SL: {applied_sl:.5f} | Effective SL Profit: ${effective_sl_profit:.2f} | "
                                         f"Reason: {reason} | Latency: {update_latency:.1f}ms | Success: {success}")
                            
                            # OPTIMIZATION: Queue CSV write to background thread instead of blocking main loop
                            # CSV writing involves file I/O which can be slow
                            with self._tracking_lock:
                                last_update_time = self._last_sl_success.get(ticket) or self._last_sl_attempt.get(ticket)
                                consecutive_failures = self._consecutive_failures.get(ticket, 0)
                            
                            # Queue CSV write to background (non-blocking)
                            try:
                                self._csv_write_queue.put_nowait({
                                    'ticket': ticket, 'symbol': symbol, 'entry_price': entry_price,
                                    'current_price': current_price, 'profit': profit,
                                    'target_sl': applied_sl, 'applied_sl': applied_sl,
                                    'effective_sl_profit': effective_sl_profit,
                                    'last_update_time': last_update_time,
                                    'last_update_result': 'SUCCESS' if success else 'FAILED',
                                    'failure_reason': reason if not success else None,
                                    'consecutive_failures': consecutive_failures
                                })
                            except queue.Full:
                                logger.debug(f"CSV write queue full for ticket {ticket}, skipping")
                            except Exception:
                                pass  # Ignore errors - CSV writing is non-critical
                    
                    # Track loop duration
                    loop_duration = (time.time() - loop_start_time) * 1000  # Convert to ms
                    with self._timing_lock:
                        self._timing_stats['loop_durations'].append(loop_duration)
                        if len(self._timing_stats['loop_durations']) > 1000:
                            self._timing_stats['loop_durations'].pop(0)
                        self._timing_stats['last_loop_time'] = datetime.now()
                    
                    # CRITICAL FIX: Log performance warning if loop exceeds target
                    # Target: <10ms ideal, <50ms acceptable
                    if loop_duration > 50:
                        loop_end_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        logger.warning(f"[{loop_end_timestamp}] [WARNING] SL Worker loop exceeded 50ms target: {loop_duration:.1f}ms (target: <50ms, ideal: <10ms) | Positions: {len(positions) if 'positions' in locals() else 0}")
                        tracer.trace(
                            function_name="SLManager._sl_worker_loop",
                            expected=f"Complete iteration {iteration} in <50ms",
                            actual=f"Iteration {iteration} exceeded 50ms target (took {loop_duration:.1f}ms)",
                            status="WARNING",
                            iteration=iteration,
                            duration_ms=loop_duration,
                            position_count=len(positions) if 'positions' in locals() else 0
                        )
                    elif loop_duration > 10:
                        # Log info if between 10-50ms (acceptable but not ideal)
                        if iteration % 50 == 0:  # Only log every 50 iterations to reduce noise
                            logger.info(f"SL Worker loop duration: {loop_duration:.1f}ms (acceptable, but target is <10ms) | Positions: {len(positions) if 'positions' in locals() else 0}")
                    
                    # Sleep to maintain cadence (or instant if interval = 0)
                    elapsed = time.time() - loop_start_time
                    sleep_time = max(0, self._sl_worker_interval - elapsed) if self._sl_worker_interval > 0 else 0
                    
                    tracer.trace(
                        function_name="SLManager._sl_worker_loop",
                        expected=f"Complete iteration {iteration} and sleep {self._sl_worker_interval}s",
                        actual=f"Iteration {iteration} completed in {elapsed:.3f}s, sleeping {sleep_time:.3f}s",
                        status="OK",
                        iteration=iteration,
                        duration_seconds=round(elapsed, 3),
                        positions_processed=len(positions) if 'positions' in locals() else 0
                    )
                    
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    # If instant trailing (sleep_time = 0), continue immediately
                    # Note: Performance warning already logged above if loop_duration > 50ms
                
        except Exception as e:
            # MANDATORY OBSERVABILITY: Log thread crash with full stack trace and system event
            import os
            import traceback
            pid = os.getpid()
            tid = threading.current_thread().ident if threading.current_thread().ident else 'unknown'
            thread_name = threading.current_thread().name
            error_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            full_traceback = traceback.format_exc()
            
            logger.critical(f"[THREAD_CRASH] {thread_name} pid={pid} tid={tid}")
            logger.error(f"[{error_timestamp}] Error in SL worker loop: {e}", exc_info=True)
            
            # Emit system event for thread crash
            try:
                system_event_logger.systemEvent("CRITICAL", {
                    "tag": "THREAD_DIED",
                    "thread_name": thread_name,
                    "thread_id": tid,
                    "error": str(e),
                    "error_type": type(e).__name__
                })
            except Exception:
                pass  # Fallback if system event logger fails
            # Notify global health monitor that SLWorker died and block trading
            try:
                system_health.mark_thread_dead("SLWorker", f"exception: {type(e).__name__}")
            except Exception:
                pass
        
            # MANDATORY OBSERVABILITY: Log thread stop
            import os
            pid = os.getpid()
            tid = threading.current_thread().ident if threading.current_thread().ident else 'unknown'
            thread_name = threading.current_thread().name
            reason = "shutdown_requested" if not self._sl_worker_running else "unexpected_stop"
            
            if self._sl_worker_running:
                logger.critical("[ERROR] SLManager worker loop STOPPED unexpectedly (shutdown event was not set)")
                logger.critical(f"[THREAD_STOP] {thread_name} pid={pid} tid={tid} reason={reason}")
            else:
                logger.info(f"[THREAD_STOP] {thread_name} pid={pid} tid={tid} reason={reason}")
                logger.info("SL Worker loop stopped normally")
        except Exception as fatal_error:
            # MANDATORY OBSERVABILITY: Catch fatal errors that would crash the thread
            import os
            import traceback
            pid = os.getpid()
            tid = threading.current_thread().ident if threading.current_thread().ident else 'unknown'
            thread_name = threading.current_thread().name
            full_traceback = traceback.format_exc()
            
            logger.critical(f"[THREAD_CRASH] {thread_name} pid={pid} tid={tid} FATAL_ERROR")
            logger.critical(f"Fatal error in SL worker loop: {fatal_error}", exc_info=True)
            
            # Emit system event for fatal thread crash
            try:
                system_event_logger.systemEvent("CRITICAL", {
                    "tag": "THREAD_DIED",
                    "thread_name": thread_name,
                    "thread_id": tid,
                    "error": str(fatal_error),
                    "error_type": type(fatal_error).__name__,
                    "fatal": True
                })
            except Exception:
                pass  # Fallback if system event logger fails
    
    def _check_global_rpc_rate_limit(self, is_emergency: bool = False, consecutive_failures: int = 0) -> Tuple[bool, float]:
        """
        Check if global RPC rate limit allows an update.
        
        Args:
            is_emergency: If True, bypass rate limit (with short backoff to avoid flooding)
            consecutive_failures: Number of consecutive failures (triggers emergency bypass after 2)
        
        Returns:
            (allowed, backoff_delay) tuple. If allowed is False, backoff_delay is 0.
        """
        with self._global_rpc_lock:
            current_time = time.time()
            
            # Remove timestamps older than 1 second
            self._global_rpc_timestamps = [
                ts for ts in self._global_rpc_timestamps
                if current_time - ts < 1.0
            ]
            
            # Emergency bypass: losing trades OR repeated verification failures (after 2 retries)
            is_emergency_bypass = is_emergency or consecutive_failures >= 2
            
            if is_emergency_bypass:
                # Emergency: apply short exponential backoff to avoid broker flooding
                recent_emergency_count = sum(1 for ts in self._global_rpc_timestamps if current_time - ts < 0.1)  # Last 100ms
                if recent_emergency_count > 5:  # More than 5 emergency updates in 100ms
                    backoff = self._emergency_backoff_base * (2 ** min(recent_emergency_count - 5, 3))  # Max 400ms
                    logger.debug(f"[WARNING] Emergency backoff: {backoff*1000:.0f}ms (recent emergencies: {recent_emergency_count})")
                    return True, backoff
                return True, 0.0  # No backoff needed
            
            # Check if we're at the limit
            if len(self._global_rpc_timestamps) >= self._global_rpc_max_per_second:
                # Queue for later (non-emergency)
                return False, 0.0
            
            # Allow update - add timestamp
            self._global_rpc_timestamps.append(current_time)
            return True, 0.0
    
    def get_timing_stats(self) -> Dict[str, Any]:
        """Get timing statistics for monitoring."""
        with self._timing_lock:
            loop_durations = self._timing_stats['loop_durations']
            ticket_times = self._timing_stats['ticket_update_times']
            
            stats = {
                'loop_count': len(loop_durations),
                'last_loop_time': self._timing_stats['last_loop_time'],
                'last_update_time': self._timing_stats['last_update_time'],
                'update_counts': dict(self._timing_stats['update_counts'])
            }
            
            if loop_durations:
                stats['loop_duration'] = {
                    'min': min(loop_durations),
                    'max': max(loop_durations),
                    'avg': sum(loop_durations) / len(loop_durations),
                    'p95': sorted(loop_durations)[int(len(loop_durations) * 0.95)] if len(loop_durations) > 0 else 0
                }
            
            # Calculate per-ticket latencies
            all_latencies = []
            for ticket, latencies in ticket_times.items():
                if latencies:
                    all_latencies.extend(latencies)
            
            if all_latencies:
                stats['ticket_update_latency'] = {
                    'min': min(all_latencies),
                    'max': max(all_latencies),
                    'avg': sum(all_latencies) / len(all_latencies),
                    'median': sorted(all_latencies)[len(all_latencies) // 2],
                    'p95': sorted(all_latencies)[int(len(all_latencies) * 0.95)] if len(all_latencies) > 0 else 0
                }
            
            return stats
    
    def get_worker_status(self) -> Dict[str, Any]:
        """Get SL worker status."""
        return {
            'running': self._sl_worker_running,
            'thread_alive': self._sl_worker_thread.is_alive() if self._sl_worker_thread else False,
            'last_position_count': self._sl_worker_last_position_count,
            'last_active_tickets': self._sl_worker_last_active_tickets,
            'manual_review_tickets': list(self._manual_review_tickets),
            'error_occurrence_metrics': dict(self._error_occurrence_metrics)
        }
    
    def _get_sl_worker_metrics(self) -> Dict[str, Any]:
        """
        Metrics provider for timer-based heartbeat monitor.
        
        NOTE: Must NOT perform MT5 calls. Uses cached values only.
        """
        with self._tracking_lock:
            return {
                "positions": self._sl_worker_last_position_count,
                "active_tickets": self._sl_worker_last_active_tickets,
            }
    
    def _track_update_metrics(self, ticket: int, symbol: str, success: bool, reason: str, profit: float = 0.0,
                              activation_time_ms: Optional[float] = None, is_profit_locking: bool = False):
        """
        Track SL update metrics for verification hooks.
        
        Args:
            ticket: Position ticket
            symbol: Trading symbol
            success: Whether update succeeded
            reason: Update reason
            profit: Current profit
            activation_time_ms: Time from profit entry to SL lock (for profit locking)
            is_profit_locking: Whether this is a profit-locking update
        """
        with self._verification_lock:
            self._verification_metrics['sl_update_attempts'] += 1
            if success:
                self._verification_metrics['sl_update_successes'] += 1
            else:
                self._verification_metrics['sl_update_failures'] += 1
            
            # Track profit locking activations and times
            if is_profit_locking and success:
                self._verification_metrics['profit_locking_activations'] += 1
                if activation_time_ms is not None:
                    self._verification_metrics['profit_locking_times'].append(activation_time_ms)
                    # Keep only last 1000 entries to prevent memory growth
                    if len(self._verification_metrics['profit_locking_times']) > 1000:
                        self._verification_metrics['profit_locking_times'] = self._verification_metrics['profit_locking_times'][-1000:]
    
    def _track_lock_contention(self, ticket: int, timeout: bool = False):
        """Track lock acquisition failures and timeouts."""
        with self._verification_lock:
            self._verification_metrics['lock_acquisition_failures'] += 1
            if timeout:
                self._verification_metrics['lock_timeouts'] += 1
            else:
                self._verification_metrics['lock_contention_count'] += 1
    
    def get_verification_metrics(self) -> Dict[str, Any]:
        """
        Get verification metrics for system health monitoring.
        
        Returns metrics including:
        - SL update success rate (target: >95%)
        - Profit locking activation time (target: <500ms)
        - Duplicate update rate (target: 0)
        - Lock contention rate (target: <5%)
        """
        with self._verification_lock:
            metrics = self._verification_metrics.copy()
            
            # Calculate success rate
            attempts = metrics['sl_update_attempts']
            successes = metrics['sl_update_successes']
            success_rate = (successes / attempts * 100) if attempts > 0 else 0.0
            
            # Calculate average profit locking activation time
            profit_times = metrics['profit_locking_times']
            avg_activation_time = sum(profit_times) / len(profit_times) if profit_times else 0.0
            max_activation_time = max(profit_times) if profit_times else 0.0
            min_activation_time = min(profit_times) if profit_times else 0.0
            
            # Calculate lock contention rate
            lock_failures = metrics['lock_acquisition_failures']
            contention_rate = (lock_failures / attempts * 100) if attempts > 0 else 0.0
            
            return {
                'sl_update_attempts': attempts,
                'sl_update_successes': successes,
                'sl_update_failures': metrics['sl_update_failures'],
                'sl_update_success_rate': success_rate,
                'sl_update_success_rate_target': 95.0,
                'sl_update_success_rate_meets_target': success_rate >= 95.0,
                'profit_locking_activations': metrics['profit_locking_activations'],
                'profit_locking_avg_activation_time_ms': avg_activation_time,
                'profit_locking_max_activation_time_ms': max_activation_time,
                'profit_locking_min_activation_time_ms': min_activation_time,
                'profit_locking_activation_time_target_ms': 500.0,
                'profit_locking_meets_target': avg_activation_time <= 500.0 if profit_times else True,
                'duplicate_update_attempts': metrics['duplicate_update_attempts'],
                'duplicate_update_rate': 0.0,  # Will be calculated separately if needed
                'lock_acquisition_failures': lock_failures,
                'lock_timeouts': metrics['lock_timeouts'],
                'lock_contention_count': metrics['lock_contention_count'],
                'lock_contention_rate': contention_rate,
                'lock_contention_rate_target': 5.0,
                'lock_contention_rate_meets_target': contention_rate < 5.0,
                'last_metrics_reset': metrics['last_metrics_reset'].isoformat() if isinstance(metrics['last_metrics_reset'], datetime) else str(metrics['last_metrics_reset'])
            }
    
    def reset_verification_metrics(self):
        """Reset verification metrics (useful for testing or periodic resets)."""
        with self._verification_lock:
            self._verification_metrics = {
                'sl_update_attempts': 0,
                'sl_update_successes': 0,
                'sl_update_failures': 0,
                'profit_locking_activations': 0,
                'profit_locking_times': [],
                'duplicate_update_attempts': 0,
                'lock_acquisition_failures': 0,
                'lock_timeouts': 0,
                'lock_contention_count': 0,
                'last_metrics_reset': datetime.now()
            }
            logger.info("[OK] Verification metrics reset")
    
    def _log_verification_metrics(self):
        """Log verification metrics for system health monitoring."""
        try:
            metrics = self.get_verification_metrics()
            
            # Only log if we have meaningful data
            if metrics['sl_update_attempts'] == 0:
                return
            
            logger.info("=" * 100)
            logger.info("📊 SL MANAGER VERIFICATION METRICS")
            logger.info("=" * 100)
            logger.info(f"  SL Update Success Rate: {metrics['sl_update_success_rate']:.1f}% "
                       f"(Target: >{metrics['sl_update_success_rate_target']:.0f}%) "
                       f"{'[OK]' if metrics['sl_update_success_rate_meets_target'] else '[ERROR]'}")
            logger.info(f"  SL Update Attempts: {metrics['sl_update_attempts']} | "
                       f"Successes: {metrics['sl_update_successes']} | "
                       f"Failures: {metrics['sl_update_failures']}")
            
            if metrics['profit_locking_activations'] > 0:
                logger.info(f"  Profit Locking Activations: {metrics['profit_locking_activations']}")
                logger.info(f"  Profit Locking Avg Activation Time: {metrics['profit_locking_avg_activation_time_ms']:.1f}ms "
                           f"(Target: <{metrics['profit_locking_activation_time_target_ms']:.0f}ms) "
                           f"{'[OK]' if metrics['profit_locking_meets_target'] else '[ERROR]'}")
                if metrics['profit_locking_max_activation_time_ms'] > 0:
                    logger.info(f"  Profit Locking Time Range: {metrics['profit_locking_min_activation_time_ms']:.1f}ms - "
                               f"{metrics['profit_locking_max_activation_time_ms']:.1f}ms")
            
            logger.info(f"  Lock Contention Rate: {metrics['lock_contention_rate']:.1f}% "
                       f"(Target: <{metrics['lock_contention_rate_target']:.0f}%) "
                       f"{'[OK]' if metrics['lock_contention_rate_meets_target'] else '[ERROR]'}")
            logger.info(f"  Lock Failures: {metrics['lock_acquisition_failures']} | "
                       f"Timeouts: {metrics['lock_timeouts']} | "
                       f"Contention: {metrics['lock_contention_count']}")
            
            if metrics['duplicate_update_attempts'] > 0:
                logger.warning(f"  [WARNING] Duplicate Update Attempts: {metrics['duplicate_update_attempts']} "
                             f"(Target: 0)")
            
            logger.info("=" * 100)
        except Exception as e:
            logger.debug(f"Error logging verification metrics: {e}")

