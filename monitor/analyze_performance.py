#!/usr/bin/env python3
"""
Comprehensive Trading Bot Performance Analysis
Parses all logs and generates full diagnostic report.
"""

import re
import json
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import os

class TradeAnalyzer:
    def __init__(self, log_dir: str = ".", symbols_log_dir: str = "logs/symbols"):
        self.log_dir = log_dir
        self.symbols_log_dir = symbols_log_dir
        self.trades = {}  # {ticket: TradeInfo}
        self.sl_adjustments = []  # List of all SL adjustments
        self.errors = []  # List of errors
        self.symbol_logs = {}  # {symbol: [log_lines]}
        
    def parse_main_log(self, log_file: str = "bot_log.txt"):
        """Parse the main bot log file."""
        log_path = os.path.join(self.log_dir, log_file)
        if not os.path.exists(log_path):
            print(f"ERROR: Main log file not found: {log_path}")
            return
        
        print(f"Parsing main log: {log_path}")
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        print(f"Total lines in main log: {len(lines)}")
        
        for line_num, line in enumerate(lines, 1):
            # Parse trade execution
            if "✅ TRADE EXECUTED:" in line or "Order placed successfully:" in line:
                self._parse_trade_execution(line, line_num)
            
            # Parse SL adjustments
            if "📈 SL ADJUSTED:" in line:
                self._parse_sl_adjustment(line, line_num)
            
            # Parse trade closures (need to check for position closed messages)
            if "Position" in line and "closed" in line.lower():
                self._parse_trade_closure(line, line_num)
            
            # Parse errors
            if "ERROR" in line or "❌" in line or "failed" in line.lower():
                self._parse_error(line, line_num)
            
            # Parse modification errors for specific tickets
            if "Modify order failed" in line:
                self._parse_modification_error(line, line_num)
    
    def _parse_trade_execution(self, line: str, line_num: int):
        """Parse trade execution log line."""
        # Pattern: ✅ TRADE EXECUTED: SYMBOL DIRECTION | Ticket: TICKET | Entry: PRICE | Lot: SIZE | SL: PIPS | Spread+Fees: $COST
        match = re.search(r'✅ TRADE EXECUTED: (\w+)\s+(LONG|SHORT)\s+\|\s+Ticket:\s+(\d+)\s+\|\s+Entry:\s+([\d.]+)\s+\|\s+Lot:\s+([\d.]+).*?SL:\s+([\d.]+)pips', line)
        if not match:
            # Try alternative pattern from "Order placed successfully"
            match = re.search(r'Order placed successfully: Ticket (\d+), Symbol (\w+), Type (BUY|SELL)', line)
            if match:
                ticket = int(match.group(1))
                symbol = match.group(2)
                direction = "LONG" if match.group(3) == "BUY" else "SHORT"
                # Try to get entry price from next line
                return  # Will be handled by next line
            return
        
        symbol = match.group(1)
        direction = match.group(2)
        ticket = int(match.group(3))
        entry_price = float(match.group(4))
        lot_size = float(match.group(5))
        sl_pips = float(match.group(6))
        
        # Extract timestamp
        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        timestamp = None
        if time_match:
            try:
                timestamp = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        # Extract spread+fees
        spread_match = re.search(r'Spread\+Fees:\s+\$([\d.]+)', line)
        spread_fees = float(spread_match.group(1)) if spread_match else 0.0
        
        # Extract quality score if available
        quality_match = re.search(r'Q:([\d.]+)', line)
        quality_score = float(quality_match.group(1)) if quality_match else None
        
        # Extract risk reason
        risk_reason_match = re.search(r'Reason: (.+?)(?:\s*\||\s*$)', line)
        risk_reason = risk_reason_match.group(1) if risk_reason_match else None
        
        if ticket not in self.trades:
            self.trades[ticket] = {
                'ticket': ticket,
                'symbol': symbol,
                'direction': direction,
                'entry_time': timestamp,
                'entry_price': entry_price,
                'lot_size': lot_size,
                'initial_sl_pips': sl_pips,
                'spread_fees': spread_fees,
                'quality_score': quality_score,
                'risk_reason': risk_reason,
                'sl_adjustments': [],
                'modification_errors': [],
                'close_time': None,
                'close_price': None,
                'close_reason': None,
                'final_profit': None,
                'actual_risk': None,
                'slippage_entry': None,
                'slippage_exit': None,
                'log_line': line_num,
                'last_sl_adjustment_time': None
            }
    
    def _parse_sl_adjustment(self, line: str, line_num: int):
        """Parse SL adjustment log line."""
        # Pattern: 📈 SL ADJUSTED: SYMBOL Ticket TICKET | Profit: $X → SL: $Y
        match = re.search(r'📈 SL ADJUSTED: (\w+)\s+Ticket\s+(\d+)\s+\|\s+Profit:\s+\$([\d.]+)\s+→\s+SL:\s+\$([\d.]+)', line)
        if not match:
            return
        
        symbol = match.group(1)
        ticket = int(match.group(2))
        profit = float(match.group(3))
        sl_profit = float(match.group(4))
        
        # Extract timestamp
        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        timestamp = None
        if time_match:
            try:
                timestamp = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        adjustment = {
            'ticket': ticket,
            'symbol': symbol,
            'timestamp': timestamp,
            'profit': profit,
            'sl_profit': sl_profit,
            'log_line': line_num
        }
        
        self.sl_adjustments.append(adjustment)
        
        if ticket in self.trades:
            self.trades[ticket]['sl_adjustments'].append(adjustment)
    
    def _parse_trade_closure(self, line: str, line_num: int):
        """Parse trade closure log line."""
        # Look for position closed messages
        match = re.search(r'Position\s+(\d+)\s+closed', line, re.IGNORECASE)
        if match:
            ticket = int(match.group(1))
            if ticket in self.trades:
                time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                if time_match:
                    try:
                        self.trades[ticket]['close_time'] = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
                    except:
                        pass
                self.trades[ticket]['close_reason'] = "Position closed"
    
    def _parse_error(self, line: str, line_num: int):
        """Parse error log line."""
        time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        timestamp = None
        if time_match:
            try:
                timestamp = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
            except:
                pass
        
        self.errors.append({
            'timestamp': timestamp,
            'line': line.strip(),
            'line_num': line_num
        })
    
    def _parse_modification_error(self, line: str, line_num: int):
        """Parse SL modification error log line."""
        # Look for ticket number in context (might be in previous lines, but try to extract from error)
        # Pattern: Modify order failed: CODE - MESSAGE
        match = re.search(r'Modify order failed:\s+(\d+)\s+-\s+(.+)', line)
        if match:
            error_code = match.group(1)
            error_msg = match.group(2)
            
            time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            timestamp = None
            if time_match:
                try:
                    timestamp = datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S')
                except:
                    pass
            
            # Try to find associated ticket from nearby SL adjustments
            # This is approximate - we'll match by timestamp proximity
            error_info = {
                'timestamp': timestamp,
                'error_code': error_code,
                'error_msg': error_msg,
                'line_num': line_num
            }
            
            # Store for later association with trades
            if not hasattr(self, 'modification_errors'):
                self.modification_errors = []
            self.modification_errors.append(error_info)
    
    def parse_symbol_logs(self):
        """Parse all symbol-specific log files."""
        if not os.path.exists(self.symbols_log_dir):
            print(f"Symbol logs directory not found: {self.symbols_log_dir}")
            return
        
        print(f"Parsing symbol logs from: {self.symbols_log_dir}")
        log_files = list(Path(self.symbols_log_dir).glob("*.log"))
        print(f"Found {len(log_files)} symbol log files")
        
        for log_file in log_files:
            symbol = log_file.stem.split('_')[0]  # Extract symbol from filename
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                self.symbol_logs[symbol] = lines
    
    def calculate_trade_metrics(self):
        """Calculate metrics for each trade."""
        for ticket, trade in self.trades.items():
            # Calculate actual risk more accurately
            # Risk = Lot Size * SL Distance (in price) * Contract Size
            # For most symbols: 1 pip = 10 points, contract_size = 100000 for forex
            # Simplified: Risk ≈ Lot * SL_pips * 10 (for standard forex)
            # But this varies by symbol - we'll use a conservative estimate
            if trade['entry_price'] and trade['initial_sl_pips'] and trade['lot_size']:
                # Rough estimate: for 0.01 lot, 10 pips SL ≈ $1 risk on standard forex
                # Adjust based on lot size
                base_risk_per_pip = 0.10  # $0.10 per pip for 0.01 lot on standard forex
                trade['estimated_risk'] = trade['lot_size'] * trade['initial_sl_pips'] * base_risk_per_pip * 10
            else:
                trade['estimated_risk'] = None
            
            # Find final SL adjustment and infer closure
            if trade['sl_adjustments']:
                final_adj = trade['sl_adjustments'][-1]
                trade['final_sl_profit'] = final_adj['sl_profit']
                trade['peak_profit'] = max([adj['profit'] for adj in trade['sl_adjustments']])
                trade['last_sl_adjustment_time'] = final_adj['timestamp']
                
                # Infer closure: if last SL adjustment was negative and > 2 hours ago, likely closed
                # Or if profit went negative significantly
                if final_adj['timestamp']:
                    time_since_last_adj = (datetime.now() - final_adj['timestamp']).total_seconds() / 3600
                    if time_since_last_adj > 2.0:  # More than 2 hours since last adjustment
                        trade['close_time'] = final_adj['timestamp'] + timedelta(minutes=5)  # Estimate
                        trade['close_reason'] = 'Inferred from SL adjustment timeout'
                        trade['final_profit'] = final_adj['sl_profit']
            else:
                trade['final_sl_profit'] = None
                trade['peak_profit'] = None
            
            # Associate modification errors with trades by timestamp proximity
            if hasattr(self, 'modification_errors'):
                for error in self.modification_errors:
                    if error['timestamp'] and trade['entry_time']:
                        # If error occurred after trade entry and within reasonable time
                        if error['timestamp'] > trade['entry_time']:
                            time_diff = (error['timestamp'] - trade['entry_time']).total_seconds() / 3600
                            if time_diff < 24:  # Within 24 hours
                                if ticket not in trade.get('modification_errors', []):
                                    if 'modification_errors' not in trade:
                                        trade['modification_errors'] = []
                                    trade['modification_errors'].append(error)
            
            # Detect early exits: trades that closed with loss < $2 but should have been $2
            if trade['final_sl_profit'] is not None and trade['final_sl_profit'] < 0:
                if abs(trade['final_sl_profit']) < 1.5:  # Closed early (loss < $1.50)
                    trade['early_exit'] = True
                    trade['exit_violation'] = f"Closed at ${trade['final_sl_profit']:.2f} instead of -$2.00"
                elif abs(trade['final_sl_profit']) > 2.5:  # Closed late (loss > $2.50)
                    trade['late_exit'] = True
                    trade['exit_violation'] = f"Closed at ${trade['final_sl_profit']:.2f} instead of -$2.00"
    
    def generate_report(self) -> str:
        """Generate comprehensive diagnostic report."""
        report = []
        report.append("=" * 80)
        report.append("TRADING BOT PERFORMANCE DIAGNOSTIC REPORT")
        report.append("=" * 80)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # 1. SESSION SUMMARY
        report.append("1. SESSION SUMMARY")
        report.append("-" * 80)
        total_trades = len(self.trades)
        closed_trades = [t for t in self.trades.values() if t['close_time']]
        open_trades = [t for t in self.trades.values() if not t['close_time']]
        
        report.append(f"Total Trades Opened: {total_trades}")
        report.append(f"Closed Trades: {len(closed_trades)}")
        report.append(f"Open Trades: {len(open_trades)}")
        report.append("")
        
        # Calculate wins/losses (need to infer from final profit or SL adjustments)
        wins = 0
        losses = 0
        breakevens = 0
        total_profit = 0.0
        total_loss = 0.0
        
        for trade in closed_trades:
            if trade['final_sl_profit'] is not None:
                if trade['final_sl_profit'] > 0:
                    wins += 1
                    total_profit += trade['final_sl_profit']
                elif trade['final_sl_profit'] < 0:
                    losses += 1
                    total_loss += abs(trade['final_sl_profit'])
                else:
                    breakevens += 1
        
        report.append(f"Wins: {wins}")
        report.append(f"Losses: {losses}")
        report.append(f"Breakevens: {breakevens}")
        if wins > 0:
            report.append(f"Average Win: ${total_profit / wins:.2f}")
        if losses > 0:
            report.append(f"Average Loss: ${total_loss / losses:.2f}")
        report.append(f"Net P/L: ${total_profit - total_loss:.2f}")
        report.append("")
        
        # Expected vs actual risk
        report.append("Expected Risk per Trade: $2.00")
        risks = [t.get('estimated_risk', 0) for t in self.trades.values() if t.get('estimated_risk')]
        if risks:
            avg_risk = sum(risks) / len(risks)
            report.append(f"Average Actual Risk: ${avg_risk:.2f}")
        report.append("")
        
        # 2. EARLY EXIT ANALYSIS
        report.append("2. EARLY EXIT ANALYSIS")
        report.append("-" * 80)
        early_exits = []
        for trade in self.trades.values():
            if trade.get('early_exit') or (trade['final_sl_profit'] is not None and 
                                          trade['final_sl_profit'] > -2.0 and 
                                          trade['final_sl_profit'] < 0):
                early_exits.append(trade)
        
        report.append(f"Trades closed early (loss < $2): {len(early_exits)}")
        for trade in early_exits[:15]:  # Show first 15
            mod_errors = len(trade.get('modification_errors', []))
            report.append(f"  Ticket {trade['ticket']}: {trade['symbol']} {trade['direction']} | "
                         f"Loss: ${trade['final_sl_profit']:.2f} | "
                         f"SL Adjustments: {len(trade['sl_adjustments'])} | "
                         f"Mod Errors: {mod_errors} | "
                         f"Reason: {trade.get('close_reason', 'Unknown')}")
        report.append("")
        
        # 3. LATE EXIT ANALYSIS
        report.append("3. LATE EXIT ANALYSIS")
        report.append("-" * 80)
        late_exits = []
        for trade in self.trades.values():
            if trade.get('late_exit') or (trade['final_sl_profit'] is not None and 
                                         trade['final_sl_profit'] < -2.5):
                late_exits.append(trade)
        
        report.append(f"Trades closed late (loss > $2.50): {len(late_exits)}")
        for trade in late_exits[:15]:  # Show first 15
            mod_errors = len(trade.get('modification_errors', []))
            report.append(f"  Ticket {trade['ticket']}: {trade['symbol']} {trade['direction']} | "
                         f"Loss: ${trade['final_sl_profit']:.2f} | "
                         f"Expected: -$2.00 | "
                         f"SL Adjustments: {len(trade['sl_adjustments'])} | "
                         f"Mod Errors: {mod_errors} | "
                         f"Reason: {trade.get('close_reason', 'Unknown')}")
        report.append("")
        
        # 4. SYMBOL-WISE PERFORMANCE
        report.append("4. SYMBOL-WISE PERFORMANCE")
        report.append("-" * 80)
        symbol_stats = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'total_pl': 0.0})
        
        for trade in self.trades.values():
            symbol = trade['symbol']
            symbol_stats[symbol]['trades'] += 1
            if trade['final_sl_profit'] is not None:
                if trade['final_sl_profit'] > 0:
                    symbol_stats[symbol]['wins'] += 1
                elif trade['final_sl_profit'] < 0:
                    symbol_stats[symbol]['losses'] += 1
                symbol_stats[symbol]['total_pl'] += trade['final_sl_profit']
        
        # Sort by total P/L
        sorted_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['total_pl'])
        
        report.append("Top 10 Worst Performing Symbols:")
        for symbol, stats in sorted_symbols[:10]:
            report.append(f"  {symbol}: {stats['trades']} trades, {stats['wins']}W/{stats['losses']}L, "
                         f"Net P/L: ${stats['total_pl']:.2f}")
        report.append("")
        
        report.append("Top 10 Best Performing Symbols:")
        for symbol, stats in sorted_symbols[-10:][::-1]:
            report.append(f"  {symbol}: {stats['trades']} trades, {stats['wins']}W/{stats['losses']}L, "
                         f"Net P/L: ${stats['total_pl']:.2f}")
        report.append("")
        
        # 5. ROOT-CAUSE FINDINGS
        report.append("5. ROOT-CAUSE FINDINGS")
        report.append("-" * 80)
        
        # Analyze SL adjustments
        report.append("SL Adjustment Analysis:")
        total_adjustments = len(self.sl_adjustments)
        report.append(f"Total SL Adjustments: {total_adjustments}")
        
        # Find trades with many adjustments
        trades_with_many_adj = [(t['ticket'], len(t['sl_adjustments'])) 
                               for t in self.trades.values() 
                               if len(t['sl_adjustments']) > 5]
        if trades_with_many_adj:
            report.append(f"Trades with >5 SL adjustments: {len(trades_with_many_adj)}")
            for ticket, count in trades_with_many_adj[:5]:
                trade = self.trades[ticket]
                report.append(f"  Ticket {ticket} ({trade['symbol']}): {count} adjustments")
        report.append("")
        
        # Error analysis
        report.append("Error Analysis:")
        report.append(f"Total Errors Found: {len(self.errors)}")
        if self.errors:
            # Group by error type
            error_types = defaultdict(int)
            mod_errors_by_code = defaultdict(int)
            for error in self.errors:
                error_line = error['line']
                if "Modify order failed" in error_line:
                    error_types["SL Modification Failed"] += 1
                    # Extract error code
                    code_match = re.search(r'failed:\s+(\d+)', error_line)
                    if code_match:
                        mod_errors_by_code[code_match.group(1)] += 1
                elif "Order failed" in error_line or "Order send returned" in error_line:
                    error_types["Order Placement Failed"] += 1
                elif "stale" in error_line.lower():
                    error_types["Price Staleness"] += 1
                else:
                    error_types["Other"] += 1
            
            for err_type, count in error_types.items():
                report.append(f"  {err_type}: {count}")
            
            if mod_errors_by_code:
                report.append("  SL Modification Error Codes:")
                for code, count in sorted(mod_errors_by_code.items(), key=lambda x: x[1], reverse=True):
                    code_meanings = {
                        '10016': 'Invalid stops (freeze level violation)',
                        '10025': 'No changes (SL already at that level)',
                        '10027': 'Trade disabled',
                        '10014': 'Invalid volume'
                    }
                    meaning = code_meanings.get(code, 'Unknown error')
                    report.append(f"    Code {code} ({meaning}): {count} occurrences")
        report.append("")
        
        # Risk analysis
        report.append("Risk Analysis:")
        report.append(f"Expected Risk per Trade: $2.00")
        risks = [t.get('estimated_risk', 0) for t in self.trades.values() if t.get('estimated_risk')]
        if risks:
            avg_risk = sum(risks) / len(risks)
            max_risk = max(risks)
            min_risk = min(risks)
            report.append(f"Average Actual Risk: ${avg_risk:.2f}")
            report.append(f"Max Risk: ${max_risk:.2f}")
            report.append(f"Min Risk: ${min_risk:.2f}")
            risk_violations = [r for r in risks if r > 2.5]
            if risk_violations:
                report.append(f"⚠️  Trades with risk > $2.50: {len(risk_violations)}")
        report.append("")
        
        # 6. DETAILED TRADE LIST
        report.append("6. DETAILED TRADE LIST (First 20)")
        report.append("-" * 80)
        for i, (ticket, trade) in enumerate(list(self.trades.items())[:20]):
            report.append(f"Trade {i+1}: Ticket {ticket}")
            report.append(f"  Symbol: {trade['symbol']} {trade['direction']}")
            report.append(f"  Entry: {trade['entry_time']} @ {trade['entry_price']}")
            report.append(f"  Lot: {trade['lot_size']}, SL: {trade['initial_sl_pips']} pips")
            report.append(f"  Quality Score: {trade.get('quality_score', 'N/A')}")
            report.append(f"  SL Adjustments: {len(trade['sl_adjustments'])}")
            if trade['close_time']:
                report.append(f"  Closed: {trade['close_time']} (Reason: {trade.get('close_reason', 'Unknown')})")
            if trade['final_sl_profit'] is not None:
                report.append(f"  Final P/L: ${trade['final_sl_profit']:.2f}")
            report.append("")
        
        # 7. REQUIRED CODE FIXES
        report.append("7. REQUIRED CODE FIXES")
        report.append("-" * 80)
        
        # Fix 1: Risk calculation issues
        risk_violations = [t for t in self.trades.values() if t.get('estimated_risk', 0) > 2.5]
        if risk_violations:
            report.append("ISSUE 1: Risk per trade exceeds $2.00")
            report.append(f"  Found: {len(risk_violations)} trades with risk > $2.50")
            report.append("  Root Cause: Lot size calculation may not properly account for symbol-specific pip values")
            report.append("  File: risk/risk_manager.py")
            report.append("  Function: calculate_minimum_lot_size_for_risk()")
            report.append("  Fix Required:")
            report.append("    - Verify pip value calculation for each symbol type (forex vs crypto vs indices)")
            report.append("    - Ensure contract_size is properly used in risk calculation")
            report.append("    - Add validation to reject trades if calculated risk > $2.00")
            report.append("")
        
        # Fix 2: SL modification errors
        mod_error_count = sum(len(t.get('modification_errors', [])) for t in self.trades.values())
        if mod_error_count > 0:
            report.append("ISSUE 2: SL Modification Failures")
            report.append(f"  Found: {mod_error_count} modification errors across trades")
            report.append("  Root Cause: ")
            report.append("    - Code 10025 (No changes): Bot trying to set SL to same value repeatedly")
            report.append("    - Code 10016 (Invalid stops): Freeze level violation - SL too close to current price")
            report.append("  File: risk/risk_manager.py")
            report.append("  Function: update_continuous_trailing_stop(), set_trailing_stop_at_profit()")
            report.append("  Fix Required:")
            report.append("    - Add check to skip modification if new SL == current SL (prevent 10025)")
            report.append("    - Validate SL distance against freeze level before modification")
            report.append("    - Add retry logic with backoff for transient errors")
            report.append("    - Log freeze level violations with symbol info for debugging")
            report.append("")
        
        # Fix 3: Early exits (if any found)
        early_exits = [t for t in self.trades.values() if t.get('early_exit')]
        if early_exits:
            report.append("ISSUE 3: Trades Closing Before -$2.00 Stop Loss")
            report.append(f"  Found: {len(early_exits)} trades closed early")
            report.append("  Root Cause: Possible causes:")
            report.append("    - Trailing stop moved too aggressively, closing trade before -$2 loss")
            report.append("    - Price feed staleness causing incorrect SL updates")
            report.append("    - Broker closing position due to margin/other reasons")
            report.append("  File: risk/risk_manager.py, execution/order_manager.py")
            report.append("  Fix Required:")
            report.append("    - Ensure trailing stop never moves SL to worse than -$2.00 from entry")
            report.append("    - Add price staleness check before SL modifications")
            report.append("    - Log all position closures with reason codes")
            report.append("")
        
        # Fix 4: Late exits (if any found)
        late_exits = [t for t in self.trades.values() if t.get('late_exit')]
        if late_exits:
            report.append("ISSUE 4: Trades Closing After -$2.00 Stop Loss")
            report.append(f"  Found: {len(late_exits)} trades closed late")
            report.append("  Root Cause: Possible causes:")
            report.append("    - SL modification failed, leaving original SL too far")
            report.append("    - Slippage on stop loss execution")
            report.append("    - Broker minimum lot size forcing higher risk")
            report.append("  File: risk/risk_manager.py, execution/order_manager.py")
            report.append("  Fix Required:")
            report.append("    - Reject trades if minimum lot causes risk > $2.00")
            report.append("    - Monitor SL modification success and retry if failed")
            report.append("    - Account for slippage in risk calculation")
            report.append("")
        
        # Fix 5: Excessive "No changes" errors
        no_change_errors = [e for e in self.errors if "10025" in e.get('line', '')]
        if len(no_change_errors) > 50:
            report.append("ISSUE 5: Excessive 'No Changes' SL Modification Attempts")
            report.append(f"  Found: {len(no_change_errors)} attempts to modify SL to same value")
            report.append("  Root Cause: Trailing stop logic not checking if SL already at target")
            report.append("  File: risk/risk_manager.py")
            report.append("  Function: update_continuous_trailing_stop()")
            report.append("  Fix Required:")
            report.append("    - Compare new SL price with current SL before calling modify_order()")
            report.append("    - Skip modification if abs(new_sl - current_sl) < minimum price increment")
            report.append("    - Add early return if no change needed")
            report.append("")
        
        # Fix 6: Missing quality scores in trade logs
        trades_without_quality = [t for t in self.trades.values() if t.get('quality_score') is None]
        if trades_without_quality:
            report.append("ISSUE 6: Missing Quality Scores in Trade Logs")
            report.append(f"  Found: {len(trades_without_quality)} trades without quality scores logged")
            report.append("  Root Cause: Quality score not included in trade execution log line")
            report.append("  File: bot/trading_bot.py")
            report.append("  Function: execute_trade()")
            report.append("  Fix Required:")
            report.append("    - Include quality_score in TRADE EXECUTED log message")
            report.append("    - Include trend strength and other filter results in log")
            report.append("")
        
        # 8. SYSTEMIC ISSUES
        report.append("8. SYSTEMIC ISSUES")
        report.append("-" * 80)
        
        # Check for pattern: many trades with no SL adjustments
        trades_no_adj = [t for t in self.trades.values() if len(t['sl_adjustments']) == 0]
        if len(trades_no_adj) > total_trades * 0.3:  # More than 30%
            report.append(f"ISSUE: {len(trades_no_adj)} trades ({len(trades_no_adj)/total_trades*100:.1f}%) have no SL adjustments")
            report.append("  Possible causes:")
            report.append("    - Trades closed immediately (hit SL/TP)")
            report.append("    - Trailing stop not working for these symbols")
            report.append("    - Trades still open but not yet profitable")
            report.append("")
        
        # Check for pattern: trades with very high SL pips
        high_sl_trades = [t for t in self.trades.values() if t.get('initial_sl_pips', 0) > 200]
        if high_sl_trades:
            report.append(f"ISSUE: {len(high_sl_trades)} trades have SL > 200 pips")
            report.append("  These trades likely have incorrect risk calculation")
            report.append("  Symbols affected:")
            symbols_high_sl = set(t['symbol'] for t in high_sl_trades)
            for symbol in list(symbols_high_sl)[:10]:
                count = sum(1 for t in high_sl_trades if t['symbol'] == symbol)
                report.append(f"    - {symbol}: {count} trades")
            report.append("")
        
        report.append("=" * 80)
        report.append("END OF DIAGNOSTIC REPORT")
        report.append("=" * 80)
        
        return "\n".join(report)

def main():
    analyzer = TradeAnalyzer()
    
    print("Starting comprehensive log analysis...")
    print("=" * 80)
    
    # Parse main log
    analyzer.parse_main_log()
    
    # Parse symbol logs
    analyzer.parse_symbol_logs()
    
    # Calculate metrics
    analyzer.calculate_trade_metrics()
    
    # Generate report
    report = analyzer.generate_report()
    
    # Save report
    report_file = "performance_diagnostic_report.txt"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    # Print report (handle Unicode)
    try:
        print("\n" + report)
    except UnicodeEncodeError:
        print("\n[Report contains Unicode characters - see file for full output]")
    
    print(f"\n{'=' * 80}")
    print(f"Report saved to: {report_file}")
    print(f"{'=' * 80}")

if __name__ == "__main__":
    main()

