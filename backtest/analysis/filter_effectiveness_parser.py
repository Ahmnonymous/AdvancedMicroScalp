#!/usr/bin/env python3
"""
Filter Effectiveness Parser
Parses [SKIP] log messages to analyze filter rejection patterns.
READ-ONLY: Does not modify trading logic or filters.
"""

import re
import os
import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Tuple

class FilterEffectivenessParser:
    """Parses and analyzes filter rejection logs."""
    
    def __init__(self, logs_dir: str = "logs/backtest"):
        self.logs_dir = logs_dir
        self.system_startup_log = os.path.join(logs_dir, "system_startup.log")
        
        # Filter categories
        self.filter_categories = {
            'volatility_floor': ['Volatility floor', 'volatility floor', 'avg range.*pips.*min'],
            'spread_sanity': ['Spread sanity', 'spread.*% of.*range', 'spread.*of avg range'],
            'candle_quality': ['Candle quality', 'current range.*% of avg', 'range.*% of avg.*min'],
            'trend_gate': ['Trend strength too weak', 'SMA separation', 'Trend too weak', 'TREND BLOCK'],
            'cooldown': ['Cooldown', 'COOLDOWN BLOCK'],
            'quality_score': ['Quality score.*< threshold', 'Quality score.*threshold'],
            'session_guard': ['Session guard', 'hour.*block', 'rollover'],
            'spread_fees': ['Spread+Fees exceed limit', 'Spread.*Fees.*\$.*>'],
            'staged_window': ['Staged window expired', 'staged window'],
            'market_closing': ['Stock index closing', 'market closing', 'closing in'],
            'news': ['NEWS BLOCKING', 'news'],
            'portfolio_risk': ['Portfolio risk', 'portfolio risk'],
            'max_trades': ['max.*trades', 'max_open_trades'],
            'other': []  # Catch-all for unmatched patterns
        }
        
        # Data structures
        self.filter_counts: Dict[str, int] = defaultdict(int)
        self.symbol_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.rejection_reasons: List[Dict[str, Any]] = []
        
    def parse_logs(self):
        """Parse system_startup.log for [SKIP] messages."""
        log_files = [
            self.system_startup_log,
            os.path.join(self.logs_dir, "system_startup.log.1"),
            os.path.join(self.logs_dir, "system_startup.log.2"),
            os.path.join(self.logs_dir, "system_startup.log.3"),
            os.path.join(self.logs_dir, "system_startup.log.4"),
            os.path.join(self.logs_dir, "system_startup.log.5"),
            os.path.join(self.logs_dir, "system_startup.log.6"),
            os.path.join(self.logs_dir, "system_startup.log.7"),
            os.path.join(self.logs_dir, "system_startup.log.8"),
            os.path.join(self.logs_dir, "system_startup.log.9"),
        ]
        
        skip_pattern = re.compile(
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[SKIP\]\s+\[SKIP\]\s+(\w+).*?Reason:\s*(.+?)(?:\s*\||$)'
        )
        
        total_skips = 0
        
        for log_file in log_files:
            if not os.path.exists(log_file):
                continue
            
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        match = skip_pattern.search(line)
                        if match:
                            timestamp_str = match.group(1)
                            symbol = match.group(2)
                            reason = match.group(3).strip()
                            
                            total_skips += 1
                            
                            # Categorize the rejection reason
                            category = self._categorize_rejection(reason)
                            
                            # Update counts
                            self.filter_counts[category] += 1
                            self.symbol_counts[symbol][category] += 1
                            
                            # Store detailed rejection
                            self.rejection_reasons.append({
                                'timestamp': timestamp_str,
                                'symbol': symbol,
                                'reason': reason,
                                'category': category
                            })
            except Exception as e:
                print(f"Error parsing {log_file}: {e}")
                continue
        
        print(f"Parsed {total_skips} [SKIP] messages from logs")
    
    def _categorize_rejection(self, reason: str) -> str:
        """Categorize a rejection reason into a filter category."""
        reason_lower = reason.lower()
        
        # Check each category (order matters - most specific first)
        for category, patterns in self.filter_categories.items():
            if category == 'other':
                continue  # Skip catch-all
            
            for pattern in patterns:
                if re.search(pattern.lower(), reason_lower, re.IGNORECASE):
                    return category
        
        return 'other'
    
    def generate_report(self) -> str:
        """Generate filter effectiveness report."""
        report = []
        report.append("=" * 100)
        report.append("FILTER EFFECTIVENESS ANALYSIS REPORT")
        report.append("=" * 100)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        total_rejections = sum(self.filter_counts.values())
        if total_rejections == 0:
            report.append("No filter rejections found in logs.")
            report.append("")
            return "\n".join(report)
        
        # 1. Overall Statistics
        report.append("1. OVERALL STATISTICS")
        report.append("-" * 100)
        report.append(f"Total Rejections: {total_rejections}")
        report.append("")
        
        # 2. Filter Category Breakdown
        report.append("2. FILTER CATEGORY BREAKDOWN")
        report.append("-" * 100)
        report.append(f"{'Filter Category':<30} {'Count':<15} {'Percentage':<15}")
        report.append("-" * 100)
        
        # Sort by count (descending)
        sorted_filters = sorted(self.filter_counts.items(), key=lambda x: x[1], reverse=True)
        
        for category, count in sorted_filters:
            percentage = (count / total_rejections * 100) if total_rejections > 0 else 0
            report.append(f"{category.replace('_', ' ').title():<30} {count:<15} {percentage:>6.2f}%")
        
        report.append("")
        
        # 3. Most Restrictive Filters
        report.append("3. MOST RESTRICTIVE FILTERS (Top 5)")
        report.append("-" * 100)
        for i, (category, count) in enumerate(sorted_filters[:5], 1):
            percentage = (count / total_rejections * 100) if total_rejections > 0 else 0
            report.append(f"{i}. {category.replace('_', ' ').title()}: {count} rejections ({percentage:.1f}%)")
        report.append("")
        
        # 4. Symbols Most Frequently Rejected
        report.append("4. SYMBOLS MOST FREQUENTLY REJECTED")
        report.append("-" * 100)
        
        symbol_totals = {}
        for symbol, categories in self.symbol_counts.items():
            symbol_totals[symbol] = sum(categories.values())
        
        sorted_symbols = sorted(symbol_totals.items(), key=lambda x: x[1], reverse=True)
        
        report.append(f"{'Symbol':<15} {'Total Rejections':<20} {'Top Filter':<30}")
        report.append("-" * 100)
        
        for symbol, total in sorted_symbols[:10]:  # Top 10
            top_category = max(self.symbol_counts[symbol].items(), key=lambda x: x[1])
            report.append(f"{symbol:<15} {total:<20} {top_category[0].replace('_', ' ').title()}: {top_category[1]}")
        
        report.append("")
        
        # 5. Filter Breakdown by Symbol
        report.append("5. FILTER BREAKDOWN BY SYMBOL")
        report.append("-" * 100)
        
        for symbol, categories in sorted(self.symbol_counts.items()):
            if sum(categories.values()) > 0:
                report.append(f"{symbol}:")
                total_for_symbol = sum(categories.values())
                for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                    pct = (count / total_for_symbol * 100) if total_for_symbol > 0 else 0
                    report.append(f"  - {cat.replace('_', ' ').title()}: {count} ({pct:.1f}%)")
                report.append("")
        
        # 6. Filters Rarely Triggered (Possible Dead Code)
        report.append("6. FILTERS RARELY TRIGGERED (< 1% of rejections)")
        report.append("-" * 100)
        rare_filters = [
            (cat, count) for cat, count in sorted_filters
            if (count / total_rejections * 100) < 1.0 and count > 0
        ]
        
        if rare_filters:
            for category, count in rare_filters:
                percentage = (count / total_rejections * 100) if total_rejections > 0 else 0
                report.append(f"- {category.replace('_', ' ').title()}: {count} rejections ({percentage:.2f}%)")
                report.append("  -> May indicate filter is too lenient or rarely applicable")
        else:
            report.append("All filters are actively used (> 1% of rejections)")
        
        report.append("")
        
        # 7. Sample Rejection Reasons (for each category)
        report.append("7. SAMPLE REJECTION REASONS (First 3 per category)")
        report.append("-" * 100)
        
        category_samples = defaultdict(list)
        for rejection in self.rejection_reasons:
            category = rejection['category']
            if len(category_samples[category]) < 3:
                category_samples[category].append(f"{rejection['symbol']}: {rejection['reason']}")
        
        for category in sorted_filters:
            cat = category[0]
            if category_samples[cat]:
                report.append(f"{cat.replace('_', ' ').title()}:")
                for sample in category_samples[cat]:
                    report.append(f"  - {sample}")
                report.append("")
        
        report.append("=" * 100)
        
        return "\n".join(report)
    
    def save_json(self, output_file: str):
        """Save results to JSON file."""
        results = {
            'total_rejections': sum(self.filter_counts.values()),
            'filter_counts': dict(self.filter_counts),
            'symbol_counts': {
                symbol: dict(categories)
                for symbol, categories in self.symbol_counts.items()
            },
            'sample_rejections': self.rejection_reasons[:100]  # First 100 for reference
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        print(f"Results saved to: {output_file}")

def main():
    """Main entry point."""
    parser = FilterEffectivenessParser()
    print("Parsing filter rejection logs...")
    parser.parse_logs()
    
    print("Generating report...")
    report = parser.generate_report()
    
    # Save report
    output_file = "backtest/analysis/FILTER_EFFECTIVENESS_REPORT.txt"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\nReport saved to: {output_file}")
    print("Report generated successfully (see file for full content)")
    
    # Save JSON
    json_file = "backtest/analysis/filter_effectiveness_data.json"
    parser.save_json(json_file)

if __name__ == "__main__":
    main()

