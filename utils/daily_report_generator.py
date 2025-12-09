"""
Daily Summary Report Generator
Generates daily summary reports in JSON and TXT formats.
"""

import json
import os
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from collections import defaultdict
import glob

from utils.logger_factory import get_logger

# Logger for report generation
_report_logger = get_logger("report_generator", "logs/system/report_generator.log")


def parse_trade_logs(symbol: str, target_date: date) -> List[Dict[str, Any]]:
    """
    Parse trade logs for a symbol on a specific date.
    
    Args:
        symbol: Trading symbol
        target_date: Target date
    
    Returns:
        List of parsed trade events
    """
    log_file = f"logs/trades/{symbol}.log"
    if not os.path.exists(log_file):
        return []
    
    trades = []
    current_trade = None
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                # Check if line is from target date
                if target_date.strftime('%Y-%m-%d') not in line:
                    continue
                
                # Parse trade execution
                if 'TRADE EXECUTED' in line or '✅ TRADE EXECUTED' in line:
                    if current_trade:
                        trades.append(current_trade)
                    current_trade = {
                        'symbol': symbol,
                        'type': 'execution',
                        'ticket': None,
                        'signal': None,
                        'entry_price': None,
                        'lot_size': None,
                        'profit': None,
                        'close_reason': None,
                        'timestamp': None
                    }
                    # Extract ticket if available
                    if 'Ticket:' in line:
                        try:
                            ticket_part = line.split('Ticket:')[1].split('|')[0].strip()
                            current_trade['ticket'] = int(ticket_part)
                        except:
                            pass
                
                # Parse position closure
                elif 'POSITION CLOSED' in line or '🔴 POSITION CLOSED' in line:
                    if current_trade:
                        # Extract profit
                        if 'Profit:' in line or 'Profit/Loss:' in line:
                            try:
                                profit_part = line.split('Profit')[1].split('|')[0].replace('$', '').replace('USD', '').strip()
                                current_trade['profit'] = float(profit_part)
                            except:
                                pass
                        # Extract close reason
                        if 'Reason:' in line:
                            try:
                                reason_part = line.split('Reason:')[1].strip()
                                current_trade['close_reason'] = reason_part
                            except:
                                pass
                        trades.append(current_trade)
                        current_trade = None
                
                # Parse micro-HFT close
                elif 'MICRO-HFT' in line or '⚡ MICRO-HFT' in line:
                    if current_trade:
                        current_trade['close_reason'] = 'Micro-HFT'
                        if 'Profit:' in line:
                            try:
                                profit_part = line.split('Profit:')[1].split('|')[0].replace('$', '').strip()
                                current_trade['profit'] = float(profit_part)
                            except:
                                pass
                        trades.append(current_trade)
                        current_trade = None
    except Exception as e:
        _report_logger.error(f"Error parsing {log_file}: {e}")
    
    if current_trade:
        trades.append(current_trade)
    
    return trades


def generate_daily_summary(target_date: Optional[date] = None) -> Dict[str, Any]:
    """
    Generate daily summary report for a specific date.
    
    Args:
        target_date: Target date (default: today)
    
    Returns:
        Dictionary containing summary statistics
    """
    if target_date is None:
        target_date = date.today()
    
    # Get all symbol log files
    trade_log_dir = "logs/trades"
    if not os.path.exists(trade_log_dir):
        os.makedirs(trade_log_dir, exist_ok=True)
    
    symbol_logs = glob.glob(os.path.join(trade_log_dir, "*.log"))
    
    # Parse all trades
    all_trades = []
    symbol_stats = defaultdict(lambda: {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'total_profit': 0.0,
        'wins_profit': 0.0,
        'losses_profit': 0.0,
        'micro_hft_closes': 0
    })
    
    for log_file in symbol_logs:
        symbol = os.path.basename(log_file).replace('.log', '')
        trades = parse_trade_logs(symbol, target_date)
        all_trades.extend(trades)
        
        for trade in trades:
            symbol_stats[symbol]['total_trades'] += 1
            if trade.get('profit') is not None:
                profit = trade['profit']
                symbol_stats[symbol]['total_profit'] += profit
                if profit > 0:
                    symbol_stats[symbol]['wins'] += 1
                    symbol_stats[symbol]['wins_profit'] += profit
                else:
                    symbol_stats[symbol]['losses'] += 1
                    symbol_stats[symbol]['losses_profit'] += profit
                
                if trade.get('close_reason') == 'Micro-HFT':
                    symbol_stats[symbol]['micro_hft_closes'] += 1
    
    # Calculate aggregate statistics
    trades_with_profit = [t for t in all_trades if t.get('profit') is not None]
    total_trades = len(trades_with_profit)
    wins = len([t for t in trades_with_profit if t.get('profit', 0) > 0])
    losses = len([t for t in trades_with_profit if t.get('profit', 0) < 0])
    
    total_profit = sum(t.get('profit', 0) for t in trades_with_profit)
    wins_profit = sum(t.get('profit', 0) for t in trades_with_profit if t.get('profit', 0) > 0)
    losses_profit = sum(t.get('profit', 0) for t in trades_with_profit if t.get('profit', 0) < 0)
    
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = (wins_profit / wins) if wins > 0 else 0.0
    avg_loss = (losses_profit / losses) if losses > 0 else 0.0
    
    # Micro-HFT statistics
    micro_hft_trades = [t for t in trades_with_profit if t.get('close_reason') == 'Micro-HFT']
    micro_hft_success = len([t for t in micro_hft_trades if t.get('profit', 0) > 0])
    micro_hft_success_rate = (micro_hft_success / len(micro_hft_trades) * 100) if micro_hft_trades else 0.0
    
    # Largest loss
    losses_list = [t.get('profit', 0) for t in trades_with_profit if t.get('profit', 0) < 0]
    largest_loss = min(losses_list) if losses_list else 0.0
    
    # Error/warning counts (from system logs)
    error_count = 0
    warning_count = 0
    
    system_error_log = "logs/system/system_errors.log"
    if os.path.exists(system_error_log):
        try:
            with open(system_error_log, 'r', encoding='utf-8') as f:
                for line in f:
                    if target_date.strftime('%Y-%m-%d') in line:
                        if 'ERROR' in line or 'CRITICAL' in line:
                            error_count += 1
                        elif 'WARNING' in line:
                            warning_count += 1
        except Exception as e:
            _report_logger.error(f"Error reading system_errors.log: {e}")
    
    # Build summary
    summary = {
        'date': target_date.strftime('%Y-%m-%d'),
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses,
        'win_rate_pct': round(win_rate, 2),
        'total_profit_usd': round(total_profit, 2),
        'avg_win_usd': round(avg_win, 2),
        'avg_loss_usd': round(avg_loss, 2),
        'largest_loss_usd': round(largest_loss, 2),
        'micro_hft': {
            'total_closes': len(micro_hft_trades),
            'successful_closes': micro_hft_success,
            'success_rate_pct': round(micro_hft_success_rate, 2)
        },
        'errors': error_count,
        'warnings': warning_count,
        'symbol_stats': {
            symbol: {
                'total_trades': stats['total_trades'],
                'wins': stats['wins'],
                'losses': stats['losses'],
                'win_rate_pct': round((stats['wins'] / stats['total_trades'] * 100) if stats['total_trades'] > 0 else 0, 2),
                'total_profit_usd': round(stats['total_profit'], 2),
                'avg_win_usd': round((stats['wins_profit'] / stats['wins']) if stats['wins'] > 0 else 0, 2),
                'avg_loss_usd': round((stats['losses_profit'] / stats['losses']) if stats['losses'] > 0 else 0, 2),
                'micro_hft_closes': stats['micro_hft_closes']
            }
            for symbol, stats in symbol_stats.items()
            if stats['total_trades'] > 0
        }
    }
    
    return summary


