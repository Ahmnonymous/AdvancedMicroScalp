"""
Logging Factory Module
Provides a centralized logging utility with file rotation, UTF-8 encoding, and thread-safe logging.
"""

import logging
import os
import threading
import time
import queue
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from typing import Optional, Dict, Any


# Cache for loggers to prevent duplicate handlers
_logger_cache = {}
# Cache for file handlers to share handlers for the same file path (prevents file locking issues)
_file_handler_cache = {}
_file_handler_lock = threading.Lock()
# Cache for queue listeners (one per file path) to serialize writes
_queue_listeners = {}
_queue_listener_lock = threading.Lock()


def get_logger(name: str, logfile_path: str, level: int = logging.INFO) -> logging.Logger:
    """
    Get or create a logger with file rotation and automatic folder creation.
    
    Args:
        name: Logger name (e.g., "hft_engine", "trend_detector")
        logfile_path: Path to log file (e.g., "logs/live/engine/hft_engine.log")
        level: Logging level (default: logging.INFO)
    
    Returns:
        Configured logger instance
    """
    # Check cache first
    cache_key = (name, logfile_path)
    if cache_key in _logger_cache:
        return _logger_cache[cache_key]
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    # Create directory if it doesn't exist
    log_dir = os.path.dirname(logfile_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # CRITICAL FIX: Use queue-based handler for thread-safe file writing
    # This prevents file locking issues when multiple loggers write to the same file
    with _file_handler_lock:
        if logfile_path in _file_handler_cache:
            # Reuse existing queue handler for this file path
            queue_handler = _file_handler_cache[logfile_path]
        else:
            # Create new rotating file handler
            # Max 5MB per file, keep 3 backup files
            max_bytes = 5 * 1024 * 1024  # 5MB
            backup_count = 3
            
            # CRITICAL FIX: Add retry logic for file access on Windows
            # Windows can have file locking issues when multiple processes try to access the same file
            file_handler = None
            max_retries = 5
            retry_delay = 0.1  # 100ms
            
            for attempt in range(max_retries):
                try:
                    file_handler = RotatingFileHandler(
                        logfile_path,
                        maxBytes=max_bytes,
                        backupCount=backup_count,
                        encoding='utf-8',
                        delay=False  # Don't delay file opening - open immediately
                    )
                    file_handler.setLevel(level)
                    
                    # Set formatter with timestamp
                    formatter = logging.Formatter(
                        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S'
                    )
                    file_handler.setFormatter(formatter)
                    break  # Success - exit retry loop
                except (OSError, PermissionError) as e:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                    else:
                        # P3-16 FIX: Last attempt failed - create a fallback handler that writes to stderr
                        import sys
                        print(f"WARNING: Could not open log file {logfile_path} after {max_retries} attempts: {e}", file=sys.stderr)
                        print(f"Falling back to stderr logging for logger '{name}'", file=sys.stderr)
                        # Create a StreamHandler as fallback
                        file_handler = logging.StreamHandler(sys.stderr)
                        file_handler.setLevel(level)
                        formatter = logging.Formatter(
                            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S'
                        )
                        file_handler.setFormatter(formatter)
                        # Don't cache fallback handlers
                        break
            
            if file_handler:
                # Create a queue for thread-safe logging
                log_queue = queue.Queue(-1)  # Unlimited queue size
                
                # Create queue handler that will put records into the queue
                queue_handler = QueueHandler(log_queue)
                queue_handler.setLevel(level)
                
                # Create queue listener that will process records from the queue and write to file
                # This runs in a separate thread, serializing all writes to the file
                queue_listener = QueueListener(log_queue, file_handler, respect_handler_level=True)
                queue_listener.start()
                
                # Cache both the queue handler and listener
                _file_handler_cache[logfile_path] = queue_handler
                with _queue_listener_lock:
                    _queue_listeners[logfile_path] = queue_listener
    
    # Add queue handler to logger (writes go through queue, then to file handler in separate thread)
    logger.addHandler(queue_handler)
    
    # Cache logger
    _logger_cache[cache_key] = logger
    
    return logger


def get_symbol_logger(symbol: str, level: int = logging.DEBUG, is_backtest: bool = False) -> logging.Logger:
    """
    Get or create a symbol-specific logger for trade logging.
    
    Args:
        symbol: Trading symbol (e.g., "EURUSD", "GBPUSD")
        level: Logging level (default: logging.DEBUG for detailed trade logs)
        is_backtest: If True, use backtest log path, otherwise use live log path
    
    Returns:
        Configured logger instance for the symbol
    """
    log_dir = "logs/backtest/trades" if is_backtest else "logs/live/trades"
    logfile_path = f"{log_dir}/{symbol}.log"
    logger_name = f"trades.{symbol}"
    return get_logger(logger_name, logfile_path, level)


class SystemEventLogger:
    """
    System event logger for diagnostic events.
    Logs to a dedicated system events log file.
    """
    
    def __init__(self):
        self.logger = get_logger("system_events", "logs/live/system/system_events.log")
        self._valid_tags = {
            # SL & Trailing Diagnostics
            "SL_UPDATE_SKIPPED", "SL_UPDATE_FAILED", "SL_NOT_MOVING",
            "TRAILING_TRIGGERED", "TRAILING_SKIPPED", "TRAILING_EXECUTED",
            "FAST_TRAILING_EXECUTED", "SWEET_SPOT_ENTERED", "SWEET_SPOT_LOCKED",
            # MT5 Execution Diagnostics
            "ORDER_REJECTED", "ORDER_SLIPPAGE", "ORDER_FILLED_DELAYED",
            "CLOSE_FAILED", "MODIFY_FAILED",
            # Price & Spread Issues
            "SPREAD_TOO_HIGH", "PRICE_JUMP_DETECTED", "BIG_JUMP_NOT_CAUGHT",
            # System Health / Session
            "SESSION_END_RECONCILIATION", "RECONCILIATION_MISMATCH", "BOT_LOOP_TICK",
            # Thread Health
            "THREAD_DIED", "CRITICAL", "SYSTEM_READY", "SYSTEM_UNSAFE",
            # Startup Test
            "BOOT_TEST",
            # Watchdog & Safety
            "WATCHDOG_HALT_TRADING", "TRADING_HALTED", "SL_WORKER_UNHEALTHY",
            # Profit Locking
            "PROFIT_ZONE_ENTERED", "LOCK_APPLIED", "LOCK_FAILED", "PROFIT_LOCK_FAILURE_ALERT",
            # Master Kill Switch
            "MASTER_KILL_SWITCH_ACTIVE"
        }
    
    def systemEvent(self, tag: str, details: Dict[str, Any]):
        """
        Log a system event.
        
        Args:
            tag: Event tag (must be one of the valid tags, or "BOOT_TEST" for startup test)
            details: Event details dictionary (only essential values, no giant objects)
        """
        # Allow BOOT_TEST tag for startup verification
        if tag not in self._valid_tags and tag != "BOOT_TEST":
            self.logger.warning(f"Invalid system event tag: {tag}")
            return
        
        # Format details as JSON for structured logging
        import json
        try:
            details_str = json.dumps(details, default=str)
            self.logger.info(f"[{tag}] {details_str}")
        except Exception as e:
            # Fallback to string representation if JSON fails
            self.logger.info(f"[{tag}] {str(details)}")


# Global system event logger instance
_system_event_logger = None

def get_system_event_logger() -> SystemEventLogger:
    """Get the global system event logger instance."""
    global _system_event_logger
    if _system_event_logger is None:
        _system_event_logger = SystemEventLogger()
    return _system_event_logger


def close_all_loggers():
    """
    Close all file handlers for all cached loggers.
    This prevents file locking issues when deleting log folders.
    """
    global _logger_cache, _file_handler_cache, _queue_listeners
    
    with _file_handler_lock:
        # Stop all queue listeners first (they manage the actual file handlers)
        with _queue_listener_lock:
            for logfile_path, listener in _queue_listeners.items():
                try:
                    listener.stop()
                except Exception as e:
                    import sys
                    print(f"Warning: Error stopping queue listener for {logfile_path}: {e}", file=sys.stderr)
            _queue_listeners.clear()
        
        # Close all handlers for all cached loggers
        for cache_key, logger in _logger_cache.items():
            for handler in logger.handlers[:]:  # Copy list to avoid modification during iteration
                try:
                    if isinstance(handler, QueueHandler):
                        # Queue handlers don't need to be closed, but remove them
                        logger.removeHandler(handler)
                    else:
                        handler.close()
                        logger.removeHandler(handler)
                except Exception as e:
                    # Log to stderr since we're closing loggers
                    import sys
                    print(f"Warning: Error closing logger handler: {e}", file=sys.stderr)
        
        # Clear the caches
        _logger_cache.clear()
        _file_handler_cache.clear()
    
    # Also close root logger handlers that might be open
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        try:
            handler.close()
            root_logger.removeHandler(handler)
        except Exception:
            pass


def close_logger(name: str, logfile_path: str):
    """
    Close a specific logger by name and path.
    
    Args:
        name: Logger name
        logfile_path: Log file path
    """
    global _file_handler_cache, _queue_listeners
    
    cache_key = (name, logfile_path)
    with _file_handler_lock:
        if cache_key in _logger_cache:
            logger = _logger_cache[cache_key]
            for handler in logger.handlers[:]:
                try:
                    if isinstance(handler, QueueHandler):
                        # Queue handlers don't need to be closed, just remove them
                        logger.removeHandler(handler)
                    else:
                        handler.close()
                        logger.removeHandler(handler)
                except Exception:
                    pass
            del _logger_cache[cache_key]
        
        # Only remove file handler from cache if no other logger is using it
        # Check if any other logger is using this file path
        handler_still_in_use = False
        for (other_name, other_path), other_logger in _logger_cache.items():
            if other_path == logfile_path and (other_name, other_path) != cache_key:
                handler_still_in_use = True
                break
        
        if not handler_still_in_use and logfile_path in _file_handler_cache:
            # Stop the queue listener for this file path
            with _queue_listener_lock:
                if logfile_path in _queue_listeners:
                    try:
                        _queue_listeners[logfile_path].stop()
                    except Exception:
                        pass
                    del _queue_listeners[logfile_path]
            del _file_handler_cache[logfile_path]