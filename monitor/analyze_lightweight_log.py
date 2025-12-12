"""
Analyze Lightweight Real-Time Logger Output
Generates a comprehensive summary from the log file.
"""

import re
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any


def analyze_lightweight_log(log_file_path: str) -> Dict[str, Any]:
    """
    Analyze the lightweight real-time log file and generate a summary.
    
    Args:
        log_file_path: Path to the log file
        
    Returns:
        Dictionary with analysis results
    """
    with open(log_file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Parse log entries
    entries = []
    current_entry = {}
    
    for line in lines:
        # Check if this is a new log entry (starts with timestamp)
        if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', line):
            if current_entry:
                entries.append(current_entry)
            current_entry = {'raw_lines': []}
            current_entry['raw_lines'].append(line.strip())
        elif current_entry:
            current_entry['raw_lines'].append(line.strip())
    
    # Add last entry
    if current_entry:
        entries.append(current_entry)
    
    # Analysis data
    analysis = {
        'total_entries': len(entries),
        'session_start': None,
        'session_end': None,
        'duration': None,
        'states': defaultdict(int),
        'symbols_scanned': set(),
        'trades_opened': [],
        'trades_closed': [],
        'open_positions': defaultdict(list),
        'events': {
            'sweet_spot_entries': [],
            'trailing_zone_entries': [],
            'sl_not_updating': [],
            'close_failures': [],
            'execution_errors': [],
            'slow_executions': []
        },
        'max_profit': 0.0,
        'min_profit': 0.0,
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'sl_update_issues': []
    }
    
    # Parse each entry
    for entry in entries:
        entry_text = '\n'.join(entry['raw_lines'])
        
        # Extract timestamp
        timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', entry_text)
        if timestamp_match:
            timestamp_str = timestamp_match.group(1)
            try:
                timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                if not analysis['session_start']:
                    analysis['session_start'] = timestamp
                analysis['session_end'] = timestamp
            except:
                pass
        
        # Extract state
        state_match = re.search(r'- State: (\w+)', entry_text)
        if state_match:
            state = state_match.group(1)
            analysis['states'][state] += 1
        
        # Extract symbol being scanned
        symbol_match = re.search(r'- Symbol: (\S+)', entry_text)
        if symbol_match:
            symbol = symbol_match.group(1)
            if symbol != 'N/A':
                analysis['symbols_scanned'].add(symbol)
        
        # Extract position information
        ticket_match = re.search(r'- Ticket: (\d+)', entry_text)
        if ticket_match:
            ticket = int(ticket_match.group(1))
            
            # Extract PnL
            pnl_match = re.search(r'PnL: \$(-?\d+\.\d+)', entry_text)
            if pnl_match:
                pnl = float(pnl_match.group(1))
                analysis['open_positions'][ticket].append({
                    'timestamp': timestamp if 'timestamp' in locals() else None,
                    'pnl': pnl,
                    'symbol': symbol if 'symbol' in locals() else 'N/A'
                })
                
                # Track max/min profit
                if pnl > analysis['max_profit']:
                    analysis['max_profit'] = pnl
                if pnl < analysis['min_profit']:
                    analysis['min_profit'] = pnl
        
        # Extract trade stats
        trades_match = re.search(r'Total Trades Today: (\d+)', entry_text)
        if trades_match:
            analysis['total_trades'] = max(analysis['total_trades'], int(trades_match.group(1)))
        
        wins_match = re.search(r'Wins: (\d+)', entry_text)
        if wins_match:
            analysis['wins'] = max(analysis['wins'], int(wins_match.group(1)))
        
        losses_match = re.search(r'Losses: (\d+)', entry_text)
        if losses_match:
            analysis['losses'] = max(analysis['losses'], int(losses_match.group(1)))
        
        # Extract events
        if '[+] Entered SWEET SPOT zone' in entry_text:
            # Find all sweet spot entries in this log entry
            sweet_spot_matches = re.findall(r'[+] Entered SWEET SPOT zone \(\$(\d+\.\d+) profit\)', entry_text)
            for profit_str in sweet_spot_matches:
                analysis['events']['sweet_spot_entries'].append({
                    'timestamp': timestamp if 'timestamp' in locals() else None,
                    'profit': float(profit_str)
                })
        
        if '🔵 Entered TRAILING ZONE' in entry_text:
            # Find all trailing zone entries in this log entry
            trailing_matches = re.findall(r'🔵 Entered TRAILING ZONE \(\$(\d+\.\d+) profit\)', entry_text)
            for profit_str in trailing_matches:
                analysis['events']['trailing_zone_entries'].append({
                    'timestamp': timestamp if 'timestamp' in locals() else None,
                    'profit': float(profit_str)
                })
        
        if '[WARNING] SL NOT UPDATED FOR >2s' in entry_text:
            sl_match = re.search(r'Ticket (\d+), ([\d.]+)s', entry_text)
            if sl_match:
                analysis['events']['sl_not_updating'].append({
                    'timestamp': timestamp if 'timestamp' in locals() else None,
                    'ticket': int(sl_match.group(1)),
                    'duration': float(sl_match.group(2))
                })
        
        if '[ERROR] Failed close attempt' in entry_text:
            analysis['events']['close_failures'].append({
                'timestamp': timestamp if 'timestamp' in locals() else None
            })
        
        if '[ERROR] Execution Error:' in entry_text:
            analysis['events']['execution_errors'].append({
                'timestamp': timestamp if 'timestamp' in locals() else None
            })
        
        if '🟠 Slow Execution:' in entry_text:
            analysis['events']['slow_executions'].append({
                'timestamp': timestamp if 'timestamp' in locals() else None
            })
    
    # Calculate duration
    if analysis['session_start'] and analysis['session_end']:
        analysis['duration'] = analysis['session_end'] - analysis['session_start']
    
    # Analyze SL update issues
    sl_issues_by_ticket = defaultdict(list)
    for sl_issue in analysis['events']['sl_not_updating']:
        sl_issues_by_ticket[sl_issue['ticket']].append(sl_issue['duration'])
    
    for ticket, durations in sl_issues_by_ticket.items():
        analysis['sl_update_issues'].append({
            'ticket': ticket,
            'count': len(durations),
            'max_duration': max(durations),
            'avg_duration': sum(durations) / len(durations)
        })
    
    return analysis


def generate_summary_report(analysis: Dict[str, Any]) -> str:
    """Generate a formatted summary report from analysis."""
    report = []
    report.append("=" * 80)
    report.append("LIGHTWEIGHT REAL-TIME LOGGER - ANALYSIS SUMMARY")
    report.append("=" * 80)
    report.append("")
    
    # Session Information
    report.append("📅 SESSION INFORMATION")
    report.append("-" * 80)
    if analysis['session_start']:
        report.append(f"Start Time: {analysis['session_start'].strftime('%Y-%m-%d %H:%M:%S')}")
    if analysis['session_end']:
        report.append(f"End Time: {analysis['session_end'].strftime('%Y-%m-%d %H:%M:%S')}")
    if analysis['duration']:
        duration_str = str(analysis['duration']).split('.')[0]
        report.append(f"Duration: {duration_str}")
    report.append(f"Total Log Entries: {analysis['total_entries']}")
    report.append("")
    
    # Bot States
    report.append("[BOT] BOT STATE DISTRIBUTION")
    report.append("-" * 80)
    for state, count in sorted(analysis['states'].items(), key=lambda x: x[1], reverse=True):
        percentage = (count / analysis['total_entries']) * 100 if analysis['total_entries'] > 0 else 0
        report.append(f"  {state}: {count} times ({percentage:.1f}%)")
    report.append("")
    
    # Symbols Scanned
    report.append("🔍 SYMBOLS SCANNED")
    report.append("-" * 80)
    if analysis['symbols_scanned']:
        report.append(f"Total Unique Symbols: {len(analysis['symbols_scanned'])}")
        report.append(f"Symbols: {', '.join(sorted(analysis['symbols_scanned']))}")
    else:
        report.append("No symbols scanned")
    report.append("")
    
    # Trade Statistics
    report.append("📊 TRADE STATISTICS")
    report.append("-" * 80)
    report.append(f"Total Trades Today: {analysis['total_trades']}")
    report.append(f"Wins: {analysis['wins']}")
    report.append(f"Losses: {analysis['losses']}")
    if analysis['total_trades'] > 0:
        win_rate = (analysis['wins'] / analysis['total_trades']) * 100
        report.append(f"Win Rate: {win_rate:.1f}%")
    report.append("")
    
    # Position Analysis
    report.append("💼 POSITION ANALYSIS")
    report.append("-" * 80)
    if analysis['open_positions']:
        for ticket, positions in analysis['open_positions'].items():
            if positions:
                first_pos = positions[0]
                last_pos = positions[-1]
                symbol = first_pos.get('symbol', 'N/A')
                initial_pnl = first_pos.get('pnl', 0.0)
                final_pnl = last_pos.get('pnl', 0.0)
                pnl_change = final_pnl - initial_pnl
                
                report.append(f"Ticket {ticket} ({symbol}):")
                report.append(f"  Initial PnL: ${initial_pnl:.2f}")
                report.append(f"  Final PnL: ${final_pnl:.2f}")
                report.append(f"  PnL Change: ${pnl_change:+.2f}")
                report.append(f"  Data Points: {len(positions)}")
    else:
        report.append("No positions tracked")
    report.append("")
    
    # Profit Analysis
    report.append("💰 PROFIT ANALYSIS")
    report.append("-" * 80)
    report.append(f"Maximum Profit: ${analysis['max_profit']:.2f}")
    report.append(f"Minimum Profit: ${analysis['min_profit']:.2f}")
    report.append(f"Profit Range: ${analysis['max_profit'] - analysis['min_profit']:.2f}")
    report.append("")
    
    # Event Summary
    report.append("🎯 EVENT SUMMARY")
    report.append("-" * 80)
    report.append(f"[+] Sweet Spot Entries: {len(analysis['events']['sweet_spot_entries'])}")
    if analysis['events']['sweet_spot_entries']:
        profits = [e['profit'] for e in analysis['events']['sweet_spot_entries']]
        report.append(f"   Profit Range: ${min(profits):.2f} - ${max(profits):.2f}")
    
    report.append(f"🔵 Trailing Zone Entries: {len(analysis['events']['trailing_zone_entries'])}")
    if analysis['events']['trailing_zone_entries']:
        profits = [e['profit'] for e in analysis['events']['trailing_zone_entries']]
        report.append(f"   Profit Range: ${min(profits):.2f} - ${max(profits):.2f}")
    
    report.append(f"[WARNING]  SL Not Updating Warnings: {len(analysis['events']['sl_not_updating'])}")
    if analysis['events']['sl_not_updating']:
        durations = [e['duration'] for e in analysis['events']['sl_not_updating']]
        report.append(f"   Duration Range: {min(durations):.1f}s - {max(durations):.1f}s")
        report.append(f"   Average Duration: {sum(durations) / len(durations):.1f}s")
    
    report.append(f"[ERROR] Close Failures: {len(analysis['events']['close_failures'])}")
    report.append(f"[ERROR] Execution Errors: {len(analysis['events']['execution_errors'])}")
    report.append(f"🟠 Slow Executions: {len(analysis['events']['slow_executions'])}")
    report.append("")
    
    # SL Update Issues Detail
    if analysis['sl_update_issues']:
        report.append("[WARNING]  SL UPDATE ISSUES DETAIL")
        report.append("-" * 80)
        for issue in analysis['sl_update_issues']:
            report.append(f"Ticket {issue['ticket']}:")
            report.append(f"  Warnings: {issue['count']}")
            report.append(f"  Max Duration: {issue['max_duration']:.1f}s")
            report.append(f"  Avg Duration: {issue['avg_duration']:.1f}s")
        report.append("")
    
    # Key Observations
    report.append("🔍 KEY OBSERVATIONS")
    report.append("-" * 80)
    
    observations = []
    
    if analysis['max_profit'] >= 0.10:
        observations.append(f"[OK] Trade reached trailing zone (max profit: ${analysis['max_profit']:.2f})")
    
    if analysis['max_profit'] >= 0.03:
        observations.append(f"[OK] Trade entered sweet spot zone (max profit: ${analysis['max_profit']:.2f})")
    
    if len(analysis['events']['sl_not_updating']) > 0:
        max_sl_duration = max([e['duration'] for e in analysis['events']['sl_not_updating']])
        observations.append(f"[WARNING]  SL update delays detected (max: {max_sl_duration:.1f}s)")
    
    if len(analysis['events']['close_failures']) > 0:
        observations.append(f"[ERROR] Close failures detected: {len(analysis['events']['close_failures'])}")
    
    if len(analysis['events']['execution_errors']) > 0:
        observations.append(f"[ERROR] Execution errors detected: {len(analysis['events']['execution_errors'])}")
    
    if len(analysis['events']['slow_executions']) > 0:
        observations.append(f"🟠 Slow executions detected: {len(analysis['events']['slow_executions'])}")
    
    if not observations:
        observations.append("[OK] No major issues detected")
    
    for obs in observations:
        report.append(f"  {obs}")
    
    report.append("")
    report.append("=" * 80)
    
    return "\n".join(report)


def main():
    """Main function to analyze log and print summary."""
    log_file = "logs/live/system/lightweight_realtime.log"
    
    print("Analyzing lightweight real-time log...")
    analysis = analyze_lightweight_log(log_file)
    
    print("\n")
    report = generate_summary_report(analysis)
    print(report)
    
    # Also save to file
    report_file = "logs/reports/lightweight_log_analysis.txt"
    import os
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n[OK] Analysis saved to: {report_file}")


if __name__ == "__main__":
    main()

