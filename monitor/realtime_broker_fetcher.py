#!/usr/bin/env python3
"""
Real-Time Broker Data Fetcher
Fetches broker trade data directly from MT5 for real-time reconciliation.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from execution.mt5_connector import MT5Connector
from utils.logger_factory import get_logger

logger = get_logger("broker_fetcher", "logs/system/broker_fetcher.log")


class RealtimeBrokerFetcher:
    """Fetches broker trade data from MT5 in real-time."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize broker data fetcher.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.mt5_connector = MT5Connector(config)
        self._last_fetch_time = None
        self._cached_deals = []
    
    def ensure_connected(self) -> bool:
        """Ensure MT5 connection is active."""
        return self.mt5_connector.ensure_connected()
    
    def get_recent_deals(self, hours_back: int = 24, session_start_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Get recent deals from MT5, optionally filtered by session start time.
        
        Args:
            hours_back: Number of hours to look back
            session_start_time: Only include deals opened after this time (for session filtering)
        
        Returns:
            List of deal dictionaries (filtered by session if provided)
        """
        if not self.ensure_connected():
            logger.error("Cannot fetch deals - MT5 not connected")
            return []
        
        try:
            import MetaTrader5 as mt5
            
            # Calculate time range
            now = datetime.now()
            if session_start_time:
                # Use session start time as minimum, but ensure we go back at least hours_back
                start_time = max(session_start_time, now - timedelta(hours=hours_back))
                logger.debug(f"Filtering deals from session start: {session_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                start_time = now - timedelta(hours=hours_back)
            
            # Get deals in time range
            deals = mt5.history_deals_get(
                start_time,
                now
            )
            
            if deals is None:
                logger.warning("No deals found or error getting deals")
                return []
            
            # Convert to dictionary format and filter by session start time
            deal_list = []
            for deal in deals:
                # Only include position deals (exclude deposits/withdrawals)
                if deal.entry in [mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_OUT]:
                    deal_time = datetime.fromtimestamp(deal.time)
                    
                    # Filter by session start time if provided
                    # Only include deals opened (entry IN) after session start
                    if session_start_time:
                        if deal.entry == mt5.DEAL_ENTRY_IN:
                            # Entry deal - must be after session start
                            if deal_time < session_start_time:
                                continue  # Skip deals opened before session
                        # Exit deals are included if their position was opened in session
                        # (we'll filter positions later based on entry_time)
                    
                    deal_dict = {
                        'ticket': deal.ticket,
                        'order_id': deal.position_id,  # Position ticket
                        'deal_time': deal_time,
                        'symbol': deal.symbol,
                        'type': 'BUY' if deal.type == mt5.DEAL_TYPE_BUY else 'SELL',
                        'entry': 'IN' if deal.entry == mt5.DEAL_ENTRY_IN else 'OUT',
                        'volume': deal.volume,
                        'price': deal.price,
                        'profit': deal.profit,
                        'commission': deal.commission,
                        'swap': deal.swap,
                        'comment': deal.comment
                    }
                    deal_list.append(deal_dict)
            
            if session_start_time:
                logger.info(f"Fetched {len(deal_list)} deals from MT5 (filtered to session since {session_start_time.strftime('%Y-%m-%d %H:%M:%S')})")
            else:
                logger.info(f"Fetched {len(deal_list)} deals from MT5")
            return deal_list
        
        except Exception as e:
            logger.error(f"Error fetching deals from MT5: {e}", exc_info=True)
            return []
    
    def get_positions_from_deals(self, hours_back: int = 24, session_start_time: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
        """
        Get position data by aggregating deals, filtered by session start time.
        
        Groups deals by position_id to create complete position information.
        Only includes positions opened after session_start_time.
        
        Args:
            hours_back: Number of hours to look back
            session_start_time: Only include positions opened after this time
        
        Returns:
            Dictionary mapping order_id to position data (only positions from session)
        """
        deals = self.get_recent_deals(hours_back, session_start_time)
        
        # Group deals by position_id
        positions = {}
        
        for deal in deals:
            order_id = deal['order_id']
            entry = deal['entry']
            
            if order_id not in positions:
                positions[order_id] = {
                    'order_id': order_id,
                    'symbol': deal['symbol'],
                    'trade_type': deal['type'],
                    'entry_deal': None,
                    'exit_deal': None,
                    'total_profit': 0.0,
                    'total_commission': 0.0,
                    'total_swap': 0.0,
                    'status': 'OPEN',
                    'entry_time': None,
                    'exit_time': None,
                    'entry_price': None,
                    'exit_price': None
                }
            
            pos = positions[order_id]
            pos['total_profit'] += deal['profit']
            pos['total_commission'] += deal['commission']
            pos['total_swap'] += deal['swap']
            
            if entry == 'IN':
                pos['entry_deal'] = deal
                pos['entry_time'] = deal['deal_time']
                pos['entry_price'] = deal['price']
            elif entry == 'OUT':
                pos['exit_deal'] = deal
                pos['exit_time'] = deal['deal_time']
                pos['exit_price'] = deal['price']
                pos['status'] = 'CLOSED'
        
        # Filter positions by session start time (only positions opened after session start)
        if session_start_time:
            filtered_positions = {}
            for order_id, pos in positions.items():
                # Only include positions opened after session start
                if pos['entry_time'] and pos['entry_time'] >= session_start_time:
                    filtered_positions[order_id] = pos
                else:
                    logger.debug(f"Excluding position {order_id} - opened before session start: {pos['entry_time']}")
            positions = filtered_positions
            logger.info(f"Filtered to {len(positions)} positions from current session (since {session_start_time.strftime('%Y-%m-%d %H:%M:%S')})")
        
        # Check if positions are still open in MT5
        current_positions = self.get_current_positions()
        open_ticket_ids = {pos['ticket'] for pos in current_positions}
        
        # Update status for positions that might be open
        for order_id, pos in positions.items():
            if order_id in open_ticket_ids:
                pos['status'] = 'OPEN'
            elif pos['exit_deal'] is not None:
                pos['status'] = 'CLOSED'
        
        return positions
    
    def get_current_positions(self) -> List[Dict[str, Any]]:
        """Get currently open positions from MT5."""
        if not self.ensure_connected():
            return []
        
        try:
            import MetaTrader5 as mt5
            
            positions = mt5.positions_get()
            if positions is None:
                return []
            
            result = []
            for pos in positions:
                result.append({
                    'ticket': pos.ticket,
                    'symbol': pos.symbol,
                    'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                    'volume': pos.volume,
                    'price_open': pos.price_open,
                    'price_current': pos.price_current,
                    'profit': pos.profit,
                    'sl': pos.sl,
                    'tp': pos.tp,
                    'time_open': datetime.fromtimestamp(pos.time),
                    'comment': pos.comment
                })
            
            return result
        
        except Exception as e:
            logger.error(f"Error getting current positions: {e}", exc_info=True)
            return []

