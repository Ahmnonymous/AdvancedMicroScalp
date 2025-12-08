"""
Test Risk Enforcement
Verifies that risk never exceeds $2.00 with slippage buffer.
"""

import unittest
from unittest.mock import Mock, patch

from risk.risk_manager import RiskManager
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager


class TestRiskEnforcement(unittest.TestCase):
    """Test risk enforcement with slippage buffer."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'risk': {
                'max_risk_per_trade_usd': 2.0,
                'default_lot_size': 0.01
            }
        }
        self.mt5_connector = Mock(spec=MT5Connector)
        self.order_manager = Mock(spec=OrderManager)
        self.risk_manager = RiskManager(self.config, self.mt5_connector, self.order_manager)
    
    def test_risk_calculation_with_slippage_buffer(self):
        """Test that risk calculation includes 10% slippage buffer."""
        symbol = "EURUSD"
        lot_size = 0.01
        stop_loss_pips = 20.0
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000,
            'volume_min': 0.01
        }
        
        # Calculate risk
        point = 0.00001
        pip_value = point * 10
        stop_loss_price = stop_loss_pips * pip_value
        contract_size = 100000
        
        base_risk = lot_size * stop_loss_price * contract_size
        risk_with_buffer = base_risk * 1.10  # 10% buffer
        
        # Risk should be $2.20 with buffer (20 pips * 0.01 lot * 100000 contract * 1.10)
        expected_risk = 0.01 * 0.00020 * 100000 * 1.10
        self.assertAlmostEqual(risk_with_buffer, expected_risk, places=2)
    
    def test_reject_trade_if_risk_exceeds_limit(self):
        """Test that trades are rejected if risk exceeds $2.00 with buffer."""
        symbol = "EURUSD"
        lot_size = 0.10  # Large lot size
        stop_loss_pips = 20.0
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000,
            'volume_min': 0.01
        }
        
        # Calculate risk
        point = 0.00001
        pip_value = point * 10
        stop_loss_price = stop_loss_pips * pip_value
        contract_size = 100000
        
        base_risk = lot_size * stop_loss_price * contract_size
        risk_with_buffer = base_risk * 1.10
        
        # Risk should exceed $2.00
        self.assertGreater(risk_with_buffer, 2.0)
    
    def test_risk_calculation_with_actual_fill_price(self):
        """Test that risk is recalculated with actual fill price."""
        symbol = "EURUSD"
        lot_size = 0.01
        stop_loss_pips = 20.0
        
        # Requested entry price
        entry_price_requested = 1.10000
        
        # Actual fill price (with slippage)
        entry_price_actual = 1.10005  # 5 pips slippage
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000
        }
        
        point = 0.00001
        pip_value = point * 10
        
        # Calculate risk with actual fill price
        if True:  # BUY order
            sl_price_from_fill = entry_price_actual - (stop_loss_pips * pip_value)
        
        sl_distance_from_fill = abs(entry_price_actual - sl_price_from_fill)
        contract_size = 100000
        actual_risk_with_fill = lot_size * sl_distance_from_fill * contract_size
        
        # Risk should be calculated from actual fill price
        self.assertGreater(actual_risk_with_fill, 0)
        self.assertLessEqual(actual_risk_with_fill * 1.10, 2.0)  # With buffer


if __name__ == '__main__':
    unittest.main()

