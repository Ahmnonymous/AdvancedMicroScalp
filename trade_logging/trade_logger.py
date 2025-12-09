"""
Unified Trade Logger Module
Provides comprehensive logging for trade execution, position closures, and trailing stops.
Now supports both text logs (for readability) and JSONL entries (for machine processing).
"""

import os
import json
from datetime import datetime
from typing import Dict, Any, Optional
from utils.logger_factory import get_symbol_logger


class TradeLogger:
    """Unified trade logging system with comprehensive trade tracking."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._ensure_trades_directory()
    
    def _ensure_trades_directory(self):
        """Ensure logs/trades directory exists."""
        os.makedirs('logs/trades', exist_ok=True)
    
    def _write_jsonl_entry(self, symbol: str, entry: Dict[str, Any]):
        """Write JSONL entry to symbol log file."""
        log_file = f'logs/trades/{symbol}.log'
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            # Fallback to system error log
            import logging
            error_logger = logging.getLogger('system_errors')
            error_logger.error(f"Failed to write JSONL entry to {log_file}: {e}")
    
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
        
        # Symbol logger (logs to logs/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol)
        symbol_logger.info("=" * 80)
        symbol_logger.info(f"✅ TRADE EXECUTED SUCCESSFULLY")
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
        
        self._write_jsonl_entry(symbol, jsonl_entry)
    
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
        # Symbol logger (logs to logs/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol)
        symbol_logger.info("=" * 80)
        symbol_logger.info(f"🔴 POSITION CLOSED")
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
        log_file = f'logs/trades/{symbol}.log'
        
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
            # Fallback: just append
            self._write_jsonl_entry(symbol, closure_data)
            import logging
            error_logger = logging.getLogger('system_errors')
            error_logger.error(f"Failed to update JSONL entry in {log_file}: {e}")
    
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
        
        # Symbol logger (logs to logs/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol)
        symbol_logger.info(
            f"📈 TRAILING STOP: Ticket {ticket} ({symbol}) | "
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
        symbol_logger = get_symbol_logger(symbol)
        symbol_logger.warning(
            f"⚠️ PREVENTED EARLY EXIT: Ticket {ticket} | "
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
        symbol_logger = get_symbol_logger(symbol)
        if sl_modification_failed:
            symbol_logger.warning(
                f"⚠️ LATE EXIT: Ticket {ticket} | "
                f"SL modification failed, position closed at ${actual_profit:.2f} (expected: ${expected_profit:.2f})"
            )
        else:
            symbol_logger.warning(
                f"⚠️ LATE EXIT: Ticket {ticket} | "
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
        # Symbol logger (logs to logs/trades/SYMBOL.log)
        symbol_logger = get_symbol_logger(symbol)
        symbol_logger.info("=" * 80)
        symbol_logger.info(f"⚡ MICRO-HFT PROFIT CLOSE")
        symbol_logger.info(f"   Symbol: {symbol}")
        symbol_logger.info(f"   Ticket: {ticket}")
        symbol_logger.info(f"   Entry Price (Actual Fill): {entry_price_actual:.5f}")
        symbol_logger.info(f"   Close Price (Actual Fill): {close_price:.5f}")
        symbol_logger.info(f"   Profit: ${profit:.2f} USD")
        symbol_logger.info(f"   Spread: {spread_points:.1f} points")
        symbol_logger.info(f"   Execution Time: {execution_time_ms:.2f} ms")
        # Determine close reason text
        if 0.03 <= profit <= 0.10:
            close_reason_text = "Micro-HFT sweet spot profit ($0.03–$0.10)"
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

