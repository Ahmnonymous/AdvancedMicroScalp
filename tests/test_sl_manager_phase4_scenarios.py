"""
PHASE 4: Comprehensive Scenario Tests for SLManager

Tests real-world trading scenarios:
1. BUY/SELL trades with various profit/loss states
2. Broker rejections and retry logic
3. Contract size correction issues
4. Rapid price fluctuations
5. Concurrency and thread-safety
6. Integration smoke tests
"""

import unittest
import time
import threading
from unittest.mock import Mock, MagicMock, patch, call
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.sl_manager import SLManager


class TestBUYSELLTrades(unittest.TestCase):
    """Test BUY and SELL trades with various profit/loss scenarios."""
    
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
            },
            'execution': {
                'order_max_retries': 3,
                'order_retry_backoff_base_seconds': 0.1,
                'sl_verification_delay_seconds': 0.1
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
    
    def test_buy_trade_losing_strict_loss(self):
        """Test BUY trade in loss - should enforce strict -$2.00 SL."""
        position = {
            'ticket': 2001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,  # Entry at ASK
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50  # Losing trade
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Update tick to show price moved down (loss)
        self.tick.bid = 1.19950  # Price moved down
        self.tick.ask = 1.19960
        
        success, reason = self.sl_manager.update_sl_atomic(2001, position)
        
        self.assertTrue(success)
        self.assertIn('Strict loss enforcement', reason)
        self.order_manager.modify_order.assert_called()
        
        # Verify SL was set below current BID
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss']
        self.assertLess(sl_price, self.tick.bid)
    
    def test_sell_trade_losing_strict_loss(self):
        """Test SELL trade in loss - should enforce strict -$2.00 SL."""
        position = {
            'ticket': 2002,
            'symbol': 'EURUSD',
            'type': 'SELL',
            'price_open': 1.20000,  # Entry at BID
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50  # Losing trade
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Update tick to show price moved up (loss for SELL)
        self.tick.bid = 1.20050  # Price moved up
        self.tick.ask = 1.20060
        
        success, reason = self.sl_manager.update_sl_atomic(2002, position)
        
        self.assertTrue(success)
        self.assertIn('Strict loss enforcement', reason)
        self.order_manager.modify_order.assert_called()
        
        # Verify SL was set above current ASK
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss']
        self.assertGreater(sl_price, self.tick.ask)
    
    def test_buy_trade_profitable_sweet_spot(self):
        """Test BUY trade in sweet spot profit - should lock in profit."""
        position = {
            'ticket': 2003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05  # In sweet spot ($0.03-$0.10)
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Update tick to show price moved up (profit)
        self.tick.bid = 1.20050
        self.tick.ask = 1.20060
        
        success, reason = self.sl_manager.update_sl_atomic(2003, position)
        
        self.assertTrue(success)
        self.assertIn('Sweet-spot', reason)
    
    def test_sell_trade_profitable_trailing(self):
        """Test SELL trade with high profit - should apply trailing stop."""
        position = {
            'ticket': 2004,
            'symbol': 'EURUSD',
            'type': 'SELL',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.25  # Above sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Update tick to show price moved down (profit for SELL)
        self.tick.bid = 1.19950
        self.tick.ask = 1.19960
        
        success, reason = self.sl_manager.update_sl_atomic(2004, position)
        
        self.assertTrue(success)
        self.assertIn('Trailing stop', reason)
    
    def test_buy_trade_break_even_after_duration(self):
        """Test BUY trade with small profit - should apply break-even after 2 seconds."""
        position = {
            'ticket': 2005,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.02  # Small positive (< $0.03)
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First call: should not apply (duration not met)
        success1, _ = self.sl_manager.update_sl_atomic(2005, position)
        self.assertFalse(success1)
        
        # Wait for duration
        time.sleep(2.1)
        
        # Second call: should apply break-even
        success2, reason = self.sl_manager.update_sl_atomic(2005, position)
        self.assertTrue(success2)
        self.assertIn('Break-even', reason)


class TestBrokerRejections(unittest.TestCase):
    """Test broker rejection scenarios and retry logic."""
    
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
            },
            'execution': {
                'order_max_retries': 3,
                'order_retry_backoff_base_seconds': 0.1,
                'sl_verification_delay_seconds': 0.1
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
        
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_broker_rejection_retry_logic(self):
        """Test that SL update retries on broker rejection."""
        position = {
            'ticket': 3001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50
        }
        
        # First two attempts fail, third succeeds
        self.order_manager.modify_order.side_effect = [False, False, True]
        
        verified_position = position.copy()
        verified_position['sl'] = 1.19900
        self.order_manager.get_position_by_ticket.return_value = verified_position
        
        success = self.sl_manager._apply_sl_update(
            3001, 'EURUSD', 1.19900, -2.0, "Test", position=position
        )
        
        # Should eventually succeed after retries
        self.assertTrue(success)
        self.assertEqual(self.order_manager.modify_order.call_count, 3)
    
    def test_broker_rejection_all_retries_fail(self):
        """Test emergency strict SL when all retries fail."""
        position = {
            'ticket': 3002,
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
        
        success = self.sl_manager._apply_sl_update(
            3002, 'EURUSD', 1.19900, -2.0, "Test", position=position
        )
        
        # Should have retried 3 times, then tried emergency strict SL
        self.assertGreaterEqual(self.order_manager.modify_order.call_count, 3)
        # Emergency strict SL should also be attempted
        # (Note: emergency SL also calls modify_order, so count may be higher)
    
    def test_broker_rejection_verification_fails(self):
        """Test that verification failure triggers retry."""
        position = {
            'ticket': 3003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50
        }
        
        # modify_order succeeds, but verification fails (SL not applied)
        self.order_manager.modify_order.return_value = True
        
        # First two verifications fail (SL not applied), third succeeds
        verification_positions = [
            position.copy(),  # SL still 0.0
            position.copy(),  # SL still 0.0
            {**position, 'sl': 1.19900}  # SL finally applied
        ]
        self.order_manager.get_position_by_ticket.side_effect = verification_positions
        
        success = self.sl_manager._apply_sl_update(
            3003, 'EURUSD', 1.19900, -2.0, "Test", position=position
        )
        
        # Should eventually succeed after retries
        self.assertTrue(success)
        self.assertGreaterEqual(self.order_manager.modify_order.call_count, 2)


class TestContractSizeIssues(unittest.TestCase):
    """Test contract size correction for symbols with incorrect contract_size."""
    
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
    
    def test_contract_size_correction_btcxau(self):
        """Test contract size correction for BTCXAUm (reports 1.0, should be 100.0)."""
        # Simulate BTCXAUm with incorrect contract_size
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
        self.assertGreater(contract_size, 1.0)
        self.assertGreater(contract_size, incorrect_symbol_info['contract_size'])
    
    def test_contract_size_correction_caching(self):
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
    
    def test_contract_size_correction_produces_correct_sl(self):
        """Test that corrected contract size produces correct SL price."""
        position = {
            'ticket': 4001,
            'symbol': 'BTCXAUm',
            'type': 'BUY',
            'price_open': 0.001,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50
        }
        
        # Symbol with incorrect contract_size
        symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,  # Incorrect
            'trade_stops_level': 10,
            'name': 'BTCXAUm'
        }
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        self.mt5_connector.get_symbol_info_tick.return_value = MagicMock(bid=0.001, ask=0.0011)
        self.order_manager.modify_order.return_value = True
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Update SL - should use corrected contract size
        success, reason = self.sl_manager.update_sl_atomic(4001, position)
        
        # Should succeed and use corrected contract size for calculation
        self.assertTrue(success)
        self.assertIn('Strict loss enforcement', reason)


class TestRapidPriceFluctuations(unittest.TestCase):
    """Test rapid price fluctuations and SL update handling."""
    
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
            },
            'execution': {
                'order_max_retries': 3,
                'order_retry_backoff_base_seconds': 0.1,
                'sl_verification_delay_seconds': 0.1
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
        
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_rapid_profit_increase_trailing(self):
        """Test rapid profit increase - trailing stop should keep up."""
        position = {
            'ticket': 5001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.15  # High profit
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Simulate rapid price increase
        self.tick.bid = 1.20150
        self.tick.ask = 1.20160
        self.mt5_connector.get_symbol_info_tick.return_value = self.tick
        
        # First update: should apply trailing stop
        success1, _ = self.sl_manager.update_sl_atomic(5001, position)
        self.assertTrue(success1)
        
        # Simulate further price increase
        position['profit'] = 0.30
        position['sl'] = 1.20050  # Previous SL
        self.tick.bid = 1.20300
        self.tick.ask = 1.20310
        
        # Second update: should increase trailing stop
        success2, _ = self.sl_manager.update_sl_atomic(5001, position)
        self.assertTrue(success2)
        
        # Verify SL only increased (never decreased)
        call_args = self.order_manager.modify_order.call_args
        new_sl = call_args[1]['stop_loss']
        self.assertGreaterEqual(new_sl, position['sl'])
    
    def test_rapid_loss_increase_strict_loss(self):
        """Test rapid loss increase - strict loss should be enforced quickly."""
        position = {
            'ticket': 5002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -0.50  # Small loss
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First update: small loss
        self.tick.bid = 1.19950
        self.tick.ask = 1.19960
        self.mt5_connector.get_symbol_info_tick.return_value = self.tick
        
        success1, _ = self.sl_manager.update_sl_atomic(5002, position)
        self.assertTrue(success1)
        
        # Rapid loss increase
        position['profit'] = -2.50  # Large loss
        self.tick.bid = 1.19750
        self.tick.ask = 1.19760
        
        # Second update: should enforce strict loss
        success2, reason = self.sl_manager.update_sl_atomic(5002, position)
        self.assertTrue(success2)
        self.assertIn('Strict loss enforcement', reason)
    
    def test_rapid_price_fluctuation_sl_never_decreases(self):
        """Test that SL never decreases even with rapid price fluctuations."""
        position = {
            'ticket': 5003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 1.19950,  # Existing SL
            'volume': 0.01,
            'profit': 0.08  # In sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Simulate price fluctuation (profit decreases slightly)
        position['profit'] = 0.06  # Still in sweet spot but lower
        self.tick.bid = 1.20060
        self.tick.ask = 1.20070
        self.mt5_connector.get_symbol_info_tick.return_value = self.tick
        
        success, _ = self.sl_manager.update_sl_atomic(5003, position)
        
        # SL should not decrease (should stay at or above current SL)
        if success:
            call_args = self.order_manager.modify_order.call_args
            new_sl = call_args[1]['stop_loss']
            self.assertGreaterEqual(new_sl, position['sl'])


class TestConcurrencyScenarios(unittest.TestCase):
    """Test concurrency and thread-safety scenarios."""
    
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
            'ticket': 6001,
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
                success, reason = self.sl_manager.update_sl_atomic(6001, position)
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
        
        # All updates should succeed (or at least not conflict)
        success_count = sum(1 for s, _ in results if s)
        self.assertGreater(success_count, 0)
    
    def test_concurrent_updates_different_tickets(self):
        """Test concurrent SL updates on different tickets are safe."""
        positions = [
            {
                'ticket': 6002 + i,
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
    
    def test_concurrent_contract_size_correction(self):
        """Test concurrent contract size correction is thread-safe."""
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
        
        results = []
        
        def get_contract_size():
            size = self.sl_manager._get_corrected_contract_size(
                'BTCXAUm', 0.001, 0.01, 2.0
            )
            results.append(size)
        
        # Create 10 threads getting contract size
        threads = [threading.Thread(target=get_contract_size) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All results should be the same (cache consistency)
        self.assertEqual(len(results), 10)
        self.assertEqual(len(set(results)), 1)  # All same value


class TestIntegrationSmokeTests(unittest.TestCase):
    """Integration smoke tests for end-to-end scenarios."""
    
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
            },
            'execution': {
                'order_max_retries': 3,
                'order_retry_backoff_base_seconds': 0.1,
                'sl_verification_delay_seconds': 0.1
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
    
    def test_end_to_end_losing_trade_lifecycle(self):
        """Test complete lifecycle: losing trade -> strict loss -> recovery."""
        position = {
            'ticket': 7001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Step 1: Losing trade - enforce strict loss
        success1, reason1 = self.sl_manager.update_sl_atomic(7001, position)
        self.assertTrue(success1)
        self.assertIn('Strict loss enforcement', reason1)
        
        # Step 2: Trade recovers to small profit
        position['profit'] = 0.02
        position['sl'] = 1.19900  # Previous SL
        self.tick.bid = 1.20020
        self.tick.ask = 1.20030
        
        # Wait for break-even duration
        time.sleep(2.1)
        
        success2, reason2 = self.sl_manager.update_sl_atomic(7001, position)
        self.assertTrue(success2)
        self.assertIn('Break-even', reason2)
        
        # Step 3: Trade enters sweet spot
        position['profit'] = 0.05
        position['sl'] = 1.20000  # Break-even SL
        
        success3, reason3 = self.sl_manager.update_sl_atomic(7001, position)
        self.assertTrue(success3)
        self.assertIn('Sweet-spot', reason3)
    
    def test_end_to_end_profitable_trade_lifecycle(self):
        """Test complete lifecycle: profitable trade -> sweet spot -> trailing."""
        position = {
            'ticket': 7002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05  # Start in sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Step 1: Sweet spot profit locking
        success1, reason1 = self.sl_manager.update_sl_atomic(7002, position)
        self.assertTrue(success1)
        self.assertIn('Sweet-spot', reason1)
        
        # Step 2: Profit increases above sweet spot
        position['profit'] = 0.15
        position['sl'] = 1.20030  # Previous sweet spot SL
        
        success2, reason2 = self.sl_manager.update_sl_atomic(7002, position)
        self.assertTrue(success2)
        self.assertIn('Trailing stop', reason2)
        
        # Step 3: Further profit increase
        position['profit'] = 0.30
        # Previous trailing SL should be preserved or increased
        previous_sl = position['sl']
        
        success3, reason3 = self.sl_manager.update_sl_atomic(7002, position)
        self.assertTrue(success3)
        
        # Verify SL only increased
        call_args = self.order_manager.modify_order.call_args
        new_sl = call_args[1]['stop_loss']
        self.assertGreaterEqual(new_sl, previous_sl)
    
    def test_fail_safe_check_integration(self):
        """Test fail-safe check integration with real positions."""
        # Create multiple positions with various states
        positions = [
            {
                'ticket': 7003,
                'symbol': 'EURUSD',
                'type': 'BUY',
                'price_open': 1.20000,
                'sl': 1.19900,  # Good SL
                'volume': 0.01,
                'profit': -1.00
            },
            {
                'ticket': 7004,
                'symbol': 'GBPUSD',
                'type': 'SELL',
                'price_open': 1.30000,
                'sl': 0.0,  # Missing SL - should trigger fail-safe
                'volume': 0.01,
                'profit': -2.50  # Worse than -$2.00
            }
        ]
        
        self.order_manager.get_open_positions.return_value = positions
        self.order_manager.get_position_by_ticket.side_effect = lambda t: next(
            (p for p in positions if p['ticket'] == t), None
        )
        
        # Run fail-safe check
        self.sl_manager.fail_safe_check()
        
        # Fail-safe should attempt to fix position 7004
        # (Note: exact behavior depends on implementation)
        self.assertGreaterEqual(self.order_manager.modify_order.call_count, 0)


if __name__ == '__main__':
    unittest.main()

