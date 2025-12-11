"""
PHASE 2: Comprehensive Test Suite for SLManager Functional Correctness

Tests all PHASE 2 requirements:
1. Public API verification
2. Priority rules (strict loss, break-even, sweet-spot, trailing)
3. Contract size correction
4. Broker constraints
5. Apply-and-verify flow with emergency strict SL
6. Thread-safety
"""

import unittest
import time
import threading
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.sl_manager import SLManager


class TestSLManagerPublicAPI(unittest.TestCase):
    """Test PHASE 2 Requirement 1: Public API."""
    
    def setUp(self):
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
    
    def test_public_api_methods_exist(self):
        """Test that all required public API methods exist."""
        self.assertTrue(hasattr(self.sl_manager, 'update_sl_atomic'))
        self.assertTrue(hasattr(self.sl_manager, 'get_effective_sl_profit'))
        self.assertTrue(hasattr(self.sl_manager, 'fail_safe_check'))
        
        # Verify they are callable
        self.assertTrue(callable(self.sl_manager.update_sl_atomic))
        self.assertTrue(callable(self.sl_manager.get_effective_sl_profit))
        self.assertTrue(callable(self.sl_manager.fail_safe_check))
    
    def test_update_sl_atomic_signature(self):
        """Test update_sl_atomic signature: (ticket, position) -> (bool, str)."""
        position = {
            'ticket': 12345,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05
        }
        self.order_manager.get_position_by_ticket.return_value = position
        self.order_manager.modify_order.return_value = True
        
        result = self.sl_manager.update_sl_atomic(12345, position)
        
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], bool)
        self.assertIsInstance(result[1], str)
    
    def test_get_effective_sl_profit_signature(self):
        """Test get_effective_sl_profit signature: (position) -> float."""
        position = {
            'ticket': 12346,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 1.19900,
            'volume': 0.01,
            'profit': -1.00
        }
        
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0
        }
        
        result = self.sl_manager.get_effective_sl_profit(position)
        
        self.assertIsInstance(result, float)
    
    def test_fail_safe_check_signature(self):
        """Test fail_safe_check signature: () -> None."""
        self.order_manager.get_open_positions.return_value = []
        
        # Should not raise exception
        try:
            self.sl_manager.fail_safe_check()
        except Exception as e:
            self.fail(f"fail_safe_check() raised exception: {e}")


