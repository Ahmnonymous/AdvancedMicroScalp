"""
Limit Entry Dry-Run System
Calculates, validates, and logs hypothetical limit-based entries using historical data.
This is DRY-RUN ONLY - does NOT execute any orders.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


class LimitEntryDryRun:
    """
    DRY-RUN system for limit entry analysis.
    Calculates hypothetical limit entries and simulates fills using historical data.
    Does NOT place any orders or modify execution behavior.
    """
    
    def __init__(self, config: Dict[str, Any], mt5_connector):
        """
        Initialize limit entry dry-run system.
        
        Args:
            config: Configuration dictionary
            mt5_connector: MT5Connector instance for market data access
        """
        self.config = config
        self.mt5_connector = mt5_connector
        self.risk_config = config.get('risk', {})
        
        # Load config flags
        self.use_limit_entries = config.get('use_limit_entries', False)
        self.limit_entry_dry_run = config.get('limit_entry_dry_run', True)
        self.min_entry_score = config.get('min_entry_score', 0.0)
        self.limit_entry_buffer_pips = config.get('limit_entry_buffer_pips', 0.5)
        self.limit_entry_ttl_seconds = config.get('limit_entry_ttl_seconds', 60)
        self.limit_entry_max_spread_pips = config.get('limit_entry_max_spread_pips', 2.0)
        
        # Determine mode
        self.mode = "BACKTEST" if config.get('mode') == 'backtest' else "LIVE"
        
        # Safety check: ensure dry-run is enabled
        if not self.limit_entry_dry_run:
            logger.warning(f"mode={self.mode} | [LIMIT_ENTRY] WARNING: limit_entry_dry_run is False - dry-run system disabled")
        
        # Safety check: ensure use_limit_entries is False
        if self.use_limit_entries:
            logger.critical(f"mode={self.mode} | [LIMIT_ENTRY] CRITICAL: use_limit_entries is True - this should be False for dry-run only!")
            logger.critical(f"mode={self.mode} | [LIMIT_ENTRY] ABORTING - refusing to proceed with use_limit_entries=True")
            raise ValueError("use_limit_entries must be False for dry-run mode. Set limit_entry_dry_run=True instead.")
        
        # Zero side effect guarantee: Log explicit assertions
        logger.info(f"mode={self.mode} | [INIT] Limit entry system loaded in DRY-RUN mode only — no execution impact")
        logger.info(f"mode={self.mode} | [SAFETY] Assertions:")
        logger.info(f"mode={self.mode} | [SAFETY]   ✓ No limit orders will be placed")
        logger.info(f"mode={self.mode} | [SAFETY]   ✓ No SL/TP behavior will be changed")
        logger.info(f"mode={self.mode} | [SAFETY]   ✓ No execution paths will be modified")
        logger.info(f"mode={self.mode} | [SAFETY]   ✓ No auto-fix logic will be triggered")
    
    def check_score_gate(self, symbol: str, score: float) -> Tuple[bool, Optional[str]]:
        """
        Check if score passes the minimum entry score gate.
        
        Args:
            symbol: Trading symbol
            score: Trade quality score
        
        Returns:
            (passes, rejection_reason)
        """
        if not self.limit_entry_dry_run:
            return True, None  # Dry-run disabled, skip check
        
        if score < self.min_entry_score:
            reason = f"Score {score:.2f} < min_entry_score {self.min_entry_score}"
            logger.info(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Score gate FAILED | {reason}")
            return False, reason
        
        logger.debug(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Score gate PASSED | Score: {score:.2f} >= {self.min_entry_score}")
        return True, None
    
    def calculate_hypothetical_limit_entry(
        self,
        symbol: str,
        direction: str,  # 'BUY' or 'SELL'
        market_price: float,
        quality_score: float
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate hypothetical limit entry price and SL.
        
        Args:
            symbol: Trading symbol
            direction: 'BUY' or 'SELL'
            market_price: Current market price (ask for BUY, bid for SELL)
            quality_score: Trade quality score
        
        Returns:
            Dictionary with limit entry details, or None if invalid
        """
        if not self.limit_entry_dry_run:
            return None  # Dry-run disabled
        
        # Get symbol info
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            logger.warning(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Cannot get symbol info")
            return None
        
        point = symbol_info.get('point', 0.00001)
        digits = symbol_info.get('digits', 5)
        pip_value = point * 10 if digits == 5 or digits == 3 else point
        
        # Get current bid/ask
        tick = self.mt5_connector.get_symbol_info_tick(symbol)
        if tick is None:
            logger.warning(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Cannot get tick data")
            return None
        
        # Handle both tuple and object formats
        if isinstance(tick, tuple):
            bid = tick[1]  # Tuple format: (time, bid, ask, ...)
            ask = tick[2]
        else:
            bid = tick.bid
            ask = tick.ask
        spread_pips = abs(ask - bid) / pip_value if pip_value > 0 else 0
        
        # Validate spread
        if spread_pips > self.limit_entry_max_spread_pips:
            logger.info(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Spread validation FAILED | "
                       f"Spread: {spread_pips:.2f}pips > max: {self.limit_entry_max_spread_pips}pips")
            return None
        
        # Calculate limit price
        buffer_price = self.limit_entry_buffer_pips * pip_value
        
        if direction == 'BUY':
            limit_price = bid - buffer_price
            entry_price_for_sl = limit_price  # Use limit price for SL calculation
        else:  # SELL
            limit_price = ask + buffer_price
            entry_price_for_sl = limit_price  # Use limit price for SL calculation
        
        # Normalize limit price
        limit_price = round(limit_price / point) * point
        limit_price = round(limit_price, digits)
        
        # Calculate SL price for exactly $2.00 risk
        # Use risk manager's USD-based SL calculation
        from risk.risk_manager import RiskManager
        from execution.order_manager import OrderType
        
        # Get risk manager from config (we'll need to pass it or access it differently)
        # For now, calculate directly using the same logic
        contract_size = symbol_info.get('contract_size', 1.0)
        risk_usd = self.risk_config.get('max_risk_per_trade_usd', 2.0)
        
        # Calculate lot size (use default for calculation)
        default_lot = self.risk_config.get('default_lot_size', 0.01)
        
        # Calculate contract value per point
        contract_value_per_point = contract_size * default_lot * point
        
        # Calculate SL distance for $2.00 risk
        if contract_value_per_point > 0:
            sl_distance_price = risk_usd / contract_value_per_point
        else:
            logger.warning(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Invalid contract_value_per_point")
            return None
        
        # Calculate SL price
        if direction == 'BUY':
            sl_price = entry_price_for_sl - sl_distance_price
        else:  # SELL
            sl_price = entry_price_for_sl + sl_distance_price
        
        # Normalize SL price
        sl_price = round(sl_price / point) * point
        sl_price = round(sl_price, digits)
        
        # Calculate SL distance in pips
        sl_distance_pips = abs(entry_price_for_sl - sl_price) / pip_value if pip_value > 0 else 0
        
        # Validate SL distance against broker minimum
        stops_level = symbol_info.get('trade_stops_level', 0)
        min_stops_distance = stops_level * point if stops_level > 0 else 0
        
        if min_stops_distance > 0:
            actual_sl_distance = abs(entry_price_for_sl - sl_price)
            if actual_sl_distance < min_stops_distance:
                # Adjust SL to meet broker minimum
                if direction == 'BUY':
                    sl_price = entry_price_for_sl - min_stops_distance
                else:  # SELL
                    sl_price = entry_price_for_sl + min_stops_distance
                sl_price = round(sl_price / point) * point
                sl_price = round(sl_price, digits)
                logger.debug(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Adjusted SL to meet broker minimum")
        
        return {
            'symbol': symbol,
            'direction': direction,
            'market_price': market_price,
            'limit_price': limit_price,
            'hypothetical_sl': sl_price,
            'sl_distance_pips': sl_distance_pips,
            'spread_pips': spread_pips,
            'quality_score': quality_score,
            'ttl_seconds': self.limit_entry_ttl_seconds
        }
    
    def simulate_fill_using_historical_data(
        self,
        symbol: str,
        limit_price: float,
        direction: str,
        entry_time: datetime,
        ttl_seconds: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Simulate whether limit order would fill using historical data.
        
        Args:
            symbol: Trading symbol
            limit_price: Hypothetical limit price
            direction: 'BUY' or 'SELL'
            entry_time: Time when limit order would be placed
            ttl_seconds: Time-to-live in seconds
        
        Returns:
            (would_fill, expiry_reason)
        """
        if not self.limit_entry_dry_run:
            return False, "Dry-run disabled"
        
        # Calculate expiry time
        expiry_time = entry_time + timedelta(seconds=ttl_seconds)
        
        # Get historical ticks/candles for the TTL period
        # Try to get tick data first (most accurate)
        timeframe = mt5.TIMEFRAME_M1  # Use 1-minute candles as fallback
        
        # Get ticks from entry_time to expiry_time
        # Convert datetime to timestamp for MT5
        try:
            ticks = mt5.copy_ticks_range(symbol, entry_time, expiry_time, mt5.COPY_TICKS_ALL)
        except Exception as e:
            logger.debug(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Error getting ticks: {e}")
            ticks = None
        
        if ticks is None or len(ticks) == 0:
            # Fallback to candles
            rates = mt5.copy_rates_range(symbol, timeframe, entry_time, expiry_time)
            if rates is None or len(rates) == 0:
                # logger.warning(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] No historical data available")
                return False, "No historical data"
            
            # Check if price touched limit in candles
            for rate in rates:
                low = rate[3]  # Low price
                high = rate[2]  # High price
                time_val = datetime.fromtimestamp(rate[0])
                
                if direction == 'BUY':
                    # BUY limit: price must drop to or below limit_price
                    if low <= limit_price:
                        logger.debug(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Would fill at {time_val} | Low: {low:.5f} <= Limit: {limit_price:.5f}")
                        return True, None
                else:  # SELL
                    # SELL limit: price must rise to or above limit_price
                    if high >= limit_price:
                        logger.debug(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Would fill at {time_val} | High: {high:.5f} >= Limit: {limit_price:.5f}")
                        return True, None
            
            # Price never touched limit
            return False, f"Price never reached limit within {ttl_seconds}s"
        
        # Use tick data (more accurate)
        # MT5 ticks are named tuples or arrays: (time, bid, ask, ...)
        for tick in ticks:
            # Handle both tuple and named tuple formats
            if hasattr(tick, 'bid'):
                bid = tick.bid
                ask = tick.ask
                time_val = datetime.fromtimestamp(tick.time)
            else:
                bid = tick[1]  # Bid price
                ask = tick[2]  # Ask price
                time_val = datetime.fromtimestamp(tick[0])
            
            if direction == 'BUY':
                # BUY limit: bid must drop to or below limit_price
                if bid <= limit_price:
                    logger.debug(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Would fill at {time_val} | Bid: {bid:.5f} <= Limit: {limit_price:.5f}")
                    return True, None
            else:  # SELL
                # SELL limit: ask must rise to or above limit_price
                if ask >= limit_price:
                    logger.debug(f"mode={self.mode} | symbol={symbol} | [DRY_RUN][LIMIT_ENTRY] Would fill at {time_val} | Ask: {ask:.5f} >= Limit: {limit_price:.5f}")
                    return True, None
        
        # Price never touched limit
        return False, f"Price never reached limit within {ttl_seconds}s"
    
    def analyze_limit_entry(
        self,
        symbol: str,
        direction: str,
        market_price: float,
        quality_score: float,
        entry_time: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Complete limit entry analysis (score gate, calculation, fill simulation).
        
        Args:
            symbol: Trading symbol
            direction: 'BUY' or 'SELL'
            market_price: Current market price
            quality_score: Trade quality score
            entry_time: Time when analysis is performed (defaults to now)
        
        Returns:
            Analysis result dictionary, or None if analysis skipped
        """
        if not self.limit_entry_dry_run:
            return None
        
        # Use current time if not provided
        if entry_time is None:
            entry_time = datetime.now()
        
        # Step 1: Check score gate
        score_passes, rejection_reason = self.check_score_gate(symbol, quality_score)
        if not score_passes:
            return {
                'symbol': symbol,
                'direction': direction,
                'score': quality_score,
                'market_price': market_price,
                'score_gate_passed': False,
                'rejection_reason': rejection_reason
            }
        
        # Step 2: Calculate hypothetical limit entry
        limit_entry = self.calculate_hypothetical_limit_entry(
            symbol=symbol,
            direction=direction,
            market_price=market_price,
            quality_score=quality_score
        )
        
        if limit_entry is None:
            return {
                'symbol': symbol,
                'direction': direction,
                'score': quality_score,
                'market_price': market_price,
                'score_gate_passed': True,
                'calculation_failed': True,
                'rejection_reason': 'Limit entry calculation failed (spread too wide or invalid)'
            }
        
        # Step 3: Simulate fill using historical data
        would_fill, expiry_reason = self.simulate_fill_using_historical_data(
            symbol=symbol,
            limit_price=limit_entry['limit_price'],
            direction=direction,
            entry_time=entry_time,
            ttl_seconds=limit_entry['ttl_seconds']
        )
        
        # Combine results
        result = {
            **limit_entry,
            'score_gate_passed': True,
            'would_fill': would_fill,
            'expiry_reason': expiry_reason if not would_fill else None,
            'entry_time': entry_time
        }
        
        # Step 4: Log result
        self._log_limit_entry_result(result)
        
        return result
    
    def _log_limit_entry_result(self, result: Dict[str, Any]):
        """
        Log limit entry dry-run result with required format.
        
        Args:
            result: Analysis result dictionary
        """
        symbol = result.get('symbol', 'UNKNOWN')
        direction = result.get('direction', 'UNKNOWN')
        score = result.get('quality_score', 0.0)
        market_price = result.get('market_price', 0.0)
        limit_price = result.get('limit_price', 0.0)
        sl_price = result.get('hypothetical_sl', 0.0)
        sl_distance_pips = result.get('sl_distance_pips', 0.0)
        spread_pips = result.get('spread_pips', 0.0)
        ttl_seconds = result.get('ttl_seconds', 0)
        would_fill = result.get('would_fill', False)
        expiry_reason = result.get('expiry_reason')
        
        # Format log message
        log_msg = (f"[DRY_RUN][LIMIT_ENTRY] "
                  f"symbol={symbol} "
                  f"direction={direction} "
                  f"score={score:.2f} "
                  f"market={market_price:.5f} "
                  f"limit={limit_price:.5f} "
                  f"SL={sl_price:.5f} "
                  f"SL_distance={sl_distance_pips:.2f}pips "
                  f"spread={spread_pips:.2f}pips "
                  f"TTL={ttl_seconds}s "
                  f"would_fill={str(would_fill).lower()}")
        
        if expiry_reason:
            log_msg += f" expiry_reason={expiry_reason}"
        
        logger.info(f"mode={self.mode} | {log_msg}")

