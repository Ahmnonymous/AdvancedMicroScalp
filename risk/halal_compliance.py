"""
Halal Compliance Module
Ensures all trading operations comply with Islamic/Halal trading principles.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager

logger = logging.getLogger(__name__)


class HalalCompliance:
    """Manages halal compliance checks for trading operations."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector, order_manager: OrderManager):
        self.config = config
        self.halal_config = config.get('halal', {})
        self.mt5_connector = mt5_connector
        self.order_manager = order_manager
        
        self.enabled = self.halal_config.get('enabled', True)
        self.swap_free_mode = self.halal_config.get('swap_free_mode', True)
        self.max_hold_hours = self.halal_config.get('max_hold_hours', 24)
        self.no_overnight_holds = self.halal_config.get('no_overnight_holds', True)
        self.log_all_actions = self.halal_config.get('log_all_actions', True)
    
    def check_swap_free(self, symbol: str) -> bool:
        """Check if symbol is swap-free (Islamic account compatible)."""
        if not self.enabled or not self.swap_free_mode:
            return True  # Compliance check disabled
        
        # Check if symbol itself is swap-free
        is_swap_free = self.mt5_connector.is_swap_free(symbol)
        
        # Check account swap mode
        account_info = self.mt5_connector.get_account_info()
        account_swap_mode = account_info.get('swap_mode', 'unknown') if account_info else 'unknown'
        
        # If account is Islamic/swap-free, broker won't charge swaps even if symbol has swaps
        # For Islamic accounts, we allow mode 4 symbols (tradeable) even if they have swaps
        if not is_swap_free:
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            trade_mode = symbol_info.get('trade_mode', 0) if symbol_info else 0
            
            # Allow if:
            # 1. Symbol is tradeable (mode 4) AND
            # 2. Account is Islamic (swap_mode indicates swap-free) OR
            # 3. Symbol is tradeable and we're on demo (demo accounts often don't charge swaps)
            if trade_mode == 4:
                # Check if account is Islamic
                if account_swap_mode == 0 or 'islamic' in str(account_swap_mode).lower():
                    logger.info(f"HALAL: {symbol} has swaps but account is Islamic/swap-free. Swaps won't be charged.")
                    return True
                else:
                    # For demo accounts, be more lenient
                    server = account_info.get('server', '').lower() if account_info else ''
                    if 'trial' in server or 'demo' in server:
                        logger.info(f"HALAL NOTE: {symbol} has swaps but is tradeable on demo account. Swaps may not be charged.")
                        return True
                    else:
                        logger.warning(f"HALAL CHECK FAILED: {symbol} is not swap-free and account is not Islamic")
                        if self.log_all_actions:
                            self._log_action(f"SWAP_CHECK_FAILED", symbol, f"Symbol not swap-free, account swap_mode: {account_swap_mode}")
                        return False
            else:
                logger.warning(f"HALAL CHECK FAILED: {symbol} is not swap-free and not tradeable (mode: {trade_mode})")
                if self.log_all_actions:
                    self._log_action(f"SWAP_CHECK_FAILED", symbol, f"Symbol not swap-free, trade_mode: {trade_mode}")
                return False
        
        return True
    
    def check_overnight_hold(self, position: Dict[str, Any]) -> bool:
        """
        Check if position would be held overnight.
        
        Returns True if position can be held, False if it violates overnight rule.
        """
        if not self.enabled or not self.no_overnight_holds:
            return True  # Compliance check disabled
        
        time_open = position.get('time_open')
        if not time_open:
            return True
        
        if isinstance(time_open, str):
            try:
                time_open = datetime.fromisoformat(time_open)
            except:
                return True
        
        # Check if position would be held past market close or next day
        now = datetime.now()
        hours_open = (now - time_open).total_seconds() / 3600
        
        # If position has been open for more than max_hold_hours
        if hours_open >= self.max_hold_hours:
            logger.warning(f"HALAL CHECK: Position {position.get('ticket')} held for {hours_open:.1f} hours, "
                          f"exceeds max {self.max_hold_hours} hours")
            if self.log_all_actions:
                self._log_action("OVERNIGHT_CHECK_FAILED", position.get('symbol'), 
                               f"Position held {hours_open:.1f} hours")
            return False
        
        # Check if position would cross into next day
        # For forex, market is open 24/5, but we check calendar days
        if time_open.date() < now.date():
            logger.warning(f"HALAL CHECK: Position {position.get('ticket')} crosses calendar day")
            if self.log_all_actions:
                self._log_action("OVERNIGHT_CHECK_FAILED", position.get('symbol'), 
                               "Position crosses calendar day")
            return False
        
        return True
    
    def validate_trade(self, symbol: str, order_type: str) -> bool:
        """
        Validate if trade complies with halal requirements.
        
        Returns True if trade is halal-compliant.
        """
        if not self.enabled:
            return True
        
        # Check swap-free
        if not self.check_swap_free(symbol):
            return False
        
        # Additional checks can be added here
        # - No interest-bearing operations
        # - No gambling-like randomness (handled in strategy)
        # - Valid trend method (handled in strategy)
        
        if self.log_all_actions:
            self._log_action("TRADE_VALIDATED", symbol, f"Order type: {order_type}")
        
        return True
    
    def check_all_positions(self) -> None:
        """Check all open positions for halal compliance and close if needed."""
        if not self.enabled:
            return
        
        positions = self.order_manager.get_open_positions()
        
        for position in positions:
            # Check overnight hold
            if not self.check_overnight_hold(position):
                logger.warning(f"Closing position {position.get('ticket')} due to halal compliance violation")
                self.order_manager.close_position(
                    position.get('ticket'),
                    comment="Closed: Halal compliance - overnight hold"
                )
                if self.log_all_actions:
                    self._log_action("POSITION_CLOSED_HALAL", position.get('symbol'), 
                                   f"Ticket: {position.get('ticket')}")
    
    def _log_action(self, action: str, symbol: str, details: str):
        """Log halal compliance action."""
        log_message = f"[HALAL] {action} | Symbol: {symbol} | Details: {details} | Time: {datetime.now()}"
        logger.info(log_message)
    
    def get_compliance_status(self) -> Dict[str, Any]:
        """Get current halal compliance status."""
        account_info = self.mt5_connector.get_account_info()
        
        return {
            'enabled': self.enabled,
            'swap_free_mode': self.swap_free_mode,
            'no_overnight_holds': self.no_overnight_holds,
            'max_hold_hours': self.max_hold_hours,
            'account_swap_free': account_info.get('swap_mode', 'unknown') if account_info else 'unknown'
        }

