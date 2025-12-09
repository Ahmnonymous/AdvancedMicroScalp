#!/usr/bin/env python3
"""
Position Closure Monitor
Monitors positions and detects closures from MT5, logging them to trade logs.

This ensures all position closures are properly logged with broker-confirmed data.
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Set
from execution.mt5_connector import MT5Connector
from trade_logging.trade_logger import TradeLogger


class PositionMonitor:
    """Monitors positions and logs closures detected from MT5."""
    
    def __init__(self, config: Dict[str, Any], trade_logger: TradeLogger):
        self.config = config
        self.trade_logger = trade_logger
        self.mt5_connector = MT5Connector(config)
        self.tracked_positions: Set[int] = set()  # Track positions we've seen
        self._ensure_system_directories()
    
    def _ensure_system_directories(self):
        """Ensure system directories exist."""
        os.makedirs('logs/system', exist_ok=True)
        os.makedirs('logs/trades', exist_ok=True)
    
    def get_deal_history_for_position(self, ticket: int) -> Optional[Dict[str, Any]]:
        """
        Get deal history for a position to determine closure details.
        
        Returns dict with:
        - entry_deal: Entry deal info
        - exit_deal: Exit deal info (if closed)
        - profit: Total profit from all deals
        - status: 'OPEN' or 'CLOSED'
        """
        if not self.mt5_connector.ensure_connected():
            return None
        
        import MetaTrader5 as mt5
        
        try:
            # Get all deals for this position
            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                return None
            
            entry_deal = None
            exit_deal = None
            total_profit = 0.0
            commission = 0.0
            swap = 0.0
            
            for deal in deals:
                if deal.entry == mt5.DEAL_ENTRY_IN:
                    entry_deal = {
                        'ticket': deal.ticket,
                        'time': datetime.fromtimestamp(deal.time),
                        'price': deal.price,
                        'volume': deal.volume,
                        'profit': deal.profit,
                        'commission': deal.commission,
                        'swap': deal.swap
                    }
                elif deal.entry == mt5.DEAL_ENTRY_OUT:
                    exit_deal = {
                        'ticket': deal.ticket,
                        'time': datetime.fromtimestamp(deal.time),
                        'price': deal.price,
                        'volume': deal.volume,
                        'profit': deal.profit,
                        'commission': deal.commission,
                        'swap': deal.swap
                    }
                
                total_profit += deal.profit
                commission += deal.commission
                swap += deal.swap
            
            return {
                'entry_deal': entry_deal,
                'exit_deal': exit_deal,
                'total_profit': total_profit,
                'commission': commission,
                'swap': swap,
                'status': 'CLOSED' if exit_deal else 'OPEN'
            }
        
        except Exception as e:
            import logging
            error_logger = logging.getLogger('system_errors')
            error_logger.error(f"Error getting deal history for position {ticket}: {e}")
            return None
    
    def detect_and_log_closures(self, tracked_tickets: Set[int]) -> List[Dict[str, Any]]:
        """
        Detect closed positions and log them.
        
        Args:
            tracked_tickets: Set of tickets we know should be open
        
        Returns:
            List of closed positions that were logged
        """
        if not self.mt5_connector.ensure_connected():
            return []
        
        import MetaTrader5 as mt5
        
        logged_closures = []
        current_positions = mt5.positions_get()
        current_tickets = {pos.ticket for pos in current_positions} if current_positions else set()
        
        # Find positions that were tracked but are no longer open
        closed_tickets = tracked_tickets - current_tickets
        
        for ticket in closed_tickets:
            try:
                # Get deal history to get closure details
                deal_info = self.get_deal_history_for_position(ticket)
                
                if deal_info and deal_info['exit_deal']:
                    entry_deal = deal_info['entry_deal']
                    exit_deal = deal_info['exit_deal']
                    
                    # Determine symbol from position history
                    # Try to get from a recent position query
                    symbol = None
                    position = mt5.positions_get(ticket=ticket)
                    if position and len(position) > 0:
                        symbol = position[0].symbol
                    else:
                        # Try to get from deal
                        if entry_deal:
                            # Get deal details to find symbol
                            deals = mt5.history_deals_get(position=ticket)
                            if deals and len(deals) > 0:
                                symbol = deals[0].symbol
                    
                    if not symbol:
                        continue
                    
                    # Calculate duration
                    duration_minutes = 0.0
                    if entry_deal and exit_deal:
                        duration = exit_deal['time'] - entry_deal['time']
                        duration_minutes = duration.total_seconds() / 60.0
                    
                    # Determine close reason
                    close_reason = self._determine_close_reason(ticket, deal_info)
                    
                    # Log closure
                    self.trade_logger.log_position_closure(
                        symbol=symbol,
                        ticket=ticket,
                        entry_price=entry_deal['price'] if entry_deal else 0.0,
                        close_price=exit_deal['price'],
                        profit=deal_info['total_profit'],
                        duration_minutes=duration_minutes,
                        close_reason=close_reason,
                        entry_time=entry_deal['time'] if entry_deal else None,
                        close_time=exit_deal['time'],
                        commission=deal_info['commission'],
                        swap=deal_info['swap']
                    )
                    
                    logged_closures.append({
                        'ticket': ticket,
                        'symbol': symbol,
                        'profit': deal_info['total_profit'],
                        'close_time': exit_deal['time']
                    })
                    
                    self.tracked_positions.discard(ticket)
            
            except Exception as e:
                import logging
                error_logger = logging.getLogger('system_errors')
                error_logger.error(f"Error detecting closure for position {ticket}: {e}")
        
        return logged_closures
    
    def _determine_close_reason(self, ticket: int, deal_info: Dict[str, Any]) -> str:
        """Determine the reason a position was closed."""
        if not deal_info or not deal_info.get('exit_deal'):
            return "Unknown"
        
        exit_deal = deal_info['exit_deal']
        profit = deal_info['total_profit']
        
        # Check if it was a micro-HFT close (very short duration, small profit)
        entry_time = deal_info['entry_deal']['time'] if deal_info.get('entry_deal') else None
        exit_time = exit_deal['time']
        
        if entry_time:
            duration_minutes = (exit_time - entry_time).total_seconds() / 60.0
            if duration_minutes < 5 and 0.01 <= abs(profit) <= 0.50:
                return "Micro-HFT sweet spot profit"
        
        # Check profit to determine reason
        if profit > 0.10:
            return "Take Profit or Trailing Stop"
        elif profit < -0.10:
            return "Stop Loss"
        elif abs(profit) <= 0.10:
            return "Small profit target or slippage"
        else:
            return "Broker Confirmed"
    
    def update_tracked_positions(self, new_ticket: int):
        """Add a new position to tracking."""
        self.tracked_positions.add(new_ticket)
    
    def get_tracked_positions(self) -> Set[int]:
        """Get set of currently tracked positions."""
        return self.tracked_positions.copy()

