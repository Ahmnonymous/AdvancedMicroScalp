"""
Intent-Driven Scenario Definitions for SIM_LIVE

Each scenario declares:
- intent: What should happen (expect_trade, direction, etc.)
- market_context: What market conditions should exist (trend_strength, volatility, etc.)
- validation: Assertions that must pass
"""

from typing import Dict, Any, Optional


def create_intent_driven_scenario(
    name: str,
    intent: Dict[str, Any],
    market_context: Dict[str, Any],
    price_script: list,
    symbol: str = 'EURUSD',
    initial_price: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Create an intent-driven scenario.
    
    Args:
        name: Scenario name
        intent: Expected behavior (expect_trade, direction, expect_sl_lock, etc.)
        market_context: Market conditions (trend_strength, volatility, pullbacks, etc.)
        price_script: Price movement script (actions)
        symbol: Trading symbol
        initial_price: Initial bid/ask prices
    
    Returns:
        Complete scenario dict
    """
    if initial_price is None:
        initial_price = {'bid': 1.1000, 'ask': 1.1002}
    
    # Determine warm-up count based on market context
    trend_strength = market_context.get('trend_strength', 'MODERATE')
    if trend_strength == 'STRONG':
        warmup_count = 60  # More candles for strong trend
    elif trend_strength == 'WEAK':
        warmup_count = 50  # Fewer candles for weak trend
    else:
        warmup_count = 60  # Default
    
    return {
        'name': name,
        'symbol': symbol,
        'trend_direction': intent.get('direction', 'BUY'),
        'warmup_candles': warmup_count,
        'duration_seconds': 600,
        'initial_price': initial_price,
        'intent': intent,
        'market_context': market_context,
        'price_script': price_script
    }


# ============================================================================
# CERTIFIED SCENARIOS
# ============================================================================

def get_certified_buy_profit_trailing_exit() -> Dict[str, Any]:
    """
    Certified Scenario 1: BUY trend → profit → trailing → exit profit
    
    Intent:
    - expect_trade: true
    - direction: BUY
    - expect_sl_lock: true
    - expect_trailing: true
    - expect_exit: SL_PROFIT
    
    Market Context:
    - trend_strength: STRONG
    - volatility: NORMAL
    - pullbacks: PRESENT
    - rsi_range: [35, 45]  # Well within entry range
    """
    return create_intent_driven_scenario(
        name='certified_buy_profit_trailing_exit',
        intent={
            'expect_trade': True,
            'direction': 'BUY',
            'expect_sl_lock': True,
            'expect_trailing': True,
            'expect_exit': 'SL_PROFIT',
            'max_cycles_to_entry': 5  # Should enter within 5 scan cycles
        },
        market_context={
            'trend_strength': 'STRONG',
            'volatility': 'NORMAL',
            'pullbacks': 'PRESENT',
            'rsi_range': [35, 45],
            'sma_separation_min_pct': 0.08,  # 0.08% minimum
            'adx_min': 18,
            'candle_quality_min_pct': 65  # Current candle >= 65% of avg
        },
        price_script=[
            {'time': 0, 'action': 'wait', 'duration': 60, 'comment': 'Warm-up phase'},
            {'time': 60, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
            {'time': 65, 'action': 'wait', 'duration': 10, 'comment': 'Wait for entry'},
            {'time': 75, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0005, 'duration': 5.0, 'comment': 'Profit zone'},
            {'time': 85, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0010, 'duration': 10.0, 'comment': 'Trailing trigger'},
            {'time': 100, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0015, 'duration': 10.0, 'comment': 'Final profit'},
            {'time': 120, 'action': 'verify_sl_lock', 'symbol': 'EURUSD', 'expected_lock_type': 'sweet_spot'},
            {'time': 130, 'action': 'verify_trailing', 'symbol': 'EURUSD', 'min_sl_distance_pips': 15},
        ]
    )


def get_certified_sell_sl_lock_reversal_profit() -> Dict[str, Any]:
    """
    Certified Scenario 2: SELL trend → SL lock → reversal → exit profit
    
    Intent:
    - expect_trade: true
    - direction: SELL
    - expect_sl_lock: true
    - expect_exit: SL_PROFIT (trailing SL hit on reversal)
    """
    return create_intent_driven_scenario(
        name='certified_sell_sl_lock_reversal_profit',
        intent={
            'expect_trade': True,
            'direction': 'SELL',
            'expect_sl_lock': True,
            'expect_trailing': True,
            'expect_exit': 'SL_PROFIT',
            'max_cycles_to_entry': 5
        },
        market_context={
            'trend_strength': 'STRONG',
            'volatility': 'NORMAL',
            'pullbacks': 'PRESENT',
            'rsi_range': [55, 65],  # For SELL, RSI should be higher (but still in range)
            'sma_separation_min_pct': 0.08,
            'adx_min': 18,
            'candle_quality_min_pct': 65
        },
        price_script=[
            {'time': 0, 'action': 'wait', 'duration': 60},
            {'time': 60, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'SELL'},
            {'time': 65, 'action': 'wait', 'duration': 10},
            {'time': 75, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': -0.0005, 'duration': 5.0},
            {'time': 85, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': -0.0010, 'duration': 10.0},
            {'time': 100, 'action': 'verify_sl_lock', 'symbol': 'EURUSD', 'expected_lock_type': 'sweet_spot'},
            {'time': 110, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': -0.0005, 'duration': 5.0, 'comment': 'Continue down'},
            {'time': 120, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0015, 'duration': 5.0, 'comment': 'Reversal hits trailing SL'},
        ]
    )


def get_certified_false_trend_rejected() -> Dict[str, Any]:
    """
    Certified Scenario 3: False trend → correctly rejected
    
    Intent:
    - expect_trade: false
    - rejection_reason: TREND_STRENGTH or QUALITY_SCORE
    """
    return create_intent_driven_scenario(
        name='certified_false_trend_rejected',
        intent={
            'expect_trade': False,
            'rejection_reason': 'QUALITY_SCORE',  # Expected rejection reason
            'max_cycles_to_reject': 3  # Should be rejected quickly
        },
        market_context={
            'trend_strength': 'WEAK',
            'volatility': 'LOW',
            'pullbacks': 'PRESENT',
            'rsi_range': [40, 50],
            'sma_separation_min_pct': 0.02,  # Very weak separation
            'adx_min': 10,  # Low ADX
            'candle_quality_min_pct': 40  # Low candle quality
        },
        price_script=[
            {'time': 0, 'action': 'wait', 'duration': 50},
            {'time': 50, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
        ]
    )


def get_certified_high_spread_rejected() -> Dict[str, Any]:
    """
    Certified Scenario 4: High spread → correctly rejected
    
    Intent:
    - expect_trade: false
    - rejection_reason: RISK_CHECK_SPREAD
    """
    return create_intent_driven_scenario(
        name='certified_high_spread_rejected',
        intent={
            'expect_trade': False,
            'rejection_reason': 'RISK_CHECK_SPREAD',
            'max_cycles_to_reject': 3
        },
        market_context={
            'trend_strength': 'STRONG',
            'volatility': 'NORMAL',
            'pullbacks': 'PRESENT',
            'rsi_range': [35, 45],
            'sma_separation_min_pct': 0.08,
            'adx_min': 18,
            'spread_points': 20  # High spread (exceeds 15 limit)
        },
        price_script=[
            {'time': 0, 'action': 'wait', 'duration': 60},
            {'time': 60, 'action': 'set_spread', 'symbol': 'EURUSD', 'spread_points': 20, 'comment': 'Set high spread'},
            {'time': 61, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
        ]
    )


def get_certified_lock_contention_stress() -> Dict[str, Any]:
    """
    Certified Scenario 5: Lock contention stress → SL still applied
    
    Intent:
    - expect_trade: true
    - expect_sl_lock: true
    - stress_test: LOCK_CONTENTION
    """
    return create_intent_driven_scenario(
        name='certified_lock_contention_stress',
        intent={
            'expect_trade': True,
            'direction': 'BUY',
            'expect_sl_lock': True,
            'stress_test': 'LOCK_CONTENTION',
            'max_cycles_to_entry': 5
        },
        market_context={
            'trend_strength': 'STRONG',
            'volatility': 'NORMAL',
            'pullbacks': 'PRESENT',
            'rsi_range': [35, 45],
            'sma_separation_min_pct': 0.08,
            'adx_min': 18
        },
        price_script=[
            {'time': 0, 'action': 'wait', 'duration': 60},
            {'time': 60, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
            {'time': 65, 'action': 'wait', 'duration': 10},
            # Rapid price updates to stress locks
            {'time': 75, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 76, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 77, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 78, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 79, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': 0.0001, 'duration': 0.1},
            {'time': 85, 'action': 'verify_sl_lock', 'symbol': 'EURUSD', 'expected_lock_type': 'sweet_spot'},
        ]
    )


def get_certified_buy_hard_sl_loss() -> Dict[str, Any]:
    """
    Certified Scenario 6: BUY entry → price moves against → hard SL loss

    Intent:
    - expect_trade: true
    - direction: BUY
    - expect_exit: HARD_SL

    Notes:
    - No profit-locking or trailing should occur.
    - Deterministic price movement must drive price through the configured SL,
      allowing the SL manager/broker to close the trade naturally.
    """
    return create_intent_driven_scenario(
        name='certified_buy_hard_sl_loss',
        intent={
            'expect_trade': True,
            'direction': 'BUY',
            'expect_exit': 'HARD_SL',
            'max_cycles_to_entry': 5,
        },
        market_context={
            'trend_strength': 'STRONG',
            'volatility': 'NORMAL',
            'pullbacks': 'ABSENT',
            'rsi_range': [35, 55],
            'sma_separation_min_pct': 0.08,
            'adx_min': 18,
            'candle_quality_min_pct': 65,
        },
        price_script=[
            # Warm-up and natural BUY entry
            {'time': 0, 'action': 'wait', 'duration': 60, 'comment': 'Warm-up phase'},
            {'time': 60, 'action': 'generate_entry_candle', 'symbol': 'EURUSD', 'trend_direction': 'BUY'},
            {'time': 65, 'action': 'wait', 'duration': 10, 'comment': 'Wait for entry'},

            # Adverse price movement: drive price DOWN well beyond typical SL distance
            # For 0.01 lot EURUSD, ~20 pips ≈ $2.00 loss.
            {'time': 80, 'action': 'move_price', 'symbol': 'EURUSD', 'delta_bid': -0.0020, 'duration': 10.0,
             'comment': 'Deterministic move against position to hit hard SL'},
        ],
    )

def get_scenario(name: str) -> Optional[Dict[str, Any]]:
    """Get scenario by name (includes both legacy and certified scenarios)."""
    certified_scenarios = {
        'certified_buy_profit_trailing_exit': get_certified_buy_profit_trailing_exit(),
        'certified_sell_sl_lock_reversal_profit': get_certified_sell_sl_lock_reversal_profit(),
        'certified_false_trend_rejected': get_certified_false_trend_rejected(),
        'certified_high_spread_rejected': get_certified_high_spread_rejected(),
        'certified_lock_contention_stress': get_certified_lock_contention_stress(),
        'certified_buy_hard_sl_loss': get_certified_buy_hard_sl_loss(),
    }
    
    # Import legacy scenarios
    try:
        from sim_live.scenarios import get_scenario as get_legacy_scenario
        legacy_scenarios = {
            'natural_buy_trend_continuation': get_legacy_scenario('natural_buy_trend_continuation'),
            'natural_sell_trend_continuation': get_legacy_scenario('natural_sell_trend_continuation'),
            'profit_zone_then_reversal': get_legacy_scenario('profit_zone_then_reversal'),
            'trailing_stop_multiple_updates': get_legacy_scenario('trailing_stop_multiple_updates'),
            'lock_contention_stress': get_legacy_scenario('lock_contention_stress'),
        }
        all_scenarios = {**certified_scenarios, **legacy_scenarios}
    except ImportError:
        all_scenarios = certified_scenarios
    
    return all_scenarios.get(name)

