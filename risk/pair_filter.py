"""
Pair Filter Module
Filters trading pairs based on spread, commission, and other criteria.
"""

import logging
import MetaTrader5 as mt5
from typing import Dict, Any, List, Optional
from execution.mt5_connector import MT5Connector

logger = logging.getLogger(__name__)


class PairFilter:
    """Filters trading pairs based on spread and commission criteria."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector):
        self.config = config
        self.pairs_config = config.get('pairs', {})
        self.mt5_connector = mt5_connector
        
        self.allowed_symbols = self.pairs_config.get('allowed_symbols', [])
        self.auto_discover_symbols = self.pairs_config.get('auto_discover_symbols', True)
        self.max_spread_points = self.pairs_config.get('max_spread_points', 15)
        self.max_commission_per_lot = self.pairs_config.get('max_commission_per_lot', 0.50)
        self.exclude_exotics = self.pairs_config.get('exclude_exotics', True)
        self.prioritize_low_fee = self.pairs_config.get('prioritize_low_fee', True)
        
        # Test mode settings
        self.test_mode = self.pairs_config.get('test_mode', False)
        self.test_mode_ignore_restrictions = self.pairs_config.get('test_mode_ignore_restrictions', False)
        self.test_mode_ignore_spread = self.pairs_config.get('test_mode_ignore_spread', False)
        self.test_mode_ignore_commission = self.pairs_config.get('test_mode_ignore_commission', False)
        self.test_mode_ignore_exotics = self.pairs_config.get('test_mode_ignore_exotics', False)
        
        # Define exotic pairs
        self.exotic_currencies = [
            'TRY', 'ZAR', 'MXN', 'BRL', 'RUB', 'INR', 'CNH', 'HKD', 'SGD',
            'THB', 'PLN', 'CZK', 'HUF', 'SEK', 'NOK', 'DKK'
        ]
    
    def is_exotic(self, symbol: str) -> bool:
        """Check if symbol is an exotic pair."""
        if self.test_mode and self.test_mode_ignore_exotics:
            return False  # Ignore exotic filter in test mode
        
        if not self.exclude_exotics:
            return False
        
        for currency in self.exotic_currencies:
            if currency in symbol:
                return True
        
        return False
    
    def is_allowed_symbol(self, symbol: str) -> bool:
        """Check if symbol is in allowed list."""
        if not self.allowed_symbols:
            return True  # If no list specified, allow all
        
        return symbol in self.allowed_symbols
    
    def get_spread_points(self, symbol: str) -> Optional[float]:
        """Get current spread in points (fixed calculation)."""
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return None
        
        # Use bid-ask spread for more accurate calculation
        bid = symbol_info.get('bid', 0)
        ask = symbol_info.get('ask', 0)
        point = symbol_info.get('point', 0.00001)
        digits = symbol_info.get('digits', 5)
        
        if bid <= 0 or ask <= 0 or point <= 0:
            return None
        
        # Calculate spread as bid-ask difference in points
        spread_price = ask - bid
        
        # For high-priced symbols (e.g., indices, crypto), points calculation may overflow
        # Use percentage-based calculation as fallback
        if point > 0:
            spread_points = spread_price / point
        else:
            # Fallback to percentage if point is invalid
            spread_points = (spread_price / bid * 100) * 10000  # Convert to approximate points (scaled)
        
        # Sanity check: if spread_points is unreasonably high, use percentage instead
        # For forex, typical spreads are 1-50 points
        # For indices, spreads can be 50-500 points
        # For high-priced symbols (e.g., >1000), use percentage calculation
        if spread_points > 10000 or bid > 1000:
            # Use percentage-based spread for high-priced symbols
            if bid > 0:
                spread_percent = (spread_price / bid) * 100
                # Convert to approximate points: 1% = 100 points (scaled)
                spread_points = spread_percent * 100
                logger.debug(f"{symbol}: Using percentage-based spread calculation (price: {bid:.2f}, spread: {spread_percent:.4f}% = {spread_points:.1f} points)")
            else:
                logger.warning(f"{symbol}: Invalid bid price ({bid}) for spread calculation")
                return None
        
        return spread_points
    
    def check_spread(self, symbol: str) -> bool:
        """Check if spread is within acceptable range."""
        # Ignore spread check in test mode if configured
        if self.test_mode and self.test_mode_ignore_spread:
            return True
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # For crypto pairs, check spread as percentage or dollar amount
        # For forex, check as points
        is_crypto = any(crypto in symbol.upper() for crypto in ['BTC', 'ETH', 'XRP', 'ADA', 'BCH', 'LTC', 'BNB', 'BAT', 'DOGE', 'DOT', 'LINK', 'UNI'])
        
        if is_crypto:
            # For crypto: check bid-ask spread as percentage (max 2%)
            bid = symbol_info.get('bid', 0)
            ask = symbol_info.get('ask', 0)
            if bid > 0:
                spread_percent = ((ask - bid) / bid) * 100
                max_spread_percent = 2.0  # Max 2% spread for crypto
                if spread_percent > max_spread_percent:
                    logger.debug(f"{symbol}: Crypto spread {spread_percent:.2f}% exceeds maximum {max_spread_percent}%")
                    return False
                return True
            return False
        else:
            # For forex: check spread in points
            spread_points = self.get_spread_points(symbol)
            if spread_points is None:
                return False
            
            # Sanity check: if spread is unreasonably high (likely calculation error), use bid-ask instead
            if spread_points > 100000:  # More than 100k points is clearly wrong
                bid = symbol_info.get('bid', 0)
                ask = symbol_info.get('ask', 0)
                if bid > 0:
                    # Calculate spread as percentage instead
                    spread_percent = ((ask - bid) / bid) * 100
                    if spread_percent > 2.0:  # Max 2% for sanity check
                        logger.debug(f"{symbol}: Spread calculation error (points: {spread_points:.0f}), using percentage: {spread_percent:.2f}% (exceeds 2%)")
                        return False
                    else:
                        logger.debug(f"{symbol}: Spread calculation error (points: {spread_points:.0f}), using percentage: {spread_percent:.2f}% (OK)")
                        return True
            
            if spread_points > self.max_spread_points:
                logger.debug(f"{symbol}: Spread {spread_points:.1f} points exceeds maximum {self.max_spread_points}")
                return False
            
            return True
    
    def check_commission(self, symbol: str, lot_size: float = 0.01) -> bool:
        """
        Check if commission is within acceptable range.
        Note: Commission calculation may vary by broker.
        """
        # Ignore commission check in test mode if configured
        if self.test_mode and self.test_mode_ignore_commission:
            return True
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Try to get commission from account info
        account_info = self.mt5_connector.get_account_info()
        if account_info:
            # For Exness, commission is usually built into spread
            # If broker charges separate commission, check here
            # For now, assume commission is acceptable if spread is low
            pass
        
        logger.debug(f"{symbol}: Commission check passed")
        return True
    
    def get_commission_estimate(self, symbol: str, lot_size: float = 0.01) -> float:
        """Get estimated commission for symbol (for prioritization)."""
        # For Exness, commission is usually in spread
        # Return 0 for now (spread is already checked)
        return 0.0
    
    def is_tradeable(self, symbol: str, check_halal: bool = True) -> bool:
        """
        Check if symbol meets all criteria for trading.
        
        Returns True if:
        - Symbol is in allowed list (or auto-discover enabled)
        - Not an exotic pair (if exotics excluded)
        - Spread is within limits
        - Commission is acceptable
        - Symbol has market data available
        - Symbol is tradeable (mode 4)
        """
        # In test mode with ignore restrictions, only check basic tradeability
        if self.test_mode and self.test_mode_ignore_restrictions:
            # Only check if symbol has market data (skip trade_mode check in backtest)
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                logger.debug(f"{symbol}: No symbol info available")
                return False
            
            # In backtest mode, we don't need to check trade_mode (historical data is always available)
            # Just verify we can get symbol info
            logger.debug(f"{symbol}: Test mode - symbol info available, allowing trade")
            return True
        
        # Normal mode - check all criteria
        # Check if symbol is allowed (if list specified and not auto-discover)
        if self.allowed_symbols and not self.auto_discover_symbols:
            if not self.is_allowed_symbol(symbol):
                logger.debug(f"{symbol}: Not in allowed symbols list")
                return False
        
        # Check if exotic
        if self.is_exotic(symbol):
            logger.debug(f"{symbol}: Exotic pair excluded")
            return False
        
        # Check if symbol has market data
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Check if symbol is tradeable (mode 4 = enabled)
        trade_mode = symbol_info.get('trade_mode', 0)
        if trade_mode != 4:
            logger.debug(f"{symbol}: Not tradeable (mode: {trade_mode})")
            return False
        
        # Try to get rates to verify market data is available
        import MetaTrader5 as mt5
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
        if rates is None or len(rates) == 0:
            logger.debug(f"{symbol}: No market data available")
            return False
        
        # Check spread
        if not self.check_spread(symbol):
            logger.debug(f"{symbol}: Spread check failed")
            return False
        
        # Check commission
        if not self.check_commission(symbol):
            logger.debug(f"{symbol}: Commission check failed")
            return False
        
        return True
    
    def get_tradeable_symbols(self) -> List[str]:
        """Get list of all tradeable symbols with prioritization."""
        if not self.mt5_connector.ensure_connected():
            return []
        
        tradeable = []
        symbol_scores = {}  # For prioritization
        
        # Determine symbols to check
        if self.allowed_symbols and not self.auto_discover_symbols:
            symbols_to_check = self.allowed_symbols
        else:
            # Get all symbols from MT5
            symbols = mt5.symbols_get()
            if symbols is None:
                return []
            symbols_to_check = [s.name for s in symbols]
        
        # In test mode with ignore restrictions, use allowed symbols directly if available
        if self.test_mode and self.test_mode_ignore_restrictions and self.allowed_symbols:
            # Just verify symbols have market data, skip all other filters
            for symbol in self.allowed_symbols:
                symbol_info = self.mt5_connector.get_symbol_info(symbol)
                if symbol_info is not None:
                    tradeable.append(symbol)
                    symbol_scores[symbol] = 0  # No prioritization in test mode
        else:
            # Check each symbol
            for symbol in symbols_to_check:
                if self.is_tradeable(symbol, check_halal=False):  # Halal check done separately
                    tradeable.append(symbol)
                
                # Calculate priority score (lower is better)
                score = 0
                spread_points = self.get_spread_points(symbol)
                if spread_points:
                    score += spread_points  # Lower spread = lower score
                
                # Prioritize gold (XAUUSD) and major pairs
                if 'XAU' in symbol.upper():
                    score -= 100  # Gold gets high priority
                elif any(major in symbol.upper() for major in ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD']):
                    score -= 50  # Major pairs get medium priority
                
                symbol_scores[symbol] = score
        
        # Sort by priority (lowest score first)
        if self.prioritize_low_fee and symbol_scores:
            tradeable.sort(key=lambda s: symbol_scores.get(s, 999))
        
        if self.test_mode:
            logger.info(f"[TEST] TEST MODE: Found {len(tradeable)} tradeable symbols (all restrictions ignored)")
            if len(tradeable) > 0:
                logger.info(f"   Sample symbols: {', '.join(tradeable[:10])}")
        else:
            logger.info(f"Found {len(tradeable)} tradeable symbols" + 
                       (f" (prioritized: {', '.join(tradeable[:5])})" if tradeable else ""))
        
        return tradeable

