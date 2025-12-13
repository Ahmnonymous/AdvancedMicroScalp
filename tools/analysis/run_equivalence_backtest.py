#!/usr/bin/env python3
"""
Equivalence Backtest Runner
Runs a controlled backtest to verify BACKTEST behavior matches LIVE behavior,
while observing DRY-RUN limit-entry analysis results.
"""

import sys
import os
import json
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.backtest_runner import BacktestRunner
from backtest.data_preflight_validator import BacktestDataPreflightValidator
from utils.config_alignment_validator import ConfigAlignmentValidator
from backtest.equivalence_validator import BacktestEquivalenceValidator
from execution.mt5_connector import MT5Connector
from utils.logger_factory import get_logger, close_all_loggers
import MetaTrader5 as mt5

logger = get_logger("equivalence_backtest", "logs/backtest/equivalence_backtest.log")


def find_recent_live_symbol(config):
    """Find a symbol that traded LIVE recently, or use default from config."""
    trade_log_dir = Path("logs/live/trades")
    
    if trade_log_dir.exists():
        # Find most recently modified trade log
        log_files = list(trade_log_dir.glob("*.log"))
        if log_files:
            # Sort by modification time
            log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            most_recent = log_files[0]
            
            # Extract symbol from filename (format: SYMBOL.log)
            symbol = most_recent.stem.upper()
            
            logger.info(f"Found recent live symbol: {symbol} (from {most_recent.name})")
            return symbol
    
    # Fallback: use first symbol from backtest config
    backtest_config = config.get('backtest', {})
    symbols = backtest_config.get('symbols', ['EURUSDm'])
    symbol = symbols[0] if symbols else 'EURUSDm'
    
    logger.warning(f"No live trade logs found, using default symbol from config: {symbol}")
    return symbol


def run_preflight_checks(config, symbol, start_date, end_date):
    """Run all mandatory pre-flight checks."""
    logger.info("=" * 80)
    logger.info("PRE-FLIGHT CHECKS")
    logger.info("=" * 80)
    
    all_passed = True
    
    # 1. Data Preflight Validation
    logger.info("\n[1/5] Data Preflight Validation...")
    try:
        mt5_connector = MT5Connector(config)
        if not mt5_connector.ensure_connected():
            logger.critical("Cannot connect to MT5 for data validation")
            return False
        
        timeframe_map = {'M1': 1}
        timeframe_int = timeframe_map.get('M1', 1)
        
        validator = BacktestDataPreflightValidator(config, mt5_connector)
        is_valid, errors, warnings = validator.validate(
            symbol=symbol,
            timeframe=timeframe_int,
            start_date=start_date,
            end_date=end_date,
            required_bars=1000
        )
        
        validator.log_results(mode="BACKTEST")
        
        if not is_valid:
            logger.critical("DATA PREFLIGHT VALIDATION FAILED - ABORTING")
            logger.critical(f"Errors: {errors}")
            all_passed = False
        else:
            logger.info("[OK] Data preflight validation passed")
    except Exception as e:
        logger.critical(f"Data preflight validation error: {e}", exc_info=True)
        all_passed = False
    
    # 2. Config Alignment Validation
    logger.info("\n[2/5] Config Alignment Validation...")
    try:
        alignment_validator = ConfigAlignmentValidator(config_path='config.json')
        is_aligned, mismatches, warnings = alignment_validator.validate_alignment()
        alignment_validator.log_results(mode="BACKTEST")
        
        if not is_aligned:
            logger.critical("CONFIG ALIGNMENT FAILED - ABORTING")
            logger.critical(f"Mismatches: {mismatches}")
            all_passed = False
        else:
            logger.info("[OK] Config alignment validated")
    except Exception as e:
        logger.critical(f"Config alignment validation error: {e}", exc_info=True)
        all_passed = False
    
    # 3. Hard SL Emergency Path Check
    logger.info("\n[3/5] Hard SL Emergency Lock-Free Path Check...")
    try:
        from risk.sl_manager import SLManager
        # Check if the method exists
        if hasattr(SLManager, '_enforce_strict_loss_emergency_lockfree'):
            logger.info("[OK] Hard SL emergency lock-free path exists")
        else:
            logger.critical("Hard SL emergency lock-free path NOT FOUND - ABORTING")
            all_passed = False
    except Exception as e:
        logger.critical(f"Hard SL check error: {e}", exc_info=True)
        all_passed = False
    
    # 4. Limit Entry Dry-Run Mode Check
    logger.info("\n[4/5] Limit Entry Dry-Run Mode Check...")
    try:
        use_limit_entries = config.get('use_limit_entries', False)
        limit_entry_dry_run = config.get('limit_entry_dry_run', False)
        
        if use_limit_entries:
            logger.critical("use_limit_entries is True - must be False for dry-run only - ABORTING")
            all_passed = False
        elif not limit_entry_dry_run:
            logger.warning("limit_entry_dry_run is False - dry-run analysis will be disabled")
        else:
            logger.info("[OK] Limit entry system in DRY-RUN mode only")
            logger.info(f"  use_limit_entries: {use_limit_entries}")
            logger.info(f"  limit_entry_dry_run: {limit_entry_dry_run}")
    except Exception as e:
        logger.critical(f"Limit entry check error: {e}", exc_info=True)
        all_passed = False
    
    # 5. Equivalence Validator Check
    logger.info("\n[5/5] Equivalence Validator Check...")
    try:
        equiv_validator = BacktestEquivalenceValidator(config)
        logger.info("[OK] Equivalence validator initialized")
    except Exception as e:
        logger.critical(f"Equivalence validator check error: {e}", exc_info=True)
        all_passed = False
    
    logger.info("\n" + "=" * 80)
    if all_passed:
        logger.info("ALL PRE-FLIGHT CHECKS PASSED - PROCEEDING WITH BACKTEST")
    else:
        logger.critical("PRE-FLIGHT CHECKS FAILED - ABORTING")
    logger.info("=" * 80)
    
    return all_passed


