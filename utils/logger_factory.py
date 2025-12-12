"""
Logging Factory Module
Provides a centralized logging utility with file rotation, UTF-8 encoding, and thread-safe logging.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any


# Cache for loggers to prevent duplicate handlers
_logger_cache = {}


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
    
    # Create rotating file handler
    # Max 5MB per file, keep 10 backup files
    max_bytes = 5 * 1024 * 1024  # 5MB
    backup_count = 10
    
    file_handler = RotatingFileHandler(
        logfile_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    
    # Set formatter with timestamp
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(file_handler)
    
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
            "SESSION_END_RECONCILIATION", "RECONCILIATION_MISMATCH", "BOT_LOOP_TICK"
        }
    
    def systemEvent(self, tag: str, details: Dict[str, Any]):
        """
        Log a system event.
        
        Args:
            tag: Event tag (must be one of the valid tags)
            details: Event details dictionary (only essential values, no giant objects)
        """
        if tag not in self._valid_tags:
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
    global _logger_cache
    
    # Close all handlers for all cached loggers
    for cache_key, logger in _logger_cache.items():
        for handler in logger.handlers[:]:  # Copy list to avoid modification during iteration
            try:
                handler.close()
                logger.removeHandler(handler)
            except Exception as e:
                # Log to stderr since we're closing loggers
                import sys
                print(f"Warning: Error closing logger handler: {e}", file=sys.stderr)
    
    # Clear the cache
    _logger_cache.clear()
    
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
    cache_key = (name, logfile_path)
    if cache_key in _logger_cache:
        logger = _logger_cache[cache_key]
        for handler in logger.handlers[:]:
            try:
                handler.close()
                logger.removeHandler(handler)
            except Exception:
                pass
        del _logger_cache[cache_key]