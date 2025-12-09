#!/usr/bin/env python3
"""
Daily Optimization Runner
Runs daily performance analysis and optimization checks.
Designed to be run as a scheduled task (cron/crontab).
"""

import os
import sys
from datetime import datetime
from monitor.bot_performance_optimizer import BotPerformanceOptimizer

def main():
    """Run daily optimization analysis."""
    print(f"=" * 80)
    print(f"Daily Bot Performance Optimization - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"=" * 80)
    print()
    
    # Determine broker report file
    broker_report = 'TradingHistoryFromBroker.html'
    if len(sys.argv) > 1:
        broker_report = sys.argv[1]
    
    # Check if broker report exists
    if not os.path.exists(broker_report):
        print(f"⚠️  Warning: Broker report not found: {broker_report}")
        print("   Continuing with analysis without broker alignment check...")
        print()
    
    # Run optimization
    optimizer = BotPerformanceOptimizer(broker_html_file=broker_report)
    analysis = optimizer.run_full_analysis()
    
    # Print summary
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"HFT Sweet Spot Rate: {analysis['hft_performance'].get('sweet_spot_rate', 0):.1f}%")
    print(f"Total Profit (24h): ${analysis['trades'].get('total_profit', 0):.2f}")
    print(f"Total Trades: {analysis['trades'].get('total_trades', 0)}")
    print(f"Optimization Suggestions: {len(analysis['suggestions'])}")
    print()
    
    # List suggestions
    if analysis['suggestions']:
        print("KEY SUGGESTIONS:")
        for i, suggestion in enumerate(analysis['suggestions'][:5], 1):
            print(f"  {i}. [{suggestion.get('priority')}] {suggestion.get('type')}")
            print(f"     {suggestion.get('reason', '')[:70]}...")
        print()
    
    print("✅ Daily optimization complete")
    return 0

if __name__ == "__main__":
    sys.exit(main())

