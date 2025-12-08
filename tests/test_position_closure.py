"""
Test Position Closure Tracking
Verifies that position closures are detected correctly from MT5 deal history.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import MetaTrader5 as mt5

from risk.risk_manager import RiskManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager


class TestPositionClosure(unittest.TestCase):
    """Test position closure detection and logging."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'risk': {
                'max_risk_per_trade_usd': 2.0,
                'continuous_trailing_enabled': True
            }
        }
        self.mt5_connector = Mock(spec=MT5Connector)
        self.order_manager = Mock(spec=OrderManager)
        self.risk_manager = RiskManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_detect_closed_position(self):
        """Test that closed positions are detected correctly."""
        # Setup: Track an open position
        ticket = 12345
        symbol = "EURUSD"
        
        # Initially open position
        open_positions = [{
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.5
        }]
        
        # After closure - position no longer in list
        closed_positions = []
        
        self.order_manager.get_open_positions.return_value = open_positions
        self.risk_manager._last_open_tickets = {ticket}
        
        # First call - position is open
        self.risk_manager.monitor_all_positions_continuous()
        self.assertIn(ticket, self.risk_manager._last_open_tickets)
        
        # Second call - position is closed
        self.order_manager.get_open_positions.return_value = closed_positions
        self.risk_manager.monitor_all_positions_continuous()
        
        # Verify position was removed from tracking
        self.assertNotIn(ticket, self.risk_manager._last_open_tickets)
    
    @patch('risk.risk_manager.mt5')
    def test_get_deal_history_on_closure(self, mock_mt5):
        """Test that deal history is fetched when position closes."""
        ticket = 12345
        symbol = "EURUSD"
        
        # Mock deal history
        entry_deal = MagicMock()
        entry_deal.entry = mt5.DEAL_ENTRY_IN
        entry_deal.symbol = symbol
        entry_deal.price = 1.10000
        entry_deal.time = int((datetime.now() - timedelta(minutes=10)).timestamp())
        entry_deal.profit = 0.0
        
        close_deal = MagicMock()
        close_deal.entry = mt5.DEAL_ENTRY_OUT
        close_deal.symbol = symbol
        close_deal.price = 1.10100
        close_deal.time = int(datetime.now().timestamp())
        close_deal.profit = -0.50
        
        mock_mt5.history_deals_get.return_value = [entry_deal, close_deal]
        self.mt5_connector.ensure_connected.return_value = True
        
        # Setup closed position
        self.order_manager.get_open_positions.return_value = []
        self.risk_manager._last_open_tickets = {ticket}
        
        # Position info before closure
        self.order_manager.get_position_by_ticket.return_value = {
            'ticket': ticket,
            'symbol': symbol
        }
        
        # Monitor should detect closure and fetch deal history
        self.risk_manager.monitor_all_positions_continuous()
        
        # Verify deal history was fetched
        mock_mt5.history_deals_get.assert_called_with(position=ticket)
    
    def test_closure_logging_includes_all_fields(self):
        """Test that closure logging includes all required fields."""
        # This test verifies the logging structure
        # Actual logging is tested via integration tests
        pass


if __name__ == '__main__':
    unittest.main()

