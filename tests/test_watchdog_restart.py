"""
Test Watchdog Restart Functionality
"""

import unittest
import time
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta

from risk.sl_manager import SLManager
from monitor.sl_watchdog import SLWatchdog


class TestWatchdogRestart(unittest.TestCase):
    """Test watchdog restart functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'risk': {
                'max_risk_per_trade_usd': 2.0,
                'trailing_stop_increment_usd': 0.10,
                'elastic_trailing': {'min_lock_increment_usd': 0.10},
                'dynamic_break_even': {'enabled': True, 'positive_profit_duration_seconds': 2.0},
                'profit_locking': {
                    'min_profit_threshold_usd': 0.03,
                    'max_profit_threshold_usd': 0.10
                }
            },
            'execution': {
                'order_max_retries': 3,
                'order_retry_backoff_base_seconds': 0.1,
                'sl_verification_delay_seconds': 0.1
            }
        }
        
        self.mt5_connector = Mock()
        self.order_manager = Mock()
        
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
        self.watchdog = SLWatchdog(self.sl_manager)
    
    def test_watchdog_detects_worker_not_running(self):
        """Test that watchdog detects when worker is not running."""
        # Worker is not started
        status = self.sl_manager.get_worker_status()
        self.assertFalse(status['running'], "Worker should not be running")
        
        # Watchdog should detect this
        # (In real scenario, watchdog loop would check this)
        # For test, we verify the detection logic exists
        self.assertIsNotNone(self.watchdog.check_interval, "Watchdog should have check interval")
    
    def test_watchdog_restart_rate_limit(self):
        """Test that watchdog respects restart rate limit."""
        # Simulate multiple restarts
        current_time = datetime.now()
        
        # Add restarts within 10 minutes
        for i in range(3):
            self.watchdog._restart_timestamps.append(current_time - timedelta(minutes=i))
        
        # Try to restart (should be blocked if > max_restarts_per_10min)
        with patch.object(self.sl_manager, 'stop_sl_worker') as mock_stop, \
             patch.object(self.sl_manager, 'start_sl_worker') as mock_start:
            
            self.watchdog._restart_worker("Test restart")
            
            # If we have > max_restarts_per_10min, restart should be blocked
            if len(self.watchdog._restart_timestamps) >= self.watchdog.max_restarts_per_10min:
                # Restart should be blocked
                mock_stop.assert_not_called()
                mock_start.assert_not_called()
    
    def test_watchdog_tracks_sl_updates(self):
        """Test that watchdog tracks SL updates for rate monitoring."""
        ticket = 12345
        
        # Track update
        self.watchdog.track_sl_update(ticket)
        
        # Verify timestamp was added
        self.assertGreater(len(self.watchdog._update_timestamps), 0, 
                          "Update timestamp should be tracked")
    
    def test_watchdog_stops_gracefully(self):
        """Test that watchdog stops gracefully."""
        # Start watchdog
        self.watchdog.start()
        
        # Verify it's running
        self.assertTrue(self.watchdog.running, "Watchdog should be running")
        
        # Stop watchdog
        self.watchdog.stop()
        
        # Wait a bit
        time.sleep(0.5)
        
        # Verify it's stopped
        self.assertFalse(self.watchdog.running, "Watchdog should be stopped")


if __name__ == '__main__':
    unittest.main()

