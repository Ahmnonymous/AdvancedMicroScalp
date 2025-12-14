#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive Trading Log Analysis Tool
Analyzes all logs (live and backtest) to extract trading behavior patterns.
READ-ONLY: Does not modify any code or logs.
"""

import json
import re
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
import statistics

# Configure UTF-8 encoding for output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

class TradingLogAnalyzer:
    """Comprehensive log analyzer for trading system."""
    
    def __init__(self):
        self.logs_dir = "logs"
        self.backtest_dir = os.path.join(self.logs_dir, "backtest")
        self.live_dir = os.path.join(self.logs_dir, "live")
        
        # Data structures
        self.trades: Dict[int, Dict[str, Any]] = {}
        self.closed_trades: List[Dict[str, Any]] = []
        self.filtered_trades: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        
        # Filter tracking
        self.filter_blocks = {
            'trend_block': 0,
            'cooldown_block': 0,
            'volatility_floor': 0,
            'spread_sanity': 0,
            'candle_quality': 0,
            'session_guard': 0,
            'circuit_breaker': 0,
            'quality_score': 0,
            'portfolio_risk': 0,
            'max_trades': 0,
            'other': 0
        }
        
        # Symbol stats
        self.symbol_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_profit': 0.0,
            'total_loss': 0.0,
            'avg_profit': 0.0,
            'avg_loss': 0.0,
            'max_profit': 0.0,
            'max_loss': 0.0,
            'sl_hits': 0,
            'tp_hits': 0,
            'quality_scores': []
        })
        
    def parse_system_startup_log(self):
        """Parse system startup log for filtered trades and quality scores."""
        log_files = [
            os.path.join(self.backtest_dir, "system_startup.log"),
            os.path.join(self.backtest_dir, "system_startup.log.1"),
            os.path.join(self.backtest_dir, "system_startup.log.2")
        ]
        
        patterns = {
            'quality_score': re.compile(r'Quality score ([\d.]+) < threshold ([\d.]+)'),
            'trend_block': re.compile(r'\[TREND BLOCK\] (\w+):'),
            'cooldown_block': re.compile(r'\[COOLDOWN BLOCK\] (\w+):'),
            'filter_block': re.compile(r'\[FILTER BLOCK\] (\w+): (.+?)(?:\| |$)'),
            'skip': re.compile(r'\[SKIP\] \[SKIP\] (\w+).*?Reason: (.+?)(?:\| |$)')
        }
        
        for log_file in log_files:
            if not os.path.exists(log_file):
                continue
            
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Quality score filter
                        match = patterns['quality_score'].search(line)
                        if match:
                            score = float(match.group(1))
                            threshold = float(match.group(2))
                            self.filter_blocks['quality_score'] += 1
                            continue
                        
                        # Trend block
                        if patterns['trend_block'].search(line):
                            self.filter_blocks['trend_block'] += 1
                            continue
                        
                        # Cooldown block
                        if patterns['cooldown_block'].search(line):
                            self.filter_blocks['cooldown_block'] += 1
                            continue
                        
                        # Filter block
                        match = patterns['filter_block'].search(line)
                        if match:
                            symbol = match.group(1)
                            reason = match.group(2).strip()
                            if 'volatility floor' in reason.lower():
                                self.filter_blocks['volatility_floor'] += 1
                            elif 'spread sanity' in reason.lower() or 'spread' in reason.lower():
                                self.filter_blocks['spread_sanity'] += 1
                            elif 'candle quality' in reason.lower():
                                self.filter_blocks['candle_quality'] += 1
                            elif 'session guard' in reason.lower():
                                self.filter_blocks['session_guard'] += 1
                            else:
                                self.filter_blocks['other'] += 1
                            continue
                        
                        # Skip patterns
                        match = patterns['skip'].search(line)
                        if match:
                            symbol = match.group(1)
                            reason = match.group(2).strip()
                            
                            if 'circuit breaker' in reason.lower():
                                self.filter_blocks['circuit_breaker'] += 1
                            elif 'portfolio risk' in reason.lower():
                                self.filter_blocks['portfolio_risk'] += 1
                            elif 'max_open_trades' in reason.lower() or 'max trades' in reason.lower():
                                self.filter_blocks['max_trades'] += 1
                            elif 'quality score' in reason.lower():
                                self.filter_blocks['quality_score'] += 1
            except Exception as e:
                print(f"Error parsing {log_file}: {e}")
    
    def parse_execution_log(self):
        """Parse execution.log for trade closures."""
        log_file = os.path.join(self.backtest_dir, "execution.log")
        if not os.path.exists(log_file):
            return
        
        # Patterns
        sl_tp_pattern = re.compile(
            r'\[SL/TP HIT\].*?Ticket (\d+).*?\| (\w+).*?Reason: (SL|TP).*?Entry: ([\d.]+).*?Close: ([\d.]+).*?Profit: \$(-?\d+\.\d+)'
        )
        
        closure_pattern = re.compile(
            r'\[OK\] SIMULATED POSITION CLOSED.*?Ticket (\d+).*?\| (\w+).*?Profit: \$(-?\d+\.\d+).*?Closure method: (\w+)'
        )
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    # SL/TP hit
                    match = sl_tp_pattern.search(line)
                    if match:
                        ticket = int(match.group(1))
                        symbol = match.group(2)
                        reason = match.group(3)
                        entry_price = float(match.group(4))
                        close_price = float(match.group(5))
                        profit = float(match.group(6))
                        
                        # Extract timestamp
                        ts_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        close_time = datetime.strptime(ts_match.group(1), '%Y-%m-%d %H:%M:%S') if ts_match else None
                        
                        trade = {
                            'ticket': ticket,
                            'symbol': symbol,
                            'close_reason': reason,
                            'entry_price': entry_price,
                            'close_price': close_price,
                            'profit_usd': profit,
                            'close_time': close_time,
                            'line': line_num
                        }
                        
                        self.closed_trades.append(trade)
                        self.symbol_stats[symbol]['total_trades'] += 1
                        if profit > 0:
                            self.symbol_stats[symbol]['wins'] += 1
                            self.symbol_stats[symbol]['total_profit'] += profit
                            self.symbol_stats[symbol]['max_profit'] = max(self.symbol_stats[symbol]['max_profit'], profit)
                        else:
                            self.symbol_stats[symbol]['losses'] += 1
                            self.symbol_stats[symbol]['total_loss'] += profit
                            self.symbol_stats[symbol]['max_loss'] = min(self.symbol_stats[symbol]['max_loss'], profit)
                        
                        if reason == 'SL':
                            self.symbol_stats[symbol]['sl_hits'] += 1
                        elif reason == 'TP':
                            self.symbol_stats[symbol]['tp_hits'] += 1
                        continue
                    
                    # General closure
                    match = closure_pattern.search(line)
                    if match:
                        ticket = int(match.group(1))
                        symbol = match.group(2)
                        profit = float(match.group(3))
                        method = match.group(4)
                        
                        if ticket not in [t['ticket'] for t in self.closed_trades]:
                            ts_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            close_time = datetime.strptime(ts_match.group(1), '%Y-%m-%d %H:%M:%S') if ts_match else None
                            
                            trade = {
                                'ticket': ticket,
                                'symbol': symbol,
                                'close_reason': method,
                                'profit_usd': profit,
                                'close_time': close_time,
                                'line': line_num
                            }
                            
                            self.closed_trades.append(trade)
                            self.symbol_stats[symbol]['total_trades'] += 1
                            if profit > 0:
                                self.symbol_stats[symbol]['wins'] += 1
                                self.symbol_stats[symbol]['total_profit'] += profit
                            else:
                                self.symbol_stats[symbol]['losses'] += 1
                                self.symbol_stats[symbol]['total_loss'] += profit
        except Exception as e:
            print(f"Error parsing execution.log: {e}")
    
    def parse_trend_detector_log(self):
        """Parse trend detector log for signal quality."""
        log_file = os.path.join(self.live_dir, "engine", "trend_detector.log")
        if not os.path.exists(log_file):
            return
        
        signal_pattern = re.compile(r'(\w+): \[OK\] TREND SIGNAL = (LONG|SHORT).*?RSI=([\d.]+)')
        quality_pattern = re.compile(r'Quality Score: ([\d.]+)')
        
        signals = defaultdict(list)
        
        try:
            # Only read last 1000 lines for performance
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines[-1000:]:
                    match = signal_pattern.search(line)
                    if match:
                        symbol = match.group(1)
                        direction = match.group(2)
                        rsi = float(match.group(3))
                        signals[symbol].append({'direction': direction, 'rsi': rsi})
        except Exception as e:
            print(f"Error parsing trend_detector.log: {e}")
        
        return signals
    
    def parse_risk_manager_log(self):
        """Parse risk manager log for circuit breaker and filter activity."""
        log_file = os.path.join(self.live_dir, "engine", "risk_manager.log")
        if not os.path.exists(log_file):
            return
        
        circuit_pattern = re.compile(r'CIRCUIT BREAKER.*?(triggered|lifted)')
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if circuit_pattern.search(line):
                        if 'triggered' in line.lower():
                            self.filter_blocks['circuit_breaker'] += 1
        except Exception as e:
            print(f"Error parsing risk_manager.log: {e}")
    
    def calculate_statistics(self):
        """Calculate overall statistics."""
        if not self.closed_trades:
            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'net_pnl': 0.0,
                'win_rate': 0.0,
                'avg_profit': 0.0,
                'avg_loss': 0.0,
                'max_drawdown': 0.0,
                'max_loss_streak': 0,
                'sl_hits': 0,
                'tp_hits': 0,
                'sl_rate': 0.0
            }
        
        profits = [t['profit_usd'] for t in self.closed_trades]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        
        # Calculate drawdown
        cumulative = 0
        peak = 0
        max_dd = 0
        for profit in profits:
            cumulative += profit
            peak = max(peak, cumulative)
            drawdown = peak - cumulative
            max_dd = max(max_dd, drawdown)
        
        # Calculate loss streak
        current_streak = 0
        max_streak = 0
        for profit in profits:
            if profit < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        total_sl = sum(1 for t in self.closed_trades if t.get('close_reason') == 'SL')
        total_tp = sum(1 for t in self.closed_trades if t.get('close_reason') == 'TP')
        
        sl_rate = (total_sl / len(self.closed_trades) * 100) if self.closed_trades else 0
        
        return {
            'total_trades': len(self.closed_trades),
            'wins': len(wins),
            'losses': len(losses),
            'net_pnl': sum(profits),
            'win_rate': (len(wins) / len(profits) * 100) if profits else 0,
            'avg_profit': statistics.mean(wins) if wins else 0,
            'avg_loss': statistics.mean(losses) if losses else 0,
            'max_drawdown': max_dd,
            'max_loss_streak': max_streak,
            'sl_hits': total_sl,
            'tp_hits': total_tp,
            'sl_rate': sl_rate
        }
    
    def generate_report(self) -> str:
        """Generate comprehensive analysis report."""
        report = []
        report.append("=" * 100)
        report.append("COMPREHENSIVE TRADING LOG ANALYSIS REPORT")
        report.append("=" * 100)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Executive Summary
        stats = self.calculate_statistics()
        report.append("1. EXECUTIVE SUMMARY")
        report.append("-" * 100)
        report.append(f"Total Closed Trades: {stats['total_trades']}")
        report.append(f"Wins: {stats['wins']} ({stats['win_rate']:.1f}%)")
        report.append(f"Losses: {stats['losses']}")
        report.append(f"Net P&L: ${stats['net_pnl']:.2f}")
        report.append(f"Average Profit per Win: ${stats['avg_profit']:.2f}")
        report.append(f"Average Loss per Loss: ${stats['avg_loss']:.2f}")
        report.append(f"Max Drawdown: ${stats['max_drawdown']:.2f}")
        report.append(f"Max Loss Streak: {stats['max_loss_streak']} trades")
        report.append(f"SL Hits: {stats['sl_hits']} ({stats['sl_rate']:.1f}%)")
        report.append(f"TP Hits: {stats['tp_hits']}")
        report.append("")
        
        # Symbol Performance
        report.append("2. SYMBOL PERFORMANCE")
        report.append("-" * 100)
        if self.symbol_stats:
            for symbol, stats_data in sorted(self.symbol_stats.items()):
                if stats_data['total_trades'] > 0:
                    win_rate = (stats_data['wins'] / stats_data['total_trades'] * 100) if stats_data['total_trades'] > 0 else 0
                    net_pnl = stats_data['total_profit'] + stats_data['total_loss']
                    report.append(f"{symbol}:")
                    report.append(f"  Trades: {stats_data['total_trades']} (W: {stats_data['wins']}, L: {stats_data['losses']}, Win Rate: {win_rate:.1f}%)")
                    report.append(f"  Net P&L: ${net_pnl:.2f}")
                    report.append(f"  Avg Profit: ${stats_data['avg_profit']:.2f} | Avg Loss: ${stats_data['avg_loss']:.2f}")
                    report.append(f"  Max Profit: ${stats_data['max_profit']:.2f} | Max Loss: ${stats_data['max_loss']:.2f}")
                    report.append(f"  SL Hits: {stats_data['sl_hits']} | TP Hits: {stats_data['tp_hits']}")
                    report.append("")
        else:
            report.append("No symbol statistics available.")
            report.append("")
        
        # Filter Effectiveness
        report.append("3. FILTER EFFECTIVENESS")
        report.append("-" * 100)
        total_blocks = sum(self.filter_blocks.values())
        if total_blocks > 0:
            for filter_name, count in sorted(self.filter_blocks.items(), key=lambda x: x[1], reverse=True):
                if count > 0:
                    pct = (count / total_blocks * 100) if total_blocks > 0 else 0
                    report.append(f"{filter_name.replace('_', ' ').title()}: {count} ({pct:.1f}%)")
        else:
            report.append("No filter blocks recorded in logs.")
        report.append("")
        
        # Anomalies
        report.append("4. ANOMALIES & ISSUES")
        report.append("-" * 100)
        if stats['total_trades'] == 0:
            report.append("⚠️  NO TRADES FOUND: No closed trades detected in execution logs.")
            report.append("   This may indicate:")
            report.append("   - Kill switch is active")
            report.append("   - All trades are being filtered out")
            report.append("   - Logs are from a non-trading session")
        
        if stats['max_loss_streak'] > 5:
            report.append(f"⚠️  HIGH LOSS STREAK: {stats['max_loss_streak']} consecutive losses detected")
        
        if stats['sl_rate'] > 70:
            report.append(f"⚠️  HIGH SL HIT RATE: {stats['sl_rate']:.1f}% of trades hit SL")
        
        if total_blocks == 0 and stats['total_trades'] == 0:
            report.append("⚠️  NO FILTER ACTIVITY: No trades executed and no filters blocking trades")
            report.append("   System may be in standby/kill switch mode")
        
        report.append("")
        
        # Recommendations
        report.append("5. RECOMMENDATIONS")
        report.append("-" * 100)
        if stats['total_trades'] == 0:
            report.append("1. Check if kill switch is active")
            report.append("2. Verify entry filters are not too restrictive")
            report.append("3. Review trend signals and quality scores")
        
        if stats['win_rate'] < 40 and stats['total_trades'] > 10:
            report.append("1. Consider tightening entry filters (increase quality score threshold)")
            report.append("2. Review stop loss placement")
            report.append("3. Analyze losing trades for common patterns")
        
        if stats['sl_rate'] > 60:
            report.append("1. Review stop loss logic (may be too tight)")
            report.append("2. Consider adjusting trailing stop parameters")
        
        if self.filter_blocks['quality_score'] > total_blocks * 0.5:
            report.append("1. Quality score threshold may be too high")
            report.append("2. Consider reviewing quality score calculation")
        
        report.append("")
        report.append("=" * 100)
        
        return "\n".join(report)
    
    def analyze(self):
        """Run complete analysis."""
        print("Starting comprehensive log analysis...")
        print("Parsing system startup logs...")
        self.parse_system_startup_log()
        
        print("Parsing execution logs...")
        self.parse_execution_log()
        
        print("Parsing trend detector logs...")
        signals = self.parse_trend_detector_log()
        
        print("Parsing risk manager logs...")
        self.parse_risk_manager_log()
        
        print("Calculating statistics...")
        stats = self.calculate_statistics()
        
        print("Generating report...")
        report = self.generate_report()
        
        return report

if __name__ == "__main__":
    analyzer = TradingLogAnalyzer()
    report = analyzer.analyze()
    
    # Save to file
    output_file = "COMPREHENSIVE_LOG_ANALYSIS_REPORT.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    # Print to console (will handle encoding issues)
    try:
        print(report)
        print(f"\nReport saved to: {output_file}")
    except UnicodeEncodeError:
        print("Report generated successfully (console output skipped due to encoding)")
        print(f"Report saved to: {output_file}")

