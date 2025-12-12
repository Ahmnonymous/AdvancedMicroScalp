"""
Real-Time Stop-Loss (SL) Monitoring System
Monitors all live trades, SL updates, and rule violations in real-time.

This module provides:
- Live console dashboard with color-coded status
- Violation detection and alerts
- Real-time updates every 500ms
- Comprehensive logging
- Thread-safe access to SL manager
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from collections import defaultdict

try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.layout import Layout
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("[WARNING]  Warning: 'rich' library not found. Install with: pip install rich")
    print("   Falling back to basic console output")

from utils.logger_factory import get_logger

logger = get_logger("sl_monitor", "logs/live/monitor/sl_monitor.log")


class SLRealtimeMonitor:
    """
    Real-Time Stop-Loss Monitoring System
    
    Monitors all live trades and displays:
    - Ticket ID, Symbol, BUY/SELL
    - Entry Price, Current Price, Current P/L
    - Applied SL, Effective SL (USD)
    - Last SL Update Reason, Time of last update
    - Violation alerts
    """
    
    def __init__(self, bot):
        """
        Initialize SL Realtime Monitor.
        
        Args:
            bot: TradingBot instance with risk_manager and sl_manager
        """
        self.bot = bot
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.shutdown_event = threading.Event()
        
        # Update interval (500ms)
        self.update_interval = 0.5
        
        # Track last SL update times per ticket
        self.last_sl_update_time = {}  # {ticket: datetime}
        self.last_sl_update_reason = {}  # {ticket: str}
        self.last_effective_sl = {}  # {ticket: float}
        self.last_applied_sl = {}  # {ticket: float}
        
        # Violation tracking
        self.violations = []  # List of violation dicts
        self.violation_count = defaultdict(int)  # {ticket: count}
        
        # Alert debouncing (5 seconds per ticket)
        self._alert_debounce = {}  # {ticket: {alert_type: last_alert_time}}
        self._alert_debounce_window = 5.0  # 5 seconds
        
        # SL update tracking for alerts
        self._sl_update_attempts = {}  # {ticket: {last_attempt_time, last_result}}
        self._broker_rejections = {}  # {ticket: {last_rejection_time, count}}
        
        # Console setup
        if RICH_AVAILABLE:
            self.console = Console()
        else:
            self.console = None
        
        logger.info("SL Realtime Monitor initialized")
    
    def start(self, standalone_display: bool = False):
        """
        Start the monitoring.
        
        Args:
            standalone_display: If True, start a separate console display thread.
                               If False, monitor is passive (integrated mode).
        """
        if standalone_display:
            # Start standalone console display thread
            self.running = True
            self.monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="SLMonitor",
                daemon=True
            )
            self.monitor_thread.start()
            logger.info("SL Realtime Monitor started (standalone display mode)")
        else:
            # Monitor is passive - just tracks data
            # No separate thread needed as it's called from summary display loop
            logger.info("SL Realtime Monitor initialized (integrated mode)")
    
    def stop(self):
        """Stop the monitoring."""
        self.running = False
        self.shutdown_event.set()
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=2.0)
        logger.info("SL Realtime Monitor stopped")
    
    def _monitor_loop(self):
        """
        Main monitoring loop - displays live console dashboard every 500ms.
        
        This runs in a separate daemon thread and provides a live Rich console
        display of all positions and their SL status.
        """
        if not RICH_AVAILABLE:
            logger.warning("Rich library not available - cannot display live console dashboard")
            return
        
        try:
            with Live(self.console, refresh_per_second=2, screen=False) as live:
                while self.running and not self.shutdown_event.is_set():
                    try:
                        # Get positions
                        positions = self._get_positions()
                        
                        # Create and update dashboard
                        dashboard = self._create_rich_dashboard(positions)
                        live.update(dashboard)
                        
                        # Wait for next update (500ms)
                        self.shutdown_event.wait(self.update_interval)
                    except Exception as e:
                        logger.error(f"Error in monitor loop: {e}", exc_info=True)
                        time.sleep(self.update_interval)
        except Exception as e:
            logger.error(f"Error starting monitor loop: {e}", exc_info=True)
    
    def _get_sl_manager(self):
        """Get SL manager from bot's risk manager."""
        try:
            if self.bot and hasattr(self.bot, 'risk_manager'):
                risk_manager = self.bot.risk_manager
                if hasattr(risk_manager, 'sl_manager') and risk_manager.sl_manager:
                    return risk_manager.sl_manager
        except Exception as e:
            logger.error(f"Error getting SL manager: {e}")
        return None
    
    def _get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions."""
        try:
            if self.bot and hasattr(self.bot, 'order_manager'):
                return self.bot.order_manager.get_open_positions(exclude_dec8=True)
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
        return []
    
    def _calculate_effective_sl(self, position: Dict[str, Any]) -> float:
        """Calculate effective SL in USD for a position."""
        sl_manager = self._get_sl_manager()
        if sl_manager:
            try:
                return sl_manager.get_effective_sl_profit(position)
            except Exception as e:
                logger.debug(f"Error calculating effective SL: {e}")
        
        # Fallback to risk manager
        if self.bot and hasattr(self.bot, 'risk_manager'):
            try:
                effective_sl, _ = self.bot.risk_manager.calculate_effective_sl_in_profit_terms(
                    position, check_pending=False
                )
                return effective_sl
            except Exception as e:
                logger.debug(f"Error calculating effective SL (fallback): {e}")
        
        return -2.0  # Default to -$2.00
    
    def _detect_violations(self, position: Dict[str, Any], effective_sl: float) -> List[Dict[str, Any]]:
        """
        Detect SL rule violations.
        
        Returns list of violation dicts with:
        - type: violation type
        - ticket: position ticket
        - symbol: trading symbol
        - message: violation message
        - severity: 'critical', 'warning', 'info'
        """
        violations = []
        ticket = position.get('ticket')
        symbol = position.get('symbol', 'N/A')
        current_profit = position.get('profit', 0.0)
        applied_sl = position.get('sl', 0.0)
        
        # Get SL manager for max risk
        sl_manager = self._get_sl_manager()
        max_risk = sl_manager.max_risk_usd if sl_manager else 2.0
        
        # Violation 1: SL > strict loss (-$2.00) for negative trades
        if current_profit < 0:
            if effective_sl < -max_risk - 0.05:  # Worse than -$2.05 (tolerance)
                violations.append({
                    'type': 'strict_loss_violation',
                    'ticket': ticket,
                    'symbol': symbol,
                    'message': f"SL violation: Effective SL ${effective_sl:.2f} worse than -${max_risk:.2f} limit",
                    'severity': 'critical',
                    'current_profit': current_profit,
                    'effective_sl': effective_sl,
                    'max_risk': max_risk
                })
        
        # Violation 2: SL decreased incorrectly (for profitable trades)
        if current_profit > 0 and ticket in self.last_effective_sl:
            last_effective = self.last_effective_sl[ticket]
            if effective_sl < last_effective - 0.01:  # Decreased by more than $0.01
                violations.append({
                    'type': 'sl_decreased',
                    'ticket': ticket,
                    'symbol': symbol,
                    'message': f"SL decreased: ${last_effective:.2f} → ${effective_sl:.2f}",
                    'severity': 'warning',
                    'previous_sl': last_effective,
                    'current_sl': effective_sl
                })
        
        # Violation 3: SL not applied for 500ms or longer (for trades needing SL)
        if ticket in self.last_sl_update_time:
            time_since_update = (datetime.now() - self.last_sl_update_time[ticket]).total_seconds()
            if time_since_update > 0.5:  # More than 500ms since last update
                # Only flag if trade needs SL update
                needs_sl = (
                    (current_profit < 0 and effective_sl < -max_risk - 0.05) or
                    (current_profit > 0.03 and effective_sl < 0.01)
                )
                if needs_sl:
                    violations.append({
                        'type': 'sl_stale',
                        'ticket': ticket,
                        'symbol': symbol,
                        'message': f"SL not updated for {time_since_update:.1f}s",
                        'severity': 'warning',
                        'time_since_update': time_since_update
                    })
        
        # Violation 4: Broker rejected SL update (detected via SL update attempts)
        if ticket in self._sl_update_attempts:
            attempt_info = self._sl_update_attempts[ticket]
            last_result = attempt_info.get('last_result', 'unknown')
            if last_result == 'rejected' or last_result == 'failed':
                # Check debounce
                last_alert_time = self._alert_debounce.get(ticket, {}).get('broker_rejection', datetime.min)
                time_since_alert = (datetime.now() - last_alert_time).total_seconds()
                
                if time_since_alert >= self._alert_debounce_window:
                    violations.append({
                        'type': 'broker_rejection',
                        'ticket': ticket,
                        'symbol': symbol,
                        'message': f"Broker rejected SL update (last attempt: {attempt_info.get('last_attempt_time', 'unknown')})",
                        'severity': 'warning',
                        'rejection_count': self._broker_rejections.get(ticket, {}).get('count', 0)
                    })
        
        return violations
    
    def _update_sl_tracking(self, position: Dict[str, Any], effective_sl: float):
        """Update tracking for SL changes."""
        ticket = position.get('ticket')
        applied_sl = position.get('sl', 0.0)
        
        # Check if SL changed
        if ticket in self.last_applied_sl:
            if abs(applied_sl - self.last_applied_sl[ticket]) > 0.00001:  # SL changed
                self.last_sl_update_time[ticket] = datetime.now()
                # Try to get update reason from SL manager tracking
                sl_manager = self._get_sl_manager()
                if sl_manager and hasattr(sl_manager, '_position_tracking'):
                    with sl_manager._tracking_lock:
                        tracking = sl_manager._position_tracking.get(ticket, {})
                        self.last_sl_update_reason[ticket] = tracking.get('last_update_reason', 'Unknown')
                else:
                    self.last_sl_update_reason[ticket] = 'SL Updated'
        
        self.last_applied_sl[ticket] = applied_sl
        self.last_effective_sl[ticket] = effective_sl
    
    def _log_violations(self, violations: List[Dict[str, Any]]):
        """Log violations to file with debouncing."""
        current_time = datetime.now()
        
        for violation in violations:
            ticket = violation.get('ticket')
            symbol = violation.get('symbol', 'N/A')
            violation_type = violation.get('type', 'unknown')
            message = violation.get('message', '')
            severity = violation.get('severity', 'info')
            
            # Check debounce (5 seconds per ticket per alert type)
            alert_key = f"{ticket}_{violation_type}"
            if ticket not in self._alert_debounce:
                self._alert_debounce[ticket] = {}
            
            last_alert_time = self._alert_debounce[ticket].get(violation_type, datetime.min)
            time_since_alert = (current_time - last_alert_time).total_seconds()
            
            # Only log if debounce window has passed
            if time_since_alert >= self._alert_debounce_window:
                # Log violation (console + file)
                log_message = f"SL VIOLATION [{severity.upper()}] | " \
                            f"Ticket: {ticket} | Symbol: {symbol} | " \
                            f"Type: {violation_type} | {message}"
                
                if severity == 'critical':
                    logger.critical(log_message)
                    # Also print to console for critical violations
                    if self.console:
                        self.console.print(f"[bold red]{log_message}[/]")
                else:
                    logger.warning(log_message)
                
                # Update debounce timestamp
                self._alert_debounce[ticket][violation_type] = current_time
                
                # Track violation count
                self.violation_count[ticket] += 1
                
                # Add to violations list (keep last 50)
                self.violations.append({
                    'timestamp': current_time,
                    **violation
                })
                if len(self.violations) > 50:
                    self.violations.pop(0)
            else:
                # Debounced - log at debug level only
                logger.debug(f"SL VIOLATION DEBOUNCED [{severity.upper()}] | "
                           f"Ticket: {ticket} | Symbol: {symbol} | "
                           f"Type: {violation_type} | Last alert {time_since_alert:.1f}s ago")
    
    def track_sl_update_attempt(self, ticket: int, success: bool, reason: str = ""):
        """
        Track SL update attempt for alerting.
        
        Args:
            ticket: Position ticket
            success: True if SL update succeeded
            reason: Reason for success/failure
        """
        current_time = datetime.now()
        
        if ticket not in self._sl_update_attempts:
            self._sl_update_attempts[ticket] = {}
        
        self._sl_update_attempts[ticket]['last_attempt_time'] = current_time
        self._sl_update_attempts[ticket]['last_result'] = 'success' if success else 'failed'
        self._sl_update_attempts[ticket]['last_reason'] = reason
        
        # Track broker rejections
        if not success and 'reject' in reason.lower() or 'invalid' in reason.lower():
            if ticket not in self._broker_rejections:
                self._broker_rejections[ticket] = {'count': 0, 'last_rejection_time': None}
            
            self._broker_rejections[ticket]['count'] += 1
            self._broker_rejections[ticket]['last_rejection_time'] = current_time
    
    # NOTE: Dashboard creation methods removed - display is handled by launch_system.py
    # This monitor only provides data (violation detection, tracking) to the summary display
    
    def _create_rich_dashboard(self, positions: List[Dict[str, Any]]):
        """
        Create Rich console dashboard.
        
        Returns Rich renderable (Table or Layout) for Live display.
        """
        # Create main table
        table = Table(title="[SL] Real-Time Stop-Loss Monitor", show_header=True, header_style="bold cyan")
        
        table.add_column("Ticket", style="cyan", width=8)
        table.add_column("Symbol", style="magenta", width=10)
        table.add_column("Side", width=6)  # BUY/SELL
        table.add_column("Entry", justify="right", width=10)
        table.add_column("CurrentPrice", justify="right", width=12)
        table.add_column("CurrentP/L", justify="right", width=12)
        table.add_column("Applied SL", justify="right", width=10)
        table.add_column("Effective SL", justify="right", width=12)
        table.add_column("LastReason", width=20)
        table.add_column("LastUpdateTime", width=12)
        table.add_column("StatusColor", width=12)
        
        # Process each position
        for position in positions:
            ticket = position.get('ticket', 0)
            symbol = position.get('symbol', 'N/A')
            order_type = position.get('type', 'N/A')
            entry_price = position.get('price_open', 0.0)
            current_price = position.get('price_current', 0.0)
            current_profit = position.get('profit', 0.0)
            applied_sl = position.get('sl', 0.0)
            
            # Calculate effective SL
            effective_sl = self._calculate_effective_sl(position)
            
            # Update tracking
            self._update_sl_tracking(position, effective_sl)
            
            # Detect violations
            violations = self._detect_violations(position, effective_sl)
            if violations:
                self._log_violations(violations)
            
            # Determine status color
            status_color = "green"
            status_text = "[OK] OK"
            if violations:
                critical = any(v.get('severity') == 'critical' for v in violations)
                status_color = "red" if critical else "yellow"
                status_text = "[W] VIOLATION" if critical else "[W] WARNING"
            elif current_profit < 0:
                # Check if SL is at strict loss
                sl_manager = self._get_sl_manager()
                max_risk = sl_manager.max_risk_usd if sl_manager else 2.0
                if abs(effective_sl + max_risk) < 0.05:
                    status_color = "green"
                    status_text = "[OK] Protected"
                else:
                    status_color = "yellow"
                    status_text = "[W] Pending"
            elif current_profit >= 0.03:
                # Check if SL is verified
                if effective_sl >= 0.03:
                    status_color = "green"
                    status_text = "[OK] Locked"
                else:
                    status_color = "yellow"
                    status_text = "[W] Pending"
            
            # Format prices
            entry_str = f"{entry_price:.5f}" if entry_price < 1000 else f"{entry_price:.2f}"
            current_str = f"{current_price:.5f}" if current_price < 1000 else f"{current_price:.2f}"
            sl_str = f"{applied_sl:.5f}" if applied_sl > 0 and applied_sl < 1000 else (f"{applied_sl:.2f}" if applied_sl > 0 else "N/A")
            
            # Format P/L
            profit_color = "green" if current_profit >= 0 else "red"
            profit_str = f"[{profit_color}]${current_profit:,.2f}[/]"
            
            # Format effective SL
            if effective_sl < 0:
                sl_color = "red"
            elif effective_sl < 0.01:
                sl_color = "yellow"
            else:
                sl_color = "green"
            effective_sl_str = f"[{sl_color}]${effective_sl:,.2f}[/]"
            
            # Get last update time
            last_update_str = "N/A"
            if ticket in self.last_sl_update_time:
                time_diff = (datetime.now() - self.last_sl_update_time[ticket]).total_seconds()
                if time_diff < 60:
                    last_update_str = f"{time_diff:.1f}s ago"
                else:
                    last_update_str = f"{time_diff/60:.1f}m ago"
            
            # Get last update reason
            reason = self.last_sl_update_reason.get(ticket, "N/A")
            
            # Add row with all required fields
            table.add_row(
                str(ticket),
                symbol,
                order_type,  # Side (BUY/SELL)
                entry_str,  # Entry
                current_str,  # CurrentPrice
                profit_str,  # CurrentP/L
                sl_str,  # Applied SL
                effective_sl_str,  # Effective SL (USD)
                reason[:18] if reason != "N/A" else "N/A",  # LastReason
                last_update_str,  # LastUpdateTime
                f"[{status_color}]{status_text}[/]"  # StatusColor
            )
        
        # Create violations panel
        violations_text = Text()
        if self.violations:
            recent_violations = self.violations[-10:]  # Last 10 violations
            for violation in recent_violations:
                timestamp = violation.get('timestamp', datetime.now())
                ticket = violation.get('ticket', 0)
                symbol = violation.get('symbol', 'N/A')
                message = violation.get('message', '')
                severity = violation.get('severity', 'info')
                
                color = "red" if severity == 'critical' else "yellow"
                violations_text.append(f"[{color}]{timestamp.strftime('%H:%M:%S')} | Ticket {ticket} ({symbol}) | {message}[/]\n")
        else:
            violations_text.append("[green]No violations detected[/]")
        
        violations_panel = Panel(
            violations_text,
            title="🚨 Recent Violations",
            border_style="red" if self.violations else "green"
        )
        
        # Create layout
        layout = Layout()
        layout.split_column(
            Layout(table, name="main", ratio=3),
            Layout(violations_panel, name="violations", ratio=1)
        )
        
        # Return layout for Live display (not string)
        return layout
    
    def _create_basic_dashboard(self, positions: List[Dict[str, Any]]) -> str:
        """Create basic console dashboard (fallback)."""
        output = []
        output.append("=" * 120)
        output.append("[SL] REAL-TIME STOP-LOSS MONITOR")
        output.append("=" * 120)
        output.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output.append("")
        
        # Header
        output.append(f"{'Ticket':<8} | {'Symbol':<10} | {'Type':<6} | {'Entry':<10} | {'Current':<10} | "
                      f"{'P/L':<10} | {'Applied SL':<10} | {'Effective SL':<12} | {'Status':<12} | "
                      f"{'Last Update':<12} | {'Reason'}")
        output.append("-" * 120)
        
        # Process each position
        for position in positions:
            ticket = position.get('ticket', 0)
            symbol = position.get('symbol', 'N/A')
            order_type = position.get('type', 'N/A')
            entry_price = position.get('price_open', 0.0)
            current_price = position.get('price_current', 0.0)
            current_profit = position.get('profit', 0.0)
            applied_sl = position.get('sl', 0.0)
            
            # Calculate effective SL
            effective_sl = self._calculate_effective_sl(position)
            
            # Update tracking
            self._update_sl_tracking(position, effective_sl)
            
            # Detect violations
            violations = self._detect_violations(position, effective_sl)
            if violations:
                self._log_violations(violations)
            
            # Determine status
            status = "[OK] OK"
            if violations:
                critical = any(v.get('severity') == 'critical' for v in violations)
                status = "[W] VIOLATION" if critical else "[W] WARNING"
            elif current_profit < 0:
                sl_manager = self._get_sl_manager()
                max_risk = sl_manager.max_risk_usd if sl_manager else 2.0
                if abs(effective_sl + max_risk) < 0.05:
                    status = "[OK] Protected"
                else:
                    status = "[W] Pending"
            elif current_profit >= 0.03:
                if effective_sl >= 0.03:
                    status = "[OK] Locked"
                else:
                    status = "[W] Pending"
            
            # Format values
            entry_str = f"{entry_price:.5f}" if entry_price < 1000 else f"{entry_price:.2f}"
            current_str = f"{current_price:.5f}" if current_price < 1000 else f"{current_price:.2f}"
            sl_str = f"{applied_sl:.5f}" if applied_sl > 0 and applied_sl < 1000 else (f"{applied_sl:.2f}" if applied_sl > 0 else "N/A")
            profit_str = f"${current_profit:,.2f}"
            effective_sl_str = f"${effective_sl:,.2f}"
            
            # Get last update
            last_update_str = "N/A"
            if ticket in self.last_sl_update_time:
                time_diff = (datetime.now() - self.last_sl_update_time[ticket]).total_seconds()
                if time_diff < 60:
                    last_update_str = f"{time_diff:.1f}s ago"
                else:
                    last_update_str = f"{time_diff/60:.1f}m ago"
            
            reason = self.last_sl_update_reason.get(ticket, "N/A")
            
            # Add row
            output.append(f"{ticket:<8} | {symbol:<10} | {order_type:<6} | {entry_str:<10} | {current_str:<10} | "
                          f"{profit_str:<10} | {sl_str:<10} | {effective_sl_str:<12} | {status:<12} | "
                          f"{last_update_str:<12} | {reason}")
        
        # Add violations section
        output.append("")
        output.append("=" * 120)
        output.append("🚨 RECENT VIOLATIONS")
        output.append("=" * 120)
        if self.violations:
            for violation in self.violations[-10:]:
                timestamp = violation.get('timestamp', datetime.now())
                ticket = violation.get('ticket', 0)
                symbol = violation.get('symbol', 'N/A')
                message = violation.get('message', '')
                severity = violation.get('severity', 'info')
                output.append(f"[{severity.upper()}] {timestamp.strftime('%H:%M:%S')} | Ticket {ticket} ({symbol}) | {message}")
        else:
            output.append("No violations detected")
        
        output.append("=" * 120)
        output.append(f"Updates every {self.update_interval*1000:.0f}ms | Press Ctrl+C to stop")
        
        return "\n".join(output)
    
    # NOTE: _monitor_loop removed - monitoring is done inline in summary display
    # This prevents overlapping displays
    
    def get_violation_summary(self) -> Dict[str, Any]:
        """Get summary of violations."""
        return {
            'total_violations': len(self.violations),
            'violations_by_ticket': dict(self.violation_count),
            'recent_violations': self.violations[-10:] if self.violations else []
        }

