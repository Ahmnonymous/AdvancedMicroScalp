"""
SIM_LIVE Validation Logger
Provides SIM_LIVE-only logging that doesn't touch live logs.
"""

from utils.logger_factory import get_logger

# SIM_LIVE-specific logger (separate from live logs)
_sim_live_logger = None


def get_sim_live_logger():
    """Get SIM_LIVE logger instance."""
    global _sim_live_logger
    if _sim_live_logger is None:
        _sim_live_logger = get_logger("sim_live_validation", "logs/sim_live/validation.log")
    return _sim_live_logger


def log_warmup_complete(symbol: str, candle_count: int):
    """Log when warm-up completes."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [WARMUP] ✓ Warm-up complete for {symbol}: {candle_count} candles generated")


def log_indicators_valid(symbol: str, indicators: dict):
    """Log when indicators become valid."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [INDICATORS] ✓ Indicators valid for {symbol}: "
                f"SMA20={indicators.get('sma20', 'N/A'):.5f}, "
                f"SMA50={indicators.get('sma50', 'N/A'):.5f}, "
                f"ADX={indicators.get('adx', 'N/A'):.1f}, "
                f"RSI={indicators.get('rsi', 'N/A'):.1f}")


def log_strategy_evaluation(symbol: str, quality_score: float, trend_signal: dict):
    """Log when strategy evaluates entry."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [STRATEGY] Strategy evaluating {symbol}: "
                f"quality_score={quality_score:.1f}, "
                f"trend={trend_signal.get('direction', 'N/A')}, "
                f"strength={trend_signal.get('strength', 0):.5f}")


def log_trade_opened(symbol: str, ticket: int, order_type: str, entry_price: float, sl: float):
    """Log when trade is opened."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [TRADE_OPENED] ✓ Trade opened: {symbol} {order_type} "
                f"ticket={ticket}, entry={entry_price:.5f}, SL={sl:.5f}")


def log_profit_zone_entered(ticket: int, symbol: str, profit_usd: float):
    """Log when profit zone is entered."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [PROFIT_ZONE] ✓ Profit zone entered: {symbol} ticket={ticket}, profit=${profit_usd:.2f}")


def log_sl_modified(ticket: int, symbol: str, old_sl: float, new_sl: float, reason: str):
    """Log when SL is modified."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [SL_MODIFIED] ✓ SL updated: {symbol} ticket={ticket}, "
                f"{old_sl:.5f} → {new_sl:.5f} ({reason})")


def log_trade_exited(ticket: int, symbol: str, exit_price: float, profit_usd: float, exit_reason: str):
    """Log when trade exits."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [TRADE_EXITED] ✓ Trade exited: {symbol} ticket={ticket}, "
                f"exit_price={exit_price:.5f}, profit=${profit_usd:.2f}, reason={exit_reason}")


def log_entry_candle_generated(symbol: str, range_pct: float, close_position: str):
    """Log when entry candle is generated."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [ENTRY_CANDLE] Generated entry candle for {symbol}: "
                f"range={range_pct:.1f}% of avg, close_position={close_position}")


