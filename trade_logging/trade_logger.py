"""
Unified Trade Logger Module
Provides comprehensive logging for trade execution, position closures, and trailing stops.
Now supports both text logs (for readability) and JSONL entries (for machine processing).
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from utils.logger_factory import get_symbol_logger, get_logger


class TradeLogger:
    """Unified trade logging system with comprehensive trade tracking."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        # Determine if we're in backtest mode
        self.is_backtest = config.get('mode') == 'backtest'
        self.trades_log_dir = 'logs/backtest/trades' if self.is_backtest else 'logs/live/trades'
        self._ensure_trades_directory()
        
        # PHASE 1 FIX 1.1: Trade logging reliability metrics
        self.log_success_count = 0
        self.log_failure_count = 0
        self.log_retry_count = 0
        self.log_timeout_count = 0
        
        # Get system error logger for fallback
        error_log_path = 'logs/backtest/system_errors.log' if self.is_backtest else 'logs/live/system/system_errors.log'
        self.error_logger = get_logger('trade_logger_errors', error_log_path)
    
    def _ensure_trades_directory(self):
        """Ensure trades directory exists."""
        os.makedirs(self.trades_log_dir, exist_ok=True)
    
    def _write_jsonl_entry(self, symbol: str, entry: Dict[str, Any], max_retries: int = 3, timeout_ms: int = 500) -> Tuple[bool, Optional[str]]:
        """
        Write JSONL entry to symbol log file with retry mechanism.
        
        PHASE 1 FIX 1.1: Trade Logging Reliability
        - Synchronous and blocking (waits for write confirmation)
        - Retry mechanism: 3 attempts with exponential backoff (100ms, 200ms, 400ms)
        - Timeout protection: fails fast if disk I/O hangs (>500ms)
        - Fallback: writes to system error log if symbol log fails
        
        Args:
            symbol: Trading symbol
            entry: JSONL entry dictionary
            max_retries: Maximum retry attempts (default: 3)
            timeout_ms: Maximum time to wait for write (default: 500ms)
            
        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        log_file = f'{self.trades_log_dir}/{symbol}.log'
        retry_delays = [0.1, 0.2, 0.4]  # 100ms, 200ms, 400ms
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                
                # Attempt write with timeout protection
                with open(log_file, 'a', encoding='utf-8') as f:
                    json_str = json.dumps(entry, ensure_ascii=False) + '\n'
                    f.write(json_str)
                    f.flush()  # Force write to disk
                    os.fsync(f.fileno())  # Ensure OS-level write completion
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                # Check for timeout
                if elapsed_ms > timeout_ms:
                    error_msg = f"Write timeout: {elapsed_ms:.1f}ms > {timeout_ms}ms"
                    self.log_timeout_count += 1
                    if attempt < max_retries - 1:
                        self.log_retry_count += 1
                        time.sleep(retry_delays[attempt])
                        continue
                    else:
                        # Final attempt failed, use fallback
                        self._write_fallback_log(symbol, entry, error_msg)
                        self.log_failure_count += 1
                        return False, error_msg
                
                # Verify write succeeded by checking file size
                if os.path.exists(log_file):
                    file_size = os.path.getsize(log_file)
                    if file_size > 0:
                        self.log_success_count += 1
                        return True, None
                
                # File exists but size is 0 or write didn't complete
                error_msg = "Write verification failed: file size is 0"
                if attempt < max_retries - 1:
                    self.log_retry_count += 1
                    time.sleep(retry_delays[attempt])
                    continue
                else:
                    self._write_fallback_log(symbol, entry, error_msg)
                    self.log_failure_count += 1
                    return False, error_msg
                    
            except Exception as e:
                error_msg = f"Exception during write (attempt {attempt + 1}/{max_retries}): {e}"
                if attempt < max_retries - 1:
                    self.log_retry_count += 1
                    time.sleep(retry_delays[attempt])
                    continue
                else:
                    # Final attempt failed, use fallback
                    self._write_fallback_log(symbol, entry, error_msg)
                    self.log_failure_count += 1
                    return False, error_msg
        
        # Should never reach here, but safety check
        error_msg = "All retry attempts exhausted"
        self._write_fallback_log(symbol, entry, error_msg)
        self.log_failure_count += 1
        return False, error_msg
    
    def _write_fallback_log(self, symbol: str, entry: Dict[str, Any], error_msg: str):
        """
        Fallback: Write to system error log if symbol log fails.
        
        PHASE 1 FIX 1.1: Ensures trade is never lost even if symbol log fails.
        """
        try:
            ticket = entry.get('order_id', 'UNKNOWN')
            fallback_entry = {
                'timestamp': entry.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                'symbol': symbol,
                'ticket': ticket,
                'original_entry': entry,
                'log_error': error_msg,
                'fallback_reason': 'symbol_log_failed'
            }
            self.error_logger.error(
                f"[FALLBACK_LOG] Trade {ticket} ({symbol}) | "
                f"Symbol log failed, writing to error log | "
                f"Error: {error_msg} | "
                f"Entry: {json.dumps(fallback_entry, ensure_ascii=False)}"
            )
        except Exception as fallback_error:
            # Last resort: print to console (should never happen)
            print(f"CRITICAL: Failed to write fallback log for trade {entry.get('order_id', 'UNKNOWN')}: {fallback_error}")
    
    def get_logging_metrics(self) -> Dict[str, Any]:
        """
        Get trade logging reliability metrics.
        
        PHASE 1 FIX 1.1: Returns metrics for validation.
        """
        total_attempts = self.log_success_count + self.log_failure_count
        success_rate = (self.log_success_count / total_attempts * 100) if total_attempts > 0 else 0.0
        
        return {
            'log_success_count': self.log_success_count,
            'log_failure_count': self.log_failure_count,
            'log_retry_count': self.log_retry_count,
            'log_timeout_count': self.log_timeout_count,
            'log_success_rate': success_rate,
            'total_attempts': total_attempts
        }
    
    def log_trade_execution(
        self,
        symbol: str,
        ticket: int,
        signal: str,
        entry_price_requested: float,
        entry_price_actual: float,
        lot_size: float,
        stop_loss_pips: float,
        stop_loss_price: float,
        quality_score: Optional[float] = None,
        spread_points: Optional[float] = None,
        spread_fees_cost: Optional[float] = None,
        slippage: Optional[float] = None,
        risk_usd: Optional[float] = None,
        **kwargs
    ):
        """
        Log trade execution with comprehensive details.
        
        Args:
            symbol: Trading symbol
            ticket: Position ticket number
            signal: 'LONG' or 'SHORT'
            entry_price_requested: Requested entry price
            entry_price_actual: Actual fill price from MT5
            lot_size: Lot size used
            stop_loss_pips: Stop loss in pips
            stop_loss_price: Stop loss price
            quality_score: Quality score (0-100)
            spread_points: Spread in points
            spread_fees_cost: Total spread + fees cost in USD
            slippage: Entry slippage
            risk_usd: Calculated risk in USD
            **kwargs: Additional fields to log
        """
        # Calculate slippage if not provided
        if slippage is None:
            slippage = abs(entry_price_actual - entry_price_requested)
        
        # Symbol logger (logs to logs/live/trades/SYMBOL.log or logs/backtest/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol, is_backtest=self.is_backtest)
        symbol_logger.info("=" * 80)
        symbol_logger.info(f"[OK] TRADE EXECUTED SUCCESSFULLY")
        symbol_logger.info(f"   Symbol: {symbol}")
        symbol_logger.info(f"   Direction: {signal}")
        symbol_logger.info(f"   Ticket: {ticket}")
        symbol_logger.info(f"   Entry Price (Requested): {entry_price_requested:.5f}")
        symbol_logger.info(f"   Entry Price (Actual Fill): {entry_price_actual:.5f}")
        if slippage > 0.00001:
            symbol_logger.info(f"   Entry Slippage: {slippage:.5f}")
        symbol_logger.info(f"   Lot Size: {lot_size:.4f}")
        symbol_logger.info(f"   Stop Loss: {stop_loss_pips:.1f} pips ({stop_loss_price:.5f})")
        if risk_usd is not None:
            symbol_logger.info(f"   Risk: ${risk_usd:.2f} USD")
        if spread_points is not None:
            symbol_logger.info(f"   Spread: {spread_points:.1f} points")
        if spread_fees_cost is not None:
            symbol_logger.info(f"   Spread+Fees Cost: ${spread_fees_cost:.2f} USD")
        if quality_score is not None:
            symbol_logger.info(f"   Quality Score: {quality_score:.1f}")
        
        # Log additional fields
        for key, value in kwargs.items():
            if value is not None:
                symbol_logger.info(f"   {key.replace('_', ' ').title()}: {value}")
        
        symbol_logger.info("=" * 80)
        
        # Also write JSONL entry
        jsonl_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'trade_type': signal,
            'entry_price': entry_price_actual,
            'exit_price': None,
            'profit_usd': None,
            'status': 'OPEN',
            'order_id': str(ticket),
            'additional_info': {
                'entry_price_requested': entry_price_requested,
                'lot_size': lot_size,
                'stop_loss_pips': stop_loss_pips,
                'stop_loss_price': stop_loss_price,
                'slippage': slippage
            }
        }
        
        if quality_score is not None:
            jsonl_entry['additional_info']['quality_score'] = quality_score
        if spread_points is not None:
            jsonl_entry['additional_info']['spread_points'] = spread_points
        if spread_fees_cost is not None:
            jsonl_entry['additional_info']['spread_fees_cost'] = spread_fees_cost
        if risk_usd is not None:
            jsonl_entry['additional_info']['risk_usd'] = risk_usd
        
        # Add any additional kwargs
        if kwargs:
            jsonl_entry['additional_info'].update(kwargs)
        
        # PHASE 1 FIX 1.1: Validate log write success
        success, error_msg = self._write_jsonl_entry(symbol, jsonl_entry)
        if not success:
            # Log warning but don't block trade execution (circuit breaker)
            symbol_logger.warning(
                f"[WARNING] Trade logging failed after retries: {error_msg} | "
                f"Trade executed but may not be fully logged. Check system error log."
            )
            # Note: Trade execution continues - logging failure doesn't block trading
            # This is by design to prevent logging issues from blocking trades
    
    def log_position_closure(
        self,
        symbol: str,
        ticket: int,
        entry_price: float,
        close_price: float,
        profit: float,
        duration_minutes: float,
        close_reason: str,
        entry_time: Optional[datetime] = None,
        close_time: Optional[datetime] = None,
        **kwargs
    ):
        """
        Log position closure with comprehensive details from MT5 deal history.
        
        Args:
            symbol: Trading symbol
            ticket: Position ticket number
            entry_price: Actual entry price from deal
            close_price: Actual close price from deal
            profit: Actual profit/loss from MT5
            duration_minutes: Trade duration in minutes
            close_reason: Reason for closure (e.g., "Stop Loss", "Take Profit", "Trailing Stop")
            entry_time: Entry timestamp
            close_time: Close timestamp
            **kwargs: Additional fields to log
        """
        # Symbol logger (logs to logs/live/trades/SYMBOL.log or logs/backtest/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol, is_backtest=self.is_backtest)
        symbol_logger.info("=" * 80)
        symbol_logger.info(f"[-] POSITION CLOSED")
        symbol_logger.info(f"   Symbol: {symbol}")
        symbol_logger.info(f"   Ticket: {ticket}")
        if entry_time:
            symbol_logger.info(f"   Entry Time: {entry_time.strftime('%Y-%m-%d %H:%M:%S')}")
        symbol_logger.info(f"   Entry Price: {entry_price:.5f}")
        if close_time:
            symbol_logger.info(f"   Close Time: {close_time.strftime('%Y-%m-%d %H:%M:%S')}")
        symbol_logger.info(f"   Close Price: {close_price:.5f}")
        symbol_logger.info(f"   Profit/Loss: ${profit:.2f} USD")
        symbol_logger.info(f"   Duration: {duration_minutes:.1f} minutes")
        symbol_logger.info(f"   Close Reason: {close_reason}")
        
        # Log additional fields
        for key, value in kwargs.items():
            if value is not None:
                symbol_logger.info(f"   {key.replace('_', ' ').title()}: {value}")
        
        symbol_logger.info("=" * 80)
        
        # Also write/update JSONL entry
        jsonl_entry = {
            'timestamp': close_time.strftime('%Y-%m-%d %H:%M:%S') if close_time else datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'trade_type': None,  # Will be updated from existing entry
            'entry_price': entry_price,
            'exit_price': close_price,
            'profit_usd': profit,
            'status': 'CLOSED',
            'order_id': str(ticket),
            'additional_info': {
                'duration_minutes': duration_minutes,
                'close_reason': close_reason
            }
        }
        
        # Add any additional kwargs
        if kwargs:
            jsonl_entry['additional_info'].update(kwargs)
        
        # Update existing JSONL entry or append
        self._update_jsonl_entry(symbol, ticket, jsonl_entry)
    
    def _update_jsonl_entry(self, symbol: str, ticket: int, closure_data: Dict[str, Any]):
        """Update existing JSONL entry with closure data."""
        log_file = f'{self.trades_log_dir}/{symbol}.log'
        
        if not os.path.exists(log_file):
            # If file doesn't exist, just append
            self._write_jsonl_entry(symbol, closure_data)
            return
        
        # Read existing entries
        entries = []
        updated = False
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and line.startswith('{'):
                        try:
                            entry = json.loads(line)
                            if str(entry.get('order_id')) == str(ticket):
                                # Update this entry
                                entry.update({
                                    'timestamp': closure_data['timestamp'],
                                    'exit_price': closure_data['exit_price'],
                                    'profit_usd': closure_data['profit_usd'],
                                    'status': 'CLOSED'
                                })
                                # Merge additional_info
                                if 'additional_info' not in entry:
                                    entry['additional_info'] = {}
                                entry['additional_info'].update(closure_data['additional_info'])
                                updated = True
                            entries.append(entry)
                        except:
                            pass
            
            # If not updated, append new entry
            if not updated:
                entries.append(closure_data)
            
            # Sort chronologically and write back
            entries.sort(key=lambda x: x.get('timestamp', '0000-00-00 00:00:00'))
            
            with open(log_file, 'w', encoding='utf-8') as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        
        except Exception as e:
            # Fallback: just append with retry mechanism
            success, error_msg = self._write_jsonl_entry(symbol, closure_data)
            if not success:
                self.error_logger.error(
                    f"Failed to update JSONL entry in {log_file}: {e} | "
                    f"Fallback append also failed: {error_msg}"
                )
    
    def log_trailing_stop_adjustment(
        self,
        symbol: str,
        ticket: int,
        current_profit: float,
        new_sl_profit: float,
        new_sl_price: float,
        sl_pips: float,
        reason: str,
        timestamp: Optional[datetime] = None
    ):
        """
        Log trailing stop adjustment with timestamp.
        
        Args:
            symbol: Trading symbol
            ticket: Position ticket number
            current_profit: Current profit in USD
            new_sl_profit: New stop loss profit level in USD
            new_sl_price: New stop loss price
            sl_pips: Stop loss distance in pips
            reason: Reason for adjustment
            timestamp: Timestamp of adjustment
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        # Symbol logger (logs to logs/live/trades/SYMBOL.log or logs/backtest/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol, is_backtest=self.is_backtest)
        symbol_logger.info(
            f"[STATS] TRAILING STOP: Ticket {ticket} ({symbol}) | "
            f"Time: {timestamp.strftime('%H:%M:%S')} | "
            f"Profit: ${current_profit:.2f} | "
            f"SL Profit: ${new_sl_profit:.2f} | "
            f"SL Price: {new_sl_price:.5f} ({abs(sl_pips):.1f} pips) | "
            f"Reason: {reason}"
        )
    
    def log_early_exit_prevention(
        self,
        symbol: str,
        ticket: int,
        attempted_sl_profit: float,
        max_risk: float
    ):
        """Log when early exit is prevented (SL would be worse than -$2.00)."""
        symbol_logger = get_symbol_logger(symbol, is_backtest=self.is_backtest)
        symbol_logger.warning(
            f"[WARNING] PREVENTED EARLY EXIT: Ticket {ticket} | "
            f"Attempted SL profit ${attempted_sl_profit:.2f} would be worse than max risk ${max_risk:.2f} | "
            f"SL update skipped to prevent early exit"
        )
    
    def log_late_exit_prevention(
        self,
        symbol: str,
        ticket: int,
        actual_profit: float,
        expected_profit: float,
        sl_modification_failed: bool = False
    ):
        """Log when late exit is detected or prevented."""
        symbol_logger = get_symbol_logger(symbol, is_backtest=self.is_backtest)
        if sl_modification_failed:
            symbol_logger.warning(
                f"[WARNING] LATE EXIT: Ticket {ticket} | "
                f"SL modification failed, position closed at ${actual_profit:.2f} (expected: ${expected_profit:.2f})"
            )
        else:
            symbol_logger.warning(
                f"[WARNING] LATE EXIT: Ticket {ticket} | "
                f"Position closed at ${actual_profit:.2f} (expected: ${expected_profit:.2f})"
            )
    
    def log_micro_profit_close(
        self,
        ticket: int,
        symbol: str,
        profit: float,
        entry_price_actual: float,
        close_price: float,
        spread_points: float,
        execution_time_ms: float
    ):
        """
        Log micro-HFT profit close with comprehensive details.
        
        Args:
            ticket: Position ticket number
            symbol: Trading symbol
            profit: Actual profit in USD
            entry_price_actual: Actual entry price from fill
            close_price: Actual close price from fill
            spread_points: Spread in points
            execution_time_ms: Execution time in milliseconds
        """
        # Symbol logger (logs to logs/live/trades/SYMBOL.log or logs/backtest/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol, is_backtest=self.is_backtest)
        symbol_logger.info("=" * 80)
        symbol_logger.info(f"[FAST] MICRO-HFT PROFIT CLOSE")
        symbol_logger.info(f"   Symbol: {symbol}")
        symbol_logger.info(f"   Ticket: {ticket}")
        symbol_logger.info(f"   Entry Price (Actual Fill): {entry_price_actual:.5f}")
        symbol_logger.info(f"   Close Price (Actual Fill): {close_price:.5f}")
        symbol_logger.info(f"   Profit: ${profit:.2f} USD")
        symbol_logger.info(f"   Spread: {spread_points:.1f} points")
        symbol_logger.info(f"   Execution Time: {execution_time_ms:.2f} ms")
        # CRITICAL FIX 1.3: Determine close reason based on ACTUAL profit
        # If profit is negative, this is an error condition
        if profit <= 0:
            close_reason_text = f"Micro-HFT ERROR: Negative profit attempted (${profit:.2f}) - This should never happen"
            symbol_logger.error(f"   [WARNING] CLOSE REASON: {close_reason_text}")
        elif 0.03 <= profit <= 0.10:
            close_reason_text = "Micro-HFT sweet spot profit ($0.03–$0.10)"
            symbol_logger.info(f"   Close Reason: {close_reason_text}")
        else:
            close_reason_text = f"Micro-HFT multiple of $0.10 (${profit:.2f})"
            symbol_logger.info(f"   Close Reason: {close_reason_text}")
        symbol_logger.info("=" * 80)
        
        # Also write/update JSONL entry
        jsonl_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'trade_type': None,  # Will be preserved from existing entry
            'entry_price': entry_price_actual,
            'exit_price': close_price,
            'profit_usd': profit,
            'status': 'CLOSED',
            'order_id': str(ticket),
            'additional_info': {
                'close_type': 'micro_hft',
                'spread_points': spread_points,
                'execution_time_ms': execution_time_ms,
                'close_reason': close_reason_text
            }
        }
        
        self._update_jsonl_entry(symbol, ticket, jsonl_entry)
