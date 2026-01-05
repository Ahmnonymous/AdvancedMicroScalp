"""
Centralized system health tracking and trading gate.

Responsibilities:
- Track lifecycle of critical threads (start, heartbeat, death)
- Emit CRITICAL events when any critical thread dies
- Provide a global "system ready" / "trading allowed" gate
- Emit [SYSTEM_READY] once all critical threads have started and heartbeated
- Run a timer-based heartbeat monitor that is NOT coupled to worker loops or MT5 calls
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from utils.logger_factory import get_logger, get_system_event_logger


@dataclass
class ThreadHealthState:
    name: str
    thread_ref: Optional[threading.Thread] = None
    started: bool = False
    dead: bool = False
    first_heartbeat_ts: Optional[float] = None
    last_heartbeat_ts: Optional[float] = None
    metrics_provider: Optional[Callable[[], Dict[str, object]]] = None


_CRITICAL_THREADS = {
    # Phase 3: SLWorker removed - SL updates now synchronous in run_cycle()
    # "SLWorker",
    "TrailingStopMonitor",
    "FastTrailingStopMonitor",
    "PositionMonitor",
}

_lock = threading.RLock()
_thread_states: Dict[str, ThreadHealthState] = {
    name: ThreadHealthState(name=name) for name in _CRITICAL_THREADS
}
_heartbeat_thread: Optional[threading.Thread] = None
_heartbeat_interval_seconds: float = 5.0
_system_ready_logged: bool = False
_trading_blocked: bool = False

# Use logger factory to write to system_startup.log (same as TradingBot logger)
def _get_system_health_logger():
    """Get logger that writes to system_startup.log for consistency"""
    import json
    # Determine mode by checking config.json
    mode = 'live'  # default
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r') as f:
                config = json.load(f)
                mode = config.get('mode', 'live')
    except Exception:
        pass  # Use default if config read fails
    
    if mode == 'backtest':
        log_path = 'logs/backtest/system_startup.log'
    else:
        log_path = 'logs/live/system/system_startup.log'
    
    return get_logger("system_health", log_path)

_logger = _get_system_health_logger()
_system_event_logger = get_system_event_logger()


def _ensure_heartbeat_thread_running() -> None:
    """Start the timer-based heartbeat monitor, once."""
    global _heartbeat_thread
    with _lock:
        if _heartbeat_thread and _heartbeat_thread.is_alive():
            return

        def _heartbeat_loop() -> None:
            while True:
                time.sleep(_heartbeat_interval_seconds)
                _run_heartbeat_cycle()

        _heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name="SystemHealthHeartbeatMonitor",
            daemon=True,
        )
        _heartbeat_thread.start()


def _run_heartbeat_cycle() -> None:
    """
    Timer-based heartbeat cycle.

    - NOT coupled to any worker loop
    - Does NOT perform MT5 calls
    - Only reads cached metrics / thread refs
    """
    global _system_ready_logged, _trading_blocked

    with _lock:
        now = time.time()
        all_started = True
        all_alive = True
        all_have_heartbeat = True

        for name, state in _thread_states.items():
            thread = state.thread_ref
            alive = bool(thread and thread.is_alive() and not state.dead)

            # Collect cached metrics if available
            metrics: Dict[str, object] = {}
            if state.metrics_provider:
                try:
                    metrics = state.metrics_provider() or {}
                except Exception as e:  # pragma: no cover - defensive logging only
                    _logger.debug(f"Error in metrics_provider for {name}: {e}")

            positions = metrics.get("positions", 0)
            active_tickets = metrics.get("active_tickets", 0)

            pid = os.getpid()
            tid = thread.ident if thread and thread.ident else "unknown"

            # Emit heartbeat log
            _logger.info(
                f"[THREAD_HEARTBEAT] {name} pid={pid} tid={tid} "
                f"alive={str(alive).lower()} positions={positions} active_tickets={active_tickets}"
            )

            # Update heartbeat times
            if alive:
                state.last_heartbeat_ts = now
                if state.first_heartbeat_ts is None:
                    state.first_heartbeat_ts = now
            else:
                all_alive = False
                # If the thread was previously started and not yet marked dead, mark it now
                if state.started and not state.dead:
                    # Mark as dead and trigger SYSTEM_UNSAFE
                    state.dead = True
                    _trading_blocked = True
                    _logger.critical(
                        f"[CRITICAL][THREAD_DIED] name={name} pid={pid} tid={tid} reason=heartbeat_detected_dead"
                    )
                    try:
                        _system_event_logger.systemEvent(
                            "CRITICAL",
                            {
                                "tag": "SYSTEM_UNSAFE",
                                "thread_name": name,
                                "thread_id": tid,
                                "reason": "heartbeat_detected_dead",
                            },
                        )
                    except Exception:  # pragma: no cover - defensive
                        pass

            # Aggregate readiness status
            all_started = all_started and state.started
            all_have_heartbeat = (
                all_have_heartbeat and state.first_heartbeat_ts is not None
            )

        # CRITICAL FIX: Reset trading block when all threads recover (not just on initial startup)
        # This allows trading to resume automatically after thread recovery
        if (
            all_started
            and all_alive
            and all_have_heartbeat
        ):
            # If trading was blocked but all threads are now healthy, reset the block
            if _trading_blocked:
                _trading_blocked = False
                _logger.info("[SYSTEM_RECOVERED] All critical threads recovered - trading unblocked")
                try:
                    _system_event_logger.systemEvent(
                        "SYSTEM_READY",
                        {"all_critical_threads_alive": True, "recovered": True},
                    )
                except Exception:  # pragma: no cover - defensive
                    pass
            
            # Log SYSTEM_READY once, when all critical threads are truly alive
            if not _system_ready_logged:
                _system_ready_logged = True
                _logger.info("[SYSTEM_READY] all_critical_threads_alive=true")
                try:
                    _system_event_logger.systemEvent(
                        "SYSTEM_READY",
                        {"all_critical_threads_alive": True},
                    )
                except Exception:  # pragma: no cover - defensive
                    pass


def register_critical_thread(
    name: str,
    thread_ref: threading.Thread,
    metrics_provider: Optional[Callable[[], Dict[str, object]]] = None,
) -> None:
    """
    Register a critical thread with the health system.

    This should be called immediately after the thread is started.
    """
    if name not in _CRITICAL_THREADS:
        return

    with _lock:
        state = _thread_states[name]
        state.thread_ref = thread_ref
        state.metrics_provider = metrics_provider

    _ensure_heartbeat_thread_running()


def mark_thread_started(name: str) -> None:
    """Mark a critical thread as started."""
    if name not in _CRITICAL_THREADS:
        return
    with _lock:
        _thread_states[name].started = True


def mark_thread_heartbeat(name: str) -> None:
    """
    Optional: record that a thread reported its own heartbeat.
    The timer-based monitor will still emit the canonical heartbeat logs.
    """
    if name not in _CRITICAL_THREADS:
        return
    now = time.time()
    with _lock:
        state = _thread_states[name]
        state.last_heartbeat_ts = now
        if state.first_heartbeat_ts is None:
            state.first_heartbeat_ts = now


def mark_thread_dead(name: str, reason: str) -> None:
    """
    Mark a critical thread as dead and block trading.

    Emits:
    - [CRITICAL][THREAD_DIED]
    - [CRITICAL][SYSTEM_UNSAFE]
    """
    global _trading_blocked
    if name not in _CRITICAL_THREADS:
        return

    with _lock:
        state = _thread_states[name]
        state.dead = True
        _trading_blocked = True

    pid = os.getpid()
    tid = None
    with _lock:
        thread = _thread_states[name].thread_ref
        tid = thread.ident if thread and thread.ident else "unknown"

    _logger.critical(
        f"[CRITICAL][THREAD_DIED] name={name} pid={pid} tid={tid} reason={reason}"
    )

    try:
        _system_event_logger.systemEvent(
            "CRITICAL",
            {
                "tag": "THREAD_DIED",
                "thread_name": name,
                "thread_id": tid,
                "reason": reason,
            },
        )
        _system_event_logger.systemEvent(
            "CRITICAL",
            {
                "tag": "SYSTEM_UNSAFE",
                "thread_name": name,
                "thread_id": tid,
                "reason": reason,
            },
        )
    except Exception:  # pragma: no cover - defensive
        pass


def mark_system_unsafe(reason: str, details: Optional[str] = None) -> None:
    """
    Mark the system as unsafe and block trading.
    
    This can be called from anywhere in the system when a critical
    safety violation is detected (e.g., watchdog detects issues,
    safety guard violations, etc.).
    
    Emits:
    - [CRITICAL][SYSTEM_UNSAFE]
    """
    global _trading_blocked
    
    with _lock:
        _trading_blocked = True
    
    pid = os.getpid()
    _logger.critical(
        f"[CRITICAL][SYSTEM_UNSAFE] reason={reason} details={details or 'N/A'} pid={pid}"
    )
    
    try:
        _system_event_logger.systemEvent(
            "CRITICAL",
            {
                "tag": "SYSTEM_UNSAFE",
                "reason": reason,
                "details": details,
                "pid": pid,
            },
        )
    except Exception:  # pragma: no cover - defensive
        pass


def is_system_ready() -> bool:
    """Return True only if all critical threads have started and heartbeated and none are dead."""
    with _lock:
        for state in _thread_states.values():
            if not state.started or state.first_heartbeat_ts is None or state.dead:
                return False
        return True


def is_trading_allowed() -> bool:
    """
    Global trading gate.

    Trading is allowed only when:
    - All critical threads started
    - All have emitted at least one heartbeat
    - None is marked dead
    """
    with _lock:
        if _trading_blocked:
            return False
        return is_system_ready()


def reset_thread_dead_flag(name: str) -> None:
    """
    Reset the dead flag for a thread when it's been restarted.
    This allows the system to recover after thread restarts.
    """
    if name not in _CRITICAL_THREADS:
        return
    
    with _lock:
        state = _thread_states[name]
        if state.dead:
            state.dead = False
            _logger.info(f"[THREAD_RECOVERY] Reset dead flag for {name} - thread restarted")
            # Note: _trading_blocked will be reset automatically by _run_heartbeat_cycle
            # when all threads are detected as alive again


def get_health_snapshot() -> Dict[str, Dict[str, object]]:
    """Return a snapshot of current thread health state (for debugging / tests)."""
    with _lock:
        return {
            name: {
                "started": state.started,
                "dead": state.dead,
                "first_heartbeat_ts": state.first_heartbeat_ts,
                "last_heartbeat_ts": state.last_heartbeat_ts,
            }
            for name, state in _thread_states.items()
        }