def log_entry_rejected(symbol: str, reason: str, details: dict = None):
    """
    Log when entry is rejected with full diagnostic details.
    
    Args:
        symbol: Trading symbol
        reason: Rejection reason (e.g., "TREND_FILTER", "QUALITY_SCORE", "TIMING_GUARD")
        details: Dict with diagnostic details (trend_signal, quality_score, etc.)
    """
    logger = get_sim_live_logger()
    
    if details is None:
        details = {}
    
    # Build diagnostic message
    msg_parts = [f"[SIM_LIVE] [ENTRY_REJECTED] {symbol} | Reason: {reason}"]
    
    # Trend filter details
    if 'trend_signal' in details:
        trend = details['trend_signal']
        signal = trend.get('signal', 'NONE')
        sma_fast = trend.get('sma_fast', 0)
        sma_slow = trend.get('sma_slow', 0)
        rsi = trend.get('rsi', 50)
        adx = trend.get('adx', 0)
        msg_parts.append(f"TrendFilter: {signal} | SMA20={sma_fast:.5f} SMA50={sma_slow:.5f} | RSI={rsi:.1f} | ADX={adx:.1f}")
    
    # Quality score details
    if 'quality_score' in details:
        score = details['quality_score']
        threshold = details.get('min_quality_score', 50.0)
        status = "PASS" if score >= threshold else "FAIL"
        msg_parts.append(f"QualityScore: {score:.1f} {status} (threshold={threshold:.1f})")
        if 'quality_reasons' in details:
            reasons = details['quality_reasons']
            if reasons:
                msg_parts.append(f"  Details: {', '.join(reasons[:3])}")  # First 3 reasons
    
    # Timing guard details
    if 'timing_guards' in details:
        guards = details['timing_guards']
        maturity_ok = guards.get('trend_maturity', {}).get('ok', True)
        maturity_reason = guards.get('trend_maturity', {}).get('reason', 'PASS')
        impulse_ok = guards.get('impulse_exhaustion', {}).get('ok', True)
        impulse_reason = guards.get('impulse_exhaustion', {}).get('reason', 'PASS')
        
        maturity_status = "PASS" if maturity_ok else f"FAIL ({maturity_reason})"
        impulse_status = "PASS" if impulse_ok else f"FAIL ({impulse_reason})"
        msg_parts.append(f"TimingGuards: Maturity={maturity_status} | Impulse={impulse_status}")
    
    # Risk check details
    if 'risk_checks' in details:
        risk = details['risk_checks']
        portfolio_ok = risk.get('portfolio', {}).get('ok', True)
        portfolio_reason = risk.get('portfolio', {}).get('reason', 'PASS')
        can_open = risk.get('can_open', {}).get('ok', True)
        can_open_reason = risk.get('can_open', {}).get('reason', 'PASS')
        spread_points = risk.get('spread', {}).get('points', 0)
        spread_max = risk.get('spread', {}).get('max', 0)
        spread_ok = risk.get('spread', {}).get('ok', True)
        
        portfolio_status = "PASS" if portfolio_ok else f"FAIL ({portfolio_reason})"
        can_open_status = "PASS" if can_open else f"FAIL ({can_open_reason})"
        spread_status = f"{spread_points:.1f}pts" if spread_ok else f"{spread_points:.1f}pts > {spread_max}pts FAIL"
        msg_parts.append(f"RiskChecks: Portfolio={portfolio_status} | CanOpen={can_open_status} | Spread={spread_status}")
    
    # Additional context
    if 'additional_context' in details:
        msg_parts.append(f"Context: {details['additional_context']}")
    
    logger.info(" | ".join(msg_parts))


def log_entry_evaluation_start(symbol: str, trend_signal: dict):
    """Log when entry evaluation starts for a symbol."""
    logger = get_sim_live_logger()
    signal = trend_signal.get('signal', 'NONE')
    sma_fast = trend_signal.get('sma_fast', 0)
    sma_slow = trend_signal.get('sma_slow', 0)
    rsi = trend_signal.get('rsi', 50)
    adx = trend_signal.get('adx', 0)
    logger.info(f"[SIM_LIVE] [EVAL_START] {symbol} | Signal={signal} | "
                f"SMA20={sma_fast:.5f} SMA50={sma_slow:.5f} | RSI={rsi:.1f} | ADX={adx:.1f}")


def log_trend_validated(symbol: str, sma20: float, sma50: float, adx: float, rsi: float, signal: str):
    """Log when trend is validated after warm-up."""
    logger = get_sim_live_logger()
    logger.info(f"[SIM_LIVE] [TREND_VALIDATED] {symbol} | "
                f"SMA20={sma20:.5f} SMA50={sma50:.5f} | "
                f"ADX={adx:.1f} | RSI={rsi:.1f} | Signal={signal}")


def log_contract_validation(symbol: str, passed: bool, reason: str = ""):
    """Log contract validation result."""
    logger = get_sim_live_logger()
    status = "PASS" if passed else "FAIL"
    logger.info(f"[SIM_LIVE] [CONTRACT_VALIDATION] {symbol} | Status: {status} | {reason}")