def run_equivalence_backtest():
    """Run the equivalence backtest."""
    print("=" * 80)
    print("EQUIVALENCE BACKTEST - OBSERVATION ONLY")
    print("=" * 80)
    
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Ensure mode is backtest
    if config.get('mode') != 'backtest':
        logger.warning("Config mode is not 'backtest', setting to backtest")
        config['mode'] = 'backtest'
    
    # Find a symbol that traded LIVE recently (or use default)
    logger.info("\nFinding symbol that traded LIVE recently...")
    symbol = find_recent_live_symbol(config)
    
    logger.info(f"Selected symbol: {symbol}")
    
    # Set date range: last 3-7 days
    end_date = datetime.now() - timedelta(days=1)  # Yesterday
    start_date = end_date - timedelta(days=5)  # 5 days before
    
    logger.info(f"\nDate Range: {start_date.date()} to {end_date.date()} (5 days)")
    
    # Update backtest config
    if 'backtest' not in config:
        config['backtest'] = {}
    
    config['backtest']['symbols'] = [symbol]
    config['backtest']['start_date'] = start_date.strftime('%Y-%m-%dT%H:%M:%S')
    config['backtest']['end_date'] = end_date.strftime('%Y-%m-%dT%H:%M:%S')
    config['backtest']['timeframe'] = 'M1'
    config['backtest']['stress_tests'] = []  # No stress tests
    config['backtest']['use_ticks'] = False
    
    # Run pre-flight checks
    if not run_preflight_checks(config, symbol, start_date, end_date):
        logger.critical("Pre-flight checks failed - aborting backtest")
        return
    
    # Initialize backtest runner
    logger.info("\n" + "=" * 80)
    logger.info("INITIALIZING BACKTEST RUNNER")
    logger.info("=" * 80)
    
    try:
        runner = BacktestRunner(config=config)
        
        # Setup environment
        logger.info("Setting up backtest environment...")
        runner.setup_backtest_environment()
        
        # Initialize trading bot
        logger.info("Initializing trading bot...")
        runner.initialize_trading_bot()
        
        # Run backtest at realistic speed (1.0 = no acceleration)
        logger.info("\n" + "=" * 80)
        logger.info("STARTING BACKTEST EXECUTION")
        logger.info("=" * 80)
        logger.info(f"Symbol: {symbol}")
        logger.info(f"Date Range: {start_date.date()} to {end_date.date()}")
        logger.info(f"Timeframe: M1")
        logger.info(f"Mode: BACKTEST (observation only)")
        logger.info(f"Replay Speed: 1.0 (realistic - no acceleration)")
        logger.info("=" * 80)
        
        # Run backtest at realistic speed
        runner.run_backtest(speed=1.0)
        
        # Generate report
        report_path = f"logs/backtest/equivalence_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report = runner.generate_report(output_path=report_path)
        
        # Generate post-run report
        generate_post_run_report(symbol, start_date, end_date, report)
        
    except Exception as e:
        logger.critical(f"Backtest execution failed: {e}", exc_info=True)
        raise
    finally:
        close_all_loggers()


