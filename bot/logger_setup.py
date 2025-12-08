"""
Logging Setup Module
Configures logging for the trading bot with minimal root logs and symbol-specific logs.
"""

import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional


# Global symbol loggers cache
_symbol_loggers = {}


def setup_logging(config: Dict[str, Any], console_output: bool = False) -> logging.Logger:
    """
    Setup logging configuration with minimal root logs and symbol-specific logs.
    
    Args:
        config: Configuration dictionary
        console_output: If True, also log to console. If False, only log to file.
    """
    log_config = config.get('logging', {})
    root_level = log_config.get('root_level', log_config.get('level', 'INFO'))
    log_file = log_config.get('log_file', 'bot_log.txt')
    symbol_log_dir = log_config.get('symbol_log_dir', 'logs/symbols')
    
    # Create logs directories if they don't exist
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    if not os.path.exists(symbol_log_dir):
        os.makedirs(symbol_log_dir, exist_ok=True)
    
    # Configure root logger with minimal INFO level (only critical events)
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, root_level.upper()))
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Root file handler (minimal logging - only critical events)
    root_file_handler = logging.FileHandler(log_file, encoding='utf-8')
    root_file_handler.setLevel(getattr(logging, root_level.upper()))
    root_file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    )
    root_logger.addHandler(root_file_handler)
    
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, root_level.upper()))
        console_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        root_logger.addHandler(console_handler)
    
    logger = logging.getLogger('TradingBot')
    logger.info("=" * 60)
    logger.info("Trading Bot Started")
    logger.info(f"Root Log Level: {root_level}")
    logger.info(f"Root Log File: {log_file}")
    logger.info(f"Symbol Log Directory: {symbol_log_dir}")
    logger.info(f"Console Output: {console_output}")
    logger.info("=" * 60)
    
    return logger


def get_symbol_logger(symbol: str, config: Dict[str, Any]) -> logging.Logger:
    """
    Get or create a symbol-specific logger with DEBUG level.
    
    Args:
        symbol: Trading symbol (e.g., 'EURUSD')
        config: Configuration dictionary
    
    Returns:
        Logger instance for the symbol
    """
    global _symbol_loggers
    
    if symbol in _symbol_loggers:
        return _symbol_loggers[symbol]
    
    log_config = config.get('logging', {})
    symbol_log_level = log_config.get('symbol_log_level', 'DEBUG')
    symbol_log_dir = log_config.get('symbol_log_dir', 'logs/symbols')
    
    # Create symbol log directory if it doesn't exist
    if not os.path.exists(symbol_log_dir):
        os.makedirs(symbol_log_dir, exist_ok=True)
    
    # Create logger for this symbol
    logger_name = f'TradingBot.Symbol.{symbol}'
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, symbol_log_level.upper()))
    
    # Prevent propagation to root logger (we want separate files)
    logger.propagate = False
    
    # Create symbol-specific log file (daily rotation)
    today = datetime.now().strftime('%Y-%m-%d')
    log_file = os.path.join(symbol_log_dir, f'{symbol}_{today}.log')
    
    # File handler for symbol logs
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(getattr(logging, symbol_log_level.upper()))
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    )
    logger.addHandler(file_handler)
    
    _symbol_loggers[symbol] = logger
    return logger

