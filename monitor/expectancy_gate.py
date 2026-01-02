"""
Expectancy Gate - Micro-HFT Expectancy Validation
Validates Micro-HFT expectancy before allowing strategy changes.
"""

from typing import Dict, Any, Tuple
from utils.logger_factory import get_logger

logger = get_logger("expectancy_gate", "logs/live/system/expectancy_gate.log")


class ExpectancyGate:
    """Validates Micro-HFT expectancy before allowing strategy changes."""
    
    # Thresholds for position size scaling
    MIN_EXPECTANCY_PER_TRADE_SCALE = 0.10  # $0.10
    MIN_EXPECTANCY_PER_HOUR_SCALE = 0.50   # $0.50
    MIN_WIN_RATE_SCALE = 0.80              # 80%
    MIN_RISK_REWARD_SCALE = 0.3            # 1:0.3
    MIN_TRADES_SCALE = 100
    MIN_DAYS_SCALE = 7
    MIN_SYMBOLS_WITH_GOOD_EXPECTANCY = 3
    MIN_SYMBOL_EXPECTANCY = 0.08           # $0.08 per symbol
    
    # Thresholds for enabling runners
    MIN_EXPECTANCY_PER_TRADE_RUNNERS = 0.15  # $0.15
    MIN_EXPECTANCY_PER_HOUR_RUNNERS = 1.00   # $1.00
    MIN_WIN_RATE_RUNNERS = 0.82              # 82%
    MIN_RISK_REWARD_RUNNERS = 0.4             # 1:0.4
    MIN_TRADES_RUNNERS = 200
    MIN_DAYS_RUNNERS = 14
    MIN_SYMBOLS_WITH_GOOD_EXPECTANCY_RUNNERS = 5
    MIN_SYMBOL_EXPECTANCY_RUNNERS = 0.12      # $0.12 per symbol
    
    # Thresholds for proceeding past Phase 4
    MIN_EXPECTANCY_PER_TRADE_PHASE5 = 0.05    # $0.05 (positive)
    MIN_EXPECTANCY_PER_HOUR_PHASE5 = 0.30     # $0.30
    MIN_WIN_RATE_PHASE5 = 0.78                # 78%
    MIN_RISK_REWARD_PHASE5 = 0.25             # 1:0.25
    MIN_TRADES_PHASE5 = 50
    MIN_DAYS_PHASE5 = 7
    
    def calculate_expectancy_per_trade(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate expectancy per trade.
        
        Formula: E_trade = (Win_Rate × Avg_Win) - ((1 - Win_Rate) × |Avg_Loss|)
        """
        if metrics.get('total_trades', 0) == 0:
            return 0.0
        
        win_rate = metrics.get('wins', 0) / metrics['total_trades']
        avg_win = metrics.get('total_profit', 0) / metrics.get('wins', 1) if metrics.get('wins', 0) > 0 else 0
        avg_loss = abs(metrics.get('total_loss', 0) / metrics.get('losses', 1)) if metrics.get('losses', 0) > 0 else 0
        
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        return expectancy
    
    def calculate_expectancy_per_hour(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate expectancy per hour.
        
        Formula: E_hour = E_trade × Trades_Per_Hour
        """
        expectancy_trade = self.calculate_expectancy_per_trade(metrics)
        hours_active = metrics.get('hours_active', 1)
        if hours_active <= 0:
            hours_active = 1
        
        trades_per_hour = metrics.get('total_trades', 0) / hours_active
        expectancy_hour = expectancy_trade * trades_per_hour
        
        return expectancy_hour
    
    def calculate_symbol_expectancy(self, symbol_metrics: Dict[str, Any]) -> float:
        """
        Calculate symbol-level expectancy.
        
        Formula: E_symbol = Σ(E_trade_i) / N_trades
        """
        if symbol_metrics.get('trade_count', 0) == 0:
            return 0.0
        
        win_rate = symbol_metrics.get('wins', 0) / symbol_metrics['trade_count']
        avg_win = symbol_metrics.get('total_profit', 0) / symbol_metrics.get('wins', 1) if symbol_metrics.get('wins', 0) > 0 else 0
        avg_loss = abs(symbol_metrics.get('total_loss', 0) / symbol_metrics.get('losses', 1)) if symbol_metrics.get('losses', 0) > 0 else 0
        
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        return expectancy
    
    def can_scale_position_size(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if position size scaling is allowed.
        
        Requirements:
        - Expectancy per Trade: ≥ $0.10
        - Expectancy per Hour: ≥ $0.50
        - Win Rate: ≥ 80%
        - Risk:Reward Ratio: ≥ 1:0.3
        - Minimum Trades: 100 trades over 7 days
        - Symbol-Level Validation: At least 3 symbols with E_symbol ≥ $0.08
        """
        # Check trade count
        if metrics.get('total_trades', 0) < self.MIN_TRADES_SCALE:
            return False, f"Insufficient trades: {metrics.get('total_trades', 0)} < {self.MIN_TRADES_SCALE}"
        
        # Check time period
        if metrics.get('days_active', 0) < self.MIN_DAYS_SCALE:
            return False, f"Insufficient days: {metrics.get('days_active', 0)} < {self.MIN_DAYS_SCALE}"
        
        # Calculate expectancy per trade
        expectancy_trade = self.calculate_expectancy_per_trade(metrics)
        if expectancy_trade < self.MIN_EXPECTANCY_PER_TRADE_SCALE:
            return False, f"Expectancy per trade too low: ${expectancy_trade:.2f} < ${self.MIN_EXPECTANCY_PER_TRADE_SCALE}"
        
        # Calculate expectancy per hour
        expectancy_hour = self.calculate_expectancy_per_hour(metrics)
        if expectancy_hour < self.MIN_EXPECTANCY_PER_HOUR_SCALE:
            return False, f"Expectancy per hour too low: ${expectancy_hour:.2f} < ${self.MIN_EXPECTANCY_PER_HOUR_SCALE}"
        
        # Check win rate
        win_rate = metrics.get('wins', 0) / metrics.get('total_trades', 1)
        if win_rate < self.MIN_WIN_RATE_SCALE:
            return False, f"Win rate too low: {win_rate*100:.1f}% < {self.MIN_WIN_RATE_SCALE*100}%"
        
        # Check risk:reward
        avg_win = metrics.get('total_profit', 0) / metrics.get('wins', 1) if metrics.get('wins', 0) > 0 else 0
        avg_loss = abs(metrics.get('total_loss', 0) / metrics.get('losses', 1)) if metrics.get('losses', 0) > 0 else 0
        
        if avg_loss > 0:
            risk_reward = avg_win / avg_loss
            if risk_reward < self.MIN_RISK_REWARD_SCALE:
                return False, f"Risk:Reward too low: 1:{risk_reward:.2f} < 1:{self.MIN_RISK_REWARD_SCALE}"
        
        # Check symbol-level expectancy
        symbol_metrics_dict = metrics.get('symbol_metrics', {})
        symbols_with_good_expectancy = 0
        
        for symbol, symbol_metrics in symbol_metrics_dict.items():
            symbol_expectancy = self.calculate_symbol_expectancy(symbol_metrics)
            if symbol_expectancy >= self.MIN_SYMBOL_EXPECTANCY:
                symbols_with_good_expectancy += 1
        
        if symbols_with_good_expectancy < self.MIN_SYMBOLS_WITH_GOOD_EXPECTANCY:
            return False, f"Insufficient symbols with good expectancy: {symbols_with_good_expectancy} < {self.MIN_SYMBOLS_WITH_GOOD_EXPECTANCY}"
        
        return True, "All criteria met for position size scaling"
    
    def can_enable_runners(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if runner positions can be enabled.
        
        Requirements:
        - Expectancy per Trade: ≥ $0.15
        - Expectancy per Hour: ≥ $1.00
        - Win Rate: ≥ 82%
        - Risk:Reward Ratio: ≥ 1:0.4
        - Minimum Trades: 200 trades over 14 days
        - Symbol-Level Validation: At least 5 symbols with E_symbol ≥ $0.12
        """
        # Check trade count
        if metrics.get('total_trades', 0) < self.MIN_TRADES_RUNNERS:
            return False, f"Insufficient trades: {metrics.get('total_trades', 0)} < {self.MIN_TRADES_RUNNERS}"
        
        # Check time period
        if metrics.get('days_active', 0) < self.MIN_DAYS_RUNNERS:
            return False, f"Insufficient days: {metrics.get('days_active', 0)} < {self.MIN_DAYS_RUNNERS}"
        
        # Calculate expectancy per trade
        expectancy_trade = self.calculate_expectancy_per_trade(metrics)
        if expectancy_trade < self.MIN_EXPECTANCY_PER_TRADE_RUNNERS:
            return False, f"Expectancy per trade too low: ${expectancy_trade:.2f} < ${self.MIN_EXPECTANCY_PER_TRADE_RUNNERS}"
        
        # Calculate expectancy per hour
        expectancy_hour = self.calculate_expectancy_per_hour(metrics)
        if expectancy_hour < self.MIN_EXPECTANCY_PER_HOUR_RUNNERS:
            return False, f"Expectancy per hour too low: ${expectancy_hour:.2f} < ${self.MIN_EXPECTANCY_PER_HOUR_RUNNERS}"
        
        # Check win rate
        win_rate = metrics.get('wins', 0) / metrics.get('total_trades', 1)
        if win_rate < self.MIN_WIN_RATE_RUNNERS:
            return False, f"Win rate too low: {win_rate*100:.1f}% < {self.MIN_WIN_RATE_RUNNERS*100}%"
        
        # Check risk:reward
        avg_win = metrics.get('total_profit', 0) / metrics.get('wins', 1) if metrics.get('wins', 0) > 0 else 0
        avg_loss = abs(metrics.get('total_loss', 0) / metrics.get('losses', 1)) if metrics.get('losses', 0) > 0 else 0
        
        if avg_loss > 0:
            risk_reward = avg_win / avg_loss
            if risk_reward < self.MIN_RISK_REWARD_RUNNERS:
                return False, f"Risk:Reward too low: 1:{risk_reward:.2f} < 1:{self.MIN_RISK_REWARD_RUNNERS}"
        
        # Check symbol-level expectancy
        symbol_metrics_dict = metrics.get('symbol_metrics', {})
        symbols_with_good_expectancy = 0
        
        for symbol, symbol_metrics in symbol_metrics_dict.items():
            symbol_expectancy = self.calculate_symbol_expectancy(symbol_metrics)
            if symbol_expectancy >= self.MIN_SYMBOL_EXPECTANCY_RUNNERS:
                symbols_with_good_expectancy += 1
        
        if symbols_with_good_expectancy < self.MIN_SYMBOLS_WITH_GOOD_EXPECTANCY_RUNNERS:
            return False, f"Insufficient symbols with good expectancy: {symbols_with_good_expectancy} < {self.MIN_SYMBOLS_WITH_GOOD_EXPECTANCY_RUNNERS}"
        
        return True, "All criteria met for enabling runners"
    
    def can_proceed_past_phase4(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if system can proceed past Phase 4 to Phase 5.
        
        Requirements:
        - Expectancy per Trade: ≥ $0.05 (positive)
        - Expectancy per Hour: ≥ $0.30
        - Win Rate: ≥ 78%
        - Risk:Reward Ratio: ≥ 1:0.25
        - Minimum Trades: 50 trades over 7 days
        """
        # Check trade count
        if metrics.get('total_trades', 0) < self.MIN_TRADES_PHASE5:
            return False, f"Insufficient trades: {metrics.get('total_trades', 0)} < {self.MIN_TRADES_PHASE5}"
        
        # Check time period
        if metrics.get('days_active', 0) < self.MIN_DAYS_PHASE5:
            return False, f"Insufficient days: {metrics.get('days_active', 0)} < {self.MIN_DAYS_PHASE5}"
        
        # Calculate expectancy per trade
        expectancy_trade = self.calculate_expectancy_per_trade(metrics)
        if expectancy_trade < self.MIN_EXPECTANCY_PER_TRADE_PHASE5:
            return False, f"Expectancy per trade too low: ${expectancy_trade:.2f} < ${self.MIN_EXPECTANCY_PER_TRADE_PHASE5}"
        
        # Calculate expectancy per hour
        expectancy_hour = self.calculate_expectancy_per_hour(metrics)
        if expectancy_hour < self.MIN_EXPECTANCY_PER_HOUR_PHASE5:
            return False, f"Expectancy per hour too low: ${expectancy_hour:.2f} < ${self.MIN_EXPECTANCY_PER_HOUR_PHASE5}"
        
        # Check win rate
        win_rate = metrics.get('wins', 0) / metrics.get('total_trades', 1)
        if win_rate < self.MIN_WIN_RATE_PHASE5:
            return False, f"Win rate too low: {win_rate*100:.1f}% < {self.MIN_WIN_RATE_PHASE5*100}%"
        
        # Check risk:reward
        avg_win = metrics.get('total_profit', 0) / metrics.get('wins', 1) if metrics.get('wins', 0) > 0 else 0
        avg_loss = abs(metrics.get('total_loss', 0) / metrics.get('losses', 1)) if metrics.get('losses', 0) > 0 else 0
        
        if avg_loss > 0:
            risk_reward = avg_win / avg_loss
            if risk_reward < self.MIN_RISK_REWARD_PHASE5:
                return False, f"Risk:Reward too low: 1:{risk_reward:.2f} < 1:{self.MIN_RISK_REWARD_PHASE5}"
        
        return True, "All criteria met for proceeding to Phase 5"

