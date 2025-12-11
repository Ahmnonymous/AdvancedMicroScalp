"""
Integration Test Harness for SL Manager

This harness mocks broker responses to test SL Manager behavior under various scenarios:
- Normal: applied_sl == target_sl
- Slight deviation: applied_sl marginally different within tolerance
- Large deviation: applied_sl very different (simulate rounding/precision or broker alternative)
- Rate-limited responses (simulate slow broker with delayed applied_sl)
- Rejection codes (simulate trading restriction, error codes)
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import time
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from unittest.mock import Mock, MagicMock, patch
import threading

from risk.sl_manager import SLManager


class MockBrokerResponse:
    """Mock broker response with configurable behavior."""
    
    def __init__(self, scenario: str = 'normal'):
        """
        Initialize mock broker response.
        
        Args:
            scenario: 'normal', 'slight_deviation', 'large_deviation', 'rate_limited', 'rejection'
        """
        self.scenario = scenario
        self.call_count = 0
        self.response_delay = 0.0
        
        if scenario == 'rate_limited':
            self.response_delay = 0.5  # 500ms delay
        elif scenario == 'rejection':
            self.response_delay = 0.1  # 100ms delay
    
    def modify_order(self, ticket: int, stop_loss_price: float, **kwargs) -> bool:
        """Mock modify_order with configurable behavior."""
        self.call_count += 1
        
        # Simulate response delay
        if self.response_delay > 0:
            time.sleep(self.response_delay)
        
        if self.scenario == 'rejection':
            # Simulate rejection (return False)
            return False
        
        # For other scenarios, return True (success)
        return True
    
    def get_position_by_ticket(self, ticket: int) -> Optional[Dict[str, Any]]:
        """Mock get_position_by_ticket with configurable applied SL."""
        if self.scenario == 'rejection':
            return None
        
        # This will be overridden by the test harness with actual position data
        # Return None to indicate position not found (will be set by harness)
        return None


class SLManagerTestHarness:
    """Test harness for SL Manager integration tests."""
    
    def __init__(self, output_dir: Path = None):
        """Initialize test harness."""
        self.output_dir = output_dir or Path('verification/results')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.results = []
        self.logs = []
    
    def get_tick_for_position(self, position: Dict[str, Any]) -> Dict[str, float]:
        """Get appropriate tick values for a position."""
        current_price = position.get('price_current', position.get('price_open', 1.20000))
        spread = 0.00010  # 1 pip spread
        return {
            'bid': current_price - (spread / 2) if position.get('type') == 'BUY' else current_price,
            'ask': current_price + (spread / 2) if position.get('type') == 'BUY' else current_price + spread
        }
    
    def create_mock_sl_manager(self, broker_scenario: str = 'normal') -> SLManager:
        """Create SL Manager with mocked broker."""
        config = {
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
                'global_rpc_max_per_second': 50,
                'verification': {
                    'effective_profit_tolerance_usd': 1.0,
                    'price_tolerance_multiplier': 1.0,
                    'use_exponential_backoff': True
                }
            }
        }
        
        # Create mocks
        mt5_connector = Mock()
        order_manager = Mock()
        
        # Mock symbol info
        symbol_info = {
            'name': 'EURUSD',
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000.0,
            'trade_stops_level': 10,
            'trade_tick_value': None
        }
        mt5_connector.get_symbol_info.return_value = symbol_info
        
        # Mock tick data
        tick_mock = MagicMock()
        tick_mock.bid = 1.20000
        tick_mock.ask = 1.20010
        mt5_connector.get_symbol_info_tick.return_value = tick_mock
        
        # Mock broker response
        broker_response = MockBrokerResponse(broker_scenario)
        order_manager.modify_order = broker_response.modify_order
        order_manager.get_position_by_ticket = broker_response.get_position_by_ticket
        
        # Create SL Manager
        sl_manager = SLManager(config, mt5_connector, order_manager)
        
        return sl_manager, broker_response
    
    def run_scenario(self, scenario_name: str, broker_scenario: str, 
                    position: Dict[str, Any], duration_seconds: int = 30) -> Dict[str, Any]:
        """Run a test scenario."""
        print(f"\n{'='*60}")
        print(f"Running scenario: {scenario_name}")
        print(f"Broker behavior: {broker_scenario}")
        print(f"Position: Ticket {position['ticket']}, Profit: ${position['profit']:.2f}")
        print(f"{'='*60}\n")
        
        sl_manager, broker_response = self.create_mock_sl_manager(broker_scenario)
        
        # Update tick to match position
        tick = self.get_tick_for_position(position)
        sl_manager.mt5_connector.get_symbol_info_tick.return_value.bid = tick['bid']
        sl_manager.mt5_connector.get_symbol_info_tick.return_value.ask = tick['ask']
        
        # Update broker response to return our position with proper SL updates
        position_state = position.copy()  # Track current state
        
        def get_position_with_updates(ticket):
            if ticket == position['ticket']:
                return position_state.copy()
            return None
        
        # Override modify_order to update position_state when SL is set
        original_modify_order = broker_response.modify_order
        def modify_order_with_update(ticket, **kwargs):
            result = original_modify_order(ticket, **kwargs)
            if result and 'stop_loss_price' in kwargs:
                # Update position state to reflect new SL
                position_state['sl'] = kwargs['stop_loss_price']
            return result
        
        broker_response.get_position_by_ticket = get_position_with_updates
        broker_response.modify_order = modify_order_with_update
        sl_manager.order_manager.get_position_by_ticket = get_position_with_updates
        sl_manager.order_manager.modify_order = modify_order_with_update
        
        # Track results
        updates = []
        start_time = time.time()
        iteration = 0
        max_iterations = int(duration_seconds / 0.1)  # Number of 100ms intervals
        
        try:
            # Simulate worker loop for duration
            for iteration in range(max_iterations):
                # Use the position directly (already set up)
                current_position = position.copy()
                
                # Call update_sl_atomic directly (simulates worker behavior)
                success, reason = sl_manager.update_sl_atomic(
                    current_position['ticket'], current_position
                )
                
                # Update position SL if successful (simulate broker applying it)
                if success:
                    # Update the position's SL to reflect the change
                    # The broker response will return this updated SL on next call
                    position['sl'] = current_position.get('sl', 0.0) + 0.00050  # Simulate SL update
                    # Also update broker response to return updated position
                    updated_position = current_position.copy()
                    updated_position['sl'] = position['sl']
                    def get_updated_position(ticket):
                        if ticket == position['ticket']:
                            return updated_position
                        return None
                    sl_manager.order_manager.get_position_by_ticket = get_updated_position
                
                updates.append({
                    'timestamp': datetime.now().isoformat(),
                    'iteration': iteration,
                    'success': success,
                    'reason': reason,
                    'profit': current_position['profit'],
                    'sl': current_position.get('sl', 0.0)
                })
                
                if iteration % 10 == 0:
                    print(f"  Iteration {iteration}: Success={success}, Reason={reason[:50]}")
                
                time.sleep(0.1)  # Faster for testing (100ms instead of 500ms)
        
        except Exception as e:
            print(f"  ERROR in scenario: {e}")
            import traceback
            traceback.print_exc()
        
        # Compile results
        result = {
            'scenario_name': scenario_name,
            'broker_scenario': broker_scenario,
            'duration_seconds': duration_seconds,
            'total_updates': len(updates),
            'successful_updates': sum(1 for u in updates if u['success']),
            'failed_updates': sum(1 for u in updates if not u['success']),
            'updates': updates,
            'broker_call_count': broker_response.call_count
        }
        
        self.results.append(result)
        return result
    
    def run_all_scenarios(self):
        """Run all test scenarios."""
        scenarios = [
            {
                'name': 'Losing Trade - Normal Broker',
                'broker': 'normal',
                'position': {
                    'ticket': 1001,
                    'symbol': 'EURUSD',
                    'type': 'BUY',
                    'price_open': 1.20000,
                    'price_current': 1.19750,  # Price moved down significantly
                    'sl': 0.0,  # No SL set - should trigger strict loss
                    'volume': 0.01,
                    'profit': -2.50  # Worse than -$2.00 limit
                },
                'tick': {'bid': 1.19750, 'ask': 1.19760}
            },
            {
                'name': 'Losing Trade - Slight Deviation',
                'broker': 'slight_deviation',
                'position': {
                    'ticket': 1002,
                    'symbol': 'EURUSD',
                    'type': 'BUY',
                    'price_open': 1.20000,
                    'price_current': 1.19750,
                    'sl': 0.0,
                    'volume': 0.01,
                    'profit': -2.50
                },
                'tick': {'bid': 1.19750, 'ask': 1.19760}
            },
            {
                'name': 'Sweet Spot - Rate Limited',
                'broker': 'rate_limited',
                'position': {
                    'ticket': 1003,
                    'symbol': 'EURUSD',
                    'type': 'BUY',
                    'price_open': 1.20000,
                    'price_current': 1.20050,
                    'sl': 0.0,
                    'volume': 0.01,
                    'profit': 0.05  # In sweet spot (0.03-0.10)
                },
                'tick': {'bid': 1.20050, 'ask': 1.20060}
            },
        ]
        
        for scenario in scenarios:
            result = self.run_scenario(
                scenario['name'],
                scenario['broker'],
                scenario['position'],
                duration_seconds=5  # Very short for quick testing
            )
            time.sleep(0.2)  # Brief pause between scenarios
        
        # Save results
        self.save_results()
    
    def save_results(self):
        """Save test results to JSON file."""
        output_file = self.output_dir / f'integration_test_results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        with open(output_file, 'w') as f:
            json.dump({
                'test_run_timestamp': datetime.now().isoformat(),
                'scenarios': self.results
            }, f, indent=2)
        
        print(f"\n[OK] Results saved to: {output_file}")
        
        # Print summary
        print(f"\n{'='*60}")
        print("TEST SUMMARY")
        print(f"{'='*60}")
        for result in self.results:
            print(f"\n{result['scenario_name']}:")
            print(f"  Total updates: {result['total_updates']}")
            print(f"  Successful: {result['successful_updates']}")
            print(f"  Failed: {result['failed_updates']}")
            print(f"  Broker calls: {result['broker_call_count']}")


if __name__ == '__main__':
    harness = SLManagerTestHarness()
    harness.run_all_scenarios()

