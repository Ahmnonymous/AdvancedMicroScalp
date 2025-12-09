#!/usr/bin/env python3
"""
Comprehensive Trading Bot Monitor
Continuously analyzes bot logs, trades, and performance metrics.
Provides real-time alerts, recommendations, and structured reports.
"""

import json
import os
import glob
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import MetaTrader5 as mt5

from execution.mt5_connector import MT5Connector
from execution.order_manager import OrderManager
from utils.logger_factory import get_logger

logger = get_logger("comprehensive_monitor", "logs/system/comprehensive_monitor.log")


class ComprehensiveBotMonitor:
    """Continuous monitoring and analysis of trading bot performance."""
    
    def __init__(self, config_path: str = 'config.json'):
        """Initialize the comprehensive monitor."""
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Initialize MT5 connection
        self.mt5_connector = MT5Connector(self.config)
        self.order_manager = OrderManager(self.mt5_connector)
        self.mt5_connector.connect()
        
        # Monitoring state
        self.running = False
        self.monitor_interval = 10.0  # Check every 10 seconds
        self.report_interval = 300.0  # Generate report every 5 minutes
        
        # Tracking data
        self.session_start = datetime.now()
        self.trades_tracked = {}  # {order_id: trade_data}
        self.lot_size_violations = []
        self.micro_hft_performance = {
            "total_closures": 0,
            "sweet_spot_closures": 0,
            "missed_sweet_spot": 0,
            "profit_distribution": []
        }
        self.filter_statistics = defaultdict(int)
        self.errors_detected = []
        self.skipped_symbols = defaultdict(list)
        
        # Alert thresholds
        self.sweet_spot_target = 0.80  # 80%
        self.max_lot_size = 0.03
        
        # Reports directory
        self.reports_dir = "logs/reports"
        os.makedirs(self.reports_dir, exist_ok=True)
    
    def analyze_active_positions(self) -> Dict[str, Any]:
        """Analyze all active positions from MT5."""
        positions = self.order_manager.get_open_positions(exclude_dec8=True)
        
        analysis = {
            "total": len(positions),
            "positions": [],
            "lot_size_issues": [],
            "symbols": defaultdict(int),
            "total_profit": 0.0
        }
        
        for pos in positions:
            lot_size = pos.get('volume', 0)
            symbol = pos.get('symbol', '')
            profit = pos.get('profit', 0)
            
            analysis["total_profit"] += profit
            analysis["symbols"][symbol] += 1
            
            position_data = {
                "ticket": pos.get('ticket'),
                "symbol": symbol,
                "lot_size": lot_size,
                "type": pos.get('type'),
                "entry_price": pos.get('price_open'),
                "current_price": pos.get('price_current'),
                "profit": profit,
                "time_open": pos.get('time_open')
            }
            analysis["positions"].append(position_data)
            
            # Check lot size compliance
            if lot_size > self.max_lot_size:
                issue = {
                    "ticket": pos.get('ticket'),
                    "symbol": symbol,
                    "lot_size": lot_size,
                    "issue": f"Lot size {lot_size} exceeds maximum {self.max_lot_size}",
                    "timestamp": datetime.now().isoformat()
                }
                analysis["lot_size_issues"].append(issue)
                self.lot_size_violations.append(issue)
        
        return analysis
    
    def analyze_trade_logs(self) -> Dict[str, Any]:
        """Analyze trade logs from logs/trades/ directory."""
        trade_log_dir = "logs/trades"
        all_trades = []
        closed_trades = []
        
        for log_file in glob.glob(os.path.join(trade_log_dir, "*.log")):
            symbol = os.path.basename(log_file).replace('.log', '')
            try:
                with open(log_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('{'):
                            try:
                                trade = json.loads(line)
                                trade['symbol'] = symbol
                                trade['log_file'] = log_file
                                all_trades.append(trade)
                                
                                if trade.get('status') == 'CLOSED':
                                    closed_trades.append(trade)
                                    
                                    # Analyze Micro-HFT performance
                                    profit = trade.get('profit_usd')
                                    close_reason = trade.get('additional_info', {}).get('close_reason', '')
                                    
                                    if profit is not None:
                                        self.micro_hft_performance["total_closures"] += 1
                                        self.micro_hft_performance["profit_distribution"].append(profit)
                                        
                                        if 'Micro-HFT' in close_reason or 'sweet spot' in close_reason.lower():
                                            if 0.03 <= profit <= 0.10:
                                                self.micro_hft_performance["sweet_spot_closures"] += 1
                                            else:
                                                self.micro_hft_performance["missed_sweet_spot"] += 1
                            except json.JSONDecodeError:
                                continue
            except Exception as e:
                logger.error(f"Error reading trade log {log_file}: {e}")
        
        return {
            "total_trades": len(all_trades),
            "closed_trades": len(closed_trades),
            "open_trades": len([t for t in all_trades if t.get('status') == 'OPEN']),
            "trades": all_trades
        }
    
    def analyze_filter_effectiveness(self) -> Dict[str, Any]:
        """Analyze filter effectiveness from system logs."""
        log_file = "logs/system/system_startup.log"
        filter_stats = defaultdict(int)
        last_scan_time = None
        
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # Read last 1000 lines for recent activity
                    recent_lines = lines[-1000:] if len(lines) > 1000 else lines
                    
                    for line in recent_lines:
                        if '[SKIP]' in line:
                            if 'Trend strength too weak' in line:
                                filter_stats['trend_strength_rejected'] += 1
                            elif 'RSI filter failed' in line:
                                filter_stats['rsi_rejected'] += 1
                            elif 'Insufficient tick volume' in line:
                                filter_stats['volume_rejected'] += 1
                            elif 'Market closing' in line or 'market close' in line.lower():
                                filter_stats['market_closing_rejected'] += 1
                            elif 'Min lot' in line and 'exceeds' in line:
                                filter_stats['lot_size_rejected'] += 1
                            elif 'Max open trades' in line:
                                filter_stats['max_trades_rejected'] += 1
                            else:
                                filter_stats['other_rejected'] += 1
                        
                        if 'Found' in line and 'opportunity' in line.lower():
                            # Extract number of opportunities
                            try:
                                parts = line.split('Found')
                                if len(parts) > 1:
                                    num_part = parts[1].split()[0]
                                    filter_stats['opportunities_found'] = int(num_part)
                            except:
                                pass
                        
                        if 'Scanning' in line:
                            last_scan_time = datetime.now()
        except Exception as e:
            logger.error(f"Error analyzing filter effectiveness: {e}")
        
        return {
            "statistics": dict(filter_stats),
            "last_scan_time": last_scan_time.isoformat() if last_scan_time else None
        }
    
    def check_lot_size_compliance(self, symbol: str) -> Tuple[bool, float, str]:
        """Check if symbol's lot size complies with system limits."""
        try:
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if not symbol_info:
                return False, 0.0, "Cannot get symbol info"
            
            broker_min_lot = symbol_info.get('volume_min', 0.01)
            
            # Check config overrides
            symbol_upper = symbol.upper()
            symbol_limits = self.config.get('risk', {}).get('symbol_limits', {})
            config_min_lot = symbol_limits.get(symbol_upper, {}).get('min_lot')
            
            # Effective minimum is max of broker and config
            if config_min_lot is not None:
                effective_min_lot = max(broker_min_lot, config_min_lot)
            else:
                effective_min_lot = broker_min_lot
            
            if effective_min_lot > self.max_lot_size:
                return False, effective_min_lot, f"Broker minimum {effective_min_lot} > {self.max_lot_size} limit"
            
            return True, effective_min_lot, f"Compliant (min: {effective_min_lot})"
        except Exception as e:
            logger.error(f"Error checking lot size compliance for {symbol}: {e}")
            return False, 0.0, f"Error: {str(e)}"
    
    def detect_errors(self) -> List[Dict[str, Any]]:
        """Detect errors from system error logs."""
        error_log = "logs/system/system_errors.log"
        errors = []
        
        try:
            if os.path.exists(error_log):
                with open(error_log, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # Check last 100 lines
                    recent_lines = lines[-100:] if len(lines) > 100 else lines
                    
                    for line in recent_lines:
                        if any(keyword in line.upper() for keyword in ['ERROR', 'FAILED', 'EXCEPTION', 'CRITICAL']):
                            errors.append({
                                "timestamp": datetime.now().isoformat(),
                                "message": line.strip(),
                                "severity": "ERROR" if "ERROR" in line.upper() else "WARNING"
                            })
        except Exception as e:
            logger.error(f"Error detecting errors: {e}")
        
        return errors
    
    def calculate_micro_hft_performance(self) -> Dict[str, Any]:
        """Calculate Micro-HFT performance metrics."""
        total = self.micro_hft_performance["total_closures"]
        
        if total == 0:
            return {
                "capture_rate": 0.0,
                "total_closures": 0,
                "sweet_spot_closures": 0,
                "missed_sweet_spot": 0,
                "status": "No closures yet"
            }
        
        capture_rate = (self.micro_hft_performance["sweet_spot_closures"] / total) * 100
        
        # Analyze profit distribution
        profits = self.micro_hft_performance["profit_distribution"]
        avg_profit = sum(profits) / len(profits) if profits else 0
        min_profit = min(profits) if profits else 0
        max_profit = max(profits) if profits else 0
        
        return {
            "capture_rate": capture_rate,
            "total_closures": total,
            "sweet_spot_closures": self.micro_hft_performance["sweet_spot_closures"],
            "missed_sweet_spot": self.micro_hft_performance["missed_sweet_spot"],
            "average_profit": avg_profit,
            "min_profit": min_profit,
            "max_profit": max_profit,
            "below_target": capture_rate < (self.sweet_spot_target * 100),
            "status": "BELOW TARGET" if capture_rate < (self.sweet_spot_target * 100) else "MEETS TARGET"
        }
    
    def generate_recommendations(self, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate actionable recommendations based on analysis."""
        recommendations = []
        
        # Check lot size violations
        if analysis.get("lot_size_issues"):
            recommendations.append({
                "priority": "HIGH",
                "category": "Lot Size Compliance",
                "issue": f"{len(analysis['lot_size_issues'])} positions exceed lot size limit",
                "recommendation": "Skip symbols where broker minimum lot > 0.03. System should automatically reject these.",
                "action": "Verify lot size validation is working correctly"
            })
        
        # Check Micro-HFT performance
        hft_perf = self.calculate_micro_hft_performance()
        if hft_perf.get("below_target"):
            recommendations.append({
                "priority": "MEDIUM",
                "category": "Micro-HFT Performance",
                "issue": f"Sweet spot capture rate: {hft_perf['capture_rate']:.2f}% (target: >80%)",
                "recommendation": "Review Micro-HFT closure timing. Ensure trailing stop locks profit before closure attempts.",
                "action": "Adjust closure logic or profit tolerance settings"
            })
        
        # Check filter effectiveness
        filter_stats = analysis.get("filter_effectiveness", {}).get("statistics", {})
        opportunities = filter_stats.get("opportunities_found", 0)
        if opportunities == 0:
            recommendations.append({
                "priority": "MEDIUM",
                "category": "Filter Effectiveness",
                "issue": "No trading opportunities found in recent scans",
                "recommendation": "Consider adjusting filter thresholds if markets are active",
                "action": "Monitor filter rejection reasons and adjust if too restrictive"
            })
        
        # Check for errors
        if analysis.get("errors"):
            recommendations.append({
                "priority": "HIGH",
                "category": "System Errors",
                "issue": f"{len(analysis['errors'])} errors detected",
                "recommendation": "Review error logs and resolve critical issues",
                "action": "Check logs/system/system_errors.log for details"
            })
        
        return recommendations
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive analysis report."""
        logger.info("=" * 80)
        logger.info("GENERATING COMPREHENSIVE ANALYSIS REPORT")
        logger.info("=" * 80)
        
        # Gather all analysis data
        positions_analysis = self.analyze_active_positions()
        trades_analysis = self.analyze_trade_logs()
        filter_analysis = self.analyze_filter_effectiveness()
        errors = self.detect_errors()
        hft_performance = self.calculate_micro_hft_performance()
        
        # Compile comprehensive report
        report = {
            "report_timestamp": datetime.now().isoformat(),
            "session_duration_minutes": (datetime.now() - self.session_start).total_seconds() / 60,
            "active_positions": positions_analysis,
            "trade_statistics": {
                "total_trades": trades_analysis["total_trades"],
                "closed_trades": trades_analysis["closed_trades"],
                "open_trades": trades_analysis["open_trades"]
            },
            "micro_hft_performance": hft_performance,
            "filter_effectiveness": filter_analysis,
            "lot_size_compliance": {
                "violations": positions_analysis.get("lot_size_issues", []),
                "max_lot_size": self.max_lot_size
            },
            "errors_detected": errors,
            "recommendations": []
        }
        
        # Generate recommendations
        recommendations = self.generate_recommendations(report)
        report["recommendations"] = recommendations
        
        # Save report
        report_filename = f"comprehensive_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_path = os.path.join(self.reports_dir, report_filename)
        
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        logger.info(f"✓ Report saved: {report_path}")
        
        # Generate CSV summary
        self._generate_csv_summary(report, report_filename.replace('.json', '.csv'))
        
        return report
    
    def _generate_csv_summary(self, report: Dict[str, Any], csv_filename: str):
        """Generate CSV summary of the report."""
        csv_path = os.path.join(self.reports_dir, csv_filename)
        
        with open(csv_path, 'w') as f:
            # Write header
            f.write("Category,Metric,Value,Status\n")
            
            # Active positions
            f.write(f"Active Positions,Total,{report['active_positions']['total']},\n")
            f.write(f"Active Positions,Total Profit,${report['active_positions']['total_profit']:.2f},\n")
            f.write(f"Active Positions,Lot Size Issues,{len(report['lot_size_compliance']['violations'])},\n")
            
            # Trade statistics
            f.write(f"Trades,Total,{report['trade_statistics']['total_trades']},\n")
            f.write(f"Trades,Closed,{report['trade_statistics']['closed_trades']},\n")
            f.write(f"Trades,Open,{report['trade_statistics']['open_trades']},\n")
            
            # Micro-HFT
            hft = report['micro_hft_performance']
            f.write(f"Micro-HFT,Capture Rate,{hft['capture_rate']:.2f}%,{hft.get('status', 'N/A')}\n")
            f.write(f"Micro-HFT,Total Closures,{hft['total_closures']},\n")
            f.write(f"Micro-HFT,Sweet Spot Closures,{hft['sweet_spot_closures']},\n")
            
            # Filters
            filter_stats = report['filter_effectiveness']['statistics']
            for key, value in filter_stats.items():
                f.write(f"Filters,{key},{value},\n")
            
            # Recommendations
            for rec in report['recommendations']:
                f.write(f"Recommendation,{rec['priority']},{rec['issue']},{rec['category']}\n")
        
        logger.info(f"✓ CSV summary saved: {csv_path}")
    
    def monitor_loop(self):
        """Main monitoring loop."""
        last_report_time = datetime.now()
        
        while self.running:
            try:
                # Quick check every interval
                positions_analysis = self.analyze_active_positions()
                
                # Check for immediate alerts
                if positions_analysis.get("lot_size_issues"):
                    for issue in positions_analysis["lot_size_issues"]:
                        logger.warning(f"🚨 LOT SIZE VIOLATION: {issue['symbol']} Ticket {issue['ticket']} using {issue['lot_size']} lots")
                
                # Generate full report periodically
                if (datetime.now() - last_report_time).total_seconds() >= self.report_interval:
                    self.generate_report()
                    last_report_time = datetime.now()
                
                time.sleep(self.monitor_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                time.sleep(self.monitor_interval)
    
    def start(self):
        """Start the monitoring system."""
        logger.info("=" * 80)
        logger.info("STARTING COMPREHENSIVE BOT MONITOR")
        logger.info("=" * 80)
        logger.info(f"Monitor interval: {self.monitor_interval}s")
        logger.info(f"Report interval: {self.report_interval}s")
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("✓ Comprehensive monitor started")
    
    def stop(self):
        """Stop the monitoring system."""
        logger.info("Stopping comprehensive monitor...")
        self.running = False
        if hasattr(self, 'monitor_thread'):
            self.monitor_thread.join(timeout=5.0)
        
        # Generate final report
        self.generate_report()
        logger.info("✓ Comprehensive monitor stopped")


if __name__ == "__main__":
    import signal
    
    monitor = ComprehensiveBotMonitor()
    
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        monitor.stop()
        os._exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    monitor.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()

