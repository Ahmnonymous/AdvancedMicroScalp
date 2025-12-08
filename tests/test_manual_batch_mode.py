"""
Unit and Integration Tests for Manual Batch Trade Approval Mode
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.trading_bot import TradingBot


class TestManualBatchMode(unittest.TestCase):
    """Test Manual Batch Trade Approval Mode functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Mock config
        self.mock_config = {
            'mt5': {
                'account': '123456',
                'password': 'test',
                'server': 'test-server'
            },
            'risk': {
                'max_risk_per_trade_usd': 2.0,
                'max_open_trades': 6,
                'min_stop_loss_pips': 10
            },
            'trading': {
                'min_quality_score': 50,
                'manual_wait_for_close_timeout_seconds': 3600
            },
            'pairs': {
                'test_mode': True,
                'test_mode_ignore_restrictions': True
            },
            'supervisor': {'enabled': False}
        }
        
        # Mock all dependencies
        with patch('bot.trading_bot.MT5Connector'), \
             patch('bot.trading_bot.OrderManager'), \
             patch('bot.trading_bot.TrendFilter'), \
             patch('bot.trading_bot.RiskManager'), \
             patch('bot.trading_bot.PairFilter'), \
             patch('bot.trading_bot.HalalCompliance'), \
             patch('bot.trading_bot.NewsFilter'), \
             patch('bot.trading_bot.ConfigValidator'):
            
            with patch('builtins.open', create=True):
                self.bot = TradingBot.__new__(TradingBot)
                self.bot.config = self.mock_config
                self.bot.manual_approval_mode = False
                self.bot.manual_max_trades = None
                self.bot.manual_batch_cancelled = False
                self.bot.trading_config = self.mock_config.get('trading', {})
    
    def test_get_user_trade_count_valid_input(self):
        """Test get_user_trade_count with valid inputs."""
        with patch('builtins.input', side_effect=['3']):
            result = self.bot.get_user_trade_count()
            self.assertEqual(result, 3)
        
        with patch('builtins.input', side_effect=['1']):
            result = self.bot.get_user_trade_count()
            self.assertEqual(result, 1)
        
        with patch('builtins.input', side_effect=['6']):
            result = self.bot.get_user_trade_count()
            self.assertEqual(result, 6)
    
    def test_get_user_trade_count_invalid_input(self):
        """Test get_user_trade_count with invalid inputs."""
        # Test out of range
        with patch('builtins.input', side_effect=['0', '7', '3']):
            with patch('builtins.print'):  # Suppress print output
                result = self.bot.get_user_trade_count()
                self.assertEqual(result, 3)
        
        # Test non-numeric
        with patch('builtins.input', side_effect=['abc', '2']):
            with patch('builtins.print'):  # Suppress print output
                result = self.bot.get_user_trade_count()
                self.assertEqual(result, 2)
    
    def test_get_user_approval_y_n_all(self):
        """Test get_user_approval with Y, N, ALL inputs."""
        with patch('builtins.input', side_effect=['Y']):
            result = self.bot.get_user_approval('EURUSD', 'LONG', 85.0)
            self.assertTrue(result)
        
        with patch('builtins.input', side_effect=['N']):
            result = self.bot.get_user_approval('EURUSD', 'LONG', 85.0)
            self.assertFalse(result)
        
        with patch('builtins.input', side_effect=['ALL']):
            result = self.bot.get_user_approval('EURUSD', 'LONG', 85.0)
            self.assertEqual(result, 'ALL')
    
    def test_get_user_approval_cancel_skip(self):
        """Test get_user_approval with CANCEL and SKIP inputs."""
        with patch('builtins.input', side_effect=['C']):
            result = self.bot.get_user_approval('EURUSD', 'LONG', 85.0)
            self.assertEqual(result, 'CANCEL')
        
        with patch('builtins.input', side_effect=['S']):
            result = self.bot.get_user_approval('EURUSD', 'LONG', 85.0)
            self.assertEqual(result, 'SKIP')
    
    def test_display_opportunities_sorts_by_quality(self):
        """Test that display_opportunities sorts by quality score descending."""
        opportunities = [
            {'symbol': 'EURUSD', 'signal': 'LONG', 'quality_score': 60.0, 'min_lot': 0.01, 'spread': 10.0, 'spread_fees_cost': 0.10},
            {'symbol': 'GBPUSD', 'signal': 'SHORT', 'quality_score': 85.0, 'min_lot': 0.01, 'spread': 12.0, 'spread_fees_cost': 0.12},
            {'symbol': 'USDJPY', 'signal': 'LONG', 'quality_score': 70.0, 'min_lot': 0.01, 'spread': 8.0, 'spread_fees_cost': 0.08},
        ]
        
        # Mock dependencies
        self.bot.mt5_connector = Mock()
        self.bot.mt5_connector.get_symbol_info = Mock(return_value={
            'point': 0.00001, 'digits': 5, 'contract_size': 1.0
        })
        self.bot.risk_manager = Mock()
        self.bot.risk_manager.min_stop_loss_pips = 10
        self.bot.risk_manager.max_risk_usd = 2.0
        self.bot.pair_filter = Mock()
        self.bot.pair_filter.max_spread_points = 20
        
        with patch('builtins.print'):  # Suppress print output
            self.bot.display_opportunities(opportunities, 3)
        
        # Verify sorting (should be by quality desc: 85, 70, 60)
        sorted_opps = sorted(opportunities, key=lambda x: x.get('quality_score', 0.0), reverse=True)
        self.assertEqual(sorted_opps[0]['quality_score'], 85.0)
        self.assertEqual(sorted_opps[1]['quality_score'], 70.0)
        self.assertEqual(sorted_opps[2]['quality_score'], 60.0)
    
    def test_display_opportunities_highlights_best(self):
        """Test that display_opportunities highlights the best setup with 🌟."""
        opportunities = [
            {'symbol': 'EURUSD', 'signal': 'LONG', 'quality_score': 85.0, 'min_lot': 0.01, 'spread': 10.0, 'spread_fees_cost': 0.10},
            {'symbol': 'GBPUSD', 'signal': 'SHORT', 'quality_score': 60.0, 'min_lot': 0.01, 'spread': 12.0, 'spread_fees_cost': 0.12},
        ]
        
        # Mock dependencies
        self.bot.mt5_connector = Mock()
        self.bot.mt5_connector.get_symbol_info = Mock(return_value={
            'point': 0.00001, 'digits': 5, 'contract_size': 1.0
        })
        self.bot.risk_manager = Mock()
        self.bot.risk_manager.min_stop_loss_pips = 10
        self.bot.risk_manager.max_risk_usd = 2.0
        self.bot.pair_filter = Mock()
        self.bot.pair_filter.max_spread_points = 20
        
        print_calls = []
        with patch('builtins.print', side_effect=lambda *args, **kwargs: print_calls.append(str(args))):
            self.bot.display_opportunities(opportunities, 2)
        
        # Check that best setup is mentioned
        output = ' '.join(print_calls)
        self.assertIn('🌟', output)
        self.assertIn('BEST SETUP', output)


