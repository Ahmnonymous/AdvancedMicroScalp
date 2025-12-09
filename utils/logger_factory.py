"""
Logging Factory Module
Provides a centralized logging utility with file rotation, UTF-8 encoding, and thread-safe logging.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional


# Cache for loggers to prevent duplicate handlers
_logger_cache = {}


def get_logger(name: str, logfile_path: str, level: int = logging.INFO) -> logging.Logger:
    """
    Get or create a logger with file rotation and automatic folder creation.
    
    Args:
        name: Logger name (e.g., "hft_engine", "trend_detector")
        logfile_path: Path to log file (e.g., "logs/engine/hft_engine.log")
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


def get_symbol_logger(symbol: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Get or create a symbol-specific logger for trade logging.
    
    Args:
        symbol: Trading symbol (e.g., "EURUSD", "GBPUSD")
        level: Logging level (default: logging.DEBUG for detailed trade logs)
    
    Returns:
        Configured logger instance for the symbol
    """
    logfile_path = f"logs/trades/{symbol}.log"
    logger_name = f"trades.{symbol}"
    return get_logger(logger_name, logfile_path, level)

