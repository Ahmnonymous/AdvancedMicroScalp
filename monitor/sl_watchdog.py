"""
SL Worker Watchdog
Monitors SL worker health and restarts if needed.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from collections import deque

from utils.logger_factory import get_logger

watchdog_logger = get_logger("watchdog", "logs/live/monitor/watchdog.log")


class SLWatchdog:
    """
    Watchdog for SL Worker health monitoring.
    
    Monitors:
    - SL updates/sec (should be > 5 over 10s window)
    - Per-ticket staleness (no update for 1s for active tickets)
    - Worker thread health
    """
    
    def __init__(self, sl_manager):
        """
        Initialize watchdog.
        
        Args:
            sl_manager: SLManager instance to monitor
        """
        self.sl_manager = sl_manager
        self.running = False
        self.watchdog_thread: Optional[threading.Thread] = None
        self.shutdown_event = threading.Event()
        
        # Configuration
        self.check_interval = 5.0  # Check every 5 seconds
        self.sl_updates_per_sec_threshold = 5  # Minimum updates/sec over 10s window
        self.sl_updates_window_seconds = 10.0  # Sliding window for update rate
        self.ticket_staleness_threshold = 1.0  # 1 second max staleness per ticket (base, will be overridden by FIX D)
        
        # FIX 3: LIVE RELIABILITY - Configuration for graceful restart
        # Extended wait time for profit-locking updates (15s) to prevent interrupting critical profit locks
        self.max_wait_for_in_flight_seconds = 5.0  # Maximum time to wait for standard in-flight updates
        self.max_wait_for_profit_lock_seconds = 15.0  # Extended wait time for profit-locking updates (LIVE SAFETY)
        self.in_flight_check_interval = 0.1  # Check every 100ms for in-flight updates
        
        # FIX 4: LIVE RELIABILITY - Relaxed stale lock thresholds for live trading
        # Increased thresholds to prevent false positives under MT5 broker latency
        risk_config = sl_manager.risk_config if hasattr(sl_manager, 'risk_config') else {}
        watchdog_config = risk_config.get('watchdog', {})
        # FIX 4: Profitable trades: ≥ 2.0 seconds (was 2.0s, keeping same)
        self.stale_threshold_profitable = watchdog_config.get('stale_threshold_profitable_seconds', 2.0)  # 2.0s for profitable trades
        self.stale_threshold_breakeven = watchdog_config.get('stale_threshold_breakeven_seconds', 1.0)  # 1.0s for breakeven
        # FIX 4: Losing trades: ≥ 1.0 second (increased from 0.5s for live safety)
        self.stale_threshold_losing = watchdog_config.get('stale_threshold_losing_seconds', 1.0)  # 1.0s for losing trades (LIVE)
        # FIX 4: Crypto/indices: multiply threshold by 2× (keeping same)
        self.stale_threshold_crypto_multiplier = watchdog_config.get('stale_threshold_crypto_multiplier', 2.0)  # 2x for BTCXAUm/DE30m
        # STEP 4 FIX: Increased restart limit and added exponential backoff
        self.max_restarts_per_10min = 10  # Increased from 5 to 10 restarts per 10 minutes
        self.restart_backoff_base_seconds = 30  # Base backoff time (30 seconds)
        self.restart_backoff_max_seconds = 300  # Maximum backoff time (5 minutes)
        # FIX 6: Read watchdog restart threshold from config
        risk_config = sl_manager.risk_config if hasattr(sl_manager, 'risk_config') else {}
        self.watchdog_restart_threshold = risk_config.get('watchdog_restart_threshold', 2.0)  # Default 2.0 seconds
        
        # Tracking
        self._update_timestamps = deque()  # Sliding window of update timestamps
        self._restart_timestamps = deque()  # Track restart times
        self._restart_lock = threading.Lock()
        
        # CRITICAL FIX: Alerting and monitoring for watchdog restarts
        self._restart_alert_threshold = watchdog_config.get('restart_alert_threshold', 5)  # Alert after 5 restarts in 10min
        self._restart_alert_cooldown = None  # Last alert time - prevent alert spam
        self._restart_alert_cooldown_seconds = 300.0  # Alert at most once per 5 minutes
        
        watchdog_logger.info("SL Watchdog initialized")
    
    def start(self):
        """Start the watchdog thread."""
        if self.running:
            watchdog_logger.warning("Watchdog already running")
            return
        
        self.running = True
        self.shutdown_event.clear()
        self.watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="SLWatchdog",
            daemon=True
        )
        self.watchdog_thread.start()
        watchdog_logger.info("[OK] SL Watchdog started")
    
    def stop(self):
        """Stop the watchdog thread."""
        self.running = False
        self.shutdown_event.set()
        
        if self.watchdog_thread and self.watchdog_thread.is_alive():
            self.watchdog_thread.join(timeout=2.0)
        
        watchdog_logger.info("SL Watchdog stopped")
    
    def _watchdog_loop(self):
        """Main watchdog monitoring loop."""
        watchdog_logger.info("Watchdog loop started")
        
        while self.running and not self.shutdown_event.is_set():
            try:
                # Check worker status
                worker_status = self.sl_manager.get_worker_status()
                
                if not worker_status.get('running', False):
                    watchdog_logger.critical("[CRITICAL] SL Worker not running - HALTING TRADING")
                    self._restart_worker("Worker not running")
                    self.shutdown_event.wait(self.check_interval)
                    continue
                
                if not worker_status.get('thread_alive', False):
                    watchdog_logger.critical("[CRITICAL] SL Worker thread not alive - HALTING TRADING")
                    self._restart_worker("Worker thread not alive")
                    self.shutdown_event.wait(self.check_interval)
                    continue
                
                # Check SL update rate
                timing_stats = self.sl_manager.get_timing_stats()
                last_update_time = timing_stats.get('last_update_time')
                
                if last_update_time:
                    # Calculate updates/sec over sliding window
                    current_time = datetime.now()
                    window_start = current_time - timedelta(seconds=self.sl_updates_window_seconds)
                    
                    # Count updates in window (from timing stats)
                    update_counts = timing_stats.get('update_counts', {})
                    total_updates = sum(update_counts.values())
                    
                    # Estimate updates/sec (rough - based on total counts)
                    # Better: track actual timestamps
                    if len(self._update_timestamps) > 0:
                        # Remove timestamps outside window
                        while len(self._update_timestamps) > 0:
                            if (current_time - self._update_timestamps[0]).total_seconds() > self.sl_updates_window_seconds:
                                self._update_timestamps.popleft()
                            else:
                                break
                        
                        updates_in_window = len(self._update_timestamps)
                        updates_per_sec = updates_in_window / self.sl_updates_window_seconds
                    else:
                        # Fallback: use timing stats
                        updates_per_sec = total_updates / self.sl_updates_window_seconds if total_updates > 0 else 0
                    
                    if updates_per_sec < self.sl_updates_per_sec_threshold:
                        watchdog_logger.critical(f"[CRITICAL] SL update rate too low: {updates_per_sec:.1f} updates/sec "
                                              f"(threshold: {self.sl_updates_per_sec_threshold}) - HALTING TRADING")
                        self._restart_worker(f"Low update rate: {updates_per_sec:.1f} updates/sec")
                
                # Check per-ticket staleness
                positions = self.sl_manager.order_manager.get_open_positions()
                if positions:
                    current_time = datetime.now()
                    stale_tickets = []
                    
                    # FIX D: Use adaptive stale lock thresholds based on trade state and symbol
                    with self.sl_manager._tracking_lock:
                        for position in positions:
                            ticket = position.get('ticket', 0)
                            symbol = position.get('symbol', '')
                            profit = position.get('profit', 0.0)
                            
                            if ticket in self.sl_manager._last_sl_update:
                                time_since_update = (current_time - self.sl_manager._last_sl_update[ticket]).total_seconds()
                                
                                # FIX D: Determine threshold based on trade state
                                if profit > 0:
                                    threshold = self.stale_threshold_profitable  # 2.0s for profitable
                                elif profit < -1.0:
                                    threshold = self.stale_threshold_losing  # 0.5s for losing
                                else:
                                    threshold = self.stale_threshold_breakeven  # 1.0s for breakeven
                                
                                # FIX D: Apply symbol-specific multiplier for crypto/indices
                                if symbol in ['BTCXAUm', 'DE30m']:
                                    threshold *= self.stale_threshold_crypto_multiplier  # 2x for crypto/indices
                                
                                if time_since_update > threshold:
                                    stale_tickets.append((ticket, time_since_update))
                    
                    if stale_tickets:
                        # FIX D: Log with adaptive threshold information
                        watchdog_logger.warning(f"[WARNING] Stale SL updates detected: {len(stale_tickets)} tickets")
                        for ticket, staleness in stale_tickets[:5]:  # Log first 5
                            position = next((p for p in positions if p.get('ticket') == ticket), None)
                            if position:
                                symbol = position.get('symbol', '')
                                profit = position.get('profit', 0.0)
                                # Calculate threshold used for this ticket
                                if profit > 0:
                                    threshold_used = self.stale_threshold_profitable
                                elif profit < -1.0:
                                    threshold_used = self.stale_threshold_losing
                                else:
                                    threshold_used = self.stale_threshold_breakeven
                                if symbol in ['BTCXAUm', 'DE30m']:
                                    threshold_used *= self.stale_threshold_crypto_multiplier
                                watchdog_logger.warning(f"   Ticket {ticket} ({symbol}): {staleness:.1f}s since last update "
                                                      f"(threshold: {threshold_used:.1f}s, profit: ${profit:.2f})")
                            else:
                                watchdog_logger.warning(f"   Ticket {ticket}: {staleness:.1f}s since last update")
                        
                        # FIX 6: Root cause analysis before restarting
                        # Check if stale tickets are due to orphaned locks
                        orphaned_lock_tickets = []
                        for ticket, _ in stale_tickets:
                            if self._is_orphaned_lock(ticket):
                                orphaned_lock_tickets.append(ticket)
                        
                        if orphaned_lock_tickets:
                            # Don't restart - force lock recovery instead
                            watchdog_logger.warning(f"[WATCHDOG] Orphaned locks detected: {len(orphaned_lock_tickets)} tickets | "
                                                  f"Attempting lock recovery instead of restart")
                            for ticket in orphaned_lock_tickets:
                                self._force_lock_recovery(ticket)
                            # Only halt if there are stale tickets NOT due to orphaned locks
                            non_orphaned_stale = [t for t, _ in stale_tickets if t not in orphaned_lock_tickets]
                            if len(non_orphaned_stale) >= len(positions) * 0.5:
                                watchdog_logger.critical(f"[CRITICAL] {len(non_orphaned_stale)} stale tickets (not orphaned locks) - HALTING TRADING")
                                self._restart_worker(f"{len(non_orphaned_stale)} stale tickets (not orphaned locks)")
                        else:
                            # No orphaned locks - halt trading if threshold met
                            if len(stale_tickets) >= len(positions) * 0.5:  # 50% or more stale
                                watchdog_logger.critical(f"[CRITICAL] {len(stale_tickets)} stale tickets (>{len(positions)*0.5}) - HALTING TRADING")
                                self._restart_worker(f"{len(stale_tickets)} stale tickets (>{len(positions)*0.5})")
                
                # Sleep until next check
                self.shutdown_event.wait(self.check_interval)
            
            except Exception as e:
                watchdog_logger.error(f"Error in watchdog loop: {e}", exc_info=True)
                self.shutdown_event.wait(self.check_interval)
        
        watchdog_logger.info("Watchdog loop stopped")
    
    def _restart_worker(self, reason: str):
        """
        CRITICAL SAFETY FIX #6: Halt trading instead of restarting worker.
        
        PROBLEM (PROVEN): Watchdog restarts abort SL updates, creating protection gaps.
        
        NEW BEHAVIOR: Instead of restarting, activate kill switch and halt trading.
        Rule: Fail closed, not open.
        
        Args:
            reason: Reason for worker health issue
        """
        current_time = datetime.now()
        
        # CRITICAL SAFETY FIX #6: HALT TRADING instead of restarting
        # Restarting creates protection gaps - better to fail closed (halt trading)
        
        watchdog_logger.critical(f"🚨 WATCHDOG: SL WORKER UNHEALTHY | Reason: {reason} | "
                               f"HALTING TRADING (fail-safe) instead of restarting")
        
        # Mark system as UNSAFE
        try:
            from utils.system_health import mark_system_unsafe
            mark_system_unsafe(
                reason="sl_worker_unhealthy",
                details=f"Watchdog detected SL worker issue: {reason}"
            )
        except Exception as e:
            watchdog_logger.error(f"Failed to mark system unsafe: {e}", exc_info=True)
        
        # Activate kill switch (if trading_bot available)
        try:
            # Try to get trading_bot from sl_manager's order_manager
            order_manager = self.sl_manager.order_manager
            if hasattr(order_manager, '_trading_bot'):
                trading_bot = order_manager._trading_bot
                if trading_bot:
                    trading_bot.activate_kill_switch(
                        reason=f"SL Worker unhealthy: {reason}"
                    )
                    watchdog_logger.critical(f"[KILL_SWITCH_ACTIVATED] Trading disabled due to SL worker health issue")
        except Exception as e:
            watchdog_logger.error(f"Failed to activate kill switch: {e}", exc_info=True)
        
        # Log system event
        try:
            from utils.logger_factory import get_system_event_logger
            system_event_logger = get_system_event_logger()
            system_event_logger.systemEvent("WATCHDOG_HALT_TRADING", {
                "reason": reason,
                "timestamp": current_time.isoformat(),
                "action": "kill_switch_activated"
            })
        except Exception as e:
            watchdog_logger.warning(f"Failed to log system event: {e}")
        
        # DO NOT RESTART WORKER - Halt trading instead
        watchdog_logger.critical(f"[WATCHDOG_HALT] Trading halted - manual intervention required | "
                               f"Reason: {reason} | "
                               f"System marked UNSAFE - no new trades will be placed")
    
    def _check_in_flight_updates(self) -> Tuple[List[int], bool]:
        """
        FIX 3: LIVE RELIABILITY - Check for in-flight SL updates.
        
        Returns tuple of:
        - List of ticket numbers that have active locks (SL updates in progress)
        - Boolean indicating if any in-flight update is profit-locking (requires extended wait)
        
        This prevents watchdog from restarting worker mid-update, especially during profit-locking.
        
        Returns:
            Tuple of (list of ticket numbers with in-flight SL updates, has_profit_locking)
        """
        in_flight_tickets = []
        has_profit_locking = False
        
        try:
            # Check lock holders to detect active SL updates
            with self.sl_manager._locks_lock:
                for ticket, holder_info in self.sl_manager._lock_holders.items():
                    if holder_info:
                        # Check if lock is currently held (SL update in progress)
                        lock_object = holder_info.get('lock_object')
                        if lock_object:
                            # Try to acquire lock without blocking to check if it's held
                            # If we can acquire immediately, lock is not held (no in-flight update)
                            # If we can't, lock is held (in-flight update)
                            acquired = lock_object.acquire(blocking=False)
                            if not acquired:
                                # Lock is held - SL update is in progress
                                in_flight_tickets.append(ticket)
                                # FIX 3: Check if this is a profit-locking update (requires extended wait)
                                is_profit_locking = holder_info.get('is_profit_locking', False)
                                if is_profit_locking:
                                    has_profit_locking = True
                            else:
                                # Lock was not held - release it immediately
                                lock_object.release()
        except Exception as e:
            watchdog_logger.error(f"Error checking in-flight updates: {e}", exc_info=True)
        
        return in_flight_tickets, has_profit_locking
    
    def track_sl_update(self, ticket: int):
        """Track an SL update for rate monitoring."""
        self._update_timestamps.append(datetime.now())
        
        # Keep only last 1000 timestamps
        if len(self._update_timestamps) > 1000:
            self._update_timestamps.popleft()
    
    def _is_orphaned_lock(self, ticket: int) -> bool:
        """
        FIX 6: Check if a ticket has an orphaned lock.
        
        Args:
            ticket: Position ticket number
        
        Returns:
            True if ticket has an orphaned lock, False otherwise
        """
        try:
            with self.sl_manager._locks_lock:
                holder_info = self.sl_manager._lock_holders.get(ticket)
                if holder_info:
                    holder_thread_id = holder_info.get('thread_id')
                    if holder_thread_id:
                        # Check if thread is still alive
                        active_thread_ids = {t.ident for t in threading.enumerate()}
                        if holder_thread_id not in active_thread_ids:
                            return True  # Thread is dead - lock is orphaned
            return False
        except Exception as e:
            watchdog_logger.error(f"Error checking orphaned lock for ticket {ticket}: {e}")
            return False
    
    def _force_lock_recovery(self, ticket: int):
        """
        FIX 6: Force recovery of an orphaned lock.
        
        Args:
            ticket: Position ticket number
        """
        try:
            with self.sl_manager._locks_lock:
                holder_info = self.sl_manager._lock_holders.get(ticket)
                if holder_info:
                    holder_thread_id = holder_info.get('thread_id')
                    lock_object = holder_info.get('lock_object')
                    
                    if holder_thread_id and lock_object:
                        # Check if thread is dead
                        active_thread_ids = {t.ident for t in threading.enumerate()}
                        if holder_thread_id not in active_thread_ids:
                            # Thread is dead - recover lock
                            watchdog_logger.warning(f"[WATCHDOG_LOCK_RECOVERY] Ticket {ticket} | "
                                                   f"Recovering orphaned lock from dead thread {holder_thread_id}")
                            self.sl_manager._recover_orphaned_lock(ticket, lock_object, holder_thread_id)
        except Exception as e:
            watchdog_logger.error(f"Error forcing lock recovery for ticket {ticket}: {e}", exc_info=True)

