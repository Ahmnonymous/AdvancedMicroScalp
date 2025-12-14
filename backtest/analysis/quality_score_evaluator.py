#!/usr/bin/env python3
"""
Quality Score Evaluator
Analyzes quality score data from executed trades to evaluate score effectiveness.
READ-ONLY: Does not modify quality score calculation or thresholds.
"""

import json
import os
import re
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional

class QualityScoreEvaluator:
    """Evaluates quality score effectiveness."""
    
    def __init__(self, logs_dir: str = "logs/backtest"):
        self.logs_dir = logs_dir
        self.trades_dir = os.path.join(logs_dir, "trades")
        self.execution_log = os.path.join(logs_dir, "execution.log")
        
        # Data structures
        self.executed_trades: List[Dict[str, Any]] = []
        self.rejected_trades: List[Dict[str, Any]] = []
        
    def parse_executed_trades(self):
        """Parse trade logs for executed trades with quality scores."""
        if not os.path.exists(self.trades_dir):
            print(f"Trades directory not found: {self.trades_dir}")
            return
        
        for trade_file in os.listdir(self.trades_dir):
            if not trade_file.endswith('.log'):
                continue
            
            symbol = trade_file.replace('.log', '')
            filepath = os.path.join(self.trades_dir, trade_file)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Look for quality score in log lines
                        quality_match = re.search(r'Quality Score:\s*([\d.]+)', line)
                        if quality_match:
                            quality_score = float(quality_match.group(1))
                            
                            # Look for JSON trade data
                            if line.strip().startswith('{'):
                                try:
                                    trade_data = json.loads(line.strip())
                                    trade_data['quality_score'] = quality_score
                                    trade_data['symbol'] = symbol
                                    if trade_data.get('status') == 'OPEN':
                                        self.executed_trades.append(trade_data)
                                except json.JSONDecodeError:
                                    continue
            
            except Exception as e:
                print(f"Error parsing {filepath}: {e}")
                continue
        
        # Also parse execution log for trade outcomes
        self._parse_execution_log()
        
        print(f"Parsed {len(self.executed_trades)} executed trades with quality scores")
    
    def _parse_execution_log(self):
        """Parse execution.log to get trade outcomes."""
        if not os.path.exists(self.execution_log):
            return
        
        sl_tp_pattern = re.compile(
            r'\[SL/TP HIT\].*?Ticket (\d+).*?\| (\w+).*?Profit: \$(-?\d+\.\d+)'
        )
        
        trade_outcomes = {}  # ticket -> profit
        
        try:
            with open(self.execution_log, 'r', encoding='utf-8') as f:
                for line in f:
                    match = sl_tp_pattern.search(line)
                    if match:
                        ticket = int(match.group(1))
                        symbol = match.group(2)
                        profit = float(match.group(3))
                        trade_outcomes[ticket] = {'symbol': symbol, 'profit': profit}
        except Exception as e:
            print(f"Error parsing execution log: {e}")
            return
        
        # Match outcomes to executed trades
        for trade in self.executed_trades:
            order_id = trade.get('order_id')
            if order_id:
                try:
                    ticket = int(order_id)
                    if ticket in trade_outcomes:
                        trade['outcome'] = 'win' if trade_outcomes[ticket]['profit'] > 0 else 'loss'
                        trade['profit_usd'] = trade_outcomes[ticket]['profit']
                except (ValueError, TypeError):
                    pass
    
    def parse_rejected_trades(self):
        """Parse system_startup.log for rejected trades with quality scores."""
        log_file = os.path.join(self.logs_dir, "system_startup.log")
        log_files = [
            log_file,
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
        
        quality_score_pattern = re.compile(
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[SKIP\].*?\[SKIP\].*?(\w+).*?Quality score ([\d.]+).*?< threshold ([\d.]+).*?Details:\s*(.+?)(?:\s*\||$)'
        )
        
        for log_file in log_files:
            if not os.path.exists(log_file):
                continue
            
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        match = quality_score_pattern.search(line)
                        if match:
                            timestamp_str = match.group(1)
                            symbol = match.group(2)
                            quality_score = float(match.group(3))
                            threshold = float(match.group(4))
                            details = match.group(5).strip()
                            
                            self.rejected_trades.append({
                                'timestamp': timestamp_str,
                                'symbol': symbol,
                                'quality_score': quality_score,
                                'threshold': threshold,
                                'details': details
                            })
            except Exception as e:
                print(f"Error parsing {log_file}: {e}")
                continue
        
        print(f"Parsed {len(self.rejected_trades)} rejected trades with quality scores")
    
    def analyze_distributions(self) -> Dict[str, Any]:
        """Analyze quality score distributions."""
        executed_scores = [t.get('quality_score', 0) for t in self.executed_trades if t.get('quality_score') is not None]
        rejected_scores = [t.get('quality_score', 0) for t in self.rejected_trades if t.get('quality_score') is not None]
        
        results = {
            'executed_count': len(executed_scores),
            'rejected_count': len(rejected_scores),
            'executed_stats': {},
            'rejected_stats': {}
        }
        
        if executed_scores:
            results['executed_stats'] = {
                'mean': statistics.mean(executed_scores),
                'median': statistics.median(executed_scores),
                'min': min(executed_scores),
                'max': max(executed_scores),
                'stdev': statistics.stdev(executed_scores) if len(executed_scores) > 1 else 0
            }
        
        if rejected_scores:
            results['rejected_stats'] = {
                'mean': statistics.mean(rejected_scores),
                'median': statistics.median(rejected_scores),
                'min': min(rejected_scores),
                'max': max(rejected_scores),
                'stdev': statistics.stdev(rejected_scores) if len(rejected_scores) > 1 else 0
            }
        
        return results
    
    def analyze_outcomes(self) -> Dict[str, Any]:
        """Analyze quality score vs trade outcomes."""
        if not self.executed_trades:
            return {}
        
        wins = [t for t in self.executed_trades if t.get('outcome') == 'win']
        losses = [t for t in self.executed_trades if t.get('outcome') == 'loss']
        
        win_scores = [t.get('quality_score', 0) for t in wins if t.get('quality_score') is not None]
        loss_scores = [t.get('quality_score', 0) for t in losses if t.get('quality_score') is not None]
        
        results = {
            'total_trades': len(self.executed_trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': (len(wins) / len(self.executed_trades) * 100) if self.executed_trades else 0,
            'win_score_stats': {},
            'loss_score_stats': {}
        }
        
        if win_scores:
            results['win_score_stats'] = {
                'mean': statistics.mean(win_scores),
                'median': statistics.median(win_scores),
                'min': min(win_scores),
                'max': max(win_scores)
            }
        
        if loss_scores:
            results['loss_score_stats'] = {
                'mean': statistics.mean(loss_scores),
                'median': statistics.median(loss_scores),
                'min': min(loss_scores),
                'max': max(loss_scores)
            }
        
        return results
    
    def analyze_components(self) -> Dict[str, Any]:
        """Analyze individual quality score components from rejection details."""
        component_patterns = {
            'trend': r'Strong trend|Moderate trend|Weak trend',
            'rsi': r'RSI',
            'volatility_floor': r'Volatility floor',
            'candle_quality': r'Candle quality',
            'choppiness': r'choppy|Low choppiness',
            'spread_penalty': r'Spread penalty'
        }
        
        component_counts = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0})
        
        # Analyze from executed trades (if we have outcome data)
        for trade in self.executed_trades:
            if 'outcome' not in trade:
                continue
            
            # Try to extract components from trade data
            # (This is limited - we may not have detailed breakdown in trade logs)
            outcome = trade['outcome']
            
            # For now, we can only analyze what's available in rejection details
            # Component analysis would require more detailed logging
        
        # Analyze from rejected trade details
        for rejection in self.rejected_trades:
            details = rejection.get('details', '').lower()
            for component, pattern in component_patterns.items():
                if re.search(pattern.lower(), details):
                    component_counts[component]['total'] += 1
        
        return {
            'component_frequency': {
                comp: counts['total']
                for comp, counts in component_counts.items()
            }
        }
    
    def generate_report(self) -> str:
        """Generate quality score evaluation report."""
        report = []
        report.append("=" * 100)
        report.append("QUALITY SCORE EVALUATION REPORT")
        report.append("=" * 100)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        if not self.executed_trades and not self.rejected_trades:
            report.append("No quality score data found in logs.")
            report.append("")
            return "\n".join(report)
        
        # 1. Data Availability
        report.append("1. DATA AVAILABILITY")
        report.append("-" * 100)
        report.append(f"Executed Trades with Quality Scores: {len(self.executed_trades)}")
        report.append(f"Rejected Trades with Quality Scores: {len(self.rejected_trades)}")
        report.append("")
        
        # 2. Score Distribution Analysis
        report.append("2. QUALITY SCORE DISTRIBUTION")
        report.append("-" * 100)
        
        dist_analysis = self.analyze_distributions()
        
        if dist_analysis['executed_stats']:
            report.append("Executed Trades:")
            stats = dist_analysis['executed_stats']
            report.append(f"  Mean: {stats['mean']:.2f}")
            report.append(f"  Median: {stats['median']:.2f}")
            report.append(f"  Range: {stats['min']:.2f} - {stats['max']:.2f}")
            report.append(f"  Std Dev: {stats['stdev']:.2f}")
        
        if dist_analysis['rejected_stats']:
            report.append("")
            report.append("Rejected Trades:")
            stats = dist_analysis['rejected_stats']
            report.append(f"  Mean: {stats['mean']:.2f}")
            report.append(f"  Median: {stats['median']:.2f}")
            report.append(f"  Range: {stats['min']:.2f} - {stats['max']:.2f}")
            report.append(f"  Std Dev: {stats['stdev']:.2f}")
        
        # Score discrimination check
        if dist_analysis['executed_stats'] and dist_analysis['rejected_stats']:
            exec_mean = dist_analysis['executed_stats']['mean']
            rej_mean = dist_analysis['rejected_stats']['mean']
            discrimination = exec_mean - rej_mean
            
            report.append("")
            report.append("Score Discrimination:")
            report.append(f"  Executed Mean - Rejected Mean: {discrimination:.2f}")
            if discrimination > 5:
                report.append("  [OK] Good discrimination (executed scores significantly higher)")
            elif discrimination > 0:
                report.append("  [WARN] Moderate discrimination (executed scores slightly higher)")
            else:
                report.append("  [FAIL] Poor discrimination (executed scores not higher than rejected)")
        
        report.append("")
        
        # 3. Quality Score vs Outcomes
        report.append("3. QUALITY SCORE VS TRADE OUTCOMES")
        report.append("-" * 100)
        
        outcome_analysis = self.analyze_outcomes()
        
        if outcome_analysis:
            report.append(f"Total Executed Trades: {outcome_analysis['total_trades']}")
            report.append(f"Wins: {outcome_analysis['wins']} ({outcome_analysis['win_rate']:.1f}%)")
            report.append(f"Losses: {outcome_analysis['losses']}")
            report.append("")
            
            if outcome_analysis['win_score_stats']:
                report.append("Winning Trades - Quality Score:")
                stats = outcome_analysis['win_score_stats']
                report.append(f"  Mean: {stats['mean']:.2f}")
                report.append(f"  Median: {stats['median']:.2f}")
                report.append(f"  Range: {stats['min']:.2f} - {stats['max']:.2f}")
            
            if outcome_analysis['loss_score_stats']:
                report.append("")
                report.append("Losing Trades - Quality Score:")
                stats = outcome_analysis['loss_score_stats']
                report.append(f"  Mean: {stats['mean']:.2f}")
                report.append(f"  Median: {stats['median']:.2f}")
                report.append(f"  Range: {stats['min']:.2f} - {stats['max']:.2f}")
            
            # Outcome discrimination check
            if outcome_analysis['win_score_stats'] and outcome_analysis['loss_score_stats']:
                win_mean = outcome_analysis['win_score_stats']['mean']
                loss_mean = outcome_analysis['loss_score_stats']['mean']
                outcome_discrimination = win_mean - loss_mean
                
                report.append("")
                report.append("Outcome Discrimination:")
                report.append(f"  Win Mean - Loss Mean: {outcome_discrimination:.2f}")
                if outcome_discrimination > 5:
                    report.append("  [OK] Strong predictive value (winners have significantly higher scores)")
                elif outcome_discrimination > 0:
                    report.append("  [WARN] Moderate predictive value (winners have slightly higher scores)")
                else:
                    report.append("  [FAIL] Weak predictive value (no clear score difference between wins/losses)")
        
        report.append("")
        
        # 4. Component Analysis
        report.append("4. COMPONENT FREQUENCY ANALYSIS")
        report.append("-" * 100)
        report.append("(Based on rejection details - limited data available)")
        report.append("")
        
        component_analysis = self.analyze_components()
        
        if component_analysis['component_frequency']:
            sorted_components = sorted(
                component_analysis['component_frequency'].items(),
                key=lambda x: x[1],
                reverse=True
            )
            
            for component, count in sorted_components:
                report.append(f"  {component.replace('_', ' ').title()}: {count} occurrences")
        else:
            report.append("  Limited component data available in logs")
        
        report.append("")
        
        # 5. Recommendations
        report.append("5. RECOMMENDATIONS (DIAGNOSTIC ONLY - NO CHANGES MADE)")
        report.append("-" * 100)
        
        if not outcome_analysis or outcome_analysis['total_trades'] < 10:
            report.append("[WARN] INSUFFICIENT DATA: Need at least 10 executed trades for meaningful analysis")
            report.append("   Recommendation: Run longer backtest to gather more data")
        else:
            # Generate recommendations based on analysis
            if outcome_analysis['win_rate'] < 30:
                report.append("[WARN] LOW WIN RATE: Current quality score may not be selecting good trades")
                report.append("   Consider: Reviewing score components and weights")
            
            if dist_analysis['executed_stats'] and dist_analysis['rejected_stats']:
                exec_mean = dist_analysis['executed_stats']['mean']
                rej_mean = dist_analysis['rejected_stats']['mean']
                if exec_mean - rej_mean < 5:
                    report.append("[WARN] POOR SCORE DISCRIMINATION: Executed scores not much higher than rejected")
                    report.append("   Consider: Tightening quality score threshold or improving score calculation")
            
            if outcome_analysis.get('win_score_stats') and outcome_analysis.get('loss_score_stats'):
                win_mean = outcome_analysis['win_score_stats']['mean']
                loss_mean = outcome_analysis['loss_score_stats']['mean']
                if win_mean - loss_mean < 3:
                    report.append("[WARN] WEAK PREDICTIVE VALUE: Winning trades don't have significantly higher scores")
                    report.append("   Consider: Reviewing which components correlate with wins vs losses")
            
            report.append("")
            report.append("[OK] CURRENT THRESHOLD ASSESSMENT:")
            if dist_analysis['executed_stats']:
                exec_mean = dist_analysis['executed_stats']['mean']
                if exec_mean >= 70:
                    report.append(f"   Threshold (70) appears reasonable - executed trades average {exec_mean:.1f}")
                elif exec_mean >= 60:
                    report.append(f"   Threshold (70) may be slightly high - executed trades average {exec_mean:.1f}")
                else:
                    report.append(f"   Threshold (70) may be too high - executed trades average {exec_mean:.1f}")
        
        report.append("")
        report.append("=" * 100)
        
        return "\n".join(report)
    
    def save_json(self, output_file: str):
        """Save analysis results to JSON."""
        results = {
            'executed_trades': len(self.executed_trades),
            'rejected_trades': len(self.rejected_trades),
            'distribution_analysis': self.analyze_distributions(),
            'outcome_analysis': self.analyze_outcomes(),
            'component_analysis': self.analyze_components()
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        print(f"Analysis results saved to: {output_file}")

def main():
    """Main entry point."""
    evaluator = QualityScoreEvaluator()
    
    print("Parsing executed trades...")
    evaluator.parse_executed_trades()
    
    print("Parsing rejected trades...")
    evaluator.parse_rejected_trades()
    
    print("Generating report...")
    report = evaluator.generate_report()
    
    # Save report
    output_file = "backtest/analysis/QUALITY_SCORE_EVALUATION_REPORT.txt"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\nReport saved to: {output_file}")
    
    # Print report with safe encoding
    try:
        import sys
        sys.stdout.reconfigure(encoding='utf-8')
        print(report)
    except:
        print("Report generated (see file for full content)")
    
    # Save JSON
    json_file = "backtest/analysis/quality_score_evaluation_data.json"
    evaluator.save_json(json_file)

if __name__ == "__main__":
    main()

