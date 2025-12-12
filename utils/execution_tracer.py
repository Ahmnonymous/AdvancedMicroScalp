"""
Execution Tracer Module
Tracks function calls, execution flow, expected vs actual behavior, and failure reasons.
"""

import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from functools import wraps
from collections import deque
import json
import os
import sys

# Add project root to path for logger import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger_factory import get_logger

# Global tracer instance
_tracer_instance = None
_tracer_lock = threading.Lock()


class ExecutionTracer:
    """Tracks function execution, expected vs actual behavior, and failures."""
    
    def __init__(self, max_entries: int = 1000, log_file: str = "logs/live/system/execution_tracer.log"):
        self.max_entries = max_entries
        self.entries = deque(maxlen=max_entries)
        self.function_stats = {}  # Track call counts per function
        self.iterations = {}  # Track iteration numbers per function
        self.enabled = True
        self.lock = threading.Lock()
        self.start_time = time.time()
        
        # Setup file logger (no console output)
        # Use DEBUG level to capture all trace messages (OK, WARNING, ERROR, FAILED)
        import logging
        self.logger = get_logger("execution_tracer", log_file, level=logging.DEBUG)
        
    def trace(self, function_name: str, iteration: Optional[int] = None, 
              expected: Optional[str] = None, actual: Optional[str] = None,
              status: str = "OK", reason: Optional[str] = None,
              **kwargs) -> None:
        """
        Trace a function call or event.
        
        Args:
            function_name: Name of the function/event
            iteration: Iteration number (if applicable)
            expected: What should have happened
            actual: What actually happened
            status: "OK", "WARNING", "ERROR", "FAILED"
            reason: Reason for failure/warning (if applicable)
            **kwargs: Additional context data
        """
        if not self.enabled:
            return
        
        timestamp = datetime.now()
        elapsed = time.time() - self.start_time
        
        # Get iteration number if not provided
        if iteration is None:
            iteration = self.iterations.get(function_name, 0) + 1
            self.iterations[function_name] = iteration
        
        # Update function stats
        with self.lock:
            if function_name not in self.function_stats:
                self.function_stats[function_name] = {
                    'total_calls': 0,
                    'ok_count': 0,
                    'warning_count': 0,
                    'error_count': 0,
                    'failed_count': 0
                }
            
            stats = self.function_stats[function_name]
            stats['total_calls'] += 1
            
            if status == "OK":
                stats['ok_count'] += 1
            elif status == "WARNING":
                stats['warning_count'] += 1
            elif status == "ERROR":
                stats['error_count'] += 1
            elif status == "FAILED":
                stats['failed_count'] += 1
        
        # Create entry
        entry = {
            'timestamp': timestamp.isoformat(),
            'elapsed_seconds': round(elapsed, 3),
            'function': function_name,
            'iteration': iteration,
            'expected': expected,
            'actual': actual,
            'status': status,
            'reason': reason,
            'context': kwargs
        }
        
        with self.lock:
            self.entries.append(entry)
        
        # Format log message
        status_emoji = {
            "OK": "[OK]",
            "WARNING": "[WARNING]",
            "ERROR": "[ERROR]",
            "FAILED": "🚨"
        }.get(status, "🔍")
        
        log_parts = [
            f"[{timestamp.strftime('%H:%M:%S.%f')[:-3]}]",
            f"[+{elapsed:.3f}s]",
            status_emoji,
            f"{function_name}",
            f"(#{iteration})"
        ]
        
        if expected:
            log_parts.append(f"Expected: {expected}")
        if actual:
            log_parts.append(f"Actual: {actual}")
        if status != "OK":
            log_parts.append(f"Status: {status}")
        if reason:
            log_parts.append(f"Reason: {reason}")
        if kwargs:
            context_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
            log_parts.append(f"Context: {context_str}")
        
        # Write to log file instead of console
        log_message = " | ".join(log_parts)
        
        # Use appropriate log level based on status
        # Always log everything - use INFO for OK to ensure it's captured
        try:
            if status == "OK":
                self.logger.info(log_message)  # Changed from debug to info to ensure visibility
            elif status == "WARNING":
                self.logger.warning(log_message)
            elif status == "ERROR":
                self.logger.error(log_message)
            elif status == "FAILED":
                self.logger.critical(log_message)
            else:
                self.logger.info(log_message)
        except Exception as e:
            # Fallback: if logger fails, at least try to write to a simple file
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{datetime.now().isoformat()} | {log_message}\n")
            except:
                pass  # Silently fail if even file write fails
    
    def get_recent_entries(self, count: int = 50, function_name: Optional[str] = None) -> list:
        """Get recent trace entries."""
        with self.lock:
            entries = list(self.entries)
            if function_name:
                entries = [e for e in entries if e['function'] == function_name]
            return entries[-count:]
    
    def get_function_stats(self, function_name: Optional[str] = None) -> Dict[str, Any]:
        """Get statistics for a function or all functions."""
        with self.lock:
            if function_name:
                return self.function_stats.get(function_name, {})
            return dict(self.function_stats)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all traced executions."""
        with self.lock:
            total_entries = len(self.entries)
            total_functions = len(self.function_stats)
            
            total_ok = sum(s['ok_count'] for s in self.function_stats.values())
            total_warning = sum(s['warning_count'] for s in self.function_stats.values())
            total_error = sum(s['error_count'] for s in self.function_stats.values())
            total_failed = sum(s['failed_count'] for s in self.function_stats.values())
            
            return {
                'total_entries': total_entries,
                'total_functions': total_functions,
                'total_ok': total_ok,
                'total_warning': total_warning,
                'total_error': total_error,
                'total_failed': total_failed,
                'uptime_seconds': round(time.time() - self.start_time, 2),
                'functions': dict(self.function_stats)
            }
    
    def clear(self):
        """Clear all trace entries."""
        with self.lock:
            self.entries.clear()
            self.function_stats.clear()
            self.iterations.clear()
            self.start_time = time.time()


def get_tracer() -> ExecutionTracer:
    """Get the global tracer instance."""
    global _tracer_instance
    with _tracer_lock:
        if _tracer_instance is None:
            _tracer_instance = ExecutionTracer()
        return _tracer_instance


def trace_function(function_name: Optional[str] = None, 
                   expected: Optional[str] = None,
                   log_args: bool = False,
                   log_result: bool = False):
    """
    Decorator to automatically trace function calls.
    
    Args:
        function_name: Custom function name (defaults to function.__name__)
        expected: What should happen in this function
        log_args: Whether to log function arguments
        log_result: Whether to log function return value
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            name = function_name or func.__name__
            
            # Log entry
            context = {}
            if log_args:
                context['args'] = str(args)[:200]  # Limit length
                context['kwargs'] = str(kwargs)[:200]
            
            tracer.trace(
                function_name=name,
                expected=expected or f"Execute {name}",
                actual=f"Calling {name}",
                status="OK",
                **context
            )
            
            try:
                result = func(*args, **kwargs)
                
                # Log success
                result_context = {}
                if log_result and result is not None:
                    result_str = str(result)
                    result_context['result'] = result_str[:200]  # Limit length
                
                tracer.trace(
                    function_name=name,
                    expected=expected or f"Execute {name}",
                    actual=f"{name} completed",
                    status="OK",
                    **result_context
                )
                
                return result
            except Exception as e:
                # Log failure
                tracer.trace(
                    function_name=name,
                    expected=expected or f"Execute {name}",
                    actual=f"{name} raised exception",
                    status="ERROR",
                    reason=str(e),
                    exception_type=type(e).__name__
                )
                raise
        
        return wrapper
    return decorator

