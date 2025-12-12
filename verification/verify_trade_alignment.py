#!/usr/bin/env python3
"""
Trade Alignment Verification Script
Verifies 100% alignment between bot logs and broker reports after reconciliation.

This script provides comprehensive verification and generates a final report.
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.compare_bot_vs_broker import BotBrokerComparator


class TradeAlignmentVerifier:
    """Verifies complete alignment between bot and broker trades."""
    
    def __init__(self, broker_html_file: str = "TradingHistoryFromBroker.html"):
        self.comparator = BotBrokerComparator(broker_html_file)
        self.verification_results = {
            'total_checks': 0,
            'passed_checks': 0,
            'failed_checks': 0,
            'issues': []
        }
    
    def verify_alignment(self) -> Dict[str, Any]:
        """Run complete verification."""
        print("=" * 80)
        print("TRADE ALIGNMENT VERIFICATION")
        print("=" * 80)
        print()
        
        # Parse trades
        print("Step 1: Parsing broker and bot trades...")
        broker_trades = self.comparator.parse_broker_html()
        bot_trades, bot_start_time, bot_end_time = self.comparator.parse_bot_logs()
        
        if not broker_trades or not bot_trades:
            print("[ERROR] Cannot verify - missing trade data")
            return {}
        
        print()
        
        # Filter broker trades
        broker_trades_filtered = self.comparator.filter_broker_trades_by_time(
            broker_trades, bot_start_time, bot_end_time
        )
        
        print()
        
        # Match trades
        matches = self.comparator.match_trades(broker_trades_filtered, bot_trades)
        
        print()
        
        # Run verification checks
        print("Step 2: Running verification checks...")
        self._verify_matching(matches)
        self._verify_status_consistency(matches)
        self._verify_profit_accuracy(matches)
        self._verify_data_completeness(bot_trades)
        self._verify_jsonl_format()
        
        # Generate report
        print()
        print("Step 3: Generating verification report...")
        report = self._generate_verification_report(matches, broker_trades_filtered, bot_trades,
                                                   bot_start_time, bot_end_time)
        
        return report
    
    def _verify_matching(self, matches: Dict):
        """Verify all trades are matched."""
        self.verification_results['total_checks'] += 1
        
        only_in_broker = len(matches['only_in_broker'])
        only_in_bot = len(matches['only_in_bot'])
        
        if only_in_broker == 0 and only_in_bot == 0:
            print("  [OK] All trades matched")
            self.verification_results['passed_checks'] += 1
        else:
            issue = f"Matching failed: {only_in_broker} only in broker, {only_in_bot} only in bot"
            print(f"  ✗ {issue}")
            self.verification_results['failed_checks'] += 1
            self.verification_results['issues'].append(issue)
    
    def _verify_status_consistency(self, matches: Dict):
        """Verify status consistency."""
        self.verification_results['total_checks'] += 1
        
        status_mismatches = sum(1 for m in matches['matched'] 
                               if m['broker']['status'] != m['bot']['status'])
        
        if status_mismatches == 0:
            print("  [OK] All statuses match")
            self.verification_results['passed_checks'] += 1
        else:
            issue = f"Status mismatches: {status_mismatches} trades"
            print(f"  ✗ {issue}")
            self.verification_results['failed_checks'] += 1
            self.verification_results['issues'].append(issue)
    
    def _verify_profit_accuracy(self, matches: Dict):
        """Verify profit accuracy."""
        self.verification_results['total_checks'] += 1
        
        profit_errors = []
        for match in matches['matched']:
            broker_profit = match['broker'].get('profit_usd')
            bot_profit = match['bot'].get('profit_usd')
            
            if broker_profit is not None and bot_profit is not None:
                diff = abs(broker_profit - bot_profit)
                if diff > 0.01:
                    profit_errors.append({
                        'order_id': match['order_id'],
                        'diff': diff,
                        'broker': broker_profit,
                        'bot': bot_profit
                    })
        
        if len(profit_errors) == 0:
            print("  [OK] All profits match (within $0.01 tolerance)")
            self.verification_results['passed_checks'] += 1
        else:
            issue = f"Profit discrepancies: {len(profit_errors)} trades"
            print(f"  ✗ {issue}")
            self.verification_results['failed_checks'] += 1
            self.verification_results['issues'].append(issue)
    
    def _verify_data_completeness(self, bot_trades: List[Dict]):
        """Verify all bot trades have required fields."""
        self.verification_results['total_checks'] += 1
        
        required_fields = ['timestamp', 'symbol', 'trade_type', 'entry_price', 'status', 'order_id']
        incomplete = []
        
        for trade in bot_trades:
            missing = [f for f in required_fields if not trade.get(f)]
            if missing:
                incomplete.append({
                    'order_id': trade.get('order_id'),
                    'missing_fields': missing
                })
        
        if len(incomplete) == 0:
            print("  [OK] All trades have required fields")
            self.verification_results['passed_checks'] += 1
        else:
            issue = f"Incomplete data: {len(incomplete)} trades missing required fields"
            print(f"  ✗ {issue}")
            self.verification_results['failed_checks'] += 1
            self.verification_results['issues'].append(issue)
    
    def _verify_jsonl_format(self):
        """Verify all log files are valid JSONL format."""
        self.verification_results['total_checks'] += 1
        
        trades_dir = Path('logs/trades')
        invalid_files = []
        
        if trades_dir.exists():
            for log_file in trades_dir.glob('*.log'):
                if 'backup' in log_file.name:
                    continue
                
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        for line_num, line in enumerate(f, 1):
                            line = line.strip()
                            if line and not line.startswith('{'):
                                invalid_files.append(f"{log_file.name}: Line {line_num}")
                                break
                            if line:
                                json.loads(line)  # Validate JSON
                except json.JSONDecodeError as e:
                    invalid_files.append(f"{log_file.name}: JSON error at line {line_num}")
                except Exception as e:
                    invalid_files.append(f"{log_file.name}: {e}")
        
        if len(invalid_files) == 0:
            print("  [OK] All log files are valid JSONL format")
            self.verification_results['passed_checks'] += 1
        else:
            issue = f"Invalid JSONL format in {len(invalid_files)} files"
            print(f"  ✗ {issue}")
            self.verification_results['failed_checks'] += 1
            self.verification_results['issues'].append(issue)
    
    def _generate_verification_report(self, matches: Dict, broker_trades: List[Dict],
                                     bot_trades: List[Dict], bot_start_time, bot_end_time) -> Dict[str, Any]:
        """Generate verification report."""
        os.makedirs('logs/reports', exist_ok=True)
        date_str = datetime.now().strftime('%Y-%m-%d')
        report_file = f'logs/reports/verification_report_{date_str}.txt'
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("TRADE ALIGNMENT VERIFICATION REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if bot_start_time and bot_end_time:
                f.write(f"Bot Trading Period: {bot_start_time.strftime('%Y-%m-%d %H:%M:%S')} to {bot_end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n")
            
            f.write("VERIFICATION RESULTS\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total Checks: {self.verification_results['total_checks']}\n")
            f.write(f"Passed: {self.verification_results['passed_checks']}\n")
            f.write(f"Failed: {self.verification_results['failed_checks']}\n")
            f.write(f"Success Rate: {(self.verification_results['passed_checks'] / self.verification_results['total_checks'] * 100) if self.verification_results['total_checks'] > 0 else 0:.1f}%\n")
            f.write("\n")
            
            if self.verification_results['issues']:
                f.write("ISSUES FOUND\n")
                f.write("-" * 80 + "\n")
                for issue in self.verification_results['issues']:
                    f.write(f"  ✗ {issue}\n")
                f.write("\n")
            
            f.write("TRADE STATISTICS\n")
            f.write("-" * 80 + "\n")
            f.write(f"Broker Trades (in period): {len(broker_trades)}\n")
            f.write(f"Bot Trades: {len(bot_trades)}\n")
            f.write(f"Matched Trades: {len(matches['matched'])}\n")
            f.write(f"Only in Broker: {len(matches['only_in_broker'])}\n")
            f.write(f"Only in Bot: {len(matches['only_in_bot'])}\n")
            f.write("\n")
            
            # Alignment status
            if (len(matches['matched']) == len(broker_trades) and 
                len(matches['only_in_broker']) == 0 and
                len(matches['only_in_bot']) == 0 and
                self.verification_results['failed_checks'] == 0):
                f.write("=" * 80 + "\n")
                f.write("[OK] 100% ALIGNMENT ACHIEVED\n")
                f.write("=" * 80 + "\n")
                f.write("All trades are properly matched and reconciled.\n")
                f.write("Bot logs are 100% aligned with broker report.\n")
            else:
                f.write("=" * 80 + "\n")
                f.write("[WARNING] ALIGNMENT ISSUES DETECTED\n")
                f.write("=" * 80 + "\n")
                f.write("Please run reconcile_broker_trades.py to fix issues.\n")
        
        print(f"  Report saved: {report_file}")
        
        return {
            'alignment_achieved': (self.verification_results['failed_checks'] == 0 and
                                 len(matches['only_in_broker']) == 0 and
                                 len(matches['only_in_bot']) == 0),
            'verification_results': self.verification_results,
            'report_file': report_file
        }
    
    def run(self):
        """Run verification."""
        results = self.verify_alignment()
        
        print()
        print("=" * 80)
        print("VERIFICATION COMPLETE")
        print("=" * 80)
        print(f"Checks Passed: {self.verification_results['passed_checks']}/{self.verification_results['total_checks']}")
        
        if results.get('alignment_achieved'):
            print("[OK] 100% ALIGNMENT ACHIEVED")
        else:
            print("[WARNING]  Alignment issues detected - see report for details")
        print()


def main():
    """Main entry point."""
    verifier = TradeAlignmentVerifier()
    verifier.run()


if __name__ == "__main__":
    main()

