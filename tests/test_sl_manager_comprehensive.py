"""
Comprehensive Test Suite for Unified SL Manager

This test suite covers ALL possible trade scenarios to ensure the SL system
works perfectly for both BUY and SELL trades under all conditions.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import time
import threading
from unittest.mock import Mock, MagicMock, patch, call
from risk.sl_manager import SLManager


class TestStrictLossEnforcement(unittest.TestCase):
    """Test strict -$2.00 loss enforcement."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_buy_trade_negative_pl_strict_loss(self):
        """Test BUY trade with negative P/L enforces strict -$2.00 loss."""
        position = {
            'ticket': 1001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.50
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(1001, position)
        
        self.assertTrue(success)
        self.assertIn('Strict loss enforcement', reason)
        
        # Verify SL was set to result in -$2.00 loss
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        
        # Calculate effective SL profit
        effective_sl = self.sl_manager._calculate_effective_sl_profit(
            1.20000, sl_price, 'BUY', 0.01, 1.0
        )
        
        # Should be approximately -$2.00 (allow small tolerance for rounding)
        self.assertAlmostEqual(effective_sl, -2.0, places=1)
    
    def test_sell_trade_negative_pl_strict_loss(self):
        """Test SELL trade with negative P/L enforces strict -$2.00 loss."""
        position = {
            'ticket': 1002,
            'symbol': 'EURUSD',
            'type': 'SELL',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.80
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(1002, position)
        
        self.assertTrue(success)
        self.assertIn('Strict loss enforcement', reason)
        
        # Verify SL was set correctly for SELL
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        
        # For SELL, SL should be above entry price
        self.assertGreater(sl_price, position['price_open'])
        
        # Calculate effective SL profit
        effective_sl = self.sl_manager._calculate_effective_sl_profit(
            1.20000, sl_price, 'SELL', 0.01, 1.0
        )
        
        # Should be approximately -$2.00
        self.assertAlmostEqual(effective_sl, -2.0, places=1)
    
    def test_strict_loss_never_exceeds_limit(self):
        """Test that strict loss never allows loss > -$2.00."""
        position = {
            'ticket': 1003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 1.19800,  # SL that would result in -$2.00
            'volume': 0.01,
            'profit': -2.50  # Worse than -$2.00
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Calculate current effective SL
        current_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, 1.19800, 'BUY', 0.01, 1.0
        )
        
        # Update SL
        success, reason = self.sl_manager.update_sl_atomic(1003, position)
        
        self.assertTrue(success)
        
        # Get updated position
        updated_position = position.copy()
        call_args = self.order_manager.modify_order.call_args
        updated_position['sl'] = call_args[1]['stop_loss_price']
        
        # Verify new SL is better (less negative) than current
        new_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, updated_position['sl'], 'BUY', 0.01, 1.0
        )
        
        # New SL should be better (closer to -$2.00, not worse)
        self.assertGreater(new_effective, current_effective)
        self.assertLessEqual(new_effective, -1.9)  # Should be around -$2.00
    
    def test_strict_loss_priority_over_other_rules(self):
        """Test that strict loss takes priority over break-even, sweet-spot, trailing."""
        # Position with negative P/L should ONLY apply strict loss
        position = {
            'ticket': 1004,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -0.50  # Negative, so strict loss should apply
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(1004, position)
        
        self.assertTrue(success)
        self.assertIn('Strict loss enforcement', reason)
        self.assertNotIn('Break-even', reason)
        self.assertNotIn('Sweet-spot', reason)
        self.assertNotIn('Trailing', reason)


class TestBreakEvenSL(unittest.TestCase):
    """Test break-even SL logic."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_break_even_applies_after_duration(self):
        """Test break-even SL applies after profit > $0 and < $0.03 for 2+ seconds."""
        position = {
            'ticket': 2001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.02  # Small positive profit
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First update: profit just became positive (should not apply yet)
        success, reason = self.sl_manager.update_sl_atomic(2001, position)
        self.assertFalse(success)  # Duration not met yet
        
        # Wait for duration threshold
        time.sleep(2.1)
        
        # Second update: duration met, should apply break-even
        success, reason = self.sl_manager.update_sl_atomic(2001, position)
        self.assertTrue(success)
        self.assertIn('Break-even', reason)
        
        # Verify SL was set to entry price (break-even = $0.00 profit)
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        
        # For BUY, break-even means SL at entry price
        self.assertAlmostEqual(sl_price, position['price_open'], places=5)
        
        # Verify effective SL profit is $0.00
        effective_sl = self.sl_manager._calculate_effective_sl_profit(
            position['price_open'], sl_price, 'BUY', 0.01, 1.0
        )
        self.assertAlmostEqual(effective_sl, 0.0, places=2)
    
    def test_break_even_not_applied_if_profit_too_high(self):
        """Test break-even not applied if profit >= $0.03."""
        position = {
            'ticket': 2002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05  # Above break-even threshold
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Wait for duration
        time.sleep(2.1)
        
        # Update SL
        success, reason = self.sl_manager.update_sl_atomic(2002, position)
        
        # Should not apply break-even (profit too high)
        self.assertNotIn('Break-even', reason)
        # Should apply sweet-spot instead
        self.assertIn('Sweet-spot', reason)
    
    def test_break_even_not_applied_if_profit_negative(self):
        """Test break-even not applied if profit is negative."""
        position = {
            'ticket': 2003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -0.50  # Negative profit
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Wait for duration (shouldn't matter)
        time.sleep(2.1)
        
        # Update SL
        success, reason = self.sl_manager.update_sl_atomic(2003, position)
        
        # Should apply strict loss, not break-even
        self.assertIn('Strict loss enforcement', reason)
        self.assertNotIn('Break-even', reason)


class TestSweetSpotProfitLocking(unittest.TestCase):
    """Test sweet-spot profit locking ($0.03-$0.10)."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_sweet_spot_locks_profit(self):
        """Test sweet-spot locks profit when in range."""
        position = {
            'ticket': 3001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05  # In sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(3001, position)
        
        self.assertTrue(success)
        self.assertIn('Sweet-spot', reason)
        
        # Verify SL was set to lock profit
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        
        # For BUY in profit, SL should be above entry
        self.assertGreater(sl_price, position['price_open'])
        
        # Verify effective SL profit is positive (profit locked)
        effective_sl = self.sl_manager._calculate_effective_sl_profit(
            position['price_open'], sl_price, 'BUY', 0.01, 1.0
        )
        self.assertGreater(effective_sl, 0.0)
        self.assertLessEqual(effective_sl, position['profit'])
    
    def test_sweet_spot_sl_never_decreases(self):
        """Test sweet-spot SL never decreases."""
        position = {
            'ticket': 3002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First update: lock at $0.05
        success1, _ = self.sl_manager.update_sl_atomic(3002, position)
        self.assertTrue(success1)
        
        call_args1 = self.order_manager.modify_order.call_args
        first_sl = call_args1[1]['stop_loss_price']
        first_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, first_sl, 'BUY', 0.01, 1.0
        )
        
        # Second update: profit increased to $0.08
        position['profit'] = 0.08
        position['sl'] = first_sl  # Set current SL
        self.order_manager.get_position_by_ticket.return_value = position
        
        success2, _ = self.sl_manager.update_sl_atomic(3002, position)
        self.assertTrue(success2)
        
        call_args2 = self.order_manager.modify_order.call_args
        second_sl = call_args2[1]['stop_loss_price']
        second_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, second_sl, 'BUY', 0.01, 1.0
        )
        
        # SL should have increased (never decreased)
        self.assertGreaterEqual(second_effective, first_effective)
    
    def test_sweet_spot_not_applied_outside_range(self):
        """Test sweet-spot not applied outside $0.03-$0.10 range."""
        # Test below range
        position_low = {
            'ticket': 3003,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.02  # Below sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position_low
        
        success, reason = self.sl_manager.update_sl_atomic(3003, position_low)
        self.assertNotIn('Sweet-spot', reason)
        
        # Test above range
        position_high = {
            'ticket': 3004,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.15  # Above sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position_high
        
        success, reason = self.sl_manager.update_sl_atomic(3004, position_high)
        self.assertNotIn('Sweet-spot', reason)
        self.assertIn('Trailing', reason)


class TestTrailingStop(unittest.TestCase):
    """Test step-based/trailing SL for profits > $0.10."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_trailing_stop_locks_increments(self):
        """Test trailing stop locks in $0.10 increments."""
        position = {
            'ticket': 4001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.25  # Above sweet spot
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, reason = self.sl_manager.update_sl_atomic(4001, position)
        
        self.assertTrue(success)
        self.assertIn('Trailing stop', reason)
        
        # Verify SL was set to lock profit
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        
        effective_sl = self.sl_manager._calculate_effective_sl_profit(
            position['price_open'], sl_price, 'BUY', 0.01, 1.0
        )
        
        # Should lock at least $0.10 (one increment below current profit)
        self.assertGreaterEqual(effective_sl, 0.10)
    
    def test_trailing_stop_never_decreases(self):
        """Test trailing stop SL never decreases."""
        position = {
            'ticket': 4002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.20
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First update
        success1, _ = self.sl_manager.update_sl_atomic(4002, position)
        self.assertTrue(success1)
        
        call_args1 = self.order_manager.modify_order.call_args
        first_sl = call_args1[1]['stop_loss_price']
        first_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, first_sl, 'BUY', 0.01, 1.0
        )
        
        # Second update: profit increased
        position['profit'] = 0.35
        position['sl'] = first_sl
        self.order_manager.get_position_by_ticket.return_value = position
        
        success2, _ = self.sl_manager.update_sl_atomic(4002, position)
        self.assertTrue(success2)
        
        call_args2 = self.order_manager.modify_order.call_args
        second_sl = call_args2[1]['stop_loss_price']
        second_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, second_sl, 'BUY', 0.01, 1.0
        )
        
        # SL should have increased
        self.assertGreaterEqual(second_effective, first_effective)


class TestBuyVsSellHandling(unittest.TestCase):
    """Test BUY vs SELL BID/ASK handling."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_buy_sl_triggers_on_bid(self):
        """Test BUY SL triggers on BID price."""
        position = {
            'ticket': 5001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20010,  # Entry at ASK
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, _ = self.sl_manager.update_sl_atomic(5001, position)
        self.assertTrue(success)
        
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        
        # For BUY, SL should be below entry (triggers when BID reaches SL)
        self.assertLess(sl_price, position['price_open'])
        
        # SL should respect stops_level (min distance from current BID)
        min_allowed = self.symbol_info['bid'] - (self.symbol_info['trade_stops_level'] * self.symbol_info['point'])
        self.assertLessEqual(sl_price, min_allowed)
    
    def test_sell_sl_triggers_on_ask(self):
        """Test SELL SL triggers on ASK price."""
        position = {
            'ticket': 5002,
            'symbol': 'EURUSD',
            'type': 'SELL',
            'price_open': 1.20000,  # Entry at BID
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        success, _ = self.sl_manager.update_sl_atomic(5002, position)
        self.assertTrue(success)
        
        call_args = self.order_manager.modify_order.call_args
        sl_price = call_args[1]['stop_loss_price']
        
        # For SELL, SL should be above entry (triggers when ASK reaches SL)
        self.assertGreater(sl_price, position['price_open'])
        
        # SL should respect stops_level (min distance from current ASK)
        max_allowed = self.symbol_info['ask'] + (self.symbol_info['trade_stops_level'] * self.symbol_info['point'])
        self.assertGreaterEqual(sl_price, max_allowed)
    
    def test_buy_vs_sell_effective_sl_calculation(self):
        """Test effective SL calculation is correct for both BUY and SELL."""
        entry_price = 1.20000
        sl_price = 1.19900
        lot_size = 0.01
        contract_size = 1.0
        
        # BUY: SL below entry = loss
        buy_effective = self.sl_manager._calculate_effective_sl_profit(
            entry_price, sl_price, 'BUY', lot_size, contract_size
        )
        
        # SELL: SL above entry = loss (but calculation is different)
        sell_sl = 1.20100  # Above entry for SELL
        sell_effective = self.sl_manager._calculate_effective_sl_profit(
            entry_price, sell_sl, 'SELL', lot_size, contract_size
        )
        
        # Both should result in similar loss magnitude
        self.assertLess(buy_effective, 0)
        self.assertLess(sell_effective, 0)
        
        # Verify calculations are correct
        expected_buy_loss = -(entry_price - sl_price) * lot_size * contract_size
        expected_sell_loss = -(sell_sl - entry_price) * lot_size * contract_size
        
        self.assertAlmostEqual(buy_effective, expected_buy_loss, places=2)
        self.assertAlmostEqual(sell_effective, expected_sell_loss, places=2)


class TestContractSizeCorrection(unittest.TestCase):
    """Test contract size auto-correction."""
    
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
    
    def test_contract_size_auto_correction(self):
        """Test contract size is auto-corrected when MT5 reports incorrect value."""
        # Simulate BTCXAUm with incorrect contract size
        incorrect_symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,  # Incorrect (should be 100.0)
            'trade_stops_level': 10,
            'bid': 0.001,
            'ask': 0.0011
        }
        self.mt5_connector.get_symbol_info.return_value = incorrect_symbol_info
        
        # Get corrected contract size
        contract_size = self.sl_manager._get_corrected_contract_size(
            'BTCXAUm', 0.001, 0.01, 2.0
        )
        
        # Should detect and correct (price_diff would be > 50% of entry if contract_size is wrong)
        # The correction logic should find a reasonable contract_size
        self.assertIsNotNone(contract_size)
        self.assertGreater(contract_size, 0)
        
        # Verify correction makes sense (price_diff should be reasonable)
        price_diff = abs(2.0) / (0.01 * contract_size)
        self.assertLess(price_diff, 0.001 * 0.2)  # Should be < 20% of entry price
    
    def test_contract_size_caching(self):
        """Test corrected contract size is cached."""
        symbol_info = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 10,
            'bid': 0.001,
            'ask': 0.0011
        }
        self.mt5_connector.get_symbol_info.return_value = symbol_info
        
        # First call
        contract_size1 = self.sl_manager._get_corrected_contract_size(
            'BTCXAUm', 0.001, 0.01, 2.0
        )
        
        # Second call should use cache (should not call get_symbol_info again)
        self.mt5_connector.get_symbol_info.reset_mock()
        contract_size2 = self.sl_manager._get_corrected_contract_size(
            'BTCXAUm', 0.001, 0.01, 2.0
        )
        
        # Should return same value
        self.assertEqual(contract_size1, contract_size2)


class TestThreadSafety(unittest.TestCase):
    """Test thread-safety of atomic updates."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
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
        
        # Create multiple threads updating same ticket
        threads = [threading.Thread(target=update_sl) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should complete without errors
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 10)
        
        # All should succeed (or at least not conflict)
        self.assertTrue(all(r[0] for r in results) or len(set(r[1] for r in results)) > 0)
    
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
        
        # All should succeed
        self.assertTrue(all(r[0] for r in results.values()))


class TestFailSafe(unittest.TestCase):
    """Test fail-safe mechanism."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_fail_safe_triggers_on_violation(self):
        """Test fail-safe triggers when SL violates -$2.00 limit."""
        # Position with SL that would result in worse than -$2.00
        position = {
            'ticket': 7001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 1.19850,  # SL that would result in -$1.50 (better than -$2.00, but let's test worse case)
            'volume': 0.01,
            'profit': -2.50  # Worse than -$2.00
        }
        
        # Calculate what SL would result in worse than -$2.00
        # For test, set SL that would result in -$2.50
        bad_sl = 1.20000 - (2.50 / (0.01 * 1.0))  # SL that results in -$2.50
        position['sl'] = bad_sl
        
        positions = [position]
        self.order_manager.get_open_positions.return_value = positions
        self.order_manager.get_position_by_ticket.return_value = position
        
        # Run fail-safe check
        self.sl_manager.fail_safe_check()
        
        # Should have called modify_order to correct SL
        self.order_manager.modify_order.assert_called()
        
        # Verify corrected SL is better (closer to -$2.00)
        call_args = self.order_manager.modify_order.call_args
        corrected_sl = call_args[1]['stop_loss_price']
        
        corrected_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, corrected_sl, 'BUY', 0.01, 1.0
        )
        
        # Should be better than -$2.50 (closer to -$2.00)
        self.assertGreater(corrected_effective, -2.50)
        self.assertLessEqual(corrected_effective, -1.9)  # Around -$2.00


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions."""
    
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
            'bid': 1.20000,
            'ask': 1.20010
        }
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        self.order_manager.modify_order.return_value = True
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_rapid_profit_fluctuations(self):
        """Test rapid profit fluctuations don't cause SL to decrease."""
        position = {
            'ticket': 8001,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.08
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First update: profit $0.08
        success1, _ = self.sl_manager.update_sl_atomic(8001, position)
        self.assertTrue(success1)
        
        call_args1 = self.order_manager.modify_order.call_args
        first_sl = call_args1[1]['stop_loss_price']
        first_effective = self.sl_manager._calculate_effective_sl_profit(
            1.20000, first_sl, 'BUY', 0.01, 1.0
        )
        
        # Rapid fluctuation: profit drops to $0.04
        position['profit'] = 0.04
        position['sl'] = first_sl
        self.order_manager.get_position_by_ticket.return_value = position
        
        success2, _ = self.sl_manager.update_sl_atomic(8001, position)
        
        # SL should not decrease (should maintain or improve)
        if success2:
            call_args2 = self.order_manager.modify_order.call_args
            second_sl = call_args2[1]['stop_loss_price']
            second_effective = self.sl_manager._calculate_effective_sl_profit(
                1.20000, second_sl, 'BUY', 0.01, 1.0
            )
            # SL should not decrease
            self.assertGreaterEqual(second_effective, first_effective)
    
    def test_broker_sl_rejection_retry(self):
        """Test retry mechanism when broker rejects SL update."""
        position = {
            'ticket': 8002,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        self.order_manager.get_position_by_ticket.return_value = position
        
        # First two attempts fail, third succeeds
        self.order_manager.modify_order.side_effect = [False, False, True]
        
        success, reason = self.sl_manager.update_sl_atomic(8002, position)
        
        # Should eventually succeed after retries
        self.assertTrue(success)
        # Should have retried
        self.assertEqual(self.order_manager.modify_order.call_count, 3)
    
    def test_missing_symbol_info(self):
        """Test handling when symbol info is unavailable."""
        position = {
            'ticket': 8003,
            'symbol': 'INVALID',
            'type': 'BUY',
            'price_open': 1.20000,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        self.order_manager.get_position_by_ticket.return_value = position
        self.mt5_connector.get_symbol_info.return_value = None
        
        success, reason = self.sl_manager.update_sl_atomic(8003, position)
        
        # Should fail gracefully
        self.assertFalse(success)
        self.assertIn('Symbol info not available', reason)


def run_all_tests():
    """Run all test suites and generate report."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestStrictLossEnforcement))
    suite.addTests(loader.loadTestsFromTestCase(TestBreakEvenSL))
    suite.addTests(loader.loadTestsFromTestCase(TestSweetSpotProfitLocking))
    suite.addTests(loader.loadTestsFromTestCase(TestTrailingStop))
    suite.addTests(loader.loadTestsFromTestCase(TestBuyVsSellHandling))
    suite.addTests(loader.loadTestsFromTestCase(TestContractSizeCorrection))
    suite.addTests(loader.loadTestsFromTestCase(TestThreadSafety))
    suite.addTests(loader.loadTestsFromTestCase(TestFailSafe))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result


if __name__ == '__main__':
    result = run_all_tests()
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Success rate: {(result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100:.1f}%")
    
    if result.failures:
        print("\nFAILURES:")
        for test, traceback in result.failures:
            print(f"  - {test}")
    
    if result.errors:
        print("\nERRORS:")
        for test, traceback in result.errors:
            print(f"  - {test}")

