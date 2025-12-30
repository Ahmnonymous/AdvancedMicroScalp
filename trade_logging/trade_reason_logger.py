"""
Detailed Trade Reason Logger
Logs comprehensive analysis of why each trade was taken with exact reasons and detailed analysis.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from utils.logger_factory import get_logger

class TradeReasonLogger:
    """Logs detailed trade execution reasons with comprehensive analysis."""
    
    def __init__(self, is_backtest: bool = False):
        """
        Initialize Trade Reason Logger.
        
        Args:
            is_backtest: If True, log to backtest directory
        """
        self.is_backtest = is_backtest
        
        # Create log directory
        log_dir = Path("logs/backtest/trades/reasons" if is_backtest else "logs/live/trades/reasons")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create timestamped log file
        timestamp = datetime.now().strftime("%Y%m%d")
        log_file = log_dir / f"trade_reasons_{timestamp}.jsonl"
        
        # Open log file in append mode
        self.log_file = open(log_file, 'a', encoding='utf-8')
        
        # Also create a text summary log
        text_log_file = log_dir / f"trade_reasons_{timestamp}.log"
        self.text_logger = get_logger("trade_reason", str(text_log_file))
        
        self.text_logger.info("=" * 100)
        self.text_logger.info("TRADE REASON LOGGER INITIALIZED")
        self.text_logger.info("=" * 100)
    
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
        
        # Extract filter results
        filters_passed = {
            'news_filter': opportunity.get('news_filter_passed', True),
            'volume_filter': opportunity.get('volume_filter_passed', True),
            'market_closing_filter': opportunity.get('market_closing_filter_passed', True),
            'rsi_filter': opportunity.get('rsi_filter_passed', True),
            'spread_filter': opportunity.get('spread_filter_passed', True),
            'volatility_filter': opportunity.get('volatility_filter_passed', True),
            'trend_strength_filter': opportunity.get('trend_strength_filter_passed', True),
        }
        
        # Extract execution details
        entry_price = execution_result.get('entry_price_actual', 0.0)
        lot_size = execution_result.get('lot_size', 0.01)
        stop_loss_price = execution_result.get('stop_loss_price', 0.0)
        take_profit_price = execution_result.get('take_profit_price', 0.0)
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
        
        # Build comprehensive reason analysis
        reason_analysis = {
            'timestamp': timestamp,
            'symbol': symbol,
            'ticket': ticket,
            'signal': signal,
            'execution_status': execution_result.get('success', False),
            
            # Quality Metrics
            'quality_score': quality_score,
            'high_quality_setup': high_quality_setup,
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
                'take_profit_price': take_profit_price,
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
        
        # Write JSONL entry
        json.dump(reason_analysis, self.log_file, ensure_ascii=False)
        self.log_file.write('\n')
        self.log_file.flush()
        
        # Write formatted text log
        self._write_text_log(reason_analysis)
    
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
    
    def close(self):
        """Close log file."""
        if self.log_file:
            self.log_file.close()

