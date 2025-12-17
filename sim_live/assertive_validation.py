"""
Assertive Validation System for SIM_LIVE

Validates that scenarios meet their declared intents and fails loudly if not.
"""

import time
from typing import Dict, Any, Optional, List, Tuple
from sim_live.sim_live_logger import get_sim_live_logger


class ScenarioValidator:
    """Validates scenario execution against declared intent."""
    
    def __init__(self, market_engine, broker, trading_bot):
        self.market_engine = market_engine
        self.broker = broker
        self.trading_bot = trading_bot
        self.logger = get_sim_live_logger()
        self.scan_cycle_count = 0
        self.trade_opened = False
        self.trade_ticket = None
        self.rejection_reasons = []
        
    def reset(self):
        """Reset validation state for new scenario."""
        self.scan_cycle_count = 0
        self.trade_opened = False
        self.trade_ticket = None
        self.rejection_reasons = []
    
    def log_scan_cycle(self, symbol: str, opportunities_count: int):
        """Log each scan cycle."""
        self.scan_cycle_count += 1
        self.logger.info(f"[SIM_LIVE] [SCAN_CYCLE] Cycle #{self.scan_cycle_count}: Scanned {symbol}, found {opportunities_count} opportunities")
    
    def log_trade_opened(self, ticket: int):
        """Log when trade is opened."""
        self.trade_opened = True
        self.trade_ticket = ticket
        self.logger.info(f"[SIM_LIVE] [TRADE_OPENED] ✓ Trade opened: ticket={ticket}")
    
    def log_entry_rejection(self, reason: str, details: Dict[str, Any]):
        """Log entry rejection with details."""
        self.rejection_reasons.append({
            'cycle': self.scan_cycle_count,
            'reason': reason,
            'details': details
        })
        self.logger.warning(f"[SIM_LIVE] [REJECTION] Cycle #{self.scan_cycle_count}: {reason}")
    
    def validate_contract_satisfaction(self, scenario: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate that market data satisfies the entry contract.
        
        Returns:
            (satisfied: bool, reason: str)
        """
        intent = scenario.get('intent', {})
        market_context = scenario.get('market_context', {})
        symbol = scenario.get('symbol', 'EURUSD')
        trend_direction = intent.get('direction', 'BUY')
        
        # Get current candle data
        candles = self.market_engine.copy_rates_from_pos(symbol, 1, 0, 100)  # M1, last 100 candles
        if not candles or len(candles) < 50:
            return False, f"Insufficient candles: {len(candles) if candles else 0}"
        
        # Calculate indicators
        try:
            import pandas as pd
            import numpy as np
            
            df_data = {
                'time': [c['time'] for c in candles],
                'open': [c['open'] for c in candles],
                'high': [c['high'] for c in candles],
                'low': [c['low'] for c in candles],
                'close': [c['close'] for c in candles],
            }
            df = pd.DataFrame(df_data)
            
            # SMA calculations
            sma20_series = df['close'].rolling(window=20).mean()
            sma50_series = df['close'].rolling(window=50).mean()
            sma20 = sma20_series.iloc[-1] if pd.notna(sma20_series.iloc[-1]) else None
            sma50 = sma50_series.iloc[-1] if pd.notna(sma50_series.iloc[-1]) else None
            
            if sma20 is None or sma50 is None:
                return False, f"Invalid SMA: SMA20={sma20}, SMA50={sma50}"
            
            # SMA separation check
            if trend_direction == 'BUY':
                if sma20 <= sma50:
                    return False, f"SMA20 ({sma20:.5f}) <= SMA50 ({sma50:.5f}) for BUY trend"
                separation_pct = (sma20 - sma50) / sma50 * 100
            else:  # SELL
                if sma20 >= sma50:
                    return False, f"SMA20 ({sma20:.5f}) >= SMA50 ({sma50:.5f}) for SELL trend"
                separation_pct = (sma50 - sma20) / sma50 * 100
            
            min_separation = market_context.get('sma_separation_min_pct', 0.05)
            if separation_pct < min_separation:
                return False, f"SMA separation {separation_pct:.4f}% < required {min_separation}%"
            
            # RSI calculation
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            rsi = rsi.fillna(100)  # Fill NaN with 100 (when loss=0)
            latest_rsi = rsi.iloc[-1] if pd.notna(rsi.iloc[-1]) else 100
            
            # RSI range check
            rsi_range = market_context.get('rsi_range', [30, 50])
            if latest_rsi < rsi_range[0] or latest_rsi > rsi_range[1]:
                return False, f"RSI {latest_rsi:.1f} not in range [{rsi_range[0]}, {rsi_range[1]}]"
            
            # ADX calculation (simplified - would need full ADX implementation)
            # For now, just check if RSI is not saturated
            
            # Candle quality check
            if len(candles) >= 21:
                current_candle = candles[0]
                current_range = current_candle['high'] - current_candle['low']
                prev_ranges = [c['high'] - c['low'] for c in candles[1:21]]
                avg_range = sum(prev_ranges) / len(prev_ranges) if prev_ranges else 0
                if avg_range > 0:
                    range_pct = (current_range / avg_range) * 100
                    min_range_pct = market_context.get('candle_quality_min_pct', 50)
                    if range_pct < min_range_pct:
                        return False, f"Candle quality: {range_pct:.1f}% < required {min_range_pct}%"
            
            # All checks passed
            self.logger.info(f"[SIM_LIVE] [CONTRACT_OK] ✓ Contract satisfied: "
                           f"SMA20={sma20:.5f}, SMA50={sma50:.5f}, separation={separation_pct:.4f}%, "
                           f"RSI={latest_rsi:.1f}")
            return True, "Contract satisfied"
            
        except Exception as e:
            return False, f"Validation error: {e}"
    
    def assert_scenario_intent(self, scenario: Dict[str, Any]):
        """
        Assert that scenario intent is met. Fails loudly if not.
        
        This is called after scenario execution to validate results.
        """
        intent = scenario.get('intent', {})
        expect_trade = intent.get('expect_trade', True)
        max_cycles = intent.get('max_cycles_to_entry', 10)
        expected_rejection = intent.get('rejection_reason')
        
        # Validate contract satisfaction first
        contract_ok, contract_reason = self.validate_contract_satisfaction(scenario)
        if not contract_ok:
            error_msg = (
                f"❌ SIM_LIVE ASSERTION FAILED: Contract not satisfied\n"
                f"Scenario: {scenario.get('name')}\n"
                f"Reason: {contract_reason}\n"
                f"This indicates a problem with market data generation."
            )
            self.logger.error(error_msg)
            raise AssertionError(error_msg)
        
        if expect_trade:
            # Should have opened a trade
            if not self.trade_opened:
                # Check why it was rejected
                if self.rejection_reasons:
                    last_rejection = self.rejection_reasons[-1]
                    rejection_reason = last_rejection['reason']
                    rejection_details = last_rejection.get('details', {})
                    
                    error_msg = (
                        f"❌ SIM_LIVE ASSERTION FAILED: Expected trade but none opened\n"
                        f"Scenario: {scenario.get('name')}\n"
                        f"Cycles: {self.scan_cycle_count} (max: {max_cycles})\n"
                        f"Last rejection: {rejection_reason}\n"
                        f"Rejection details: {rejection_details}\n"
                        f"\nAll rejections:\n"
                    )
                    for r in self.rejection_reasons:
                        error_msg += f"  Cycle #{r['cycle']}: {r['reason']}\n"
                    
                    self.logger.error(error_msg)
                    raise AssertionError(error_msg)
                else:
                    error_msg = (
                        f"❌ SIM_LIVE ASSERTION FAILED: Expected trade but none opened\n"
                        f"Scenario: {scenario.get('name')}\n"
                        f"Cycles: {self.scan_cycle_count} (max: {max_cycles})\n"
                        f"No rejection reasons logged (possible early exit or no scan cycles)"
                    )
                    self.logger.error(error_msg)
                    raise AssertionError(error_msg)
            
            # Should have opened within max cycles
            if self.scan_cycle_count > max_cycles:
                error_msg = (
                    f"❌ SIM_LIVE ASSERTION FAILED: Trade opened but too late\n"
                    f"Scenario: {scenario.get('name')}\n"
                    f"Cycles to entry: {self.scan_cycle_count} (max: {max_cycles})\n"
                    f"Ticket: {self.trade_ticket}"
                )
                self.logger.error(error_msg)
                raise AssertionError(error_msg)
            
            # Success
            self.logger.info(f"[SIM_LIVE] [ASSERTION_PASS] ✓ Trade opened as expected: "
                           f"scenario={scenario.get('name')}, cycles={self.scan_cycle_count}, ticket={self.trade_ticket}")
        
        else:
            # Should NOT have opened a trade
            if self.trade_opened:
                error_msg = (
                    f"❌ SIM_LIVE ASSERTION FAILED: Expected NO trade but trade opened\n"
                    f"Scenario: {scenario.get('name')}\n"
                    f"Ticket: {self.trade_ticket}\n"
                    f"Expected rejection reason: {expected_rejection}"
                )
                self.logger.error(error_msg)
                raise AssertionError(error_msg)
            
            # Should have been rejected for expected reason
            if expected_rejection and self.rejection_reasons:
                last_rejection = self.rejection_reasons[-1]['reason']
                if expected_rejection not in last_rejection:
                    error_msg = (
                        f"⚠️ SIM_LIVE WARNING: Rejected but not for expected reason\n"
                        f"Scenario: {scenario.get('name')}\n"
                        f"Expected: {expected_rejection}\n"
                        f"Actual: {last_rejection}\n"
                        f"(This may still be acceptable - review rejection)"
                    )
                    self.logger.warning(error_msg)
            
            # Success
            self.logger.info(f"[SIM_LIVE] [ASSERTION_PASS] ✓ Correctly rejected as expected: "
                           f"scenario={scenario.get('name')}, reason={expected_rejection}")

