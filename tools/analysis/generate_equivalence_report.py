#!/usr/bin/env python3
"""
Generate Equivalence Backtest Final Report
Comprehensive analysis of backtest results including DRY-RUN limit entry analysis.
"""

import sys
import os
import re
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger_factory import get_logger

logger = get_logger("equivalence_report", "logs/backtest/equivalence_report.log")


def parse_logs_for_analysis():
    """Parse all backtest logs for comprehensive analysis."""
    log_dir = Path("logs/backtest")
    
    results = {
        'trades': {
            'taken': [],
            'rejected': [],
            'skipped': defaultdict(int)
        },
        'limit_entry': {
            'total': 0,
            'score_passed': 0,
            'would_fill': 0,
            'would_expire': 0,
            'spread_rejections': 0,
            'sl_distances': [],
            'details': []
        },
        'sl_updates': {
            'total': 0,
            'success': 0,
            'failed': 0,
            'lock_timeouts': 0
        },
        'worker_loops': {
            'run_cycle_times': [],
            'sl_worker_times': [],
            'overruns': 0
        },
        'filters': defaultdict(int)
    }
    
    # Parse all log files
    for log_file in log_dir.rglob("*.log"):
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    # Trade executions
                    if "[OK]" in line and "Position" in line and "verified" in line:
                        match = re.search(r'Position (\d+)', line)
                        if match:
                            results['trades']['taken'].append({
                                'ticket': match.group(1),
                                'line': line.strip()
                            })
                    
                    # Trade rejections
                    elif "[ERROR]" in line and ("Order" in line or "rejected" in line.lower()):
                        results['trades']['rejected'].append(line.strip())
                    
                    # Trade skips
                    elif "[SKIP]" in line and "Reason:" in line:
                        reason_match = re.search(r'Reason:\s*(.+?)(?:\s*$|\s*\||\s*\[)', line)
                        if reason_match:
                            reason = reason_match.group(1).strip()
                            results['trades']['skipped'][reason] += 1
                            results['filters'][reason] += 1
                    
                    # DRY-RUN Limit Entry
                    elif "[DRY_RUN][LIMIT_ENTRY]" in line:
                        results['limit_entry']['total'] += 1
                        
                        # Extract details
                        detail = {
                            'symbol': re.search(r'symbol=(\w+)', line).group(1) if re.search(r'symbol=(\w+)', line) else 'UNKNOWN',
                            'direction': re.search(r'direction=(\w+)', line).group(1) if re.search(r'direction=(\w+)', line) else 'UNKNOWN',
                            'score': float(re.search(r'score=([0-9.]+)', line).group(1)) if re.search(r'score=([0-9.]+)', line) else 0.0,
                            'would_fill': 'would_fill=true' in line,
                            'expiry_reason': None
                        }
                        
                        if 'would_fill=true' in line:
                            results['limit_entry']['would_fill'] += 1
                            results['limit_entry']['score_passed'] += 1
                        elif 'would_fill=false' in line:
                            results['limit_entry']['would_expire'] += 1
                            results['limit_entry']['score_passed'] += 1
                            expiry_match = re.search(r'expiry_reason=([^\s]+)', line)
                            if expiry_match:
                                detail['expiry_reason'] = expiry_match.group(1)
                        
                        if 'rejection_reason' in line and 'spread' in line.lower():
                            results['limit_entry']['spread_rejections'] += 1
                        
                        sl_dist_match = re.search(r'SL_distance=([0-9.]+)', line)
                        if sl_dist_match:
                            sl_dist = float(sl_dist_match.group(1))
                            results['limit_entry']['sl_distances'].append(sl_dist)
                            detail['sl_distance'] = sl_dist
                        
                        results['limit_entry']['details'].append(detail)
                    
                    # SL Updates
                    elif "SL UPDATE" in line or "update_sl_atomic" in line:
                        results['sl_updates']['total'] += 1
                        if "Success" in line or "SUCCESS" in line:
                            results['sl_updates']['success'] += 1
                        elif "FAILED" in line or "failed" in line:
                            results['sl_updates']['failed'] += 1
                        if "timeout" in line.lower() or "Lock acquisition timeout" in line:
                            results['sl_updates']['lock_timeouts'] += 1
                    
                    # Worker loop timing
                    elif "[RUN_CYCLE]" in line and "Duration:" in line:
                        duration_match = re.search(r'Duration:\s*([0-9.]+)s', line)
                        if duration_match:
                            results['worker_loops']['run_cycle_times'].append(float(duration_match.group(1)))
                    
                    elif "[SL_WORKER]" in line and "Duration:" in line:
                        duration_match = re.search(r'Duration:\s*([0-9.]+)ms', line)
                        if duration_match:
                            results['worker_loops']['sl_worker_times'].append(float(duration_match.group(1)))
                    
                    elif "overrun" in line.lower() or "Overrun" in line:
                        results['worker_loops']['overruns'] += 1
                        
        except Exception as e:
            logger.debug(f"Error parsing {log_file}: {e}")
            continue
    
    return results


