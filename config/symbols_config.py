"""
Symbol Risk Profiles Configuration
Provides symbol-specific risk parameters for accurate risk calculations.
"""

from typing import Dict, Any, Optional, Tuple


class SymbolRiskProfiles:
    """Manages symbol-specific risk profiles and configurations."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize symbol risk profiles from config.
        
        Args:
            config: Configuration dictionary with symbol_limits and risk settings
        """
        self.config = config
        self.risk_config = config.get('risk', {})
        self.symbol_limits = self.risk_config.get('symbol_limits', {})
        self.max_risk_usd = self.risk_config.get('max_risk_per_trade_usd', 2.0)
        
        # Default symbol profile template
        self.default_profile = {
            'spread_limit_points': 5000,
            'spread_limit_percent': None,
            'pip_value_multiplier': 10,  # For 5-digit symbols
            'contract_size': 1.0,
            'freeze_level': 0,
            'min_lot_risk_validation': True,
            'slippage_buffer': 1.10  # 10% buffer
        }
    
    def get_symbol_profile(self, symbol: str) -> Dict[str, Any]:
        """
        Get risk profile for a specific symbol.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Dictionary with symbol risk profile parameters
        """
        symbol_upper = symbol.upper()
        symbol_config = self.symbol_limits.get(symbol_upper, {})
        
        # Start with default profile
        profile = self.default_profile.copy()
        
        # Override with symbol-specific config if available
        if symbol_config:
            profile.update({
                'min_lot': symbol_config.get('min_lot'),
                'max_lot': symbol_config.get('max_lot'),
                'spread_limit_points': symbol_config.get('spread_limit_points', profile['spread_limit_points']),
                'spread_limit_percent': symbol_config.get('spread_limit_percent'),
                'slippage_buffer': symbol_config.get('slippage_buffer', profile['slippage_buffer'])
            })
        
        return profile
    
    def validate_min_lot_risk(
        self,
        symbol: str,
        min_lot: float,
        stop_loss_pips: float,
        pip_value: float,
        contract_size: float
    ) -> Tuple[bool, float, str]:
        """
        Validate that minimum lot size doesn't cause risk to exceed $2.00.
        
        Args:
            symbol: Trading symbol
            min_lot: Minimum lot size
            stop_loss_pips: Stop loss in pips
            pip_value: Pip value in price units
            contract_size: Contract size
        
        Returns:
            Tuple of (is_valid, actual_risk, reason)
        """
        stop_loss_price = stop_loss_pips * pip_value
        actual_risk = min_lot * stop_loss_price * contract_size
        
        # Apply slippage buffer
        profile = self.get_symbol_profile(symbol)
        slippage_buffer = profile.get('slippage_buffer', 1.10)
        risk_with_buffer = actual_risk * slippage_buffer
        
        if risk_with_buffer > self.max_risk_usd:
            return False, risk_with_buffer, f"Min lot {min_lot:.4f} causes risk ${risk_with_buffer:.2f} > ${self.max_risk_usd:.2f}"
        
        return True, risk_with_buffer, f"Min lot {min_lot:.4f} risk ${risk_with_buffer:.2f} <= ${self.max_risk_usd:.2f}"
    
    def get_spread_limit(self, symbol: str) -> Optional[float]:
        """
        Get spread limit for symbol (in points or percent).
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Spread limit in points, or None if not configured
        """
        profile = self.get_symbol_profile(symbol)
        return profile.get('spread_limit_points')
    
    def get_slippage_buffer(self, symbol: str) -> float:
        """
        Get slippage buffer for symbol.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Slippage buffer multiplier (default 1.10 = 10%)
        """
        profile = self.get_symbol_profile(symbol)
        return profile.get('slippage_buffer', 1.10)

