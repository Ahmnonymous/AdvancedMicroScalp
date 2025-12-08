"""
Test Micro-HFT Profit Engine
Verifies that positions are closed immediately when profit is within $0.03–$0.10 sweet spot range.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import time

from bot.micro_profit_engine import MicroProfitEngine
from trade_logging.trade_logger import TradeLogger


class TestMicroProfitClose(unittest.TestCase):
    """Test micro-HFT profit engine closes positions correctly."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'micro_profit_engine': {
                'enabled': True,
                'min_profit_threshold_usd': 0.03,
                'max_profit_threshold_usd': 0.10,
                'max_retries': 3,
                'retry_delay_ms': 10
            }
        }
        self.order_manager = Mock()
        self.trade_logger = Mock(spec=TradeLogger)
        self.micro_engine = MicroProfitEngine(
            config=self.config,
            order_manager=self.order_manager,
            trade_logger=self.trade_logger
        )
    
    def test_close_position_within_sweet_spot(self):
        """Test that position is closed when profit is within $0.03–$0.10 sweet spot."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit in sweet spot range
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.05,  # $0.05 profit (within $0.03–$0.10 sweet spot)
            'price_open': 1.10000,
            'price_current': 1.10050,
            'type': 'BUY'
        }
        
        # Mock order_manager methods
        self.order_manager.get_position_by_ticket.return_value = position
        self.order_manager.close_position.return_value = True
        
        # Mock MT5 connector
        mt5_connector = Mock()
        mt5_connector.ensure_connected.return_value = True
        mt5_connector.get_symbol_info.return_value = {
            'bid': 1.10050,
            'ask': 1.10060,
            'spread': 10,
            'digits': 5
        }
        
        # Check and close
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Verify position was closed
        self.assertTrue(result)
        self.order_manager.close_position.assert_called_once()
        
        # Verify logging was called
        self.trade_logger.log_micro_profit_close.assert_called_once()
        call_args = self.trade_logger.log_micro_profit_close.call_args
        self.assertEqual(call_args[1]['ticket'], ticket)
        self.assertEqual(call_args[1]['symbol'], symbol)
        self.assertEqual(call_args[1]['profit'], 0.05)
    
    def test_skip_close_when_profit_below_min_threshold(self):
        """Test that position is NOT closed when profit < $0.03."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit < $0.03
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.02,  # $0.02 profit (below $0.03 minimum threshold)
            'price_open': 1.10000,
            'price_current': 1.10020,
            'type': 'BUY'
        }
        
        # Mock order_manager
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock MT5 connector
        mt5_connector = Mock()
        
        # Check and close
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Verify position was NOT closed
        self.assertFalse(result)
        self.order_manager.close_position.assert_not_called()
        self.trade_logger.log_micro_profit_close.assert_not_called()
    
    def test_skip_close_when_profit_above_max_threshold(self):
        """Test that position is NOT closed when profit > $0.10."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit > $0.10
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.15,  # $0.15 profit (above $0.10 maximum threshold)
            'price_open': 1.10000,
            'price_current': 1.10150,
            'type': 'BUY'
        }
        
        # Mock order_manager
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock MT5 connector
        mt5_connector = Mock()
        
        # Check and close
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Verify position was NOT closed
        self.assertFalse(result)
        self.order_manager.close_position.assert_not_called()
        self.trade_logger.log_micro_profit_close.assert_not_called()
    
    def test_skip_close_when_profit_negative(self):
        """Test that position is NOT closed when profit is negative."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with negative profit
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': -0.50,  # Negative profit
            'price_open': 1.10000,
            'price_current': 1.09500,
            'type': 'BUY'
        }
        
        # Mock order_manager
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock MT5 connector
        mt5_connector = Mock()
        
        # Check and close
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Verify position was NOT closed
        self.assertFalse(result)
        self.order_manager.close_position.assert_not_called()
        self.trade_logger.log_micro_profit_close.assert_not_called()
    
    def test_retry_on_close_failure(self):
        """Test that close is retried on failure."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit in sweet spot
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.05,  # Within $0.03–$0.10 range
            'price_open': 1.10000,
            'price_current': 1.10050,
            'type': 'BUY'
        }
        
        # Mock order_manager - fail first 2 times, succeed on 3rd
        self.order_manager.get_position_by_ticket.return_value = position
        self.order_manager.close_position.side_effect = [False, False, True]
        
        # Mock MT5 connector
        mt5_connector = Mock()
        mt5_connector.ensure_connected.return_value = True
        mt5_connector.get_symbol_info.return_value = {
            'bid': 1.10020,
            'ask': 1.10030,
            'spread': 10,
            'digits': 5
        }
        
        # Check and close
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Verify close was retried 3 times
        self.assertEqual(self.order_manager.close_position.call_count, 3)
        self.assertTrue(result)
    
    def test_skip_retry_if_profit_drops_below_min(self):
        """Test that retry is skipped if profit drops below $0.03."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit in sweet spot initially
        position_initial = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.05,  # Within sweet spot
            'price_open': 1.10000,
            'price_current': 1.10050,
            'type': 'BUY'
        }
        
        # Mock position with profit < $0.03 on retry
        position_retry = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.02,  # Dropped below minimum threshold
            'price_open': 1.10000,
            'price_current': 1.10020,
            'type': 'BUY'
        }
        
        # Mock order_manager - fail first time
        self.order_manager.get_position_by_ticket.side_effect = [position_initial, position_retry]
        self.order_manager.close_position.return_value = False
        
        # Mock MT5 connector
        mt5_connector = Mock()
        mt5_connector.ensure_connected.return_value = True
        mt5_connector.get_symbol_info.return_value = {
            'bid': 1.10020,
            'ask': 1.10030,
            'spread': 10,
            'digits': 5
        }
        
        # Check and close
        result = self.micro_engine.check_and_close(position_initial, mt5_connector)
        
        # Verify close was attempted once, then skipped on retry
        self.assertEqual(self.order_manager.close_position.call_count, 1)
        self.assertFalse(result)
    
    def test_skip_retry_if_profit_rises_above_max(self):
        """Test that retry is skipped if profit rises above $0.10."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit in sweet spot initially
        position_initial = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.05,  # Within sweet spot
            'price_open': 1.10000,
            'price_current': 1.10050,
            'type': 'BUY'
        }
        
        # Mock position with profit > $0.10 on retry
        position_retry = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.15,  # Rose above maximum threshold
            'price_open': 1.10000,
            'price_current': 1.10150,
            'type': 'BUY'
        }
        
        # Mock order_manager - fail first time
        self.order_manager.get_position_by_ticket.side_effect = [position_initial, position_retry]
        self.order_manager.close_position.return_value = False
        
        # Mock MT5 connector
        mt5_connector = Mock()
        mt5_connector.ensure_connected.return_value = True
        mt5_connector.get_symbol_info.return_value = {
            'bid': 1.10050,
            'ask': 1.10060,
            'spread': 10,
            'digits': 5
        }
        
        # Check and close
        result = self.micro_engine.check_and_close(position_initial, mt5_connector)
        
        # Verify close was attempted once, then skipped on retry
        self.assertEqual(self.order_manager.close_position.call_count, 1)
        self.assertFalse(result)
    
    def test_prevent_duplicate_close_attempts(self):
        """Test that duplicate close attempts are prevented."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit in sweet spot
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.05,  # Within sweet spot
            'price_open': 1.10000,
            'price_current': 1.10050,
            'type': 'BUY'
        }
        
        # Mock order_manager
        self.order_manager.get_position_by_ticket.return_value = position
        self.order_manager.close_position.return_value = True
        
        # Mock MT5 connector
        mt5_connector = Mock()
        mt5_connector.ensure_connected.return_value = True
        mt5_connector.get_symbol_info.return_value = {
            'bid': 1.10020,
            'ask': 1.10030,
            'spread': 10,
            'digits': 5
        }
        
        # First call
        result1 = self.micro_engine.check_and_close(position, mt5_connector)
        self.assertTrue(result1)
        
        # Second call (should be prevented)
        result2 = self.micro_engine.check_and_close(position, mt5_connector)
        self.assertFalse(result2)
        
        # Verify close was only called once
        self.assertEqual(self.order_manager.close_position.call_count, 1)
    
    def test_close_at_minimum_threshold(self):
        """Test that position is closed at exactly $0.03."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit exactly at minimum
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.03,  # Exactly at minimum threshold
            'price_open': 1.10000,
            'price_current': 1.10030,
            'type': 'BUY'
        }
        
        # Mock order_manager
        self.order_manager.get_position_by_ticket.return_value = position
        self.order_manager.close_position.return_value = True
        
        # Mock MT5 connector
        mt5_connector = Mock()
        mt5_connector.ensure_connected.return_value = True
        mt5_connector.get_symbol_info.return_value = {
            'bid': 1.10030,
            'ask': 1.10040,
            'spread': 10,
            'digits': 5
        }
        
        # Check and close
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Verify position was closed
        self.assertTrue(result)
        self.order_manager.close_position.assert_called_once()
    
    def test_close_at_maximum_threshold(self):
        """Test that position is closed at exactly $0.10."""
        symbol = "EURUSD"
        ticket = 12345
        
        # Mock position with profit exactly at maximum
        position = {
            'ticket': ticket,
            'symbol': symbol,
            'profit': 0.10,  # Exactly at maximum threshold
            'price_open': 1.10000,
            'price_current': 1.10100,
            'type': 'BUY'
        }
        
        # Mock order_manager
        self.order_manager.get_position_by_ticket.return_value = position
        self.order_manager.close_position.return_value = True
        
        # Mock MT5 connector
        mt5_connector = Mock()
        mt5_connector.ensure_connected.return_value = True
        mt5_connector.get_symbol_info.return_value = {
            'bid': 1.10100,
            'ask': 1.10110,
            'spread': 10,
            'digits': 5
        }
        
        # Check and close
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Verify position was closed
        self.assertTrue(result)
        self.order_manager.close_position.assert_called_once()
    
    def test_engine_disabled(self):
        """Test that engine does nothing when disabled."""
        # Disable engine
        self.micro_engine.enabled = False
        
        position = {
            'ticket': 12345,
            'symbol': 'EURUSD',
            'profit': 0.05,  # Within sweet spot
            'type': 'BUY'
        }
        
        mt5_connector = Mock()
        
        result = self.micro_engine.check_and_close(position, mt5_connector)
        
        # Should return False when disabled
        self.assertFalse(result)
        self.order_manager.close_position.assert_not_called()


if __name__ == '__main__':
    unittest.main()