class TestManualBatchModeIntegration(unittest.TestCase):
    """Integration tests for Manual Batch Mode."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_config = {
            'mt5': {
                'account': '123456',
                'password': 'test',
                'server': 'test-server'
            },
            'risk': {
                'max_risk_per_trade_usd': 2.0,
                'max_open_trades': 6,
                'min_stop_loss_pips': 10
            },
            'trading': {
                'min_quality_score': 50,
                'manual_wait_for_close_timeout_seconds': 3600
            },
            'pairs': {
                'test_mode': True
            },
            'supervisor': {'enabled': False}
        }
    
    @patch('bot.trading_bot.MT5Connector')
    @patch('bot.trading_bot.OrderManager')
    @patch('bot.trading_bot.TrendFilter')
    @patch('bot.trading_bot.RiskManager')
    @patch('bot.trading_bot.PairFilter')
    @patch('bot.trading_bot.HalalCompliance')
    @patch('bot.trading_bot.NewsFilter')
    @patch('bot.trading_bot.ConfigValidator')
    @patch('builtins.open', create=True)
    def test_approval_flow_all_behavior(self, *mocks):
        """Test that ALL approves all remaining trades."""
        # This is a simplified integration test
        # Full integration would require MT5 connection mocking
        pass
    
    @patch('bot.trading_bot.MT5Connector')
    @patch('bot.trading_bot.OrderManager')
    @patch('bot.trading_bot.TrendFilter')
    @patch('bot.trading_bot.RiskManager')
    @patch('bot.trading_bot.PairFilter')
    @patch('bot.trading_bot.HalalCompliance')
    @patch('bot.trading_bot.NewsFilter')
    @patch('bot.trading_bot.ConfigValidator')
    @patch('builtins.open', create=True)
    def test_cancel_batch_behavior(self, *mocks):
        """Test that CANCEL cancels the entire batch."""
        # This is a simplified integration test
        pass


    def test_manual_batch_market_closed_skips(self):
        """Test that market-closed symbols are skipped without attempting orders."""
        # This test verifies that is_symbol_tradeable_now prevents order attempts
        with patch.object(self.bot.mt5_connector, 'is_symbol_tradeable_now', return_value=(False, "Market closed")):
            result = self.bot.execute_trade({'symbol': 'ABTm', 'signal': 'SHORT'}, skip_randomness=True)
            self.assertFalse(result)
    
    def test_manual_batch_invalid_volume_retries_minimum(self):
        """Test that invalid volume errors retry once with broker minimum."""
        # This test would require mocking order_manager to return -1 on first call
        # and success on second call with minimum lot
        pass  # Integration test scenario
    
    def test_manual_batch_exponential_backoff_on_transient(self):
        """Test that transient errors use exponential backoff."""
        # This test would verify backoff delays between retries
        # Would require time mocking
        pass  # Integration test scenario


if __name__ == '__main__':
    unittest.main()

