"""
Integration Layer for Backtest Mode
Injects backtest providers into existing bot components without breaking live mode.
"""

import sys
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional
from bot.trading_bot import TradingBot
from backtest.market_data_provider import MarketDataProvider, HistoricalMarketDataProvider
from backtest.order_execution_provider import OrderExecutionProvider, SimulatedOrderExecutionProvider

from utils.logger_factory import get_logger

logger = get_logger("backtest_integration", "logs/backtest/integration.log")


class BacktestIntegration:
    """Integrates backtest providers with existing bot components."""
    
    @staticmethod
    def inject_providers(bot: TradingBot, market_data_provider: MarketDataProvider,
                        order_execution_provider: OrderExecutionProvider, backtest_symbols: list = None):
        """
        Inject backtest providers into bot components.
        
        This replaces MT5Connector and OrderManager with backtest providers
        while maintaining the same interface.
        
        Args:
            bot: TradingBot instance
            market_data_provider: MarketDataProvider instance
            order_execution_provider: OrderExecutionProvider instance
            backtest_symbols: List of symbols to use in backtest (from backtest config)
        """
        logger.info("Injecting backtest providers into bot...")
        
        # Replace mt5_connector with market data provider wrapper
        bot.mt5_connector = BacktestMT5ConnectorWrapper(market_data_provider)
        
        # Replace order_manager with order execution provider wrapper
        bot.order_manager = BacktestOrderManagerWrapper(order_execution_provider)
        
        # Update components that depend on mt5_connector
        if hasattr(bot, 'trend_filter'):
            bot.trend_filter.mt5_connector = bot.mt5_connector
        
        if hasattr(bot, 'pair_filter'):
            bot.pair_filter.mt5_connector = bot.mt5_connector
            # Enable test mode in backtest to bypass symbol filters
            bot.pair_filter.test_mode = True
            bot.pair_filter.test_mode_ignore_restrictions = True
            bot.pair_filter.test_mode_ignore_spread = True
            bot.pair_filter.test_mode_ignore_commission = True
            bot.pair_filter.test_mode_ignore_exotics = True
            
            # CRITICAL: Set allowed_symbols from backtest config so test mode logic works
            if backtest_symbols:
                bot.pair_filter.allowed_symbols = backtest_symbols
                bot.pair_filter.auto_discover_symbols = False  # Use provided symbols
                logger.info(f"Set allowed_symbols for backtest: {backtest_symbols}")
            else:
                logger.warning("No backtest symbols provided - PairFilter may not find symbols")
            
            logger.info("Enabled test mode for PairFilter (backtest mode)")
        
        if hasattr(bot, 'halal_compliance'):
            bot.halal_compliance.mt5_connector = bot.mt5_connector
        
        if hasattr(bot, 'news_filter'):
            bot.news_filter.mt5_connector = bot.mt5_connector
        
        if hasattr(bot, 'market_closing_filter'):
            bot.market_closing_filter.mt5_connector = bot.mt5_connector
        
        if hasattr(bot, 'volume_filter'):
            bot.volume_filter.mt5_connector = bot.mt5_connector
        
        # Update risk manager
        if hasattr(bot, 'risk_manager'):
            bot.risk_manager.mt5_connector = bot.mt5_connector
            bot.risk_manager.order_manager = bot.order_manager
        
        logger.info("Backtest providers injected successfully")


