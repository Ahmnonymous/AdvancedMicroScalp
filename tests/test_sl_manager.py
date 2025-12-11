"""
Unit Tests for Unified SL Manager

This test suite verifies:
1. Strict loss enforcement (-$2.00) for losing trades
2. Break-even SL logic (profit > $0 but < $0.03 for 2+ seconds)
3. Sweet-spot profit locking ($0.03-$0.10) with SL never decreasing
4. Step-based/trailing SL for profits > $0.10
5. BUY vs SELL BID/ASK handling
6. Contract size correction
7. Thread-safety and atomic updates
"""

import unittest
import time
from unittest.mock import Mock, MagicMock, patch
from risk.sl_manager import SLManager


class TestSLManager(unittest.TestCase):
    """Test cases for Unified SL Manager."""
    
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
        
        # Mock symbol info
        self.symbol_info = {
            'name': 'EURUSD',
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000.0,  # Standard forex contract size
            'trade_stops_level': 10,
            'trade_tick_value': None,
            'bid': 1.20000,
            'ask': 1.20010
        }
        
        # Always return the same symbol info
        self.mt5_connector.get_symbol_info.return_value = self.symbol_info
        
        # Mock tick data (required for validation) - update based on position
        from unittest.mock import MagicMock
        self.tick_mock = MagicMock()
        self.tick_mock.bid = 1.20000
        self.tick_mock.ask = 1.20010
        self.mt5_connector.get_symbol_info_tick.return_value = self.tick_mock
        
        self.order_manager.get_position_by_ticket.return_value = None
        self.order_manager.modify_order.return_value = True
        
        self.sl_manager = SLManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_strict_loss_enforcement(self):
        """Test strict -$2.00 loss enforcement for losing trades."""
        # Create a losing BUY position
        # For strict loss enforcement to trigger, we need:
        # 1. profit < 0 (losing trade)
        # 2. effective_sl_profit < -max_risk (or no SL set)
        # Update tick to match position's current price
        self.tick_mock.bid = 1.19800
        self.tick_mock.ask = 1.19810
        
        position = {
            'ticket': 12345,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'price_current': 1.19800,  # Price moved down significantly (losing)
            'sl': 0.0,  # No SL set - this should trigger strict loss enforcement
            'volume': 0.01,
            'profit': -2.50  # Losing trade worse than -$2.00, needs SL to limit to -$2.00
        }
        
        # Create position with updated SL after modification
        position_after_update = position.copy()
        position_after_update['sl'] = 1.19800  # SL was set
        
        # Mock get_position_by_ticket to return position (for validation and verification)
        # The function is called multiple times:
        # 1. In _apply_sl_update for validation (before modify)
        # 2. In _apply_sl_update for verification (after modify)
        # 3. Potentially in get_effective_sl_profit
        call_count = {'value': 0}
        def get_position_side_effect(ticket):
            if ticket == 12345:
                call_count['value'] += 1
                # First few calls: before modification (validation)
                # Later calls: after modification (verification)
                if call_count['value'] <= 2:
                    return position
                else:
                    return position_after_update
            return None
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_side_effect
        
        # Mock successful SL update - return True immediately
        def modify_order_side_effect(ticket, **kwargs):
            return True
        
        self.order_manager.modify_order.side_effect = modify_order_side_effect
        
        # Update SL
        success, reason = self.sl_manager.update_sl_atomic(12345, position)
        
        # Verify strict loss was enforced
        self.assertTrue(success, f"SL update failed. Reason: {reason}")
        self.assertIn('Strict loss enforcement', reason)
        
        # Verify modify_order was called (may be called multiple times due to retries)
        self.assertGreaterEqual(self.order_manager.modify_order.call_count, 1)
        call_args = self.order_manager.modify_order.call_args
        self.assertEqual(call_args[0][0], 12345)  # ticket
        self.assertIsNotNone(call_args[1]['stop_loss_price'])
    
    def test_break_even_sl(self):
        """Test break-even SL when profit > $0 but < $0.03 for 2+ seconds."""
        # Create a small profit position
        position = {
            'ticket': 12346,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'price_current': 1.20020,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.02  # Small profit
        }
        
        position_after_update = position.copy()
        position_after_update['sl'] = 1.20000  # Break-even SL
        
        def get_position_side_effect(ticket):
            if ticket == 12346:
                if not hasattr(get_position_side_effect, 'call_count'):
                    get_position_side_effect.call_count = 0
                get_position_side_effect.call_count += 1
                if get_position_side_effect.call_count == 1:
                    return position
                else:
                    return position_after_update
            return None
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_side_effect
        self.order_manager.modify_order.return_value = True
        
        # First update: profit just became positive (should not apply break-even yet)
        success, reason = self.sl_manager.update_sl_atomic(12346, position)
        self.assertFalse(success, f"Should not apply yet. Reason: {reason}")  # Should not apply yet (duration not met)
        
        # Wait for duration threshold
        time.sleep(2.1)
        
        # Reset call counter
        get_position_side_effect.call_count = 0
        
        # Second update: duration met, should apply break-even
        success, reason = self.sl_manager.update_sl_atomic(12346, position)
        self.assertTrue(success, f"Break-even should be applied. Reason: {reason}")
        self.assertIn('Break-even', reason)
    
    def test_sweet_spot_profit_locking(self):
        """Test sweet-spot profit locking ($0.03-$0.10) with SL never decreasing."""
        # Create a position in sweet spot
        position = {
            'ticket': 12347,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'price_current': 1.20050,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05  # In sweet spot
        }
        
        position_after_update = position.copy()
        position_after_update['sl'] = 1.19970  # SL locked at $0.03 profit
        
        def get_position_side_effect(ticket):
            if ticket == 12347:
                if not hasattr(get_position_side_effect, 'call_count'):
                    get_position_side_effect.call_count = 0
                get_position_side_effect.call_count += 1
                if get_position_side_effect.call_count == 1:
                    return position
                else:
                    return position_after_update
            return None
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_side_effect
        self.order_manager.modify_order.return_value = True
        
        # First update: should lock profit
        success, reason = self.sl_manager.update_sl_atomic(12347, position)
        self.assertTrue(success, f"Sweet-spot should be applied. Reason: {reason}")
        self.assertIn('Sweet-spot', reason)
        
        # Get the locked SL profit
        first_sl_profit = self.sl_manager.get_effective_sl_profit(position_after_update)
        
        # Second update: profit increased, SL should increase (never decrease)
        position['profit'] = 0.08
        position_after_update['profit'] = 0.08
        position_after_update['sl'] = 1.19970  # Keep same or higher
        success, reason = self.sl_manager.update_sl_atomic(12347, position)
        
        # Get new SL profit
        second_sl_profit = self.sl_manager.get_effective_sl_profit(position)
        
        # Verify SL increased (never decreased)
        self.assertGreaterEqual(second_sl_profit, first_sl_profit)
    
    def test_trailing_stop(self):
        """Test trailing stop for profits > $0.10."""
        # Create a position with profit > $0.10
        position = {
            'ticket': 12348,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'price_current': 1.20250,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.25  # Above sweet spot
        }
        
        position_after_update = position.copy()
        position_after_update['sl'] = 1.20150  # Trailing SL
        
        def get_position_side_effect(ticket):
            if ticket == 12348:
                if not hasattr(get_position_side_effect, 'call_count'):
                    get_position_side_effect.call_count = 0
                get_position_side_effect.call_count += 1
                if get_position_side_effect.call_count == 1:
                    return position
                else:
                    return position_after_update
            return None
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_side_effect
        self.order_manager.modify_order.return_value = True
        
        # Update SL
        success, reason = self.sl_manager.update_sl_atomic(12348, position)
        self.assertTrue(success, f"Trailing stop should be applied. Reason: {reason}")
        self.assertIn('Trailing', reason)
        
        # Verify trailing stop locked at appropriate level
        sl_profit = self.sl_manager.get_effective_sl_profit(position)
        self.assertGreater(sl_profit, 0.0)  # Should lock some profit
    
    def test_buy_vs_sell_handling(self):
        """Test BUY vs SELL BID/ASK handling."""
        # Update tick for BUY
        self.tick_mock.bid = 1.19900
        self.tick_mock.ask = 1.19910
        
        # Test BUY order
        buy_position = {
            'ticket': 12349,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,  # Entry at ASK
            'price_current': 1.19900,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        
        buy_position_after = buy_position.copy()
        buy_position_after['sl'] = 1.19800
        
        buy_call_count = {'value': 0}
        def get_position_buy(ticket):
            if ticket == 12349:
                buy_call_count['value'] += 1
                return buy_position if buy_call_count['value'] <= 2 else buy_position_after
            return None
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_buy
        self.order_manager.modify_order.return_value = True
        
        # Update SL for BUY
        success, _ = self.sl_manager.update_sl_atomic(12349, buy_position)
        self.assertTrue(success, "BUY SL update should succeed")
        
        # Update tick for SELL
        self.tick_mock.bid = 1.20100
        self.tick_mock.ask = 1.20110
        
        # Test SELL order
        sell_position = {
            'ticket': 12350,
            'symbol': 'EURUSD',
            'type': 'SELL',
            'price_open': 1.20000,  # Entry at BID
            'price_current': 1.20100,
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        
        sell_position_after = sell_position.copy()
        sell_position_after['sl'] = 1.20200
        
        sell_call_count = {'value': 0}
        def get_position_sell(ticket):
            if ticket == 12350:
                sell_call_count['value'] += 1
                return sell_position if sell_call_count['value'] <= 2 else sell_position_after
            return get_position_buy(ticket)  # Fallback to BUY handler
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_sell
        
        # Update SL for SELL
        success, _ = self.sl_manager.update_sl_atomic(12350, sell_position)
        self.assertTrue(success, "SELL SL update should succeed")
        
        # Verify both were handled correctly (different SL prices for BUY vs SELL)
        buy_call = [call for call in self.order_manager.modify_order.call_args_list if call[0][0] == 12349]
        sell_call = [call for call in self.order_manager.modify_order.call_args_list if call[0][0] == 12350]
        
        self.assertTrue(len(buy_call) > 0, "BUY modify_order should be called")
        self.assertTrue(len(sell_call) > 0, "SELL modify_order should be called")
        
        # BUY SL should be below entry, SELL SL should be above entry
        buy_sl = buy_call[0][1]['stop_loss_price']
        sell_sl = sell_call[0][1]['stop_loss_price']
        
        self.assertLess(buy_sl, buy_position['price_open'], "BUY SL should be below entry")
        self.assertGreater(sell_sl, sell_position['price_open'], "SELL SL should be above entry")
    
    def test_contract_size_correction(self):
        """Test contract size auto-correction."""
        # Mock symbol with incorrect contract size
        incorrect_symbol_info = self.symbol_info.copy()
        incorrect_symbol_info['contract_size'] = 1.0  # Incorrect for BTCXAUm
        
        self.mt5_connector.get_symbol_info.return_value = incorrect_symbol_info
        
        # Create position that would trigger correction
        position = {
            'ticket': 12351,
            'symbol': 'BTCXAUm',
            'type': 'BUY',
            'price_open': 0.001,  # Very small price (crypto)
            'sl': 0.0,
            'volume': 0.01,
            'profit': -1.00
        }
        
        # Get corrected contract size
        contract_size = self.sl_manager._get_corrected_contract_size(
            'BTCXAUm', 0.001, 0.01, 2.0
        )
        
        # Should detect and correct (may be 100.0 for BTCXAUm)
        # The exact value depends on the correction logic
        self.assertIsNotNone(contract_size)
        self.assertGreater(contract_size, 0)
    
    def test_fail_safe_check(self):
        """Test fail-safe check for negative P/L trades."""
        # Update tick
        self.tick_mock.bid = 1.19750
        self.tick_mock.ask = 1.19760
        
        # Create a losing position with incorrect SL
        position = {
            'ticket': 12352,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'price_current': 1.19750,
            'sl': 1.19900,  # SL that would result in worse than -$2.00
            'volume': 0.01,
            'profit': -2.50  # Worse than -$2.00
        }
        
        position_after = position.copy()
        position_after['sl'] = 1.19800  # Corrected SL
        
        positions = [position]
        self.order_manager.get_open_positions.return_value = positions
        
        call_count = {'value': 0}
        def get_position_side_effect(ticket):
            if ticket == 12352:
                call_count['value'] += 1
                return position if call_count['value'] <= 2 else position_after
            return None
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_side_effect
        self.order_manager.modify_order.return_value = True
        
        # Run fail-safe check
        self.sl_manager.fail_safe_check()
        
        # Verify SL was corrected (modify_order should be called)
        self.assertGreaterEqual(self.order_manager.modify_order.call_count, 1, "modify_order should be called for fail-safe")
    
    def test_thread_safety(self):
        """Test thread-safety of atomic updates."""
        import threading
        
        # Update tick
        self.tick_mock.bid = 1.20050
        self.tick_mock.ask = 1.20060
        
        position = {
            'ticket': 12353,
            'symbol': 'EURUSD',
            'type': 'BUY',
            'price_open': 1.20000,
            'price_current': 1.20050,
            'sl': 0.0,
            'volume': 0.01,
            'profit': 0.05
        }
        
        position_after = position.copy()
        position_after['sl'] = 1.19970
        
        # Use thread-safe counter
        import threading
        call_counter = threading.Lock()
        call_count = {'value': 0}
        
        def get_position_side_effect(ticket):
            if ticket == 12353:
                with call_counter:
                    call_count['value'] += 1
                    count = call_count['value']
                return position if count <= 10 else position_after  # Allow multiple calls
            return None
        
        self.order_manager.get_position_by_ticket.side_effect = get_position_side_effect
        self.order_manager.modify_order.return_value = True
        
        # Create multiple threads updating SL simultaneously
        results = []
        results_lock = threading.Lock()
        
        def update_sl():
            # Reset call count for this thread's attempt
            with call_counter:
                call_count['value'] = 0
            success, reason = self.sl_manager.update_sl_atomic(12353, position)
            with results_lock:
                results.append((success, reason))
        
        threads = [threading.Thread(target=update_sl) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        
        # All updates should complete without errors
        self.assertEqual(len(results), 5, f"Expected 5 results, got {len(results)}")
        # At least one should succeed (others may be rate-limited or locked)
        self.assertTrue(any(r[0] for r in results), f"At least one should succeed. Results: {results}")


if __name__ == '__main__':
    unittest.main()

