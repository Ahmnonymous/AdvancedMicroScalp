"""
Test Trailing Stop Engine
Verifies that trailing stop never worsens beyond -$2.00 and prevents early/late exits.
"""

import unittest
from unittest.mock import Mock, patch

from risk.risk_manager import RiskManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager


class TestTrailingStop(unittest.TestCase):
    """Test trailing stop engine fixes."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'risk': {
                'max_risk_per_trade_usd': 2.0,
                'trailing_stop_increment_usd': 0.10,
                'continuous_trailing_enabled': True
            }
        }
        self.mt5_connector = Mock(spec=MT5Connector)
        self.order_manager = Mock(spec=OrderManager)
        self.risk_manager = RiskManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_prevent_early_exit(self):
        """Test that SL never goes worse than -$2.00."""
        ticket = 12345
        symbol = "EURUSD"
        
        # Mock position
        self.order_manager.get_position_by_ticket.return_value = {
            'ticket': ticket,
            'symbol': symbol,
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.09800,  # Losing position
            'volume': 0.01,
            'sl': 1.09800,
            'profit': -1.50  # Current loss
        }
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000
        }
        
        # Try to update trailing stop with profit worse than -$2.00
        current_profit = -2.50  # Worse than max risk
        
        # Should prevent early exit
        success, reason = self.risk_manager.update_continuous_trailing_stop(ticket, current_profit)
        
        # Should fail and prevent early exit
        self.assertFalse(success)
        self.assertIn("Prevented early exit", reason or "")
    
    def test_prevent_late_exit_with_retry(self):
        """Test that SL modifications are retried up to 3 times."""
        ticket = 12345
        symbol = "EURUSD"
        
        # Mock position
        self.order_manager.get_position_by_ticket.return_value = {
            'ticket': ticket,
            'symbol': symbol,
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10200,  # Profitable position
            'volume': 0.01,
            'sl': 1.10000,
            'profit': 0.20
        }
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000,
            'trade_stops_level': 10
        }
        
        # Mock modify_order to fail first 2 times, succeed on 3rd
        self.order_manager.modify_order.side_effect = [False, False, True]
        
        current_profit = 0.20
        
        # Should retry and eventually succeed
        success, reason = self.risk_manager.update_continuous_trailing_stop(ticket, current_profit)
        
        # Should succeed after retries
        self.assertTrue(success)
        self.assertEqual(self.order_manager.modify_order.call_count, 3)
    
    def test_manual_close_on_sl_modification_failure(self):
        """Test that position is manually closed if SL modification fails after retries."""
        ticket = 12345
        symbol = "EURUSD"
        
        # Mock position
        self.order_manager.get_position_by_ticket.return_value = {
            'ticket': ticket,
            'symbol': symbol,
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10200,
            'volume': 0.01,
            'sl': 1.10000,
            'profit': 0.20
        }
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000,
            'trade_stops_level': 10
        }
        
        # Mock modify_order to always fail
        self.order_manager.modify_order.return_value = False
        
        # Mock close_position to succeed
        self.order_manager.close_position.return_value = True
        
        current_profit = 0.20
        
        # Should fail SL modification and close position manually
        success, reason = self.risk_manager.update_continuous_trailing_stop(ticket, current_profit)
        
        # Should close position manually
        self.order_manager.close_position.assert_called_once_with(ticket, comment="SL modification failed - prevent late exit")
    
    def test_trailing_stop_only_moves_forward(self):
        """Test that trailing stop only moves in favorable direction."""
        ticket = 12345
        symbol = "EURUSD"
        
        # Mock position with existing SL at +$0.10 profit
        self.order_manager.get_position_by_ticket.return_value = {
            'ticket': ticket,
            'symbol': symbol,
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10050,  # Small profit
            'volume': 0.01,
            'sl': 1.10010,  # SL already at +$0.10
            'profit': 0.05
        }
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000,
            'trade_stops_level': 10
        }
        
        # Try to update with lower profit (should not move SL backward)
        current_profit = 0.03  # Lower than before
        
        success, reason = self.risk_manager.update_continuous_trailing_stop(ticket, current_profit)
        
        # Should not update (prevent backward movement)
        self.assertFalse(success)


if __name__ == '__main__':
    unittest.main()

