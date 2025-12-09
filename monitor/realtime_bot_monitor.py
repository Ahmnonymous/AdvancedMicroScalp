#!/usr/bin/env python3
"""
Real-Time Bot Monitor
Monitors trading bot performance in real-time during execution.

Features:
- Tracks trades closed early (before $2 stop-loss)
- Monitors Micro-HFT sweet spot performance
- Logs skipped symbols
- Detects missed opportunities
- Runs alongside bot without interference
"""

import os
import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from collections import defaultdict

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.realtime_broker_fetcher import RealtimeBrokerFetcher
from monitor.realtime_reconciliation import RealtimeReconciliation
from utils.logger_factory import get_logger

logger = get_logger("realtime_monitor", "logs/system/realtime_monitor.log")


class RealtimeBotMonitor:
    """Real-time monitoring of bot performance during execution."""
    
    def __init__(self, config: Dict[str, Any], reconciliation_interval_minutes: int = 30):
        """
        Initialize real-time monitor.
        
        Args:
            config: Configuration dictionary
            reconciliation_interval_minutes: Minutes between reconciliation checks
        """
        self.config = config
        self.broker_fetcher = RealtimeBrokerFetcher(config)
        self.reconciliation = RealtimeReconciliation(config, reconciliation_interval_minutes)
        
        # Monitoring state
        self.monitoring_active = False
        self.monitor_thread = None
        self.reconciliation_thread = None
        
        # Tracked data
        self.early_closures = []  # Trades closed before $2 stop-loss
        self.hft_trades = []  # Micro-HFT trades
        self.skipped_symbols = defaultdict(int)  # Symbol -> skip count
        self.missed_opportunities = []  # Missed profit opportunities
        
        # Setup monitoring log
        os.makedirs('logs/system', exist_ok=True)
        self.monitoring_logger = get_logger("monitoring", "logs/system/realtime_monitoring.log")
    
    def start_monitoring(self):
        """Start real-time monitoring."""
        if self.monitoring_active:
            logger.warning("Monitoring already active")
            return
        
        self.monitoring_active = True
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name="RealtimeMonitor",
            daemon=True
        )
        self.monitor_thread.start()
        logger.info("✅ Real-time monitoring started")
        
        # Start reconciliation thread
        self.reconciliation_thread = threading.Thread(
            target=self._reconciliation_loop,
            name="RealtimeReconciliation",
            daemon=True
        )
        self.reconciliation_thread.start()
        logger.info("✅ Real-time reconciliation started")
    
    def stop_monitoring(self):
        """Stop real-time monitoring."""
        self.monitoring_active = False
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5.0)
        
        if self.reconciliation_thread:
            self.reconciliation_thread.join(timeout=5.0)
        
        logger.info("Real-time monitoring stopped")
    
    def _monitoring_loop(self):
        """Main monitoring loop."""
        check_interval = 10.0  # Check every 10 seconds
        
        while self.monitoring_active:
            try:
                # Monitor open positions
                self._monitor_open_positions()
                
                # Monitor skipped symbols
                self._monitor_skipped_symbols()
                
                # Monitor trade logs for new entries
                self._monitor_trade_logs()
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
            
            time.sleep(check_interval)
    
    def _reconciliation_loop(self):
        """Reconciliation loop."""
        self.reconciliation.run_continuous(stop_event=threading.Event() if not self.monitoring_active else None)
    
    def _monitor_open_positions(self):
        """Monitor currently open positions."""
        if not self.broker_fetcher.ensure_connected():
            return
        
        try:
            positions = self.broker_fetcher.get_current_positions()
            
            for position in positions:
                ticket = position['ticket']
                profit = position.get('profit', 0.0)
                
                # Check for early closure opportunities (profit > $0.10 but not at $0.10 multiple)
                if profit > 0.10:
                    is_multiple = abs(profit % 0.10) < 0.005
                    if not is_multiple:
                        # Could close at next $0.10 multiple
                        next_multiple = ((int(profit / 0.10) + 1) * 0.10)
                        potential_profit = next_multiple - profit
                        
                        if potential_profit < 0.05:  # Close to next multiple
                            self.missed_opportunities.append({
                                'ticket': ticket,
                                'symbol': position['symbol'],
                                'current_profit': profit,
                                'next_multiple': next_multiple,
                                'timestamp': datetime.now()
                            })
                            self.monitoring_logger.info(
                                f"Near $0.10 multiple: Ticket {ticket} ({position['symbol']}) "
                                f"at ${profit:.2f}, next multiple: ${next_multiple:.2f}"
                            )
        
        except Exception as e:
            logger.debug(f"Error monitoring open positions: {e}")
    
    def _monitor_skipped_symbols(self):
        """Monitor skipped symbols from filter logs."""
        skipped_log = 'logs/system/skipped_pairs.log'
        
        if not os.path.exists(skipped_log):
            return
        
        try:
            # Read last 100 lines
            with open(skipped_log, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Parse recent skips (last 5 minutes)
            cutoff = datetime.now() - timedelta(minutes=5)
            
            for line in lines[-100:]:  # Last 100 lines
                if not line.strip():
                    continue
                
                try:
                    # Parse: TIMESTAMP - LEVEL - MESSAGE
                    parts = line.split(' - ', 2)
                    if len(parts) >= 3:
                        timestamp_str = parts[0]
                        message = parts[2]
                        
                        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                        if timestamp < cutoff:
                            continue
                        
                        # Extract symbol
                        if ':' in message:
                            symbol = message.split(':')[0].strip()
                            self.skipped_symbols[symbol] += 1
                except:
                    pass
        
        except Exception as e:
            logger.debug(f"Error monitoring skipped symbols: {e}")
    
    def _monitor_trade_logs(self):
        """Monitor trade logs for new Micro-HFT closures."""
        trades_dir = Path('logs/trades')
        if not trades_dir.exists():
            return
        
        try:
            # Check each symbol log for new Micro-HFT entries
            for log_file in trades_dir.glob('*.log'):
                if 'backup' in log_file.name:
                    continue
                
                # Read last few lines
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # Check last 10 lines for new Micro-HFT trades
                for line in lines[-10:]:
                    line = line.strip()
                    if line and line.startswith('{'):
                        try:
                            trade = json.loads(line)
                            additional_info = trade.get('additional_info', {})
                            
                            # Check if Micro-HFT trade
                            if (additional_info.get('close_type') == 'micro_hft' or
                                'Micro-HFT' in str(additional_info.get('close_reason', ''))):
                                
                                order_id = str(trade.get('order_id', ''))
                                
                                # Check if already tracked
                                if not any(hft['order_id'] == order_id for hft in self.hft_trades):
                                    profit = trade.get('profit_usd', 0)
                                    in_sweet_spot = 0.03 <= profit <= 0.10
                                    
                                    self.hft_trades.append({
                                        'order_id': order_id,
                                        'symbol': trade.get('symbol', ''),
                                        'profit': profit,
                                        'in_sweet_spot': in_sweet_spot,
                                        'timestamp': trade.get('timestamp', '')
                                    })
                                    
                                    if in_sweet_spot:
                                        self.monitoring_logger.info(
                                            f"Micro-HFT sweet spot: Order {order_id} ({trade.get('symbol', '')}) "
                                            f"closed at ${profit:.2f}"
                                        )
                                    else:
                                        self.monitoring_logger.info(
                                            f"Micro-HFT non-sweet-spot: Order {order_id} ({trade.get('symbol', '')}) "
                                            f"closed at ${profit:.2f}"
                                        )
                        except:
                            pass
        
        except Exception as e:
            logger.debug(f"Error monitoring trade logs: {e}")
    
    def get_monitoring_summary(self) -> Dict[str, Any]:
        """Get current monitoring summary."""
        return {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'hft_trades': len(self.hft_trades),
            'hft_sweet_spot': sum(1 for hft in self.hft_trades if hft['in_sweet_spot']),
            'hft_sweet_spot_rate': (sum(1 for hft in self.hft_trades if hft['in_sweet_spot']) / 
                                   len(self.hft_trades) * 100) if self.hft_trades else 0,
            'early_closures': len(self.early_closures),
            'skipped_symbols_count': len(self.skipped_symbols),
            'missed_opportunities': len(self.missed_opportunities)
        }

