"""
Test staged open logic for multi-trade support.
Simulates opening staged trades and verifies staging rules.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock
from datetime import datetime, timedelta
from risk.risk_manager import RiskManager


class MockOrderManager:
    """Mock order manager for testing."""
    
    def __init__(self):
        self.positions = {}
    
    def get_position_by_ticket(self, ticket):
        return self.positions.get(ticket)
    
    def get_open_positions(self):
        return list(self.positions.values())
    
    def get_position_count(self):
        return len(self.positions)


class MockMT5Connector:
    """Mock MT5 connector for testing."""
    
    def get_symbol_info(self, symbol):
        return {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 1.0,
            'trade_stops_level': 0
        }
    
    def ensure_connected(self):
        return True


def test_staged_open_basic():
    """Test basic staged open logic."""
    print("\n" + "=" * 80)
    print("TEST: Staged Open - Basic Logic")
    print("=" * 80)
    
    # Setup
    config = {
        'risk': {
            'max_open_trades': 2,
            'staged_open_enabled': True,
            'staged_open_window_seconds': 60,
            'staged_quality_threshold': 50.0,
            'staged_min_profit_usd': -0.10
        }
    }
    
    mt5_connector = MockMT5Connector()
    order_manager = MockOrderManager()
    risk_manager = RiskManager(config, mt5_connector, order_manager)
    
    symbol = 'EURUSD'
    signal = 'LONG'
    quality_score = 75.0
    
    # Test 1: First trade should be allowed
    print("\nTest 1: First trade")
    can_open, reason = risk_manager.can_open_trade(symbol=symbol, signal=signal, quality_score=quality_score)
    assert can_open, f"First trade should be allowed, got: {reason}"
    print(f"✅ First trade allowed: {reason}")
    
    # Simulate opening first trade
    ticket1 = 1001
    order_manager.positions[ticket1] = {
        'ticket': ticket1,
        'symbol': symbol,
        'type': 'BUY',
        'profit': 0.05  # Small profit
    }
    risk_manager.register_staged_trade(symbol, ticket1, signal)
    
    # Test 2: Second trade within window should be allowed
    print("\nTest 2: Second trade within window")
    can_open, reason = risk_manager.can_open_trade(symbol=symbol, signal=signal, quality_score=quality_score)
    assert can_open, f"Second trade should be allowed within window, got: {reason}"
    print(f"✅ Second trade allowed: {reason}")
    
    # Simulate opening second trade
    ticket2 = 1002
    order_manager.positions[ticket2] = {
        'ticket': ticket2,
        'symbol': symbol,
        'type': 'BUY',
        'profit': 0.03
    }
    risk_manager.register_staged_trade(symbol, ticket2, signal)
    
    # Test 3: Third trade should be blocked (max_open_trades = 2)
    print("\nTest 3: Third trade (should be blocked)")
    can_open, reason = risk_manager.can_open_trade(symbol=symbol, signal=signal, quality_score=quality_score)
    assert not can_open, f"Third trade should be blocked, got: {reason}"
    print(f"✅ Third trade blocked: {reason}")
    
    # Test 4: Different trend should be blocked
    print("\nTest 4: Different trend (should be blocked)")
    can_open, reason = risk_manager.can_open_trade(symbol=symbol, signal='SHORT', quality_score=quality_score)
    assert not can_open, f"Different trend should be blocked, got: {reason}"
    print(f"✅ Different trend blocked: {reason}")
    
    # Test 5: Low quality score should be blocked
    print("\nTest 5: Low quality score (should be blocked)")
    can_open, reason = risk_manager.can_open_trade(symbol=symbol, signal=signal, quality_score=30.0)
    assert not can_open, f"Low quality should be blocked, got: {reason}"
    print(f"✅ Low quality blocked: {reason}")
    
    # Test 6: First trade with loss below threshold should block second
    print("\nTest 6: First trade with loss below threshold")
    order_manager.positions[ticket1]['profit'] = -0.15  # Below -0.10 threshold
    can_open, reason = risk_manager.can_open_trade(symbol=symbol, signal=signal, quality_score=quality_score)
    assert not can_open, f"Second trade should be blocked if first trade loss < threshold, got: {reason}"
    print(f"✅ Blocked due to first trade loss: {reason}")
    
    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)
    return True


def test_staged_open_window_expiry():
    """Test staged open window expiry."""
    print("\n" + "=" * 80)
    print("TEST: Staged Open - Window Expiry")
    print("=" * 80)
    
    # Setup with max_open_trades = 1 to force staged open check
    config = {
        'risk': {
            'max_open_trades': 1,
            'staged_open_enabled': True,
            'staged_open_window_seconds': 60,
            'staged_quality_threshold': 50.0,
            'staged_min_profit_usd': -0.10
        }
    }
    
    mt5_connector = MockMT5Connector()
    order_manager = MockOrderManager()
    risk_manager = RiskManager(config, mt5_connector, order_manager)
    
    symbol = 'EURUSD'
    signal = 'LONG'
    
    # Open first trade (this fills max_open_trades = 1)
    ticket1 = 2001
    order_manager.positions[ticket1] = {
        'ticket': ticket1,
        'symbol': symbol,
        'type': 'BUY',
        'profit': 0.05
    }
    risk_manager.register_staged_trade(symbol, ticket1, signal)
    
    # Manually expire the window by setting first_trade_time to past
    with risk_manager._staged_lock:
        if symbol in risk_manager._staged_trades:
            risk_manager._staged_trades[symbol]['first_trade_time'] = datetime.now() - timedelta(seconds=61)
    
    # Try to open second trade (should be blocked due to expired window)
    print("\nTest: Second trade after window expiry")
    can_open, reason = risk_manager.can_open_trade(symbol=symbol, signal=signal, quality_score=75.0)
    assert not can_open, f"Second trade should be blocked after window expiry, got: {reason}"
    print(f"✅ Window expiry detected: {reason}")
    
    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)
    return True


if __name__ == '__main__':
    try:
        test_staged_open_basic()
        test_staged_open_window_expiry()
        print("\n✅ All tests completed successfully!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

