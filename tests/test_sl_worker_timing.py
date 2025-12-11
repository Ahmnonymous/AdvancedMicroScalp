"""
Test SL Worker Timing and Per-Ticket Atomic Updates
"""

import unittest
import time
import threading
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from risk.sl_manager import SLManager


class TestSLWorkerTiming(unittest.TestCase):
    """Test SL worker loop timing and per-ticket atomic updates."""
    
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
    
    def test_worker_loop_250ms_cadence(self):
        """Test that worker loop maintains 250ms cadence."""
        # Mock positions
        self.order_manager.get_open_positions.return_value = []
        
        # Start worker
        self.sl_manager.start_sl_worker()
        
        # Wait for a few loops
        time.sleep(1.0)
        
        # Check timing stats
        stats = self.sl_manager.get_timing_stats()
        loop_durations = stats.get('loop_duration', {})
        
        # Stop worker
        self.sl_manager.stop_sl_worker()
        
        # Verify loop durations exist
        self.assertIsNotNone(loop_durations)
        if loop_durations:
            # Average should be close to 250ms (allowing some variance)
            avg_duration = loop_durations.get('avg', 0)
            self.assertLess(avg_duration, 500, "Loop duration should be < 500ms")
            self.assertGreater(avg_duration, 0, "Loop duration should be > 0")
    
    def test_per_ticket_atomic_updates(self):
        """Test that SL updates are atomic per ticket."""
        # Create mock positions
        ticket1 = 12345
        ticket2 = 67890
        
        position1 = {
            'ticket': ticket1,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10050,
            'profit': -1.50,
            'volume': 0.01,
            'sl': 0.0
        }
        
        position2 = {
            'ticket': ticket2,
            'symbol': 'GBPUSD',
            'type': 'SELL',
            'price_open': 1.25000,
            'price_current': 1.24950,
            'profit': -1.80,
            'volume': 0.01,
            'sl': 0.0
        }
        
        # Mock symbol info
        symbol_info = {
            'contract_size': 100000.0,
            'point': 0.00001,
            'stops_level': 10,
            'bid': 1.10050,
            'ask': 1.10060
        }
        
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = Mock(bid=1.10050, ask=1.10060)
        
        # Mock order manager
        self.order_manager.get_open_positions.return_value = [position1, position2]
        self.order_manager.get_position_by_ticket.side_effect = lambda t: position1 if t == ticket1 else position2
        self.order_manager.modify_order.return_value = True
        
        # Start worker
        self.sl_manager.start_sl_worker()
        
        # Wait for updates
        time.sleep(2.0)
        
        # Stop worker
        self.sl_manager.stop_sl_worker()
        
        # Verify updates were attempted
        stats = self.sl_manager.get_timing_stats()
        update_counts = stats.get('update_counts', {})
        
        # Both tickets should have update attempts
        self.assertGreater(update_counts.get(ticket1, 0), 0, "Ticket1 should have update attempts")
        self.assertGreater(update_counts.get(ticket2, 0), 0, "Ticket2 should have update attempts")
    
    def test_per_ticket_lock_isolation(self):
        """Test that per-ticket locks prevent concurrent updates."""
        ticket = 12345
        
        position = {
            'ticket': ticket,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10050,
            'profit': -1.50,
            'volume': 0.01,
            'sl': 0.0
        }
        
        # Mock symbol info
        symbol_info = {
            'contract_size': 100000.0,
            'point': 0.00001,
            'stops_level': 10,
            'bid': 1.10050,
            'ask': 1.10060
        }
        
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = Mock(bid=1.10050, ask=1.10060)
        
        # Mock order manager
        self.order_manager.get_open_positions.return_value = [position]
        self.order_manager.get_position_by_ticket.return_value = position
        self.order_manager.modify_order.return_value = True
        
        # Track concurrent access
        update_count = {'count': 0}
        lock_held = {'held': False}
        
        original_update = self.sl_manager.update_sl_atomic
        
        def tracked_update(ticket, position):
            if lock_held['held']:
                # Another update is in progress - this should not happen with proper locking
                update_count['count'] += 1
            lock_held['held'] = True
            try:
                result = original_update(ticket, position)
                return result
            finally:
                lock_held['held'] = False
        
        self.sl_manager.update_sl_atomic = tracked_update
        
        # Start worker
        self.sl_manager.start_sl_worker()
        
        # Wait for updates
        time.sleep(1.0)
        
        # Stop worker
        self.sl_manager.stop_sl_worker()
        
        # Verify no concurrent updates (count should be 0)
        self.assertEqual(update_count['count'], 0, "No concurrent updates should occur with proper locking")
    
    def test_worker_stop_gracefully(self):
        """Test that worker stops gracefully."""
        self.order_manager.get_open_positions.return_value = []
        
        # Start worker
        self.sl_manager.start_sl_worker()
        
        # Verify it's running
        status = self.sl_manager.get_worker_status()
        self.assertTrue(status['running'], "Worker should be running")
        
        # Stop worker
        self.sl_manager.stop_sl_worker()
        
        # Wait a bit
        time.sleep(0.5)
        
        # Verify it's stopped
        status = self.sl_manager.get_worker_status()
        self.assertFalse(status['running'], "Worker should be stopped")


if __name__ == '__main__':
    unittest.main()