def generate_report():
    """Generate comprehensive equivalence backtest report."""
    logger.info("=" * 80)
    logger.info("GENERATING EQUIVALENCE BACKTEST REPORT")
    logger.info("=" * 80)
    
    results = parse_logs_for_analysis()
    
    # Generate report
    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("EQUIVALENCE BACKTEST REPORT")
    report_lines.append("=" * 80)
    report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("")
    
    # 1. Trade Activity
    report_lines.append("1️⃣ TRADE ACTIVITY")
    report_lines.append("-" * 80)
    report_lines.append(f"Trades Taken: {len(results['trades']['taken'])}")
    report_lines.append(f"Trades Rejected: {len(results['trades']['rejected'])}")
    report_lines.append(f"Trades Skipped: {sum(results['trades']['skipped'].values())}")
    report_lines.append("")
    
    if results['trades']['skipped']:
        report_lines.append("Skip Reasons (Top 10):")
        for reason, count in sorted(results['trades']['skipped'].items(), key=lambda x: x[1], reverse=True)[:10]:
            report_lines.append(f"  {reason}: {count}")
    report_lines.append("")
    
    # 2. DRY-RUN Limit Entry Analysis
    report_lines.append("2️⃣ DRY-RUN LIMIT ENTRY ANALYSIS")
    report_lines.append("-" * 80)
    report_lines.append(f"Total Opportunities Analyzed: {results['limit_entry']['total']}")
    
    if results['limit_entry']['total'] > 0:
        report_lines.append(f"Score Pass Rate: {results['limit_entry']['score_passed']/results['limit_entry']['total']*100:.1f}%")
        report_lines.append(f"Would-Fill Rate: {results['limit_entry']['would_fill']/results['limit_entry']['total']*100:.1f}%")
        report_lines.append(f"Would-Expire Rate: {results['limit_entry']['would_expire']/results['limit_entry']['total']*100:.1f}%")
        report_lines.append(f"Spread Rejections: {results['limit_entry']['spread_rejections']}")
        
        if results['limit_entry']['sl_distances']:
            avg_sl = sum(results['limit_entry']['sl_distances']) / len(results['limit_entry']['sl_distances'])
            min_sl = min(results['limit_entry']['sl_distances'])
            max_sl = max(results['limit_entry']['sl_distances'])
            report_lines.append(f"Average SL Distance: {avg_sl:.2f} pips")
            report_lines.append(f"Min SL Distance: {min_sl:.2f} pips")
            report_lines.append(f"Max SL Distance: {max_sl:.2f} pips")
        
        # Expiry reasons
        expiry_reasons = defaultdict(int)
        for detail in results['limit_entry']['details']:
            if detail.get('expiry_reason'):
                expiry_reasons[detail['expiry_reason']] += 1
        
        if expiry_reasons:
            report_lines.append("")
            report_lines.append("Expiry Reasons:")
            for reason, count in sorted(expiry_reasons.items(), key=lambda x: x[1], reverse=True):
                report_lines.append(f"  {reason}: {count}")
    else:
        report_lines.append("No limit entry analyses found in logs")
    report_lines.append("")
    
    # 3. Risk & System Health
    report_lines.append("3️⃣ RISK & SYSTEM HEALTH")
    report_lines.append("-" * 80)
    if results['sl_updates']['total'] > 0:
        success_rate = results['sl_updates']['success'] / results['sl_updates']['total'] * 100
        report_lines.append(f"SL Update Success Rate: {success_rate:.1f}%")
        report_lines.append(f"SL Updates: {results['sl_updates']['total']} (success: {results['sl_updates']['success']}, failed: {results['sl_updates']['failed']})")
        report_lines.append(f"Lock Timeouts: {results['sl_updates']['lock_timeouts']}")
    else:
        report_lines.append("No SL updates found in logs")
    report_lines.append("")
    
    # Worker loop timing
    if results['worker_loops']['run_cycle_times']:
        avg_run_cycle = sum(results['worker_loops']['run_cycle_times']) / len(results['worker_loops']['run_cycle_times'])
        max_run_cycle = max(results['worker_loops']['run_cycle_times'])
        report_lines.append(f"Run Cycle Timing: Avg {avg_run_cycle:.3f}s, Max {max_run_cycle:.3f}s")
    
    if results['worker_loops']['sl_worker_times']:
        avg_sl_worker = sum(results['worker_loops']['sl_worker_times']) / len(results['worker_loops']['sl_worker_times'])
        max_sl_worker = max(results['worker_loops']['sl_worker_times'])
        report_lines.append(f"SL Worker Timing: Avg {avg_sl_worker:.1f}ms, Max {max_sl_worker:.1f}ms")
    
    report_lines.append(f"Worker Loop Overruns: {results['worker_loops']['overruns']}")
    report_lines.append("")
    
    # 4. Equivalence Assessment
    report_lines.append("4️⃣ EQUIVALENCE ASSESSMENT")
    report_lines.append("-" * 80)
    
    if len(results['trades']['taken']) == 0:
        report_lines.append("⚠️ ZERO TRADES CONDITION DETECTED")
        report_lines.append("")
        report_lines.append("Analysis:")
        report_lines.append(f"  Total opportunities skipped: {sum(results['trades']['skipped'].values())}")
        report_lines.append("")
        report_lines.append("Top filter rejection reasons:")
        for reason, count in sorted(results['trades']['skipped'].items(), key=lambda x: x[1], reverse=True)[:5]:
            report_lines.append(f"  - {reason}: {count} times")
        report_lines.append("")
        report_lines.append("This may be expected if:")
        report_lines.append("  - Market conditions don't meet entry criteria")
        report_lines.append("  - Filters are too strict for the date range")
        report_lines.append("  - Symbol has insufficient data or liquidity")
    else:
        report_lines.append(f"✅ BACKTEST executed {len(results['trades']['taken'])} trades")
        report_lines.append("")
        report_lines.append("Behavior observed:")
        report_lines.append("  - Trading logic executed")
        report_lines.append("  - Risk management active")
        report_lines.append("  - SL system operational")
    
    report_lines.append("")
    report_lines.append("=" * 80)
    
    # Write report
    report_text = "\n".join(report_lines)
    report_file = f"logs/backtest/equivalence_final_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    with open(report_file, 'w') as f:
        f.write(report_text)
    
    # Also print to console
    print(report_text)
    
    logger.info(f"\nReport saved to: {report_file}")
    
    return report_file


if __name__ == "__main__":
    try:
        report_file = generate_report()
        print(f"\n✅ Report generated: {report_file}")
    except Exception as e:
        logger.critical(f"Error generating report: {e}", exc_info=True)
        sys.exit(1)

