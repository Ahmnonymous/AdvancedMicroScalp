#!/usr/bin/env python3
"""
Verification Script for Filters and Micro-HFT Performance
Verifies that filters are working correctly and Micro-HFT trades achieve expected profits.
"""

import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Tuple


class FilterAndHFTVerifier:
    """Verifies filter performance and Micro-HFT trade results."""
    
    def __init__(self):
        self.results = {
            'filter_checks': {'passed': 0, 'failed': 0, 'issues': []},
            'hft_checks': {'passed': 0, 'failed': 0, 'issues': []},
            'summary': {}
        }
    
    def verify_all(self) -> Dict[str, Any]:
        """Run complete verification."""
        print("=" * 80)
        print("FILTER AND MICRO-HFT VERIFICATION")
        print("=" * 80)
        print()
        
        # Verify skipped pairs log
        print("Step 1: Verifying filter logs...")
        self._verify_filter_logs()
        
        print()
        
        # Verify Micro-HFT performance
        print("Step 2: Verifying Micro-HFT performance...")
        self._verify_hft_performance()
        
        print()
        
        # Generate report
        print("Step 3: Generating verification report...")
        report_file = self._generate_report()
        
        return {
            'results': self.results,
            'report_file': report_file
        }
    
    def _verify_filter_logs(self):
        """Verify that skipped pairs are properly logged."""
        skipped_log_file = 'logs/live/system/skipped_pairs.log'
        
        if not os.path.exists(skipped_log_file):
            self.results['filter_checks']['issues'].append("Skipped pairs log file not found")
            print("  [WARNING]  Skipped pairs log file not found (may be normal if no symbols skipped)")
            return
        
        # Count skipped entries
        with open(skipped_log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        market_closing_skips = sum(1 for line in lines if 'MARKET_CLOSING' in line)
        volume_skips = sum(1 for line in lines if 'LOW_VOLUME' in line)
        
        print(f"  Found {len(lines)} total skip entries")
        print(f"    - Market closing skips: {market_closing_skips}")
        print(f"    - Low volume skips: {volume_skips}")
        
        if len(lines) > 0:
            self.results['filter_checks']['passed'] += 1
            print("  [OK] Filter logging is working")
        else:
            print("  [WARNING]  No skip entries found (may be normal if all symbols passed filters)")
        
        # Check if any executed trades were incorrectly skipped
        self._check_incorrectly_skipped_trades(lines)
    
    def _check_incorrectly_skipped_trades(self, skipped_lines: List[str]):
        """Check if any trades were executed that should have been skipped."""
        # Get recent trades
        trades_dir = Path('logs/trades')
        if not trades_dir.exists():
            return
        
        # Get trade timestamps from last 24 hours
        now = datetime.now()
        recent_trades = []
        
        for log_file in trades_dir.glob('*.log'):
            if 'backup' in log_file.name:
                continue
            
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and line.startswith('{'):
                            try:
                                trade = json.loads(line)
                                trade_time = datetime.strptime(trade.get('timestamp', ''), '%Y-%m-%d %H:%M:%S')
                                if (now - trade_time).total_seconds() < 86400:  # Last 24 hours
                                    recent_trades.append(trade)
                            except:
                                pass
            except:
                pass
        
        # Check if any trades match skipped symbols at similar times
        issues = 0
        for trade in recent_trades:
            symbol = trade.get('symbol', '')
            trade_time = trade.get('timestamp', '')
            
            # Check if this symbol was skipped around the same time
            for skip_line in skipped_lines:
                if symbol in skip_line:
                    # Parse skip time
                    try:
                        skip_time_str = skip_line.split(' - ')[0]
                        skip_time = datetime.strptime(skip_time_str, '%Y-%m-%d %H:%M:%S')
                        trade_time_obj = datetime.strptime(trade_time, '%Y-%m-%d %H:%M:%S')
                        
                        # Check if times are close (within 1 hour)
                        time_diff = abs((trade_time_obj - skip_time).total_seconds())
                        if time_diff < 3600:
                            issues += 1
                            self.results['filter_checks']['issues'].append(
                                f"Trade executed for {symbol} at {trade_time} but was skipped at {skip_time_str}"
                            )
                    except:
                        pass
        
        if issues == 0:
            self.results['filter_checks']['passed'] += 1
            print("  [OK] No incorrectly executed trades found")
        else:
            self.results['filter_checks']['failed'] += 1
            print(f"  ✗ Found {issues} potential filter violations")
    
    def _verify_hft_performance(self):
        """Verify Micro-HFT trades achieved expected profits."""
        trades_dir = Path('logs/trades')
        if not trades_dir.exists():
            self.results['hft_checks']['issues'].append("Trade logs directory not found")
            return
        
        hft_trades = []
        
        # Find all Micro-HFT trades
        for log_file in trades_dir.glob('*.log'):
            if 'backup' in log_file.name:
                continue
            
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and line.startswith('{'):
                            try:
                                trade = json.loads(line)
                                additional_info = trade.get('additional_info', {})
                                
                                # Check if this is a Micro-HFT trade
                                if (additional_info.get('close_type') == 'micro_hft' or
                                    'Micro-HFT' in str(additional_info.get('close_reason', ''))):
                                    hft_trades.append(trade)
                            except:
                                pass
            except:
                pass
        
        if len(hft_trades) == 0:
            print("  [WARNING]  No Micro-HFT trades found in logs")
            self.results['hft_checks']['issues'].append("No Micro-HFT trades found")
            return
        
        print(f"  Found {len(hft_trades)} Micro-HFT trades")
        
        # Analyze profit distribution
        profits = [trade.get('profit_usd', 0) for trade in hft_trades if trade.get('profit_usd')]
        in_sweet_spot = sum(1 for p in profits if 0.03 <= p <= 0.10)
        below_min = sum(1 for p in profits if p < 0.03)
        above_max = sum(1 for p in profits if p > 0.10)
        
        print(f"    - Trades in sweet spot ($0.03-$0.10): {in_sweet_spot}/{len(profits)}")
        print(f"    - Trades below $0.03: {below_min}")
        print(f"    - Trades above $0.10: {above_max}")
        
        if profits:
            avg_profit = sum(profits) / len(profits)
            min_profit = min(profits)
            max_profit = max(profits)
            print(f"    - Average profit: ${avg_profit:.2f}")
            print(f"    - Min profit: ${min_profit:.2f}")
            print(f"    - Max profit: ${max_profit:.2f}")
        
        # Check success rate
        sweet_spot_rate = (in_sweet_spot / len(profits) * 100) if profits else 0
        
        if sweet_spot_rate >= 70:  # At least 70% in sweet spot
            self.results['hft_checks']['passed'] += 1
            print(f"  [OK] Micro-HFT performance good ({sweet_spot_rate:.1f}% in sweet spot)")
        else:
            self.results['hft_checks']['failed'] += 1
            self.results['hft_checks']['issues'].append(
                f"Only {sweet_spot_rate:.1f}% of Micro-HFT trades in sweet spot (target: 70%+)"
            )
            print(f"  ✗ Micro-HFT performance below target ({sweet_spot_rate:.1f}% in sweet spot)")
        
        # Store summary
        self.results['summary'] = {
            'total_hft_trades': len(hft_trades),
            'sweet_spot_count': in_sweet_spot,
            'sweet_spot_rate': sweet_spot_rate,
            'avg_profit': avg_profit if profits else 0,
            'min_profit': min_profit if profits else 0,
            'max_profit': max_profit if profits else 0
        }
    
    def _generate_report(self) -> str:
        """Generate verification report."""
        os.makedirs('logs/reports', exist_ok=True)
        date_str = datetime.now().strftime('%Y-%m-%d')
        report_file = f'logs/reports/filter_hft_verification_{date_str}.txt'
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("FILTER AND MICRO-HFT VERIFICATION REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n")
            
            f.write("FILTER VERIFICATION\n")
            f.write("-" * 80 + "\n")
            f.write(f"Passed Checks: {self.results['filter_checks']['passed']}\n")
            f.write(f"Failed Checks: {self.results['filter_checks']['failed']}\n")
            if self.results['filter_checks']['issues']:
                f.write("\nIssues:\n")
                for issue in self.results['filter_checks']['issues']:
                    f.write(f"  - {issue}\n")
            f.write("\n")
            
            f.write("MICRO-HFT PERFORMANCE\n")
            f.write("-" * 80 + "\n")
            f.write(f"Passed Checks: {self.results['hft_checks']['passed']}\n")
            f.write(f"Failed Checks: {self.results['hft_checks']['failed']}\n")
            if self.results['summary']:
                f.write(f"\nSummary:\n")
                f.write(f"  Total HFT Trades: {self.results['summary'].get('total_hft_trades', 0)}\n")
                f.write(f"  In Sweet Spot: {self.results['summary'].get('sweet_spot_count', 0)}\n")
                f.write(f"  Sweet Spot Rate: {self.results['summary'].get('sweet_spot_rate', 0):.1f}%\n")
                f.write(f"  Average Profit: ${self.results['summary'].get('avg_profit', 0):.2f}\n")
                f.write(f"  Min Profit: ${self.results['summary'].get('min_profit', 0):.2f}\n")
                f.write(f"  Max Profit: ${self.results['summary'].get('max_profit', 0):.2f}\n")
            if self.results['hft_checks']['issues']:
                f.write("\nIssues:\n")
                for issue in self.results['hft_checks']['issues']:
                    f.write(f"  - {issue}\n")
            f.write("\n")
        
        print(f"  Report saved: {report_file}")
        return report_file
    
    def run(self):
        """Run verification."""
        results = self.verify_all()
        
        print()
        print("=" * 80)
        print("VERIFICATION COMPLETE")
        print("=" * 80)
        print(f"Filter Checks: {self.results['filter_checks']['passed']} passed, {self.results['filter_checks']['failed']} failed")
        print(f"HFT Checks: {self.results['hft_checks']['passed']} passed, {self.results['hft_checks']['failed']} failed")
        
        if self.results['filter_checks']['failed'] == 0 and self.results['hft_checks']['failed'] == 0:
            print("[OK] All verification checks passed")
        else:
            print("[WARNING]  Some verification checks failed - see report for details")


def main():
    """Main entry point."""
    verifier = FilterAndHFTVerifier()
    verifier.run()


if __name__ == "__main__":
    main()

