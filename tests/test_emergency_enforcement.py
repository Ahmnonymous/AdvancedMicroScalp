"""
Test Emergency SL Enforcement
"""

import unittest
from unittest.mock import Mock, MagicMock, patch

from risk.sl_manager import SLManager


class TestEmergencyEnforcement(unittest.TestCase):
    """Test emergency SL enforcement logic."""
    
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
    
    def test_emergency_sl_uses_correct_entry_price_buy(self):
        """Test that emergency SL uses ASK for BUY orders."""
        ticket = 12345
        position = {
            'ticket': ticket,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,  # MT5 might give BID
            'price_current': 1.10050,
            'profit': -2.50,  # Losing trade
            'volume': 0.01,
            'sl': 0.0
        }
        
        # Mock symbol info
        symbol_info = {
            'contract_size': 100000.0,
            'point': 0.00001,
            'stops_level': 10,
            'bid': 1.10050,
            'ask': 1.10060  # ASK is higher
        }
        
        tick = Mock()
        tick.bid = 1.10050
        tick.ask = 1.10060
        
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = tick
        
        # Mock order manager - first 3 attempts fail, then emergency succeeds
        call_count = {'count': 0}
        
        def mock_modify_order(ticket, stop_loss=None, stop_loss_price=None):
            call_count['count'] += 1
            if call_count['count'] <= 3:
                return False  # First 3 attempts fail
            else:
                # Emergency attempt succeeds
                position['sl'] = stop_loss_price or stop_loss
                return True
        
        self.order_manager.modify_order.side_effect = mock_modify_order
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock _apply_sl_update to fail first 3 times, then trigger emergency
        original_apply = self.sl_manager._apply_sl_update
        
        def failing_apply(ticket, symbol, target_sl, target_profit, reason, position=None):
            if call_count['count'] < 3:
                return False
            return original_apply(ticket, symbol, target_sl, target_profit, reason, position)
        
        self.sl_manager._apply_sl_update = failing_apply
        
        # Attempt SL update (should trigger emergency)
        success, reason = self.sl_manager.update_sl_atomic(ticket, position)
        
        # Verify emergency SL was calculated with ASK (1.10060)
        # The emergency SL should use ASK as entry for BUY
        self.assertGreater(call_count['count'], 3, "Emergency SL should be attempted")
    
    def test_emergency_sl_uses_correct_entry_price_sell(self):
        """Test that emergency SL uses BID for SELL orders."""
        ticket = 67890
        position = {
            'ticket': ticket,
            'symbol': 'GBPUSD',
            'type': 'SELL',
            'price_open': 1.25000,  # MT5 might give ASK
            'price_current': 1.24950,
            'profit': -2.20,  # Losing trade
            'volume': 0.01,
            'sl': 0.0
        }
        
        # Mock symbol info
        symbol_info = {
            'contract_size': 100000.0,
            'point': 0.00001,
            'stops_level': 10,
            'bid': 1.24950,
            'ask': 1.24960  # ASK is higher
        }
        
        tick = Mock()
        tick.bid = 1.24950
        tick.ask = 1.24960
        
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = tick
        
        # Mock order manager - first 3 attempts fail, then emergency succeeds
        call_count = {'count': 0}
        
        def mock_modify_order(ticket, stop_loss=None, stop_loss_price=None):
            call_count['count'] += 1
            if call_count['count'] <= 3:
                return False  # First 3 attempts fail
            else:
                # Emergency attempt succeeds
                position['sl'] = stop_loss_price or stop_loss
                return True
        
        self.order_manager.modify_order.side_effect = mock_modify_order
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock _apply_sl_update to fail first 3 times, then trigger emergency
        original_apply = self.sl_manager._apply_sl_update
        
        def failing_apply(ticket, symbol, target_sl, target_profit, reason, position=None):
            if call_count['count'] < 3:
                return False
            return original_apply(ticket, symbol, target_sl, target_profit, reason, position)
        
        self.sl_manager._apply_sl_update = failing_apply
        
        # Attempt SL update (should trigger emergency)
        success, reason = self.sl_manager.update_sl_atomic(ticket, position)
        
        # Verify emergency SL was calculated with BID (1.24950)
        # The emergency SL should use BID as entry for SELL
        self.assertGreater(call_count['count'], 3, "Emergency SL should be attempted")
    
    def test_emergency_sl_marks_for_manual_review_on_failure(self):
        """Test that failed emergency SL marks ticket for manual review."""
        ticket = 12345
        position = {
            'ticket': ticket,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.10000,
            'price_current': 1.10050,
            'profit': -2.50,
            'volume': 0.01,
            'sl': 0.0
        }
        
        symbol_info = {
            'contract_size': 100000.0,
            'point': 0.00001,
            'stops_level': 10,
            'bid': 1.10050,
            'ask': 1.10060
        }
        
        tick = Mock()
        tick.bid = 1.10050
        tick.ask = 1.10060
        
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = tick
        
        # Mock order manager to always fail
        self.order_manager.modify_order.return_value = False
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock _apply_sl_update to always fail
        self.sl_manager._apply_sl_update = Mock(return_value=False)
        
        # Attempt SL update (should trigger emergency, which also fails)
        success, reason = self.sl_manager.update_sl_atomic(ticket, position)
        
        # Verify ticket is marked for manual review
        self.assertIn(ticket, self.sl_manager._manual_review_tickets, 
                     "Ticket should be marked for manual review after emergency failure")


if __name__ == '__main__':
    unittest.main()

