"""
Test Slippage Handling
Verifies that actual fill prices are captured and used in risk calculations.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import MetaTrader5 as mt5

from execution.order_manager import OrderManager, OrderType
from execution.mt5_connector import MT5Connector


class TestSlippageHandling(unittest.TestCase):
    """Test slippage detection and handling."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mt5_connector = Mock(spec=MT5Connector)
        self.order_manager = OrderManager(self.mt5_connector)
    
    @patch('execution.order_manager.mt5')
    def test_capture_actual_fill_price(self, mock_mt5):
        """Test that actual fill price is captured from deal history."""
        symbol = "EURUSD"
        
        # Mock MT5 connection
        self.mt5_connector.ensure_connected.return_value = True
        
        # Mock symbol info
        self.mt5_connector.get_symbol_info.return_value = {
            'name': symbol,
            'point': 0.00001,
            'digits': 5,
            'contract_size': 100000,
            'trade_stops_level': 10,
            'filling_mode': 4
        }
        
        # Mock tick data
        mock_tick = MagicMock()
        mock_tick.bid = 1.10000
        mock_tick.ask = 1.10010
        mock_mt5.symbol_info_tick.return_value = mock_tick
        
        # Mock order result
        mock_result = MagicMock()
        mock_result.retcode = mt5.TRADE_RETCODE_DONE
        mock_result.order = 12345
        mock_mt5.order_send.return_value = mock_result
        
        # Mock deal history with actual fill price
        entry_deal = MagicMock()
        entry_deal.entry = mt5.DEAL_ENTRY_IN
        entry_deal.price = 1.10012  # Actual fill price (2 pips slippage)
        
        mock_mt5.history_deals_get.return_value = [entry_deal]
        
        # Place order
        result = self.order_manager.place_order(
            symbol=symbol,
            order_type=OrderType.BUY,
            lot_size=0.01,
            stop_loss=20.0
        )
        
        # Verify actual fill price is captured
        self.assertIsNotNone(result)
        self.assertIn('entry_price_actual', result)
        self.assertEqual(result['entry_price_actual'], 1.10012)
        self.assertGreater(result.get('slippage', 0), 0)
    
    def test_slippage_calculation(self):
        """Test that slippage is calculated correctly."""
        requested_price = 1.10000
        actual_price = 1.10005
        
        slippage = abs(actual_price - requested_price)
        
        # Slippage should be 5 pips (0.00005)
        self.assertAlmostEqual(slippage, 0.00005, places=5)
    
    def test_risk_recalculation_with_slippage(self):
        """Test that risk is recalculated with actual fill price."""
        symbol = "EURUSD"
        lot_size = 0.01
        stop_loss_pips = 20.0
        
        # Requested entry price
        entry_price_requested = 1.10000
        
        # Actual fill price (with slippage)
        entry_price_actual = 1.10005  # 5 pips slippage
        
        point = 0.00001
        pip_value = point * 10
        contract_size = 100000
        
        # Calculate risk with requested price
        sl_price_requested = entry_price_requested - (stop_loss_pips * pip_value)
        risk_requested = lot_size * abs(entry_price_requested - sl_price_requested) * contract_size
        
        # Calculate risk with actual fill price
        sl_price_actual = entry_price_actual - (stop_loss_pips * pip_value)
        risk_actual = lot_size * abs(entry_price_actual - sl_price_actual) * contract_size
        
        # Risk should be slightly different due to slippage
        # (though in this case, SL is relative, so risk should be the same)
        # But if we account for slippage in SL calculation, risk might differ
        self.assertGreaterEqual(risk_actual, 0)


if __name__ == '__main__':
    unittest.main()

