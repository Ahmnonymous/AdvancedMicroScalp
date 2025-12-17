#!/usr/bin/env python3
"""
Simple runner script for comprehensive backtest.
Usage: python run_comprehensive_backtest.py [--months 2] [--timeframe M1]
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.comprehensive_backtest import ComprehensiveBacktest

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Run comprehensive multi-symbol backtest with stress testing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default settings (2 months, M1 timeframe)
  python run_comprehensive_backtest.py
  
  # Run with 1 month of data
  python run_comprehensive_backtest.py --months 1
  
  # Run with 5-minute candles
  python run_comprehensive_backtest.py --timeframe M5
  
  # Custom output directory
  python run_comprehensive_backtest.py --output logs/my_backtest
        """
    )
    
    parser.add_argument('--config', default='config.json',
                       help='Config file path (default: config.json)')
    parser.add_argument('--months', type=int, default=2,
                       help='Number of months of historical data (default: 2)')
    parser.add_argument('--timeframe', default='M1',
                       choices=['M1', 'M5', 'M15', 'H1'],
                       help='Timeframe for historical data (default: M1)')
    parser.add_argument('--output',
                       help='Output directory (default: auto-generated with timestamp)')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("COMPREHENSIVE MULTI-SYMBOL BACKTEST SYSTEM")
    print("=" * 80)
    print(f"Config: {args.config}")
    print(f"Months: {args.months}")
    print(f"Timeframe: {args.timeframe}")
    if args.output:
        print(f"Output: {args.output}")
    print("=" * 80)
    print()
    
    try:
        # Run comprehensive backtest
        backtest = ComprehensiveBacktest(
            config_path=args.config,
            output_dir=args.output
        )
        backtest.run_comprehensive_backtest(
            months=args.months,
            timeframe=args.timeframe
        )
        
        print("\n" + "=" * 80)
        print("BACKTEST COMPLETE")
        print("=" * 80)
        print(f"Results saved to: {backtest.output_dir}")
        print("=" * 80)
        
    except KeyboardInterrupt:
        print("\n\nBacktest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

