#!/usr/bin/env python3
"""
Comprehensive Verification System
Complete verification of bot performance, filters, broker alignment, and system integrity.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from typing import Dict, Any
from verification.verify_trade_alignment import TradeAlignmentVerifier
from verification.verify_filters_and_hft import FilterAndHFTVerifier
from monitor.bot_performance_optimizer import BotPerformanceOptimizer
from monitor.reconcile_broker_trades import TradeReconciler
from utils.logger_factory import get_logger

logger = get_logger("comprehensive_verification", "logs/live/system/comprehensive_verification.log")


class ComprehensiveVerification:
    """Complete verification of all bot systems."""
    
    def __init__(self):
        """Initialize comprehensive verification."""
        self.results = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'alignment': {},
            'filters_hft': {},
            'optimization': {},
            'reconciliation': {}
        }
    
    def run_full_verification(self, broker_html_file: str = 'TradingHistoryFromBroker.html') -> Dict[str, Any]:
        """Run complete verification suite."""
        print("=" * 80)
        print("COMPREHENSIVE VERIFICATION SYSTEM")
        print("=" * 80)
        print()
        
        # Step 1: Reconcile broker trades
        print("Step 1: Reconciling broker trades...")
        try:
            reconciler = TradeReconciler(broker_html_file)
            recon_results = reconciler.reconcile_trades(create_backup=True)
            self.results['reconciliation'] = recon_results
            print(f"  [OK] Reconciliation complete: {recon_results.get('updated_trades', 0)} trades updated")
        except Exception as e:
            logger.error(f"Error in reconciliation: {e}", exc_info=True)
            print(f"  ✗ Error: {e}")
        
        print()
        
        # Step 2: Verify trade alignment
        print("Step 2: Verifying trade alignment...")
        try:
            alignment_verifier = TradeAlignmentVerifier(broker_html_file)
            alignment_results = alignment_verifier.verify_alignment()
            self.results['alignment'] = alignment_results
            if alignment_results.get('alignment_achieved'):
                print("  [OK] 100% alignment achieved")
            else:
                print("  [WARNING]  Alignment issues detected")
        except Exception as e:
            logger.error(f"Error in alignment verification: {e}", exc_info=True)
            print(f"  ✗ Error: {e}")
        
        print()
        
        # Step 3: Verify filters and HFT
        print("Step 3: Verifying filters and Micro-HFT...")
        try:
            hft_verifier = FilterAndHFTVerifier()
            hft_results = hft_verifier.verify_all()
            self.results['filters_hft'] = hft_results
            hft_rate = hft_results.get('summary', {}).get('sweet_spot_rate', 0)
            print(f"  [OK] Filter/HFT verification complete: {hft_rate:.1f}% sweet spot rate")
        except Exception as e:
            logger.error(f"Error in filter/HFT verification: {e}", exc_info=True)
            print(f"  ✗ Error: {e}")
        
        print()
        
        # Step 4: Performance optimization analysis
        print("Step 4: Analyzing performance and generating optimizations...")
        try:
            optimizer = BotPerformanceOptimizer(broker_html_file=broker_html_file)
            opt_results = optimizer.run_full_analysis()
            self.results['optimization'] = opt_results
            suggestions = len(opt_results.get('suggestions', []))
            print(f"  [OK] Optimization analysis complete: {suggestions} suggestions generated")
        except Exception as e:
            logger.error(f"Error in optimization analysis: {e}", exc_info=True)
            print(f"  ✗ Error: {e}")
        
        print()
        
        # Generate comprehensive report
        report_file = self._generate_comprehensive_report()
        
        return self.results
    
    def _generate_comprehensive_report(self) -> str:
        """Generate comprehensive verification report."""
        os.makedirs('logs/reports', exist_ok=True)
        date_str = datetime.now().strftime('%Y-%m-%d')
        report_file = f'logs/reports/comprehensive_verification_{date_str}.txt'
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("COMPREHENSIVE VERIFICATION REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {self.results['timestamp']}\n")
            f.write("\n")
            
            # Reconciliation
            recon = self.results.get('reconciliation', {})
            f.write("RECONCILIATION\n")
            f.write("-" * 80 + "\n")
            f.write(f"Trades Reconciled: {recon.get('reconciled_trades', 0)}\n")
            f.write(f"Trades Updated: {recon.get('updated_trades', 0)}\n")
            f.write(f"Discrepancies Logged: {recon.get('discrepancies', 0)}\n")
            f.write("\n")
            
            # Alignment
            alignment = self.results.get('alignment', {})
            f.write("TRADE ALIGNMENT\n")
            f.write("-" * 80 + "\n")
            if alignment.get('alignment_achieved'):
                f.write("[OK] 100% ALIGNMENT ACHIEVED\n")
            else:
                f.write("[WARNING]  Alignment issues detected\n")
            f.write("\n")
            
            # Filters & HFT
            hft = self.results.get('filters_hft', {})
            summary = hft.get('summary', {})
            f.write("FILTERS & MICRO-HFT\n")
            f.write("-" * 80 + "\n")
            f.write(f"HFT Sweet Spot Rate: {summary.get('sweet_spot_rate', 0):.1f}%\n")
            f.write(f"Total HFT Trades: {summary.get('total_hft_trades', 0)}\n")
            f.write("\n")
            
            # Optimization
            opt = self.results.get('optimization', {})
            suggestions = opt.get('suggestions', [])
            f.write("OPTIMIZATION SUGGESTIONS\n")
            f.write("-" * 80 + "\n")
            if suggestions:
                for i, suggestion in enumerate(suggestions, 1):
                    f.write(f"{i}. [{suggestion.get('priority')}] {suggestion.get('type')}\n")
                    f.write(f"   {suggestion.get('reason', '')}\n")
            else:
                f.write("No optimization suggestions at this time.\n")
            f.write("\n")
            
            # Overall Status
            f.write("=" * 80 + "\n")
            f.write("OVERALL STATUS\n")
            f.write("=" * 80 + "\n")
            
            alignment_ok = alignment.get('alignment_achieved', False)
            hft_rate = summary.get('sweet_spot_rate', 0)
            hft_ok = hft_rate >= 70
            
            if alignment_ok and hft_ok:
                f.write("[OK] SYSTEM FULLY OPERATIONAL\n")
                f.write("All verification checks passed.\n")
            else:
                f.write("[WARNING]  ISSUES DETECTED\n")
                if not alignment_ok:
                    f.write("- Trade alignment issues detected\n")
                if not hft_ok:
                    f.write(f"- HFT sweet spot rate below target ({hft_rate:.1f}% < 70%)\n")
        
        print(f"  Report saved: {report_file}")
        return report_file
    
    def run(self):
        """Run comprehensive verification."""
        results = self.run_full_verification()
        
        print()
        print("=" * 80)
        print("VERIFICATION COMPLETE")
        print("=" * 80)
        
        alignment_ok = results.get('alignment', {}).get('alignment_achieved', False)
        hft_rate = results.get('filters_hft', {}).get('summary', {}).get('sweet_spot_rate', 0)
        
        print(f"Trade Alignment: {'[OK] 100%' if alignment_ok else '[WARNING] Issues'}")
        print(f"HFT Sweet Spot Rate: {hft_rate:.1f}%")
        print()
        
        if alignment_ok and hft_rate >= 70:
            print("[OK] SYSTEM FULLY OPERATIONAL")
        else:
            print("[WARNING]  Review report for issues and suggestions")


def main():
    """Main entry point."""
    verifier = ComprehensiveVerification()
    verifier.run()


if __name__ == "__main__":
    main()

