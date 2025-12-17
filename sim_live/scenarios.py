"""
Test Scenarios for Synthetic Live Testing
Defines deterministic price movement scenarios for testing execution pipeline.
"""

from typing import Dict, Any, List, Optional
import math


def get_natural_buy_trend_continuation_scenario() -> Dict[str, Any]:
    """
    Natural BUY Trend Continuation Scenario
    
    Creates realistic market context where strategy will naturally decide to enter LONG.
    
    Flow:
    1. Warm-up: 35 M1 candles forming clear uptrend (price above SMAs)
    2. Entry candle: Above-average range, closes near high, triggers BUY entry
    3. Post-entry: Price moves into profit zone, triggers SL lock, then trails
    
    Tests:
    - Natural entry decision by strategy
    - Profit zone detection ($0.03 - $0.10)
    - Sweet-spot SL locking
    - Trailing stop progression
    """
    return {
        'name': 'natural_buy_trend_continuation',
        'symbol': 'EURUSD',
        'trend_direction': 'BUY',
        'warmup_candles': 35,
        'duration_seconds': 600,
        'initial_price': {
            'bid': 1.1000,
            'ask': 1.1002
        },
        'price_script': [
            # Phase 1: Warm-up (candles generated automatically by engine)
            {
                'time': 0,
                'action': 'wait',
                'duration': 35,  # Wait for warm-up candles to be available
                'comment': 'Warm-up phase: waiting for indicator warm-up'
            },
            
            # Phase 2: Generate entry candle (will trigger natural entry)
            {
                'time': 35,
                'action': 'generate_entry_candle',
                'symbol': 'EURUSD',
                'trend_direction': 'BUY',
                'comment': 'Generate entry candle that triggers natural BUY entry'
            },
            
            # Phase 3: Wait for entry (strategy will evaluate and enter)
            {
                'time': 40,
                'action': 'wait',
                'duration': 10,
                'comment': 'Wait for strategy to evaluate and enter'
            },
            
            # Phase 4: Move price into profit zone ($0.03 - $0.10)
            # For 0.01 lot EURUSD: 1 pip = $0.10, so need ~3-10 pips profit
            {
                'time': 50,
                'action': 'move_price',
                'symbol': 'EURUSD',
                'delta_bid': 0.0005,  # 5 pips up (~$0.05 profit for 0.01 lot)
                'duration': 5.0,
                'comment': 'Move into profit zone'
            },
            
            # Phase 5: Continue moving up (should trigger trailing)
            {
                'time': 60,
                'action': 'move_price',
                'symbol': 'EURUSD',
                'delta_bid': 0.0010,  # 10 more pips
                'duration': 10.0,
                'comment': 'Continue trend for trailing stop'
            },
            
            # Phase 6: Verify SL lock and trailing
            {
                'time': 80,
                'action': 'verify_sl_lock',
                'symbol': 'EURUSD',
                'expected_lock_type': 'sweet_spot'
            },
            
            # Phase 7: Pullback (should not hit SL if trailing worked)
            {
                'time': 100,
                'action': 'move_price',
                'symbol': 'EURUSD',
                'delta_bid': -0.0003,  # 3 pips pullback
                'duration': 5.0,
                'comment': 'Pullback test'
            },
            
            # Phase 8: Resume upward (final profit)
            {
                'time': 120,
                'action': 'move_price',
                'symbol': 'EURUSD',
                'delta_bid': 0.0015,  # 15 more pips
                'duration': 10.0,
                'comment': 'Final profit move'
            },
        ]
    }


def get_natural_sell_trend_continuation_scenario() -> Dict[str, Any]:
    """
    Natural SELL Trend Continuation Scenario
    
    Creates realistic market context where strategy will naturally decide to enter SHORT.
    """
    return {
        'name': 'natural_sell_trend_continuation',
        'symbol': 'EURUSD',
        'trend_direction': 'SELL',
        'warmup_candles': 35,
        'duration_seconds': 600,
        'initial_price': {
            'bid': 1.1000,
            'ask': 1.1002
        },
        'price_script': [
            # Phase 1: Warm-up
            {
                'time': 0,
                'action': 'wait',
                'duration': 35,
                'comment': 'Warm-up phase'
            },
            
            # Phase 2: Generate entry candle (SELL)
            {
                'time': 35,
                'action': 'generate_entry_candle',
                'symbol': 'EURUSD',
                'trend_direction': 'SELL',
                'comment': 'Generate entry candle that triggers natural SELL entry'
            },
            
            # Phase 3: Wait for entry
            {
                'time': 40,
                'action': 'wait',
                'duration': 10,
                'comment': 'Wait for strategy to evaluate and enter'
            },
            
            # Phase 4: Move price down into profit zone
            {
                'time': 50,
                'action': 'move_price',
                'symbol': 'EURUSD',
                'delta_bid': -0.0005,  # 5 pips down
                'duration': 5.0,
                'comment': 'Move into profit zone (SELL)'
            },
            
            # Phase 5: Continue down
            {
                'time': 60,
                'action': 'move_price',
                'symbol': 'EURUSD',
                'delta_bid': -0.0010,
                'duration': 10.0,
                'comment': 'Continue SELL trend'
            },
            
            # Phase 6: Verify SL lock
            {
                'time': 80,
                'action': 'verify_sl_lock',
                'symbol': 'EURUSD',
                'expected_lock_type': 'sweet_spot'
            },
        ]
    }


