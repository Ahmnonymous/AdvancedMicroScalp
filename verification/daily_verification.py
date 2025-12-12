#!/usr/bin/env python3
"""
Daily Verification System
Comprehensive verification of bot performance, filters, and broker alignment.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from verification.verify_trade_alignment import TradeAlignmentVerifier
from verification.verify_filters_and_hft import FilterAndHFTVerifier
from monitor.bot_performance_optimizer import BotPerformanceOptimizer
from utils.logger_factory import get_logger

logger = get_logger("daily_verification", "logs/live/system/daily_verification.log")


def run_daily_verification():
    """Run complete daily verification."""
    print("=" * 80)
    print("DAILY VERIFICATION SYSTEM")
    print("=" * 80)
    print()
    
    results = {
        'alignment': {},
        'filters_hft': {},
        'optimization': {}
    }
    
    # 1. Verify trade alignment
    print("Step 1: Verifying trade alignment...")
    try:
        verifier = TradeAlignmentVerifier()
        alignment_results = verifier.verify_alignment()
        results['alignment'] = alignment_results
    except Exception as e:
        logger.error(f"Error in trade alignment verification: {e}", exc_info=True)
        print(f"  Error: {e}")
    
    print()
    
    # 2. Verify filters and HFT
    print("Step 2: Verifying filters and Micro-HFT...")
    try:
        hft_verifier = FilterAndHFTVerifier()
        hft_results = hft_verifier.verify_all()
        results['filters_hft'] = hft_results
    except Exception as e:
        logger.error(f"Error in filter/HFT verification: {e}", exc_info=True)
        print(f"  Error: {e}")
    
    print()
    
    # 3. Run optimization analysis
    print("Step 3: Running performance optimization analysis...")
    try:
        optimizer = BotPerformanceOptimizer()
        opt_results = optimizer.run_full_analysis()
        results['optimization'] = opt_results
    except Exception as e:
        logger.error(f"Error in optimization analysis: {e}", exc_info=True)
        print(f"  Error: {e}")
    
    print()
    
    # Generate summary
    print("=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    
    alignment_achieved = results.get('alignment', {}).get('alignment_achieved', False)
    hft_rate = results.get('filters_hft', {}).get('summary', {}).get('sweet_spot_rate', 0)
    
    print(f"Trade Alignment: {'[OK] 100%' if alignment_achieved else '[WARNING] Issues detected'}")
    print(f"HFT Sweet Spot Rate: {hft_rate:.1f}%")
    print(f"Suggestions Generated: {len(results.get('optimization', {}).get('suggestions', []))}")
    print()
    
    return results


if __name__ == "__main__":
    run_daily_verification()

