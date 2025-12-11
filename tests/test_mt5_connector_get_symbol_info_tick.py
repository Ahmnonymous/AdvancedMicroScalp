"""
Test for MT5Connector.get_symbol_info_tick() method.

This test verifies that the missing get_symbol_info_tick() method
is properly implemented and returns valid tick data.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.mt5_connector import MT5Connector


class TestMT5ConnectorGetSymbolInfoTick(unittest.TestCase):
    """Test cases for get_symbol_info_tick() method."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'mt5': {
                'account': '123456',
                'password': 'test',
                'server': 'test_server',
                'path': '',
                'timeout': 60000,
                'reconnect_attempts': 5,
                'reconnect_delay': 5
            }
        }
        self.connector = MT5Connector(self.config)
    
    @patch('execution.mt5_connector.mt5')
    def test_get_symbol_info_tick_returns_tick_object(self, mock_mt5):
        """Test that get_symbol_info_tick() returns a tick object with bid/ask."""
        # Mock MT5 connection
        mock_mt5.terminal_info.return_value = MagicMock()
        mock_mt5.account_info.return_value = MagicMock()
        self.connector.connected = True
        
        # Create mock tick object
        mock_tick = MagicMock()
        mock_tick.bid = 1.20000
        mock_tick.ask = 1.20010
        mock_tick.time = 1234567890
        
        mock_mt5.symbol_info_tick.return_value = mock_tick
        
        # Call method
        tick = self.connector.get_symbol_info_tick('EURUSD')
        
        # Verify
        self.assertIsNotNone(tick)
        self.assertEqual(tick.bid, 1.20000)
        self.assertEqual(tick.ask, 1.20010)
        self.assertEqual(tick.time, 1234567890)
        mock_mt5.symbol_info_tick.assert_called_once_with('EURUSD')
    
    @patch('execution.mt5_connector.mt5')
    def test_get_symbol_info_tick_returns_none_when_not_connected(self, mock_mt5):
        """Test that get_symbol_info_tick() returns None when MT5 is not connected."""
        self.connector.connected = False
        mock_mt5.terminal_info.return_value = None
        
        tick = self.connector.get_symbol_info_tick('EURUSD')
        
        self.assertIsNone(tick)
        mock_mt5.symbol_info_tick.assert_not_called()
    
    @patch('execution.mt5_connector.mt5')
    def test_get_symbol_info_tick_returns_none_when_symbol_not_found(self, mock_mt5):
        """Test that get_symbol_info_tick() returns None when symbol is not found."""
        mock_mt5.terminal_info.return_value = MagicMock()
        mock_mt5.account_info.return_value = MagicMock()
        self.connector.connected = True
        
        mock_mt5.symbol_info_tick.return_value = None
        
        tick = self.connector.get_symbol_info_tick('INVALID')
        
        self.assertIsNone(tick)
        mock_mt5.symbol_info_tick.assert_called_once_with('INVALID')
    
    @patch('execution.mt5_connector.mt5')
    def test_get_symbol_info_tick_validates_bid_ask(self, mock_mt5):
        """Test that get_symbol_info_tick() returns tick with valid bid < ask."""
        mock_mt5.terminal_info.return_value = MagicMock()
        mock_mt5.account_info.return_value = MagicMock()
        self.connector.connected = True
        
        mock_tick = MagicMock()
        mock_tick.bid = 1.20000
        mock_tick.ask = 1.20010
        mock_tick.time = 1234567890
        
        mock_mt5.symbol_info_tick.return_value = mock_tick
        
        tick = self.connector.get_symbol_info_tick('EURUSD')
        
        self.assertIsNotNone(tick)
        self.assertGreater(tick.ask, tick.bid)
        self.assertGreater(tick.bid, 0)
        self.assertGreater(tick.ask, 0)


if __name__ == '__main__':
    unittest.main()

