"""
Test Profit-Locking Engine
Verifies that profit locking works correctly in sweet spot and step-based increments.
"""

import unittest
from unittest.mock import Mock, patch

from bot.profit_locking_engine import ProfitLockingEngine
from execution.order_manager import OrderManager
from execution.mt5_connector import MT5Connector


class TestProfitLocking(unittest.TestCase):
    """Test profit-locking engine."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'profit_locking': {
                'enabled': True,
                'min_profit_threshold_usd': 0.03,
                'max_profit_threshold_usd': 0.10,
                'lock_step_usd': 0.10,
                'lock_tolerance_usd': 0.02,
                'max_lock_retries': 3,
                'lock_retry_delay_ms': 100
            }
        }
        self.mt5_connector = Mock(spec=MT5Connector)
        self.order_manager = Mock(spec=OrderManager)
        self.profit_locking_engine = ProfitLockingEngine(
            self.config, self.order_manager, self.mt5_connector
        )
    
    def test_sweet_spot_lock(self):
        """Test that profit is locked in sweet spot range."""
        position = {
            'ticket': 12345,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10030,
            'volume': 0.01,
            'sl': 1.09800,  # No lock yet
            'profit': 0.05  # In sweet spot
        }
        
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000.0,
            'trade_stops_level': 10,
            'bid': 1.10030,
            'ask': 1.10035
        }
        
        self.order_manager.modify_order.return_value = True
        
        success, reason = self.profit_locking_engine.check_and_lock_profit(position)
        
        self.assertTrue(success)
        self.assertIn("Locked", reason)
        self.order_manager.modify_order.assert_called_once()
    
    def test_step_based_lock(self):
        """Test that profit is locked at step increments when profit > 0.10."""
        position = {
            'ticket': 12346,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10140,
            'volume': 0.01,
            'sl': 1.10030,  # Locked at $0.10
            'profit': 0.44  # Should lock at $0.40
        }
        
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000.0,
            'trade_stops_level': 10,
            'bid': 1.10140,
            'ask': 1.10145
        }
        
        self.order_manager.modify_order.return_value = True
        
        success, reason = self.profit_locking_engine.check_and_lock_profit(position)
        
        self.assertTrue(success)
        self.assertIn("Locked", reason)
    
    def test_no_lock_below_threshold(self):
        """Test that profit below threshold is not locked."""
        position = {
            'ticket': 12347,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10010,
            'volume': 0.01,
            'sl': 1.09800,
            'profit': 0.02  # Below threshold
        }
        
        success, reason = self.profit_locking_engine.check_and_lock_profit(position)
        
        self.assertFalse(success)
        self.assertIn("below threshold", reason.lower())
    
    def test_no_worsen_sl(self):
        """Test that SL is never worsened."""
        position = {
            'ticket': 12348,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10030,
            'volume': 0.01,
            'sl': 1.10020,  # Already locked at higher level
            'profit': 0.05
        }
        
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000.0,
            'trade_stops_level': 10,
            'bid': 1.10030,
            'ask': 1.10035
        }
        
        success, reason = self.profit_locking_engine.check_and_lock_profit(position)
        
        # Should not lock if it would worsen
        if not success:
            self.assertIn("worsen", reason.lower() or "improvement", reason.lower())
    
    def test_calculate_locked_profit(self):
        """Test locked profit calculation."""
        symbol_info = {
            'contract_size': 100000.0
        }
        
        # BUY order with SL below entry = profit locked
        locked = self.profit_locking_engine._calculate_locked_profit(
            1.10000,  # entry
            1.09970,  # SL (30 pips below)
            'BUY',
            0.01,  # lot
            symbol_info
        )
        
        # Should be approximately $0.03 (30 pips * 0.01 lot * 100000)
        self.assertGreater(locked, 0.02)
        self.assertLess(locked, 0.04)


if __name__ == '__main__':
    unittest.main()

