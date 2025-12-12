#!/usr/bin/env python3
"""
Generate Test Analysis Report
Parses bot_log.txt and generates a comprehensive analysis report for test mode trading.
"""

import re
import json
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any

def parse_trades_from_log(log_file='bot_log.txt'):
    """Parse all trades from log file."""
    trades = []
    symbols_analyzed = defaultdict(list)
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            # Parse trade executions
            if "Trade executed:" in line or "[TEST] TEST MODE: [OK] Trade executed:" in line:
                match = re.search(r'Ticket (\d+).*?(\w+)\s+(LONG|SHORT).*?Lot: ([\d.]+).*?SL: ([\d.]+)', line)
                if match:
                    quality_match = re.search(r'Quality[:\s]+([\d.]+)', line)
                    spread_match = re.search(r'Spread[:\s]+([\d.]+)', line)
                    time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    
                    trades.append({
                        'ticket': int(match.group(1)),
                        'symbol': match.group(2),
                        'direction': match.group(3),
                        'lot_size': float(match.group(4)),
                        'stop_loss_pips': float(match.group(5)),
                        'quality_score': float(quality_match.group(1)) if quality_match else None,
                        'spread': float(spread_match.group(1)) if spread_match else None,
                        'time': datetime.strptime(time_match.group(1), '%Y-%m-%d %H:%M:%S') if time_match else None
                    })
            
            # Parse symbols analyzed (test mode)
            if "[TEST] TEST MODE:" in line and "[OK]" in line:
                match = re.search(r'[OK]\s+(\w+):\s+Signal=(\w+).*?RSI=([\d.]+).*?Spread=([\d.]+).*?Quality.*?Score:\s+([\d.]+)', line)
                if match:
                    symbols_analyzed[match.group(1)].append({
                        'signal': match.group(2),
                        'rsi': float(match.group(3)),
                        'spread': float(match.group(4)),
                        'quality_score': float(match.group(5))
                    })
    
    except FileNotFoundError:
        print(f"Log file {log_file} not found")
        return [], {}
    except Exception as e:
        print(f"Error parsing log: {e}")
        return [], {}
    
    return trades, dict(symbols_analyzed)

def generate_report(trades: List[Dict], symbols_analyzed: Dict[str, List]):
    """Generate analysis report."""
    print("=" * 100)
    print("📊 TEST MODE ANALYSIS REPORT")
    print("=" * 100)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Trade statistics
    print("=" * 100)
    print("[STATS] TRADE STATISTICS")
    print("=" * 100)
    print(f"Total Trades Executed: {len(trades)}")
    
    if not trades:
        print("No trades found in log file.")
        return
    
    # Group by symbol
    trades_by_symbol = defaultdict(list)
    for trade in trades:
        trades_by_symbol[trade['symbol']].append(trade)
    
    print(f"Unique Symbols Traded: {len(trades_by_symbol)}")
    print()
    
    # Symbol performance
    print("=" * 100)
    print("🎯 SYMBOL PERFORMANCE")
    print("=" * 100)
    print(f"{'Symbol':<15} {'Trades':<8} {'Avg Quality':<12} {'Avg Spread':<12} {'Avg SL (pips)':<15}")
    print("-" * 100)
    
    for symbol in sorted(trades_by_symbol.keys()):
        symbol_trades = trades_by_symbol[symbol]
        avg_quality = sum(t.get('quality_score', 0) or 0 for t in symbol_trades) / len(symbol_trades)
        avg_spread = sum(t.get('spread', 0) or 0 for t in symbol_trades) / len(symbol_trades)
        avg_sl = sum(t.get('stop_loss_pips', 0) for t in symbol_trades) / len(symbol_trades)
        
        print(f"{symbol:<15} {len(symbol_trades):<8} {avg_quality:<12.1f} {avg_spread:<12.1f} {avg_sl:<15.1f}")
    
    print()
    
    # Quality distribution
    print("=" * 100)
    print("📊 QUALITY SCORE DISTRIBUTION")
    print("=" * 100)
    quality_ranges = {
        '60-70': 0,
        '70-80': 0,
        '80-90': 0,
        '90-100': 0
    }
    
    for trade in trades:
        q = trade.get('quality_score', 0) or 0
        if 60 <= q < 70:
            quality_ranges['60-70'] += 1
        elif 70 <= q < 80:
            quality_ranges['70-80'] += 1
        elif 80 <= q < 90:
            quality_ranges['80-90'] += 1
        elif 90 <= q <= 100:
            quality_ranges['90-100'] += 1
    
    for range_name, count in quality_ranges.items():
        print(f"{range_name}: {count} trades")
    
    print()
    
    # Symbols analyzed (test mode)
    if symbols_analyzed:
        print("=" * 100)
        print("🔍 SYMBOLS ANALYZED (Not Necessarily Traded)")
        print("=" * 100)
        print(f"Total Unique Symbols Analyzed: {len(symbols_analyzed)}")
        print()
        
        # Top symbols by analysis frequency
        sorted_symbols = sorted(symbols_analyzed.items(), key=lambda x: len(x[1]), reverse=True)
        print("Top 20 Most Analyzed Symbols:")
        for symbol, analyses in sorted_symbols[:20]:
            avg_quality = sum(a['quality_score'] for a in analyses) / len(analyses)
            print(f"  {symbol}: {len(analyses)} analyses, Avg Quality: {avg_quality:.1f}")
    
    print()
    print("=" * 100)
    print("Report complete. Check bot_log.txt for detailed trade information.")
    print("=" * 100)

def main():
    """Main function."""
    trades, symbols_analyzed = parse_trades_from_log()
    generate_report(trades, symbols_analyzed)

if __name__ == '__main__':
    main()

