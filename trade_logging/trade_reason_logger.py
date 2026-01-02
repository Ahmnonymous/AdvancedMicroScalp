"""
Detailed Trade Reason Logger
Logs comprehensive analysis of why each trade was taken with exact reasons and detailed analysis.
Includes strategy identification and outcome analysis.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from utils.logger_factory import get_logger

def _make_json_serializable(obj):
    """
    Recursively convert objects to JSON-serializable types.
    Handles bool, numpy types, and other non-serializable objects.
    """
    if isinstance(obj, bool):
        return bool(obj)  # Ensure native Python bool
    elif isinstance(obj, (int, float, str, type(None))):
        return obj
    elif isinstance(obj, dict):
        return {key: _make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]
    else:
        # Try to convert to string for unknown types
        try:
            # Try to convert numpy types and other special types
            if hasattr(obj, 'item'):  # numpy scalar
                return obj.item()
            elif hasattr(obj, 'tolist'):  # numpy array
                return obj.tolist()
            else:
                return str(obj)
        except Exception:
            return str(obj)

class TradeReasonLogger:
    """Logs detailed trade execution reasons with comprehensive analysis."""
    
    def __init__(self, is_backtest: bool = False, mt5_connector=None, order_manager=None):
        """
        Initialize Trade Reason Logger.
        
        Args:
            is_backtest: If True, log to backtest directory
            mt5_connector: MT5 connector instance for historical data access (optional)
            order_manager: Order manager instance for symbol info access (optional)
        """
        self.is_backtest = is_backtest
        self.mt5_connector = mt5_connector
        self.order_manager = order_manager
        
        # Create log directory
        log_dir = Path("logs/backtest/trades/reasons" if is_backtest else "logs/live/trades/reasons")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create timestamped log file
        timestamp = datetime.now().strftime("%Y%m%d")
        log_file = log_dir / f"trade_reasons_{timestamp}.jsonl"
        
        # Open log file in append mode
        self.log_file = open(log_file, 'a', encoding='utf-8')
        
        # Text summary log disabled to save storage space (JSONL format is sufficient)
        self.text_logger = None
        
        # Track open trades for outcome logging
        self._open_trades = {}  # {ticket: {strategy_id, entry_data, ...}}
    
    def log_trade_reason(
        self,
        symbol: str,
        ticket: int,
        signal: str,
        opportunity: Dict[str, Any],
        execution_result: Dict[str, Any],
        **kwargs
    ):
        """
        Log detailed trade reason with comprehensive analysis.
        
        Args:
            symbol: Trading symbol
            ticket: Position ticket number
            signal: 'LONG' or 'SHORT'
            opportunity: Full opportunity dictionary with all filters and scores
            execution_result: Trade execution result dictionary
            **kwargs: Additional context
        """
        timestamp = datetime.now().isoformat()
        
        # Extract all relevant information from opportunity
        quality_score = opportunity.get('quality_score', 0.0)
        trend_signal = opportunity.get('trend_signal', {})
        sma_fast = opportunity.get('sma_fast', 0.0)
        sma_slow = opportunity.get('sma_slow', 0.0)
        rsi = opportunity.get('rsi', 50.0)
        # Handle both 'spread' and 'spread_points' keys
        spread_points = opportunity.get('spread_points', opportunity.get('spread', 0.0))
        spread_fees_cost = opportunity.get('spread_fees_cost', 0.0)
        trend_strength = opportunity.get('trend_strength', 0.0)
        high_quality_setup = opportunity.get('high_quality_setup', False)
        quality_assessment = opportunity.get('quality_assessment', {})
        atr = opportunity.get('atr', trend_signal.get('atr', 0.0))
        
        # Extract config values from kwargs or use defaults
        config = kwargs.get('config', {})
        trading_config = config.get('trading', {})
        risk_config = config.get('risk', {})
        
        # RSI filter limits (from opportunity or config)
        rsi_entry_range_min = opportunity.get('rsi_entry_range_min', trading_config.get('rsi_entry_range_min', 15.0))
        rsi_entry_range_max = opportunity.get('rsi_entry_range_max', trading_config.get('rsi_entry_range_max', 80.0))
        
        # Spread filter limit (from opportunity or default)
        max_spread_points = opportunity.get('max_spread_points', kwargs.get('max_spread_points', 2.0))
        
        # Volatility filter limit (min ATR) - from opportunity or config
        min_atr_pips = risk_config.get('entry_filters', {}).get('volatility_floor', {}).get('min_range_pips', 0.8)
        # Convert pips to ATR value (approximate - adjust based on symbol)
        min_atr_value = min_atr_pips * 0.0001  # Rough conversion for 5-digit pairs
        
        # Quality threshold (from opportunity or config)
        min_quality_score = opportunity.get('min_quality_score', trading_config.get('min_quality_score', 50.0))
        
        # Store config values in opportunity for buffer calculations (if not already present)
        if 'rsi_entry_range_min' not in opportunity:
            opportunity['rsi_entry_range_min'] = rsi_entry_range_min
        if 'rsi_entry_range_max' not in opportunity:
            opportunity['rsi_entry_range_max'] = rsi_entry_range_max
        if 'max_spread_points' not in opportunity:
            opportunity['max_spread_points'] = max_spread_points
        if 'min_atr' not in opportunity:
            opportunity['min_atr'] = min_atr_value
        if 'min_quality_score' not in opportunity:
            opportunity['min_quality_score'] = min_quality_score
        
        # Extract filter results - ensure all are native Python booleans
        filters_passed = {
            'news_filter': bool(opportunity.get('news_filter_passed', True)),
            'volume_filter': bool(opportunity.get('volume_filter_passed', True)),
            'market_closing_filter': bool(opportunity.get('market_closing_filter_passed', True)),
            'rsi_filter': bool(opportunity.get('rsi_filter_passed', True)),
            'spread_filter': bool(opportunity.get('spread_filter_passed', True)),
            'volatility_filter': bool(opportunity.get('volatility_filter_passed', True)),
            'trend_strength_filter': bool(opportunity.get('trend_strength_filter_passed', True)),
        }
        
        # Extract execution details
        entry_price = execution_result.get('entry_price_actual', 0.0)
        lot_size = execution_result.get('lot_size', 0.01)
        stop_loss_price = execution_result.get('stop_loss_price', 0.0)
        take_profit_price = execution_result.get('take_profit_price')  # Can be None
        risk_usd = execution_result.get('risk_usd', 0.0)
        slippage = execution_result.get('slippage', 0.0)
        
        # Calculate filter buffers (rejection distance)
        filter_buffers = self._calculate_filter_buffers(opportunity, rsi, spread_points, atr)
        
        # Determine decision path
        decision_path = self._determine_decision_path(opportunity, signal, sma_fast, sma_slow, rsi, quality_score)
        
        # Classify market regime
        market_regime = self._classify_market_regime(opportunity, trend_signal, atr)
        
        # Get quality score breakdown
        quality_breakdown = self._get_quality_score_breakdown(quality_assessment, quality_score)
        
        # Calculate trade expectation
        trade_expectation = self._calculate_trade_expectation(
            opportunity, signal, entry_price, stop_loss_price, take_profit_price, atr
        )
        
        # Generate human summary
        human_summary = self._generate_human_summary(
            signal, opportunity, sma_fast, sma_slow, rsi, quality_score, trend_strength
        )
        
        # Generate strategy ID
        try:
            strategy_id = self._generate_strategy_id(opportunity, signal)
            strategy_name = self._get_strategy_name(strategy_id, opportunity, signal)
        except AttributeError as e:
            # Fallback if method doesn't exist (shouldn't happen, but handle gracefully)
            strategy_id = f"{signal}_FALLBACK_{datetime.now().timestamp()}"
            strategy_name = f"{signal} Strategy (Fallback)"
            if self.text_logger:
                self.text_logger.warning(f"Strategy ID generation failed: {e}, using fallback")
        except Exception as e:
            # Fallback for any other error
            strategy_id = f"{signal}_FALLBACK_{datetime.now().timestamp()}"
            strategy_name = f"{signal} Strategy (Fallback)"
            if self.text_logger:
                self.text_logger.warning(f"Strategy ID generation failed: {e}, using fallback")
        
        # Store trade data for outcome logging
        self._open_trades[ticket] = {
            'strategy_id': strategy_id,
            'strategy_name': strategy_name,
            'symbol': symbol,
            'signal': signal,
            'entry_time': timestamp,
            'entry_price': entry_price,
            'stop_loss_price': stop_loss_price,
            'take_profit_price': take_profit_price,
            'quality_score': quality_score,
            'trend_strength': trend_strength,
            'lot_size': lot_size,  # Store lot_size for post-trade analysis
            'opportunity': opportunity.copy()
        }
        
        # Build comprehensive reason analysis
        reason_analysis = {
            'timestamp': timestamp,
            'symbol': symbol,
            'ticket': ticket,
            'signal': signal,
            'execution_status': bool(execution_result.get('success', False)),
            
            # Strategy Information
            'strategy': {
                'strategy_id': strategy_id,
                'strategy_name': strategy_name,
                'strategy_description': self._get_strategy_description(strategy_id, opportunity, signal),
                'entry_logic': self._get_entry_logic_description(opportunity, signal),
                'filter_configuration': self._get_filter_configuration(opportunity),
            },
            
            # Quality Metrics
            'quality_score': quality_score,
            'high_quality_setup': bool(high_quality_setup),
            'trend_strength_pct': trend_strength * 100 if trend_strength else 0.0,
            
            # Technical Indicators
            'indicators': {
                'sma_fast': sma_fast,
                'sma_slow': sma_slow,
                'rsi': rsi,
                'trend_direction': trend_signal.get('signal', 'NONE'),
                'sma_separation_pct': trend_signal.get('separation_pct', 0.0) * 100 if trend_signal.get('separation_pct') else 0.0,
                'atr': atr,
            },
            
            # Decision Path
            'decision_path': decision_path,
            
            # Market Regime
            'market_regime': market_regime,
            
            # Filter Analysis with Buffers
            'filters': {
                'all_passed': all(filters_passed.values()),
                'details': filters_passed,
                'rejection_reasons': self._extract_rejection_reasons(opportunity),
                'buffers': filter_buffers,
            },
            
            # Quality Score Breakdown
            'quality_breakdown': quality_breakdown,
            
            # Cost Analysis
            'costs': {
                'spread_points': spread_points,
                'spread_fees_usd': spread_fees_cost,
                'slippage': slippage,
                'total_cost_usd': spread_fees_cost + (abs(slippage) * lot_size * 100000 if slippage else 0),
            },
            
            # Risk Analysis
            'risk': {
                'risk_usd': risk_usd,
                'max_risk_usd': opportunity.get('max_risk_usd', 3.0),
                'lot_size': lot_size,
                'stop_loss_price': stop_loss_price,
                'stop_loss_distance': abs(entry_price - stop_loss_price) if entry_price and stop_loss_price else 0.0,
                'risk_reward_ratio': self._calculate_risk_reward_ratio(entry_price, stop_loss_price, take_profit_price, signal),
            },
            
            # Execution Details
            'execution': {
                'entry_price_requested': execution_result.get('entry_price_requested', entry_price),
                'entry_price_actual': entry_price,
                'take_profit_price': take_profit_price if take_profit_price is not None else None,
                'execution_time_seconds': execution_result.get('execution_time', 0.0),
            },
            
            # Trade Expectation
            'expectation': trade_expectation,
            
            # Human Summary
            'human_summary': human_summary,
            
            # Primary Reason (why trade was taken) - kept for backward compatibility
            'primary_reason': self._determine_primary_reason(opportunity, quality_score, signal),
            
            # Detailed Analysis
            'detailed_analysis': self._generate_detailed_analysis(opportunity, signal, quality_score),
        }
        
        # Write JSONL entry - ensure all values are JSON-serializable
        try:
            # Convert all values to JSON-serializable types
            serializable_analysis = _make_json_serializable(reason_analysis)
            json.dump(serializable_analysis, self.log_file, ensure_ascii=False)
            self.log_file.write('\n')
            self.log_file.flush()
        except (TypeError, ValueError) as e:
            # If serialization still fails, log error but don't crash
            if self.text_logger:
                self.text_logger.error(f"JSON serialization error for ticket {ticket}: {e}")
            # Try to write a minimal entry instead
            minimal_entry = {
                'timestamp': timestamp,
                'symbol': symbol,
                'ticket': ticket,
                'signal': signal,
                'error': f"Serialization failed: {str(e)}"
            }
            json.dump(minimal_entry, self.log_file, ensure_ascii=False)
        self.log_file.write('\n')
        self.log_file.flush()
        
        # Text log writing disabled to save storage space
    
    def _extract_rejection_reasons(self, opportunity: Dict[str, Any]) -> list:
        """Extract all rejection reasons from opportunity."""
        reasons = []
        
        if not opportunity.get('news_filter_passed', True):
            reasons.append(f"News filter: {opportunity.get('news_filter_reason', 'High-impact news blocking')}")
        if not opportunity.get('volume_filter_passed', True):
            reasons.append(f"Volume filter: {opportunity.get('volume_filter_reason', 'Insufficient volume')}")
        if not opportunity.get('market_closing_filter_passed', True):
            reasons.append(f"Market closing filter: {opportunity.get('market_closing_filter_reason', 'Market closing soon')}")
        if not opportunity.get('rsi_filter_passed', True):
            rsi = opportunity.get('rsi', 0.0)
            reasons.append(f"RSI filter: RSI {rsi:.1f} not in range 15-80")
        if not opportunity.get('spread_filter_passed', True):
            reasons.append(f"Spread filter: {opportunity.get('spread_filter_reason', 'Spread too high')}")
        if not opportunity.get('volatility_filter_passed', True):
            reasons.append(f"Volatility filter: {opportunity.get('volatility_filter_reason', 'Volatility too low')}")
        if not opportunity.get('trend_strength_filter_passed', True):
            reasons.append(f"Trend strength filter: {opportunity.get('trend_strength_filter_reason', 'Trend too weak')}")
        
        return reasons
    
    def _determine_primary_reason(self, opportunity: Dict[str, Any], quality_score: float, signal: str) -> str:
        """Determine the primary reason why this trade was taken."""
        reasons = []
        
        # Quality score
        if quality_score >= 85:
            reasons.append(f"High quality score ({quality_score:.1f})")
        elif quality_score >= 70:
            reasons.append(f"Good quality score ({quality_score:.1f})")
        else:
            reasons.append(f"Quality score ({quality_score:.1f})")
        
        # Trend strength
        trend_strength = opportunity.get('trend_strength', 0.0)
        if trend_strength and trend_strength > 0.05:
            reasons.append(f"Strong trend ({trend_strength*100:.2f}% separation)")
        
        # High quality setup
        if opportunity.get('high_quality_setup', False):
            reasons.append("High quality setup detected")
        
        # Signal confirmation
        reasons.append(f"{signal} signal confirmed")
        
        # All filters passed
        if all([
            opportunity.get('news_filter_passed', True),
            opportunity.get('volume_filter_passed', True),
            opportunity.get('rsi_filter_passed', True),
            opportunity.get('spread_filter_passed', True),
        ]):
            reasons.append("All entry filters passed")
        
        return " | ".join(reasons)
    
    def _calculate_filter_buffers(self, opportunity: Dict[str, Any], rsi: float, spread_points: float, atr: float) -> Dict[str, Any]:
        """Calculate how close each filter was to failing (rejection distance)."""
        buffers = {}
        
        # RSI filter buffer
        rsi_min = opportunity.get('rsi_entry_range_min', 15.0)
        rsi_max = opportunity.get('rsi_entry_range_max', 80.0)
        if rsi_min <= rsi <= rsi_max:
            # Calculate buffer to nearest limit
            buffer_to_min = rsi - rsi_min
            buffer_to_max = rsi_max - rsi
            rsi_buffer = min(buffer_to_min, buffer_to_max)
            buffers['rsi'] = {
                'value': rsi,
                'min_limit': rsi_min,
                'max_limit': rsi_max,
                'buffer': rsi_buffer,
                'passed': True
            }
        else:
            buffers['rsi'] = {
                'value': rsi,
                'min_limit': rsi_min,
                'max_limit': rsi_max,
                'buffer': 0.0,
                'passed': False
            }
        
        # Spread filter buffer
        max_spread = opportunity.get('max_spread_points', 2.0)
        spread_buffer = max_spread - spread_points if spread_points <= max_spread else 0.0
        buffers['spread'] = {
            'value': spread_points,
            'max_limit': max_spread,
            'buffer': spread_buffer,
            'passed': spread_points <= max_spread
        }
        
        # Volatility filter buffer (ATR-based)
        min_atr = opportunity.get('min_atr', 0.010)
        if atr > 0:
            volatility_buffer = atr - min_atr if atr >= min_atr else 0.0
            buffers['volatility'] = {
                'value': atr,
                'min_limit': min_atr,
                'buffer': volatility_buffer,
                'passed': atr >= min_atr
            }
        else:
            buffers['volatility'] = {
                'value': atr,
                'min_limit': min_atr,
                'buffer': 0.0,
                'passed': False
            }
        
        return buffers
    
    def _determine_decision_path(self, opportunity: Dict[str, Any], signal: str, sma_fast: float, sma_slow: float, rsi: float, quality_score: float) -> Dict[str, Any]:
        """Determine the decision path - what actually triggered the entry."""
        trend_signal = opportunity.get('trend_signal', {})
        trend_direction = trend_signal.get('signal', 'NONE')
        
        # Determine primary trigger
        primary_trigger = None
        if signal == 'LONG':
            if sma_fast > sma_slow:
                primary_trigger = "SMA Fast crossed above SMA Slow"
            else:
                primary_trigger = "SMA alignment (Fast > Slow)"
        elif signal == 'SHORT':
            if sma_fast < sma_slow:
                primary_trigger = "SMA Fast crossed below SMA Slow"
            else:
                primary_trigger = "SMA alignment (Fast < Slow)"
        else:
            primary_trigger = f"{signal} signal detected"
        
        # Build confirmations
        confirmations = []
        
        # RSI confirmation
        if signal == 'LONG' and rsi < 50:
            confirmations.append(f"RSI below neutral ({rsi:.1f} < 50)")
        elif signal == 'SHORT' and rsi > 50:
            confirmations.append(f"RSI above neutral ({rsi:.1f} > 50)")
        elif 25 <= rsi <= 75:
            confirmations.append(f"RSI in neutral range ({rsi:.1f})")
        
        # Trend direction confirmation
        if trend_direction == signal:
            confirmations.append(f"Trend direction confirmed {signal}")
        elif trend_direction != 'NONE':
            confirmations.append(f"Trend direction: {trend_direction}")
        
        # Quality score confirmation
        min_quality = opportunity.get('min_quality_score', 50.0)
        if quality_score >= min_quality:
            confirmations.append(f"Quality score exceeded threshold ({quality_score:.1f} >= {min_quality:.1f})")
        
        return {
            'primary_trigger': primary_trigger,
            'confirmations': confirmations,
            'total_confirmations': len(confirmations)
        }
    
    def _classify_market_regime(self, opportunity: Dict[str, Any], trend_signal: Dict[str, Any], atr: float) -> Dict[str, Any]:
        """Classify the current market regime."""
        trend_strength = opportunity.get('trend_strength', 0.0)
        trend_direction = trend_signal.get('signal', 'NONE')
        sma_separation_pct = trend_signal.get('separation_pct', 0.0) * 100 if trend_signal.get('separation_pct') else 0.0
        
        # Determine regime type
        if trend_strength > 0.05:
            regime_type = "Strong Trend"
        elif trend_strength > 0.02:
            regime_type = "Moderate Trend"
        elif trend_strength > 0.001:
            regime_type = "Weak Trend / Range-Biased"
        else:
            regime_type = "Range / Consolidation"
        
        # Determine volatility level
        # Compare ATR to typical values (this is a heuristic - adjust based on your symbols)
        if atr > 0.020:
            volatility_level = "High"
        elif atr > 0.010:
            volatility_level = "Medium"
        elif atr > 0.005:
            volatility_level = "Low"
        else:
            volatility_level = "Very Low"
        
        # Determine session (simplified - could be enhanced with actual timezone logic)
        now = datetime.now()
        hour_gmt = now.hour  # Simplified - assumes local time is GMT
        if 22 <= hour_gmt or hour_gmt < 6:
            session = "Asian"
        elif 6 <= hour_gmt < 13:
            session = "London"
        elif 13 <= hour_gmt < 22:
            session = "London-New York Overlap"
        else:
            session = "New York"
        
        return {
            'type': regime_type,
            'volatility': volatility_level,
            'session': session,
            'trend_direction': trend_direction,
            'trend_strength_pct': trend_strength * 100 if trend_strength else 0.0
        }
    
    def _get_quality_score_breakdown(self, quality_assessment: Dict[str, Any], total_score: float) -> Dict[str, Any]:
        """Extract quality score component breakdown."""
        # Try to extract from quality_assessment if available
        # assess_setup_quality doesn't return component scores directly, so we estimate from reasons
        breakdown = {
            'trend_component': 0,
            'trend_max': 35,
            'momentum_component': 0,
            'momentum_max': 20,
            'volatility_component': 0,
            'volatility_max': 10,
            'candle_quality': 0,
            'candle_max': 15,
            'choppiness_component': 0,
            'choppiness_max': 20,
            'spread_penalty': 0,
            'spread_penalty_max': -15,
            'total': total_score,
            'total_max': 100
        }
        
        # If quality_assessment has component scores, use them
        if 'component_scores' in quality_assessment:
            breakdown.update(quality_assessment['component_scores'])
        elif 'trend_score' in quality_assessment:
            # Direct component scores available
            breakdown['trend_component'] = quality_assessment.get('trend_score', 0)
            breakdown['momentum_component'] = quality_assessment.get('rsi_score', 0)
            breakdown['volatility_component'] = quality_assessment.get('volatility_score', 0)
            breakdown['candle_quality'] = quality_assessment.get('candle_score', 0)
            breakdown['choppiness_component'] = quality_assessment.get('choppiness_score', 0)
            breakdown['spread_penalty'] = quality_assessment.get('spread_penalty', 0)
        else:
            # Estimate from reasons and available data
            reasons = quality_assessment.get('reasons', [])
            reasons_str = ' '.join(reasons).lower()
            
            # Estimate trend component (35 max)
            if 'strong trend' in reasons_str:
                breakdown['trend_component'] = 30
            elif 'moderate trend' in reasons_str:
                breakdown['trend_component'] = 20
            elif 'weak trend' in reasons_str:
                breakdown['trend_component'] = 10
            else:
                breakdown['trend_component'] = 15  # Default estimate
            
            # Estimate momentum component (20 max)
            if 'rsi confirmation' in reasons_str:
                breakdown['momentum_component'] = 20
            elif 'rsi acceptable' in reasons_str:
                breakdown['momentum_component'] = 10
            else:
                breakdown['momentum_component'] = 15  # Default estimate
            
            # Estimate volatility component (10 max)
            if 'volatility floor' in reasons_str:
                breakdown['volatility_component'] = 10
            else:
                breakdown['volatility_component'] = 5  # Default estimate
            
            # Estimate candle quality (15 max)
            if 'candle quality' in reasons_str:
                breakdown['candle_quality'] = 15
            else:
                breakdown['candle_quality'] = 10  # Default estimate
            
            # Estimate choppiness (20 max)
            if 'low choppiness' in reasons_str:
                breakdown['choppiness_component'] = 20
            elif 'choppy' in reasons_str:
                breakdown['choppiness_component'] = 0
            else:
                breakdown['choppiness_component'] = 15  # Default estimate
            
            # Estimate spread penalty (negative)
            if 'spread penalty' in reasons_str:
                breakdown['spread_penalty'] = -10  # Default penalty
            else:
                breakdown['spread_penalty'] = 0
        
        return breakdown
    
    def _calculate_trade_expectation(self, opportunity: Dict[str, Any], signal: str, entry_price: float, stop_loss_price: float, take_profit_price: float, atr: float) -> Dict[str, Any]:
        """Calculate trade expectations - expected move, time horizon, exit conditions."""
        if not entry_price or not stop_loss_price:
            return {
                'expected_move_pct': 0.0,
                'time_horizon': "Unknown",
                'ideal_exit': "Unknown",
                'failure_condition': "Unknown"
            }
        
        # Calculate expected move percentage
        if take_profit_price is not None and entry_price > 0:
            if signal == 'LONG' and take_profit_price > entry_price:
                expected_move = ((take_profit_price - entry_price) / entry_price) * 100
            elif signal == 'SHORT' and take_profit_price < entry_price:
                expected_move = ((entry_price - take_profit_price) / entry_price) * 100
            else:
                # Fallback: estimate based on ATR
                if atr > 0 and entry_price > 0:
                    expected_move = (atr / entry_price) * 100
                else:
                    expected_move = 0.35  # Default estimate
        else:
            # Fallback: estimate based on ATR when take_profit_price is None
            if atr > 0 and entry_price > 0:
                expected_move = (atr / entry_price) * 100
            else:
                expected_move = 0.35  # Default estimate
        
        # Estimate time horizon based on ATR and typical scalping behavior
        if atr > 0:
            # For scalping, typical moves happen within 15-45 minutes
            time_horizon = "15–45 minutes"
        else:
            time_horizon = "15–45 minutes"
        
        # Determine ideal exit
        trend_signal = opportunity.get('trend_signal', {})
        trend_direction = trend_signal.get('signal', 'NONE')
        if trend_direction == signal:
            ideal_exit = "Momentum continuation"
        else:
            ideal_exit = "Take profit target or trailing stop"
        
        # Determine failure condition
        if signal == 'LONG':
            failure_condition = "RSI divergence or SMA flattening/reversal"
        else:
            failure_condition = "RSI divergence or SMA flattening/reversal"
        
        return {
            'expected_move_pct': expected_move,
            'time_horizon': time_horizon,
            'ideal_exit': ideal_exit,
            'failure_condition': failure_condition
        }
    
    def _calculate_risk_reward_ratio(self, entry_price: float, stop_loss_price: float, take_profit_price: float, signal: str) -> float:
        """Calculate risk:reward ratio."""
        if not entry_price or not stop_loss_price or not take_profit_price:
            return 0.0
        
        if signal == 'LONG':
            risk = entry_price - stop_loss_price
            reward = take_profit_price - entry_price
        else:  # SHORT
            risk = stop_loss_price - entry_price
            reward = entry_price - take_profit_price
        
        if risk > 0:
            return reward / risk
        return 0.0
    
    def _generate_human_summary(self, signal: str, opportunity: Dict[str, Any], sma_fast: float, sma_slow: float, rsi: float, quality_score: float, trend_strength: float) -> str:
        """Generate a one-line human-readable summary of the trade reason."""
        trend_signal = opportunity.get('trend_signal', {})
        trend_direction = trend_signal.get('signal', 'NONE')
        
        # Build summary components
        parts = []
        
        # Entry direction
        parts.append(f"Entered {signal}")
        
        # Primary reason
        if signal == 'LONG' and sma_fast > sma_slow:
            parts.append("as price rejected SMA slow")
        elif signal == 'SHORT' and sma_fast < sma_slow:
            parts.append("as price rejected SMA slow")
        else:
            parts.append("on trend signal")
        
        # Momentum
        if signal == 'LONG' and rsi < 50:
            parts.append("with weak momentum")
        elif signal == 'SHORT' and rsi > 50:
            parts.append("with weak momentum")
        elif 25 <= rsi <= 75:
            parts.append("with neutral momentum")
        else:
            parts.append("with aligned momentum")
        
        # Volatility/risk
        spread_points = opportunity.get('spread_points', 0.0)
        if spread_points < 1.0:
            parts.append("and low execution cost")
        else:
            parts.append("with acceptable execution cost")
        
        # Quality
        if quality_score >= 85:
            parts.append("(high quality setup)")
        elif quality_score >= 70:
            parts.append("(good quality setup)")
        
        return " ".join(parts) + "."
    
    def _generate_detailed_analysis(self, opportunity: Dict[str, Any], signal: str, quality_score: float) -> Dict[str, Any]:
        """Generate detailed analysis of trade decision."""
        analysis = {
            'signal_confidence': 'HIGH' if quality_score >= 85 else 'MEDIUM' if quality_score >= 70 else 'LOW',
            'trend_alignment': 'STRONG' if opportunity.get('trend_strength', 0.0) > 0.05 else 'MODERATE' if opportunity.get('trend_strength', 0.0) > 0.02 else 'WEAK',
            'market_conditions': {
                'spread_acceptable': opportunity.get('spread_filter_passed', True),
                'volume_sufficient': opportunity.get('volume_filter_passed', True),
                'volatility_adequate': opportunity.get('volatility_filter_passed', True),
            },
            'risk_reward': {
                'risk_usd': opportunity.get('risk_usd', 3.0),
                'target_profit_usd': opportunity.get('take_profit_usd', 1.0),
                'risk_reward_ratio': (opportunity.get('take_profit_usd', 1.0) / opportunity.get('risk_usd', 3.0)) if opportunity.get('risk_usd', 0) > 0 else 0.0,
            },
        }
        return analysis
    
    def _write_text_log(self, reason_analysis: Dict[str, Any]):
        """Write formatted text log entry."""
        self.text_logger.info("=" * 100)
        self.text_logger.info(f"TRADE REASON ANALYSIS - {reason_analysis['symbol']} Ticket {reason_analysis['ticket']}")
        self.text_logger.info("=" * 100)
        
        # DECISION PATH
        decision_path = reason_analysis.get('decision_path', {})
        self.text_logger.info("DECISION PATH:")
        self.text_logger.info(f"  Primary Trigger: {decision_path.get('primary_trigger', 'Unknown')}")
        for i, confirmation in enumerate(decision_path.get('confirmations', []), 1):
            self.text_logger.info(f"  Confirmation {i}: {confirmation}")
        self.text_logger.info("")
        
        # MARKET REGIME
        market_regime = reason_analysis.get('market_regime', {})
        self.text_logger.info("MARKET REGIME:")
        self.text_logger.info(f"  Type: {market_regime.get('type', 'Unknown')}")
        self.text_logger.info(f"  Volatility: {market_regime.get('volatility', 'Unknown')}")
        self.text_logger.info(f"  Session: {market_regime.get('session', 'Unknown')}")
        self.text_logger.info("")
        
        # TECHNICAL SNAPSHOT
        indicators = reason_analysis['indicators']
        self.text_logger.info("TECHNICAL SNAPSHOT:")
        self.text_logger.info(f"  SMA Fast / Slow: {indicators['sma_fast']:.5f} / {indicators['sma_slow']:.5f}")
        self.text_logger.info(f"  RSI: {indicators['rsi']:.1f}")
        self.text_logger.info(f"  Trend Strength: {reason_analysis['trend_strength_pct']:.4f}%")
        self.text_logger.info("")
        
        # FILTER BUFFERS (Rejection Distance)
        filters = reason_analysis['filters']
        buffers = filters.get('buffers', {})
        self.text_logger.info("FILTER BUFFERS:")
        if 'rsi' in buffers:
            rsi_buf = buffers['rsi']
            status = "✓ PASS" if rsi_buf.get('passed', False) else "✗ FAIL"
            self.text_logger.info(f"  rsi_filter: {status} (RSI={rsi_buf.get('value', 0):.1f} | Range={rsi_buf.get('min_limit', 0):.1f}-{rsi_buf.get('max_limit', 0):.1f} | Buffer={rsi_buf.get('buffer', 0):.1f})")
        if 'spread' in buffers:
            spread_buf = buffers['spread']
            status = "✓ PASS" if spread_buf.get('passed', False) else "✗ FAIL"
            self.text_logger.info(f"  spread_filter: {status} (Spread={spread_buf.get('value', 0):.2f} | Max={spread_buf.get('max_limit', 0):.2f} | Buffer={spread_buf.get('buffer', 0):.2f})")
        if 'volatility' in buffers:
            vol_buf = buffers['volatility']
            status = "✓ PASS" if vol_buf.get('passed', False) else "✗ FAIL"
            self.text_logger.info(f"  volatility_filter: {status} (ATR={vol_buf.get('value', 0):.5f} | Min={vol_buf.get('min_limit', 0):.5f} | Buffer={vol_buf.get('buffer', 0):.5f})")
        self.text_logger.info("")
        
        # QUALITY SCORE BREAKDOWN
        quality_breakdown = reason_analysis.get('quality_breakdown', {})
        self.text_logger.info("QUALITY SCORE BREAKDOWN:")
        self.text_logger.info(f"  Trend Component: {quality_breakdown.get('trend_component', 0)} / {quality_breakdown.get('trend_max', 35)}")
        self.text_logger.info(f"  Momentum Component: {quality_breakdown.get('momentum_component', 0)} / {quality_breakdown.get('momentum_max', 20)}")
        self.text_logger.info(f"  Volatility Component: {quality_breakdown.get('volatility_component', 0)} / {quality_breakdown.get('volatility_max', 10)}")
        self.text_logger.info(f"  Candle Quality: {quality_breakdown.get('candle_quality', 0)} / {quality_breakdown.get('candle_max', 15)}")
        if quality_breakdown.get('choppiness_component', 0) > 0:
            self.text_logger.info(f"  Choppiness Component: {quality_breakdown.get('choppiness_component', 0)} / {quality_breakdown.get('choppiness_max', 20)}")
        if quality_breakdown.get('spread_penalty', 0) < 0:
            self.text_logger.info(f"  Spread Penalty: {quality_breakdown.get('spread_penalty', 0)} / {quality_breakdown.get('spread_penalty_max', -15)}")
        self.text_logger.info(f"  {'-' * 30}")
        self.text_logger.info(f"  TOTAL: {quality_breakdown.get('total', 0)} / {quality_breakdown.get('total_max', 100)}")
        self.text_logger.info("")
        
        # RISK & COST
        risk = reason_analysis['risk']
        costs = reason_analysis['costs']
        self.text_logger.info("RISK & COST:")
        self.text_logger.info(f"  Risk: ${risk['risk_usd']:.2f} / ${risk['max_risk_usd']:.2f}")
        rr_ratio = risk.get('risk_reward_ratio', 0.0)
        if rr_ratio > 0:
            self.text_logger.info(f"  R:R Planned: 1:{rr_ratio:.2f}")
        self.text_logger.info(f"  Spread Cost: ${costs['spread_fees_usd']:.2f}")
        self.text_logger.info("")
        
        # EXPECTATION
        expectation = reason_analysis.get('expectation', {})
        self.text_logger.info("EXPECTATION:")
        self.text_logger.info(f"  Move: {expectation.get('expected_move_pct', 0):.2f}%")
        self.text_logger.info(f"  Duration: {expectation.get('time_horizon', 'Unknown')}")
        self.text_logger.info(f"  Ideal Exit: {expectation.get('ideal_exit', 'Unknown')}")
        self.text_logger.info(f"  Failure Condition: {expectation.get('failure_condition', 'Unknown')}")
        self.text_logger.info("")
        
        # HUMAN SUMMARY
        human_summary = reason_analysis.get('human_summary', '')
        if human_summary:
            self.text_logger.info("HUMAN SUMMARY:")
            self.text_logger.info(f"  \"{human_summary}\"")
            self.text_logger.info("")
        
        self.text_logger.info("=" * 100)
        self.text_logger.info("")
    
    def _generate_strategy_id(self, opportunity: Dict[str, Any], signal: str) -> str:
        """Generate strategy ID from opportunity."""
        try:
            from strategies.strategy_fingerprint import StrategyFingerprint
            fingerprint = StrategyFingerprint()
            return fingerprint.generate_strategy_id(opportunity)
        except Exception as e:
            # Fallback: simple strategy ID
            trend_signal = opportunity.get('trend_signal', {})
            sma_fast = trend_signal.get('sma_fast', 20)
            sma_slow = trend_signal.get('sma_slow', 50)
            rsi = opportunity.get('rsi', 50.0)
            quality_score = opportunity.get('quality_score', 0.0)
            return f"{signal}_SMA{int(sma_fast)}x{int(sma_slow)}_RSI{int(rsi)}_Q{int(quality_score)}"
    
    def _get_strategy_name(self, strategy_id: str, opportunity: Dict[str, Any], signal: str) -> str:
        """Get human-readable strategy name."""
        # Extract components from strategy_id
        if '_' in strategy_id:
            parts = strategy_id.split('_')
            direction = parts[0] if parts else signal
            entry_cluster = parts[1] if len(parts) > 1 else 'SMA20x50'
            
            # Build readable name
            if 'SMA' in entry_cluster:
                return f"{direction} {entry_cluster} Strategy"
            else:
                return f"{direction} {entry_cluster} Strategy"
        return f"{signal} Strategy"
    
    def _get_strategy_description(self, strategy_id: str, opportunity: Dict[str, Any], signal: str) -> str:
        """Get detailed strategy description."""
        trend_signal = opportunity.get('trend_signal', {})
        sma_fast = trend_signal.get('sma_fast', 20)
        sma_slow = trend_signal.get('sma_slow', 50)
        rsi = opportunity.get('rsi', 50.0)
        quality_score = opportunity.get('quality_score', 0.0)
        
        description = f"{signal} entry on SMA{int(sma_fast)} crossing SMA{int(sma_slow)}"
        description += f" with RSI {rsi:.1f}"
        description += f" (Quality: {quality_score:.1f})"
        
        return description
    
    def _get_entry_logic_description(self, opportunity: Dict[str, Any], signal: str) -> str:
        """Get entry logic description."""
        trend_signal = opportunity.get('trend_signal', {})
        trend_direction = trend_signal.get('signal', 'NONE')
        
        if signal == 'LONG':
            return f"Enter LONG when SMA Fast > SMA Slow and trend confirms {trend_direction}"
        else:
            return f"Enter SHORT when SMA Fast < SMA Slow and trend confirms {trend_direction}"
    
    def _get_filter_configuration(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        """Get filter configuration used."""
        return {
            'news_filter': bool(opportunity.get('news_filter_passed', True)),
            'volume_filter': bool(opportunity.get('volume_filter_passed', True)),
            'market_closing_filter': bool(opportunity.get('market_closing_filter_passed', True)),
            'rsi_filter': bool(opportunity.get('rsi_filter_passed', True)),
            'spread_filter': bool(opportunity.get('spread_filter_passed', True)),
            'volatility_filter': bool(opportunity.get('volatility_filter_passed', True)),
            'trend_strength_filter': bool(opportunity.get('trend_strength_filter_passed', True)),
        }
    
    def _analyze_post_trade_price_movement(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        signal: str,
        entry_time: datetime,
        lot_size: float,
        duration_minutes: float,
        actual_profit_usd: float
    ) -> Dict[str, Any]:
        """
        Analyze price movement after trade closure to determine maximum profit potential.
        
        Returns:
            Dictionary with max_profit_usd, max_profit_price, max_profit_time, etc.
        """
        analysis = {
            'max_profit_usd': actual_profit_usd,  # Default to actual profit
            'max_profit_price': exit_price,  # Default to exit price
            'max_profit_time_minutes': duration_minutes,  # Default to duration
            'profit_left_on_table_usd': 0.0,
            'max_profit_achieved_after_close': False,
            'analysis_available': False
        }
        
        # Only analyze if we have MT5 connector and order manager
        if not self.mt5_connector or not self.order_manager:
            return analysis
        
        try:
            # Get symbol info for profit calculation
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if not symbol_info:
                return analysis
            
            # Get historical price data after trade closure (up to 4 hours or until breakeven would have triggered)
            import MetaTrader5 as mt5
            import time as time_module
            
            # Ensure connection with retry
            max_connection_retries = 3
            connection_retry_delay = 0.5
            connected = False
            for retry in range(max_connection_retries):
                if self.mt5_connector.ensure_connected():
                    connected = True
                    break
                if retry < max_connection_retries - 1:
                    time_module.sleep(connection_retry_delay)
                    connection_retry_delay *= 2  # Exponential backoff
            
            if not connected:
                if self.text_logger:
                    self.text_logger.debug(f"Failed to connect to MT5 for post-trade analysis after {max_connection_retries} retries")
                return analysis
            
            # Calculate time window: from entry to 4 hours after closure (or until breakeven would have triggered)
            from datetime import timedelta
            exit_time = datetime.now()
            analysis_end_time = exit_time + timedelta(hours=4)
            
            # Get M1 candles from entry time to analysis end time with retry logic
            timeframe = mt5.TIMEFRAME_M1
            rates = None
            max_data_retries = 3
            data_retry_delay = 0.5
            
            for retry in range(max_data_retries):
                try:
                    rates = mt5.copy_rates_range(symbol, timeframe, entry_time, analysis_end_time)
                    if rates is not None and len(rates) > 0:
                        break
                except Exception as data_error:
                    if self.text_logger and retry == max_data_retries - 1:
                        self.text_logger.debug(f"Error fetching historical data for {symbol} (attempt {retry + 1}/{max_data_retries}): {data_error}")
                
                if retry < max_data_retries - 1:
                    time_module.sleep(data_retry_delay)
                    data_retry_delay *= 2  # Exponential backoff
            
            # If M1 candles failed, try H1 candles as fallback
            if rates is None or len(rates) == 0:
                if self.text_logger:
                    self.text_logger.debug(f"M1 candles unavailable for {symbol}, trying H1 candles as fallback")
                try:
                    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, entry_time, analysis_end_time)
                except Exception as fallback_error:
                    if self.text_logger:
                        self.text_logger.debug(f"Fallback to H1 candles also failed for {symbol}: {fallback_error}")
            
            if rates is None or len(rates) == 0:
                if self.text_logger:
                    self.text_logger.debug(f"No historical data available for post-trade analysis: {symbol} | Entry: {entry_time} | Exit: {exit_time}")
                return analysis
            
            # Convert to list for easier processing
            import numpy as np
            if isinstance(rates, np.ndarray):
                rates_list = [dict(zip(rates.dtype.names, rate)) for rate in rates]
            else:
                rates_list = rates
            
            # Calculate profit for each candle
            point_value = symbol_info.get('trade_tick_value', None)
            contract_size = symbol_info.get('contract_size', 100000)
            point = symbol_info.get('point', 0.00001)
            
            max_profit_usd = actual_profit_usd
            max_profit_price = exit_price
            max_profit_time = exit_time
            max_profit_candle_idx = -1
            
            # Find maximum profit point
            for idx, candle in enumerate(rates_list):
                candle_time = datetime.fromtimestamp(candle['time'])
                high = candle['high']
                low = candle['low']
                
                # Calculate profit at high/low depending on signal
                if signal == 'LONG':
                    # For LONG: profit increases with higher prices
                    price_diff = high - entry_price
                    price_diff_points = price_diff / point if point > 0 else 0
                    
                    if point_value and point_value > 0:
                        profit_at_high = price_diff_points * lot_size * point_value
                    else:
                        profit_at_high = price_diff * lot_size * contract_size
                    
                    if profit_at_high > max_profit_usd:
                        max_profit_usd = profit_at_high
                        max_profit_price = high
                        max_profit_time = candle_time
                        max_profit_candle_idx = idx
                else:  # SHORT
                    # For SHORT: profit increases with lower prices
                    price_diff = entry_price - low
                    price_diff_points = price_diff / point if point > 0 else 0
                    
                    if point_value and point_value > 0:
                        profit_at_low = price_diff_points * lot_size * point_value
                    else:
                        profit_at_low = price_diff * lot_size * contract_size
                    
                    if profit_at_low > max_profit_usd:
                        max_profit_usd = profit_at_low
                        max_profit_price = low
                        max_profit_time = candle_time
                        max_profit_candle_idx = idx
            
            # Calculate profit left on table
            profit_left_on_table = max(0.0, max_profit_usd - actual_profit_usd)
            
            # Check if max profit was achieved after trade closure
            max_profit_after_close = max_profit_time > exit_time
            
            analysis.update({
                'max_profit_usd': max_profit_usd,
                'max_profit_price': max_profit_price,
                'max_profit_time': max_profit_time.isoformat() if isinstance(max_profit_time, datetime) else str(max_profit_time),
                'max_profit_time_minutes': (max_profit_time - entry_time).total_seconds() / 60 if isinstance(max_profit_time, datetime) else duration_minutes,
                'profit_left_on_table_usd': profit_left_on_table,
                'max_profit_achieved_after_close': max_profit_after_close,
                'analysis_available': True
            })
            
        except Exception as e:
            # Log error but don't fail
            if self.text_logger:
                self.text_logger.debug(f"Error analyzing post-trade price movement for {symbol}: {e}")
        
        return analysis
    
    def _calculate_strategy_suggested_tp(
        self,
        symbol: str,
        entry_price: float,
        signal: str,
        opportunity: Dict[str, Any],
        lot_size: float
    ) -> Dict[str, Any]:
        """
        Calculate what TP the strategy would suggest based on entry conditions.
        
        Uses ATR, trend strength, and quality score to determine optimal TP.
        """
        suggestion = {
            'suggested_tp_price': None,
            'suggested_tp_usd': None,
            'calculation_method': 'unknown',
            'reasoning': [],
            'analysis_available': False
        }
        
        if not self.order_manager:
            return suggestion
        
        try:
            # Get symbol info
            symbol_info = self.order_manager.mt5_connector.get_symbol_info(symbol) if hasattr(self.order_manager, 'mt5_connector') else None
            if not symbol_info:
                return suggestion
            
            # Extract entry conditions
            atr = opportunity.get('atr', 0.0)
            trend_strength = opportunity.get('trend_strength', 0.0)
            quality_score = opportunity.get('quality_score', 0.0)
            point = symbol_info.get('point', 0.00001)
            point_value = symbol_info.get('trade_tick_value', None)
            contract_size = symbol_info.get('contract_size', 100000)
            
            # Method 1: ATR-based TP (most common for scalping)
            if atr > 0:
                # For scalping: TP = 1.5-3x ATR depending on quality
                if quality_score >= 85:
                    atr_multiplier = 3.0  # High quality: take more profit
                elif quality_score >= 70:
                    atr_multiplier = 2.5
                else:
                    atr_multiplier = 2.0
                
                tp_distance = atr * atr_multiplier
                
                if signal == 'LONG':
                    suggested_tp_price = entry_price + tp_distance
                else:  # SHORT
                    suggested_tp_price = entry_price - tp_distance
                
                # Calculate profit in USD
                if point_value and point_value > 0:
                    tp_distance_points = tp_distance / point
                    suggested_tp_usd = tp_distance_points * lot_size * point_value
                else:
                    suggested_tp_usd = tp_distance * lot_size * contract_size
                
                suggestion.update({
                    'suggested_tp_price': suggested_tp_price,
                    'suggested_tp_usd': suggested_tp_usd,
                    'calculation_method': 'atr_based',
                    'reasoning': [
                        f"ATR-based calculation: {atr_multiplier}x ATR ({atr:.5f})",
                        f"Quality score: {quality_score:.1f} (determines multiplier)",
                        f"Trend strength: {trend_strength:.4f}"
                    ],
                    'analysis_available': True
                })
            
            # Method 2: Trend strength-based TP (if ATR not available)
            elif trend_strength > 0:
                # Use trend strength to determine TP distance
                # Strong trends: 2-3% of entry price
                # Weak trends: 1-1.5% of entry price
                if trend_strength > 0.05:
                    tp_pct = 0.03  # 3% for strong trends
                elif trend_strength > 0.02:
                    tp_pct = 0.02  # 2% for moderate trends
                else:
                    tp_pct = 0.015  # 1.5% for weak trends
                
                if signal == 'LONG':
                    suggested_tp_price = entry_price * (1 + tp_pct)
                else:  # SHORT
                    suggested_tp_price = entry_price * (1 - tp_pct)
                
                tp_distance = abs(suggested_tp_price - entry_price)
                
                # Calculate profit in USD
                if point_value and point_value > 0:
                    tp_distance_points = tp_distance / point
                    suggested_tp_usd = tp_distance_points * lot_size * point_value
                else:
                    suggested_tp_usd = tp_distance * lot_size * contract_size
                
                suggestion.update({
                    'suggested_tp_price': suggested_tp_price,
                    'suggested_tp_usd': suggested_tp_usd,
                    'calculation_method': 'trend_strength_based',
                    'reasoning': [
                        f"Trend strength-based: {tp_pct*100:.1f}% of entry price",
                        f"Trend strength: {trend_strength:.4f}",
                        f"Quality score: {quality_score:.1f}"
                    ],
                    'analysis_available': True
                })
            
        except Exception as e:
            if self.text_logger:
                self.text_logger.debug(f"Error calculating strategy-suggested TP for {symbol}: {e}")
        
        return suggestion
    
    def log_trade_outcome(
        self,
        ticket: int,
        exit_price: float,
        profit_usd: float,
        close_reason: str,
        duration_minutes: float
    ):
        """Log trade outcome analysis when position closes."""
        if ticket not in self._open_trades:
            # Trade not tracked - skip outcome logging
            return
        
        trade_data = self._open_trades.pop(ticket)
        strategy_id = trade_data.get('strategy_id', 'UNKNOWN')
        strategy_name = trade_data.get('strategy_name', 'Unknown Strategy')
        entry_price = trade_data.get('entry_price', 0.0)
        entry_time_str = trade_data.get('entry_time', datetime.now().isoformat())
        entry_time = datetime.fromisoformat(entry_time_str) if isinstance(entry_time_str, str) else entry_time_str
        quality_score = trade_data.get('quality_score', 0.0)
        signal = trade_data.get('signal', 'UNKNOWN')
        symbol = trade_data.get('symbol', 'UNKNOWN')
        opportunity = trade_data.get('opportunity', {})
        # Get lot_size from stored trade data or opportunity
        lot_size = trade_data.get('lot_size', opportunity.get('lot_size', 0.01) if opportunity else 0.01)
        
        # Analyze what worked and what didn't
        what_worked = []
        what_didnt_work = []
        
        # Analyze outcome
        if profit_usd > 0:
            what_worked.append("Trade closed in profit")
            if quality_score >= 70:
                what_worked.append("High quality setup delivered expected profit")
            if "Take Profit" in close_reason or "Trailing Stop" in close_reason:
                what_worked.append("Take profit target reached")
        elif profit_usd < 0:
            what_didnt_work.append("Trade closed at a loss")
            if abs(profit_usd + 2.0) <= 0.10:  # Close to -$2.00
                what_didnt_work.append("Stop loss hit as expected")
            elif profit_usd > -2.0:
                what_didnt_work.append("Early closure - SL calculation issue or manual close")
            if quality_score >= 70:
                what_didnt_work.append("High quality setup did not deliver expected profit")
        
        # Performance analysis
        price_movement = abs(exit_price - entry_price) if entry_price > 0 else 0.0
        price_movement_pct = (price_movement / entry_price * 100) if entry_price > 0 else 0.0
        
        # Post-trade analysis: Maximum profit potential
        post_trade_analysis = self._analyze_post_trade_price_movement(
            symbol=symbol,
            entry_price=entry_price,
            exit_price=exit_price,
            signal=signal,
            entry_time=entry_time,
            lot_size=lot_size,
            duration_minutes=duration_minutes,
            actual_profit_usd=profit_usd
        )
        
        # Strategy-suggested TP analysis
        strategy_tp_analysis = self._calculate_strategy_suggested_tp(
            symbol=symbol,
            entry_price=entry_price,
            signal=signal,
            opportunity=opportunity,
            lot_size=lot_size
        )
        
        # Get configured TP for comparison
        configured_tp_price = opportunity.get('take_profit_price') if opportunity else None
        configured_tp_usd = None
        if configured_tp_price and entry_price > 0:
            # Calculate configured TP profit
            try:
                symbol_info = self.order_manager.mt5_connector.get_symbol_info(symbol) if self.order_manager and hasattr(self.order_manager, 'mt5_connector') else None
                if symbol_info:
                    point = symbol_info.get('point', 0.00001)
                    point_value = symbol_info.get('trade_tick_value', None)
                    contract_size = symbol_info.get('contract_size', 100000)
                    
                    if signal == 'LONG':
                        tp_distance = configured_tp_price - entry_price
                    else:  # SHORT
                        tp_distance = entry_price - configured_tp_price
                    
                    if point_value and point_value > 0:
                        tp_distance_points = tp_distance / point
                        configured_tp_usd = tp_distance_points * lot_size * point_value
                    else:
                        configured_tp_usd = tp_distance * lot_size * contract_size
            except Exception:
                pass
        
        # Build outcome analysis
        outcome_analysis = {
            'timestamp': datetime.now().isoformat(),
            'ticket': ticket,
            'strategy_id': strategy_id,
            'strategy_name': strategy_name,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'profit_usd': profit_usd,
            'close_reason': close_reason,
            'duration_minutes': duration_minutes,
            'price_movement': price_movement,
            'price_movement_pct': price_movement_pct,
            'what_worked': what_worked,
            'what_didnt_work': what_didnt_work,
            'entry_quality_score': quality_score,
            'signal': signal,
            
            # Post-trade analysis: Maximum profit potential
            'post_trade_analysis': post_trade_analysis,
            
            # Strategy-suggested TP analysis
            'strategy_tp_analysis': strategy_tp_analysis,
            
            # Configured TP for comparison
            'configured_tp': {
                'tp_price': configured_tp_price,
                'tp_usd': configured_tp_usd,
                'vs_suggested_tp_usd': strategy_tp_analysis.get('suggested_tp_usd') - configured_tp_usd if configured_tp_usd and strategy_tp_analysis.get('suggested_tp_usd') else None
            }
        }
        
        # Write to log file - ensure all values are JSON-serializable
        try:
            # Convert all values to JSON-serializable types
            serializable_analysis = _make_json_serializable(outcome_analysis)
            json.dump(serializable_analysis, self.log_file, ensure_ascii=False)
            self.log_file.write('\n')
            self.log_file.flush()
        except Exception as e:
            # Log error but don't fail
            if self.text_logger:
                self.text_logger.warning(f"Failed to write outcome analysis for ticket {ticket}: {e}")
    
    def close(self):
        """Close log file."""
        if self.log_file:
            self.log_file.close()