def get_profit_zone_then_reversal_scenario() -> Dict[str, Any]:
    """Test profit zone entry followed by reversal that hits trailing SL."""
    return {
        'name': 'profit_zone_then_reversal',
        'symbol': 'EURUSD',
        'trend_direction': 'BUY',
        'warmup_candles': 35,
        'duration_seconds': 600,
        'initial_price': {
            'bid': 1.1000,
            'ask': 1.1002
        },
        'price_script': [
            {'time': 0, 'action': 'wait', 'duration': 35},
            {'time': 35, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
            {'time': 40, 'action': 'wait', 'duration': 10},
            {'time': 50, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0008, 'duration': 5.0},
            {'time': 65, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': -0.0020, 'duration': 10.0, 'comment': 'Reversal to hit SL'},
        ]
    }


def get_trailing_stop_multiple_updates_scenario() -> Dict[str, Any]:
    """Test multiple trailing stop updates."""
    return {
        'name': 'trailing_stop_multiple_updates',
        'symbol': 'EURUSD',
        'trend_direction': 'BUY',
        'warmup_candles': 35,
        'duration_seconds': 900,
        'initial_price': {
            'bid': 1.1000,
            'ask': 1.1002
        },
        'price_script': [
            {'time': 0, 'action': 'wait', 'duration': 35},
            {'time': 35, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
            {'time': 40, 'action': 'wait', 'duration': 10},
            # Multiple gradual moves to trigger trailing
            {'time': 50, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0005, 'duration': 5.0},
            {'time': 70, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0005, 'duration': 5.0},
            {'time': 90, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0005, 'duration': 5.0},
            {'time': 110, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0005, 'duration': 5.0},
            {'time': 130, 'action': 'verify_trailing', 'symbol': 'EURUSD', 'min_sl_distance_pips': 10},
        ]
    }


def get_lock_contention_stress_scenario() -> Dict[str, Any]:
    """Test lock contention with rapid price updates."""
    return {
        'name': 'lock_contention_stress',
        'symbol': 'EURUSD',
        'trend_direction': 'BUY',
        'warmup_candles': 35,
        'duration_seconds': 300,
        'initial_price': {
            'bid': 1.1000,
            'ask': 1.1002
        },
        'price_script': [
            {'time': 0, 'action': 'wait', 'duration': 35},
            {'time': 35, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
            {'time': 40, 'action': 'wait', 'duration': 10},
            # Rapid small moves to stress lock contention
            {'time': 50, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 51, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 52, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 53, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 54, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
        ]
    }


# Legacy scenario (kept for compatibility)
def get_profit_zone_entry_scenario() -> Dict[str, Any]:
    """Legacy scenario - use natural_buy_trend_continuation instead."""
    return get_natural_buy_trend_continuation_scenario()


def get_scenario(name: str) -> Optional[Dict[str, Any]]:
    """
    Get scenario by name.
    
    Args:
        name: Scenario name
    
    Returns:
        Scenario dict or None if not found
    """
    scenarios = {
        'profit_zone_entry': get_profit_zone_entry_scenario(),  # Legacy
        'natural_buy_trend_continuation': get_natural_buy_trend_continuation_scenario(),
        'natural_sell_trend_continuation': get_natural_sell_trend_continuation_scenario(),
        'profit_zone_then_reversal': get_profit_zone_then_reversal_scenario(),
        'trailing_stop_multiple_updates': get_trailing_stop_multiple_updates_scenario(),
        'lock_contention_stress': get_lock_contention_stress_scenario(),
    }
    
    return scenarios.get(name)
