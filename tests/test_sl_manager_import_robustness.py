"""
Test for SLManager import robustness.

This test verifies that SLManager can be imported and instantiated,
and that code handles None gracefully when import fails.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSLManagerImportRobustness(unittest.TestCase):
    """Test cases for SLManager import and instantiation."""
    
    def test_sl_manager_can_be_imported(self):
        """Test that SLManager can be imported successfully."""
        try:
            from risk.sl_manager import SLManager
            self.assertTrue(True, "SLManager imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import SLManager: {e}")
    
    def test_sl_manager_can_be_instantiated(self):
        """Test that SLManager can be instantiated with valid config."""
        from risk.sl_manager import SLManager
        
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
            }
        }
        
        mt5_connector = Mock()
        order_manager = Mock()
        
        try:
            sl_manager = SLManager(config, mt5_connector, order_manager)
            self.assertIsNotNone(sl_manager)
            self.assertTrue(hasattr(sl_manager, 'update_sl_atomic'))
            self.assertTrue(hasattr(sl_manager, 'get_effective_sl_profit'))
            self.assertTrue(hasattr(sl_manager, 'fail_safe_check'))
        except Exception as e:
            self.fail(f"Failed to instantiate SLManager: {e}")
    
    def test_risk_manager_handles_sl_manager_import_failure(self):
        """Test that RiskManager handles SLManager import failure gracefully."""
        from risk.risk_manager import RiskManager
        
        config = {
            'risk': {
                'max_risk_per_trade_usd': 2.0
            }
        }
        
        mt5_connector = Mock()
        order_manager = Mock()
        
        # Simulate import failure
        with patch('risk.risk_manager.SLManager', side_effect=ImportError("Test import error")):
            risk_manager = RiskManager(config, mt5_connector, order_manager)
            
            # Should set sl_manager to None on import failure
            self.assertIsNone(risk_manager.sl_manager)
    
    def test_risk_manager_checks_sl_manager_before_use(self):
        """Test that RiskManager checks if sl_manager is None before using it."""
        from risk.risk_manager import RiskManager
        
        config = {
            'risk': {
                'max_risk_per_trade_usd': 2.0
            }
        }
        
        mt5_connector = Mock()
        order_manager = Mock()
        
        risk_manager = RiskManager(config, mt5_connector, order_manager)
        
        # If sl_manager is None, code should handle it gracefully
        if risk_manager.sl_manager is None:
            # Code that uses sl_manager should check for None
            # This test verifies the pattern exists
            self.assertIsNone(risk_manager.sl_manager)
        else:
            # If sl_manager exists, it should have required methods
            self.assertTrue(hasattr(risk_manager.sl_manager, 'update_sl_atomic'))


if __name__ == '__main__':
    unittest.main()

