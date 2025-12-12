"""
SL Worker Watchdog
Monitors SL worker health and restarts if needed.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
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
        self.ticket_staleness_threshold = 1.0  # 1 second max staleness per ticket
        self.max_restarts_per_10min = 2  # Max 2 restarts per 10 minutes
        
        # Tracking
        self._update_timestamps = deque()  # Sliding window of update timestamps
        self._restart_timestamps = deque()  # Track restart times
        self._restart_lock = threading.Lock()
        
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
                    watchdog_logger.warning("[WARNING] SL Worker not running - attempting restart")
                    self._restart_worker("Worker not running")
                    self.shutdown_event.wait(self.check_interval)
                    continue
                
                if not worker_status.get('thread_alive', False):
                    watchdog_logger.warning("[WARNING] SL Worker thread not alive - attempting restart")
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
                        watchdog_logger.warning(f"[WARNING] SL update rate too low: {updates_per_sec:.1f} updates/sec "
                                              f"(threshold: {self.sl_updates_per_sec_threshold})")
                        self._restart_worker(f"Low update rate: {updates_per_sec:.1f} updates/sec")
                
                # Check per-ticket staleness
                positions = self.sl_manager.order_manager.get_open_positions()
                if positions:
                    current_time = datetime.now()
                    stale_tickets = []
                    
                    with self.sl_manager._tracking_lock:
                        for position in positions:
                            ticket = position.get('ticket', 0)
                            if ticket in self.sl_manager._last_sl_update:
                                time_since_update = (current_time - self.sl_manager._last_sl_update[ticket]).total_seconds()
                                if time_since_update > self.ticket_staleness_threshold:
                                    stale_tickets.append((ticket, time_since_update))
                    
                    if stale_tickets:
                        watchdog_logger.warning(f"[WARNING] Stale SL updates detected: {len(stale_tickets)} tickets")
                        for ticket, staleness in stale_tickets[:5]:  # Log first 5
                            watchdog_logger.warning(f"   Ticket {ticket}: {staleness:.1f}s since last update")
                        
                        # Only restart if many tickets are stale
                        if len(stale_tickets) >= len(positions) * 0.5:  # 50% or more stale
                            self._restart_worker(f"{len(stale_tickets)} stale tickets (>{len(positions)*0.5})")
                
                # Sleep until next check
                self.shutdown_event.wait(self.check_interval)
            
            except Exception as e:
                watchdog_logger.error(f"Error in watchdog loop: {e}", exc_info=True)
                self.shutdown_event.wait(self.check_interval)
        
        watchdog_logger.info("Watchdog loop stopped")
    
    def _restart_worker(self, reason: str):
        """
        Restart the SL worker.
        
        Args:
            reason: Reason for restart
        """
        current_time = datetime.now()
        
        # Check restart rate limit
        with self._restart_lock:
            # Remove restarts older than 10 minutes
            cutoff_time = current_time - timedelta(minutes=10)
            self._restart_timestamps = [
                ts for ts in self._restart_timestamps
                if ts > cutoff_time
            ]
            
            if len(self._restart_timestamps) >= self.max_restarts_per_10min:
                watchdog_logger.critical(f"🚨 WATCHDOG RESTART RATE LIMIT EXCEEDED: "
                                      f"{len(self._restart_timestamps)} restarts in last 10 minutes | "
                                      f"Reason: {reason} | "
                                      f"BLOCKING RESTART - Manual intervention required")
                return
            
            # Log restart
            watchdog_logger.warning(f"🔄 WATCHDOG RESTARTING SL WORKER | Reason: {reason} | "
                                  f"Restarts in last 10min: {len(self._restart_timestamps)}")
            
            # Restart worker
            try:
                self.sl_manager.stop_sl_worker()
                time.sleep(0.1)  # Brief pause
                self.sl_manager.start_sl_worker()
                
                # Track restart
                self._restart_timestamps.append(current_time)
                
                watchdog_logger.info(f"[OK] SL Worker restarted successfully")
            except Exception as e:
                watchdog_logger.error(f"[ERROR] Failed to restart SL worker: {e}", exc_info=True)
    
    def track_sl_update(self, ticket: int):
        """Track an SL update for rate monitoring."""
        self._update_timestamps.append(datetime.now())
        
        # Keep only last 1000 timestamps
        if len(self._update_timestamps) > 1000:
            self._update_timestamps.popleft()

