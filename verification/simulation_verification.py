"""
Simulation Verification Script

Tests SL Manager across symbol categories (indices, forex, commodities, crypto, other)
with various broker response scenarios.
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import json
import time
from datetime import datetime
from typing import Dict, Any, List
from unittest.mock import Mock, MagicMock

from risk.sl_manager import SLManager


class SimulationVerification:
    """Comprehensive simulation verification for SL Manager."""
    
    def __init__(self, output_dir: Path = None):
        """Initialize verification."""
        self.output_dir = output_dir or Path('verification/data')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.results = {
            'test_run_timestamp': datetime.now().isoformat(),
            'symbols_tested': [],
            'scenarios_passed': 0,
            'scenarios_failed': 0,
            'detailed_results': []
        }
    
    def create_mock_sl_manager(self) -> SLManager:
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
        
        mt5_connector = Mock()
        order_manager = Mock()
        
        # Mock symbol info (will be customized per symbol)
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
        
        # Mock broker responses
        position_state = {}
        
        def get_position_mock(ticket):
            if ticket in position_state:
                return position_state[ticket].copy()
            return None
        
        def modify_order_mock(ticket, **kwargs):
            if 'stop_loss_price' in kwargs:
                if ticket in position_state:
                    position_state[ticket]['sl'] = kwargs['stop_loss_price']
                return True
            return False
        
        order_manager.get_position_by_ticket.side_effect = get_position_mock
        order_manager.modify_order.side_effect = modify_order_mock
        
        sl_manager = SLManager(config, mt5_connector, order_manager)
        
        return sl_manager, mt5_connector, order_manager, position_state
    
    def test_symbol_category(self, category: str, symbols: List[str], 
                            test_scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Test a category of symbols."""
        print(f"\n{'='*60}")
        print(f"Testing Category: {category}")
        print(f"Symbols: {', '.join(symbols)}")
        print(f"{'='*60}\n")
        
        category_results = {
            'category': category,
            'symbols': symbols,
            'scenarios': []
        }
        
        for symbol in symbols:
            print(f"  Testing {symbol}...")
            
            # Create fresh SL Manager for each symbol
            sl_manager, mt5_connector, order_manager, position_state = self.create_mock_sl_manager()
            
            # Configure symbol-specific info
            symbol_info = self.get_symbol_info(symbol)
            mt5_connector.get_symbol_info.return_value = symbol_info
            
            # Update tick
            tick = self.get_tick_for_symbol(symbol)
            mt5_connector.get_symbol_info_tick.return_value.bid = tick['bid']
            mt5_connector.get_symbol_info_tick.return_value.ask = tick['ask']
            
            for scenario in test_scenarios:
                scenario_result = self.run_scenario(
                    sl_manager, mt5_connector, order_manager, position_state,
                    symbol, symbol_info, scenario
                )
                category_results['scenarios'].append(scenario_result)
        
        return category_results
    
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get symbol info based on symbol type."""
        symbol_upper = symbol.upper()
        
        # Forex
        if any(x in symbol_upper for x in ['EUR', 'GBP', 'USD', 'JPY', 'AUD', 'NZD', 'CAD', 'CHF']):
            return {
                'name': symbol,
                'point': 0.00001,
                'digits': 5,
                'contract_size': 100000.0,
                'trade_stops_level': 10,
                'trade_tick_value': None
            }
        
        # Crypto
        elif any(x in symbol_upper for x in ['BTC', 'ETH', 'LTC', 'XRP', 'DOGE']):
            return {
                'name': symbol,
                'point': 0.01,
                'digits': 2,
                'contract_size': 1.0,  # May need correction
                'trade_stops_level': 10,
                'trade_tick_value': 1.0
            }
        
        # Indices
        elif any(x in symbol_upper for x in ['US30', 'US500', 'UK100', 'STOXX', 'NAS100']):
            return {
                'name': symbol,
                'point': 0.01,
                'digits': 2,
                'contract_size': 1.0,
                'trade_stops_level': 10,
                'trade_tick_value': 1.0
            }
        
        # Default (forex-like)
        else:
            return {
                'name': symbol,
                'point': 0.00001,
                'digits': 5,
                'contract_size': 100000.0,
                'trade_stops_level': 10,
                'trade_tick_value': None
            }
    
    def get_tick_for_symbol(self, symbol: str) -> Dict[str, float]:
        """Get appropriate tick values for symbol."""
        symbol_upper = symbol.upper()
        
        # Forex
        if any(x in symbol_upper for x in ['EUR', 'GBP', 'USD']):
            return {'bid': 1.20000, 'ask': 1.20010}
        
        # Crypto/Indices
        elif any(x in symbol_upper for x in ['BTC', 'US30', 'US500']):
            return {'bid': 50000.00, 'ask': 50010.00}
        
        # Default
        return {'bid': 1.20000, 'ask': 1.20010}
    
    def run_scenario(self, sl_manager, mt5_connector, order_manager, position_state,
                    symbol: str, symbol_info: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
        """Run a single test scenario."""
        position = scenario['position'].copy()
        position['symbol'] = symbol
        
        # Update tick to match position
        tick = self.get_tick_for_symbol(symbol)
        if 'price_current' in position:
            tick['bid'] = position['price_current'] - 0.00005
            tick['ask'] = position['price_current'] + 0.00005
        mt5_connector.get_symbol_info_tick.return_value.bid = tick['bid']
        mt5_connector.get_symbol_info_tick.return_value.ask = tick['ask']
        
        # Store position state
        position_state[position['ticket']] = position.copy()
        
        # Run update
        success, reason = sl_manager.update_sl_atomic(position['ticket'], position)
        
        return {
            'symbol': symbol,
            'scenario_name': scenario.get('name', 'Unknown'),
            'position_ticket': position['ticket'],
            'position_profit': position['profit'],
            'success': success,
            'reason': reason,
            'applied_sl': position_state[position['ticket']].get('sl', 0.0) if position['ticket'] in position_state else 0.0
        }
    
    def run_full_verification(self):
        """Run full verification across all symbol categories."""
        print("="*60)
        print("SL MANAGER SIMULATION VERIFICATION")
        print("="*60)
        
        # Test scenarios
        test_scenarios = [
            {
                'name': 'Losing Trade - No SL',
                'position': {
                    'ticket': 2001,
                    'type': 'BUY',
                    'price_open': 1.20000,
                    'price_current': 1.19750,
                    'sl': 0.0,
                    'volume': 0.01,
                    'profit': -2.50
                }
            },
            {
                'name': 'Sweet Spot',
                'position': {
                    'ticket': 2002,
                    'type': 'BUY',
                    'price_open': 1.20000,
                    'price_current': 1.20050,
                    'sl': 0.0,
                    'volume': 0.01,
                    'profit': 0.05
                }
            },
            {
                'name': 'Trailing Zone',
                'position': {
                    'ticket': 2003,
                    'type': 'BUY',
                    'price_open': 1.20000,
                    'price_current': 1.20150,
                    'sl': 0.0,
                    'volume': 0.01,
                    'profit': 0.25
                }
            }
        ]
        
        # Symbol categories
        categories = {
            'Forex': ['EURUSDm', 'GBPUSDm', 'USDJPYm'],
            'Crypto': ['BTCUSDm', 'ETHUSDm', 'LTCUSDm'],
            'Indices': ['US30m', 'US500m', 'UK100m'],
            'Commodities': ['XAUUSDm', 'XAGUSDm', 'OILUSDm']
        }
        
        for category, symbols in categories.items():
            category_result = self.test_symbol_category(category, symbols, test_scenarios)
            self.results['symbols_tested'].extend(symbols)
            
            # Count pass/fail
            for scenario_result in category_result['scenarios']:
                if scenario_result['success']:
                    self.results['scenarios_passed'] += 1
                else:
                    self.results['scenarios_failed'] += 1
            
            self.results['detailed_results'].append(category_result)
        
        # Save results
        self.save_results()
        self.print_summary()
    
    def save_results(self):
        """Save verification results."""
        output_file = self.output_dir / 'simulation_verification_results.json'
        with open(output_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        print(f"\n[OK] Results saved to: {output_file}")
    
    def print_summary(self):
        """Print verification summary."""
        print(f"\n{'='*60}")
        print("VERIFICATION SUMMARY")
        print(f"{'='*60}")
        print(f"Symbols tested: {len(set(self.results['symbols_tested']))}")
        print(f"Scenarios passed: {self.results['scenarios_passed']}")
        print(f"Scenarios failed: {self.results['scenarios_failed']}")
        print(f"Success rate: {self.results['scenarios_passed']/(self.results['scenarios_passed']+self.results['scenarios_failed'])*100:.1f}%")
        
        # Per-category breakdown
        print(f"\nPer-Category Results:")
        for category_result in self.results['detailed_results']:
            category = category_result['category']
            passed = sum(1 for s in category_result['scenarios'] if s['success'])
            total = len(category_result['scenarios'])
            print(f"  {category}: {passed}/{total} passed ({passed/total*100:.1f}%)")


if __name__ == '__main__':
    verification = SimulationVerification()
    verification.run_full_verification()

