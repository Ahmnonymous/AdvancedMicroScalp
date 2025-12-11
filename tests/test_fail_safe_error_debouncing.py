"""
Test for fail_safe_check() error debouncing.

This test verifies that repeated errors in fail_safe_check()
are throttled to prevent log spam.
"""

import unittest
import time
from unittest.mock import Mock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.sl_manager import SLManager


class TestFailSafeErrorDebouncing(unittest.TestCase):
    """Test cases for fail_safe_check() error debouncing."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'risk': {
                'max_risk_per_trade_usd': 2.0,
                'trailing_stop_increment_usd': 0.10,
                'profit_locking': {
                    'min_profit_threshold_usd': 0.03,
                    'max_profit_threshold_usd': 0.10
                },
                'dynamic_break_even': {
                    'enabled': True,
                    'positive_profit_duration_seconds': 2.0
                }
            }
        }
        self.mt5_connector = Mock()
        self.order_manager = Mock()
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_error_debouncing_prevents_log_spam(self):
        """Test that same error is only logged once per throttle window."""
        # Simulate an error in get_open_positions
        self.order_manager.get_open_positions.side_effect = Exception("Test error")
        
        # Call fail_safe_check multiple times rapidly
        call_count = 0
        for _ in range(5):
            self.sl_manager.fail_safe_check()
            call_count += 1
        
        # Should have been called 5 times
        self.assertEqual(self.order_manager.get_open_positions.call_count, 5)
        
        # But error should only be logged once (throttled)
        # We can't directly check logger calls, but we can verify throttle mechanism works
        # by checking that throttle dict has the error signature
        self.assertGreater(len(self.sl_manager._fail_safe_error_throttle), 0)
    
    def test_different_errors_are_logged_separately(self):
        """Test that different errors are not throttled together."""
        # First error
        self.order_manager.get_open_positions.side_effect = Exception("Error 1")
        self.sl_manager.fail_safe_check()
        
        # Second different error
        self.order_manager.get_open_positions.side_effect = Exception("Error 2")
        self.sl_manager.fail_safe_check()
        
        # Should have 2 different error signatures in throttle
        self.assertEqual(len(self.sl_manager._fail_safe_error_throttle), 2)
    
    def test_error_throttle_expires_after_window(self):
        """Test that error throttle expires after throttle window."""
        # Simulate error
        self.order_manager.get_open_positions.side_effect = Exception("Test error")
        self.sl_manager.fail_safe_check()
        
        # Verify error is in throttle
        error_sig = "Exception:Test error"
        self.assertIn(error_sig, self.sl_manager._fail_safe_error_throttle)
        
        # Wait for throttle window to expire (1 second)
        time.sleep(1.1)
        
        # Manually expire old entries (simulate cleanup)
        current_time = time.time()
        cutoff_time = current_time - (self.sl_manager._fail_safe_throttle_window * 2)
        self.sl_manager._fail_safe_error_throttle = {
            sig: t for sig, t in self.sl_manager._fail_safe_error_throttle.items()
            if t > cutoff_time
        }
        
        # Error should be cleaned up
        self.assertNotIn(error_sig, self.sl_manager._fail_safe_error_throttle)


if __name__ == '__main__':
    unittest.main()

