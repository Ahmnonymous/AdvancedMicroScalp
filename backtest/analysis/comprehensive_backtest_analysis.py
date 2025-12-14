#!/usr/bin/env python3
"""
Comprehensive Backtest Analysis Tool
Reads backtest logs and generates complete performance analysis report.
READ-ONLY: Does not modify trading logic or backtest engine.
"""

import json
import re
import os
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional
import statistics

class BacktestAnalyzer:
    """Analyzes completed backtest runs."""
    
    def __init__(self, logs_dir: str = "logs/backtest"):
        self.logs_dir = logs_dir
        self.trades_dir = os.path.join(logs_dir, "trades")
        self.execution_log = os.path.join(logs_dir, "execution.log")
        
        # Data structures
        self.trades: Dict[int, Dict[str, Any]] = {}  # ticket -> trade data
        self.closed_trades: List[Dict[str, Any]] = []
        self.symbol_trades: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        
    def parse_trade_logs(self):
        """Parse JSON trade logs to get entry information."""
        if not os.path.exists(self.trades_dir):
            print(f"Trade logs directory not found: {self.trades_dir}")
            return
        
        trade_files = [f for f in os.listdir(self.trades_dir) if f.endswith('.log')]
        
        for trade_file in trade_files:
            symbol = trade_file.replace('.log', '')
            filepath = os.path.join(self.trades_dir, trade_file)
            
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or not line.startswith('{'):
                        continue
                    
                    try:
                        trade_data = json.loads(line)
                        if trade_data.get('status') == 'OPEN':
                            ticket = int(trade_data.get('order_id', 0))
                            if ticket > 0:
                                self.trades[ticket] = {
                                    'ticket': ticket,
                                    'symbol': symbol,
                                    'direction': trade_data.get('trade_type', ''),
                                    'entry_price': trade_data.get('entry_price'),
                                    'entry_time': datetime.strptime(trade_data['timestamp'], '%Y-%m-%d %H:%M:%S'),
                                    'lot_size': trade_data.get('additional_info', {}).get('lot_size', 0.01),
                                    'stop_loss_pips': trade_data.get('additional_info', {}).get('stop_loss_pips', 0),
                                    'quality_score': trade_data.get('additional_info', {}).get('quality_score', 0),
                                    'spread_cost': trade_data.get('additional_info', {}).get('spread_fees_cost', 0),
                                    'risk_usd': trade_data.get('additional_info', {}).get('risk_usd', 2.0),
                                    'status': 'open',
                                    'close_time': None,
                                    'close_price': None,
                                    'close_reason': None,
                                    'profit_usd': None,
                                    'duration_seconds': None
                                }
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        continue
    
    def parse_execution_log(self):
        """Parse execution.log to get trade closure information."""
        if not os.path.exists(self.execution_log):
            print(f"Execution log not found: {self.execution_log}")
            return
        
        # Patterns for trade closure
        closure_pattern = re.compile(
            r'\[OK\] SIMULATED POSITION CLOSED: Ticket (\d+) \| (\w+) \| Profit: \$(-?\d+\.\d+) \| '
            r'Closure method: (\w+)\(\) \| SL hit: (True|False) \| TP hit: (True|False)'
        )
        sl_tp_pattern = re.compile(
            r'\[SL/TP HIT\].*?Ticket (\d+).*?Reason: (SL|TP).*?Profit: \$(-?\d+\.\d+)'
        )
        
        with open(self.execution_log, 'r', encoding='utf-8') as f:
            for line in f:
                # Try SL/TP hit first (more specific) - these take priority
                match = sl_tp_pattern.search(line)
                if match:
                    ticket = int(match.group(1))
                    reason = match.group(2)
                    profit = float(match.group(3))
                    # Extract symbol separately
                    symbol_match = re.search(r'Ticket \d+.*?\| (\w+)', line)
                    symbol = symbol_match.group(1) if symbol_match else 'UNKNOWN'
                    
                    # Extract timestamp
                    time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    close_time = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S') if time_match else None
                    
                    # Create trade entry if it doesn't exist (from execution log only)
                    if ticket not in self.trades:
                        self.trades[ticket] = {
                            'ticket': ticket,
                            'symbol': symbol,
                            'direction': 'UNKNOWN',
                            'entry_price': None,
                            'entry_time': close_time,  # Use close time as entry time if no entry log
                            'lot_size': 0.01,
                            'stop_loss_pips': 0,
                            'quality_score': 0,
                            'spread_cost': 0,
                            'risk_usd': 2.0,
                            'status': 'closed',
                            'close_time': close_time,
                            'close_price': None,
                            'close_reason': reason,
                            'profit_usd': profit,
                            'duration_seconds': None
                        }
                    else:
                        # Update existing trade
                        self.trades[ticket]['close_reason'] = reason
                        self.trades[ticket]['profit_usd'] = profit
                        self.trades[ticket]['status'] = 'closed'
                        self.trades[ticket]['close_time'] = close_time
                    continue
                
                # Try regular closure
                match = closure_pattern.search(line)
                if match:
                    ticket = int(match.group(1))
                    symbol = match.group(2)
                    profit = float(match.group(3))
                    close_method = match.group(4)
                    sl_hit = match.group(5) == 'True'
                    tp_hit = match.group(6) == 'True'
                    
                    # Create trade entry if it doesn't exist
                    if ticket not in self.trades:
                        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                        self.trades[ticket] = {
                            'ticket': ticket,
                            'symbol': symbol,
                            'direction': 'UNKNOWN',
                            'entry_price': None,
                            'entry_time': datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S') if time_match else None,
                            'lot_size': 0.01,
                            'stop_loss_pips': 0,
                            'quality_score': 0,
                            'spread_cost': 0,
                            'risk_usd': 2.0,
                            'status': 'closed',
                            'close_time': None,
                            'close_price': None,
                            'close_reason': None,
                            'profit_usd': profit,
                            'duration_seconds': None
                        }
                    
                    self.trades[ticket]['profit_usd'] = profit
                    self.trades[ticket]['status'] = 'closed'
                    if sl_hit:
                        self.trades[ticket]['close_reason'] = 'SL'
                    elif tp_hit:
                        self.trades[ticket]['close_reason'] = 'TP'
                    else:
                        self.trades[ticket]['close_reason'] = close_method
                    
                    # Extract timestamp
                    time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    if time_match:
                        self.trades[ticket]['close_time'] = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
                    
                    # Flag anomalous profits (likely data errors)
                    if abs(profit) > 10000:
                        self.trades[ticket]['anomalous'] = True
    
    def finalize_trades(self):
        """Calculate final metrics for all trades."""
        for ticket, trade in self.trades.items():
            if trade['status'] == 'closed':
                # Calculate duration if both times available
                if trade.get('close_time') and trade.get('entry_time'):
                    trade['duration_seconds'] = (trade['close_time'] - trade['entry_time']).total_seconds()
                elif trade.get('close_time'):
                    # If no entry time, use close time as both (for trades only in execution log)
                    trade['entry_time'] = trade['close_time']
                    trade['duration_seconds'] = 0
                
                self.closed_trades.append(trade)
                self.symbol_trades[trade['symbol']].append(trade)
    
    def calculate_symbol_performance(self) -> Dict[str, Dict[str, Any]]:
        """Calculate performance metrics per symbol."""
        symbol_perf = {}
        
        for symbol, trades in self.symbol_trades.items():
            if not trades:
                continue
            
            # Filter out anomalous trades
            closed_trades = [t for t in trades if t['status'] == 'closed' and t.get('profit_usd') is not None and not t.get('anomalous', False)]
            if not closed_trades:
                continue
            
            profits = [t['profit_usd'] for t in closed_trades]
            wins = [p for p in profits if p > 0]
            losses = [p for p in profits if p < 0]
            breakeven = [p for p in profits if p == 0]
            
            total_profit = sum(profits)
            win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0
            avg_profit = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            profit_factor = abs(avg_profit / avg_loss) if avg_loss != 0 else 0
            
            # SL hits
            sl_hits = sum(1 for t in closed_trades if t.get('close_reason') == 'SL')
            sl_hit_rate = (sl_hits / len(closed_trades) * 100) if closed_trades else 0
            
            # Average quality score
            avg_quality = statistics.mean([t.get('quality_score', 0) for t in closed_trades if t.get('quality_score')])
            
            # Average spread cost
            avg_spread = statistics.mean([t.get('spread_cost', 0) for t in closed_trades if t.get('spread_cost')])
            
            symbol_perf[symbol] = {
                'total_trades': len(closed_trades),
                'winning_trades': len(wins),
                'losing_trades': len(losses),
                'breakeven_trades': len(breakeven),
                'net_profit': total_profit,
                'win_rate': win_rate,
                'avg_profit': avg_profit,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor,
                'best_trade': max(profits) if profits else 0,
                'worst_trade': min(profits) if profits else 0,
                'sl_hits': sl_hits,
                'sl_hit_rate': sl_hit_rate,
                'avg_quality_score': avg_quality,
                'avg_spread_cost': avg_spread,
                'total_profit_sum': sum(wins) if wins else 0,
                'total_loss_sum': sum(losses) if losses else 0
            }
        
        return symbol_perf
    
    def calculate_strategy_metrics(self) -> Dict[str, Any]:
        """Calculate strategy behavior metrics."""
        # Filter out anomalous trades (data errors)
        valid_trades = [t for t in self.closed_trades if not t.get('anomalous', False)]
        if not valid_trades:
            return {}
        
        # SL effectiveness
        sl_hits = [t for t in valid_trades if t.get('close_reason') == 'SL']
        sl_hit_count = len(sl_hits)
        sl_hit_rate = (sl_hit_count / len(valid_trades) * 100) if valid_trades else 0
        
        # Losses close to -$2.00 (SL hit)
        losses = [t['profit_usd'] for t in valid_trades if t['profit_usd'] < 0]
        sl_size_losses = [l for l in losses if abs(l + 2.0) < 0.15]
        sl_effectiveness = (len(sl_size_losses) / len(losses) * 100) if losses else 0
        
        # Profit locking analysis (trades closed with profit without SL/TP)
        profit_closes = [t for t in valid_trades 
                        if t['profit_usd'] > 0 and t.get('close_reason') not in ['SL', 'TP']]
        profit_lock_rate = (len(profit_closes) / len([t for t in valid_trades if t['profit_usd'] > 0]) * 100) if [t for t in valid_trades if t['profit_usd'] > 0] else 0
        
        # Micro-profit analysis (small profits)
        micro_profits = [t for t in valid_trades if 0.01 <= t['profit_usd'] <= 0.50]
        micro_profit_contribution = sum(t['profit_usd'] for t in micro_profits)
        
        # Trailing stop behavior (trades with positive profit closed via close_position)
        trailing_stops = [t for t in valid_trades 
                         if t['profit_usd'] > 0 and t.get('close_reason') == 'close_position']
        
        # Count anomalous trades
        anomalous_trades = [t for t in self.closed_trades if t.get('anomalous', False)]
        
        return {
            'sl_hit_count': sl_hit_count,
            'sl_hit_rate': sl_hit_rate,
            'sl_effectiveness': sl_effectiveness,
            'profit_lock_rate': profit_lock_rate,
            'profit_lock_count': len(profit_closes),
            'micro_profit_count': len(micro_profits),
            'micro_profit_contribution': micro_profit_contribution,
            'trailing_stop_count': len(trailing_stops),
            'total_losses': len(losses),
            'expected_sl_losses': len(sl_size_losses),
            'anomalous_trades_count': len(anomalous_trades),
            'valid_trades_count': len(valid_trades)
        }
    
    def calculate_risk_metrics(self) -> Dict[str, Any]:
        """Calculate risk and drawdown metrics."""
        # Filter out anomalous trades
        valid_trades = [t for t in self.closed_trades if not t.get('anomalous', False)]
        if not valid_trades:
            return {}
        
        # Sort trades by close time
        sorted_trades = sorted(valid_trades, key=lambda t: t.get('close_time', datetime.min))
        
        # Calculate equity curve
        equity_curve = []
        running_equity = 10000.0  # Starting balance
        peak = running_equity
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        drawdown_start = None
        drawdown_end = None
        
        for trade in sorted_trades:
            if trade.get('profit_usd') is not None:
                running_equity += trade['profit_usd']
                equity_curve.append({
                    'time': trade.get('close_time'),
                    'equity': running_equity
                })
                
                if running_equity > peak:
                    peak = running_equity
                    drawdown_start = None
                    drawdown_end = None
                else:
                    drawdown = peak - running_equity
                    if drawdown > max_drawdown:
                        max_drawdown = drawdown
                        max_drawdown_pct = (drawdown / peak * 100) if peak > 0 else 0
                        if drawdown_start is None:
                            drawdown_start = trade.get('close_time')
                    drawdown_end = trade.get('close_time')
        
        # Loss streaks
        profits = [t['profit_usd'] for t in sorted_trades if t.get('profit_usd') is not None]
        current_streak = 0
        max_loss_streak = 0
        loss_streaks = []
        
        for profit in profits:
            if profit < 0:
                current_streak += 1
                max_loss_streak = max(max_loss_streak, current_streak)
            else:
                if current_streak > 0:
                    loss_streaks.append(current_streak)
                current_streak = 0
        
        avg_loss_streak = statistics.mean(loss_streaks) if loss_streaks else 0
        
        # Recovery analysis
        recovery_time = None
        if drawdown_start and drawdown_end:
            recovery_time = (drawdown_end - drawdown_start).total_seconds() / 3600  # hours
        
        return {
            'starting_balance': 10000.0,
            'final_equity': running_equity if equity_curve else 10000.0,
            'total_return': (running_equity - 10000.0) if equity_curve else 0.0,
            'total_return_pct': ((running_equity - 10000.0) / 10000.0 * 100) if equity_curve else 0.0,
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown_pct,
            'max_loss_streak': max_loss_streak,
            'avg_loss_streak': avg_loss_streak,
            'loss_streaks': loss_streaks,
            'recovery_time_hours': recovery_time
        }
    
    def analyze_time_performance(self) -> Dict[str, Any]:
        """Analyze performance by time of day."""
        # Filter out anomalous trades
        valid_trades = [t for t in self.closed_trades if not t.get('anomalous', False)]
        if not valid_trades:
            return {}
        
        hourly_performance = defaultdict(lambda: {'profits': [], 'trades': 0})
        
        for trade in valid_trades:
            if trade.get('close_time') and trade.get('profit_usd') is not None:
                hour = trade['close_time'].hour
                hourly_performance[hour]['profits'].append(trade['profit_usd'])
                hourly_performance[hour]['trades'] += 1
        
        hourly_stats = {}
        for hour, data in hourly_performance.items():
            profits = data['profits']
            hourly_stats[hour] = {
                'trades': data['trades'],
                'total_profit': sum(profits),
                'avg_profit': statistics.mean(profits) if profits else 0,
                'win_rate': (len([p for p in profits if p > 0]) / len(profits) * 100) if profits else 0
            }
        
        # Session analysis (GMT hours)
        sessions = {
            'Asian (22:00-06:00 GMT)': list(range(22, 24)) + list(range(0, 7)),
            'European (06:00-14:00 GMT)': list(range(6, 15)),
            'American (13:00-22:00 GMT)': list(range(13, 23))
        }
        
        session_stats = {}
        for session_name, hours in sessions.items():
            session_profits = []
            session_trades = 0
            for hour in hours:
                if hour in hourly_stats:
                    session_profits.extend([p for p in hourly_performance[hour]['profits']])
                    session_trades += hourly_stats[hour]['trades']
            
            if session_profits:
                session_stats[session_name] = {
                    'trades': session_trades,
                    'total_profit': sum(session_profits),
                    'avg_profit': statistics.mean(session_profits),
                    'win_rate': (len([p for p in session_profits if p > 0]) / len(session_profits) * 100)
                }
        
        return {
            'hourly': hourly_stats,
            'sessions': session_stats
        }
    
    def generate_report(self) -> str:
        """Generate comprehensive analysis report."""
        report_lines = []
        report_lines.append("=" * 100)
        report_lines.append("COMPREHENSIVE BACKTEST ANALYSIS REPORT")
        report_lines.append("=" * 100)
        report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("")
        
        # 1. EXECUTIVE SUMMARY
        report_lines.append("1. EXECUTIVE SUMMARY")
        report_lines.append("-" * 100)
        
        # Filter out anomalous trades for accurate analysis
        valid_closed_trades = [t for t in self.closed_trades if not t.get('anomalous', False)]
        anomalous_trades = [t for t in self.closed_trades if t.get('anomalous', False)]
        
        total_trades = len(valid_closed_trades)
        if total_trades == 0:
            report_lines.append("No valid closed trades found in backtest data.")
            if anomalous_trades:
                report_lines.append(f"Note: {len(anomalous_trades)} anomalous trades (data errors) excluded from analysis.")
            return "\n".join(report_lines)
        
        if anomalous_trades:
            report_lines.append(f"WARNING: {len(anomalous_trades)} anomalous trades excluded (likely data errors)")
            anomalous_profit = sum(t['profit_usd'] for t in anomalous_trades)
            report_lines.append(f"   Anomalous trade P&L: ${anomalous_profit:.2f} (excluded from metrics)")
            report_lines.append("")
        
        all_profits = [t['profit_usd'] for t in valid_closed_trades if t.get('profit_usd') is not None]
        wins = [p for p in all_profits if p > 0]
        losses = [p for p in all_profits if p < 0]
        net_profit = sum(all_profits)
        win_rate = (len(wins) / len(all_profits) * 100) if all_profits else 0
        
        risk_metrics = self.calculate_risk_metrics()
        
        report_lines.append(f"Total Trades: {total_trades}")
        report_lines.append(f"Winning Trades: {len(wins)} ({len(wins)/len(all_profits)*100:.1f}%)")
        report_lines.append(f"Losing Trades: {len(losses)} ({len(losses)/len(all_profits)*100:.1f}%)")
        report_lines.append(f"Net Profit: ${net_profit:.2f}")
        report_lines.append(f"Win Rate: {win_rate:.1f}%")
        report_lines.append(f"Starting Balance: ${risk_metrics.get('starting_balance', 10000):.2f}")
        report_lines.append(f"Final Equity: ${risk_metrics.get('final_equity', 10000):.2f}")
        report_lines.append(f"Total Return: ${risk_metrics.get('total_return', 0):.2f} ({risk_metrics.get('total_return_pct', 0):.2f}%)")
        report_lines.append(f"Max Drawdown: ${risk_metrics.get('max_drawdown', 0):.2f} ({risk_metrics.get('max_drawdown_pct', 0):.2f}%)")
        report_lines.append(f"Max Loss Streak: {risk_metrics.get('max_loss_streak', 0)}")
        report_lines.append("")
        
        # Profitability assessment
        if net_profit > 0 and win_rate > 50:
            profitability = "PROFITABLE"
        elif net_profit > 0:
            profitability = "SLIGHTLY PROFITABLE (Low Win Rate)"
        elif net_profit > -100:
            profitability = "SLIGHTLY LOSS-MAKING"
        else:
            profitability = "LOSS-MAKING"
        
        report_lines.append(f"Overall Assessment: {profitability}")
        report_lines.append("")
        
        # 2. SYMBOL PERFORMANCE
        report_lines.append("2. SYMBOL PERFORMANCE")
        report_lines.append("-" * 100)
        
        symbol_perf = self.calculate_symbol_performance()
        
        # Sort by net profit
        sorted_symbols = sorted(symbol_perf.items(), key=lambda x: x[1]['net_profit'], reverse=True)
        
        report_lines.append(f"{'Symbol':<12} {'Trades':<8} {'Win%':<8} {'Net P&L':<12} {'Avg Win':<10} {'Avg Loss':<10} {'PF':<6} {'SL%':<8}")
        report_lines.append("-" * 100)
        
        for symbol, perf in sorted_symbols:
            report_lines.append(
                f"{symbol:<12} {perf['total_trades']:<8} {perf['win_rate']:<7.1f}% "
                f"${perf['net_profit']:<11.2f} ${perf['avg_profit']:<9.2f} ${perf['avg_loss']:<9.2f} "
                f"{perf['profit_factor']:<5.2f} {perf['sl_hit_rate']:<7.1f}%"
            )
        
        report_lines.append("")
        report_lines.append("Best Performing Symbols:")
        for symbol, perf in sorted_symbols[:3]:
            if perf['net_profit'] > 0:
                report_lines.append(f"  {symbol}: ${perf['net_profit']:.2f} ({perf['total_trades']} trades, {perf['win_rate']:.1f}% win rate)")
        
        report_lines.append("")
        report_lines.append("Worst Performing Symbols:")
        for symbol, perf in sorted_symbols[-3:]:
            report_lines.append(f"  {symbol}: ${perf['net_profit']:.2f} ({perf['total_trades']} trades, {perf['win_rate']:.1f}% win rate)")
        report_lines.append("")
        
        # 3. STRATEGY BEHAVIOR
        report_lines.append("3. STRATEGY BEHAVIOR")
        report_lines.append("-" * 100)
        
        strategy_metrics = self.calculate_strategy_metrics()
        
        report_lines.append("Stop Loss Effectiveness:")
        report_lines.append(f"  SL Hits: {strategy_metrics.get('sl_hit_count', 0)} ({strategy_metrics.get('sl_hit_rate', 0):.1f}% of all trades)")
        report_lines.append(f"  Losses at SL Size (-$2.00 +/- $0.15): {strategy_metrics.get('expected_sl_losses', 0)} ({strategy_metrics.get('sl_effectiveness', 0):.1f}% of losses)")
        report_lines.append("")
        
        report_lines.append("Profit Locking Performance:")
        report_lines.append(f"  Trades Closed with Profit (non-SL/TP): {strategy_metrics.get('profit_lock_count', 0)} ({strategy_metrics.get('profit_lock_rate', 0):.1f}% of winners)")
        report_lines.append("")
        
        report_lines.append("Micro-Profit Contribution:")
        report_lines.append(f"  Micro-Profit Trades ($0.01-$0.50): {strategy_metrics.get('micro_profit_count', 0)}")
        report_lines.append(f"  Total Micro-Profit Contribution: ${strategy_metrics.get('micro_profit_contribution', 0):.2f}")
        report_lines.append("")
        
        report_lines.append("Trailing Stop Behavior:")
        report_lines.append(f"  Trades Closed via Trailing Stop: {strategy_metrics.get('trailing_stop_count', 0)}")
        report_lines.append("")
        
        # 4. RISK & DRAWDOWN
        report_lines.append("4. RISK & DRAWDOWN ANALYSIS")
        report_lines.append("-" * 100)
        
        report_lines.append(f"Maximum Drawdown: ${risk_metrics.get('max_drawdown', 0):.2f} ({risk_metrics.get('max_drawdown_pct', 0):.2f}%)")
        if risk_metrics.get('recovery_time_hours'):
            report_lines.append(f"Recovery Time: {risk_metrics.get('recovery_time_hours', 0):.1f} hours")
        report_lines.append(f"Maximum Loss Streak: {risk_metrics.get('max_loss_streak', 0)} consecutive losses")
        report_lines.append(f"Average Loss Streak: {risk_metrics.get('avg_loss_streak', 0):.1f} consecutive losses")
        report_lines.append("")
        
        # 5. FILTER EFFECTIVENESS (placeholder - would need filter logs)
        report_lines.append("5. FILTER EFFECTIVENESS")
        report_lines.append("-" * 100)
        report_lines.append("Filter rejection data not available in current log format.")
        report_lines.append("Note: Filter effectiveness analysis requires filter rejection logs.")
        report_lines.append("")
        
        # 6. TIME & MARKET CONDITIONS
        report_lines.append("6. TIME & MARKET CONDITIONS")
        report_lines.append("-" * 100)
        
        time_perf = self.analyze_time_performance()
        
        if time_perf.get('sessions'):
            report_lines.append("Trading Session Performance:")
            for session, stats in time_perf['sessions'].items():
                report_lines.append(f"  {session}:")
                report_lines.append(f"    Trades: {stats['trades']}")
                report_lines.append(f"    Net Profit: ${stats['total_profit']:.2f}")
                report_lines.append(f"    Avg Profit: ${stats['avg_profit']:.2f}")
                report_lines.append(f"    Win Rate: {stats['win_rate']:.1f}%")
                report_lines.append("")
            
            # Best and worst sessions
            best_session = max(time_perf['sessions'].items(), key=lambda x: x[1]['total_profit'])
            worst_session = min(time_perf['sessions'].items(), key=lambda x: x[1]['total_profit'])
            report_lines.append(f"Best Session: {best_session[0]} (${best_session[1]['total_profit']:.2f})")
            report_lines.append(f"Worst Session: {worst_session[0]} (${worst_session[1]['total_profit']:.2f})")
            report_lines.append("")
        
        # 7. ACTIONABLE RECOMMENDATIONS
        report_lines.append("7. ACTIONABLE RECOMMENDATIONS")
        report_lines.append("-" * 100)
        
        recommendations = []
        
        # Symbol recommendations
        worst_symbols = [s for s, p in sorted_symbols if p['net_profit'] < -10]
        if worst_symbols:
            recommendations.append(f"DISABLE/RESTRICT: {', '.join(worst_symbols[:3])} (consistently loss-making)")
        
        best_symbols = [s for s, p in sorted_symbols[:3] if p['net_profit'] > 0 and p['win_rate'] > 50]
        if best_symbols:
            recommendations.append(f"FOCUS ON: {', '.join(best_symbols)} (profitable with good win rate)")
        
        # SL recommendations
        if strategy_metrics.get('sl_effectiveness', 0) < 80:
            recommendations.append("TIGHTEN SL ENFORCEMENT: Many losses not hitting SL, consider stricter SL management")
        
        # Win rate recommendations
        if win_rate < 45:
            recommendations.append("IMPROVE ENTRY QUALITY: Low win rate suggests entry filters need tightening")
        elif win_rate > 60:
            recommendations.append("CONSIDER INCREASING POSITION SIZE: High win rate suggests strategy is working well")
        
        # Profit factor recommendations
        avg_profit_factor = statistics.mean([p['profit_factor'] for p in symbol_perf.values() if p['profit_factor'] > 0])
        if avg_profit_factor < 1.0:
            recommendations.append("IMPROVE RISK-REWARD: Profit factor below 1.0 indicates losses exceed wins on average")
        
        # Drawdown recommendations
        if risk_metrics.get('max_drawdown_pct', 0) > 20:
            recommendations.append("REDUCE RISK PER TRADE: High drawdown suggests excessive risk exposure")
        
        # Loss streak recommendations
        if risk_metrics.get('max_loss_streak', 0) > 10:
            recommendations.append("IMPLEMENT CIRCUIT BREAKER: Long loss streaks detected, consider pausing after N consecutive losses")
        
        if not recommendations:
            recommendations.append("No critical issues detected. Monitor live performance closely.")
        
        for i, rec in enumerate(recommendations, 1):
            report_lines.append(f"{i}. {rec}")
        
        report_lines.append("")
        report_lines.append("=" * 100)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 100)
        
        return "\n".join(report_lines)
    
    def analyze(self):
        """Run complete analysis."""
        print("Parsing trade logs...")
        self.parse_trade_logs()
        print(f"Found {len(self.trades)} trades")
        
        print("Parsing execution log...")
        self.parse_execution_log()
        print(f"Found {len([t for t in self.trades.values() if t['status'] == 'closed'])} closed trades")
        
        print("Finalizing trade data...")
        self.finalize_trades()
        
        print("Generating report...")
        report = self.generate_report()
        
        return report


def main():
    """Main entry point."""
    analyzer = BacktestAnalyzer()
    report = analyzer.analyze()
    
    print("\n" + report)
    
    # Save report
    output_file = "backtest/analysis/FULL_BACKTEST_REPORT.txt"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\nReport saved to: {output_file}")


if __name__ == '__main__':
    main()