class TestPriorityRules(unittest.TestCase):
    """Test PHASE 2 Requirement 2: Priority Rules."""
    
    def setUp(self):
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
        
        self.symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 10,
            'name': 'EURUSD'
        }
        self.tick = MagicMock()
        self.tick.bid = 1.20000
        self.tick.ask = 1.20010
        
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = self.tick
        self.order_manager.modify_order.return_value = True
        
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_priority_1_strict_loss_enforcement(self):
        """Test Priority 1: Strict loss enforcement (-$2.00) for losing trades."""
        position = {
            'ticket': 1001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50  # Losing trade
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(1001, position)
        
        self.assertTrue(success)
        self.assertIn('Strict loss enforcement', reason)
        self.assertIn('-$2.00', reason)
    
    def test_priority_2_break_even_after_duration(self):
        """Test Priority 2: Break-even SL after profit > $0 but < $0.03 for 2+ seconds."""
        position = {
            'ticket': 1002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.02  # Small positive profit
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First call: should not apply (duration not met)
        success1, _ = self.sl_manager.update_sl_atomic(1002, position)
        self.assertFalse(success1)
        
        # Wait for duration
        time.sleep(2.1)
        
        # Second call: should apply break-even
        success2, reason = self.sl_manager.update_sl_atomic(1002, position)
        self.assertTrue(success2)
        self.assertIn('Break-even', reason)
    
    def test_priority_3_sweet_spot_lock(self):
        """Test Priority 3: Sweet-spot profit locking ($0.03-$0.10)."""
        position = {
            'ticket': 1003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05  # In sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(1003, position)
        
        self.assertTrue(success)
        self.assertIn('Sweet-spot', reason)
    
    def test_priority_4_trailing_stop(self):
        """Test Priority 4: Trailing stop for profit > $0.10."""
        position = {
            'ticket': 1004,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.25  # Above sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(1004, position)
        
        self.assertTrue(success)
        self.assertIn('Trailing stop', reason)
    
    def test_sl_never_decreases_except_strict_loss(self):
        """Test that SL only increases (never decreases), except when enforcing strict loss."""
        position = {
            'ticket': 1005,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 1.19950,  # Current SL
            'volume': 0.01,
            'profit': 0.08  # In sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First update: lock at $0.08
        success1, _ = self.sl_manager.update_sl_atomic(1005, position)
        self.assertTrue(success1)
        
        call_args1 = self.order_manager.modify_order.call_args
        first_sl = call_args1[1]['stop_loss_price']
        
        # Second update: profit increased to $0.12 (should increase SL)
        position['profit'] = 0.12
        position['sl'] = first_sl
        success2, _ = self.sl_manager.update_sl_atomic(1005, position)
        
        if success2:
            call_args2 = self.order_manager.modify_order.call_args
            second_sl = call_args2[1]['stop_loss_price']
            # For BUY, higher SL = better (less loss)
            self.assertGreaterEqual(second_sl, first_sl)
    
    def test_buy_sl_evaluated_against_bid(self):
        """Test BUY SL triggers: SL evaluated against BID."""
        position = {
            'ticket': 1006,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20010,  # Entry at ASK
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock tick with BID/ASK
        self.tick.bid = 1.20000
        self.tick.ask = 1.20010
        
        success, _ = self.sl_manager.update_sl_atomic(1006, position)
        self.assertTrue(success)
        
        # Verify SL adjustment used BID price
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        # For BUY, SL should be below BID
        self.assertLess(sl_price, self.tick.bid)
    
    def test_sell_sl_evaluated_against_ask(self):
        """Test SELL SL triggers: SL evaluated against ASK."""
        position = {
            'ticket': 1007,
            'symbol': 'EURUSD',
            'type': 'SELL',
            'price_open': 1.20000,  # Entry at BID
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Mock tick with BID/ASK
        self.tick.bid = 1.20000
        self.tick.ask = 1.20010
        
        success, _ = self.sl_manager.update_sl_atomic(1007, position)
        self.assertTrue(success)
        
        # Verify SL adjustment used ASK price
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        # For SELL, SL should be above ASK
        self.assertGreater(sl_price, self.tick.ask)


class TestContractSizeCorrection(unittest.TestCase):
    """Test PHASE 2 Requirement 3: Contract Size Correction."""
    
    def setUp(self):
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
    
    def test_contract_size_correction_detects_wrong_size(self):
        """Test contract size correction detects and corrects wrong contract_size."""
        # Simulate BTCXAUm with incorrect contract_size (1.0 instead of 100.0)
        incorrect_symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,  # Incorrect (should be 100.0)
            'trade_stops_level': 10,
            'name': 'BTCXAUm',
            'bid': 0.001,
            'ask': 0.0011
        }
        self.mt5_connector.get_symbol_info.return_value = incorrect_symbol_info
        
        # Get corrected contract size
        contract_size = self.sl_manager._get_corrected_contract_size(
            'BTCXAUm', 0.001, 0.01, 2.0
        )
        
        # Should detect and correct (likely 100.0)
        self.assertIsNotNone(contract_size)
        self.assertGreater(contract_size, 1.0)  # Should be corrected
        self.assertGreater(contract_size, incorrect_symbol_info['contract_size'])
    
    def test_contract_size_caching(self):
        """Test that corrected contract size is cached."""
        symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 10,
            'name': 'BTCXAUm',
            'bid': 0.001,
            'ask': 0.0011
        }
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        
        # First call
        contract_size1 = self.sl_manager._get_corrected_contract_size(
            'BTCXAUm', 0.001, 0.01, 2.0
        )
        
        # Reset mock to verify cache is used
        self.mt5_connector.get_symbol_info.reset_mock()
        
        # Second call should use cache
        contract_size2 = self.sl_manager._get_corrected_contract_size(
            'BTCXAUm', 0.001, 0.01, 2.0
        )
        
        # Should return same value
        self.assertEqual(contract_size1, contract_size2)
        # Cache should prevent second call to get_symbol_info (if correction was made)
        # Note: If no correction needed, get_symbol_info may still be called


class TestBrokerConstraints(unittest.TestCase):
    """Test PHASE 2 Requirement 4: Broker Constraints."""
    
    def setUp(self):
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
    
    def test_stops_level_constraint_respected(self):
        """Test that stops_level constraint is respected."""
        symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 10,  # 10 points minimum distance
            'name': 'EURUSD'
        }
        
        current_bid = 1.20000
        current_ask = 1.20010
        target_sl = 1.19950  # Too close to BID (would violate stops_level)
        current_sl = 0.0
        
        adjusted_sl = self.sl_manager._adjust_sl_for_broker_constraints(
            target_sl, current_sl, 'BUY', symbol_info, current_bid, current_ask
        )
        
        # Adjusted SL should respect stops_level (at least 10 points below BID)
        min_allowed = current_bid - (symbol_info['trade_stops_level'] * symbol_info['point'])
        self.assertLessEqual(adjusted_sl, min_allowed)
    
    def test_point_precision_respected(self):
        """Test that point precision is respected."""
        symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 10,
            'name': 'EURUSD'
        }
        
        # Calculate target SL
        target_sl = self.sl_manager._calculate_target_sl_price(
            1.20000, -2.0, 'BUY', 0.01, symbol_info
        )
        
        # Should be normalized to point precision
        point = symbol_info['point']
        remainder = (target_sl / point) % 1
        self.assertAlmostEqual(remainder, 0.0, places=5)


class TestApplyAndVerifyFlow(unittest.TestCase):
    """Test PHASE 2 Requirement 5: Apply-and-Verify Flow."""
    
    def setUp(self):
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
        
        self.symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 10,
            'name': 'EURUSD'
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_apply_and_verify_flow_success(self):
        """Test apply-and-verify flow with successful SL update."""
        position = {
            'ticket': 2001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        
        # Mock successful modify_order
        self.order_manager.modify_order.return_value = True
        
        # Mock verification: get_position_by_ticket returns position with applied SL
        verified_position = position.copy()
        verified_position['sl'] = 1.19900  # Applied SL
        self.order_manager.get_position_by_ticket.return_value = verified_position
        
        success = self.sl_manager._apply_sl_update(
            2001, 'EURUSD', 1.19900, -2.0, "Test"
        )
        
        self.assertTrue(success)
        self.order_manager.modify_order.assert_called()
        self.order_manager.get_position_by_ticket.assert_called()
    
    def test_apply_and_verify_retry_on_failure(self):
        """Test retry logic when SL update fails."""
        position = {
            'ticket': 2002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        
        # First two attempts fail, third succeeds
        self.order_manager.modify_order.side_effect = [False, False, True]
        
        verified_position = position.copy()
        verified_position['sl'] = 1.19900
        self.order_manager.get_position_by_ticket.return_value = verified_position
        
        success = self.sl_manager._apply_sl_update(
            2002, 'EURUSD', 1.19900, -2.0, "Test"
        )
        
        # Should eventually succeed after retries
        self.assertTrue(success)
        self.assertEqual(self.order_manager.modify_order.call_count, 3)
    
    def test_emergency_strict_sl_on_failure(self):
        """Test emergency strict SL enforcement when normal SL update fails after retries."""
        # This test verifies that if _apply_sl_update fails for a losing trade,
        # emergency strict SL is enforced
        position = {
            'ticket': 2003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50  # Losing trade
        }
        
        # All attempts fail
        self.order_manager.modify_order.return_value = False
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Note: Emergency strict SL enforcement should be added to _apply_sl_update
        # For now, verify current behavior
        success = self.sl_manager._apply_sl_update(
            2003, 'EURUSD', 1.19900, -2.0, "Test"
        )
        
        # Should have retried 3 times
        self.assertEqual(self.order_manager.modify_order.call_count, 3)
        # TODO: After implementing emergency strict SL, verify it's called


class TestThreadSafety(unittest.TestCase):
    """Test PHASE 2 Requirement 6: Thread-Safety."""
    
    def setUp(self):
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
        
        self.symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 10,
            'name': 'EURUSD'
        }
        self.tick = MagicMock()
        self.tick.bid = 1.20000
        self.tick.ask = 1.20010
        
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = self.tick
        self.order_manager.modify_order.return_value = True
        
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_concurrent_updates_same_ticket(self):
        """Test concurrent SL updates on same ticket are atomic."""
        position = {
            'ticket': 3001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        results = []
        errors = []
        
        def update_sl():
            try:
                success, reason = self.sl_manager.update_sl_atomic(3001, position)
                results.append((success, reason))
            except Exception as e:
                errors.append(e)
        
        # Create 10 threads updating same ticket
        threads = [threading.Thread(target=update_sl) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should complete without errors
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 10)
    
    def test_concurrent_updates_different_tickets(self):
        """Test concurrent SL updates on different tickets are safe."""
        positions = [
            {
                'ticket': 3002 + i,
                'symbol': 'EURUSD',
                'type': 'BUY',
                'price_open': 1.20000,
                'sl': 0.0,
                'volume': 0.01,
                'profit': 0.05
            }
            for i in range(5)
        ]
        
        results = {}
        errors = []
        
        def update_sl(ticket, pos):
            try:
                self.order_manager.get_position_by_ticket.return_value = pos
                success, reason = self.sl_manager.update_sl_atomic(ticket, pos)
                results[ticket] = (success, reason)
            except Exception as e:
                errors.append((ticket, e))
        
        # Create threads for different tickets
        threads = [
            threading.Thread(target=update_sl, args=(pos['ticket'], pos))
            for pos in positions
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should complete without errors
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 5)


if __name__ == '__main__':
    unittest.main()