def save_daily_summary(target_date: Optional[date] = None) -> bool:
    """
    Generate and save daily summary report to files.
    
    Args:
        target_date: Target date (default: today)
    
    Returns:
        True if successful, False otherwise
    """
    if target_date is None:
        target_date = date.today()
    
    try:
        # Generate summary
        summary = generate_daily_summary(target_date)
        
        # Ensure reports directory exists
        reports_dir = "logs/reports"
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir, exist_ok=True)
        
        date_str = target_date.strftime('%Y-%m-%d')
        
        # Save JSON
        json_file = os.path.join(reports_dir, f"summary_{date_str}.json")
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        # Save TXT
        txt_file = os.path.join(reports_dir, f"summary_{date_str}.txt")
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"DAILY TRADING SUMMARY - {date_str}\n")
            f.write("=" * 80 + "\n\n")
            
            f.write("OVERALL STATISTICS\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total Trades: {summary['total_trades']}\n")
            f.write(f"Wins: {summary['wins']}\n")
            f.write(f"Losses: {summary['losses']}\n")
            f.write(f"Win Rate: {summary['win_rate_pct']}%\n")
            f.write(f"Total Profit: ${summary['total_profit_usd']:.2f}\n")
            f.write(f"Average Win: ${summary['avg_win_usd']:.2f}\n")
            f.write(f"Average Loss: ${summary['avg_loss_usd']:.2f}\n")
            f.write(f"Largest Loss: ${summary['largest_loss_usd']:.2f}\n\n")
            
            f.write("MICRO-HFT STATISTICS\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total Closes: {summary['micro_hft']['total_closes']}\n")
            f.write(f"Successful Closes: {summary['micro_hft']['successful_closes']}\n")
            f.write(f"Success Rate: {summary['micro_hft']['success_rate_pct']}%\n\n")
            
            f.write("SYSTEM HEALTH\n")
            f.write("-" * 80 + "\n")
            f.write(f"Errors: {summary['errors']}\n")
            f.write(f"Warnings: {summary['warnings']}\n\n")
            
            if summary['symbol_stats']:
                f.write("SYMBOL-WISE STATISTICS\n")
                f.write("-" * 80 + "\n")
                for symbol, stats in sorted(summary['symbol_stats'].items()):
                    f.write(f"\n{symbol}:\n")
                    f.write(f"  Total Trades: {stats['total_trades']}\n")
                    f.write(f"  Wins: {stats['wins']} | Losses: {stats['losses']}\n")
                    f.write(f"  Win Rate: {stats['win_rate_pct']}%\n")
                    f.write(f"  Total Profit: ${stats['total_profit_usd']:.2f}\n")
                    f.write(f"  Avg Win: ${stats['avg_win_usd']:.2f} | Avg Loss: ${stats['avg_loss_usd']:.2f}\n")
                    f.write(f"  Micro-HFT Closes: {stats['micro_hft_closes']}\n")
        
        _report_logger.info(f"Daily summary saved for {date_str}")
        return True
    
    except Exception as e:
        _report_logger.error(f"Error generating daily summary: {e}", exc_info=True)
        return False

