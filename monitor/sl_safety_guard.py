"""
Global SL Safety Guard (Kill Switch)

CRITICAL SAFETY FIX: Monitors all open positions for SL=0.0 violations.
If any position has SL=0.0 or missing SL, immediately:
- Close the position
- Log CRITICAL
- Mark system UNSAFE
- Disable new trades

This guard runs independently of workers and survives watchdog restarts.
"""

import threading
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from utils.logger_factory import get_logger
from utils.system_health import mark_system_unsafe

logger = get_logger("sl_safety_guard", "logs/live/monitor/sl_safety_guard.log")


class SLSafetyGuard:
    """
    Global SL Safety Guard - Independent monitoring thread that checks all positions for SL=0.0.
    
    CRITICAL: This is a fail-safe mechanism that runs independently of all other systems.
    It must execute within <50ms per check to avoid blocking.
    """
    
    def __init__(self, order_manager, risk_manager, trading_bot):
        """
        Initialize SL Safety Guard.
        
        Args:
            order_manager: OrderManager instance
            risk_manager: RiskManager instance
            trading_bot: TradingBot instance (for kill switch)
        """
        self.order_manager = order_manager
        self.risk_manager = risk_manager
        self.trading_bot = trading_bot
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._check_interval = 0.1  # 100ms check interval (10 checks per second)
        self._last_check_time = 0.0
        
        # Statistics
        self._violations_detected = 0
        self._positions_closed = 0
        
        logger.info("[SL_SAFETY_GUARD_INIT] Global SL Safety Guard initialized")
    
    def start(self):
        """Start the safety guard monitoring thread."""
        if self._running:
            logger.warning("[SL_SAFETY_GUARD] Already running")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="SLSafetyGuard")
        self._thread.start()
        logger.info("[SL_SAFETY_GUARD_STARTED] Monitoring thread started")
    
    def stop(self):
        """Stop the safety guard monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("[SL_SAFETY_GUARD_STOPPED] Monitoring thread stopped")
    
    def _monitor_loop(self):
        """Main monitoring loop - checks all positions for SL=0.0 violations."""
        logger.info("[SL_SAFETY_GUARD_LOOP] Monitoring loop started")
        
        while self._running:
            try:
                start_time = time.time()
                
                # Get all open positions
                positions = self.order_manager.get_open_positions()
                
                if positions:
                    violations = []
                    
                    for position in positions:
                        ticket = position.get('ticket', 0)
                        symbol = position.get('symbol', '')
                        sl = position.get('sl', 0.0)
                        
                        # CRITICAL: Check if SL is 0.0 or missing
                        if sl == 0.0 or sl is None:
                            violations.append({
                                'ticket': ticket,
                                'symbol': symbol,
                                'sl': sl,
                                'position': position
                            })
                    
                    # If violations detected, handle them immediately
                    if violations:
                        self._handle_violations(violations)
                
                # Track check time
                check_duration = (time.time() - start_time) * 1000  # Convert to ms
                self._last_check_time = check_duration
                
                # Warn if check takes too long (>50ms target)
                if check_duration > 50:
                    logger.warning(f"[SL_SAFETY_GUARD_SLOW] Check took {check_duration:.1f}ms (target: <50ms)")
                
                # Sleep for check interval
                time.sleep(self._check_interval)
                
            except Exception as e:
                logger.error(f"[SL_SAFETY_GUARD_ERROR] Exception in monitor loop: {e}", exc_info=True)
                time.sleep(self._check_interval)
    
    def _handle_violations(self, violations: list):
        """
        Handle SL=0.0 violations - close positions and activate kill switch.
        
        Args:
            violations: List of violation dicts with ticket, symbol, sl, position
        """
        self._violations_detected += len(violations)
        
        logger.critical(f"[SL_SAFETY_GUARD_VIOLATION] CRITICAL: {len(violations)} position(s) with SL=0.0 detected!")
        
        for violation in violations:
            ticket = violation['ticket']
            symbol = violation['symbol']
            sl = violation['sl']
            
            logger.critical(f"[SL_SAFETY_GUARD_VIOLATION] Ticket {ticket} | {symbol} | SL={sl} | "
                          f"CLOSING POSITION IMMEDIATELY FOR SAFETY")
            
            # Close position immediately
            try:
                success = self.order_manager.close_position(
                    ticket=ticket,
                    comment="SL Safety Guard: SL=0.0 violation - position closed for safety"
                )
                if success:
                    self._positions_closed += 1
                    logger.critical(f"[SL_SAFETY_GUARD_CLOSED] Ticket {ticket} | {symbol} | Position closed successfully")
                else:
                    logger.error(f"[SL_SAFETY_GUARD_CLOSE_FAILED] Ticket {ticket} | {symbol} | Failed to close position")
            except Exception as e:
                logger.error(f"[SL_SAFETY_GUARD_CLOSE_EXCEPTION] Ticket {ticket} | {symbol} | "
                           f"Exception closing position: {e}", exc_info=True)
        
        # Mark system as UNSAFE
        try:
            mark_system_unsafe(
                reason="sl_safety_guard_violation",
                details=f"{len(violations)} position(s) with SL=0.0 detected and closed"
            )
        except Exception as e:
            logger.error(f"[SL_SAFETY_GUARD] Exception marking system unsafe: {e}", exc_info=True)
        
        # Activate kill switch to prevent new trades
        try:
            if self.trading_bot:
                self.trading_bot.activate_kill_switch(
                    reason=f"SL Safety Guard: {len(violations)} position(s) with SL=0.0 detected"
                )
                logger.critical(f"[SL_SAFETY_GUARD_KILL_SWITCH] Kill switch activated - trading disabled")
        except Exception as e:
            logger.error(f"[SL_SAFETY_GUARD] Exception activating kill switch: {e}", exc_info=True)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get safety guard statistics."""
        return {
            'running': self._running,
            'check_interval_ms': self._check_interval * 1000,
            'last_check_time_ms': self._last_check_time,
            'violations_detected': self._violations_detected,
            'positions_closed': self._positions_closed
        }

