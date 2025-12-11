"""Debug script to understand why SL updates aren't triggering."""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from unittest.mock import Mock, MagicMock
from risk.sl_manager import SLManager

# Create test setup
config = {
    'risk': {
        'max_risk_per_trade_usd': 2.0,
    },
    'execution': {
        'global_rpc_max_per_second': 50,
        'verification': {
            'effective_profit_tolerance_usd': 1.0,
            'price_tolerance_multiplier': 1.0,
            'use_exponential_backoff': True
        }
    }
}

mt5_connector = Mock()
order_manager = Mock()

symbol_info = {
    'name': 'EURUSD',
    'point': 0.00001,
    'digits': 5,
    'contract_size': 100000.0,
    'trade_stops_level': 10,
    'trade_tick_value': None
}
mt5_connector.get_symbol_info.return_value = symbol_info

tick_mock = MagicMock()
tick_mock.bid = 1.19750
tick_mock.ask = 1.19760
mt5_connector.get_symbol_info_tick.return_value = tick_mock

# Position with no SL - should trigger strict loss enforcement
position = {
    'ticket': 1001,
    'symbol': 'EURUSD',
    'type': 'BUY',
    'price_open': 1.20000,
    'price_current': 1.19750,
    'sl': 0.0,  # No SL set
    'volume': 0.01,
    'profit': -2.50  # Worse than -$2.00
}

order_manager.get_position_by_ticket.return_value = position
order_manager.modify_order.return_value = True

sl_manager = SLManager(config, mt5_connector, order_manager)

print("Testing strict loss enforcement...")
print(f"Position: Ticket {position['ticket']}, Profit: ${position['profit']:.2f}, SL: {position['sl']}")

# Test _enforce_strict_loss_limit directly with detailed logging
print("\n=== Testing _enforce_strict_loss_limit ===")
print(f"Position profit: ${position['profit']:.2f}")
print(f"Current SL: {position['sl']}")
print(f"Max risk: ${sl_manager.max_risk_usd:.2f}")

# Check if it thinks SL is already correct
current_effective_sl = sl_manager.get_effective_sl_profit(position)
print(f"Current effective SL profit: ${current_effective_sl:.2f}")
print(f"Target effective SL profit: ${-sl_manager.max_risk_usd:.2f}")

# Mock get_position_by_ticket properly
position_after = position.copy()
position_after['sl'] = 1.19800  # Will be set after modification

call_count = {'value': 0}
def get_position_mock(ticket):
    call_count['value'] += 1
    if call_count['value'] <= 2:
        return position
    else:
        return position_after

order_manager.get_position_by_ticket.side_effect = get_position_mock
order_manager.modify_order.return_value = True

# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

success, reason, target_sl = sl_manager._enforce_strict_loss_limit(position)
print(f"\n_enforce_strict_loss_limit result:")
print(f"  Success: {success}")
print(f"  Reason: {reason}")
print(f"  Target SL: {target_sl}")
print(f"  modify_order call count: {order_manager.modify_order.call_count if hasattr(order_manager.modify_order, 'call_count') else 'N/A'}")
print(f"  get_position_by_ticket call count: {call_count['value']}")

# Check what _calculate_target_sl_price returns
print("\n=== Testing _calculate_target_sl_price ===")
try:
    symbol_info = mt5_connector.get_symbol_info('EURUSD')
    target_sl_calc = sl_manager._calculate_target_sl_price(
        position['price_open'],
        -sl_manager.max_risk_usd,
        position['type'],
        position['volume'],
        symbol_info,
        position=position
    )
    print(f"Calculated target SL: {target_sl_calc:.5f}")
except Exception as e:
    print(f"ERROR calculating target SL: {e}")
    import traceback
    traceback.print_exc()

# Test _apply_sl_update directly if we have a target
if target_sl:
    print(f"\n=== Testing _apply_sl_update with target SL {target_sl:.5f} ===")
    print(f"Position before: SL={position['sl']}, Profit=${position['profit']:.2f}")
    
    # Mock get_position_by_ticket to return position with updated SL after modification
    position_after = position.copy()
    position_after['sl'] = target_sl
    
    call_count = {'value': 0}
    def get_position_mock(ticket):
        call_count['value'] += 1
        if call_count['value'] <= 2:
            return position
        else:
            return position_after
    
    order_manager.get_position_by_ticket.side_effect = get_position_mock
    order_manager.modify_order.return_value = True
    
    apply_success = sl_manager._apply_sl_update(
        position['ticket'],
        position['symbol'],
        target_sl,
        -sl_manager.max_risk_usd,
        "Test strict loss enforcement",
        position=position
    )
    print(f"_apply_sl_update result: {apply_success}")
    print(f"modify_order call count: {order_manager.modify_order.call_count}")

# Test update_sl_atomic
print(f"\n=== Testing update_sl_atomic ===")
success, reason = sl_manager.update_sl_atomic(position['ticket'], position)
print(f"  Success: {success}")
print(f"  Reason: {reason}")