def generate_post_run_report(symbol, start_date, end_date, backtest_report):
    """Generate comprehensive post-run report."""
    logger.info("\n" + "=" * 80)
    logger.info("GENERATING POST-RUN REPORT")
    logger.info("=" * 80)
    
    # Parse logs for analysis
    log_dir = Path("logs/backtest")
    
    # 1. Trade Activity
    logger.info("\n[1/4] Trade Activity Analysis...")
    trades_taken = 0
    trades_rejected = 0
    trades_skipped = 0
    
    # Parse trade logs
    trade_log_files = list(log_dir.glob("trades/*.log")) + list(log_dir.glob("*.log"))
    for log_file in trade_log_files:
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "[OK]" in line and "Position" in line and "verified" in line:
                        trades_taken += 1
                    elif "[SKIP]" in line:
                        trades_skipped += 1
                    elif "[ERROR]" in line and "Order" in line:
                        trades_rejected += 1
        except Exception:
            continue
    
    logger.info(f"  Trades taken: {trades_taken}")
    logger.info(f"  Trades rejected: {trades_rejected}")
    logger.info(f"  Trades skipped: {trades_skipped}")
    
    # 2. DRY-RUN Limit Entry Analysis
    logger.info("\n[2/4] DRY-RUN Limit Entry Analysis...")
    total_analyzed = 0
    score_passed = 0
    would_fill = 0
    would_expire = 0
    spread_rejections = 0
    sl_distances = []
    
    for log_file in trade_log_files:
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "[DRY_RUN][LIMIT_ENTRY]" in line:
                        total_analyzed += 1
                        
                        if "score_gate_passed=true" in line or "would_fill" in line:
                            score_passed += 1
                        
                        if "would_fill=true" in line:
                            would_fill += 1
                        elif "would_fill=false" in line:
                            would_expire += 1
                        
                        if "rejection_reason" in line and "spread" in line.lower():
                            spread_rejections += 1
                        
                        # Extract SL distance
                        import re
                        match = re.search(r'SL_distance=([0-9.]+)', line)
                        if match:
                            sl_distances.append(float(match.group(1)))
        except Exception:
            continue
    
    logger.info(f"  Total opportunities analyzed: {total_analyzed}")
    if total_analyzed > 0:
        logger.info(f"  Score pass rate: {score_passed/total_analyzed*100:.1f}%")
        logger.info(f"  Would-fill rate: {would_fill/total_analyzed*100:.1f}%")
        logger.info(f"  Would-expire rate: {would_expire/total_analyzed*100:.1f}%")
        logger.info(f"  Spread rejections: {spread_rejections}")
        if sl_distances:
            logger.info(f"  Avg SL distance: {sum(sl_distances)/len(sl_distances):.2f}pips")
            logger.info(f"  Min SL distance: {min(sl_distances):.2f}pips")
            logger.info(f"  Max SL distance: {max(sl_distances):.2f}pips")
    
    # 3. Risk & System Health
    logger.info("\n[3/4] Risk & System Health...")
    # Parse SL update logs
    sl_updates = 0
    sl_success = 0
    sl_failures = 0
    
    sl_log_files = list(log_dir.glob("engine/sl_manager.log"))
    for log_file in sl_log_files:
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if "SL UPDATE" in line or "update_sl_atomic" in line:
                        sl_updates += 1
                        if "Success" in line or "SUCCESS" in line:
                            sl_success += 1
                        elif "FAILED" in line or "failed" in line:
                            sl_failures += 1
        except Exception:
            continue
    
    if sl_updates > 0:
        logger.info(f"  SL update success rate: {sl_success/sl_updates*100:.1f}%")
        logger.info(f"  SL updates: {sl_updates} (success: {sl_success}, failed: {sl_failures})")
    
    # 4. Equivalence Assessment
    logger.info("\n[4/4] Equivalence Assessment...")
    
    if trades_taken == 0:
        logger.warning("ZERO TRADES CONDITION DETECTED")
        logger.warning("Analyzing why no trades occurred...")
        
        # Check for filter rejections
        filter_reasons = {}
        for log_file in trade_log_files:
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if "[SKIP]" in line and "Reason:" in line:
                            # Extract reason
                            if "Reason:" in line:
                                reason = line.split("Reason:")[-1].strip()
                                filter_reasons[reason] = filter_reasons.get(reason, 0) + 1
            except Exception:
                continue
        
        logger.warning("Filter rejection reasons:")
        for reason, count in sorted(filter_reasons.items(), key=lambda x: x[1], reverse=True):
            logger.warning(f"  {reason}: {count} times")
    
    logger.info("\n" + "=" * 80)
    logger.info("EQUIVALENCE ASSESSMENT")
    logger.info("=" * 80)
    
    # Final assessment
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Date Range: {start_date.date()} to {end_date.date()}")
    logger.info(f"Trades Executed: {trades_taken}")
    logger.info(f"Opportunities Analyzed: {total_analyzed}")
    
    if trades_taken > 0:
        logger.info("\n[ASSESSMENT] BACKTEST executed trades - behavior observed")
    else:
        logger.warning("\n[ASSESSMENT] ZERO TRADES - see filter analysis above")
    
    logger.info("=" * 80)
    
    # Write report to file
    report_file = f"logs/backtest/equivalence_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("EQUIVALENCE BACKTEST REPORT\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Symbol: {symbol}\n")
        f.write(f"Date Range: {start_date.date()} to {end_date.date()}\n")
        f.write(f"Timeframe: M1\n")
        f.write(f"Mode: BACKTEST\n\n")
        f.write(f"Trades Taken: {trades_taken}\n")
        f.write(f"Trades Rejected: {trades_rejected}\n")
        f.write(f"Trades Skipped: {trades_skipped}\n")
        f.write(f"\nDRY-RUN Limit Entry Analysis:\n")
        f.write(f"  Total Analyzed: {total_analyzed}\n")
        if total_analyzed > 0:
            f.write(f"  Score Pass Rate: {score_passed/total_analyzed*100:.1f}%\n")
            f.write(f"  Would-Fill Rate: {would_fill/total_analyzed*100:.1f}%\n")
            f.write(f"  Would-Expire Rate: {would_expire/total_analyzed*100:.1f}%\n")
        f.write(f"\nReport saved to: {report_file}\n")
    
    logger.info(f"\nFull report saved to: {report_file}")


if __name__ == "__main__":
    try:
        run_equivalence_backtest()
    except KeyboardInterrupt:
        logger.info("\nBacktest interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

