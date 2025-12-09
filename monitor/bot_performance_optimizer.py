#!/usr/bin/env python3
"""
Bot Performance Optimizer
Continuously monitors, analyzes, and optimizes bot performance to maximize profitability.

Features:
- Monitors filter performance and trade execution
- Analyzes Micro-HFT success rates
- Compares trades with broker reports
- Provides optimization suggestions
- Generates comprehensive daily reports
"""

import os
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import logging

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.compare_bot_vs_broker import BotBrokerComparator


class BotPerformanceOptimizer:
    """Monitors and optimizes bot performance."""
    
    def __init__(self, config_path: str = 'config.json', broker_html_file: str = 'TradingHistoryFromBroker.html'):
        """
        Initialize performance optimizer.
        
        Args:
            config_path: Path to config.json
            broker_html_file: Path to broker HTML report
        """
        self.config_path = config_path
        self.broker_html_file = broker_html_file
        
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Initialize components
        if os.path.exists(broker_html_file):
            self.comparator = BotBrokerComparator(broker_html_file)
            self.has_broker_report = True
        else:
            self.comparator = None
            self.has_broker_report = False
            self.logger.warning(f"Broker report not found: {broker_html_file}")
        
        # Performance tracking
        self.performance_metrics = {
            'scan_count': 0,
            'skipped_by_market_close': 0,
            'skipped_by_volume': 0,
            'skipped_by_other': 0,
            'trades_executed': 0,
            'hft_trades': 0,
            'hft_in_sweet_spot': 0,
            'total_profit': 0.0,
            'hft_profit': 0.0,
            'symbol_stats': defaultdict(lambda: {
                'scanned': 0,
                'skipped': 0,
                'traded': 0,
                'profit': 0.0,
                'hft_count': 0
            })
        }
        
        # Optimization suggestions
        self.suggestions = []
        
        # Setup logging
        os.makedirs('logs/system', exist_ok=True)
        self.logger = logging.getLogger('performance_optimizer')
        handler = logging.FileHandler('logs/system/optimization.log')
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
    
    def analyze_daily_performance(self) -> Dict[str, Any]:
        """
        Analyze bot performance for the current day.
        
        Returns:
            Dictionary with performance metrics and suggestions
        """
        print("=" * 80)
        print("BOT PERFORMANCE ANALYSIS & OPTIMIZATION")
        print("=" * 80)
        print()
        
        analysis = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'filters': {},
            'trades': {},
            'hft_performance': {},
            'broker_alignment': {},
            'suggestions': []
        }
        
        # Step 1: Analyze filter performance
        print("Step 1: Analyzing filter performance...")
        filter_analysis = self._analyze_filter_performance()
        analysis['filters'] = filter_analysis
        print(f"  Market closing skips: {filter_analysis['market_closing_skips']}")
        print(f"  Volume skips: {filter_analysis['volume_skips']}")
        print(f"  Other skips: {filter_analysis['other_skips']}")
        print()
        
        # Step 2: Analyze trade execution
        print("Step 2: Analyzing trade execution...")
        trade_analysis = self._analyze_trade_execution()
        analysis['trades'] = trade_analysis
        print(f"  Total trades: {trade_analysis['total_trades']}")
        print(f"  Closed trades: {trade_analysis['closed_trades']}")
        print(f"  Open trades: {trade_analysis['open_trades']}")
        print(f"  Total profit: ${trade_analysis['total_profit']:.2f}")
        print()
        
        # Step 3: Analyze Micro-HFT performance
        print("Step 3: Analyzing Micro-HFT performance...")
        hft_analysis = self._analyze_hft_performance()
        analysis['hft_performance'] = hft_analysis
        print(f"  HFT trades: {hft_analysis['total_hft_trades']}")
        print(f"  Sweet spot rate: {hft_analysis['sweet_spot_rate']:.1f}%")
        print(f"  Average HFT profit: ${hft_analysis['avg_profit']:.2f}")
        print()
        
        # Step 4: Verify broker alignment
        print("Step 4: Verifying broker alignment...")
        alignment_analysis = self._verify_broker_alignment()
        analysis['broker_alignment'] = alignment_analysis
        print(f"  Matched trades: {alignment_analysis['matched_trades']}")
        print(f"  Status mismatches: {alignment_analysis['status_mismatches']}")
        print(f"  Profit discrepancies: {alignment_analysis['profit_discrepancies']}")
        print()
        
        # Step 5: Generate optimization suggestions
        print("Step 5: Generating optimization suggestions...")
        suggestions = self._generate_suggestions(filter_analysis, trade_analysis, hft_analysis, alignment_analysis)
        analysis['suggestions'] = suggestions
        print(f"  Generated {len(suggestions)} suggestions")
        print()
        
        return analysis
    
    def _analyze_filter_performance(self) -> Dict[str, Any]:
        """Analyze filter skip rates and patterns."""
        skipped_log = 'logs/system/skipped_pairs.log'
        
        analysis = {
            'market_closing_skips': 0,
            'volume_skips': 0,
            'other_skips': 0,
            'skip_rate_by_symbol': defaultdict(int),
            'recent_skips': []
        }
        
        if not os.path.exists(skipped_log):
            return analysis
        
        # Analyze last 24 hours
        cutoff_time = datetime.now() - timedelta(hours=24)
        
        with open(skipped_log, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    # Parse log line: TIMESTAMP - LEVEL - MESSAGE
                    parts = line.split(' - ', 2)
                    if len(parts) >= 3:
                        timestamp_str = parts[0]
                        message = parts[2]
                        
                        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                        if timestamp < cutoff_time:
                            continue
                        
                        # Extract symbol and reason
                        if 'MARKET_CLOSING' in message:
                            analysis['market_closing_skips'] += 1
                            symbol = message.split(':')[0] if ':' in message else 'UNKNOWN'
                            analysis['skip_rate_by_symbol'][symbol] += 1
                        elif 'LOW_VOLUME' in message:
                            analysis['volume_skips'] += 1
                            symbol = message.split(':')[0] if ':' in message else 'UNKNOWN'
                            analysis['skip_rate_by_symbol'][symbol] += 1
                        else:
                            analysis['other_skips'] += 1
                        
                        # Store recent skip
                        if len(analysis['recent_skips']) < 50:
                            analysis['recent_skips'].append({
                                'timestamp': timestamp_str,
                                'message': message
                            })
                except Exception as e:
                    continue
        
        # Calculate skip rates
        total_skips = (analysis['market_closing_skips'] + 
                      analysis['volume_skips'] + 
                      analysis['other_skips'])
        analysis['total_skips'] = total_skips
        analysis['skip_rate'] = (total_skips / max(100, total_skips)) * 100  # Rough estimate
        
        return analysis
    
    def _analyze_trade_execution(self) -> Dict[str, Any]:
        """Analyze trade execution and profits."""
        trades_dir = Path('logs/trades')
        
        analysis = {
            'total_trades': 0,
            'closed_trades': 0,
            'open_trades': 0,
            'total_profit': 0.0,
            'wins': 0,
            'losses': 0,
            'symbol_profits': defaultdict(float),
            'trades_by_symbol': defaultdict(int)
        }
        
        if not trades_dir.exists():
            return analysis
        
        # Analyze last 24 hours
        cutoff_time = datetime.now() - timedelta(hours=24)
        
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
                                timestamp_str = trade.get('timestamp', '')
                                
                                if timestamp_str:
                                    trade_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                                    if trade_time < cutoff_time:
                                        continue
                                
                                analysis['total_trades'] += 1
                                symbol = trade.get('symbol', 'UNKNOWN')
                                analysis['trades_by_symbol'][symbol] += 1
                                
                                status = trade.get('status', 'OPEN')
                                if status == 'CLOSED':
                                    analysis['closed_trades'] += 1
                                    profit = trade.get('profit_usd', 0)
                                    if profit:
                                        analysis['total_profit'] += profit
                                        analysis['symbol_profits'][symbol] += profit
                                        if profit > 0:
                                            analysis['wins'] += 1
                                        else:
                                            analysis['losses'] += 1
                                else:
                                    analysis['open_trades'] += 1
                            except:
                                pass
            except:
                pass
        
        # Calculate win rate
        total_closed = analysis['closed_trades']
        analysis['win_rate'] = (analysis['wins'] / total_closed * 100) if total_closed > 0 else 0
        analysis['avg_profit'] = (analysis['total_profit'] / total_closed) if total_closed > 0 else 0
        
        return analysis
    
    def _analyze_hft_performance(self) -> Dict[str, Any]:
        """Analyze Micro-HFT trade performance."""
        trades_dir = Path('logs/trades')
        
        analysis = {
            'total_hft_trades': 0,
            'sweet_spot_count': 0,
            'below_min': 0,
            'above_max': 0,
            'profits': [],
            'avg_profit': 0.0,
            'min_profit': 0.0,
            'max_profit': 0.0,
            'sweet_spot_rate': 0.0
        }
        
        if not trades_dir.exists():
            return analysis
        
        # Analyze last 24 hours
        cutoff_time = datetime.now() - timedelta(hours=24)
        
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
                                timestamp_str = trade.get('timestamp', '')
                                
                                if timestamp_str:
                                    trade_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                                    if trade_time < cutoff_time:
                                        continue
                                
                                # Check if Micro-HFT trade
                                additional_info = trade.get('additional_info', {})
                                if (additional_info.get('close_type') == 'micro_hft' or
                                    'Micro-HFT' in str(additional_info.get('close_reason', ''))):
                                    
                                    analysis['total_hft_trades'] += 1
                                    profit = trade.get('profit_usd', 0)
                                    
                                    if profit:
                                        analysis['profits'].append(profit)
                                        
                                        if 0.03 <= profit <= 0.10:
                                            analysis['sweet_spot_count'] += 1
                                        elif profit < 0.03:
                                            analysis['below_min'] += 1
                                        else:
                                            analysis['above_max'] += 1
                            except:
                                pass
            except:
                pass
        
        # Calculate statistics
        if analysis['profits']:
            analysis['avg_profit'] = sum(analysis['profits']) / len(analysis['profits'])
            analysis['min_profit'] = min(analysis['profits'])
            analysis['max_profit'] = max(analysis['profits'])
        
        if analysis['total_hft_trades'] > 0:
            analysis['sweet_spot_rate'] = (analysis['sweet_spot_count'] / analysis['total_hft_trades']) * 100
        
        return analysis
    
    def _verify_broker_alignment(self) -> Dict[str, Any]:
        """Verify alignment with broker report."""
        if not self.has_broker_report or not self.comparator:
            return {
                'matched_trades': 0,
                'status_mismatches': 0,
                'profit_discrepancies': 0,
                'alignment_rate': 0.0,
                'note': 'Broker report not available'
            }
        
        try:
            # Parse broker and bot trades
            broker_trades = self.comparator.parse_broker_html()
            bot_trades, bot_start_time, bot_end_time = self.comparator.parse_bot_logs()
            
            if not broker_trades or not bot_trades:
                return {
                    'matched_trades': 0,
                    'status_mismatches': 0,
                    'profit_discrepancies': 0,
                    'alignment_rate': 0.0
                }
            
            # Filter broker trades
            broker_trades_filtered = self.comparator.filter_broker_trades_by_time(
                broker_trades, bot_start_time, bot_end_time
            )
            
            # Match trades
            matches = self.comparator.match_trades(broker_trades_filtered, bot_trades)
            
            # Count mismatches
            status_mismatches = sum(1 for m in matches['matched'] 
                                   if m['broker']['status'] != m['bot']['status'])
            
            profit_discrepancies = 0
            for match in matches['matched']:
                broker_profit = match['broker'].get('profit_usd')
                bot_profit = match['bot'].get('profit_usd')
                if broker_profit is not None and bot_profit is not None:
                    if abs(broker_profit - bot_profit) > 0.01:
                        profit_discrepancies += 1
            
            alignment_rate = (len(matches['matched']) / len(broker_trades_filtered) * 100) if broker_trades_filtered else 0
            
            return {
                'matched_trades': len(matches['matched']),
                'total_broker_trades': len(broker_trades_filtered),
                'total_bot_trades': len(bot_trades),
                'status_mismatches': status_mismatches,
                'profit_discrepancies': profit_discrepancies,
                'alignment_rate': alignment_rate
            }
        except Exception as e:
            self.logger.error(f"Error verifying broker alignment: {e}")
            return {
                'matched_trades': 0,
                'status_mismatches': 0,
                'profit_discrepancies': 0,
                'alignment_rate': 0.0,
                'error': str(e)
            }
    
    def _generate_suggestions(self, filter_analysis: Dict, trade_analysis: Dict, 
                             hft_analysis: Dict, alignment_analysis: Dict) -> List[Dict[str, Any]]:
        """Generate optimization suggestions based on analysis."""
        suggestions = []
        
        # Check filter skip rates
        total_skips = filter_analysis.get('total_skips', 0)
        market_close_skips = filter_analysis.get('market_closing_skips', 0)
        volume_skips = filter_analysis.get('volume_skips', 0)
        
        # Suggestion 1: Volume filter too aggressive
        if volume_skips > market_close_skips * 2 and volume_skips > 50:
            suggestions.append({
                'type': 'FILTER_ADJUSTMENT',
                'priority': 'MEDIUM',
                'filter': 'volume',
                'current_value': self.config.get('filters', {}).get('volume', {}).get('min_tick_volume', 10),
                'suggested_value': max(5, int(self.config.get('filters', {}).get('volume', {}).get('min_tick_volume', 10) * 0.8)),
                'reason': f'Volume filter skipping too many symbols ({volume_skips} vs {market_close_skips} market closing skips)',
                'action': 'Reduce min_tick_volume by 20%'
            })
        
        # Suggestion 2: Market closing filter too aggressive
        if market_close_skips > volume_skips * 3 and market_close_skips > 100:
            suggestions.append({
                'type': 'FILTER_ADJUSTMENT',
                'priority': 'LOW',
                'filter': 'market_closing',
                'current_value': self.config.get('filters', {}).get('market_closing', {}).get('minutes_before_close', 30),
                'suggested_value': max(15, int(self.config.get('filters', {}).get('market_closing', {}).get('minutes_before_close', 30) * 0.75)),
                'reason': f'Market closing filter skipping many symbols ({market_close_skips})',
                'action': 'Reduce minutes_before_close to allow more trading time'
            })
        
        # Suggestion 3: HFT sweet spot rate below target
        sweet_spot_rate = hft_analysis.get('sweet_spot_rate', 0)
        if sweet_spot_rate < 70 and hft_analysis.get('total_hft_trades', 0) > 10:
            micro_config = self.config.get('risk', {}).get('micro_profit_engine', {})
            suggestions.append({
                'type': 'HFT_OPTIMIZATION',
                'priority': 'HIGH',
                'component': 'micro_profit_engine',
                'current_values': {
                    'min_profit_threshold_usd': micro_config.get('min_profit_threshold_usd', 0.03),
                    'max_profit_threshold_usd': micro_config.get('max_profit_threshold_usd', 0.10),
                    'max_retries': micro_config.get('max_retries', 5),
                    'retry_delay_ms': micro_config.get('retry_delay_ms', 5)
                },
                'suggested_values': {
                    'min_profit_threshold_usd': 0.025,  # Slightly lower
                    'max_profit_threshold_usd': 0.12,   # Slightly higher
                    'max_retries': 7,                    # More retries
                    'retry_delay_ms': 3                  # Faster retries
                },
                'reason': f'HFT sweet spot rate {sweet_spot_rate:.1f}% below target (70%)',
                'action': 'Widen profit range and increase retries'
            })
        
        # Suggestion 4: Low trade execution
        if trade_analysis.get('total_trades', 0) < 10 and total_skips > 100:
            suggestions.append({
                'type': 'FILTER_RELAXATION',
                'priority': 'HIGH',
                'reason': f'Only {trade_analysis.get("total_trades", 0)} trades executed with {total_skips} skips - filters may be too strict',
                'action': 'Consider relaxing filter thresholds to increase trading opportunities'
            })
        
        # Suggestion 5: High profit symbols
        symbol_profits = trade_analysis.get('symbol_profits', {})
        if symbol_profits:
            top_symbols = sorted(symbol_profits.items(), key=lambda x: x[1], reverse=True)[:5]
            if top_symbols and top_symbols[0][1] > 5.0:
                suggestions.append({
                    'type': 'SYMBOL_PRIORITIZATION',
                    'priority': 'MEDIUM',
                    'symbols': [s[0] for s in top_symbols],
                    'profits': [s[1] for s in top_symbols],
                    'reason': 'These symbols show strong profitability',
                    'action': 'Consider prioritizing these symbols in scanning'
                })
        
        # Suggestion 6: Broker alignment issues
        if alignment_analysis.get('status_mismatches', 0) > 5:
            suggestions.append({
                'type': 'ALIGNMENT_ISSUE',
                'priority': 'HIGH',
                'issue': 'Status mismatches detected',
                'count': alignment_analysis.get('status_mismatches', 0),
                'reason': 'Bot logs show OPEN but broker shows CLOSED',
                'action': 'Run reconciliation script and check position monitoring'
            })
        
        return suggestions
    
    def generate_daily_report(self, analysis: Dict[str, Any]) -> str:
        """Generate comprehensive daily optimization report."""
        os.makedirs('logs/reports', exist_ok=True)
        date_str = analysis['date']
        report_file = f'logs/reports/optimization_{date_str}.txt'
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("BOT PERFORMANCE OPTIMIZATION REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {analysis['timestamp']}\n")
            f.write(f"Date: {date_str}\n")
            f.write("\n")
            
            # Filter Performance
            filters = analysis['filters']
            f.write("FILTER PERFORMANCE\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total Skips (24h): {filters.get('total_skips', 0)}\n")
            f.write(f"  - Market Closing: {filters.get('market_closing_skips', 0)}\n")
            f.write(f"  - Low Volume: {filters.get('volume_skips', 0)}\n")
            f.write(f"  - Other: {filters.get('other_skips', 0)}\n")
            f.write("\n")
            
            # Trade Execution
            trades = analysis['trades']
            f.write("TRADE EXECUTION\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total Trades (24h): {trades.get('total_trades', 0)}\n")
            f.write(f"  - Closed: {trades.get('closed_trades', 0)}\n")
            f.write(f"  - Open: {trades.get('open_trades', 0)}\n")
            f.write(f"  - Wins: {trades.get('wins', 0)}\n")
            f.write(f"  - Losses: {trades.get('losses', 0)}\n")
            f.write(f"  - Win Rate: {trades.get('win_rate', 0):.1f}%\n")
            f.write(f"Total Profit: ${trades.get('total_profit', 0):.2f}\n")
            f.write(f"Average Profit: ${trades.get('avg_profit', 0):.2f}\n")
            f.write("\n")
            
            # Micro-HFT Performance
            hft = analysis['hft_performance']
            f.write("MICRO-HFT PERFORMANCE\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total HFT Trades: {hft.get('total_hft_trades', 0)}\n")
            f.write(f"  - In Sweet Spot ($0.03-$0.10): {hft.get('sweet_spot_count', 0)}\n")
            f.write(f"  - Below $0.03: {hft.get('below_min', 0)}\n")
            f.write(f"  - Above $0.10: {hft.get('above_max', 0)}\n")
            f.write(f"Sweet Spot Rate: {hft.get('sweet_spot_rate', 0):.1f}%\n")
            f.write(f"Average HFT Profit: ${hft.get('avg_profit', 0):.2f}\n")
            f.write(f"Min/Max HFT Profit: ${hft.get('min_profit', 0):.2f} / ${hft.get('max_profit', 0):.2f}\n")
            f.write("\n")
            
            # Broker Alignment
            alignment = analysis['broker_alignment']
            f.write("BROKER ALIGNMENT\n")
            f.write("-" * 80 + "\n")
            f.write(f"Matched Trades: {alignment.get('matched_trades', 0)}\n")
            f.write(f"Status Mismatches: {alignment.get('status_mismatches', 0)}\n")
            f.write(f"Profit Discrepancies: {alignment.get('profit_discrepancies', 0)}\n")
            f.write(f"Alignment Rate: {alignment.get('alignment_rate', 0):.1f}%\n")
            f.write("\n")
            
            # Optimization Suggestions
            suggestions = analysis['suggestions']
            f.write("OPTIMIZATION SUGGESTIONS\n")
            f.write("-" * 80 + "\n")
            if suggestions:
                for i, suggestion in enumerate(suggestions, 1):
                    f.write(f"\n{i}. [{suggestion.get('priority', 'MEDIUM')}] {suggestion.get('type', 'SUGGESTION')}\n")
                    f.write(f"   Reason: {suggestion.get('reason', 'N/A')}\n")
                    f.write(f"   Action: {suggestion.get('action', 'N/A')}\n")
                    if 'current_value' in suggestion:
                        f.write(f"   Current: {suggestion['current_value']} → Suggested: {suggestion.get('suggested_value', 'N/A')}\n")
            else:
                f.write("No optimization suggestions at this time.\n")
            f.write("\n")
        
        return report_file
    
    def apply_suggestion(self, suggestion: Dict[str, Any], create_backup: bool = True) -> bool:
        """
        Apply an optimization suggestion (safely).
        
        Args:
            suggestion: Suggestion dictionary
            create_backup: Create backup before modifying config
        
        Returns:
            True if successfully applied
        """
        if create_backup:
            backup_path = f"{self.config_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(self.config_path, backup_path)
            self.logger.info(f"Created config backup: {backup_path}")
        
        try:
            suggestion_type = suggestion.get('type')
            
            if suggestion_type == 'FILTER_ADJUSTMENT':
                filter_name = suggestion.get('filter')
                suggested_value = suggestion.get('suggested_value')
                
                if 'filters' not in self.config:
                    self.config['filters'] = {}
                if filter_name not in self.config['filters']:
                    self.config['filters'][filter_name] = {}
                
                # Update appropriate field
                if filter_name == 'volume':
                    self.config['filters']['volume']['min_tick_volume'] = suggested_value
                    self.logger.info(f"Updated volume filter: min_tick_volume = {suggested_value}")
                elif filter_name == 'market_closing':
                    self.config['filters']['market_closing']['minutes_before_close'] = suggested_value
                    self.logger.info(f"Updated market closing filter: minutes_before_close = {suggested_value}")
                
                # Save config
                with open(self.config_path, 'w') as f:
                    json.dump(self.config, f, indent=2)
                
                return True
            
            elif suggestion_type == 'HFT_OPTIMIZATION':
                suggested_values = suggestion.get('suggested_values', {})
                
                if 'risk' not in self.config:
                    self.config['risk'] = {}
                if 'micro_profit_engine' not in self.config['risk']:
                    self.config['risk']['micro_profit_engine'] = {}
                
                # Update HFT settings
                for key, value in suggested_values.items():
                    self.config['risk']['micro_profit_engine'][key] = value
                    self.logger.info(f"Updated HFT {key} = {value}")
                
                # Save config
                with open(self.config_path, 'w') as f:
                    json.dump(self.config, f, indent=2)
                
                return True
            
            else:
                self.logger.warning(f"Cannot auto-apply suggestion type: {suggestion_type}")
                return False
        
        except Exception as e:
            self.logger.error(f"Error applying suggestion: {e}")
            return False
    
    def run_full_analysis(self) -> Dict[str, Any]:
        """Run complete analysis and generate report."""
        analysis = self.analyze_daily_performance()
        report_file = self.generate_daily_report(analysis)
        
        print("=" * 80)
        print("ANALYSIS COMPLETE")
        print("=" * 80)
        print(f"Report saved: {report_file}")
        print(f"Suggestions generated: {len(analysis['suggestions'])}")
        print()
        
        return analysis
    
    def run_continuous_monitoring(self, interval_minutes: int = 60):
        """
        Run continuous monitoring (for background service).
        
        Args:
            interval_minutes: Minutes between analysis runs
        """
        import time
        
        print(f"Starting continuous monitoring (interval: {interval_minutes} minutes)")
        print("Press Ctrl+C to stop")
        
        try:
            while True:
                analysis = self.run_full_analysis()
                
                # Log key metrics
                self.logger.info(f"Analysis complete - HFT Rate: {analysis['hft_performance'].get('sweet_spot_rate', 0):.1f}%, "
                               f"Profit: ${analysis['trades'].get('total_profit', 0):.2f}, "
                               f"Suggestions: {len(analysis['suggestions'])}")
                
                # Wait for next interval
                time.sleep(interval_minutes * 60)
        
        except KeyboardInterrupt:
            print("\nMonitoring stopped")


def main():
    """Main entry point."""
    import sys
    
    optimizer = BotPerformanceOptimizer()
    
    if len(sys.argv) > 1 and sys.argv[1] == '--continuous':
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        optimizer.run_continuous_monitoring(interval)
    else:
        optimizer.run_full_analysis()


if __name__ == "__main__":
    main()