class BacktestMT5ConnectorWrapper:
    """Wrapper to make MarketDataProvider compatible with MT5Connector interface."""
    
    def __init__(self, market_data_provider: MarketDataProvider):
        self.market_data_provider = market_data_provider
        self.connected = True
    
    def connect(self) -> bool:
        """Always connected in backtest."""
        self.connected = True
        return True
    
    def reconnect(self) -> bool:
        """Always connected in backtest."""
        return True
    
    def ensure_connected(self) -> bool:
        """Always connected in backtest."""
        return True
    
    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Get account info from market data provider."""
        return self.market_data_provider.get_account_info()
    
    def get_symbol_info(self, symbol: str, check_price_staleness: bool = False) -> Optional[Dict[str, Any]]:
        """Get symbol info from market data provider."""
        return self.market_data_provider.get_symbol_info(symbol, check_price_staleness)
    
    def get_symbol_info_tick(self, symbol: str):
        """Get tick info from market data provider."""
        return self.market_data_provider.get_symbol_info_tick(symbol)
    
    def is_symbol_tradeable_now(self, symbol: str, check_trade_allowed: bool = True):
        """Check if symbol is tradeable."""
        return self.market_data_provider.is_symbol_tradeable_now(symbol, check_trade_allowed)
    
    def is_swap_free(self, symbol: str) -> bool:
        """Check if symbol is swap-free."""
        symbol_info = self.get_symbol_info(symbol)
        if symbol_info:
            return symbol_info.get('swap_mode', 1) == 0
        return False
    
    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int):
        """
        Mimic mt5.copy_rates_from_pos() for backtesting.
        
        This is called by TrendFilter.get_rates() to get historical data.
        
        Args:
            symbol: Symbol to get rates for
            timeframe: MT5 timeframe constant (ignored in backtest, uses data timeframe)
            start_pos: Starting position (0 = from current time backwards)
            count: Number of bars to return
        
        Returns:
            NumPy array with rate data, or None if not available
        """
        # Get historical rates from provider
        df = self.market_data_provider.get_historical_rates(symbol, count=count)
        if df is None or len(df) == 0:
            return None
        
        # Convert DataFrame to NumPy structured array (MT5 format)
        # MT5 format: time, open, high, low, close, tick_volume, spread, real_volume
        import numpy as np
        
        # Ensure we have required columns
        required_cols = ['time', 'open', 'high', 'low', 'close']
        for col in required_cols:
            if col not in df.columns:
                logger.warning(f"Missing required column '{col}' in historical rates for {symbol}")
                return None
        
        # Ensure data types are correct before conversion
        try:
            # Convert time to int64 if needed
            if 'time' in df.columns:
                if pd.api.types.is_datetime64_any_dtype(df['time']):
                    df['time'] = ((df['time'] - pd.Timestamp('1970-01-01')) // pd.Timedelta('1s')).astype('int64')
                else:
                    df['time'] = df['time'].astype('int64')
            
            # Ensure numeric columns are float64
            for col in ['open', 'high', 'low', 'close']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').astype('float64')
        except Exception as e:
            logger.error(f"Error preparing data for {symbol}: {e}", exc_info=True)
            return None
        
        # Create structured array
        dtype = [
            ('time', 'int64'),
            ('open', 'float64'),
            ('high', 'float64'),
            ('low', 'float64'),
            ('close', 'float64'),
            ('tick_volume', 'int64'),
            ('spread', 'int32'),
            ('real_volume', 'int64')
        ]
        
        # Prepare data
        times = df['time'].values.astype('int64')
        opens = df['open'].values.astype('float64')
        highs = df['high'].values.astype('float64')
        lows = df['low'].values.astype('float64')
        closes = df['close'].values.astype('float64')
        volumes = df.get('volume', pd.Series([0] * len(df))).values.astype('int64')
        spreads = df.get('spread', pd.Series([20] * len(df))).values.astype('int32')
        
        # Create structured array
        rates = np.empty(len(df), dtype=dtype)
        rates['time'] = times
        rates['open'] = opens
        rates['high'] = highs
        rates['low'] = lows
        rates['close'] = closes
        rates['tick_volume'] = volumes
        rates['spread'] = spreads
        rates['real_volume'] = volumes
        
        return rates
    
    def shutdown(self):
        """Shutdown (no-op in backtest)."""
        self.connected = False


class BacktestOrderManagerWrapper:
    """Wrapper to make OrderExecutionProvider compatible with OrderManager interface."""
    
    def __init__(self, order_execution_provider: OrderExecutionProvider):
        self.order_execution_provider = order_execution_provider
    
    def place_order(self, symbol: str, order_type, lot_size: float, stop_loss: float,
                   take_profit: Optional[float] = None, comment: str = "Trading Bot"):
        """Place order via execution provider."""
        from backtest.order_execution_provider import OrderType as BTOrderType
        
        bt_order_type = BTOrderType.BUY if order_type.value == 0 else BTOrderType.SELL
        return self.order_execution_provider.place_order(
            symbol, bt_order_type, lot_size, stop_loss, take_profit, comment
        )
    
    def modify_order(self, ticket: int, stop_loss: Optional[float] = None,
                    take_profit: Optional[float] = None,
                    stop_loss_price: Optional[float] = None,
                    take_profit_price: Optional[float] = None) -> bool:
        """Modify order via execution provider."""
        return self.order_execution_provider.modify_order(
            ticket, stop_loss, take_profit, stop_loss_price, take_profit_price
        )
    
    def get_open_positions(self, exclude_dec8: bool = True):
        """Get open positions via execution provider."""
        return self.order_execution_provider.get_open_positions(exclude_dec8)
    
    def get_position_by_ticket(self, ticket: int, exclude_dec8: bool = True):
        """Get position by ticket via execution provider."""
        return self.order_execution_provider.get_position_by_ticket(ticket, exclude_dec8)
    
    def close_position(self, ticket: int, comment: str = None) -> bool:
        """Close position via execution provider."""
        # Accept comment parameter for compatibility, but don't use it in backtest
        return self.order_execution_provider.close_position(ticket)
    
    def get_position_count(self, exclude_dec8: bool = True) -> int:
        """Get position count."""
        positions = self.get_open_positions(exclude_dec8)
        return len(positions) if positions else 0

