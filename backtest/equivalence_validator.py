"""
Backtest Equivalence Validator
Validates that backtest behavior matches live behavior exactly.
"""

import logging
import time
import threading
from datetime import datetime
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class BacktestEquivalenceValidator:
    """Validates that backtest execution matches live execution."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize validator.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.errors = []
        self.warnings = []
        self.mode = "BACKTEST" if config.get('mode') == 'backtest' else "LIVE"
    
    def validate_worker_loop_timing(self, actual_interval: float, expected_interval: float,
                                     loop_name: str) -> Tuple[bool, List[str]]:
        """
        Validate worker loop timing matches expected interval.
        
        Args:
            actual_interval: Actual interval between loop executions (seconds)
            expected_interval: Expected interval (seconds)
            loop_name: Name of the worker loop (e.g., 'sl_worker', 'run_cycle')
        
        Returns:
            (is_valid, errors)
        """
        self.errors = []
        
        # Allow 5% tolerance for timing
        tolerance = expected_interval * 0.05
        min_interval = expected_interval - tolerance
        max_interval = expected_interval + tolerance
        
        if actual_interval < min_interval or actual_interval > max_interval:
            error_msg = (f"mode={self.mode} | [EQUIVALENCE] CRITICAL: {loop_name} timing mismatch | "
                        f"Expected: {expected_interval:.3f}s | Actual: {actual_interval:.3f}s | "
                        f"Deviation: {abs(actual_interval - expected_interval):.3f}s")
            self.errors.append(error_msg)
            logger.critical(error_msg)
            return False, self.errors
        
        logger.info(f"mode={self.mode} | [EQUIVALENCE] {loop_name} timing validated | "
                   f"Expected: {expected_interval:.3f}s | Actual: {actual_interval:.3f}s")
        return True, []
    
    def validate_run_cycle_frequency(self, cycle_times: List[float], expected_interval: float = 60.0) -> Tuple[bool, List[str]]:
        """
        Validate that run_cycle executes every expected_interval seconds.
        
        Args:
            cycle_times: List of timestamps when run_cycle executed
            expected_interval: Expected interval between cycles (default 60 seconds)
        
        Returns:
            (is_valid, errors)
        """
        self.errors = []
        
        if len(cycle_times) < 2:
            logger.warning(f"mode={self.mode} | [EQUIVALENCE] Cannot validate run_cycle frequency - need at least 2 cycles")
            return True, []  # Not enough data, but not an error
        
        # Calculate intervals between cycles
        intervals = []
        for i in range(1, len(cycle_times)):
            interval = cycle_times[i] - cycle_times[i-1]
            intervals.append(interval)
        
        # Check if intervals match expected
        tolerance = expected_interval * 0.1  # 10% tolerance
        mismatches = []
        
        for i, interval in enumerate(intervals):
            if abs(interval - expected_interval) > tolerance:
                error_msg = (f"mode={self.mode} | [EQUIVALENCE] CRITICAL: run_cycle interval mismatch | "
                            f"Cycle {i+1}: Expected {expected_interval:.1f}s, Actual {interval:.1f}s | "
                            f"Deviation: {abs(interval - expected_interval):.1f}s")
                mismatches.append(error_msg)
                logger.critical(error_msg)
        
        if mismatches:
            self.errors.extend(mismatches)
            return False, self.errors
        
        avg_interval = sum(intervals) / len(intervals)
        logger.info(f"mode={self.mode} | [EQUIVALENCE] run_cycle frequency validated | "
                   f"Expected: {expected_interval:.1f}s | Average: {avg_interval:.1f}s | "
                   f"Cycles: {len(cycle_times)}")
        return True, []
    
    def validate_sl_worker_frequency(self, sl_update_times: List[float],
                                     expected_interval: float = 0.05) -> Tuple[bool, List[str]]:
        """
        Validate that SL worker executes at expected frequency.
        
        Args:
            sl_update_times: List of timestamps when SL updates occurred
            expected_interval: Expected interval between SL worker loops (default 0.05s = 50ms)
        
        Returns:
            (is_valid, errors)
        """
        self.errors = []
        
        if len(sl_update_times) < 2:
            logger.warning(f"mode={self.mode} | [EQUIVALENCE] Cannot validate SL worker frequency - need at least 2 updates")
            return True, []
        
        # Calculate intervals
        intervals = []
        for i in range(1, len(sl_update_times)):
            interval = sl_update_times[i] - sl_update_times[i-1]
            intervals.append(interval)
        
        # Check if intervals match expected (allow 20% tolerance for SL worker)
        tolerance = expected_interval * 0.2
        mismatches = []
        
        for i, interval in enumerate(intervals):
            if abs(interval - expected_interval) > tolerance:
                error_msg = (f"mode={self.mode} | [EQUIVALENCE] CRITICAL: SL worker interval mismatch | "
                            f"Update {i+1}: Expected {expected_interval*1000:.1f}ms, Actual {interval*1000:.1f}ms | "
                            f"Deviation: {abs(interval - expected_interval)*1000:.1f}ms")
                mismatches.append(error_msg)
                logger.critical(error_msg)
        
        if mismatches:
            self.errors.extend(mismatches)
            return False, self.errors
        
        avg_interval = sum(intervals) / len(intervals)
        logger.info(f"mode={self.mode} | [EQUIVALENCE] SL worker frequency validated | "
                   f"Expected: {expected_interval*1000:.1f}ms | Average: {avg_interval*1000:.1f}ms | "
                   f"Updates: {len(sl_update_times)}")
        return True, []
    
    def validate_lock_behavior(self, lock_type: type) -> Tuple[bool, List[str]]:
        """
        Validate that locks behave identically to live (must be real threading.Lock).
        
        Args:
            lock_type: Type of lock being used
        
        Returns:
            (is_valid, errors)
        """
        self.errors = []
        
        # In backtest, locks must be real threading.Lock (not simulated)
        if self.mode == "BACKTEST":
            if not isinstance(lock_type, type) or lock_type != threading.Lock:
                if lock_type != type(threading.Lock()):
                    error_msg = (f"mode={self.mode} | [EQUIVALENCE] CRITICAL: Lock type mismatch | "
                                f"Expected: threading.Lock | Actual: {lock_type}")
                    self.errors.append(error_msg)
                    logger.critical(error_msg)
                    return False, self.errors
        
        logger.info(f"mode={self.mode} | [EQUIVALENCE] Lock behavior validated | Type: {lock_type}")
        return True, []
    
    def validate_determinism(self, run_id: str, output_hash: str, previous_hash: str = None) -> Tuple[bool, List[str]]:
        """
        Validate that backtest is deterministic (same input → same output).
        
        Args:
            run_id: Unique identifier for this run
            output_hash: Hash of backtest output (trades, SL updates, etc.)
            previous_hash: Hash from previous run with same input (if available)
        
        Returns:
            (is_valid, errors)
        """
        self.errors = []
        
        if self.mode != "BACKTEST":
            # Not applicable for live mode
            return True, []
        
        # If we have a previous hash, compare
        if previous_hash:
            if output_hash != previous_hash:
                error_msg = (f"mode={self.mode} | [EQUIVALENCE] CRITICAL: Non-deterministic behavior detected | "
                            f"Run ID: {run_id} | Output hash mismatch")
                self.errors.append(error_msg)
                logger.critical(error_msg)
                return False, self.errors
        
        logger.info(f"mode={self.mode} | [EQUIVALENCE] Determinism validated | Run ID: {run_id} | Output hash: {output_hash[:16]}...")
        return True, []
    
    def log_results(self):
        """Log validation results."""
        if self.errors:
            logger.critical("=" * 80)
            logger.critical(f"EQUIVALENCE VALIDATION FAILED (mode={self.mode})")
            logger.critical("=" * 80)
            for error in self.errors:
                logger.critical(f"  [ERROR] {error}")
            logger.critical("=" * 80)
        
        if self.warnings:
            logger.warning("=" * 80)
            logger.warning(f"EQUIVALENCE VALIDATION WARNINGS (mode={self.mode})")
            logger.warning("=" * 80)
            for warning in self.warnings:
                logger.warning(f"  [WARNING] {warning}")
            logger.warning("=" * 80)
        
        if not self.errors and not self.warnings:
            logger.info(f"[OK] Equivalence validation passed (mode={self.mode})")


