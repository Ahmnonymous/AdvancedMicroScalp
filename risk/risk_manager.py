"""
Risk Management Module
Handles position sizing, risk calculation, and trailing stops.
"""

import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from utils.logger_factory import get_logger

logger = get_logger("risk_manager", "logs/engine/risk_manager.log")


class RiskManager:
    """Manages risk per trade and trailing stop logic."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector, order_manager: OrderManager):
        self.config = config
        self.risk_config = config.get('risk', {})
        self.mt5_connector = mt5_connector
        self.order_manager = order_manager
        
        self.max_risk_usd = self.risk_config.get('max_risk_per_trade_usd', 2.0)
        self.default_lot_size = self.risk_config.get('default_lot_size', 0.01)
        self.max_open_trades = self.risk_config.get('max_open_trades', 1)
        self.trailing_increment_usd = self.risk_config.get('trailing_stop_increment_usd', 0.10)
        self.min_stop_loss_pips = self.risk_config.get('min_stop_loss_pips', 10)
        
        # Per-symbol lot size limits
        self.symbol_limits = self.risk_config.get('symbol_limits', {})
        
        # Continuous trailing stop configuration
        self.continuous_trailing_enabled = self.risk_config.get('continuous_trailing_enabled', True)
        self.trailing_cycle_interval = self.risk_config.get('trailing_cycle_interval_seconds', 1.0)
        self.big_jump_threshold_usd = self.risk_config.get('big_jump_threshold_usd', 0.40)
        
        # Staged open configuration
        self.staged_open_enabled = self.risk_config.get('staged_open_enabled', False)
        self.staged_open_window_seconds = self.risk_config.get('staged_open_window_seconds', 60)
        self.staged_quality_threshold = self.risk_config.get('staged_quality_threshold', 50.0)
        self.staged_min_profit_usd = self.risk_config.get('staged_min_profit_usd', -0.10)
        
        # Fast trailing configuration
        self.fast_trailing_threshold_usd = self.risk_config.get('fast_trailing_threshold_usd', 0.10)
        self.fast_trailing_interval_ms = self.risk_config.get('fast_trailing_interval_ms', 300)
        self.fast_trailing_debounce_cycles = self.risk_config.get('fast_trailing_debounce_cycles', 3)
        
        # Elastic trailing configuration
        elastic_config = self.risk_config.get('elastic_trailing', {})
        self.elastic_trailing_enabled = elastic_config.get('enabled', False)
        self.pullback_tolerance_pct = elastic_config.get('pullback_tolerance_pct', 0.40)
        self.min_lock_increment_usd = elastic_config.get('min_lock_increment_usd', 0.10)
        self.big_jump_lock_margin_usd = elastic_config.get('big_jump_lock_margin_usd', 0.10)
        self.max_peak_lock_usd = elastic_config.get('max_peak_lock_usd', 0.80)
        
        # Thread-safe tracking of last profit per position (enhanced for SETE)
        self._position_tracking = {}  # {ticket: {'last_profit': float, 'last_sl_profit': float, 'peak_profit': float, 'lock': threading.Lock, 'fast_polling': bool, 'debounce_count': int}}
        self._tracking_lock = threading.Lock()
        
        # Staged trade tracking (for multi-trade logic)
        self._staged_trades = {}  # {symbol: {'trades': [ticket1, ticket2, ...], 'first_trade_time': datetime, 'trend': 'LONG'|'SELL', 'lock': threading.Lock}}
        self._staged_lock = threading.Lock()
        
        # Portfolio risk limit configuration
        self.max_portfolio_risk_pct = self.risk_config.get('max_portfolio_risk_pct', 15.0)  # Default 15% of account balance
        self.max_portfolio_risk_usd = self.risk_config.get('max_portfolio_risk_usd', None)  # Absolute USD limit (optional)
        
        # Micro-HFT Profit Engine (optional add-on, initialized separately)
        self._micro_profit_engine = None
    
    def set_micro_profit_engine(self, micro_profit_engine):
        """
        Set the Micro-HFT Profit Engine instance.
        
        This allows the bot to inject the micro profit engine without
        modifying the core risk manager logic.
        
        Args:
            micro_profit_engine: MicroProfitEngine instance
        """
        self._micro_profit_engine = micro_profit_engine
        logger.info("Micro-HFT Profit Engine registered with RiskManager")
    
    def calculate_minimum_lot_size_for_risk(
        self,
        symbol: str,
        stop_loss_pips: float,
        risk_usd: Optional[float] = None
    ) -> Tuple[float, float, str]:
        """
        Calculate minimum possible lot size based on risk per trade, respecting broker's absolute minimum.
        
        This method calculates the lot size needed to achieve the target risk, but ensures
        it never goes below the broker's absolute minimum lot size.
        
        Args:
            symbol: Trading symbol
            stop_loss_pips: Stop loss in pips
            risk_usd: Risk amount in USD (defaults to max_risk_usd)
        
        Returns:
            Tuple of (final_lot_size, calculated_lot, reason)
            - final_lot_size: Lot size to use (max of calculated and broker minimum)
            - calculated_lot: Risk-based calculated lot size
            - reason: Explanation of the lot size decision
        """
        if risk_usd is None:
            risk_usd = self.max_risk_usd
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            logger.warning(f"Cannot get symbol info for {symbol}, using DEFAULT {self.default_lot_size}")
            return self.default_lot_size, self.default_lot_size, "Symbol info unavailable"
        
        # Get point value
        point = symbol_info['point']
        contract_size = symbol_info['contract_size']
        
        # Calculate pip value (for most pairs, 1 pip = 10 points)
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        # Get account currency
        account_info = self.mt5_connector.get_account_info()
        if account_info is None:
            return self.default_lot_size, self.default_lot_size, "Account info unavailable"
        
        account_currency = account_info['currency']
        
        # Calculate lot size based on risk
        # Risk = Lot Size * Stop Loss (in price) * Contract Size
        # Lot Size = Risk / (Stop Loss (in price) * Contract Size)
        
        stop_loss_price = stop_loss_pips * pip_value
        
        # Safety check
        if stop_loss_price <= 0 or contract_size <= 0:
            logger.warning(f"Invalid stop_loss_price ({stop_loss_price}) or contract_size ({contract_size}) for {symbol}")
            return self.default_lot_size, self.default_lot_size, "Invalid stop loss or contract size"
        
        # Calculate lot size needed for target risk (per user requirement: always use minimum lot size)
        # But ensure risk never exceeds $2 USD
        if account_currency == 'USD':
            calculated_lot = risk_usd / (stop_loss_price * contract_size)
        else:
            # Non-USD account - simplified conversion
            calculated_lot = risk_usd / (stop_loss_price * contract_size)
        
        # Ensure calculated lot respects minimum lot size (per user requirement)
        # We'll use the maximum of calculated lot and minimum lot, but cap risk at $2
        
        # Round to valid lot size (respect volume_step)
        volume_step = symbol_info.get('volume_step', 0.01)
        if volume_step > 0:
            calculated_lot = round(calculated_lot / volume_step) * volume_step
        else:
            calculated_lot = round(calculated_lot, 2)
        
        # Ensure calculated lot is not negative or zero
        if calculated_lot <= 0:
            calculated_lot = self.default_lot_size
        
        # Get per-symbol limits from config (if set)
        symbol_upper = symbol.upper()
        symbol_limit_config = self.symbol_limits.get(symbol_upper, {})
        config_min_lot = symbol_limit_config.get('min_lot')
        config_max_lot = symbol_limit_config.get('max_lot')
        
        # Get symbol's native lot size limits from broker
        symbol_min_lot = symbol_info.get('volume_min', 0.01)
        symbol_max_lot = symbol_info.get('volume_max', 100.0)
        
        # Determine effective minimum: use config limit if set, otherwise use broker's minimum
        # Always respect broker's absolute minimum (can't go below broker's minimum)
        if config_min_lot is not None:
            effective_min_lot = max(symbol_min_lot, config_min_lot)
            limit_source = "config"
        else:
            effective_min_lot = symbol_min_lot
            limit_source = "broker"
        
        # Determine effective maximum: use config limit if set, otherwise use broker's maximum
        if config_max_lot is not None:
            effective_max_lot = min(symbol_max_lot, config_max_lot)
        else:
            effective_max_lot = symbol_max_lot
        
        # Use the maximum of calculated lot and effective minimum
        # This ensures we use minimum possible lot while respecting broker's absolute minimum
        # Per user requirement: Always use minimum lot size for the symbol
        final_lot_size = max(calculated_lot, effective_min_lot)
        
        # However, if calculated lot is less than minimum, we must use minimum
        # but verify risk doesn't exceed $2 USD
        if calculated_lot < effective_min_lot:
            # Recalculate actual risk with minimum lot
            actual_risk_with_min = effective_min_lot * stop_loss_price * contract_size
            if actual_risk_with_min > risk_usd:
                logger.warning(f"⚠️ {symbol}: Using minimum lot {effective_min_lot:.4f} results in risk ${actual_risk_with_min:.2f} > ${risk_usd:.2f}")
                # Still use minimum lot (per user requirement), but log the risk
        
        # CRITICAL: Enforce maximum lot limit to prevent invalid volume errors
        if final_lot_size > effective_max_lot:
            logger.warning(f"⚠️ {symbol}: Calculated lot {final_lot_size:.4f} exceeds max {effective_max_lot:.4f}, "
                          f"capping to max. This may result in risk < ${risk_usd:.2f}")
            final_lot_size = effective_max_lot
            # Recalculate actual risk with capped lot size
            if stop_loss_price > 0 and contract_size > 0:
                actual_risk_capped = final_lot_size * stop_loss_price * contract_size
                logger.warning(f"⚠️ {symbol}: Actual risk with capped lot: ${actual_risk_capped:.2f} (target: ${risk_usd:.2f})")
        
        # Calculate actual risk with final lot size
        actual_risk = 0.0
        if stop_loss_price > 0 and contract_size > 0:
            actual_risk = final_lot_size * stop_loss_price * contract_size
        
        # Determine reason
        if calculated_lot < effective_min_lot:
            reason = f"Calculated {calculated_lot:.4f} < min {effective_min_lot:.4f} ({limit_source}), using min"
        elif calculated_lot > effective_max_lot:
            reason = f"Calculated {calculated_lot:.4f} > max {effective_max_lot:.4f}, capped to max (risk: ${actual_risk:.2f})"
        elif calculated_lot > effective_min_lot:
            reason = f"Using calculated {calculated_lot:.4f} (min: {effective_min_lot:.4f} {limit_source}, max: {effective_max_lot:.4f})"
        else:
            reason = f"Using minimum {effective_min_lot:.4f} ({limit_source})"
        
        return final_lot_size, calculated_lot, reason
    
    def choose_lot_for_trade(
        self,
        symbol: str,
        risk_usd: float,
        stop_loss_pips: float,
        force_minimum_lot_mode: bool = True
    ) -> Tuple[float, str]:
        """
        Helper function to choose lot size for a trade.
        
        Args:
            symbol: Trading symbol
            risk_usd: Risk amount in USD (typically $2.0)
            stop_loss_pips: Stop loss in pips
            force_minimum_lot_mode: If True, ensures lot is at least broker minimum
        
        Returns:
            Tuple of (lot_size, reason)
            - lot_size: Final lot size to use
            - reason: Explanation of lot size decision
        """
        final_lot_size, calculated_lot, reason = self.calculate_minimum_lot_size_for_risk(
            symbol, stop_loss_pips, risk_usd
        )
        
        # In force_minimum_lot_mode, ensure we never go below broker minimum
        if force_minimum_lot_mode:
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info:
                broker_min_lot = symbol_info.get('volume_min', 0.01)
                if final_lot_size < broker_min_lot:
                    final_lot_size = broker_min_lot
                    reason = f"Forced to broker minimum {broker_min_lot:.4f} (calculated: {calculated_lot:.4f})"
        
        return final_lot_size, reason
    
    def calculate_lot_size(
        self,
        symbol: str,
        stop_loss_pips: float,
        risk_usd: Optional[float] = None
    ) -> float:
        """
        Calculate lot size - returns minimum possible lot size based on risk, respecting broker minimum.
        
        This method calculates the lot size needed to achieve target risk ($2.0), but ensures
        it never goes below the broker's absolute minimum lot size.
        
        Args:
            symbol: Trading symbol
            stop_loss_pips: Stop loss in pips
            risk_usd: Risk amount in USD (defaults to max_risk_usd)
        
        Returns:
            Minimum possible lot size (max of risk-based calculation and broker minimum)
        """
        final_lot_size, calculated_lot, reason = self.calculate_minimum_lot_size_for_risk(
            symbol, stop_loss_pips, risk_usd
        )
        
        # Log comprehensive lot size details
        logger.info(f"📦 {symbol}: LOT SIZE | "
                   f"Final: {final_lot_size:.4f} | "
                   f"Calculated: {calculated_lot:.4f} | "
                   f"Risk: ${risk_usd:.2f} | "
                   f"SL: {stop_loss_pips:.1f} pips | "
                   f"Reason: {reason}")
        
        return final_lot_size
    
    def check_min_lot_size_for_testing(self, symbol: str) -> Tuple[bool, float, str]:
        """
        Check if symbol's minimum lot size is suitable for testing mode.
        
        Requirements:
        - Min lot size must be between 0.01 and 0.1
        - Min lot size must not cause risk to exceed $2.0
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Tuple of (is_valid, min_lot, reason)
            - is_valid: True if symbol passes testing mode lot size requirements
            - min_lot: The minimum lot size for this symbol
            - reason: Reason for pass/fail
        """
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False, 0.0, "Cannot get symbol info"
        
        # Get per-symbol limits from config (if set)
        symbol_upper = symbol.upper()
        symbol_limit_config = self.symbol_limits.get(symbol_upper, {})
        config_min_lot = symbol_limit_config.get('min_lot')
        
        # Get symbol's native minimum lot size from broker
        symbol_min_lot = symbol_info.get('volume_min', 0.01)
        
        # Determine effective minimum
        if config_min_lot is not None:
            effective_min_lot = max(symbol_min_lot, config_min_lot)
            limit_source = "config"
        else:
            effective_min_lot = symbol_min_lot
            limit_source = "broker"
        
        # Check 1: Min lot must be between 0.01 and 0.1
        if effective_min_lot < 0.01:
            return False, effective_min_lot, f"Min lot {effective_min_lot:.4f} < 0.01 (too small for testing)"
        
        if effective_min_lot > 0.1:
            return False, effective_min_lot, f"Min lot {effective_min_lot:.4f} > 0.1 (too large for testing)"
        
        # Check 2: Min lot must not cause risk to exceed $2.0
        # We need to estimate risk with minimum lot size
        # Use a typical stop loss (min_stop_loss_pips) to estimate
        point = symbol_info.get('point', 0.00001)
        pip_value = point * 10 if symbol_info.get('digits', 5) == 5 or symbol_info.get('digits', 3) == 3 else point
        contract_size = symbol_info.get('contract_size', 1.0)
        
        # Estimate risk with minimum lot and minimum stop loss
        min_stop_loss_price = self.min_stop_loss_pips * pip_value
        if min_stop_loss_price > 0 and contract_size > 0:
            estimated_risk = effective_min_lot * min_stop_loss_price * contract_size
            if estimated_risk > self.max_risk_usd:
                return False, effective_min_lot, f"Min lot {effective_min_lot:.4f} would cause risk ${estimated_risk:.2f} > ${self.max_risk_usd:.2f} (exceeds max risk per trade)"
        
        return True, effective_min_lot, f"Min lot {effective_min_lot:.4f} ({limit_source}) passes testing requirements"
    
    def calculate_spread_and_fees_cost(self, symbol: str, lot_size: float) -> Tuple[float, str]:
        """
        Calculate total cost (spread + fees) for a trade in USD.
        
        Args:
            symbol: Trading symbol
            lot_size: Lot size to use
            
        Returns:
            Tuple of (total_cost_usd, description)
            - total_cost_usd: Total cost in USD (spread + fees)
            - description: Description of cost breakdown
        """
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return 999.0, "Cannot get symbol info"
        
        bid = symbol_info.get('bid', 0)
        ask = symbol_info.get('ask', 0)
        contract_size = symbol_info.get('contract_size', 1.0)
        
        if bid <= 0 or ask <= 0:
            return 999.0, "Invalid bid/ask prices"
        
        # Calculate spread cost
        spread_price = ask - bid
        spread_cost_usd = spread_price * lot_size * contract_size
        
        # Estimate commission (for Exness, usually built into spread, but estimate separately if needed)
        # For now, assume commission is minimal or built into spread
        commission_usd = 0.0  # Can be enhanced if broker charges separate commission
        
        total_cost = spread_cost_usd + commission_usd
        
        description = f"Spread: ${spread_cost_usd:.2f}, Fees: ${commission_usd:.2f}"
        
        return total_cost, description
    
    def calculate_stop_loss_pips(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        order_type: str
    ) -> float:
        """Calculate stop loss in pips."""
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return 0
        
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        if order_type == 'BUY':
            price_diff = entry_price - stop_loss_price
        else:  # SELL
            price_diff = stop_loss_price - entry_price
        
        pips = price_diff / pip_value
        return abs(pips)
    
    def can_open_trade(
        self,
        symbol: Optional[str] = None,
        signal: Optional[str] = None,
        quality_score: Optional[float] = None,
        high_quality_setup: bool = False
    ) -> Tuple[bool, str]:
        """
        Check if we can open a new trade (with staged open support).
        
        Args:
            symbol: Trading symbol (required for staged open)
            signal: 'LONG' or 'SHORT' (required for staged open)
            quality_score: Quality score (0-100) for staged open validation
            high_quality_setup: Legacy parameter for high quality override
        
        Returns:
            (can_open: bool, reason: str)
        """
        current_positions = self.order_manager.get_position_count()
        
        # If we're below max, we can always open
        if current_positions < self.max_open_trades:
            return True, "Below max open trades limit"
        
        # If we're at max and staged open is disabled, check override
        if not self.staged_open_enabled:
            if high_quality_setup and self.risk_config.get('high_quality_setup_override', True):
                return True, "High quality setup override"
            return False, f"Max open trades ({self.max_open_trades}) reached"
        
        # Staged open logic: check if we can open additional trade within window
        if symbol is None or signal is None:
            return False, "Symbol and signal required for staged open"
        
        # Get existing positions for this symbol
        positions = self.order_manager.get_open_positions()
        symbol_positions = [p for p in positions if p['symbol'] == symbol]
        
        if len(symbol_positions) >= self.max_open_trades:
            return False, f"Max trades for {symbol} reached"
        
        # Check if we have any staged trades for this symbol
        with self._staged_lock:
            staged_info = self._staged_trades.get(symbol, None)
            
            if staged_info is None:
                # First trade for this symbol - can open if we have room
                if current_positions < self.max_open_trades:
                    return True, "First trade for symbol"
                return False, "Max open trades reached (no staged window)"
            
            # Check if trend matches
            if staged_info['trend'] != signal:
                return False, f"Trend mismatch: existing {staged_info['trend']}, new {signal}"
            
            # Check if within staged window
            time_since_first = (datetime.now() - staged_info['first_trade_time']).total_seconds()
            if time_since_first > self.staged_open_window_seconds:
                return False, f"Staged window expired ({time_since_first:.0f}s > {self.staged_open_window_seconds}s)"
            
            # Check quality score
            if quality_score is not None and quality_score < self.staged_quality_threshold:
                return False, f"Quality score {quality_score:.1f} < threshold {self.staged_quality_threshold}"
            
            # Check if existing trades meet profit requirement (relaxed for medium-frequency trading)
            existing_tickets = staged_info['trades']
            if existing_tickets:
                # Get first trade profit
                first_trade = self.order_manager.get_position_by_ticket(existing_tickets[0])
                if first_trade:
                    first_profit = first_trade.get('profit', 0.0)
                    # Only block if first trade is significantly losing (more than $0.50)
                    # This allows recovery trades and doesn't block small losses
                    if first_profit < self.staged_min_profit_usd:
                        return False, f"First trade profit ${first_profit:.2f} < minimum ${self.staged_min_profit_usd}"
            
            # Check if we can open another staged trade
            if len(existing_tickets) >= self.max_open_trades:
                return False, f"Max staged trades ({self.max_open_trades}) for {symbol} reached"
            
            return True, f"Staged open allowed (window: {time_since_first:.0f}s, trades: {len(existing_tickets)})"
    
    def check_portfolio_risk(self, new_trade_risk_usd: float = 0.0) -> Tuple[bool, str]:
        """
        Check if adding new trade would exceed portfolio risk limit.
        
        Args:
            new_trade_risk_usd: Risk amount in USD for the new trade being considered
        
        Returns:
            (allowed: bool, reason: str)
        """
        # Get account balance
        account_info = self.mt5_connector.get_account_info()
        if account_info is None:
            return False, "Cannot get account info for portfolio risk check"
        
        account_balance = account_info.get('balance', 0.0)
        if account_balance <= 0:
            return False, "Invalid account balance"
        
        # Get all open positions and calculate total risk
        positions = self.order_manager.get_open_positions()
        total_risk_usd = 0.0
        
        for position in positions:
            symbol = position.get('symbol')
            if not symbol:
                continue
            
            # Get position's stop loss distance
            entry_price = position.get('price_open', 0)
            sl_price = position.get('sl', 0)
            lot_size = position.get('volume', 0)
            order_type = position.get('type')
            
            if entry_price <= 0 or lot_size <= 0:
                continue
            
            # Get symbol info for calculations
            symbol_info = self.mt5_connector.get_symbol_info(symbol, check_price_staleness=False)
            if symbol_info is None:
                continue
            
            point = symbol_info.get('point', 0.00001)
            pip_value = point * 10 if symbol_info.get('digits', 5) == 5 or symbol_info.get('digits', 3) == 3 else point
            contract_size = symbol_info.get('contract_size', 1.0)
            
            # Calculate stop loss distance in price
            if order_type == 'BUY' and sl_price > 0:
                sl_distance_price = entry_price - sl_price
            elif order_type == 'SELL' and sl_price > 0:
                sl_distance_price = sl_price - entry_price
            else:
                # No SL set, use default min stop loss
                sl_distance_price = self.min_stop_loss_pips * pip_value
            
            # Calculate risk for this position
            position_risk = lot_size * abs(sl_distance_price) * contract_size
            total_risk_usd += position_risk
        
        # Add new trade risk
        total_risk_with_new = total_risk_usd + new_trade_risk_usd
        
        # Check absolute USD limit first (if set)
        if self.max_portfolio_risk_usd is not None:
            if total_risk_with_new > self.max_portfolio_risk_usd:
                return False, f"Portfolio risk ${total_risk_with_new:.2f} would exceed absolute limit ${self.max_portfolio_risk_usd:.2f} (current: ${total_risk_usd:.2f})"
        
        # Check percentage limit
        portfolio_risk_pct = (total_risk_with_new / account_balance) * 100
        if portfolio_risk_pct > self.max_portfolio_risk_pct:
            return False, f"Portfolio risk {portfolio_risk_pct:.1f}% would exceed limit {self.max_portfolio_risk_pct}% (current: ${total_risk_usd:.2f}, new: ${total_risk_with_new:.2f}, balance: ${account_balance:.2f})"
        
        return True, f"Portfolio risk OK ({portfolio_risk_pct:.1f}% <= {self.max_portfolio_risk_pct}%, ${total_risk_with_new:.2f})"
    
    def update_trailing_stop(
        self,
        ticket: int,
        current_profit_usd: float
    ) -> bool:
        """
        Update trailing stop based on profit increments.
        
        Trailing stop logic (user requirement):
        - For every $0.10 increase in profit, move SL +$0.10 behind the price
        - Profit +0.10 → SL at +0.00 (breakeven)
        - Profit +0.20 → SL at +0.10
        - Profit +0.30 → SL at +0.20
        - Continue until hit or manually closed
        """
        position = self.order_manager.get_position_by_ticket(ticket)
        if position is None:
            return False
        
        # Need at least $0.10 profit to start trailing
        if current_profit_usd < self.trailing_increment_usd:
            return False
        
        # Calculate target SL profit: floor(profit / 0.10) * 0.10 - 0.10
        # This moves SL $0.10 behind price for every $0.10 profit
        increment_level = int(current_profit_usd / self.trailing_increment_usd)
        target_sl_profit = (increment_level * self.trailing_increment_usd) - self.trailing_increment_usd
        
        # Ensure target SL is not negative (minimum breakeven)
        if target_sl_profit < 0:
            target_sl_profit = 0
        
        # Calculate new stop loss price
        symbol = position['symbol']
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        entry_price = position['price_open']
        current_price = position['price_current']
        order_type = position['type']
        
        # Calculate price difference needed for target SL profit
        lot_size = position['volume']
        contract_size = symbol_info['contract_size']
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        # Get current profit in account currency (from MT5)
        current_profit_account_currency = position.get('profit', 0)
        
        if abs(current_profit_account_currency) < 0.01:
            return False
        
        # Calculate current price difference that gives current profit
        current_price_diff = abs(current_price - entry_price)
        
        # Calculate price difference needed for target SL profit
        # Profit = Price_Diff * Lot_Size * Contract_Size
        # Price_Diff = Profit / (Lot_Size * Contract_Size)
        if lot_size <= 0 or contract_size <= 0:
            return False
        
        target_price_diff = target_sl_profit / (lot_size * contract_size)
        
        # Safety check
        if target_price_diff < 0:
            return False
        
        # Set new stop loss price
        if order_type == 'BUY':
            # For BUY: SL should be entry_price + target_price_diff (below entry for profit)
            new_sl_price = entry_price + target_price_diff
            # Ensure SL is not above current price (should be below for profit)
            if new_sl_price >= current_price:
                new_sl_price = current_price - (pip_value)  # 1 pip below current
        else:  # SELL
            # For SELL: SL should be entry_price - target_price_diff (above entry for profit)
            new_sl_price = entry_price - target_price_diff
            # Ensure SL is not below current price (should be above for profit)
            if new_sl_price <= current_price:
                new_sl_price = current_price + (pip_value)  # 1 pip above current
        
        # Check if SL needs updating
        current_sl = position['sl']
        if order_type == 'BUY' and new_sl_price > current_sl:
            # Use absolute price for trailing stop
            return self.order_manager.modify_order(ticket, stop_loss_price=new_sl_price)
        elif order_type == 'SELL' and (current_sl == 0 or new_sl_price < current_sl):
            # Use absolute price for trailing stop
            return self.order_manager.modify_order(ticket, stop_loss_price=new_sl_price)
        
        return False
    
    def validate_stop_loss(self, symbol: str, stop_loss_pips: float, entry_price: Optional[float] = None, order_type: Optional[str] = None) -> bool:
        """
        Validate that stop loss meets minimum requirements.
        
        Args:
            symbol: Trading symbol
            stop_loss_pips: Stop loss in pips
            entry_price: Entry price (optional, for distance validation)
            order_type: 'BUY' or 'SELL' (optional, for distance validation)
        """
        if stop_loss_pips < self.min_stop_loss_pips:
            logger.warning(f"Stop loss {stop_loss_pips} pips is below minimum {self.min_stop_loss_pips} pips")
            return False
        
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Check against broker's minimum stops level
        stops_level = symbol_info.get('trade_stops_level', 0)
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        # Convert stops_level to pips
        min_stops_pips = (stops_level * point) / pip_value if pip_value > 0 else 0
        
        # If we have entry price and order type, validate actual distance
        if entry_price and order_type:
            # Calculate SL price
            if order_type == 'BUY':
                sl_price = entry_price - (stop_loss_pips * pip_value)
            else:  # SELL
                sl_price = entry_price + (stop_loss_pips * pip_value)
            
            # Calculate actual distance from entry to SL
            actual_distance = abs(entry_price - sl_price)
            min_distance = stops_level * point
            
            if min_distance > 0 and actual_distance < min_distance:
                logger.warning(f"Stop loss distance {actual_distance:.5f} is less than broker minimum {min_distance:.5f} "
                            f"(stops_level: {stops_level}, min_stops_pips: {min_stops_pips:.1f})")
                return False
        
        # Also check if stop_loss_pips meets minimum stops level in pips
        if min_stops_pips > 0 and stop_loss_pips < min_stops_pips:
            logger.warning(f"Stop loss {stop_loss_pips:.1f} pips is less than broker minimum {min_stops_pips:.1f} pips "
                        f"(stops_level: {stops_level})")
            return False
        
        return True
    
    def _get_position_tracking(self, ticket: int) -> Dict[str, Any]:
        """Get thread-safe position tracking data (enhanced for SETE)."""
        with self._tracking_lock:
            if ticket not in self._position_tracking:
                self._position_tracking[ticket] = {
                    'last_profit': 0.0,
                    'last_sl_profit': 0.0,
                    'peak_profit': 0.0,
                    'lock': threading.Lock(),
                    'fast_polling': False,
                    'debounce_count': 0
                }
            return self._position_tracking[ticket]
    
    def _update_position_tracking(self, ticket: int, last_profit: float, last_sl_profit: float):
        """Update thread-safe position tracking data."""
        with self._tracking_lock:
            if ticket not in self._position_tracking:
                self._position_tracking[ticket] = {
                    'last_profit': last_profit,
                    'last_sl_profit': last_sl_profit,
                    'lock': threading.Lock()
                }
            else:
                self._position_tracking[ticket]['last_profit'] = last_profit
                self._position_tracking[ticket]['last_sl_profit'] = last_sl_profit
    
    def _remove_position_tracking(self, ticket: int):
        """Remove position tracking when position is closed."""
        with self._tracking_lock:
            self._position_tracking.pop(ticket, None)
    
    def register_staged_trade(self, symbol: str, ticket: int, signal: str):
        """Register a staged trade for tracking."""
        with self._staged_lock:
            if symbol not in self._staged_trades:
                self._staged_trades[symbol] = {
                    'trades': [],
                    'first_trade_time': datetime.now(),
                    'trend': signal,
                    'lock': threading.Lock()
                }
            self._staged_trades[symbol]['trades'].append(ticket)
    
    def unregister_staged_trade(self, symbol: str, ticket: int):
        """Unregister a staged trade when closed."""
        with self._staged_lock:
            if symbol in self._staged_trades:
                if ticket in self._staged_trades[symbol]['trades']:
                    self._staged_trades[symbol]['trades'].remove(ticket)
                # Clean up if no more trades
                if not self._staged_trades[symbol]['trades']:
                    self._staged_trades.pop(symbol, None)
    
    def update_continuous_trailing_stop(
        self,
        ticket: int,
        current_profit_usd: float
    ):
        """
        Update trailing stop with Smart Elastic Trailing Engine (SETE).
        
        SETE Logic:
        - Tracks peak profit per position
        - Allows pullback tolerance (default 40% of peak)
        - Uses elastic SL calculation based on peak and pullback
        - Detects big jumps and locks SL immediately
        - Ensures SL only moves forward (never backward)
        
        Args:
            ticket: Position ticket number
            current_profit_usd: Current profit in USD
        
        Returns:
            (success: bool, reason: str)
        """
        position = self.order_manager.get_position_by_ticket(ticket)
        if position is None:
            # Position closed, remove tracking
            self._remove_position_tracking(ticket)
            return False, "Position not found"
        
        # Get thread-safe tracking
        tracking = self._get_position_tracking(ticket)
        
        # Use position-specific lock to prevent race conditions
        with tracking['lock']:
            last_profit = tracking.get('last_profit', 0.0)
            last_sl_profit = tracking.get('last_sl_profit', 0.0)
            peak_profit = tracking.get('peak_profit', 0.0)
            
            # Update peak profit if current is higher
            if current_profit_usd > peak_profit:
                peak_profit = current_profit_usd
                tracking['peak_profit'] = peak_profit
            
            # Need at least minimum increment profit to start trailing
            if current_profit_usd < self.min_lock_increment_usd:
                tracking['last_profit'] = current_profit_usd
                # Reset fast polling if profit drops below threshold
                if tracking.get('fast_polling', False):
                    tracking['fast_polling'] = False
                    tracking['debounce_count'] = 0
                return False, "Profit below minimum threshold"
            
            # Enable fast polling if profit >= threshold
            if current_profit_usd >= self.fast_trailing_threshold_usd:
                if not tracking.get('fast_polling', False):
                    tracking['fast_polling'] = True
                    tracking['debounce_count'] = 0
            else:
                # Debounce fast polling disable
                if tracking.get('fast_polling', False):
                    tracking['debounce_count'] = tracking.get('debounce_count', 0) + 1
                    if tracking['debounce_count'] >= self.fast_trailing_debounce_cycles:
                        tracking['fast_polling'] = False
                        tracking['debounce_count'] = 0
            
            # Smart Elastic Trailing Engine (SETE) logic
            if self.elastic_trailing_enabled:
                # Calculate floor increment lock (base incremental logic)
                increment_level = int(current_profit_usd / self.min_lock_increment_usd)
                floor_lock = max(0.0, (increment_level * self.min_lock_increment_usd) - self.min_lock_increment_usd)
                
                # Calculate elastic lock based on peak and pullback tolerance
                allowed_pullback = peak_profit * self.pullback_tolerance_pct
                elastic_lock = max(floor_lock, peak_profit - allowed_pullback)
                
                # Big jump detection
                profit_increase = current_profit_usd - last_profit
                is_big_jump = profit_increase > self.big_jump_threshold_usd
                
                if is_big_jump:
                    # Immediate lock for big jumps: lock 75% of peak profit
                    big_jump_lock_pct = 0.75  # Lock 75% of peak profit on big jumps
                    target_lock = peak_profit * big_jump_lock_pct
                    # Ensure we don't go below elastic lock
                    target_lock = max(elastic_lock, target_lock)
                    reason = f"Big jump detected (+${profit_increase:.2f}), locking 75% of peak (${peak_profit:.2f} → ${target_lock:.2f})"
                    # Log big jump to root (critical event) - get symbol from position
                    symbol = position['symbol']
                    root_logger = logging.getLogger()
                    root_logger.info(f"🚀 BIG JUMP: {symbol} Ticket {ticket} | ${last_profit:.2f} → ${current_profit_usd:.2f} (+${profit_increase:.2f})")
                else:
                    # Normal elastic trailing
                    target_lock = elastic_lock
                    reason = f"Elastic trailing (peak: ${peak_profit:.2f}, pullback: {self.pullback_tolerance_pct*100:.0f}%)"
                
                # Apply max peak lock cap - ensure we don't lock more than max_peak_lock_usd
                # This allows profits to grow up to max_risk_per_trade_usd while protecting gains
                if target_lock > self.max_peak_lock_usd:
                    target_lock = self.max_peak_lock_usd
                    reason += f", max lock cap applied (${self.max_peak_lock_usd:.2f})"
                
                # Ensure we only move SL forward (compare in profit USD terms)
                if target_lock <= last_sl_profit:
                    tracking['last_profit'] = current_profit_usd
                    return False, f"SL already at target (current: ${last_sl_profit:.2f}, target: ${target_lock:.2f})"
                
                target_sl_profit = target_lock
            else:
                # Fallback to standard incremental logic (if elastic disabled)
                increment_level = int(current_profit_usd / self.trailing_increment_usd)
                target_sl_profit = (increment_level * self.trailing_increment_usd) - self.trailing_increment_usd
                if target_sl_profit < 0:
                    target_sl_profit = 0.0
                
                # Check for big jump
                profit_increase = current_profit_usd - last_profit
                is_big_jump = profit_increase > self.big_jump_threshold_usd
                
                if is_big_jump:
                    # Lock 75% of current profit on big jumps
                    big_jump_lock_pct = 0.75
                    target_sl_profit = max(target_sl_profit, current_profit_usd * big_jump_lock_pct)
                    reason = f"Big jump detected (+${profit_increase:.2f}), locking 75% of profit (${current_profit_usd:.2f} → ${target_sl_profit:.2f})"
                else:
                    reason = f"Incremental update (profit: ${current_profit_usd:.2f})"
                
                # CRITICAL FIX: Ensure SL never goes worse than -$2.00 (max risk per trade)
                # This prevents early exits - SL should never be worse than the initial risk
                if target_sl_profit < -self.max_risk_usd:
                    # Log early exit prevention
                    from trade_logging.trade_logger import TradeLogger
                    trade_logger = TradeLogger(self.config)
                    trade_logger.log_early_exit_prevention(
                        symbol=symbol,
                        ticket=ticket,
                        attempted_sl_profit=target_sl_profit,
                        max_risk=self.max_risk_usd
                    )
                    logger.warning(f"⚠️  Ticket {ticket}: Target SL profit ${target_sl_profit:.2f} would exceed max risk ${self.max_risk_usd:.2f}, preventing early exit")
                    tracking['last_profit'] = current_profit_usd
                    return False, f"Prevented early exit: SL would exceed max risk ${self.max_risk_usd:.2f}"
                
                # Only update if target SL is higher than current SL profit
                if target_sl_profit <= last_sl_profit:
                    tracking['last_profit'] = current_profit_usd
                    return False, f"SL already at target (current: ${last_sl_profit:.2f}, target: ${target_sl_profit:.2f})"
            
            # CRITICAL FIX: Final check - ensure SL never goes worse than -$2.00
            if target_sl_profit < -self.max_risk_usd:
                # Log early exit prevention
                from trade_logging.trade_logger import TradeLogger
                trade_logger = TradeLogger(self.config)
                trade_logger.log_early_exit_prevention(
                    symbol=symbol,
                    ticket=ticket,
                    attempted_sl_profit=target_sl_profit,
                    max_risk=self.max_risk_usd
                )
                logger.warning(f"⚠️  Ticket {ticket}: Target SL profit ${target_sl_profit:.2f} would exceed max risk ${self.max_risk_usd:.2f}, preventing early exit")
                tracking['last_profit'] = current_profit_usd
                return False, f"Prevented early exit: SL would exceed max risk ${self.max_risk_usd:.2f}"
            
            # Ensure we never move SL backward
            if target_sl_profit < last_sl_profit:
                logger.warning(f"⚠️  Ticket {ticket}: Attempted to move SL backward (${last_sl_profit:.2f} → ${target_sl_profit:.2f}), preventing")
                tracking['last_profit'] = current_profit_usd
                return False, "Prevented backward SL movement"
            
            # Calculate new stop loss price
            symbol = position['symbol']
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                tracking['last_profit'] = current_profit_usd
                return False, "Symbol info not available"
            
            entry_price = position['price_open']
            current_price = position['price_current']
            order_type = position['type']
            lot_size = position['volume']
            contract_size = symbol_info['contract_size']
            point = symbol_info['point']
            pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
            
            # Validate against broker's minimum stops level
            stops_level = symbol_info.get('trade_stops_level', 0)
            min_distance = stops_level * point if stops_level > 0 else 0
            
            # Calculate price difference needed for target SL profit
            if lot_size <= 0 or contract_size <= 0:
                tracking['last_profit'] = current_profit_usd
                return False, "Invalid lot size or contract size"
            
            target_price_diff = target_sl_profit / (lot_size * contract_size)
            
            if target_price_diff < 0:
                tracking['last_profit'] = current_profit_usd
                return False, "Invalid target price difference"
            
            # Calculate new stop loss price
            if order_type == 'BUY':
                new_sl_price = entry_price + target_price_diff
                # Ensure SL is not above current price and respects min distance
                if new_sl_price >= current_price:
                    new_sl_price = current_price - max(pip_value * 2, min_distance)
                elif min_distance > 0 and (current_price - new_sl_price) < min_distance:
                    new_sl_price = current_price - min_distance
            else:  # SELL
                new_sl_price = entry_price - target_price_diff
                # Ensure SL is not below current price and respects min distance
                if new_sl_price <= current_price:
                    new_sl_price = current_price + max(pip_value * 2, min_distance)
                elif min_distance > 0 and (new_sl_price - current_price) < min_distance:
                    new_sl_price = current_price + min_distance
            
            # Get current SL to check if update is needed
            current_sl = position['sl']
            
            # CRITICAL FIX: Check if SL needs updating with proper tolerance
            # Use point size as tolerance to prevent floating point precision issues
            point = symbol_info.get('point', 0.00001)
            sl_difference = abs(new_sl_price - current_sl) if current_sl > 0 else float('inf')
            
            # Check if SL needs updating (only move forward)
            needs_update = False
            if order_type == 'BUY':
                # For BUY: SL should be below entry, so higher SL = better (closer to entry)
                if current_sl == 0 or (new_sl_price > current_sl and sl_difference >= point):
                    needs_update = True
            else:  # SELL
                # For SELL: SL should be above entry, so lower SL = better (closer to entry)
                if current_sl == 0 or (new_sl_price < current_sl and sl_difference >= point):
                    needs_update = True
            
            if not needs_update:
                tracking['last_profit'] = current_profit_usd
                return False, f"SL already optimal (current: {current_sl:.5f}, calculated: {new_sl_price:.5f}, diff: {sl_difference:.8f} < point: {point:.8f})"
            
            # CRITICAL FIX: Update stop loss with retry logic (up to 3 attempts)
            # If SL modification fails after retries, manually close position to prevent late exit
            success = False
            max_retries = 3
            last_error = None
            
            for attempt in range(max_retries):
                success = self.order_manager.modify_order(ticket, stop_loss_price=new_sl_price)
                if success:
                    break
                if attempt < max_retries - 1:
                    import time
                    time.sleep(0.1 * (attempt + 1))  # Increasing backoff
                else:
                    # Get last error for logging
                    import MetaTrader5 as mt5
                    error = mt5.last_error()
                    last_error = error if error else "Unknown error"
            
            # If SL modification failed after all retries, manually close position to prevent late exit
            if not success:
                logger.error(f"❌ Ticket {ticket}: SL modification failed after {max_retries} attempts. "
                           f"Manually closing position to prevent late exit. Error: {last_error}")
                
                # Calculate expected loss (should be -$2.00)
                expected_loss = -self.max_risk_usd
                
                # Close position manually
                close_success = self.order_manager.close_position(ticket, comment="SL modification failed - prevent late exit")
                if close_success:
                    logger.warning(f"⚠️ Ticket {ticket}: Position closed manually due to SL modification failure")
                    # Log late exit prevention
                    from trade_logging.trade_logger import TradeLogger
                    trade_logger = TradeLogger(self.config)
                    # Get actual profit from position before it closes
                    position = self.order_manager.get_position_by_ticket(ticket)
                    actual_profit = position.get('profit', expected_loss) if position else expected_loss
                    trade_logger.log_late_exit_prevention(
                        symbol=symbol,
                        ticket=ticket,
                        actual_profit=actual_profit,
                        expected_profit=expected_loss,
                        sl_modification_failed=True
                    )
                else:
                    logger.error(f"❌ Ticket {ticket}: Failed to close position manually after SL modification failure")
                
                tracking['last_profit'] = current_profit_usd
                return False, f"SL modification failed after {max_retries} attempts, position closed manually"
            
            if success:
                # Update tracking
                tracking['last_profit'] = current_profit_usd
                tracking['last_sl_profit'] = target_sl_profit
                
                # Calculate SL in pips for logging
                if order_type == 'BUY':
                    sl_pips = (new_sl_price - entry_price) / pip_value
                else:
                    sl_pips = (entry_price - new_sl_price) / pip_value
                
                # Log to root (minimal) - only critical events
                root_logger = logging.getLogger()
                root_logger.info(f"📈 SL ADJUSTED: {symbol} Ticket {ticket} | Profit: ${current_profit_usd:.2f} → SL: ${target_sl_profit:.2f}")
                
                # Use unified trade logger for trailing stop adjustments
                from trade_logging.trade_logger import TradeLogger
                trade_logger = TradeLogger(self.config)
                trade_logger.log_trailing_stop_adjustment(
                    symbol=symbol,
                    ticket=ticket,
                    current_profit=current_profit_usd,
                    new_sl_profit=target_sl_profit,
                    new_sl_price=new_sl_price,
                    sl_pips=sl_pips,
                    reason=reason
                )
                
                return True, reason
            else:
                tracking['last_profit'] = current_profit_usd
                return False, "Failed to modify order (retries exhausted)"
    
    def monitor_all_positions_continuous(self, use_fast_polling: bool = False):
        """
        Monitor all open positions and update trailing stops continuously.
        
        This function checks P/L in millisecond margins (300ms intervals) for fast trailing stop updates.
        Fast polling mode is automatically enabled for positions with profit >= fast_trailing_threshold_usd.
        
        Features:
        - Breakeven protection: Moves SL to $0.00 when profit is between $0.00 and $0.10
        - Continuous trailing: Updates SL as profit increases
        - Millisecond-level checking: Fast polling (300ms) for profitable positions
        
        Args:
            use_fast_polling: If True, only monitor positions in fast polling mode (300ms intervals)
                             This enables millisecond-level P/L checking for profitable positions
        """
        if not self.continuous_trailing_enabled:
            return
        
        try:
            positions = self.order_manager.get_open_positions()
            
            # Get current tickets
            current_tickets = {pos['ticket'] for pos in positions} if positions else set()
            
            # CRITICAL FIX: Detect and log position closures
            # Track previously open positions to detect closures
            if not hasattr(self, '_last_open_tickets'):
                self._last_open_tickets = set()
            
            # Detect closed positions
            closed_tickets = self._last_open_tickets - current_tickets
            closed_tickets_symbols = {}  # {ticket: symbol}
            closed_positions_info = {}  # {ticket: position_info}
            
            # Get information about closed positions from tracking before cleanup
            for ticket in closed_tickets:
                # Get symbol and entry info from tracking
                tracking = self._get_position_tracking(ticket)
                if tracking:
                    # Try to get position info from order manager (might still be in cache)
                    pos_info = self.order_manager.get_position_by_ticket(ticket)
                    if pos_info:
                        closed_positions_info[ticket] = pos_info
                        closed_tickets_symbols[ticket] = pos_info.get('symbol')
                    else:
                        # Position fully closed - try to get from deal history
                        import MetaTrader5 as mt5
                        if self.mt5_connector.ensure_connected():
                            # Get deal history for this ticket (position identifier)
                            # Note: ticket is position ID, we need to get deals by position
                            deals = mt5.history_deals_get(position=ticket)
                            if deals and len(deals) > 0:
                                # Sort deals by time
                                deals_sorted = sorted(deals, key=lambda d: d.time)
                                
                                # Get entry deal (first deal, type IN)
                                entry_deal = None
                                close_deal = None
                                total_profit = 0.0
                                
                                for deal in deals_sorted:
                                    if deal.entry == mt5.DEAL_ENTRY_IN:
                                        entry_deal = deal
                                    elif deal.entry == mt5.DEAL_ENTRY_OUT:
                                        close_deal = deal
                                    total_profit += deal.profit
                                
                                if entry_deal and close_deal:
                                    symbol = entry_deal.symbol
                                    entry_price = entry_deal.price
                                    entry_time = datetime.fromtimestamp(entry_deal.time)
                                    close_price = close_deal.price
                                    close_time = datetime.fromtimestamp(close_deal.time)
                                    
                                    closed_positions_info[ticket] = {
                                        'symbol': symbol,
                                        'entry_price': entry_price,
                                        'entry_time': entry_time,
                                        'close_price': close_price,
                                        'close_time': close_time,
                                        'profit': total_profit
                                    }
                                    closed_tickets_symbols[ticket] = symbol
                                    
                                    # Use unified trade logger for position closure
                                    from trade_logging.trade_logger import TradeLogger
                                    trade_logger = TradeLogger(self.config)
                                    
                                    duration_min = (close_time - entry_time).total_seconds() / 60
                                    
                                    # Determine close reason
                                    close_reason = "Unknown"
                                    if abs(total_profit + 2.0) < 0.10:  # Close to -$2.00
                                        close_reason = "Stop Loss (-$2.00)"
                                    elif total_profit > 0:
                                        close_reason = "Take Profit or Trailing Stop"
                                    elif total_profit < -2.0:
                                        close_reason = f"Stop Loss exceeded (${total_profit:.2f})"
                                        # Log late exit warning
                                        trade_logger.log_late_exit_prevention(
                                            symbol=symbol,
                                            ticket=ticket,
                                            actual_profit=total_profit,
                                            expected_profit=-2.0,
                                            sl_modification_failed=False
                                        )
                                    
                                    # Log position closure with unified logger
                                    trade_logger.log_position_closure(
                                        symbol=symbol,
                                        ticket=ticket,
                                        entry_price=entry_price,
                                        close_price=close_price,
                                        profit=total_profit,
                                        duration_minutes=duration_min,
                                        close_reason=close_reason,
                                        entry_time=entry_time,
                                        close_time=close_time
                                    )
            
            # Now remove tracking entries
            with self._tracking_lock:
                for ticket in closed_tickets:
                    self._position_tracking.pop(ticket, None)
                    # Clean up micro-HFT engine tracking
                    if hasattr(self, '_micro_profit_engine') and self._micro_profit_engine:
                        self._micro_profit_engine.cleanup_closed_position(ticket)
            
            # Update last open tickets
            self._last_open_tickets = current_tickets.copy()
            
            # Clean up staged trades for closed positions
            if closed_tickets_symbols:
                with self._staged_lock:
                    for ticket, symbol in closed_tickets_symbols.items():
                        if symbol and symbol in self._staged_trades:
                            if ticket in self._staged_trades[symbol].get('trades', []):
                                self._staged_trades[symbol]['trades'].remove(ticket)
                            # Clean up if no more trades for this symbol
                            if not self._staged_trades[symbol].get('trades', []):
                                self._staged_trades.pop(symbol, None)
            
            if not positions:
                return
            
            # Process each position
            for position in positions:
                ticket = position['ticket']
                current_profit = position.get('profit', 0.0)
                
                # IMPORTANT: Run trailing stop FIRST to lock profit at $0.10 increments
                # Then Micro-HFT can close if appropriate
                # This ensures profit is locked before closing
                
                # Check if this position should use fast polling
                tracking = self._get_position_tracking(ticket)
                if use_fast_polling and not tracking.get('fast_polling', False):
                    # Skip if not in fast polling mode and we're in fast polling cycle
                    continue
                
                # CRITICAL: Update trailing stop FIRST to lock profit at $0.10 increments
                # This ensures profit is locked BEFORE Micro-HFT tries to close
                # When profit reaches $0.10, trailing stop locks it, preventing premature closure at $0.03
                success, reason = self.update_continuous_trailing_stop(ticket, current_profit)
                
                # MICRO-HFT PROFIT ENGINE: Check and close AFTER trailing stop has locked profit
                # Only closes if profit is in sweet spot ($0.03–$0.10) or if trailing stop has locked at $0.10+
                # Run this AFTER trailing stop update to ensure profit is locked first
                if hasattr(self, '_micro_profit_engine') and self._micro_profit_engine:
                    try:
                        # Get fresh position data after trailing stop update
                        fresh_position = self.order_manager.get_position_by_ticket(ticket)
                        if fresh_position:
                            # Check if position should be closed by micro-HFT engine
                            # This only closes after trailing stop has locked profit appropriately
                            was_closed = self._micro_profit_engine.check_and_close(fresh_position, self.mt5_connector)
                            if was_closed:
                                # Position was closed by micro-HFT engine
                                # Position closure will be detected in next cycle
                                continue
                    except Exception as e:
                        logger.error(f"Error in micro-HFT profit engine: {e}", exc_info=True)
                        # Continue with normal processing if micro-HFT engine fails
                
                # Log if update was skipped (for debugging)
                if not success and current_profit >= self.min_lock_increment_usd:
                    # Only log if it's a meaningful skip (not just "below threshold")
                    if "below minimum" not in reason.lower():
                        logger.debug(f"Trailing stop skipped for ticket {ticket}: {reason}")
        
        except Exception as e:
            logger.error(f"Error in continuous trailing stop monitoring: {e}", exc_info=True)

